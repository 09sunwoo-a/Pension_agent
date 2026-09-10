"""행내 GenAI 플랫폼용 HTTP 진입점 — FastAPI.

플랫폼이 요구하는 I/O 스키마는 **고정**이라 여기서 임의로 바꾸지 않는다
(skills/genai-platform-agent-dev/refs/genai-platform.md «API I/O 스키마 (고정)»):

    POST /chat   {"input_value": "<JSON 문자열>", "message_hists": null}
      → text/event-stream, 이벤트마다 {"event": "CHUNK", "content": "..."}

━━ 응답 프레이밍 — 문서와 게이트웨이가 어긋난다 (2026-09-09 행내 실측) ━━
위 문서는 «줄마다 JSON» 이라고 적었지만, 게이트웨이(/openapi/agent-chat/v1/agent-messages)
를 거쳐 부르면 그 형식은 읽히지 않았다:

  isStream=true  → 게이트웨이가 content "" · status SUCCESS 인 이벤트 하나만 돌려준다.
                   SSE 파서는 `data:` 로 시작하지 않는 줄을 버리므로, 우리 줄이 전부
                   무시된 것과 정확히 같은 결과다(추정 — 파서 코드는 못 봤다).
  isStream=false → status ERROR · responseCode R40000 · content 에
                   "[Errno Extra data] {우리 응답 원문} : 92" — 게이트웨이가 응답 본문
                   전체를 json.loads 했고 첫 줄(92자) 뒤에서 죽었다(확정 — 오류 문구가
                   파이썬 JSONDecodeError 그대로다). 비스트림은 **JSON 하나**를 기대한다.

그래서 스트림은 SSE 프레임(`data: {...}\n\n`)으로 내보내고(CHAT_SSE_FRAMING=0 이면 문서
형식으로 되돌린다), 호출이 비스트림임을 알 수 있으면 JSON 하나로 답한다. 게이트웨이가
비스트림을 어떻게 알려오는지는 아직 못 봤다 — 그래서 요청마다 헤더·키를 로그에 찍는다
(아래 «로그»). 지금 보는 신호: Accept 가 application/json 뿐이거나, 본문·input_value 에
stream/isStream/is_stream 이 false 로 있으면 비스트림.

`input_value` 는 **JSON 을 문자열로 직렬화한 것**이다. 이 프로젝트가 그 안에서 읽는 키:

    message         (필수) 직원이 입력한 질문
    x_client_user   (필수) 호출한 직원 식별자 — **「사번 7자리 + uuid」** 꼴이다
                           (`3902172-550e8400-…`. uuid 는 LLM 중복 호출을 가른다).
                           플랫폼의 감사 기록이자 쿼터 버킷이고, 앞 7자리가 쪽지의
                           받는 사람·보내는 사람이 된다(아래 employee_id)
    customer_id     (선택) 지금 열려 있는 브리핑 화면의 고객 id. 고객 관련 기능은
                           이것이 있어야 성립한다 — 없으면 에이전트가 그렇게 답한다
    session_id      (선택) 상담 세션 구분자. 없으면 "default". 같은 값으로 이어 보내면
                           이전 턴의 맥락이 이어진다(아래 «대화 맥락»)
    employee_id     (선택) 로그인한 직원의 **WorkB 사번**. 쪽지의 기본 수신자이자 발송
                           주체이고 상담이력에 «누가 상담했나»로 남는다. 보통은 넘길
                           필요가 없다 — `x_client_user` 앞 7자리에서 읽는다
                           (`pension_agent/workb.py::as_emp_no`). 사번을 다른 데서
                           받아오는 배포를 위한 자리이고, 여기 실은 값은 꼴을 검사하지
                           않고 그대로 쓴다. 사번을 하나도 못 읽으면 `WORKB_EMP_NO`
                           환경변수로 떨어지고, 그것도 없으면 쪽지 발송을 제안하지 않는다

━━ 출력 형식 — CHUNK 의 content 는 JSON 이벤트 하나다 ━━
프론트가 답변·근거·진행·추천질문을 **다른 자리에** 그려야 하는데 플랫폼 스키마는 CHUNK 텍스트
하나뿐이다. 그래서 content 에 JSON 객체 문자열을 싣고 `type` 으로 가른다(플랫폼 자체 채팅창은
JSON 원문을 보게 되므로 포기했다 — 2026-09-09 결정). 한 턴의 순서:

    {"type": "progress",  "text": "질문 내용을 파악하고 있어요"}            0개 이상 · 답변 전에
    {"type": "answer",    "text": "<본문>", "intent": "situation"}        1개
    {"type": "action",    "kind", "label", "prompt", ...}                  연계 제안 턴에만 — 네/아니오 버튼용.
                                                                          본문 끝의 제안 문장은 그대로 둔다
    {"type": "clarify",   "question": "...", "options": ["..."]}           되묻기 턴에만 — 선택지 버튼용
    {"type": "sources",   "items": [{"id","doc","title","url","score","page","role"}]}
                                                                          항상 · 0건이면 [] (근거 없음을 화면이 말해야 한다)
    {"type": "followups", "items": ["..."]}                                항상 · 없으면 []
    {"type": "done"}                                                       항상 마지막
    {"type": "error",     "text": "LLMError: ..."}                         실패 시 answer 대신 · 그 뒤 done

answer.text 에서 추천질문 블록(graph.FOLLOWUP_HEADER)은 뗀다 — followups 로만 간다. 연계 제안
문장(«… 연계해드릴까요? (네 / 아니오)»)은 답변의 마지막 문장으로 남긴다 — 직원이 「네」로 답하는
대화 경로가 그 문장을 전제로 한다. 프론트는 action 이벤트로 버튼만 그린다.
진행(progress)은 항상 흘린다 — 별도 type 이라 답변과 섞일 일이 없다.
프론트 파서는 content 하나에 JSON 객체가 연달아 있어도 읽어야 한다(게이트웨이가 이벤트를 합쳐
보내지 않는다는 확인이 아직 없다) — client/call_agent.py 의 `_events_in` 이 참조 구현이다.
계약 문서는 client/README.md — 이벤트 type 을 바꾸면 함께 고친다. tests/test_api.py
가 여기서 내보내는 type 전부가 그 문서에 있는지 검사한다.

━━ 대화 맥락 — 멀티턴 ━━
후속 질문("그럼 안 된다고 하면요?")·되묻기의 답·연계 확인("네")은 이전 턴의 `history`
(Turn 목록, state.Turn)가 있어야 해석된다. 게이트웨이 경로는 그것을 돌려줄 자리가 없으므로
진입점이 `(x_client_user, session_id)` 키로 메모리에 맡겨 두고 다음 턴에 되찾는다
(consult_agent/context_store.py — 최근 4턴 · 2시간 · 500세션 · 디스크에 안 쓴다).
호출자가 `message_hists` 에 Turn 형식(`question` 키가 있는 dict 목록)을 실어 보내면 그것이
저장본보다 우선한다. 다른 형식(OpenAI 식 messages 등)은 버린다 — Turn 이 아닌 것을 넘기면
`format_history` 가 `turn['question']` 에서 죽어 500 이 난다.

이 파일은 **얇다.** 판단·검증·문장 생성은 전부 consult_agent 안에서 끝나고, 여기서는
파싱·스트리밍·오류 형태만 맡는다. 화면(Streamlit app.py)과 이 API 는 같은 `ask()` 하나를
부른다 — 두 경로가 갈리면 «화면에서는 되는데 API 에서는 다르게 나오는» 자리가 생긴다.

━━ 무엇을 흘리는가 ━━
  답변      항상, 한 이벤트로. 토큰 단위로 흘리지 않는다 — compose 의 생성문은 검증 게이트
            (verify_texts · relations · 원문 스팬)에서 **통째로 폐기**될 수 있어서, 토큰을
            흘려보내면 직원이 이미 읽은 문장이 사라진다. "근거 밖 수치를 내보내지 않는다"는
            보증이 화면에서 뒤집히는 것이다(progress.py 주석).
  출처      **항상.** 이 에이전트의 답은 «근거 안에서만» 나오고, 그 근거를 보여주는 것이
            존재 이유다(루트 CLAUDE.md §2). 출처 없는 답변은 이 시스템의 산출물이 아니다.
            0건이면 빈 목록을 보낸다 — «근거 없이 답했다»와 «근거를 못 실었다»가 화면에서
            같아 보이면 안 된다(render.sources_block 주석과 같은 이유).
  진행 표시 항상. 답변이 만들어지는 동안 «지금 무엇을 하고 있는지». 문구는 전부 코드가
            정한다(progress.py).

━━ 로그 ━━
행내 플랫폼은 컨테이너의 stdout 을 모아 Grafana 에 보여준다. uvicorn 은 제 로거만
설정하고 루트 로거에는 핸들러를 달지 않으므로, 여기서 설정하지 않으면 이 파일과
pension_agent 의 `log.info` 는 **어디에도 나가지 않는다** — 행내에서 보인 것이 접속 로그
(`POST /chat 200 OK`) 한 줄뿐이었던 이유다. 그래서 루트 로거를 stdout·INFO 로 잡고,
요청마다 짧은 id 를 붙여 «받음 → 진행 단계 → 완료 / 실패 / 연결 끊김»을 찍는다.
진행 단계 문구는 progress.emit 이 코드로 정한 것이라(LLM 문장이 아니다) 로그에도 그대로
싣는다(화면에는 progress 이벤트로 간다 — «출력 형식»).
에이전트 안에서 일어난 일(도구 실행 결과 · 연계 실행 결과 · 검증 게이트 · 판정)은 `[agent]`
로거의 «상태» 줄로 찍힌다 — observability.score() 가 Langfuse 활성 여부와 무관하게 남기고,
여기서 연 request_id 컨텍스트로 같은 요청 id 가 붙는다. 직원이 받는 답이 실패·축소로 바뀐
사실만 WARNING 이다(observability._state_level).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, List, Optional

import socket
import urllib.parse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from pension_agent import config, llm, mcp, observability
from pension_agent.consult_agent import context_store
from pension_agent.consult_agent import graph as consult_graph
from pension_agent.strategy_agent import briefing_store


def _setup_logging() -> None:
    """루트 로거 → stdout · INFO. 바깥(테스트 러너 등)이 이미 잡아 두었으면 손대지 않는다.

    타임스탬프는 넣지 않는다 — 플랫폼 수집기가 줄마다 붙인다(행내 화면에서 확인).
    형식은 uvicorn 의 접속 로그(`INFO:     …`)와 나란히 읽히게 맞춘다.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:     [%(name)s] %(message)s",
        stream=sys.stdout,
    )


