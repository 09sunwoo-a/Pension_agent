"""situation_slots 노드용(pitch.py 전용) — intent 가 situation/guide 로 확정된 뒤에만

`prompts.py` 에서 갈라낸 모듈 — 공개 표면은 패키지 `__init__` 이다.
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────
# situation_slots 노드용(pitch.py 전용) — intent 가 situation/guide 로 확정된 뒤에만
# 호출된다. 화법 카드 검색(retrieve)에 필요한 고객유형·거절유형·상담단계만 분해한다 —
# 라우팅과 무관한 이 도메인 어휘를 ROUTE_PROMPT 에서 떼어낸 이유가 여기 있다.
# ─────────────────────────────────────────────────────────────

SITUATION_PROMPT = """아래 질문을 화법 카드 검색 조건으로 분해해 JSON만 출력하라. 설명·코드펜스 금지.

{{
  "customer_type": {customer_types} 중 하나 또는 null,
  "objection_type": {objection_types} 중 하나 또는 null,
  "stage": {stages} 중 하나 또는 null
}}

목록에 없는 값은 만들어내지 말고 null 을 넣어라.
objection_type 은 고객이 실제로 그 거절·우려를 말이나 반응으로 드러낸 경우에만 채운다.
단지 주제어(예: "연금 개시", "수수료")가 등장했다는 이유로 비슷한 라벨을 고르지 말고,
확실치 않으면 null 로 둬라 — 틀린 objection_type 은 엉뚱한 화법을 부른다.
customer_type·stage 도 질문에서 분명히 드러날 때만 채우고, 아니면 null 로 둔다.
intent 가 "guide"(직원 업무 절차 질문)이면 customer_type·objection_type 은 추정하지 말고
null 로 둬라.
이전 대화가 있으면 참고하되, 이번 질문에서 언급하지 않은 항목은 이전 값을 이어받아도 된다.
다만 이번 질문이 다른 고객유형·상황을 가리키면 이전 값 대신 새로 판단하라.
{history_block}
intent: {intent}
utterance: {utterance}
이번 질문: {question}"""
