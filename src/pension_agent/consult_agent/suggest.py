"""추천 질문 칩 — 고객 화면을 열었을 때 에이전트가 먼저 내미는 질문.

직원은 "이 고객과 지난 상담이 있었다"는 사실 자체를 모르면 물어볼 생각도 못 한다.
그래서 기록이 **있는 고객에 한해** 그 사실(날짜·경과일)을 담은 질문을 칩으로 띄운다 —
읽는 순간 정보 전달이 끝나고, 누르면 계획 루프가 history 도구로 깊이 들어간다.

문구는 전부 **코드가 조립한다**(고정 템플릿 + 계산값). LLM 이 칩을 쓰면 기록에 없는
내용이 질문에 실려 들어오고, 직원이 누르는 순간 그 문장이 원장의 출발점이 된다.
템플릿에는 지어낼 자리가 없다 — 날짜와 경과일은 세션 저장소·clock.today() 에서 온
계산값이다.

기록이 없으면 빈 리스트다. 항상 뜨는 칩은 배경이 되어 아무도 읽지 않는다
(nodes/act.py 가 매 턴 제안을 하지 않는 것과 같은 이유). 고객마다 다르게 뜨는 것
자체가 "상황을 읽고 있다"는 표시다.
"""

from __future__ import annotations

import re
from datetime import date


def history_chips(customer_id: str | None) -> list[str]:
    """고객의 과거 상담(record)이 있으면 그 기반 추천 질문을 돌려준다. 없으면 [].

    최신 record 세션 하나만 본다 — 칩은 입구이지 목록이 아니고, 깊이는 history 도구가
    담당한다. 에이전트와 나눈 대화 세션(user/agent)은 세지 않는다: 방금 한 대화를
    "지난 상담"이라 부르면 직원이 오독한다.
    """
    if not customer_id:
        return []
    from pension_agent import session_store  # noqa: PLC0415
    try:
        sessions = session_store.list_sessions(customer_id)
    except Exception:
        return []

    latest: date | None = None
    for s in sessions:
        if not any(t.get("role") == "record" for t in (s.get("turns") or [])):
            continue
        try:
            d = date.fromisoformat((s.get("started_at") or "")[:10])
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    if latest is None:
        return []

    # 경과일은 브리핑의 다른 경과일(미접촉 일수 등)과 같은 축이어야 화면에서 어긋나지
    # 않는다 — 그 축은 원장 스냅샷이 아니라 오늘이다(customer.py 의 두 시간축 주석).
    from pension_agent.clock import today  # noqa: PLC0415
    elapsed = (today() - latest).days
    when = f"{latest.month}/{latest.day}"
    return [
        f"지난 상담({when} · {elapsed}일 전)에서 무슨 얘기 했지?",
        "지난 상담 내용 참고해서 오늘 뭐라고 말하면 좋을까?",
    ]


def outreach_chips(customer_id: str | None) -> list[str]:
    """이 고객 상태에 걸린 세미나·이벤트가 열려 있으면 그 사실을 담은 질문을 돌려준다.

    히스토리 칩과 같은 자세다 — 직원은 «지금 이 고객에게 맞는 세미나가 열려 있다»는 사실
    자체를 모르면 물어볼 생각도 못 한다. 문구가 곧 알림이고, 누르면 계획 루프가 `outreach`
    도구로 들어간다.

    **관련도 0 인 폴백은 세지 않는다.** 화면 ⑨ 는 섹션을 비우지 않으려고 이 고객과 무관해도
    임박 순으로 한 건을 세우는데(그건 화면 요건이다), 그걸 «있다»로 세면 이 칩이 어느
    고객에게나 떠서 배경이 된다. 문구는 전부 코드 조립이다 — 건수와 종류는 계산값이고,
    콘텐츠 이름은 DB 원문 그대로다.
    """
    if not customer_id:
        return []
    try:
        from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
        from pension_agent.strategy_agent import situations as strategy_situations  # noqa: PLC0415
        from pension_agent.strategy_agent import support  # noqa: PLC0415
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return []
        rows = support.relevant_outreach(strategy_situations.problem_situations(profile))
    except Exception:
        return []
    if not rows:
        return []
    kinds = [k for k in ("이벤트", "세미나")
             if any(r["content_type"] == k for r in rows)]
    # 건수는 싣지 않는다 — 칩은 직원이 누르는 순간 직원의 질문이 되는데, 직원은 콘텐츠가
    # 몇 건 열려 있는지 모르는 사람이다. 「2건 있어」는 직원이 알 수 없는 사실을 직원이
    # 단정하는 꼴이라 어색하다. 알림은 **칩이 떴다는 사실**이 한다(조건이 맞는 고객에게만
    # 뜬다 — 이 모듈 머리말). 종류만 실어 «무엇을 묻는 칩인가»가 보이게 한다.
    return [f"이 고객한테 안내할 만한 {'나 '.join(kinds)} 있어?"]


