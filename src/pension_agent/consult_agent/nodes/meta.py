"""메타 질문 응답 노드 `agent_help` — 에이전트 자신에 대한 질문에 답한다.

「뭘 도와줄 수 있어」·「이전 대화 기억해」·「고객한테 문자 직접 보내줄 수 있어」. 재료는 코드가
만든다(`help_material` — 지금 쓸 수 있는 도구 목록·대화 기억의 범위·하지 않는 것) 그리고 LLM 이
그 안에서 **질문에 맞춰** 쓴다(루트 규칙 2 — 경계는 코드, 표현은 LLM).

예전에는 LLM 없이 능력 목록을 템플릿으로 찍었다. 어떤 질문이든 같은 목록이 나가서 「이전 대화
기억해?」에도 「문자 보내줄 수 있어?」에도 능력 표가 답으로 섰고, 문장도 규칙으로 찍어 낸 티가
났다(2026-09-22 행내 실측, 질문 리스트 121~123번).

이름이 `capabilities` 가 아닌 이유는 routing.INTENTS 의 주석 참고 — 07_에이전트_기능정의/01 ② "가능 여부
즉시 확인"(고객 계좌 상태 조회)과 구분하기 위해서다. 이 노드는 '에이전트 자신'이 무엇을 돕는지만 답한다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pension_agent import observability
from pension_agent.consult_agent import progress, tools
from pension_agent.consult_agent.prompts import HELP_PROMPT, HELP_SYSTEM
from pension_agent.consult_agent.state import HISTORY_LIMIT, KB, AgentState, format_history
from pension_agent.llm import LLMError, generate
from pension_agent.verify import verify_texts

#: 에이전트가 **하지 않는 것** — 기준서 §1 「하지 않는 것」·§10 의 결정을 직원이 읽는 말로 옮긴
#: 것이다. 코드가 들고 있는 이유는 LLM 이 능력을 넓혀 말하지 못하게 하기 위해서다(«문자를
#: 보내드릴게요»가 그 사고다). 결정이 바뀌면 여기와 기준서를 함께 고친다.
NOT_DOING = (
    "고객에게 문자(LMS)를 직접 보내지 않아요. 발송 화면을 열고 보낼 문구를 채워 드리는 데까지이고, "
    "보내는 것은 직원이 그 화면에서 해요.",
    "쪽지는 직원 본인 쪽지함으로만 보내요. 그것도 초안을 보여드리고 승낙을 받은 뒤에 보내요.",
    "브리핑 화면의 평가금액·수익률·상품처럼 시스템이 계산한 값은 대화로 바꾸지 않아요. "
    "AI 가 쓴 문장만 고쳐 드려요.",
    "자료에 없는 기준이나 수치는 만들지 않아요. 없으면 없다고 답해요.",
)

#: 재료 종류의 직원 표현. 답의 재료가 무엇인지 한 줄로 말할 때 쓴다 — 카드 장수는 싣지 않는다
#: (직원이 쓸 정보가 아니고, LLM 이 그 숫자를 답에 옮긴다).
_KIND_LABEL = {
    "pitch": "상담 화법", "fact": "제도·상품 수치", "procedure": "업무 처리 절차",
    "screen": "단말 화면번호", "channel": "비대면 채널 경로", "segment": "관리 대상 고객군 정의",
    "method": "관리 방법론", "fieldtip": "영업점 현장 관찰", "market": "시황 자료",
    "lineup": "운용 상품 자료",
}


#: 도구 설명(계획 LLM 용)에 있는 내부 표현 → 직원이 읽는 말. 설명 자체는 계획의 판단 재료라 그대로
#: 두고, 여기 실을 때만 바꾼다. 재료에 있는 말은 답변에 그대로 나온다(§5 「재료에 개발 용어를
#: 쓰지 않는다」) — 「적합성 게이트」가 그렇게 능력 안내 답변에 설 뻔했다.
_STAFF_WORDING = (
    ("적합성 게이트가 허용하는", "이 고객 투자성향으로 안내할 수 있는"),
    ("이전 세션", "이전 상담"), ("지금 진행 중인 세션", "지금 진행 중인 상담"), ("세션", "상담"),
    ("브리핑 화면 ⑥⑦⑧ 과 같은 후보군에서", "브리핑 화면과 같은 자료에서"), ("후보군", "자료"),
    ("**", ""),
)


def _staff_wording(desc: str) -> str:
    """도구 설명 한 줄을 직원이 읽는 말로. 「도구」라는 말이 든 문장·괄호는 통째로 뗀다 —
    그 문장은 계획 LLM 에게 어느 도구를 고를지 말하는 것이라 직원에게는 뜻이 없다."""
    text = desc
    for src, dst in _STAFF_WORDING:
        text = text.replace(src, dst)
    # 다른 도구를 이름으로 가리키는 자리(「… 여기가 아니라 pitch 다」)는 그 도구의 표시 이름으로.
    for name, tool in tools.TOOLS.items():
        text = re.sub(rf"(?<![0-9A-Za-z_]){re.escape(name)}(?![0-9A-Za-z_])",
                      tool.progress or name, text)
    text = re.sub(r"\([^()]*도구[^()]*\)", "", text)
    sentences = [s for s in re.split(r"(?<=[.。])\s+", text) if "도구" not in s]
    return " ".join(s.strip() for s in sentences if s.strip()).strip(" .")


def help_material(state: AgentState) -> str:
    """LLM 이 쓸 «나에 대한 사실». 전부 코드가 아는 값이다 — 지금 턴에 실제로 쓸 수 있는 도구
    (`tools.usable` — 고객 화면이 닫혀 있으면 고객 도구는 빠진다), 대화 기억의 범위, 하지 않는 것.
    못 쓰는 능력을 목록에 세우지 않는다(§3)."""
    usable = tools.usable(state)
    lines = ["■ 지금 도울 수 있는 일 (실제로 쓸 수 있는 것만)"]
    for name in usable:
        tool = tools.TOOLS[name]
        lines.append(f"· {tool.progress or name}: {_staff_wording(tool.desc)}")
    kinds = sorted({c["_kind"] for c in KB.cards}, key=lambda k: list(_KIND_LABEL).index(k)
                   if k in _KIND_LABEL else 99)
    lines += ["", "■ 답의 재료",
              "· 행내 자료를 정리한 카드 — " + " · ".join(_KIND_LABEL.get(k, k) for k in kinds),
              "· 답마다 어느 자료에서 나온 말인지 근거를 함께 보여줘요. 자료에 없는 것은 없다고 답해요."]
    lines += ["", "■ 대화 기억",
              f"· 이번 상담에서 오간 대화는 최근 {HISTORY_LIMIT}턴까지 함께 보고 답해요 — 앞 질문을 "
              "이어받는 후속 질문(「그럼 안 된다고 하면?」)이 돼요.",
              "· 「지금까지 얘기한 거 정리해줘」에는 이번 상담의 대화를 요약해 드려요."]
    if state.get("customer_id"):
        lines.append("· 지금 고객 화면이 열려 있어서, 이 고객과 지난 상담에서 무슨 얘기를 했는지도 "
                     "볼 수 있어요.")
    else:
        lines.append("· 지금은 고객 화면이 열려 있지 않아서, 고객 개별 정보와 그 고객의 지난 상담 "
                     "기록은 볼 수 없어요. 고객 화면을 열면 볼 수 있어요.")
    lines += ["", "■ 하지 않는 것"] + [f"· {x}" for x in NOT_DOING]
    return "\n".join(lines)


def agent_help(state: AgentState) -> dict[str, Any]:
    progress.emit("무엇을 도울 수 있는지 정리하고 있어요")
    material = help_material(state)
    prompt = HELP_PROMPT.format(material=material,
                                history_block=format_history(state.get("history")),
                                question=state["question"])
    try:
        answer = generate(prompt, max_tokens=600, system=HELP_SYSTEM, name="consult.help").strip()
    except LLMError as exc:
        # 규칙으로 대신 답하지 않는다(§11) — 다른 노드와 같은 안내로 끝낸다.
        from pension_agent.consult_agent.nodes.plan import LLM_FAILED, short_reason  # noqa: PLC0415
        observability.step("agent_help", error=f"{type(exc).__name__}: {exc}", level=logging.WARNING)
        return {"answer": LLM_FAILED.format(reason=short_reason(f"{type(exc).__name__}: {exc}")),
                "llm_error": f"{type(exc).__name__}: {exc}", "sources": []}

    # 재료 밖 수치(턴 수·건수)를 지어냈으면 재료를 그대로 낸다 — 재료는 코드가 쓴 사실 목록이라
    # 답변으로 읽혀도 틀린 말이 없다. 화법 답변의 근거 원문 폴백과 같은 자리다(§6).
    ok, faults = verify_texts(answer, [material], echoable=[state["question"]])
    if not ok or not answer:
        observability.step("agent_help", passed=False, reason="; ".join(faults[:2]),
                           level=logging.WARNING)
        answer = material
    else:
        observability.step("agent_help", passed=True, draft=f"{len(answer)}자")
    return {"answer": answer, "sources": []}
