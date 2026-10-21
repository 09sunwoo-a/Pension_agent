"""산출물 감사 회귀 테스트 — 감사 결함 14건 + 부수 결함 3건 재유입 방지(각 검사는 결함 번호 대응).

페르소나 기준은 시연 목업 9케이스(customers.json ← IRP_Agent_더미고객_9Cases_v3.xlsx).
실행: python -m tests.test_engine
"""

from __future__ import annotations

import copy
import dataclasses
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent.common import config
from pension_agent.tools.customer_solution import engine
from pension_agent.tools.customer_solution import customer
from pension_agent.tools.customer_solution.customer import Profile, conditions

from pension_agent.prompts import briefing as prompts

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((bool(ok), label, detail))


def with_specs(mutate) -> tuple[list[str], list[str]]:
    """전략 정의 임시 변형 후 validate() 실행, 원본 복원."""
    original = engine.catalog.SPECS
    try:
        clone = copy.deepcopy(original)
        mutate(clone)
        engine.catalog.SPECS = clone
        return engine.validate()
    finally:
        engine.catalog.SPECS = original


def _spec(specs: list[dict], sid: str) -> dict:
    return next(s for s in specs if s["id"] == sid)


# 정의 검증 — 결함 재유입 시 ERROR

errors, warns = engine.validate()
check(not errors, "현행 정의 ERROR 0건", "; ".join(errors[:3]))

# 결함 B — 근거 오인용(세액공제 전략에 리밸런싱 콜 스크립트 근거)
e, _ = with_specs(lambda s: _spec(s, "st.tax_fill").__setitem__(
    "sources", ["pitch.k03.040"]))
check(any("근거오인용" in x for x in e), "근거 오인용 검출", "오인용 sources 가 통과됨")

# 존재하지 않는 문서 id
e, _ = with_specs(lambda s: _spec(s, "st.nod_set").__setitem__("sources", ["guide01.p99"]))
check(any("깨진참조" in x for x in e), "미실재 근거 검출")

# topic_keys 없이 sources 만이면 대조 불가 — 불통과
e, _ = with_specs(lambda s: _spec(s, "st.mat_reprice").__setitem__("topic_keys", []))
check(any("검증불가" in x for x in e), "topic_keys 누락 검출")

# 결함 8 — 행위 주체: 고객 운용지시를 직원 행동으로 서술 금지
e, _ = with_specs(lambda s: _spec(s, "st.mat_reprice").__setitem__("actor", None))
check(any("잘못된값" in x and "actor" in x for x in e), "행위 주체 누락 검출")

# 결함 9 — 용어: 상품 슬롯으로 끝나는 절은 분기 verb 필수
e, _ = with_specs(lambda s: _spec(s, "st.nod_set")["product"].pop("verb"))
check(any("절규약위반" in x for x in e), "동작 명사 누락 검출")

# 결함 14 — 기준선: 출처 없는 임의 기준선 차단
e, _ = with_specs(lambda s: _spec(s, "st.dep_shift")["impact"].__setitem__("base_ref", "BL.없음"))
check(any("깨진참조" in x for x in e), "미등록 기준선 검출")

check(all(b.get("source") for b in engine.BASELINES.values()),
      "모든 기준선에 출처 기재")

# 규약 B — 미확인 기능은 WARN
check(any("제안보류" in w for w in warns), "미확인 기능 경고 노출")


# 결함 1~3 — 존재하지 않는 절차·산출물

_all_text = " ".join(
    str(s.get(k, "")) for s in engine.SPECS
    for k in ("clause", "clause_if_asset", "clause_if_merged", "evidence")
)
check("계좌 진단 리포트" not in _all_text, "근거 없는 산출물('계좌 진단 리포트') 부재")
check("3개월 분할" not in _all_text, "근거 없는 절차('3개월 분할 조정') 부재")

# nch_autorebal 은 원천 근거 없어 제거 — 요건 'nch' 는 성립, validate() 가 [미대응] 경고
check(not any(s["id"] == "st.nch_autorebal" for s in engine.SPECS),
      "근거 없는 전략(자동 리밸런싱) 제거됨")
check(any("미대응" in w and "nch" in w for w in warns),
      "근거 없는 요건(nch)은 [미대응] 경고로 노출됨")

# 발송 콘텐츠 승인 전 발송 절 금지
check(engine.customer_facing_asset() is None, "미승인 자료가 발송 대상에서 제외됨")


# 로직화 — 시스템 보유 데이터 조회는 전략이 아닌 briefing(코드)

# 절에 조회 지시 금지 — 보유상품·수익률은 Profile 에 이미 존재
for _m in engine.LOOKUP_MARKERS:
    check(_m not in _all_text, f"절에 조회 지시어('{_m}') 부재")

# 조회 지시 재유입 시 validate() 가 [로직화대상]
e, _ = with_specs(lambda s: _spec(s, "st.dor_contact").__setitem__(
    "clause", "MyStar에서 보유상품·수익률을 확인하고 유선 접촉"))
