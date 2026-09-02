"""산출물 감사 회귀 테스트.

2026-08-11 산출물 감사에서 확인된 결함 14건과 부수 결함 3건을 고정한다. 정의 파일이나
엔진 로직이 변경되어 동일 성격의 결함이 재유입되면 본 테스트가 실패한다.

각 테스트는 감사 결함 번호와 대응한다. 신규 전략·상품·근거를 추가할 때는 본
테스트를 함께 통과시켜야 한다.

페르소나 절은 시연용 목업 9케이스(customers.json ← IRP_Agent_더미고객_9Cases_v3.xlsx)를
기준으로 다시 세웠다 — 옛 C1~C6 시절의 세부 수치 단언 일부(예금자보호 고지·만기/편중 병합·
흡수 근거 보존)는 새 데이터에 그 상황이 없어 세우지 않았고, 그 상황이 성립하는 케이스가
다시 들어오면 그때 단언을 추가한다.

실행: python -m tests.test_engine
"""

from __future__ import annotations

import copy
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import config
from pension_agent.strategy_agent import engine
from pension_agent.strategy_agent import customer
from pension_agent.strategy_agent.customer import Profile, conditions

from pension_agent.strategy_agent import prompts

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((bool(ok), label, detail))


def with_specs(mutate) -> tuple[list[str], list[str]]:
    """전략 정의를 임시로 변형해 validate() 를 실행한다. 원본은 복원된다."""
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


# ─────────────────────────────────────────────────────────────
# 정의 검증 — 결함이 재유입되면 ERROR 로 잡히는가
# ─────────────────────────────────────────────────────────────

errors, warns = engine.validate()
check(not errors, "현행 정의 ERROR 0건", "; ".join(errors[:3]))

# 결함 B — 근거 오인용. 세액공제 전략이 리밸런싱 콜 스크립트를 근거로 달았던 사례.
e, _ = with_specs(lambda s: _spec(s, "st.tax_fill").__setitem__(
    "sources", ["pitch.k03.040"]))
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
check(not any(s["id"] == "st.nch_autorebal" for s in engine.SPECS),
      "근거 없는 전략(자동 리밸런싱) 제거됨")
check(any("미대응" in w and "nch" in w for w in warns),
      "근거 없는 요건(nch)은 [미대응] 경고로 노출됨")

# 발송 콘텐츠가 승인되기 전에는 발송 절을 쓰지 않는다.
check(engine.customer_facing_asset() is None, "미승인 자료가 발송 대상에서 제외됨")


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

# briefing 근거는 지식베이스에 실재해야 한다(engine.validate 가 [깨진참조] 로 검사).
check(engine.BRIEFING_SOURCE == "proc.001", "briefing 근거 상수 고정")

# 접촉 전략의 절은 조회 접두도, 확정되지 않은 시한 표현도 없이 순수 행동만 남는다.
# ('금주 내' 처럼 절 서두에 못 박힌 시한 표현은 이질감을 준다. 시급성은 urgency 필드와
#  실행 순서로 표현한다.)
_chn = engine.BY_ID["st.chn_retain"]["clause"]
check(_chn == "유선 접촉", "이탈 방어 절은 행동만 담음", _chn)


# ─────────────────────────────────────────────────────────────
# 결함 4~6 — 규정·적합성 게이트
# ─────────────────────────────────────────────────────────────

# 규정 근거가 있는 전략은 sources 대신 regulation 을 갖는다.
check(engine.BY_ID["st.limit_fix"].get("regulation"), "한도 전략에 규정 근거 기재")

# 결함 6 — 예금자보호
check(all(r.get("depositor_protection") is not None
          for r in engine.PRODUCTS if r["payout"] == "원리금보장"),
      "원리금보장 상품에 예금자보호 여부 기재")


