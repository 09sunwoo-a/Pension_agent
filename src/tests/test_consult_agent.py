"""검색 정확도 · 그래프 라우팅 · 계획 루프 테스트 — 진입점.

LLM 을 부르는 자리(understand·계획·문장생성)를 스텁으로 갈아끼우므로 API 키가 없어도 돌아간다.
검사 본문은 `tests/consult/` 아래 주제별 모듈에 있고(그 패키지 머리말에 지도가 있다), 이
파일은 스텁을 설치하고 그것들을 **정해진 순서로** 부른 뒤 잔여물을 지우는 일만 한다.
순서가 뜻이 있다 — 앞 검사가 갈아끼운 것을 뒤 검사가 되돌려 놓는 자리가 있다.

실행:  cd src && python -m tests.test_consult_agent
"""

from __future__ import annotations

from pension_agent import config
from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import tools
from pension_agent.consult_agent.nodes import meta, plan
from pension_agent.consult_agent.evidence import pitch_slots

from tests.consult._common import (  # noqa: A001 — 집계용 print
    CASES, _TALLY, _stdout_print, print, stub_plan_pitch, stub_slots, stub_talk, stub_understand,
)
from tests.consult.routing import (
    check_guard,
    check_intent_routing,
    check_knowledge_intents,
    check_lms_link_parsing,
    check_pitch_stages,
    check_verify_gate,
)
from tests.consult.screens import check_outreach, check_screen_link, check_screen_registry
from tests.consult.material import (
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
from tests.consult.clarify import (
    check_adequacy_and_shape,
    check_answer_parallel,
    check_branch_answer_amount,
    check_clarify_golden,
    check_clarify_settled,
    check_context_and_clarify,
    check_graded_judge,
)
from tests.consult.plan_loop import (
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
from tests.consult.answer import (
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
from tests.consult.index import (
    check_all_kinds_reachable,
    check_fact_in_index,
    check_hier_index,
    check_l0_skip,
    check_tool_axes,
    check_trigger_entrances,
)
from tests.consult.tax_credit import check_tax_credit_calc
from tests.consult.memo import check_memo, check_memo_edit, check_memo_schedule
from tests.consult.meta import (
    check_architecture_doc,
    check_node_label_collision,
    check_rehearsal_expectations,
)

#: main() 시작 시점에 이미 있던 상담이력 파일. 끝에서 이번 실행이 만든 것만 지운다.
_SESSIONS_BEFORE: set = set()


def main() -> int:
    # 정리할 것과 원래 있던 것을 가른다(아래 끝부분).
    global _SESSIONS_BEFORE
    _SESSIONS_BEFORE = set(config.SESSION_DATA_DIR.glob("*.json")) \
        if config.SESSION_DATA_DIR.exists() else set()

    # build_agent() 는 호출 시점에 모듈 전역에서 노드 함수를 찾으므로 치환이 그대로 먹는다
    # 화법 슬롯 분해는 이제 노드가 아니라 화법 도구가 부른다 — 모듈 함수를 갈아끼운다.
    pitch_slots.extract_slots = stub_slots
    G.understand = stub_understand
    G.plan_step = stub_plan_pitch          # 계획은 고정 — CASES 는 카드 채점을 잰다
    plan.generate = stub_talk              # compose 의 화법 생성
    meta.generate = stub_talk              # agent_help 의 능력 안내 작성
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

    # 검사 도중 예외가 나도 정리는 돈다. try/finally 가 없던 동안, 실패한 실행이
    # 남긴 세션 파일(TEST_ACT.json)이 저장소에 그대로 커밋될 뻔했다 — 정리를
    # 성공 경로에만 두면 «정리가 필요한 상황»에서만 정리가 안 된다.
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
        check_rehearsal_expectations()
        check_notice_scope()
        check_guard()
        check_architecture_doc()
        check_node_label_collision()
    finally:
        # 위 테스트들(특히 lms_link)이 상담이력 저장소에 기록을 남기므로 **이번 실행이 만든
        # 것만** 지운다. 예전에는 디렉터리를 통째로 지웠는데, 경로가 옮겨진 뒤로는 존재하지
        # 않는 곳을 지우고 있어서 실제로는 아무것도 정리되지 않았다(루트 CLAUDE.md 규칙 4의
        # 같은 사고 — 경로를 하드코딩하면 한 칸 움직였을 때 조용히 빗나간다).
        for fp in set(config.SESSION_DATA_DIR.glob("*.json")) - _SESSIONS_BEFORE:
            fp.unlink()

    total = _TALLY["ok"] + _TALLY["fail"]
    _stdout_print(f"\n{_TALLY['ok']}/{total} 통과")
    return 0 if not _TALLY["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
