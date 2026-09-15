"""WorkB 쪽지 — 오늘의 타겟 고객 본문

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

import asyncio

from tests.infra._common import check

import os


# ─────────────────────────────────────────────────────────────
# WorkB 쪽지 — 오늘의 타겟 고객 본문
#
# 고정하는 것은 «본문이 무엇을 말하는가»다. 문장을 LLM 이 쓰지 않으므로 같은 입력이면 같은
# 글이 나와야 하고, 자를 때도 값이 반쪽으로 남지 않아야 한다(보내고 나면 못 되돌린다).
# ─────────────────────────────────────────────────────────────

from pension_agent import note  # noqa: E402
from pension_agent.strategy_agent import customer as _customer  # noqa: E402
from pension_agent.strategy_agent.target_list import today_targets  # noqa: E402

_targets = today_targets()
check(bool(_targets), "target_list: 오늘의 타겟 고객이 산출된다", str(len(_targets)))
check(all(t.conds for t in _targets),
      "target_list: 요건이 하나도 없는 고객은 목록에 오르지 않는다")
check([t.rank for t in _targets] == sorted(t.rank for t in _targets),
      "target_list: PRIO → 요건수 → id 순으로 정렬된다(결정론)")
check(all(c in _customer.CONDS for t in _targets for c in t.conds),
      "target_list: 요건 코드가 customer.CONDS 안에서만 나온다 — 새 판정을 만들지 않는다")

# 형식 둘(텍스트·HTML)은 **같은 사실을 말해야 한다** — 표로 바꾸면서 정보가 빠지면
# 직원이 보는 목록이 형식에 따라 달라진다.
# 잘라내기 상한은 형식마다 다르게 잡는다 — HTML 은 표 뼈대(머리·머리행·꼬리)만 700자를
# 넘어서, 텍스트 기준 상한을 그대로 쓰면 «한 명은 담는다» 하한에 걸려 상한을 넘게 된다.
for _fmt, _cap in (("text", 700), ("html", 1800)):
    _n = note.daily_targets_note(fmt=_fmt)
    check(_n.body == note.daily_targets_note(fmt=_fmt).body,
          f"note[{_fmt}]: 같은 입력이면 같은 본문 (LLM 을 타지 않는다)")
    check(_n.count == len(_targets) and not _n.truncated,
          f"note[{_fmt}]: 기본 상한에서는 전원이 실린다", f"{_n.shown}/{_n.count}")
    check(_customer.AS_OF.isoformat() in _n.body,
          f"note[{_fmt}]: 원장 기준일이 본문에 남는다 — 평가금액이 오늘 값으로 읽히지 않게")
    check(all(t.profile.nm in _n.body for t in _targets),
          f"note[{_fmt}]: 목록에 오른 고객이 본문에서 빠지지 않는다")
    check(all(t.profile.id not in _n.body for t in _targets),
          f"note[{_fmt}]: 고객 id 원문이 본문에 실리지 않는다 (MASK_ID 기본값)")
    _cb, _cs = note.RENDERERS[_fmt](_targets, max_chars=_cap)
    check(0 < _cs < len(_targets) and len(_cb) <= _cap,
          f"note[{_fmt}]: 상한을 넘으면 고객 수가 줄고 본문이 상한 안에 든다",
          f"{_cs}/{len(_targets)} · {len(_cb)}자 (상한 {_cap})")
    check(f"외 {len(_targets) - _cs}명" in _cb,
          f"note[{_fmt}]: 몇 명이 빠졌는지 본문이 밝힌다")
    check(note.RENDERERS[_fmt]([], max_chars=_cap)[1] == 0,
          f"note[{_fmt}]: 타겟이 0명이어도 렌더가 죽지 않는다")

# HTML 은 뷰어·위생처리기가 걷어내는 것을 처음부터 쓰지 않는다(이메일 HTML 규율).
_html = note.daily_targets_note(fmt="html").body
check("<table" in _html and _html.count("<tr") == len(_targets) + 1,
      "note[html]: 고객 한 명이 표의 한 줄이다(머리행 포함)", str(_html.count("<tr")))
check("<style" not in _html and "class=" not in _html,
      "note[html]: <style> 블록·클래스를 쓰지 않는다 — 위생처리기가 걷어낸다")
check("http://" not in _html and "https://" not in _html,
      "note[html]: 바깥 자원을 부르지 않는다 — 막히면 표가 무너진다")
# 행 단위로 덜어내도 표가 깨지지 않아야 한다.
# WorkB 쪽지 뷰어는 **인라인 style 을 걷어낸다**(2026-09-03 실물 확인). 그래서 여백·크기를
# style 로 만들려는 시도는 무효였고, 블록 요소도 뷰어가 자기 간격을 얹는다 — 남는 것은
# <br>·<b>·표의 옛 속성뿐이다. 여기가 다시 늘면 화면에서 조용히 어긋난다.
check(not any(t in _html for t in ("<p ", "<p>", "<div", "<h1", "<h2", "<ul", "<li")),
      "note[html]: 블록 요소를 쓰지 않는다 — 뷰어가 자기 간격을 얹는다")

# 표를 만드는 곳은 하나다. 둘이 되면 한쪽만 마스킹하거나 한쪽만 잘라내는 상태가 곧 생긴다.
_bare, _bare_shown = note.targets_table(_targets)
check(_bare.startswith("<table ") and _bare.endswith("</table>") and _bare in _html
      and _bare_shown == len(_targets),
      "note.targets_table: 표만 따로 내고, 목록 쪽지는 그 표를 그대로 쓴다")

_cut_html = note.render_html(_targets, max_chars=1200)[0]
check(_cut_html.count("<table") == _cut_html.count("</table") == 1,
      "note[html]: 잘라내도 표가 열고 닫힌다")
# 속성이 중복되면 뒤엣것이 통째로 무시된다(실제로 style 이 두 번 붙어 font-size 가 죽었다).
import re as _re
check(not [t for t in _re.findall(r"<[^>]+>", _html) if t.count("style=") > 1],
      "note[html]: 한 태그에 같은 속성을 두 번 쓰지 않는다")

_note = note.daily_targets_note()

# 요건 이름은 CONDS 원문 그대로 실린다 — 쪽지가 요건 이름을 새로 지어내면 화면과 갈린다.
_lead = _targets[0]
check(_customer.CONDS[_lead.conds[0]] in _note.body,
      "note: 요건 이름이 CONDS 원문 그대로 실린다", _customer.CONDS[_lead.conds[0]])

# 고객 id 는 기본으로 가린다 (KB-PIN 앞자리가 생년월일이고, 쪽지는 받은편지함에 남는다).
check(all(t.profile.id.partition("-")[0] in _note.body for t in _targets),
      "note: 마스킹해도 앞자리는 남아 화면과 대조할 수 있다")

# 자를 때는 고객 블록 단위 — 줄 중간에서 끊으면 반쪽 수치가 남고, 그건 틀린 값을 보낸 것이다.

# 한 명도 못 담는 상한이어도 한 명은 담는다 — 빈 쪽지가 «장애»처럼 읽히는 것보다 낫다.
check(note.EMPTY_BODY in note.render([])[0],
      "note: 타겟이 0명이면 빈 쪽지가 아니라 «0명»이라고 적는다")

_min_body, _min_shown = note.render(_targets, max_chars=1)
check(_min_shown == 1, "note: 상한이 아무리 작아도 고객 한 명은 담는다", str(_min_shown))


# ── 발송 ───────────────────────────────────────────────────
# 수신자는 리스트다. 문자열 하나를 넘기면 WorkB 가 64;ETC_ERR(기타 오류)로 거부하는데,
# 파이썬은 문자열도 시퀀스라 타입 오류 없이 거기까지 가고 서버 사유도 «기타»라 어디가
# 틀렸는지 아무 데서도 안 나온다(실제로 그렇게 한 번 잡았다).
try:
    note.validate_recipients("3902172")
    check(False, "note: 수신자에 문자열 하나를 넘기면 나가기 전에 막는다")
except TypeError as _exc:
    check("리스트" in str(_exc), "note: 수신자에 문자열 하나를 넘기면 나가기 전에 막는다")
for _bad in ([], ["", "3902172"], None):
    try:
        note.validate_recipients(_bad)
        check(False, f"note: 빈 수신자를 막는다 ({_bad!r})")
    except (TypeError, ValueError):
        check(True, f"note: 빈 수신자를 막는다 ({_bad!r})")

# 어댑터의 성공은 서버의 성공이 아니다 — WorkB 는 실패를 isError 가 아니라 본문에 담는다.
# 아래 두 형태는 실제로 관측된 응답이다.
_REFUSED = [{"type": "text", "text": '{\n  "success": false,\n  "error": "64;ETC_ERR"\n}'}]
check(note.parse_result(_REFUSED)["status"] == "failed",
      "note.parse_result: 본문의 success:false 를 «발송 완료»로 보고하지 않는다",
      str(note.parse_result(_REFUSED)))
check(note.parse_result(_REFUSED).get("error") == "64;ETC_ERR",
      "note.parse_result: 서버 오류코드를 그대로 남긴다")
check(note.parse_result([{"type": "text", "text": '{"success": true}'}])["status"] == "sent",
      "note.parse_result: success:true 는 발송으로 본다")
check(note.parse_result('{"success": true}')["status"] == "sent",
      "note.parse_result: 문자열로 온 응답도 읽는다")
check(note.parse_result(([{"type": "text", "text": '{"success": true}'}], None))["status"] == "sent",
      "note.parse_result: (content, artifact) 튜플도 읽는다")
# 판정하지 못한 것을 성공 쪽으로 접지 않는다 — 그게 안 한 일을 했다고 말하는 경로다.
for _amb in ("", "OK", '{"result": 1}', None):
    check(note.parse_result(_amb)["status"] == "unknown",
          f"note.parse_result: 판정 불가는 unknown 이다 ({_amb!r})",
          str(note.parse_result(_amb)))

_sent = asyncio.run(note.send_note(["E00000"], _note))
check(_sent["status"] == "not_connected" and _sent["body"] == _note.body,
      "note.send_note: 클라이언트 미주입을 «보냄»으로 보고하지 않는다", str(_sent["status"]))

async def _fake_send(recipients, title, body):
    _fake_send.seen = (recipients, title, body)
    return [{"type": "text", "text": '{"success": true}'}]

_ok = asyncio.run(note.send_note(["3902172"], _note, send=_fake_send))
check(_ok["status"] == "sent" and _fake_send.seen[0] == ["3902172"],
      "note.send_note: 주입한 클라이언트로 수신자·제목·본문을 그대로 넘긴다", str(_ok))
check(_fake_send.seen[2] == _note.body, "note.send_note: 본문을 손대지 않고 넘긴다")

async def _boom(recipients, title, body):
    raise RuntimeError("전송 끊김")

_err = asyncio.run(note.send_note(["3902172"], _note, send=_boom))
check(_err["status"] == "failed" and "전송 끊김" in _err["detail"],
      "note.send_note: 호출이 죽으면 실패로 보고한다(삼키지 않는다)", str(_err))

# ── 「누구 이름으로 보내나」 ────────────────────────────────
# 받는 사람과 다른 축이다. MCP 인증에 들어가고 행내 감사 기록이 그 사번으로 남는다.
# 값은 진입점의 x_client_user·employee_id 에서 대화 상태를 거쳐 내려온다.
#
# x_client_user 는 사번 뒤에 접미가 붙어 올 수 있다(LLM 호출을 가르는 uuid 등).
# 전체 일치로 재면 그런 값이 전부 «사번 아님»으로 떨어지고, 그러면 쪽지가 환경변수에
# 적힌 한 사람 앞으로 몰린다 — 이 함수가 막으려는 바로 그 상태다.
for _raw in ("3902172", " 3902172 ", "3902172-550e8400-e29b-41d4-a716-446655440000",
             "3902172_550e8400e29b", "3902172:a1b2"):
    check(note.as_emp_no(_raw) == "3902172",
          f"note.as_emp_no: 사번 뒤에 구분자와 접미가 붙어도 읽는다 ({_raw!r})",
          str(note.as_emp_no(_raw)))
# 뒤에 **숫자가 더 붙어 있으면 읽지 않는다.** 「7자리 + 무엇이든」으로 자르면 사번이 아닌
# 숫자 id 에서 실재하는 남의 사번을 만들어내고, 그 사람 받은편지함에 고객 정보가 남는다.
# 못 읽으면 환경변수 폴백으로 떨어지므로 틀리는 방향이 되돌릴 수 있는 쪽이다.
# 그 값은 쿼터 버킷 이름이기도 해서 사번이 아닌 값도 들어온다(pension-agent 는 기본값이다).
for _bad in ("pension-agent", "streamlit-dev", "390217", "emp-3902172",
             "20250910123456", "39021725f3a9c", "", None):
    check(note.as_emp_no(_bad) is None,
          f"note.as_emp_no: 사번으로 확정되지 않으면 읽지 않는다 ({_bad!r})",
          str(note.as_emp_no(_bad)))

_saved_emp_env = os.environ.get(note.EMP_NO_ENV)
os.environ[note.EMP_NO_ENV] = "3900000"
try:
    check(note.employee_id() == "3900000",
          "note.employee_id: 아무것도 없으면 환경변수로 떨어진다", str(note.employee_id()))
    with note.acting("3902174"):
        check(note.employee_id() == "3902174" and note.acting_employee() == "3902174",
              "note.acting: 블록 안에서는 그 사번이 «보내는 사람»이다", str(note.employee_id()))
        check(note.employee_id("3902175") == "3902175",
              "note.employee_id: 로그인 사번(명시)이 가장 먼저다")
    check(note.employee_id() == "3900000" and note.acting_employee() is None,
          "note.acting: 블록을 벗어나면 원래대로 돌아온다", str(note.employee_id()))

    # 발송 함수는 주입받은 것이라 시그니처를 늘릴 수 없다 — 주체는 ContextVar 로 건넨다.
    _seen_actor: list = []

    async def _who(recipients, title, body):
        _seen_actor.append(note.acting_employee())
        return '{"success": true}'

    asyncio.run(note.send_note(["3902172"], _note, send=_who, as_employee="3902174"))
    asyncio.run(note.send_note(["3902172"], _note, send=_who))
    check(_seen_actor == ["3902174", None],
          "note.send_note: as_employee 가 발송 함수에게 «보내는 사람»으로 건네진다",
          str(_seen_actor))
finally:
    if _saved_emp_env is None:
        os.environ.pop(note.EMP_NO_ENV, None)
    else:
        os.environ[note.EMP_NO_ENV] = _saved_emp_env
