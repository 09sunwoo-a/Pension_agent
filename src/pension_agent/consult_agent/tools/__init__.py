"""도구 레지스트리 — 에이전트가 할 수 있는 일의 능력 표면.

능력은 의도 enum 이 아니라 도구 목록이다. 계획 루프(nodes/plan)가 한 턴에 여러 도구를
부르고, 돌려받은 근거를 원장에 쌓고, compose 가 그것만으로 답을 쓴다. 모든 도구는 같은
꼴(Evidence)을 내놓고, 원문 강제 여부는 그 안의 atomic·notices 로 선언한다 — 규약은
evidence/record.py. 못 찾으면 None, 죽으면 ToolFailure — tools/base.py.

도구 함수는 `_이름` 이고 아래 TOOLS 에 Tool 로 올라간다. [LLM] 은 근거를 만들며 LLM 을
부른다는 뜻이다. 도구가 쓰는 검색·규약·검사는 evidence/ 에 있고, 그중 몇 이름을 여기서
재노출한다 — 테스트·디버그 실행기가 `tools.X` 로 갈아끼우는 후크다.

    규약
    base.py         Tool · ToolFailure       도구 선언 · «확인하지 못함» 예외

    지식베이스 도구
    cards.py        fact procedure screen channel segment method fieldtip
                                             수치·절차·화면번호·채널·고객군·방법론·현장 [LLM]
    market.py       market lineup            시황·투자전략 · 추천펀드·디폴트옵션·TDF [LLM]
    pitch.py        pitch                    고객의 말·반응으로 화법을 찾는다 [LLM]
    playbook.py     playbook                 이 고객 상태에 걸린 참고자료(화면 ⑥⑦⑧)

    현재 고객 도구 — strategy_agent 가 계산한 것을 그대로
    briefing.py     customer                 브리핑 재료 — 잔액·수익률·요건·타겟 근거
    suitability.py  suitable                 투자성향으로 어디까지 안내할 수 있는지
    outreach.py     outreach                 안내할 세미나·이벤트와 발송 문구
    history.py      history transcript       지난 상담 · 이번 상담의 대화

    고객 화면 없이 쓰는 도구
    targets.py      targets                  오늘의 타겟 고객 목록
    dates.py        date                     오늘 날짜 · 연말까지 남은 일수
    tax_credit.py   tax_credit               «얼마 더 넣으면 얼마 받나» — 코드 계산
    answered.py     last_answer              이번 상담에서 한 답변 원문

    도구 위에서 도는 것
    adequacy.py     fits_question            고른 근거가 질문에 답이 되는가 [LLM]
    combine.py      evidence_from_cards      여러 종류의 카드 묶음 → 원장 항목 하나
"""

from __future__ import annotations

import logging

from pension_agent import observability
from pension_agent.consult_agent import progress
from pension_agent.consult_agent.evidence.select import llm_pick, pick  # noqa: F401 — 후크(머리말)
from pension_agent.consult_agent.state import KB, AgentState  # noqa: F401 — 후크(머리말)
from pension_agent.knowledge.kb import retrieve  # noqa: F401 — 후크(머리말)
from pension_agent.llm import LLMError, generate  # noqa: F401 — 후크(머리말)

