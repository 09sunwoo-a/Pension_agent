"""추천 질문 칩 — 고객 화면 진입 시 칩과 답변 끝 추천질문. 문구는 전부 코드 조립.

조건 성립 시에만 표시(항상 뜨는 칩은 무시됨). LLM 작성 금지 — 기록에 없는 내용이 원장 출발점이 됨.
"""

from __future__ import annotations

import re
from datetime import date


def history_chips(customer_id: str | None) -> list[str]:
    """최신 상담 기록(record) 기반 칩. 에이전트 대화 세션은 미포함 — «지난 상담» 오독 방지."""
    if not customer_id:
        return []
    from pension_agent.common import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return []

    latest: date | None = None
    for s in sessions:
        if not any(t.get("role") == "record" for t in (s.get("turns") or [])):
            continue
        try:
            d = date.fromisoformat((s.get("started_at") or "")[:10])
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    if latest is None:
        return []

    # 경과일 기준은 원장 기준일이 아닌 오늘(브리핑 경과일과 같은 축)
    from pension_agent.common.clock import today  # noqa: PLC0415
    elapsed = (today() - latest).days
    when = f"{latest.month}/{latest.day}"
    return [
        f"지난 상담({when} · {elapsed}일 전)에서 무슨 얘기 했지?",
        "지난 상담 내용 참고해서 오늘 뭐라고 말하면 좋을까?",
    ]


def outreach_chips(customer_id: str | None) -> list[str]:
    """고객 상태에 맞는 세미나·이벤트가 열려 있으면 칩. 관련도 0 폴백은 미포함."""
    if not customer_id:
        return []
    try:
        from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
        from pension_agent.tools.customer_solution import situations as strategy_situations  # noqa: PLC0415
        from pension_agent.tools.customer_solution import support  # noqa: PLC0415
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return []
        rows = support.relevant_outreach(strategy_situations.problem_situations(profile))
    except Exception:
        return []
    if not rows:
        return []
    kinds = [k for k in ("이벤트", "세미나")
             if any(r["content_type"] == k for r in rows)]
    # 건수 미포함 — 칩은 직원의 질문이 되므로 직원이 모르는 건수 단정 금지
    return [f"이 고객한테 안내할 만한 {'나 '.join(kinds)} 있어?"]


# 답변 끝 추천질문 — 후보마다 답할 재료 존재를 코드가 먼저 확인(_has_material)
# 검색 히트는 필요조건일 뿐 — 클릭 후 적합성 게이트(LLM)에서 걸러질 수 있음

#: 추천질문 수 상한(억지로 채우지 않음, 0개 가능).
MAX_FOLLOWUPS = 3

#: `{topic}` 슬롯 제목 길이 상한. 초과 시 슬롯 없는 변형 사용
TOPIC_MAX = 20

