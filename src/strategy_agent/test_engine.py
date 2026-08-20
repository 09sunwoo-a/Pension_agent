"""산출물 감사 회귀 테스트.

2026-08-11 산출물 감사에서 확인된 결함 14건과 부수 결함 3건을 고정한다. 정의 파일이나
엔진 로직이 변경되어 동일 성격의 결함이 재유입되면 본 테스트가 실패한다.

각 테스트는 감사 결함 번호와 대응한다. 신규 전략·상품·근거를 추가할 때는 본
테스트를 함께 통과시켜야 한다.

실행: python test_engine.py
"""

from __future__ import annotations

import copy
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import engine  # noqa: E402
from customer import PERSONAS, Profile, conditions  # noqa: E402

# agent 는 여기서(위 engine.validate 로 sys.path 에 ../consult_agent 가 삽입되기 전에) 임포트한다.
# 두 에이전트가 동명의 prompts·llm·kb 모듈을 가지므로, 늦게 임포트하면 consult_agent 쪽이 잡힌다.
import agent  # noqa: E402

BY_NAME = {p.nm: p for p in PERSONAS}
FACTS = {p.nm: engine.prepare(p) for p in PERSONAS}

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((bool(ok), label, detail))


def with_specs(mutate) -> tuple[list[str], list[str]]:
    """전략 정의를 임시로 변형해 validate() 를 실행한다. 원본은 복원된다."""
    original = engine.SPECS
    try:
        clone = copy.deepcopy(original)
        mutate(clone)
        engine.SPECS = clone
        return engine.validate()
    finally:
        engine.SPECS = original


def _spec(specs: list[dict], sid: str) -> dict:
    return next(s for s in specs if s["id"] == sid)


def _products_of(item: dict) -> dict[str, dict]:
    """표기명으로부터 상품 레코드를 되찾는다."""
    return {lb: next(r for r in engine.PRODUCTS if r["name"] == n.split("(")[0])
            for lb, n in item["products"].items()}


# ─────────────────────────────────────────────────────────────
# 정의 검증 — 결함이 재유입되면 ERROR 로 잡히는가
# ─────────────────────────────────────────────────────────────

errors, warns = engine.validate()
check(not errors, "현행 정의 ERROR 0건", "; ".join(errors[:3]))

# 결함 B — 근거 오인용. 세액공제 전략이 리밸런싱 콜 스크립트를 근거로 달았던 사례.
e, _ = with_specs(lambda s: _spec(s, "st.tax_fill").__setitem__(
    "sources", ["guide01_yield_mgmt.p05"]))
check(any("근거오인용" in x for x in e), "근거 오인용 검출", "오인용 sources 가 통과됨")

# 존재하지 않는 문서 id
e, _ = with_specs(lambda s: _spec(s, "st.nod_set").__setitem__("sources", ["guide01.p99"]))
check(any("깨진참조" in x for x in e), "미실재 근거 검출")

# topic_keys 없이 sources 만 있으면 대조가 불가능하므로 통과시키지 않는다.
e, _ = with_specs(lambda s: _spec(s, "st.mat_reprice").__setitem__("topic_keys", []))
check(any("검증불가" in x for x in e), "topic_keys 누락 검출")

# 결함 8 — 행위 주체. 고객 운용지시를 직원 행동으로 서술하는 정의를 차단한다.
e, _ = with_specs(lambda s: _spec(s, "st.mat_reprice").__setitem__("actor", None))
check(any("잘못된값" in x and "actor" in x for x in e), "행위 주체 누락 검출")

# 결함 9 — 용어. 상품 슬롯으로 끝나는 절은 분기 verb 가 정의되어야 한다.
e, _ = with_specs(lambda s: _spec(s, "st.nod_set")["product"].pop("verb"))
check(any("절규약위반" in x for x in e), "동작 명사 누락 검출")

# 결함 14 — 기준선. 출처 없는 임의 기준선을 차단한다.
e, _ = with_specs(lambda s: _spec(s, "st.dep_shift")["impact"].__setitem__("base_ref", "BL.없음"))
check(any("깨진참조" in x for x in e), "미등록 기준선 검출")

check(all(b.get("source") for b in engine.BASELINES.values()),
      "모든 기준선에 출처 기재")

# 규약 B — 미확인 기능은 WARN 으로 노출된다.
check(any("제안보류" in w for w in warns), "미확인 기능 경고 노출")


# ─────────────────────────────────────────────────────────────
# 결함 1~3 — 존재하지 않는 절차·산출물
# ─────────────────────────────────────────────────────────────

_all_text = " ".join(
    str(s.get(k, "")) for s in engine.SPECS
    for k in ("clause", "clause_if_asset", "clause_if_merged", "evidence")
)
check("계좌 진단 리포트" not in _all_text, "근거 없는 산출물('계좌 진단 리포트') 부재")
check("3개월 분할" not in _all_text, "근거 없는 절차('3개월 분할 조정') 부재")

