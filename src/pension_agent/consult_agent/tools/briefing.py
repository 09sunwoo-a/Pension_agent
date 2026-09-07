"""고객 브리핑 재료 도구(customer) — strategy_agent 가 계산한 것을 그대로 옮긴다.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

import json
from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent.tools.base import Evidence, _ev


def _customer(state: AgentState, query: str) -> Evidence | None:
    """지금 열려 있는 고객의 브리핑 재료. 계산은 strategy_agent 가 이미 한 것을 그대로 쓴다
    (같은 판정을 두 번 구현하지 않는다). 화면에 보이는 것과 다른 값을 말하면 안 되기 때문이다.

    **고객 재료로 답하는 경로는 이것 하나다.** 예전에는 `briefing_qa` 노드가 같은
    propose() 를 자기 프롬프트·자기 검증으로 한 번 더 답했다. 두 경로는 규약이 갈렸고
    (한쪽만 「하지 말 것」을 무조건 붙이고, 한쪽만 브리핑 산문을 보여줬다) 같은 질문이
    분류에 따라 다른 답을 받았다(CLAUDE.md §3 · §12 gap 11). 노드를 지우면서 그쪽이
    갖고 있던 재료 — 화면에 실제로 뜬 AI브리핑 문장·근거 해설 — 를 여기로 옮겼다.
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
        result = strategy_agent.propose(profile)
        facts = result["facts"]
    except Exception:
        return None

    lines = [f"■ 고객 {customer_id} — 브리핑 자료"]
    lines += [f"· {k} {v}" for k, v in facts["customer"].items()]
    lines += [f"· {k} {v}" for k, v in facts["briefing"].items() if k != "source"]
    # 계좌 상태 — **정상인 항목도 값으로** 싣는다. 화면(briefing)은 요건이 성립한 것만
    # 렌더하는데(그게 맞다 — 한 장짜리 브리핑이다), 그 필터가 그대로 넘어오면 직원이
    # "디폴트옵션 설정돼 있어?" 라고 물었을 때 **미설정 고객에게만** 답이 나갔다. 설정된
    # 고객에게는 "준비된 자료가 없어요" — 정확히 "네, 돼 있습니다" 라고 답해야 하는 자리다.
    # 값이 없어서가 아니라 문제일 때만 실려서였다(engine/render.py::_account_state).
    lines += [f"· {k.replace('_', ' ')} {v}" for k, v in (facts.get("account_state") or {}).items()]
    if facts.get("conditions"):
        lines.append(f"· 성립 요건: {', '.join(_cond_labels(facts['conditions']))}")
    # 「왜 이 고객이 관리 대상인가」 — 직원이 실제로 가장 많이 묻는 것인데 통째로 빠져
    # 있었다. 요건 코드(dor·mis…)만 있고 그 요건이 왜 문제인지, 어느 세그먼트에 걸렸는지,
    # 무엇을 근거로 뽑혔는지가 재료에 없어서 LLM 이 요건 이름만 풀어 쓰거나 지어냈다.
    for label, values in (("왜 이 고객인가", facts.get("why_this_customer")),
                          ("판단근거", facts.get("rationale")),
                          ("고지 필요", facts.get("cautions")),
                          ("확인 필요", facts.get("needs_confirm"))):
        for v in values or []:
            lines.append(f"· {label}: {v}")
    for sit in facts.get("problem_situations") or []:
        lines.append(f"· 문제상황 {sit['no']}: {sit['title']} [{sit['group']}]")
    # 화면 ⑥⑦⑧ 이 **이 고객에게** 고른 화법·반론·참고자료. 위 문제상황에서 나온 짝이라
    # 여기 붙는다(REQUIREMENTS.md §2 「문제상황 정의 — ⑥⑦⑧⑨ 의 출발점」).
    #
    # 이 세 줄이 없던 동안 "이 고객한테 뭐라고 말하지"는 `pitch` 도구가 지식베이스 화법
    # 102건을 **고객과 무관하게** 검색해 답했다 — 화면 ⑥에 그 고객 맞춤 화법이 떠 있는데도.
    # 화면과 대화가 같은 질문에 다른 카드를 말하는 상태였고, 그게 이 저장소가 가장
    # 경계하는 실패다(CLAUDE.md §3). allow 에는 이미 facts 전체가 있어 인용은 허용됐지만,
    # 재료 텍스트에 없으면 LLM 은 그 카드를 본 적이 없다.
    #
    # 값은 facts 를 **그대로** 옮긴다. 여기서 다시 고르면 그것이 두 번째 선정 경로가 되고,
    # 화면과 다시 갈린다. 고르는 것은 strategy_agent 몫이다.
    card_sources: list[dict] = []
    card_lines: list[str] = []
    for label, items, keys in (("이렇게 말해보세요", facts.get("talking_points"), ("title", "talk")),
                               ("예상 반론", facts.get("objections"), ("objection", "response")),
                               ("상담 참고", facts.get("consult_resources"), ("title", "snippet"))):
        for item in items or []:
            head, body = (str(item.get(k) or "").strip() for k in keys)
            if not head and not body:
                continue
            mark = " · ".join(x for x in (item.get("card_id"), item.get("situation")) if x)
            card_lines.append(f"· {label}: {head} — {body}" + (f" [{mark}]" if mark else ""))
            # 이 줄들의 출처는 **지식 카드**다(고객 원장이 아니다). 답에 영향을 준 재료는
            # 전부 출처에 실린다(§3) — 안 실으면 직원은 화법이 어디서 나왔는지 모른 채
            # 읽는다. 검색으로 온 재료가 아니므로 관련도(score)는 없다.
            #
            # **URL 도 함께 되짚는다.** strategy_agent 가 넘겨주는 항목에는 문서명(`source`)
            # 밖에 없어서, 출처에 원천 게시글 URL 을 싣기로 한 변경이 이 재료만 비껴갔다 —
            # 같은 카드가 검색으로 오면 ↗ 줄이 붙고 고객 재료로 오면 안 붙었다. 표기를
            # 만드는 곳은 하나라는 규약대로 kb 에서 되짚는다(`card_source_meta`).
            if item.get("card_id"):
                meta = KBMOD.card_source_meta(KB, item["card_id"])
                card_sources.append({"id": item["card_id"], "title": head,
                                     "doc": meta.get("doc") or item.get("source") or "출처 미상",
                                     "url": meta.get("url"),
                                     "score": None, "page": None})
    lines += card_lines
    # 화면에 뜬 AI 산문. 직원은 이걸 보면서 묻기 때문에 재료에 없으면 "화면에 저렇게
    # 써 있는데 왜 다르게 말하느냐"가 된다. 값이 아니라 산문이므로 원문 스팬은 아니다.
    for label, text in (("AI브리핑 문장", result.get("sentence")),
                        ("AI브리핑 근거해설", result.get("insight"))):
        if text:
            lines.append(f"· {label}: {text}")
    # allow 에는 facts 전체를 넣는다 — 화면에 안 띄운 값도 '재료'이므로 인용 자체는 이탈이 아니다.
    # atomic·notices 를 비워 둔다. 고객 재료는 항목이 많아 전부 원문으로 요구하면 답변이 표 덤프가
    # 되고, 항목별로 고르면 무엇을 고를지에 근거가 없다. 수치 집합 검사만 걸린다
    # (재조합은 잡지 못한다).
    # 출처를 낸다. 지식 카드가 아니라 **전략제안이 계산한 브리핑 재료**라 카드 id 가 없지만,
    # "이 값이 어디서 왔나"는 답해야 한다(§3 모든 답에 출처를 밝힌다). 출처가 안 실리면
    # 화면에 "근거: 없음"이 뜨고, 직원은 값을 어디서 확인할지 모른 채 답만 받는다.
    # 출처는 **고객 원장**이지 지식 카드가 아니다. 예전 표기("AI브리핑 재료 /
    # briefing.<id>")는 화면에서 카드 id 처럼 읽혀, 계좌에 그냥 들어 있는 값이 어딘가에서
    # 검색해 온 자료처럼 보였다. 검색으로 온 재료가 아니므로 관련도(score)도 없다.
    # 같은 카드가 ⑥과 ⑧에 함께 뽑힐 수 있다(pitch.k03.038 이 실제로 그렇다) — 첫 등장만 남긴다.
    seen: set[str] = set()
    deduped = [s for s in card_sources if not (s["id"] in seen or seen.add(s["id"]))]
    # 레이블–값 짝을 선언한다. 이 재료의 허용 집합에는 화면 값 말고도 ⑥⑦⑧ 의 화법·반론·
    # 참고자료 수치가 함께 들어 있어서(직원이 그것도 묻는다) 집합 포함 검사만으로는
    # "세액공제 잔여한도는 300만원이에요"(실제 0만원)가 통과한다 — 300 은 화법 문구에 실제로
    # 있는 숫자다. 선언을 relations.labeled_mispaired() 가 대조한다(fact 의 tiers·05 표의
    # tables 와 같은 자리다).
    labeled = [{"label": k.replace("_", " "), "value": str(v)}
               for src in (facts["customer"],
                           {k: v for k, v in facts["briefing"].items() if k != "source"},
                           facts.get("account_state") or {})
               for k, v in src.items()]
    return _ev("customer", query, "\n".join(lines),
               [{"id": f"customer.{customer_id}",
                 "title": f"{profile.nm} 고객 계좌 현황 (KB-PIN {customer_id})",
                 "doc": "고객 정보 — 계좌 원장 조회값 (브리핑 화면과 같은 값)",
                 "score": None, "page": None}, *deduped],
               allow=["\n".join(lines), json.dumps(_citable(facts), ensure_ascii=False, default=str)],
               cards=[{"id": f"customer.{customer_id}", "labeled": labeled,
                       # 재료 전문 — 항목 이름이 다른 자리(문제상황 제목·⑥⑦⑧ 카드 문구
                       # 등)에도 나오면 그 항목은 판정에서 뺀다(relations.checkable).
                       #
                       # **밑줄을 공백으로 펴서 넘긴다.** `labeled` 의 레이블은 정규화된
                       # 이름("당해 납입액")인데 재료 줄은 원장 키 그대로("당해_납입액")라,
                       # 그냥 넘기면 **자기 이름이 한 번도 안 세어진다** — 「재료 어디에든
                       # 제 이름이 다시 나오면 뺀다」가 통째로 작동하지 않는다. 그 상태에서
                       # ⑧ 의 일반 제도 설명("전년·당해 납입액이 세액공제 한도 900만원에
                       # 미달하는 고객")이 이 고객의 「당해 납입액 600만원」과 짝지어져
                       # "600인데 900이라 한다"로 잡혔다 — 카드 원문을 고객 값 주장으로
                       # 오독한 것이다(§6 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을
                       # 통과시키는 것보다 나쁘다). 이름이 두 자리에 나오면 가릴 수 없으므로
                       # 그 항목만 빠지고, 안 겹치는 항목의 판정은 그대로 남는다.
                       "context": "\n".join(lines).replace("_", " ")}])