#: 이번 턴 재료 → [(문구 변형, 답할 도구, 재료 확인어)]. `{topic}` 은 근거 카드 제목.
#: 확인어 빈 칸 = 카드 제목으로 확인. 자기완결형 질문만 확인어 사용 — 일반어면 항상 통과
_NEXT: dict[str, tuple[tuple[tuple[str, ...], str, str], ...]] = {
    "fact": ((("「{topic}」 이 내용, 고객에게는 뭐라고 설명하면 좋을까?",
               "이 수치로 고객을 설득하려면 뭐라고 말하지?",
               "고객이 이 부분을 반문하면 어떻게 답하지?"), "pitch", ""),
             (("이건 실제로 어떻게 처리해?",
               "「{topic}」 관련 업무 처리 순서가 어떻게 돼?",
               "이거 처리할 때 걸리는 주의사항이 있어?"), "procedure", ""),
             (("고객이 앱에서 직접 할 수 있어?",
               "이건 비대면으로도 되나?",
               "고객이 스타뱅킹에서 직접 하는 경로가 있어?"), "channel", "")),
    # pitch → pitch 는 «이미 쓴 재료» 제외의 예외(반론 후속은 같은 재료의 다른 카드)
    "pitch": ((("고객이 그래도 망설이면 뭐라고 답하지?",
                "고객이 싫다고 하면 어떻게 대응하지?",
                "고객이 타행이랑 비교하면 뭐라고 하지?"), "pitch", ""),
              (("여기서 근거로 댈 수 있는 수치가 뭐가 있어?",
                "「{topic}」 관련 한도나 요건이 어떻게 돼?",
                "이 얘기에 붙일 확정된 수치가 있어?"), "fact", ""),
              (("고객이 하겠다고 하면 어떻게 처리해?",
                "이거 진행하려면 절차가 어떻게 돼?",
                "실제 처리 순서를 알려줘"), "procedure", "")),
    "procedure": ((("고객이 앱에서 직접 하려면 어떻게 안내하지?",
                    "이건 고객이 비대면으로도 할 수 있어?",
                    "고객이 스타뱅킹에서 직접 하는 경로가 있어?"), "channel", ""),
                  (("이 업무는 단말 어느 화면에서 처리해?",
                    "관련 화면번호가 뭐야?",
                    "이 처리는 어느 화면에서 조회해?"), "screen", ""),
                  (("여기 걸리는 한도나 요건이 뭐야?",
                    "「{topic}」 관련 확정된 수치가 있어?",
                    "이 절차에 붙는 기준값이 뭐가 있어?"), "fact", "")),
    "screen": ((("이 화면에서 처리하는 절차가 어떻게 돼?",
                 "이 업무 처리 순서를 알려줘",
                 "이 화면 쓰기 전에 확인할 게 있어?"), "procedure", ""),
               (("고객이 직접 하는 경로도 있어?",
                 "이건 고객이 앱에서도 할 수 있나?",
                 "비대면 채널로도 되는 업무야?"), "channel", "")),
    "channel": ((("직원이 단말에서 처리하려면 어느 화면이야?",
                  "이건 창구에서 처리하면 화면번호가 뭐야?",
                  "직원이 대신 처리할 때는 어느 화면을 봐?"), "screen", ""),
                (("창구에서 처리하는 절차는 어떻게 돼?",
                  "직원이 처리할 때 순서가 어떻게 되지?",
                  "이 업무 처리 순서를 알려줘"), "procedure", "")),
    "segment": ((("「{topic}」 이 고객군한테는 뭐라고 말을 꺼내지?",
                  "이런 고객에게는 어떻게 접근하면 좋을까?",
                  "이 고객군 상담은 어떻게 시작하지?"), "pitch", ""),
                (("이런 고객은 어떤 기준으로 관리해?",
                  "「{topic}」 이 고객군은 뭐부터 챙겨야 해?",
                  "이 고객군 관리 방법론이 뭐야?"), "method", ""),
                (("이 고객도 여기 해당해?",
                  "지금 열려 있는 고객은 어느 요건에 걸렸어?",
                  "이 고객이 이 고객군에 들어가?"), "customer", "")),
    "method": ((("이 기준을 고객한테는 어떻게 설명하지?",
                 "이 판단을 고객에게 말할 때 뭐라고 하지?",
                 "고객이 왜 그러냐고 물으면 뭐라고 답하지?"), "pitch", ""),
               (("이 기준에 걸리는 고객군이 뭐야?",
                 "「{topic}」 이건 어떤 고객을 대상으로 해?",
                 "이 관리 대상은 어떻게 선정해?"), "segment", "")),
    "fieldtip": ((("이 상황에서 고객에게 할 말은 뭐가 좋을까?",
                   "이럴 때 어떻게 말을 꺼내지?",
                   "이 경우 고객 응대를 어떻게 하지?"), "pitch", ""),
                 (("이건 실제로 어떻게 처리해?",
                   "관련 업무 절차가 어떻게 돼?",
                   "처리 순서를 알려줘"), "procedure", "")),
    "market": ((("지금 우리 운용 상품 라인업은 뭐가 있어?",
                 "이달의 추천 상품은 뭐야?",
                 "디폴트옵션 포트폴리오 구성은 어떻게 돼?"), "lineup", "추천펀드 디폴트옵션"),
               (("이 시장 상황을 고객에게 어떻게 설명하지?",
                 "고객이 시장 불안해하면 뭐라고 말하지?",
                 "요즘 시황을 고객에게 뭐라고 전하지?"), "pitch", ""),
               (("이 고객한테는 이 중에 뭐가 가능해?",
                 "지금 이 고객에게 안내할 수 있는 범위가 어디까지야?",
                 "이 고객 성향으로 가능한 상품이 뭐야?"), "suitable", "")),
    "lineup": ((("요즘 시장 상황은 어때?",
                 "지금 금리·환율 흐름이 어떻게 돼?",
                 "최근 투자전략 자료에 뭐라고 나와 있어?"), "market", "시황 금리 환율"),
               (("「{topic}」 이걸 고객에게 어떻게 안내하지?",
                 "이 상품을 고객에게 어떻게 설명하지?",
                 "고객이 상품 물어보면 뭐라고 말하지?"), "pitch", ""),
               (("이 고객한테는 이 중에 뭐가 가능해?",
                 "이 고객 성향으로 안내할 수 있는 게 뭐야?",
                 "지금 고객에게 가능한 범위가 어디까지야?"), "suitable", "")),
    "suitable": ((("이 범위를 고객에게 어떻게 안내하면 좋을까?",
                   "적합성 범위를 고객에게 뭐라고 설명하지?",
                   "고객이 다른 상품 원하면 뭐라고 답하지?"), "pitch", ""),
                 (("우리 라인업 전체는 뭐가 있어?",
                   "이달의 추천 상품은 뭐야?",
                   "디폴트옵션 포트폴리오는 어떻게 구성돼 있어?"), "lineup", "추천펀드 디폴트옵션"),
                 (("이 고객 현황이 어떻게 돼?",
                   "이 고객 평가금액이랑 보유 상품이 뭐야?",
                   "이 고객 왜 관리 대상이야?"), "customer", ""),
                 (("이 고객한테 안내할 만한 세미나나 이벤트 있어?",
                   "이 고객에게 보낼 만한 안내 콘텐츠가 뭐가 있어?",
                   "이 고객한테 지금 안내할 이벤트가 있어?"), "outreach", "")),
    "customer": ((("이 고객한테 안내할 수 있는 상품 범위는 뭐야?",
                   "이 고객 투자성향으로 어디까지 안내할 수 있어?",
                   "이 고객에게 가능한 상품이 뭐가 있어?"), "suitable", ""),
                 # 고객 상태에 맞는 콘텐츠가 열려 있을 때만(관련도 0 폴백 제외)
                 (("이 고객한테 안내할 만한 세미나나 이벤트 있어?",
                   "이 고객에게 보낼 만한 안내 콘텐츠가 뭐가 있어?",
                   "이 고객한테 지금 안내할 이벤트가 있어?"), "outreach", ""),
                 (("이 고객과 지난 상담에서 무슨 얘기 했지?",
                   "이 고객 상담 이력 있어?",
                   "이 고객과 전에 나눈 얘기가 뭐야?"), "history", ""),
                 (("이 고객 왜 관리 대상이야?",
                   "이 고객이 걸린 고객군이 뭐야?",
                   "이 고객군은 왜 관리해야 해?"), "segment", ""),
                 (("이 고객에게 어떻게 말을 꺼내지?",
                   "이 고객한테 뭐라고 안내하면 좋을까?",
                   "이 고객 상담을 어떻게 시작하지?"), "pitch", "")),
    "history": ((("지난 상담 내용 참고해서 오늘 뭐라고 말하면 좋을까?",
                  "지난번 얘기를 이어서 어떻게 말을 꺼내지?",
                  "그때 얘기를 다시 꺼내려면 뭐라고 하지?"), "pitch", ""),
                (("이 고객한테 안내할 만한 세미나나 이벤트 있어?",
                  "이어서 안내할 만한 콘텐츠가 뭐가 있어?",
                  "이 고객에게 보낼 만한 이벤트가 있어?"), "outreach", ""),
                (("이 고객 지금 현황은 어때?",
                  "그 사이에 이 고객 상황이 어떻게 바뀌었어?",
                  "이 고객 지금 평가금액이 얼마야?"), "customer", "")),
    # 발송 칩 없음 — 발송 화면 연계는 nodes/act.py::_propose_lms 담당
    "outreach": ((("이 콘텐츠를 고객에게 어떻게 안내하지?",
                   "고객에게 이 세미나를 뭐라고 소개하지?",
                   "고객이 관심 없다고 하면 뭐라고 답하지?"), "pitch", ""),
                 (("이 고객한테 안내할 수 있는 상품 범위는 뭐야?",
                   "이 고객 성향으로 안내할 수 있는 게 뭐야?",
                   "이 고객에게 가능한 상품이 뭐가 있어?"), "suitable", "")),
    # date 미포함(이어질 질문 없음)
}

