"""⑥~⑨ 지원 섹션 회귀 테스트 — 안내 콘텐츠 규약과 화법 카드의 시효성 수치.

test_engine.py(팀원 담당, ①~⑤ 산출물 감사)와 분리해 둔다.

이 파일이 잡는 실제 회귀:
  · 지식베이스 적재 실패가 조용히 넘어가는 것 — load_reference_kb() 가 예외를 삼켜 kb=None 이
    되어도 engine 은 talk 폴백으로 통과해 버린다. 적재 성공을 명시적으로 단언한다.
  · ⑨ 더미 규약 붕괴 — 화면·발송문 딱지가 되살아나거나 dummy 플래그가 사라지는 것.
  · 원문 인용 훼손 — 시효성 수치를 갈아끼우면서 quotes 원문까지 건드리는 것.

**지금은 축소 상태다.** 더미 페르소나(C1~C6)를 걷어내면서 그 페르소나의 산출물을 직접
단언하던 검사를 함께 들어냈다(customer.PERSONAS 가 비어 있다). 시연용 고객 데이터가 새로
정해지면 아래 항목을 그 데이터 기준으로 다시 세워야 한다:

  · 1. 문제상황 매칭 — 요건이 있는 고객엔 세그먼트가 붙고, 요건 0건인 고객엔 사유를
       지어내지 않는가 / 세그먼트 요건이 고객 요건의 부분집합인가 / 컴플라이언스 우선 정렬
  · 1. 제외 조건 — 연금개시 계좌에 추가납·세액공제 세그먼트가 빠지는가
  · 2. ⑥⑦⑧ — 화법 2건·반론 2건·참고자료 1건 이상 / ⑥ 과 ⑧ 이 같은 카드를 안 쓰는가 /
       반론이 고객마다 다른가(전 고객 동일 폴백 회귀) / 후보군 상한 / 사후관리 카드만 인용 /
       인용 카드의 실재 / ⑥ 화법의 원천 문서 표기
  · 3. ⑨ — 고객마다 이벤트·세미나 각 1건 / 문제상황에 걸린 콘텐츠가 먼저 정렬되는가

실행: python -m tests.test_support
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent.strategy_agent import engine
from pension_agent.strategy_agent import situations as situations_mod
from pension_agent.strategy_agent import support

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((bool(ok), label, detail))


# ─────────────────────────────────────────────────────────────
# 0. 지식베이스가 실제로 적재되었는가
# ─────────────────────────────────────────────────────────────

kb = support.pitch_kb()
check(kb is not None, "지식베이스 적재 성공 (load_reference_kb 가 예외를 삼키지 않았는가)")
check(kb is not None and len(kb.pitches) > 100,
      "화법 카드가 06/03 변환분까지 적재됨", str(len(kb.pitches) if kb else 0))
check(bool(situations_mod.SEGMENTS), "세그먼트 레코드 적재됨", str(len(situations_mod.SEGMENTS)))
check(kb is not None and bool(kb.docs_by_id),
      "원천 문서 레지스트리 적재됨", str(len(kb.docs_by_id) if kb else 0))


# ─────────────────────────────────────────────────────────────
# 1. 세그먼트 정의 — 새 판정 규칙을 만들지 않았는가
# ─────────────────────────────────────────────────────────────

# 세그먼트 조건이 코드 판정(CONDS)의 부분집합으로만 성립한다 — 새 판정 규칙을 만들지 않았다.
valid_conds = set(engine.CONDS)
for rec in situations_mod.SEGMENTS:
    declared = set((rec.get("fields") or {}).get("conds") or [])
    check(declared <= valid_conds,
          f"세그먼트 {rec['id']} 의 conds 가 customer.CONDS 키만 쓴다", str(declared - valid_conds))


# ─────────────────────────────────────────────────────────────
# 2. ⑨ — 안내 콘텐츠와 더미 표시 규약
# ─────────────────────────────────────────────────────────────

# 종료된 콘텐츠는 노출하지 않는다.
for key, rows in support.outreach_candidates().items():
    check(all(r["end_date"] >= str(support.TODAY) for r in rows),
          f"⑨ {key}: 종료된 콘텐츠가 후보에 없다")

# 더미 표시는 기계판독(dummy) 하나뿐이다 — 화면·발송문 딱지는 붙이지 않는다.
#
# 이 저장소는 한때 3종 표시(dummy + 이름 앞 "(더미) " + 발송문 앞 "[더미] ")를 함께 강제했다.
# 발표용 데모에서는 화면도 발송문도 산출물이라 딱지가 없어야 한다는 결정으로 텍스트 딱지를
# 뗐고, 대신 보호막을 게이트로 옮겼다(pension_agent.tools.open_lms_screen). 이 테스트는 방향이
# 반대다 — "딱지가 있는가"가 아니라 **"딱지가 없고 dummy 플래그는 남아 있는가"**를 본다.
for a in support.ASSETS:
    if a.get("content_type") not in ("이벤트", "세미나"):
        continue
    check(not a["name"].startswith("(더미) "),
          f"⑨ {a['id']}: 화면 이름에 더미 딱지 없음", a["name"][:30])
    check(not (a.get("lms_message") or "").startswith("[더미] "),
          f"⑨ {a['id']}: 발송문에 더미 딱지 없음", (a.get("lms_message") or "")[:30])
    if not a.get("dummy"):
        check(bool(a.get("source")) or bool(a.get("source_resource")),
              f"⑨ 실제 콘텐츠 {a['id']} 는 출처를 갖는다")

# 더미가 하나라도 남아 있어야 게이트가 의미를 갖는다(전부 실데이터면 이 절이 무의미해진다).
_dummies = [a for a in support.ASSETS if a.get("dummy")]
check(all(isinstance(a.get("dummy"), bool) for a in _dummies),
      "⑨ dummy 는 기계판독 가능한 불리언")

# ⑨ 더미 게이트 — 딱지 대신 이것이 더미 문구가 발송 화면에 채워지는 것을 막는다.
#
# 에이전트는 이제 발송하지 않고 화면만 연다(consult_agent/CLAUDE.md §10). 그래도 게이트는
# 남는다 — 화면에 채워 넣으면 직원이 **그대로 보낼 수 있기 때문**이다. 자산을 인자로 받던
# 것을 문구로 되짚는 것으로 바꿨다(발송 화면에 채워지는 것은 문구이지 자산이 아니다).
from pension_agent import tools as _tools

_dummy_msg = next((a.get("lms_message") for a in _dummies if a.get("lms_message")), None)
if _dummy_msg:
    _blocked = _tools.open_lms_screen("TEST", _dummy_msg, session_id="test-gate")
    check(_blocked["status"] == "blocked",
          "⑨ 더미 문구는 발송 화면에 채우는 것이 거부됨", str(_blocked["status"]))
check(_tools.open_lms_screen("TEST", "행내 자산과 무관한 직접 작성 문구입니다",
                             session_id="test-gate")["status"] != "blocked",
      "⑨ 더미 자산에서 온 문구가 아니면 막지 않음")

# 기준일 인자 — 과거 시점으로 물어보면 그때 열려 있던 콘텐츠가 나온다(선별 로직 자체의 검증).
from datetime import date as _date

past = support.outreach_candidates(today=_date(2026, 12, 31))
check(all(r["end_date"] >= "2026-12-31" for r in past["event"]),
      "⑨ today 인자로 다른 기준일의 후보를 확인할 수 있다", str(len(past["event"])))


# ─────────────────────────────────────────────────────────────
# 3. 화법 카드의 시효성 수치 — 금리 슬롯 · 주장 성립 조건
#
# 원문(quotes)은 어떤 경우에도 고치지 않는다. 파생 텍스트(대사·정리·핵심)만 현재값으로
# 바꿔 끼우고, 수치를 갈아끼우면 주장이 거짓이 될 수 있는 카드는 조건을 판정해 뺀다.
# ─────────────────────────────────────────────────────────────

import copy

from pension_agent import market as _market
from pension_agent.consult_agent import kb as _kbmod
_kb = support.pitch_kb()

_slotted = [c for c in _kb.pitches if c.get("_rate_slots_applied") or c.get("_rate_notes")]
check(len(_slotted) == 5, "금리 슬롯 카드 5건", str(sorted(c["id"] for c in _slotted)))

# 원문 인용 보존 — 여기가 깨지면 "출처는 진짜인데 수치는 가짜인 카드"가 된다.
for _pid, _was in (("pitch.k03.010", "2.50%"), ("pitch.k03.013", "4%"),
                   ("pitch.k03.069", "2.76%"), ("pitch.k03.070", "3.05%")):
    _c = next((x for x in _kb.pitches if x["id"] == _pid), None)
    check(_c is not None and any(_was in (q.get("text") or "") for q in (_c.get("quotes") or [])),
          f"{_pid}: 원문 인용에 '{_was}' 보존")

# 치환된 카드는 기준시점·더미 여부를 들고 다닌다(구성 원칙 4 — 수치엔 기준시점).
for _c in _slotted:
    check(_c.get("_rate_as_of") and _c.get("_rate_dummy") is not None,
          f"{_c['id']}: 금리 기준시점·더미 표시 보유", str(_c.get("_rate_as_of")))

# 판정 근거가 없는 주장은 지어내지 않고 확인을 요구한다.
_verify = {c["id"] for c in _kb.pitches if c.get("_verify_first")}
check(_verify == {"pitch.k03.058", "pitch.k03.061", "pitch.k03.063", "pitch.k03.088"},
      "확인 요구 카드 4건", str(sorted(_verify)))
for _pid in sorted(_verify):
    _c = next(x for x in _kb.pitches if x["id"] == _pid)
    check("(확인 필요)" in support._card_talk(_c),
          f"{_pid}: 확인 요구가 ⑥ 재료에 노출")

# 조건이 깨지면 카드가 후보군에서 빠진다 — 금리를 뒤집어 실제로 확인한다.
_saved = _market._cache
try:
    _flip = copy.deepcopy(_market.current())
    _flip["rates"]["irp_avg_1y"]["value"] = 1.0        # 정기예금(3.18%)보다 낮게
    _market._cache = _flip
    _re = _kbmod.load_kb()
    check(any(c["id"] == "pitch.k03.045" for c in _re.suppressed),
          "주장이 깨지면 카드가 격리된다", str([c["id"] for c in _re.suppressed]))
    check(not any(c["id"] == "pitch.k03.045" for c in _re.pitches),
          "격리된 카드는 검색 후보에 없다")
    check(not _kbmod.retrieve(_re, top_k=50, stage="이탈방어") or
          all(h[1]["id"] != "pitch.k03.045" for h in _kbmod.retrieve(_re, top_k=50)),
          "격리된 카드는 retrieve 결과에도 없다")
finally:
    _market._cache = _saved

check(not support.pitch_kb().suppressed,
      "현재 금리에서는 격리 카드 없음", str([c["id"] for c in support.pitch_kb().suppressed]))


failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    if not ok:
        print(f"✗ {label}" + (f" — {detail}" if detail else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ ⑥~⑨ 지원 섹션 회귀 테스트 통과")
