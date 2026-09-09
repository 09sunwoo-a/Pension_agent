"""배포된 에이전트를 행내 GenAI 플랫폼 게이트웨이를 통해 호출하는 최소 클라이언트.

에이전트(`src/`)와는 별개다 — `pension_agent` 를 임포트하지 않고, 설정도 공유하지 않는다.

    pip install -r client/requirements.txt
    python client/call_agent.py "IRP 수수료가 부담된다는데 뭐라고 답하죠?"

게이트웨이 규격(호출 측 파이프라인 예시에서 확인한 것):

    POST {ENDPOINT_URL}/openapi/agent-chat/v1/agent-messages
    headers  x-openapi-token: Bearer <토큰> · x-generative-ai-client: <클라이언트 ID>
    body     {"agentId": <assetId>, "contents": [<JSON 문자열>], "llmConfig": {}, "isStream": true}
    응답     SSE — "data: {...}" 줄, "data: [DONE]" 으로 끝난다. 각 JSON 의 content 를 이어 붙인다.
             "data:" 없이 JSON 줄만 오는 경우(에이전트의 CHUNK 줄 그대로)도 같은 방식으로 읽는다.
             content 를 하나도 못 찾으면 받은 원문을 stderr 에 찍는다 — 형태가 다를 때 그것을 보고 고친다.

`contents[0]` 은 에이전트의 `input_value` 로 그대로 전달된다고 본다. 그래서 그 안에는
에이전트(`src/main.py`)가 요구하는 키를 넣는다 — `message`(질문) · `x_client_user`(호출 직원).
이 가정이 틀리면 첫 호출이 422 로 돌아오고 본문에 «'x_client_user' 키가 필요합니다» 가
찍힌다 — 그 경우 아래 `_inner()` 의 형태를 게이트웨이가 실제로 넘기는 형태에 맞춘다.
"""

from __future__ import annotations

import json
import sys
from typing import Iterator

import requests

# ── 설정 — 여기만 채운다. 채운 채로 커밋하지 않는다. ──────────────────────────
ENDPOINT_URL = ""          # 콘솔이 준 호스트. /openapi/... 경로는 코드가 붙인다
OPENAPI_TOKEN = ""         # x-openapi-token. "Bearer " 접두는 코드가 붙인다
GENERATIVE_AI_CLIENT = ""  # x-generative-ai-client
ASSET_ID = ""              # agentId

X_CLIENT_USER = "test-user"  # 호출 직원 식별자 — 에이전트의 감사 기록·쿼터 버킷
VERIFY_TLS = False           # 행내 게이트웨이는 사설 인증서라 참고 파이프라인도 끄고 있다
TIMEOUT = 180                # 초. 한 턴이 LLM 호출 여러 번이라 길게 잡는다
DEBUG_LINES = 40             # content 를 못 찾았을 때 stderr 에 보여줄 원문 줄 수

# ── 진단 스위치 — 답변이 비어서 올 때 하나씩 바꿔 본다 ──
IS_STREAM = True             # False 면 응답 JSON 하나를 받아 content 를 읽고, 없으면 전체를 찍는다
INNER_SHAPE = "agent"        # "agent": {"message", "x_client_user"} — src/main.py 규약
                             # "reference": 참고 파이프라인 형태 {"filtered_body": {...}, "file_objects": []}
                             #   게이트웨이가 contents[0] 를 그대로 넘기지 않고 이 형태를 기대할 때 확인용
# ──────────────────────────────────────────────────────────────────────────────

PATH = "/openapi/agent-chat/v1/agent-messages"

if not VERIFY_TLS:
    # VERIFY_TLS=False 는 의도한 설정이라 매 호출 InsecureRequestWarning 을 찍지 않는다.
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-openapi-token": f"Bearer {OPENAPI_TOKEN}",
        "x-generative-ai-client": GENERATIVE_AI_CLIENT,
    }