# nch_autorebal(자동 리밸런싱)은 원천 근거가 없어 전략 정의에서 제거되었다(정합성 정비).
# 요건 'nch' 자체는 여전히 성립하되, 대응 전략이 없으므로 validate() 가 [미대응] 으로 경고한다.
_lee = FACTS["이현우"]
check(not any(s["id"] == "st.nch_autorebal" for s in engine.SPECS),
      "근거 없는 전략(자동 리밸런싱) 제거됨")
check("nch" in conditions(BY_NAME["이현우"]), "요건 자체는 성립")
check(any("미대응" in w and "nch" in w for w in warns),
      "근거 없는 요건(nch)은 [미대응] 경고로 노출됨")

# 발송 콘텐츠가 승인되기 전에는 발송 절을 쓰지 않는다.
check(engine.customer_facing_asset() is None, "미승인 자료가 발송 대상에서 제외됨")
check("LMS" not in " ".join(i["clause"] for i in _lee["items"]),
      "미승인 상태에서 발송 절이 생성되지 않음")
check(any("발송 가능 자료" in n for n in _lee["needs_confirm"]), "발송 자료 확인 항목 노출")


# ─────────────────────────────────────────────────────────────
# 로직화 — 시스템 보유 데이터의 조회는 전략이 아니라 briefing(코드)으로 표현한다
# ─────────────────────────────────────────────────────────────

# 절에는 조회 지시가 남지 않는다. 보유상품·수익률은 Profile 로 이미 시스템에 들어와 있다.
for _m in engine.LOOKUP_MARKERS:
    check(_m not in _all_text, f"절에 조회 지시어('{_m}') 부재")

# 조회 지시가 절에 재유입되면 validate() 가 [로직화대상] 으로 잡는다.
e, _ = with_specs(lambda s: _spec(s, "st.dor_contact").__setitem__(
    "clause", "MyStar에서 보유상품·수익률을 확인하고 유선 접촉"))
check(any("로직화대상" in x for x in e), "절 내 조회 지시 검출")

# 조회 데이터는 briefing 으로 제시된다. 근거는 가이드 p02(상품 보유 현황 확인)이다.
for nm, f in FACTS.items():
    bf = f.get("briefing") or {}
    p = BY_NAME[nm]
    check(bf.get("source") == "guide01_yield_mgmt.p02", f"{nm}: briefing 근거 p02")
    check(str(p.port[0]) in bf.get("보유구성", ""), f"{nm}: 보유구성이 코드로 산출됨",
          bf.get("보유구성", ""))
    check(bool(bf.get("운용수익률")), f"{nm}: 수익률이 briefing 에 제시됨")
    check(("만기도래" in bf) == (p.matDD is not None), f"{nm}: 만기 정보 조건부 표기")

# briefing 근거는 지식베이스에 실재해야 한다(engine.validate 가 [깨진참조] 로 검사).
check(engine.BRIEFING_SOURCE == "guide01_yield_mgmt.p02", "briefing 근거 상수 고정")

# 접촉 전략의 절은 조회 접두도, 확정되지 않은 시한 표현도 없이 순수 행동만 남는다.
# (이현우 피드백: '금주 내' 처럼 절 서두에 못 박힌 시한 표현은 이질감을 준다. 시급성은
#  urgency 필드와 실행 순서로 표현한다.)
_chn = engine.BY_ID["st.chn_retain"]["clause"]
check(_chn == "유선 접촉", "이탈 방어 절은 행동만 담음", _chn)


# ─────────────────────────────────────────────────────────────
# 결함 4~6 — 규정·적합성 게이트
# ─────────────────────────────────────────────────────────────

# 결함 4 — 최소가입금액은 적립금이 아니라 '배분액' 과 대조한다.
# ELB(최소 5천만원)를 예로, 적립금이 한도를 넘어도 해당 전략 배분액이 미달하면 차단되어야 한다.
_elb = next(r for r in engine.PRODUCTS if r["id"] == "P09")  # KB ELB 지수연계 · 최소 5천만원
_kim_big = BY_NAME["김민수"]  # 적립금 1.8억 (>5천만원)
_ok_lo, _why_lo = engine.gate_amount(_elb, _kim_big, 36_000_000, None)  # 배분 3,600만원 < 최소
_ok_hi, _ = engine.gate_amount(_elb, _kim_big, 50_000_000, None)        # 배분 5,000만원 = 최소
check((not _ok_lo) and ("최소 가입금액" in _why_lo) and _ok_hi,
      "최소가입금액이 적립금이 아니라 배분액과 대조됨", _why_lo)
# 페르소나에서도 배분액 미달 분기의 상품이 차단 사유로 기록된다.
# 김민수 예금편중 해소 배분액 3,600만원 < ELB 최소 5천만원 → ELB 차단(적립금 1.8억과 무관).
check(any("ELB" in b and "최소 가입금액" in b and "배분액" in b
          for b in FACTS["김민수"]["blocked_products"]),
      "김민수: 배분액 미달 상품(ELB) 최소가입금액 차단 사유 기록")