check(any("로직화대상" in x for x in e), "절 내 조회 지시 검출")

# briefing 근거는 지식베이스 실재(validate 의 [깨진참조])
check(engine.BRIEFING_SOURCE == "proc.001", "briefing 근거 상수 고정")

# 접촉 전략 절은 조회 접두·못 박은 시한 표현 없이 행동만 — 시급성은 urgency·실행 순서
_chn = engine.BY_ID["st.chn_retain"]["clause"]
check(_chn == "유선 접촉", "이탈 방어 절은 행동만 담음", _chn)


# 결함 4~6 — 규정·적합성 게이트

# 규정 근거 전략은 sources 대신 regulation
check(engine.BY_ID["st.limit_fix"].get("regulation"), "한도 전략에 규정 근거 기재")

# 결함 6 — 예금자보호
check(all(r.get("depositor_protection") is not None
          for r in engine.PRODUCTS if r["payout"] == "원리금보장"),
      "원리금보장 상품에 예금자보호 여부 기재")


# 결함 7~12 — 논리·용어

# 근거 등급 분리 — SPECS 는 원천·규정 근거 전략만, 추론성 안전망(재진단)은 engine.SYSTEM_STRATEGIES
check(all(s["confidence"] in ("행내가이드", "규정") for s in engine.SPECS),
      "JSON 플레이북은 근거 있는 전략만 — '추정' 없음",
      "; ".join(f"{s['id']}={s['confidence']}" for s in engine.SPECS
                if s["confidence"] not in ("행내가이드", "규정")))
check(not any(s["id"] == "st.risk_reassess" for s in engine.SPECS),
      "재진단 전략은 JSON 플레이북에서 분리됨")
check("st.risk_reassess" in {s["id"] for s in engine.SYSTEM_STRATEGIES},
      "재진단 전략은 시스템 전략(코드)으로 따로 관리됨")
check("st.risk_reassess" in engine.BY_ID, "시스템 전략도 통합 인덱스(BY_ID)로 조회 가능")
# 시스템 전략도 validate 대상 — 절 종결·주체 규약
for _sys in engine.SYSTEM_STRATEGIES:
    check(_sys["clause"].endswith(engine.CLAUSE_ENDINGS), f"{_sys['id']}: 절 명사형 종결")
    check(_sys.get("actor") in ("직원", "고객"), f"{_sys['id']}: 행위 주체 지정")


# 결함 13~14 — 수치 근거

# 결함 13 — 세액공제율은 소득 구간별
check(abs(Profile(id="T", nm="T", ag=40, bal=1, rk="안정형", grade="낮은위험", port=[0, 0, 0, 0],
                  ret=0, retPct=50, dopt="설정", room=0, dorm=0, nchM=0,
                  income_bracket="5500이하").tax_credit_rate - 0.165) < 1e-9,
      "5,500만원 이하 공제율 16.5%")
check(abs(Profile(id="T", nm="T", ag=40, bal=1, rk="안정형", grade="낮은위험", port=[0, 0, 0, 0],
                  ret=0, retPct=50, dopt="설정", room=0, dorm=0, nchM=0,
                  income_bracket="5500초과").tax_credit_rate - 0.132) < 1e-9,
      "5,500만원 초과 공제율 13.2%")
check(abs(Profile(id="T", nm="T", ag=40, bal=1, rk="안정형", grade="낮은위험", port=[0, 0, 0, 0],
                  ret=0, retPct=50, dopt="설정", room=0, dorm=0, nchM=0,
                  income_bracket=None).tax_credit_rate - 0.132) < 1e-9,
      "구간 미확인 시 보수적 적용")


# 페르소나 회귀 — 시연 목업 9케이스 전원
# 요건 판정 스냅샷은 데이터(xlsx)+판정 로직 고정 — 원본 xlsx 교체 시 표도 갱신

from pension_agent.tools.customer_solution import solution
from pension_agent.tools.customer_solution.customer import PERSONAS

BY_NAME = {p.nm: p for p in PERSONAS}
check(len(PERSONAS) == 12, "목업 고객 적재 (xlsx 9케이스 + 데모 골든 케이스 3명)",
      str(len(PERSONAS)))

# 배지 골든셋 — 기획자 부여 첫 화면 배지(08_BADGES) Y/N 을 customer.conditions() 요건 5종이 정확히 재현
# 판정 기준·임계값은 세그먼트 condition_text 원문(customer.py 주석에 세그먼트 번호)
import json as _json

from pension_agent.common import config as _cfg

