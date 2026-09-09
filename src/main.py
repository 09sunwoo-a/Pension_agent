"""행내 GenAI 플랫폼용 HTTP 진입점 — FastAPI.

플랫폼이 요구하는 I/O 스키마는 **고정**이라 여기서 임의로 바꾸지 않는다
(skills/genai-platform-agent-dev/refs/genai-platform.md «API I/O 스키마 (고정)»):

    POST /chat   {"input_value": "<JSON 문자열>", "message_hists": null}
      → text/event-stream, 줄마다 {"event": "CHUNK", "content": "..."}

`input_value` 는 **JSON 을 문자열로 직렬화한 것**이다. 이 프로젝트가 그 안에서 읽는 키:

    message         (필수) 직원이 입력한 질문
    x_client_user   (필수) 호출한 직원 식별자. 플랫폼의 감사 기록이자 쿼터 버킷이다
    customer_id     (선택) 지금 열려 있는 브리핑 화면의 고객 id. 고객 관련 기능은
                           이것이 있어야 성립한다 — 없으면 에이전트가 그렇게 답한다
    session_id      (선택) 상담 세션 구분자. 없으면 "default"
    stream_progress (선택) 답변을 기다리는 동안 «지금 무엇을 하고 있는지»를 함께 흘린다.
                           기본 거짓 — 아래 참고

이 파일은 **얇다.** 판단·검증·문장 생성은 전부 consult_agent 안에서 끝나고, 여기서는
파싱·스트리밍·오류 형태만 맡는다. 화면(Streamlit app.py)과 이 API 는 같은 `ask()` 하나를
부른다 — 두 경로가 갈리면 «화면에서는 되는데 API 에서는 다르게 나오는» 자리가 생긴다.

━━ 무엇을 흘리는가 ━━
플랫폼 스키마의 이벤트는 CHUNK 한 종류뿐이고, 소비자는 content 를 이어 붙여 답변으로
삼는다. 그래서 무엇을 싣느냐가 곧 "답변에 무엇이 남느냐"다.

  답변      항상. 줄 단위로 쪼개 흘린다.
  출처      **항상.** 이 에이전트의 답은 «근거 안에서만» 나오고, 그 근거를 보여주는 것이
            존재 이유다(루트 CLAUDE.md §2). 출처 없는 답변은 이 시스템의 산출물이 아니다.
            추천질문이 이미 같은 방식으로 답변 끝에 붙는다(graph.ask) — 같은 규약이다.
  진행 표시 **요청이 켤 때만**(stream_progress). 이건 답변이 아니라 «기다리는 동안의
            화면»이라, 이어 붙였을 때 답변의 일부가 되면 안 된다. 사람이 터미널에서 보는
            테스트(test_local.sh)에서는 켜고, 플랫폼 UI 가 부르는 기본 호출에서는 끈다.

답변 자체를 토큰 단위로 흘리지 않는 이유는 따로 있다 — compose 의 생성문은 검증 게이트
(verify_texts · relations · 원문 스팬)에서 **통째로 폐기**될 수 있어서, 토큰을 흘려보내면
직원이 이미 읽은 문장이 사라진다. "근거 밖 수치를 내보내지 않는다"는 보증이 화면에서
뒤집히는 것이다(progress.py 주석).

━━ 로그 ━━
행내 플랫폼은 컨테이너의 stdout 을 모아 Grafana 에 보여준다. uvicorn 은 제 로거만
설정하고 루트 로거에는 핸들러를 달지 않으므로, 여기서 설정하지 않으면 이 파일과
pension_agent 의 `log.info` 는 **어디에도 나가지 않는다** — 행내에서 보인 것이 접속 로그
(`POST /chat 200 OK`) 한 줄뿐이었던 이유다. 그래서 루트 로거를 stdout·INFO 로 잡고,
요청마다 짧은 id 를 붙여 «받음 → 진행 단계 → 완료 / 실패 / 연결 끊김»을 찍는다.
진행 단계 문구는 progress.emit 이 코드로 정한 것이라(LLM 문장이 아니다) 로그에 그대로
싣는다. 화면에는 종전대로 stream_progress 를 켤 때만 흐른다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, List, Optional

import socket
import urllib.parse

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pension_agent import config, llm
from pension_agent.consult_agent import graph as consult_graph
from pension_agent.consult_agent import render
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
    # 이 컨테이너가 어떤 설정으로 떴는지 한 줄 — /health 와 같은 내용이다. «키를 넣었는데
    # 왜 안 되나 / train URL 을 보고 있나»를 Grafana 에서 로그 첫 줄로 끝내려고 둔다.
    log.info("기동 · %s", json.dumps(health(), ensure_ascii=False))
    yield


app = FastAPI(title="퇴직연금 AI 사후관리 에이전트", lifespan=_lifespan)


class ChatRequest(BaseModel):
    input_value: str
    message_hists: Optional[List] = None


def _chunk(text: str) -> str:
    return json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False) + "\n"


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
    # 돌려준 history(Turn 목록)다. 형태가 맞을 때만 넘긴다 — 플랫폼이 다른 것을 실어
    # 보내도 턴이 깨지지 않아야 한다(맥락이 없어지는 것과 500 이 나는 것은 다르다).
    hists = req.message_hists
    history = hists if isinstance(hists, list) and all(
        isinstance(h, dict) for h in hists) else None

    return {
        "question": str(message),
        "history": history,
        "customer_id": payload.get("customer_id") or None,
        "session_id": str(payload.get("session_id") or "default"),
        "x_client_user": str(x_client_user),
        "stream_progress": bool(payload.get("stream_progress")),
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
async def chat(req: ChatRequest) -> StreamingResponse:
    rid = uuid.uuid4().hex[:8]   # 이 요청의 로그 줄을 한데 묶는 id
    args = _parse(req, rid)
    started = time.monotonic()
    question = args["question"]
    log.info(
        "[%s] 요청 · x_client_user=%s customer_id=%s session_id=%s stream_progress=%s "
        "history=%s · 질문(%d자) %r",
        rid, args["x_client_user"], args["customer_id"], args["session_id"],
        args["stream_progress"], len(args["history"] or []), len(question),
        question[:QUESTION_PREVIEW] + ("…" if len(question) > QUESTION_PREVIEW else ""),
    )

    async def generate():
        loop = asyncio.get_running_loop()
        # 진행 표시는 답변을 만드는 **워커 스레드**에서 나오고, 흘리는 것은 이벤트 루프다.
        # 큐로 건네야 «기다리는 동안» 나간다 — 다 끝난 뒤 몰아서 주면 진행 표시가 아니다.
        lines: asyncio.Queue = asyncio.Queue()
        DONE = object()
        show_progress = args["stream_progress"]

        def on_progress(text: str) -> None:
            # 로그에는 항상 — Grafana 에서 «이 요청이 지금 어디까지 갔나»를 보는 자리다.
            # 화면에는 요청이 켰을 때만(위 머리말).
            log.info("[%s] 진행 %.1f초 · %s", rid, time.monotonic() - started, text)
            if show_progress:
                loop.call_soon_threadsafe(lines.put_nowait, text)

        def run() -> dict[str, Any]:
            try:
                return consult_graph.ask(
                    question, args["history"],
                    customer_id=args["customer_id"], session_id=args["session_id"],
                    x_client_user=args["x_client_user"],
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
                yield _chunk(f"⋯ {item}\n")

            try:
                result = await task
            except Exception as exc:  # noqa: BLE001
                # 스트리밍이 이미 시작돼 상태코드를 바꿀 수 없다. 그래서 실패도 CHUNK 로
                # 나간다 — 클라이언트가 빈 응답을 받고 «답이 없다»로 오해하는 것보다,
                # 무엇이 깨졌는지 화면에서 읽는 편이 진단이 빠르다(LLMError 주석과 같은 취지).
                log.exception("[%s] ask() 실패 %.1f초", rid, time.monotonic() - started)
                yield _chunk(f"[오류] {type(exc).__name__}: {exc}")
                finished = True
                return

            answer = result.get("answer", "")
            sources = result.get("sources") or []
            for line in answer.splitlines(keepends=True):
                yield _chunk(line)
            # 출처는 답변의 일부다 — 근거를 못 보여주면 이 에이전트의 답이 아니다(위 주석).
            yield _chunk("\n" + render.sources_block(sources) + "\n")
            finished = True
            log.info("[%s] 완료 %.1f초 · intent=%s · 답변 %d자 · 출처 %d건",
                     rid, time.monotonic() - started, result.get("intent"),
                     len(answer), len(sources))
        finally:
            if not finished:
                # 호출자가 다 받기 전에 끊었다(게이트웨이 타임아웃 등). 접속 로그에는
                # 200 으로만 남아 «답이 비었다»와 구분이 안 되므로 여기서 갈라 찍는다.
                log.warning("[%s] 응답을 다 보내기 전에 연결이 끊겼다 %.1f초",
                            rid, time.monotonic() - started)

    return StreamingResponse(generate(), media_type="text/event-stream")