# 결함 5 — 위험자산 70% 한도
check("lim" in conditions(BY_NAME["박지영"]), "박지영: 위험자산 한도 초과 판정(75%)")
check("lim" in conditions(BY_NAME["정수연"]), "정수연: 위험자산 한도 초과 판정(82%)")
check(BY_NAME["정수연"].risk_headroom_amt == 0, "한도 초과 시 여력 0")
_soo = FACTS["정수연"]
_risk_named = [
    n for i in _soo["items"] for lb, n in i["products"].items()
    if any(r["name"] == n.split("(")[0] and r.get("risk_asset") for r in engine.PRODUCTS)
]
check(not any(i["id"] == "st.nod_set" and any(
    r["name"] == n.split("(")[0] and r.get("risk_asset")
    for n in i["products"].values() for r in engine.PRODUCTS) for i in _soo["items"]),
    "정수연: 여력 0에서 디폴트옵션이 위험자산으로 지정되지 않음")
check(any("위험자산 한도 여력" in b for b in _soo["blocked_products"]),
      "위험자산 한도 차단 사유 기록")

# 규정 근거가 있는 전략은 sources 대신 regulation 을 갖는다.
check(engine.BY_ID["st.limit_fix"].get("regulation"), "한도 전략에 규정 근거 기재")

# 결함 6 — 예금자보호
check(any("예금자보호 한도" in c for c in _lee["cautions"]),
      "이현우: 보호한도 초과분 고지 항목 생성")
check(all(r.get("depositor_protection") is not None
          for r in engine.PRODUCTS if r["payout"] == "원리금보장"),
      "원리금보장 상품에 예금자보호 여부 기재")


# ─────────────────────────────────────────────────────────────
# 결함 7~12 — 논리·용어
# ─────────────────────────────────────────────────────────────

# 결함 8 — 산출 문장은 직원이 실행 가능한 형태여야 한다.
for nm, f in FACTS.items():
    for it in f["items"]:
        fc = engine.final_clause(it)
        ok = fc.endswith(("제안", "안내")) if it["actor"] == "고객" else True
        check(ok, f"{nm}: '{it['title']}' 주체 반영", fc[-20:])
    # 실행 항목이 있으면 '하세요.' 로 종결하고, 매칭 전략이 없으면 '제안 항목 없음' 안내로 종결한다.
    _cr = engine.compose_rule(f)
    check(_cr.endswith("하세요.") if f["items"]
          else _cr.startswith("제안 가능한 실행 항목이 없습니다"),
          f"{nm}: 문장 종결", _cr[:30])

# 결함 9 — 실적배당 상품에 '재예치' 를 붙이지 않는다.
for nm, f in FACTS.items():
    for it in f["items"]:
        spec = engine.BY_ID[it["id"]]
        for lb in it["products"]:
            row = next(r for r in engine.PRODUCTS if r["name"] == it["products"][lb].split("(")[0])
            verb = engine._verb(spec, lb, row)
            check(not (row["payout"] == "실적배당" and verb == "재예치"),
                  f"{nm}: '{row['name']}' 동작 명사 적정", verb)

# 결함 7 — 예금 편중 해소가 기준선 이하 상품을 권하지 않는다.
_kim = FACTS["김민수"]
_dep = next((i for i in _kim["items"] if i["id"] == "st.dep_shift"), None)
check(_dep is not None, "김민수: 예금 편중 해소 유지")
if _dep:
    base = engine.BASELINES["BL.bank_deposit"]["rate"]
    rets = [engine._ret_of(r) for r in engine.PRODUCTS
            if r["name"] in [n.split("(")[0] for n in _dep["products"].values()]]
    check(all(v > base for v in rets), "김민수: 기준선 초과 상품만 제안", str(rets))

# 결함 11 — 흡수된 전략의 근거가 보존된다. (정수연: 섹터·한도 전략이 성향 정리에 흡수됨)
check(any(i["evidence_extra"] for i in _soo["items"]), "흡수된 항목의 근거 보존")

# 흡수된 근거는 그 전략 자신의 파생값으로 렌더링되어야 한다.
# 흡수한 쪽의 값을 쓰면 정의되지 않은 슬롯이 '{sec_excess}' 처럼 원문으로 남는다.
import re  # noqa: E402

for nm, f in FACTS.items():
    for it in f["items"]:
        for text in (it["clause"], it["evidence"], *it["evidence_extra"]):
            hit = re.search(r"\{[a-z_0-9]+\}", text)
            check(hit is None, f"{nm}: '{it['title']}' 슬롯 전량 치환",
                  hit.group() if hit else "")