_BADGE_COND = {
    "미운용현금자산": "idl",                    # 세그 1(고유대 50%↑) + 26(1개월 변동 없음)
    "판매중단펀드보유": "hlt",                   # 세그 8
    "연금개시 요건충족 후 미개시": "pen",          # 세그 19-1
    "ISA만기자금 보유 고객": "isa",              # 세그 17
    "이탈위험관찰": "out",                      # 세그 1+26+34 (유입 임계값 미정 — >0)
}
_LEDGER = {r["id"]: r for r in
           _json.loads(_cfg.CUSTOMERS_JSON.read_text(encoding="utf-8"))["records"]}
for _p in PERSONAS:
    _c = set(conditions(_p))
    for _badge, _cond in _BADGE_COND.items():
        _want = _LEDGER[_p.id]["badges"].get(_badge) == "Y"
        check((_cond in _c) == _want, f"배지 골든셋: {_p.nm} {_badge}",
              f"요건 {_cond}={_cond in _c} vs 배지={_want}")

# 요건 판정 스냅샷 — 원본 xlsx 00_시연케이스 시연 포인트와 정합(scripts/import_customers.py)
_EXPECTED_CONDS = {
    "김현수": ["dep", "idl", "nod"],                   # 현금성 방치(고유대 75%, 1개월 무변동)
    "박지민": ["lim", "tax", "add"],                   # 실적배당 75% > 한도 70% + 세액공제 잔여
    "이준호": ["mat"],                                  # 예금 만기 D-17
    "최서윤": ["dor", "hlt"],                          # 8개월 미접촉 + 판매중단 펀드 보유
    "정민석": ["dor", "mis", "dep", "nch"],            # 공격투자형 + 원리금보장 100%
    "한지우": ["isa", "tax", "add"],                   # ISA 만기 D-12 + 세액공제 잔여 300만원
    "오세훈": ["nod", "pen"],                          # 연금개시 요건충족·미개시. 만기 10/9 는 D-46 이라 창(30일) 밖
    "윤가영": ["out", "nod"],                          # 고유대 52% + 최근 1개월 1.7억 유입
    "송도윤": ["dor", "hlt", "idl", "nod", "isa", "nch"],  # 3개 복합 케이스
}
# 룰베이스 정렬로 바뀐 칸(근거 보존용)
#   오세훈 dep 제외: 합산 69% < TG-202 80%(기획자 배지도 N)
#   김현수·최서윤 nch 제외: 10.1·7.3개월 < TG-201 12개월
# 김현수 dep 유지(합산 90%) — 배지는 고유계정대 축만 표기, 룰베이스는 중복 태그 허용(TG-004)
# → 배지 골든셋에서 「원리금보장 편중」 미고정(_BADGE_COND)
for nm, want in _EXPECTED_CONDS.items():
    got = conditions(BY_NAME[nm])
    check(got == want, f"{nm}: 요건 판정 스냅샷", f"{got} != {want}")

# 전원 산출 스모크 — 노출 개수 넓게(TOP_N 과 무관)
_WIDE = 99
FACTS = {p.nm: engine.prepare(p, top_n=_WIDE) for p in PERSONAS}

# 배지 요건에 대응 세그먼트 선언 필수 — 문제상황 노출 조건
_BADGE_SEG = {"idl": "3", "hlt": "8", "isa": "17", "pen": "19-1", "out": "34"}
for _cond, _no in _BADGE_SEG.items():
    _who = [p_ for p_ in PERSONAS if _cond in conditions(p_)]
    check(bool(_who), f"배지 요건 {_cond}: 성립 고객 존재")
    for p_ in _who:
        _nos = {s["no"] for s in FACTS[p_.nm]["problem_situations"]}
        check(_no in _nos, f"배지 요건 {_cond}: {p_.nm} 에 세그먼트 {_no} 문제상황",
              str(sorted(_nos)))

