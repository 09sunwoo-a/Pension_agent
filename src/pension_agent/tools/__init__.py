"""도구 레지스트리 — 계획 루프가 부르는 도구 목록과 실행·기록.

    base.py         Tool · ToolFailure       도구 선언 · 실행 실패 예외

    지식베이스 (kb_search/)
    cards.py        fact procedure screen channel segment method fieldtip
                                             수치·절차·화면·채널·고객군·방법론·현장 [LLM]
    market.py       market lineup            시황 · 운용 상품 [LLM]
    pitch.py        pitch                    고객 발화로 화법 검색 [LLM]
    playbook.py     playbook                 고객 상태별 참고자료(화면 ⑥⑦⑧)

    고객별 솔루션 (customer_solution/tool.py)
                    customer                 브리핑 재료
                    briefing                 전체 브리핑 문서 [LLM 1회]
                    suitable                 적합성 범위
                    outreach                 세미나·이벤트·발송 문구

    부점 브리핑 (customer_solution/tool.py · branch_briefing/tool.py)
                    branch_targets           관리 대상 추천(오늘의 타겟 목록 · CASE 순서 · 첫 추천 유지)
                    branch_status            부점 현황(지표·세그먼트 전월말 대비 · 오늘의 CASE)
                    branch_search            고객 검색(부점 고객 데이터 SQL 질의) [LLM]

    일반 (general/)
    history.py      history transcript       지난 상담 · 이번 상담 대화
    dates.py        date                     오늘 날짜 · 연말 잔여일
    tax_credit.py   tax_credit               세액공제 환급 계산
    answered.py     last_answer              이번 상담의 이전 답변

    공용
    adequacy.py     fits_question            근거 적합성 판정 [LLM]
    kb_search/combine.py evidence_from_cards  카드 묶음 → 원장 항목
"""

from __future__ import annotations

import logging

from pension_agent.common import observability
from pension_agent.common import progress
from pension_agent.tools.kb_search.select import llm_pick, pick  # noqa: F401 — 테스트 교체 후크
from pension_agent.state import KB, AgentState  # noqa: F401 — 테스트 교체 후크
from pension_agent.knowledge.kb import retrieve  # noqa: F401 — 테스트 교체 후크
from pension_agent.common.llm import LLMError, generate  # noqa: F401 — 테스트 교체 후크