# 전체 브리핑 뒤에도 같은 고객의 상담·적합성·상담이력으로 이어짐.
_NEXT["briefing"] = _NEXT["customer"]

#: 같은 도구로 이끄는 칩 수 상한.
MAX_PER_LEAD = 2

#: n-gram 검색(retrieve)으로 존재 확인하는 재료 — LLM 미호출.
_KB_KINDS = frozenset({"pitch", "procedure", "screen", "channel", "segment",
                       "method", "fieldtip", "market", "lineup"})

#: 도구를 직접 호출해 확인하는 재료(`_adopt` 미경유라 클릭 경로와 동일 판정).
_LEDGER_TOOLS = frozenset({"history", "suitable"})


#: 질문 비교 시 무시하는 공백·문장부호.
_TRIVIAL = re.compile(r"[\s?!.,·]+")


def _norm(question: str | None) -> str:
    """질문 비교용 정규형."""
    return _TRIVIAL.sub("", question or "")


#: 어미로 보고 무시하는 끝 글자 수 — 어미만 다른 질문을 같은 질문으로 판정
_ENDING = 3

#: 어미 무시 판정의 최소 공통 앞부분 길이 — 짧은 질문 오판정 방지
_SAME_MIN = 8


def _same_question(a: str, b: str) -> bool:
    """정규형 둘의 동일 질문 여부(어미 차이 무시)."""
    if a == b:
        return True
    short = min(len(a), len(b))
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common >= _SAME_MIN and common >= short - _ENDING


