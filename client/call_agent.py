"""배포된 에이전트를 행내 GenAI 플랫폼 게이트웨이를 통해 호출하는 최소 클라이언트.

에이전트(`src/`)와는 별개다 — `pension_agent` 를 임포트하지 않고, 설정도 공유하지 않는다.

    pip install -r client/requirements.txt
    python client/call_agent.py "IRP 수수료가 부담된다는데 뭐라고 답하죠?"   # 한 턴
    python client/call_agent.py                                             # 대화형 — 여러 턴, 한 세션

이 클라이언트는 **«열려 있는 고객 화면을 아는 프론트»의 자리**를 흉내 낸다 — 실서비스 프론트가
할 일과 같다: 열린 화면의 고객 id(CUSTOMER_ID)와 상담 세션 id 를 질문과 함께 싣는다. 고객을
문장에서 알아내는 장치는 없다(에이전트 설계 — state.Turn 주석 «호출자가 넘김»).

게이트웨이 규격(호출 측 파이프라인 예시 + 행내 실측):

    POST {ENDPOINT_URL}/openapi/agent-chat/v1/agent-messages
    headers  x-openapi-token: Bearer <토큰> · x-generative-ai-client: <클라이언트 ID>
    body     {"agentId": <assetId>, "contents": [<JSON 문자열>], "llmConfig": {}, "isStream": bool}
    응답     isStream=true : SSE — "data: {...}" 줄, "data: [DONE]" 으로 끝난다. **기본.**
                             "data:" 없이 JSON 줄만 오는 경우도 같은 방식으로 읽는다.
             isStream=false: JSON 하나.
             어느 쪽이든 각 content 는 **에이전트 이벤트(JSON 객체)** 다 — src/main.py 머리말 «출력 형식»:
               progress(진행 문구) → answer(본문·intent) → [action | clarify] → sources → followups → done
             `_events_in` 이 content 에서 이벤트를 꺼내고 `_render` 가 type 별로 그린다. 프론트가 할 일과 같다.
             content 를 하나도 못 찾으면 받은 원문을 stderr 에 찍는다 — 형태가 다를 때 그것을 보고 고친다.

`contents[0]` 은 에이전트의 `input_value` 로 그대로 전달된다(행내 실측 — 에이전트가 200 을 내고
답변을 만들었다). 그 안에는 에이전트(`src/main.py`)가 읽는 키를 넣는다 — `message`(질문) ·
`x_client_user`(호출 직원) · `customer_id`(열린 고객, 선택) · `session_id`(상담 세션).
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Iterator

import requests

try:
    # 대화형 input() 이 readline 을 쓰게 한다. 없으면 터미널의 줄 편집에 맡겨지는데, 한글
    # 한 글자(UTF-8 3바이트)를 지울 때 1바이트만 지우거나 IME 가 조합 중 자모를 보냈다
    # 지우는 터미널에서는 남은 바이트가 대리 문자(U+DCxx)로 들어온다 — 아래 _check_utf8.
    import readline  # noqa: F401
except ImportError:
    pass

# ── 설정 — 여기만 채운다. 채운 채로 커밋하지 않는다. ──────────────────────────
ENDPOINT_URL = ""          # 콘솔이 준 호스트. /openapi/... 경로는 코드가 붙인다
OPENAPI_TOKEN = ""         # x-openapi-token. "Bearer " 접두는 코드가 붙인다
GENERATIVE_AI_CLIENT = ""  # x-generative-ai-client
ASSET_ID = ""              # agentId

X_CLIENT_USER = "test-user"  # 호출 직원 식별자 — 에이전트의 감사 기록·쿼터 버킷
CUSTOMER_ID = ""             # 지금 열려 있는 브리핑 화면의 고객 id(예: 198734-1205842). 비우면
                             # 고객 없이 호출 — 지식 질의응답·화법은 답하고 고객 질문에는
                             # 「고객 화면을 먼저 열어달라」고 답한다. 실서비스 프론트가 이 자리다
SESSION_ID = ""              # 비우면 실행마다 새로 만든다(실행 한 번 = 상담 한 번). 채우면 그
                             # 세션을 이어간다 — 에이전트가 이 값으로 이전 턴의 맥락을 되찾는다
VERIFY_TLS = False           # 행내 게이트웨이는 사설 인증서라 참고 파이프라인도 끄고 있다
TIMEOUT = 180                # 초. 한 턴이 LLM 호출 여러 번이라 길게 잡는다
DEBUG_LINES = 40             # content 를 못 찾았을 때 stderr 에 보여줄 원문 줄 수

# ── 호출 방식 ──
# 행내 실측(2026-09-09): 에이전트가 «줄마다 JSON» 으로 답하던 동안 isStream=True 는 빈
# content 하나, isStream=False 는 status ERROR(R40000) 에 «[Errno Extra data] {에이전트 원문}»
# 이 왔다 — 게이트웨이는 스트림을 SSE 로, 비스트림을 JSON 하나로 읽는다. 에이전트(src/main.py)
# 를 거기에 맞췄으므로 기본은 스트림이다. 게이트웨이 status 가 SUCCESS 가 아니면 stderr 에
# 찍는다 — 오류 문구 안에 답변처럼 보이는 글이 있어도 답변이 아니다.
IS_STREAM = True             # True 면 SSE 로 받는다. False 면 응답 JSON 하나에서 content 를 읽는다
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


def _inner(question: str, x_client_user: str, customer_id: str = "", session_id: str = "default") -> dict:
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
    inner = {"message": question, "x_client_user": x_client_user, "session_id": session_id}
    if customer_id:
        inner["customer_id"] = customer_id
    return inner


def _payload(question: str, x_client_user: str, customer_id: str = "", session_id: str = "default") -> dict:
    return {
        "agentId": ASSET_ID,
        "contents": [json.dumps(_inner(question, x_client_user, customer_id, session_id), ensure_ascii=False)],
        "llmConfig": {},
        "isStream": IS_STREAM,
    }


_DECODER = json.JSONDecoder()


def _events_in(text: str) -> Iterator[dict]:
    """게이트웨이 content 문자열에서 에이전트 이벤트(JSON 객체, `type` 키)를 차례로 꺼낸다.

    프론트가 그대로 따라 하면 되는 참조 구현이다(src/main.py 머리말 «출력 형식»).
      · content 하나에 이벤트가 하나인 것이 정상이지만, 게이트웨이가 여러 개를 합쳐 보내지
        않는다는 확인이 없어 객체를 **연달아** 읽는다(`raw_decode` 반복).
      · 게이트웨이 오류 문구 안에 에이전트의 CHUNK 원문이 통째로 실려 오는 경우가 있었다
        (행내 실측 — «[Errno Extra data] {"event": "CHUNK", "content": …}»). CHUNK 객체를
        만나면 그 content 안을 다시 읽는다.
      · 이벤트를 하나도 못 찾으면 원문을 {"type": "raw"} 로 넘긴다 — 무엇이 왔는지 보이게.
    """
    found = False
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        i = end
        if not isinstance(obj, dict):
            continue
        if obj.get("event") == "CHUNK" and isinstance(obj.get("content"), str):
            for inner in _events_in(obj["content"]):
                if inner.get("type") != "raw":
                    found = True
                    yield inner
        elif "type" in obj:
            found = True
            yield obj
    if not found and text.strip():
        yield {"type": "raw", "text": text}


def _note_status(obj: dict) -> None:
    """게이트웨이 이벤트의 status 가 SUCCESS 가 아니면 알린다 — content 는 그때 오류 문구다."""
    status = obj.get("status")
    if status and status != "SUCCESS":
        code = obj.get("responseCode") or obj.get("response_code")
        print(f"[게이트웨이 status={status} responseCode={code}] content 는 답변이 아니라 오류 문구입니다.",
              file=sys.stderr)


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


def _check_utf8(question: str) -> None:
    """질문이 UTF-8 로 인코딩되는지 — 아니면 보내지 않는다.

    행내 실측(2026-09-10): 같은 «IRP 세액공제 얼마지» 가 인자로는 정상, 대화형 타이핑으로는
    게이트웨이 500(에이전트 422 «input_value 가 JSON 이 아님»)이었다. 터미널이 한글 지우기를
    바이트 단위로 처리해 남은 바이트가 대리 문자(U+DCxx)로 들어왔고, JSON 으로는 ASCII
    이스케이프라 전송은 되지만 게이트웨이가 UTF-8 로 다시 인코딩하지 못해 깨진 input_value 를
    에이전트에 넘긴 것이다. 여기서 막아야 원인이 클라이언트 쪽에서 바로 보인다.
    """
    try:
        question.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"질문 {exc.start}번째 글자가 UTF-8 이 아닙니다({question!r}). 터미널이 한글을 지울 때 "
            f"바이트 단위로 지운 흔적입니다 — 다시 입력하거나 인자로 넘기세요: "
            f'python call_agent.py "질문"'
        ) from exc


def events(question: str, x_client_user: str = X_CLIENT_USER, *,
           customer_id: str = "", session_id: str = "default") -> Iterator[dict]:
    """한 턴의 이벤트를 오는 순서대로 낸다(progress… → answer → sources → followups → done).
    200 이 아니면 응답 본문·헤더를 찍고 예외를 올린다."""
    _check_utf8(question)
    with requests.post(
        ENDPOINT_URL.rstrip("/") + PATH,
        headers=_headers(),
        json=_payload(question, x_client_user, customer_id, session_id),
        stream=IS_STREAM,
        verify=VERIFY_TLS,
        timeout=TIMEOUT,
    ) as resp:
        if resp.status_code != 200:
            print(f"[HTTP {resp.status_code}] {resp.text}", file=sys.stderr)
            # 게이트웨이가 붙인 추적 id(x-request-id 류)가 헤더에 있으면 플랫폼팀이 그 요청을
            # 찾을 수 있다. 쿠키는 찍지 않는다.
            headers = {k: v for k, v in resp.headers.items() if k.lower() != "set-cookie"}
            print(f"[응답 헤더] {json.dumps(headers, ensure_ascii=False)}", file=sys.stderr)
            resp.raise_for_status()
        # Content-Type 에 charset 이 없으면 requests 는 text/* 를 ISO-8859-1 로 풀어 한글이
        # 깨진다. 플랫폼 응답은 UTF-8 이므로 여기서 고정한다.
        resp.encoding = "utf-8"
        if not IS_STREAM:
            # 응답 JSON 하나. content 가 있으면 그것, 없으면 전체를 찍어 무엇이 채워졌는지 본다
            # (response_code · filter_block_reason · truncated 같은 필드가 단서다).
            text = resp.text
            try:
                obj = json.loads(text)
                _note_status(obj)
                content = obj.get("content")
            except (json.JSONDecodeError, AttributeError):
                content = None
            if isinstance(content, str) and content:
                yield from _events_in(content)
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
            if not isinstance(obj, dict):
                continue
            _note_status(obj)
            content = obj.get("content")
            if isinstance(content, str) and content:
                found = True
                yield from _events_in(content)
        if not found:
            _dump_raw(resp, raw)


def ask(question: str, x_client_user: str = X_CLIENT_USER, *,
        customer_id: str = "", session_id: str = "default") -> dict:
    """한 턴을 돌려 이벤트를 종류별로 모은 dict 를 돌려준다.

        {"answer": str, "intent": str|None, "sources": [..], "followups": [..],
         "action": dict|None, "clarify": dict|None, "error": str|None, "progress": [..]}
    """
    out: dict = {"answer": "", "intent": None, "sources": [], "followups": [],
                 "action": None, "clarify": None, "error": None, "progress": [], "raw": []}
    for ev in events(question, x_client_user, customer_id=customer_id, session_id=session_id):
        t = ev.get("type")
        if t == "progress":
            out["progress"].append(ev.get("text", ""))
        elif t == "answer":
            out["answer"] += ev.get("text", "")
            out["intent"] = ev.get("intent")
        elif t == "sources":
            out["sources"] = list(ev.get("items") or [])
        elif t == "followups":
            out["followups"] = list(ev.get("items") or [])
        elif t == "action":
            out["action"] = {k: v for k, v in ev.items() if k != "type"}
        elif t == "clarify":
            out["clarify"] = {k: v for k, v in ev.items() if k != "type"}
        elif t == "error":
            out["error"] = ev.get("text")
        elif t == "raw":
            out["raw"].append(ev.get("text", ""))
    return out


def _render(ev: dict) -> None:
    """이벤트 하나를 터미널에 그린다 — 프론트가 type 별로 자리를 정하는 것과 같은 일이다."""
    t = ev.get("type")
    if t == "progress":
        print(f"  ⋯ {ev.get('text')}", file=sys.stderr, flush=True)
    elif t == "answer":
        print(ev.get("text", ""), flush=True)
    elif t == "action":
        # 본문 끝에 제안 문장이 이미 있다. 여기서는 «버튼 자리»만 알린다 — 다음 턴에 네/아니오.
        print(f"  [연계 제안 · {ev.get('label')}] — 다음 질문에 «네» 또는 «아니오»로 답합니다", flush=True)
    elif t == "clarify":
        print("  [되묻기 선택지] " + " / ".join(ev.get("options") or []), flush=True)
    elif t == "sources":
        items = ev.get("items") or []
        ground = [s for s in items if s.get("role", "근거") == "근거"]
        caution = [s for s in items if s.get("role") == "주의"]
        print("\n─ 근거" + ("" if ground else ": 없음"))
        for s in ground:
            score = f" · 관련도 {s['score']}" if s.get("score") is not None else ""
            print(f"  · {s.get('doc') or ''} — {s.get('title') or ''} [{s.get('id')}{score}]")
        if caution:
            print("\n─ 이 고객 상담에서 지켜야 할 것 (근거 카드)")
            for s in caution:
                print(f"  · {s.get('doc') or ''} — {s.get('title') or ''} [{s.get('id')}]")
    elif t == "followups":
        if ev.get("items"):
            print("\n── 이어서 물어보실 수 있어요")
            for q in ev["items"]:
                print(f"  · {q}")
    elif t == "error":
        print(f"[오류] {ev.get('text')}", file=sys.stderr, flush=True)
    elif t == "raw":
        print(f"[이벤트가 아닌 응답] {ev.get('text')}", file=sys.stderr, flush=True)
    elif t == "done":
        print()


def _turn(question: str, session_id: str) -> None:
    for ev in events(question, customer_id=CUSTOMER_ID, session_id=session_id):
        _render(ev)


def main(argv: list[str]) -> int:
    """인자로 질문을 주면 한 턴, 없으면 대화형 루프.

    실행 한 번이 상담 한 번이다 — 세션 id 를 실행마다 새로 만들고(SESSION_ID 가 비어 있을
    때) 그 실행 안의 턴들은 같은 세션으로 보낸다. 에이전트가 그 값으로 이전 턴의 맥락을
    되찾으므로 후속 질문·되묻기의 답·「네」가 이어진다. 이전 실행의 대화는 고객 화면이 열려
    있었다면 «지난 상담»으로 기록돼 있고, 에이전트가 필요할 때 찾아 읽는다.
    """
    missing = [n for n in ("ENDPOINT_URL", "OPENAPI_TOKEN", "GENERATIVE_AI_CLIENT", "ASSET_ID")
               if not globals()[n]]
    if missing:
        print(f"파일 상단 설정이 비어 있습니다: {', '.join(missing)}", file=sys.stderr)
        return 2
    session_id = SESSION_ID or uuid.uuid4().hex[:8]
    print(f"[세션 {session_id} · 직원 {X_CLIENT_USER} · 고객 {CUSTOMER_ID or '(없음)'}]",
          file=sys.stderr)
    if len(argv) >= 2:
        _turn(" ".join(argv[1:]), session_id)
        return 0
    print("질문을 입력하세요. 빈 줄이나 Ctrl-D 로 끝냅니다.", file=sys.stderr)
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            break
        if not question:
            break
        try:
            _turn(question, session_id)
        except ValueError as exc:   # _check_utf8 — 깨진 입력은 보내지 않고 다시 받는다
            print(f"[입력 오류] {exc}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
