"""카드 선택 — LLM 버킷 → 카드 2단 선택, 아무것도 못 고르면 n-gram 폴백.

전 카드가 컨텍스트 예산 안이면 1단. LLM 장애는 LLMError 로 전파(n-gram 대체 금지).
"""

from __future__ import annotations


from pension_agent.tools.kb_search.kb_index import index_catalog, index_slice, whole_index
from pension_agent.knowledge.kb import retrieve
from pension_agent.prompts import BUCKET_PROMPT, SELECT_PROMPT
from pension_agent.state import KB
from pension_agent.common.llm import generate, json_list

#: LLM 선택 카드 점수 — n-gram 점수(0~1)보다 앞에 오도록.
LLM_SCORE = 2.0


def llm_pick(kinds: tuple[str, ...], query: str) -> list[tuple[float, dict]]:
    """버킷 → 카드로 고른 카드(예산 안이면 1단). 없으면 빈 목록, LLM 장애는 LLMError."""
    card_slice = whole_index(KB, kinds)
    if card_slice is None:
        codes = json_list(generate(
            BUCKET_PROMPT.format(catalog=index_catalog(KB, kinds), question=query),
            max_tokens=60,
            name="consult.select.bucket",
        ))
        card_slice = index_slice(KB, codes, kinds=kinds)
    picked = json_list(generate(
        SELECT_PROMPT.format(card_slice=card_slice, question=query),
        max_tokens=200,
        name="consult.select.card",
    )) if card_slice else []

    by_id = {c["id"]: c for c in KB.cards if c["_kind"] in kinds}
    return [(LLM_SCORE, by_id[cid]) for cid in picked if cid in by_id]  # 실재 id 만 통과


def pick(kinds: tuple[str, ...], query: str, *, top_k: int = 3, **scope) -> list[tuple[float, dict]]:
    """LLM 선택, 0건일 때만 n-gram. scope(화법 슬롯)는 n-gram 에만 전달."""
    hits = llm_pick(kinds, query)
    if hits:
        return hits[:top_k]
    return retrieve(KB, top_k=top_k, kinds=list(kinds), utterance=query, **scope)
