"""캔드 LLM 시나리오 — 키 없이 결정론적 재현. LLM 만 스텁, 검색·근거·게이트·폴백은 실코드.

  understand.generate   의도 분류 — `"{}"` 면 기본 의도(계획 루프)
  plan.generate         계획 JSON·답변 문장 둘 다(프롬프트로 구분)
  clarify.generate      되묻기 판정 — `{"ask": null}` 고정(비우면 트레이스에 무관한 LLM 실패 기록)
  tools.fits_question   적합성 게이트 — 시나리오 지정 카드만 유지
  select.llm_pick       카드 선택 1차(LLM) 끄기 — n-gram 검색만 사용
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field

from pension_agent.tools.kb_search import select as S
from pension_agent import tools as T
from pension_agent.nodes import clarify as CL
from pension_agent.nodes import plan as P
from pension_agent.nodes import understand as U
from pension_agent.common.llm import LLMError
from tests.debug import trace as TR

#: 세액공제 카드(fact.k04.f2) 근거 답변들 — 게이트 판정 대조용으로 최소 차이만 둠
_CLEAN = (
    "세액공제 한도는 연 900만원이에요(연금저축 포함). 총급여 5,500만원 이하면 16.5%, "
    "초과면 13.2%라서 900만원을 다 채우면 각각 1,485,000원 / 1,188,000원을 돌려받아요. "
    "다만 결정세액이 공제액보다 적으면 최대 환급액을 다 못 받으니, 소득이 낮은 고객에게는 "
    "그 단서를 꼭 함께 말씀해 주세요."
)
#: 오기를 정정하는 문장 추가 — 인용부호 + 정정 표지로 정정 판정, 통과해야 함
_CORRECTION = _CLEAN + ' 안내하실 때 "5,500만원 이상 13.2%"는 오기니까 "초과"로 말씀해 주세요.'

#: 같은 문구를 주장한 답변 — 정정과의 반대짝
_ASSERTS_WRONG = (
    "세액공제 한도는 연 900만원이에요. 총급여 5,500만원 이상 13.2% 로 안내하시면 돼요."
)
#: 같은 표기, 값 하나 오류(148만 6천원) — 단위 접기가 아무 금액이나 통과시키지 않음 고정
_KOREAN_UNITS_WRONG = (
    "세액공제 한도는 연 900만원이에요. 900만원을 채우면 148만 6천원을 돌려받아요."
)

#: 원장 밖 수치 답변
_OUT_OF_LEDGER = "세액공제 한도는 연 1,200만원이에요. 공제율은 총급여에 따라 갈려요."

#: 원장 값 그대로, 표기만 만·천·«년 월» — `verify._measures()` 덩이 읽기로 통과해야 함
_KOREAN_UNITS = (
    "세액공제 한도는 연 900만원이에요. 총급여 5,500만원 이하면 16.5%라 148만 5천원, "
    "초과면 13.2%라 118만 8천원을 돌려받아요. 2026년 6월 기준입니다."
)


@dataclass(frozen=True)
class Scenario:
    """LLM 응답 스크립트 한 건."""
    name: str
    question: str
    plan: tuple[dict, ...]              # 계획 단계 응답(순서대로), 소진 시 done
    compose: str | None                 # None 이면 compose 단계 LLM 실패
    keep: tuple[str, ...] = ()          # 적합성 게이트가 남길 카드 id
    expect: str = ""                    # 사람용 기대 설명(검사는 test_trace.py)
    route: str = "{}"
    customer_id: str | None = None
    follow_ups: tuple[str, ...] = field(default=())


_FACT_STEP = ({"tool": "fact", "query": "세액공제 한도", "last": True},)

SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in (
        Scenario(
            name="tax_credit_correction",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_CORRECTION, keep=("fact.k04.f2",),
            expect="오기를 «틀렸다»고 짚는 정정은 통과한다(예전엔 이것까지 폐기됐다)",
        ),
        Scenario(
            name="tax_credit_asserts_wrong",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_ASSERTS_WRONG, keep=("fact.k04.f2",),
            expect="같은 문구라도 주장하면 relations 가 폐기 → 근거 원문 폴백",
        ),
        Scenario(
            name="tax_credit_clean",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_CLEAN, keep=("fact.k04.f2",),
            expect="게이트 전부 통과 → 생성문 그대로",
        ),
        Scenario(
            name="out_of_ledger",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_OUT_OF_LEDGER, keep=("fact.k04.f2",),
            expect="verify_texts 가 원장 밖 수치로 폐기 → relations 는 실행되지 않는다",
        ),
        Scenario(
            name="korean_units",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_KOREAN_UNITS, keep=("fact.k04.f2",),
            expect="만·천으로 끊어 쓴 금액도 통과한다(예전엔 표기 때문에 폐기되던 자리)",
        ),
        Scenario(
            name="korean_units_wrong",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_KOREAN_UNITS_WRONG, keep=("fact.k04.f2",),
            expect="같은 표기라도 값이 다르면 여전히 폐기 — 단위 접기가 문을 열어준 게 아니다",
        ),
        Scenario(
            name="llm_dead",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=None, keep=("fact.k04.f2",),
            expect="§11 — 근거 원문을 대신 내보내지 않고 'LLM 실패' 로 답한다",
        ),
    )
}


@contextmanager
def installed(scn: Scenario):
    """시나리오 LLM 응답 설치, 종료 시 복원."""
    saved = [(U, "generate", U.generate), (P, "generate", P.generate),
             (CL, "generate", CL.generate), (T, "fits_question", T.fits_question),
             (T, "llm_pick", T.llm_pick), (S, "llm_pick", S.llm_pick)]
    steps = list(scn.plan)

    def plan_generate(prompt, **kw):
        if TR.is_compose_prompt(prompt):                # 답변 작성
            if scn.compose is None:
                raise LLMError("스크립트: compose 단계에서 LLM 이 죽은 상황")
            return scn.compose
        return json.dumps(steps.pop(0) if steps else {"done": True}, ensure_ascii=False)

    try:
        U.generate = lambda prompt, **kw: scn.route
        P.generate = plan_generate
        CL.generate = lambda prompt, **kw: '{"ask": null}'
        # 나머지 인자는 `**kw` — 실서명 복사 시 인자 추가마다 TypeError 로 «도구 고장» 둔갑
        T.fits_question = lambda q, hits, kind="지식", **kw: (
            [(s, c) for s, c in hits if c.get("id") in scn.keep] if scn.keep else hits)
        T.llm_pick = S.llm_pick = lambda kinds, query: []
        yield scn
    finally:
        for mod, attr, value in saved:
            setattr(mod, attr, value)