check(any("섹터 ETF" in i["clause"] for i in _soo["items"]),
      "정수연: 흡수 후에도 축소 대상이 절에 남음")

# 결함 12 — 만기 재예치와 예금 편중 해소의 병합
check(any("만기" in i["clause"] and "예금 100%" in i["clause"] for i in _lee["items"]),
      "이현우: 만기·편중이 하나의 의사결정으로 병합")
check(not any(i["id"] == "st.dep_shift" for i in _lee["items"]), "이현우: 편중 해소 중복 제거")
check(any("Mission 2" in i["evidence"] for i in _lee["items"]), "이현우: 가이드 근거 유지")

# 신설 경로 — 실적배당이 전량 차단되면 성향 재진단을 제안한다.
check(any(i["id"] == "st.risk_reassess" for i in _lee["items"]),
      "이현우: 투자성향 재진단 경로 제안")
check(not any(i["id"] == "st.risk_reassess" for i in _kim["items"]),
      "김민수: 실적배당 가능 시 재진단 미제안")

# 근거 등급 분리 — JSON 플레이북(SPECS)은 원천 소스·규정 근거 전략만 담고, 게이트 결과에
# 대한 '추론'(추정)인 조건부 안전망(재진단)은 engine.SYSTEM_STRATEGIES 로 따로 관리한다.
check(all(s["confidence"] in ("행내가이드", "규정") for s in engine.SPECS),
      "JSON 플레이북은 근거 있는 전략만 — '추정' 없음",
      "; ".join(f"{s['id']}={s['confidence']}" for s in engine.SPECS
                if s["confidence"] not in ("행내가이드", "규정")))
check(not any(s["id"] == "st.risk_reassess" for s in engine.SPECS),
      "재진단 전략은 JSON 플레이북에서 분리됨")
check("st.risk_reassess" in {s["id"] for s in engine.SYSTEM_STRATEGIES},
      "재진단 전략은 시스템 전략(코드)으로 따로 관리됨")
check("st.risk_reassess" in engine.BY_ID, "시스템 전략도 통합 인덱스(BY_ID)로 조회 가능")
# 분리 후에도 렌더링 필드(카드·근거)를 갖춰 산출물에 정상 노출된다.
_rr = next(i for i in _lee["items"] if i["id"] == "st.risk_reassess")
check(bool(_rr["card"]["headline"] and _rr["card"]["benefit"] and _rr["evidence"]),
      "재진단 시스템 전략도 카드·근거 완비")
# 시스템 전략도 구조 검증(validate)의 대상이다 — 절 종결·주체 규약을 지킨다.
for _sys in engine.SYSTEM_STRATEGIES:
    check(_sys["clause"].endswith(engine.CLAUSE_ENDINGS), f"{_sys['id']}: 절 명사형 종결")
    check(_sys.get("actor") in ("직원", "고객"), f"{_sys['id']}: 행위 주체 지정")

# 위험자산 목표 비중은 가장 강한 기준 하나만 적용한다(합산 축소 방지).
check(not any(i["id"] == "st.limit_fix" for i in _soo["items"]),
      "정수연: 성향 기준이 한도 기준을 대체")
check(any(i["id"] == "st.limit_fix" for i in FACTS["박지영"]["items"]),
      "박지영: 성향 불일치 미성립 시 한도 전략 유지")


# ─────────────────────────────────────────────────────────────
# 결함 13~14 — 수치 근거
# ─────────────────────────────────────────────────────────────

# 결함 13 — 세액공제율은 소득 구간을 따른다.
check(abs(Profile(id="T", nm="T", ag=40, bal=1, rk="안정형", grade="낮은위험", port=[0, 0, 0, 0],
                  ret=0, retPct=50, dopt="설정", room=0, dorm=0, nchM=0,
                  income_bracket="5500이하").tax_credit_rate - 0.165) < 1e-9,
      "5,500만원 이하 공제율 16.5%")
check(abs(BY_NAME["김민수"].tax_credit_rate - 0.132) < 1e-9, "5,500만원 초과 공제율 13.2%")
check(abs(BY_NAME["정수연"].tax_credit_rate - 0.132) < 1e-9, "구간 미확인 시 보수적 적용")
check(any("총급여 구간 미확인" in n for n in _soo["needs_confirm"]), "구간 미확인 노출")

# 세액공제 대상액은 잔여 한도를 넘지 않는다.
_tax = next(i for i in _kim["items"] if i["id"] == "st.tax_fill")
check(_tax["amount"] == engine.won(9_000_000 - BY_NAME["김민수"].pension_paid_ytd),
      "세액공제 대상액이 잔여 한도로 제한", str(_tax["amount"]))

# 결함 14 — 분기 미확정 기대효과는 합계에 산입하지 않는다.
_mat = next(i for i in _kim["items"] if i["id"] == "st.mat_reprice")
check(len(_mat["products"]) > 1 and _mat["impact"] is None and _mat["impact_range"],
      "분기 미확정 시 확정값 대신 범위 제시")
