"""행내 GenAI 플랫폼용 HTTP 진입점(FastAPI). 상세 호출 계약은 client/README.md.

    POST /chat   {"input_value": "<JSON 문자열>", "message_hists": null}
      → text/event-stream, 이벤트마다 {"event": "CHUNK", "content": "<JSON 이벤트>"}

요청 키(input_value 안):
    message         (필수 · mode=branch_briefing 이면 생략) 직원 질문
    mode            (선택) "branch_briefing" — 부점 브리핑 화면 진입. 질문 없이 현황 패널 + 전략 브리핑
    x_client_user   (필수) 호출 직원 식별자 · 쿼터 버킷. 사번 7자리로 시작하면 사번으로 사용
    customer_id     (선택) 브리핑 화면 고객 id. `b64:` + base64(원장 표기) 권장 — 원장 표기는 게이트웨이 FILTER_INVALID
    session_id      (선택) 세션 구분자. 기본 "default"
    employee_id     (선택) WorkB 사번. 없으면 x_client_user → WORKB_EMP_NO 순
    log_events      (선택) true 면 이 요청에 log·trace 이벤트 포함

응답 이벤트 type(한 턴 순서):
    progress   0개 이상 · 답변 전
    answer     1개 · text · intent · links · messages(없으면 [])
    action     연계 제안 턴에만
    clarify    되묻기 턴에만
    sources    항상(0건이면 [])
    followups  항상(없으면 [])
    trace      로그 이벤트 켰을 때만 · done 바로 앞
    done       항상 마지막
    error      실패 시 answer 대신 · 그 뒤 done
    log        로그 이벤트 켰을 때만 · 아무 자리

mode=branch_briefing 응답 이벤트 type(순서):
    branch_panel     1개 · 부점 현황(지표·세그먼트 전월말 대비·오늘의 CASE) — 숫자는 여기서만
    branch_briefing  1개 · text · status(ok·fallback·no_case·llm_down) · reason
    done             항상 마지막 · 실패 시 error 뒤 done

프레이밍: 스트림은 SSE(`data: {...}\n\n`, CHAT_SSE_FRAMING=0 이면 줄마다 JSON) · 비스트림 신호 시 JSON 하나.
로그: 루트 로거 → stdout · INFO. 로거 `api`(HTTP 경계) · `agent`(단계별).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Callable, List, Optional

import socket
import urllib.parse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from pension_agent.common import config, llm, mcp, observability, privacy
from pension_agent import context_store
from pension_agent import graph as consult_graph
from pension_agent.nodes import act as consult_act
from pension_agent.tools.branch_briefing import strategy as branch_strategy
from pension_agent.tools.customer_solution import customer as strategy_customer


#: INFO 를 끄는 바깥 라이브러리 로거 — httpx·행내 SDK 가 MCP 주소·사번을 INFO 로 출력
_QUIET_LOGGERS = mcp.NOISY_LOGGERS


def _setup_logging() -> None:
    """루트 로거 → stdout · INFO. 이미 설정돼 있으면 유지. 타임스탬프는 수집기가 부여."""
    mcp.quiet_loggers()
    if logging.getLogger().handlers:
        return
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)


#: stdout 로그 줄 형식. 로그 이벤트(`_LogTap`)도 같은 형식 사용.
LOG_FORMAT = "%(levelname)s:     [%(name)s] %(message)s"


class _LogTap(logging.Handler):
    """요청 id 별 로그 줄 사본을 그 요청 응답에 싣는 루트 핸들러.

    요청 id 판별: `observability.request_id` 컨텍스트, 없으면 `"[%s] …"` 의 첫 인자(`[api]` 줄).
    """

    #: 동시 요청 상한. 스트림 시작 전 끊긴 요청은 detach 누락 — 오래된 것부터 폐기.
    MAX_SINKS = 200

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self._sinks: dict[str, Callable[[dict[str, Any]], None]] = {}

    def attach(self, rid: str, sink: Callable[[dict[str, Any]], None]) -> None:
        while len(self._sinks) >= self.MAX_SINKS:
            self._sinks.pop(next(iter(self._sinks)), None)
        self._sinks[rid] = sink

    def detach(self, rid: str) -> None:
        self._sinks.pop(rid, None)

    @staticmethod
    def _rid_of(record: logging.LogRecord) -> Optional[str]:
        rid = observability.current_request_id()
        if rid:
            return rid
        args = record.args
        if isinstance(record.msg, str) and record.msg.startswith("[%s]") \
                and isinstance(args, tuple) and args and isinstance(args[0], str):
            return args[0]
        return None

    def emit(self, record: logging.LogRecord) -> None:
        if not self._sinks:
            return
        try:
            rid = self._rid_of(record)
            sink = self._sinks.get(rid) if rid else None
            if sink is not None:
                # 원장 표기 고객 id 그대로 실으면 게이트웨이 FILTER_INVALID — 응답 쪽만 가림
                text, _ = privacy.mask(self.format(record))
                sink({"type": "log", "level": record.levelname, "logger": record.name,
                      "text": text})
        except Exception:                                  # noqa: BLE001 — 기록은 흐름을 막지 않는다
            self.handleError(record)


_setup_logging()
_log_tap = _LogTap()
logging.getLogger().addHandler(_log_tap)
#: HTTP 경계 로거. `__name__` 은 실행 방식에 따라 main·__main__ 으로 갈려 고정 문자열 사용.
log = logging.getLogger("api")

#: 요청 로그의 질문 미리보기 길이. 전문 미기록.
QUESTION_PREVIEW = 60


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # 행내 MCP 연결은 기동 시 한 번 — 설정 없으면 쪽지는 «미연결»
    mcp.install()
    # 기동 설정 한 줄(/health 와 동일)
    log.info("[-] startup     설정=%s", json.dumps(health(), ensure_ascii=False))
    # 단계와 어긋난 키는 첫 호출의 401 로만 드러나므로 기동 시 경고
    if llm.key_stage_mismatch():
        log.warning("[-] startup     LLM 키가 단계와 어긋납니다 — URL=%s · 키=%s(접미사 없음) · 단계=%s"
                    " · %s 에 이 단계의 키를 넣으십시오(지금 상태로는 게이트웨이가"
                    " 401 invalid subscription key 로 끊습니다)",
                    llm.BASE_URL_SRC, llm.API_KEY_SRC, llm.STAGE, llm.wanted_key_name())
    yield


app = FastAPI(title="퇴직연금 AI 사후관리 에이전트", lifespan=_lifespan)


class ChatRequest(BaseModel):
    # 스키마 밖 키(stream 등)도 비스트림 판정에 사용 — 보존
    model_config = ConfigDict(extra="allow")

    input_value: str
    message_hists: Optional[List] = None


#: 스트림 프레이밍. 기본 SSE — 게이트웨이 SSE 파서가 `data:` 없는 줄을 버림. 0 이면 줄마다 JSON.
SSE_FRAMING = os.getenv("CHAT_SSE_FRAMING", "1").strip().lower() not in ("0", "false", "no")

#: 모든 요청에 로그 이벤트 포함. 꺼져 있어도 input_value 의 `log_events` 로 요청별 켜기 가능.
LOG_EVENTS = os.getenv("CHAT_LOG_EVENTS", "0").strip().lower() in ("1", "true", "yes")


def _wants_logs(payload: dict[str, Any]) -> bool:
    return LOG_EVENTS or payload.get("log_events") in (True, 1, "1", "true", "True")


#: 비스트림 신호 키(본문 최상위 · input_value 안).
_STREAM_KEYS = ("stream", "isStream", "is_stream")


def _chunk(text: str) -> str:
    line = json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False)
    return f"data: {line}\n\n" if SSE_FRAMING else line + "\n"


def _event_json(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False)


def _event(event: dict[str, Any]) -> str:
    """이벤트 하나를 CHUNK 로 — content 는 JSON 객체 문자열."""
    return _chunk(_event_json(event))


def _strip_followups(answer: str) -> str:
    """답변 끝 추천질문 블록 제거 — followups 이벤트로만 전달."""
    head, sep, _ = answer.rpartition("\n\n" + consult_graph.FOLLOWUP_HEADER + "\n")
    return head if sep else answer


#: action 이벤트에 싣는 pending_action 키. 실행 인자(html·recipients·params) 미포함.
_ACTION_KEYS = ("kind", "label", "prompt", "title", "text", "to")


def _masked(value: Any) -> Any:
    """값 안의 모든 문자열에 개인정보 가림 적용. 구조 유지."""
    if isinstance(value, str):
        return privacy.mask(value)[0]
    if isinstance(value, list):
        return [_masked(v) for v in value]
    if isinstance(value, dict):
        return {k: _masked(v) for k, v in value.items()}
    return value


def _turn_events(result: dict[str, Any], *, with_trace: bool = False) -> list[dict[str, Any]]:
    """ask() 결과 → answer → (action | clarify) → sources → followups → [trace] → done."""
    events: list[dict[str, Any]] = [{
        "type": "answer",
        "text": _strip_followups(result.get("answer", "")),
        "intent": result.get("intent"),
        # 단말 화면 딥링크 — 본문 안 화면번호를 감싸는 재료라 별도 이벤트가 아닌 answer 필드
        "links": list(result.get("links") or []),
        # 본문이 인용한 고객 발송 문구(LMS) — links 와 같은 이유로 answer 필드
        "messages": list(result.get("messages") or []),
    }]
    action = result.get("pending_action")
    # 동명이인 후보 턴(`candidates`)은 네/아니오 턴이 아님 — action 미전송
    if action and not action.get("candidates"):
        ev = {k: action[k] for k in _ACTION_KEYS if action.get(k) is not None}
        # 본문 제안 문장과 같은 함수 사용 — 별도 폴백 문자열 금지
        ev.setdefault("prompt", consult_act.offer_prompt(action))
        events.append({"type": "action", **ev})
    clarify = result.get("clarify")
    if clarify:
        events.append({"type": "clarify", "question": clarify.get("question"),
                       "options": list(clarify.get("options") or [])})
    events.append({"type": "sources", "items": list(result.get("sources") or [])})
    events.append({"type": "followups", "items": list(result.get("followups") or [])})
    if with_trace and result.get("trace") is not None:
        # 근거 카드 원문 포함 — 게이트웨이 개인정보 필터 대비 가림
        events.append({"type": "trace", **_masked(result["trace"])})
    events.append({"type": "done"})
    return events


def _error_events(exc: BaseException) -> list[dict[str, Any]]:
    # 실패도 이벤트로 전달 — 빈 응답과 구분
    return [{"type": "error", "text": f"{type(exc).__name__}: {exc}"}, {"type": "done"}]


#: 부점 브리핑 화면 진입 요청(질문 없음).
BRIEFING_MODE = "branch_briefing"


def _briefing_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    """strategy.build() 결과 → branch_panel → branch_briefing → done."""
    brief = result["briefing"]
    return [{"type": "branch_panel", **result["panel"]},
            {"type": "branch_briefing", "text": brief.get("text"), "status": brief.get("status"),
             "reason": brief.get("reason")},
            {"type": "done"}]


async def _branch_briefing(args: dict[str, Any], rid: str, streaming: bool):
    """화면 진입 — 대화 그래프를 거치지 않고 현황(코드) + 전략 브리핑 문장(LLM 1회). 맥락 저장 안 함."""
    started = time.monotonic()
    emp_no = consult_graph.employee_no(args["employee_id"], args["x_client_user"])
    log.info("[%s] request     모드=%s 사용자=%s 사번=%s", rid, BRIEFING_MODE,
             args["x_client_user"], emp_no or "-")

    def run_once() -> dict[str, Any]:
        with observability.request_id(rid), llm.client_user(args["x_client_user"]):
            return branch_strategy.build(emp_no)
    try:
        events = _briefing_events(await asyncio.to_thread(run_once))
        log.info("[%s] done        모드=%s 소요=%.1f초 브리핑=%s", rid, BRIEFING_MODE,
                 time.monotonic() - started, events[1].get("status"))
    except Exception as exc:  # noqa: BLE001 — 부점 미확인·DB 미연결도 이벤트로
        log.warning("[%s] error       모드=%s 사유=%r", rid, BRIEFING_MODE,
                    f"{type(exc).__name__}: {exc}")
        events = _error_events(exc)
    if not streaming:
        return JSONResponse({"event": "CHUNK",
                             "content": "\n".join(_event_json(e) for e in events)})

    async def generate():
        for ev in events:
            yield _event(ev)
    return StreamingResponse(generate(), media_type="text/event-stream")


def _wants_stream(request: Request, req: ChatRequest, payload: dict[str, Any]) -> bool:
    """비스트림 신호가 하나라도 있으면 False. 기본 스트림."""
    for src in (req.model_extra or {}, payload):
        for key in _STREAM_KEYS:
            if key in src and src[key] in (False, 0, "false", "False", "0"):
                return False
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/event-stream" not in accept and "*/*" not in accept:
        return False
    return True


_SECRET_HINTS = ("token", "key", "auth", "cookie", "secret")


#: 거부된 요청의 input_value 로그 미리보기 길이(글자 수).
INPUT_VALUE_PREVIEW = 120


def _request_shape(request: Request, req: ChatRequest, payload: Optional[dict[str, Any]]) -> str:
    """요청 모양(헤더·키) 로그용 JSON. 비밀 헤더 값 가림. payload None 이면 원문 길이·앞부분."""
    headers = {
        k: ("***" if any(h in k.lower() for h in _SECRET_HINTS) else v)
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "user-agent")
    }
    hists = req.message_hists
    shape: dict[str, Any] = {
        "headers": headers,
        "body_extra": sorted((req.model_extra or {}).keys()),
        "message_hists": None if hists is None else f"{type(hists).__name__}[{len(hists)}]",
    }
    if payload is None:
        raw = req.input_value
        shape["input_value_raw"] = {
            "len": len(raw),
            "head": raw[:INPUT_VALUE_PREVIEW] + ("…" if len(raw) > INPUT_VALUE_PREVIEW else ""),
        }
    else:
        shape["input_value_keys"] = sorted(payload.keys())
    return json.dumps(shape, ensure_ascii=False)


def _reject(rid: str, detail: str) -> HTTPException:
    """422 반환 + 사유 로그(접속 로그에는 상태코드만 남음)."""
    log.warning("[%s] reject      상태=422 사유=%r", rid, detail)
    return HTTPException(status_code=422, detail=detail)


def _parse(req: ChatRequest, rid: str = "-") -> dict[str, Any]:
    """input_value 파싱 · 필수 키 확인. 어긋나면 422."""
    try:
        payload = json.loads(req.input_value)
    except json.JSONDecodeError as exc:
        raise _reject(rid, f"input_value 가 JSON 문자열이 아닙니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise _reject(rid, "input_value 는 JSON 객체를 직렬화한 문자열이어야 합니다.")

    x_client_user = payload.get("x_client_user")
    if not x_client_user:
        raise _reject(rid, "input_value 에 'x_client_user' 키가 필요합니다.")
    mode = payload.get("mode")
    if mode not in (None, "", BRIEFING_MODE):
        raise _reject(rid, f"input_value 의 'mode' 는 {BRIEFING_MODE!r} 만 쓸 수 있습니다.")
    message = payload.get("message")
    if not message and mode != BRIEFING_MODE:
        raise _reject(rid, "input_value 에 'message' 키가 필요합니다.")

    # Turn 형식(question 키)만 사용 — 다른 형식은 format_history 에서 500. None 이면 저장 맥락 사용
    hists = req.message_hists
    history = hists if isinstance(hists, list) and hists and all(
        isinstance(h, dict) and "question" in h for h in hists) else None

    return {
        "question": str(message or ""),
        "mode": mode or None,
        "history": history,
        # b64·13자리 → 원장 표기
        "customer_id": strategy_customer.normalize_id(payload.get("customer_id")),
        "session_id": str(payload.get("session_id") or "default"),
        "x_client_user": str(x_client_user),
        # 없으면 ask() 가 x_client_user 에서 판정 — 여기서 판정 금지
        "employee_id": str(payload.get("employee_id") or "").strip() or None,
        "payload": payload,
    }


def _host_check() -> dict[str, Any]:
    """LLM_BASE_URL 호스트의 DNS 해석 여부. `*.svc.cluster.local` 은 클러스터 밖에서 미해석. 연결 미시도."""
    if not llm.BASE_URL:
        return {"host": None, "resolves": None}
    host = urllib.parse.urlparse(llm.BASE_URL).hostname or ""
    try:
        socket.getaddrinfo(host, None)
        return {"host": host, "resolves": True}
    except socket.gaierror as exc:
        return {"host": host, "resolves": False, "error": str(exc)}


@app.get("/health")
def health() -> dict[str, Any]:
    """기동 확인 + LLM 설정 진단. 키 값은 미노출(설정 여부만)."""
    return {
        "status": "ok",
        # 읽힌 .env — 상세는 `python -m pension_agent.common.env`
        "env": {"dotenv": str(config.DOTENV), "exists": config.DOTENV.is_file()},
        "llm": {
            "provider": llm.PROVIDER,
            # train(…/trnn/…) · serving(…/serv/…)
            "stage": llm.STAGE,
            "available": llm.available(),
            "base_url_set": bool(llm.BASE_URL),
            "api_key_set": bool(llm.API_KEY),
            # 값 대신 출처 변수명 — URL·키가 각자 폴백해 단계 불일치(401) 발생 가능
            "base_url_from": llm.BASE_URL_SRC or None,
            "api_key_from": llm.API_KEY_SRC or None,
            "key_stage_mismatch": llm.key_stage_mismatch(),
            "model": llm.MODEL or "(게이트웨이 기본 라우팅)",
            "timeout_sec": llm.TIMEOUT,
            **_host_check(),
        },
        # 메모리에 살아 있는 대화 맥락 수
        "context_store": context_store.stats(),
        # 행내 MCP 연결 상태 · 빠진 설정. 키·토큰 미노출
        "mcp": mcp.stats(),
        # 429 대응용 호출 게이트 설정
        "rate_gate": {
            "max_concurrency": llm.MAX_CONCURRENCY,
            "min_interval_sec": llm.MIN_INTERVAL,
            "retry_attempts": llm.RETRY_ATTEMPTS,
            "cooldown_sec": llm.COOLDOWN,
            # 쿼터 버킷 분산(0=끔)
            "client_user_spread": llm.CLIENT_USER_SPREAD,
        },
    }


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    rid = uuid.uuid4().hex[:8]   # 이 요청의 로그 줄을 한데 묶는 id
    try:
        args = _parse(req, rid)
    except HTTPException:
        # 거부된 요청도 모양 기록
        log.warning("[%s] reject      모양=%s", rid, _request_shape(request, req, None))
        raise
    started = time.monotonic()
    question = args["question"]
    x_client_user, session_id = args["x_client_user"], args["session_id"]
    streaming = _wants_stream(request, req, args["payload"])
    if args["mode"] == BRIEFING_MODE:
        return await _branch_briefing(args, rid, streaming)
    # 로그 이벤트는 요청 줄보다 먼저 attach — 스트림은 큐로, 비스트림은 모아서 content 앞에
    with_logs = _wants_logs(args["payload"])
    log_events: list[dict[str, Any]] = []
    if streaming:
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        # 워커 스레드 → 이벤트 루프 전달 큐
        lines: asyncio.Queue = asyncio.Queue()

        def _push(ev: dict[str, Any]) -> None:
            # 루프 스레드 줄(`[api]`)은 즉시 — call_soon 이면 뒤 이벤트(error 등)보다 늦음
            if threading.get_ident() == loop_thread:
                lines.put_nowait(ev)
            else:
                loop.call_soon_threadsafe(lines.put_nowait, ev)
    if with_logs:
        _log_tap.attach(rid, _push if streaming else log_events.append)
    # 대화 맥락: 호출자 Turn 우선, 없으면 저장본
    if args["history"] is not None:
        history, history_from = args["history"], "호출자"
    else:
        history = context_store.get(x_client_user, session_id)
        history_from = "저장" if history else "없음"
    log.info(
        "[%s] request     사용자=%s 사번=%s 고객=%s 세션=%s 맥락=%d턴(%s) 질문=%d자 %r",
        rid, x_client_user,
        # «-» 이면 환경변수 폴백 — 다중 사용자 배포에서 남의 이름으로 발송
        consult_graph.employee_no(args["employee_id"], x_client_user) or "-",
        args["customer_id"] or "-", session_id,
        len(history or []), history_from, len(question),
        question[:QUESTION_PREVIEW] + ("…" if len(question) > QUESTION_PREVIEW else ""),
    )

    def _remember(result: dict[str, Any]) -> None:
        context_store.put(x_client_user, session_id, result.get("history"))

    def _log_done(result: dict[str, Any]) -> None:
        # HTTP 정보만 — 나머지는 `[agent]` turn 줄
        log.info("[%s] done        소요=%.1f초 응답=%s 답변=%d자",
                 rid, time.monotonic() - started, "sse" if streaming else "json",
                 len(result.get("answer", "")))
    log.info("[%s] request     응답=%s 모양=%s", rid, "sse" if streaming else "json",
             _request_shape(request, req, args["payload"]))

    if not streaming:
        # 비스트림 — 게이트웨이가 본문 전체를 json.loads. 이벤트를 줄바꿈으로 이어 content 하나에(progress 제외)
        def run_once() -> dict[str, Any]:
            with observability.request_id(rid):
                return consult_graph.ask(
                    question, history,
                    customer_id=args["customer_id"], session_id=session_id,
                    x_client_user=x_client_user, employee_id=args["employee_id"],
                )
        try:
            result = await asyncio.to_thread(run_once)
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] error       시점=처리중 소요=%.1f초 사유=%r",
                          rid, time.monotonic() - started, f"{type(exc).__name__}: {exc}")
            events = _error_events(exc)
        else:
            _remember(result)
            _log_done(result)
            events = _turn_events(result, with_trace=with_logs)
        finally:
            _log_tap.detach(rid)
        events = log_events + events
        return JSONResponse({"event": "CHUNK",
                             "content": "\n".join(_event_json(e) for e in events)})

    async def generate():
        DONE = object()

        def on_progress(text: str) -> None:
            # 화면 전용 — 로그 미기록
            loop.call_soon_threadsafe(lines.put_nowait, {"type": "progress", "text": text})

        def _drain():
            # DONE 뒤에 찍힌 로그 줄 비우기
            while not lines.empty():
                item = lines.get_nowait()
                if item is not DONE:
                    yield _event(item)

        def run() -> dict[str, Any]:
            try:
                with observability.request_id(rid):
                    result = consult_graph.ask(
                        question, history,
                        customer_id=args["customer_id"], session_id=session_id,
                        x_client_user=x_client_user, employee_id=args["employee_id"],
                        on_progress=on_progress,
                    )
                # 맥락 저장은 스레드 안에서 — 호출자가 중간에 끊어도 다음 턴 «네»가 이어지도록
                _remember(result)
                _log_done(result)
                return result
            finally:
                # 반드시 DONE — 누락 시 아래 루프 무한 대기
                loop.call_soon_threadsafe(lines.put_nowait, DONE)

        # ask() 는 동기 블로킹 — to_thread 필수(컨텍스트 복사로 x-client-user 전달)
        task = asyncio.create_task(asyncio.to_thread(run))
        finished = False
        closed = False

        def _settle(t: "asyncio.Task[dict[str, Any]]") -> None:
            # 끊긴 뒤의 ask() 예외는 await 되지 않으므로 여기서 기록
            if closed and not t.cancelled() and t.exception() is not None:
                log.error("[%s] error       시점=끊긴뒤 사유=%r", rid,
                          f"{type(t.exception()).__name__}: {t.exception()}")

        task.add_done_callback(_settle)
        try:
            while True:
                item = await lines.get()
                if item is DONE:
                    break
                yield _event(item)

            try:
                result = await task
            except Exception as exc:  # noqa: BLE001
                # 스트림 시작 후라 상태코드 변경 불가 — 이벤트로 전달
                log.exception("[%s] error       시점=처리중 소요=%.1f초 사유=%r",
                              rid, time.monotonic() - started, f"{type(exc).__name__}: {exc}")
                for chunk in _drain():
                    yield chunk
                for ev in _error_events(exc):
                    yield _event(ev)
                finished = True
                return

            for chunk in _drain():
                yield chunk
            for ev in _turn_events(result, with_trace=with_logs):
                yield _event(ev)
            finished = True
        finally:
            _log_tap.detach(rid)
            if not finished:
                closed = True
                # 호출자 조기 종료 — 접속 로그엔 200 으로만 남음
                log.warning("[%s] disconnect  소요=%.1f초 답변전송=아니오",
                            rid, time.monotonic() - started)

    return StreamingResponse(generate(), media_type="text/event-stream")