# ─────────────────────────────────────────────────────────────
# 결함 7~12 — 논리·용어
# ─────────────────────────────────────────────────────────────

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
# 시스템 전략도 구조 검증(validate)의 대상이다 — 절 종결·주체 규약을 지킨다.
for _sys in engine.SYSTEM_STRATEGIES:
    check(_sys["clause"].endswith(engine.CLAUSE_ENDINGS), f"{_sys['id']}: 절 명사형 종결")
    check(_sys.get("actor") in ("직원", "고객"), f"{_sys['id']}: 행위 주체 지정")


# ─────────────────────────────────────────────────────────────
# 결함 13~14 — 수치 근거
# ─────────────────────────────────────────────────────────────

# 결함 13 — 세액공제율은 소득 구간을 따른다.
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


# ─────────────────────────────────────────────────────────────
# 페르소나 회귀 — 시연용 목업 9케이스 전원
#
# 요건 판정 스냅샷은 데이터(xlsx)와 판정 로직을 함께 고정한다 — 어느 쪽이 바뀌어도 여기서
# 드러난다. 원본 xlsx 를 교체했다면 이 표를 새 데이터 기준으로 함께 갱신한다.
# ─────────────────────────────────────────────────────────────

from pension_agent.strategy_agent import agent
from pension_agent.strategy_agent.customer import PERSONAS

BY_NAME = {p.nm: p for p in PERSONAS}
check(len(PERSONAS) == 9, "목업 9케이스 적재", str(len(PERSONAS)))

# ── 배지 골든셋 ──────────────────────────────────────────────
# 첫 화면 배지(원본 08_BADGES)는 기획자가 지식베이스 세그먼트를 읽고 9케이스에 부여한
# 값이다. 그 Y/N 을 골든셋으로 두고, customer.conditions() 의 요건 5종이 **정확히** 재현하는지
# 본다 — 데이터가 바뀌든 판정이 바뀌든 어긋나면 여기서 드러난다. 판정 기준은 전부 세그먼트의
# condition_text 원문이고 임계값도 원문 값이다(customer.py 주석에 세그먼트 번호 기재).
import json as _json

from pension_agent import config as _cfg

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

# 요건 판정 스냅샷 (원본 xlsx 00_시연케이스의 시연 포인트와 정합해야 한다 — 그 메모
# 자체는 원장에 담지 않는다, scripts/import_customers.py 참고)
_EXPECTED_CONDS = {
    "김현수": ["dep", "idl", "nod"],                   # 현금성 방치(고유대 75%, 1개월 무변동)
    "박지민": ["lim", "tax", "add"],                   # 실적배당 75% > 한도 70% + 세액공제 잔여
    "이준호": ["mat"],                                  # 예금 만기 D-17
    "최서윤": ["dor", "hlt"],                          # 8개월 미접촉 + 판매중단 펀드 보유
    "정민석": ["dor", "mis", "dep", "nch"],            # 공격투자형 + 원리금보장 100%
    "한지우": ["isa", "tax", "add"],                   # ISA 만기 D-12 + 세액공제 잔여 300만원
    "오세훈": ["mat", "pen"],                          # 만기 D-25 + 연금개시 요건충족·미개시
    "윤가영": ["out", "nod"],                          # 고유대 52% + 최근 1개월 1.7억 유입
    "송도윤": ["dor", "hlt", "idl", "nod", "isa", "nch"],  # 3개 복합 케이스
}
# 위 스냅샷에서 룰베이스 정렬로 바뀐 세 칸 — 지우면 왜 바뀌었는지 다시 알 수 없다.
#   오세훈  dep 빠짐 : 합산 69% → TG-202 의 80% 미만. 옛 임계값 60 은 근거가 없었고,
#                     기획자 배지도 그를 「원리금보장 편중」 N 으로 둔다.
#   김현수·최서윤 nch 빠짐 : 10.1개월·7.3개월 → TG-201 의 12개월 미만. 옛 임계값 6 도
#                     근거가 없었다.
# 김현수는 합산 90% 라 TG-202 에 걸려 dep 을 유지한다. 다만 기획자 배지는 그를
# 「원리금보장 편중」 N·「미운용현금자산」 Y 로 둔다 — 그의 90% 는 대부분 고유계정대(75%)이고
# 배지는 그 축 하나로 표기했다. 룰베이스는 중복 태그를 허용하므로(TG-004 구분사항) 둘 다
# 서는 것이 표와 어긋나지는 않는다. 배지 골든셋에서 「원리금보장 편중」을 고정하지 않는
# 이유가 이것이다(_BADGE_COND 참고).
for nm, want in _EXPECTED_CONDS.items():
    got = conditions(BY_NAME[nm])
    check(got == want, f"{nm}: 요건 판정 스냅샷", f"{got} != {want}")

