"""캔드 LLM 시나리오 — 키 없이 같은 현상을 결정론적으로 재현한다.

**LLM 만 스크립트한다.** KB 검색·근거 조립·게이트·폴백은 전부 진짜 코드가 돈다 — 그래야
트레이스가 "이 코드가 이렇게 판정한다"의 기록이 된다. 스텁이 게이트까지 흉내 내면 그건
운영 동작이 아니라 스텁의 동작을 재는 것이다.

스텁을 거는 자리는 기존 `tests/test_consult_agent.py` 와 같은 규약이다:
  understand.generate   의도 분류 — `"{}"` 면 기본 의도(계획 루프)로 떨어진다
  plan.generate         계획 JSON 과 답변 문장 둘 다. 프롬프트로 갈린다
  clarify.generate      되묻기 판정 — `{"ask": null}` = 되묻지 않는다
  tools.fits_question   적합성 게이트 — 시나리오가 지정한 카드만 남긴다
  select.llm_pick       카드 선택 1차(LLM) 끄기 — n-gram 검색만으로 재현하기 위해

되묻기 판정까지 스크립트하는 이유: 이 자리를 비워 두면 키 없는 환경에서 `clarify` 가 진짜
LLM 을 부르다 죽는다. 코드가 그 실패를 삼키고 compose 로 흘려보내므로 답은 같게 나오지만,
트레이스에는 시나리오와 무관한 LLM 실패가 찍힌다 — **재현하려는 사건이 아닌 것이 기록에
섞이면 그 기록은 진단에 쓸 수 없다.**
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field

from pension_agent.consult_agent import select as S
from pension_agent.consult_agent import tools as T
from pension_agent.consult_agent.nodes import clarify as CL
from pension_agent.consult_agent.nodes import plan as P
from pension_agent.consult_agent.nodes import understand as U
from pension_agent.llm import LLMError
from tests.debug import trace as TR

#: 세액공제 카드(fact.k04.f2)를 근거로 받은 답변들. 게이트가 무엇을 어떻게 가르는지
#: 눈으로 대조하려고 **한 글자 차이**로 갈리게 만들어 뒀다.
_CLEAN = (
    "세액공제 한도는 연 900만원이에요(연금저축 포함). 총급여 5,500만원 이하면 16.5%, "
    "초과면 13.2%라서 900만원을 다 채우면 각각 1,485,000원 / 1,188,000원을 돌려받아요. "
    "다만 결정세액이 공제액보다 적으면 최대 환급액을 다 못 받으니, 소득이 낮은 고객에게는 "
    "그 단서를 꼭 함께 말씀해 주세요."
)
#: 위 문장에 **오기를 오기라고 짚는 한 문장**을 더한 것. 카드의 `verify_points` 가 직원에게
#: 바로 그렇게 안내하라고 적어둔 내용인데, 이 문장 때문에 답변이 통째로 폐기된다.
_KNOWN_WRONG = _CLEAN + ' 안내하실 때 "5,500만원 이상 13.2%"는 오기니까 "초과"로 말씀해 주세요.'
#: 원장에 없는 수치를 지어낸 답변. 걸리는 게이트가 달라지는 것을 보려고 둔다.
_OUT_OF_LEDGER = "세액공제 한도는 연 1,200만원이에요. 공제율은 총급여에 따라 갈려요."

#: **실제 LLM 이 쓴 문장의 형태.** 값은 원장 그대로인데 표기가 다르다 — 원장은
#: "1,485,000원"·"148.5만원"·"2026.06" 이고, LLM 은 직원이 실제로 말하는 대로
#: "148만 5천원"·"2026년 6월" 이라고 쓴다. `verify.numbers()` 는 숫자 토큰의 집합
#: 비교라, 만·천으로 끊긴 표기는 원장에 없는 토큰(148 · 5 · 118 · 8 · 6)을 만든다.
#: **맞는 답변이 표기 때문에 버려지는 자리다.**
_KOREAN_UNITS = (
    "세액공제 한도는 연 900만원이에요. 총급여 5,500만원 이하면 16.5%라 148만 5천원, "
    "초과면 13.2%라 118만 8천원을 돌려받아요. 2026년 6월 기준입니다."
)


@dataclass(frozen=True)
class Scenario:
    """LLM 이 이렇게 답했을 때 파이프라인이 어떻게 되는가."""
    name: str
    question: str
    plan: tuple[dict, ...]              # 계획 단계 응답(순서대로). 떨어지면 done
    compose: str | None                 # None 이면 compose 단계에서 LLM 이 죽는다
    keep: tuple[str, ...] = ()          # 적합성 게이트가 남길 카드 id
    expect: str = ""                    # 사람이 읽을 기대. 검사는 test_trace.py 가 한다
    route: str = "{}"
    customer_id: str | None = None
    follow_ups: tuple[str, ...] = field(default=())


_FACT_STEP = ({"tool": "fact", "query": "세액공제 한도", "last": True},)

SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in (
        Scenario(
            name="tax_credit_known_wrong",
            question="세액공제 한도가 얼마야?",
            plan=_FACT_STEP, compose=_KNOWN_WRONG, keep=("fact.k04.f2",),
            expect="relations 가 '알려진 오답' 으로 폐기 → 근거 원문 폴백(말투가 달라진다)",
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
            expect="값은 맞는데 표기가 달라 verify_texts 가 폐기 — 실제 실행에서 걸린 자리",
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
    """시나리오의 LLM 응답을 걸고, 나갈 때 원래대로 돌려놓는다."""
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
        # 적합성 게이트는 이 스위트의 관심사가 아니다 — 시나리오가 지정한 카드만 남긴다.
        T.fits_question = lambda q, hits, kind="지식", history=None: (
            [(s, c) for s, c in hits if c.get("id") in scn.keep] if scn.keep else hits)
        T.llm_pick = S.llm_pick = lambda kinds, query: []
        yield scn
    finally:
        for mod, attr, value in saved:
            setattr(mod, attr, value)
