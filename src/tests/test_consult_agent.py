"""검색 정확도 · 그래프 라우팅 · 계획 루프 테스트.

LLM 을 부르는 자리(understand·계획·문장생성)를 스텁으로 갈아끼우므로 API 키가 없어도 돌아간다.
새 화법 카드를 추가할 때마다 CASES 에 한 줄씩 넣으면,
카드가 늘어나 검색이 엉키는 것을 회귀 테스트로 잡을 수 있다.

실행:  python test_agent.py
"""

from __future__ import annotations

import json
import os
import pathlib
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

from pension_agent import config
from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import routing, select, tools
from pension_agent.consult_agent.nodes import pitch, plan, understand
from pension_agent.llm import LLMError
from pension_agent.verify import numbers, verify_texts

# 카드 선택 1차(LLM)를 전역에서 끈다. 이 스위트가 재는 것은 n-gram 채점과 그래프 배선이고,
# LLM 선택을 켜두면 키가 있는 환경에서 실제 호출이 나가 측정값이 흔들린다.
# 진짜 llm_pick 을 검사하는 테스트(check_hier_index)는 아래 원본을 직접 부른다.
_REAL_LLM_PICK = select.llm_pick
# main() 이 적합성 게이트도 전역에서 스텁으로 갈아끼운다(대부분의 검사는 게이트를 재는
# 것이 아니라 통과시키고 싶어 한다). 게이트 자체를 재는 검사는 이 원본을 직접 부른다.
_REAL_FITS = tools.fits_question
# 화법 슬롯 분해도 main() 이 스텁으로 갈아끼운다. 분해 자체(와 그 LLM 실패)를 재는 검사는
# 이 원본을 되돌려 놓고 부른다.
_REAL_EXTRACT_SLOTS = pitch.extract_slots
select.llm_pick = lambda kinds, query: []
tools.llm_pick = select.llm_pick

# 거절유형이 null 로 나와(=미탐지) 1·2차 재검색까지 0건인 애매한 질문. broaden 이
# 채택 기준(kb.MIN_TOPICAL)은 건드리지 않으므로 여전히 FALLBACK 이 맞다 — 실수로
# 문턱을 낮춰 무관한 질문까지 통과시키지 않는지 잡아주는 회귀 테스트다.
#: main() 시작 시점에 이미 있던 상담이력 파일. 끝에서 이번 실행이 만든 것만 지운다.
_SESSIONS_BEFORE: set = set()

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
    (실제로도 화법 도구가 n-gram 폴백에 들어갈 때만 pitch.extract_slots 를 부른다)."""
    q = state["question"]
    if q in _OVERRIDES:
        return {"intent": _OVERRIDES[q].get("intent", "situation"), "utterance": q, "broaden_count": 0}
    return {
        "intent": "agent_help" if any(k in q for k in _AGENT_HELP_HINTS) else "situation",
        "utterance": q,
        "broaden_count": 0,
    }


def stub_slots(state):
    """pitch.extract_slots 가 실제로 뽑아낼 법한 화법 슬롯을 규칙으로 흉내낸다.

    **단계(stage)는 지정한 케이스에만 채운다.** 예전에는 모든 질문에 "신규"를 넣었는데,
    지식베이스가 사후관리 범위로 정리되면서 그 단계가 없어졌다. 없는 단계를 채우면
    `_matches_scope` 가 후보를 전부 걸러내 모든 질문이 FALLBACK 이 된다 — 검색이 아니라
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
    out = {"plan_done": True, "plan_calls": ["pitch"]}
    if found is not None:
        out["evidence"] = [found]
    return out


def check_pitch_stages() -> bool:
    """화법 도구가 LLM → 조건부 n-gram → 조건 완화 3단으로 물러서는지 검증한다.

    예전 그래프의 llm_select → retrieve → broaden 이 이 도구 안으로 접혔다. 접히면서
    단계가 사라졌는지(특히 조건 완화 재검색) 잡는 것이 이 테스트의 목적이다.
    """
    calls: list[dict] = []
    real = tools.KB.pitches[0]
    # 이 검사가 재는 것은 후퇴 단계이지 슬롯 분해가 아니다 — 상태에 심어둔 슬롯을 그대로
    # 돌려주게 해서, 1회차가 조건으로 좁히는지만 본다.
    pitch.extract_slots = lambda st: {k: st.get(k)
                                      for k in ("customer_type", "objection_type", "stage")}

    def spy_retrieve(kb, **kw):
        calls.append(kw)
        # 1회차(조건 있음)는 0건, 2회차(조건 풀림)에서 찾은 것으로 흉내낸다
        return [] if any(kw.get(k) for k in ("customer_type", "objection_type", "stage")) else [(0.5, real)]

    orig_retrieve, orig_verify = tools.retrieve, tools.fits_question
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None: h
    try:
        found = tools._pitch(
            {"question": "질문", "customer_type": "사업자", "stage": "이탈방어", "objection_type": None},
            "질문",
        )
    finally:
        tools.retrieve, tools.fits_question = orig_retrieve, orig_verify

    ok = (len(calls) == 2                                     # 조건부 → 조건 완화
          and calls[0].get("customer_type") == "사업자"        # 1회차는 슬롯으로 좁힌다
          and not any(calls[1].get(k) for k in ("customer_type", "objection_type", "stage"))
          and found is not None and found["sources"][0]["id"] == real["id"])
    print(f"{'✓' if ok else '✗'} 화법 도구 3단 후퇴(n-gram {len(calls)}회 · 2회차 조건 완화)")
    return ok


_ROUTED_INTENTS = ("lms_send", "correction", "confirm_action")

# 예전에는 값·절차·고객군·브리핑 질의도 각자 intent 와 노드를 갖고 있었다. 능력 표면이
# 도구 목록이 되면서(CLAUDE.md §3) 그 넷은 전용 노드를 잃고 계획 루프로 합쳐졌다 —
# 같은 재료를 두 경로로 답하면 프롬프트·검증·표시 규약이 갈리기 때문이다(§12 gap 11).
_PLAN_ROUTED = ("situation", "guide", "fact_lookup", "briefing_qa",
                "objection_drill", "없는_의도")


def check_intent_routing() -> bool:
    """인텐트가 understand 분류에 따라 올바른 노드로 라우팅되는지 확인한다 — 각 노드 자체의
    로직은 해당 파일에서 별도로 검증할 대상이라, 여기서는 그래프 배선만 본다."""
    orig = {name: getattr(G, name) for name in _ROUTED_INTENTS}

    def make_probe(name):
        def probe(state):
            return {"answer": f"({name} 응답)", "sources": []}
        return probe

    for name in orig:
        setattr(G, name, make_probe(name))

    ok = True
    for intent in _ROUTED_INTENTS:
        question = f"{intent}-라우팅-테스트"
        _OVERRIDES[question] = {"intent": intent}
        agent = G.build_agent()
        out = agent.invoke({"question": question, "customer_id": "CX"})
        this_ok = out.get("answer") == f"({intent} 응답)"
        ok = ok and this_ok
        print(f"{'✓' if this_ok else '✗'} intent={intent} → {intent} 노드로 라우팅")
        del _OVERRIDES[question]

    # 지식·고객 재료로 답하는 질문은 전부 계획 루프로 간다. 목록 밖 의도도 마찬가지 —
    # 분류가 어긋났다고 능력이 잘리면 안 된다(무엇으로 답할지는 도구 목록이 정한다).
    for intent in _PLAN_ROUTED:
        this_ok = routing.route_intent({"intent": intent}) == "plan"
        ok = ok and this_ok
        print(f"{'✓' if this_ok else '✗'} intent={intent} → 계획 루프(plan)")

    # LLM 이 죽은 턴은 무엇으로 분류됐든 안내 하나로 끝난다 (§11).
    this_ok = routing.route_intent({"intent": "situation", "llm_error": "X"}) == routing.LLM_DOWN
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} llm_error 가 있으면 → llm_down 노드")

    # understand 가 LLM 실패를 규칙으로 대신 분류하지 않는가 (§11 · gap 9).
    #
    # 예전에는 여기서 키워드 표(guess_intent)로 의도를 어림하고 즉답 노드가 LLM 없이
    # 답을 만들었다. 그 경로가 남아 있으면 "LLM 없이도 절반은 도는" 상태가 굳는다.
    def _dead_llm(*a, **kw):
        raise LLMError("LLM 없음")

    orig_gen = understand.generate
    understand.generate = _dead_llm
    try:
        out = understand.understand({"question": "세액공제 한도"})
    finally:
        understand.generate = orig_gen
    this_ok = out.get("intent") == routing.LLM_DOWN and "LLM 없음" in out.get("llm_error", "")
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} understand: LLM 실패를 규칙으로 대신 분류하지 않는다")

    this_ok = not hasattr(understand, "guess_intent")
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} 규칙 라우팅 폴백(guess_intent)이 남아 있지 않다")

    for name, fn in orig.items():
        setattr(G, name, fn)
    return ok


def check_lms_send_parsing() -> bool:
    """lms_send 는 **보내지 않는다** — 발송 화면 연계를 제안할 뿐이다(§10).

    인용부호 파싱·문구 누락·customer_id 없음을 직접 검증한다(LLM 을 쓰지 않는 노드다).
    """
    from pension_agent.consult_agent.nodes import lms

    out = lms.lms_send({"question": '"안내 문구입니다" 로 LMS 보내줘', "customer_id": "CX"})
    pending = out.get("pending_action")
    ok1 = (bool(pending) and pending["kind"] == "lms" and pending["screen"]
           and pending["message"] == "안내 문구입니다"
           and "보낼지는 그 화면에서" in out["answer"])
    ok2 = "큰따옴표" in lms.lms_send({"question": "그냥 보내줘", "customer_id": "CX"})["answer"]
    ok3 = "찾을 수 없어요" in lms.lms_send({"question": '"문구" 보내줘', "customer_id": None})["answer"]
    ok = ok1 and ok2 and ok3
    print(f"{'✓' if ok else '✗'} lms_send: 발송이 아니라 화면 연계를 제안한다")
    return ok


def check_knowledge_intents() -> bool:
    """값·절차·고객군 재료가 **도구로** 닿고, 근거에 기준시점·출처가 함께 실리는지 본다.

    예전에는 같은 것을 즉답 노드(fact_lookup·procedure·segment_explain)로 쟀다. 그 노드들은
    §11 에 따라 지웠다 — LLM 없이 답을 만드는 경로였기 때문이다. 재료 자체는 그대로 남아
    도구가 쓰므로, 재는 자리를 노드에서 도구로 옮긴다.

    확인하는 것은 두 가지다. ① 질문에 맞는 근거를 실제로 찾는가, ② 근거 블록에 기준시점·
    출처처럼 '이 값을 언제·어디 근거로 말하는지'가 함께 나오는가. ②가 빠지면 숫자만 맞고
    근거가 없는 답이 되어, 직원이 그대로 고객에게 옮길 수 없다.
    """
    from pension_agent.consult_agent.nodes import facts_qa

    checks = [
        ("fact", "세액공제 한도가 얼마야?", ("만원", "출처")),
        ("procedure", "적립금 수익률 조회 화면번호 알려줘", ("화면번호", "출처")),
        ("segment", "현금성자산 편중 고객은 왜 관리 대상이야?", ("골라내나", "출처")),
    ]
    ok = True
    for name, question, expected in checks:
        found = tools.run(name, {"question": question}, question)
        text = (found or {}).get("text", "")
        this_ok = bool(found) and bool(found["sources"]) and all(t in text for t in expected)
        ok = ok and this_ok
        detail = "" if this_ok else f" — {text[:70]!r}"
        print(f"{'✓' if this_ok else '✗'} {name} 도구: 근거를 찾고 출처·기준을 함께 싣는다{detail}")

    # 고객 대사를 그대로 던진 질문도 같은 화법 재료로 답한다 — 전용 양식 노드는 없다.
    q = "증권사는 수수료 무료라는데요"
    found = tools.run("pitch", {"question": q}, q)
    this_ok = bool(found) and found["tool"] == "pitch" and bool(found["sources"])
    ok = ok and this_ok
    print(f"{'✓' if this_ok else '✗'} 고객 대사 질문도 pitch 재료로 닿는다")

    import importlib
    gone = importlib.util.find_spec("pension_agent.consult_agent.nodes.drill") is None
    ok = ok and gone
    print(f"{'✓' if gone else '✗'} 즉답 카드 양식 노드(drill)가 남아 있지 않다")

    # 지식베이스에 없는 것을 물으면 도구가 근거를 만들어내지 않는다.
    no_invent = not facts_qa.search("오늘 서울 날씨 어때?")
    ok = ok and no_invent
    print(f"{'✓' if no_invent else '✗'} fact 도구: 없는 값은 지어내지 않고 0건으로 답한다")

    # 지운 즉답 노드가 되살아나지 않았는가 (§11 회귀).
    from pension_agent.consult_agent.nodes import procedure_qa, segment_qa
    gone = not any(hasattr(m, fn) for m, fn in
                   ((facts_qa, "fact_lookup"), (procedure_qa, "procedure"),
                    (segment_qa, "segment_explain")))
    ok = ok and gone
    print(f"{'✓' if gone else '✗'} LLM 없이 답을 만들던 즉답 노드 3종이 남아 있지 않다")
    return ok