# 전원 산출 스모크 — 로직 검사는 노출 개수를 넓게 열어 만든다(TOP_N 좁힘과 무관해야 한다).
_WIDE = 99
FACTS = {p.nm: engine.prepare(p, top_n=_WIDE) for p in PERSONAS}

# 배지 요건에는 대응 세그먼트가 선언돼 있어야 «왜 관리 대상인가»가 문제상황으로 뜬다.
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
    # retPct 는 새 데이터에 모수가 없어 전원 None — 화면 문자열로 새지 않아야 한다.
    check(p_.retPct is None and "None" not in str(f["customer"].get("수익률", "")),
          f"{nm}: retPct=None 그레이스풀", str(f["customer"].get("수익률")))
    # 스타클럽 등급은 전원 값이 있으므로 헤더에 표기된다.
    check(f["customer"].get("스타클럽등급") == p_.club_grade, f"{nm}: 스타클럽등급 표기")
    # 산출 문장 규약 — 주체 반영·종결.
    for it in f["items"]:
        fc = engine.final_clause(it)
        check(fc.endswith(("제안", "안내")) if it["actor"] == "고객" else True,
              f"{nm}: '{it['title']}' 주체 반영", fc[-20:])
    _cr = engine.compose_rule(f)
    check(_cr.endswith("하세요.") if f["items"]
          else _cr.startswith("제안 가능한 실행 항목이 없습니다"), f"{nm}: 문장 종결", _cr[:30])
    # 슬롯 전량 치환 · 카드 규약.
    for it in f["items"]:
        for text in (it["clause"], it["evidence"], *it["evidence_extra"],
                     *it["card"].values()):
            hit = re.search(r"\{[a-z_0-9]+\}", text)
            check(hit is None, f"{nm}: '{it['title']}' 슬롯 전량 치환", hit.group() if hit else "")
        check(bool(it["card"]["headline"] and it["card"]["tag"] and it["card"]["benefit"]),
              f"{nm}: '{it['title']}' 카드 핵심 필드 존재")
    # 요건이 성립하지 않는 전략을 정원 충족 목적으로 채우지 않는다.
    conds_ = {c.split(":")[0] for c in f["conditions"]}
    for it in f["items"]:
        spec = engine.BY_ID[it["id"]]
        check(spec.get("when") in conds_ or spec.get("trigger"),
              f"{nm}: '{it['title']}' 요건 성립 확인", str(spec.get("when")))

# 노출 개수 — 제안 1 + 예비 1, 좁혀도 후보 평가는 동일.
for p_ in PERSONAS:
    _d = engine.prepare(p_)
    check(len(_d["items"]) <= 1 and len(_d["alternatives"]) <= 1,
          f"{p_.nm}: 제안·예비 최대 1건", f"{len(_d['items'])}/{len(_d['alternatives'])}")
    check(len(_d["candidates"]) == len(FACTS[p_.nm]["candidates"]),
          f"{p_.nm}: 노출을 좁혀도 후보 평가는 동일")
    check({a["id"] for a in _d["alternatives"]}.isdisjoint({i["id"] for i in _d["items"]}),
          f"{p_.nm}: 예비와 제안이 겹치지 않음")

# 결함 13 재현 — 세액공제 대상액은 잔여 한도를 넘지 않는다 (박지민 잔여 300만원).
_tax = next((i for i in FACTS["박지민"]["items"] if i["id"] == "st.tax_fill"), None)
check(_tax is not None, "박지민: 세액공제 전략 성립")
if _tax:
    check(_tax["amount"] == engine.won(3_000_000), "박지민: 세액공제 대상액 = 잔여 한도 300만원",
          str(_tax["amount"]))