for nm, f in FACTS.items():
    p_ = BY_NAME[nm]
    bf = f.get("briefing") or {}
    check(bf.get("source") == engine.BRIEFING_SOURCE, f"{nm}: briefing 근거 {engine.BRIEFING_SOURCE}")
    check(str(p_.port[0]) in bf.get("보유구성", ""), f"{nm}: 보유구성이 코드로 산출됨",
          bf.get("보유구성", ""))
    check(bool(bf.get("운용수익률")), f"{nm}: 수익률이 briefing 에 제시됨")
    check(("만기도래" in bf) == (p_.matDD is not None), f"{nm}: 만기 정보 조건부 표기")
    # 펀드·ETF 경험은 화법 분기 기준(pitch.k03.025 ↔ 026)
    # ① 재료에 포함 ② 「없음」 누락 금지 — 빈 칸과 「없음」이 같아지면 025 미발동
    _a = p_.activity or {}
    if _a.get("holds_fund") is not None or _a.get("traded_fund") is not None:
        _exp = bf.get("펀드·ETF 경험", "")
        check("펀드" in _exp, f"{nm}: 펀드 경험이 재료에 실림", _exp)
        _want = ("보유 중" if _a.get("holds_fund")
                 else "과거 매매 이력" if _a.get("traded_fund") else "경험 없음")
        check(_want in _exp, f"{nm}: 펀드 경험 표기가 원장과 맞음({_want})", _exp)
    # retPct 는 전원 None — 화면 문자열 유출 금지
    check(p_.retPct is None and "None" not in str(f["customer"].get("수익률", "")),
          f"{nm}: retPct=None 그레이스풀", str(f["customer"].get("수익률")))
    # 스타클럽 등급은 헤더에 표기
    check(f["customer"].get("스타클럽등급") == p_.club_grade, f"{nm}: 스타클럽등급 표기")
    # 산출 문장 규약 — 주체 반영·종결
    for it in f["items"]:
        fc = engine.final_clause(it)
        check(fc.endswith(("제안", "안내")) if it["actor"] == "고객" else True,
              f"{nm}: '{it['title']}' 주체 반영", fc[-20:])
    _cr = engine.compose_rule(f)
    check(_cr.endswith("하세요.") if f["items"]
          else _cr.startswith("제안 가능한 실행 항목이 없습니다"), f"{nm}: 문장 종결", _cr[:30])
    # 슬롯 전량 치환 · 카드 규약
    for it in f["items"]:
        for text in (it["clause"], it["evidence"], *it["evidence_extra"],
                     *it["card"].values()):
            hit = re.search(r"\{[a-z_0-9]+\}", text)
            check(hit is None, f"{nm}: '{it['title']}' 슬롯 전량 치환", hit.group() if hit else "")
        check(bool(it["card"]["headline"] and it["card"]["tag"] and it["card"]["benefit"]),
              f"{nm}: '{it['title']}' 카드 핵심 필드 존재")
    # 미성립 전략으로 정원 채우기 금지
    conds_ = {c.split(":")[0] for c in f["conditions"]}
    for it in f["items"]:
        spec = engine.BY_ID[it["id"]]
        check(spec.get("when") in conds_ or spec.get("trigger"),
              f"{nm}: '{it['title']}' 요건 성립 확인", str(spec.get("when")))

# 노출 개수 — 제안 1 + 예비 1, 좁혀도 후보 평가 동일
for p_ in PERSONAS:
    _d = engine.prepare(p_)
    check(len(_d["items"]) <= 1 and len(_d["alternatives"]) <= 1,
          f"{p_.nm}: 제안·예비 최대 1건", f"{len(_d['items'])}/{len(_d['alternatives'])}")
    check(len(_d["candidates"]) == len(FACTS[p_.nm]["candidates"]),
          f"{p_.nm}: 노출을 좁혀도 후보 평가는 동일")
    check({a["id"] for a in _d["alternatives"]}.isdisjoint({i["id"] for i in _d["items"]}),
          f"{p_.nm}: 예비와 제안이 겹치지 않음")

# 결함 13 재현 — 세액공제 대상액 ≤ 잔여 한도(박지민 잔여 300만원)
_tax = next((i for i in FACTS["박지민"]["items"] if i["id"] == "st.tax_fill"), None)
check(_tax is not None, "박지민: 세액공제 전략 성립")
if _tax:
    check(_tax["amount"] == engine.won(3_000_000), "박지민: 세액공제 대상액 = 잔여 한도 300만원",
          str(_tax["amount"]))
# «확인 필요»는 직원·고객 확인 항목만 — 저작자용 문구(자료 미등록) 혼입 금지(DEMO_STATUS 가 집계)
check(not any(("assets.json" in n or "customer_facing" in n)
              for p in PERSONAS for n in FACTS[p.nm]["needs_confirm"]),
      "확인 필요 목록에 저작자용 문장(assets.json·customer_facing)이 없다")
# 소득 구간은 양쪽 모두 측정 — 확인 고객은 «미노출», 지운 고객은 «노출»
# (실데이터에 컬럼이 없을 수 있음 — 조용히 숨기면 공제율 하락을 모름)
check(not any("총급여 구간 미확인" in n for n in FACTS["한지우"]["needs_confirm"]),
      "한지우: 소득 구간이 확인되면 확인 항목에 안 올린다")
_no_income = dataclasses.replace(BY_NAME["한지우"], income_bracket=None)
check(any("총급여 구간 미확인" in n for n in engine.prepare(_no_income)["needs_confirm"]),
      "소득 구간이 미확인이면 확인 항목으로 노출된다")

# 결함 5 재현 — 위험자산 70% 한도(박지민 실적배당 75%)
check("lim" in conditions(BY_NAME["박지민"]), "박지민: 위험자산 한도 초과 판정(75%)")
check(any(i["id"] == "st.limit_fix" for i in FACTS["박지민"]["items"]),
      "박지민: 한도 정리 전략 소집")
check(any(rg["regulation"] for rg in FACTS["박지민"]["regulations"]),
      "박지민: 위험자산 한도 규정 근거 노출")