# ─────────────────────────────────────────────────────────────
# 답변 끝 추천질문 — "이어서 물어보실 수 있어요"
#
# 답을 읽은 직원이 다음에 무엇을 물으면 되는지를 답변 끝에 몇 줄로 내민다. 위 히스토리
# 칩이 «고객 화면을 열었을 때»의 입구라면, 이쪽은 «답을 다 읽은 뒤»의 입구다.
#
# ━━ 이 기능의 알맹이는 문구가 아니라 재료 존재 확인이다 ━━
# 추천질문을 눌렀는데 "근거를 찾지 못했습니다"가 나오면 안 띄우느니만 못하다. 그래서
# 후보마다 **그 질문에 답할 재료가 실제로 있는지 코드가 먼저 찾아본다**(_has_material).
# 없으면 그 칩은 안 뜬다 — 재료가 없는 요건에 아무것도 띄우지 않는 것과 같은 자세다
# (루트 「판단의 근본」 · CLAUDE.md §8).
#
# 이 확인이 가능한 것은 후보가 «어느 도구가 답할 질문인지»를 함께 들고 있기 때문이다.
# LLM 이 자유 문장으로 질문을 지어내면 어느 도구로 가야 할 질문인지조차 코드가 모르고,
# 그러면 답할 수 있는지도 확인할 수 없다.
#
# **한계를 적어둔다**: 검색 히트 ≥1 은 답이 나올 «필요조건»이지 충분조건이 아니다.
# 실제로 누르면 적합성 게이트(tools._adopt — LLM)가 한 번 더 거르므로, 통과한 칩도
# 드물게 근거 없음으로 끝날 수 있다. 여기서 걸러지는 것은 **확실히 죽는 질문**이다.
#
# ━━ 매 턴 붙지 않는다 ━━
# 되묻기 턴·확인 대기 턴·LLM 실패 턴·근거 0건 턴에는 아무것도 안 붙는다. 항상 붙는
# 것은 배경이 되어 아무도 읽지 않는다(nodes/act.py 가 매 턴 화면 연계를 제안하지 않는
# 것과 같은 이유).
# ─────────────────────────────────────────────────────────────

#: 한 답변에 붙이는 추천질문 수의 상한. 3개를 억지로 채우지 않는다 — 재료가 있는 것만
#: 남기고 나면 0개일 수도 있고, 그게 이 설계의 요점이다.
MAX_FOLLOWUPS = 3

#: `{topic}` 슬롯에 넣을 근거 카드 제목의 길이 상한. 긴 제목이 문장에 박히면 질문이
#: 아니라 인용문이 된다 — 넘으면 슬롯 없는 변형으로 물러선다.
TOPIC_MAX = 20