from pension_agent.consult_agent.tools.base import (  # noqa: F401
    Tool,
    ToolFailure,
)
from pension_agent.consult_agent.evidence.record import (  # noqa: F401
    Evidence,
    _clean,
    _ev,
    _scope,
)
from pension_agent.consult_agent.tools.cards import (  # noqa: F401
    FIELDTIP_MARK,
    _channel,
    _fact,
    _fieldtip,
    _method,
    _method_decls,
    _procedure,
    _procedure_decls,
    _render_channel,
    _render_fieldtip,
    _render_method,
    _render_screen,
    _screen,
    _segment,
    advisory_mark,
    stale_mark,
)
from pension_agent.consult_agent.tools.market import (  # noqa: F401
    MARKET_TOP_K,
    _market_like,
    _prefer_sections,
    _render_market,
)
from pension_agent.consult_agent.tools.suitability import (  # noqa: F401
    _suitable,
)
from pension_agent.consult_agent.tools.briefing import (  # noqa: F401
    _POOL_KEYS,
    _citable,
    _cond_labels,
    _customer,
)
from pension_agent.consult_agent.tools.history import (  # noqa: F401
    HISTORY_DIALOG_SESSIONS,
    HISTORY_EXCERPT,
    HISTORY_SESSIONS,
    HISTORY_TURNS,
    TRANSCRIPT_EXCERPT,
    TRANSCRIPT_TURNS,
    HISTORY_MARK,
    HISTORY_NONE,
    TRANSCRIPT_NONE,
    _HISTORY_ROLE,
    _OFFER_TRAILER,
    _history,
    _strip_devices,
    _transcript,
)
from pension_agent.consult_agent.tools.adequacy import (  # noqa: F401
    ADEQUACY_MAX_TOKENS,
    _adequacy_verdict,
    _adopt,
    _headline,
    fits_question,
    record_branches,
)
from pension_agent.consult_agent.tools.pitch import (  # noqa: F401
    PITCH_TOP_K,
    _pitch,
)
from pension_agent.consult_agent.tools.dates import (  # noqa: F401
    _WEEKDAYS,
    _date,
)
from pension_agent.consult_agent.tools.tax_credit import (  # noqa: F401
    ISA_FACT_ID,
    TAX_FACT_ID,
    _CAVEAT_MARK,
    _caveat,
    _extra_paid,
    _isa_convertible,
    _isa_rollover_lines,
    _tax_credit,
    _won,
)
from pension_agent.consult_agent.tools.playbook import (  # noqa: F401
    PLAYBOOK_LANES,
    PLAYBOOK_TOP_K,
    _PLAYBOOK_TYPES,
    _playbook,
    cited_cards,
    playbook_evidence,
    playbook_hits,
    playbook_ranked,
)
from pension_agent.consult_agent.tools.outreach import (  # noqa: F401
    _outreach,
)
from pension_agent.consult_agent.tools.targets import (  # noqa: F401
    _targets,
)
from pension_agent.consult_agent.tools.answered import (  # noqa: F401
    CARDS_HEADER,
    SELF_SOURCE,
    _last_answer,
    last_answered,
    referenced_turn,
)
from pension_agent.consult_agent.tools.combine import (  # noqa: F401
    evidence_from_cards,
)
from pension_agent.consult_agent.evidence.ledger import (  # noqa: F401
    CAUTION,
    GROUND,
    ledger_marks,
    ledger_related,
    ledger_slots,
    ledger_sources,
    ledger_texts,
    source_lines,
    summarize,
)


# ─────────────────────────────────────────────────────────────
# 레지스트리
# ─────────────────────────────────────────────────────────────