_setup_logging()
log = logging.getLogger(__name__)

#: 요청 로그에 싣는 질문 미리보기 길이. 전문은 싣지 않는다 — 로그는 상담 내용의 저장소가 아니다.
QUESTION_PREVIEW = 60


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # 행내 MCP(쪽지 발송)를 이 프로세스에 붙인다 — 설정이 없으면 아무것도 하지 않고,
    # 그때 쪽지는 «미연결»로 답한다(보내지 않고 본문만 만든다 — pension_agent/mcp).
    # 여기서 붙이는 이유는 **붙는 시점이 한 곳이어야 하기 때문**이다: 첫 발송 때 붙이면
    # 그 요청 하나만 토큰 발급 왕복을 물고, 설정이 틀린 것도 그때서야 드러난다.
    mcp.install()
    # 이 컨테이너가 어떤 설정으로 떴는지 한 줄 — /health 와 같은 내용이다. «키를 넣었는데
    # 왜 안 되나 / train URL 을 보고 있나»를 Grafana 에서 로그 첫 줄로 끝내려고 둔다.
    log.info("기동 · %s", json.dumps(health(), ensure_ascii=False))
    yield


app = FastAPI(title="퇴직연금 AI 사후관리 에이전트", lifespan=_lifespan)


class ChatRequest(BaseModel):
    # 게이트웨이가 스키마 밖 키(stream 같은)를 실어 보내는지 봐야 한다 — 버리지 않고 남긴다.
    model_config = ConfigDict(extra="allow")

    input_value: str
    message_hists: Optional[List] = None


