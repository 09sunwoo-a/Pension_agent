"""⑥~⑨ 지원 섹션 회귀 테스트 — KB 적재 성공 · ⑨ 더미 규약(딱지 없음 + dummy 플래그) · 시효성 수치(원문 인용 보존).

①~⑤ 산출물 감사는 test_engine.py. 페르소나 기준은 customers.json.
실행: python -m tests.test_support
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import dataclasses
import json

from pension_agent.knowledge import shared_store
from pension_agent.tools.customer_solution import engine
from pension_agent.tools.customer_solution import situations as situations_mod
from pension_agent.tools.customer_solution import support
from pension_agent.tools.customer_solution.customer import PERSONAS, conditions

FACTS = {p.nm: engine.prepare(p) for p in PERSONAS}
BY_NAME = {p.nm: p for p in PERSONAS}

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((bool(ok), label, detail))


# 0. 지식베이스 적재 — load_reference_kb() 가 예외를 삼켜도 engine 이 통과하므로 명시 단언

kb = support.pitch_kb()
check(kb is not None, "지식베이스 적재 성공 (load_reference_kb 가 예외를 삼키지 않았는가)")
check(kb is not None and len(kb.pitches) > 100,
      "화법 카드가 06/03 변환분까지 적재됨", str(len(kb.pitches) if kb else 0))
check(bool(situations_mod.SEGMENTS), "세그먼트 레코드 적재됨", str(len(situations_mod.SEGMENTS)))
check(kb is not None and bool(kb.docs_by_id),
      "원천 문서 레지스트리 적재됨", str(len(kb.docs_by_id) if kb else 0))


# 1. 문제상황 매칭 — 성립 요건 있는 고객은 관리 사유 포착

for p in PERSONAS:
    conds = conditions(p)
    found = FACTS[p.nm]["problem_situations"]
    if conds:
        check(bool(found), f"문제상황: {p.nm}(요건 {len(conds)}건) 에 세그먼트가 매칭됨",
              f"conds={sorted(conds)}")
    else:
        # 요건 없는 고객에 관리 사유 생성 금지
        check(not found, f"문제상황: {p.nm}(요건 0건) 은 매칭 없음 — 사유를 만들어내지 않는다")
    for sit in found:
        check(set(sit["conds"]) <= set(conds),
              f"문제상황: {p.nm} — {sit['no']}번 세그먼트의 요건이 모두 성립",
              f"{sit['conds']} ⊄ {sorted(conds)}")

# 정렬 — 컴플라이언스 우선(박지민: 위험자산 한도 초과)
_pj = FACTS["박지민"]["problem_situations"]
check(_pj and _pj[0]["group"].startswith("컴플라이언스"),
      "문제상황 정렬: 박지민은 컴플라이언스 세그먼트가 첫 번째", _pj[0]["group"] if _pj else "-")

# 제외 조건 — 연금개시 계좌에 추가납 세그먼트 미부착
started = dataclasses.replace(BY_NAME["한지우"], pension_started=True)
not_started = dataclasses.replace(BY_NAME["한지우"], pension_started=False)
excluded = {s["no"] for s in situations_mod.problem_situations(started, ["tax", "add"])}
included = {s["no"] for s in situations_mod.problem_situations(not_started, ["tax", "add"])}
check({"13", "15", "16"} & included and not ({"13", "15", "16"} & excluded),
      "제외 조건: 연금개시 고객에게만 추가납·세액공제 세그먼트가 빠진다",
      f"개시전 {sorted(included)} / 개시후 {sorted(excluded)}")


# 1-1. ⑥⑦⑧ — 관리 사유 있는 고객은 내용 채움

ACTIVE = [p for p in PERSONAS if conditions(p)]
check(len(ACTIVE) == len(PERSONAS), "목업 9케이스는 전원 관리 사유 보유", str(len(ACTIVE)))

for p in ACTIVE:
    f = FACTS[p.nm]
    tps, objs, res = f["talking_points"], f["objections"], f["consult_resources"]
    check(len(tps) == 2, f"⑥ {p.nm}: 화법 정확히 2건", str(len(tps)))
    check(all((t.get("script") or t.get("talk")) for t in tps), f"⑥ {p.nm}: 각 화법에 내용이 있다")
    check(len({t["title"] for t in tps}) == len(tps),
          f"⑥ {p.nm}: 화법 제목이 서로 다르다(스크립트 매핑 키)")
    check(len(objs) == 2, f"⑦ {p.nm}: 예상 반론 정확히 2건", str(len(objs)))
    check(all(o.get("objection") and o.get("response") for o in objs),
          f"⑦ {p.nm}: 각 반론에 고객 발화와 대응 화법이 있다")
    check(len(res) >= 1, f"⑧ {p.nm}: 참고 자료 1건 이상", str(len(res)))
    check(not ({t["title"] for t in tps} & {r["title"] for r in res}),
          f"⑧ {p.nm}: ⑥ 화법과 같은 카드를 다시 보여주지 않는다")

# 고객마다 다른 반론(id 순 폴백 전원 동일 회귀 방지)
objection_sets = {p.nm: tuple(o["objection"] for o in FACTS[p.nm]["objections"]) for p in ACTIVE}
check(len(set(objection_sets.values())) > 1,
      "⑦ 예상 반론이 고객마다 다르다(전 고객 동일 폴백 회귀)", str(objection_sets))

# 후보군 상한 — LLM 선별 적정 크기
for p in ACTIVE:
    pools = FACTS[p.nm]["pools"]
    check(len(pools["objections"]) <= support.MAX_OBJECTION_CANDIDATES,
          f"⑦ {p.nm}: 후보군이 상한 이내", str(len(pools["objections"])))
    check(len(pools["consult_resources"]) <= support.MAX_RESOURCE_CANDIDATES,
          f"⑧ {p.nm}: 후보군이 상한 이내", str(len(pools["consult_resources"])))

# 사후관리 카드만 · 인용 카드 실재
_kb0 = support.pitch_kb()
by_id = {c["id"]: c for c in (_kb0.cards if _kb0 else [])}
for p in ACTIVE:
    f = FACTS[p.nm]
    used = [item.get("card_id") for item in (*f["talking_points"], *f["objections"],
                                             *f["consult_resources"], *f["pools"]["objections"],
                                             *f["pools"]["consult_resources"])
            if item.get("card_id")]
    scoped = [cid for cid in used if cid in by_id and by_id[cid].get("scope")]
    check(all(by_id[cid]["scope"] == "사후관리" for cid in scoped),
          f"후보군: {p.nm} 은 사후관리 카드만 인용", str(len(used)))
    check(all(cid in by_id for cid in used),
          f"후보군: {p.nm} 이 인용한 카드가 지식베이스에 실재한다",
          str([cid for cid in used if cid not in by_id][:3]))

# 출처 — 카드 유래 항목은 원천 문서 명시
sourced = [item for p in ACTIVE for item in FACTS[p.nm]["talking_points"] if item.get("card_id")]
check(sourced and all(item.get("source") for item in sourced),
      "⑥ 카드에서 온 화법은 원천 문서를 함께 표기한다", str(len(sourced)))

# ⑨ — 고객마다 이벤트·세미나 각 1건, 문제상황 관련 콘텐츠 우선
for p in PERSONAS:
    out = FACTS[p.nm]["outreach"]
    check(out.get("event") and out.get("seminar"), f"⑨ {p.nm}: 이벤트·세미나 각 1건", str(out))
# 관련도는 요건(CONDS) 축 — 콘텐츠 keywords 를 요건으로 매핑(support.KEYWORD_CONDS)
_sit1 = FACTS["김현수"]["problem_situations"]
ordered = support.outreach_candidates(_sit1)["event"]
wanted = {c for s in _sit1 for c in (s.get("conds") or [])}
if len(ordered) > 1:
    overlaps = [len(wanted & set(r["conds"])) for r in ordered]
    check(overlaps == sorted(overlaps, reverse=True),
          "⑨ 문제상황에 걸린 콘텐츠가 먼저 정렬된다", str(overlaps))
    check(overlaps[0] > 0, "⑨ 김현수(미운용 현금성자산)에게 걸리는 이벤트가 있다", str(overlaps))

# 관련도 0 폴백과 실제 매칭 구분 — 추천 칩이 폴백을 «있다»로 세면 조건 무의미
for p in PERSONAS:
    _sits = FACTS[p.nm]["problem_situations"]
    _wanted = {c for s in _sits for c in (s.get("conds") or [])}
    _rel = support.relevant_outreach(_sits, name=p.nm)
    check(all(_wanted & set(r["conds"]) for r in _rel),
          f"⑨ {p.nm}: relevant_outreach 는 걸린 것만 돌려준다", str([r["id"] for r in _rel]))
    check(all(r["end_date"] >= str(support.today()) for r in _rel),
          f"⑨ {p.nm}: relevant_outreach 도 종료된 콘텐츠를 빼고 본다")
# 대조군 필요 — 전원 매칭이면 조건부 노출 미검증
check(any(not support.relevant_outreach(FACTS[p.nm]["problem_situations"]) for p in PERSONAS),
      "⑨ 걸린 콘텐츠가 없는 고객이 있다(칩 대조군)",
      str([p.nm for p in PERSONAS
           if not support.relevant_outreach(FACTS[p.nm]["problem_situations"])]))

# keywords → 요건 매핑은 이름 대응 — 없는 요건이면 영구 관련도 0(화면에 안 드러남)
check(set(sum(map(list, support.KEYWORD_CONDS.values()), [])) <= set(engine.CONDS),
      "⑨ KEYWORD_CONDS 가 customer.CONDS 키만 쓴다",
      str(set(sum(map(list, support.KEYWORD_CONDS.values()), [])) - set(engine.CONDS)))


# 1-2. 세그먼트 정의 — 새 판정 규칙 금지

# 세그먼트 조건은 코드 판정(CONDS)의 부분집합으로만 성립
valid_conds = set(engine.CONDS)
for rec in situations_mod.SEGMENTS:
    declared = set((rec.get("fields") or {}).get("conds") or [])
    check(declared <= valid_conds,
          f"세그먼트 {rec['id']} 의 conds 가 customer.CONDS 키만 쓴다", str(declared - valid_conds))


# 2. ⑨ — 안내 콘텐츠와 더미 표시 규약

# 종료 콘텐츠 미노출
for key, rows in support.outreach_candidates().items():
    check(all(r["end_date"] >= str(support.today()) for r in rows),
          f"⑨ {key}: 종료된 콘텐츠가 후보에 없다")

# 더미 표시는 기계판독(dummy)만 — 화면·발송문 딱지 없음, 보호는 게이트(actions/actions.py::open_lms_screen)
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

# ⑨ 더미 게이트 — 화면에 채우면 직원이 그대로 발송 가능
# 등록 9건은 전부 비더미 → 검사용 자산을 끼워 확인(데이터를 테스트에 맞추지 않음)
from pension_agent.actions import actions as _actions

_probe = {"id": "TEST-DUMMY", "name": "게이트 검사용 더미 콘텐츠", "content_type": "이벤트",
          "url": "https://example.invalid/demo/gate-probe", "dummy": True,
          "lms_message": "(광고) 검사 고객님, KB국민은행입니다."}
support.ASSETS.append(_probe)
try:
    _msg = f"(광고) 검사 고객님, KB국민은행입니다.\n안내드려요.\n▶ {_probe['url']}\n{support.OPT_OUT}"
    _blocked = _actions.open_lms_screen("TEST", _msg, session_id="test-gate")
    check(_blocked["status"] == "blocked",
          "⑨ 더미 문구는 발송 화면에 채우는 것이 거부됨", str(_blocked["status"]))
    check(_blocked.get("asset_id") == "TEST-DUMMY",
          "⑨ 게이트는 안내 링크로 콘텐츠를 되짚는다 — 본문이 다시 쓰여도 빗나가지 않는다",
          str(_blocked.get("asset_id")))
finally:
    support.ASSETS.remove(_probe)

# 콘텐츠 DB 9건은 미차단 — 출처 있는 콘텐츠
for a in support.ASSETS:
    if a.get("content_type") not in ("이벤트", "세미나"):
        continue
    _real = support.lms_frame("검사", "안내드려요.", a.get("url") or "")
    check(_actions.open_lms_screen("TEST", _real, session_id="test-gate")["status"] != "blocked",
          f"⑨ {a['id']} 는 발송 화면 연계가 막히지 않는다")

check(_actions.open_lms_screen("TEST", "행내 자산과 무관한 직접 작성 문구입니다",
                             session_id="test-gate")["status"] != "blocked",
      "⑨ 더미 자산에서 온 문구가 아니면 막지 않음")
# 이 테스트 생성 세션이력만 삭제 — session_data 에 시연 픽스처 공존
from pension_agent.common import config as _config
(_config.SESSION_DATA_DIR / "TEST.json").unlink(missing_ok=True)

# ⑨ LMS 문구 골격 — 광고 표기·수신거부·안내 링크는 코드가 부착, LLM 재작성 후에도 유지
for p in PERSONAS:
    for key in ("event", "seminar"):
        item = FACTS[p.nm]["outreach"].get(key)
        if not item:
            continue
        msg = item["lms_message"]
        check(msg.startswith(f"{support.AD_PREFIX} {p.nm} 고객님"),
              f"⑨ {p.nm} {key}: 광고 표기와 고객명으로 시작한다", msg[:24])
        check(support.OPT_OUT in msg, f"⑨ {p.nm} {key}: 수신거부 안내가 있다")
        check(item["url"] and item["url"] in msg, f"⑨ {p.nm} {key}: 안내 링크가 문구에 있다")

# 평가용 정답(golden_dataset)은 LLM 입력 금지 — 별도 파일 + 프롬프트 필드 화이트리스트
_golden = {r["id"]: (r.get("fields") or {}).get("message")
           for r in shared_store().records("outreach_golden")}
check(len(_golden) == 9, "⑨ 평가용 정답 9건이 따로 적재된다", str(len(_golden)))
for a in support.ASSETS:
    if a.get("content_type") in ("이벤트", "세미나"):
        check(a["id"] in _golden, f"⑨ {a['id']} 의 평가용 정답이 있다")
        check(not any("golden" in k for k in a), f"⑨ {a['id']} 자산에는 정답이 없다", str(list(a)))
# 발송 문구는 규칙 본문(LLM 입력 없음) — 정답 문장 유출은 산출물에서 확인
_leaked = [(p.nm, k) for p in PERSONAS for k, item in (FACTS[p.nm].get("outreach") or {}).items()
           if item and (_golden.get(item["id"]) or "\0") in (item.get("lms_message") or "")]
check(not _leaked, "⑨ 평가용 정답 문장이 발송 문구로 새지 않는다", str(_leaked))

# 기준일 인자 — 과거 시점이면 그때 열린 콘텐츠
from datetime import date as _date

past = support.outreach_candidates(today=_date(2026, 12, 31))
check(all(r["end_date"] >= "2026-12-31" for r in past["event"]),
      "⑨ today 인자로 다른 기준일의 후보를 확인할 수 있다", str(len(past["event"])))


# 3. 화법 카드 시효성 수치 — 금리 슬롯 · 주장 성립 조건
# 원문(quotes) 수정 금지, 파생 텍스트만 현재값으로, 주장이 거짓이 될 카드는 제외

import copy

from pension_agent.knowledge import market as _market
from pension_agent.knowledge import kb as _kbmod
_kb = support.pitch_kb()

_slotted = [c for c in _kb.pitches if c.get("_rate_slots_applied") or c.get("_rate_notes")]
check(len(_slotted) == 5, "금리 슬롯 카드 5건", str(sorted(c["id"] for c in _slotted)))

# 원문 인용 보존 — 깨지면 «출처는 진짜, 수치는 가짜» 카드
for _pid, _was in (("pitch.k03.010", "2.50%"), ("pitch.k03.013", "4%"),
                   ("pitch.k03.069", "2.76%"), ("pitch.k03.070", "3.05%")):
    _c = next((x for x in _kb.pitches if x["id"] == _pid), None)
    check(_c is not None and any(_was in (q.get("text") or "") for q in (_c.get("quotes") or [])),
          f"{_pid}: 원문 인용에 '{_was}' 보존")

# 치환된 카드는 기준시점·더미 여부 동반
for _c in _slotted:
    check(_c.get("_rate_as_of") and _c.get("_rate_dummy") is not None,
          f"{_c['id']}: 금리 기준시점·더미 표시 보유", str(_c.get("_rate_as_of")))

# 판정 근거 없는 주장은 확인 요구
_verify = {c["id"] for c in _kb.pitches if c.get("_verify_first")}
check(_verify == {"pitch.k03.058", "pitch.k03.061", "pitch.k03.063", "pitch.k03.088"},
      "확인 요구 카드 4건", str(sorted(_verify)))
for _pid in sorted(_verify):
    _c = next(x for x in _kb.pitches if x["id"] == _pid)
    check("(확인 필요)" in support._card_talk(_c),
          f"{_pid}: 확인 요구가 ⑥ 재료에 노출")

# 조건 깨지면 후보군 제외 — 금리를 뒤집어 확인
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
