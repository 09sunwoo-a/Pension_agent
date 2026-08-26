"""산출물 감사 회귀 테스트.

2026-08-11 산출물 감사에서 확인된 결함 14건과 부수 결함 3건을 고정한다. 정의 파일이나
엔진 로직이 변경되어 동일 성격의 결함이 재유입되면 본 테스트가 실패한다.

각 테스트는 감사 결함 번호와 대응한다. 신규 전략·상품·근거를 추가할 때는 본
테스트를 함께 통과시켜야 한다.

**지금은 축소 상태다.** 더미 페르소나(C1~C6)를 걷어내면서 그 페르소나의 산출물을 직접
단언하던 검사를 함께 들어냈다(customer.PERSONAS 가 비어 있다). 남은 것은 고객 데이터 없이
서는 검사 — 전략 정의 검증(validate)·절 규약·상품 데이터 규약·요건 판정 로직이다.
시연용 고객 데이터가 새로 정해지면 아래 항목을 그 데이터 기준으로 다시 세워야 한다:

  · 결함 4  최소가입금액이 적립금이 아니라 배분액과 대조되는가 (engine.gate_amount)
  · 결함 5  위험자산 70% 한도 판정과 여력 0 에서의 디폴트옵션 지정
  · 결함 6  예금자보호 한도 초과분 고지
  · 결함 7  예금 편중 해소가 기준선 이하 상품을 권하지 않는가
  · 결함 8~9 산출 문장의 주체 반영 · 동작 명사 적정
  · 결함 11~12 흡수된 전략의 근거 보존 · 만기/편중 병합
  · 결함 13~14 세액공제 대상액 한도 · 미확정 분기 기대효과 제외
  · briefing 산출 · 카드 필드 완비 · 슬롯 전량 치환 · 출처 표기 · 제안 개수(TOP_N/ALT_N)
  · Tier2(행내전략 미매칭 → LLM판단) 승격과 재료 이탈 폴백
  · retPct·dorm 이 None 이어도 브리핑이 죽지 않는가

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