TOOLS: dict[str, Tool] = {
    t.name: t for t in (
        # 갈리는 축은 **무엇으로 찾나**다 — 이쪽은 질문에 담긴 «고객이 한 말», `playbook` 은
        # 고객 계좌 상태. 예전 설명("대사·반론 대응·논거를 만든다")은 그 축을 말하지 않아서,
        # 고객 화면이 열려 있으면 반론 질문이 통째로 `playbook` 으로 갔다(gap 35 실측:
        # 「손실만 나는데 해지하겠다」에 절차 카드 한 장(관련도 0.09)으로 끝났고, 정작 맞는
        # 카드 pitch.k03.012 는 이 도구로 오면 4.25 로 1등이었다). 도구 설명이 곧 계획의
        # 판단 재료라, 축을 설명에 박아야 갈린다.
        Tool("pitch", "직원이 전한 **고객의 말·반응**으로 화법(대사·반론 대응·논거)을 "
             "찾는다 — 「고객이 '…'라는데 어떻게 대응하지」·「이렇게 거절하면 뭐라고 "
             "하지」가 여기다. 질문 안에 고객이 한 말이 있으면 고객 화면이 열려 있어도 "
             "여기다(그 말로 찾는 도구는 이것 하나뿐이다)", _pitch,
             progress="상담 화법"),
        Tool("fact", "제도·상품의 확정된 수치를 기준시점·출처와 함께 돌려준다", _fact,
             progress="제도·상품 수치"),
        Tool("procedure", "업무를 어떤 순서·채널로 처리하는지와 걸리는 주의를 돌려준다", _procedure,
             progress="업무 처리 절차"),
        Tool("screen", "단말 화면번호를 찾는다 — '무슨무슨 조회/등록은 몇 번 화면인가'", _screen,
             progress="단말 화면번호"),
        Tool("channel", "고객이 스타뱅킹·인터넷뱅킹에서 직접 처리하는 메뉴 경로를 돌려준다",
             _channel, progress="비대면 채널 경로"),
        Tool("segment", "관리 대상 고객군의 정의와 선정 조건을 설명한다", _segment,
             progress="고객군 정의"),
        Tool("method", "무엇을 어떤 기준으로 판단하는지(관리 방법론)를 돌려준다", _method,
             progress="관리 방법론"),
        Tool("fieldtip", "영업점 현장 관찰(본부 지침 아님)을 돌려준다", _fieldtip,
             progress="영업점 현장 관찰"),
        Tool("market", "시장이 어떻게 돌아가나 — 시황·증시·환율·금리·경제 이벤트와 "
                       "투자전략을 기준시점과 함께 돌려준다",
             _market_like("market", "시황"), progress="시황 자료"),
        Tool("lineup", "우리가 뭘 파나 — 이달의 추천펀드, 디폴트옵션 포트폴리오 구성상품·"
                       "비중·금리, 투자성향별 포트폴리오, TDF 빈티지별 비중을 기준시점과 "
                       "함께 돌려준다",
             _market_like("lineup", "운용 상품"), progress="운용 상품 자료"),
        # "왜 관리 대상(타겟)인가"를 설명에 명시한다 — 재료에 실려 있는데(why_this_customer·
        # 판단근거) 설명이 잔액·수익률만 말하면, 계획이 그 질문을 segment(고객군 일반 정의)로
        # 보내고 이 도구를 안 부른다. 도구 설명이 곧 계획의 판단 재료다.
        # 「이 고객한테 뭘 추천하지」가 이 도구다. 설명에 **어디까지 안내할 수 있는가**를
        # 적는다 — 계획 LLM 이 읽는 유일한 판단 재료라, 여기가 흐리면 그 질문이 lineup 만
        # 세 바퀴 돌다가 재료 0건으로 끝난다(실제로 그랬다). 답의 톤(범위 안의 특정 상품은
        # 짚어 말해도 되고 권유 표현만 금지)은 도구 설명이 아니라 생성 지시가 정한다
        # (COMPOSE_SYSTEM 8 · §8 관리대장 2026-09-02 개정).
        Tool("suitable", "이 고객 투자성향으로 **어디까지 안내할 수 있는지** — 적합성 게이트가 "
             "허용하는 위험등급 상한, 그 범위를 통과한 상품·포트폴리오 목록, 제외된 상품과 "
             "그 사유를 돌려준다. 「이 고객한테 뭘 추천하지」·「무슨 상품 있어」가 여기다",
             _suitable, progress="적합성 범위"),
        Tool("customer", "지금 열려 있는 고객의 브리핑 자료(잔액·수익률·성립 요건, 그리고 이 고객이 "
             "왜 관리 대상(타겟)으로 선정됐는지의 근거)를 돌려준다", _customer,
             progress="고객 브리핑 자료"),
        Tool("history", "이 고객과 **지난** 상담(이전 세션)에서 무슨 얘기를 했는지(날짜·질문·"
             "안내 요지) 돌려준다 — 지금 진행 중인 상담은 들어 있지 않다",
             _history, progress="지난 상담 기록"),
        # `history` 와 갈라 두는 축은 **시점**이다 — 그쪽은 이번 세션을 제외하고 이쪽은 이번
        # 세션만 싣는다. 설명이 갈리지 않으면 「대화 내용 요약해줘」가 history 로 가서 지난
        # 상담을 요약하거나 «기록 없음»으로 끝난다.
        Tool("transcript", "**이번 상담**(지금 진행 중인 세션)에서 지금까지 오간 대화 전문 — "
             "직원 질문과 에이전트 답변을 돌려준다. 「지금까지 대화 요약해줘」·「상담 내용 "
             "정리해서 쪽지로 보내줘」처럼 이번 상담을 정리·요약·전달하려는 요청은 여기다",
             _transcript, progress="이번 상담 대화 기록"),
        # 시점·기한이 걸린 질문은 재료가 없으면 답이 안 나온다(§8 "지어내지 않는다"가 그대로
        # «말하지 못한다»가 된다). 도구 설명이 곧 계획의 판단 재료이므로, 언제 부르는지를
        # 예시로 박아 둔다 — "얼마 안 남았다"류 문장을 쓰려는 턴이 전부 여기 걸려야 한다.
        # 계산기(07/01 ② 3번)의 첫 조각. 「얼마 더 넣으면 얼마 받나」는 검색으로 답할 수
        # 없고, 재료 밖 계산은 금지라(§5) 코드가 계산해 싣지 않으면 말할 방법이 없다.
        Tool("tax_credit", "«얼마를 더 납입하면 세액공제로 얼마나 돌려받는지»를 계산한다 — "
             "'300만원 더 넣으면 얼마 받아', '한도 채우면 얼마 돌려받아'처럼 **환급액·"
             "납입액을 계산해 달라는** 질문에 쓴다(제도 설명이 아니라 이 고객의 금액). "
             "이 고객이 ISA 만기자금을 갖고 있으면 «일부만 옮기면 세액공제 얼마»처럼 "
             "전환액에 걸린 계산도 여기다 — 전환 특례까지 함께 계산해 돌려준다",
             _tax_credit, progress="세액공제 환급액"),
        Tool("date", "오늘이 며칠인지와 연말까지 남은 일수를 돌려준다 — '오늘 며칠이야', "
             "'연말까지 얼마 남았어', '언제까지 납입해야 해'처럼 **시점·기한**이 걸린 질문, "
             "그리고 답변에 '며칠 남았다·올해 안에'를 쓰려는 모든 경우에 먼저 부른다", _date,
             progress="오늘 날짜·기한"),
        # 화면 ⑨ 가 이미 고른 안내 콘텐츠를 대화 쪽 재료로 잇는다. `lineup`(우리가 뭘 파나)·
        # `suitable`(어디까지 안내할 수 있나)과 갈리는 축은 **고객에게 보낼 콘텐츠**다 —
        # 설명이 갈리지 않으면 계획이 세미나 질문을 lineup 으로 보내고 재료 0건으로 끝난다.
        Tool("outreach", "이 고객에게 안내할 세미나·이벤트와 그 발송 문구를 돌려준다 — "
             "「보낼 만한 세미나 있어」·「왜 이 이벤트야」·「다른 건 없어」·「문자로 뭐라고 "
             "보내지」가 여기다", _outreach, progress="안내할 이벤트·세미나"),
        # 고객 화면을 열기 «전»의 재료라 _NEEDS_CUSTOMER 에 넣지 않는다 — 오히려 고객이
        # 안 열려 있을 때가 이 도구의 자리다.
        Tool("targets", "오늘 관리해야 할 타겟 고객 목록 — 누가 왜 선정됐는지(성립 요건과 "
             "그 요건을 성립시킨 값)를 순서대로 돌려준다. 「오늘 누구부터 봐야 해」·"
             "「타겟 몇 명이야」·「타겟 목록 쪽지로 보내줘」가 여기다", _targets,
             progress="오늘의 타겟 고객 목록"),
        # `pitch` 와 갈라 두는 이유는 재료가 오는 곳이 다르기 때문이다. pitch 는 질문으로
        # 지식베이스 전체를 찾고, 이쪽은 **이 고객의 문제상황**에 걸린 것만 본다 — 화면
        # ⑥⑦⑧ 과 같은 후보군이다. 설명이 갈리지 않으면 계획이 둘을 구분하지 못한다.
        Tool("playbook", "지금 열려 있는 고객의 **계좌 상태**(문제상황)에 걸린 화법·"
             "예상반론·관리방법론·업무절차 참고자료를 브리핑 화면 ⑥⑦⑧ 과 같은 후보군에서 "
             "돌려준다. **후보를 고르는 것은 고객 상태이고 질문은 그것을 좁히기만 한다** — "
             "이 고객 상태에 안 걸린 자료는 무엇을 물어도 여기서 안 나온다. 「이 고객 상담 "
             "전에 뭘 준비하지」·「이 고객한테 걸린 자료 뭐 있어」처럼 **찾을 대상이 질문에 "
             "특정되지 않은** 턴이 여기다. 고객이 한 말에 대응하는 화법은 여기가 아니라 "
             "pitch 다",
             # 진행 문구는 직원이 읽는 말이다 — 다른 도구처럼 평이한 이름으로 둔다.
             # 「상태에 걸린」은 요건 매칭을 가리키는 코드 안의 말이고, 화면에 세우면
             # 기계가 지어낸 문장으로 읽힌다(nodes/act.py 의 제안 문구와 같은 자리).
             _playbook, progress="이 고객에게 맞는 참고자료"),
        # 재료가 지식베이스도 고객 원장도 아니고 **이 에이전트가 방금 한 답변**이다. 설명이
        # 갈리는 축은 «무엇을 고치나»다 — 화면의 AI 문장(correction 노드)이 아니라 대화
        # 답변이고, 새로 찾는 것이 아니라 있던 것을 다시 쓴다. 이 도구가 없던 동안 「좀 더
        # 짧게 줄여줘」는 재료가 없어 답할 길이 없었고, 분류는 그것을 브리핑 수정으로 읽어
        # 화법과 무관한 화면 문장을 고쳤다(2026-09-10 실측 — tools/answered.py 머리말).
        Tool("last_answer", "**이 에이전트가 이번 상담에서 한 답변**의 원문 — 직원이 이전 답변을 "
             "가리키는 요청 전부에 쓴다: «더 짧게»·«쉽게»·«고객 대사만»·«핵심만» 같은 다시 쓰기, "
             "«그 중 두 번째»·«그거 왜»처럼 답변 안의 것을 가리키는 후속 질문, «아까 수수료 "
             "설명한 거 요약해줘·자세히 설명해줘»처럼 몇 턴 전 답변을 되짚는 요청. query 에는 "
             "이전 대화의 턴 번호를 «[3]» 꼴로 적는다(없으면 직전 답변). 원문에 없는 세부까지 "
             "설명해야 하면(자세히·왜) «[3] 근거» 처럼 «근거»를 붙인다 — 그 답변이 썼던 자료를 "
             "함께 싣는다. 새 검색이 아니라 있던 답을 다시 쓰는 재료라 대개 이것 하나로 끝난다. "
             "그 답변에 없던 내용을 더해 달라면(«수수료도 넣어서») 그 재료의 도구를 함께 "
             "부른다. 화면(AI브리핑)의 문장을 고치는 요청은 여기가 아니다",
             _last_answer, progress="이전 답변"),
    )
}

