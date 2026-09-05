"""안내 콘텐츠 도구(outreach) — 이 고객에게 안내할 세미나·이벤트와 발송 문구.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from pension_agent.consult_agent.state import AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev
from pension_agent.consult_agent.tools.briefing import _cond_labels


def _outreach(state: AgentState, query: str) -> Evidence | None:
    """⑨ 이 고객에게 안내할 세미나·이벤트 — 화면 ⑨ 와 **같은 산출**을 재료로 싣는다.

    화면 ⑨ 는 상담 전에 이벤트 1건 + 세미나 1건을 골라 두는데, 그 선정과 문구가 대화
    쪽에는 재료로 없었다. 그래서 "이 고객한테 보낼 만한 세미나 있어?"·"왜 이거야?"·"다른 건
    없어?"가 전부 재료 0건으로 끝났고, 문구를 다듬어 달라는 요청도 일정·링크가 원장에 없어
    검증기에 잘렸다(pension_agent/verify.py 는 원장 밖 수치를 자른다).

    **여기서 다시 고르지 않는다.** 선정은 strategy_agent 가 하고 이 도구는 그 결과와 후보군을
    옮기기만 한다 — 고르는 경로가 둘이면 화면과 대화가 같은 고객에게 다른 세미나를 말한다
    (`_customer` 가 ⑥⑦⑧ 을 옮기기만 하는 것과 같은 이유).

    문구도 마찬가지다. 발송 화면 연계(nodes/act.py)가 쓰는 문구는 여기 실린 `lms_message`
    이고, 그것은 브리핑이 만든 값 그대로다 — 대화가 문구를 새로 생성하면 화면에 뜬 것과
    다른 문자가 나간다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.strategy_agent import agent as strategy_agent  # noqa: PLC0415
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        facts = strategy_agent.propose(profile)["facts"]
    except Exception:
        return None

    picked = facts.get("outreach") or {}
    pools = ((facts.get("pools") or {}).get("outreach")) or {}
    if not (picked.get("event") or picked.get("seminar")):
        return None

    # **개수를 코드가 세어 싣는다.** 답을 쓰면 「2건을 추천드려요」·「1. … 2. …」처럼 개수와
    # 열거 번호가 문장에 들어가는데, 그 수가 재료에 없으면 verify 가 «원장 밖 수치»로 보고
    # **맞는 답을 통째로 폐기한다**(그러면 compose 가 이 블록을 그대로 덤프한다 — 실측:
    # 안내 콘텐츠 2건을 고른 답이 "수치 '2'" 로 잘렸다). 세는 것은 코드가 이미 아는 사실이라
    # 지어낼 자리가 없다 — `suitable` 도구가 「안내할 수 있는 상품 N종」을 싣는 것과 같다.
    n_picked = sum(1 for key in ("event", "seminar") if picked.get(key))
    n_other = {key: max(len(pools.get(key) or []) - (1 if picked.get(key) else 0), 0)
               for key in ("event", "seminar")}
    lines = [f"■ 고객 {customer_id} — 안내할 이벤트·세미나 (브리핑 ⑨ 와 같은 선정)",
             f"· 지금 안내할 것 {n_picked}건 — "
             + " · ".join(f"{label} {1 if picked.get(key) else 0}건"
                          for key, label in (("event", "이벤트"), ("seminar", "세미나")))
             + f" · 아직 열려 있는 다른 후보 이벤트 {n_other['event']}건 · "
               f"세미나 {n_other['seminar']}건"]
    atomic: list[str] = []
    lms: dict[str, dict] = {}
    for key, label in (("event", "이벤트"), ("seminar", "세미나")):
        item = picked.get(key)
        if not item:
            continue
        lines.append(f"· [{label}] {item['name']} — {item['schedule']} · 주관 {item.get('organizer') or '미상'}")
        if item.get("description"):
            lines.append(f"  내용: {item['description']}")
        if item.get("reason"):
            lines.append(f"  추천 사유: {item['reason']}")
        if item.get("keywords"):
            lines.append(f"  매칭 키워드: {', '.join(item['keywords'])}")
        if item.get("url"):
            lines.append(f"  안내 링크: {item['url']}")
            # 링크는 한 글자만 달라도 죽는다 — 답변이 이 값을 말하면 원문 그대로여야 한다.
            atomic.append(item["url"])
        lines.append(f"  발송 문구: {item['lms_message']}")
        lms[key] = {"id": item["id"], "name": item["name"], "message": item["lms_message"]}
        # 다른 후보 — "다른 건 없어?" 에 답할 재료다. 선정된 것은 위에 이미 있으므로 뺀다.
        others = [c for c in (pools.get(key) or []) if c["id"] != item["id"]]
        if others:
            lines.append(f"  다른 {label} 후보 {len(others)}건:")
        for other in others:
            lines.append(f"  · {other['name']} — {other['schedule']}")

    for label, values in (("문제상황", [s["title"] for s in facts.get("problem_situations") or []]),
                          ("성립 요건", _cond_labels(facts.get("conditions") or []))):
        if values:
            lines.append(f"· {label}: {', '.join(values[:4])}")
    # 선별·문구가 LLM 산출이 아니면 그 사실을 재료에 남긴다 — 직원이 "AI 가 고른 것"으로
    # 읽는 것과 "임박 순으로 뜬 것"으로 읽는 것은 다른 판단이다(REQUIREMENTS.md 「LLM 미생성 표시」).
    for key, why in (facts.get("llm_skipped") or {}).items():
        if key.startswith("outreach") or key == "lms_message":
            lines.append(f"· 참고: {key} — {why}")

    return _ev("outreach", query, "\n".join(lines),
               [{"id": f"outreach.{customer_id}",
                 "title": "고객님께 안내해보세요 — 열려 있는 이벤트·세미나",
                 "doc": "안내 콘텐츠 레지스트리 (브리핑 ⑨ 와 같은 산출)",
                 "score": None, "page": None}],
               atomic=atomic,
               # 발송 화면 연계(act.py)가 쓰는 문구. 승낙 턴이 문구를 다시 만들지 않도록
               # 이번 턴의 산출을 그대로 들려 보낸다(CLAUDE.md §10 「제안한 턴이 남긴 것으로 정한다」).
               meta={"lms": lms})