def _already(question: str, pool: set[str]) -> bool:
    """pool 에 같은 질문 존재 여부."""
    return any(_same_question(question, p) for p in pool)


def _topic(found: dict) -> str:
    """`{topic}` 슬롯용 근거 카드 제목. 없으면 ""."""
    for source in found.get("sources") or []:
        title = (source.get("title") or "").strip()
        if title and len(title) <= TOPIC_MAX:
            return title
    return ""


def _phrase(variants: tuple[str, ...], topic: str, turn: int) -> str:
    """턴 수로 변형 회전(결정론). 제목 없으면 슬롯 든 변형은 건너뜀."""
    for i in range(len(variants)):
        text = variants[(turn + i) % len(variants)]
        if "{topic}" not in text:
            return text
        if topic:
            return text.format(topic=topic)
    return ""


def _has_material(lead: str, probes: tuple[str, ...], state: dict) -> bool:
    """답할 재료 존재 여부(LLM 미호출). `probes` 중 하나라도 걸리면 있음 — 문장만으론 주제어가 묻힘."""
    from pension_agent.knowledge import kb as KBMOD  # noqa: PLC0415
    from pension_agent import tools  # noqa: PLC0415
    from pension_agent.tools.kb_search import facts_qa  # noqa: PLC0415
    from pension_agent.state import KB  # noqa: PLC0415

    try:
        if lead == "fact":
            return any(facts_qa.search(q) for q in probes)
        if lead in _KB_KINDS:
            return any(KBMOD.retrieve(KB, top_k=1, kinds=[lead], utterance=q) for q in probes)
        if lead == "customer":
            from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
            return strategy_customer.get_profile(state.get("customer_id") or "") is not None
        if lead == "outreach":
            # 관련도 0 폴백 제외 — 세면 모든 고객에게 칩이 뜸
            from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
            from pension_agent.tools.customer_solution import situations as strategy_situations  # noqa: PLC0415
            from pension_agent.tools.customer_solution import support  # noqa: PLC0415
            profile = strategy_customer.get_profile(state.get("customer_id") or "")
            return profile is not None and bool(
                support.relevant_outreach(strategy_situations.problem_situations(profile)))
        if lead in _LEDGER_TOOLS:
            return any(tools.TOOLS[lead].run(state, q) is not None for q in probes)
    except Exception:
        return False
    return False


