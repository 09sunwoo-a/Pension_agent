"""배포된 GenAI 플랫폼 에이전트를 **밖에서** 호출해 보는 테스트 클라이언트.

`src/test_local.sh` 는 로컬 uvicorn 을 두드린다. 이 스크립트는 그 다음 단계 — GenAI
플랫폼에 에이전트를 만들어 올린 뒤, **통합 웹앱이 부르는 것과 같은 모양으로** 부른다.

━━ 왜 `src/` 밖에 있나 ━━
`src/` 는 플랫폼에 **올라가는 것**이고(`pension_agent` 단일 패키지 · 임포트 루트가 `src/`),
이 파일은 그것을 **부르는 쪽**이다. 경계가 셋으로 갈린다.

  · 배포 이미지에 안 들어간다 — Dockerfile 이 담는 것은 main.py·pension_agent·session_data 다
  · `src/scripts/` 와 다른 부류다 — 그쪽은 에이전트가 자기 데이터를 만드는 내부 도구고
    (import_customers·build_kb·demo_status) `src/` 에서 `python -m scripts.X` 로 돈다
  · **`pension_agent` 을 임포트하지 않는다.** 웹앱이 짜야 하는 코드가 이 파일의 `call()`
    이라 그대로 떼어다 쓸 수 있어야 하고, 그러려면 저장소 밖에서도 돌아야 한다 —
    표준 라이브러리만 쓴다. 서버와 상수를 공유하지 못하는 자리는 주석으로 짝을 밝힌다

호출 규약은 플랫폼이 고정한 것이라 여기서 바꾸지 않는다
(`skills/genai-platform-agent-dev/refs/genai-platform.md` «API I/O 스키마 (고정)»):

    POST <base>/chat
      {"input_value": "<JSON 을 직렬화한 문자열>", "message_hists": null}
      → 200 text/event-stream, 줄마다 {"event": "CHUNK", "content": "..."}

`input_value` 안의 키는 프로젝트가 정한다. 이 에이전트가 읽는 것은 `src/main.py` 기준으로
message(필수) · x_client_user(필수) · customer_id · session_id · stream_progress 다.

━━ 확정하지 못한 것 두 가지 ━━
배포된 에이전트의 **경로 접두**와 **인증 헤더 이름**은 플랫폼 문서(「통합 웹앱 연동」)가
정하는데, 이 저장소에는 그 원문이 없다. 그래서 둘 다 환경변수로 빼 두었다 —
문서에서 확인한 값을 넣으면 코드를 고칠 필요가 없다.

    AGENT_BASE_URL     배포된 에이전트의 주소. 기본 http://localhost:8000
                       (예: https://<플랫폼호스트>/agent/<에이전트id>)
    AGENT_CHAT_PATH    기본 /chat
    AGENT_HEALTH_PATH  기본 /health — 없는 배포면 --no-health 로 건너뛴다
    AGENT_API_KEY      인증 키. 비우면 인증 헤더를 아예 안 붙인다(사내망 무인증 배포)
    AGENT_KEY_HEADER   키를 실을 헤더 이름. 기본 kb-key
                       (refs/genai-platform.md 의 LLM 호출 규격에서 가져온 기본값이다 —
                        에이전트 앞단의 게이트웨이가 다른 이름을 쓰면 여기서 바꾼다)
    AGENT_CLIENT_USER  호출자 식별자. 기본 test-user
    AGENT_EXTRA_HEADERS  JSON 객체. 문서가 요구하는 헤더가 더 있으면 여기에
    AGENT_TIMEOUT      초. 기본 120 (한 턴에 LLM 호출이 4~7회 나가므로 넉넉히)

━━ 쓰는 법 ━━
어디서 실행해도 된다(임포트 루트에 얽매이지 않는다 — 위 «왜 src 밖에 있나»).

    export AGENT_BASE_URL=https://...   AGENT_API_KEY=...

    python client/call_agent.py --check            # 규약 검증 (권장 — 먼저 이것)
    python client/call_agent.py "IRP 수수료가 부담된다는데요?"
    python client/call_agent.py -c 198734-1205842 "이 고객 뭐가 문제죠?" --progress
    python client/call_agent.py --raw "..."        # 서버가 준 줄을 그대로 본다

종료코드는 0(성공) / 1(실패) 이라 CI 나 배포 후 스모크에 그대로 걸 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:8000").rstrip("/")
CHAT_PATH = os.environ.get("AGENT_CHAT_PATH", "/chat")
HEALTH_PATH = os.environ.get("AGENT_HEALTH_PATH", "/health")
API_KEY = os.environ.get("AGENT_API_KEY", "")
KEY_HEADER = os.environ.get("AGENT_KEY_HEADER", "kb-key")
CLIENT_USER = os.environ.get("AGENT_CLIENT_USER", "test-user")
TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "120"))


def _extra_headers() -> dict[str, str]:
    raw = os.environ.get("AGENT_EXTRA_HEADERS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"AGENT_EXTRA_HEADERS 가 JSON 이 아닙니다: {exc}")
    if not isinstance(parsed, dict):
        sys.exit("AGENT_EXTRA_HEADERS 는 JSON 객체여야 합니다.")
    return {str(k): str(v) for k, v in parsed.items()}


def headers(client_user: str = CLIENT_USER) -> dict[str, str]:
    """플랫폼이 요구하는 헤더.

    `x-client-user` 는 input_value 안에도 들어가지만 헤더로도 싣는다 — 앞단 게이트웨이가
    쿼터·감사를 헤더로 집계하는 경우가 있고, 둘이 어긋나면 진단이 어려워진다.
    키가 없으면 인증 헤더를 붙이지 않는다(빈 키를 보내면 401 인지 미설정인지 갈리지 않는다).
    """
    h = {"Content-Type": "application/json", "x-client-user": client_user}
    if API_KEY:
        h[KEY_HEADER] = API_KEY
    h.update(_extra_headers())
    return h


def build_input_value(
    message: str, *, client_user: str = CLIENT_USER,
    customer_id: str | None = None, session_id: str = "default",
    stream_progress: bool = False,
) -> str:
    """input_value — 구조화된 메시지를 JSON 으로 직렬화한 **문자열**.

    객체가 아니라 문자열이다. 여기서 실수하면 서버가 422 로 돌려준다.
    """
    payload: dict[str, Any] = {
        "message": message,
        "x_client_user": client_user,
        "session_id": session_id,
    }
    if customer_id:
        payload["customer_id"] = customer_id
    if stream_progress:
        payload["stream_progress"] = True
    return json.dumps(payload, ensure_ascii=False)


def post_chat(
    input_value: str, *, message_hists: Any = None, client_user: str = CLIENT_USER,
    timeout: float = TIMEOUT,
) -> tuple[int, str, Iterator[str]]:
    """POST /chat. (상태코드, Content-Type, 줄 이터레이터) 를 준다.

    이터레이터는 **응답이 오는 대로** 흘러나온다 — 다 받아 놓고 주면 스트리밍을 테스트하는
    의미가 없다. 오류 응답(4xx·5xx)도 예외로 던지지 않고 같은 모양으로 돌려준다: 규약
    검증이 «422 가 오는가»를 확인해야 하기 때문이다.
    """
    body = json.dumps(
        {"input_value": input_value, "message_hists": message_hists},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + CHAT_PATH, data=body, headers=headers(client_user), method="POST")

    def lines(resp) -> Iterator[str]:
        try:
            for raw in resp:
                text = raw.decode("utf-8", "replace").strip()
                if text:
                    yield text
        finally:
            resp.close()

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), lines(exc)
    return resp.status, resp.headers.get("Content-Type", ""), lines(resp)


def get_health(timeout: float = 15.0) -> tuple[int, Any]:
    req = urllib.request.Request(
        BASE_URL + HEALTH_PATH, headers=headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def call(
    message: str, *, customer_id: str | None = None, session_id: str = "default",
    client_user: str = CLIENT_USER, stream_progress: bool = False,
    message_hists: Any = None, on_chunk: Callable[[str], None] | None = None,
    timeout: float = TIMEOUT,
) -> str:
    """한 턴을 돌려 답변 전문을 문자열로 준다. **웹앱이 그대로 떼어 쓸 함수다.**

    on_chunk 를 주면 청크가 도착할 때마다 부른다(화면에 흘리려면 이것).
    CHUNK 가 아닌 이벤트는 조용히 버린다 — 플랫폼이 나중에 다른 이벤트를 추가해도
    답변이 깨지지 않아야 한다. 규약 위반을 **잡아내는** 것은 --check 의 몫이다.
    """
    status, ctype, lines = post_chat(
        build_input_value(
            message, client_user=client_user, customer_id=customer_id,
            session_id=session_id, stream_progress=stream_progress),
        message_hists=message_hists, client_user=client_user, timeout=timeout)

    if status != 200:
        detail = " ".join(lines)
        raise RuntimeError(f"HTTP {status} ({ctype}): {detail[:800]}")

    parts: list[str] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "CHUNK":
            continue
        content = event.get("content", "")
        parts.append(content)
        if on_chunk:
            on_chunk(content)
    return "".join(parts)


# ────────────────────────────────── 규약 검증 ──────────────────────────────────
# 「배포는 됐는데 통합 웹앱에서 부르면 안 된다」가 어디서 갈리는지 한 번에 보려고 둔다.
# 각 항목은 refs/genai-platform.md 의 고정 스키마와 main.py 가 문서화한 동작에서 왔다.

class _Result:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def add(self, ok: bool, name: str, note: str = "") -> None:
        self.rows.append((ok, name, note))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {note}" if note else ""))

    @property
    def failed(self) -> int:
        return sum(1 for ok, _, _ in self.rows if not ok)


def _expect_422(res: _Result, name: str, input_value: str, hists: Any = None) -> None:
    try:
        status, _, lines = post_chat(input_value, message_hists=hists, timeout=30)
        body = " ".join(lines)[:200]
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 그 자체가 결과다
        res.add(False, name, f"호출 실패: {exc}")
        return
    res.add(status == 422, name, f"HTTP {status} {body}" if status != 422 else "")


def check(*, live: bool, customer_id: str | None, skip_health: bool) -> int:
    print(f"대상: {BASE_URL}")
    print(f"인증: {KEY_HEADER}={'설정됨' if API_KEY else '(없음)'}"
          f" · x-client-user={CLIENT_USER}")
    print()

    res = _Result()

    if skip_health:
        print("[health] 건너뜀 (--no-health)")
    else:
        print("[health]")
        try:
            status, body = get_health()
            res.add(status == 200, "GET /health 200", f"HTTP {status}" if status != 200 else "")
            if isinstance(body, dict):
                print("        " + json.dumps(body, ensure_ascii=False, indent=2)
                      .replace("\n", "\n        "))
                llm_cfg = body.get("llm") or {}
                # 첫 연결에서 실제로 걸린 자리다 — 인증도 쿼터도 아니고 DNS 였다(main.py).
                # 여기서 걸러 두면 아래 /chat 실패의 원인이 바로 보인다.
                if llm_cfg.get("resolves") is False:
                    res.add(False, "LLM 엔드포인트 이름 해석",
                            f"{llm_cfg.get('host')} 를 못 찾는다 — 클러스터 밖에서 부르고 있다")
                if llm_cfg.get("api_key_set") is False:
                    res.add(False, "LLM_API_KEY", "서버에 키가 안 잡혔다")
        except Exception as exc:  # noqa: BLE001
            res.add(False, "GET /health", str(exc))

    print("\n[스키마 거부 — 잘못된 요청은 422 여야 한다]")
    _expect_422(res, "input_value 가 JSON 문자열이 아니면 422", "이건 JSON 이 아니다")
    _expect_422(res, "input_value 가 객체가 아니면 422", json.dumps(["배열"]))
    _expect_422(res, "message 누락이면 422",
                json.dumps({"x_client_user": CLIENT_USER}, ensure_ascii=False))
    _expect_422(res, "x_client_user 누락이면 422",
                json.dumps({"message": "안녕하세요"}, ensure_ascii=False))

    if not live:
        print("\n[정상 호출] 건너뜀 — LLM 호출이 나가므로 --live 로 켠다")
        print(f"\n{len(res.rows) - res.failed}/{len(res.rows)} 통과")
        return 1 if res.failed else 0

    print("\n[정상 호출 — 실제 LLM 호출이 나간다]")
    started = time.monotonic()
    input_value = build_input_value(
        "IRP 수수료가 부담된다고 하시는데 뭐라고 답하면 좋을까요?",
        customer_id=customer_id)
    try:
        status, ctype, lines = post_chat(input_value)
    except Exception as exc:  # noqa: BLE001
        res.add(False, "POST /chat", str(exc))
        print(f"\n{len(res.rows) - res.failed}/{len(res.rows)} 통과")
        return 1

    res.add(status == 200, "POST /chat 200", f"HTTP {status}" if status != 200 else "")
    # 스트리밍이 아니면 «기다리는 동안 아무것도 안 나오는» 화면이 된다. 규약이 정한 값이다.
    res.add("text/event-stream" in ctype, "Content-Type 이 text/event-stream", ctype)

    bad_lines, chunks, text = 0, 0, []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if not isinstance(event, dict) or event.get("event") != "CHUNK":
            bad_lines += 1
            continue
        chunks += 1
        text.append(event.get("content", ""))

    res.add(bad_lines == 0, "모든 줄이 {\"event\":\"CHUNK\",...} 형태",
            f"규약을 벗어난 줄 {bad_lines}개" if bad_lines else "")
    res.add(chunks > 0, "CHUNK 를 하나 이상 받았다", f"{chunks}개")
    answer = "".join(text).strip()
    res.add(bool(answer), "답변이 비어 있지 않다", f"{len(answer)}자")
    # 근거 없는 답은 이 시스템의 산출물이 아니다(루트 CLAUDE.md §2, main.py 주석).
    # 문자열은 `src/pension_agent/consult_agent/render.py::GROUND_HEADER` 와 같아야 한다 —
    # 이 스크립트는 저장소 밖에서도 돌아야 해서 그 상수를 임포트하지 못한다(맨 위 주석).
    ground = "─ 근거"
    res.add(ground in answer, "답변에 출처 블록이 붙어 있다",
            "" if ground in answer else "출처 블록이 안 보인다")
    if f"{ground}: 없음" in answer:
        # 블록은 있는데 «없음»이다 — 규약 위반은 아니지만 답이 근거 없이 나왔다는 뜻이다.
        print("  주의  출처가 «없음»이다 — LLM 이 죽었거나 재료를 못 찾은 턴이다")

    print(f"\n  걸린 시간 {time.monotonic() - started:.1f}초")
    print("  ─── 답변 ───")
    print("  " + answer.replace("\n", "\n  ")[:2000])

    print(f"\n{len(res.rows) - res.failed}/{len(res.rows)} 통과")
    return 1 if res.failed else 0


# ──────────────────────────────────── CLI ────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python client/call_agent.py",
        description="배포된 GenAI 플랫폼 에이전트를 통합 웹앱과 같은 방식으로 호출한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="주소·인증은 환경변수로 준다: AGENT_BASE_URL · AGENT_API_KEY ·"
               " AGENT_KEY_HEADER · AGENT_CLIENT_USER · AGENT_EXTRA_HEADERS",
    )
    p.add_argument("message", nargs="*", help="보낼 질문. 여러 개면 순서대로 여러 턴")
    p.add_argument("-c", "--customer", help="열려 있는 브리핑 화면의 고객 id")
    p.add_argument("-s", "--session", default="default", help="상담 세션 구분자")
    p.add_argument("-u", "--user", default=CLIENT_USER, help="x_client_user (호출한 직원)")
    p.add_argument("--progress", action="store_true",
                   help="기다리는 동안 진행 표시도 함께 받는다 (사람이 보는 테스트용)")
    p.add_argument("--raw", action="store_true", help="서버가 준 줄을 가공 없이 출력")
    p.add_argument("--check", action="store_true", help="호출 규약 검증만 한다")
    p.add_argument("--live", action="store_true",
                   help="--check 에 정상 호출까지 포함 (실제 LLM 호출이 나간다)")
    p.add_argument("--no-health", action="store_true", help="/health 조회를 건너뛴다")
    args = p.parse_args(argv)

    if args.check:
        return check(live=args.live, customer_id=args.customer,
                     skip_health=args.no_health)

    if not args.message:
        p.error("질문을 하나 이상 주거나 --check 를 쓰세요.")

    print(f"→ {BASE_URL + CHAT_PATH}", file=sys.stderr)

    for i, message in enumerate(args.message):
        if len(args.message) > 1:
            print(f"\n──── 턴 {i + 1} ────", file=sys.stderr)
        print(f"Q. {message}\n", file=sys.stderr)

        if args.raw:
            status, ctype, lines = post_chat(build_input_value(
                message, client_user=args.user, customer_id=args.customer,
                session_id=args.session, stream_progress=args.progress))
            print(f"HTTP {status} {ctype}", file=sys.stderr)
            for line in lines:
                print(line)
            continue

        try:
            call(message, customer_id=args.customer, session_id=args.session,
                 client_user=args.user, stream_progress=args.progress,
                 on_chunk=lambda text: print(text, end="", flush=True))
        except Exception as exc:  # noqa: BLE001 — 진단이 목적이라 트레이스보다 메시지
            print(f"\n호출 실패: {exc}", file=sys.stderr)
            return 1
        print()

    # 여러 턴을 줘도 맥락은 이어지지 않는다. 플랫폼 응답은 CHUNK 텍스트뿐이라
    # 다음 턴에 넘길 history 를 받을 방법이 없다 — message_hists 자리는 있지만
    # 서버가 받는 것은 graph.ask() 의 내부 Turn 모양이고, 그것은 /chat 응답에 없다.
    if len(args.message) > 1:
        print("\n(주의: 턴 사이 맥락은 이어지지 않는다 — /chat 응답이 history 를"
              " 돌려주지 않는다)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
