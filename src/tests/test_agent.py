"""검색 정확도 · 그래프 라우팅 · 계획 루프 테스트 진입점. LLM 자리는 스텁(API 키 불필요).

`tests/agent/` 검사를 정해진 순서로 호출 후 잔여물 정리(순서 의존).
실행:  cd src && python -m tests.test_agent
"""

from __future__ import annotations

from pension_agent.common import config
from pension_agent import graph as G
from pension_agent import tools
from pension_agent.nodes import meta, plan
from pension_agent.tools.kb_search import pitch_slots

from tests.agent._common import (  # noqa: A001 — 집계용 print
    CASES, _TALLY, _stdout_print, print, stub_plan_pitch, stub_slots, stub_talk, stub_understand,
)
from tests.agent.routing import (
    check_guard,
    check_intent_routing,
    check_knowledge_intents,
    check_lms_link_parsing,
    check_pitch_stages,
    check_verify_gate,
)
from tests.agent.screens import check_outreach, check_screen_link, check_screen_registry
from tests.agent.material import (
    check_account_state,
    check_briefing_shared,
    check_caution_roles,
    check_customer_material,
    check_history_material,
    check_history_selection,
    check_market_material,
    check_material_marks,
    check_no_card_ids_in_material,
    check_origin,
    check_playbook_material,
    check_relations,
    check_today_material,
)
from tests.agent.clarify import (
    check_adequacy_and_shape,
    check_answer_parallel,
    check_branch_answer_amount,
    check_clarify_golden,
    check_clarify_settled,
    check_context_and_clarify,
    check_graded_judge,
)
from tests.agent.plan_loop import (
    check_atomic_spans,
    check_compose_retry,
    check_llm_down,
    check_miss_recovery,
    check_no_material_tone,
    check_notice_scope,
    check_order_flipped,
    check_plan_failure,
    check_progress,
    check_replan_on_empty,
    check_tone_and_marks,
    check_tool_loop,
    check_turn_cost,
)
from tests.agent.answer import (
    check_followups,
    check_labeled_pairs,
    check_last_answer,
    check_no_repeat,
    check_product_advice,
    check_prompt_is_quotable,
    check_question_echo,
    check_suitable_shape,
    check_table_row_names,
)
from tests.agent.index import (
    check_all_kinds_reachable,
    check_fact_in_index,
    check_hier_index,
    check_l0_skip,
    check_procedure_axes,
    check_tool_axes,
    check_trigger_entrances,
)
from tests.agent.tax_credit import check_tax_credit_calc
from tests.agent.turn_trace import check_turn_trace
from tests.agent.memo import check_memo, check_memo_edit, check_memo_schedule, check_memo_by_name, check_memo_edit_material, check_memo_also, check_memo_pick_partial, check_memo_echo_and_address, check_memo_title
from tests.agent.meta import (
    check_architecture_doc,
    check_node_label_collision,
    check_rehearsal_expectations,
)

#: main() 시작 전 상담이력 파일 — 이번 실행 생성분만 삭제용
_SESSIONS_BEFORE: set = set()


def main() -> int:
    global _SESSIONS_BEFORE
    _SESSIONS_BEFORE = set(config.SESSION_DATA_DIR.glob("*.json")) \
        if config.SESSION_DATA_DIR.exists() else set()

    # build_agent() 는 호출 시점에 모듈 전역에서 노드 함수 조회 — 치환 유효
    pitch_slots.extract_slots = stub_slots
    G.understand = stub_understand
    G.plan_step = stub_plan_pitch          # 계획 고정 — CASES 는 카드 채점 측정
    plan.generate = stub_talk              # compose 화법 생성
    meta.generate = stub_talk              # agent_help 능력 안내
    tools.fits_question = lambda q, h, kind="", history=None, query=None, sink=None: h
    agent = G.build_agent()

    for question, expected in CASES:
        out = agent.invoke({"question": question})
        if expected == "AGENT_HELP":
            ok = out.get("intent") == "agent_help" and bool(out.get("answer")) and not out.get("sources")
            found = "agent_help 노드 응답" if ok else f"intent={out.get('intent')!r}"
        else:
            top = out["sources"][0]["id"] if out["sources"] else "FALLBACK"
            ok = top == expected
            found = ", ".join(f"{s['id'].split('.')[-1]}({s['score']})" for s in out["sources"]) or "→ FALLBACK"
        print(f"{'✓' if ok else '✗'} {question[:32]:<34} {found}")

    # 예외 시에도 정리 — 실패 실행의 세션 파일 커밋 방지
    try:
        check_pitch_stages()
        check_verify_gate()
        check_intent_routing()
        check_lms_link_parsing()
        check_knowledge_intents()
        check_screen_link()
        check_briefing_shared()
        check_customer_material()
        check_playbook_material()
        check_no_card_ids_in_material()
        check_context_and_clarify()
        check_adequacy_and_shape()
        check_material_marks()
        check_relations()
        check_turn_cost()
        check_miss_recovery()
        check_no_material_tone()
        check_clarify_golden()
        check_clarify_settled()
        check_answer_parallel()
        check_replan_on_empty()
        check_outreach()
        check_prompt_is_quotable()
        check_branch_answer_amount()
        check_screen_registry()
        check_market_material()
        check_product_advice()
        check_caution_roles()
        check_question_echo()
        check_table_row_names()
        check_no_repeat()
        check_last_answer()
        check_suitable_shape()
        check_history_material()
        check_memo()
        check_memo_edit()
        check_memo_schedule()
        check_memo_by_name()
        check_memo_edit_material()
        check_memo_also()
        check_memo_pick_partial()
        check_memo_echo_and_address()
        check_memo_title()
        check_today_material()
        check_account_state()
        check_labeled_pairs()
        check_tax_credit_calc()
        check_fact_in_index()
        check_history_selection()
        check_followups()
        check_hier_index()
        check_l0_skip()
        check_progress()
        check_order_flipped()
        check_tool_loop()
        check_all_kinds_reachable()
        check_trigger_entrances()
        check_atomic_spans()
        check_tone_and_marks()
        check_origin()
        check_plan_failure()
        check_llm_down()
        check_compose_retry()
        check_graded_judge()
        check_tool_axes()
        check_procedure_axes()
        check_rehearsal_expectations()
        check_notice_scope()
        check_guard()
        check_architecture_doc()
        check_turn_trace()
        check_node_label_collision()
    finally:
        # 이번 실행이 만든 상담이력만 삭제(경로는 config 기준)
        for fp in set(config.SESSION_DATA_DIR.glob("*.json")) - _SESSIONS_BEFORE:
            fp.unlink()

    total = _TALLY["ok"] + _TALLY["fail"]
    _stdout_print(f"\n{_TALLY['ok']}/{total} 통과")
    return 0 if not _TALLY["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