#: 이번 턴에 쓴 재료 → [(문구 변형들, 이 질문에 답할 도구)]. 문구는 코드가 조립하고,
#: `{topic}` 은 그 재료의 근거 카드 제목이다(지어낼 자리가 없다).
#:
#: **한 재료에 여러 방향을 단다.** 「세액공제 한도 얼마야?」의 다음 질문은 화법만이
#: 아니다 — 어떻게 처리하는지도, 고객이 직접 할 수 있는지도 자연스러운 다음 걸음이다.
#: 방향이 하나뿐이면 그 재료로 답한 턴은 추천질문이 최대 한 개다(필터가 깎은 것이 아니라
#: 만들어진 적이 없는 것이다).
#:
#: 변형이 여럿인 것은 **같은 문구가 매번 뜨면 읽지 않게 되기 때문**이다. 다양성은 세
#: 축에서 나온다 — ① 이번 턴 원장의 재료 조합(계획이 다른 도구를 부르면 다른 칩) ②
#: 아래 변형 회전(대화가 이어지면 문구가 바뀐다) ③ 카드 제목 슬롯.
#:
#: 세 번째 칸은 **재료 확인어**다. 대부분의 칩은 «이 답의 주제»를 되짚는 질문이라
#: ("이건 어떻게 처리해?" · "여기 걸리는 한도가 뭐야?") 확인도 그 주제로 해야 한다 —
#: 빈 칸이 그 뜻이고, 카드 제목으로 확인한다. 주제와 무관하게 성립하는 자기완결형
#: 질문만("요즘 시장 상황은 어때?") 자기 확인어를 갖는다. 여기에 일반 확인어를 달면
#: 그 칩이 **항상** 통과해, 이 답과 상관없는 카드로 안내하게 된다.
_NEXT: dict[str, tuple[tuple[tuple[str, ...], str, str], ...]] = {
    "fact": ((("「{topic}」 이 내용, 고객에게는 뭐라고 설명하면 좋을까?",
               "이 수치로 고객을 설득하려면 뭐라고 말하지?",
               "고객이 이 부분을 반문하면 어떻게 답하지?"), "pitch", ""),
             (("이건 실제로 어떻게 처리해?",
               "「{topic}」 관련 업무 처리 순서가 어떻게 돼?",
               "이거 처리할 때 걸리는 주의사항이 있어?"), "procedure", ""),
             (("고객이 앱에서 직접 할 수 있어?",
               "이건 비대면으로도 되나?",
               "고객이 스타뱅킹에서 직접 하는 경로가 있어?"), "channel", "")),
    # pitch → pitch 는 «이미 쓴 재료» 제외의 유일한 예외다. 화법의 다음 질문은 거의
    # 항상 «그래도 안 된다고 하면»이고, 그건 같은 재료에서 나오는 다른 카드다.
    "pitch": ((("고객이 그래도 망설이면 뭐라고 답하지?",
                "고객이 싫다고 하면 어떻게 대응하지?",
                "고객이 타행이랑 비교하면 뭐라고 하지?"), "pitch", ""),
              (("여기서 근거로 댈 수 있는 수치가 뭐가 있어?",
                "「{topic}」 관련 한도나 요건이 어떻게 돼?",
                "이 얘기에 붙일 확정된 수치가 있어?"), "fact", ""),
              (("고객이 하겠다고 하면 어떻게 처리해?",
                "이거 진행하려면 절차가 어떻게 돼?",
                "실제 처리 순서를 알려줘"), "procedure", "")),
    "procedure": ((("고객이 앱에서 직접 하려면 어떻게 안내하지?",
                    "이건 고객이 비대면으로도 할 수 있어?",
                    "고객이 스타뱅킹에서 직접 하는 경로가 있어?"), "channel", ""),
                  (("이 업무는 단말 어느 화면에서 처리해?",
                    "관련 화면번호가 뭐야?",
                    "이 처리는 어느 화면에서 조회해?"), "screen", ""),
                  (("여기 걸리는 한도나 요건이 뭐야?",
                    "「{topic}」 관련 확정된 수치가 있어?",
                    "이 절차에 붙는 기준값이 뭐가 있어?"), "fact", "")),
    "screen": ((("이 화면에서 처리하는 절차가 어떻게 돼?",
                 "이 업무 처리 순서를 알려줘",
                 "이 화면 쓰기 전에 확인할 게 있어?"), "procedure", ""),
               (("고객이 직접 하는 경로도 있어?",
                 "이건 고객이 앱에서도 할 수 있나?",
                 "비대면 채널로도 되는 업무야?"), "channel", "")),
    "channel": ((("직원이 단말에서 처리하려면 어느 화면이야?",
                  "이건 창구에서 처리하면 화면번호가 뭐야?",
                  "직원이 대신 처리할 때는 어느 화면을 봐?"), "screen", ""),
                (("창구에서 처리하는 절차는 어떻게 돼?",
                  "직원이 처리할 때 순서가 어떻게 되지?",
                  "이 업무 처리 순서를 알려줘"), "procedure", "")),
    "segment": ((("「{topic}」 이 고객군한테는 뭐라고 말을 꺼내지?",
                  "이런 고객에게는 어떻게 접근하면 좋을까?",
                  "이 고객군 상담은 어떻게 시작하지?"), "pitch", ""),
                (("이런 고객은 어떤 기준으로 관리해?",
                  "「{topic}」 이 고객군은 뭐부터 챙겨야 해?",
                  "이 고객군 관리 방법론이 뭐야?"), "method", ""),
                (("이 고객도 여기 해당해?",
                  "지금 열려 있는 고객은 어느 요건에 걸렸어?",
                  "이 고객이 이 고객군에 들어가?"), "customer", "")),
    "method": ((("이 기준을 고객한테는 어떻게 설명하지?",
                 "이 판단을 고객에게 말할 때 뭐라고 하지?",
                 "고객이 왜 그러냐고 물으면 뭐라고 답하지?"), "pitch", ""),
               (("이 기준에 걸리는 고객군이 뭐야?",
                 "「{topic}」 이건 어떤 고객을 대상으로 해?",
                 "이 관리 대상은 어떻게 선정해?"), "segment", "")),
    "fieldtip": ((("이 상황에서 고객에게 할 말은 뭐가 좋을까?",
                   "이럴 때 어떻게 말을 꺼내지?",
                   "이 경우 고객 응대를 어떻게 하지?"), "pitch", ""),
                 (("이건 실제로 어떻게 처리해?",
                   "관련 업무 절차가 어떻게 돼?",
                   "처리 순서를 알려줘"), "procedure", "")),
    "market": ((("지금 우리 운용 상품 라인업은 뭐가 있어?",
                 "이달의 추천 상품은 뭐야?",
                 "디폴트옵션 포트폴리오 구성은 어떻게 돼?"), "lineup", "추천펀드 디폴트옵션"),
               (("이 시장 상황을 고객에게 어떻게 설명하지?",
                 "고객이 시장 불안해하면 뭐라고 말하지?",
                 "요즘 시황을 고객에게 뭐라고 전하지?"), "pitch", ""),
               (("이 고객한테는 이 중에 뭐가 가능해?",
                 "지금 이 고객에게 안내할 수 있는 범위가 어디까지야?",
                 "이 고객 성향으로 가능한 상품이 뭐야?"), "suitable", "")),
    "lineup": ((("요즘 시장 상황은 어때?",
                 "지금 금리·환율 흐름이 어떻게 돼?",
                 "최근 투자전략 자료에 뭐라고 나와 있어?"), "market", "시황 금리 환율"),
               (("「{topic}」 이걸 고객에게 어떻게 안내하지?",
                 "이 상품을 고객에게 어떻게 설명하지?",
                 "고객이 상품 물어보면 뭐라고 말하지?"), "pitch", ""),
               (("이 고객한테는 이 중에 뭐가 가능해?",
                 "이 고객 성향으로 안내할 수 있는 게 뭐야?",
                 "지금 고객에게 가능한 범위가 어디까지야?"), "suitable", "")),
    "suitable": ((("이 범위를 고객에게 어떻게 안내하면 좋을까?",
                   "적합성 범위를 고객에게 뭐라고 설명하지?",
                   "고객이 다른 상품 원하면 뭐라고 답하지?"), "pitch", ""),
                 (("우리 라인업 전체는 뭐가 있어?",
                   "이달의 추천 상품은 뭐야?",
                   "디폴트옵션 포트폴리오는 어떻게 구성돼 있어?"), "lineup", "추천펀드 디폴트옵션"),
                 (("이 고객 현황이 어떻게 돼?",
                   "이 고객 평가금액이랑 보유 상품이 뭐야?",
                   "이 고객 왜 관리 대상이야?"), "customer", ""),
                 (("이 고객한테 안내할 만한 세미나나 이벤트 있어?",
                   "이 고객에게 보낼 만한 안내 콘텐츠가 뭐가 있어?",
                   "이 고객한테 지금 안내할 이벤트가 있어?"), "outreach", "")),
    "customer": ((("이 고객한테 안내할 수 있는 상품 범위는 뭐야?",
                   "이 고객 투자성향으로 어디까지 안내할 수 있어?",
                   "이 고객에게 가능한 상품이 뭐가 있어?"), "suitable", ""),
                 # 안내 콘텐츠 칩은 **이 고객 상태에 걸린 세미나·이벤트가 실제로 열려 있을
                 # 때만** 뜬다(_has_material 의 outreach 분기 — 관련도 0 인 폴백은 세지
                 # 않는다). 직원은 지금 어떤 세미나가 열려 있는지 모르면 물어볼 생각도
                 # 못 한다 — 히스토리 칩과 같은 이유로, 칩 문구 자체가 알림이다.
                 (("이 고객한테 안내할 만한 세미나나 이벤트 있어?",
                   "이 고객에게 보낼 만한 안내 콘텐츠가 뭐가 있어?",
                   "이 고객한테 지금 안내할 이벤트가 있어?"), "outreach", ""),
                 (("이 고객과 지난 상담에서 무슨 얘기 했지?",
                   "이 고객 상담 이력 있어?",
                   "이 고객과 전에 나눈 얘기가 뭐야?"), "history", ""),
                 (("이 고객 왜 관리 대상이야?",
                   "이 고객이 걸린 고객군이 뭐야?",
                   "이 고객군은 왜 관리해야 해?"), "segment", ""),
                 (("이 고객에게 어떻게 말을 꺼내지?",
                   "이 고객한테 뭐라고 안내하면 좋을까?",
                   "이 고객 상담을 어떻게 시작하지?"), "pitch", "")),
    "history": ((("지난 상담 내용 참고해서 오늘 뭐라고 말하면 좋을까?",
                  "지난번 얘기를 이어서 어떻게 말을 꺼내지?",
                  "그때 얘기를 다시 꺼내려면 뭐라고 하지?"), "pitch", ""),
                # 지난 상담의 다음 걸음은 «다시 접점을 만드는 것»이다 — 열려 있는 세미나·
                # 이벤트가 그 접점이고, 이 고객에게 걸린 것이 있을 때만 뜬다.
                (("이 고객한테 안내할 만한 세미나나 이벤트 있어?",
                  "이어서 안내할 만한 콘텐츠가 뭐가 있어?",
                  "이 고객에게 보낼 만한 이벤트가 있어?"), "outreach", ""),
                (("이 고객 지금 현황은 어때?",
                  "그 사이에 이 고객 상황이 어떻게 바뀌었어?",
                  "이 고객 지금 평가금액이 얼마야?"), "customer", "")),
    # 안내 콘텐츠를 본 다음 걸음은 «어떻게 말을 얹을까»와 «그 김에 뭘 더 권할까»다.
    # 발송 자체는 여기 없다 — 답변이 콘텐츠를 가리키면 발송 화면 연계 제안이 붙고
    # (nodes/act.py::_propose_lms), 확인 대기 턴에는 추천질문이 아예 안 붙는다.
    "outreach": ((("이 콘텐츠를 고객에게 어떻게 안내하지?",
                   "고객에게 이 세미나를 뭐라고 소개하지?",
                   "고객이 관심 없다고 하면 뭐라고 답하지?"), "pitch", ""),
                 (("이 고객한테 안내할 수 있는 상품 범위는 뭐야?",
                   "이 고객 성향으로 안내할 수 있는 게 뭐야?",
                   "이 고객에게 가능한 상품이 뭐가 있어?"), "suitable", "")),
    # date 는 없다 — 「오늘 며칠이야」에서 이어질 자연스러운 다음 질문이 없다.
}