# 투자성향 불일치 두 방향 — 축소는 mis_fix 성립, 편중은 0원이라 미성립
_soo = engine.prepare(Profile(  # 축소 방향 합성 케이스
    id="T", nm="T", ag=52, bal=90_000_000, rk="안정추구형", grade="낮은위험",
    port=[10, 8, 30, 52], ret=-3.2, retPct=None, dopt="미설정", room=0, dorm=30, nchM=2.0,
), top_n=_WIDE)
check(any(i["id"] == "st.mis_fix" for i in _soo["items"]), "축소 방향: 성향 정리 전략 성립")
check(not any(i["id"] == "st.limit_fix" for i in _soo["items"]), "축소 방향: 성향 기준이 한도 기준을 대체")
check("mis" in conditions(BY_NAME["정민석"]), "정민석: 편중 방향도 mis 요건 성립")
check(not any(i["id"] == "st.mis_fix" for i in FACTS["정민석"]["items"]),
      "정민석: 축소분 0원이면 mis_fix 미소집")
check(any("투자성향 불일치 정리 — 대상 금액 0원" in d for d in FACTS["정민석"]["dropped"]),
      "정민석: 0원 미소집 사유 기록")
check(any(i["id"] == "st.dep_shift" for i in FACTS["정민석"]["items"]),
      "정민석: 편중 해소 전략이 대신 선다")

# 만기 여러 건 가능 — 최근접 1건(matDD·matAmt)은 판정·배분 입력, 재료에는 전부
_MULTI = {"정민석": 2, "한지우": 2, "오세훈": 2}
for nm, cnt in _MULTI.items():
    p_ = BY_NAME[nm]
    check(len(p_.maturities) == cnt, f"{nm}: 만기 보유 {cnt}건", str(len(p_.maturities)))
    bf = FACTS[nm]["briefing"]["만기도래"]
    check(all(m["date"] in bf for m in p_.maturities),
          f"{nm}: 만기 전건이 브리핑 재료에 실림", bf)
    check(all(m["type"] in bf for m in p_.maturities),
          f"{nm}: 만기마다 상품 유형 표기(예금/GIC 분간)", bf)
for p_ in PERSONAS:
    if not p_.maturities:
        continue
    check(p_.matDate == p_.maturities[0]["date"] and p_.matDD == p_.maturities[0]["dd"],
          f"{p_.nm}: matDate·matDD 는 가장 가까운 만기", f"{p_.matDate} vs {p_.maturities[0]['date']}")
    check(p_.matAmt == sum(m["amount"] for m in p_.maturities if m["date"] == p_.matDate),
          f"{p_.nm}: matAmt 는 그 날짜 도래분 합", str(p_.matAmt))
# 재예치 전략은 최근접 만기분만 — 오세훈 만기를 창 안(D-10)으로 옮긴 사본에서 GIC 제외 여부 측정
from dataclasses import replace as _replace_p  # noqa: E402
_ose_p = _replace_p(BY_NAME["오세훈"], matDD=10)
_ose = next((i for i in engine.prepare(_ose_p, top_n=_WIDE)["items"] if i["id"] == "st.mat_reprice"), None)
check(_ose is not None and _ose["amount"] == engine.won(_ose_p.matAmt),
      "오세훈: 재예치 대상액은 가까운 예금 만기분만(먼 GIC 제외)",
      str(_ose and _ose["amount"]))

# 만기 요건 — D-17(이준호) 성립, D-46(오세훈) 창 밖
check("mat" in conditions(BY_NAME["이준호"]) and "mat" not in conditions(BY_NAME["오세훈"]),
      "이준호 D-17 만기 요건 성립 · 오세훈 D-46 은 창 밖")
check(any(i["id"] == "st.mat_reprice" for i in FACTS["이준호"]["items"]), "이준호: 만기 재예치 전략 소집")

# 결함 7 재현 — 예금 편중 해소가 기준선 이하 상품 미권유(김현수)
_dep = next((i for i in FACTS["김현수"]["items"] if i["id"] == "st.dep_shift"), None)
check(_dep is not None, "김현수: 예금 편중 해소 성립")
if _dep:
    _base_rate = engine.BASELINES["BL.bank_deposit"]["rate"]
    _rets = [engine._ret_of(r) for r in engine.PRODUCTS
             if r["name"] in [n.split("(")[0] for n in _dep["products"].values()]]
    check(all(v > _base_rate for v in _rets), "김현수: 기준선 초과 상품만 제안", str(_rets))

# 전체 브리핑 — 사실은 코드, 서술은 LLM 1회 + 검증(solution.py)
import contextlib  # noqa: E402,F401
import io  # noqa: E402,F401
from dataclasses import replace as _replace  # noqa: E402

check(solution.tier(solution.facts(BY_NAME["이준호"])) == "행내전략",
      "매칭 전략 있으면 tier=행내전략")
