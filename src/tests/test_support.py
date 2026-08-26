"""⑥~⑨ 지원 섹션 회귀 테스트 — 문제상황 매칭과 그 위에 얹힌 후보군.

test_engine.py(팀원 담당, ①~⑤ 산출물 감사)와 분리해 둔다. 여기서 고정하는 것은 "고객의 관리
사유에서 화법·반론·자료·안내 콘텐츠가 나오는가" 하나다.

이 파일이 잡는 실제 회귀:
  · 지식베이스 적재 실패가 조용히 넘어가는 것 — load_reference_kb() 가 예외를 삼켜 kb=None 이
    되어도 engine 은 talk 폴백으로 통과해 버린다. 적재 성공을 명시적으로 단언한다.
  · 전 고객 동일 반론 — objection_refs 저작이 없어 id 순 폴백이 돌면 편중 고객에게 "지금 쓸 돈도
    없어요" 가 나온다. 고객마다 달라야 한다.
  · ⑥ 과 ⑧ 이 같은 카드를 보여주는 것 — ⑧ 은 제안이 아니라 참고 자료다(REQUIREMENTS.md ⑧).
  · ⑨ 더미 표시 누락 — 지어낸 일정이 실제 안내처럼 보이면 그대로 고객에게 나간다.

실행: python test_support.py
"""

from __future__ import annotations

import dataclasses
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent.strategy_agent import engine
from pension_agent.strategy_agent import situations as situations_mod
from pension_agent.strategy_agent import support
from pension_agent.strategy_agent.customer import PERSONAS, conditions

FACTS = {p.nm: engine.prepare(p) for p in PERSONAS}
BY_NAME = {p.nm: p for p in PERSONAS}

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
# 1. 문제상황 매칭 — 성립 요건이 있는 고객은 관리 사유가 잡힌다
# ─────────────────────────────────────────────────────────────

for p in PERSONAS:
    conds = conditions(p)
    found = FACTS[p.nm]["problem_situations"]
    if conds:
        check(bool(found), f"문제상황: {p.nm}(요건 {len(conds)}건) 에 세그먼트가 매칭됨",
              f"conds={sorted(conds)}")
    else:
        # 요건이 하나도 없는 고객에게 관리 사유를 지어내지 않는다(C5 한서진).
        check(not found, f"문제상황: {p.nm}(요건 0건) 은 매칭 없음 — 사유를 만들어내지 않는다")

    for s in found:
        check(set(s["conds"]) <= set(conds),
              f"문제상황: {p.nm} — {s['no']}번 세그먼트의 요건이 모두 성립",
              f"{s['conds']} ⊄ {sorted(conds)}")

# 세그먼트 조건이 코드 판정(CONDS)의 부분집합으로만 성립한다 — 새 판정 규칙을 만들지 않았다.
valid_conds = set(engine.CONDS)
for rec in situations_mod.SEGMENTS:
    declared = set((rec.get("fields") or {}).get("conds") or [])
    check(declared <= valid_conds,
          f"세그먼트 {rec['id']} 의 conds 가 customer.CONDS 키만 쓴다", str(declared - valid_conds))

# 정렬 — 컴플라이언스(규정 위반 점검)와 이탈위험이 앞에 온다.
c4 = FACTS["정수연"]["problem_situations"]
check(c4 and c4[0]["group"].startswith("컴플라이언스"),
      "문제상황 정렬: 정수연은 컴플라이언스 세그먼트가 첫 번째",
      c4[0]["group"] if c4 else "-")

# 제외 조건 — 연금개시 계좌에는 추가납 세그먼트를 붙이지 않는다(REQUIREMENTS.md §7).
started = dataclasses.replace(BY_NAME["오지호"], pension_started=True)
not_started = dataclasses.replace(BY_NAME["오지호"], pension_started=False)
# 같은 요건을 주고 연금개시 여부만 바꾼다 — 요건이 성립해도 제외 조건이 세그먼트를 걷어내야 한다.
excluded = {s["no"] for s in situations_mod.problem_situations(started, ["tax", "add"])}
included = {s["no"] for s in situations_mod.problem_situations(not_started, ["tax", "add"])}
check({"13", "15", "16"} & included and not ({"13", "15", "16"} & excluded),
      "제외 조건: 연금개시 고객에게만 추가납·세액공제 세그먼트가 빠진다",
      f"개시전 {sorted(included)} / 개시후 {sorted(excluded)}")


# ─────────────────────────────────────────────────────────────
# 2. ⑥⑦⑧ — 관리 사유가 있는 고객은 내용이 채워진다
# ─────────────────────────────────────────────────────────────

ACTIVE = [p for p in PERSONAS if conditions(p)]