#: 스트림 프레이밍. 기본 SSE(`data: {...}\n\n`) — 머리말 «응답 프레이밍». 0 이면 문서의
#: «줄마다 JSON» 으로 되돌린다.
SSE_FRAMING = os.getenv("CHAT_SSE_FRAMING", "1").strip().lower() not in ("0", "false", "no")

#: 비스트림 신호로 보는 키 이름들(본문 최상위 · input_value 안 어느 쪽이든).
_STREAM_KEYS = ("stream", "isStream", "is_stream")


def _chunk(text: str) -> str:
    line = json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False)
    return f"data: {line}\n\n" if SSE_FRAMING else line + "\n"


def _event_json(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False)


def _event(event: dict[str, Any]) -> str:
    """이벤트 하나를 CHUNK 로 — content 가 JSON 객체 문자열이다(머리말 «출력 형식»)."""
    return _chunk(_event_json(event))


def _strip_followups(answer: str) -> str:
    """graph.ask 가 답변 끝에 붙인 추천질문 블록을 뗀다 — followups 이벤트로만 간다.

    붙이는 쪽(graph.ask)은 건드리지 않는다. 상담이력·`history` 도구가 되읽는 텍스트는 그대로다.
    """
    head, sep, _ = answer.rpartition("\n\n" + consult_graph.FOLLOWUP_HEADER + "\n")
    return head if sep else answer


