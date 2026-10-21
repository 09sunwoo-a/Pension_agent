"""답변 속 고객 발송 문구(`(광고)` 로 시작하는 인용) 추출 — `answer.messages` 재료.

화법 인용과 구분용. `copy` 는 공백만 다르면 브리핑 원본, 아니면 본문 그대로. dummy 자산 문구 미포함.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pension_agent.tools.customer_solution.support.assets import AD_PREFIX

from pension_agent.actions.actions import _match_asset

#: 큰따옴표 인용(곧은·둥근). 여러 줄 가능.
_QUOTE = re.compile(r"“([^“”]+?)”|\"([^\"]+?)\"", re.S)

LABEL = "고객 발송 문구"


def _squash(text: str) -> str:
    return " ".join(text.split())


def canonical(evidence: Iterable[dict]) -> list[str]:
    """이번 턴 원장 outreach 재료의 발송 문구 원본(`meta.messages`)."""
    out: list[str] = []
    for ev in evidence or []:
        for text in (ev.get("meta") or {}).get("messages") or []:
            if text and text not in out:
                out.append(text)
    return out


def messages_in(answer: str, originals: Iterable[str] = ()) -> list[dict]:
    """본문의 발송 문구 인용 — `{"kind", "label", "text", "copy"}` 목록."""
    by_squash = {_squash(o): o for o in originals}
    out: list[dict] = []
    for m in _QUOTE.finditer(answer or ""):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        if not inner.strip().startswith(AD_PREFIX):
            continue
        asset = _match_asset(inner)
        # dummy 제외 — 복사 블록이 발송 화면 게이트 우회로가 됨
        if asset is not None and asset.get("dummy"):
            continue
        if any(item["text"] == inner for item in out):
            continue
        out.append({"kind": "lms", "label": LABEL, "text": inner,
                    "copy": by_squash.get(_squash(inner), inner.strip())})
    return out