def check_screen_link() -> int:
    """화면 연계 — 제안 → 확인 → 연계 (§10 · gap 14·15).

    회귀 대상:
    ① LMS 를 **발송까지 수행**하는 스텁이었고, 화면 URL·파라미터라는 개념이 없었다.
       에이전트는 화면을 열어줄 뿐 작업을 대신 수행하지 않는다.
    ② 절차 안내로 화면번호를 알려준 답변에는 연계 제안이 붙지 않았다.
    ③ 확인 응답이 대화 이력에서 **가장 최근의** 제안을 찾아 실행했다 — 사이에 다른 질문이
       오간 뒤의 "네"도 몇 턴 전 제안을 실행할 수 있었다. 직원이 잊은 제안이 뒤늦게
       실행되는 것은 승낙이 아니다.
    """
    from pension_agent.consult_agent import screens
    from pension_agent.consult_agent.nodes import act
    from pension_agent.consult_agent.state import KB
    from pension_agent import session_store

    ok = 0
    talk = '이렇게 말해보세요. "고객님, 남은 세액공제 한도가 264만원 있어요. 연말이 지나면 사라집니다."'

    # ② 답변이 짚은 화면번호로 연계를 제안한다 — 단, **근거 카드에 있는 번호만**.
    proc = {"tool": "procedure", "query": "q", "text": "블록", "atomic": ["[75-08-110]"],
            "notices": [], "notice_scopes": [], "allow": ["블록"], "marks": [],
            "sources": [], "meta": {}}
    offered = act.offer({"answer": "[75-08-110] 화면에서 처리하시면 돼요.",
                         "evidence": [proc], "customer_id": "TEST_ACT"})
    pending = offered.get("pending_action")
    hit = bool(pending) and pending["screen"] == "75-08-110" and "연계해드릴까요" in offered["answer"]
    print(f"{'✓' if hit else '✗'} 절차 화면번호가 실린 답변에 연계를 제안한다")
    ok += hit

    # 답변이 지어낸 번호로는 링크를 만들지 않는다 — 직원이 엉뚱한 화면에서 작업하게 된다.
    hit = not act.offer({"answer": "[99-99-999] 화면에서 처리하세요.",
                         "evidence": [proc], "customer_id": "TEST_ACT"}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 근거에 없는 화면번호로는 연계를 만들지 않는다")
    ok += hit

    # ① 답변에 따옴표 친 대사가 있다는 이유로 발송 화면을 제안하지 않는다.
    #
    # 회귀 대상: 그 조건은 화법 코칭 답변이면 **거의 항상 참**이다 — 고객에게 할 말을
    # 큰따옴표로 쓰라고 작성 프롬프트가 지시하기 때문이다. 그래서 사후관리 방법을 물었을
    # 뿐인 턴에도 "발송 화면 열까요?"가 붙었고, 매 턴 붙는 제안은 직원이 읽지 않게 된다.
    hit = not act.offer({"answer": talk, "customer_id": "TEST_ACT"}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 대사가 있다는 이유만으로 발송 화면을 제안하지 않는다")
    ok += hit

    # 문구를 보내려는 직원은 그렇게 말한다 — 그 요청이 같은 화면 연계를 제안한다.
    from pension_agent.consult_agent.nodes import lms
    lms_pending = lms.lms_send(
        {"question": '"고객님, 남은 세액공제 한도가 264만원 있어요" 이 문구로 LMS 보내줘',
         "customer_id": "TEST_ACT"}).get("pending_action")
    hit = (bool(lms_pending) and lms_pending["kind"] == "lms"
           and lms_pending["screen"] == (screens.lms_screen(KB) or ("",))[0]
           and "264만원" in lms_pending["message"])
    print(f"{'✓' if hit else '✗'} 발송 요청은 발송 '화면'을 제안한다(발송이 아니다)")
    ok += hit

    # 발송 화면번호는 지식베이스에서 온다 — 코드에 박아두지 않는다.
    found = screens.lms_screen(KB)
    hit = bool(found) and found[1].startswith("proc.")
    print(f"{'✓' if hit else '✗'} 발송 화면번호를 지식베이스 절차 카드에서 찾는다"
          + (f" — {found[0]} ({found[1]})" if found else ""))
    ok += hit

    # 조건이 아니면 제안하지 않는다 — 매 턴 "연계해드릴까요?"가 붙으면 확인이 의미를 잃는다.
    hit = (not act.offer({"answer": talk, "customer_id": None})
           and not act.offer({"answer": "세액공제 한도는 900만원입니다.", "customer_id": "TEST_ACT"}))
    print(f"{'✓' if hit else '✗'} 고객 화면이 없거나 가리키는 화면이 없으면 제안하지 않는다")
    ok += hit

    # 확인 — 승낙이면 파라미터를 채운 URL 을 주고, 무엇을 어떤 값으로 열었는지 알린다.
    history = [{"question": "...", "pending_action": lms_pending}]
    yes = act.confirm_action({"question": "네 열어주세요", "history": history,
                              "customer_id": "TEST_ACT"})
    hit = (screens.SCHEME in yes["answer"] and "264만원" in yes["answer"]
           and "customer_id" not in yes["answer"] and yes["pending_action"] is None)
    print(f"{'✓' if hit else '✗'} '네' 면 화면 URL 을 주고, 링크가 못 싣는 문구는 따로 알린다")
    ok += hit

    logged = session_store.list_sessions("TEST_ACT")
    hit = any(t.get("role") == "tool" for sess in logged for t in sess["turns"])
    print(f"{'✓' if hit else '✗'} 연계 호출이 상담이력에 남는다")
    ok += hit

    no = act.confirm_action({"question": "아니요 괜찮아요", "history": history,
                             "customer_id": "TEST_ACT"})
    hit = "취소" in no["answer"] and no["pending_action"] is None
    print(f"{'✓' if hit else '✗'} '아니오' 면 제안을 물린다")
    ok += hit

    # 애매한 답을 승낙으로 해석하지 않는다 — 제안을 유지한 채 다시 묻는다.
    maybe = act.confirm_action({"question": "음 글쎄요", "history": history,
                                "customer_id": "TEST_ACT"})
    hit = maybe["pending_action"] == lms_pending and "네' 또는 '아니오" in maybe["answer"]
    print(f"{'✓' if hit else '✗'} 애매하면 제안을 유지한 채 다시 묻는다")
    ok += hit

    # ③ 제안은 그 자리에서만 유효하다 — 사이에 다른 질문이 오갔으면 무효.
    stale = [{"question": "...", "pending_action": lms_pending},
             {"question": "그건 그렇고 세액공제 한도가 얼마야?"}]
    out = act.confirm_action({"question": "네", "history": stale, "customer_id": "TEST_ACT"})
    hit = "제안드린 작업이 없어요" in out["answer"] and out["pending_action"] is None
    print(f"{'✓' if hit else '✗'} 몇 턴 전 제안을 뒤늦은 '네'로 실행하지 않는다")
    ok += hit

    hit = "제안드린 작업이 없어요" in act.confirm_action(
        {"question": "네", "history": [], "customer_id": "TEST_ACT"})["answer"]
    print(f"{'✓' if hit else '✗'} 제안이 없으면 아무것도 열지 않는다")
    ok += hit

    # 더미 문구는 코드가 막는다 — 화면에 채우면 직원이 그대로 보낼 수 있기 때문이다.
    from pension_agent.strategy_agent import support
    dummy = next((a for a in support.ASSETS if a.get("dummy") and a.get("lms_message")), None)
    if dummy:
        blocked = act.confirm_action({
            "question": "네",
            "history": [{"question": "...", "pending_action": {
                **lms_pending, "message": dummy["lms_message"]}}],
            "customer_id": "TEST_ACT"})
        hit = "연계하지 않았어요" in blocked["answer"] and screens.SCHEME not in blocked["answer"]
    else:
        hit = True
    print(f"{'✓' if hit else '✗'} 더미 문구는 화면에 채우지 않는다(코드가 막는다)")
    ok += hit

    # URL 은 화면번호가 있을 때만 만든다.
    hit = screens.link("") is None and \
        screens.link("[75-08-110]") == f"{screens.SCHEME}scnNo=7508110&mode={screens.MODE}"
    print(f"{'✓' if hit else '✗'} 화면번호가 없으면 링크를 만들지 않는다")
    ok += hit

    # 단말 딥링크 규격 — `mystar-link://scnNo=...&mode=...`, 구분자는 `&`(§10).
    # scnNo 는 화면호출번호 7자리(지식베이스 표기에서 구분자를 뺀 것) 또는 단말화면번호
    # 11자리다. 자릿수가 맞지 않으면 링크를 만들지 않는다 — 단말이 엉뚱한 화면을 열거나
    # 아무것도 열지 못하는 링크를 직원에게 주지 않는다.
    url = screens.link("[06-12-604]")
    hit = (url.startswith("mystar-link://scnNo=0612604&mode=")
           and url.split("mode=")[1] in screens.MODES
           and "?" not in url and url.count("&") == 1
           and screens.scn_no("[06-7E-001]") == "067E001"
           and screens.scn_no("06126041234") == "06126041234"
           and screens.link("[99-9]") is None)
    print(f"{'✓' if hit else '✗'} 딥링크는 scnNo(7·11자리)+mode 를 & 로 잇는다 — {url}")
    ok += hit

    # 규격에 없는 파라미터는 싣지 않는다 — 단말이 받지도 않는 키를 붙이면 링크를 통째로
    # 못 읽을 수 있고, 고객 식별자·문구를 "채웠다"고 말하는 답변은 거짓이 된다.
    # 그 값들은 직원이 열린 화면에서 입력한다.
    hit = (set(url.split("://")[1].split("&")) == {"scnNo=0612604", f"mode={screens.MODE}"}
           and "customer_id" not in (act.confirm_action(
               {"question": "네", "history": history, "customer_id": "TEST_ACT"})["answer"]))
    print(f"{'✓' if hit else '✗'} scnNo·mode 밖의 파라미터는 링크에 싣지 않는다")
    ok += hit

    # 정리는 main() 이 «이번 실행이 만든 파일만» 지운다. 예전에는 여기서 디렉터리를
    # 통째로 rmtree 했는데, session_data 가 시연 픽스처를 담게 되면서(과거 상담 기록 —
    # scripts/seed_sessions.py) 테스트 한 번에 그 픽스처가 날아갔다.
    return ok


def check_verify_gate() -> bool:
    """적합성 게이트가 NO 를 내면(의도 불일치) 카드가 검색됐어도 근거로 안 쓰이는지 검증한다.

    게이트가 그래프의 노드에서 화법 도구 안으로 옮겨졌다. 거부되면 도구가 None 을 반환하고,
    원장이 비면 compose 가 정직하게 '근거 없음'으로 답한다.
    """
    G.understand = stub_understand
    G.plan_step = stub_plan_pitch
    agent = G.build_agent()

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: []
    try:
        out = agent.invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
    finally:
        tools.fits_question = orig

    # '없다'에는 무엇을 찾아봤는지가 덧붙는다(§5) — 앞부분이 같은지로 본다.
    ok = not out.get("sources") and (out.get("answer") or "").startswith(plan.NO_EVIDENCE)
    print(f"{'✓' if ok else '✗'} 적합성 게이트 NO → 근거 없음 답변")
    return ok


def check_guard() -> int:
    """「하지 말 것」 가드 — 지식베이스에 있는 것만.

    07_에이전트_기능정의/01 ① 필수 구성 요소 6. 규칙을 새로 쓰지 않고 행원들이 정리해둔
    method.cautions(caution 역할)와 민감 응대 화법 카드만 쓴다. 재료가 없으면 만들지 않는다.
    """
    from pension_agent.consult_agent import guard as GD
    from pension_agent.consult_agent import kb as KBM

    gkb = KBM.load_kb()
    cases: list[tuple[bool, str]] = []

    low = GD.cautions_for(gkb, ["low:수익률 하위 30%"])
    cases.append((any("지적" in g["text"] and "개선" in g["text"] for g in low),
                  "low: '지적 대신 개선안'이 지식베이스에서 나온다"))
    cases.append((bool(low) and all(g["card"].startswith("m.") for g in low),
                  "가드 근거는 method 카드"))

    alt = GD.sensitive_cards(gkb, ["low:수익률 하위 30%"])
    cases.append((any(a["card"] == "pitch.k03.028" for a in alt),
                  "low: 민감 응대 대안 화법을 함께 제시"))

    dep = GD.cautions_for(gkb, ["dep:원리금보장상품 편중(80% 이상)"])
    cases.append((any("현금성자산" in g["text"] for g in dep),
                  "dep: 용어 주의(고유계정대→현금성자산)도 지식베이스에서"))

    cases.append((GD.cautions_for(gkb, []) == [], "요건이 없으면 가드도 없다"))
    cases.append((GD.cautions_for(gkb, ["zzz:없는요건"]) == [],
                  "모르는 요건에 가드를 지어내지 않는다"))
    cases.append((GD.prompt_note([], []) == "", "가드가 없으면 프롬프트 지시도 빈다"))

    # procedure.cautions 는 전부 "필자 해석 / 확인 필요" 같은 문서 검증 메모다.
    # 상담 경고로 새면 진짜 경고가 그 사이에 묻힌다.
    every = [g for c in ("low", "dor", "dep", "nod", "mat") for g in GD.cautions_for(gkb, [c])]
    cases.append((not any(m in g["text"] for g in every
                          for m in ("필자", "팀 논의", "확인 필요")),
                  "저자 검증 메모가 상담 경고로 새지 않는다"))

    # 프롬프트 지시와 화면 경고가 같은 재료를 쓴다 — 어긋나면 톤과 표시가 따로 논다.
    note = GD.prompt_note(low, alt)
    cases.append((all(g["text"] in note for g in low),
                  "프롬프트 지시가 화면 경고와 같은 문장"))

    for good, label in cases:
        print(f"{'✓' if good else '✗'} {label}")
    return sum(1 for good, _ in cases if good)


def check_customer_material() -> int:
    """고객 재료는 **한 경로**이고, 그 고객에게 걸린 주의는 **코드가** 붙는다.

    회귀 대상 둘.
    ① 같은 고객 재료를 `briefing_qa` 노드와 `customer` 도구 두 경로가 답했다. 프롬프트·
       검증·표시 규약이 갈려서, 같은 질문이 분류에 따라 다른 답을 받았다(§3 · gap 11).
    ② 「하지 말 것」이 원장에 고객 요건이 실렸을 때만 — 즉 LLM 이 customer 도구를 부른
       턴에만 — 붙었다. 고객 상태는 코드가 이미 아는 값인데 LLM 의 도구 선택에 의존한
       것이고, 그래서 화법만 물은 턴에는 조용히 빠졌다(§8 · gap 10).

    실존 고객이 필요한 검사는 시연용 목업 9케이스의 이준호(KB-PIN 198734-1205842)를
    쓴다. "CX" 는 존재하지 않는 id 로 남겨 "고객 없음" 경로를 함께 검증한다.
    """
    from pension_agent.consult_agent import guard as GD
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0

    # ① 고객 재료 경로가 하나뿐인가.
    import importlib
    gone = importlib.util.find_spec("pension_agent.consult_agent.nodes.briefing_qa") is None
    print(f"{'✓' if gone else '✗'} 고객 재료를 답하던 두 번째 경로(briefing_qa)가 없다")
    ok += gone

    found = tools.run("customer", {"customer_id": "198734-1205842"}, "이 고객 평가금액 얼마야?")
    hit = bool(found) and "평가금액" in found["text"] and "성립 요건" in found["text"]
    print(f"{'✓' if hit else '✗'} customer 도구가 브리핑 재료(값·요건)를 싣는다")
    ok += hit

    # 화면에 뜬 AI 산문(제안 문장·근거 해설)도 재료에 실린다 — briefing_qa 노드가 갖고
    # 있던 노출 수준이다. 직원은 그 문장을 보면서 묻기 때문에, 재료에 없으면 "화면엔
    # 저렇게 써 있는데 왜 다르게 말하느냐"가 된다. LLM 없이는 산문이 비므로 스텁을 쓴다.
    from pension_agent.strategy_agent import agent as SA
    orig_propose = SA.propose
    SA.propose = lambda profile: {**orig_propose(profile),
                                  "sentence": "만기 예금을 이렇게 제안해 보세요.",
                                  "insight": "만기 자금이 대기 중이라 수익 기회를 놓칩니다."}
    try:
        found = tools.run("customer", {"customer_id": "198734-1205842"}, "브리핑 요약해줘")
        hit = bool(found) and "AI브리핑 문장" in found["text"] and "AI브리핑 근거해설" in found["text"]
    finally:
        SA.propose = orig_propose
    print(f"{'✓' if hit else '✗'} customer 도구가 화면에 뜬 AI브리핑 산문까지 재료로 싣는다")
    ok += hit

    # ── CLAUDE.md §3 「고객 정보 질의응답 — 되어야 하는 것」 재료 요건 ──────────────
    # 문서에 적어둔 것이 실제로 재료에 실리는지 본다. 아래가 하나라도 빠지면 그 질문은
    # 답이 나올 수 없고, LLM 은 없는 재료에 대해 말을 만든다.
    _mat = tools.run("customer", {"customer_id": "181245-3097614"}, "이 고객 현황")["text"]
    _NEED = [
        ("① 값 — 자산군별 금액", "고유계정대 2,000만원"),   # 비중만 있으면 금액을 못 답한다
        ("① 값 — 자산군별 비중(원장값)", "(7.7%)"),          # 4분류 반올림(8%)이 아니라 원장값
        ("① 값 — 만기 전건", "2027-02-01"),                 # 가장 가까운 한 건만이 아니다
        ("② 왜 이 고객인가", "· 왜 이 고객인가:"),
        ("② 판단근거", "· 판단근거:"),
        ("② 문제상황", "· 문제상황 1:"),
        ("② 성립 요건", "· 성립 요건:"),
    ]
    # 원장 → Profile → 재료 경로가 뚫려 있는지. 하나만 하면 값은 있는데 답은 못 한다.
    _big = tools.run("customer", {"customer_id": "188406-7352194"}, "현황")["text"]
    for _label, _needle in (("보유상품 개별 종목", "KB 퇴직연금 배당"),
                            ("판매중단 표시", "⚠판매중단"),
                            ("동연령대 비교", "동연령 평균 수익률"),
                            ("거래 활동", "1년 매매")):
        _h = _needle in _big
        print(f"{'✓' if _h else '✗'} 고객 재료: {_label}" + ("" if _h else f" — '{_needle}' 없음"))
        ok += _h

    # 과거 상담 기록은 세션 저장소에 심겨 있고(scripts/seed_sessions.py), 읽는 경로는
    # 하나다 — 화면 §14 와 대화형 history 도구가 같은 것을 본다. 원장에서 따로 읽는 두
    # 번째 경로를 만들면 같은 상담이 두 번 실린다.
    _PAST = "재투자하고 싶다"          # 송도윤 2025-10-06 상담 기록의 한 조각
    _hist = tools.run("history", {"customer_id": "188406-7352194"}, "지난번에 무슨 얘기 했어")
    hit = bool(_hist) and _PAST in _hist["text"] and "상담기록" in _hist["text"]
    print(f"{'✓' if hit else '✗'} history 도구: 과거 상담 기록을 싣는다(role=record)")
    ok += hit
    from pension_agent.strategy_agent import engine as _eng
    from pension_agent.strategy_agent.customer import get_profile as _gp
    _screen = _eng.prepare(_gp("188406-7352194"))["consult_history"]
    hit = sum(_PAST in line for line in _screen) == 1
    print(f"{'✓' if hit else '✗'} 화면 §14 도 같은 기록을 «한 번만» 본다(대화형과 답이 갈리지 않는다)")
    ok += hit

    # ISA 만기자금·납입이력 — 원장에 컬럼이 있어도 Profile 이 안 접으면 대화형은 못 본다.
    _isa_mat = tools.run("customer", {"customer_id": "188406-7352194"}, "ISA")["text"]
    hit = "ISA만기자금" in _isa_mat and "1억 2,000만원" in _isa_mat
    print(f"{'✓' if hit else '✗'} 고객 재료: ISA 만기자금이 원장에서 대화형까지 온다")
    ok += hit
    _pay_mat = tools.run("customer", {"customer_id": "176903-5528417"}, "납입")["text"]
    hit = "납입이력" in _pay_mat and "2025년" in _pay_mat
    print(f"{'✓' if hit else '✗'} 고객 재료: 연도별 납입 이력이 실린다(당해분만이 아니다)")
    ok += hit

    for _label, _needle in _NEED:
        _h = _needle in _mat
        print(f"{'✓' if _h else '✗'} 고객 재료: {_label}" + ("" if _h else f" — '{_needle}' 없음"))
        ok += _h

    # 같은 항목이 재료 안에서 두 값이 되면 안 된다 — 3분류와 자산군별의 고유계정대가
    # 각각 8% · 7.7% 로 실리던 자리(4분류 반올림 대 원장값).
    import re as _re
    _three = _re.search(r"운용현황\(3분류\)[^\n]*고유계정대 ([\d.]+)%", _mat)
    _asset = _re.search(r"자산군별[^\n]*고유계정대[^(]*\(([\d.]+)%\)", _mat)
    hit = bool(_three and _asset) and _three.group(1) == _asset.group(1)
    print(f"{'✓' if hit else '✗'} 고객 재료: 같은 항목(고유계정대 비중)이 한 값으로만 실린다"
          + ("" if hit else f" — 3분류 {_three and _three.group(1)} vs 자산군별 {_asset and _asset.group(1)}"))
    ok += hit

    # 인용 허용 집합에 후보 더미(pools)를 싣지 않는다 — 답변이 쓰지도 않을 카드의 숫자가
    # 아무 주장에나 근거를 대주면 검증이 무력해진다("만기일 2026년 9월 11일" 이 통과하던 자리).
    from pension_agent.verify import verify_texts as _vt
    _ev = tools.run("customer", {"customer_id": "198734-1205842"}, "만기")
    hit = (_vt("만기일은 2026-09-10, 금액 4,050만원이에요.", _ev["allow"])[0]
           and not _vt("만기일은 2026년 9월 11일입니다.", _ev["allow"])[0])
    print(f"{'✓' if hit else '✗'} 고객 재료: 화면 값은 인용 통과, 후보 더미가 licensing 하던 오답은 거부")
    ok += hit

    hit = tools.run("customer", {"customer_id": None}, "평가금액") is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 고객 재료를 만들지 않는다")
    ok += hit

    # ② 고객 상태 주의를 코드가 판단하는가. (이준호는 만기 요건이 성립한다)
    hit = "mat" in [c.split(":")[0] for c in GD.conditions_of("198734-1205842")]
    print(f"{'✓' if hit else '✗'} conditions_of: customer_id 만으로 고객 요건을 읽는다")
    ok += hit

    hit = GD.conditions_of(None) == [] and GD.conditions_of("없는고객") == []
    print(f"{'✓' if hit else '✗'} conditions_of: 고객이 없으면 요건을 지어내지 않는다")
    ok += hit

    # 화법만 물어 원장에 고객 재료가 **하나도 없는** 턴. 예전에는 여기서 가드가 빠졌다.
    pitch_only = [{"tool": "pitch", "query": "q", "text": "수익률 이야기를 이렇게 꺼내세요.",
                   "atomic": [], "notices": [], "notice_scopes": [],
                   "allow": ["수익률 이야기를 이렇게 꺼내세요."], "sources": [], "meta": {}}]
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "(스텁 답변)"
    try:
        # 김현수(dep·nod) — 이 요건에는 지식베이스에 대응 주의 카드가 실재한다.
        # 이준호(mat)로 재면 안 된다: 만기 요건의 주의 카드는 없어서 가드가 정당하게 빈다.
        out = P.compose({"question": "뭐라고 말하지?", "customer_id": "173544-2074623",
                         "evidence": pitch_only})
        hit = bool(out.get("guards"))
        closed = P.compose({"question": "뭐라고 말하지?", "evidence": pitch_only})
        hit = hit and not closed.get("guards")
    finally:
        P.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 원장에 고객 재료가 없어도 가드가 붙는다(고객 화면이 열려 있으면)")
    ok += hit
    return ok


def check_context_and_clarify() -> int:
    """후속 질문이 맥락을 이어받는가(§2-1 · gap 1), 그리고 모호하면 되묻는가(§5 · gap 5).

    둘을 한 자리에서 재는 이유는 **함께여야 성립하기 때문**이다. 되물은 다음 턴의
    답("타행에서요")을 이전 질문과 이어 해석하지 못하면 되묻기는 직원을 한 번 더
    귀찮게 하고 끝난다.

    회귀 대상:
    ① 히스토리가 라우팅·화법 슬롯 추출에만 전달되고 계획·작성 프롬프트에는 실리지 않았다.
       그래서 계획이 이번 질문 한 줄만 보고 재료를 골랐다.
    ② 되묻기 경로 자체가 없었다. 의도 분류가 항상 하나로 확정하고, 모호해도 기본값으로
       넘겨 한쪽을 골라 답했다 — 직원은 그게 다른 절차인 줄도 모른다.
    """
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.state import KB, format_history

    ok = 0
    history = [{"question": "실물이전 어떻게 처리해?", "stage": "계약이전",
                "pending_clarify": {"question": "어느 방향인가요?",
                                    "options": ["타행 → 당행", "당행 → 타행"]}}]

    # ① 대화 맥락이 계획·작성 프롬프트에 실리는가.
    seen: dict[str, str] = {}
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: seen.setdefault(
        "plan" if "쓸 수 있는 도구" in prompt else "compose", prompt) and '{"done": true}'
    try:
        P.plan_step({"question": "타행에서요", "history": history})
        evidence = [{"tool": "procedure", "query": "q", "text": "계약이전 절차입니다.",
                     "atomic": [], "notices": [], "notice_scopes": [],
                     "allow": ["계약이전 절차입니다."], "sources": [], "meta": {}}]
        P.compose({"question": "타행에서요", "history": history, "evidence": evidence})
    finally:
        P.generate = orig_gen
    hit = all("실물이전 어떻게 처리해?" in seen.get(k, "") for k in ("plan", "compose"))
    print(f"{'✓' if hit else '✗'} 이전 대화가 계획·작성 프롬프트에 실린다")
    ok += hit

    hit = "되물음" in format_history(history) and "타행 → 당행" in format_history(history)
    print(f"{'✓' if hit else '✗'} 되물은 내용이 다음 턴의 맥락으로 남는다")
    ok += hit

    # ② 되묻기 — 갈래가 있으면 답변 대신 질문으로 끝난다.
    evidence = [{"tool": "procedure", "query": "실물이전", "text": "타행→당행 절차 / 당행→타행 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "allow": [],
                 "sources": [], "meta": {}}]
    orig_gen = CL.generate
    CL.generate = lambda prompt, **kw: (
        '{"ask": "타행에서 가져오는 건가요, 당행에서 내보내는 건가요?",'
        ' "options": ["타행 → 당행", "당행 → 타행"]}')
    try:
        out = CL.clarify({"question": "실물이전 어떻게 처리해?", "evidence": evidence})
        hit = bool(out.get("clarify")) and "타행 → 당행" in out["answer"]
        print(f"{'✓' if hit else '✗'} 갈래가 갈리면 답변 대신 선택지를 보여주고 되묻는다")
        ok += hit

        # 연속으로 되묻지 않는다 — 상한은 코드가 정한다.
        again = CL.clarify({"question": "실물이전", "evidence": evidence,
                            "history": [{"question": "q", "pending_clarify": {"question": "?"}}]})
        hit = not again.get("clarify")
        print(f"{'✓' if hit else '✗'} 직전 턴이 되묻기였으면 다시 되묻지 않는다")
        ok += hit

        # 근거를 못 찾은 것은 모호한 것이 아니다.
        hit = not CL.clarify({"question": "실물이전", "evidence": []}).get("clarify")
        print(f"{'✓' if hit else '✗'} 근거가 0건이면 되묻지 않는다(없다고 답할 일이다)")
        ok += hit
    finally:
        CL.generate = orig_gen

    # 선택지를 못 보여주면 되묻지 않는다 — "무엇을 원하세요?" 는 되묻기가 아니다.
    CL.generate = lambda prompt, **kw: '{"ask": "무엇을 원하세요?", "options": ["하나"]}'
    try:
        hit = not CL.clarify({"question": "실물이전", "evidence": evidence}).get("clarify")
    finally:
        CL.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 갈래를 2개 이상 못 보여주면 되묻지 않는다")
    ok += hit

    # 되묻지 않기로 하면 그대로 답변으로 흘러간다.
    CL.generate = lambda prompt, **kw: '{"ask": null}'
    try:
        hit = CL.clarify({"question": "한도 얼마야?", "evidence": evidence}) == {}
    finally:
        CL.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 되묻지 않기로 하면 답변 경로를 막지 않는다")
    ok += hit

    # 되묻기 턴에는 화면 연계 제안이 붙지 않는다 — 배선으로 막는다(§5 마지막).
    hit = routing.route_answer({"clarify": {"question": "?"}}) == "__end__" \
        and routing.route_answer({}) == "offer"
    print(f"{'✓' if hit else '✗'} 되묻기 턴은 offer 를 거치지 않고 끝난다")
    ok += hit

    # ③ 되묻기의 답이 확인 응답으로 오분류돼도 막다른 안내로 끝나지 않는다(gap 19).
    #
    # 실제 사례: 되물은 다음 턴의 "2번째꺼"를 분류 LLM 이 confirm_action 으로 읽었고,
    # confirm_action 노드는 직전 턴의 화면 연계 제안(pending_action)만 찾으므로
    # "직전에 제안드린 작업이 없어요"로 끝났다. 확인할 제안이 있는지는 코드가 아는
    # 값이다 — 분기표가 LLM 분류에 의존하지 않고 계획 루프로 돌려보낸다.
    hit = routing.route_intent({"intent": "confirm_action", "history": history}) == "plan"
    print(f"{'✓' if hit else '✗'} 되묻기 직후의 확인 응답 오분류는 계획 루프로 돌아간다")
    ok += hit

    # 제안이 실제로 걸려 있으면 그대로 confirm_action 이다 — 정상 확인 경로는 안 바뀐다.
    hit = routing.route_intent(
        {"intent": "confirm_action",
         "history": [{"question": "q", "pending_action": {"label": "x"}}]}) == "confirm_action"
    print(f"{'✓' if hit else '✗'} 제안이 걸린 확인 응답은 그대로 confirm_action")
    ok += hit

    # 제안도 되묻기도 없었으면 confirm_action 노드가 사실대로 안내한다(빈 히스토리 포함).
    hit = (routing.route_intent({"intent": "confirm_action",
                                 "history": [{"question": "q"}]}) == "confirm_action"
           and routing.route_intent({"intent": "confirm_action"}) == "confirm_action")
    print(f"{'✓' if hit else '✗'} 제안도 되묻기도 없으면 '제안 없음' 안내를 유지한다")
    ok += hit

    # ④ 적합성 게이트도 이전 대화를 본다 (gap 21).
    #
    # 실제 사례: 되묻기 다음 턴 "1번꺼" 에 도구가 화면 카드를 제대로 찾아왔는데, 게이트가
    # "직원 질문: 1번꺼" 하나만 보고 판정해 전부 탈락시켰다 — 화면에는 "근거를 찾지
    # 못했습니다" 가 떴다. 히스토리를 계획·작성 프롬프트에 싣던 gap 1 이 이 프롬프트만
    # 빠뜨렸다. 후속 질문은 그 말만으로는 어떤 후보와도 맞지 않는다.
    seen: dict[str, str] = {}
    orig_gen, orig_fits = tools.generate, tools.fits_question
    tools.fits_question = _REAL_FITS          # 게이트 본체를 재야 하므로 스텁을 잠시 걷는다
    tools.generate = lambda p, **kw: seen.setdefault("p", p) and "[]"
    try:
        card = next(c for c in KB.cards if c["_kind"] == "screen")
        tools._adopt({"question": "1번꺼", "history": history}, "화면번호", [(2.0, card)], "화면")
    finally:
        tools.generate, tools.fits_question = orig_gen, orig_fits
    hit = "실물이전 어떻게 처리해?" in seen.get("p", "") and "타행 → 당행" in seen.get("p", "")
    print(f"{'✓' if hit else '✗'} 적합성 게이트 프롬프트에 이전 대화·되물은 선택지가 실린다")
    ok += hit

    # ⑤ 되묻기 턴도 근거를 밝힌다 (gap 22).
    #
    # 선택지는 근거 카드에서 나온 것인데 sources 를 비워 화면이 "근거: 없음" 이라고 말했다.
    # 직원 입장에서는 어디서 나온 갈래인지 모른 채 고르라는 말이 된다(§3).
    ev = tools._ev("screen", "q", "■ [04-12-179] 퇴직연금 상품 조회",
                   [{"id": "screen.04-12-179", "title": "퇴직연금 상품 조회", "doc": "화면번호 안내"}])
    CL.generate = lambda p, **kw: ('{"ask": "어느 쪽인가요?", '
                                   '"options": ["[04-12-179] 상품 조회", "[04-12-17A] NEW"]}')
    try:
        out = CL.clarify({"question": "퇴직연금 상품 조회 화면번호", "evidence": [ev]})
    finally:
        CL.generate = orig_gen
    hit = ([s["id"] for s in out["sources"]] == ["screen.04-12-179"]
           and all(s["role"] == tools.GROUND for s in out["sources"]))
    print(f"{'✓' if hit else '✗'} 되묻기 턴이 선택지를 만든 근거를 출처로 싣는다")
    ok += hit

    # 출처 역할 어휘는 한 곳에서 온다 — 답을 내보내는 노드가 둘이라 갈리면 화면이
    # 한쪽만 갈라 보여준다.
    hit = (P.GROUND, P.CAUTION) == (tools.GROUND, tools.CAUTION)
    print(f"{'✓' if hit else '✗'} compose·clarify 가 같은 출처 역할 어휘를 쓴다")
    ok += hit
    return ok


def check_adequacy_and_shape() -> int:
    """근거가 질문에 답이 되는지(§5 · gap 3), 답의 형태가 유형에 맞는지(§5 표 · gap 4).

    회귀 대상:
    ① 적합성 판정이 화법 도구에만 있었다. 값·절차·정의·방법론·현장 관찰은 검색 점수만으로
       채택돼서, 주제어만 겹친 카드를 거를 장치가 없었다. §6 의 점검은 전부 "틀린 것을
       막는" 검사라 여기를 대신하지 못한다 — 어긋난 카드로 쓴 답도 수치는 원장 안에 있다.
    ② 형태 요구가 작성 프롬프트의 산문 지시로만 있고 유형별 기준이 없었다. 근거가 맞아도
       값을 물었는데 화법이 나오면 답이 아니다.
    """
    from pension_agent.consult_agent.nodes import plan as P, procedure_qa
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES

    ok = 0

    # ① 게이트가 재료 종류를 가리지 않는가 — 전부 버리면 어느 도구도 근거를 못 내놓는다.
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: []
    try:
        blocked = [name for name in ("fact", "procedure", "segment", "method", "fieldtip", "pitch")
                   if tools.run(name, {"question": "세액공제 한도가 얼마야?"},
                                "세액공제 한도가 얼마야?") is not None]
    finally:
        tools.fits_question = orig
    hit = not blocked
    print(f"{'✓' if hit else '✗'} 적합성 게이트가 모든 재료 종류에 걸린다"
          + ("" if hit else f" — 통과해버린 도구 {blocked}"))
    ok += hit

    # 0건이면 게이트를 부르지 않는다 — 부를 이유가 없는 자리에서 LLM 을 쓰지 않는다.
    called: list[str] = []
    tools.fits_question = lambda q, h, kind="", history=None: (called.append(kind), h)[1]
    try:
        tools.run("fact", {"question": "오늘 서울 날씨 어때?"}, "오늘 서울 날씨 어때?")
    finally:
        tools.fits_question = orig
    hit = not called
    print(f"{'✓' if hit else '✗'} 후보가 0건이면 게이트를 돌리지 않는다")
    ok += hit

    # 후보 한 줄이 종류를 가리지 않고 만들어지는가(팩트는 title 이 없고 label 을 쓴다).
    head = tools._headline({"id": "f.1", "label": "세액공제 한도", "value": "연 900만원"})
    hit = "세액공제 한도" in head and "연 900만원" in head
    print(f"{'✓' if hit else '✗'} 후보 요약이 종류마다 다른 필드 이름을 흡수한다")
    ok += hit

    # 게이트는 **카드 하나씩** 판정한다 — 옆 후보가 빗나갔다고 맞는 카드까지 버리지 않는다.
    #
    # 회귀 대상: 처음에는 후보 묶음 전체를 한 번에 YES/NO 로 물었다. "디폴트옵션 변경
    # 화면번호 알려줘" 의 후보 2장 중 하나가 다른 주제였다는 이유로 둘 다 버려졌고,
    # 지식베이스에 멀쩡히 있는 절차를 "근거를 찾지 못했다"고 답했다.
    q = "디폴트옵션 변경 화면번호 알려줘"
    candidates = procedure_qa.search(q)
    keep = candidates[-1][1]["id"] if candidates else ""
    tools.fits_question = lambda question, h, kind="", history=None: [x for x in h if x[1]["id"] == keep]
    try:
        found = tools.run("procedure", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(candidates) and bool(found) and found["sources"][0]["id"] == keep \
        and len(found["sources"]) == 1
    print(f"{'✓' if hit else '✗'} 후보 일부만 맞으면 그것만 남긴다(전부 버리지 않는다)")
    ok += hit

    # 남길 것이 하나도 없을 때만 근거 없음이다.
    tools.fits_question = lambda question, h, kind="", history=None: []
    try:
        hit = tools.run("procedure", {"question": q}, q) is None
    finally:
        tools.fits_question = orig
    print(f"{'✓' if hit else '✗'} 맞는 후보가 하나도 없을 때만 근거를 내놓지 않는다")
    ok += hit

    # LLM 이 없는 id 를 지어내도 실재 후보와 대조해 걸러낸다.
    orig_gen = tools.generate
    tools.generate = lambda prompt, **kw: '["없는카드id"]'
    try:
        hit = _REAL_FITS(q, candidates, "업무 처리 절차") == []
    finally:
        tools.generate = orig_gen
    print(f"{'✓' if hit else '✗'} 지어낸 id 는 실재 후보와 대조해 걸러낸다")
    ok += hit

    # ② 답의 형태 요구가 **원장에 실린 재료의 것만** 실리는가.
    def ev(tool):
        return {"tool": tool, "query": "q", "text": f"{tool} 근거", "atomic": [], "notices": [],
                "notice_scopes": [], "allow": [f"{tool} 근거"], "sources": [], "meta": {}}

    block = P._shape_block([ev("fact"), ev("pitch")])
    hit = (ANSWER_SHAPES["fact"] in block and ANSWER_SHAPES["pitch"] in block
           and ANSWER_SHAPES["procedure"] not in block)
    print(f"{'✓' if hit else '✗'} 쓴 재료의 형태 요구만 싣는다(안 쓴 재료의 것은 빼고)")
    ok += hit

    hit = P._shape_block([]) == ""
    print(f"{'✓' if hit else '✗'} 재료가 없으면 형태 요구도 비운다")
    ok += hit

    # 등록된 도구는 전부 형태 요구를 갖는다 — 새 도구를 붙이고 여기를 빼먹으면
    # 그 재료만 조용히 형태 기준 없이 답해진다.
    missing = [n for n in tools.TOOLS if n not in ANSWER_SHAPES]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 모든 도구에 형태 요구가 선언돼 있다"
          + ("" if hit else f" — 빠진 도구 {missing}"))
    ok += hit

    # 실제 프롬프트에 그 요구가 실리는가.
    seen: dict[str, str] = {}
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: seen.setdefault("compose", prompt) and "(스텁)"
    try:
        P.compose({"question": "한도 얼마야?", "evidence": [ev("fact")]})
    finally:
        P.generate = orig_gen
    hit = ANSWER_SHAPES["fact"] in seen.get("compose", "")
    print(f"{'✓' if hit else '✗'} 형태 요구가 작성 프롬프트에 실린다")
    ok += hit
    return ok


def check_material_marks() -> int:
    """재료 성격 표시 — 어느 자료에서 왔고, 고객에게 그대로 옮겨도 되는지 (§7 · gap 8·13).

    회귀 대상:
    ① `customer_facing` 이 참일 때 "안내 가능" 표시만 있고, **내부용 재료가 실렸을 때의
       주의는 없었다.** 직원용 에이전트라 내부용 자료도 답변에 쓰는데, 그러면 무엇을
       고객에게 옮기면 안 되는지 직원이 알 방법이 없었다.
    ② 신뢰 등급이 즉답 카드에만 붙었다. 일반 답변은 현장 관찰 한 종류만 전용 문구로
       붙였고, 본부 공식·대외 공개·교육자료 구분은 답변에 나타나지 않았다 — 현장 노하우가
       본부 지침으로 읽히면 그게 곧 잘못된 안내다.
    """
    from pension_agent.consult_agent import marks as M
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.state import KB

    ok = 0

    # 등급은 문서 레지스트리에서 나온다 — 카드 내용을 보고 추론하지 않는다.
    field = next(c for c in KB.cards if c["_kind"] == "fieldtip")
    hit = M.tier_of(KB, field) == M.TIER_NOTE["현장팁"]
    print(f"{'✓' if hit else '✗'} 신뢰 등급을 문서 레지스트리의 tier 에서 그대로 옮긴다")
    ok += hit

    hit = M.tier_of(KB, {"_source": {"doc": "없는문서"}}) is None and M.notes_for(KB, []) == []
    print(f"{'✓' if hit else '✗'} 문서를 못 찾으면 등급을 지어내지 않는다")
    ok += hit

    # 내부용 주의는 선언이 **거짓일 때만**. 선언이 없는 종류(pitch·method·segment)는
    # 판단 근거가 없다는 뜻이라 아무 쪽으로도 세지 않는다.
    internal = {"_source": {"doc": "없는문서"}, "customer_facing": False}
    facing = {"_source": {"doc": "없는문서"}, "customer_facing": True}
    undeclared = {"_source": {"doc": "없는문서"}}
    hit = (M.notes_for(KB, [internal]) == [M.INTERNAL_NOTE]
           and M.notes_for(KB, [facing]) == []
           and M.notes_for(KB, [undeclared]) == [])
    print(f"{'✓' if hit else '✗'} 내부용 주의는 customer_facing 선언이 거짓일 때만 붙는다")
    ok += hit

    # 같은 등급을 여러 장 썼다고 같은 문장을 여러 번 세우지 않는다.
    tips = [c for c in KB.cards if c["_kind"] == "fieldtip"][:3]
    hit = M.notes_for(KB, tips) == [M.TIER_NOTE["현장팁"]]
    print(f"{'✓' if hit else '✗'} 같은 표시를 겹쳐 세우지 않는다")
    ok += hit

    # 표시가 실제로 답변에 붙는가 — 등급 종류를 가리지 않고.
    def ev(marks):
        return {"tool": "fact", "query": "q", "text": "근거 블록", "atomic": [], "notices": [],
                "notice_scopes": [], "allow": ["근거 블록"], "marks": marks,
                "sources": [], "meta": {}}

    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "이렇게 안내하시면 돼요."
    try:
        out = P.compose({"question": "q",
                         "evidence": [ev([M.TIER_NOTE["본부공식"], M.INTERNAL_NOTE])]})
        hit = M.TIER_NOTE["본부공식"] in out["answer"] and M.INTERNAL_NOTE in out["answer"]
        print(f"{'✓' if hit else '✗'} 본부 공식·내부용 표시가 일반 답변에도 붙는다")
        ok += hit

        # 답변이 이미 같은 말을 했으면 겹쳐 붙이지 않는다(§7 — 표시가 늘수록 묻힌다).
        P.generate = lambda prompt, **kw: f"이건 {M.TIER_NOTE['본부공식']} 근거예요."
        out = P.compose({"question": "q", "evidence": [ev([M.TIER_NOTE["본부공식"]])]})
        hit = out["answer"].count(M.TIER_NOTE["본부공식"]) == 1
        print(f"{'✓' if hit else '✗'} 답변이 이미 밝힌 표시를 겹쳐 세우지 않는다")
        ok += hit

        # 표시가 없으면 빈 머리말만 남기지 않는다.
        out = P.compose({"question": "q", "evidence": [ev([])]})
        hit = P.MATERIAL_MARKS not in out["answer"]
        print(f"{'✓' if hit else '✗'} 붙일 표시가 없으면 머리말도 붙이지 않는다")
        ok += hit
    finally:
        P.generate = orig_gen

    # 도구가 실제로 표시를 실어 보내는가(선언이 아니라 배선을 본다).
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        q = "사전 고지를 안 하면 민원으로 돌아온다는데 현장에서는 어떻게 하나요?"
        found = tools.run("fieldtip", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and M.TIER_NOTE["현장팁"] in (found.get("marks") or [])
    print(f"{'✓' if hit else '✗'} 도구가 근거와 함께 재료 성격 표시를 돌려준다")
    ok += hit

    return ok


def check_relations() -> int:
    """관계 기반 점검 — 값–조건 오짝과 알려진 오답을 데이터 선언으로 잡는가 (§6 · gap 2·6).

    회귀 대상: `verify_texts` 는 수치의 **집합 포함** 검사라 잘못 짝지은 것을 못 잡았다.
    "5,500만원 이하 16.5% / 초과 13.2%" 가 원장에 있으면 "초과면 16.5%" 도 통과한다 —
    두 숫자가 다 원장에 있으므로. 그 구멍 때문에 원문 문장을 통째로 답변에 싣게 강제했고
    (`atomic`), 답변이 인용문 나열이 됐다.

    **검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다 나쁘다** — 직원은
    왜 막혔는지 알 수 없다. 그래서 잡는 것만큼 통과시키는 것도 함께 잰다.
    """
    from pension_agent.consult_agent import relations as R
    from pension_agent.consult_agent.nodes import facts_qa, plan as P
    from pension_agent.consult_agent.state import KB

    ok = 0
    f2 = KB.facts.get("fact.k04.f2") or {}

    hit = len(f2.get("tiers") or []) >= 2
    print(f"{'✓' if hit else '✗'} 세액공제 팩트가 조건–값 쌍을 선언한다 ({len(f2.get('tiers') or [])}쌍)")
    ok += hit

    hit = bool(R.mispaired("총급여 5,500만원 초과면 16.5% 를 공제받아요.", f2.get("tiers") or []))
    print(f"{'✓' if hit else '✗'} 조건과 값을 뒤집은 답변을 잡는다(수치 집합 검사는 못 잡던 것)")
    ok += hit

    hit = not R.mispaired("총급여 5,500만원 이하면 16.5%, 초과면 13.2% 예요.",
                          f2.get("tiers") or [])
    print(f"{'✓' if hit else '✗'} 옳게 짝지은 답변은 막지 않는다")
    ok += hit

    # 조건을 다른 말로 풀어 쓴 옳은 답 — 판정 불가이지 위반이 아니다.
    hit = not R.mispaired("총급여가 5,500만원을 넘으면 13.2% 가 적용돼요.", f2.get("tiers") or [])
    print(f"{'✓' if hit else '✗'} 풀어 쓴 옳은 답을 위반으로 바꾸지 않는다(판정 불가 ≠ 위반)")
    ok += hit

    hit = R.known_wrong("5,500만원 이상 13.2% 로 안내하시면 돼요.", f2.get("pitfalls") or []) != []
    print(f"{'✓' if hit else '✗'} 행원들이 적어둔 알려진 오답을 그대로 말하면 잡는다")
    ok += hit

    # 인용은 주장이 아니다 — 그 문구를 «틀렸다»고 짚는 답변까지 잡던 자리다. 카드의
    # verify_points 가 직원에게 그렇게 짚어주라고 적어둔 바로 그 문구라, 데이터가 시킨 일을
    # 했다고 벌하는 꼴이었다(폐기 뒤 나가는 카드 원문에는 같은 문구가 그대로 실려 있다).
    pf = f2.get("pitfalls") or []
    hit = (not R.known_wrong('"5,500만원 이상 13.2%"는 오기예요. "초과"가 맞습니다.', pf)
           and not R.known_wrong("「5,500만원 이상 13.2%」는 틀린 표기니 주의하세요.", pf))
    print(f"{'✓' if hit else '✗'} 오답 문구를 «틀렸다»고 짚는 정정은 막지 않는다")
    ok += hit

    # 정정으로 보는 조건은 둘 다다 — 하나만으로는 헐겁다.
    hit = R.known_wrong("오기 주의하시고, 5,500만원 이상 13.2% 로 안내하세요.", pf) != []
    print(f"{'✓' if hit else '✗'} 정정 표지만 곁에 있고 문구는 주장했으면 잡는다")
    ok += hit

    hit = R.known_wrong('고객님께 "5,500만원 이상 13.2%" 라고 안내드릴게요.', pf) != []
    print(f"{'✓' if hit else '✗'} 따옴표만 있고 정정 표지가 없으면 잡는다(고객 대사도 따옴표에 담긴다)")
    ok += hit

    hit = R.known_wrong('"5,500만원 이상 13.2%"는 오기예요. '
                        "그래도 5,500만원 이상 13.2% 로 하세요.", pf) != []
    print(f"{'✓' if hit else '✗'} 한쪽에서 정정하고 다른 쪽에서 그대로 말하면 잡는다")
    ok += hit

    # 오답 문자열은 **구절**이어야 한다 — 값 하나짜리는 다른 팩트의 맞는 문장에도 들어간다.
    bare = [w for f in KB.facts.values() for x in f.get("pitfalls") or []
            for w in x.get("wrong") or [] if " " not in w and len(w) < 8]
    hit = not bare
    print(f"{'✓' if hit else '✗'} 값 하나짜리 오답 문자열은 대조에 쓰지 않는다"
          + ("" if hit else f" — {bare[:3]}"))
    ok += hit

    # 옳은 표현의 인용(→ O 로 표시된 것, 기준을 짚은 것)이 오답 목록에 섞이지 않았는가.
    every = [w for f in KB.facts.values() for x in f.get("pitfalls") or []
             for w in x.get("wrong") or []]
    hit = "평가금액" not in every and "퇴직금 포함 시 5년 전 가능" not in every
    print(f"{'✓' if hit else '✗'} 옳은 표현의 인용을 오답으로 등록하지 않는다")
    ok += hit

    # 관계를 선언한 팩트는 원문 강제가 해제되고(이행 순서 3), 선언이 없는 팩트는 같은
    # 원장에 섞여 있어도 그대로 강제된다 — 저작된 만큼만 물러난다. 두 종류를 한 원장에
    # 함께 올려서 본다(검색어에 의존하면 데이터가 바뀔 때 무엇을 재는지 흐려진다 —
    # 실제로 1세대 손저작 팩트가 지워졌을 때 이 검사가 조용히 빈 목록을 재고 있었다).
    by_id = {f["id"]: f for f in KB.facts.values()}
    with_rel = next(f for f in by_id.values() if R.declared(f) and f.get("value"))
    without_rel = next(f for f in by_id.values() if not R.declared(f) and f.get("value"))
    orig_fits, orig_search = tools.fits_question, facts_qa.search
    tools.fits_question = lambda question, h, kind="", history=None: h
    facts_qa.search = lambda question: [(2.0, with_rel), (2.0, without_rel)]
    try:
        found = tools.run("fact", {"question": "q"}, "세액공제 공제율")
    finally:
        tools.fits_question, facts_qa.search = orig_fits, orig_search
    atomic = (found or {}).get("atomic") or []

    hit = with_rel["value"] not in atomic
    print(f"{'✓' if hit else '✗'} 관계를 선언한 팩트는 원문을 통째로 강제하지 않는다")
    ok += hit

    hit = without_rel["value"] in atomic
    print(f"{'✓' if hit else '✗'} 선언이 없는 팩트는 같은 원장에서도 원문 강제가 남는다")
    ok += hit

    # 선언이 없는 카드는 아직 원문 강제가 남는다 — 저작이 넓어지는 만큼만 물러난다.
    hit = not R.declared({"tiers": [], "pitfalls": [{"wrong": [], "why": "메모"}]}) \
        and R.declared({"tiers": [{"when": "a", "value": "b"}]})
    print(f"{'✓' if hit else '✗'} 선언이 없으면 관계 검사가 원문 강제를 대신하지 않는다")
    ok += hit

    # compose 가 실제로 관계 위반을 걸러내는가(배선을 본다).
    ev = {"tool": "fact", "query": "q", "text": (f2.get("value") or "")[:200],
          "atomic": [], "notices": [], "notice_scopes": [],
          "allow": [f2.get("value") or ""], "marks": [], "related": [f2],
          "sources": [], "meta": {}}
    orig_gen = P.generate
    P.generate = lambda prompt, **kw: "총급여 5,500만원 초과면 16.5% 예요."
    try:
        out = P.compose({"question": "세액공제율이 얼마야?", "evidence": [ev]})
        hit = "총급여 5,500만원 초과면 16.5%" not in out["answer"]
    finally:
        P.generate = orig_gen
    print(f"{'✓' if hit else '✗'} compose 가 관계를 어긴 생성문을 내보내지 않는다")
    ok += hit
    return ok


def check_turn_cost() -> int:
    """값 하나 묻는 턴이 LLM 을 몇 번 부르는가 — 직원은 상담 중에 이 화면을 읽는다.

    회귀 대상: "이 고객 예금 잔액 얼마지" 한 마디가 **순차 LLM 호출 6번**으로 끝났다.
    의도분류 → 화법 슬롯 분해 → 계획(도구 고르기) → 계획(끝났다고 말하기) → 되묻기 판정
    → 답변 작성. 이 중 셋은 이 질문에 아무것도 하지 않는다:

      · 화법 슬롯 분해 — `pitch` 를 부르지도 않는 턴인데 모든 턴 앞에 노드로 있었다.
      · 계획의 두 번째 호출 — 재료 하나로 끝나는데 "이제 됐다"를 따로 말하게 했다.
      · 되묻기 판정 — 열려 있는 고객의 재료에는 갈래가 없다(어느 고객인지가 정해져 있다).

    지연은 정확성과 맞바꾸는 것이 아니다. **하는 일이 없는 호출을 빼는 것**이다.
    """
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    ev_customer = {"tool": "customer", "query": "q", "text": "· 평가금액 2억 3,000만원",
                   "atomic": [], "notices": [], "notice_scopes": [], "marks": [],
                   "related": [], "allow": ["· 평가금액 2억 3,000만원"],
                   "sources": [{"id": "briefing.CX"}], "meta": {}}

    # ① 화법 슬롯 분해가 화법을 안 부르는 턴에서는 아예 안 돈다.
    called: list[str] = []
    orig_extract = pitch.extract_slots
    pitch.extract_slots = lambda st: called.append("slots") or {}
    orig_plan_gen = P.generate
    P.generate = lambda prompt, **kw: '{"tool": "customer", "query": "예금 잔액", "last": true}'
    try:
        state = {"question": "이 고객 예금 잔액 얼마지", "customer_id": "198734-1205842"}
        state.update(P.plan_step(state))
    finally:
        pitch.extract_slots, P.generate = orig_extract, orig_plan_gen
    hit = not called
    print(f"{'✓' if hit else '✗'} 화법을 안 부르는 턴은 슬롯 분해 호출이 없다")
    ok += hit

    # ② 계획이 한 호출로 끝난다("last": true) — 그 도구가 실제로 재료를 내놨을 때만.
    hit = state.get("plan_done") is True and len(state.get("plan_calls") or []) == 1
    print(f"{'✓' if hit else '✗'} 재료 하나로 끝나는 질문은 계획 호출 1번으로 끝난다")
    ok += hit

    # ③ 고객 재료만 있는 턴은 되묻기 판정을 돌리지 않는다.
    called.clear()
    orig_cl_gen = CL.generate
    CL.generate = lambda prompt, **kw: called.append("clarify") or '{"ask": null}'
    try:
        out = CL.clarify({"question": "이 고객 예금 잔액 얼마지", "evidence": [ev_customer]})
    finally:
        CL.generate = orig_cl_gen
    hit = not called and out == {}
    print(f"{'✓' if hit else '✗'} 갈래가 있을 수 없는 재료뿐이면 되묻기 판정을 돌리지 않는다")
    ok += hit

    # 지식 재료가 섞이면 판정은 그대로 돈다 — 아낀 것이 기능을 없앤 것이 아니다.
    ev_proc = {**ev_customer, "tool": "procedure", "text": "절차 블록"}
    called.clear()
    CL.generate = lambda prompt, **kw: called.append("clarify") or '{"ask": null}'
    try:
        CL.clarify({"question": "실물이전 절차", "evidence": [ev_customer, ev_proc]})
    finally:
        CL.generate = orig_cl_gen
    hit = called == ["clarify"]
    print(f"{'✓' if hit else '✗'} 지식 재료가 섞이면 되묻기 판정은 그대로 돈다")
    ok += hit

    # ④ 화법을 부르는 턴에는 슬롯 분해가 살아 있다(n-gram 폴백에 들어갈 때).
    called.clear()
    orig_extract, orig_pick = pitch.extract_slots, tools.llm_pick
    orig_fits = tools.fits_question
    pitch.extract_slots = lambda st: called.append("slots") or {}
    tools.llm_pick = lambda kinds, q: []
    tools.fits_question = lambda question, h, kind="", history=None: h
    try:
        tools.run("pitch", {"question": "수수료 부담된다고 하시네요"}, "수수료 부담")
    finally:
        pitch.extract_slots, tools.llm_pick = orig_extract, orig_pick
        tools.fits_question = orig_fits
    hit = called == ["slots"]
    print(f"{'✓' if hit else '✗'} 화법 도구가 n-gram 으로 물러설 때는 슬롯을 뽑는다")
    ok += hit

    # ⑤ 화법을 안 쓴 답변에 '파악된 상황' 줄을 붙이지 않는다(없는 상담 상황을 상상하게 둔다).
    hit = pitch.situation_line("situation", {}) == "" and \
        "고객유형" in pitch.situation_line("situation", {"customer_type": "사업자"})
    print(f"{'✓' if hit else '✗'} 슬롯이 없으면 '파악된 상황' 줄을 싣지 않는다")
    ok += hit
    return ok


def check_miss_recovery() -> int:
    """계획이 질의·도구를 잘못 골랐다고 **지식베이스에 있는 답이 사라지지 않는가.**

    회귀 대상 셋. 셋 다 "분명 있는 지식인데 못 찾는다"로 나타난다.

    ① `"last": true` 를 재료 없이도 존중했다. 계획이 도구 하나를 고르고 "이걸로 끝"이라고
       말했는데 그 도구가 근거를 못 찾으면, 다른 도구를 써 볼 기회 없이 턴이 '근거 없음'으로
       끝났다. 한 바퀴를 아끼는 것은 재료를 실제로 얻었을 때의 이야기다.
    ② 계획이 만든 질의는 질문을 줄여 쓴 것이라 검색이 기대는 말이 빠질 수 있다 —
       "포트폴리오 운용현황 조회 화면 번호는?"이 "운용현황 조회 화면번호"가 되면 n-gram 이
       0건을 낸다(원문으로는 찾는다). 못 찾으면 원문으로 한 번 더 찾는다.
    ③ '없다'가 무엇을 찾아봤는지 말하지 않았다. 그러면 직원도 우리도 왜 못 찾았는지
       알 수 없다 — 진단이 화면에서 끝나야 한다.
    """
    from pension_agent.consult_agent.nodes import plan as P
    from pension_agent.consult_agent.nodes import procedure_qa

    ok = 0

    # ① 근거를 못 찾으면 last 를 존중하지 않는다.
    orig_gen, orig_run = P.generate, P.tools.run
    P.generate = lambda prompt, **kw: '{"tool": "procedure", "query": "없는 절차", "last": true}'
    P.tools.run = lambda name, state, query: None
    try:
        state = {"question": "질문"}
        state.update(P.plan_step(state))
        hit = not state.get("plan_done")
    finally:
        P.generate, P.tools.run = orig_gen, orig_run
    print(f"{'✓' if hit else '✗'} 도구가 근거를 못 찾으면 'last' 로 루프를 끝내지 않는다")
    ok += hit

    # 재료를 얻었으면 그대로 한 바퀴를 아낀다 — 고친 것이 최적화를 없앤 것이 아니다.
    P.generate = lambda prompt, **kw: '{"tool": "procedure", "query": "q", "last": true}'
    P.tools.run = lambda name, state, query: {"tool": name, "query": query, "text": "블록",
                                              "atomic": [], "notices": [], "notice_scopes": [],
                                              "marks": [], "related": [], "allow": ["블록"],
                                              "sources": [], "meta": {}}
    try:
        state = {"question": "질문"}
        state.update(P.plan_step(state))
        hit = state.get("plan_done") is True
    finally:
        P.generate, P.tools.run = orig_gen, orig_run
    print(f"{'✓' if hit else '✗'} 재료를 얻었으면 'last' 로 한 바퀴를 아낀다")
    ok += hit

    # ② 줄여 쓴 질의가 0건이면 직원의 원문 질문으로 한 번 더 찾는다.
    question = "포트폴리오 운용현황 조회 화면 번호는?"
    shrunk = "운용현황 조회 화면번호"
    orig_fits = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        hit = (not procedure_qa.search(shrunk)                       # 줄여 쓰면 0건인데
               and bool(procedure_qa.search(question))               # 원문으로는 찾고
               and tools.run("procedure", {"question": question}, shrunk) is not None)
    finally:
        tools.fits_question = orig_fits
    print(f"{'✓' if hit else '✗'} 계획의 질의가 0건이면 원문 질문으로 한 번 더 찾는다")
    ok += hit

    # 재검색은 **같은 도구·같은 지식베이스**다 — 근거의 경계를 넓히지 않는다.
    tried: list[str] = []
    orig_tool = tools.TOOLS["procedure"]
    tools.TOOLS["procedure"] = tools.Tool(
        "procedure", orig_tool.desc, lambda st, q: tried.append(q) or None)
    try:
        tools.run("procedure", {"question": question}, shrunk)
    finally:
        tools.TOOLS["procedure"] = orig_tool
    hit = tried == [shrunk, question]
    print(f"{'✓' if hit else '✗'} 재시도는 질의 하나만 늘린다(도구·지식베이스는 그대로)")
    ok += hit

    # 원문과 질의가 같으면 두 번 부르지 않는다.
    tried.clear()
    tools.TOOLS["procedure"] = tools.Tool(
        "procedure", orig_tool.desc, lambda st, q: tried.append(q) or None)
    try:
        tools.run("procedure", {"question": question}, question)
    finally:
        tools.TOOLS["procedure"] = orig_tool
    hit = tried == [question]
    print(f"{'✓' if hit else '✗'} 질의가 원문과 같으면 헛되이 두 번 부르지 않는다")
    ok += hit

    # ③ '없다'가 무엇을 찾아봤는지 말한다.
    answer = P._no_evidence({"plan_calls": ["procedure:운용현황 조회 화면번호"]})
    hit = "찾아본 곳" in answer and "운용현황 조회 화면번호" in answer
    print(f"{'✓' if hit else '✗'} '근거 없음'이 무엇을 어떤 말로 찾아봤는지 밝힌다")
    ok += hit

    hit = P._no_evidence({}) == P.NO_EVIDENCE
    print(f"{'✓' if hit else '✗'} 아무것도 안 불러본 턴에는 빈 '찾아본 곳'을 붙이지 않는다")
    ok += hit
    return ok


#: 되묻기 판정 골든셋 — «되물어야 하는 질문»과 «되물으면 안 되는 질문»을 실제 지식베이스
#: 재료로 고정한다. 판정 자체는 LLM 이 하므로 여기서 재는 것은 **판정이 내려졌을 때 턴이
#: 어떻게 끝나는가**다. 그 배선이 바뀌지 않았음을 보증해야, 되묻기 판정을 다른 자리로
#: 옮기는 변경(예: 작성과 동시 실행)이 답을 바꾸지 않았다고 말할 수 있다.
#:
#: (질문, 도구, 판정, 되묻기로 끝나야 하나, 근거에 있어야 할 갈래 표시)
_CLARIFY_GOLDEN = (
    # ① 진짜 갈래 — 근거에 신청 경로가 셋이라 어느 쪽인지 정해야 답이 갈린다.
    ("계약이전 어떻게 신청해?", "procedure",
     {"ask": "어느 경로로 신청하시나요?", "options": ["후선 의뢰", "스타뱅킹", "인터넷뱅킹"]},
     True, "3경로"),
    # ② 가짜 갈래 — 카드는 여러 장이지만 답은 화면번호 하나로 확정된다. 카드 수는 갈래가
    #    아니다("근거가 여러 장 = 모호하다"로 읽으면 답할 수 있는 질문에 되묻게 된다).
    ("실물이전 가능여부 조회 화면번호?", "screen", {"ask": None}, False, "06-AD-020"),
)


def check_clarify_golden() -> int:
    """되묻기 판정 골든셋 — 판정이 내려진 뒤 **턴이 어떻게 끝나는가**를 실제 재료로 고정한다.

    §5 가 정한 것은 둘이다. 되물으면 그 턴은 답변도 화면 연계 제안도 없이 끝나고(선택지와
    출처만 나간다), 되묻지 않으면 답변 경로를 막지 않는다. 이 검사는 그 두 갈래를 **그래프
    전체로** 통과시켜 잰다 — 노드 하나만 직접 부르면 배선이 바뀌었을 때 조용히 지나간다.

    판정 자체(LLM)는 여기서 재지 않는다. 재료는 진짜 지식베이스에서 오고, 그 재료에 갈래가
    실제로 실려 있는지(=LLM 이 볼 수 있는지)까지가 코드가 보증할 수 있는 범위다.
    """
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    for question, tool, verdict, should_ask, marker in _CLARIFY_GOLDEN:
        found = tools.run(tool, {"question": question}, question)
        if not found:
            print(f"✗ 골든셋 재료 확보: {tool}({question}) → 근거 없음")
            continue

        # 재료에 갈래(또는 확정 답)가 실제로 실려 있나 — 없으면 판정은 근거 없는 추측이 된다.
        hit = marker in found["text"]
        print(f"{'✓' if hit else '✗'} 골든셋 재료에 «{marker}» 가 실려 있다 — {question}")
        ok += hit

        seen: list[str] = []
        orig_cl, orig_plan_node, orig_gen = CL.generate, G.plan_step, P.generate
        CL.generate = lambda prompt, **kw: (seen.append(prompt), json.dumps(verdict))[1]
        # 계획은 재료를 이미 넣어 둔 채로 끝낸다 — 이 검사가 재는 것은 판정 뒤의 배선이지
        # 도구 선택이 아니다(모듈 전역 stub_plan_pitch 는 화법 재료로 덮어쓴다).
        G.plan_step = lambda state: {"plan_done": True}
        P.generate = lambda prompt, **kw: "안내드릴게요."
        try:
            agent = G.build_agent()
            out = agent.invoke({"question": question, "evidence": [found],
                                "plan_calls": [f"{tool}:{question}"]})
        finally:
            CL.generate, G.plan_step, P.generate = orig_cl, orig_plan_node, orig_gen

        asked = bool(out.get("clarify"))
        hit = asked == should_ask
        print(f"{'✓' if hit else '✗'} {'되묻고 끝난다' if should_ask else '답변으로 흘러간다'}"
              f" — {question}")
        ok += hit

        if should_ask:
            # 되묻기 턴 — 선택지가 답으로 나가고, 출처가 실리고(§5 마지막·gap 22),
            # 화면 연계 제안은 붙지 않는다(§5 · §10 은 다른 사건이다).
            hit = all(o in out["answer"] for o in verdict["options"]) \
                and bool(out.get("sources")) and not out.get("pending_action")
            print(f"{'✓' if hit else '✗'} 되묻기 턴: 선택지+출처가 나가고 연계 제안은 없다")
        else:
            # 되묻지 않은 턴 — 답변이 나가고 되묻기 흔적이 남지 않는다.
            hit = bool(out.get("answer")) and not out.get("clarify")
            print(f"{'✓' if hit else '✗'} 되묻지 않은 턴: 답변이 그대로 나간다")
        ok += hit

        # 판정 프롬프트가 그 재료를 실제로 봤나 — 못 보면 판정은 질문 한 줄로 하는 추측이다.
        hit = bool(seen) and marker in seen[0]
        print(f"{'✓' if hit else '✗'} 판정 프롬프트에 근거가 실린다 — {question}")
        ok += hit

    # ③ 맥락으로 갈래가 이미 정해진 후속 질문 — 판정 프롬프트가 이전 대화를 본다.
    #    "타행에서 가져오려는 고객"이 앞 턴에 나왔으면 방향은 정해진 것이고, 여기서 또
    #    되물으면 직원은 방금 말한 것을 다시 말해야 한다(§5 "맥락으로 추측이 서면 되묻지
    #    않는다"). 그 판단의 재료가 프롬프트에 실리는지가 코드의 몫이다.
    history = [{"question": "타행에서 퇴직금 가져오려는 고객인데 뭐라고 말하지",
                "stage": "계약이전"}]
    evidence = [{"tool": "procedure", "query": "계약이전", "text": "전입 절차 / 전출 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                 "allow": [], "sources": [], "meta": {}}]
    seen = []
    orig_cl = CL.generate
    CL.generate = lambda prompt, **kw: (seen.append(prompt), '{"ask": null}')[1]
    try:
        out = CL.clarify({"question": "그럼 계약이전은 어떻게 신청해?",
                          "history": history, "evidence": evidence})
    finally:
        CL.generate = orig_cl
    hit = bool(seen) and "타행에서 퇴직금 가져오려는 고객" in seen[0] and out == {}
    print(f"{'✓' if hit else '✗'} 판정 프롬프트가 이전 대화를 본다(맥락으로 갈래가 정해진 후속 질문)")
    ok += hit

    # ④ 갈래가 있을 수 없는 재료뿐이면 판정 자체를 돌리지 않는다 — 오판의 기회를 없앤다.
    #    (check_turn_cost 가 같은 것을 '아낀 호출' 쪽에서 재고, 여기서는 '판정 정확도' 쪽에서 잰다.)
    called: list[str] = []
    CL.generate = lambda prompt, **kw: (called.append("clarify"), '{"ask": null}')[1]
    try:
        for tool_name in ("customer", "history", "date"):
            CL.clarify({"question": "이 고객 평가금액 얼마야",
                        "evidence": [{**evidence[0], "tool": tool_name}]})
    finally:
        CL.generate = orig_cl
    hit = not called
    print(f"{'✓' if hit else '✗'} 고객·상담기록·날짜 재료뿐이면 되묻기 판정을 돌리지 않는다")
    ok += hit
    return ok


def check_answer_parallel() -> int:
    """되묻기 판정과 답변 작성을 동시에 돌려도 **답이 달라지지 않는가**(nodes/answer.py).

    아낀 것은 순차 왕복 하나이고, 아끼려고 판정을 건너뛰거나 규약을 바꾸지 않았다는 것이
    이 검사의 전부다. 세 가지를 고정한다:

      ① 되묻기로 결정되면 **써 둔 답은 나가지 않는다** — 투기 실행이 §5 를 뚫으면 안 된다.
      ② 판정이 없는 턴(근거 0건·갈래 없는 재료)은 스레드를 띄우지 않고 그대로 작성한다.
      ③ 진행 표시가 스레드를 건너간다 — ContextVar 는 자동으로 따라가지 않아서, 복사를
         빠뜨리면 "작성하고 있어요"가 조용히 사라진다(progress.py).

    판정이 LLM 장애로 죽었을 때의 답도 직렬일 때와 같아야 한다 — 그때는 원인만 남기고
    작성 결과가 나갔다(route_clarify 가 clarify 키만 봤다). §11 이 요구하는 것은 «어느
    단계에서 깨졌든 직원이 받는 답이 같을 것»이고, 작성이 성공했다면 그 답은 게이트를
    통과한 답이다.
    """
    from pension_agent.consult_agent import progress as PROG
    from pension_agent.consult_agent.nodes import answer as A
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    evidence = [{"tool": "procedure", "query": "q", "text": "전입 절차 / 전출 절차",
                 "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                 "allow": ["전입 절차 / 전출 절차"], "sources": [{"id": "proc.x"}], "meta": {}}]
    state = {"question": "계약이전 어떻게 신청해?", "evidence": evidence}

    # ① 되묻기로 결정되면 써 둔 답은 버려진다.
    orig_cl, orig_gen = CL.generate, P.generate
    CL.generate = lambda prompt, **kw: '{"ask": "어느 방향인가요?", "options": ["전입", "전출"]}'
    P.generate = lambda prompt, **kw: "전입 절차는 이렇습니다."
    try:
        out = A.answer(dict(state))
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = bool(out.get("clarify")) and "전입 절차는 이렇습니다." not in out["answer"]
    print(f"{'✓' if hit else '✗'} 되묻기로 끝나면 동시에 써 둔 답변은 나가지 않는다")
    ok += hit

    # 판정이 죽어도 작성이 성공했으면 그 답이 나간다 — 직렬일 때와 같은 규약(§11).
    def _dead(prompt, **kw):
        raise LLMError("timeout")

    CL.generate, P.generate = _dead, (lambda prompt, **kw: "전입 절차는 이렇습니다.")
    try:
        out = A.answer(dict(state))
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = not out.get("clarify") and "전입 절차는 이렇습니다." in out["answer"] \
        and "LLMError" in (out.get("llm_error") or "")
    print(f"{'✓' if hit else '✗'} 판정이 죽어도 작성이 됐으면 그 답이 나가고 원인이 남는다")
    ok += hit

    # ② 판정이 없는 턴은 판정 LLM 을 부르지 않는다(스레드도 띄우지 않는다).
    called: list[str] = []
    CL.generate = lambda prompt, **kw: (called.append("clarify"), '{"ask": null}')[1]
    P.generate = lambda prompt, **kw: "고객 재료로 답합니다."
    try:
        out = A.answer({"question": "이 고객 평가금액 얼마야",
                        "evidence": [{**evidence[0], "tool": "customer"}]})
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = not called and bool(out.get("answer"))
    print(f"{'✓' if hit else '✗'} 갈래가 없는 재료뿐이면 판정 없이 바로 답을 쓴다")
    ok += hit

    # ③ 진행 표시가 스레드를 건너간다 — 작성은 다른 스레드에서 돈다.
    events: list[str] = []
    CL.generate = lambda prompt, **kw: '{"ask": null}'
    P.generate = lambda prompt, **kw: "전입 절차는 이렇습니다."
    try:
        with PROG.reporting(events.append):
            A.answer(dict(state))
    finally:
        CL.generate, P.generate = orig_cl, orig_gen
    hit = any("작성" in e for e in events) and any("검증" in e for e in events)
    print(f"{'✓' if hit else '✗'} 작성·검증 진행 표시가 스레드를 건너 전달된다 — {events}")
    ok += hit
    return ok


def check_replan_on_empty() -> int:
    """근거 0건인 채 계획이 끝나려 하면 **한 번은 다시 계획하는가**(§5).

    회귀 대상: "이 고객은 왜 타겟이 됐지?"(고객 화면 열림). 계획이 segment 를 골랐고
    (타겟 = 관리 대상 고객군이라는 말은 알아들었다) segment 가 0건을 냈는데, 원장에는
    성공한 재료만 실려서 계획은 자기가 뭘 불러봤는지 몰랐다 — 같은 호출을 반복하다
    반복 차단에 걸려, customer(왜 이 고객인가·판단근거를 들고 있는 도구)를 써 볼 기회
    없이 턴이 '근거 없음'으로 끝났다. 재료가 없는 것이 아니라 고르기를 실패한 것이다.

    고친 것 셋: ① 빗나간 호출이 계획 프롬프트에 실린다 ② 근거 0건인 채 끝내려 하면
    코드가 안 써 본 도구 목록과 함께 한 번 되돌려 보낸다(두 번째 끝내기는 존중 — 정직한
    '없음' 경로를 막지 않는다) ③ customer 도구 설명이 "왜 관리 대상(타겟)인가"를 말한다.
    """
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    ev_customer = {"tool": "customer", "query": "왜 타겟", "text": "· 왜 이 고객인가: 미운용 방치",
                   "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
                   "allow": ["· 왜 이 고객인가: 미운용 방치"],
                   "sources": [{"id": "customer.CX", "title": "고객 계좌 현황"}], "meta": {}}

    # ① 회귀 시나리오 그대로: segment 빗나감 → done → (재계획) → customer 로 답 재료 확보.
    prompts: list[str] = []
    script = ['{"tool": "segment", "query": "타겟 고객군 선정 조건"}',
              '{"done": true}',
              '{"tool": "customer", "query": "왜 타겟이 됐는지", "last": true}']
    orig_gen, orig_run = P.generate, P.tools.run
    P.generate = lambda prompt, **kw: prompts.append(prompt) or script.pop(0)
    P.tools.run = lambda name, state, query: ev_customer if name == "customer" else None
    try:
        state = {"question": "이 고객은 왜 타겟이 됐지?", "customer_id": "188406-7352194"}
        for _ in range(plan.MAX_STEPS + 2):
            state.update(P.plan_step(state))
            if state.get("plan_done"):
                break
    finally:
        P.generate, P.tools.run = orig_gen, orig_run
    used = [e["tool"] for e in state.get("evidence") or []]
    hit = used == ["customer"] and state.get("plan_done") is True
    print(f"{'✓' if hit else '✗'} 첫 도구가 빗나가도 재계획으로 customer 에 닿는다 → 원장 {used}")
    ok += hit

    # 빗나간 호출이 다음 계획 프롬프트에 보인다 — 원장에는 성공한 재료만 실리므로,
    # 이게 없으면 계획은 같은 호출을 반복한다.
    hit = len(prompts) == 3 and "segment:타겟 고객군 선정 조건" in prompts[1] \
        and "반복해도 소용없다" in prompts[1]
    print(f"{'✓' if hit else '✗'} 빗나간 호출이 계획 프롬프트에 실린다")
    ok += hit

    # 재계획 턴의 프롬프트는 아직 안 써 본 도구를 이름으로 보여준다.
    hit = len(prompts) == 3 and "아직 근거가 0건이다" in prompts[2] and "customer" in prompts[2]
    print(f"{'✓' if hit else '✗'} 재계획 지시가 안 써 본 도구(customer 포함)를 보여준다")
    ok += hit

    # ② 두 번째 done 은 존중한다 — 재계획이 정직한 '없음' 경로를 막지 않는다.
    P.generate = lambda prompt, **kw: '{"done": true}'
    try:
        st = {"question": "질문"}
        st.update(P.plan_step(st))
        retried = st.get("plan_retry") is True and not st.get("plan_done")
        st.update(P.plan_step(st))
    finally:
        P.generate = orig_gen
    hit = retried and st.get("plan_done") is True \
        and P.compose(st)["answer"] == P.NO_EVIDENCE
    print(f"{'✓' if hit else '✗'} 두 번째 done 은 존중 → 여전히 정직한 '근거 없음'")
    ok += hit

    # 근거를 모았으면 done 을 바로 존중한다 — 재계획은 0건일 때만이다.
    P.generate = lambda prompt, **kw: '{"done": true}'
    try:
        st2 = {"question": "질문", "evidence": [ev_customer], "plan_calls": ["customer:q"]}
        st2.update(P.plan_step(st2))
    finally:
        P.generate = orig_gen
    hit = st2.get("plan_done") is True and not st2.get("plan_retry")
    print(f"{'✓' if hit else '✗'} 근거가 있으면 done 즉시 존중(재계획 없음)")
    ok += hit

    # ③ customer 도구 설명이 "왜 관리 대상(타겟)인가"를 말한다 — 도구 설명이 곧 계획의
    #    판단 재료라, 잔액·수익률만 말하면 이 질문이 segment 로 흘러간다.
    desc = tools.TOOLS["customer"].desc
    hit = "타겟" in desc and "왜" in desc
    print(f"{'✓' if hit else '✗'} customer 도구 설명이 선정 이유(타겟)를 말한다")
    ok += hit
    return ok


def check_screen_registry() -> int:
    """단말 화면번호·비대면 채널 경로를 묻는 질문에 답할 재료가 있는가.

    회귀 대상: 변환기가 06/05 의 절차 항목 74건만 읽고 그 위의 **화면번호 대응표 88행을
    통째로 건너뛰었다.** 그래서 절차 항목이 본문에서 언급하지 않는 화면은 지식베이스에
    존재하지 않았고, "포트폴리오 운용현황 조회 화면 번호는?"에 [06-12-604] 가 원문 표에
    버젓이 있는데도 "찾지 못했습니다"로 답했다.

    화면번호는 직원이 가장 자주 묻는 것 중 하나다(07/01 "화면번호·처리 순서까지 담는다").
    옮겨 적기만 하면 되는 재료가 적재되지 않은 채로 있었던 것이다.
    """
    from pension_agent.consult_agent.kb import buckets
    from pension_agent.consult_agent.state import KB

    ok = 0
    by_screen = {c["screen"]: c for c in KB.cards if c["_kind"] == "screen"}

    hit = len(by_screen) >= 80
    print(f"{'✓' if hit else '✗'} 화면번호 대응표가 지식베이스에 적재된다 ({len(by_screen)}건)")
    ok += hit

    card = by_screen.get("[06-12-604]")
    hit = bool(card) and card["title"] == "포트폴리오 운용현황 조회"
    print(f"{'✓' if hit else '✗'} 절차 본문이 언급하지 않는 화면도 있다 — [06-12-604]")
    ok += hit

    # 화면번호 질문이 그 카드에 닿는가.
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        q = "포트폴리오 운용현황 조회 화면 번호는?"
        found = tools.run("screen", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and "[06-12-604]" in (found.get("atomic") or [])
    print(f"{'✓' if hit else '✗'} screen 도구가 그 화면번호를 근거로 돌려준다")
    ok += hit

    # 화면번호는 한 글자만 틀려도 없는 화면이라 원문 그대로 요구한다.
    hit = bool(found) and all(a.startswith("[") for a in found["atomic"])
    print(f"{'✓' if hit else '✗'} 화면번호는 원문 표기 그대로 요구한다(atomic)")
    ok += hit

    # 출처가 원천 문서를 가리킨다 — 06/05 는 재배열한 정리본이다.
    hit = bool(found) and all(s.get("doc") for s in found["sources"])
    print(f"{'✓' if hit else '✗'} 화면 카드도 원천 문서로 출처를 말한다")
    ok += hit

    # 적재되는 종류는 전부 버킷에 들어가야 한다 — 빠지면 LLM 후보 목록에서 사라진다.
    bucketed = {c["id"] for b in buckets(KB).values() for c in b["cards"]}
    missing = [c["id"] for c in KB.cards if c["id"] not in bucketed]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 새 종류가 버킷 카탈로그에 빠지지 않는다"
          + ("" if hit else f" — {missing[:3]}"))
    ok += hit

    # 원문이 "현행 확인 필요"라 적어둔 화면은 그 표기를 그대로 옮긴다.
    stale = [c for c in by_screen.values() if c.get("status") == "확인 필요"]
    hit = bool(stale)
    print(f"{'✓' if hit else '✗'} 번호가 낡았을 수 있는 화면은 그 표기를 옮긴다 ({len(stale)}건)")
    ok += hit

    # ── 표B. 비대면 채널 처리 경로 — 같은 이유로 빠져 있던 61행 ──
    #
    # screen 과 나누는 기준은 **누가 하는가**다. screen 은 직원이 단말에서, channel 은
    # 고객이 앱·웹에서. 같은 업무라도 답이 다르고 묻는 사람도 다르다.
    channels = [c for c in KB.cards if c["_kind"] == "channel"]
    hit = len(channels) >= 50
    print(f"{'✓' if hit else '✗'} 비대면 채널 처리 경로가 적재된다 ({len(channels)}건)")
    ok += hit

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        q = "고객이 스타뱅킹에서 직접 상품변경 하려면 어느 메뉴로 가나요"
        found = tools.run("channel", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and "변경관리" in found["text"] and "KB스타뱅킹" in found["text"]
    print(f"{'✓' if hit else '✗'} channel 도구가 고객이 따라갈 메뉴 경로를 돌려준다")
    ok += hit

    # 메뉴명이 바뀔 수 있다는 원문 경고를 빼지 않는다 — 경로를 불러주는 자리다.
    #
    # 그리고 그 표시는 **데이터가 정한다**(§12 gap 16). 한때 코드 상수였는데, 그러면
    # 붙일지를 코드가 정하고(§7) 기준시점이 생성물과 코드 두 곳에 중복돼 갈린다 —
    # 갈리면 답변이 틀린 기준시점을 말한다.
    hit = bool(found) and any("메뉴명이 바뀔 수 있" in n for n in (found.get("notices") or []))
    print(f"{'✓' if hit else '✗'} 앱 개편으로 경로가 바뀔 수 있다는 표시가 함께 나간다")
    ok += hit

    sample = next((c for c in channels if c.get("volatile")), None)
    hit = bool(sample) and sample["volatile"] in tools.stale_mark(sample) \
        and sample.get("as_of") in tools.stale_mark(sample)
    print(f"{'✓' if hit else '✗'} 낡을 수 있다는 경고와 기준시점을 원문에서 읽어 온다")
    ok += hit

    hit = tools.stale_mark({"task": "x"}) is None
    print(f"{'✓' if hit else '✗'} 선언이 없는 재료에는 시효 표시를 붙이지 않는다")
    ok += hit

    # 기준시점이 코드에 박혀 있지 않은가 — 두 곳에 있으면 원문이 바뀔 때 갈린다.
    src = pathlib.Path(tools.__file__).read_text(encoding="utf-8")
    hit = "2025.03.31" not in src and not hasattr(tools, "CHANNEL_MARK")
    print(f"{'✓' if hit else '✗'} 기준시점·경고 문구가 코드 상수로 남아 있지 않다")
    ok += hit

    # 24시간 원칙의 예외(이용 가능 시간)도 같은 재료로 답한다.
    hours = [c for c in channels if c.get("hours")]
    hit = bool(hours) and all(not c.get("starbanking") for c in hours)
    print(f"{'✓' if hit else '✗'} 이용 가능 시간 예외도 함께 적재된다 ({len(hours)}건)")
    ok += hit

    # 이용시간 행에 "채널 목록에 없음"을 붙이지 않는다 — 되는 업무를 안 된다고 말하게 된다.
    hit = "해당 채널 목록에 없음" not in tools._render_channel(hours[0]) if hours else False
    print(f"{'✓' if hit else '✗'} 시간 표에서 온 행을 '채널에 없음'으로 말하지 않는다")
    ok += hit

    # 두 채널 모두 없는 업무는 애초에 싣지 않는다(비대면으로 못 하는 업무다).
    hit = all(c.get("starbanking") or c.get("ibank") or c.get("hours") for c in channels)
    print(f"{'✓' if hit else '✗'} 두 채널 모두 없는 행은 싣지 않는다")
    ok += hit

    # ── 화면 시효 표시도 channel 과 같은 규약이다(§12 지워진 gap 18) ──
    #
    # 문구는 표A 머리말의 ⚠ 에서 오고(volatile), 원문이 "현행 확인 필요"를 표기한 화면에만
    # 붙는다. 코드 상수면 원문 머리말이 바뀔 때 두 곳이 갈린다.
    hit = (bool(stale) and all(c.get("volatile") for c in stale)
           and all(not c.get("volatile") for c in by_screen.values()
                   if c.get("status") != "확인 필요"))
    print(f"{'✓' if hit else '✗'} 화면 시효 경고는 확인 필요 화면에만, 원문에서 읽어 온다")
    ok += hit

    hit = bool(stale) and stale[0]["volatile"] in (tools.stale_mark(stale[0]) or "") \
        and tools.stale_mark(stale[0]) in tools._render_screen(stale[0])
    print(f"{'✓' if hit else '✗'} 화면 렌더의 시효 표시가 volatile 선언에서 만들어진다")
    ok += hit

    hit = "번호가 낡았을" not in src
    print(f"{'✓' if hit else '✗'} 화면 시효 문구가 코드 상수로 남아 있지 않다")
    ok += hit

    # "확인 필요"가 **해소됐다**는 비고를 부분문자열로 뒤집어 읽지 않는다.
    solved = by_screen.get("[04-12-640]")
    hit = bool(solved) and solved.get("status") is None and not solved.get("volatile")
    print(f"{'✓' if hit else '✗'} 확인 필요 '해소' 비고를 경고로 뒤집어 읽지 않는다")
    ok += hit

    # 메뉴 이름도 검색 단서다 — 직원이 업무명이 아니라 메뉴명으로 물을 때가 있다.
    from pension_agent.consult_agent.kb import retrieve
    menu_hits = retrieve(KB, kinds=["channel"], utterance="변경관리 퇴직연금 상품변경관리 메뉴",
                         top_k=3)
    hit = any("변경관리" in (c.get("starbanking") or "") for _s, c in menu_hits)
    print(f"{'✓' if hit else '✗'} 메뉴 이름으로도 채널 카드에 닿는다")
    ok += hit
    return ok


def check_caution_roles() -> int:
    """주의·비고의 역할 선언 — 저작 메모(authoring)가 직원 답변에 새지 않는가.

    회귀 대상: 비고·⚠ 유의가 역할 구분 없는 한 덩이라, "화면번호안내PDF 미수록 → 관계
    확인 필요" 같은 지식베이스 검증 메모가 화면·채널 비고와 절차 표시(notices)로 직원
    답변에 그대로 나갔다(§12 지워진 gap 17). 역할은 데이터가 선언하고(build_kb + config
    예외표) 소비 코드는 선언만 본다 — guard 의 문자열 휴리스틱(_AUTHORING)은 지웠다.
    """
    from pension_agent.consult_agent import guard as GD
    from pension_agent.consult_agent.kb import ROLE_FIELDS, role_texts
    from pension_agent.consult_agent.nodes import procedure_qa, segment_qa
    from pension_agent.consult_agent.state import KB

    ok = 0
    with_field = [c for c in KB.cards
                  if ROLE_FIELDS.get(c["_kind"]) and c.get(ROLE_FIELDS[c["_kind"]])]

    # ① 역할 선언이 전부 채워져 있다 — 선언 없는 항목은 어느 역할로도 안 세서 조용히 빠진다.
    undeclared = [c["id"] for c in with_field
                  for e in c[ROLE_FIELDS[c["_kind"]]]
                  if not isinstance(e, dict)
                  or e.get("role") not in ("caution", "info", "authoring")]
    hit = bool(with_field) and not undeclared
    print(f"{'✓' if hit else '✗'} 주의·비고 항목 전부에 역할이 선언돼 있다"
          f"({len(with_field)}카드)" + ("" if hit else f" — 누락 {undeclared[:3]}"))
    ok += hit

    # ② authoring 텍스트가 직원용 렌더에 나가지 않는다 — 종류별 렌더 전부.
    render = {"screen": tools._render_screen, "channel": tools._render_channel,
              "method": tools._render_method,
              "procedure": lambda c: "\n".join(procedure_qa._render(c)),
              "segment": lambda c: "\n".join(segment_qa._render(c, None, None))}
    leaked = []
    for c in with_field:
        memos = role_texts(c.get(ROLE_FIELDS[c["_kind"]]), "authoring")
        if memos and any(m in render[c["_kind"]](c) for m in memos):
            leaked.append(c["id"])
    hit = not leaked
    print(f"{'✓' if hit else '✗'} 저작 메모가 답변 재료 렌더에 실리지 않는다"
          + ("" if hit else f" — 유출 {leaked[:3]}"))
    ok += hit

    # ③ 절차 표시(notices)에도 새지 않는다 — proc.001 의 ⚠ 유의는 "필자 해석" 메모다.
    from pension_agent.consult_agent.nodes import procedure_qa as PQ
    by_id = {c["id"]: c for c in KB.cards}
    orig_search, orig_fits = PQ.search, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        PQ.search = lambda q: [(2.0, by_id["proc.001"])]
        found = tools.run("procedure", {"question": "q"}, "적립금 조회 절차")
    finally:
        PQ.search, tools.fits_question = orig_search, orig_fits
    hit = bool(found) and not any("필자" in n for n in found["notices"])
    print(f"{'✓' if hit else '✗'} 절차의 저작 메모가 표시(notices)로 강제되지 않는다")
    ok += hit

    # ④ caution 은 표시로 나간다 — 역할을 나눈 목적은 진짜 주의를 살리는 것이다.
    orig_pick, orig_fits = tools.pick, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        tools.pick = lambda kinds, q, **kw: [(2.0, by_id["screen.06-10-182"])]
        found = tools.run("screen", {"question": "q"}, "연금납입정보 조회 화면")
    finally:
        tools.pick, tools.fits_question = orig_pick, orig_fits
    hit = bool(found) and any("징구 필수" in n for n in found["notices"])
    print(f"{'✓' if hit else '✗'} caution 역할의 주의는 표시로 강제된다")
    ok += hit

    # ⑤ 가드는 method 의 caution 역할만 쓰고, 문자열 휴리스틱은 남아 있지 않다.
    gkb_cards = [c for c in KB.cards if c["_kind"] == "method"]
    m010 = next(c for c in gkb_cards if c["id"] == "m.010")
    hit = (role_texts(m010.get("cautions"), "authoring")
           and not GD._texts(m010)
           # 정의가 없어야 한다 — 주석의 언급("예전에는 …")까지 막지는 않는다.
           and "_AUTHORING =" not in pathlib.Path(GD.__file__).read_text(encoding="utf-8"))
    hit = bool(hit)
    print(f"{'✓' if hit else '✗'} 가드가 역할 선언만 본다(_AUTHORING 휴리스틱 삭제)")
    ok += hit
    return ok


def check_today_material() -> int:
    """오늘 날짜 재료(§3) — 시점·기한이 걸린 질문에 답이 없던 자리.

    작성 규약이 «재료에 없는 값은 계산해서 만들어내지 않는다(날짜·차액·비율 전부)»라,
    오늘이 며칠인지가 재료에 없으면 "연말까지 며칠 남았다"를 **말할 수가 없다**. 세액공제는
    연말이 마감이라 그 문장이 상담의 알맹이인데도 그랬다. 고칠 방향은 규약을 푸는 게 아니라
    (풀면 LLM 의 학습 시점 감각이 그 자리를 채운다) 코드가 오늘을 재료로 싣는 것이다.

    여기서 재는 것 셋:
      ① 도구가 능력 목록에 있고 고객 화면과 무관하게 항상 쓸 수 있는가
      ② 재료가 오늘 날짜와 «두 가지 세는 법»을 함께 밝히는가 — 하나만 실으면 126 인지
         127 인지 분간되지 않아 하루짜리 오안내가 된다
      ③ 그 수치가 검증기를 통과하는가 — 원장에 없으면 답변에서 잘려 나간다
    """
    ok = 0
    from datetime import date

    import tests
    from pension_agent.strategy_agent import customer as CUST

    hit = "date" in tools.usable({}) and "date" in tools.usable({"customer_id": "CX"})
    print(f"{'✓' if hit else '✗'} 오늘 도구는 고객 화면이 닫혀 있어도 쓸 수 있다")
    ok += hit

    pinned = date.fromisoformat(tests.PINNED_TODAY)
    left = CUST.days_to_year_end(pinned)
    ev = tools.TOOLS["date"].run({}, "연말까지 얼마 남았어?")
    text = (ev or {}).get("text", "")

    hit = f"{pinned.year}년 {pinned.month}월 {pinned.day}일" in text
    print(f"{'✓' if hit else '✗'} 재료가 오늘 날짜를 그대로 밝힌다")
    ok += hit

    hit = str(left) in text and str(left + 1) in text and "오늘을 세지 않은" in text
    print(f"{'✓' if hit else '✗'} 연말 잔여일수를 두 가지 세는 법으로 함께 싣는다"
          + ("" if hit else f" — {text!r}"))
    ok += hit

    # 원장 기준일은 고객 화면이 열려 있을 때만. 닫혀 있으면 어느 고객의 원장인지가 없다.
    # 날짜값이 아니라 **줄**로 본다 — 테스트는 오늘을 AS_OF 로 고정한 채 돌아서 두 날짜가
    # 같은 문자열이고, 값으로 비교하면 "닫혀 있어도 실려 있다"로 잘못 읽힌다.
    opened = tools.TOOLS["date"].run({"customer_id": "198734-1205842"}, "오늘 며칠이야")
    label = "고객 계좌 원장 기준일"
    hit = (label in (opened or {}).get("text", "") and label not in text
           and CUST.AS_OF.isoformat() in (opened or {}).get("text", ""))
    print(f"{'✓' if hit else '✗'} 원장 스냅샷 기준일은 고객 화면이 열렸을 때만 함께 싣는다")
    ok += hit

    # ③ 답변이 그 수치를 써도 검증기가 자르지 않는가. 재료로 싣는 목적이 이것이다.
    allow = (ev or {}).get("allow") or []
    hit = verify_texts(f"올해가 {left}일 남았으니 연내 납입해야 세액공제를 받으세요.", allow)[0]
    print(f"{'✓' if hit else '✗'} 답변이 그 잔여일수를 써도 검증기가 자르지 않는다")
    ok += hit

    # 반대로 재료에 없는 날짜 수치는 여전히 잘린다 — 재료를 실었다고 경계가 넓어지면 안 된다.
    hit = not verify_texts(f"올해가 {left + 40}일 남았어요.", allow)[0]
    print(f"{'✓' if hit else '✗'} 재료 밖 잔여일수는 그대로 잘린다(경계는 넓어지지 않았다)")
    ok += hit
    return ok


def check_history_material() -> int:
    """상담 이력 재료(§3) — "지난번에 무슨 얘기 했지"가 답이 없던 자리.

    회귀 대상 셋이 한 턴에 얽혀 있었다.

      ① 기록은 매 턴 쌓이는데(graph.ask → session_store) 읽는 **도구가 없었다.** 능력
         표면은 도구 목록이라(§3) 없는 도구는 없는 능력이고, 그 질문은 "제가 도와드릴 수
         있는 것" 안내로 끝났다.
      ② 다음 턴(자동이체 화면번호) 답변에 앞 턴의 미답이 "이전 대화 내용은 기억하지
         못해요"로 따라 나왔다 — 사실도 아니었다. 재료가 없으면 LLM 은 없는 재료에 대해
         말을 만든다. 그래서 재료를 주고, 이전 대화의 쓰임을 프롬프트가 못박는다.
      ③ 그 답변의 근거 목록에는 질문과 무관한 수익률 관리 카드 4장이 '관련도 None' 으로
         서 있었다 — 고객 상태에 걸린 가드가 원장과 한 목록에 섞여서다(§8 · plan._sources).
    """
    import tempfile
    from pathlib import Path

    from pension_agent import session_store
    from pension_agent.consult_agent import prompts
    from pension_agent.consult_agent.nodes import clarify as CL
    from pension_agent.consult_agent.nodes import meta
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            # 과거 상담 1건 + 에이전트와 나눈 대화 1세션. 시효 표시는 **과거 상담이
            # 실렸을 때** 붙는 것이라 이 픽스처에 record 가 있어야 그 검사가 성립한다.
            session_store.append_turn("CX", "past-2026-07-01", {
                "role": "record", "text": "타사 수수료 비교 문의", "ts": "2026-07-01T09:00:00Z"})
            session_store.append_turn("CX", "s1", {
                "role": "user", "text": "수수료 부담된다고 하시네요", "ts": "2026-08-01T09:00:00Z"})
            session_store.append_turn("CX", "s1", {
                "role": "agent", "text": "수수료는 " + "가" * 400, "ts": "2026-08-01T09:00:05Z"})
            state = {"question": "지난번에 고객 상담에서 무슨 얘기 했지?", "customer_id": "CX"}
            found = tools.run("history", state, "지난 상담 내용")
            closed = tools.run("history", {"question": "지난번에 무슨 얘기 했지?"}, "지난 상담")
            unseen = tools.run("history", {"question": "q", "customer_id": "C_없음"}, "지난 상담")
        finally:
            session_store.SESSION_DATA_DIR = orig_dir

    hit = bool(found) and "수수료 부담된다고 하시네요" in found["text"] \
        and found["sources"][0]["id"] == "session.CX"
    print(f"{'✓' if hit else '✗'} 지난 상담 기록이 재료로 올라온다")
    ok += hit

    # 지난 답변을 통째로 실으면 원장이 지난 상담의 문장으로 뒤덮인다 — 발췌만 싣는다.
    longest = max(len(x) for x in found["text"].splitlines()) if found else 0
    hit = bool(found) and longest <= tools.HISTORY_EXCERPT + 20 and "…" in found["text"]
    print(f"{'✓' if hit else '✗'} 에이전트 답변은 발췌만 싣는다(가장 긴 줄 {longest}자)")
    ok += hit

    # 기록은 "그때 무슨 얘기를 했나"의 근거이지 현재 기준 값의 근거가 아니다.
    hit = bool(found) and tools.HISTORY_MARK in found["notices"]
    print(f"{'✓' if hit else '✗'} 시효 표시를 재료가 달고 나온다(빠지면 코드가 채운다)")
    ok += hit

    hit = closed is None and unseen is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혔거나 기록이 없으면 지어내지 않는다")
    ok += hit

    hit = "history" not in tools.catalog({}) and "history" in tools.catalog({"customer_id": "CX"})
    print(f"{'✓' if hit else '✗'} 못 쓰는 도구는 계획에 보여주지 않는다")
    ok += hit

    hit = "history" in CL._NO_BRANCH and "history" in prompts.ANSWER_SHAPES
    print(f"{'✓' if hit else '✗'} 상담 기록에는 갈래가 없고, 답의 형태 요구는 등록돼 있다")
    ok += hit

    # ② 라우팅 기준과 작성 기준. 두 문장이 없으면 같은 증상이 그대로 돌아온다.
    hit = "지난 상담에서 무슨 얘기를 했는지 묻는 것도" in prompts.ROUTE_PROMPT
    print(f"{'✓' if hit else '✗'} 지난 상담을 묻는 질문이 agent_help 로 새지 않는다")
    ok += hit

    hit = "이전 대화는 이번 질문을 해석하는 데만 쓴다" in prompts.COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 앞 턴의 미답을 이번 답변에서 사과하지 않는다")
    ok += hit

    opened = meta.agent_help({"question": "뭘 도와줄 수 있어?", "customer_id": "CX"})["answer"]
    shut = meta.agent_help({"question": "뭘 도와줄 수 있어?"})["answer"]
    hit = "지난 상담 기록" in opened and "지난 상담 기록" not in shut \
        and "단말 화면번호" in opened
    print(f"{'✓' if hit else '✗'} 도울 수 있는 것 안내가 실제 능력과 같다(화면번호·채널·상담 기록)")
    ok += hit

    # ③ 답이 나온 재료와 표현을 제한한 재료를 갈라 싣는다.
    ev = [{"tool": "screen", "query": "자동이체", "text": "퇴직연금 자동이체 [06-12-619]",
           "atomic": [], "notices": [], "notice_scopes": [], "marks": [], "related": [],
           "allow": ["퇴직연금 자동이체 [06-12-619]"], "meta": {},
           "sources": [{"id": "screen.06-12-619", "title": "퇴직연금 자동이체",
                        "doc": "화면번호 안내", "score": 2.0, "page": None}]}]
    guards = [{"cond": "low", "text": "지적이 아니라 개선방안 제시로 접근", "card": "m.004", "doc": "d"}]
    alts = [{"card": "pitch.k03.028", "title": "민감 응대", "doc": "d"}]
    srcs = P._sources(ev, guards, alts)
    ground = [s for s in srcs if s["role"] == P.GROUND]
    caution = [s for s in srcs if s["role"] == P.CAUTION]
    hit = [s["id"] for s in ground] == ["screen.06-12-619"] \
        and {s["id"] for s in caution} == {"m.004", "pitch.k03.028"}
    print(f"{'✓' if hit else '✗'} 고객 상태 가드가 답의 '근거'로 서지 않는다(근거 {len(ground)} · 주의 {len(caution)})")
    ok += hit

    # 관련도는 검색으로 온 재료에만 있다. 화면은 이 값이 None 이면 그 칸을 아예 안 찍는다.
    hit = all(s.get("score") is None for s in caution) and ground[0]["score"] == 2.0
    print(f"{'✓' if hit else '✗'} 검색으로 오지 않은 재료에는 관련도가 없다")
    ok += hit
    return ok




def check_history_selection() -> int:
    """상담 이력의 선별 — 질의 반영·과거/오늘 분리·추천 칩(칩+검색 확장).

    회귀 대상 셋.

      ① `_history` 가 `query` 를 버리던 것. 계획 루프가 "어떤 질의로 부를지"를 정하는데
         (§2) 도구가 그 질의를 안 읽으면 그 절반이 껍데기다 — 무슨 질문이든 같은 최신순
         덤프가 나갔다. 이제 질의어가 걸리는 과거 상담을 앞세운다(순서만 — 걸러내면
         표현이 다른 기록을 없다고 답하게 된다).
      ② 과거 상담(record)과 에이전트 대화(user/agent)가 한 최신순 창을 쓰던 것. 시연 중
         대화 몇 턴이면(graph.ask 가 매 턴 2턴 append) "지난번"이 창 밖으로 밀렸다.
         예산을 갈라 과거 상담은 항상 실린다.
      ③ 추천 칩(suggest.history_chips) — 기록이 있는 고객에게만, 코드 조립로만 뜬다.
         LLM 이 칩을 쓰면 기록에 없는 내용이 질문에 실려 들어온다.
    """
    import tempfile
    from pathlib import Path

    from pension_agent import session_store
    from pension_agent.consult_agent import suggest

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            # 과거 상담 2건(최신=ETF, 과거=수수료) + 대화 세션 3개 — 옛 로직이면
            # 대화 3세션이 최신순 창(3)을 다 차지해 record 가 밀린다.
            session_store.append_turn("CY", "past-2026-05-01", {
                "role": "record", "text": "수수료 부담 문의로 상품 전환 보류",
                "ts": "2026-05-01T09:00:00Z"})
            session_store.append_turn("CY", "past-2026-07-01", {
                "role": "record", "text": "ETF 거래 편의성 문의", "ts": "2026-07-01T09:00:00Z"})
            for i in range(3):
                session_store.append_turn("CY", f"chat-{i}", {
                    "role": "user", "text": f"오늘 질문 {i}", "ts": f"2026-08-2{i}T09:00:00Z"})
                session_store.append_turn("CY", f"chat-{i}", {
                    "role": "agent", "text": f"오늘 답변 {i}", "ts": f"2026-08-2{i}T09:00:05Z"})

            plain = tools.run("history", {"customer_id": "CY"}, "지난 상담 내용")
            fee = tools.run("history", {"customer_id": "CY"}, "수수료 얘기 했었나")
            chips = suggest.history_chips("CY")
            no_chips = suggest.history_chips("C_없음")

            # 과거 상담이 없고 오늘 대화만 있는 고객 — 시효 표시가 붙으면 안 된다.
            session_store.append_turn("CZ", "chat-0", {
                "role": "user", "text": "평가금액 얼마야?", "ts": "2026-08-24T09:00:00Z"})
            session_store.append_turn("CZ", "chat-0", {
                "role": "agent", "text": "1억 2,000만원입니다", "ts": "2026-08-24T09:00:05Z"})
            today_only = tools.run("history", {"customer_id": "CZ"}, "오늘 무슨 얘기 했지")
        finally:
            session_store.SESSION_DATA_DIR = orig_dir

    # ② 대화가 아무리 쌓여도 과거 상담은 실린다 — 그리고 구획이 갈라져 있다.
    hit = bool(plain) and "수수료 부담 문의" in plain["text"] and "ETF 거래" in plain["text"] \
        and plain["text"].index("[과거 상담 기록]") < plain["text"].index("[에이전트와 나눈 최근 대화]")
    print(f"{'✓' if hit else '✗'} 오늘 대화가 쌓여도 과거 상담이 밀리지 않는다(구획 분리)")
    ok += hit

    # 대화 세션은 최근 1개만 — 원장이 오늘 발화로 뒤덮이지 않게.
    hit = bool(plain) and plain["text"].count("오늘 질문") == 1
    print(f"{'✓' if hit else '✗'} 에이전트 대화는 최근 {tools.HISTORY_DIALOG_SESSIONS}세션만 싣는다")
    ok += hit

    # ① 질의어가 걸린 상담(수수료·5/1)이 최신(ETF·7/1)보다 앞선다. 걸러내지는 않는다.
    hit = bool(fee) and fee["text"].index("수수료 부담") < fee["text"].index("ETF 거래")
    print(f"{'✓' if hit else '✗'} 질의어가 걸린 과거 상담을 앞세운다(query 반영)")
    ok += hit
    hit = bool(fee) and "ETF 거래" in fee["text"]
    print(f"{'✓' if hit else '✗'} 질의어와 다른 기록도 걸러내지 않는다(순서만 바꾼다)")
    ok += hit

    # ③ 칩 — record 있는 고객에게만, 날짜·경과일은 계산값.
    hit = len(chips) == 2 and "7/1" in chips[0] and no_chips == []
    print(f"{'✓' if hit else '✗'} 추천 칩은 기록 있는 고객에게만, 최신 상담 날짜로 뜬다")
    ok += hit

    # 시효 표시는 과거 상담이 실렸을 때만. 방금 나눈 대화에 "지난 상담 기록입니다"가
    # 붙으면 표시가 거짓말을 하고, 매번 붙는 표시는 정작 낡은 값이 실린 턴에서 안 읽힌다.
    hit = bool(today_only) and today_only["notices"] == [] \
        and "[과거 상담 기록]" not in today_only["text"]
    print(f"{'✓' if hit else '✗'} 오늘 대화만 있으면 시효 표시를 달지 않는다")
    ok += hit
    hit = bool(plain) and tools.HISTORY_MARK in plain["notices"]
    print(f"{'✓' if hit else '✗'} 과거 상담이 실리면 시효 표시를 단다")
    ok += hit
    return ok


def check_hier_index() -> int:
    """계층 인덱스 — 버킷 카탈로그(L0) → 카드 슬라이스(L1).

    행내에서 쓸 수 있는 모델(gemma4-31b·dna3.0-35b)의 컨텍스트에 카드 목록을
    실을 수 있는지가 이 기능의 존재 이유다. 그래서 "예산이 실제 상한인가"와
    "버킷이 카드를 빠뜨리지 않는가"를 회귀로 잡는다.
    """
    from pension_agent.consult_agent import kb as K

    kb = K.load_kb()
    ok = 0

    # ① 버킷이 카드를 하나도 빠뜨리지 않는다.
    #    축을 tags.topics 로 잡으면 429장 중 69장이 어떤 버킷에도 안 들어가서
    #    영구히 검색되지 않는 카드가 생긴다 — group 축을 고른 이유가 이것이다.
    bk = K.buckets(kb)
    covered = sum(len(b["cards"]) for b in bk.values())
    hit = covered == len(kb.cards)
    print(f"{'✓' if hit else '✗'} 버킷 커버리지 {covered}/{len(kb.cards)}장 (버킷 {len(bk)}개)")
    ok += hit

    # ② 카탈로그는 결정론적이다(같은 KB → 같은 문자열). 코드가 흔들리면 프롬프트가 흔들린다.
    hit = K.index_catalog(kb) == K.index_catalog(kb)
    print(f"{'✓' if hit else '✗'} 카탈로그 결정론")
    ok += hit

    # ③ L0 카탈로그가 작게 유지된다. 카드가 늘어도 여기가 커지면 안 된다(그게 계층의 목적).
    cat = K.index_catalog(kb)
    hit = len(cat) <= 2000
    print(f"{'✓' if hit else '✗'} L0 카탈로그 {len(cat)}자 ≤ 2000")
    ok += hit

    # ④ 예산은 실제 상한이다 — 헤더·생략안내까지 포함해서 절대 넘지 않는다.
    codes = list(bk)
    over = [b for b in (200, 500, 1000, 2500, 4000)
            if len(K.index_slice(kb, codes, budget_chars=b)) > b]
    hit = not over
    print(f"{'✓' if hit else '✗'} 예산 상한 준수 (초과: {over or '없음'})")
    ok += hit

    # ⑤ 잘라냈으면 몇 장을 못 보여줬는지 밝힌다(조용히 자르지 않는다).
    tight = K.index_slice(kb, codes, budget_chars=500)
    hit = "생략" in tight
    print(f"{'✓' if hit else '✗'} 절단 시 생략 사실 명시")
    ok += hit

    # ⑥ 버킷 하나는 기본 예산 안에 통째로 들어간다 = 2단으로 충분하다는 보장.
    worst = max(len(K.index_slice(kb, [c])) for c in bk)
    hit = worst <= K.INDEX_BUDGET_CHARS
    print(f"{'✓' if hit else '✗'} 최악 버킷 {worst}자 ≤ 기본예산 {K.INDEX_BUDGET_CHARS}")
    ok += hit

    # ⑦ 목록에 없는 버킷 코드는 조회되지 않는다(안전장치 ②).
    hit = K.index_slice(kb, ["ZZ99", "없는코드"]) == ""
    print(f"{'✓' if hit else '✗'} 없는 버킷 코드 → 빈 슬라이스")
    ok += hit

    # ⑧ llm_pick 이 버킷 → id 2단으로 돌고, 지어낸 id 는 걸러진다(안전장치 ③).
    real = tools.KB.pitches[0]["id"]
    calls: list[str] = []

    def stub_generate(prompt, **kw):
        calls.append(prompt)
        if "묶음 식별자" in prompt:      # 1차: 버킷 선택
            return '["P01", "ZZ99"]'
        return f'["{real}", "존재하지_않는_id"]'  # 2차: 카드 선택 + 지어낸 id

    orig = select.generate
    select.generate = stub_generate
    try:
        hits = _REAL_LLM_PICK(("pitch",), "아무 질문")
    finally:
        select.generate = orig
    hit = len(calls) == 2 and [c["id"] for _, c in hits] == [real]
    print(f"{'✓' if hit else '✗'} llm_pick 2단 호출({len(calls)}회) · 지어낸 id 차단")
    ok += hit

    # ⑨ 1차에서 버킷을 못 고르면 2차 호출을 하지 않는다(낭비 방지).
    calls.clear()
    select.generate = lambda prompt, **kw: (calls.append(prompt), "[]")[1]
    try:
        hits = _REAL_LLM_PICK(("pitch",), "아무 질문")
    finally:
        select.generate = orig
    hit = len(calls) == 1 and hits == []
    print(f"{'✓' if hit else '✗'} 버킷 0건 → 2차 호출 생략({len(calls)}회)")
    ok += hit

    # ⑩ 종류를 넓히면 그 종류 카드가 후보로 들어온다 — 화법 전용이 아니다.
    calls.clear()
    first_method = next(c["id"] for c in tools.KB.cards if c["_kind"] == "method")
    select.generate = lambda prompt, **kw: (
        calls.append(prompt),
        '["M01"]' if "묶음 식별자" in prompt else f'["{first_method}"]',
    )[1]
    try:
        hits = _REAL_LLM_PICK(("method",), "어떤 고객부터 관리해야 하나")
    finally:
        select.generate = orig
    hit = [c["id"] for _, c in hits] == [first_method]
    print(f"{'✓' if hit else '✗'} llm_pick 이 화법 아닌 종류(method)도 고른다")
    ok += hit

    return ok


def check_l0_skip() -> int:
    """전 카드 인덱스가 예산에 들어가는 종류는 버킷 선택(L0) 호출을 생략한다.

    2단의 존재 이유는 "카드 전부는 컨텍스트에 못 싣는다"인데, channel(56장)처럼 그 전제가
    안 서는 종류에서 L0 은 후보를 좁히지 않고 순차 LLM 왕복 하나만 쓴다 — 오히려 버킷
    오선택으로 맞는 카드가 후보에서 빠지는 자리다. 판정은 kb.whole_index 가 데이터로
    한다: 카드가 늘어 예산을 넘으면 저절로 2단으로 돌아간다(check_hier_index ⑧이 pitch
    로 2단 경로를 그대로 고정하고 있다 — 이 검사는 그 반대짝이다).
    """
    from pension_agent.consult_agent import kb as K

    ok = 0

    # ① 판정 자체 — 작은 종류는 텍스트, 큰 종류는 None(= 2단 유지).
    small = K.whole_index(tools.KB, ("channel",))
    hit = small is not None and K.whole_index(tools.KB, ("pitch",)) is None
    print(f"{'✓' if hit else '✗'} whole_index: channel 은 1단, pitch 는 2단 유지")
    ok += hit

    # ② 1단으로 돌 때 카드가 하나도 빠지지 않는다 — 왕복을 아끼는 것이지 후보를 줄이는 게
    #    아니다. 제목만 남기는 압축(examples=0)까지 내려가서 얻은 1단도 아니다(예상질문이
    #    최소 1개는 남는 예산일 때만 생략한다 — 선택 품질을 팔아 왕복을 사지 않는다).
    n_channel = sum(1 for c in tools.KB.cards if c["_kind"] == "channel")
    hit = small is not None and \
        sum(1 for line in small.splitlines() if line.startswith("[")) == n_channel
    print(f"{'✓' if hit else '✗'} 1단 인덱스에 channel 전 카드({n_channel}장)가 실린다")
    ok += hit

    # ③ 배선 — llm_pick(("channel",)) 은 LLM 을 카드 선택 한 번만 부른다(버킷 프롬프트 없음).
    real = next(c["id"] for c in tools.KB.cards if c["_kind"] == "channel")
    calls: list[str] = []
    orig = select.generate
    select.generate = lambda prompt, **kw: (calls.append(prompt), f'["{real}"]')[1]
    try:
        hits = _REAL_LLM_PICK(("channel",), "연금 수령 신청 스타뱅킹에서 돼?")
    finally:
        select.generate = orig
    hit = len(calls) == 1 and "묶음 식별자" not in calls[0] \
        and [c["id"] for _, c in hits] == [real]
    print(f"{'✓' if hit else '✗'} llm_pick: 작은 종류는 호출 1번({len(calls)}회) · 버킷 프롬프트 없음")
    ok += hit
    return ok


def check_progress() -> int:
    """진행 표시 — 실제로 시작한 일만, 코드가 정한 문구로, 상태에 흔적 없이 흘린다.

    답변 스트리밍은 못 한다(생성문이 게이트에서 통째로 폐기될 수 있다). 그래서 흘리는
    것은 진행이고, 규칙은 progress.py 머리말의 셋이다. 여기서는 ① 문구가 코드 소유인지
    (도구 선언 progress 라벨), ② 하지 않은 일을 알리지 않는지(재료 0건 턴에 '작성' 없음),
    ③ 콜백이 죽어도 답변이 사는지를 고정한다.
    """
    from pension_agent.consult_agent import progress as PROG
    from pension_agent.consult_agent.nodes import plan as P

    ok = 0
    events: list[str] = []

    # ① 도구 실행이 도구 선언의 라벨로 알린다 — LLM 질의가 아니라.
    orig_tool = tools.TOOLS["screen"]
    tools.TOOLS["screen"] = tools.Tool("screen", orig_tool.desc, lambda st, q: None,
                                       progress=orig_tool.progress)
    try:
        with PROG.reporting(events.append):
            tools.run("screen", {"question": "질문"}, "LLM이 만든 질의")
    finally:
        tools.TOOLS["screen"] = orig_tool
    hit = events == ["단말 화면번호를 찾고 있어요"]
    print(f"{'✓' if hit else '✗'} 도구 진행 표시는 코드 선언 라벨로 찍힌다 — {events}")
    ok += hit

    # ①-보조 조사는 받침으로 갈린다 — 병기("을(를)")를 화면에 내보내지 않는다.
    hit = (PROG.object_of("상담 화법") == "상담 화법을"
           and PROG.object_of("업무 처리 절차") == "업무 처리 절차를"
           and PROG.object_of("IRP") == "IRP을(를)")   # 한글 아니면 병기로 물러선다
    print(f"{'✓' if hit else '✗'} 진행 문구의 목적격 조사가 받침에 맞게 붙는다")
    ok += hit

    # ② 재료 0건 턴은 '작성' 을 알리지 않는다 — compose 가 생성 없이 '없음' 으로 답하므로,
    #    알리면 하지 않은 일을 화면이 말하는 것이 된다.
    events.clear()
    with PROG.reporting(events.append):
        out = P.compose({"question": "질문", "evidence": [], "plan_calls": []})
    hit = events == [] and bool(out["answer"])
    print(f"{'✓' if hit else '✗'} 재료 0건 턴은 작성 진행을 알리지 않는다 — {events}")
    ok += hit

    # ③ 콜백이 죽어도 답변 생성은 계속된다 — 진행 표시는 곁가지다.
    def broken(text: str) -> None:
        raise RuntimeError("표시 실패")

    with PROG.reporting(broken):
        out = P.compose({"question": "질문", "evidence": [], "plan_calls": []})
    hit = bool(out["answer"])
    print(f"{'✓' if hit else '✗'} 진행 콜백이 죽어도 답변은 나온다")
    ok += hit

    # ④ 콜백이 없으면(배치·테스트 기본) emit 은 no-op — 켜지 않은 화면에 아무 일도 없다.
    PROG.emit("아무도 안 듣는 진행")   # 예외 없이 지나가면 통과
    print("✓ 콜백 없는 emit 은 no-op 이다")
    ok += 1
    return ok


def check_order_flipped() -> int:
    """카드 선택 1차가 LLM 인지 — 순서가 실제로 뒤집혔는지 검증한다.

    n-gram 은 문자 유사도라 '주제어만 겹치는 확신 있는 오답'을 만든다. 그 오답을 게이트로
    사후에 걸러내는 대신, 애초에 의미로 고르게 한 것이 이 순서의 이유다. n-gram 은 버리지
    않고 LLM 이 0건일 때의 폴백으로 남긴다.
    """
    target = tools.KB.pitches[0]
    visited: list[str] = []

    def spy_retrieve(kb, **kw):
        visited.append("retrieve")
        return []

    orig_pick, orig_retrieve, orig_verify = tools.llm_pick, tools.retrieve, tools.fits_question
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None: h
    ok = 0
    try:
        # ① LLM 이 골랐으면 n-gram 은 아예 돌지 않는다.
        tools.llm_pick = lambda kinds, query: [(2.0, target)]
        found = tools._pitch({"question": "질문"}, "질문")
        hit = not visited and found is not None and found["sources"][0]["id"] == target["id"]
        print(f"{'✓' if hit else '✗'} LLM 채택 → n-gram 미실행(retrieve {len(visited)}회)")
        ok += hit

        # ② LLM 이 0건이면 n-gram 폴백이 돈다(조건부 → 조건 완화 2회).
        tools.llm_pick = lambda kinds, query: []
        found = tools._pitch({"question": "질문", "stage": "신규"}, "질문")
        hit = len(visited) == 2 and found is None
        print(f"{'✓' if hit else '✗'} LLM 0건 → n-gram 폴백 실행(retrieve {len(visited)}회)")
        ok += hit

        # ③ LLM 의 선택도 게이트를 그대로 통과해야 한다(1차가 됐다고 면제 아님).
        tools.llm_pick = lambda kinds, query: [(2.0, target)]
        tools.fits_question = lambda q, h, kind="", history=None: []
        hit = tools._pitch({"question": "질문"}, "질문") is None
        print(f"{'✓' if hit else '✗'} LLM 선택도 적합성 게이트 적용")
        ok += hit
    finally:
        tools.llm_pick, tools.retrieve, tools.fits_question = orig_pick, orig_retrieve, orig_verify

    return ok


def check_tool_loop() -> int:
    """계획 루프 — 여러 도구를 한 턴에 부르고 결합하는지, 그리고 경계를 코드가 쥐는지.

    이 스위트의 존재 이유가 여기다. 예전에는 의도 하나 = 노드 하나 = 답변 하나였고, 한 턴에
    재료 하나만 쓸 수 있었다. 결합이 되는지, 그리고 결합하면서 「코드=사실」이 새지 않는지.
    """
    ok = 0
    orig_gen, orig_verify = plan.generate, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h

    # 절차 카드는 검색 1위가 아니라 **이름으로 고정**한다. 예전에는 "디폴트옵션 변경 화면번호"
    # 의 1위(proc.018)에 기댔는데, 그 카드의 화면번호는 ⚠ 유의 박스에서 잘못 딸려 온 것이라
    # 데이터를 고치며 비었고(build_kb 의 화면번호 추출), 화면을 묻는 질의는 화면번호가 있는
    # 카드를 앞세우므로(procedure_qa.search) 1위가 바뀌었다. 표시 복구를 보는 검사가 검색
    # 순위에 흔들리지 않게 한다 — 이 검사가 보는 것은 검색이 아니라 근거별 선별 복구다.
    from pension_agent.consult_agent.nodes import procedure_qa as _proc_qa
    _proc_card = next(c for c in tools.KB.cards if c["id"] == "proc.041")   # 화면번호 + status=확인 필요
    _orig_proc_search = _proc_qa.search
    _proc_qa.search = lambda q, _c=_proc_card: [(2.0, _c)]
    try:
        # ① 두 도구를 부르고 두 근거가 한 답변에 다 들어간다. 절차 질의는 status=확인 필요
        #    카드를 겨냥한다 — ⚠ 유의 텍스트는 이제 역할 선언상 authoring 이라
        #    표시로 강제되지 않고, 절차의 강제 표시는 상충 상태 표기에서만 나온다.
        script = [
            '{"tool": "fact", "query": "세액공제 한도"}',
            '{"tool": "procedure", "query": "디폴트옵션 상품 변경 시 기존 적립금 매도 화면번호"}',
            '{"done": true}',
        ]
        plan.generate = lambda prompt, **kw: script.pop(0) if script else '{"done": true}'
        state = {"question": "세액공제 한도랑 디폴트옵션 변경 화면번호 알려줘"}
        for _ in range(3):
            state.update(plan.plan_step(state))
            if state.get("plan_done"):
                break
        used = [e["tool"] for e in state.get("evidence") or []]
        hit = used == ["fact", "procedure"]
        print(f"{'✓' if hit else '✗'} 한 턴에 도구 2개 호출 → 원장 {used}")
        ok += hit

        # ② 복구는 **근거별**이고, 채우는 것은 빠진 표시뿐이다 — 근거 블록을 통째로 덤프하지
        #    않는다. procedure 는 상충 상태 표시(notices)가 있어 그 표시가 붙고, fact 는 값을
        #    언급하지 않았으므로 원문을 강요받지 않는다. 답변이 표 덤프로 뭉개지지 않는 이유가
        #    이 선별이다.
        plan.generate = lambda prompt, **kw: "두 가지를 함께 보시면 돼요."
        out = plan.compose(state)
        ev_fact = next(e for e in state["evidence"] if e["tool"] == "fact")
        ev_proc = next(e for e in state["evidence"] if e["tool"] == "procedure")
        proc_notice = ev_proc["notices"][0]
        hit = (out["answer"].startswith("두 가지를 함께 보시면 돼요.")
               and proc_notice in out["answer"]          # ⚠ 유의 때문에 복구
               and ev_proc["text"] not in out["answer"]  # 그렇다고 카드 전문을 붙이진 않는다
               and ev_fact["text"] not in out["answer"]  # 값 미언급 → 복구 불필요
               and len(out["sources"]) >= 2)
        print(f"{'✓' if hit else '✗'} 근거별 선별 복구(procedure 붙고 fact 안 붙음) · 근거 {len(out['sources'])}건")
        ok += hit

        # ③ 원장 밖 수치가 있으면 생성문을 통째로 버리고 근거 원문으로 답한다(복구 불가).
        blocks = [e["text"] for e in state["evidence"]]
        plan.generate = lambda prompt, **kw: "한도는 1,234,567원이에요."
        out_bad = plan.compose(state)
        hit = "1,234,567" not in out_bad["answer"] and all(b in out_bad["answer"] for b in blocks)
        print(f"{'✓' if hit else '✗'} 원장 밖 수치 → 생성문 폐기 · 근거 원문으로 답변")
        ok += hit

        # ④ 상한은 코드가 정한다 — LLM 이 계속 도구를 불러도 MAX_STEPS 에서 끊긴다.
        plan.generate = lambda prompt, **kw: '{"tool": "fact", "query": "무한"}'
        st = {"question": "질문"}
        for _ in range(plan.MAX_STEPS + 3):
            st.update(plan.plan_step(st))
            if st.get("plan_done"):
                break
        hit = len(st.get("plan_calls") or []) <= plan.MAX_STEPS
        print(f"{'✓' if hit else '✗'} MAX_STEPS 상한 준수(호출 {len(st.get('plan_calls') or [])}회 ≤ {plan.MAX_STEPS})")
        ok += hit

        # ⑤ 같은 도구를 같은 질의로 다시 부르면 진전이 없으므로 도구를 다시 돌리지 않는다.
        #    근거가 0건이면 바로 끝내는 대신 한 번 재계획으로 되돌리고(check_replan_on_empty),
        #    그 뒤에도 반복이면 끝낸다.
        st2 = {"question": "질문", "plan_calls": ["fact:무한"]}
        st2.update(plan.plan_step(st2))
        first = st2.get("plan_retry") is True and not st2.get("plan_done")
        st2.update(plan.plan_step(st2))
        hit = first and st2.get("plan_done") is True and len(st2["plan_calls"]) == 1
        print(f"{'✓' if hit else '✗'} 같은 호출 반복 차단(재계획 한 번 뒤 종료)")
        ok += hit

        # 근거를 이미 모은 턴이면 반복은 재계획 없이 바로 끝낸다 — 되돌릴 이유가 없다.
        st2e = {"question": "질문", "plan_calls": ["fact:무한"],
                "evidence": [{"tool": "fact", "query": "q", "text": "블록", "atomic": [],
                              "notices": [], "notice_scopes": [], "marks": [], "related": [],
                              "allow": ["블록"], "sources": [], "meta": {}}]}
        st2e.update(plan.plan_step(st2e))
        hit = st2e.get("plan_done") is True
        print(f"{'✓' if hit else '✗'} 근거가 있으면 반복 즉시 종료(재계획 없음)")
        ok += hit

        # ⑥ LLM 이 없는 도구 이름을 내놓으면 실행하지 않는다.
        plan.generate = lambda prompt, **kw: '{"tool": "존재하지_않는_도구", "query": "x"}'
        st3 = plan.plan_step({"question": "질문"})
        st3_done = plan.plan_step({"question": "질문", "plan_retry": True})
        hit = "evidence" not in st3 and not st3.get("plan_done") \
            and st3_done.get("plan_done") is True and "evidence" not in st3_done
        print(f"{'✓' if hit else '✗'} 미등록 도구 이름 차단")
        ok += hit

        # ⑦ 근거를 못 모으면 지어내지 않고 없다고 답한다.
        hit = plan.compose({"question": "질문"})["answer"] == plan.NO_EVIDENCE
        print(f"{'✓' if hit else '✗'} 원장 0건 → 정직한 '근거 없음'")
        ok += hit

        # ⑧ 고객 화면이 닫혀 있으면 customer 도구를 아예 보여주지 않는다(스텝 낭비 방지).
        hit = ("customer" not in tools.catalog({})
               and "customer" in tools.catalog({"customer_id": "CX"}))
        print(f"{'✓' if hit else '✗'} 쓸 수 없는 도구는 카탈로그에서 제외")
        ok += hit
    finally:
        plan.generate, tools.fits_question = orig_gen, orig_verify
        _proc_qa.search = _orig_proc_search

    return ok


def check_all_kinds_reachable() -> int:
    """카드 종류가 전부 도구로 닿는지 — method 131장·fieldtip 10장이 답변 근거로
    쓰이는 경로가 없던 것이 이 변경의 동기 중 하나였다(guard 가 caution 8건만 썼다).
    market 23장은 적재 경로 자체가 없어 통째로 닿지 않던 자리다(check_market_material).

    두 경로를 다 본다. 이 종류들은 trigger_examples 가 제목과 거의 같아서 n-gram 폴백이
    화법보다 약하다 — 사실상 LLM 선택이 주 경로다.
    """
    ok = 0
    hit = ({"fact", "procedure", "segment", "method", "fieldtip", "market", "lineup"}
           <= set(tools.TOOLS))
    print(f"{'✓' if hit else '✗'} 일곱 종류 모두 도구로 등록됨")
    ok += hit

    for kind in ("method", "fieldtip", "market", "lineup"):
        card = next(c for c in tools.KB.cards if c["_kind"] == kind)

        # ① LLM 선택 경로 — 주 경로다. 이 도구들은 select.pick() 을 거치므로 시임이 거기다.
        orig = select.llm_pick
        select.llm_pick = lambda kinds, query, _c=card: [(2.0, _c)]
        try:
            found = tools.run(kind, {"question": "q"}, "아무 질문")
        finally:
            select.llm_pick = orig
        by_llm = (found is not None and found["text"].startswith("■")
                  and found["sources"][0]["id"] == card["id"])
        print(f"{'✓' if by_llm else '✗'} {kind} — LLM 선택으로 근거 반환")
        ok += by_llm

        # ② n-gram 폴백 — 예상질문에 가까운 질의라면 LLM 없이도 닿는다.
        ex = next((e for e in (card.get("trigger_examples") or [])), card["title"])
        found = tools.run(kind, {"question": ex}, ex)
        by_ngram = found is not None and found["text"].startswith("■")
        print(f"{'✓' if by_ngram else '✗'} {kind} — n-gram 폴백으로도 근거 반환")
        ok += by_ngram

    # 현장팁은 본부 지침이 아니라는 표시를 본문에 남긴다(신뢰 표시가 답변에 붙어야 한다).
    tip = next(c for c in tools.KB.cards if c["_kind"] == "fieldtip")
    hit = "본부 공식 지침이 아닙니다" in tools._render_fieldtip(tip)
    print(f"{'✓' if hit else '✗'} fieldtip 근거에 '본부 지침 아님' 표시")
    ok += hit
    return ok


def check_market_material() -> int:
    """시황·상품 기반지식(05 폴더)이 답변 재료로 닿는가.

    회귀 대상: 05_시황_상품_기반지식 5개 문서는 「상담 시 근거로 인용할 시장·상품 데이터」
    라고 폴더가 스스로 규정하고 문서마다 검색용 front-matter(trigger_keywords·key_points·
    as_of)까지 갖춰 저작돼 있었는데, **변환기에 경로가 없어** 에이전트에게는 통째로 없는
    재료였다(knowledge/CLAUDE.md 적재 감사 — 원문 폴더 중 유일하게 ❌ 였던 자리).
    "8월 추천펀드 뭐야"·"디폴트옵션 알파드림 구성"에 답할 재료가 저장소에 있는데도
    "찾지 못했습니다"로 끝났다 — screen 표A 88행이 빠져 있던 것과 같은 유형이다.

    이 재료가 다른 것과 갈리는 지점은 **시효**다(CLAUDE.md §9). 제도 확정값과 달리 시황
    수치는 주·월 단위로 낡으므로, 기준시점과 원문의 시효 경고가 답변에 함께 나가야 한다.
    """
    from pension_agent.consult_agent import marks as MARKS
    from pension_agent.consult_agent import relations as REL
    from pension_agent.consult_agent.kb import buckets
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES
    from pension_agent.consult_agent.state import KB

    ok = 0
    # market(시황) · lineup(운용 상품) 두 종류다. 05 한 폴더에서 나오지만 **묻는 것이
    # 달라** 갈라 놨다 — screen(직원이 단말에서)·channel(고객이 앱에서)과 같은 이유다.
    cards = [c for c in KB.cards if c["_kind"] in ("market", "lineup")]

    hit = len(cards) >= 20
    print(f"{'✓' if hit else '✗'} 시황·상품 기반지식이 적재된다 ({len(cards)}장)")
    ok += hit

    # 두 갈래가 다 들어와야 하고, **갈래와 종류가 어긋나면 안 된다** — 상품 문서가 market
    # 으로 들어가면 「추천펀드」를 물었을 때 시황 도구가 그걸 들고 있게 된다.
    pairs = {(c["_kind"], c["category"]) for c in cards}
    hit = pairs == {("market", "시황"), ("lineup", "상품")}
    print(f"{'✓' if hit else '✗'} 시황→market · 상품→lineup 으로 갈라 적재된다 ({sorted(pairs)})")
    ok += hit

    # 도구·버킷도 함께 갈려야 라우팅이 쉬워진다. 종류만 나누고 도구를 하나로 두면 계획 LLM
    # 은 여전히 도구 하나로 둘을 다 받는다(이 분리의 목적이 그것이다).
    hit = ("market" in tools.TOOLS and "lineup" in tools.TOOLS
           and tools.TOOLS["market"].desc != tools.TOOLS["lineup"].desc)
    print(f"{'✓' if hit else '✗'} 도구가 둘로 갈리고 설명이 서로 다르다")
    ok += hit

    letters = {b["kind"]: code[0] for code, b in buckets(KB).items()
               if b["kind"] in ("market", "lineup")}
    hit = len(letters) == 2 and letters["market"] != letters["lineup"]
    print(f"{'✓' if hit else '✗'} 버킷 카탈로그에서도 갈린다 ({letters})")
    ok += hit

    # 기준시점 없는 시황·상품 수치는 인용 불가다(폴더 README 수록 규칙) — 필수로 잡는다.
    missing = [c["id"] for c in cards if not c.get("as_of")]
    hit = not missing
    print(f"{'✓' if hit else '✗'} 모든 카드가 기준시점을 갖는다"
          + ("" if hit else f" — {missing[:3]}"))
    ok += hit

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        q = "디폴트옵션 알파드림 구성상품이 뭐야"
        found = tools.run("lineup", {"question": q}, q)
    finally:
        tools.fits_question = orig

    hit = bool(found) and "수협은행 노후보장 정기예금" in found["text"]
    print(f"{'✓' if hit else '✗'} market 도구가 디폴트옵션 편입상품을 근거로 돌려준다")
    ok += hit

    # 시효 표시는 **데이터가 정한다** — 폴더 README 의 ※ 안내(volatile)와 카드의 as_of 다.
    # 코드 상수로 붙이면 원문이 바뀔 때 두 곳이 갈린다(§12 지워진 gap 16·18 과 같은 자리).
    hit = bool(found) and any("빠르게 달라집니다" in n for n in (found.get("notices") or []))
    print(f"{'✓' if hit else '✗'} 시장·상품이 달라질 수 있다는 원문 경고가 함께 나간다")
    ok += hit

    sample = next((c for c in cards if c.get("volatile")), None)
    hit = bool(sample) and sample["volatile"] in tools.stale_mark(sample) \
        and sample["as_of"] in tools.stale_mark(sample)
    print(f"{'✓' if hit else '✗'} 경고 문구와 기준시점을 원문에서 읽어 온다")
    ok += hit

    # 필드 이름을 코드표기로 인용한 원문(`as_of`)이 밑줄 제거로 깨지지 않는가 —
    # 깨지면 직원이 존재하지 않는 필드를 찾게 된다.
    hit = bool(sample) and "asof" not in sample["volatile"]
    print(f"{'✓' if hit else '✗'} 원문의 필드 이름 표기가 깨지지 않는다")
    ok += hit

    # 행내한 자료는 고객에게 그대로 못 준다 — 원문 confidentiality 선언에서 온다.
    internal = [c for c in cards if c.get("customer_facing") is False]
    facing = [c for c in cards if c.get("customer_facing") is True]
    hit = bool(internal) and bool(facing)
    print(f"{'✓' if hit else '✗'} 고객용·행내한이 원문 표기대로 갈린다 "
          f"(행내한 {len(internal)} · 고객용 {len(facing)})")
    ok += hit

    marks = MARKS.notes_for(KB, internal[:1]) if internal else []
    hit = any("고객에게 그대로 안내하지는 마세요" in m for m in marks)
    print(f"{'✓' if hit else '✗'} 행내한 자료를 쓰면 고객 안내 주의가 붙는다")
    ok += hit

    # 원문(content)은 고치지 않는다 — 표의 값이 그대로 실려 있어야 인용이 성립한다.
    tdf = next((c for c in cards if "TDF" in c["title"]), None)
    hit = bool(tdf) and "Glide-Path" in (tdf.get("content") or "")
    print(f"{'✓' if hit else '✗'} 절 본문이 원문 그대로 실린다")
    ok += hit

    # 절 카드는 자기 문서의 개요 카드를 부모로 갖는다 — 어느 회차 자료인지가 카드에 남는다.
    sections = [c for c in cards if c.get("parent")]
    ids = {c["id"] for c in cards}
    hit = bool(sections) and all(c["parent"] in ids for c in sections)
    print(f"{'✓' if hit else '✗'} 절 카드가 개요 카드를 부모로 가리킨다 ({len(sections)}장)")
    ok += hit

    # 저작·검수 기록(추출 노트)은 카드가 아니다 — 직원 답변 재료가 아니라 저작 메모다.
    hit = not any("추출 노트" in c["title"] for c in cards)
    print(f"{'✓' if hit else '✗'} 추출 노트·목차는 카드로 만들지 않는다")
    ok += hit

    # 한 글자 키워드는 검색 예시에서 빠진다 — 「금」은 거의 모든 절에 걸려 갈래를 못 가른다.
    one_char = [(c["id"], t) for c in cards for t in (c.get("trigger_examples") or [])
                if len(t.strip()) < 2]
    hit = not one_char
    print(f"{'✓' if hit else '✗'} 한 글자 검색 키워드를 달지 않는다"
          + ("" if hit else f" — {one_char[:3]}"))
    ok += hit

    # 새 종류를 적재하면 함께 손대야 하는 자리들 — 빠지면 "적재는 됐는데 검색되지 않는다".
    hit = all(k in tools.TOOLS and k in ANSWER_SHAPES for k in ("market", "lineup"))
    print(f"{'✓' if hit else '✗'} 도구·답변 형태 요구에 등록됨")
    ok += hit

    bucketed = {c["id"] for b in buckets(KB).values() for c in b["cards"]}
    hit = all(c["id"] in bucketed for c in cards)
    print(f"{'✓' if hit else '✗'} 버킷 카탈로그에 들어간다(LLM 후보 목록에 보인다)")
    ok += hit

    # ── 표를 관계로 선언했는가 (knowledge/CLAUDE.md §1) ──────────────
    #
    # 05 문서의 알맹이는 산문이 아니라 표다. 표를 텍스트 덩어리로만 실으면 두 가지가 같이
    # 막힌다 — 검색 입구가 없고(「1975년생이면 TDF 몇 년」의 답이 표에 있는데 못 찾았다),
    # 값–조건 오짝을 잡을 재료가 없다(「알파드림 금리 3.40」은 지켜드림의 값인데 통과했다).
    tabled = [c for c in cards if c.get("tables")]
    hit = len(tabled) >= 8
    print(f"{'✓' if hit else '✗'} 표가 행 단위 관계로 선언된다 ({len(tabled)}장)")
    ok += hit

    deck = next((c for c in cards if c["id"].endswith("추천펀드_2026-08.01")), None)
    rows = (deck or {}).get("tables", [{}])[0].get("rows") or []
    # 병합 셀(합계 행)은 위 행에서 이름을 이어받는다 — 안 이어받으면 「알파드림 포트폴리오
    # 수익률 4.23」이라는 **맞는 답변**이 남의 값으로 몰려 막힌다.
    hit = any(r["keys"][:2] == ["저위험", "알파드림"] and "100" in r["values"] for r in rows)
    print(f"{'✓' if hit else '✗'} 병합 셀 합계 행이 상품 이름을 이어받는다")
    ok += hit

    # 행을 못 가리는 이름(「포트폴리오」는 모든 상품 밑에 달려 있다)은 행 이름이 아니다.
    hit = not any("포트폴리오" in (r.get("keys") or []) for r in rows)
    print(f"{'✓' if hit else '✗'} 행을 못 가리는 이름은 행 이름으로 쓰지 않는다")
    ok += hit

    hit = REL.declared(deck or {})
    print(f"{'✓' if hit else '✗'} 표를 선언한 카드가 관계 검사 대상이 된다")
    ok += hit

    # 표에서 나온 검색 입구 — 열 머리말(1975년)과 행 이름(알파드림 III)이 둘 다 있어야 한다.
    tdf = next((c for c in cards if c["title"] == "TDF 포트폴리오"), None)
    hit = bool(tdf) and "1975년" in (tdf.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 표의 열 머리말이 검색 입구가 된다 — 1975년")
    ok += hit

    hit = bool(deck) and "알파드림 III" in (deck.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 표의 행 이름이 검색 입구가 된다 — 알파드림 III")
    ok += hit

    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        q = "1975년생이면 TDF 몇 년짜리 골라야 해?"
        found = tools.run("lineup", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(found) and "출생연도" in found["text"]
    print(f"{'✓' if hit else '✗'} 표 안에만 있던 질문이 근거에 닿는다 — 출생연도별 TDF")
    ok += hit

    # ── 검색이 «답을 가진 카드»에 닿는가 ───────────────────────────
    #
    # 셋 다 실측으로 잡은 자리다. 재료는 적재돼 있는데 순위가 엉켜서 «상품을 물으면 잘 못
    # 찾는다»가 됐다 — 적재와 검색은 다른 문제라는 것을 이 검사가 지킨다.

    # ① category 를 topics 에 넣지 않는다. 「상품」·「시황」은 두 글자 흔한 말이라, 질문에
    #    "구성상품"·"편입상품"처럼 그 글자가 들어가면 **모든 카드가 똑같이** 가산점을 받아
    #    무더기 동점이 되고 순위가 사실상 id 사전순이 된다(config.TOPIC_VOCAB 머리말이
    #    금지한 그것). 갈래는 category 필드가 이미 들고 있다.
    polluted = [c["id"] for c in cards
                if {"상품", "시황"} & set(c["tags"].get("topics") or [])]
    hit = not polluted
    print(f"{'✓' if hit else '✗'} category 를 검색 태그에 섞지 않는다"
          + ("" if hit else f" — {polluted[:3]}"))
    ok += hit

    # ② 같은 문서의 절이 걸리면 개요 카드는 자리를 비켜준다. 개요는 문서 키워드를 통째로
    #    들고 있어 어떤 질문에나 걸리는데, **답이 든 표는 절에 있다**.
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    try:
        found = tools.run("lineup", {"question": "지켜드림 금리 얼마야"}, "지켜드림 금리 얼마야")
    finally:
        tools.fits_question = orig
    ids = [s["id"] for s in (found or {}).get("sources") or []]
    by_id = {c["id"]: c for c in cards}
    hit = bool(ids) and all(by_id[i].get("parent") for i in ids)   # 전부 절 카드인가
    print(f"{'✓' if hit else '✗'} 절이 걸리면 개요가 후보 자리를 먹지 않는다 — {ids}")
    ok += hit

    # ③ 「+65,469억원」을 이름 칸으로 읽지 않는다. 읽으면 그 열이 이름 열이 되어 값 열이
    #    하나도 안 남고, **표가 통째로 버려진다** — 자금 동향 표가 그렇게 빠져서 「코스피
    #    얼마야」가 검색되지 않았다.
    flows = next((c for c in cards if c["title"].endswith("주간 자금 동향")), None)
    hit = bool(flows) and "코스피" in (flows.get("trigger_examples") or [])
    print(f"{'✓' if hit else '✗'} 수치에 한국어 단위가 붙어도 값으로 읽는다 — 코스피 행")
    ok += hit

    # 날짜는 반대다 — 일정표에서 「20일」은 값이 아니라 행 이름이다.
    sched = [t for c in cards for t in (c.get("tables") or [])
             if "일자" in (t.get("columns") or [])]
    hit = bool(sched) and any("20일" in (r.get("keys") or [])
                              for t in sched for r in t["rows"])
    print(f"{'✓' if hit else '✗'} 일정표의 날짜는 행 이름으로 남는다 — 20일")
    ok += hit

    # ── 오짝 판정: 막아야 할 것과 **막으면 안 되는 것** ─────────────
    #
    # 뒤쪽이 더 중요하다 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다
    # 나쁘다(relations.py 머리말). 그래서 맞는 답변 쪽을 더 많이 건다.
    tables = (deck or {}).get("tables") or []
    for expect, label, answer in (
        (True, "다른 행의 금리를 갖다 붙임",
         "알파드림의 정기예금 금리는 3.40 이에요."),
        (True, "형제 상품의 수익률을 갖다 붙임",
         "모두드림 III 의 1년 수익률은 20.63 이에요."),
        (False, "원문 그대로 옮긴 답",
         "알파드림은 수협은행 노후보장 정기예금 디폴트옵션용(3년) 70, "
         "키움키워드림적격TDF2030 20, 삼성글로벌EMP적격TDF2035 10 으로 구성돼요."),
        (False, "산문 칸(상품특징)의 수치를 인용",
         "알파드림은 시중은행 정기예금 70, TDF 30 투자하는 포트폴리오예요."),
        (False, "합계 행의 값을 인용",
         "알파드림 포트폴리오의 1년 수익률은 4.23 이에요."),
        (False, "여러 행을 함께 말함",
         "지켜드림은 3.40·3.25·3.32, 알파드림은 3.27 이에요."),
        (False, "어느 행인지 안 밝힘 — 판정 불가는 위반이 아니다",
         "정기예금 금리는 3.40 수준이에요."),
    ):
        broken = REL.table_mispaired(answer, tables)
        hit = bool(broken) == expect
        print(f"{'✓' if hit else '✗'} {'차단' if expect else '통과'}: {label}")
        ok += hit
    return ok


def check_atomic_spans() -> int:
    """원문 스팬 집행 — 도구 종류가 아니라 **재료**가 보호 수준을 정한다.

    이게 필요한 이유는 verify_texts 가 수치의 집합 포함 검사라서, 원장에 있는 숫자를
    잘못 짝지은 것을 못 잡기 때문이다. 아래 ③ 이 그 구멍이고, 스팬 집행이 그것을 막는다.
    """
    ok = 0
    VALUE = "총급여 5,500만원 이하 16.5%, 초과 13.2% (지방소득세 포함)"
    ev_num = tools._ev("fact", "q", f"■ 세액공제율\n{VALUE}", [{"id": "f.1", "title": "세액공제율"}],
                       atomic=[VALUE])
    ev_mark = tools._ev("fieldtip", "q", f"■ 팁\n  {tools.FIELDTIP_MARK}\n현장 관찰 요약.",
                        [{"id": "tip.1", "title": "팁"}], notices=[tools.FIELDTIP_MARK])

    orig = plan.generate
    try:
        # ① 값을 언급하지 않으면 원문을 강요하지 않는다 — 모든 답변이 표 덤프가 되지 않는다.
        plan.generate = lambda p, **kw: "세액공제율은 소득에 따라 달라져요."
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = out["answer"] == "세액공제율은 소득에 따라 달라져요." and VALUE not in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 미언급 → 원문 스팬 미요구(산문만)")
        ok += hit

        # ② 값을 원문 그대로 실으면 그대로 통과한다 — 산문 안에 인용이 녹는다.
        plan.generate = lambda p, **kw: f"정리하면 이래요. {VALUE} 라고 안내하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = VALUE in out["answer"] and plan.MISSING_NOTICES not in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 원문 인용 → 블록 덧붙임 없이 통과")
        ok += hit

        # ③ 값을 **잘못 짝지으면** 생성문을 폐기한다. 두 숫자가 다 원장에 있으므로
        #    수치 집합 검사만으로는 통과하는 문장이다(그게 스팬 집행이 있는 이유다).
        wrong = "총급여 5,500만원 초과면 16.5% 예요."
        assert verify_texts(wrong, [ev_num["text"]])[0], "수치 검사만으로는 통과해야 한다(구멍 재현)"
        plan.generate = lambda p, **kw: wrong
        out = plan.compose({"question": "q", "evidence": [ev_num]})
        hit = wrong not in out["answer"] and VALUE in out["answer"]
        print(f"{'✓' if hit else '✗'} 값 재조합 → 생성문 폐기 · 원문으로 답변")
        ok += hit

        # ④ 숫자 없는 표시(「본부 지침 아님」)가 빠지면 폐기가 아니라 덧붙여 채운다.
        plan.generate = lambda p, **kw: "현장에서는 KPI부터 본다고 해요."
        out = plan.compose({"question": "q", "evidence": [ev_mark]})
        hit = ("현장에서는" in out["answer"] and tools.FIELDTIP_MARK in out["answer"]
               and plan.MISSING_NOTICES in out["answer"]
               and ev_mark["text"] not in out["answer"])   # 표시만 붙고 카드 전문은 안 붙는다
        print(f"{'✓' if hit else '✗'} 필수 표시 누락 → 생성문 유지 + 빠진 표시만 덧붙임")
        ok += hit

        # ⑤ 화법은 atomic 이 비어 있을 뿐, 처리 경로가 다르지 않다.
        ev_pitch = tools._ev("pitch", "q", "화법 카드 컨텍스트", [{"id": "p.1", "title": "화법"}])
        plan.generate = lambda p, **kw: "고객에게는 이렇게 말해보세요."
        out = plan.compose({"question": "q", "evidence": [ev_pitch]})
        hit = (ev_pitch["atomic"] == [] and ev_pitch["notices"] == []
               and out["answer"] == "고객에게는 이렇게 말해보세요.")
        print(f"{'✓' if hit else '✗'} 화법은 atomic·notices 가 빈 도구일 뿐(경로 동일)")
        ok += hit

        # ⑥ 도구가 실제로 스팬을 선언하는지 — 선언이 비면 집행할 것이 없다. fact 는 관계
        #    선언이 **없는** 카드를 집어 본다 — 선언이 있는 카드의 atomic 이 비는 것은
        #    정상이고(relations 가 대신한다), 그건 check_relations 가 잰다.
        from pension_agent.consult_agent import relations as REL
        from pension_agent.consult_agent.nodes import facts_qa as FQ
        from pension_agent.consult_agent.state import KB as _KB
        bare = next(x for x in _KB.facts.values() if not REL.declared(x) and x.get("value"))
        orig_fits, orig_search = tools.fits_question, FQ.search
        tools.fits_question = lambda question, h, kind="", history=None: h
        FQ.search = lambda question: [(2.0, bare)]
        try:
            f = tools.run("fact", {"question": "q"}, "확정값")
        finally:
            tools.fits_question, FQ.search = orig_fits, orig_search
        pr = tools.run("procedure", {"question": "디폴트옵션 변경 화면번호"}, "디폴트옵션 변경 화면번호")
        hit = bool(f and f["atomic"]) and bool(pr and pr["atomic"])
        print(f"{'✓' if hit else '✗'} fact·procedure 가 값 스팬 선언(fact {len(f['atomic']) if f else 0}건 · "
              f"procedure {len(pr['atomic']) if pr else 0}건)")
        ok += hit

        # ⑦ 화면번호를 인용하는 주의 표시(notices)가 값 스팬으로 오판되지 않는다 — 숫자
        #    유무로 종류를 추론했을 때 실제로 났던 오판이다. 절차의 ⚠ 유의가 역할 선언상
        #    authoring 으로 내려간 지금, 화면번호를 인용하는 표시는 화면 비고의 caution 이다
        #    (screen.06-12-501 "당일처리는 17시까지 [06-7A-R51] …").
        by_id = {c["id"]: c for c in tools.KB.cards}
        orig_pick = tools.pick
        tools.pick = lambda kinds, q, **kw: [(2.0, by_id["screen.06-12-501"])]
        try:
            sc = tools.run("screen", {"question": "q"}, "퇴직금 입금 등록 화면번호")
        finally:
            tools.pick = orig_pick
        screens = sc["atomic"][0] if sc and sc["atomic"] else ""
        quoting = any(numbers(n) & numbers(screens) for n in sc["notices"]) if sc else False
        plan.generate = lambda p, **kw: f"등록은 화면 {screens} 에서 하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [sc]})
        hit = quoting and screens in out["answer"] and "화면" in out["answer"].split("──")[0]
        print(f"{'✓' if hit else '✗'} 화면번호 인용 주의 표시를 값 스팬으로 오판하지 않음")
        ok += hit
    finally:
        plan.generate = orig
    return ok


def check_notice_scope() -> int:
    """표시는 **답변이 실제로 쓴 카드**의 것만, 그리고 **표시만** 붙는가.

    회귀 대상: 한 도구가 카드 여러 장을 근거 블록 하나로 돌려주는데, 표시 누락 판정이
    블록 단위였다. 그래서 "디폴트옵션 변경 화면번호"를 물으면 답변이 쓰지도 않은 다른
    절차 카드(교체매매 3경로)의 ⚠ 가 따라 붙고, 그것도 카드 전문 1,000자로 붙었다 —
    답변 3줄에 근거 덤프 2,300자. 정작 관계있는 표시가 그 안에 묻혔다.
    """
    from pension_agent.consult_agent.kb import role_texts
    from pension_agent.consult_agent.state import KB

    ok = 0
    orig_gen, orig_pick = plan.generate, tools.pick
    by_id = {c["id"]: c for c in KB.cards}
    # 표시(caution)를 가진 카드 두 장: 답변이 안 쓴 카드(06-10-182 징구 필수) + 쓴 카드
    # (75-08-110 SMS거절 발송 불가). 예전에는 절차의 ⚠ 유의로 재현했는데, 역할 선언이
    # 들어오며 절차 유의는 authoring 으로 내려갔고 카드 단위 표시는 화면 비고의 caution 이
    # 맡는다. 두 화면번호는 숫자 조각이 겹치지 않는 조합이어야 한다 — 겹치면(06·12 등)
    # 값 스팬 검사(_span_verdict 의 DISCARD)가 인용 안 된 쪽 번호로 먼저 걸린다.
    pair = [(2.0, by_id["screen.06-10-182"]), (2.0, by_id["screen.75-08-110"])]
    used_mark = role_texts(by_id["screen.75-08-110"].get("note"), "caution")[0]
    unused_mark = role_texts(by_id["screen.06-10-182"].get("note"), "caution")[0]
    try:
        tools.pick = lambda kinds, q, **kw: pair
        found = tools.run("screen", {"question": "q"}, "연금납입정보 조회랑 상품변경 문자 발송 화면번호")
        tools.pick = orig_pick

        hit = len(found["notice_scopes"]) == 2 and all(s["keys"] for s in found["notice_scopes"])
        print(f"{'✓' if hit else '✗'} 도구가 표시를 카드 단위로 나눠 선언한다"
              f"({len(found['notice_scopes'])}묶음)")
        ok += hit

        # 답변이 [75-08-110] 의 화면번호만 인용했다 → [06-10-182] 의 표시는 붙지 않는다.
        plan.generate = lambda p, **kw: (
            "상품변경 안내 문자는 [75-08-110] 화면에서 발송해요.")
        out = plan.compose({"question": "상품변경 문자 발송 화면번호 알려줘", "evidence": [found]})
        hit = used_mark in out["answer"] and unused_mark not in out["answer"]
        print(f"{'✓' if hit else '✗'} 답변이 쓴 카드의 표시만 붙는다")
        ok += hit

        hit = found["text"] not in out["answer"] and len(out["answer"]) < len(found["text"])
        print(f"{'✓' if hit else '✗'} 카드 전문을 덤프하지 않는다 "
              f"(답변 {len(out['answer'])}자 < 근거 {len(found['text'])}자)")
        ok += hit

        # 답변이 어느 카드를 썼는지 분간이 안 되면(화면번호 미인용) 표시를 다 유지한다 —
        # 잡음을 줄이자고 ⚠ 를 잃지는 않는다.
        plan.generate = lambda p, **kw: "두 화면을 함께 확인하시면 돼요."
        out = plan.compose({"question": "q", "evidence": [found]})
        hit = used_mark in out["answer"] and unused_mark in out["answer"]
        print(f"{'✓' if hit else '✗'} 분간이 안 되면 표시를 잃지 않는다(전부 유지)")
        ok += hit
    finally:
        plan.generate, tools.pick = orig_gen, orig_pick
    return ok


def check_plan_failure() -> int:
    """계획이 깨진 것과 재료가 없는 것을 **다르게 말하는가.**

    회귀 대상: 계획 노드가 LLM 예외를 통째로 삼키고 루프만 끝냈다. 그래서 401·타임아웃·
    모델명 오류·규격 밖 응답이 전부 "그 질문에 쓸 근거를 찾지 못했습니다"로 둔갑했고,
    지식베이스에 멀쩡히 있는 자료를 없다고 답하면서 원인은 화면에서 사라졌다.
    찾아보고 없는 것과 찾아보지도 못한 것은 다른 사건이다.
    """
    ok = 0
    orig_gen, orig_verify = plan.generate, tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None: h
    question = "고객이 주식이 더 낫다는데 뭐라고 하지?"
    base = {"question": question, "utterance": question}

    def drive(gen) -> tuple[dict, str]:
        plan.generate = gen
        state = dict(base)
        for _ in range(plan.MAX_STEPS + 1):
            state.update(plan.plan_step(state))
            if state.get("plan_done"):
                break
        plan.generate = lambda p, **kw: "(스텁 답변)"
        return state, plan.compose(state)["answer"]

    try:
        def dead(prompt, **kw):
            # generate() 는 프로바이더 예외를 전부 LLMError 로 모아 올린다 — 삼켜도 되는
            # 예외와 삼키면 안 되는 예외를 호출부가 구분할 수 있어야 하기 때문이다(llm.py).
            raise LLMError("LLM 미설정")

        state, answer = drive(dead)
        hit = bool(state.get("llm_error")) and "지식베이스에 자료가 없다는 뜻이 아니" in answer \
            and plan.NO_EVIDENCE not in answer
        print(f"{'✓' if hit else '✗'} LLM 호출 실패를 '근거 없음'으로 둔갑시키지 않음")
        ok += hit

        # 잘린 JSON — max_tokens 가 빠듯할 때 실제로 나오던 응답 형태다.
        state, answer = drive(lambda p, **kw: '{"tool": "pitch", "query": "고객이 주식이')
        hit = "JSON" in (state.get("llm_error") or "") and plan.NO_EVIDENCE not in answer
        print(f"{'✓' if hit else '✗'} 규격 밖·잘린 응답도 원인을 남긴다")
        ok += hit

        # 계획이 정상이면 llm_error 가 없고, 재료를 못 찾은 것은 그대로 '근거 없음'이다.
        state, answer = drive(lambda p, **kw: '{"done": true}')
        hit = not state.get("llm_error") and answer == plan.NO_EVIDENCE
        print(f"{'✓' if hit else '✗'} 정상 종료 + 재료 0건은 여전히 '근거 없음'")
        ok += hit

        # 질문을 되받아 적어도 잘리지 않을 만큼은 준다(80 토큰에서 잘려 도구를 못 부르던 자리).
        hit = plan.PLAN_MAX_TOKENS >= 200
        print(f"{'✓' if hit else '✗'} 계획 응답 토큰 상한 {plan.PLAN_MAX_TOKENS}")
        ok += hit
    finally:
        plan.generate, tools.fits_question = orig_gen, orig_verify
    return ok


def check_llm_down() -> int:
    """LLM 이 죽었을 때 **어느 단계에서 죽든** 같은 안내로 끝나는가 (CLAUDE.md §11).

    회귀 대상: 슬롯 분해만 예외를 잡지 않아서, 기본 경로인 화법 상황 질문은 답변 대신
    RuntimeError 로 턴이 끝났다. 다른 단계는 전부 잡고 있었으므로 **같은 LLM 미설정이
    질문 종류에 따라 안내가 되기도 하고 크래시가 되기도 했다.** 지금 슬롯 분해는 화법
    도구 안에서 돌고 LLMError 를 그대로 올리는데, 그것을 tools.run → plan_step 이 받아
    같은 안내로 끝낸다 — 잡는 자리가 옮겨졌을 뿐 결과는 같아야 한다.
    """
    ok = 0

    def dead(*a, **kw):
        raise LLMError("LLM 미설정 — PROVIDER=none")

    # ① 슬롯 분해에서 죽어도 계획 루프가 받아 같은 안내로 끝난다.
    orig_pitch, orig_plan = pitch.generate, plan.generate
    orig_extract, orig_pick = pitch.extract_slots, tools.llm_pick
    pitch.generate = dead
    pitch.extract_slots = _REAL_EXTRACT_SLOTS   # 분해 자체를 재는 검사라 원본으로 되돌린다
    plan.generate = lambda p, **kw: '{"tool": "pitch", "query": "수수료"}'
    tools.llm_pick = lambda kinds, q: []        # n-gram 폴백으로 들어가야 슬롯을 뽑는다
    try:
        state = {"question": "사업자 고객인데 수수료 부담된다고 하시네요",
                 "utterance": "수수료 부담"}
        state.update(plan.plan_step(state))
        hit = "LLMError" in (state.get("llm_error") or "") and state.get("plan_done") is True
    except Exception:
        hit = False
    finally:
        pitch.generate, plan.generate = orig_pitch, orig_plan
        pitch.extract_slots, tools.llm_pick = orig_extract, orig_pick
    print(f"{'✓' if hit else '✗'} 슬롯 분해가 죽어도 크래시가 아니라 원인 기록으로 끝난다")
    ok += hit

    # ② 그래프 전체 — 모든 단계가 죽어도 턴은 안내로 끝난다(스텁 없이 진짜 노드로 돈다).
    saved = {n: getattr(G, n) for n in ("understand", "plan_step")}
    origs = (understand.generate, pitch.generate, plan.generate)
    G.understand, G.plan_step = understand.understand, plan.plan_step
    understand.generate = pitch.generate = plan.generate = dead
    try:
        out = G.build_agent().invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
        answer = out.get("answer", "")
        hit = ("지식베이스에 자료가 없다는 뜻이 아니" in answer
               and plan.NO_EVIDENCE not in answer and "LLMError" in answer)
    except Exception as exc:
        answer, hit = f"({type(exc).__name__})", False
    finally:
        for name, fn in saved.items():
            setattr(G, name, fn)
        understand.generate, pitch.generate, plan.generate = origs
    print(f"{'✓' if hit else '✗'} 화법 상황 질문 + LLM 미설정 → 크래시 없이 안내 — {answer[:38]}")
    ok += hit

    # ③ 재료는 모았는데 문장 작성만 죽은 경우. 근거 원문을 그대로 답으로 내보내면 완성된
    #    답변처럼 보인다 — 다른 단계와 같은 안내로 끝나야 한다.
    orig = plan.generate
    plan.generate = dead
    try:
        evidence = [{"tool": "fact", "query": "한도", "text": "세액공제 한도는 900만원이다.",
                     "atomic": [], "notices": [], "notice_scopes": [], "allow": [],
                     "sources": [{"id": "f1"}], "meta": {}}]
        answer = plan.compose({"question": "한도가 얼마야?", "evidence": evidence})["answer"]
        hit = "지식베이스에 자료가 없다는 뜻이 아니" in answer and "900만원" not in answer
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} compose: 문장 작성 실패를 근거 원문 덤프로 덮지 않음")
    ok += hit

    # ④ 뒤집힌 방향의 같은 사고 방지 — 슬롯 분해만 일시적으로 실패하고 계획은 정상이면,
    #    재료를 못 찾은 턴은 여전히 '근거 없음'이다('LLM 실패'로 둔갑시키지 않는다).
    orig = plan.generate
    plan.generate = lambda p, **kw: '{"done": true}'
    try:
        state = {"question": "질문", "llm_error": "LLMError: 일시 실패"}
        state.update(plan.plan_step(state))
        hit = plan.compose(state)["answer"] == plan.NO_EVIDENCE
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} 계획이 정상이면 앞 단계의 일시 실패로 답이 바뀌지 않음")
    ok += hit

    return ok


def check_origin() -> int:
    """출처는 **원문 문서명**으로 말한다 — 적재 json 의 이름표가 새어나가면 안 된다.

    회귀 대상: 예전에는 원천 문서를 못 찾으면 적재 파일의 meta.title("영업 화법 — 06/03
    영업화법")로 물러서거나 출처 줄을 통째로 생략했다. 앞은 사내 파일명을 출처라고
    말하는 것이고, 뒤는 행원이 고객에게 옮길 수 없는 답을 주는 것이다.
    """
    from pension_agent.consult_agent.kb import origin_of, sources_of
    from pension_agent.consult_agent.nodes import facts_qa
    from pension_agent.consult_agent.state import KB

    ok = 0
    materials = list(KB.cards) + list(KB.facts.values())

    leaked = [c["id"] for c in materials if c.get("_doc") and origin_of(KB, c) == c["_doc"]]
    hit = not leaked
    print(f"{'✓' if hit else '✗'} 적재 파일 제목이 출처로 나가지 않음"
          + ("" if hit else f" — {leaked[:3]}"))
    ok += hit

    empty = [c["id"] for c in materials if not (origin_of(KB, c) or "").strip()]
    hit = not empty
    print(f"{'✓' if hit else '✗'} 출처 줄이 비는 카드 없음(못 찾으면 '확인 필요'라고 말한다)"
          + ("" if hit else f" — {empty[:3]}"))
    ok += hit

    # 카드가 밝힌 원천 문서(source.doc)가 레지스트리로 이어져 문서명으로 나온다.
    # 적재 파일(06/03 영업화법의 변환본)이 아니라 그 앞의 행내 PDF 이름이어야 한다.
    card = next((c for c in KB.cards if c["id"] == "pitch.k03.001"), None)
    origin = origin_of(KB, card) if card else ""
    hit = "연금왕" in origin and "06/" not in origin
    print(f"{'✓' if hit else '✗'} 카드가 밝힌 원천 문서가 문서명으로 해석됨 — {origin[:48]}")
    ok += hit

    # 답변에 붙는 근거 목록에 원문 출처가 함께 실린다(화면·CLI 가 이걸 읽어준다).
    hits = facts_qa.search("세액공제 한도")
    srcs = sources_of(KB, hits)
    hit = bool(srcs) and all(s.get("doc") for s in srcs)
    print(f"{'✓' if hit else '✗'} sources_of 가 근거마다 원문 출처(doc)를 함께 돌려줌")
    ok += hit

    return ok


def main() -> int:
    # 정리할 것과 원래 있던 것을 가른다(아래 끝부분).
    global _SESSIONS_BEFORE
    _SESSIONS_BEFORE = set(config.SESSION_DATA_DIR.glob("*.json")) \
        if config.SESSION_DATA_DIR.exists() else set()

    # build_agent() 는 호출 시점에 모듈 전역에서 노드 함수를 찾으므로 치환이 그대로 먹는다
    # 화법 슬롯 분해는 이제 노드가 아니라 화법 도구가 부른다 — 모듈 함수를 갈아끼운다.
    pitch.extract_slots = stub_slots
    G.understand = stub_understand
    G.plan_step = stub_plan_pitch          # 계획은 고정 — CASES 는 카드 채점을 잰다
    plan.generate = stub_talk              # compose 의 화법 생성
    tools.fits_question = lambda q, h, kind="", history=None: h
    agent = G.build_agent()

    for question, expected in CASES:
        out = agent.invoke({"question": question})
        if expected == "AGENT_HELP":
            ok = out.get("intent") == "agent_help" and bool(out.get("answer")) and not out.get("sources")
            found = "agent_help 노드 응답" if ok else f"intent={out.get('intent')!r}"
        else:
            top = out["sources"][0]["id"] if out["sources"] else "FALLBACK"
            ok = top == expected
            found = ", ".join(f"{s['id'].split('.')[-1]}({s['score']})" for s in out["sources"]) or "→ FALLBACK"
        print(f"{'✓' if ok else '✗'} {question[:32]:<34} {found}")

    # 검사 도중 예외가 나도 정리는 돈다. try/finally 가 없던 동안, 실패한 실행이
    # 남긴 세션 파일(TEST_ACT.json)이 저장소에 그대로 커밋될 뻔했다 — 정리를
    # 성공 경로에만 두면 «정리가 필요한 상황»에서만 정리가 안 된다.
    try:
        check_pitch_stages()
        check_verify_gate()
        check_intent_routing()
        check_lms_send_parsing()
        check_knowledge_intents()
        check_screen_link()
        check_customer_material()
        check_context_and_clarify()
        check_adequacy_and_shape()
        check_material_marks()
        check_relations()
        check_turn_cost()
        check_miss_recovery()
        check_clarify_golden()
        check_answer_parallel()
        check_replan_on_empty()
        check_screen_registry()
        check_market_material()
        check_caution_roles()
        check_history_material()
        check_today_material()
        check_history_selection()
        check_hier_index()
        check_l0_skip()
        check_progress()
        check_order_flipped()
        check_tool_loop()
        check_all_kinds_reachable()
        check_atomic_spans()
        check_origin()
        check_plan_failure()
        check_llm_down()
        check_notice_scope()
        check_guard()
    finally:
        # 위 테스트들(특히 lms_send)이 상담이력 저장소에 기록을 남기므로 **이번 실행이 만든
        # 것만** 지운다. 예전에는 디렉터리를 통째로 지웠는데, 경로가 옮겨진 뒤로는 존재하지
        # 않는 곳을 지우고 있어서 실제로는 아무것도 정리되지 않았다(루트 CLAUDE.md 규칙 4의
        # 같은 사고 — 경로를 하드코딩하면 한 칸 움직였을 때 조용히 빗나간다).
        for fp in set(config.SESSION_DATA_DIR.glob("*.json")) - _SESSIONS_BEFORE:
            fp.unlink()

    total = _TALLY["ok"] + _TALLY["fail"]
    _stdout_print(f"\n{_TALLY['ok']}/{total} 통과")
    return 0 if not _TALLY["fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