#: 열려 있는 고객이 있어야 성립하는 도구. 어느 고객인지가 재료의 전제다(§3).
_NEEDS_CUSTOMER = frozenset({"customer", "history", "transcript", "suitable", "tax_credit",
                             "playbook", "outreach"})

#: 직전 답변이 있어야 성립하는 도구. 첫 턴이나 되묻기 직후처럼 다시 쓸 답이 없으면 카탈로그에
#: 올리지 않는다 — 재료가 없는 도구를 보여주면 계획이 한 바퀴를 버린다(고객 전제 도구와
#: 같은 이유). 판정은 코드가 아는 값(턴 기록의 answer)으로 한다.
_NEEDS_ANSWER = frozenset({"last_answer"})


def usable(state: AgentState | None = None) -> list[str]:
    """이 턴에 실제로 부를 수 있는 도구 이름. 고객 화면이 닫혀 있으면 고객 전제 도구는
    빠진다(§3). 카탈로그와 재계획의 '아직 안 써 본 도구'가 같은 목록을 봐야 한다 —
    갈리면 카탈로그에 없는 도구를 다시 시도하라고 말하게 된다.

    **이번 턴에 죽은 도구도 빠진다.** 고장은 질의를 바꿔 고쳐지는 것이 아니라서, 다시
    보여주면 계획이 같은 도구를 다시 골라 한 바퀴를 버린다(빗나간 호출은 «말을 바꿔라»가
    답이지만 고장은 아니다 — PLAN_MISSES_BLOCK 과 여기가 갈리는 이유다). 재계획의
    '아직 안 써 본 도구'에서도 같은 이유로 빠져야 하므로 판정은 여기 한 곳이다.
    """
    opened = bool((state or {}).get("customer_id"))
    answered = last_answered((state or {}).get("history")) is not None
    # 이번 턴의 장부에서 «고장»으로 끝난 호출의 도구(`nodes/plan.py` 의 steps 규약).
    broken = {s.get("tool") for s in ((state or {}).get("steps") or [])
              if s.get("outcome") == "failed"}
    return [t.name for t in TOOLS.values()
            if (opened or t.name not in _NEEDS_CUSTOMER)
            and (answered or t.name not in _NEEDS_ANSWER)
            and t.name not in broken]