check(_kim["impact_total"] == sum(
    i["impact"] for i in _kim["items"] if i["impact"] and i["impact"] > 0),
    "합계에 확정 분기만 산입")
check(_kim["impact_pending"] is not None, "미확정 범위 별도 표기")


# ─────────────────────────────────────────────────────────────
# 부수 결함
# ─────────────────────────────────────────────────────────────

# 정렬 — 확정금리만 보유한 상품이 exp_return 부재로 밀리지 않는다.
_p12 = next(r for r in engine.PRODUCTS if r["id"] == "P12")
_p10 = next(r for r in engine.PRODUCTS if r["id"] == "P10")
_picked = engine._best(BY_NAME["김민수"], {"apply_preference": False},
                       {"order": "-exp_return"}, [_p10, _p12])
check(_picked["id"] == "P12", "원리금보장 상품 정렬 정상", _picked["id"])

# 축약 — 인접하지 않은 절을 가리키는 대명사적 축약을 쓰지 않는다.
# 구 구현은 세 절 건너뛴 뒤에도 '위와 같은 기준' 을 붙여 지시 대상이 불명확했다.
for nm, f in FACTS.items():
    cls = [i["clause"] for i in f["items"]]
    check(not any("위와 같은 기준" in c for c in cls), f"{nm}: 구 축약 표현 부재")
    for idx, c in enumerate(cls):
        if "앞 항목과 동일" in c:
            prev = engine._action(engine.BY_ID[f["items"][idx - 1]["id"]],
                                  _products_of(f["items"][idx - 1]))
            check(idx > 0 and prev, f"{nm}: 축약이 직전 절을 가리킴", c)

# 만기 요건은 D-90 이내에서만 성립한다.
_far = Profile(id="T", nm="T", ag=40, bal=100_000_000, rk="안정형", grade="낮은위험",
               port=[100, 0, 0, 0], ret=1.0, retPct=50, dopt="설정", room=0, dorm=0,
               nchM=0, matDD=300, matAmt=100_000_000)
check("mat" not in conditions(_far), "D-300 은 만기 요건 미성립")
check("mat" in conditions(BY_NAME["이현우"]), "D-22 는 만기 요건 성립")

# 요건이 성립하지 않는 전략을 정원 충족 목적으로 채우지 않는다.
for nm, f in FACTS.items():
    conds = {c.split(":")[0] for c in f["conditions"]}
    for it in f["items"]:
        spec = engine.BY_ID[it["id"]]
        check(spec.get("when") in conds or spec.get("trigger"),
              f"{nm}: '{it['title']}' 요건 성립 확인", str(spec.get("when")))


# ─────────────────────────────────────────────────────────────
# 산출물 형식 — 원화 기대효과 제외 · 출처 원본명 · 판단근거 1~3문장 · 다른 제안
# ─────────────────────────────────────────────────────────────

_GRADES = {"큼", "보통", "작음", "—"}

for nm, f in FACTS.items():
    # 판단근거는 선정 항목의 근거 중 핵심 1~3문장.
    check(len(f["rationale"]) <= 3, f"{nm}: 판단근거 최대 3문장", str(len(f["rationale"])))
    if f["items"]:
        check(f["rationale"] == [i["evidence"] for i in f["items"] if i["evidence"]][:3],
              f"{nm}: 판단근거가 선정 항목 근거와 일치")
    # 효과는 원화 금액이 아니라 정성 등급으로만 노출된다.
    for it in f["items"]:
        check(it["effect_grade"] in _GRADES, f"{nm}: '{it['title']}' 효과 등급값", it["effect_grade"])
    # 다른 제안 = 메인 문장 밖 전략, 최대 ALT_N 건, 수익률 개선폭 내림차순.
    check(len(f["alternatives"]) <= engine.ALT_N, f"{nm}: 다른 제안 최대 {engine.ALT_N}건")
    check({a["id"] for a in f["alternatives"]}.isdisjoint({i["id"] for i in f["items"]}),
          f"{nm}: 다른 제안은 메인 문장 밖 전략")
    _ys = [a["yield_delta"] if a["yield_delta"] is not None else float("-inf")
           for a in f["alternatives"]]
    check(_ys == sorted(_ys, reverse=True), f"{nm}: 다른 제안 수익률 개선폭 내림차순", str(_ys))
    for a in f["alternatives"]:
        check(a["effect_grade"] in _GRADES, f"{nm}: 다른 제안 효과 등급값", a["effect_grade"])

# 효과 등급은 수익률 개선폭 경계(EFFECT_BANDS)를 따른다.
check(engine._effect_grade(1.13) == "큼" and engine._effect_grade(0.63) == "보통"
      and engine._effect_grade(None) == "—", "수익률 개선폭 → 정성 등급 환산")

