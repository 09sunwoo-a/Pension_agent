"""카드 선택 — LLM 이 버킷 → 카드 2단으로 고르고, 못 고르면 n-gram 으로 물러선다.

`nodes/pitch.py` 안에 있던 llm_select 본체를 여기로 옮겼다. 화법 전용이었을 때는 노드
안에 있어도 됐지만, 도구 루프가 종류별 도구(팩트·절차·세그먼트·방법론·현장팁)를 갖게
되면서 "어떤 종류에서든 카드를 고른다"가 공용 기능이 됐다. 종류는 인자로 받는다.

━━ 왜 2단인가 ━━
카드 429장의 평면 목록은 약 30k 토큰이라 행내 모델(gemma4-31b·dna3.0-35b)의 컨텍스트에
실을 수 없다. 그래서 ① 버킷 카탈로그(수백 토큰)로 주제 묶음을 고르고 ② 그 버킷의 카드만
보여주고 id 를 고르게 한다. 두 호출 모두 2k 토큰 아래다.
단, **종류 전체가 예산에 들어가면 ①을 생략하고 1단으로 돈다**(llm_pick 참고) — 2단은
목적이 아니라 컨텍스트 제약의 결과라서, 제약이 없는 종류에서는 왕복 낭비다.

━━ 안전장치 3겹 ━━
① 후보가 kb 적재분으로 한정된다 — 저작되지 않은 내용은 애초에 후보가 될 수 없다.
② 목록에 없는 버킷 코드는 index_slice 가 무시한다.
③ LLM 이 없는 id 를 지어내도 실재 id 와 대조해 걸러낸다.

━━ n-gram 폴백은 '검색'의 폴백이지 '답변'의 폴백이 아니다 ━━
예전에는 LLM 장애도 삼켜서 빈 결과로 만들었고, 그러면 호출부가 n-gram 으로 카드를 골라
LLM 없이도 재료가 모이는 것처럼 보였다. 그건 §11 이 막으려는 경로다 — 지금 LLM 장애는
`LLMError` 로 그대로 올라가고 턴은 'LLM 연결이 안 되어 있다'로 끝난다. n-gram 이 남아
있는 경우는 **LLM 이 살아서 아무것도 고르지 않았을 때**뿐이다.
"""

from __future__ import annotations

import json
import re

from pension_agent.consult_agent.kb import index_catalog, index_slice, retrieve, whole_index
from pension_agent.consult_agent.prompts import BUCKET_PROMPT, SELECT_PROMPT
from pension_agent.consult_agent.state import KB
from pension_agent.llm import generate

#: LLM 이 고른 카드에 붙이는 점수. n-gram 점수(0~1 근방)와 섞였을 때 앞에 오도록 크게 둔다.
LLM_SCORE = 2.0


def _json_list(text: str) -> list:
    """LLM 응답에서 JSON 배열만 꺼낸다. 못 찾으면 빈 목록(= 고른 것 없음)."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        val = json.loads(m.group())
    except ValueError:
        return []
    return val if isinstance(val, list) else []


def llm_pick(kinds: tuple[str, ...], query: str) -> list[tuple[float, dict]]:
    """버킷 → 카드 2단으로 고른 카드. 고른 것이 없으면 빈 목록, LLM 이 죽으면 `LLMError`.

    **전 카드 인덱스가 예산에 들어가는 종류는 1단으로 돈다**(kb.whole_index). 2단의
    존재 이유는 "카드 전부는 컨텍스트에 못 싣는다"인데, 그 전제가 안 서는 종류에서
    버킷 선택은 후보를 좁히지 않고 순차 LLM 왕복 하나만 쓴다 — 오히려 버킷 오선택으로
    맞는 카드가 후보에서 빠지는 자리다. 판정은 데이터가 하므로 카드가 늘면 저절로
    2단으로 돌아간다.
    """
    card_slice = whole_index(KB, kinds)
    if card_slice is None:
        codes = _json_list(generate(
            BUCKET_PROMPT.format(catalog=index_catalog(KB, kinds), question=query),
            max_tokens=60,
            name="consult.select.bucket",
        ))
        card_slice = index_slice(KB, codes, kinds=kinds)
    # 고른 버킷이 없으면 2차 호출은 낭비다 — 빈 결과로 두고 호출부가 폴백하게 한다.
    picked = _json_list(generate(
        SELECT_PROMPT.format(card_slice=card_slice, question=query),
        max_tokens=200,
        name="consult.select.card",
    )) if card_slice else []

    by_id = {c["id"]: c for c in KB.cards if c["_kind"] in kinds}
    return [(LLM_SCORE, by_id[cid]) for cid in picked if cid in by_id]  # 실재 id 만 통과


def pick(kinds: tuple[str, ...], query: str, *, top_k: int = 3, **scope) -> list[tuple[float, dict]]:
    """카드 선택 — LLM 1차, **LLM 이 아무것도 못 고른 경우에만** n-gram. scope 는 n-gram 에만
    전달된다(customer_type·objection_type·stage 는 화법 카드에만 있는 축이다)."""
    hits = llm_pick(kinds, query)
    if hits:
        return hits[:top_k]
    return retrieve(KB, top_k=top_k, kinds=list(kinds), utterance=query, **scope)
