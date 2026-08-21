"""AI 브리핑 화면 ①~⑨ 섹션 레지스트리 — 제목과 생성 주체를 한 곳에서 관리한다.

docs/REQUIREMENTS.md §2(섹션 정의 · §15 생성 주체)를 코드 상수로 옮긴 것이다. 화면을 그리는
세 곳(agent.py CLI · app.py Streamlit · bff/main.py → React BriefingPanel.jsx)이 전부 여기서
제목을 읽는다.

이 모듈이 생긴 이유는 실제로 어긋났기 때문이다 — 같은 섹션 제목이 세 파일에 각각 하드코딩돼
있었고, agent.py 만 원문자 없이 "[상담 화법]"·"[예상 반론 및 대응]" 으로 흘러 있었다. 제목은
화면의 사실이므로 한 곳에서만 정한다.

`key` 는 engine.section_summaries() 가 돌려주는 키와 1:1 로 같다 — 화면이 섹션 요약을 찾을 때
쓰는 그 키다. 별도 매핑 계층을 두지 않는다.

customer·llm·prompts 와 같은 잎(leaf) 모듈이라 다른 모듈을 임포트하지 않는다. 두 에이전트를
한 프로세스에서 함께 쓸 때를 위해 common/agent_loader.py 의 적재 목록에도 engine 보다 앞서
등록돼 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(Enum):
    """섹션의 생성 주체(REQUIREMENTS.md §15).

    RULE_LLM 과 LLM_ONLY 의 차이는 'LLM 이 실패했을 때 무엇을 보여주는가' 다 —
    RULE_LLM 은 규칙 산출물이 남아 그것을 보여주고, LLM_ONLY 는 규칙 폴백이 없어
    섹션을 비운 채 "생성 안 됨"을 표시한다. 규칙으로 쓴 문장을 AI 산출로 오인시키지
    않기 위한 구분이다.
    """

    RULE = "rule"            # 규칙 산출, LLM 미개입
    RULE_LLM = "rule_llm"    # 규칙이 만들고 LLM 이 문장화·해석·선별(실패 시 규칙 결과 유지)
    LLM_ONLY = "llm_only"    # LLM 전용 — 실패 시 비움. 규칙 폴백을 두지 않는다


@dataclass(frozen=True)
class Section:
    key: str          # engine.section_summaries() 의 키와 동일
    label: str        # 원문자 ①~⑨
    title: str        # 정식 제목(원문자 제외)
    source: Source    # 생성 주체(§15)
    note: str = ""    # 제목 옆에 함께 노출할 단서. 없으면 빈 문자열

    @property
    def full_title(self) -> str:
        """화면·CLI 가 그대로 출력하는 제목("④ 수익률 상위 1% …")."""
        return f"{self.label} {self.title}"


SECTIONS: tuple[Section, ...] = (
    Section("ai_briefing", "①", "AI 브리핑", Source.RULE_LLM),
    Section("why_this_customer", "②", "왜 이 고객님인가요", Source.RULE_LLM),
    Section("current_state", "③", "현재 운용상태", Source.LLM_ONLY),
    Section("top_holdings", "④", "수익률 상위 1% 고객님들이 많이 담은 상품은?",
            Source.RULE_LLM, note="이 고객 대상 추천 아님, 참고용"),
    Section("recommendation", "⑤", "이런 상품이 적합할 수 있어요", Source.LLM_ONLY),
    Section("talking_points", "⑥", "이렇게 말해보세요", Source.RULE_LLM),
    Section("objections", "⑦", "예상 반론 및 대응 화법", Source.RULE_LLM),
    Section("consult_resources", "⑧", "상담에 참고하세요", Source.RULE_LLM),
    Section("outreach", "⑨", "고객님께 안내해보세요", Source.RULE_LLM),
)

BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}

# ①~⑨ 밖이지만 화면이 함께 그리는 항목(REQUIREMENTS.md §14).
CONSULT_HISTORY_TITLE = "상담 이력"


def title(key: str) -> str:
    """섹션 키로 화면 제목을 얻는다. 모르는 키면 키 자체를 돌려준다(화면이 죽지 않게)."""
    s = BY_KEY.get(key)
    return s.full_title if s else key


def payload() -> list[dict]:
    """BFF 응답에 실어 프론트가 제목을 API 에서 받게 하는 직렬화 형태.

    프론트가 제목 문자열을 자기 코드에 다시 적어두지 않도록 하는 것이 목적이다.
    """
    return [
        {"key": s.key, "label": s.label, "title": s.title,
         "full_title": s.full_title, "note": s.note,
         "llm_only": s.source is Source.LLM_ONLY}
        for s in SECTIONS
    ]