#: 같은 도구로 이끄는 칩을 몇 개까지 두나. 하나로 조이면 «고객에게 어떻게 설명하지»와
#: «고객이 반문하면 뭐라고 답하지» 처럼 직원에게는 다른 질문이 도구가 같다는 이유로
#: 하나 빠진다. 둘을 넘기면 한 방향으로 쏠려 다음 걸음이 한 갈래로만 보인다.
MAX_PER_LEAD = 2

#: 지식베이스를 n-gram 으로 뒤져 존재를 확인하는 재료. `pick()` 의 LLM 1차는 건너뛰고
#: 폴백 경로(retrieve)만 쓴다 — 칩 하나 띄우자고 LLM 을 부르지 않는다.
_KB_KINDS = frozenset({"pitch", "procedure", "screen", "channel", "segment",
                       "method", "fieldtip", "market", "lineup"})

#: 검색이 아니라 원장 조회로 존재를 확인하는 재료. 이 도구들은 `_adopt`(LLM)를 거치지
#: 않으므로 그대로 불러 보는 것이 **정확한** 판정이다 — 실제 클릭 경로와 같은 계산이다.
_LEDGER_TOOLS = frozenset({"history", "suitable"})


#: 질문이 같은지 볼 때 무시하는 것 — 공백과 문장부호. "이 고객 왜 관리 대상이야?" 와
#: "이 고객 왜 관리대상이야" 는 직원에게 같은 질문이고, 한쪽만 걸러지면 표기 차이 하나로
#: 방금 답한 질문이 다시 추천된다.
_TRIVIAL = re.compile(r"[\s?!.,·]+")


