"""고객별 솔루션의 도구 — 계획 단계(nodes/plan)가 부르는 함수는 전부 이 파일에 있다.

계산은 하지 않는다. customer.py · solution.py · engine/ · support/ 가 이미 계산한 것을
원장 근거(Evidence)로 옮긴다 — 같은 판정을 두 번 구현하면 브리핑 화면과 대화가 다른 값을 말한다.

    customer    부분 질문(«수익률 얼마야»·«왜 타겟이야»)의 재료. LLM 을 부르지 않는다
    briefing    전체 브리핑 문서. 이 도구가 실은 문서는 답변 작성(compose)을 거치지 않고
                그대로 답이 된다(nodes/answer.py)
    suitable    투자성향으로 어디까지 안내할 수 있는지
    outreach    안내할 세미나·이벤트와 발송 문구
    targets     오늘의 타겟 고객 목록 — 고객 화면을 열기 «전»의 재료

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

import json

from pension_agent.common import clock
from pension_agent.evidence.record import Evidence, _ev, _scope
from pension_agent.knowledge import kb as KBMOD
from pension_agent.state import KB, AgentState
from pension_agent.tools.kb_search import kb_index
from pension_agent.tools.kb_search.cards import advisory_mark


# ═════════════════════════════════════════════════════════════
# customer · briefing
# ═════════════════════════════════════════════════════════════

def _customer(state: AgentState, query: str) -> Evidence | None:
    """지금 열려 있는 고객의 브리핑 재료. 계산은 solution.py 가 이미 한 것을 그대로 쓴다
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
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import solution  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        facts = solution.facts(profile)
        # 전체 브리핑을 이미 만들었으면 그 서술을 함께 싣는다 — 직원은 그 문서를 보면서
        # 묻는다. 없으면 만들지 않는다(부분 질문에 LLM 을 쓰지 않는다).
        narrative = solution.peek(profile) or {}
    except Exception:
        return None

    # 머리줄은 **이름으로** 부른다. 고객 식별번호(KB-PIN)를 싣지 않는 이유는 둘이다:
    # LLM 이 답하는 데 쓰지 않는 값이고(직원은 그 고객 화면을 열어 둔 채 묻는다), 행내
    # 게이트웨이의 개인정보 필터가 이 꼴을 주민등록번호로 보고 **요청을 통째로 400 으로
    # 끊는다**(2026-09-29 실측 — `pension_agent/common/privacy.py` 머리말). 한 턴에 열려 있는
    # 고객은 하나라 이름만으로 어느 고객인지 갈린다. 경계는 여기서 긋는다 — 재료에 없으면
    # 프롬프트에도 없다(§3 오늘의 타겟 목록이 같은 결정을 이미 적어 뒀다).
    lines = [f"■ 고객 {profile.nm} — 브리핑 자료"]
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
    # 화면과 다시 갈린다. 고르는 것은 solution.py 몫이다.
    card_sources: list[dict] = []
    card_keys: dict[str, list[str]] = {}
    card_lines: list[str] = []
    for label, items, keys in (("이렇게 말해보세요", facts.get("talking_points"), ("title", "talk")),
                               ("예상 반론", facts.get("objections"), ("objection", "response")),
                               ("상담 참고", facts.get("consult_resources"), ("title", "snippet"))):
        for item in items or []:
            head, body = (str(item.get(k) or "").strip() for k in keys)
            if not head and not body:
                continue
            # 줄 꼬리에는 «어느 문제상황에서 나온 카드인가»만 붙인다. 카드 id 는 붙이지 않는다 —
            # 재료에 있는 말은 답변에 그대로 나온다(§5 「재료에 개발 용어를 쓰지 않는다」).
            # 화법 렌더러가 머리줄에 id 를 싣던 동안 답변이 「[pitch.k03.020] 자료를 활용해
            # 보세요」라고 썼고(2026-09-21 행내 실측), 이 줄도 같은 꼴(`[pitch.k03.020 · 상황]`)
            # 이었다. id 는 아래 출처(card_sources)에 남는다 — 그것이 화면의 역추적 자리다.
            mark = item.get("situation")
            card_lines.append(f"· {label}: {head} — {body}" + (f" [{mark}]" if mark else ""))
            # 이 줄들의 출처는 **지식 카드**다(고객 원장이 아니다). 답에 영향을 준 재료는
            # 전부 출처에 실린다(§3) — 안 실으면 직원은 화법이 어디서 나왔는지 모른 채
            # 읽는다. 검색으로 온 재료가 아니므로 관련도(score)는 없다.
            #
            # **URL 도 함께 되짚는다.** solution.py 가 넘겨주는 항목에는 문서명(`source`)
            # 밖에 없어서, 출처에 원천 게시글 URL 을 싣기로 한 변경이 이 재료만 비껴갔다 —
            # 같은 카드가 검색으로 오면 ↗ 줄이 붙고 고객 재료로 오면 안 붙었다. 표기를
            # 만드는 곳은 하나라는 규약대로 kb 에서 되짚는다(`card_source_meta`).
            if item.get("card_id"):
                meta = KBMOD.card_source_meta(KB, item["card_id"])
                card_sources.append({"id": item["card_id"], "title": head,
                                     "doc": meta.get("doc") or item.get("source") or "출처 미상",
                                     "url": meta.get("url"),
                                     "score": None, "page": None})
                # 이 카드를 답변이 실제로 썼는지 가릴 스팬(record.Evidence.source_keys).
                # 머리글과 본문은 카드마다 다르므로, 둘 중 어느 것도 답변에 없으면 이 답은
                # 그 카드로 쓰인 것이 아니다. 검색 결과가 아니라 **브리핑 묶음에 딸려 온**
                # 재료라 이 판정을 붙인다 — 「디폴트옵션 등록됐어?」에 원장 한 줄로 답하고도
                # 화법 다섯 장이 근거로 따라 섰다(2026-09-17 실측, 박정호).
                card_keys.setdefault(item["card_id"], []).extend([head, body])
    lines += card_lines
    # 전체 브리핑에 나간 AI 분석. 직원은 그 문서를 보면서 묻기 때문에 재료에 없으면
    # "브리핑에 저렇게 써 있는데 왜 다르게 말하느냐"가 된다. 값이 아니라 서술이므로 원문 스팬은 아니다.
    for key, label in solution.NARRATIVE_FIELDS.items():
        value = narrative.get(key)
        for text in (value if isinstance(value, list) else [value]):
            if text:
                lines.append(f"· 브리핑 {label}: {text}")
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
               # 출처 id 에도 KB-PIN 을 넣지 않는다. 출처는 응답의 `sources` 이벤트로 프론트까지
               # 나가는데, 그 길에 선 플랫폼 게이트웨이의 개인정보 필터가 `customer.171203-4815062`
               # 를 주민등록번호로 보고 **응답을 통째로 막았다**(FILTER_INVALID — 김서연 「이 고객
               # 왜 타겟」, LLM 쪽을 고친 다음 날 실측). 한 턴에 열려 있는 고객은 하나라 id 에
               # 번호가 없어도 갈리고, 이 id 를 되읽어 고객을 찾는 코드는 없다(고객은 항상
               # `state["customer_id"]` 에서 읽는다). 제목도 같다 — 제목은 화면뿐 아니라 계획
               # 프롬프트의 「이미 모은 재료」로도 나간다(`evidence/ledger.py::summarize`).
               [{"id": "customer",
                 "title": f"{profile.nm} 고객 계좌 현황",
                 "doc": "고객 정보 — 계좌 원장 조회값 (브리핑 화면과 같은 값)",
                 "score": None, "page": None}, *deduped],
               source_keys=card_keys,
               allow=["\n".join(lines), json.dumps(_citable(facts), ensure_ascii=False, default=str)],
               cards=[{"id": "customer", "labeled": labeled,
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
    """브리핑 산출의 성립 요건(`코드:이름`)에서 이름만.

    코드(`isa`·`tax`·`add`)를 재료에 그대로 실었던 동안 답변이 그것을 옮겼다 — 「세액공제
    활용 가능(tax)과 추가입금 여력 보유(add) 요건이 성립되어 있어서」(2026-09-03 실측,
    확정본 E1). 재료에 있는 말은 답변에 그대로 나오고 생성 지시로는 못 막는다(CLAUDE.md
    §5 「재료에 개발 용어를 쓰지 않는다」). 코드는 `customer.CONDS` 의 키일 뿐 직원에게
    뜻이 없다.
    """
    return [c.split(":", 1)[1] if ":" in c else c for c in conditions]


def _briefing(state: AgentState, query: str) -> Evidence | None:
    """전체 브리핑 문서 — «이 고객 브리핑 해줘»·«전체 정리해줘».

    문서는 `solution.document()` 가 만든다(사실은 코드, 서술은 LLM 1회 + 검증). 이 도구의
    산출은 답변 작성을 거치지 않고 그대로 답이 된다(`meta["document"]` — nodes/answer.py).
    compose 가 다시 쓰면 양식이 흐트러지고 표의 값도 LLM 이 옮겨 적게 된다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import solution  # noqa: PLC0415
    profile = strategy_customer.get_profile(customer_id)
    if profile is None:
        return None
    doc = solution.document(profile)
    facts = solution.facts(profile)
    return _ev("briefing", query, doc["markdown"],
               [{"id": "customer", "title": f"{profile.nm} 고객 브리핑",
                 "doc": "고객 정보 — 계좌 원장 조회값과 AI 분석",
                 "score": None, "page": None}],
               allow=[doc["markdown"], json.dumps(_citable(facts), ensure_ascii=False, default=str)],
               meta={"document": doc["markdown"], "tier": doc["tier"],
                     "narrative": bool(doc["narrative"])})

# ═════════════════════════════════════════════════════════════
# suitable
# ═════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────
# 적합성 범위 — "이 고객에게 뭘 추천하지?" 에 답할 수 있는 것
#
# **권유가 아니라 정보 제공이다.** 직원이 상품을 물으면 «이 고객 투자성향에서 어디까지
# 가능한가»와 «그 안에 무엇이 있는가»를 답하고, 범위 안의 특정 상품을 짚어 말할 수도
# 있다 — 다만 «이런 상품이 있습니다» 톤까지이고, 무엇을 권유할지는 자본시장법과 당행
# 규정에 따라 직원이 정한다(§8 관리대장, 2026-09-02 개정).
#
# 판정은 **하지 않는다.** engine 의 적합성 게이트가 이미 계산한 것을 그대로
# 옮긴다(위험등급 상한·거래채널). 같은 판정을 두 번 구현하면 브리핑 화면 ⑤ 「이런 상품이
# 적합할 수 있어요」와 대화형이 다른 목록을 말하게 된다.
#
# 이 도구가 없던 동안, 「이 고객 무슨 상품 추천해주지?」는 lineup 을 세 바퀴 돌고 재료
# 0건으로 끝났다. 계산은 코드가 이미 해뒀는데 **대화형에 그걸 부를 도구가 없었다** —
# 능력 표면은 도구 목록이므로(§3) 없는 도구는 없는 능력이다.
# ─────────────────────────────────────────────────────────────

# 제외 상품은 **전부** 싣는다. 예전에는 5건에서 잘랐는데(통과 목록이 묻힌다는 이유), 머리말은
# «안내할 수 없는 상품 6종»이라 쓰고 목록은 5건만 실리는 고객(안정추구형 — 제외 6건)이
# 생겼다. LLM 이 목록을 세어 «5종»이라 쓰면 verify 가 원장에 없는 수로 답을 통째로 버리고
# 이 블록을 덤프한다(2026-09-07 실측 — 오세훈·박정호 두 번). 재료가 말하는 수와 보여주는
# 목록은 같아야 한다. 카탈로그가 12종이라 제외 목록이 길어질 일도 없다.


def _suitable(state: AgentState, query: str) -> Evidence | None:
    """적합성 게이트가 허용하는 범위와 그 안의 상품. 고객 화면이 닫혀 있으면 없다(§3)."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import engine  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        pool = engine.candidate_pool_for_recommendation(profile)
        passed = pool["products"]
        # 상한은 게이트가 쓰는 것과 같은 값이어야 한다 — 통과 목록만 옮기고 상한을 따로
        # 셈하면 "다소높은위험까지 됩니다"와 목록이 어긋난다.
        cap = profile.grade
        conds = strategy_customer.conditions(profile)
        if "mis" in conds and strategy_customer.PREF.get(profile.rk):
            cap = strategy_customer.RISK[min(
                strategy_customer.RISK.index(cap),
                strategy_customer.RISK.index(strategy_customer.PREF[profile.rk]))]
        blocked: list[tuple[dict, str]] = []
        for row in engine.query_products(engine.PRODUCTS):
            ok, why = engine.gate_static(row, profile, cap)
            if not ok:
                blocked.append((row, why))
    except Exception:
        return None
    if not passed and not blocked:
        return None
    advice = advisory_mark({"advisory": kb_index.advisory_note(KB)})

    # 고객 식별번호 대신 이름으로 부른다 — 행내 개인정보 필터가 KB-PIN 을 주민등록번호로
    # 보고 요청을 400 으로 끊는다(`pension_agent/common/privacy.py` · _customer 의 같은 자리).
    lines = [f"■ 고객 {profile.nm} — 투자성향 {profile.rk} · 위험등급 {profile.grade}",
             f"· 적합성 허용 상한: {cap} (이 등급까지의 상품만 안내할 수 있다)",
             "",
             f"── 안내할 수 있는 상품 {len(passed)}종"]
    for r in passed:
        ret = engine.product_return(r)
        tail = f" · 최근 1년 {ret}%" if ret is not None else ""
        lines.append(f"· {r['name']} — {r['risk']}{tail}"
                     + (f" · {r['category']}" if r.get("category") else ""))
    for pf in pool["portfolios"]:
        lines.append(f"· [포트폴리오] {pf['name']} — {pf.get('description') or ''}".rstrip())
    if blocked:
        # 게이트가 돌려주는 사유는 「상품 위험등급 X > 허용 상한 Y」 꼴이라 **Y 가 줄마다
        # 반복된다.** 상한은 이 재료의 머리말에 이미 한 번 적혀 있는데, 그 줄을 그대로 옮긴
        # 답변은 제외 상품 여섯 줄이 전부 «> 허용 상한 낮은위험»으로 끝났다(2026-09-18 실측,
        # 박정호) — 줄마다 다른 것은 그 상품의 등급 하나뿐이니 읽는 사람에게 나머지는 잡음이다.
        #
        # **판정을 다시 하지 않는다.** 위험등급 초과인지 비대면 불가인지는 게이트가 정한
        # 것이고(`engine.gate_static`), 여기서는 그 문장에서 **이 도구가 방금 머리말에 쓴
        # 상한 문구만** 떼어낸다. 게이트 쪽 문장이 바뀌면 이 떼기가 아무 일도 하지 않고 사유가
        # 통째로 남는다 — 지금까지의 출력과 같으므로 틀린 쪽으로 실패하지 않는다.
        cap_tail = f" > 허용 상한 {cap}"
        lines += ["", f"── 안내할 수 없는 상품 {len(blocked)}종 (왜 목록에 없는지 — "
                      f"등급만 적힌 줄은 허용 상한 {cap}을 넘는다는 뜻이다)"]
        lines += [f"· {r['name']} — {why.replace(cap_tail, '')}" for r, why in blocked]
    else:
        # **0건일 때 침묵하지 않는다.** 재료가 아무 말도 안 하면 답변 형태가 요구하는
        # 「안내할 수 없는 상품」을 LLM 이 통과 목록에서 만들어 채운다(실측: 정민석 —
        # 12종을 11종이라 말하고 하나를 뺐다). 그리고 직원 입장에서도 «없는 것»과
        # «안 알려준 것»은 다르다 — 바로 앞 고객에서는 제외 4종이 나왔기 때문이다.
        lines += ["", "── 안내할 수 없는 상품 없음 "
                      f"(허용 상한이 {cap}이라 카탈로그 전부가 범위 안이다)"]
    return _ev("suitable", query, "\n".join(lines),
               # id·제목에 KB-PIN 을 넣지 않는다(_customer 와 같은 자리) — 제목은 계획 프롬프트로,
               # id 는 응답의 sources 이벤트로 나가고 그 길마다 개인정보 필터가 서 있다.
               [{"id": "suitable",
                 "title": f"{profile.nm} 고객 적합성 판정",
                 "doc": "투자성향 적합성 확인 — 위험등급 상한·거래채널 판정 결과 "
                        "(브리핑 화면 ⑤ 와 같은 후보군)",
                 "score": None, "page": None}],
               # 고지 문구를 **여기서 만들지 않는다.** 지식베이스가 선언한 것을 그대로
               # 옮긴다 — 재료 종류마다 코드 상수를 하나씩 두면 §7 이 사실상 없어진다
               # (§12 gap 20 이 그 경고다).
               notices=[advice] if advice else [],
               scopes=[_scope("적합성 판정", [], [advice])] if advice else [])

# ═════════════════════════════════════════════════════════════
# outreach
# ═════════════════════════════════════════════════════════════

def _outreach(state: AgentState, query: str) -> Evidence | None:
    """⑨ 이 고객에게 안내할 세미나·이벤트 — 화면 ⑨ 와 **같은 산출**을 재료로 싣는다.

    화면 ⑨ 는 상담 전에 이벤트 1건 + 세미나 1건을 골라 두는데, 그 선정과 문구가 대화
    쪽에는 재료로 없었다. 그래서 "이 고객한테 보낼 만한 세미나 있어?"·"왜 이거야?"·"다른 건
    없어?"가 전부 재료 0건으로 끝났고, 문구를 다듬어 달라는 요청도 일정·링크가 원장에 없어
    검증기에 잘렸다(pension_agent/evidence/verify.py 는 원장 밖 수치를 자른다).

    **여기서 다시 고르지 않는다.** 선정은 support/assets.py 가 하고 이 도구는 그 결과와 후보군을
    옮기기만 한다 — 고르는 경로가 둘이면 화면과 대화가 같은 고객에게 다른 세미나를 말한다
    (`_customer` 가 ⑥⑦⑧ 을 옮기기만 하는 것과 같은 이유).

    문구도 마찬가지다. 발송 화면 연계(nodes/act.py)가 쓰는 문구는 여기 실린 `lms_message`
    이고, 그것은 브리핑이 만든 값 그대로다 — 대화가 문구를 새로 생성하면 화면에 뜬 것과
    다른 문자가 나간다.
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import solution  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        facts = solution.facts(profile)
    except Exception:
        return None

    picked = facts.get("outreach") or {}
    pools = ((facts.get("pools") or {}).get("outreach")) or {}
    if not (picked.get("event") or picked.get("seminar")):
        return None

    # **요건에 맞는 것과 임박 순 폴백을 가른다.** 화면 ⑨ 는 이 고객 요건에 걸린 콘텐츠가
    # 하나도 없어도 날짜가 가까운 것을 세운다(섹션을 비우지 않는 요건). 그 폴백이 여기서
    # 요건에 맞는 것과 같은 모양으로 실리면 답변이 «이 고객에게 적합하다»고 쓴다 — 실측
    # (2026-09-07 최서윤): 걸린 콘텐츠 0건인데 답이 임박순 2건을 «적합»으로 세우고 사유까지
    # 붙였다. 판정은 추천 질문 칩과 **같은 함수**(`support.relevant_outreach`)로 한다 —
    # 칩은 안 뜨는데 대화는 «있다»고 말하는 상태를 만들지 않기 위해서다.
    from pension_agent.tools.customer_solution import support as strategy_support  # noqa: PLC0415
    try:
        relevant_ids = {r["id"] for r in strategy_support.relevant_outreach(
            facts.get("problem_situations") or [], name=profile.nm)}
    except Exception:
        relevant_ids = set()
    matched = {key: bool(picked.get(key)) and picked[key]["id"] in relevant_ids
               for key in ("event", "seminar")}

    # **개수를 코드가 세어 싣는다.** 답을 쓰면 「2건을 추천드려요」·「1. … 2. …」처럼 개수와
    # 열거 번호가 문장에 들어가는데, 그 수가 재료에 없으면 verify 가 «원장 밖 수치»로 보고
    # **맞는 답을 통째로 폐기한다**(그러면 compose 가 이 블록을 그대로 덤프한다 — 실측:
    # 안내 콘텐츠 2건을 고른 답이 "수치 '2'" 로 잘렸다). 세는 것은 코드가 이미 아는 사실이라
    # 지어낼 자리가 없다 — `suitable` 도구가 「안내할 수 있는 상품 N종」을 싣는 것과 같다.
    n_picked = sum(1 for key in ("event", "seminar") if matched[key])
    n_fallback = sum(1 for key in ("event", "seminar") if picked.get(key) and not matched[key])
    n_other = {key: max(len(pools.get(key) or []) - (1 if picked.get(key) else 0), 0)
               for key in ("event", "seminar")}
    # 다른 후보 중 이 고객 요건에 맞는 것 — 선정은 종류마다 한 건이라, 요건에 맞는 세미나가
    # 둘이면 하나는 «다른 후보»로 밀린다. 거기서 요건 표시 없이 이름만 실리면 답변이 그것을
    # 맞는 콘텐츠로 알아볼 수 없다. 실측(2026-09-28 오세훈): 만기예금 요건에 맞는 세미나가 후보
    # 목록에 이름만 있었고, «다른 건 없어?»에 답변이 요건 무관 폴백 이벤트를 «적합»으로 세웠다.
    n_other_fit = {key: sum(1 for c in (pools.get(key) or [])
                            if c["id"] in relevant_ids and c["id"] != (picked.get(key) or {}).get("id"))
                   for key in ("event", "seminar")}
    wanted = {c for s in (facts.get("problem_situations") or []) for c in (s.get("conds") or [])}

    def _fit_names(item: dict) -> list[str]:
        """이 콘텐츠가 맞는 이 고객의 요건 이름(CONDS). 맞댄 축은 `relevant_outreach` 와 같다."""
        return [strategy_customer.CONDS.get(c, c) for c in (item.get("conds") or []) if c in wanted]
    # 고객 식별번호 대신 이름으로 부른다 — 행내 개인정보 필터가 KB-PIN 을 주민등록번호로
    # 보고 요청을 400 으로 끊는다(`pension_agent/common/privacy.py` · _customer 의 같은 자리).
    lines = [f"■ 고객 {profile.nm} — 안내할 이벤트·세미나 (브리핑 ⑨ 와 같은 선정)",
             f"· 지금 안내할 것 {n_picked}건 — "
             + " · ".join(f"{label} {1 if matched[key] else 0}건"
                          for key, label in (("event", "이벤트"), ("seminar", "세미나")))
             + (f" · 이 고객 요건과 무관한 것 {n_fallback}건" if n_fallback else "")
             + f" · 아직 열려 있는 다른 후보 이벤트 {n_other['event']}건 · "
               f"세미나 {n_other['seminar']}건"
             + (f" · 다른 후보 중 이 고객 요건에 맞는 것 "
                f"{n_other_fit['event'] + n_other_fit['seminar']}건"
                if n_other_fit["event"] or n_other_fit["seminar"] else "")]
    if n_picked == 0:
        lines.append("· 이 고객 요건에 맞는 콘텐츠는 없다. 아래는 열려 있는 콘텐츠 중 날짜가 "
                     "가까운 것이며, 이 고객에게 맞는 것으로 안내하지 않는다")
    atomic: list[str] = []
    lms: dict[str, dict] = {}
    messages: list[str] = []
    for key, label in (("event", "이벤트"), ("seminar", "세미나")):
        item = picked.get(key)
        if not item:
            continue
        lines.append(f"· [{label}] {item['name']} — {item['schedule']} · 주관 {item.get('organizer') or '미상'}")
        if item.get("description"):
            lines.append(f"  내용: {item['description']}")
        if matched[key]:
            # 맞댄 축은 문제상황의 요건이다(`relevant_outreach` 와 같은 축). 이름은 CONDS 에서.
            lines.append(f"  요건 일치: {', '.join(_fit_names(item)) or '있음'}")
            if item.get("reason"):
                lines.append(f"  추천 사유: {item['reason']}")
        else:
            # 폴백은 사유를 싣지 않는다 — 사유 문장이 있으면 답변이 그것을 «맞춤»의 근거로 쓴다.
            lines.append("  요건 일치: 없음 — 이 고객 요건과 무관. 열려 있는 콘텐츠 중 날짜가 가까운 것")
        if item.get("keywords"):
            # 폴백의 키워드는 «이런 고객에게 맞는 콘텐츠»라는 뜻이지 이 고객과 맞은 것이 아니다 —
            # «매칭»이라고 적으면 답변이 이 고객이 그 상태인 것처럼 옮긴다.
            lines.append(f"  매칭 키워드: {', '.join(item['keywords'])}" if matched[key]
                         else f"  콘텐츠 대상 키워드(이 고객과 무관): {', '.join(item['keywords'])}")
        if item.get("url"):
            lines.append(f"  안내 링크: {item['url']}")
            # 링크는 한 글자만 달라도 죽는다 — 답변이 이 값을 말하면 원문 그대로여야 한다.
            atomic.append(item["url"])
        lines.append(f"  발송 문구: {item['lms_message']}")
        if not matched[key]:
            # 폴백 문구에 고객 상태가 섞여 있을 수 있다(예전 브리핑 · 규칙 밖 생성). 그 말을
            # 적합성의 근거로 옮기면 «요건 일치: 없음»이 답변에서 뒤집힌다.
            lines.append("  주의: 요건 무관 콘텐츠의 문구다 — 문구 안의 말을 이 고객에게 "
                         "맞는다는 근거로 쓰지 않는다")
        # 답변이 인용한 발송 문구를 화면이 «복사할 문구»로 가를 때 원본으로 쓴다
        # (actions/messages.py). 요건 일치와 무관하다 — 폴백 문구도 답변에 인용되면 같은 꼴로 선다.
        messages.append(item["lms_message"])
        # 발송 화면 제안(act._propose_lms)은 요건에 맞는 것에만 붙는다 — 폴백 문구가 나가면
        # 그 고객과 무관한 문자가 나간다.
        if matched[key]:
            # url 도 들려 보낸다 — 답변이 이름을 바꿔 써도 링크는 원문 그대로라(atomic)
            # 제안 노드가 «이 콘텐츠를 가리켰다»를 링크로 판정할 수 있다(act._mentions).
            lms[key] = {"id": item["id"], "name": item["name"], "message": item["lms_message"],
                        "url": item.get("url") or ""}
        # 다른 후보 — "다른 건 없어?" 에 답할 재료다. 선정된 것은 위에 이미 있으므로 뺀다.
        others = [c for c in (pools.get(key) or []) if c["id"] != item["id"]]
        if others:
            lines.append(f"  다른 {label} 후보 {len(others)}건:")
        for other in others:
            fit = _fit_names(other) if other["id"] in relevant_ids else []
            lines.append(f"  · {other['name']} — {other['schedule']}"
                         + (f" · 요건 일치: {', '.join(fit)}" if fit else ""))

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
               # id 에 KB-PIN 을 넣지 않는다(_customer 와 같은 자리) — 응답의 sources 이벤트로 나간다.
               [{"id": "outreach",
                 "title": "고객님께 안내해보세요 — 열려 있는 이벤트·세미나",
                 "doc": "안내 콘텐츠 레지스트리 (브리핑 ⑨ 와 같은 산출)",
                 "score": None, "page": None}],
               atomic=atomic,
               # 발송 화면 연계(act.py)가 쓰는 문구. 승낙 턴이 문구를 다시 만들지 않도록
               # 이번 턴의 산출을 그대로 들려 보낸다(CLAUDE.md §10 「제안한 턴이 남긴 것으로 정한다」).
               meta={"lms": lms, "messages": messages})

# ═════════════════════════════════════════════════════════════
# targets
# ═════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────
# 오늘의 타겟 고객 — 고객 화면을 열기 «전»의 재료
#
# 브리핑이 고객 하나를 펼치는 자리라면, 이 목록은 그 앞에 놓이는 «오늘 볼 사람들»이다
# (서비스를 열면 가장 먼저 뜨는 화면 — tools/customer_solution/target_list.py).
#
# 대화 쪽에 이 도구가 없던 동안 「오늘 누구부터 봐야 해」·「타겟 목록 쪽지로 보내줘」는
# 재료 0건으로 끝났다. 화면에는 떠 있는데 원장에는 없어서다 — 답은 원장 안에서만 나오므로
# (규칙 2), 화면이 아는 것을 대화가 말하려면 도구가 있어야 한다.
#
# 고객 식별번호(KB-PIN)는 싣지 않는다. 목록에서 사람을 특정하는 데 필요한 것은 이름과
# 순번이고, 실제 조회는 그 고객을 열어서 한다(`customer` 도구는 직원이 이미 연 고객이라
# 싣는다). 원장에 없는 값은 답변에도 쪽지에도 나갈 수 없다 — 경계를 여기서 긋는 것이
# 「쓰지 말라」는 지시보다 확실하다.
# ─────────────────────────────────────────────────────────────

def _targets(state: AgentState, query: str) -> Evidence | None:
    """오늘의 타겟 고객 목록. 선정도 순서도 여기서 만들지 않는다(target_list 그대로)."""
    from pension_agent.common import note  # noqa: PLC0415 — note 가 이 패키지를 임포트하므로 순환을 피한다

    try:
        found = note.today_targets()
    except Exception:
        return None
    day = clock.today().isoformat()
    lines = [f"■ 오늘의 타겟 고객 — {day} 기준 · {len(found)}명"]
    if not found:
        # 0건도 이 도구가 확인한 값이다 — 이 줄이 없으면 «질의가 빗나감»과 구별되지 않는다
        # (_history 의 HISTORY_NONE 과 같은 자리).
        lines.append("· 오늘 사후관리 타겟으로 선정된 고객이 없습니다.")
    for i, t in enumerate(found, 1):
        p = t.profile
        attrs = " · ".join([f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else []))
        lines.append(f"· {i}. {p.nm} — {attrs} · 평가금액 {note.won(p.bal)}")
        # 요건 이름 옆에 «무엇 때문에 걸렸나»의 원장 값을 붙인다. 이름만 적으면 답변도
        # 이름만 옮기게 되고, 그러면 직원은 결국 고객을 하나씩 열어봐야 한다.
        lines += [f"    - {note.cond_text(t, c)}" for c in t.conds]
    text = "\n".join(lines)
    return _ev("targets", query, text,
               [{"id": "targets.today",
                 "title": f"오늘의 타겟 고객 {len(found)}명 ({day})",
                 "doc": "고객 정보 — 계좌 원장 조회값 · 선정 기준은 사후관리 타겟 룰베이스",
                 "score": None, "page": None}],
               allow=[text],
               meta={"count": len(found)})