def catalog(state: AgentState | None = None) -> str:
    """계획 프롬프트에 실리는 도구 목록. 쓸 수 없는 도구는 애초에 보여주지 않는다 —
    고객 화면이 닫혀 있는데 customer 를 제안하게 두면 한 스텝을 낭비한다."""
    return "\n".join(f"- {TOOLS[n].name}: {TOOLS[n].desc}" for n in usable(state))


#: 도구 호출 하나의 결과 셋. 장부(`state["steps"]`)의 `outcome` 칸과 로그·점수가 같은 값을 쓴다 —
#: «찾아보고 없음»(MISS)과 «확인하지 못함»(FAILED)은 다른 사건이다(base.ToolFailure 머리말).
FOUND, MISS, FAILED = "found", "miss", "failed"

#: 로그에 싣는 질의 미리보기 길이 — 질의는 직원의 말이라 전문을 남기지 않는다.
QUERY_PREVIEW = 40


def _preview(query: str) -> str:
    text = " ".join(query.split())
    return text[:QUERY_PREVIEW] + "…" if len(text) > QUERY_PREVIEW else text


def record(name: str, query: str, outcome: str, *, reason: str = "",
           found: Evidence | None = None, gate: dict | None = None,
           requeried: bool = False) -> None:
    """도구 호출 한 건을 «코드가 아는 사실»로 남긴다 — 단계 로그 한 줄 + Langfuse 점수 한 건.

    장부(`steps`)는 이 턴의 답을 만드는 재료이고, 이것은 나중에 되짚는 기록이다. 고장(FAILED)만
    WARNING 이다. 후보·채택 수는 적합성 게이트가 돈 도구에만 있다(adequacy._adopt).
    """
    cards = [f"{s['id']}({s['score']})" if s.get("score") is not None else str(s["id"])
             for s in ((found or {}).get("sources") or []) if s.get("id")]
    observability.step(
        "tool", name, result=outcome,
        candidates=(gate or {}).get("candidates"), picked=(gate or {}).get("picked"),
        cards=cards or None, branches=(gate or {}).get("branches") or None,
        requeried=requeried or None, reason=reason or None,
        level=logging.WARNING if outcome == FAILED else logging.INFO)
    observability.score("tool_outcome", outcome,
                        comment=f"{name} · 질의 {_preview(query)!r}" + (f" · 사유 {reason}" if reason else ""))