from pension_agent.tools.base import (  # noqa: F401
    Tool,
    ToolFailure,
)
from pension_agent.evidence.record import (  # noqa: F401
    Evidence,
    _clean,
    _ev,
    _scope,
)
from pension_agent.tools.kb_search.cards import (  # noqa: F401
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
from pension_agent.tools.kb_search.market import (  # noqa: F401
    MARKET_TOP_K,
    _market_like,
    _prefer_sections,
    _render_market,
)
from pension_agent.tools.customer_solution.tool import (  # noqa: F401
    _POOL_KEYS,
    _briefing,
    _citable,
    _cond_labels,
    _customer,
    _outreach,
    _suitable,
    _targets,
)
from pension_agent.tools.general.history import (  # noqa: F401
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
from pension_agent.tools.adequacy import (  # noqa: F401
    ADEQUACY_MAX_TOKENS,
    _adequacy_verdict,
    _adopt,
    _headline,
    fits_question,
    record_branches,
)
from pension_agent.tools.kb_search.pitch import (  # noqa: F401
    PITCH_TOP_K,
    _pitch,
)
from pension_agent.tools.general.dates import (  # noqa: F401
    _WEEKDAYS,
    _date,
)
from pension_agent.tools.general.tax_credit import (  # noqa: F401
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
from pension_agent.tools.kb_search.playbook import (  # noqa: F401
    PLAYBOOK_LANES,
    PLAYBOOK_TOP_K,
    _PLAYBOOK_TYPES,
    _playbook,
    cited_cards,
    playbook_evidence,
    playbook_hits,
    playbook_ranked,
)
from pension_agent.tools.branch_briefing.tool import (  # noqa: F401
    _branch,
    _branch_status,
)
from pension_agent.tools.general.answered import (  # noqa: F401
    CARDS_HEADER,
    SELF_SOURCE,
    _last_answer,
    last_answered,
    referenced_turn,
)
from pension_agent.tools.kb_search.combine import (  # noqa: F401
    evidence_from_cards,
)
from pension_agent.evidence.ledger import (  # noqa: F401
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


TOOLS: dict[str, Tool] = {
    t.name: t for t in (
        # playbook 과의 구분 축(고객 발화 vs 계좌 상태)을 설명에 명시 — 빠지면 반론 질문이 playbook 으로 감
        Tool("pitch", "직원이 전한 **고객의 말·반응**으로 화법(대사·반론 대응·논거)을 "
             "찾는다 — 「고객이 '…'라는데 어떻게 대응하지」·「이렇게 거절하면 뭐라고 "
             "하지」가 여기다. 질문 안에 고객이 한 말이 있으면 고객 화면이 열려 있어도 "
             "여기다(그 말로 찾는 도구는 이것 하나뿐이다)", _pitch,
             progress="상담 화법"),
        Tool("fact", "제도·상품의 확정된 수치를 기준시점·출처와 함께 돌려준다", _fact,
             progress="제도·상품 수치"),
        # 화면번호·메뉴 경로는 screen·channel 소관임을 명시 — 빠지면 «등록 방법»이 procedure 하나로 끝남
        Tool("procedure", "업무를 어떤 고객 상황에서 어떤 순서로 처리하는지와 걸리는 주의를 "
             "돌려준다. 그 업무의 단말 화면번호는 screen, 고객이 앱·웹에서 하는 메뉴 경로는 "
             "channel 에 따로 있다 — 「~ 등록·신청·변경 방법」을 물으면 그 둘도 함께 부른다",
             _procedure, progress="업무 처리 절차"),
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
        # «어디까지 안내 가능한가»를 설명에 명시 — 흐리면 추천 질문이 lineup 만 돌다 0건. 톤은 생성 지시 소관
        Tool("suitable", "이 고객 투자성향으로 **어디까지 안내할 수 있는지** — 적합성 게이트가 "
             "허용하는 위험등급 상한, 그 범위를 통과한 상품·포트폴리오 목록, 제외된 상품과 "
             "그 사유를 돌려준다. 「이 고객한테 뭘 추천하지」·「무슨 상품 있어」가 여기다",
             _suitable, progress="적합성 범위"),
        # 재료에 실린 축(거래활동·타겟 근거 등)은 설명에도 적을 것 — 설명에 없으면 계획이 이 도구를 안 부름
        Tool("customer", "지금 열려 있는 고객의 브리핑 자료(잔액·수익률·보유상품·거래활동(최근 "
             "매매·입금·최근 1개월 고유계정대 증감)·계좌 상태·성립 요건, 그리고 이 고객이 "
             "왜 관리 대상(타겟)으로 선정됐는지의 근거)를 돌려준다", _customer,
             progress="고객 브리핑 자료"),
        # customer 와의 구분: 부분 질문 재료가 아닌 양식 문서 한 편, 그대로 답이 됨
        Tool("briefing", "지금 열려 있는 고객의 **전체 브리핑 문서**(고객 정보·계좌 현황 표와 "
             "AI 분석)를 만든다 — 「이 고객 브리핑 해줘」·「전체 정리해줘」처럼 브리핑 전체를 "
             "요청할 때만 쓴다. 값 하나·이유 하나를 묻는 질문은 customer 다. 이 도구를 부르면 "
             "다른 도구는 부르지 않고 끝낸다",
             _briefing, progress="고객 브리핑 문서"),
        Tool("history", "이 고객과 **지난** 상담(이전 세션)에서 무슨 얘기를 했는지(날짜·질문·"
             "안내 요지) 돌려준다 — 지금 진행 중인 상담은 들어 있지 않다",
             _history, progress="지난 상담 기록"),
        # history 와의 구분: 이번 세션만 — 설명이 흐리면 «대화 요약»이 history 로 감
        Tool("transcript", "**이번 상담**(지금 진행 중인 세션)에서 지금까지 오간 대화 전문 — "
             "직원 질문과 에이전트 답변을 돌려준다. 「지금까지 대화 요약해줘」·「상담 내용 "
             "정리해서 쪽지로 보내줘」처럼 이번 상담을 정리·요약·전달하려는 요청은 여기다",
             _transcript, progress="이번 상담 대화 기록"),
        # 재료 밖 계산 금지 — 환급액은 코드가 계산해 실어야만 답 가능
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
        # lineup·suitable 과의 구분: 고객에게 보낼 콘텐츠(화면 ⑨) — 흐리면 세미나 질문이 lineup 으로 감
        Tool("outreach", "이 고객에게 안내할 세미나·이벤트와 그 발송 문구를 돌려준다 — "
             "「보낼 만한 세미나 있어」·「왜 이 이벤트야」·「다른 건 없어」·「문자로 뭐라고 "
             "보내지」가 여기다", _outreach, progress="안내할 이벤트·세미나"),
        # 부점 브리핑 세 기능 — 고객 화면 없이 쓰는 도구(_NEEDS_CUSTOMER 미포함).
        # 축: 누구를 볼지(branch_targets, 룰베이스 선정) · 부점이 어떤가(branch_status, 코드 계산) ·
        # 직원이 정한 조건의 고객(branch_search, SQL). 설명이 겹치면 계획이 엉뚱한 쪽을 고른다
        Tool("branch_targets", "**관리 대상 추천** — 오늘 중점적으로 관리할 고객 목록. 누가 왜 "
             "선정됐는지(타겟 룰베이스의 성립 요건과 그 요건을 성립시킨 값)를 오늘의 부점 관리 방향 "
             "순서대로 돌려준다. 이 대화에서 이미 추천했으면 **그 첫 추천 목록을 그대로** 돌려준다. "
             "「오늘 중점적으로 관리할 고객은 누구야」·「오늘 누구부터 봐야 해」·「처음 추천한 고객 "
             "다시 보여줘」·「타겟 몇 명이야」·「타겟 목록 쪽지로 보내줘」가 여기다", _targets,
             progress="관리 대상 추천"),
        Tool("branch_status", "**부점 현황** — 우리 부점의 지표(잔액·계좌 수·평균 수익률)와 "
             "세그먼트 인원(미운용 현금성자산·만기도래·계약이전 사전조회·세액공제 여유·투자성향 "
             "불일치·연금개시 가능·디폴트옵션 미설정 등)을 **전월말 대비 변화**와 함께, 그리고 그 "
             "변화로 판정한 **오늘의 관리 방향(CASE)** 을 돌려준다. 「오늘 부점 현황 말해줘」·"
             "「지난달보다 뭐가 늘었어」·「오늘 부점에서 뭘 신경 써야 해」가 여기다", _branch_status,
             progress="부점 현황"),
        Tool("branch_search", "**고객 검색** — 우리 부점의 IRP 고객 데이터를 직원이 말한 조건으로 "
             "조회·집계한다. 「현금성 장기대기 고객 보여줘」·「IRP 잔액 7천만원 이상 고객 보여줘」·"
             "「디폴트옵션 미등록 고객 보여줘」·「ISA 만기 고객은 몇 명이야」·「납입금 미운용 고객 몇 "
             "명이고 현금 합계 얼마야」·「평가금액 상위 5명」·「다른 부점이랑 타겟 수 비교」처럼 "
             "**여러 고객을 조건으로 세거나 고르는** 질문이 여기다. 다른 부점은 집계로만 볼 수 있다. "
             "지금 열려 있는 고객 한 명의 값을 묻는 질문은 여기가 아니다",
             _branch, progress="부점 고객 데이터"),
        # pitch 와의 구분: 질문이 아닌 고객 문제상황으로 후보 선정(화면 ⑥⑦⑧ 과 같은 후보군)
        Tool("playbook", "지금 열려 있는 고객의 **계좌 상태**(문제상황)에 걸린 화법·"
             "예상반론·관리방법론·업무절차 참고자료를 브리핑 화면 ⑥⑦⑧ 과 같은 후보군에서 "
             "돌려준다. **후보를 고르는 것은 고객 상태이고 질문은 그것을 좁히기만 한다** — "
             "이 고객 상태에 안 걸린 자료는 무엇을 물어도 여기서 안 나온다. 「이 고객 상담 "
             "전에 뭘 준비하지」·「이 고객한테 걸린 자료 뭐 있어」처럼 **찾을 대상이 질문에 "
             "특정되지 않은** 턴이 여기다. 고객이 한 말에 대응하는 화법은 여기가 아니라 "
             "pitch 다",
             # 진행 문구는 직원용 — 「상태에 걸린」 같은 코드 용어 금지
             _playbook, progress="이 고객에게 맞는 참고자료"),
        # 재료는 이전 대화 답변 — 화면 AI 문장 수정(correction)과 구분
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

#: 열린 고객이 있어야 성립하는 도구.
_NEEDS_CUSTOMER = frozenset({"briefing", "customer", "history", "transcript", "suitable", "tax_credit",
                             "playbook", "outreach"})

#: 직전 답변이 있어야 성립하는 도구. 없으면 카탈로그 미노출(계획 한 바퀴 낭비 방지).
_NEEDS_ANSWER = frozenset({"last_answer"})


def usable(state: AgentState | None = None) -> list[str]:
    """이 턴에 부를 수 있는 도구 이름. 전제 미충족·이번 턴 고장 도구 제외 — 카탈로그와 재계획이 공유."""
    opened = bool((state or {}).get("customer_id"))
    answered = last_answered((state or {}).get("history")) is not None
    broken = {s.get("tool") for s in ((state or {}).get("steps") or [])
              if s.get("outcome") == "failed"}
    return [t.name for t in TOOLS.values()
            if (opened or t.name not in _NEEDS_CUSTOMER)
            and (answered or t.name not in _NEEDS_ANSWER)
            and t.name not in broken]


def catalog(state: AgentState | None = None) -> str:
    """계획 프롬프트용 도구 목록(usable 만)."""
    return "\n".join(f"- {TOOLS[n].name}: {TOOLS[n].desc}" for n in usable(state))


#: 도구 호출 결과값(steps 의 outcome · 로그 · 점수 공용). MISS(0건)와 FAILED(고장)는 별개.
FOUND, MISS, FAILED = "found", "miss", "failed"

#: 로그용 질의 미리보기 길이 — 직원 발화 전문 미기록.
QUERY_PREVIEW = 40


def _preview(query: str) -> str:
    text = " ".join(query.split())
    return text[:QUERY_PREVIEW] + "…" if len(text) > QUERY_PREVIEW else text


def record(name: str, query: str, outcome: str, *, reason: str = "",
           found: Evidence | None = None, gate: dict | None = None,
           requeried: bool = False) -> None:
    """도구 호출 한 건 기록 — 단계 로그 + Langfuse 점수. FAILED 만 WARNING."""
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
    """도구 실행. 0건이면 직원 원문 질문으로 한 번 재검색, 예외는 ToolFailure(재검색 없음)."""
    tool = TOOLS.get(name)
    if tool is None:
        return None
    if tool.progress:
        # 문구는 도구 선언에서만 — LLM 질의 미노출
        progress.emit(f"{progress.object_of(tool.progress)} 찾고 있어요")
    question = (state.get("question") or "").strip()
    attempts = [query] + ([question] if question and question != query else [])
    # _gate 는 adequacy._adopt 가 채움 — 호출 전에 비워 지난 호출 값 잔류 방지
    state.pop("_gate", None)
    with observability.span(f"tool:{name}", input=query) as sp:
        for i, attempt in enumerate(attempts):
            try:
                found = tool.run(state, attempt)
            except LLMError:
                # LLM 장애는 도구 고장과 별개 — 그대로 전파
                raise
            except Exception as exc:
                # 0건으로 접지 않음 — 접으면 «자료 없음»으로 답해짐
                reason = f"{type(exc).__name__}: {exc}"
                sp.update(output=None, found=False, failed=True)
                record(name, query, FAILED, reason=reason)
                raise ToolFailure(name, reason) from exc
            if found is not None:
                sp.update(output=found["text"], found=True, retried=bool(i))
                record(name, query, FOUND, found=found, gate=state.pop("_gate", None),
                       requeried=bool(i))
                return found
        sp.update(output=None, found=False)
        record(name, query, MISS, gate=state.pop("_gate", None), requeried=len(attempts) > 1)
        return None
