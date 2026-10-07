"""답변 본문 속 «고객에게 보낼 문자» — 화법 대사와 갈라 프론트에 알린다.

프론트는 답변의 큰따옴표 인용을 전부 «고객에게 이렇게 말씀해 보세요»(화법) 블록으로 그린다
(prompts/compose.py 6번 — 대사는 큰따옴표로 감싼다). LMS 발송 문구도 큰따옴표로 인용되므로
같은 블록으로 섰다. 하지만 둘은 쓰임이 다르다 — 화법은 직원이 **말하는** 것이고, 발송 문구는
직원이 발송 화면에 **붙여 넣는** 것이다. 그래서 어느 인용이 발송 문구인지 코드가 짚어
`answer.messages` 로 싣는다(main.py 머리말 «출력 형식» · client/README.md).

━━ 무엇을 발송 문구로 보나 ━━
**`(광고)` 로 시작하는 인용**이다. 이 접두는 LLM 이 쓰는 것이 아니라 발송 문구의 골격을
조립하는 코드가 붙인다(strategy_agent/support/outreach.py::lms_frame · AD_PREFIX). 그래서
화법 대사가 여기 걸릴 일이 없고, 직원이 「더 짧게」로 다시 쓰게 한 턴(원장에 outreach
재료가 없는 턴)의 문구도 놓치지 않는다.

━━ 복사할 값 ━━
`text` 는 **본문에 있는 인용 내용 그대로**다 — 프론트는 그것으로 본문의 인용을 찾는다
(`links` 의 `screen` 과 같은 규약). `copy` 는 클립보드에 넣을 값이다. 본문의 인용이 브리핑이
만든 문구와 공백만 다르면(답변이 줄바꿈을 한 줄로 이어 쓰는 경우) 브리핑 원본을 준다 —
광고 표기·링크·수신거부가 줄마다 선 원래 꼴이 발송 화면에 들어가야 한다. 다르게 고쳐 쓴
문구면 본문 그대로다(직원이 읽은 것이 복사된다).

더미 콘텐츠에서 온 문구는 싣지 않는다 — 발송 화면에 채우는 것을 막는 게이트
(actions.open_lms_screen)와 같은 판정이다. 복사 블록은 그 게이트를 우회하는 지름길이 된다.
그 인용은 지금처럼 화법 블록으로 남는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pension_agent.strategy_agent.support.outreach import AD_PREFIX

from pension_agent.consult_agent.effects.actions import _match_asset

#: 큰따옴표 인용 — 곧은 따옴표와 둥근 따옴표. 발송 문구는 여러 줄일 수 있다.
_QUOTE = re.compile(r"“([^“”]+?)”|\"([^\"]+?)\"", re.S)

LABEL = "고객 발송 문구"


def _squash(text: str) -> str:
    return " ".join(text.split())


def canonical(evidence: Iterable[dict]) -> list[str]:
    """이번 턴 원장의 outreach 재료가 실어 온 발송 문구 원본(tools/outreach.py `meta.messages`)."""
    out: list[str] = []
    for ev in evidence or []:
        for text in (ev.get("meta") or {}).get("messages") or []:
            if text and text not in out:
                out.append(text)
    return out


def messages_in(answer: str, originals: Iterable[str] = ()) -> list[dict]:
    """본문의 발송 문구 인용 — `{"kind": "lms", "label", "text", "copy"}` 목록. 없으면 []."""
    by_squash = {_squash(o): o for o in originals}
    out: list[dict] = []
    for m in _QUOTE.finditer(answer or ""):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        if not inner.strip().startswith(AD_PREFIX):
            continue
        asset = _match_asset(inner)
        if asset is not None and asset.get("dummy"):
            continue
        if any(item["text"] == inner for item in out):
            continue
        out.append({"kind": "lms", "label": LABEL, "text": inner,
                    "copy": by_squash.get(_squash(inner), inner.strip())})
    return out
