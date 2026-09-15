"""consult_agent 테스트가 함께 쓰는 것 — 집계용 print · 전역 스텁 · 검색 정확도 케이스.

여기 있는 것은 **한 프로세스에 하나**여야 하는 것들이다: 통과/실패를 세는 print 래퍼,
LLM 을 부르는 자리(카드 선택 1차·적합성 게이트·슬롯 분해)를 전역에서 끄는 스위치, 그리고
그 원본(_REAL_*). 검사 모듈(routing·material·…)은 전부 여기서 가져다 쓰고, 순서대로 부르는
것은 `tests/test_consult_agent.py::main` 이다.

새 화법 카드를 추가할 때마다 CASES 에 한 줄씩 넣으면,
카드가 늘어나 검색이 엉키는 것을 회귀 테스트로 잡을 수 있다.
"""

from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")  # 클라이언트 초기화만 통과시킨다

# ── 집계 ──────────────────────────────────────────────────────
# 검사 한 건 = "✓"/"✗" 로 시작하는 출력 한 줄이다. 그래서 총계를 손으로 더하지 않고
# 출력에서 센다. 예전에는 `total = len(CASES) + 6 + 9 + 10 + ...` 처럼 검사 개수를 손으로
# 유지했는데, 검사를 하나 늘릴 때마다 그 상수를 같이 고쳐야 했고 잊으면 "87/80 통과"처럼
# 통과인데 실패로 끝났다. 세는 일을 코드에 맡긴다.
_TALLY = {"ok": 0, "fail": 0}
_stdout_print = print


def print(*args, **kwargs):  # noqa: A001 — 이 모듈 안에서만 가리는 집계용 래퍼
    if args and isinstance(args[0], str):
        if args[0].startswith("✓"):
            _TALLY["ok"] += 1
        elif args[0].startswith("✗"):
            _TALLY["fail"] += 1
    _stdout_print(*args, **kwargs)

from pension_agent.consult_agent import select, tools
from pension_agent.consult_agent.tools import pitch_slots
from pension_agent.verify import verify_texts

_vt = verify_texts

# 카드 선택 1차(LLM)를 전역에서 끈다. 이 스위트가 재는 것은 n-gram 채점과 그래프 배선이고,
# LLM 선택을 켜두면 키가 있는 환경에서 실제 호출이 나가 측정값이 흔들린다.
# 진짜 llm_pick 을 검사하는 테스트(check_hier_index)는 아래 원본을 직접 부른다.
_REAL_LLM_PICK = select.llm_pick
# main() 이 적합성 게이트도 전역에서 스텁으로 갈아끼운다(대부분의 검사는 게이트를 재는
# 것이 아니라 통과시키고 싶어 한다). 게이트 자체를 재는 검사는 이 원본을 직접 부른다.
_REAL_FITS = tools.fits_question
# 화법 슬롯 분해도 main() 이 스텁으로 갈아끼운다. 분해 자체(와 그 LLM 실패)를 재는 검사는
# 이 원본을 되돌려 놓고 부른다.
_REAL_EXTRACT_SLOTS = pitch_slots.extract_slots
select.llm_pick = lambda kinds, query: []
tools.llm_pick = select.llm_pick

# 거절유형이 null 로 나와(=미탐지) 1·2차 재검색까지 0건인 애매한 질문. broaden 이
# 채택 기준(kb.MIN_TOPICAL)은 건드리지 않으므로 여전히 FALLBACK 이 맞다 — 실수로
# 문턱을 낮춰 무관한 질문까지 통과시키지 않는지 잡아주는 회귀 테스트다.
_AMBIGUOUS_Q = "타사에서 이미 운용중이라고 하시는데 관리가 안되고 있다고 하시네요"

# 같은 거절유형 라벨이 여러 단계(stage)에 걸쳐 있을 때 서로 안 섞이는지 검증하는 케이스.
# 일반 키워드 휴리스틱(_OBJ_HINTS)으로는 단계를 구분할 수 없어서 슬롯을 직접 지정한다.
# 질문 문구는 해당 카드의 실제 trigger_examples 그대로 — kb.py 점수 계산이 진짜로
# 동작하는지 보려는 것이라 임의로 바꾸면 안 된다.
_OVERRIDES: dict[str, dict] = {
    # '해지·망설임' 라벨 하나를 수령·추가납입·이탈방어 세 단계가 함께 쓴다. stage 로 후보를
    # 먼저 좁히면 세 장이 서로 안 섞이는지 확인한다.
    "1억원 쓸 곳이 있어서 해지하려고요": {"stage": "수령", "objection_type": "해지·망설임"},
    "만 55세 아직 멀었어요, 수수료만 나가잖아요": {"stage": "추가납입", "objection_type": "해지·망설임"},
    "생각 좀 해볼게요": {"stage": "이탈방어", "objection_type": "해지·망설임"},
}

# (질문, 기대하는 1순위 카드 / FALLBACK / AGENT_HELP)
# 질문은 카드의 trigger_examples 원문이다. 직원이 고객 말을 그대로 옮겨 적는 것이 이
# 기능의 실제 사용 형태라, 검색이 그 경로에서 정확한지를 잰다.
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

# situation_slots 가 실제로 뽑아낼 법한 거절유형을 규칙으로 흉내낸다. 라벨은 kb_pitches 의
# tags.objection_type 실제 값이어야 한다 — 없는 라벨을 넣으면 채점이 조용히 0점이 된다.
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
    """understand 는 intent+utterance 만 낸다 — 화법 슬롯은 stub_slots 가 낸다
    (실제로도 화법 도구가 n-gram 폴백에 들어갈 때만 pitch_slots.extract_slots 를 부른다)."""
    q = state["question"]
    if q in _OVERRIDES:
        return {"intent": _OVERRIDES[q].get("intent", "situation"), "utterance": q, "broaden_count": 0}
    return {
        "intent": "agent_help" if any(k in q for k in _AGENT_HELP_HINTS) else "situation",
        "utterance": q,
        "broaden_count": 0,
    }


def stub_slots(state):
    """pitch_slots.extract_slots 가 실제로 뽑아낼 법한 화법 슬롯을 규칙으로 흉내낸다.

    **단계(stage)는 지정한 케이스에만 채운다.** 예전에는 모든 질문에 "신규"를 넣었는데,
    지식베이스가 사후관리 범위로 정리되면서 그 단계가 없어졌다. 없는 단계를 채우면
    `matches_scope` 가 후보를 전부 걸러내 모든 질문이 FALLBACK 이 된다 — 검색이 아니라
    스텁이 만든 결과라 원인이 안 보인다. 모르면 비운다.
    """
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
    """compose 가 화법 문장을 쓸 때의 LLM. 문장 내용은 이 스위트의 관심사가 아니다
    (재는 것은 '어떤 카드가 근거로 잡혔나'다)."""
    return "(LLM 생성 화법 — 스텁)"


def stub_plan_pitch(state):
    """계획 루프를 '화법 도구 한 번'으로 고정한다.

    CASES 는 화법 검색 정확도의 회귀 스위트다. 어떤 도구를 부를지까지 LLM 에 맡기면
    재려는 것(카드 채점)이 계획의 흔들림에 묻힌다. 계획 자체는 check_tool_loop 가 본다.
    """
    found = tools.run("pitch", state, state.get("utterance") or state["question"])
    out = {"plan_done": True, "steps": [{"tool": "pitch", "query": "q", "outcome": "found"}]}
    if found is not None:
        out["evidence"] = [found]
    return out