# 요건 없는 고객 합성(9케이스엔 없음) — 새 요건 추가 시 여기도 비울 것
_calm = _replace(BY_NAME["한지우"], room=0, isa=None)
check(not conditions(_calm), "합성 무요건 고객: 요건 0건", str(conditions(_calm)))
check(solution.tier(solution.facts(_calm)) == "미매칭", "요건이 없으면 tier=미매칭")

_saved = (solution.llm.available, solution.llm.generate)
_calls: list[str] = []
try:
    solution.llm.available = lambda: False
    solution.clear_cache()
    _d0 = solution.document(_calm)
    check(_d0["narrative"] is None and "제안 가능한 실행 항목이 없습니다" in _d0["markdown"],
          "LLM 없이: 서술 없이 사실 표 + '제안 항목 없음' 규칙 문장", _d0["reason"])
    check("### 고객 정보" in _d0["markdown"] and "### IRP 계좌 현황" in _d0["markdown"],
          "LLM 없이도 사실 부분(고객 정보·계좌 현황 표)은 나온다")

    solution.llm.available = lambda: True

    def _gen(prompt, system="", **kw):
        _calls.append(kw.get("name", ""))
        return ('{"recent": ["평가금액은 ' + BY_NAME["이준호"].nm + ' 고객 기준 재료의 값입니다.", '
                '"KB 특판 정기예금 연 9.99% 상품이 있습니다."], '
                '"focus": "만기 자금의 운용 방향을 확인합니다.", '
                '"why_now": "", "confirm": ["계속 운용할 계획인지"]}')
    solution.llm.generate = _gen
    solution.clear_cache()
    _d1 = solution.document(BY_NAME["이준호"])
    check(_calls == ["solution.narrative"], "전체 브리핑 한 편에 LLM 호출은 1회", str(_calls))
    check(_d1["narrative"] is not None and len(_d1["narrative"]["recent"]) == 1,
          "재료 밖 수치(9.99%)가 든 서술 문장만 버린다", str(_d1["narrative"]))
    check(any("9.99" in r for r in _d1["rejected"]), "버린 사유가 남는다", str(_d1["rejected"])[:80])
    check("9.99" not in _d1["markdown"] and "- [ ] 계속 운용할 계획인지" in _d1["markdown"],
          "문서에는 통과한 서술만 실린다")
    _again = solution.document(BY_NAME["이준호"])
    check(_calls == ["solution.narrative"] and _again["markdown"] == _d1["markdown"],
          "같은 고객·같은 날이면 다시 만들지 않는다(같은 서술)", str(_calls))
    check(solution.peek(BY_NAME["이준호"]) == _d1["narrative"], "peek(): 만든 서술을 돌려준다")
    check(solution.peek(BY_NAME["한지우"]) is None, "peek(): 만든 적 없으면 None (LLM 을 부르지 않는다)")
finally:
    solution.llm.available, solution.llm.generate = _saved
    solution.clear_cache()

# dorm=None 이어도 브리핑 정상(김현수는 상담이력 없음)
check(BY_NAME["김현수"].dorm is None, "김현수: 상담이력 없음 → dorm=None")
check("dor" not in conditions(BY_NAME["김현수"]), "dorm=None 이면 dor 요건 미성립")
check("None" not in str(FACTS["김현수"]["customer"]["최근접촉"]),
      "dorm=None 이 화면 문자열로 새지 않음", str(FACTS["김현수"]["customer"]["최근접촉"]))

# 산출물 형식 — 원화 기대효과 미노출
_art = solution.render(BY_NAME["이준호"], solution.facts(BY_NAME["이준호"]), None)
check("기대효과" not in _art, "산출물에 '기대효과' 표기 없음")
check(BY_NAME["이준호"].id not in _art, "산출물에 고객 식별번호를 싣지 않는다(게이트웨이 개인정보 필터)")


# 부수 결함

# 만기 요건은 D-30 이내만 — 본부 가이드 «만기 1개월 전 반드시 만기 안내»
_far = Profile(id="T", nm="T", ag=40, bal=100_000_000, rk="안정형", grade="낮은위험",
               port=[100, 0, 0, 0], ret=1.0, retPct=50, dopt="설정", room=0, dorm=0,
               nchM=0, matDD=300, matAmt=100_000_000)
check("mat" not in conditions(_far), "D-300 은 만기 요건 미성립")
_far.matDD = 45
check("mat" not in conditions(_far), "D-45 는 만기 요건 미성립(창 D-30)")
_far.matDD = 26
check("mat" in conditions(_far), "D-26 은 만기 요건 성립")

# 연금개시 계좌에 추가납 요건(add·tax) 미성립 — «연금개시 계좌 → 추가납 권유 금지»
_pens = Profile(id="T", nm="T", ag=60, bal=100_000_000, rk="안정형", grade="낮은위험",
                port=[100, 0, 0, 0], ret=1.0, retPct=50, dopt="설정", room=500, dorm=0,
                nchM=0, pension_paid_ytd=0, pension_started=True)