#: 인용 허용 집합에서 빼는 facts 가지. 값이 아니라 **선별 전 후보 더미**다.
#:
#: allow 는 "이 답변이 인용해도 되는 값"의 집합이고, verify 는 답변의 수치가 그 안에 있는지만
#: 본다. 그래서 답변이 쓰지도 않을 카드 더미를 넣으면 그 안의 온갖 숫자가 **아무 주장에나
#: 근거를 대주는 꼴**이 된다. pools(⑦⑧ 후보군)는 카드 id·발췌가 통째로 들어와 이준호
#: 케이스에서만 허용 수치를 22개 → 110개로 5배 불렸고, 그 결과 "만기일은 2026년 9월
#: 11일"(오답)이 통과했다 — 9 와 11 이 무관한 카드 어딘가에 있었기 때문이다.
#:
#: 반대로 items·blocked_products·dropped·outreach 는 뺄 수 없다. 직원이 실제로 묻는
#: 것들이다("왜 ELB 는 빠졌어?" → blocked_products 의 최소가입금액).
_POOL_KEYS = ("pools",)


def _citable(facts: dict) -> dict:
    """인용 허용 집합에 실을 facts. 후보 더미만 걷어낸다."""
    return {k: v for k, v in facts.items() if k not in _POOL_KEYS}


def _cond_labels(conditions: list[str]) -> list[str]:
    """브리핑 산출의 성립 요건(`코드:이름`)에서 이름만. 화면(`app.py`)이 하는 것과 같은 처리.

    코드(`isa`·`tax`·`add`)를 재료에 그대로 실었던 동안 답변이 그것을 옮겼다 — 「세액공제
    활용 가능(tax)과 추가입금 여력 보유(add) 요건이 성립되어 있어서」(2026-09-03 실측,
    확정본 E1). 재료에 있는 말은 답변에 그대로 나오고 생성 지시로는 못 막는다(CLAUDE.md
    §5 「재료에 개발 용어를 쓰지 않는다」). 코드는 `customer.CONDS` 의 키일 뿐 직원에게
    뜻이 없다.
    """
    return [c.split(":", 1)[1] if ":" in c else c for c in conditions]