def _norm(question: str | None) -> str:
    """질문 비교용 정규형. 표기 차이를 지우고 «같은 질문인가»만 남긴다."""
    return _TRIVIAL.sub("", question or "")


def _topic(found: dict) -> str:
    """이 재료의 근거 카드 제목 — `{topic}` 슬롯에 들어갈 값. 없으면 빈 문자열."""
    for source in found.get("sources") or []:
        title = (source.get("title") or "").strip()
        if title and len(title) <= TOPIC_MAX:
            return title
    return ""


def _phrase(variants: tuple[str, ...], topic: str, turn: int) -> str:
    """변형 중 하나를 고른다 — 회전은 대화 턴 수로 한다(결정론. 난수·시각을 쓰지 않는다).

    슬롯이 든 변형은 제목이 있을 때만 쓸 수 있다. 없으면 회전 순서대로 다음 변형으로
    물러선다 — 슬롯을 빈칸으로 두면 「「」 이 내용」 같은 문장이 나간다.
    """
    for i in range(len(variants)):
        text = variants[(turn + i) % len(variants)]
        if "{topic}" not in text:
            return text
        if topic:
            return text.format(topic=topic)
    return ""


def _has_material(lead: str, probes: tuple[str, ...], state: dict) -> bool:
    """이 질문에 답할 재료가 실제로 있나 — **LLM 을 부르지 않고** 확인한다.

    이 함수가 이 기능의 알맹이다(위 머리말). 재료가 없는 질문을 띄우면 직원이 눌렀을 때
    '근거 없음'이 나오고, 그건 안 띄우느니만 못하다.

    `probes` 는 찾아볼 말들이고 **하나라도 걸리면 재료가 있다**고 본다. 둘인 이유는
    n-gram 이 문장을 통째로 받으면 주제어가 묻히기 때문이다 — "고객이 그래도 망설이면
    뭐라고 답하지?"는 화법 카드 102장 중 어느 것과도 안 걸리지만(서술어뿐이다) 그 칩의
    주제인 「수수료 부담 반론」으로는 걸린다. 실제로 누르면 계획이 이번 대화 맥락에서
    질의를 만들므로, 서술어만으로 재본 결과를 «없음»으로 굳히면 있는 자료를 못 띄운다.
    """
    from pension_agent.consult_agent import kb as KBMOD  # noqa: PLC0415
    from pension_agent.consult_agent import tools  # noqa: PLC0415
    from pension_agent.consult_agent.nodes import facts_qa  # noqa: PLC0415
    from pension_agent.consult_agent.state import KB  # noqa: PLC0415

    try:
        if lead == "fact":
            return any(facts_qa.search(q) for q in probes)
        if lead in _KB_KINDS:
            return any(KBMOD.retrieve(KB, top_k=1, kinds=[lead], utterance=q) for q in probes)
        if lead == "customer":
            # `customer` 도구 자체는 부르지 않는다 — 그 안의 strategy_agent.propose 가
            # LLM 을 쓴다. 재료의 유무를 가르는 것은 프로파일 존재이므로 그것만 본다.
            from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
            return strategy_customer.get_profile(state.get("customer_id") or "") is not None
        if lead == "outreach":
            # 도구를 그대로 부르지 않는다 — 그 안의 strategy_agent.propose() 가 LLM 을
            # 쓴다(`customer` 를 프로파일 존재로만 재는 것과 같은 이유). 여기서 가르는 것은
            # «이 고객 상태에 걸린 콘텐츠가 열려 있는가»이고, 그 판정은 규칙이라 LLM 이
            # 필요 없다. **관련도 0 인 폴백은 세지 않는다** — 화면 ⑨ 는 섹션을 비우지 않으려
            # 임박 순으로 한 건을 세우지만, 그걸 «있다»로 세면 칩이 어느 고객에게나 뜨고
            # 그러면 배경이 되어 아무도 읽지 않는다(이 모듈 머리말).
            from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
            from pension_agent.strategy_agent import situations as strategy_situations  # noqa: PLC0415
            from pension_agent.strategy_agent import support  # noqa: PLC0415
            profile = strategy_customer.get_profile(state.get("customer_id") or "")
            return profile is not None and bool(
                support.relevant_outreach(strategy_situations.problem_situations(profile)))
        if lead in _LEDGER_TOOLS:
            return any(tools.TOOLS[lead].run(state, q) is not None for q in probes)
    except Exception:
        return False
    return False