check(any("총급여 구간 미확인" in n for n in FACTS["한지우"]["needs_confirm"]),
      "한지우: 소득 구간 미확인이 확인 항목으로 노출")

# 결함 5 재현 — 위험자산 70% 한도 (박지민 실적배당 75%).
check("lim" in conditions(BY_NAME["박지민"]), "박지민: 위험자산 한도 초과 판정(75%)")
check(any(i["id"] == "st.limit_fix" for i in FACTS["박지민"]["items"]),
      "박지민: 한도 정리 전략 소집")
check(any(rg["regulation"] for rg in FACTS["박지민"]["regulations"]),
      "박지민: 위험자산 한도 규정 근거 노출")

# 투자성향 불일치의 두 방향 — 축소 방향은 mis_fix 가 서고, 편중 방향은 0원이라 서지 않는다.
_soo = engine.prepare(Profile(  # 축소 방향 합성 케이스 (옛 감사 케이스의 형태 보존)
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

# 만기는 여러 건일 수 있다 — 예금·GIC 의 만기가 서로 다른 고객이 있다.
# 가장 가까운 한 건(matDD·matAmt)은 요건 판정·재예치 배분액의 입력이고, 재료에는 **전부**
# 실려야 한다. 하나만 실으면 "만기 언제야?"에 나머지가 없는 것처럼 답하게 된다.
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
# 재예치 전략은 가장 가까운 만기분만 대상으로 한다(먼 만기를 지금 끌어오지 않는다).
_ose = next((i for i in FACTS["오세훈"]["items"] if i["id"] == "st.mat_reprice"), None)
check(_ose is not None and _ose["amount"] == engine.won(BY_NAME["오세훈"].matAmt),
      "오세훈: 재예치 대상액은 가까운 예금 만기분만(먼 GIC 제외)",
      str(_ose and _ose["amount"]))

# 만기 요건 — D-17(이준호)·D-25(오세훈)는 성립, 창 밖은 위 합성 케이스가 고정.
check("mat" in conditions(BY_NAME["이준호"]) and "mat" in conditions(BY_NAME["오세훈"]),
      "이준호 D-17 · 오세훈 D-25 만기 요건 성립")
for nm in ("이준호", "오세훈"):
    check(any(i["id"] == "st.mat_reprice" for i in FACTS[nm]["items"]), f"{nm}: 만기 재예치 전략 소집")

# 결함 7 재현 — 예금 편중 해소가 기준선 이하 상품을 권하지 않는다 (김현수).
_dep = next((i for i in FACTS["김현수"]["items"] if i["id"] == "st.dep_shift"), None)
check(_dep is not None, "김현수: 예금 편중 해소 성립")
if _dep:
    _base_rate = engine.BASELINES["BL.bank_deposit"]["rate"]
    _rets = [engine._ret_of(r) for r in engine.PRODUCTS
             if r["name"] in [n.split("(")[0] for n in _dep["products"].values()]]
    check(all(v > _base_rate for v in _rets), "김현수: 기준선 초과 상품만 제안", str(_rets))

# Tier2 — 미매칭 시 '제안 항목 없음', LLM 스텁으로 승격·재료 이탈 폴백.
import contextlib
import io
from dataclasses import replace as _replace

check(agent.propose(BY_NAME["이준호"], use_llm=False)["tier"] == "행내전략",
      "매칭 전략 있으면 tier=행내전략")
# 요건이 하나도 없는 고객을 합성한다 — 9케이스에는 그런 고객이 없어서(전원 관리 사유 보유)
# 실존 고객에서 요건 원인을 걷어내 만든다. 새 요건이 늘면 여기도 함께 비워야 한다.
_calm = _replace(BY_NAME["한지우"], room=0, isa=None)
check(not conditions(_calm), "합성 무요건 고객: 요건 0건", str(conditions(_calm)))
_r = agent.propose(_calm, use_llm=False)
check(_r["tier"] == "미매칭" and _r["sentence"].startswith("제안 가능한 실행 항목이 없습니다"),
      "미매칭 시 '제안 항목 없음' 규칙 문장", _r["tier"])
# 같은 프로파일에 LLM 스텁을 갈아끼워 두 산출을 비교한다. 브리핑은 프로세스당 한 번만
# 만들어지므로(agent.propose 캐시 — 화면과 대화형이 같은 문장을 보게 하는 장치),
# **입력이 바뀐 셈인 스텁 교체 때마다 캐시를 비운다.** 실행 중에 LLM 이 바뀌는 것은
# 테스트에서만 있는 일이라, 이 호출이 필요한 것도 여기뿐이다.
_saved = (agent.llm.available, agent.llm.generate)
try:
    agent.llm.available = lambda: True
    agent.llm.generate = lambda prompt, system="": (
        '{"insight": "현 구성 양호", '
        '"sentence": "보유 구성과 수익률이 양호해 특별한 조치는 필요하지 않습니다."}')
    agent.clear_briefing_cache()
    _r2 = agent.propose(_calm, use_llm=True)
    check(_r2["tier"] == "LLM판단" and _r2["source"] == "LLM",
          "미매칭 + LLM → tier=LLM판단", f'{_r2["tier"]}/{_r2["source"]}')
    agent.llm.generate = lambda prompt, system="": (
        '{"insight": "x", "sentence": "KB 특판 정기예금 연 9.99% 가입을 권합니다."}')
    agent.clear_briefing_cache()
    _r3 = agent.propose(_calm, use_llm=True)
    check(_r3["tier"] == "미매칭" and bool(_r3["rejected"]),
          "재료 이탈 산출은 폴백(tier=미매칭)", str(_r3["rejected"])[:40])
finally:
    agent.llm.available, agent.llm.generate = _saved
    # 스텁이 만든 브리핑을 뒤 검사에 물려주지 않는다.
    agent.clear_briefing_cache()

# ── 미리 만들어 둔 브리핑(briefing_store) ────────────────────────────
# 프로세스 캐시는 프로세스가 끝나면 사라져서, 리허설이 매 실행 앞에서 브리핑 생성(LLM 11회)을
# 다시 치른다. 파일로 남겨 그것을 건너뛰되, **입력이 달라진 저장분은 절대 읽지 않는다** —
# 낡은 브리핑이 화면에 뜨는 것은 이 저장소가 가장 경계하는 «화면과 값이 갈리는» 실패의
# 조용한 형태다(캐시라서 아무도 안 본다).
import pathlib as _pathlib  # noqa: E402
import shutil as _shutil  # noqa: E402
import tempfile as _tempfile  # noqa: E402

from pension_agent import config as _config  # noqa: E402
from pension_agent.strategy_agent import briefing_store as _store  # noqa: E402

_saved_dir, _saved_fp = _config.BRIEFING_CACHE_DIR, _store._FINGERPRINT
_tmp = _pathlib.Path(_tempfile.mkdtemp(prefix="briefing-store-"))
try:
    # 디렉터리가 없으면 저장소는 통째로 꺼진 것이다 — 돌린 적 없는 사람에게는 무변경이다.
    _config.BRIEFING_CACHE_DIR = _tmp / "none"
    check(not _store.enabled(), "디렉터리가 없으면 브리핑 저장소는 꺼져 있다")

    _config.BRIEFING_CACHE_DIR = _tmp
    _store._FINGERPRINT = "fp-A"
    _store.save("k1", {"sentence": "저장본"})
    check((_store.load("k1") or {}).get("sentence") == "저장본", "저장한 브리핑을 다시 읽는다")

    # 지식 카드·프롬프트·날짜 중 하나라도 바뀌면 지문이 달라진다.
    _store._FINGERPRINT = "fp-B"
    check(_store.load("k1") is None, "입력(지문)이 바뀐 저장분은 읽지 않는다")

    _store._FINGERPRINT = "fp-A"
    check(_store.load("없는키") is None, "저장된 적 없는 키는 None")
finally:
    _config.BRIEFING_CACHE_DIR, _store._FINGERPRINT = _saved_dir, _saved_fp
    _shutil.rmtree(_tmp, ignore_errors=True)

# dorm=None 이어도 브리핑이 죽지 않는다 (김현수는 실제로 상담이력이 없어 dorm=None 이다).
check(BY_NAME["김현수"].dorm is None, "김현수: 상담이력 없음 → dorm=None")
check("dor" not in conditions(BY_NAME["김현수"]), "dorm=None 이면 dor 요건 미성립")
check("None" not in str(FACTS["김현수"]["customer"]["최근접촉"]),
      "dorm=None 이 화면 문자열로 새지 않음", str(FACTS["김현수"]["customer"]["최근접촉"]))

# 산출물(문자열) 형식 — 원화 기대효과 미노출 · 신규 섹션 존재 (이준호 기준).
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    agent._print(agent.propose(BY_NAME["이준호"], use_llm=False))
_art = _buf.getvalue()
check("기대효과" not in _art, "산출물에 '기대효과' 표기 없음")
check(all(sec in _art for sec in ("판단근거", "근거 문서")), "산출물에 판단근거·근거 문서 섹션")


# ─────────────────────────────────────────────────────────────
# 부수 결함
# ─────────────────────────────────────────────────────────────

# 만기 요건은 D-30 이내에서만 성립한다 — 본부 가이드 "만기 1개월 전 반드시 만기 안내"
# (06_주제별_추출지식/01_고객세그먼트 9 · 02_IRP관리방법론 18, 07_에이전트_기능정의/01 ④ "기한 임박").
_far = Profile(id="T", nm="T", ag=40, bal=100_000_000, rk="안정형", grade="낮은위험",
               port=[100, 0, 0, 0], ret=1.0, retPct=50, dopt="설정", room=0, dorm=0,
               nchM=0, matDD=300, matAmt=100_000_000)
check("mat" not in conditions(_far), "D-300 은 만기 요건 미성립")
_far.matDD = 45
check("mat" not in conditions(_far), "D-45 는 만기 요건 미성립(창 D-30)")
_far.matDD = 26
check("mat" in conditions(_far), "D-26 은 만기 요건 성립")

# 연금수령 개시 계좌에는 추가납 요건(add·tax)이 성립하지 않는다 — 07_에이전트_기능정의/01 ①
# "⚠ 하지 말 것: 연금개시 계좌 → 추가납 권유 금지", 02_IRP관리방법론 59 "연금개시 → 추가입금 불가".
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


# ─────────────────────────────────────────────────────────────
# 산출물 형식 — 효과 등급 · 출처 원본명
# ─────────────────────────────────────────────────────────────

# 효과 등급은 수익률 개선폭 경계(EFFECT_BANDS)를 따른다.
check(engine._effect_grade(1.13) == "큼" and engine._effect_grade(0.63) == "보통"
      and engine._effect_grade(None) == "—", "수익률 개선폭 → 정성 등급 환산")

# 등급 문자는 산출물에 사람이 읽는 라벨로 노출된다('큼'·'—' 만으로는 의미가 불명확하므로).
check(engine.effect_label("큼") == "수익 개선폭 큼"
      and engine.effect_label("—") == "수익 개선 해당 없음", "수익 개선폭 등급 라벨 명확화")

# 출처는 코드용 doc_id 가 아니라 원본 문서명으로 표기된다(req 2).
# 지식베이스 경로가 있을 때만 검증(단독 실행 환경에서는 doc_id 폴백).
if engine.DOC_TITLES:
    # 종류가 다른 카드(세그먼트·화법)라도 같은 원천 문서면 한 줄로 묶인다.
    _same = engine.format_sources(["seg.01", "pitch.k03.040"])
    check(len(_same) == 1 and _same[0].startswith("개인형IRP 고객관리 가이드"),
          "다른 종류의 카드도 원본 문서명 한 줄로 묶임", str(_same))
    # 페이지 표기(pNN)는 색인에 없는 id 의 폴백 경로로 남아 있다.
    check(engine.format_sources(["guide01_yield_mgmt.p01", "guide01_yield_mgmt.p05"])
          == ["guide01_yield_mgmt (p1/p5)"], "색인 밖 id 는 doc_id·페이지로 폴백")
    _tax = engine.format_sources(["fact.k04.f2", "fact.k04.f3", "pitch.k03.029"])
    check(len(_tax) == 1 and not _tax[0].startswith("fact"),
          "fact 인용이 원본 문서로 병합", str(_tax))


# ─────────────────────────────────────────────────────────────
# 승인 피드백 회귀 — 절 시한표현 · 상담 화법 주체 · 카드 규약
# ─────────────────────────────────────────────────────────────

# 'D-{matDD}' 처럼 Profile 값으로 산출되는 기한은 허용하되, '금주 내'·'당장' 같은 못 박은
# 시한은 금지한다. 시급성은 urgency 필드와 실행 순서로 표현한다.
for _m in engine.TIME_PRESSURE_MARKERS:
    for _s in engine.SPECS + engine.SYSTEM_STRATEGIES:
        for _k in ("clause", "clause_if_asset", "clause_if_merged"):
            check(_m not in (_s.get(_k) or ""),
                  f"{_s['id']}.{_k}: 시한 표현('{_m}') 부재")
# 재유입되면 validate() 가 [시급성문구] 로 잡는다.
e, _ = with_specs(lambda s: _spec(s, "st.chn_retain").__setitem__("clause", "금주 내 유선 접촉"))
check(any("시급성문구" in x for x in e), "절 내 시한 표현 검출")

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

# 카드 혜택(benefit)은 판단근거(evidence)와 구별되는 '왜 이로운가' 문구다(단순 재사용 아님).
check(engine.BY_ID["st.mat_reprice"]["benefit"] != engine.BY_ID["st.mat_reprice"]["evidence"],
      "benefit 은 evidence 와 구별되는 별도 문구")

# 핵심가치(tag)는 전략 성격에서 도출된다(개별 문구 하드코딩 아님).
check(engine._value_tag({"kind": "접촉"}, "—") == "관계 관리", "tag: 접촉 → 관계 관리")
check(engine._value_tag({"kind": "운용", "reduces_risk": True}, "—") == "위험 조정",
      "tag: 위험감축 → 위험 조정")
check(engine._value_tag({"kind": "납입", "impact": {"kind": "tax_credit"}}, "—") == "세제 혜택",
      "tag: 세액공제 → 세제 혜택")

# 출력 포맷 변경은 프롬프트(prompts.py)에 계약으로 반영된다.
_ptxt = prompts.SYSTEM + prompts.WRITE_PROMPT
check("카드" in _ptxt, "프롬프트: 카드형 포맷 계약 명시")
check(("2~3문장" in _ptxt) or ("짧게" in _ptxt), "프롬프트: 간결 요약 지시(AI 브리핑 2~3문장 길이 제약)")
check(("명령" in _ptxt) and ("제안" in _ptxt), "프롬프트: 매수 명령이 아닌 제안 어조 지시")


# ─────────────────────────────────────────────────────────────
# 코드·데이터 상수 정합
# ─────────────────────────────────────────────────────────────

_caps = [int(m) for m in re.findall(
    r'"credit_limit":\s*(\d+)',
    (config.STRATEGY_DATA_DIR / "strategies.json").read_text(encoding="utf-8"))]
check(_caps and all(v == customer.TAX_CREDIT_CAP_WON for v in _caps),
      "세액공제 한도가 코드·데이터에서 일치", f"{_caps} vs {customer.TAX_CREDIT_CAP_WON}")

# 노출 기본값 — "오늘의 제안 1개 (+예비 1개)"
# 07_에이전트_기능정의/01 ① 필수 구성 요소 4: "우선순위가 정해져서 옴. 제안 5개 나열은
# 안 하느니만 못함."
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