# 등급 문자는 산출물에 사람이 읽는 라벨로 노출된다('큼'·'—' 만으로는 의미가 불명확하므로).
check(engine.effect_label("큼") == "수익 개선폭 큼"
      and engine.effect_label("—") == "수익 개선 해당 없음", "수익 개선폭 등급 라벨 명확화")

# 출처는 코드용 doc_id 가 아니라 원본 문서명으로 표기된다(req 2).
# 지식베이스 경로가 있을 때만 검증(단독 실행 환경에서는 doc_id 폴백).
if engine.DOC_TITLES:
    check(engine.format_sources(["guide01_yield_mgmt.p01", "guide01_yield_mgmt.p05"])
          == ["개인형IRP 고객관리 가이드 Series 1 — IRP 수익률 관리 (p1/p5)"],
          "페이지 인용이 원본 문서명으로 표기")
    _tax = engine.format_sources(
        ["fact.irp.tax_credit_rate", "fact.irp.tax_credit_limit", "ch01_new.p01"])
    check(len(_tax) == 1 and not _tax[0].startswith("fact"),
          "fact 인용이 원본 문서로 병합", str(_tax))
    check(all("guide01_yield_mgmt" not in s for s in _lee["source_titles"]),
          "산출물 출처에 코드용 doc_id 미노출", str(_lee["source_titles"]))

# 원화 기대효과는 최종 산출물(문자열)에 노출되지 않는다(req 1).
import contextlib  # noqa: E402
import io  # noqa: E402

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    agent._print(agent.propose(BY_NAME["이현우"], use_llm=False))
_art = _buf.getvalue()
check("기대효과" not in _art, "산출물에 '기대효과' 표기 없음")
check(all(s in _art for s in ("판단근거", "근거 문서", "다른 제안")),
      "산출물에 신규 섹션(판단근거·근거 문서·다른 제안) 포함")


# ─────────────────────────────────────────────────────────────
# 승인 피드백 3건 회귀 — 카드형 제안 · 절 시한표현 · 상담 화법 · 규정 근거 추적
#
# 예시 문구를 그대로 검사하지 않고, 각 피드백에서 추출한 '범용 의도' 를 검증한다.
#   김민수 — 제안이 길고 지엽적: 항목별 카드(헤드라인·핵심가치·추천상품/대상·한 줄 혜택)로 압축.
#   이현우 — '금주 내' 서두 이질감 + 화법 부재: 절에 확정 안 된 시한표현 금지 + 상담 화법 노출.
#   정수연 — 적합성 원칙 출처 불명: 적합성 원칙의 규정 근거를 명시·추적 가능하게 노출.
# ─────────────────────────────────────────────────────────────

import prompts  # noqa: E402