for p in ACTIVE:
    f = FACTS[p.nm]
    tps, objs, res = f["talking_points"], f["objections"], f["consult_resources"]

    check(len(tps) == 2, f"⑥ {p.nm}: 화법 정확히 2건", str(len(tps)))
    check(all((t.get("script") or t.get("talk")) for t in tps),
          f"⑥ {p.nm}: 각 화법에 내용이 있다")
    check(len({t["title"] for t in tps}) == len(tps),
          f"⑥ {p.nm}: 화법 제목이 서로 다르다(스크립트 매핑 키)")

    check(len(objs) == 2, f"⑦ {p.nm}: 예상 반론 정확히 2건", str(len(objs)))
    check(all(o.get("objection") and o.get("response") for o in objs),
          f"⑦ {p.nm}: 각 반론에 고객 발화와 대응 화법이 있다")

    check(len(res) >= 1, f"⑧ {p.nm}: 참고 자료 1건 이상", str(len(res)))
    check(not ({t["title"] for t in tps} & {r["title"] for r in res}),
          f"⑧ {p.nm}: ⑥ 화법과 같은 카드를 다시 보여주지 않는다")

# 고객마다 다른 반론이 나온다 — 예전 id 순 폴백은 전원 동일 2건이었다.
objection_sets = {p.nm: tuple(o["objection"] for o in FACTS[p.nm]["objections"]) for p in ACTIVE}
check(len(set(objection_sets.values())) > 1,
      "⑦ 예상 반론이 고객마다 다르다(전 고객 동일 폴백 회귀)", str(objection_sets))

# 후보군 상한 — LLM 선별이 고르기 좋은 크기.
for p in ACTIVE:
    pools = FACTS[p.nm]["pools"]
    check(len(pools["objections"]) <= support.MAX_OBJECTION_CANDIDATES,
          f"⑦ {p.nm}: 후보군이 상한 이내", str(len(pools["objections"])))
    check(len(pools["consult_resources"]) <= support.MAX_RESOURCE_CANDIDATES,
          f"⑧ {p.nm}: 후보군이 상한 이내", str(len(pools["consult_resources"])))

# 사후관리 카드만 쓴다 — 범위 밖(scope='참고') 카드가 사후관리 화면에 섞이면 상담 맥락이
# 어긋난다. 인덱스는 **화법만이 아니라 검색 대상 카드 전체**다 — ⑧ 상담 참고 자료는
# 방법론·절차 카드를 인용하므로, 화법만 담으면 그 카드들이 검사에서 통째로 빠진다.
by_id = {c["id"]: c for c in (kb.cards if kb else [])}
for p in ACTIVE:
    f = FACTS[p.nm]
    used = [item.get("card_id") for item in (*f["talking_points"], *f["objections"],
                                             *f["consult_resources"], *f["pools"]["objections"],
                                             *f["pools"]["consult_resources"])
            if item.get("card_id")]
    # 범위를 선언하는 종류(화법·방법론·세그먼트)만 본다. 절차·현장관찰은 scope 가 없다.
    scoped = [cid for cid in used if cid in by_id and by_id[cid].get("scope")]
    check(all(by_id[cid]["scope"] == "사후관리" for cid in scoped),
          f"후보군: {p.nm} 은 사후관리 카드만 인용", str(len(used)))
    # 위 검사는 `cid in by_id` 인 것만 본다 — 없는 카드를 인용하면 조용히 건너뛴다.
    # 실재 여부를 따로 못 박아야 그 구멍으로 지워진 카드 id 가 남아 있지 않다.
    check(all(cid in by_id for cid in used),
          f"후보군: {p.nm} 이 인용한 카드가 지식베이스에 실재한다",
          str([cid for cid in used if cid not in by_id][:3]))

# 출처 — 카드에서 온 항목은 원천 문서를 밝힌다(06 기능정의 ① 근거 표기).
sourced = [item for p in ACTIVE for item in FACTS[p.nm]["talking_points"] if item.get("card_id")]
check(sourced and all(item.get("source") for item in sourced),
      "⑥ 카드에서 온 화법은 원천 문서를 함께 표기한다", str(len(sourced)))


# ─────────────────────────────────────────────────────────────
# 3. ⑨ — 안내 콘텐츠와 더미 표시 규약
# ─────────────────────────────────────────────────────────────

for p in PERSONAS:
    out = FACTS[p.nm]["outreach"]
    check(out.get("event") and out.get("seminar"),
          f"⑨ {p.nm}: 이벤트·세미나 각 1건", str(out))

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

# 문제상황에 맞는 콘텐츠가 앞에 온다.
c1_situations = FACTS["김민수"]["problem_situations"]
ordered = support.outreach_candidates(c1_situations)["event"]
wanted = {s["id"] for s in c1_situations}
if len(ordered) > 1:
    overlaps = [len(wanted & set(r["segments"])) for r in ordered]
    check(overlaps == sorted(overlaps, reverse=True),
          "⑨ 문제상황에 걸린 콘텐츠가 먼저 정렬된다", str(overlaps))

# 기준일 인자 — 과거 시점으로 물어보면 그때 열려 있던 콘텐츠가 나온다(선별 로직 자체의 검증).
from datetime import date as _date

past = support.outreach_candidates(today=_date(2026, 12, 31))
check(all(r["end_date"] >= "2026-12-31" for r in past["event"]),
      "⑨ today 인자로 다른 기준일의 후보를 확인할 수 있다", str(len(past["event"])))


# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# 4. 화법 카드의 시효성 수치 — 금리 슬롯 · 주장 성립 조건
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
