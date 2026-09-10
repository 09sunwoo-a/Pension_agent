"""쪽지 발송만 따로 시험하는 파일 — 행내에서 한 번 돌려 보고 지우거나 두거나.

에이전트 전체(대화·LLM·고객 브리핑)를 태우지 않고 **쪽지가 실제로 나가는지만** 본다.
그래서 LLM 키가 없어도 돌고, 실패하면 어디서 갈렸는지 그 자리에서 찍는다.

    cd src
    python try_memo.py 3902172                 # 붙는지만 본다 — **보내지 않는다**
    python try_memo.py 3902172 --send          # 본인에게 시험 쪽지를 실제로 보낸다
    python try_memo.py 3902172 --send --targets        # 오늘의 타겟 목록 쪽지(진짜 본문)
    python try_memo.py 3902172 --send --targets --html # 그 목록을 HTML 표로
    python try_memo.py 3902172 --send --to 3902173     # 다른 직원에게

━━ 사번은 인자로 준다 ━━
첫 인자가 **로그인한 직원의 사번**이다 — 실서비스에서 `x_client_user` 로 들어오는 그
자리다(`graph.employee_no`). 이 파일에는 HTTP 요청이 없으니 그 값을 손으로 준다.

받는 사람은 기본이 **본인**(그 사번)이고, `--to` 를 적으면 그 사번으로 간다. 제품에서
받는 사람이 갈리는 규칙과 같다 — 기본은 본인, 타인은 직원이 사번을 적었을 때만.

사번을 안 주면 `WORKB_EMP_NO` 환경변수로 떨어진다. **그건 폴백이다** — 실서비스에서
그 자리까지 떨어지면 여러 직원의 쪽지가 전부 한 사람 앞으로 가는 상태이고
(`docs/PRODUCTION_RISKS.md` 10), 시험할 때 그 값에 기대면 무엇을 재는지가 흐려진다.

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


def _parse(argv: list[str]) -> tuple[set[str], str, str]:
    """(플래그, 첫 인자=로그인 사번, --to 값). `--to` 만 값을 하나 먹는다."""
    flags: set[str] = set()
    to, emp_no = "", ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--to":
            to, i = (argv[i + 1] if i + 1 < len(argv) else ""), i + 2
        elif arg.startswith("--"):
            flags.add(arg)
            i += 1
        else:
            emp_no = emp_no or arg
            i += 1
    return flags, emp_no, to


def main(argv: list[str]) -> int:
    flags, emp_no, to_arg = _parse(argv)
    send = "--send" in flags
    use_targets = "--targets" in flags
    fmt = "html" if "--html" in flags else "text"

    # 로그인한 직원의 사번 — 실서비스에서 x_client_user 로 들어오는 그 자리다.
    # 안 주면 환경변수로 떨어지지만 그건 폴백이다(머리말).
    sender = workb.employee_id(emp_no)
    from_env = bool(sender) and not emp_no
    # 받는 사람은 기본이 본인, --to 를 적으면 그 사번(제품의 갈림과 같다).
    to = to_arg or sender

    # ── ① 설정 · 패키지 ────────────────────────────────────────────────
    cfg = mcp.settings()
    print("[설정]")
    print(f"  게이트웨이   {cfg.base_url or '(비어 있음)'}")
    print(f"  설정         {'갖춰짐' if cfg.configured else '모자람 — ' + ', '.join(cfg.missing())}")
    print(f"  보내는 사번   {sender or '(없음)'}"
          f"{f' — 인자를 안 줘서 {workb.EMP_NO_ENV} 로 떨어졌다(폴백)' if from_env else ''}")
    print(f"  받는 사번     {to or '(없음)'}{' — 본인' if to == sender else ''}")
    try:
        mcp.client.backend()
        print("  행내 패키지   있음")
    except mcp.MCPUnavailable as exc:
        print(f"  행내 패키지   없음 · {exc}")

    if not sender:
        print("\n사번이 없습니다 — 첫 인자로 로그인 사번을 주세요:"
              "\n    python try_memo.py 3902172 --send")
        return 1
    # 꼴이 아니어도 막지 않는다 — 손으로 적어 준 값은 그대로 믿는다(제품의 employee_id 와
    # 같은 규약). 다만 오타는 서버가 «64;ETC_ERR» 로만 답하므로 여기서 먼저 알려준다.
    for label, value in (("보내는", sender), ("받는", to)):
        if not workb.as_emp_no(value):
            print(f"  ⚠ {label} 사번 {value!r} 이 사번 꼴(숫자 7자리)이 아닙니다 — "
                  "그대로 보내지만 서버가 거부할 수 있습니다.")

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