#: action 이벤트에 싣는 pending_action 의 키. html·recipients·params 같은 실행 인자는
#: 화면이 알 필요가 없고(실행은 대화의 「네」가 한다), 쪽지 초안(title·text·to)은 미리보기용이다.
_ACTION_KEYS = ("kind", "label", "prompt", "title", "text", "to")


def _turn_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    """ask() 결과를 이벤트 목록으로 — answer → (action | clarify) → sources → followups → done."""
    events: list[dict[str, Any]] = [{
        "type": "answer",
        "text": _strip_followups(result.get("answer", "")),
        "intent": result.get("intent"),
    }]
    action = result.get("pending_action")
    if action:
        ev = {k: action[k] for k in _ACTION_KEYS if action.get(k) is not None}
        # act.py 와 같은 폴백 — 본문에 붙는 문장과 버튼 위 문장이 같아야 한다.
        ev.setdefault("prompt", f"{action.get('label')}, 연계해드릴까요? (네 / 아니오)")
        events.append({"type": "action", **ev})
    clarify = result.get("clarify")
    if clarify:
        events.append({"type": "clarify", "question": clarify.get("question"),
                       "options": list(clarify.get("options") or [])})
    events.append({"type": "sources", "items": list(result.get("sources") or [])})
    events.append({"type": "followups", "items": list(result.get("followups") or [])})
    events.append({"type": "done"})
    return events


def _error_events(exc: BaseException) -> list[dict[str, Any]]:
    # 실패도 이벤트로 나간다 — 클라이언트가 빈 응답을 받고 «답이 없다»로 오해하는 것보다,
    # 무엇이 깨졌는지 화면에서 읽는 편이 진단이 빠르다(LLMError 주석과 같은 취지).
    return [{"type": "error", "text": f"{type(exc).__name__}: {exc}"}, {"type": "done"}]


