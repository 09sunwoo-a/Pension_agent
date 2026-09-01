"""행내 GenAI 플랫폼용 HTTP 진입점 — FastAPI.

플랫폼이 요구하는 I/O 스키마는 **고정**이라 여기서 임의로 바꾸지 않는다
(refs/genai-platform.md «API I/O 스키마 (고정)»):

    POST /chat   {"input_value": "<JSON 문자열>", "message_hists": null}
      → text/event-stream, 줄마다 {"event": "CHUNK", "content": "..."}

`input_value` 는 **JSON 을 문자열로 직렬화한 것**이다. 이 프로젝트가 그 안에서 읽는 키:

    message        (필수) 직원이 입력한 질문
    x_client_user  (필수) 호출한 직원 식별자. 플랫폼의 감사 기록이자 쿼터 버킷이다
    customer_id    (선택) 지금 열려 있는 브리핑 화면의 고객 id. 고객 관련 기능은
                          이것이 있어야 성립한다 — 없으면 에이전트가 그렇게 답한다
    session_id     (선택) 상담 세션 구분자. 없으면 "default"

이 파일은 **얇다.** 판단·검증·문장 생성은 전부 consult_agent 안에서 끝나고, 여기서는
파싱·스트리밍·오류 형태만 맡는다. 화면(Streamlit app.py)과 이 API 는 같은 `ask()` 하나를
부른다 — 두 경로가 갈리면 «화면에서는 되는데 API 에서는 다르게 나오는» 자리가 생긴다.

━━ 왜 진행 표시를 흘리지 않는가 ━━
consult_agent 는 답변이 만들어지는 동안 "지금 무엇을 하고 있는지"를 한 줄씩 흘릴 수
있다(progress.py). 그런데 플랫폼 스키마의 이벤트는 CHUNK 한 종류뿐이고, 소비자는 CHUNK
의 content 를 이어 붙여 답변으로 삼는다 — 거기에 진행 문구를 섞으면 그게 답변의 일부가
된다. 그래서 이 경로는 **답변만** 내보낸다(줄 단위로 쪼개 흘린다). 진행 표시는 콜백을
직접 받을 수 있는 화면(app.py)의 몫이다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, List, Optional

import socket
import urllib.parse

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pension_agent import llm
from pension_agent.consult_agent import graph as consult_graph

log = logging.getLogger(__name__)

app = FastAPI(title="퇴직연금 AI 사후관리 에이전트")


class ChatRequest(BaseModel):
    input_value: str
    message_hists: Optional[List] = None


def _chunk(text: str) -> str:
    return json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False) + "\n"


def _parse(req: ChatRequest) -> dict[str, Any]:
    """input_value(JSON 문자열)를 풀고 필수 키를 확인한다. 어긋나면 422."""
    try:
        payload = json.loads(req.input_value)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"input_value 가 JSON 문자열이 아닙니다: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422, detail="input_value 는 JSON 객체를 직렬화한 문자열이어야 합니다.")

    x_client_user = payload.get("x_client_user")
    if not x_client_user:
        raise HTTPException(
            status_code=422, detail="input_value 에 'x_client_user' 키가 필요합니다.")
    message = payload.get("message")
    if not message:
        raise HTTPException(
            status_code=422, detail="input_value 에 'message' 키가 필요합니다.")

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
        "llm": {
            "provider": llm.PROVIDER,
            "available": llm.available(),
            "base_url_set": bool(llm.BASE_URL),
            "api_key_set": bool(llm.API_KEY),
            "model": llm.MODEL or "(게이트웨이 기본 라우팅)",
            "timeout_sec": llm.TIMEOUT,
            **_host_check(),
        },
        # 429 를 만났을 때 무엇을 조일지 바로 보이도록 게이트 설정을 함께 노출한다.
        "rate_gate": {
            "max_concurrency": llm.MAX_CONCURRENCY,
            "min_interval_sec": llm.MIN_INTERVAL,
            "retry_attempts": llm.RETRY_ATTEMPTS,
            "cooldown_sec": llm.COOLDOWN,
        },
    }


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    args = _parse(req)

    async def generate():
        try:
            # ask() 는 동기 호출이고 그 안에서 LLM I/O 로 오래 막힌다. 이벤트 루프에서
            # 직접 부르면 이 워커가 다른 요청을 하나도 못 받는다. to_thread 는 컨텍스트를
            # 복사해 넘기므로 x-client-user 도 스레드 안까지 따라간다.
            result = await asyncio.to_thread(
                consult_graph.ask,
                args["question"], args["history"],
                customer_id=args["customer_id"], session_id=args["session_id"],
                x_client_user=args["x_client_user"],
            )
        except Exception as exc:  # noqa: BLE001
            # 스트리밍이 이미 시작돼 상태코드를 바꿀 수 없다. 그래서 실패도 CHUNK 로
            # 나간다 — 클라이언트가 빈 응답을 받고 «답이 없다»로 오해하는 것보다,
            # 무엇이 깨졌는지 화면에서 읽는 편이 진단이 빠르다(LLMError 주석과 같은 취지).
            log.exception("ask() 실패")
            yield _chunk(f"[오류] {type(exc).__name__}: {exc}")
            return

        answer = result.get("answer", "")
        # 답변은 토큰 단위로 흘릴 수 없다 — compose 의 생성문은 검증 게이트에서 통째로
        # 폐기될 수 있어서, 흘려보낸 뒤 사라지면 «근거 밖 수치를 내보내지 않는다»는 보증이
        # 화면에서 뒤집힌다(progress.py). 검증을 통과한 완성본을 줄 단위로 나눠 보낸다.
        for line in answer.splitlines(keepends=True):
            yield _chunk(line)

    return StreamingResponse(generate(), media_type="text/event-stream")
