"""메타 질문 노드 agent_help — 에이전트 자신에 대한 질문(능력·기억·발송 여부)에 응답.

재료(help_material)는 코드, 질문에 맞춘 표현은 LLM. 고객 계좌 가능 여부 확인과는 별개.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pension_agent.common import observability
from pension_agent.common import progress
from pension_agent import tools
from pension_agent.prompts import HELP_PROMPT, HELP_SYSTEM
from pension_agent.state import HISTORY_LIMIT, KB, AgentState, format_history
from pension_agent.common.llm import LLMError, generate
from pension_agent.evidence.verify import verify_texts

#: 에이전트가 하지 않는 것 — LLM 이 능력을 넓혀 말하지 못하게 코드가 보유.
NOT_DOING = (
    "고객에게 문자(LMS)를 직접 보내지 않아요. 발송 화면을 열고 보낼 문구를 채워 드리는 데까지이고, "
    "보내는 것은 직원이 그 화면에서 해요.",
    "쪽지는 직원 본인 쪽지함으로만 보내요. 그것도 초안을 보여드리고 승낙을 받은 뒤에 보내요.",
    "브리핑 화면의 평가금액·수익률·상품처럼 시스템이 계산한 값은 대화로 바꾸지 않아요. "
    "AI 가 쓴 문장만 고쳐 드려요.",
    "자료에 없는 기준이나 수치는 만들지 않아요. 없으면 없다고 답해요.",
)

#: 재료 종류의 직원 표현. 카드 장수 미포함 — LLM 이 답에 옮김.
_KIND_LABEL = {
    "pitch": "상담 화법", "fact": "제도·상품 수치", "procedure": "업무 처리 절차",
    "screen": "단말 화면번호", "channel": "비대면 채널 경로", "segment": "관리 대상 고객군 정의",
    "method": "관리 방법론", "fieldtip": "영업점 현장 관찰", "market": "시황 자료",
    "lineup": "운용 상품 자료",
}


#: 도구 설명의 내부 표현 → 직원 표현. 재료의 개발 용어는 답변에 그대로 노출됨.
_STAFF_WORDING = (
    ("적합성 게이트가 허용하는", "이 고객 투자성향으로 안내할 수 있는"),
    ("이전 세션", "이전 상담"), ("지금 진행 중인 세션", "지금 진행 중인 상담"), ("세션", "상담"),
    ("브리핑 화면 ⑥⑦⑧ 과 같은 후보군에서", "브리핑 화면과 같은 자료에서"), ("후보군", "자료"),
    ("**", ""),
)


def _staff_wording(desc: str) -> str:
    """도구 설명 한 줄을 직원 표현으로. 「도구」가 든 문장·괄호는 제거(계획 LLM 용)."""
    text = desc
    for src, dst in _STAFF_WORDING:
        text = text.replace(src, dst)
    # 다른 도구 이름 → 표시 이름
    for name, tool in tools.TOOLS.items():
        text = re.sub(rf"(?<![0-9A-Za-z_]){re.escape(name)}(?![0-9A-Za-z_])",
                      tool.progress or name, text)
    text = re.sub(r"\([^()]*도구[^()]*\)", "", text)
    sentences = [s for s in re.split(r"(?<=[.。])\s+", text) if "도구" not in s]
    return " ".join(s.strip() for s in sentences if s.strip()).strip(" .")


def help_material(state: AgentState) -> str:
    """LLM 이 쓸 «나에 대한 사실» — 이번 턴 사용 가능 도구·대화 기억 범위·하지 않는 것."""
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
        # 규칙 응답 대체 금지 — LLM 장애 안내로 종료
        from pension_agent.nodes.plan import LLM_FAILED, short_reason  # noqa: PLC0415
        observability.step("agent_help", error=f"{type(exc).__name__}: {exc}", level=logging.WARNING)
        return {"answer": LLM_FAILED.format(reason=short_reason(f"{type(exc).__name__}: {exc}")),
                "llm_error": f"{type(exc).__name__}: {exc}", "sources": []}

    # 재료 밖 수치 발견 시 재료(코드가 쓴 사실 목록)로 폴백
    ok, faults = verify_texts(answer, [material], echoable=[state["question"]])
    if not ok or not answer:
        observability.step("agent_help", passed=False, reason="; ".join(faults[:2]),
                           level=logging.WARNING)
        answer = material
    else:
        observability.step("agent_help", passed=True, draft=f"{len(answer)}자")
    return {"answer": answer, "sources": []}
