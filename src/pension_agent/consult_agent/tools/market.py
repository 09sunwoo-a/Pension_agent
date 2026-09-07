"""시황(market) · 운용 상품(lineup) 도구 — 05_시황_상품_기반지식.

tools 패키지 머리말(`tools/__init__.py`)이 도구 전체의 규약을 말한다.
"""

from __future__ import annotations

from collections.abc import Callable
from pension_agent.consult_agent import kb as KBMOD
from pension_agent.consult_agent.state import KB, AgentState
from pension_agent.consult_agent import tools as _T  # noqa: PLC0415 — 후크는 패키지를 거쳐 부른다(머리말)
from pension_agent.consult_agent.tools.adequacy import _adopt
from pension_agent.consult_agent.tools.base import Evidence, _ev, _scope
from pension_agent.consult_agent.tools.cards import advisory_mark, stale_mark


# ── 시황(market) · 운용 상품(lineup) 기반지식 — 05_시황_상품_기반지식 ──
#
# **둘은 다른 도구다.** 묻는 것이 다르기 때문이다 — market 은 «시장이 어떻게 돌아가나»
# (시황·환율·금리·FOMC), lineup 은 «우리가 뭘 파나»(추천펀드·디폴트옵션 포트폴리오·TDF).
# screen(직원이 단말에서)과 channel(고객이 앱에서)을 같은 원문 표에서 나눈 것과 같은 이유다.
# 하나로 묶여 있던 동안 계획 LLM 은 도구 하나로 둘을 다 받아야 해서 무엇을 부를지 흐렸고,
# 버킷 카탈로그도 시황 문서와 상품 문서를 한 묶음에 세웠다.
#
# 두 도구가 **같은 함수를 쓴다**(_market_like) — 재료의 성격이 같아서다(원문·표 · 기준시점 ·
# 시효 경고). 갈리는 것은 어느 종류를 뒤지는지와 도구 설명뿐이다. 종류마다 렌더·검증을
# 복사하면 한쪽만 고쳐지는 자리가 생긴다.
#
# 「이 자료는 상담 시 근거로 인용할 시장·상품 데이터」라고 폴더가 스스로 규정하고 문서마다
# 검색용 front-matter(trigger_keywords·key_points·as_of)까지 갖춰 저작돼 있었는데, 적재
# 경로가 없어 에이전트에게는 통째로 없는 재료였다(knowledge/CLAUDE.md 적재 감사).
#
# 다른 도구와 갈리는 지점은 **시효**다. 화면번호·제도값과 달리 시황 수치는 주·월 단위로
# 낡는다. 그래서 카드마다 기준시점(as_of)과 원문의 시효 경고(volatile)를 싣고, 그 표시를
# notices 로 강제한다 — 답변에서 빠지면 코드가 채워 넣는다(§9). 붙일지도 문구도 데이터가
# 정한다는 규약은 screen·channel 과 같다(stale_mark).

#: 후보 수. 카드 하나가 표 통째(디폴트옵션 9종 = 약 4천 자)인 경우가 있어 좁게 둔다 —
#: 세 장이면 재료가 1만 자를 넘고, 답이 그 안에서 길을 잃는다.
MARKET_TOP_K = 2


def _render_market(card: dict) -> str:
    # 개요 카드는 제목과 group(문서명)이 같다 — 같은 말을 두 번 세우지 않는다.
    doc_name = card.get("group") if card.get("group") != card.get("title") else None
    scope = " · ".join(x for x in (card.get("category"), doc_name) if x)
    lines = [f"■ {card['title']}" + (f"  ({scope})" if scope else "")]
    if card.get("topic"):
        lines.append(f"· 무엇을 다루나: {card['topic']}")
    lines.append(f"· 기준시점: {card['as_of']}")
    for k in card.get("key_points") or []:
        lines.append(f"· 요점: {k}")
    if card.get("content"):
        lines += ["", card["content"].strip()]
    mark = stale_mark(card)
    if mark:
        lines.append(mark)
    lines.append(f"· 출처 {KBMOD.origin_of(KB, card)}")
    return "\n".join(lines)


def _prefer_sections(hits: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
    """같은 문서의 절이 함께 걸렸으면 그 문서의 **개요 카드는 뺀다.**

    개요 카드는 문서의 front-matter 키워드를 통째로 들고 있어서(주간시황만 24개) 그 문서에
    대한 어떤 질문에나 걸린다. 그런데 답이 든 **표는 절 카드에 있다** — 「지켜드림 금리」의
    답은 디폴트옵션 절에 있고 개요에는 없는데, 둘이 동점이라 개요가 1위로 올라가 후보 두
    자리 중 하나를 먹었다(실측). 개요는 문서의 현관이지 답이 아니다.

    절이 하나도 없으면 개요를 그대로 둔다 — 넓은 질문("요즘 시장 어때")에는 그게 답이다.
    """
    parents = {c.get("parent") for _s, c in hits if c.get("parent")}
    return [(s, c) for s, c in hits if c["id"] not in parents]


def _market_like(kind: str, label: str) -> Callable[[AgentState, str], Evidence | None]:
    """market·lineup 도구 본체. 재료의 성격이 같아 한 함수를 종류만 바꿔 쓴다.

    `fact`(제도 확정값)와 나누는 기준은 **시효**다. 세액공제 한도는 제도가 바뀌기 전까지
    참이고, 이 재료는 다음 회차 자료가 나오면 낡는다 — 그래서 기준시점 표시가 답에 반드시
    따라붙어야 하고(ANSWER_SHAPES), 그 표시는 카드의 선언에서 온다.

    본문은 원문 표·산문이라 atomic 으로 요구하지 않는다. 표를 통째로 원문 강제하면 답변이
    표 덤프가 되고(tools 머리말), 그건 이 재료를 못 쓰게 만드는 것과 같다. 값–조건 오짝은
    `tables` 선언을 relations.table_mispaired() 가 대조해 잡는다.
    """

    def run(state: AgentState, query: str) -> Evidence | None:
        hits = _adopt(state, query, _prefer_sections(
            _T.pick((kind,), query, top_k=MARKET_TOP_K * 3))[:MARKET_TOP_K], label)
        if not hits:
            return None
        notices: list[str] = []
        scopes: list[dict] = []
        for _s, c in hits:
            # 시효 표시(※)와 인용 고지(⚖)는 다른 것을 말한다 — 앞은 «이 수치가 낡을 수
            # 있다», 뒤는 «이건 정보 제공이지 권유가 아니다». 둘 다 카드의 선언에서 온다.
            marks = [m for m in (stale_mark(c), advisory_mark(c)) if m]
            if not marks:
                continue
            notices += [m for m in marks if m not in notices]
            scopes.append(_scope(c["title"], [], marks))
        return _ev(kind, query, "\n\n".join(_render_market(c) for _, c in hits),
                   KBMOD.sources_of(KB, hits), notices=notices, scopes=scopes,
                   cards=[c for _s, c in hits])

    return run