def _inner(question: str, x_client_user: str) -> dict:
    """contents[0] 에 실을 객체.

    "agent"     — 에이전트의 input_value 가 되는 객체. src/main.py 가 읽는 키.
    "reference" — 참고 파이프라인이 보내던 형태. 게이트웨이가 이 형태에서 message 를 뽑아
                  input_value 를 만드는지 확인할 때만 쓴다.
    """
    if INNER_SHAPE == "reference":
        return {
            "filtered_body": {
                "messages": [{"role": "user", "content": question}],
                "stream": IS_STREAM,
                "user_id": x_client_user,
            },
            "file_objects": [],
        }
    return {"message": question, "x_client_user": x_client_user}


def _payload(question: str, x_client_user: str) -> dict:
    return {
        "agentId": ASSET_ID,
        "contents": [json.dumps(_inner(question, x_client_user), ensure_ascii=False)],
        "llmConfig": {},
        "isStream": IS_STREAM,
    }


def _dump_raw(resp: requests.Response, raw: list[str]) -> None:
    print("[content 를 찾지 못했습니다] 게이트웨이가 보낸 원문:", file=sys.stderr)
    print(f"  status={resp.status_code} content-type={resp.headers.get('Content-Type')}",
          file=sys.stderr)
    for line in raw[:DEBUG_LINES]:
        print(f"  {line}", file=sys.stderr)
    if len(raw) > DEBUG_LINES:
        print(f"  … 외 {len(raw) - DEBUG_LINES}줄", file=sys.stderr)
    if not raw:
        print("  (본문이 비어 있음)", file=sys.stderr)


def stream(question: str, x_client_user: str = X_CLIENT_USER) -> Iterator[str]:
    """답변 조각을 오는 순서대로 낸다. 200 이 아니면 응답 본문을 찍고 예외를 올린다."""
    with requests.post(
        ENDPOINT_URL.rstrip("/") + PATH,
        headers=_headers(),
        json=_payload(question, x_client_user),
        stream=IS_STREAM,
        verify=VERIFY_TLS,
        timeout=TIMEOUT,
    ) as resp:
        if resp.status_code != 200:
            print(f"[HTTP {resp.status_code}] {resp.text}", file=sys.stderr)
            resp.raise_for_status()
        # Content-Type 에 charset 이 없으면 requests 는 text/* 를 ISO-8859-1 로 풀어 한글이
        # 깨진다. 플랫폼 응답은 UTF-8 이므로 여기서 고정한다.
        resp.encoding = "utf-8"
        if not IS_STREAM:
            # 응답 JSON 하나. content 가 있으면 그것, 없으면 전체를 찍어 무엇이 채워졌는지 본다
            # (response_code · filter_block_reason · truncated 같은 필드가 단서다).
            text = resp.text
            try:
                content = json.loads(text).get("content")
            except (json.JSONDecodeError, AttributeError):
                content = None
            if isinstance(content, str) and content:
                yield content
            else:
                _dump_raw(resp, text.splitlines())
            return
        raw: list[str] = []   # content 를 하나도 못 찾았을 때 «무엇이 왔는지» 보여주려고 모은다
        found = False
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            raw.append(line)
            # SSE("data: {...}") 든, 에이전트의 CHUNK 줄("{...}") 이 그대로 오든 같이 읽는다.
            data = line[len("data:"):].strip() if line.startswith("data:") else line.strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            content = obj.get("content") if isinstance(obj, dict) else None
            if isinstance(content, str) and content:
                found = True
                yield content
        if not found:
            _dump_raw(resp, raw)


def ask(question: str, x_client_user: str = X_CLIENT_USER) -> str:
    """한 턴을 돌려 전체 답변 문자열을 돌려준다."""
    return "".join(stream(question, x_client_user))


def main(argv: list[str]) -> int:
    missing = [n for n in ("ENDPOINT_URL", "OPENAPI_TOKEN", "GENERATIVE_AI_CLIENT", "ASSET_ID")
               if not globals()[n]]
    if missing:
        print(f"파일 상단 설정이 비어 있습니다: {', '.join(missing)}", file=sys.stderr)
        return 2
    if len(argv) < 2:
        print(f"사용법: python {argv[0]} \"질문\"", file=sys.stderr)
        return 2
    for piece in stream(" ".join(argv[1:])):
        print(piece, end="", flush=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