def _material_followups(out: dict) -> list[str]:
    """근거 도구별 추천질문. 고객 목록의 선택 안내는 followup_questions 에서 처리."""
    # 미표시: 되묻기·확인 대기·LLM 실패(정상 답변처럼 보임)·근거 0건 턴
    if out.get("clarify") or out.get("pending_action") or out.get("llm_error"):
        return []
    evidence = list(out.get("evidence") or [])
    if not evidence:
        return []

    from pension_agent import tools  # noqa: PLC0415

    state = {"customer_id": out.get("customer_id"), "question": out.get("question") or ""}
    # 고객 화면 없으면 고객 전제 도구 칩 제외
    usable = set(tools.usable(state))
    used = {e["tool"] for e in evidence}
    history = out.get("history") or []
    # 이번 질문 포함 — history 에는 invoke 뒤에야 붙어, 빠지면 방금 질문이 재추천됨
    asked = {_norm(t.get("question")) for t in history} | {_norm(out.get("question"))}
    asked.discard("")

    picked: list[str] = []
    seen: set[str] = set()          # 이미 고른 문구(정규형)
    per_lead: dict[str, int] = {}   # 도구별 개수
    for found in evidence:
        if len(picked) >= MAX_FOLLOWUPS:
            break
        # 재료 도구를 이번 턴 못 쓰면 후속질문도 제외
        if found["tool"] not in usable:
            continue
        topic = _topic(found)
        for variants, lead, probe in _NEXT.get(found["tool"], ()):
            if len(picked) >= MAX_FOLLOWUPS:
                break
            if per_lead.get(lead, 0) >= MAX_PER_LEAD or lead not in usable:
                continue
            # 이미 쓴 재료 제외(pitch → pitch 예외)
            if lead in used and not (lead == "pitch" and found["tool"] == "pitch"):
                continue
            # outreach 재료가 폴백뿐(meta.lms 빈 값)이면 안내 화법 칩 제외
            if found["tool"] == "outreach" and lead == "pitch" \
                    and not (found.get("meta") or {}).get("lms"):
                continue
            question = _phrase(variants, topic, len(history))
            if not question or _already(_norm(question), asked | seen):
                continue
            # 짧은 말 먼저 — n-gram 유사도는 긴 문장에서 희석되어 있는 자료도 0건
            probes = tuple(dict.fromkeys(x for x in (topic, probe, question) if x))
            if not _has_material(lead, probes, state):
                continue
            picked.append(question)
            seen.add(_norm(question))
            per_lead[lead] = per_lead.get(lead, 0) + 1
    return picked


# 큰 목록에서도 추천을 위해 전체 명부를 재조회하지 않음.
MAX_CUSTOMER_PROBES = 10