def _wants_stream(request: Request, req: ChatRequest, payload: dict[str, Any]) -> bool:
    """호출자가 비스트림을 원한다는 신호가 하나라도 있으면 거짓. 없으면 스트림(기본)."""
    for src in (req.model_extra or {}, payload):
        for key in _STREAM_KEYS:
            if key in src and src[key] in (False, 0, "false", "False", "0"):
                return False
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/event-stream" not in accept and "*/*" not in accept:
        return False
    return True


_SECRET_HINTS = ("token", "key", "auth", "cookie", "secret")


#: 거부된 요청의 input_value 를 로그에 얼마나 보여주나(글자 수). JSON 으로 풀리지 않은
#: 문자열이라 키를 뽑을 수 없으므로 앞부분을 그대로 남긴다 — 질문 미리보기(QUESTION_PREVIEW)와
#: 같은 수준의 노출이다.
INPUT_VALUE_PREVIEW = 120


def _request_shape(request: Request, req: ChatRequest, payload: Optional[dict[str, Any]]) -> str:
    """게이트웨이가 실제로 무엇을 보내는지 — 값이 아니라 **모양**만. 비밀 헤더는 값을 가린다.

    payload 가 None 이면 input_value 가 JSON 으로 풀리지 않은 요청이다 — 키 대신 원문의
    길이와 앞부분을 남긴다. 행내 실측(2026-09-10): 같은 세션 4턴째에 게이트웨이가
    «Expecting value: line 1 column 1» 인 input_value 를 보내 422 가 났는데, 그때 이 로그가
    거부 뒤에만 찍혀 무엇이 왔는지(빈 문자열인지 · 질문 원문인지) 알 수 없었다.
    """
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
    """422 — 접속 로그에는 상태코드만 남으므로 무엇이 빠졌는지는 여기서 찍는다."""
    log.warning("[%s] 요청 거부 422 · %s", rid, detail)
    return HTTPException(status_code=422, detail=detail)


def _parse(req: ChatRequest, rid: str = "-") -> dict[str, Any]:
    """input_value(JSON 문자열)를 풀고 필수 키를 확인한다. 어긋나면 422."""
    try:
        payload = json.loads(req.input_value)
    except json.JSONDecodeError as exc:
        raise _reject(rid, f"input_value 가 JSON 문자열이 아닙니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise _reject(rid, "input_value 는 JSON 객체를 직렬화한 문자열이어야 합니다.")

    x_client_user = payload.get("x_client_user")
    if not x_client_user:
        raise _reject(rid, "input_value 에 'x_client_user' 키가 필요합니다.")
    message = payload.get("message")
    if not message:
        raise _reject(rid, "input_value 에 'message' 키가 필요합니다.")

    # message_hists 는 플랫폼 스키마의 자리이고, 이 에이전트의 대화 맥락은 ask() 가
    # 돌려준 history(Turn 목록)다. Turn 형식(question 키)일 때만 넘긴다 — 플랫폼이 다른
    # 것을 실어 보내도 턴이 깨지지 않아야 한다(맥락이 없어지는 것과 500 이 나는 것은
    # 다르다). None 이면 진입점이 저장해 둔 맥락을 쓴다(머리말 «대화 맥락»).
    hists = req.message_hists
    history = hists if isinstance(hists, list) and hists and all(
        isinstance(h, dict) and "question" in h for h in hists) else None

    return {
        "question": str(message),
        "history": history,
        "customer_id": payload.get("customer_id") or None,
        "session_id": str(payload.get("session_id") or "default"),
        "x_client_user": str(x_client_user),
        # 사번을 따로 실어 보내는 게이트웨이·프론트를 위한 자리(머리말). 없으면 ask() 가
        # x_client_user 에서 «사번 꼴일 때만» 가져온다 — 여기서 판정하지 않는다.
        "employee_id": str(payload.get("employee_id") or "").strip() or None,
        "payload": payload,
    }


def _host_check() -> dict[str, Any]:
    """LLM_BASE_URL 의 호스트가 이 컨테이너에서 이름이 풀리는가.

    행내 첫 연결에서 실제로 걸린 자리다 — 인증도 쿼터도 아니고 DNS 였다
    (`URLError: [Errno -2] Name or service not known`). LLM Gateway 의 base_url 은
    `*.svc.cluster.local` 이라 **그 쿠버네티스 클러스터 안에서만** 풀리는데, 개발용
    컴퓨트 인스턴스는 그 밖이다. 그런데 실패는 첫 대화 턴에 가서야 «LLM 호출이
    실패했습니다»로 나타나 원인이 안 보인다.

    조회는 이름 해석까지만 한다(연결·인증은 하지 않는다) — /health 는 싸고 빨라야 하고,
    붙는지까지는 실제 턴이 답한다.
    """
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
    """기동 확인 + LLM 설정 진단.

    행내에서 처음 붙일 때 «키가 안 잡혔나 / 어디를 보고 있나 / 게이트 설정이 얼마인가»를
    로그 뒤지지 않고 한 번에 보려고 둔다. 키 값은 절대 내보내지 않는다 — 설정 여부만
    참/거짓으로 준다. 엔드포인트 호스트는 내보낸다(비밀이 아니고, 이것이 안 보이면
    «어느 주소를 보고 있는지»를 알 방법이 없다).
    """
    return {
        "status": "ok",
        # 어느 파일이 읽혔나 — «키를 넣었는데 왜 안 되나»의 첫 질문이다.
        # 자세한 것은 `python -m pension_agent.env` 가 터미널에 찍는다.
        "env": {"dotenv": str(config.DOTENV), "exists": config.DOTENV.is_file()},
        "llm": {
            "provider": llm.PROVIDER,
            # 어느 단계의 URL 을 읽었나 — train(…/trnn/…) 인지 serving(…/serv/…) 인지.
            # 배포된 컨테이너가 train URL 을 보고 있으면 여기서 바로 드러난다.
            "stage": llm.STAGE,
            "available": llm.available(),
            "base_url_set": bool(llm.BASE_URL),
            "api_key_set": bool(llm.API_KEY),
            "model": llm.MODEL or "(게이트웨이 기본 라우팅)",
            "timeout_sec": llm.TIMEOUT,
            **_host_check(),
        },
        # 미리 만들어 둔 브리핑을 **지금 실제로 읽고 있나.** 대화형은 브리핑이 이미 있다고
        # 보고 답하는데(고객 재료 도구가 `strategy_agent.propose()` 를 부른다), 그것을 이
        # 컨테이너가 직접 만들면 고객당 순차 LLM 11 회다. 미리 구워 넣었는지, 그게 지금
        # 지문으로 읽히는지, 런타임에 만든 것을 저장할 수 있는지 — 셋 다 어긋나도 답변은
        # 정상으로 나가고 «느리다»로만 보인다(briefing_store.stats 머리말).
        "briefing_cache": briefing_store.stats(),
        # 진행 중인 대화 맥락이 몇 세션 살아 있나(메모리 · 머리말 «대화 맥락»).
        "context_store": context_store.stats(),
        # 행내 MCP(쪽지 발송)가 붙었나. 안 붙었으면 무엇이 비어 있는지까지 말한다 —
        # 「보낸다고 했는데 왜 미연결이지」가 여기서 끝나야 한다. 키·토큰은 내보내지 않는다.
        "mcp": mcp.stats(),
        # 429 를 만났을 때 무엇을 조일지 바로 보이도록 게이트 설정을 함께 노출한다.
        "rate_gate": {
            "max_concurrency": llm.MAX_CONCURRENCY,
            "min_interval_sec": llm.MIN_INTERVAL,
            "retry_attempts": llm.RETRY_ATTEMPTS,
            "cooldown_sec": llm.COOLDOWN,
            # 사번 하나에 쿼터가 몰리지 않게 버킷을 나누고 있나(0=끔). 「.env 를 고쳤는데
            # 먹었나」가 여기서 끝나야 한다 — 안 먹은 것과 안 듣는 것은 처방이 정반대다.
            "client_user_spread": llm.CLIENT_USER_SPREAD,
        },
    }


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    rid = uuid.uuid4().hex[:8]   # 이 요청의 로그 줄을 한데 묶는 id
    try:
        args = _parse(req, rid)
    except HTTPException:
        # 거부된 요청도 모양을 남긴다 — 게이트웨이가 무엇을 보냈는지는 여기서만 보이고,
        # 422 사유 한 줄로는 «왜 JSON 이 아니었나»를 되짚을 수 없다(_request_shape 머리말).
        log.warning("[%s] 거부된 요청 모양 · %s", rid, _request_shape(request, req, None))
        raise
    started = time.monotonic()
    question = args["question"]
    x_client_user, session_id = args["x_client_user"], args["session_id"]
    streaming = _wants_stream(request, req, args["payload"])
    # 대화 맥락 — 호출자가 Turn 형식으로 실어 보낸 것이 우선, 없으면 저장해 둔 것.
    if args["history"] is not None:
        history, history_from = args["history"], "caller"
    else:
        history = context_store.get(x_client_user, session_id)
        history_from = "store" if history else "none"
    log.info(
        "[%s] 요청 · x_client_user=%s emp_no=%s customer_id=%s session_id=%s "
        "맥락=%d턴(%s) · 질문(%d자) %r",
        rid, x_client_user,
        # 이 턴의 쪽지가 누구 앞으로 · 누구 이름으로 나갈지가 여기서 정해진다. 값이 «-»
        # 이면 환경변수 폴백으로 떨어졌다는 뜻이고, 그건 여러 직원이 쓰는 배포에서
        # 남의 이름으로 나가는 상태다(docs/PRODUCTION_RISKS.md 10).
        consult_graph.employee_no(args["employee_id"], x_client_user) or "-",
        args["customer_id"], session_id,
        len(history or []), history_from, len(question),
        question[:QUESTION_PREVIEW] + ("…" if len(question) > QUESTION_PREVIEW else ""),
    )

    def _remember(result: dict[str, Any]) -> None:
        # 다음 턴이 이어받을 맥락. ask() 가 이미 HISTORY_LIMIT 으로 잘라 돌려준다.
        context_store.put(x_client_user, session_id, result.get("history"))

    def _log_done(result: dict[str, Any]) -> None:
        log.info("[%s] 완료 %.1f초 · intent=%s · 답변 %d자 · 출처 %d건 · 추천질문 %d건%s%s",
                 rid, time.monotonic() - started, result.get("intent"),
                 len(result.get("answer", "")), len(result.get("sources") or []),
                 len(result.get("followups") or []),
                 " · 연계 제안" if result.get("pending_action") else "",
                 " · 되묻기" if result.get("clarify") else "")
    # 게이트웨이가 무엇을 보내는지는 여기서만 보인다(머리말 «응답 프레이밍») — 모양만 찍는다.
    log.info("[%s] 요청 모양 · 응답=%s · %s", rid, "sse" if streaming else "json",
             _request_shape(request, req, args["payload"]))

    if not streaming:
        # 비스트림 — 게이트웨이가 본문 전체를 json.loads 한다. JSON 하나의 content 에 같은
        # 이벤트들을 줄바꿈으로 이어 싣는다(진행은 뺀다 — 기다리는 동안 보여줄 수 없다).
        def run_once() -> dict[str, Any]:
            def on_progress(text: str) -> None:
                log.info("[%s] 진행 %.1f초 · %s", rid, time.monotonic() - started, text)
            with observability.request_id(rid):
                return consult_graph.ask(
                    question, history,
                    customer_id=args["customer_id"], session_id=session_id,
                    x_client_user=x_client_user, employee_id=args["employee_id"],
                    on_progress=on_progress,
                )
        try:
            result = await asyncio.to_thread(run_once)
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] ask() 실패 %.1f초", rid, time.monotonic() - started)
            events = _error_events(exc)
        else:
            _remember(result)
            _log_done(result)
            events = _turn_events(result)
        return JSONResponse({"event": "CHUNK",
                             "content": "\n".join(_event_json(e) for e in events)})

    async def generate():
        loop = asyncio.get_running_loop()
        # 진행 표시는 답변을 만드는 **워커 스레드**에서 나오고, 흘리는 것은 이벤트 루프다.
        # 큐로 건네야 «기다리는 동안» 나간다 — 다 끝난 뒤 몰아서 주면 진행 표시가 아니다.
        lines: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def on_progress(text: str) -> None:
            # 로그에도 — Grafana 에서 «이 요청이 지금 어디까지 갔나»를 보는 자리다.
            log.info("[%s] 진행 %.1f초 · %s", rid, time.monotonic() - started, text)
            loop.call_soon_threadsafe(lines.put_nowait, text)

        def run() -> dict[str, Any]:
            try:
                with observability.request_id(rid):
                    return consult_graph.ask(
                        question, history,
                        customer_id=args["customer_id"], session_id=session_id,
                        x_client_user=x_client_user, employee_id=args["employee_id"],
                        on_progress=on_progress,
                    )
            finally:
                # 성공이든 실패든 반드시 닫는다 — 안 닫으면 아래 루프가 영원히 기다린다.
                loop.call_soon_threadsafe(lines.put_nowait, DONE)

        # ask() 는 동기 호출이고 그 안에서 LLM I/O 로 오래 막힌다. 이벤트 루프에서 직접
        # 부르면 이 워커가 다른 요청을 하나도 못 받는다. to_thread 는 컨텍스트를 복사해
        # 넘기므로 x-client-user 도 스레드 안까지 따라간다.
        task = asyncio.create_task(asyncio.to_thread(run))
        finished = False
        try:
            while True:
                item = await lines.get()
                if item is DONE:
                    break
                yield _event({"type": "progress", "text": item})

            try:
                result = await task
            except Exception as exc:  # noqa: BLE001
                # 스트리밍이 이미 시작돼 상태코드를 바꿀 수 없다 — 실패도 이벤트로 나간다.
                log.exception("[%s] ask() 실패 %.1f초", rid, time.monotonic() - started)
                for ev in _error_events(exc):
                    yield _event(ev)
                finished = True
                return

            _remember(result)
            for ev in _turn_events(result):
                yield _event(ev)
            finished = True
            _log_done(result)
        finally:
            if not finished:
                # 호출자가 다 받기 전에 끊었다(게이트웨이 타임아웃 등). 접속 로그에는
                # 200 으로만 남아 «답이 비었다»와 구분이 안 되므로 여기서 갈라 찍는다.
                log.warning("[%s] 응답을 다 보내기 전에 연결이 끊겼다 %.1f초",
                            rid, time.monotonic() - started)

    return StreamingResponse(generate(), media_type="text/event-stream")