def run(name: str, state: AgentState, query: str) -> Evidence | None:
    """도구 하나를 부른다. 근거를 못 찾으면 **직원의 원문 질문으로 한 번 더** 찾는다.

    계획이 만든 질의는 질문을 줄여 쓴 것이라, 줄이는 과정에서 검색이 기대는 말이 빠질 수
    있다 — "포트폴리오 운용현황 조회 화면 번호는?"이 "운용현황 조회 화면번호"가 되면
    n-gram 이 0건을 낸다(원문으로는 찾는다). 재검색은 같은 도구·같은 지식베이스이므로
    근거의 경계를 넓히지 않는다. 넓히는 것은 **질의 한 개**뿐이다.

    LLM 이 고른 질의가 항상 낫다고 볼 이유가 없다는 것이 이 재시도의 근거다. 지식베이스에
    답이 있는데 질의를 잘못 골라 "없습니다"로 끝나는 것이 가장 나쁜 실패다.

    도구가 죽으면 `ToolFailure` 를 올린다 — 0건과 같은 값으로 접지 않는다(그 클래스 머리말).
    **재검색으로 넘어가지도 않는다**: 두 번째 질의는 «검색이 빗나갔을 때» 건지는 장치이고,
    터진 코드는 말을 바꾼다고 돌아오지 않는다.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return None
    if tool.progress:
        # 실제로 이 도구를 돌리기 직전에만 찍는다(progress.py ②). 문구는 도구 선언에서
        # 온다 — LLM 이 만든 질의(query)는 싣지 않는다.
        progress.emit(f"{progress.object_of(tool.progress)} 찾고 있어요")
    question = (state.get("question") or "").strip()
    attempts = [query] + ([question] if question and question != query else [])
    # 관측 span — 「어떤 도구를 어떤 질의로 불러 무엇을 얻었나」가 답이 갈리는 자리다.
    # generation 만 보내면 트레이스에는 «LLM 을 다섯 번 불렀다»까지만 남는다.
    # 적합성 게이트가 이번 호출에서 본 후보·채택 수(adequacy._adopt 가 같은 노드 안에서
    # 채운다 — record_branches 와 같은 규약). 호출 전에 비워 지난 호출의 값이 남지 않게 한다.
    state.pop("_gate", None)
    with observability.span(f"tool:{name}", input=query) as sp:
        for i, attempt in enumerate(attempts):
            try:
                found = tool.run(state, attempt)
            except LLMError:
                # 도구가 죽은 것과 **LLM 이 죽은 것**은 다른 사건이다. 뒤를 앞으로 접으면
                # "찾아봤는데 재료가 없다"로 나가고, 그게 §11 이 막으려는 바로 그 답이다.
                raise
            except Exception as exc:
                # 도구 하나가 죽어도 루프는 다음 도구로 간다 — 다만 그 사실을 **0건과 같은
                # 값으로 접지 않는다**. 접으면 이 턴의 답이 «찾아봤는데 자료가 없습니다»가
                # 된다(ToolFailure 머리말). 사유를 실어 올리고 처분은 계획 루프가 한다.
                reason = f"{type(exc).__name__}: {exc}"
                sp.update(output=None, found=False, failed=True)
                record(name, query, FAILED, reason=reason)
                raise ToolFailure(name, reason) from exc
            if found is not None:
                # 원문 재검색으로 건졌는지도 남긴다 — 계획이 고른 질의가 얼마나 빗나가는지가
                # 이 한 칸에 쌓인다(재검색이 잦으면 계획 프롬프트를 봐야 한다는 신호다).
                sp.update(output=found["text"], found=True, retried=bool(i))
                record(name, query, FOUND, found=found, gate=state.pop("_gate", None),
                       requeried=bool(i))
                return found
        sp.update(output=None, found=False)
        record(name, query, MISS, gate=state.pop("_gate", None), requeried=len(attempts) > 1)
        return None
