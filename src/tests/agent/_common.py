"""consult_agent 테스트 공용 — 집계용 print · 전역 LLM 스텁과 원본(_REAL_*) · 화법 검색 CASES.

새 화법 카드 추가 시 CASES 에 한 줄 추가.
"""

from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")  # 클라이언트 초기화만 통과시킨다

# 집계 — "✓"/"✗" 로 시작하는 출력 한 줄 = 검사 한 건. 총계 상수 수기 관리 금지
_TALLY = {"ok": 0, "fail": 0}
_stdout_print = print


def print(*args, **kwargs):  # noqa: A001 — 이 모듈 안에서만 가리는 집계용 래퍼
    if args and isinstance(args[0], str):
        if args[0].startswith("✓"):
            _TALLY["ok"] += 1
        elif args[0].startswith("✗"):
            _TALLY["fail"] += 1
    _stdout_print(*args, **kwargs)

from pension_agent import tools
from pension_agent.tools.kb_search import select
from pension_agent.tools.kb_search import pitch_slots
from pension_agent.evidence.verify import verify_texts

_vt = verify_texts

# 카드 선택 1차(LLM) 전역 off — 키가 있으면 실호출로 측정값이 흔들림. 원본은 check_hier_index 가 사용
_REAL_LLM_PICK = select.llm_pick
# 적합성 게이트 원본 — main() 이 전역 스텁으로 교체, 게이트 검사만 원본 사용
_REAL_FITS = tools.fits_question
# 화법 슬롯 분해 원본 — main() 이 스텁으로 교체, 분해 검사만 원복해 사용
_REAL_EXTRACT_SLOTS = pitch_slots.extract_slots
select.llm_pick = lambda kinds, query: []
tools.llm_pick = select.llm_pick

# 거절유형 미탐지·재검색 0건인 애매한 질문 — FALLBACK 유지(문턱 하향 회귀 방지)
_AMBIGUOUS_Q = "타사에서 이미 운용중이라고 하시는데 관리가 안되고 있다고 하시네요"

# 같은 거절유형 라벨의 단계별 분리 검증 — 슬롯 직접 지정. 질문은 카드 trigger_examples 원문(변경 금지)
_OVERRIDES: dict[str, dict] = {
    # '해지·망설임' 라벨 — 수령·추가납입·이탈방어 세 단계 공용
    "1억원 쓸 곳이 있어서 해지하려고요": {"stage": "수령", "objection_type": "해지·망설임"},
    "만 55세 아직 멀었어요, 수수료만 나가잖아요": {"stage": "추가납입", "objection_type": "해지·망설임"},
    "생각 좀 해볼게요": {"stage": "이탈방어", "objection_type": "해지·망설임"},
}

# (질문, 기대 1순위 카드 / FALLBACK / AGENT_HELP). 질문은 카드 trigger_examples 원문
CASES = [
    ("증권사는 수수료 무료라는데요", "pitch.k03.001"),
    ("그동안 관리 안 해줬잖아요", "pitch.k03.004"),
    ("은행 다 거기서 거기 아니에요?", "pitch.k03.003"),
    ("수익률은 다른 은행이 더 좋던데요", "pitch.k03.002"),
    ("이미 다른 데 IRP 있어요", "pitch.k03.051"),
    ("나중에 돈 못 빼는 거 아니에요?", "pitch.k03.052"),
    ("지금 쓸 돈도 없어요", "pitch.k03.053"),
    ("예금만 해서 관리 필요 없어요", "pitch.k03.009"),
    ("펀드는 고르기가 너무 어려워요", "pitch.k03.015"),
    ("주택청약 금리 어떻게 되나요?", "FALLBACK"),
    ("오늘 점심 뭐 먹지", "FALLBACK"),
    (_AMBIGUOUS_Q, "FALLBACK"),
    ("네가 답변할 수 있는 화법은 뭐가 있어?", "AGENT_HELP"),
    # 스코프 케이스 — 같은 '해지·망설임' 라벨, 다른 단계
    ("1억원 쓸 곳이 있어서 해지하려고요", "pitch.k03.012"),
    ("만 55세 아직 멀었어요, 수수료만 나가잖아요", "pitch.k03.013"),
    ("생각 좀 해볼게요", "pitch.k03.019"),
]

# 거절유형 추출 흉내 규칙. 라벨은 kb_pitches tags.objection_type 실제 값만 — 없는 라벨은 조용히 0점
_OBJ_HINTS = [
    ("증권사", "증권사 비교"), ("수수료 무료", "증권사 비교"),
    ("관리 안", "관리·수익률 불만"), ("수익률", "관리·수익률 불만"),
    ("거기서 거기", "관리·수익률 불만"),
    ("못 빼", "제도·투자 거부"), ("못빼", "제도·투자 거부"),
    ("돈도 없", "제도·투자 거부"), ("이미", "제도·투자 거부"),
    ("예금만", "제도·투자 거부"),
]

_AGENT_HELP_HINTS = ("화법이 뭐가 있", "화법은 뭐가 있", "도와줄", "어떤 상황을 도와", "뭘 할 수 있")


def stub_understand(state):
    """understand 스텁 — intent·utterance 만 반환(슬롯은 stub_slots)."""
    q = state["question"]
    if q in _OVERRIDES:
        return {"intent": _OVERRIDES[q].get("intent", "situation"), "utterance": q, "broaden_count": 0}
    return {
        "intent": "agent_help" if any(k in q for k in _AGENT_HELP_HINTS) else "situation",
        "utterance": q,
        "broaden_count": 0,
    }


def stub_slots(state):
    """extract_slots 스텁. stage 는 지정 케이스만 — 없는 단계는 matches_scope 가 전부 걸러 FALLBACK."""
    q = state["question"]
    if q in _OVERRIDES:
        override = _OVERRIDES[q]
        return {
            "customer_type": override.get("customer_type"),
            "objection_type": override.get("objection_type"),
            "stage": override.get("stage"),
        }
    if q == _AMBIGUOUS_Q:
        objection_type = None  # 미탐지 재현 — "이미"가 있어도 일부러 못 잡은 것으로 취급
    else:
        objection_type = next((v for k, v in _OBJ_HINTS if k in q), None)
    return {"customer_type": None, "objection_type": objection_type, "stage": None}


def stub_talk(prompt, **kw):
    """compose 화법 문장 LLM 스텁."""
    return "(LLM 생성 화법 — 스텁)"


def stub_plan_pitch(state):
    """계획 루프를 화법 도구 1회로 고정(CASES 채점이 계획 흔들림에 묻히지 않게)."""
    found = tools.run("pitch", state, state.get("utterance") or state["question"])
    out = {"plan_done": True, "steps": [{"tool": "pitch", "query": "q", "outcome": "found"}]}
    if found is not None:
        out["evidence"] = [found]
    return out