def _artifact(nm: str) -> str:
    """_print 산출물(문자열)을 캡처한다. LLM 미사용 결정적 경로로 검증한다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        agent._print(agent.propose(BY_NAME[nm], use_llm=False))
    return buf.getvalue()


# ── 이현우: 절에 확정되지 않은 시한 표현을 넣지 않는다 ────────────────────────
# 'D-{matDD}' 처럼 Profile 값으로 산출되는 기한은 허용하되, '금주 내'·'당장' 같은 못 박은
# 시한은 금지한다. 시급성은 urgency 필드와 실행 순서로 표현한다.
for _m in engine.TIME_PRESSURE_MARKERS:
    for _s in engine.SPECS + engine.SYSTEM_STRATEGIES:
        for _k in ("clause", "clause_if_asset", "clause_if_merged"):
            check(_m not in (_s.get(_k) or ""),
                  f"{_s['id']}.{_k}: 시한 표현('{_m}') 부재")
for nm, f in FACTS.items():
    for it in f["items"]:
        for _m in engine.TIME_PRESSURE_MARKERS:
            check(_m not in it["clause"], f"{nm}: '{it['title']}' 절 시한 표현 부재", it["clause"])
# 재유입되면 validate() 가 [시급성문구] 로 잡는다.
e, _ = with_specs(lambda s: _spec(s, "st.chn_retain").__setitem__("clause", "금주 내 유선 접촉"))
check(any("시급성문구" in x for x in e), "절 내 시한 표현 검출")


# ── 이현우: 접촉 전략에 상담 추천 화법을 노출한다 ─────────────────────────────
_lee = FACTS["이현우"]
check(bool(_lee["talking_points"]), "이현우: 상담 화법 노출")
check(all(tp["talk"].strip() for tp in _lee["talking_points"]), "상담 화법 내용 존재")
check("상담 화법" in _artifact("이현우"), "이현우: 산출물에 상담 화법 섹션")
# 화법은 직원(접촉) 전략에만, 내부 조회 지시어 없이 정의된다.
for _s in engine.SPECS + engine.SYSTEM_STRATEGIES:
    if _s.get("talk"):
        check(_s.get("actor") == "직원", f"{_s['id']}: talk 은 직원 전략에만", _s.get("actor"))
        for _m in engine.LOOKUP_MARKERS:
            check(_m not in _s["talk"], f"{_s['id']}: 화법에 조회 지시어('{_m}') 부재")
# talk 이 고객 주체 전략에 붙으면 [화법대상], 조회 지시어가 들어가면 [로직화대상] 으로 잡는다.
e, _ = with_specs(lambda s: _spec(s, "st.mat_reprice").__setitem__("talk", "임의 화법을 안내"))
check(any("화법대상" in x for x in e), "화법 주체 위반 검출")
e, _ = with_specs(lambda s: _spec(s, "st.chn_retain").__setitem__(
    "talk", "MyStar에서 확인하고 안내"))
check(any("로직화대상" in x and "talk" in x for x in e), "화법 내 조회 지시 검출")


# ── 정수연: 적합성 원칙의 규정 근거를 명시·추적 가능하게 노출한다 ───────────────
_soo = FACTS["정수연"]
_mis = next((i for i in _soo["items"] if i["id"] == "st.mis_fix"), None)
check(_mis is not None, "정수연: 투자성향 불일치 정리 유지")
check(bool(_mis and _mis["regulation"]), "정수연: 적합성 정리에 규정 근거 기재")
check(any(("적합성" in rg["regulation"]) or ("금융소비자보호" in rg["regulation"])
          for rg in _soo["regulations"]), "정수연: 적합성 원칙의 규정 출처 노출")
_soo_art = _artifact("정수연")
check("근거 규정" in _soo_art, "정수연: 산출물에 근거 규정 섹션 노출")
check(("금융소비자보호" in _soo_art) or ("적합성 원칙" in _soo_art),
      "정수연: 규정 출처가 산출물에 드러남")
# 규정 근거를 가진 선정 항목은 facts.regulations 로 일반 노출된다(박지영 한도 규정도 동일).
check(any(rg["regulation"] for rg in FACTS["박지영"]["regulations"]),
      "박지영: 위험자산 한도 규정 근거도 노출")
for nm, f in FACTS.items():
    for rg in f["regulations"]:
        check(bool(rg["regulation"]), f"{nm}: 규정 근거 문구 비어있지 않음")


# ── 김민수: 제안을 항목별 카드로 압축한다 ────────────────────────────────────
_CARD_KEYS = {"headline", "tag", "product", "target", "action", "benefit"}
for nm, f in FACTS.items():
    for it in f["items"]:
        c = it["card"]
        check(_CARD_KEYS <= set(c), f"{nm}: '{it['title']}' 카드 필드 완비", str(set(c)))
        check(bool(c["headline"] and c["tag"] and c["benefit"]),
              f"{nm}: '{it['title']}' 카드 핵심 필드 존재")
        # 카드에 미치환 슬롯이 남지 않는다.
        for v in c.values():
            hit = re.search(r"\{[a-z_0-9]+\}", v)
            check(hit is None, f"{nm}: '{it['title']}' 카드 슬롯 전량 치환", hit.group() if hit else "")
        # 상품·대상은 확정된 재료에서만 온다(환각 차단).
        check(c["product"] == ", ".join(it["products"].values()),
              f"{nm}: '{it['title']}' 카드 상품이 확정 재료와 일치", c["product"])
        check(c["target"] == (it["amount"] or ""),
              f"{nm}: '{it['title']}' 카드 대상이 확정 금액과 일치")
        # 상품이 있으면 추천상품/대상으로, 없으면 행동(action)으로 — 정확히 하나로 구체를 채운다.
        check(bool(c["product"]) != bool(c["action"]),
              f"{nm}: '{it['title']}' 카드 구체 표기 일관",
              f"product={c['product']!r} action={c['action']!r}")

# 카드 혜택(benefit)은 판단근거(evidence)와 구별되는 '왜 이로운가' 문구다(단순 재사용 아님).
check(engine.BY_ID["st.mat_reprice"]["benefit"] != engine.BY_ID["st.mat_reprice"]["evidence"],
      "benefit 은 evidence 와 구별되는 별도 문구")
for nm, f in FACTS.items():
    if f["items"]:
        check(any(it["card"]["benefit"] != it["evidence"] for it in f["items"]),
              f"{nm}: 카드 혜택이 판단근거와 구별됨")

# 핵심가치(tag)는 전략 성격에서 도출된다(개별 문구 하드코딩 아님).
check(engine._value_tag({"kind": "접촉"}, "—") == "관계 관리", "tag: 접촉 → 관계 관리")
check(engine._value_tag({"kind": "운용", "reduces_risk": True}, "—") == "위험 조정",
      "tag: 위험감축 → 위험 조정")
check(engine._value_tag({"kind": "납입", "impact": {"kind": "tax_credit"}}, "—") == "세제 혜택",
      "tag: 세액공제 → 세제 혜택")

# 산출물은 단일 장문 지시가 아니라 카드 구조(추천 상품·대상)로 제시된다.
_kim_art = _artifact("김민수")
check("추천 상품" in _kim_art and "대상" in _kim_art, "김민수: 산출물이 카드(추천 상품·대상) 구조")
check("[전략 제안]" in _kim_art, "김민수: 전략 제안 섹션 유지")
# 등급 문자('큼/보통/작음/—')는 카드 핵심가치 자리에 노출되지 않는다(핵심가치는 _value_tag).
for nm, f in FACTS.items():
    for it in f["items"]:
        check(it["card"]["tag"] not in _GRADES,
              f"{nm}: '{it['title']}' 핵심가치가 수익 개선폭 등급이 아님", it["card"]["tag"])
# 다른 제안의 효과 표기는 암호형 등급이 아니라 풀어쓴 라벨로 노출된다.
check("[효과 —]" not in _kim_art and "효과 —" not in _artifact("이현우"),
      "다른 제안에 암호형 등급('효과 —') 미노출")

# 출력 포맷 변경은 프롬프트(prompts.py)에 계약으로 반영된다.
_ptxt = prompts.SYSTEM + prompts.WRITE_PROMPT
check("카드" in _ptxt, "프롬프트: 카드형 포맷 계약 명시")
check(("2~3문장" in _ptxt) or ("짧게" in _ptxt), "프롬프트: 간결 요약 지시(AI 브리핑 2~3문장 길이 제약)")
check(("명령" in _ptxt) and ("제안" in _ptxt), "프롬프트: 매수 명령이 아닌 제안 어조 지시")


# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# Tier2 / provenance — 행내 전략 미매칭 시 tier 로 근거 계층을 구분·표기한다
#
# 매칭 전략이 있으면 tier='행내전략'. 없으면 LLM 이 대신 답하되(Tier2), 그것이 행내 근거 없는
# 'LLM판단' 임을 tier 로 남겨 사람이 검토·관리할 수 있게 한다. LLM 미사용/미설정 시에는
# tier='미매칭' 으로 '제안 항목 없음' 을 반환한다.
# ─────────────────────────────────────────────────────────────

check(agent.propose(BY_NAME["김민수"], use_llm=False)["tier"] == "행내전략",
      "매칭 전략 있으면 tier=행내전략")
for _nm in ("한서진", "오지호"):
    _r = agent.propose(BY_NAME[_nm], use_llm=False)
    check(not _r["facts"]["items"], f"{_nm}: 매칭 전략 없음(items 0)")
    check(_r["tier"] == "미매칭", f"{_nm}: LLM 미사용 시 tier=미매칭", _r["tier"])
    check(_r["source"] == "규칙" and _r["sentence"].startswith("제안 가능한 실행 항목이 없습니다"),
          f"{_nm}: 미매칭 시 '제안 항목 없음' 규칙 문장")
# 오지호는 납입여력이 briefing 에 남아 Tier2 LLM 이 근거로 삼을 수 있다.
check("납입여력" in agent.propose(BY_NAME["오지호"], use_llm=False)["facts"]["briefing"],
      "오지호: 납입여력이 briefing 에 노출(Tier2 재료)")

# LLM 을 모의로 붙이면 미매칭 고객이 tier='LLM판단' 으로 승격되고, verify 로 재료 이탈은 차단된다.
_saved = (agent.llm.available, agent.llm.generate)
try:
    agent.llm.available = lambda: True
    agent.llm.generate = lambda prompt, system="": (
        '{"insight": "현 구성 양호", '
        '"sentence": "보유 구성과 수익률이 양호해 특별한 조치는 필요하지 않습니다."}')
    _r2 = agent.propose(BY_NAME["한서진"], use_llm=True)
    check(_r2["tier"] == "LLM판단" and _r2["source"] == "LLM",
          "미매칭 + LLM → tier=LLM판단", f'{_r2["tier"]}/{_r2["source"]}')
    check(_r2["sentence"].startswith("보유 구성"), "Tier2 LLM 문장이 채택됨")
    # 재료(보유현황) 밖의 상품·수치를 지어내면 폴백되고 tier 는 승격되지 않는다.
    agent.llm.generate = lambda prompt, system="": (
        '{"insight": "x", "sentence": "KB 특판 정기예금 연 9.99% 가입을 권합니다."}')
    _r3 = agent.propose(BY_NAME["한서진"], use_llm=True)
    check(_r3["tier"] == "미매칭" and bool(_r3["rejected"]),
          "재료 이탈 산출은 폴백(tier=미매칭)", str(_r3["rejected"])[:40])
finally:
    agent.llm.available, agent.llm.generate = _saved


# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    failed = [r for r in _results if not r[0]]
    for ok, label, detail in _results:
        if not ok:
            print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))
    print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
    print("✅ 감사 회귀 테스트 통과" if not failed else "❌ 회귀 발생")
    raise SystemExit(1 if failed else 0)