check("add" not in conditions(_pens) and "tax" not in conditions(_pens), "연금개시 계좌: add·tax 미성립",
      str(conditions(_pens)))
_pb = engine.prepare(_pens)["briefing"]
check("납입여력" not in _pb and "연금수령" in _pb, "연금개시 계좌: 납입여력 미노출 · 연금수령 사실 노출", str(_pb))
_pens.pension_started = False
check("tax" in conditions(_pens), "미개시 계좌(대조군): tax 성립")
check("납입여력" in engine.prepare(_pens)["briefing"], "미개시 계좌(대조군): 납입여력 노출")

# 「납입여력」(1,800만원 납입한도)과 「세액공제_잔여한도」(900만원 공제한도)는 다른 축 — 같은 수 금지
_room = Profile(id="T2", nm="T2", ag=52, bal=45_000_000, rk="위험중립형", grade="보통위험",
                port=[67, 33, 0, 0], ret=3.1, retPct=50, dopt="설정", room=500, dorm=0,
                nchM=0, pension_paid_ytd=4_000_000, paid_ytd_total=4_000_000)
check(_room.deposit_room == 14_000_000,
      "납입여력은 1,800만원 − 당해 실납입액이다 (세액공제 잔여한도가 아니다)",
      f"{_room.deposit_room:,}원")
_rb = engine.prepare(_room)["briefing"]
check("1,400만원" in _rb["납입여력"] and "500만원" in _rb["납입여력"],
      "브리핑이 두 축을 한 줄에서 갈라 적는다 (납입 가능액 · 그중 공제 대상)", _rb["납입여력"])
check(_rb["납입여력"].split("(")[0].strip() != f"{_room.room:,}만원",
      "납입여력의 값이 세액공제 잔여한도와 같은 수가 아니다", _rb["납입여력"])
# 잔여한도 소진 고객 — 납입여력은 대화형 재료(account_state)에만, 브리핑에 있으면 중복 금지
_full = Profile(id="T4", nm="T4", ag=61, bal=165_000_000, rk="안정추구형", grade="낮은위험",
                port=[69, 0, 21, 10], ret=6.2, retPct=50, dopt="미설정", room=0, dorm=0,
                nchM=0, pension_paid_ytd=9_000_000, paid_ytd_total=9_000_000)
_fp = engine.prepare(_full)
check("납입여력" not in _fp["briefing"] and "900만원" in _fp["account_state"].get("납입여력", ""),
      "잔여한도 0 고객: 화면엔 납입여력 없음 · 대화형 재료엔 납입 가능액 900만원",
      str(_fp["account_state"].get("납입여력")))
check("납입여력" not in engine.prepare(_room)["account_state"],
      "잔여한도가 남은 고객: 납입여력은 브리핑 한 곳에만 있다")
_full.pension_started = True
check("납입여력" not in engine.prepare(_full)["account_state"],
      "연금개시 계좌: 대화형 재료에도 납입여력 미노출")
# «ISA 만기자금 없음» ≠ «ISA 계좌 없음» — 판정 범위 동반. 값에 `·` 금지(되묻기 블록 구분자)
_isa = _fp["account_state"]["ISA_만기자금"]
check(_isa.startswith("없음") and "ISA 계좌" in _isa and "·" not in _isa,
      "ISA 만기자금 없음에 판정 범위(만기 예정 자금 기준 · 계좌 내역은 원장 밖)가 붙는다", _isa)
# 실납입액 컬럼 없으면 인정액 사용 — 여력 과대 산출 방지
check(Profile(id="T3", nm="T3", ag=50, bal=1, rk="안정형", grade="낮은위험",
              port=[100, 0, 0, 0], ret=0.0, retPct=50, dopt="설정", room=0, dorm=0,
              nchM=0).deposit_room == 18_000_000,
      "실납입액을 모르면 납입여력은 한도 전액이다(기본값 0)")


# 산출물 형식 — 효과 등급 · 출처 원본명

# 효과 등급은 EFFECT_BANDS 경계
check(engine._effect_grade(1.13) == "큼" and engine._effect_grade(0.63) == "보통"
      and engine._effect_grade(None) == "—", "수익률 개선폭 → 정성 등급 환산")

# 등급은 사람이 읽는 라벨로 노출
check(engine.effect_label("큼") == "수익 개선폭 큼"
      and engine.effect_label("—") == "수익 개선 해당 없음", "수익 개선폭 등급 라벨 명확화")

