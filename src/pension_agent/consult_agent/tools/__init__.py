"""도구 레지스트리 — 에이전트가 할 수 있는 일의 **능력 표면**.

예전에는 능력 표면이 `routing.INTENTS` 였다. 의도 enum 하나 = 노드 하나 = 답변 하나였고,
새 기능을 붙이려면 enum·분기표·노드를 함께 늘려야 했고, 무엇보다 **한 턴에 하나만** 쓸 수
있었다. "이 고객 수수료 불만인데 우리 IRP 수수료가 얼마고 뭐라고 말해야 하나" 같은 질문은
값·고객·화법 세 재료가 필요한데 그중 하나만 골라졌다.

여기서는 능력이 도구 목록이다. 계획 루프(nodes/plan.py)가 한 턴에 여러 도구를 부르고,
반환된 근거를 **원장**에 쌓고, compose 가 그것만으로 답을 만든다.

━━ 모든 도구는 같다. 다른 것은 `atomic` 목록 하나다 ━━
도구는 종류로 갈리지 않는다. 전부 근거를 내놓고, compose 가 그 근거로 답을 쓴다. 화법도
예외가 아니다 — 화법은 `atomic` 이 비어 있는 도구일 뿐이다.

`atomic` 은 **원문 그대로여야 하는 스팬** 목록이다. 이게 필요한 이유는 verify_texts 가
수치의 *집합 포함* 검사라서, 원장에 있는 숫자를 **잘못 짝지은 것을 못 잡기** 때문이다.
"총급여 5,500만원 이하 16.5%, 초과 13.2%" 가 원장에 있으면 "초과면 16.5%" 도 통과한다
(두 숫자가 다 원장에 있으므로). 값과 조건이 한 줄에 붙어 있어 분리 검증이 불가능하다.
그래서 그 줄은 **통째로 그대로** 나가야 한다.

원문 요구는 두 종류이고, **도구가 명시적으로 갈라 선언한다**(숫자가 있는지로 추론하지
않는다 — ⚠ 경고문이 화면번호를 인용하고 있어서 수치 주장으로 오판되는 일이 실제로 있었다).

  atomic    값 + 그 값이 성립하는 조건이 붙은 한 덩이. 답변이 그 숫자를 쓸 때만 원문을
            요구한다(값을 언급하지 않는 답변까지 강요하면 전부 표 덤프가 된다).
            어기면 생성문을 **폐기**한다 — 틀린 짝을 옳은 블록 옆에 남겨둘 수 없다.
  notices   빠지면 안 되는 표시(⚠ 유의 · 「하지 말 것」 · 「본부 지침 아님」). 언급 여부와
            무관하게 항상 요구한다. 누락은 답변이 틀린 게 아니라 덜 갖춰진 것이므로
            블록을 **덧붙여** 채운다.

atomic 이 비어 있는 도구(pitch·customer)는 수치 집합 검사만 걸린다.

━━ 반환 규약 ━━
Evidence 또는 None. None 은 "이 도구로는 근거를 못 찾았다"는 뜻이고, 루프는 다른 도구를
시도하거나 원장이 빈 채로 끝낸다(→ 정직한 '없음' 답변). 도구가 억지로 뭔가 만들어내는
경로는 두지 않는다.
"""

from __future__ import annotations

from pension_agent import observability
from pension_agent.consult_agent import progress
from pension_agent.consult_agent.select import llm_pick, pick  # noqa: F401 — 후크(머리말)
from pension_agent.consult_agent.state import KB, AgentState  # noqa: F401 — 후크(머리말)
from pension_agent.knowledge.kb import retrieve  # noqa: F401 — 후크(머리말)
from pension_agent.llm import LLMError, generate  # noqa: F401 — 후크(머리말)

