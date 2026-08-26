"""⑨ 고객님께 안내해보세요 — 고객에게 실제로 나가는 콘텐츠.

여기 있는 자산만 LMS 발송 대상이 된다. 더미 콘텐츠의 발송은 텍스트 표시가 아니라
`pension_agent/tools.py::open_lms_screen()` 의 게이트가 막는다(CLAUDE.md 5번).
"""

from __future__ import annotations

from datetime import date

from pension_agent.strategy_agent.customer import TODAY
from pension_agent.strategy_agent.support.matching import ASSETS


# ─────────────────────────────────────────────────────────────
# ⑨ 고객님께 안내해보세요
# ─────────────────────────────────────────────────────────────

def _outreach_row(a: dict) -> dict:
    return {
        "name": a["name"], "start_date": a["start_date"], "end_date": a["end_date"],
        "channel": a.get("channel"), "lms_message": a.get("lms_message"),
        "segments": list(a.get("segments") or []),
        # 실제 콘텐츠 캘린더가 아니라 데모용으로 지어낸 일정인지. 화면·발송 문구가 이 표시를
        # 그대로 노출해서, 더미를 실제 안내로 오해한 채 고객에게 보내는 일을 막는다.
        "dummy": bool(a.get("dummy")),
    }


def _open_assets(content_type: str, today: date) -> list[dict]:
    """종료되지 않은(end_date >= today) 해당 종류의 콘텐츠."""
    return [
        a for a in ASSETS
        if a.get("content_type") == content_type and a.get("end_date")
        and date.fromisoformat(a["end_date"]) >= today
    ]


def _outreach_order(situations: list[dict] | None):
    """정렬 키 — 이 고객의 문제상황에 걸린 콘텐츠를 먼저, 그다음 임박한 순.

    같은 기간에 여러 콘텐츠가 열려 있으면 '가장 임박한 것'만으로는 이 고객과 상관없는 안내가
    먼저 나온다. 관리 사유에 맞는 콘텐츠를 앞세우고, 그 안에서 임박 순으로 본다.
    """
    wanted = {s["id"] for s in (situations or [])}

    def key(a: dict) -> tuple[int, str]:
        overlap = len(wanted & set(a.get("segments") or []))
        return (-overlap, a["start_date"])

    return key


def outreach_candidates(situations: list[dict] | None = None,
                        today: date | None = None) -> dict[str, list[dict]]:
    """⑨ 안내 콘텐츠의 후보군 — 종료되지 않은 이벤트·세미나 전체를 관련도·임박 순으로 돌려준다.

    REQUIREMENTS.md §15 는 세미나/이벤트를 '콘텐츠 DB(Rule) + 선별(LLM)' 로 지정한다. 종료 콘텐츠 제외와
    정렬은 규칙(여기)이 하고, 그중 어느 것이 이 고객에게 맞는지는 LLM 이 고른다
    (agent._select_db_sections). LLM 이 없으면 next_event_and_seminar() 의 첫 건이 그대로 쓰인다.
    """
    today = today or TODAY
    order = _outreach_order(situations)
    return {key: [_outreach_row(a) for a in sorted(_open_assets(content_type, today), key=order)]
            for content_type, key in (("이벤트", "event"), ("세미나", "seminar"))}


def next_event_and_seminar(situations: list[dict] | None = None,
                           today: date | None = None) -> dict[str, dict | None]:
    """이 고객에게 안내할 이벤트 1개 + 세미나 1개(REQUIREMENTS.md ⑨ "고객님께 안내해보세요").

    content_type 별로 종료되지 않은 것(end_date >= today) 중 문제상황에 맞는 것을 먼저,
    같으면 start_date 가 빠른 것을 고른다 — 진행 중이거나 미래 일정인 콘텐츠를 우선하고
    종료된 콘텐츠는 노출하지 않는다는 요건을 그대로 코드로 옮긴 것. LLM 은 개입하지 않는다.
    """
    today = today or TODAY
    order = _outreach_order(situations)

    def _pick(content_type: str) -> dict | None:
        candidates = _open_assets(content_type, today)
        return _outreach_row(min(candidates, key=order)) if candidates else None

    return {"event": _pick("이벤트"), "seminar": _pick("세미나")}