# 출처는 원본 문서명(doc_id 아님). 지식베이스 경로 있을 때만 검증
if engine.DOC_TITLES:
    # 같은 원천 문서면 카드 종류가 달라도 한 줄
    _same = engine.format_sources(["seg.01", "pitch.k03.040"])
    check(len(_same) == 1 and _same[0].startswith("개인형IRP 고객관리 가이드"),
          "다른 종류의 카드도 원본 문서명 한 줄로 묶임", str(_same))
    # 페이지 표기(pNN)는 색인 밖 id 폴백
    check(engine.format_sources(["guide01_yield_mgmt.p01", "guide01_yield_mgmt.p05"])
          == ["guide01_yield_mgmt (p1/p5)"], "색인 밖 id 는 doc_id·페이지로 폴백")
    _tax = engine.format_sources(["fact.k04.f2", "fact.k04.f3", "pitch.k03.029"])
    check(len(_tax) == 1 and not _tax[0].startswith("fact"),
          "fact 인용이 원본 문서로 병합", str(_tax))


# 승인 피드백 회귀 — 절 시한표현 · 상담 화법 주체 · 카드 규약

# Profile 산출 기한('D-{matDD}')은 허용, 못 박은 시한('금주 내'·'당장') 금지
for _m in engine.TIME_PRESSURE_MARKERS:
    for _s in engine.SPECS + engine.SYSTEM_STRATEGIES:
        for _k in ("clause", "clause_if_asset", "clause_if_merged"):
            check(_m not in (_s.get(_k) or ""),
                  f"{_s['id']}.{_k}: 시한 표현('{_m}') 부재")
# 재유입 시 validate() 가 [시급성문구]
e, _ = with_specs(lambda s: _spec(s, "st.chn_retain").__setitem__("clause", "금주 내 유선 접촉"))
check(any("시급성문구" in x for x in e), "절 내 시한 표현 검출")

# 화법은 직원(접촉) 전략에만, 내부 조회 지시어 없이
for _s in engine.SPECS + engine.SYSTEM_STRATEGIES:
    if _s.get("talk"):
        check(_s.get("actor") == "직원", f"{_s['id']}: talk 은 직원 전략에만", _s.get("actor"))
        for _m in engine.LOOKUP_MARKERS:
            check(_m not in _s["talk"], f"{_s['id']}: 화법에 조회 지시어('{_m}') 부재")
# 고객 주체 전략의 talk → [화법대상], 조회 지시어 → [로직화대상]
e, _ = with_specs(lambda s: _spec(s, "st.mat_reprice").__setitem__("talk", "임의 화법을 안내"))
check(any("화법대상" in x for x in e), "화법 주체 위반 검출")
e, _ = with_specs(lambda s: _spec(s, "st.chn_retain").__setitem__(
    "talk", "MyStar에서 확인하고 안내"))
check(any("로직화대상" in x and "talk" in x for x in e), "화법 내 조회 지시 검출")

# 카드 혜택(benefit)은 판단근거(evidence)와 다른 «왜 이로운가» 문구
check(engine.BY_ID["st.mat_reprice"]["benefit"] != engine.BY_ID["st.mat_reprice"]["evidence"],
      "benefit 은 evidence 와 구별되는 별도 문구")

# 핵심가치(tag)는 전략 성격에서 도출(하드코딩 아님)
check(engine._value_tag({"kind": "접촉"}, "—") == "관계 관리", "tag: 접촉 → 관계 관리")
check(engine._value_tag({"kind": "운용", "reduces_risk": True}, "—") == "위험 조정",
      "tag: 위험감축 → 위험 조정")
check(engine._value_tag({"kind": "납입", "impact": {"kind": "tax_credit"}}, "—") == "세제 혜택",
      "tag: 세액공제 → 세제 혜택")

# 서술 프롬프트 계약 — 재료 밖 값·재계산·권유 금지
_ptxt = prompts.NARRATIVE_SYSTEM + prompts.NARRATIVE_PROMPT
check("재료" in _ptxt and "계산하지 않습니다" in _ptxt, "프롬프트: 재료 밖 값·재계산 금지")
check("권유" in _ptxt, "프롬프트: 권유·수익 약속 금지")
check(all(k in _ptxt for k in ("recent", "focus", "why_now", "confirm")), "프롬프트: 서술 네 항목")


# 코드·데이터 상수 정합

_caps = [int(m) for m in re.findall(
    r'"credit_limit":\s*(\d+)',
    (config.STRATEGY_DATA_DIR / "strategies.json").read_text(encoding="utf-8"))]
check(_caps and all(v == customer.TAX_CREDIT_CAP_WON for v in _caps),
      "세액공제 한도가 코드·데이터에서 일치", f"{_caps} vs {customer.TAX_CREDIT_CAP_WON}")

# 노출 기본값 — 제안 1개(+예비 1개). 5개 나열 금지
check(engine.TOP_N == 1 and engine.ALT_N == 1, "노출 기본값 = 제안 1 + 예비 1",
      f"TOP_N={engine.TOP_N} ALT_N={engine.ALT_N}")


if __name__ == "__main__":
    failed = [r for r in _results if not r[0]]
    for ok, label, detail in _results:
        if not ok:
            print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))
    print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
    print("✅ 감사 회귀 테스트 통과" if not failed else "❌ 회귀 발생")
    raise SystemExit(1 if failed else 0)