def followup_questions(out: dict) -> list[str]:
    """답변 끝에 붙일 추천질문. 조건이 아니면 [].

    `out` 은 그래프의 최종 상태(graph.ask 가 invoke 로 받은 것)다.
    """
    # 붙이지 않는 턴 넷. 전부 «다음 질문»을 내밀 자리가 아니다.
    #   clarify        되묻기 턴 — 갈래를 고르는 자리다(§5)
    #   pending_action "네/아니오"를 기다리는 자리 — 확인 절차가 흐려진다(§10)
    #   llm_error      실패 안내에 추천질문이 붙으면 정상 답변처럼 보인다(§11)
    #   evidence 0건   근거 없음·메타·확인·수정 턴 — 읽어낼 상황 자체가 없다
    if out.get("clarify") or out.get("pending_action") or out.get("llm_error"):
        return []
    evidence = list(out.get("evidence") or [])
    if not evidence:
        return []

    from pension_agent.consult_agent import tools  # noqa: PLC0415

    state = {"customer_id": out.get("customer_id"), "question": out.get("question") or ""}
    # 부를 수 있는 도구가 곧 답할 수 있는 것이다 — 고객 화면이 닫혀 있으면 고객 전제
    # 도구로 이끄는 칩은 애초에 후보가 아니다. 판정은 tools 한 곳에서 온다(§3).
    usable = set(tools.usable(state))
    used = {e["tool"] for e in evidence}
    history = out.get("history") or []
    # **이번 질문도 «이미 물은 것»이다.** history 는 이 턴에 들어온 이력이고 이번 질문은
    # ask() 가 invoke 를 마친 뒤에야 거기 붙는다 — 그래서 이 줄이 없으면 방금 물은 것이
    # 그대로 추천으로 되돌아온다("이 고객 왜 관리 대상이야?" 를 묻고 답을 다 읽었는데
    # 맨 아래에 같은 질문이 다시 서 있었다). 첫 턴이면 history 가 비어 있어 더 그렇다.
    #
    # 「이미 쓴 재료」 제외가 이걸 못 잡는다: 그 답은 customer 재료로 나왔는데 그 질문은
    # segment 로 이끄는 후보라, 재료 축으로는 겹치지 않는다. 겹치는 것은 **질문**이다.
    asked = {_norm(t.get("question")) for t in history} | {_norm(out.get("question"))}
    asked.discard("")

    picked: list[str] = []
    seen: set[str] = set()          # 이미 고른 문구(정규형) — 다른 재료가 같은 질문을 낸다
    per_lead: dict[str, int] = {}   # 도구별 개수 — MAX_PER_LEAD 까지
    for found in evidence:
        if len(picked) >= MAX_FOLLOWUPS:
            break
        # **재료 자체가 이번 턴에 쓸 수 없으면 그 재료의 후속질문도 성립하지 않는다.**
        # customer 재료의 후속은 전부 "이 고객 ~" 인데, 고객 화면이 닫힌 채로 그 문구가
        # 뜨면 어느 고객인지 없는 질문이 된다(§3). 판정은 여기서도 tools.usable 이 한다.
        if found["tool"] not in usable:
            continue
        topic = _topic(found)
        for variants, lead, probe in _NEXT.get(found["tool"], ()):
            if len(picked) >= MAX_FOLLOWUPS:
                break
            if per_lead.get(lead, 0) >= MAX_PER_LEAD or lead not in usable:
                continue
            # 이번 턴에 이미 쓴 재료로 다시 보내지 않는다 — 방금 답한 것을 또 묻게 된다.
            # pitch → pitch(반론 후속)만 예외다: 같은 재료의 **다른 카드**가 답한다.
            if lead in used and not (lead == "pitch" and found["tool"] == "pitch"):
                continue
            question = _phrase(variants, topic, len(history))
            if not question or _norm(question) in asked or _norm(question) in seen:
                continue
            # 이 칩을 눌렀을 때 답이 나올 재료가 실제로 있나 — 없으면 안 띄운다.
            #
            # 찾아볼 말의 순서가 중요하다. n-gram 유사도는 **질의가 길수록 희석되어**
            # (kb._sim) 자연스러운 문장은 문턱(MIN_TOPICAL) 아래로 떨어진다 — "시황"은
            # 걸리는 시황 카드가 "요즘 시장 상황은 어때?" 로는 0건이 된다. 실제 클릭
            # 경로는 계획이 만든 짧은 질의를 쓰므로, 문장으로만 재보면 **있는 자료를
            # 없다고 판정**한다. 그래서 짧은 말(카드 제목·확인어)을 먼저 대본다.
            probes = tuple(dict.fromkeys(x for x in (topic, probe, question) if x))
            if not _has_material(lead, probes, state):
                continue
            picked.append(question)
            seen.add(_norm(question))
            per_lead[lead] = per_lead.get(lead, 0) + 1
    return picked