def _customer_followups(out: dict) -> list[str] | None:
    """목록 → 선택 확인 → 선택 고객의 질문. None 이면 기존 근거별 추천으로."""
    from pension_agent.tools import customer_selection as selection  # noqa: PLC0415
    from pension_agent.tools.branch_briefing import run  # noqa: PLC0415
    from pension_agent.tools.customer_solution import customer as C  # noqa: PLC0415

    saved = out.get("customer_context") or {}
    if not saved:
        return None
    employee = saved.get("employee_id")
    case = out.get("selection_case")
    pending = saved.get("pending") or {}
    lists = saved.get("lists") or []
    listed = selection.get_list(saved, pending.get("list", saved.get("active")))

    def resolves(question: str, accepted: set[str], expected_ref: str | None = None) -> bool:
        try:
            resolved = selection.resolve(question, saved, employee)
            return (resolved.case in accepted and (expected_ref is None
                    or (resolved.context.get("selected") or {}).get("ref") == expected_ref))
        except run.DBUnavailable:
            return False

    if pending:
        # 실제 후보의 이름·구분 정보를 인용. 번호를 외울 필요 없이 원래 질문으로 연결.
        entries = (listed or {}).get("entries") or []
        candidates = [f"{reference}으로 확인해줘" for entry in entries
                      if entry["number"] in pending.get("numbers", [])
                      and (reference := selection.name_reference(entry, entries))]
        return [q for q in candidates[:MAX_CUSTOMER_PROBES]
                if resolves(q, {"selected", "detail_unavailable"})][:MAX_FOLLOWUPS]
    if out.get("clarify"):
        return []
    if case in {"db_unavailable", "screen_conflict"}:
        return []

    if case in {"ambiguous_list", "multiple_lists", "list_missing"}:
        candidates = [f"목록 {s['number']} 다시 보여줘" for s in lists]
        available = [q for q in candidates if resolves(q, {"list_replay"})]
        if available:
            return available[:MAX_FOLLOWUPS]

    replay = "목록 다시 보여줘"
    valid_list = (case not in {"changed", "no_identifier"} and listed is not None
                  and resolves(replay, {"list_replay"}))
    selected = saved.get("selected")
    profile = C.get_profile(out.get("customer_id") or "")
    if selected and case == "detail_unavailable":
        return []
    if selected and profile and case == "selected":
        selected_list = selection.get_list(saved, selected["list"]) or {}
        entries = selected_list.get("entries") or []
        entry = next((e for e in entries if e.get("ref") == selected["ref"]), {})
        reference = selection.name_reference(entry, entries) or "이 고객"

        def normalized(question: str) -> str:
            question = re.sub(r"^목록\s*\d+의\s*", "", question)
            return _norm(question.replace(reference, "이 고객").replace(f"{profile.nm} 고객", "이 고객"))

        # 같은 문구라도 다른 고객에게는 새 질문. 현재 고객에게 이미 물은 질문만 제외.
        history = [t for t in (out.get("history") or [])
                   if ((t.get("customer_context") or {}).get("selected") or {}).get("ref") == selected["ref"]]
        asked = [t.get("question") or "" for t in history] + [out.get("question") or ""]
        candidates: list[str] = []
        used = {e["tool"] for e in out.get("evidence") or []}
        if "briefing" not in used:
            if profile.maturities and not any("만기" in q for q in asked):
                candidates.append("이 고객의 만기 상품을 확인해줘")
            if profile.room > 0 and not any("공제" in q for q in asked):
                candidates.append("이 고객의 세액공제 잔여한도를 알려줘")
            if profile.holdings and not any("보유" in q or "수익률" in q for q in asked):
                candidates.append("이 고객의 보유상품과 수익률을 알려줘")
        candidates += _material_followups({**out, "history": history})
        seen = {normalized(q) for q in asked}
        picked = []
        for q in candidates:
            named = re.sub(r"이 고객(?!군)", lambda _: reference, q)
            if (selected["list"] != selection.DIRECTORY and selected["list"] != saved.get("active")
                    and reference != "이 고객"):
                # 이전 목록의 고객을 골랐으면 최신 목록의 동명이인으로 바뀌지 않게 목록도 명시.
                named = (f"목록 {selected['list']}의 {named}" if reference in named
                         else f"목록 {selected['list']}의 {reference}에 대해, {named}")
            if not _already(normalized(named), seen) and resolves(named, {"selected"}, selected["ref"]):
                picked.append(named)
                seen.add(normalized(named))
        return picked[:MAX_FOLLOWUPS]

    if valid_list and listed["entries"]:
        # 실제 목록의 이름을 인용하고, 상세 원장 연결까지 확인한 질문만 추천.
        asked = {_norm(t.get("question")) for t in out.get("history") or []
                 if (t.get("customer_context") or {}).get("active") == listed["number"]}
        asked.add(_norm(out.get("question")))
        candidates = []
        for entry in listed["entries"][:MAX_CUSTOMER_PROBES]:
            reference = selection.name_reference(entry, listed["entries"])
            if not reference:
                continue
            q = f"{reference}을 자세히 알려줘"
            if not _already(_norm(q), asked) and resolves(q, {"selected", "detail_unavailable"}, entry.get("ref")):
                candidates.append(q)
            if len(candidates) >= 2:
                break
        if case not in (None, "none", "list_replay") and not _already(_norm(replay), asked) and not any(
                e.get("meta", {}).get("customer_list") for e in out.get("evidence") or []):
            candidates.append(replay)
        return candidates[:MAX_FOLLOWUPS]

    # 만료된 목록은 안전하게 재실행 가능한 원래 질문만 제안. 부점 변경 시 이전 조건을 재사용하지 않음.
    if lists or case not in (None, "none", "selected"):
        try:
            if not run.my_branch(employee):
                return []
        except run.DBUnavailable:
            return []
        query = (listed or {}).get("query") or ""
        if case in {"expired", "changed", "no_identifier"} and query and not re.search(
                r"목록|이\s*고객|그\s*고객|\d+\s*번|그\s*중|이\s*중|처음|아까", query):
            return [query]
        q = "오늘 중점적으로 관리할 고객을 보여줘"
        return [] if _same_question(_norm(q), _norm(out.get("question"))) else [q]
    return None


def followup_questions(out: dict) -> list[str]:
    """현재 목록·선택 상태를 우선 반영한 답변 끝 추천질문. LLM 미호출."""
    if out.get("pending_action") or out.get("llm_error"):
        return []
    try:
        contextual = _customer_followups(out)
    except Exception:
        # 추천 검증 장애가 이미 완성한 답변을 실패로 바꾸지 않음.
        return []
    return contextual if contextual is not None else _material_followups(out)