from pension_agent.consult_agent.tools.base import (  # noqa: F401
    Evidence,
    Tool,
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
    BLOCKED_MAX,
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
    _adopt,
    _headline,
    fits_question,
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
)
from pension_agent.consult_agent.tools.outreach import (  # noqa: F401
    _outreach,
)
from pension_agent.consult_agent.tools.ledger import (  # noqa: F401
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
        Tool("pitch", "고객에게 실제로 할 말(대사·반론 대응·논거)을 만든다", _pitch,
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
        # `pitch` 와 갈라 두는 이유는 재료가 오는 곳이 다르기 때문이다. pitch 는 질문으로
        # 지식베이스 전체를 찾고, 이쪽은 **이 고객의 문제상황**에 걸린 것만 본다 — 화면
        # ⑥⑦⑧ 과 같은 후보군이다. 설명이 갈리지 않으면 계획이 둘을 구분하지 못한다.
        # 화면 ⑨ 가 이미 고른 안내 콘텐츠를 대화 쪽 재료로 잇는다. `lineup`(우리가 뭘 파나)·
        # `suitable`(어디까지 안내할 수 있나)과 갈리는 축은 **고객에게 보낼 콘텐츠**다 —
        # 설명이 갈리지 않으면 계획이 세미나 질문을 lineup 으로 보내고 재료 0건으로 끝난다.
        Tool("outreach", "이 고객에게 안내할 세미나·이벤트와 그 발송 문구를 돌려준다 — "
             "「보낼 만한 세미나 있어」·「왜 이 이벤트야」·「다른 건 없어」·「문자로 뭐라고 "
             "보내지」가 여기다", _outreach, progress="안내할 이벤트·세미나"),
        Tool("playbook", "지금 열려 있는 고객의 상태(문제상황)에 걸린 화법·예상반론·"
             "관리방법론·업무절차 참고자료를 브리핑 화면 ⑥⑦⑧ 과 같은 후보군에서 돌려준다",
             _playbook, progress="이 고객 상태에 걸린 참고자료"),
    )
}

#: 열려 있는 고객이 있어야 성립하는 도구. 어느 고객인지가 재료의 전제다(§3).
_NEEDS_CUSTOMER = frozenset({"customer", "history", "transcript", "suitable", "tax_credit",
                             "playbook", "outreach"})


def usable(state: AgentState | None = None) -> list[str]:
    """이 턴에 실제로 부를 수 있는 도구 이름. 고객 화면이 닫혀 있으면 고객 전제 도구는
    빠진다(§3). 카탈로그와 재계획의 '아직 안 써 본 도구'가 같은 목록을 봐야 한다 —
    갈리면 카탈로그에 없는 도구를 다시 시도하라고 말하게 된다."""
    opened = bool((state or {}).get("customer_id"))
    return [t.name for t in TOOLS.values() if opened or t.name not in _NEEDS_CUSTOMER]


def catalog(state: AgentState | None = None) -> str:
    """계획 프롬프트에 실리는 도구 목록. 쓸 수 없는 도구는 애초에 보여주지 않는다 —
    고객 화면이 닫혀 있는데 customer 를 제안하게 두면 한 스텝을 낭비한다."""
    return "\n".join(f"- {TOOLS[n].name}: {TOOLS[n].desc}" for n in usable(state))


def run(name: str, state: AgentState, query: str) -> Evidence | None:
    """도구 하나를 부른다. 근거를 못 찾으면 **직원의 원문 질문으로 한 번 더** 찾는다.

    계획이 만든 질의는 질문을 줄여 쓴 것이라, 줄이는 과정에서 검색이 기대는 말이 빠질 수
    있다 — "포트폴리오 운용현황 조회 화면 번호는?"이 "운용현황 조회 화면번호"가 되면
    n-gram 이 0건을 낸다(원문으로는 찾는다). 재검색은 같은 도구·같은 지식베이스이므로
    근거의 경계를 넓히지 않는다. 넓히는 것은 **질의 한 개**뿐이다.

    LLM 이 고른 질의가 항상 낫다고 볼 이유가 없다는 것이 이 재시도의 근거다. 지식베이스에
    답이 있는데 질의를 잘못 골라 "없습니다"로 끝나는 것이 가장 나쁜 실패다.
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
    with observability.span(f"tool:{name}", input=query) as sp:
        for i, attempt in enumerate(attempts):
            try:
                found = tool.run(state, attempt)
            except LLMError:
                # 도구가 죽은 것과 **LLM 이 죽은 것**은 다른 사건이다. 뒤를 앞으로 접으면
                # "찾아봤는데 재료가 없다"로 나가고, 그게 §11 이 막으려는 바로 그 답이다.
                raise
            except Exception:
                sp.update(output=None, found=False, failed=True)
                return None  # 도구 하나가 죽어도 루프는 다음 도구로 간다
            if found is not None:
                # 원문 재검색으로 건졌는지도 남긴다 — 계획이 고른 질의가 얼마나 빗나가는지가
                # 이 한 칸에 쌓인다(재검색이 잦으면 계획 프롬프트를 봐야 한다는 신호다).
                sp.update(output=found["text"], found=True, retried=bool(i))
                return found
        sp.update(output=None, found=False)
        return None
