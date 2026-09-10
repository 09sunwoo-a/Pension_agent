"""쪽지 발송만 따로 시험하는 파일 — 행내에서 한 번 돌려 보고 지우거나 두거나.

에이전트 전체(대화·LLM·고객 브리핑)를 태우지 않고 **쪽지가 실제로 나가는지만** 본다.
그래서 LLM 키가 없어도 돌고, 실패하면 어디서 갈렸는지 그 자리에서 찍는다.

    cd src
    python try_memo.py                 # 붙는지만 본다 — **보내지 않는다**
    python try_memo.py --send          # 본인에게 시험 쪽지를 실제로 보낸다
    python try_memo.py --send --targets        # 오늘의 타겟 목록 쪽지(진짜 본문)
    python try_memo.py --send --targets --html # 그 목록을 HTML 표로 — 뷰어가 표를 그리나
    python try_memo.py --send --to 3902173     # 다른 직원에게(사번을 직접 적을 때만)

받는 사람은 기본이 **본인**이다 — `WORKB_EMP_NO` 환경변수(`src/.env`)나 `--emp` 로 준
사번. `--to` 를 적으면 그 사번으로 간다.

━━ 무엇을 보게 되나 ━━
발송은 되돌릴 수 없어서(루트 CLAUDE.md 규칙 5) `--send` 없이는 본문만 찍고 끝난다.
`--send` 를 붙이면 나가는 본문을 먼저 보여주고, 보낸 뒤에는 **서버 응답 원문**과
그것을 코드가 어떻게 판정했는지(sent / failed / unknown / not_connected)를 함께 찍는다.
WorkB 는 실패를 본문에 `{"success": false, "error": "64;ETC_ERR"}` 로 담아 보내므로
«호출이 성공했다»와 «쪽지가 나갔다»는 다른 말이다(`workb.parse_result`).

━━ 확인할 만한 것 두 가지 ━━
① `--targets --html` — 쪽지 뷰어가 표를 렌더하는지는 아직 실물로 확인하지 못했다
   (`workb.FORMAT` 이 그래서 "text" 다). 표가 제대로 뜨면 그 한 줄을 "html" 로 바꾼다.
   태그가 그대로 보이면 텍스트 그대로 두면 된다.
② 본문 길이 상한(`workb.MAX_CHARS`)도 규격을 못 받은 자리표시자다. 이 스크립트가 찍는
   «본문 N자»를 서버가 받아들이는지가 그 자리의 첫 실측이다.
"""

from __future__ import annotations

import sys

from pension_agent import mcp, workb

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 시험 쪽지의 제목·본문. 마음대로 고쳐도 된다 ────────────────────────────
# 꼴은 WorkB 가 렌더하는 최소한만 쓴다 — 줄바꿈 <br>, 굵게 <b>. 인라인 style 은 뷰어가
# 걷어낸다(실물 확인). 표는 `--targets` 쪽이 코드로 만든다.
TITLE = "쪽지 발송 확인"
BODY = ("<b>퇴직연금 AI 사후관리 에이전트</b><br>"
        "행내 MCP 연결을 확인하려고 보낸 쪽지입니다.<br>"
        "이 쪽지가 보이면 발송 경로가 살아 있습니다.")


def main(argv: list[str]) -> int:
    send = "--send" in argv
    use_targets = "--targets" in argv
    fmt = "html" if "--html" in argv else "text"

    def _opt(name: str) -> str:
        """`--to 3902173` 처럼 값이 따라오는 인자."""
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else ""

    # 보내는 주체(사번). 운영에서는 로그인 사번이 여기 온다 — 이 파일은 그 자리를
    # `--emp` 나 WORKB_EMP_NO 로 대신한다.
    sender = workb.employee_id(_opt("--emp"))
    to = _opt("--to") or sender

    # ── ① 설정 · 패키지 ────────────────────────────────────────────────
    cfg = mcp.settings()
    print("[설정]")
    print(f"  게이트웨이   {cfg.base_url or '(비어 있음)'}")
    print(f"  설정         {'갖춰짐' if cfg.configured else '모자람 — ' + ', '.join(cfg.missing())}")
    print(f"  보내는 사번   {sender or f'(비어 있음 — {workb.EMP_NO_ENV})'}")
    print(f"  받는 사번     {to or '(없음)'}")
    try:
        mcp.client.backend()
        print("  행내 패키지   있음")
    except mcp.MCPUnavailable as exc:
        print(f"  행내 패키지   없음 · {exc}")

    if not to:
        print(f"\n받는 사람이 없습니다 — src/.env 의 {workb.EMP_NO_ENV} 를 채우거나 "
              "--emp 3902172 로 주세요.")
        return 1

    # ── ② 나가는 본문 ─────────────────────────────────────────────────
    if use_targets:
        # 진짜로 나가게 될 쪽지 — 오늘의 타겟 목록(코드가 원장 값으로 만든다).
        note = workb.daily_targets_note(fmt=fmt)
        print(f"\n[본문] 오늘의 타겟 {note.shown}/{note.count}명 · {fmt}"
              f"{' · 잘림' if note.truncated else ''}")
    else:
        note = workb.Note(title=TITLE, body=BODY)
        print("\n[본문] 시험 쪽지")
    print(f"  제목 {note.title}")
    print(f"  본문 {len(note.body)}자")
    print("  " + "─" * 60)
    print("\n".join("  " + line for line in note.body.splitlines()))
    print("  " + "─" * 60)

    if not send:
        print("\n보내지 않았습니다. 실제로 보내려면 --send 를 붙이세요.")
        return 0

    # ── ③ 발송 ────────────────────────────────────────────────────────
    # install() 이 행내 MCP 를 발송 함수로 등록한다. 설정이 없으면 등록하지 않고,
    # 그때 send_note_sync 는 보내지 않고 «미연결»로 답한다(조용히 성공처럼 끝나지 않는다).
    connected = mcp.install()
    print(f"\n[발송] MCP {'연결됨' if connected else '미연결'} · 받는 사람 {to}")
    result = workb.send_note_sync([to], note, as_employee=sender)

    print(f"  판정  {result.get('status')}")
    print(f"  사유  {result.get('detail')}")
    if result.get("error"):
        print(f"  오류  {result['error']}")
    if result.get("status") == "sent":
        print("\n받은편지함을 확인하세요. 제목 앞에는 WorkB 가 [AI 에이전트] 를 붙입니다.")
        return 0
    print("\n보내지 못했습니다 — 위 «사유» 가 어디서 갈렸는지 말합니다."
          "\n  not_connected  설정(MCP_*)이 없어 등록 자체를 안 했다 — src/.env 를 채운다"
          "\n  failed         붙었는데 호출·서버가 거부했다 — 오류 코드를 그대로 남겼다"
          "\n  unknown        서버 응답을 판정하지 못했다 — 나갔는지 알 수 없다")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
