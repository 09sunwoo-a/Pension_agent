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
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None, query=None: h
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


_ROUTED_INTENTS = ("lms_link", "correction", "confirm_action")

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


def check_lms_link_parsing() -> bool:
    """lms_link 는 **보내지 않는다** — 발송 화면 연계를 제안할 뿐이다(§10).

    인용부호 파싱·문구 누락·customer_id 없음을 직접 검증한다(LLM 을 쓰지 않는 노드다).
    """
    from pension_agent.consult_agent.nodes import lms

    out = lms.lms_link({"question": '"안내 문구입니다" 로 LMS 보내줘', "customer_id": "CX"})
    pending = out.get("pending_action")
    ok1 = (bool(pending) and pending["kind"] == "lms" and pending["screen"]
           and pending["message"] == "안내 문구입니다"
           and "보낼지는 그 화면에서" in out["answer"])
    ok2 = "큰따옴표" in lms.lms_link({"question": "그냥 보내줘", "customer_id": "CX"})["answer"]
    ok3 = "찾을 수 없어요" in lms.lms_link({"question": '"문구" 보내줘', "customer_id": None})["answer"]
    ok = ok1 and ok2 and ok3
    print(f"{'✓' if ok else '✗'} lms_link: 발송이 아니라 화면 연계를 제안한다")
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


def check_branch_answer_amount() -> int:
    """되묻기 다음 턴이 «원래 질문»의 금액으로 계산하는가 · 화면번호를 일부만 써도 되는가.

    둘 다 리허설에서 실제로 터진 것이다(2026-09-02 이수민·박정호).

    ① `tax_credit` 이 이번 턴 질문에서만 금액을 뽑아, 되물은 갈래를 고르는 답의 수치를
       납입액으로 읽었다. 「300만원 더 넣으면?」 → 총급여 구간 되묻기 → 「5,500만원
       이하야」 에서 5,500만원을 납입액으로 읽고 잔여한도로 잘라 **1,485,000원**을 답했다.
       물어본 300만원의 답(495,000원)이 아니고, 되묻기 선택지에 방금 495,000원이라 적어
       놓고 그랬다. 기준서 §5 — 「원래 질문과 고른 갈래를 합쳐 답한다」.

    ② `span` 게이트가 화면번호를 흩어진 토큰으로 재서, 답변이 **일부만 인용하면** 폐기했다.
       번호끼리 앞 마디를 공유하기 때문이다(`04-12-…`·`06-12-…`). 원장 화면 일곱 개 중
       여섯 개를 정확히 인용한 절차 답변이 그래서 덤프됐다(박정호 P3).
    """
    from pension_agent.consult_agent import tools
    from pension_agent.consult_agent.nodes.plan import _span_verdict
    from pension_agent.strategy_agent.customer import PERSONAS

    ok = 0
    cid = next((p.id for p in PERSONAS if p.room > 0), PERSONAS[0].id)

    # ① 되묻기 다음 턴 — 원래 질문의 금액을 쓴다
    clarified = [{"question": "300만원 더 넣으면 얼마 돌려받아?",
                  "pending_clarify": {"question": "총급여 구간을 확인해 주세요",
                                      "options": ["5,500만원 이하", "5,500만원 초과"]}}]
    ev = tools._tax_credit({"customer_id": cid, "question": "5,500만원 이하야",
                            "history": clarified}, "")
    hit = ev is not None and "추가 납입액 300만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 답: 갈래를 고른 말이 아니라 원래 질문의 금액으로 계산한다")
    ok += hit

    # 되묻기가 아니면 이번 질문에서 그대로 읽는다 — 넓히기만 하고 기존 동작을 바꾸지 않는다
    ev = tools._tax_credit({"customer_id": cid, "question": "500만원 더 넣으면?",
                            "history": []}, "")
    hit = ev is not None and "추가 납입액 500만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 되묻기 답: 평범한 턴은 이번 질문의 금액을 그대로 쓴다")
    ok += hit

    # ② 화면번호 — 일부만 인용해도 통과, 근거에 없는 번호는 폐기
    atomic = ["[06-12-501]", "[01-12-213]", "[04-12-641]", "[04-12-648]",
              "[04-12-644]", "[06-12-626]", "[04-12-646]"]
    found = {"atomic": atomic, "notices": [], "notice_scopes": [], "text": "", "allow": []}
    passing = [a for a in (
        "[06-12-501] 등록 후 [01-12-213] 로 입금하고 [04-12-646] 로 발굴합니다.",
        "06-12-501 등록 후 01-12-213 으로 입금합니다.",          # 대괄호 없이도 같은 화면이다
        "과세이연정보를 먼저 등록하고 60일 안에 입금합니다.",       # 아예 안 쓴 것은 위반이 아니다
    ) if _span_verdict(found, a)[0] != "discard"]
    hit = len(passing) == 3
    print(f"{'✓' if hit else '✗'} 화면번호: 일부만 인용하거나 대괄호를 빼도 폐기되지 않는다")
    ok += hit

    blocked = [a for a in ("[04-12-640] 화면에서 조회하세요.",
                           "[06-12-502] 후선 업무의뢰로 등록합니다.")
               if _span_verdict(found, a)[0] == "discard"]
    hit = len(blocked) == 2
    print(f"{'✓' if hit else '✗'} 화면번호: 근거에 없는 번호는 여전히 폐기된다")
    ok += hit
    return ok


def check_prompt_is_quotable() -> int:
    """프롬프트에 들어간 것은 인용도 허용된다 (§6).

    코드가 이번 턴 프롬프트에 실어 보내는데 원장에는 없는 텍스트가 있었다. 시킨 대로
    인용하면 «자료 밖 수치»로 답이 통째로 버려지고 근거 원문이 덤프됐다 — `relations.py`
    머리말이 「데이터가 시킨 일을 했다고 벌하는 것」이라 부른 것의 네 번째다.

    실측(2026-09-02 박정호 P2)에서 답을 죽인 것은 **카드 기준시점의 범위 표기**다.
    `ANSWER_SHAPES["fact"]` 가 기준시점을 쓰라고 요구하는데, `as_of` 가 «2026.03~04» 일 때
    답변의 «2026년 3~4월» 이 날짜로 안 끊겨 3·4 가 맨숫자로 남았다. 재작성해도 형태 요구가
    그대로라 또 썼다.

    **짝으로 잰다.** 넓힌 쪽만 재면 헐거워진 것을 못 잡는다.
    """
    from pension_agent.consult_agent import guard, kb as KBMOD, tools
    from pension_agent.consult_agent.nodes import facts_qa, plan as PLAN
    from pension_agent.consult_agent.state import KB
    from pension_agent.verify import verify_texts

    ok = 0

    # ── ① 기준시점 범위 표기 — 원장·답변 양쪽 정규화 (verify.py)
    ledger = ["· 기준시점 2026.03~04 · 출처 …"]
    passes = [t for t in ("이 내용은 2026년 3~4월 기준이에요.",
                          "이 내용은 2026년 3월~4월 기준이에요.",
                          "이 내용은 2026.03~04 기준이에요.",
                          "2026년 3월 기준 자료입니다.")
              if verify_texts(t, ledger, echoable=[""])[0]]
    hit = len(passes) == 4
    print(f"{'✓' if hit else '✗'} 기준시점: 원장의 기간 표기를 한국어로 풀어 쓴 답변이 통과한다")
    ok += hit

    # 넓히기만 하고 좁히는 쪽은 그대로여야 한다 — 기간을 늘리거나 옮기면 여전히 걸린다.
    blocked = [t for t in ("이 내용은 2026년 3~9월 기준이에요.",
                           "이 내용은 2026년 1~4월 기준이에요.",
                           "이 내용은 2025년 3~4월 기준이에요.",
                           "2026년 7월 기준 자료입니다.")
               if not verify_texts(t, ledger, echoable=[""])[0]]
    hit = len(blocked) == 4
    print(f"{'✓' if hit else '✗'} 기준시점: 기간을 늘리거나 옮긴 답변은 여전히 걸린다")
    ok += hit

    # 화면번호·대표번호가 기간으로 오독되면 그 답변이 통째로 거부된다(_DATE_DOT 과 같은 경계).
    hit = verify_texts("[04-12-640] 화면에서 1588-1234 로 문의하세요.",
                       ["[04-12-640] 1588-1234"], echoable=[""])[0]
    print(f"{'✓' if hit else '✗'} 기준시점: 화면번호·대표번호를 기간으로 읽지 않는다")
    ok += hit

    # ── ② 가드·승낙 문구 — 프롬프트에 실어 보낸 것 (plan._screen)
    card = KB.facts.get("fact.k04.f47")
    if card is None:
        print("✗ 프롬프트 인용: 기준 카드(fact.k04.f47)가 없어 검사를 건너뛴다")
        return ok
    ev = tools._ev("fact", "q", facts_qa.render([(1.0, card)]),
                   KBMOD.sources_of(KB, [(1.0, card)]), cards=[card])
    known = PLAN._known_products()
    question = "이 절차 얼마나 걸려?"
    injected = ["- 사용계획 있는 자금은 먼저 걸러낼 것 → 6번",
                "이 고객 «원리금보장상품 편중» 상태에 걸린 화법 2건"]

    quoted = [a for a in ("사용계획 있는 자금은 먼저 걸러내세요(6번). 60일 이내면 됩니다.",
                          "말씀하신 화법 2건을 보여드릴게요. 60일 이내면 재입금이 됩니다.")
              if not PLAN._screen(a, [ev], question, known, prompt_texts=injected)[0]]
    hit = len(quoted) == 2
    print(f"{'✓' if hit else '✗'} 프롬프트 인용: 가드·승낙 문구를 인용한 답변이 폐기되지 않는다")
    ok += hit

    # 넓힌 것은 «프롬프트에 실제로 들어간 수치» 하나뿐이다 — 지어낸 값은 그대로 걸린다.
    still = [a for a in ("이 상품은 연 7.2% 수익을 보장해요.",
                         "이 고객은 IRP에 2,000만원이 있어요.",
                         "사용계획 있는 자금은 먼저 걸러내세요(9번).",
                         "말씀하신 화법 5건을 보여드릴게요.")
             if PLAN._screen(a, [ev], question, known, prompt_texts=injected)[0]]
    hit = len(still) == 4
    print(f"{'✓' if hit else '✗'} 프롬프트 인용: 프롬프트에 없던 수치는 여전히 걸린다")
    ok += hit

    # 상품명은 넓히지 않는다 — 이름만 대서 적합성 게이트를 뚫는 길을 열지 않는다.
    src = pathlib.Path("pension_agent/consult_agent/nodes/plan.py").read_text(encoding="utf-8")
    hit = "echoable=[question, *(t for t in prompt_texts if t)]" in src
    print(f"{'✓' if hit else '✗'} 프롬프트 인용: 넓히는 통로가 echoable(수치 전용) 하나다")
    ok += hit
    return ok


def check_outreach() -> int:
    """⑨ 안내 콘텐츠 — 대화 재료(outreach 도구)와 발송 화면 제안(§10 예정 확장의 구현).

    회귀 대상:
    ① 화면 ⑨ 는 이벤트·세미나를 골라 두는데 그 산출이 **대화 쪽 재료로 없었다.** "이 고객한테
       보낼 만한 세미나 있어?"·"왜 이거야?"·"다른 건 없어?"가 전부 재료 0건으로 끝났고,
       문구를 다듬어 달라는 요청도 일정·링크가 원장에 없어 검증기에 잘렸다.
    ② LMS 발송 화면은 직원이 문구를 따옴표로 옮겨 적어야만(lms_link) 열렸다.
    ③ 그 제안이 **매 턴 붙지 않는가** — 예전 따옴표 휴리스틱 갈래가 지워진 이유다.
    """
    from pension_agent.consult_agent import tools
    from pension_agent.consult_agent.nodes import act
    from pension_agent.strategy_agent.customer import PERSONAS

    ok = 0
    cid = PERSONAS[0].id
    state = {"customer_id": cid, "question": "이 고객한테 안내할 세미나 있어?"}
    ev = tools.run("outreach", state, "안내할 세미나")

    hit = ev is not None and ev["tool"] == "outreach"
    print(f"{'✓' if hit else '✗'} outreach: 열려 있는 고객의 안내 콘텐츠를 재료로 낸다")
    ok += hit
    if ev is None:
        return ok

    text = ev["text"]
    hit = ("발송 문구:" in text and "다른 세미나 후보 4건:" in text
           and "매칭 키워드:" in text and "안내 링크:" in text
           and "지금 안내할 것 2건" in text)
    print(f"{'✓' if hit else '✗'} outreach: 문구·다른 후보·매칭 키워드·링크가 재료에 함께 실린다")
    ok += hit

    # 링크는 한 글자만 달라도 죽는다 — 답변이 그 값을 말하면 원문 그대로여야 한다.
    hit = bool(ev["atomic"]) and all(a.startswith("http") for a in ev["atomic"])
    print(f"{'✓' if hit else '✗'} outreach: 안내 링크를 원문 스팬으로 선언한다", )
    ok += hit

    # **개수와 열거 번호가 재료에 있어야 답이 살아남는다.**
    #
    # 회귀 대상(실측): 이벤트 1건 + 세미나 1건을 고른 답이 "2건을 추천드려요" 라고 쓰자
    # verify_texts 가 «원장 밖 수치 2» 로 판정해 **생성문을 통째로 폐기**했고, compose 가
    # 이 근거 블록을 그대로 덤프했다 — 직원에게 발송 문구·다른 후보·문제상황이 뒤섞인
    # 내부 블록이 답변으로 나갔다. 세는 것은 코드가 이미 아는 사실이라 재료에 싣는다
    # (`suitable` 이 「안내할 수 있는 상품 N종」을 싣는 것과 같은 처리).
    from pension_agent.verify import verify_texts
    _natural = ["김현수 고객님께는 2건을 추천드려요.",
                "이벤트 1건과 세미나 1건, 총 2건을 안내해보세요.",
                "1. 잠자는 IRP 자금 깨우기 운용 이벤트\n2. 예금만으로 괜찮을까?",
                "다른 이벤트 후보도 3건 더 있어요."]
    _killed = [t for t in _natural
               if not verify_texts(t, [ev["text"]], echoable=[state["question"]])[0]]
    hit = not _killed
    print(f"{'✓' if hit else '✗'} outreach: 개수·열거 번호를 쓴 답이 폐기되지 않는다"
          + (f" (잘린 것: {_killed})" if _killed else ""))
    ok += hit

    # 그렇다고 재료 밖 수치가 통과하면 안 된다 — 넓힌 것은 «코드가 센 개수» 하나뿐이다.
    hit = not verify_texts("이 세미나는 연 7.2% 수익을 보장해요.", [ev["text"]])[0]
    print(f"{'✓' if hit else '✗'} outreach: 지어낸 수치는 그대로 걸린다")
    ok += hit

    # 고객 화면이 닫혀 있으면 성립하지 않는 재료다(§3).
    hit = (tools.run("outreach", {"question": "세미나 있어?"}, "세미나") is None
           and "outreach" not in tools.usable({}))
    print(f"{'✓' if hit else '✗'} outreach: 고객 화면이 닫혀 있으면 부를 수 없다")
    ok += hit

    # ② 답변이 그 콘텐츠를 가리키면 발송 화면 연계를 제안한다.
    name = (ev["meta"]["lms"].get("seminar") or ev["meta"]["lms"]["event"])["name"]
    offered = act.offer({**state, "evidence": [ev], "answer": f"«{name}» 를 안내해보세요."})
    pending = offered.get("pending_action")
    hit = (bool(pending) and pending["kind"] == "lms" and name in pending["label"]
           and pending["message"] == (ev["meta"]["lms"].get("seminar")
                                      or ev["meta"]["lms"]["event"])["message"])
    print(f"{'✓' if hit else '✗'} 답변이 가리킨 콘텐츠의 발송 화면을 제안한다(문구는 브리핑 산출 그대로)")
    ok += hit

    # ③ 재료만 있고 답변이 아무것도 고르지 않았으면 붙지 않는다 — 매 턴 붙는 제안은
    # 직원이 읽지 않게 되고, 그게 §10 이 경계하는 상태다.
    hit = not act.offer({**state, "evidence": [ev],
                         "answer": "열려 있는 세미나가 몇 건 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 콘텐츠를 가리키지 않은 답변에는 제안이 붙지 않는다")
    ok += hit

    # ④ 답변이 등록 이름 끝의 종류 낱말(«이벤트»·«세미나»)을 떼고 불러도 그 콘텐츠를
    # 가리킨 것이다. 글자 그대로 대조하던 동안 확정본 E1 에서 「…절세혜택 챙기기 (9/30까지)」가
    # «언급 안 함»으로 탈락하고, 같은 답변이 그대로 옮긴 세미나 이름에 제안이 붙었다 —
    # 승낙 턴이 ISA 만기 고객에게 자산배분 세미나 문자를 열었다(2026-09-03 실측).
    event = ev["meta"]["lms"].get("event")
    seminar = ev["meta"]["lms"].get("seminar")
    _stem = event["name"].removesuffix("이벤트").strip() if event else ""
    both = f"{_stem} (9/30까지)를 안내해보세요. 세미나는 «{seminar['name']}» 가 있어요." \
        if event and seminar else ""
    pending = act.offer({**state, "evidence": [ev], "answer": both}).get("pending_action") \
        if both else None
    hit = bool(pending) and pending["content_id"] == event["id"]
    print(f"{'✓' if hit else '✗'} 종류 낱말을 뗀 이벤트 이름도 가리킨 것으로 보고, "
          f"둘 다 불렀으면 이벤트를 먼저 제안한다")
    ok += hit

    # 이름의 앞부분만 잘라 부른 것은 여전히 «가리킨 것»이 아니다 — 넓힌 것은 끝의 종류
    # 낱말과 공백뿐이다.
    half = _stem[: max(len(_stem) // 2, 1)] if _stem else ""
    hit = bool(half) and not act.offer({**state, "evidence": [ev],
                                        "answer": f"{half}… 같은 게 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 이름을 앞부분만 잘라 부른 답변에는 붙지 않는다")
    ok += hit

    # 재료에 요건 코드(isa·tax·add)가 실리면 답변이 그대로 옮긴다(§5 「재료에 개발 용어를
    # 쓰지 않는다」) — 실측: 「세액공제 활용 가능(tax)과 추가입금 여력 보유(add) 요건」.
    import re as _re
    _code = _re.compile(r"(?<![A-Za-z])[a-z]{3}:")
    _cust = tools.run("customer", {"customer_id": cid}, "현황")
    hit = not _code.search(text) and bool(_cust) and not _code.search(_cust["text"])
    print(f"{'✓' if hit else '✗'} outreach·customer 재료의 성립 요건에 요건 코드가 실리지 않는다")
    ok += hit

    # 이번 턴이 안내 콘텐츠를 안 다뤘으면(원장에 outreach 근거가 없으면) 붙지 않는다.
    hit = not act.offer({**state, "evidence": [],
                         "answer": f"«{name}» 라는 세미나가 있어요."}).get("pending_action")
    print(f"{'✓' if hit else '✗'} 원장에 안내 콘텐츠 근거가 없으면 제안하지 않는다")
    ok += hit

    # 문구는 대화가 새로 만들지 않는다 — 화면 ⑨ 와 같은 값이어야 같은 문자가 나간다.
    from pension_agent.strategy_agent import agent as strategy_agent
    from pension_agent.strategy_agent import customer as strategy_customer
    facts = strategy_agent.propose(strategy_customer.get_profile(cid))["facts"]
    hit = all(ev["meta"]["lms"][k]["message"] == facts["outreach"][k]["lms_message"]
              for k in ev["meta"]["lms"])
    print(f"{'✓' if hit else '✗'} 대화가 싣는 문구가 브리핑 ⑨ 의 문구와 같다")
    ok += hit
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
    lms_pending = lms.lms_link(
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
    #
    # 등록된 안내 콘텐츠 9건은 연금사업부 DB 에서 와 전부 dummy 가 아니다. 그래서 검사용
    # 자산을 하나 끼워 넣어 확인한다 — 레지스트리에 더미가 남아 있을 때만 도는 검사였다면
    # 실데이터로 갈아탄 지금 **조용히 사라졌을** 자리다.
    from pension_agent.strategy_agent import support
    probe = {"id": "TEST-DUMMY", "name": "게이트 검사용 더미", "content_type": "이벤트",
             "url": "https://example.invalid/demo/gate-probe", "dummy": True}
    support.ASSETS.append(probe)
    try:
        blocked = act.confirm_action({
            "question": "네",
            "history": [{"question": "...", "pending_action": {
                **lms_pending,
                "message": support.lms_frame("검사", "안내드려요.", probe["url"])}}],
            "customer_id": "TEST_ACT"})
        hit = "연계하지 않았어요" in blocked["answer"] and screens.SCHEME not in blocked["answer"]
    finally:
        support.ASSETS.remove(probe)
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: []
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


def check_briefing_shared() -> int:
    """브리핑을 **화면과 대화형이 같은 것으로** 본다 (§3 · 지워진 gap 25).

    `propose()` 는 LLM 으로 산문을 쓰므로 부를 때마다 다른 문장이 나온다. 그런데 그 산출을
    화면(브리핑)과 대화형(`customer` 도구가 sentence·insight 를 재료에 그대로 싣는다)이
    함께 읽는다 — 각자 생성하면 "화면에 저렇게 써 있는데 왜 다르게 말하느냐"가 된다.

    부수 효과가 지연이다. 브리핑 한 편이 순차 LLM 11 회인데 대화형이 고객 질문마다 그걸
    새로 돌리고 있었다("이 고객 평가금액 얼마야" 한 마디가 순차 14 회).

    캐시가 «같은 값을 준다»만으로는 부족하다 — 돌려받은 것을 고쳤을 때 다음 호출자가
    고쳐진 브리핑을 받으면, 공유하려던 장치가 도리어 둘을 갈라놓는다. 그것도 함께 잰다.
    """
    from pension_agent.strategy_agent import agent as SA
    from pension_agent.strategy_agent import customer as CUST

    ok = 0
    calls: list[str] = []
    orig_gen, orig_avail = SA.llm.generate, SA.llm.available
    profile = CUST.PERSONAS[0]

    SA.clear_briefing_cache()
    SA.llm.available = lambda: True
    # 부를 때마다 다른 문장을 내는 LLM — 캐시가 없으면 두 호출이 갈린다.
    SA.llm.generate = lambda prompt, **kw: (
        calls.append("llm"),
        json.dumps({"insight": f"해설 {len(calls)}", "sentence": f"문장 {len(calls)}"},
                   ensure_ascii=False))[1]
    try:
        first = SA.propose(profile)
        n_first = len(calls)
        second = SA.propose(profile)
        n_second = len(calls) - n_first
    finally:
        SA.llm.generate, SA.llm.available = orig_gen, orig_avail
        SA.clear_briefing_cache()

    hit = n_first > 0 and n_second == 0
    print(f"{'✓' if hit else '✗'} 같은 고객 브리핑은 한 번만 만든다 "
          f"(1회차 LLM {n_first}회 → 2회차 {n_second}회)")
    ok += hit

    hit = first["sentence"] == second["sentence"] and first["insight"] == second["insight"]
    print(f"{'✓' if hit else '✗'} 두 번째 호출이 같은 문장을 받는다(화면·대화형이 같은 브리핑)")
    ok += hit

    # 돌려받은 것을 고쳐도 캐시가 오염되지 않는다.
    SA.clear_briefing_cache()
    SA.llm.available, SA.llm.generate = (lambda: True), (
        lambda prompt, **kw: '{"insight": "해설", "sentence": "문장"}')
    try:
        a = SA.propose(profile)
        before = (a["sentence"], dict(a["facts"]["customer"]))
        a["sentence"] = "호출부가 고친 문장"      # 최상위 값
        a["facts"]["customer"] = {}              # 중첩된 값(얕은 복사로는 못 막는다)
        b = SA.propose(profile)
        hit = (b["sentence"], b["facts"]["customer"]) == before and bool(before[1])
    finally:
        SA.llm.generate, SA.llm.available = orig_gen, orig_avail
        SA.clear_briefing_cache()
    print(f"{'✓' if hit else '✗'} 돌려받은 브리핑을 고쳐도 다음 호출자는 원본을 받는다")
    ok += hit

    # 입력이 다르면 다른 브리핑이다 — id 가 같아도 내용이 다르면 캐시를 공유하지 않는다.
    # (`dataclasses.replace` 로 요건을 걷어낸 합성 고객이 실제로 같은 id 를 갖는다.)
    import dataclasses
    other = dataclasses.replace(profile, room=0, isa=None, bal=profile.bal + 1)
    hit = SA._cache_key(profile, True, 1) != SA._cache_key(other, True, 1)
    print(f"{'✓' if hit else '✗'} 캐시 키는 id 가 아니라 프로파일 내용이다")
    ok += hit

    # 무한히 쌓이지 않는다 — 실서비스는 고객 수만큼 부른다(시연 로스터 9명으로는 안 드러난다).
    SA.clear_briefing_cache()
    try:
        for i in range(SA._BRIEFING_MAX + 5):
            SA._BRIEFING_CACHE[f"key-{i}"] = {"x": i}
            while len(SA._BRIEFING_CACHE) > SA._BRIEFING_MAX:
                SA._BRIEFING_CACHE.popitem(last=False)
        hit = len(SA._BRIEFING_CACHE) == SA._BRIEFING_MAX \
            and "key-0" not in SA._BRIEFING_CACHE \
            and f"key-{SA._BRIEFING_MAX + 4}" in SA._BRIEFING_CACHE
    finally:
        SA.clear_briefing_cache()
    print(f"{'✓' if hit else '✗'} 캐시가 상한({SA._BRIEFING_MAX})에서 오래된 것부터 밀어낸다")
    ok += hit
    return ok


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


def check_playbook_material() -> int:
    """고객 상태에 걸린 화법 — 화면 ⑥⑦⑧ 과 **같은 후보군**에서 나오는가(§3 · §10).

    회귀 대상 넷.
    ① `customer` 도구가 화면 ⑥⑦⑧ 이 고른 것을 재료에 안 실었다. `allow` 에는 있어 인용은
       허용됐지만 재료 텍스트에 없어 LLM 이 본 적이 없었고, 그래서 "이 고객한테 뭐라고
       말하지"를 `pitch` 가 지식베이스 전체에서 고객과 무관하게 답했다 — 화면과 대화가
       같은 질문에 다른 카드를 말하는 상태다(§3).
    ② 선제 제안이 매 턴 붙으면 안 된다(§10). 지워진 LMS 갈래가 그렇게 죽었다.
    ③ 승낙 턴이 지식 카드를 **손으로 렌더하면** §5 형태·§6 점검·§7 표시가 그 경로만
       빠진다 — 근거만 싣고 답변은 compose 가 쓴다.
    ④ 화면이 막은 것을 대화형이 권하면 안 된다. 목업 9케이스는 전원 `pension_started=False`
       라 이 경로가 한 번도 발동한 적이 없다 — 합성 프로필로 고정한다.
    """
    import dataclasses

    from pension_agent.consult_agent.nodes import act as ACT
    from pension_agent.consult_agent import routing as R
    from pension_agent.strategy_agent import customer as SC
    from pension_agent.strategy_agent.situations import problem_situations

    ok = 0
    SONG = "188406-7352194"   # 송도윤 — 요건 6건이라 문제상황도 화법 후보도 넉넉하다

    # ① 화면 ⑥⑦⑧ 이 고른 것이 재료와 출처에 함께 실리는가.
    ev = tools.run("customer", {"customer_id": SONG}, "이 고객한테 뭐라고 말하지")
    text = ev["text"]
    hit = all(k in text for k in ("· 이렇게 말해보세요:", "· 예상 반론:", "· 상담 참고:"))
    print(f"{'✓' if hit else '✗'} customer 재료에 화면 ⑥⑦⑧ 이 고른 화법·반론·참고자료가 실린다")
    ok += hit

    card_ids = {s["id"] for s in ev["sources"] if s["id"].startswith(("pitch.", "m.", "proc."))}
    hit = bool(card_ids)
    print(f"{'✓' if hit else '✗'} 그 카드가 **출처**에도 실린다(§3) — {len(card_ids)}건")
    ok += hit

    # 검색으로 온 재료가 아니므로 관련도를 지어내지 않는다(§3).
    hit = all(s.get("score") is None for s in ev["sources"])
    print(f"{'✓' if hit else '✗'} 고객 재료의 출처에는 관련도를 붙이지 않는다")
    ok += hit

    # 같은 카드가 검색으로 오면 원천 게시글 URL 이 붙는데(sources_of) 이 재료로 오면 안
    # 붙던 자리다 — strategy_agent 가 넘겨주는 항목에 문서명만 있어서, 「출처에 URL 을
    # 싣는다」는 변경이 이 경로만 비껴갔다. 화면에는 ↗ 줄이 붙는 근거와 안 붙는 근거가
    # 섞여 나갔고, 직원은 왜 어떤 것만 원문으로 갈 수 있는지 알 수 없었다.
    from pension_agent.consult_agent.kb import card_source_meta
    from pension_agent.consult_agent.state import KB as _KB
    hit = all("url" in s and s["url"] == card_source_meta(_KB, s["id"]).get("url")
              for s in ev["sources"] if s["id"] in card_ids)
    print(f"{'✓' if hit else '✗'} 고객 재료의 카드 출처가 검색 경로와 같은 URL 을 싣는다")
    ok += hit

    # 위 대조가 «둘 다 None» 으로 늘 참이 되지 않게, 되짚기가 실제로 도는지 따로 잰다 —
    # 송도윤의 ⑥⑦⑧ 은 본부 자료라 URL 이 없어서(핫팁 게시글이 아니다) 그 고객만으로는
    # 판정할 수 없다. 어느 고객에게 어떤 카드가 뽑히느냐에 이 회귀가 좌우되면 안 된다.
    linked = [c["id"] for c in _KB.cards if card_source_meta(_KB, c["id"]).get("url")]
    hit = len(linked) > 10
    print(f"{'✓' if hit else '✗'} 카드 id 로 원천 게시글 URL 을 되짚을 수 있다({len(linked)}건)")
    ok += hit

    # ② 후보는 strategy_agent 매칭에서만 나온다 — 대화형이 자기 매칭을 만들지 않는다.
    hits = tools.playbook_hits({"customer_id": SONG, "question": "증권사 얘기를 꺼내네요"})
    from pension_agent.strategy_agent.support import matching as M
    sits = problem_situations(SC.get_profile(SONG), SC.conditions(SC.get_profile(SONG)))
    pool = ({c["id"] for t in ("proposal", "objection", "guide")
             for _s, c, _seg in M.scored_situation_cards(sits, t, 50)}
            | {c["id"] for _s, c, _seg in M.scored_situation_procedures(sits, 50)}
            | {c["id"] for _s, c, _seg in M.scored_situation_methods(sits, 50)})
    hit = bool(hits) and all(c["id"] in pool for _s, c in hits)
    print(f"{'✓' if hit else '✗'} situation 후보가 화면 ⑥⑦⑧ 과 같은 매칭 결과 안에 있다")
    ok += hit

    hit = tools.playbook_hits({"customer_id": None, "question": "뭐라고 말하지"}) == []
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 상태 화법을 만들지 않는다")
    ok += hit

    hit = "playbook" not in tools.usable({}) and "playbook" in tools.usable({"customer_id": SONG})
    print(f"{'✓' if hit else '✗'} 고객이 닫혀 있으면 도구 목록에서도 빠진다")
    ok += hit

    # ③ 제안 트리거 — 네 조건 중 하나라도 어긋나면 안 붙는다(§10).
    pitch_ev = [{"tool": "pitch", "query": "q", "text": "이렇게 말해보세요.", "atomic": [],
                 "notices": [], "notice_scopes": [], "allow": ["이렇게 말해보세요."],
                 "sources": [], "meta": {}}]
    fact_ev = [{**pitch_ev[0], "tool": "fact"}]
    hit = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": fact_ev}) is None
    print(f"{'✓' if hit else '✗'} 화법을 다루지 않은 턴(값 질의)에는 상태 화법 제안이 안 붙는다")
    ok += hit

    hit = ACT._propose({"answer": "a", "evidence": pitch_ev}) is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 제안이 안 붙는다")
    ok += hit

    action = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": pitch_ev})
    hit = bool(action) and action["kind"] == "pitch" and bool(action.get("cards"))
    print(f"{'✓' if hit else '✗'} 화법 턴 + 고객 열림 + 남은 카드 → 제안이 붙는다")
    ok += hit

    # 무엇에 걸렸는지 밝힌다 — 열어보지 않고도 왜 떴는지 알 수 있어야 한다.
    hit = bool(action) and any(n in action["label"] for n in SC.CONDS.values())
    print(f"{'✓' if hit else '✗'} 제안 문구가 걸린 요건 이름을 밝힌다 — {action and action['label']}")
    ok += hit

    # 이미 이번 턴 원장에 실린 카드는 다시 제안하지 않는다.
    used = dict(pitch_ev[0], sources=[{"id": c["id"], "title": "", "doc": "", "score": None,
                                       "page": None} for c in
                                      [SITU for _s, SITU in tools.playbook_hits(
                                          {"customer_id": SONG, "question": "q"})]])
    again = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": [used]})
    hit = again is None or not ({c["id"] for c in again["cards"]}
                                & {s["id"] for s in used["sources"]})
    print(f"{'✓' if hit else '✗'} 이번 턴이 이미 쓴 카드를 다시 보여드릴까요 하고 묻지 않는다")
    ok += hit

    # ③-b 갈래 일치 — 제안은 이번 턴이 다룬 갈래의 나머지 후보만이다. 절차를 물은 턴에
    #     화법을 제안하면 §3 「묻지 않은 값」의 제안 버전이 된다.
    hit = all(c["_kind"] == "pitch" for c in
              (lambda a: [next(x for x in tools.KB.cards if x["id"] == cc["id"])
                          for cc in a["cards"]])(action))
    print(f"{'✓' if hit else '✗'} 화법 턴의 제안은 화법 카드만 담는다")
    ok += hit

    proc_ev = [{**pitch_ev[0], "tool": "procedure"}]
    p_action = ACT._propose({"answer": "a", "customer_id": SONG, "evidence": proc_ev})
    by_id = {c["id"]: c for c in tools.KB.cards}
    hit = bool(p_action) and all(by_id[c["id"]]["_kind"] == "procedure"
                                 for c in p_action["cards"])
    print(f"{'✓' if hit else '✗'} 절차 턴의 제안은 절차 카드만 담는다 — {p_action and p_action['label']}")
    ok += hit

    m_action = ACT._propose({"answer": "a", "customer_id": SONG,
                             "evidence": [{**pitch_ev[0], "tool": "method"}]})
    hit = bool(m_action) and all(by_id[c["id"]]["_kind"] == "method" for c in m_action["cards"])
    print(f"{'✓' if hit else '✗'} 방법론 턴의 제안은 방법론 카드만 담는다")
    ok += hit

    # ③-c 종류별 렌더러·선언 — 화법 렌더러에 절차를 태우면 저작 메모(authoring)가 새고
    #     화면번호가 원문 강제(atomic)를 안 받는다(지워진 gap 17 이 고친 실패의 재발 경로).
    mixed = tools.playbook_hits({"customer_id": SONG, "question": "q"},
                                lanes=("procedure", "method"))
    pev = tools.playbook_evidence("점검", mixed)
    proc_cards = [c for _s, c in mixed if c["_kind"] == "procedure"]
    hit = (bool(pev) and "필자 해석" not in pev["text"] and "'role'" not in pev["text"]
           and all(sc in pev["atomic"] for c in proc_cards for sc in (c.get("screens") or [])))
    print(f"{'✓' if hit else '✗'} playbook 근거가 절차·방법론에 그 종류의 렌더러·선언을 쓴다"
          f" (화면번호 atomic {len(pev['atomic'])}건 · 저작 메모 미유출)")
    ok += hit

    # ④ 승낙 턴은 근거만 싣고 답변은 작성 단계가 쓴다.
    out = ACT.confirm_action({"question": "네", "customer_id": SONG,
                              "history": [{"pending_action": action}]})
    hit = bool(out.get("evidence")) and not out.get("answer")
    print(f"{'✓' if hit else '✗'} 승낙 턴이 근거만 싣고 답변 문장을 손으로 만들지 않는다")
    ok += hit

    # ④-b 승낙받은 자료가 «없는 자료»가 되어 나가면 안 된다.
    #
    #     실측(2026-09-02 김현수 세션): 화법 2건을 승낙받아 원장에 싣고도 답변은 "해당
    #     질문에 대응하는 대사가 지금 준비된 자료에는 없어요"로 시작했고, 직원에게
    #     디폴트옵션 등록 현황을 되물으며 끝났다. 작성 프롬프트에 실리는 것이 「직원 질문:
    #     네」와 이전 대화뿐이라, LLM 이 <자료>를 **직전 턴의 질문**에 대고 재고 안 맞으니
    #     시스템 규칙 9(핵심 대상이 자료에 없으면 그것이 결론)를 적용한 것이다. 그 판정은
    #     여기서 성립하지 않는다 — 자료를 고른 것은 질문이 아니라 고객 상태이고, 무엇을
    #     보여줄지는 제안한 턴이 이미 정했다(§10). 그래서 **코드가** 그 사실을 실어 준다.
    hit = out.get("accepted") == action["label"]
    print(f"{'✓' if hit else '✗'} 승낙 턴이 무엇을 승낙받았는지 남긴다(작성 단계가 볼 수 있게)")
    ok += hit

    # 선언이 없으면 LangGraph 가 노드 반환값에서 그 키를 **조용히** 버린다(state.py 주석).
    # 그러면 위 검사는 통과하는데 그래프로 돌린 턴만 옛 증상으로 돌아간다.
    from pension_agent.consult_agent.state import AgentState as _AS
    hit = "accepted" in _AS.__annotations__
    print(f"{'✓' if hit else '✗'} 그 값이 상태에 선언돼 있다(선언 없으면 그래프가 버린다)")
    ok += hit

    seen: dict[str, str] = {}
    orig_gen = plan.generate
    plan.generate = lambda p, **kw: seen.setdefault("p", p) or "답변"
    try:
        plan.compose({"question": "네", "customer_id": SONG, **out})
        hit = ("승낙에 대한 답이다" in seen["p"] and action["label"] in seen["p"]
               and '"준비된 자료가 없다"고 말하지 않는다' in seen["p"])
        print(f"{'✓' if hit else '✗'} 승낙 턴의 작성 프롬프트가 «이 자료를 보여주라»고 말한다")
        ok += hit

        seen.clear()
        plan.compose({"question": "실물이전 절차 알려줘", "customer_id": SONG,
                      "evidence": out["evidence"]})
        hit = "승낙에 대한 답이다" not in seen["p"]
        print(f"{'✓' if hit else '✗'} 승낙 턴이 아니면 그 블록이 붙지 않는다")
        ok += hit
    finally:
        plan.generate = orig_gen

    # 도착지는 `compose`(답변 작성) 다 — 되묻기 판정과 답변 작성이 그 노드에서 함께
    # 끝난다. 라벨이 상태 키 `answer` 와 다른 이유는 graph.py 의 add_node 주석 참고.
    hit = R.route_confirm(out) == "compose" and R.route_confirm(
        {"answer": "화면을 열었어요"}) == "__end__"
    print(f"{'✓' if hit else '✗'} 분기표가 그 턴을 답변 작성으로 보낸다(화면 연계는 그대로 끝)")
    ok += hit

    # 그 턴에는 되묻기 판정이 돌지 않는다 — 입력이 "네" 한 글자라 판정할 질문이 없다(§10).
    from pension_agent.consult_agent.nodes import clarify as _CL
    hit = not _CL.applicable({**out, "intent": "confirm_action"}) \
        and _CL.applicable({**out, "intent": "situation", "question": "실물이전 절차"})
    print(f"{'✓' if hit else '✗'} 승낙 턴은 되묻기 판정을 돌리지 않는다")
    ok += hit

    # 제안한 턴이 남긴 카드만 싣는다 — 이번 턴의 "네" 에서 다시 고르지 않는다(§10).
    hit = bool(out.get("evidence")) and {s["id"] for s in out["evidence"][0]["sources"]} == {
        c["id"] for c in action["cards"]}
    print(f"{'✓' if hit else '✗'} 승낙 턴이 제안한 턴의 카드를 그대로 싣는다")
    ok += hit

    # ⑤ 연금수령 개시 계좌 — 납입·세액공제 세그먼트가 후보에서 빠진다(§8 관리대장).
    #    9케이스 전원 pension_started=False 라 합성 프로필로만 재현된다.
    base = SC.get_profile("176903-5528417")       # 한지우 — isa·tax·add
    started = dataclasses.replace(base, pension_started=True)
    before = {s["id"] for s in problem_situations(base, SC.conditions(base))}
    after = {s["id"] for s in problem_situations(started, SC.conditions(started))}
    hit = {"seg.13", "seg.15", "seg.16"} <= before and not ({"seg.13", "seg.15", "seg.16"} & after)
    print(f"{'✓' if hit else '✗'} 연금개시 계좌에서 납입·세액공제 세그먼트가 빠진다(exclusions)")
    ok += hit

    orig = SC.get_profile
    SC.get_profile = lambda cid: started if cid == "PENSION-STARTED" else orig(cid)
    try:
        blocked = tools.playbook_hits({"customer_id": "PENSION-STARTED", "question": "뭐라고 말하지"})
        allowed_sits = {s["id"] for s in problem_situations(started, SC.conditions(started))}
        sits2 = problem_situations(started, SC.conditions(started))
        pool2 = ({c["id"] for t in ("proposal", "objection", "guide")
                  for _s, c, _seg in M.scored_situation_cards(sits2, t, 50)}
                 | {c["id"] for _s, c, _seg in M.scored_situation_procedures(sits2, 50)}
                 | {c["id"] for _s, c, _seg in M.scored_situation_methods(sits2, 50)})
        hit = all(c["id"] in pool2 for _s, c in blocked) and "seg.13" not in allowed_sits
    finally:
        SC.get_profile = orig
    print(f"{'✓' if hit else '✗'} 그 차단이 대화형 후보에도 그대로 상속된다(따로 막지 않는다)")
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
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES, COMPOSE_SYSTEM

    ok = 0

    # ① 게이트가 재료 종류를 가리지 않는가 — 전부 버리면 어느 도구도 근거를 못 내놓는다.
    orig = tools.fits_question
    tools.fits_question = lambda q, h, kind="", history=None, query=None: []
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: (called.append(kind), h)[1]
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
    tools.fits_question = lambda question, h, kind="", history=None, query=None: [x for x in h if x[1]["id"] == keep]
    try:
        found = tools.run("procedure", {"question": q}, q)
    finally:
        tools.fits_question = orig
    hit = bool(candidates) and bool(found) and found["sources"][0]["id"] == keep \
        and len(found["sources"]) == 1
    print(f"{'✓' if hit else '✗'} 후보 일부만 맞으면 그것만 남긴다(전부 버리지 않는다)")
    ok += hit

    # 남길 것이 하나도 없을 때만 근거 없음이다.
    tools.fits_question = lambda question, h, kind="", history=None, query=None: []
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

    # 출처는 화면의 근거 목록이 전담한다(§5 「출처는 본문 문장이 아니다」). 형태 요구가
    # 본문에 출처를 요구하던 동안 LLM 이 재료의 「· 기준시점 … · 출처 …」 메타 줄을 통째로
    # 복사했다(gemma 실측 — 카드 덤프체 답변). 기준시점은 시효성 요구라 남는다.
    hit = ("출처" not in ANSWER_SHAPES["fact"] and "기준시점" in ANSWER_SHAPES["fact"]
           and "재료 블록의 형식은 옮기지 않는다" in COMPOSE_SYSTEM)
    print(f"{'✓' if hit else '✗'} 출처는 형태 요구가 아니라 근거 목록이 전담한다")
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
    from pension_agent.consult_agent.nodes import facts_qa, plan as P, procedure_qa
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

    # ③ **재료에도 같은 선언이 보여야 한다.** 주의(notes_for)는 거짓을 보는데 재료 조립은
    #    참일 때만 표시를 붙였다 — 그래서 답변 아래에는 "고객에게 안내하지 마세요"가 서고
    #    본문은 그 카드를 근거로 "고객에게 이렇게 안내하는 게 핵심"이라고 썼다(송도윤 S6).
    #    작성 프롬프트의 「'내부용'으로 표시된 재료는…」 규칙이 가리킬 표시가 없었다.
    hit = (M.facing_note(internal) == M.FACING_NOTE[False]
           and M.facing_note(facing) == M.FACING_NOTE[True]
           and M.facing_note(undeclared) is None)
    print(f"{'✓' if hit else '✗'} 재료 표시는 참·거짓을 둘 다 싣고 선언 없음은 비운다")
    ok += hit

    # 실제 재료 블록에 실리는가 — 두 종류 모두. 여기가 끊기면 위 단위 판정이 통과해도
    # LLM 은 여전히 내부용 카드를 구분하지 못한다.
    internal_fact = next(f for f in KB.facts.values() if f.get("customer_facing") is False)
    hit = M.FACING_NOTE[False] in "\n".join(facts_qa._render(internal_fact))
    print(f"{'✓' if hit else '✗'} 내부용 팩트의 재료 블록에 내부용 표시가 실린다")
    ok += hit

    def proc(card):
        return "\n".join(procedure_qa._render({**card, "title": "t"}))

    hit = (M.FACING_NOTE[False] in proc(internal)
           and M.FACING_NOTE[True] in proc(facing)
           and not any(n in proc(undeclared) for n in M.FACING_NOTE.values()))
    print(f"{'✓' if hit else '✗'} 절차 재료 블록도 같은 표시를 쓴다")
    ok += hit

    # 작성 프롬프트가 그 표시를 실제로 가리키는가. 문구가 갈리면 규칙이 다시 헛돈다.
    from pension_agent.consult_agent import prompts as PR
    hit = "내부용" in PR.COMPOSE_SYSTEM and "고객에게 할 말로 옮기지" in PR.COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 작성 규칙이 내부용 재료를 고객 안내로 옮기지 말라고 못 박는다")
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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

    # **정정 표지 목록은 카드가 실제로 쓰는 낱말을 덮어야 한다.** 「오답」이 빠져 있던 동안
    # F47(퇴직금 60일 내 IRP 입금)의 verify_points 가 «"이미 통장으로 받았으면 끝" = 오답»
    # 이라 적혀 있는데, 그 줄대로 짚어준 답변이 폐기되고 근거 원문이 덤프됐다 — 위와 똑같이
    # 데이터가 시킨 일을 했다고 벌하는 자리다(2026-09-02 실측).
    f47 = KB.facts.get("fact.k04.f47")
    pf47 = (f47 or {}).get("pitfalls") or []
    hit = bool(pf47) and not R.known_wrong(
        '"이미 통장으로 받았으면 끝"이라고 생각하기 쉽지만 오답이에요. 60일 이내면 됩니다.', pf47)
    print(f"{'✓' if hit else '✗'} 카드가 「오답」이라 부르는 문구를 그 말로 짚는 정정도 막지 않는다")
    ok += hit

    # 그렇다고 헐거워지지 않는다 — 따옴표 없이 그대로 주장하면 여전히 잡힌다.
    hit = R.known_wrong("이미 통장으로 받았으면 끝이니 어쩔 수 없다고 안내하세요.", pf47) != []
    print(f"{'✓' if hit else '✗'} 표지가 늘어도 그대로 주장한 오답은 잡는다")
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
    tools.fits_question = lambda question, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda question, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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


def check_product_advice() -> int:
    """「이 고객 무슨 상품 추천해주지?」가 답이 되는가 — 그리고 그 답이 권유가 아닌가.

    회귀 대상은 한 질문에서 함께 터진 결함 넷이다. 실제 트레이스에서 lineup 이 세 바퀴
    돌며 전부 '재료 없음'을 내고, 겨우 쓴 문장은 '미등록 상품명'으로 폐기돼, 화면에는
    고객 브리핑 재료가 통째로 떨어졌다.

    ① 적합성 게이트가 **계획이 무엇을 찾는 중인지**를 못 봤다. 직원 질문만 보고 판정하니
       「투자성향별 포트폴리오」 같은 일반 자료가 "이 고객에 대한 답이 아니다"로 전멸했다.
    ② 상품 등록부가 데모 카탈로그 12종뿐이라, 행내 원문 표에 버젓이 있는 상품
       (「KB 온국민 TDF 시리즈」)을 말한 답변이 '미등록'으로 통째로 버려졌다.
    ③ 상품명 정규식이 문장을 삼켜 **실재 상품과 지어낸 상품을 한 이름으로** 붙였다.
    ④ 적합성 게이트가 이미 계산해둔 «허용 범위»를 부를 도구가 대화형에 없었다.
    """
    from pension_agent.consult_agent import kb as KBMOD
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES, COMPOSE_SYSTEM
    from pension_agent.consult_agent.state import KB
    ok = 0

    # ── ① 게이트가 계획 질의를 받는다 ──────────────────────────────
    seen: dict[str, str] = {}
    orig_gen, orig_fits = tools.generate, tools.fits_question
    tools.fits_question = _REAL_FITS          # 게이트 본체를 재야 하므로 스텁을 걷는다
    tools.generate = lambda p, **kw: seen.setdefault("p", p) and "[]"
    try:
        card = next(c for c in KB.cards if c["_kind"] == "lineup")
        tools._adopt({"question": "이 고객 무슨 상품 추천해주지?"},
                     "투자성향별 추천 포트폴리오", [(2.0, card)], "운용 상품")
    finally:
        tools.generate, tools.fits_question = orig_gen, orig_fits
    prompt = seen.get("p", "")
    hit = "이 고객 무슨 상품 추천해주지?" in prompt and "투자성향별 추천 포트폴리오" in prompt
    print(f"{'✓' if hit else '✗'} 적합성 게이트 프롬프트에 직원 질문과 계획 질의가 함께 실린다")
    ok += hit

    # 「고객 이름이 안 적힌 자료는 뺀다」로 읽히지 않도록 판단 기준에 명시돼 있는가.
    hit = "일반 자료는 남긴다" in prompt
    print(f"{'✓' if hit else '✗'} 고객 특정 질문에서도 일반 자료를 남기라는 기준이 실린다")
    ok += hit

    # ── ② 등록부가 지식베이스 상품명을 안다 ────────────────────────
    names = KBMOD.product_names(KB)
    hit = "KB 온국민 TDF 시리즈" in names and "KB RISE 미국ETF 모아드림 (주식-재간접)" in names
    print(f"{'✓' if hit else '✗'} 지식베이스가 선언한 상품명이 등록부에 있다 ({len(names)}종)")
    ok += hit

    # 등록부는 **표의 상품명 칸**만 본다 — 합계 행의 라벨은 상품이 아니다.
    hit = "포트폴리오" not in names
    print(f"{'✓' if hit else '✗'} 합계 행 라벨(「포트폴리오」)은 상품 등록부에 안 들어간다")
    ok += hit

    known = plan._known_products()
    hit = "KB 온국민TDF2040 C-P" in known and "KB 온국민 TDF 시리즈" in known
    print(f"{'✓' if hit else '✗'} 등록부가 상품 카탈로그와 지식베이스를 합친다 ({len(known)}종)")
    ok += hit

    # ── ③ 상품명 경계 — 실재 상품은 통과하고 지어낸 이름만 걸린다 ──
    #
    # 트레이스에 찍힌 실제 문장이다. 예전 정규식은 마크다운 강조를 넘어
    # 'KB 온국민 TDF 시리즈**나 **KBSTAR 미국나스닥100' 을 **한 이름**으로 읽어,
    # 원문 표에 있는 앞쪽까지 미등록으로 판정했다.
    ledger = ["KB 온국민 TDF 시리즈 · KB 온국민TDF2040 C-P"]
    real = "동연령 인기 상품인 **KB 온국민 TDF 시리즈**를 보실 수 있어요."
    mixed = ("**KB 온국민 TDF 시리즈**나 **KBSTAR 미국나스닥100**을 보실 수 있어요.")
    tail = "다만 KB 온국민TDF2040 C-P의 적격 TDF 위험자산 한도는 확인이 필요해요."
    made_up = "KB 무지개 성장 펀드를 보실 수 있어요."

    hit = verify_texts(real, ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 원문 표에 있는 상품명을 말한 답변이 통과한다")
    ok += hit

    hit = verify_texts(tail, ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 상품명 뒤에 조사가 붙어도 통과한다")
    ok += hit

    # 예전 정규식은 이 문장에서 두 이름을 **한 토큰**으로 읽어, 원문 표에 있는 앞쪽까지
    # 미등록으로 몰았다. 지금은 마크다운 강조에서 끊겨 앞쪽만 후보가 되고 통과한다.
    from pension_agent.verify import _PROD
    hit = _PROD.findall(mixed) == ["KB 온국민 TDF 시리즈"]
    print(f"{'✓' if hit else '✗'} 실재 상품과 지어낸 상품이 붙어 있어도 따로 잡힌다")
    ok += hit

    # 이 문장은 여전히 거부된다 — 다만 걸리는 이유가 «지어낸 이름이 달고 온 수치»여야지,
    # 원문 표에 있는 앞쪽 상품이 「미등록」으로 몰려서는 안 된다.
    _good, bad = verify_texts(mixed, ledger, known_products=known)
    hit = not any(b.startswith("상품명") for b in bad)
    print(f"{'✓' if hit else '✗'} 앞쪽 실재 상품이 뒤쪽 때문에 미등록으로 몰리지 않는다")
    ok += hit

    hit = not verify_texts(made_up, ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 등록부에 없는 상품명은 여전히 거부된다")
    ok += hit

    # 등록부에 있어도 **이번 턴 재료에 없으면** 인용할 수 없다 — 등록부를 12종에서
    # 80여 종으로 넓히면서 함께 조인 자리다.
    hit = not verify_texts(real, ["다른 재료"], known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 등록 상품이어도 이번 턴 원장에 없으면 못 쓴다")
    ok += hit

    # 괄호 표기가 판정을 뒤집으면 안 된다 — 등록명이 "KB 정기예금(1년)"인데 `_PROD` 가
    # 괄호에서 이름을 끊으므로 LLM 은 "KB 정기예금 1년"으로 풀어 쓸 수밖에 없다. 공백만
    # 지우던 동안 두 표기가 다른 키가 되어, suitable 재료의 8종을 정확히 옮긴 답변이
    # '미등록'으로 통째로 버려지고 근거 원문이 덤프됐다(시연 대본 T10).
    paren_ledger = ["KB 정기예금(1년) — 매우낮은위험 · 최근 1년 3.1%"]
    paren_answer = "KB 정기예금 1년(매우낮은위험, 최근 1년 3.1%)도 범위 안에 들어요."
    hit = verify_texts(paren_answer, paren_ledger, known_products=known)[0]
    print(f"{'✓' if hit else '✗'} 등록명의 괄호를 풀어 쓴 표기('KB 정기예금 1년')가 통과한다")
    ok += hit

    # ── ④ 적합성 범위 도구 ─────────────────────────────────────────
    cid = "176903-5528417"
    q = "이 고객 무슨 상품 추천해주지?"
    found = tools.run("suitable", {"question": q, "customer_id": cid}, q)
    text = (found or {}).get("text", "")
    hit = bool(found) and "적합성 허용 상한: 다소높은위험" in text
    print(f"{'✓' if hit else '✗'} suitable 이 이 고객에게 허용되는 위험등급 상한을 말한다")
    ok += hit

    hit = "KB 성장형 MP" in text and "KB 온국민TDF2040 C-P" in text
    print(f"{'✓' if hit else '✗'} 게이트를 통과한 상품이 목록으로 나온다")
    ok += hit

    # "왜 이건 없어?" 에 답할 수 있어야 목록을 믿을 수 있다.
    hit = "KB 글로벌리츠 ETF" in text and "허용 상한" in text.split("제외된 상품")[-1]
    print(f"{'✓' if hit else '✗'} 제외된 상품과 그 사유가 함께 나온다")
    ok += hit

    # 답이 상품명을 말할 텐데, 그 이름이 이번 턴 원장에 있어야 통과한다(위 ③ 의 조임).
    hit = verify_texts("KB 성장형 MP 를 보실 수 있어요.", tools.ledger_texts([found]),
                       known_products=known)[0]
    print(f"{'✓' if hit else '✗'} suitable 재료로 쓴 답변이 검증을 통과한다")
    ok += hit

    hit = "suitable" in tools.TOOLS and "suitable" in ANSWER_SHAPES
    print(f"{'✓' if hit else '✗'} suitable 이 도구 목록과 답변 형태 요구 양쪽에 있다")
    ok += hit

    # 고객 화면이 닫혀 있으면 성립하지 않는다(§3) — 카탈로그에도 안 뜬다.
    hit = ("suitable" not in tools.usable({})
           and "suitable" in tools.usable({"customer_id": cid}))
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혀 있으면 suitable 을 제안하지 않는다")
    ok += hit

    # ── 스탠스 — 권유가 아니라 정보 제공 ───────────────────────────
    #
    # 표시는 **코드가** 붙인다(guard.py 규약). 프롬프트로 톤만 잡으면 LLM 이 무시해도
    # 아무도 모른다 — 검증기는 수치·상품명만 보지 톤은 안 본다.
    note = KBMOD.advisory_note(KB)
    hit = bool(note) and "정보 제공" in note and "자본시장" in note
    print(f"{'✓' if hit else '✗'} 인용 고지를 지식베이스 선언에서 읽어 온다")
    ok += hit

    hit = any("정보 제공" in n for n in (found or {}).get("notices") or [])
    print(f"{'✓' if hit else '✗'} 적합성 판정 재료에 정보제공 고지가 붙는다")
    ok += hit

    # 선언이 없는 재료에는 붙지 않는다 — 무조건 붙는 표시는 §7 이 막는 것이다.
    hit = tools.advisory_mark({}) is None
    print(f"{'✓' if hit else '✗'} 선언이 없으면 고지를 붙이지 않는다")
    ok += hit

    # 2026-09-02 개정(§8 관리대장): 직원 대상 도구라 특정 상품을 짚어 말하는 것은 허용하고,
    # 남는 경계는 표현이다 — «이런 상품이 있습니다» 톤까지, 권유(«추천드립니다»)는 금지.
    hit = ("특정해 말하" in COMPOSE_SYSTEM
           and "권유 표현은 쓰지 않는다" in COMPOSE_SYSTEM
           and "직원이 정한다" in COMPOSE_SYSTEM)
    print(f"{'✓' if hit else '✗'} 생성 지시가 상품 특정을 허용하되 권유 표현을 금지한다")
    ok += hit

    hit = "권유 표현" in ANSWER_SHAPES["lineup"]
    print(f"{'✓' if hit else '✗'} lineup 의 답변 형태도 권유 표현 금지를 요구한다")
    ok += hit

    hit = "투자권유가 아니라는 표시" in ANSWER_SHAPES["suitable"]
    print(f"{'✓' if hit else '✗'} suitable 의 답변 형태가 '권유 아님'을 요구한다")
    ok += hit
    return ok


def check_no_repeat() -> int:
    """좁히는 후속 질문에 앞 답을 통째로 다시 세우지 않는가.

    회귀 대상: 「그 중에 ISA 만기자금이랑 같이 가져갈 만한 건?」에 「자료가 없어요」로 잘
    시작해 놓고, 직전 턴에서 방금 말한 적합성 목록 8종 + 제외 4종을 그대로 반복했다
    (실 LLM 시연 대본 T11, 1,021자). **지시로 못 막는다** — 프롬프트에 실리는 이전 대화는
    직원 질문만이고 답변 원문이 없어서(state.Turn) LLM 은 자기가 무엇을 나열했는지 볼 수
    없다. 답변 원문을 싣는 것도 답이 아니다: 그 수치를 되받으면 이번 턴 원장 밖이라
    `verify` 가 답을 통째로 버린다(§6). 그래서 **도구 이름만** 턴에 남기고, 겹침 판정은
    코드가 한다.
    """
    ok = 0
    ev = tools._ev("suitable", "q", "■ 재료", [{"id": "s.1", "title": "적합성"}])
    seen: dict[str, str] = {}
    orig = plan.generate
    plan.generate = lambda p, **kw: seen.setdefault("p", p) or "답변"
    try:
        plan.compose({"question": "그 중에 ISA 만기자금이랑 같이 가져갈 만한 건?",
                      "evidence": [ev],
                      "history": [{"question": "그럼 이 고객한테 뭘 권할 수 있어?",
                                   "tools": ["suitable"]}]})
        hit = "직전 답변과 겹치는 자료" in seen["p"]
        print(f"{'✓' if hit else '✗'} 직전 턴과 재료가 겹치면 반복 금지 블록이 실린다")
        ok += hit

        seen.clear()
        plan.compose({"question": "q", "evidence": [ev],
                      "history": [{"question": "IRP 세액공제 한도?", "tools": ["fact"]}]})
        hit = "직전 답변과 겹치는 재료" not in seen["p"]
        print(f"{'✓' if hit else '✗'} 재료가 다르면 붙지 않는다")
        ok += hit

        seen.clear()
        plan.compose({"question": "q", "evidence": [ev], "history": []})
        hit = "직전 답변과 겹치는 재료" not in seen["p"]
        print(f"{'✓' if hit else '✗'} 첫 턴에는 붙지 않는다")
        ok += hit
    finally:
        plan.generate = orig

    # 형태 요구와 모순이 되면 안 된다 — 「목록을 실어라」와 「다시 세우지 마라」가 함께
    # 걸리면 LLM 은 어느 쪽이든 지키려다 재료를 지어낸다(§5, T13 과 같은 부류).
    from pension_agent.consult_agent.prompts import REPEAT_BLOCK
    hit = "이미 채운 항목은 다시 채우지 않아도 된다" in REPEAT_BLOCK
    print(f"{'✓' if hit else '✗'} 반복 금지 블록이 형태 요구를 명시적으로 풀어준다")
    ok += hit

    # 턴 기록에 도구 이름이 남아야 판정이 성립한다 — **답변 원문은 남기지 않는다**(§6).
    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "KB 중립형 MP 는 최근 1년 5.1% 예요.", "sources": [],
            "evidence": [ev, tools._ev("customer", "q", "■ 고객", [])],
        })})
        out = G.ask("이 고객한테 뭘 권할 수 있어?")
    finally:
        G._AGENT = orig_agent
    turn = out["history"][-1]
    hit = turn.get("tools") == ["customer", "suitable"]
    print(f"{'✓' if hit else '✗'} 턴 기록이 무슨 재료로 답했는지 남긴다({turn.get('tools')})")
    ok += hit

    hit = not any("5.1" in str(v) for v in turn.values())
    print(f"{'✓' if hit else '✗'} 턴 기록에 답변 수치는 남지 않는다")
    ok += hit
    return ok


def check_table_row_names() -> int:
    """표의 행 이름을 **답변이 부르는 표기**로도 알아보는가.

    회귀 대상: 행 이름이 「사용자부담금(퇴직금)」한 덩이라, 답변이 「퇴직금(사용자부담금)」·
    「사용자부담금」으로 부르면 그 행을 말한 줄 몰랐다. 그러면 그 행이 «답변이 말하지 않은
    행»이 되어 그 값이 남의 값으로 신고되고, **표의 네 구간을 전부 정확히 옮긴 답변이
    폐기됐다**(실 LLM 시연 대본 T8b). 카드의 pitfalls 는 반대로 「구간을 확인하지 않은 단일
    수치 답변은 오답」이라고 적혀 있어, 재료와 검증기가 서로 모순이었다.

    이름을 못 알아본 것은 판정 불가이지 위반이 아니다(§6). 별칭은 «말한 행»을 늘리는
    쪽이라 판정을 좁히기만 한다 — 오짝 검출은 그대로여야 한다(아래 ③④).
    """
    from pension_agent.consult_agent import relations
    from pension_agent.consult_agent.state import KB as _KB
    ok = 0
    card = next((c for c in _KB.cards if c["id"] == "fact.k04.f50"), None)
    if card is None:
        print("✗ fact.k04.f50 카드를 찾지 못했다")
        return 0

    both = ("퇴직금(사용자부담금) 대면은 5천만원 미만 연 0.45%, 5천만원 이상 연 0.38%이고, "
            "비대면은 5천만원 미만 연 0.20%, 5천만원 이상은 면제예요. 가입자부담금 대면은 "
            "1억원 미만 연 0.28%, 1억원 이상 연 0.25%이고, 비대면은 1억원 미만 연 0.23%, "
            "1억원 이상 연 0.21%예요.")
    hit = not relations.check(both, [card])
    print(f"{'✓' if hit else '✗'} 두 부담금을 함께 정확히 말한 답변이 통과한다(T8b 회귀)")
    ok += hit

    one = "가입자부담금 대면은 1억원 미만 연 0.28%, 1억원 이상 연 0.25%예요."
    hit = not relations.check(one, [card])
    print(f"{'✓' if hit else '✗'} 한쪽만 말한 답변도 그대로 통과한다")
    ok += hit

    wrong = "가입자부담금 대면 1억원 미만은 연 0.45%예요."
    hit = bool(relations.check(wrong, [card]))
    print(f"{'✓' if hit else '✗'} 가입자부담금에 사용자부담금 값을 붙이면 잡힌다")
    ok += hit

    # 예전에는 이 방향이 **판정 불가로 통과**했다 — 「사용자부담금(퇴직금)」을 아예 못
    # 알아봐서 said 가 비었기 때문이다. 별칭이 그 구멍을 함께 막는다.
    flipped = "퇴직금(사용자부담금) 대면 5천만원 미만은 연 0.28%예요."
    hit = bool(relations.check(flipped, [card]))
    print(f"{'✓' if hit else '✗'} 사용자부담금에 가입자부담금 값을 붙여도 잡힌다")
    ok += hit
    return ok


def check_suitable_shape() -> int:
    """적합성 재료와 답변 형태가 서로 모순되지 않는가 — 제외가 0건인 고객.

    회귀 대상: 형태가 「제외된 상품과 사유」를 무조건 요구하는데 재료는 제외 0건일 때
    침묵했다. LLM 은 형태를 지키려고 **통과 목록에서 하나를 골라 뺐다** — 12종을 11종이라
    말하고 "상담 실익이 없다"는 재료에 없는 사유를 붙였다(실 LLM 시연 대본 T13, 정민석).
    재료에 없는 것을 요구한 쪽이 원인이다.

    「게이트」는 개발 용어라 재료에서 걷어낸다 — 재료에 있으면 답변이 그대로 쓴다.
    """
    from pension_agent.consult_agent.prompts import ANSWER_SHAPES, SHAPE_BLOCK
    ok = 0
    open_top = tools._suitable({"customer_id": "181245-3097614"}, "q")   # 상한 = 최고 등급
    capped = tools._suitable({"customer_id": "188406-7352194"}, "q")     # 제외가 있는 고객
    if not open_top or not capped:
        print("✗ 적합성 재료를 만들지 못했다")
        return 0

    hit = "안내할 수 없는 상품 없음" in open_top["text"]
    print(f"{'✓' if hit else '✗'} 제외 0건이면 재료가 «없음»을 말한다(침묵하지 않는다)")
    ok += hit

    hit = "안내할 수 없는 상품 4종" in capped["text"]
    print(f"{'✓' if hit else '✗'} 제외가 있으면 종수와 사유를 그대로 싣는다")
    ok += hit

    hit = all("게이트" not in x["text"] for x in (open_top, capped))
    print(f"{'✓' if hit else '✗'} 재료 본문에 개발 용어 «게이트»가 없다")
    ok += hit

    hit = all("게이트" not in (s.get("doc") or "")
              for x in (open_top, capped) for s in x["sources"])
    print(f"{'✓' if hit else '✗'} 근거 출처 이름에도 «게이트»가 없다")
    ok += hit

    hit = "안내할 수 없는 상품이 있으면" in ANSWER_SHAPES["suitable"]
    print(f"{'✓' if hit else '✗'} 형태가 제외를 조건부로 요구한다")
    ok += hit

    hit = "자료가 적은 종수를 그대로 쓴다" in ANSWER_SHAPES["suitable"]
    print(f"{'✓' if hit else '✗'} 형태가 통과 종수를 그대로 쓰라고 요구한다")
    ok += hit

    hit = "자료에 없는 항목은 쓰지 않는다" in SHAPE_BLOCK
    print(f"{'✓' if hit else '✗'} 형태 머리말이 «없으면 안 쓴다»를 전역으로 건다")
    ok += hit
    return ok


def check_question_echo() -> int:
    """직원이 질문에 넣은 수치를 **되받아 말한** 답변이 살아남는가.

    회귀 대상: 원장은 턴 단위인데 대화는 이어진다. "총급여 6천만원이면 얼마 돌려받아?"
    에 답하려면 답변이 그 전제를 옮겨 적는데(6,000), 카드가 아는 경계값은 5,500 뿐이라
    **맞는 답변이 원장 밖 수치로 통째로 폐기되고** 근거 원문이 덤프됐다 — 실 LLM 시연
    대본 T2 의 실제 결과다. §6 이 "검증기가 옳은 문장을 거부하는 것은 틀린 문장을
    통과시키는 것보다 나쁘다"고 적어 둔 자리다.

    넓히는 폭은 «되받기» 하나다. 질문의 수치로 **계산한** 값과 **상품명**은 그대로 막힌다.
    """
    ok = 0
    VALUE = "총급여 5,500만원 이하 16.5%, 초과 13.2% (지방소득세 포함)"
    ev = tools._ev("fact", "q", f"■ 세액공제율\n{VALUE}",
                   [{"id": "f.1", "title": "세액공제율"}], atomic=[VALUE])
    q = "총급여 6천만원이면 얼마 돌려받아?"
    echoed = "총급여 6,000만원이면 초과 구간이에요."

    # ① 구멍 재현 — 질문을 재료로 안 보면 되받은 문장이 폐기된다.
    hit = not verify_texts(echoed, [ev["text"]])[0]
    print(f"{'✓' if hit else '✗'} 질문을 빼면 되받은 답변이 폐기된다(구멍 재현)")
    ok += hit

    # ② 질문을 함께 보면 통과한다 — 직원이 방금 말한 값을 옮겨 적은 것이다.
    hit = verify_texts(echoed, [ev["text"]], echoable=[q])[0]
    print(f"{'✓' if hit else '✗'} 질문의 수치를 되받은 답변은 통과한다")
    ok += hit

    # ③ 되받기까지다. 질문 수치로 **계산한** 값은 질문에도 원장에도 없다.
    derived = "총급여 6,000만원이면 792만원을 돌려받아요."
    good, bad = verify_texts(derived, [ev["text"]], echoable=[q])
    hit = not good and any("792" in b for b in bad)
    print(f"{'✓' if hit else '✗'} 질문 수치로 계산한 값은 여전히 거부된다")
    ok += hit

    # ④ 상품명 게이트는 넓어지지 않는다 — 이름만 대서 적합성 밖 상품을 올릴 수 없다.
    known = {"KB 글로벌리츠 ETF"}
    hit = not verify_texts("KB 글로벌리츠 ETF 를 보실 수 있어요.", ["다른 재료"],
                           known_products=known, echoable=["KB 글로벌리츠 ETF 어때?"])[0]
    print(f"{'✓' if hit else '✗'} 질문이 부른 상품명은 인용 허가가 되지 않는다")
    ok += hit

    # ⑤ 배선 — compose 가 실제로 이번 턴 질문을 넘긴다(넘기지 않으면 ① 로 되돌아간다).
    orig = plan.generate
    try:
        plan.generate = lambda p, **kw: echoed
        out = plan.compose({"question": q, "evidence": [ev]})
        hit = out["answer"] == echoed
    finally:
        plan.generate = orig
    print(f"{'✓' if hit else '✗'} compose 가 질문을 검증 재료로 넘긴다")
    ok += hit

    # ⑥ 「없는 것은 첫 문장에서 없다고」 — 가진 재료로 다른 질문에 답하지 않게 하는 지시.
    from pension_agent.consult_agent.prompts import COMPOSE_SYSTEM
    hit = "핵심 대상이 자료에 없으면 그것이 결론" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 «없음»을 결론 자리에 세운다")
    ok += hit

    # ⑦ 고객 속성은 원장 값만 — 화법·방법론 카드의 상황 설명(«비대면 개설 + 권유직원
    # 미존재…»)을 이 고객의 속성으로 굳히지 않게 하는 지시. 실 LLM 시연 대본 T4 에서
    # m.045 카드를 근거로 "비대면 신규 계좌"라고 단정했는데, 원장에는 채널 컬럼 자체가
    # 없다 — 숫자가 아니라 검증 게이트에도 걸리지 않는 자리라 지시로 막는다.
    hit = "원장 값에 있는 것만" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 고객 속성 단정을 원장 값으로 한정한다 (T4 회귀)")
    ok += hit

    # ⑧ 인용이 생략한 조건은 코칭 문장이 채운다 — 실 LLM 시연 대본 T9 에서 답변이 표로는
    # 「대면 5천만원 이상 0.38%」라 말하고, 채널 조건을 생략한 대사 원문을 근거로
    # 「5천만원 이상이면 면제」라고 이어 말해 스스로 모순됐다. 원문은 못 고치므로(절대
    # 규칙 1) 조건 보완은 생성 지시가 맡는다.
    hit = "생략한 조건은 코칭 문장이 채운다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 인용 대사의 생략 조건을 채우게 한다 (T9 회귀)")
    ok += hit

    # ⑨ 상품 나열은 한 줄에 하나 — 실 LLM 시연 대본 T13 에서 12종을 쉼표로 이은 한
    # 문단이 나왔다. 출력 형식의 «불릿 없이»가 목록까지 줄글로 밀어붙인 것이라, 3종
    # 이상 나열에는 예외를 선언한다.
    hit = "3종 이상 나열할 때는 줄글로 잇지 않는다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 상품 나열을 줄 단위로 세우게 한다 (T13 회귀)")
    ok += hit

    # ⑩ 스냅샷에서 이력·추세를 추정하지 않는다 — 실 LLM 시연 대본 T4 에서 카드의
    # 「당해 납입액 0원」만 보고 "전년 납입 이력이 있었던 고객이라 납입이 끊긴 신호"라고
    # 지어냈다. 원장의 연도별 납입액은 전부 0원인데 0원 연도는 카드 렌더에서 빠지므로
    # (customer._paid_by_year 의 `and v` 필터) LLM 은 과거 값을 본 적이 없다. 성립 요건
    # 6종에 없는 «납입 중단»을 일곱 번째 사유로 세운 것도 같은 문장이다 — 숫자가 없어
    # 검증 게이트에 안 걸리는 자리라 지시로 막는다.
    hit = "과거 이력이나 추세를 추정하지 않는다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 스냅샷 값의 이력·추세 추정을 금지한다 (T4 회귀)")
    ok += hit
    hit = "사유를 새로 만들어 붙이지 않는다" in COMPOSE_SYSTEM
    print(f"{'✓' if hit else '✗'} 생성 지시가 관리 사유를 성립 요건 목록으로 한정한다 (T4 회귀)")
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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


def check_fact_in_index() -> int:
    """팩트가 카드 색인에 있는가 — LLM 카드 선택의 후보가 되는가(§3).

    팩트는 오래도록 «id 로 참조되는 값»이기만 해서(kinds.json `consumed: reference`) 카드
    색인 밖에 살았고, 그래서 **9종 재료 중 유일하게 LLM 카드 선택을 못 받았다**. 다른
    종류는 LLM 이 버킷→카드로 고르고 못 고를 때만 n-gram 으로 물러서는데(select.pick),
    팩트는 n-gram 하나뿐이라 직원 말과 카드 말이 다르면 통째로 0건이 났다 —
    "연말정산 얼마나 돌려받아?" 가 세액공제 카드를, "중도에 깨면 세금 얼마나 떼?" 가
    중도해지 카드를 못 찾았다. 하필 팩트는 한도·세율처럼 숫자를 묻는 재료다.

    여기서 재는 것은 **배선**이다(모델의 판단이 아니라). LLM 이 골랐을 때 그 팩트가 실제로
    돌아오는지, 그리고 못 골랐을 때 n-gram 이 예전 그대로인지.
    """
    ok = 0
    from pension_agent.consult_agent import kb as K
    from pension_agent.consult_agent.nodes import facts_qa

    kb = tools.KB
    # 같은 객체로 두 자리에 산다 — 사본이면 한쪽만 고쳐지는 자리가 생긴다(화법과 같은 규약).
    f2 = kb.facts["fact.k04.f2"]
    hit = any(c is f2 for c in kb.cards) and sum(1 for c in kb.cards if c["_kind"] == "fact") == len(kb.facts)
    print(f"{'✓' if hit else '✗'} 팩트가 카드 색인에 **같은 객체로** 실린다 ({len(kb.facts)}장)")
    ok += hit

    # 버킷 카탈로그에 종류가 뜬다 — 여기 빠지면 LLM 후보에서 통째로 사라진다.
    cat = K.index_catalog(kb, ("fact",))
    hit = cat.startswith("■ fact") and "납입·세액공제" in cat
    print(f"{'✓' if hit else '✗'} 팩트 버킷이 카탈로그에 뜬다")
    ok += hit

    # 슬라이스에 카드가 예상질문과 함께 실린다 — LLM 이 id 를 고를 재료다.
    sl = K.index_slice(kb, ["X01"], kinds=("fact",))
    hit = "fact.k04.f2" in sl and "예상질문" in sl
    print(f"{'✓' if hit else '✗'} 팩트 슬라이스에 카드와 예상질문이 실린다")
    ok += hit

    # LLM 이 골랐을 때 그 팩트가 실제로 돌아오는가(배선 검증 — 캔드 응답).
    # 이 스위트는 전역에서 llm_pick 을 꺼 두므로(머리말) 여기서만 원본을 되살린다 —
    # check_hier_index 와 같은 방식이다. 모델 응답은 캔드로 고정한다.
    canned = iter(['["X01"]', '["fact.k04.f2"]'])
    real_gen, real_pick = select.generate, select.llm_pick
    select.generate = lambda prompt, **kw: next(canned)
    select.llm_pick = _REAL_LLM_PICK
    try:
        got = [h[1]["id"] for h in facts_qa.search("연말정산 얼마나 돌려받아?")]
    finally:
        select.generate, select.llm_pick = real_gen, real_pick
    hit = got[:1] == ["fact.k04.f2"]
    print(f"{'✓' if hit else '✗'} LLM 이 고른 팩트가 검색 결과로 돌아온다 {got[:1]}")
    ok += hit

    # 못 골랐을 때는 예전 n-gram 그대로다 — 넓히기만 하고 좁히지 않는다.
    hit = [h[1]["no"] for h in facts_qa.search("세액공제 한도")][:1] == ["F2"]
    print(f"{'✓' if hit else '✗'} LLM 이 못 고르면 n-gram 폴백이 예전대로 동작한다")
    ok += hit
    return ok


def check_tax_credit_calc() -> int:
    """환급 예상액 계산기(07/01 ② 3번) — 「얼마 더 넣으면 얼마 받나」에 답이 없던 자리.

    재료에는 **현재 납입액 기준 한 값**만 있었고("예상 세액공제액 118만원"), 재료 밖 계산은
    금지라(§5) 직원이 실제로 묻는 것에 답할 방법이 없었다. 그 장이 근거로 든 것이 이것이다 —
    직원 두 명이 각자 엑셀 계산기를 만들어 배포했을 만큼 니즈가 강하다.

    여기서 재는 것 다섯:
      ① 입력 금액은 **직원이 친 말**에서 뽑는다(계획 LLM 의 재작성본이 아니라)
      ② 총급여 구간이 미확인이면 두 경우를 다 낸다
      ③ 한도를 이미 채웠으면 «추가 공제 없음»으로 갈리고, 그 갈래에는 결정세액 단서를
         붙이지 않는다 — 최대 환급액을 단정할 때 걸리는 단서라 여기서는 무관하다(§7)
      ④ 계산 결과는 인용할 수 있고, 계산 밖 금액은 잘린다
      ⑤ **«어디에 넣는 금액인가»가 재료에 있다** — 질문("300만원 더 넣으면")에는 계좌가
         없다. 갈래가 있어서가 아니라 직원이 말하지 않을 뿐이고, 열려 있는 고객의 계좌가
         개인형IRP 다. 재료가 비워 두면 답변도 비우고, 직원은 어느 계좌에 넣는 300만원인지
         적히지 않은 금액을 고객에게 옮긴다. 한도 쪽은 반대로 계좌를 가리지 않는다 —
         연금저축과 함께 쓰는 한도라(fact.k04.f2) 그 사실도 함께 적혀야 한다
    """
    ok = 0
    from pension_agent.consult_agent import relations as REL
    from pension_agent.strategy_agent import customer as CUST

    room = next(p for p in CUST.PERSONAS if p.room > 0)          # 잔여한도가 있는 고객
    full = next(p for p in CUST.PERSONAS if p.room == 0)         # 한도를 채운 고객

    # ① 금액은 직원 질문에서 온다. 계획이 넘기는 query 에 다른 수가 있어도 그쪽을 안 쓴다.
    ev = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "300만원 더 넣으면 얼마 받아?"}, "세액공제 900만원")
    hit = ev is not None and "추가 납입액 300만원" in ev["text"]
    print(f"{'✓' if hit else '✗'} 입력 금액은 직원 질문에서 뽑는다(계획의 재작성본이 아니다)")
    ok += hit

    # 단위 없는 맨숫자는 금액으로 보지 않는다 — 300원인지 300만원인지 가릴 근거가 없다.
    bare = tools.TOOLS["tax_credit"].run(
        {"customer_id": room.id, "question": "300 더 넣으면 얼마 받아?"}, "q")
    hit = bare is not None and "질문에 금액이 없어 잔여한도로 계산했다" in bare["text"]
    print(f"{'✓' if hit else '✗'} 단위 없는 맨숫자는 금액으로 읽지 않는다(잔여한도로 떨어진다)")
    ok += hit

    # ② 구간 미확인이면 두 경우를 다 낸다.
    hit = all(f"{r * 100:.1f}%" in ev["text"] for r in CUST.TAX_CREDIT_RATE.values())
    print(f"{'✓' if hit else '✗'} 총급여 구간 미확인이면 두 공제율을 다 싣는다")
    ok += hit

    # ③ 한도를 채운 고객은 다른 갈래로 가고, 그 갈래에는 결정세액 단서가 없다.
    done = tools.TOOLS["tax_credit"].run(
        {"customer_id": full.id, "question": "500만원 더 넣으면 얼마 받아?"}, "q")
    hit = done is not None and "추가 공제 대상이 없다" in done["text"] and not done["notices"]
    print(f"{'✓' if hit else '✗'} 한도를 채웠으면 «추가 공제 없음» + 결정세액 단서를 붙이지 않는다"
          + ("" if hit else f" — notices={(done or {}).get('notices')}"))
    ok += hit

    # 반대로 금액을 내놓는 갈래에는 반드시 붙는다. 문장은 코드가 아니라 카드에서 온다.
    card = tools.KB.facts[tools.TAX_FACT_ID]
    hit = bool(ev["notices"]) and ev["notices"][0] in card["value"]
    print(f"{'✓' if hit else '✗'} 금액을 내놓는 갈래에는 카드가 못박은 단서가 따라붙는다")
    ok += hit

    # ④ 계산 결과는 인용 가능, 계산 밖 금액은 잘린다.
    gain = CUST.tax_credit(min(room.pension_paid_ytd + room.room * 10_000,
                               CUST.TAX_CREDIT_CAP_WON), CUST.TAX_CREDIT_RATE["5500이하"]) \
        - CUST.tax_credit(room.pension_paid_ytd, CUST.TAX_CREDIT_RATE["5500이하"])
    hit = (_vt(f"16.5% 구간이면 {gain:,}원 더 돌려받으세요.", ev["allow"])[0]
           and not _vt(f"16.5% 구간이면 {gain + 70_000:,}원 더 돌려받으세요.", ev["allow"])[0])
    print(f"{'✓' if hit else '✗'} 계산 결과는 인용되고 계산 밖 금액은 잘린다 ({gain:,}원)")
    ok += hit

    # 공제율 카드의 조건–값 짝도 그대로 걸린다(오짝은 수치 검사로는 안 잡힌다).
    cards = tools.ledger_related([ev])
    hit = (not REL.check(ev["text"], cards)
           and bool(REL.check("총급여 5,500만원 초과면 16.5% 적용돼요.", cards)))
    print(f"{'✓' if hit else '✗'} 재료는 자기대조를 통과하고, 공제율 오짝은 잡힌다")
    ok += hit

    # ⑤ 어디에 넣는 금액인지가 재료에 있다. 없으면 답변도 말하지 않는다.
    hit = "개인형IRP 계좌 추가 납입" in ev["text"]
    print(f"{'✓' if hit else '✗'} 어느 계좌에 넣는 금액인지를 재료가 적는다")
    ok += hit

    # 정해져 있는 값이라 «가정하면» 으로 적지 않는다 — 추측처럼 적으면 직원은 확인해야
    # 할 것이 있는 줄 안다. 되묻기 대상도 아니다(갈래가 없다).
    hit = not any(w in ev["text"] for w in ("가정", "~라면", "로 보고 계산"))
    print(f"{'✓' if hit else '✗'} 계좌를 가정 어법으로 적지 않는다(정해져 있는 값이다)")
    ok += hit

    # 한도 쪽은 반대로 계좌를 가리지 않는다 — 「IRP 에 900만원까지」로 읽히면 안 된다.
    hit = "연금저축 납입분까지 합산한 값" in ev["text"]
    print(f"{'✓' if hit else '✗'} 한도·잔여한도가 연금저축과 합산된 값이라는 것을 함께 적는다")
    ok += hit

    # 브리핑과 계산기가 같은 산식을 쓴다 — 두 곳이 각자 곱하면 화면과 답변이 갈린다.
    from pension_agent.strategy_agent import agent as SA
    shown = SA.propose(room)["facts"]["briefing"]["예상_세액공제액"]
    now = CUST.tax_credit(room.pension_paid_ytd, room.tax_credit_rate)
    hit = f"{now // 10_000:,}만원" in shown or f"{now:,}" in shown
    print(f"{'✓' if hit else '✗'} 화면의 예상 세액공제액과 같은 산식을 쓴다 ({shown})")
    ok += hit
    return ok + check_tax_credit_isa()


def check_tax_credit_isa() -> int:
    """ISA 만기자금 전환 — 900만원 한도 «안»이 아니라 그 한도에 **더해지는** 축.

    이 갈래가 없던 동안 「ISA 8,000만원 중 일부만 옮기면 세액공제 어떻게 돼?」에 잔여한도
    500만원으로 답했다 — 같은 대화에서 방금 인용한 카드(fact.k04.f4 「최대 1,200만원」)와
    어긋나는 금액이었고, 카드가 오답으로 못박은 「전환금 전액이 공제 대상」의 반대편 오답
    (「전환해도 잔여한도까지만」)이었다.

    재는 것 다섯:
      ① ISA 보유 고객이면 질문에 'ISA' 라는 말이 없어도 전환 축이 실린다(되묻기 뒤의
         한 마디 답 "초과야" 가 그 자리다 — 말에서 찾으면 정작 필요한 턴에 빠진다)
      ② 늘어나는 것은 전환액의 10%(300만원 상한)이지 전환액 전체가 아니다
      ③ 잔여한도가 0이어도 전환으로는 더 받을 수 있다 — 두 축이 다르다는 것의 실증
      ④ 60일이 지난 자금에는 싣지 않는다
      ⑤ ISA 가 없는 고객의 답은 예전과 같다(축을 늘리지 않는다)
    """
    ok = 0
    from pension_agent.strategy_agent import customer as CUST

    isa = next(p for p in CUST.PERSONAS if p.isa and p.room > 0)
    ev = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "초과야"}, "세액공제")
    text = (ev or {}).get("text", "")

    hit = ev is not None and "ISA 만기자금을 전환하는 경우" in text
    print(f"{'✓' if hit else '✗'} 질문에 'ISA' 가 없어도 보유 고객이면 전환 축이 실린다")
    ok += hit

    # ② 전환액 전체가 아니라 10%·300만원 상한. 상한에 닿는 전환액도 함께 있어야
    #    「일부만 옮기면?」이 금액과 이어진다.
    cap = CUST.ISA_ROLLOVER_CREDIT_CAP_WON
    at_cap = int(cap / CUST.ISA_ROLLOVER_CREDIT_RATE)
    add = CUST.isa_rollover_credit(isa.isa["amount"])
    hit = (add == cap and CUST.isa_rollover_credit(10_000_000) == 1_000_000
           and f"{at_cap // 10_000:,}만원에서 상한에 닿는다" in text
           and "전환금 전액이 공제 대상이 되는 것이 아니다" in text)
    print(f"{'✓' if hit else '✗'} 늘어나는 몫은 전환액의 10%(상한 {cap // 10_000:,}만원)다")
    ok += hit

    # 환급액은 «늘어난 공제 대상»에만 공제율을 곱한 값이다. 전환액을 tax_credit() 에 그대로
    # 넣으면 8,000만원이 한도를 채운 것으로 계산된다 — 그 오답이 여기서 갈린다.
    gain = CUST.tax_credit(add, CUST.TAX_CREDIT_RATE["5500초과"])
    hit = f"{gain:,}원" in text and _vt(f"이 전환으로 {gain:,}원 더 돌려받아요.", ev["allow"])[0]
    print(f"{'✓' if hit else '✗'} 전환 환급액은 늘어난 공제 대상 × 공제율이다 ({gain:,}원)")
    ok += hit

    # 카드가 못박은 1,200만원 한도와 코드의 두 상수가 같은 값을 말한다.
    hit = f"{(CUST.TAX_CREDIT_CAP_WON + cap) // 10_000:,}만원" in text
    print(f"{'✓' if hit else '✗'} 공제 대상 한도가 900만원 → 1,200만원으로 늘어난다고 싣는다")
    ok += hit

    # ③ 잔여한도가 0인데 ISA 가 있는 고객 — 예전에는 "더 넣어도 안 늘어난다"로 끝났다.
    full_isa = next((p for p in CUST.PERSONAS if p.isa and p.room == 0), None)
    if full_isa is not None:
        zero = tools.TOOLS["tax_credit"].run({"customer_id": full_isa.id, "question": "얼마 더 받아?"}, "q")
        hit = (zero is not None and "추가 공제 대상이 없다" in zero["text"]
               and "ISA 만기자금을 전환하는 경우" in zero["text"] and bool(zero["notices"]))
        print(f"{'✓' if hit else '✗'} 잔여한도 0이어도 전환 축은 따로 답한다 + 결정세액 단서가 붙는다")
        ok += hit

    # ④ 60일이 지나면 안내할 수 없는 것이라 싣지 않는다.
    keep = isa.isa
    try:
        isa.isa = dict(keep, dd=-(CUST.ISA_ROLLOVER_DEADLINE_DAYS + 1))
        late = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "얼마 더 받아?"}, "q")
        hit = late is not None and "ISA 만기자금을 전환하는 경우" not in late["text"]
        print(f"{'✓' if hit else '✗'} 전환 기한(60일)이 지난 자금에는 싣지 않는다")
        ok += hit
        isa.isa = dict(keep, dd=-10)
        mid = tools.TOOLS["tax_credit"].run({"customer_id": isa.id, "question": "얼마 더 받아?"}, "q")
        hit = mid is not None and "만기 10일 경과 · 전환 기한 50일 남음" in mid["text"]
        print(f"{'✓' if hit else '✗'} 만기가 지났어도 60일 안이면 남은 기한을 싣는다")
        ok += hit
    finally:
        isa.isa = keep

    # ⑤ ISA 가 없는 고객에게는 축을 늘리지 않는다.
    plain = next(p for p in CUST.PERSONAS if not p.isa and p.room > 0)
    ev2 = tools.TOOLS["tax_credit"].run({"customer_id": plain.id, "question": "300만원 더 넣으면?"}, "q")
    hit = ev2 is not None and "ISA" not in ev2["text"] and "연금계좌에 현금을" not in ev2["text"]
    print(f"{'✓' if hit else '✗'} ISA 가 없는 고객의 재료에는 전환 축도 축 이름도 없다")
    ok += hit
    return ok


def check_labeled_pairs() -> int:
    """레이블–값 짝(§6) — 「이 항목의 값이라며 남의 수치를 붙였는가」.

    고객 재료의 허용 집합에는 화면 값 말고도 ⑥⑦⑧ 에 실린 화법·반론·참고자료의 수치가 함께
    들어 있다 — 직원이 그것도 묻기 때문에 뺄 수 없다. 그래서 수치 집합 포함 검사만으로는
    **"세액공제 잔여한도는 300만원이에요"(실제 0만원)가 통과했다** — 300 은 화법 문구
    「적립금 300만원 이상…」에 실제로 있는 숫자다. 경계 밖으로 나간 게 아니라 **엉뚱한
    이름표에 갖다 붙인 것**이라, 막을 자리는 verify 가 아니라 relations 다.

    이 테스트가 재는 것 셋. 뒤엣것이 더 중요하다 — 옳은 문장을 거부하는 것은 틀린 문장을
    통과시키는 것보다 나쁘다(relations.py 머리말).
      ① 남의 값을 갖다 붙이면 잡는다
      ② 재료를 그대로 옮긴 답변은 한 줄도 막지 않는다
      ③ 이름이 재료의 다른 자리에도 나오는 항목은 아예 판정하지 않는다(판정 불가)
    """
    ok = 0
    from pension_agent.consult_agent import relations as REL
    from pension_agent.strategy_agent import customer as CUST

    evs = {p.id: tools.TOOLS["customer"].run({"customer_id": p.id}, "확인") for p in CUST.PERSONAS}

    # ① 그 항목의 값이 아닌 수치를 붙이면 잡는다.
    cid = CUST.PERSONAS[0].id
    cards = tools.ledger_related([evs[cid]])
    rows = REL.checkable(cards[0]["labeled"], cards[0]["context"])
    numeric = [r for r in rows if REL.numbers(r["value"])]
    caught = cases = 0
    for i, row in enumerate(numeric):
        other = numeric[(i + 1) % len(numeric)]
        if other["value"] == row["value"]:
            continue
        cases += 1
        caught += bool(REL.check(f"{row['label']}은 {other['value']}이에요.", cards))
    hit = cases and caught / cases >= 0.8
    print(f"{'✓' if hit else '✗'} 남의 값을 갖다 붙이면 잡는다 ({caught}/{cases})")
    ok += hit

    # 원래 증상 그대로. 재료 밖 수치가 아니라 **재료 안에 있는 남의 수치**여야 의미가 있다.
    ev = evs[cid]
    room = [r for r in cards[0]["labeled"] if r["label"] == "세액공제 잔여한도"]
    wrong = f"세액공제 잔여한도는 300만원이에요."
    hit = bool(room) and bool(REL.check(wrong, cards)) and _vt(wrong, ev["allow"])[0]
    print(f"{'✓' if hit else '✗'} 수치 검사는 통과하지만 관계 검사가 잡는다 (원래 증상)")
    ok += hit

    # ② 재료를 그대로 옮긴 답변은 막지 않는다 — 9명 전원의 모든 줄.
    false_rej, total = 0, 0
    for e in evs.values():
        c2 = tools.ledger_related([e])
        for line in e["text"].split("\n")[1:]:
            total += 1
            false_rej += bool(REL.check(line.strip("· ").strip(), c2))
    hit = false_rej == 0
    print(f"{'✓' if hit else '✗'} 재료를 그대로 옮긴 답변은 막지 않는다 ({total}줄 · 거짓 거부 {false_rej})")
    ok += hit

    # 여러 항목을 한 답변에 묶어도 마찬가지다.
    joined = 0
    for e in evs.values():
        whole = " ".join(l.strip("· ").strip() for l in e["text"].split("\n")[1:])
        joined += bool(REL.check(whole, tools.ledger_related([e])))
    hit = joined == 0
    print(f"{'✓' if hit else '✗'} 여러 항목을 묶어 말해도 막지 않는다 ({joined}/9)")
    ok += hit

    # ③ 이름이 겹치는 항목은 판정 대상에서 빠진다 — 「수익률」은 다른 값 안에도 있다.
    labels = {r["label"] for r in rows}
    hit = "수익률" not in labels and "운용수익률" in labels
    print(f"{'✓' if hit else '✗'} 이름이 겹치는 항목은 판정하지 않는다(수익률 제외·운용수익률 유지)")
    ok += hit

    # context 를 안 넘기면 문제상황 제목 같은 다른 자리를 못 걸러낸다 — 넘기는 쪽이 안전하다.
    hit = len(REL.checkable(cards[0]["labeled"], cards[0]["context"])) <= \
          len(REL.checkable(cards[0]["labeled"]))
    print(f"{'✓' if hit else '✗'} 재료 전문을 넘기면 판정 대상이 좁아진다(넓어지지 않는다)")
    ok += hit
    return ok


def check_account_state() -> int:
    """계좌 상태 재료(§3) — «정상»인 항목을 물었을 때 답이 없던 자리.

    화면(①~⑨)은 «왜 이 고객이 관리 대상인가»를 보여주는 자리라 요건이 성립한 항목만
    렌더한다. 그게 맞다 — 한 장짜리 브리핑이다. 그런데 대화형은 같은 재료로 직원이 묻는
    아무 질문에나 답하므로, 그 필터가 그대로 넘어오면 **부정 확인만 되고 긍정 확인이
    안 된다**: "디폴트옵션 설정돼 있어?" 가 미설정 고객에게만 답해지고, 설정된 고객에게는
    "준비된 자료가 없어요" 가 나갔다 — 정확히 "네, 돼 있습니다" 라고 답해야 하는 자리에서.

    값이 없어서가 아니었다. 전부 Profile 에 있었고, 렌더 경로만 걸러냈다. 그래서 이 테스트는
    **9명 전원**에 대해 재료가 있는지 본다 — 한 명이라도 빠지면 그 상태의 고객이 답을 못 받는
    것이고, 그게 원래 증상이었다(고치기 전 0~3/9).
    """
    ok = 0
    from pension_agent.strategy_agent import customer as CUST

    STATES = ("디폴트옵션", "연금개시", "연금개시요건", "세액공제 잔여한도",
              "판매중단 보유상품", "ISA 만기자금", "IRP 가입일")
    texts = {p.id: ((tools.TOOLS["customer"].run({"customer_id": p.id}, "확인") or {}).get("text", ""))
             for p in CUST.PERSONAS}
    for key in STATES:
        missing = [pid for pid, t in texts.items() if key not in t]
        hit = not missing
        print(f"{'✓' if hit else '✗'} 계좌 상태 «{key}» 가 9명 전원 재료에 있다"
              + ("" if hit else f" — 빠진 고객 {len(missing)}명"))
        ok += hit

    # 값이 «정상»인 쪽도 말할 수 있어야 한다. 미설정만 실리던 것이 원래 증상이라, 설정된
    # 고객에서 그 값이 나오는지를 따로 본다.
    setted = [p for p in CUST.PERSONAS if p.dopt == "설정"]
    hit = bool(setted) and all("디폴트옵션 설정" in texts[p.id] for p in setted)
    print(f"{'✓' if hit else '✗'} 디폴트옵션이 «설정»된 고객도 그 사실을 재료로 갖는다 ({len(setted)}명)")
    ok += hit

    # 없는 것도 «없음»이라고 말할 수 있어야 한다 — 침묵과 부재는 다르다.
    clean = [p for p in CUST.PERSONAS if not any(h.get("discontinued") for h in p.holdings)]
    hit = bool(clean) and all("판매중단 보유상품 없음" in texts[p.id] for p in clean)
    print(f"{'✓' if hit else '✗'} 판매중단 상품이 없는 고객은 «없음»을 재료로 갖는다 ({len(clean)}명)")
    ok += hit

    # 화면은 건드리지 않았다 — 계좌 상태는 briefing(화면 요건)이 아니라 별도 키다.
    from pension_agent.strategy_agent import agent as SA
    facts = SA.propose(CUST.PERSONAS[0])["facts"]
    hit = ("account_state" in facts
           and not (set(facts["account_state"]) & set(facts["briefing"]))
           and not (set(facts["account_state"]) & set(facts["customer"])))
    print(f"{'✓' if hit else '✗'} 계좌 상태는 화면(briefing·상단)이 아니라 대화형 재료다")
    ok += hit

    # 가입일은 **날짜로** 싣는다. 경과연수만 주면 LLM 이 오늘에서 빼서 날짜를 만들어 말한다.
    p0 = CUST.PERSONAS[0]
    ev = tools.TOOLS["customer"].run({"customer_id": p0.id}, "언제 가입했어?")
    hit = bool(p0.joined) and p0.joined in (ev or {}).get("text", "")
    print(f"{'✓' if hit else '✗'} 가입일이 경과연수가 아니라 날짜로 실린다")
    ok += hit

    # 실린 값은 인용할 수 있고, 안 실린 날짜는 여전히 막힌다(경계는 넓어지지 않았다).
    from datetime import date, timedelta
    allow = (ev or {}).get("allow") or []
    real = date.fromisoformat(p0.joined)
    wrong = real + timedelta(days=3)
    hit = (_vt(f"{real.year}년 {real.month}월 {real.day}일에 가입하셨어요.", allow)[0]
           and not _vt(f"{wrong.year}년 {wrong.month}월 {wrong.day}일에 가입하셨어요.", allow)[0])
    print(f"{'✓' if hit else '✗'} 가입일은 인용되고, 하루라도 어긋난 날짜는 잘린다")
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

    # 고객 화면이 닫혀 있으면 어느 고객인지가 없다 — «확인하지 못함»이라 None 이고, 계획은
    # 다른 도구를 써 볼 여지가 남는다.
    hit = closed is None
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫혔으면 지어내지 않는다")
    ok += hit

    # **기록 0건은 «확인한 값»이라 재료다**(2026-09-02). None 이던 동안 이것이 «질의가
    # 빗나감»과 구별되지 않아, 계획이 재계획으로 `customer` 를 끌어와 브리핑 한 편을
    # 원장에 싣고 질문과 무관한 ⑥⑦⑧ 화법 카드를 «근거»로 세웠다. 시효 표시는 붙지
    # 않는다 — 낡을 값 자체가 없다.
    hit = (bool(unseen) and tools.HISTORY_NONE in unseen["text"]
           and tools.HISTORY_MARK not in unseen["notices"])
    print(f"{'✓' if hit else '✗'} 기록 0건도 재료로 올라온다(없다고 답할 근거)")
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


def check_memo() -> int:
    """이번 상담 요약 → 쪽지(§3 «이번 상담 대화» 재료 · §10 쪽지 제안).

    확정본 마지막 턴 「대화 내용 요약해서 쪽지로 보내줘」→「응, 보내줘」의 코드 경로.

      ① `transcript` 는 **이번 세션만** 싣고 `history` 는 이번 세션을 뺀다 — 둘이 겹치면 방금
         한 말이 «지난 상담»이 되고, 둘 다 비면 요약할 재료가 없다.
      ② 기록에 붙은 제안 문구·도구 실행 줄은 재료에서 뗀다(안내가 아니라 화면 장치).
      ③ 제안은 «쪽지»를 말한 요약 턴에만 붙는다 — 요약만 부탁한 턴에는 안 붙는다(§3).
      ④ 승낙하면 제안한 턴의 본문 **그대로** 보내고 기록에 남긴다. 거절하면 보내지 않는다.
    """
    import tempfile
    from pathlib import Path

    from pension_agent import session_store
    from pension_agent import tools as REG
    from pension_agent.consult_agent import prompts
    from pension_agent.consult_agent.nodes import act
    from pension_agent.consult_agent.nodes import clarify as CL

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = session_store.SESSION_DATA_DIR
        session_store.SESSION_DATA_DIR = Path(tmp)
        try:
            now, old = "s-now", "s-old"
            session_store.append_turn("CM", old, {"role": "user", "text": "지난 세션의 질문"})
            session_store.append_turn("CM", now, {"role": "user", "text": "과세이연 등록은 어떻게 해?"})
            session_store.append_turn("CM", now, {
                "role": "agent",
                "text": "[06-12-501] 후선업무 의뢰등록부터 해요. 60일 내 입금이에요.\n\n"
                        "— 06-12-501 화면 열기, 연계해드릴까요? (네 / 아니오)"})
            session_store.append_turn("CM", now, {"role": "tool", "text": "[발송 화면 연계] 문구"})
            state = {"question": "대화 내용 요약해서 쪽지로 보내줘", "customer_id": "CM",
                     "session_id": now}
            found = tools.run("transcript", state, "이번 상담 요약")
            past = tools.run("history", state, "지난 상담")
            closed = tools.run("transcript", {"question": "q", "session_id": now}, "요약")
            nosess = tools.run("transcript", {"question": "q", "customer_id": "CM"}, "요약")
            empty = tools.run("transcript", {"question": "q", "customer_id": "CM",
                                             "session_id": "s-new"}, "요약")

            summary = "직원이 과세이연 등록 절차를 물었고, [06-12-501] 등록부터 60일 내 입금까지 안내했어요."
            offered = act.offer({**state, "answer": summary, "evidence": [found]})
            pending = offered.get("pending_action")
            plain = act.offer({**state, "question": "지금까지 대화 내용 요약해줘",
                               "answer": summary, "evidence": [found]})
            shut = act.offer({**state, "customer_id": None, "answer": summary, "evidence": [found]})
            history = [{"question": state["question"], "pending_action": pending}]
            yes = act.confirm_action({"question": "응, 보내줘", "history": history, "customer_id": "CM"})
            sent = [t for s in session_store.list_sessions("CM") for t in s["turns"]
                    if any(c.get("name") == "send_memo" for c in (t.get("tool_calls") or []))]
            no = act.confirm_action({"question": "아니 괜찮아", "history": history, "customer_id": "CM"})
            sent_after_no = [t for s in session_store.list_sessions("CM") for t in s["turns"]
                             if any(c.get("name") == "send_memo" for c in (t.get("tool_calls") or []))]
        finally:
            session_store.SESSION_DATA_DIR = orig_dir

    # ① 시점으로 갈린다.
    hit = (bool(found) and "과세이연 등록은 어떻게 해?" in found["text"]
           and "지난 세션의 질문" not in found["text"]
           and found["sources"][0]["id"] == f"session.CM.{now}")
    print(f"{'✓' if hit else '✗'} transcript 는 이번 세션의 대화만 싣는다")
    ok += hit

    hit = bool(past) and "지난 세션의 질문" in past["text"] and "과세이연" not in past["text"]
    print(f"{'✓' if hit else '✗'} history 는 이번 세션을 빼고 싣는다(둘이 겹치지 않는다)")
    ok += hit

    # ② 화면 장치는 재료가 아니다 — 답변 안의 화면번호·기한은 남는다(요약이 옮길 값).
    hit = (bool(found) and "연계해드릴까요" not in found["text"] and "도구실행" not in found["text"]
           and "[06-12-501]" in found["text"] and "60일" in found["text"])
    print(f"{'✓' if hit else '✗'} 제안 문구·도구 실행 줄은 떼고 답변 본문은 그대로 싣는다")
    ok += hit

    hit = closed is None and nosess is None and bool(empty) and tools.TRANSCRIPT_NONE in empty["text"]
    print(f"{'✓' if hit else '✗'} 고객·세션이 없으면 None, 세션은 있는데 0건이면 «기록 없음» 재료")
    ok += hit

    hit = ("transcript" in tools._NEEDS_CUSTOMER and "transcript" in CL._NO_BRANCH
           and "transcript" in prompts.ANSWER_SHAPES
           and "transcript" not in tools.catalog({}) and "transcript" in tools.catalog({"customer_id": "CM"}))
    print(f"{'✓' if hit else '✗'} 고객 전제 도구이고 갈래가 없으며 답의 형태 요구가 등록돼 있다")
    ok += hit

    # ③ 제안 조건 — «쪽지»를 말한 요약 턴에만.
    hit = (bool(pending) and pending["kind"] == "memo" and pending["text"] == summary
           and pending["to"] == REG.MEMO_DEFAULT_TO
           and "쪽지로 보낼까요" in offered["answer"] and offered["answer"].endswith("(네 / 아니오)"))
    print(f"{'✓' if hit else '✗'} 쪽지를 말한 요약 턴에 «쪽지로 보낼까요?»가 붙고 본문은 답변 그대로다")
    ok += hit

    hit = not plain.get("pending_action") and not shut.get("pending_action")
    print(f"{'✓' if hit else '✗'} 요약만 부탁했거나 고객 화면이 닫혀 있으면 제안하지 않는다")
    ok += hit

    # ④ 승낙 → 그대로 보내고 기록. 거절 → 보내지 않는다.
    hit = (yes["pending_action"] is None and "쪽지를 보냈어요" in yes["answer"] and summary in yes["answer"]
           and len(sent) == 1 and sent[0]["tool_calls"][0]["args"]["text"] == summary
           and sent[0]["tool_calls"][0]["args"]["to"] == REG.MEMO_DEFAULT_TO)
    print(f"{'✓' if hit else '✗'} '응, 보내줘' 면 제안한 본문 그대로 보내고 상담이력에 남는다")
    ok += hit

    hit = "취소" in no["answer"] and no["pending_action"] is None and len(sent_after_no) == 1
    print(f"{'✓' if hit else '✗'} '아니' 면 보내지 않는다")
    ok += hit

    hit = "send_memo" in REG.TOOL_REGISTRY and "쪽지로 보내줘" in prompts.ROUTE_PROMPT
    print(f"{'✓' if hit else '✗'} 발송 스텁이 레지스트리에 있고, 쪽지 요청이 lms_link 로 새지 않게 라우팅 기준이 있다")
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


def check_followups() -> int:
    """답변 끝 추천질문 — **재료가 있는 것만 띄운다**가 이 기능의 알맹이다.

    추천질문을 눌렀는데 "근거를 찾지 못했습니다"가 나오면 안 띄우느니만 못하다. 그래서
    suggest 는 후보마다 그 질문에 답할 재료가 실제로 있는지 LLM 없이 먼저 찾아본다.
    없으면 안 뜬다 — 아래 첫 두 검사가 그 계약을 고정한다.

    나머지는 «매 턴 붙지 않는다»(게이트 넷)와 «고객 화면이 닫히면 고객 질문은 없다»,
    그리고 문구가 매번 같지 않다는 것(회전·슬롯)이다.
    """
    from pension_agent.consult_agent import kb as KBMOD
    from pension_agent.consult_agent import suggest
    from pension_agent.consult_agent.nodes import facts_qa
    from pension_agent.strategy_agent.customer import PERSONAS

    def ev(tool: str, title: str | None = None) -> dict:
        return {"tool": tool, "query": "q", "text": "t", "atomic": [], "notices": [],
                "notice_scopes": [], "allow": [], "related": [], "marks": [],
                "sources": ([{"id": "x", "title": title}] if title else []), "meta": {}}

    ok = 0

    # ① 재료가 하나도 없으면 아무것도 띄우지 않는다. 이 검사가 이 기능의 존재 이유다 —
    #    빠지면 "누르면 근거 없음"인 질문이 답변마다 세 줄씩 붙는다.
    orig_retrieve, orig_facts = KBMOD.retrieve, facts_qa.search
    try:
        KBMOD.retrieve = lambda *a, **k: []
        facts_qa.search = lambda q: []
        dead = suggest.followup_questions(
            {"evidence": [ev("fact", "세액공제 한도"), ev("procedure", "계약이전")], "history": []})
    finally:
        KBMOD.retrieve, facts_qa.search = orig_retrieve, orig_facts
    hit = dead == []
    print(f"{'✓' if hit else '✗'} 재료가 없으면 추천질문을 띄우지 않는다({dead})")
    ok += hit

    # ② 한 종류만 재료가 있으면 그 칩만 뜬다 — 있는 것과 없는 것을 실제로 가른다.
    try:
        KBMOD.retrieve = lambda kb, **k: [(1.0, {"id": "c"})] if k.get("kinds") == ["screen"] else []
        facts_qa.search = lambda q: []
        only = suggest.followup_questions({"evidence": [ev("procedure", "계약이전")], "history": []})
    finally:
        KBMOD.retrieve, facts_qa.search = orig_retrieve, orig_facts
    hit = only == ["이 업무는 단말 어느 화면에서 처리해?"]
    print(f"{'✓' if hit else '✗'} 재료가 있는 후보만 남는다(screen 만 열어둠 → {len(only)}건)")
    ok += hit

    # ③ 게이트 넷 — 되묻기·확인대기·LLM실패·근거0건 턴에는 붙지 않는다.
    base = {"evidence": [ev("fact", "세액공제 한도")], "history": []}
    gates = {"되묻기": {**base, "clarify": {"question": "어느 쪽이요?"}},
             "확인대기": {**base, "pending_action": {"label": "화면 열기"}},
             "LLM실패": {**base, "llm_error": "LLMError: down"},
             "근거0건": {**base, "evidence": []}}
    blocked = [name for name, st in gates.items() if suggest.followup_questions(st)]
    hit = not blocked
    print(f"{'✓' if hit else '✗'} 되묻기·확인대기·LLM실패·근거0건 턴에는 붙지 않는다"
          + (f" (샌 것: {blocked})" if blocked else ""))
    ok += hit

    # ④ 고객 화면이 닫혀 있으면 "이 고객 ~" 질문은 성립하지 않는다(§3).
    closed = suggest.followup_questions({"evidence": [ev("customer")], "history": []})
    opened = suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": PERSONAS[0].id})
    hit = closed == [] and opened and all("이 고객" in q for q in opened)
    print(f"{'✓' if hit else '✗'} 고객 화면이 닫히면 고객 질문은 안 뜬다(닫힘 {len(closed)} · 열림 {len(opened)})")
    ok += hit

    # ④-b 안내 콘텐츠 칩은 **이 고객 상태에 걸린 콘텐츠가 실제로 열려 있을 때만** 뜬다.
    #      화면 ⑨ 는 섹션을 비우지 않으려고 관련 없는 콘텐츠도 한 건 세우는데(화면 요건이다),
    #      그 폴백을 «있다»로 세면 칩이 어느 고객에게나 떠서 배경이 된다.
    from pension_agent.strategy_agent import situations as _sit
    from pension_agent.strategy_agent import support as _sup
    matched = [p for p in PERSONAS if _sup.relevant_outreach(_sit.problem_situations(p))]
    unmatched = [p for p in PERSONAS if not _sup.relevant_outreach(_sit.problem_situations(p))]
    _CHIP = "이 고객한테 안내할 만한 세미나나 이벤트 있어?"
    hit = bool(matched) and _CHIP in suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": matched[0].id})
    print(f"{'✓' if hit else '✗'} 걸린 콘텐츠가 있는 고객에게는 안내 콘텐츠 질문이 뜬다"
          + (f" ({matched[0].nm})" if matched else " — 대상 고객 없음"))
    ok += hit

    hit = bool(unmatched) and _CHIP not in suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": unmatched[0].id})
    print(f"{'✓' if hit else '✗'} 걸린 콘텐츠가 없는 고객에게는 안 뜬다"
          + (f" ({unmatched[0].nm})" if unmatched else " — 대조군 없음: 전원이 매칭되면 «조건이 맞을 때만»이 검증되지 않는다"))
    ok += hit

    # 화면을 여는 칩도 같은 판정이다 — 문구 자체가 «지금 이 고객에게 맞는 세미나가 열려
    # 있다»는 알림이라, 조건이 아닌 고객에게 뜨면 알림이 아니라 배경이 된다.
    hit = (bool(matched) and suggest.outreach_chips(matched[0].id)
           and (not unmatched or not suggest.outreach_chips(unmatched[0].id))
           and not suggest.outreach_chips(None))
    print(f"{'✓' if hit else '✗'} 입구 칩도 조건이 맞는 고객에게만 뜬다")
    ok += hit

    # **칩 판정은 LLM 을 부르지 않는다.** 칩 하나 띄우자고 브리핑 한 편(LLM 11회)을
    # 돌리면 답변 지연이 그대로 늘고, LLM 이 죽으면 칩이 통째로 사라진다.
    from pension_agent import llm as _llm
    _orig_gen = _llm.generate
    try:
        _llm.generate = lambda *a, **k: (_ for _ in ()).throw(AssertionError("칩이 LLM 을 불렀다"))
        chips = suggest.outreach_chips(matched[0].id) if matched else []
        follow = suggest.followup_questions(
            {"evidence": [ev("customer")], "history": [],
             "customer_id": matched[0].id if matched else None})
        no_llm = True
    except AssertionError:
        chips, follow, no_llm = [], [], False
    finally:
        _llm.generate = _orig_gen
    hit = no_llm and bool(chips) and bool(follow)
    print(f"{'✓' if hit else '✗'} 안내 콘텐츠 칩 판정에 LLM 을 부르지 않는다")
    ok += hit

    # ⑤ 이번 턴에 이미 쓴 재료로 다시 보내지 않는다 — 방금 답한 것을 또 묻게 된다.
    both = suggest.followup_questions(
        {"evidence": [ev("procedure", "계약이전"), ev("screen", "계약이전")], "history": []})
    hit = not any(q in ("이 업무는 단말 어느 화면에서 처리해?",
                        "이 화면에서 처리하는 절차가 어떻게 돼?") for q in both)
    print(f"{'✓' if hit else '✗'} 이미 쓴 재료로 이끄는 질문은 빠진다({both})")
    ok += hit

    # pitch → pitch(반론 후속)만 예외다. 같은 재료의 **다른 카드**가 답하기 때문이다.
    again = suggest.followup_questions({"evidence": [ev("pitch", "수수료 부담 반론")], "history": []})
    hit = "고객이 그래도 망설이면 뭐라고 답하지?" in again
    print(f"{'✓' if hit else '✗'} 화법의 반론 후속만은 같은 재료로 다시 보낸다({again})")
    ok += hit

    # ⑤-b 같은 도구로 이끄는 칩은 MAX_PER_LEAD 까지만. 하나로 조이면 직원에게는 다른
    #     질문인 것이 «도구가 같다»는 이유로 빠지고, 넘기면 다음 걸음이 한 갈래로 보인다.
    def lead_of(question: str) -> str | None:
        """이 문구가 어느 도구로 이끄는 후보였나 — 상한을 세려면 되짚어야 한다."""
        for rows in suggest._NEXT.values():
            for variants, lead, _probe in rows:
                for text in variants:
                    tail = text.split("}")[-1] if "{topic}" in text else text
                    if question == text or question.endswith(tail):
                        return lead
        return None

    # 재료 확인과 총 상한을 잠시 걷어내고 **도구별 상한만** 잰다 — 안 그러면 총 상한(3)에
    # 먼저 걸려 도구별 상한이 실제로 도는지 알 수 없다.
    orig_has, orig_max = suggest._has_material, suggest.MAX_FOLLOWUPS
    try:
        suggest._has_material = lambda *a, **k: True
        suggest.MAX_FOLLOWUPS = 99
        wide = suggest.followup_questions(
            {"evidence": [ev("fact", "세액공제 한도"), ev("segment", "미운용 현금성자산"),
                          ev("method", "수익률 관리"), ev("fieldtip", "현장 관찰")],
             "history": []})
    finally:
        suggest._has_material, suggest.MAX_FOLLOWUPS = orig_has, orig_max
    to_pitch = [q for q in wide if lead_of(q) == "pitch"]
    hit = len(to_pitch) == suggest.MAX_PER_LEAD
    print(f"{'✓' if hit else '✗'} 같은 도구로 이끄는 칩은 {suggest.MAX_PER_LEAD}개까지만"
          f"(후보 4개 → pitch 행 {len(to_pitch)}개)")
    ok += hit

    # ⑤-c 재료 확인어 — n-gram 유사도는 질의가 길수록 희석돼(kb._sim) 자연스러운 문장이
    #     문턱 아래로 떨어진다. 자기완결형 칩("요즘 시장 상황은 어때?")이 자기 확인어를
    #     갖지 않으면, 시황 카드가 멀쩡히 있는데도 «없다»로 판정돼 안 뜬다.
    selfcontained = [(found, lead, probe) for found, rows in suggest._NEXT.items()
                     for _v, lead, probe in rows if probe]
    market_chip = suggest.followup_questions(
        {"evidence": [ev("lineup", "8월 추천펀드")], "history": []})
    hit = bool(selfcontained) and "요즘 시장 상황은 어때?" in market_chip
    print(f"{'✓' if hit else '✗'} 자기완결형 칩은 확인어로 재료를 찾는다(문장으로는 0건인 자리)")
    ok += hit

    # ⑥ 슬롯 — 근거 카드 제목이 문구에 박힌다. 없으면 슬롯 없는 변형으로 물러선다.
    slotted = suggest.followup_questions({"evidence": [ev("fact", "세액공제 한도")], "history": []})
    hit = bool(slotted) and "「세액공제 한도」" in slotted[0]
    print(f"{'✓' if hit else '✗'} 근거 카드 제목이 추천질문에 실린다")
    ok += hit
    # 슬롯을 못 채우면 그 변형은 건너뛴다 — 빈칸으로 두면 「「」 이 내용」 이 나간다.
    long_title = "가" * (suggest.TOPIC_MAX + 1)
    hit = suggest._topic({"sources": [{"title": long_title}]}) == "" \
        and suggest._phrase(("「{topic}」 앞", "뒤"), "", 0) == "뒤" \
        and suggest._phrase(("「{topic}」 앞", "뒤"), "제목", 0) == "「제목」 앞"
    print(f"{'✓' if hit else '✗'} 제목이 길거나 없으면 슬롯 없는 변형으로 물러선다")
    ok += hit

    # ⑦ 회전 — 대화가 이어지면 같은 재료에서도 문구가 바뀐다(매번 같으면 안 읽힌다).
    turns = {suggest._phrase(("A{topic}", "B", "C"), "T", n) for n in range(3)}
    hit = len(turns) == 3
    print(f"{'✓' if hit else '✗'} 대화 턴에 따라 문구 변형이 회전한다({sorted(turns)})")
    ok += hit

    # ⑧ 직원이 이미 물어본 질문을 다시 제안하지 않는다 — **이번 질문 포함**이 핵심이다.
    #    history 는 이 턴에 들어온 이력이라 이번 질문이 없다(ask 가 invoke 뒤에 붙인다).
    #    그래서 이 필터가 이전 턴만 보면 방금 물은 것이 그대로 추천으로 되돌아온다
    #    — 「이 고객 왜 관리 대상이야?」 를 묻고 답을 읽었는데 맨 아래 같은 질문이 다시
    #    서 있던 자리다. 「이미 쓴 재료」 제외는 이걸 못 잡는다: 그 답은 customer 재료로
    #    나왔는데 그 질문은 segment 로 이끄는 후보라 재료 축이 겹치지 않는다.
    asked = suggest.followup_questions(
        {"evidence": [ev("fact", "세액공제 한도")], "history": [], "customer_id": None})
    repeat = suggest.followup_questions(
        {"evidence": [ev("fact", "세액공제 한도")], "history": [{"question": asked[0]}]})
    hit = asked[0] not in repeat
    print(f"{'✓' if hit else '✗'} 직원이 이미 물은 질문은 다시 제안하지 않는다")
    ok += hit

    same = suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": PERSONAS[0].id,
         "question": "이 고객 왜 관리 대상이야?"})
    hit = "이 고객 왜 관리 대상이야?" not in same
    print(f"{'✓' if hit else '✗'} 방금 물은 질문이 추천으로 되돌아오지 않는다({same})")
    ok += hit

    # 표기 차이(공백·물음표) 하나로 같은 질문이 다시 서면 안 된다.
    loose = suggest.followup_questions(
        {"evidence": [ev("customer")], "history": [], "customer_id": PERSONAS[0].id,
         "question": "이 고객 왜 관리대상이야"})
    hit = "이 고객 왜 관리 대상이야?" not in loose
    print(f"{'✓' if hit else '✗'} 공백·물음표만 다른 같은 질문도 다시 제안하지 않는다")
    ok += hit

    # ⑨ ask() 배선 — 답변 끝에 머리말과 함께 붙고, 반환에 followups 가 따로 실린다.
    #    상담이력에는 **붙이기 전 원 답변**이 남는다(history 도구가 재료로 되읽는 텍스트다).
    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "답변 본문", "sources": [],
            "evidence": [ev("fact", "세액공제 한도")],
            "customer_id": st.get("customer_id"), "history": st.get("history") or []})})()
        wired = G.ask("세액공제 한도 얼마야?")
    finally:
        G._AGENT = orig_agent
    hit = wired["followups"] and G.FOLLOWUP_HEADER in wired["answer"] \
        and wired["answer"].startswith("답변 본문") \
        and all(q in wired["answer"] for q in wired["followups"])
    print(f"{'✓' if hit else '✗'} ask() 가 답변 끝에 추천질문을 붙이고 followups 로도 준다")
    ok += hit

    orig_agent = G._AGENT
    try:
        G._AGENT = type("Fake", (), {"invoke": staticmethod(lambda st: {
            "answer": "어느 쪽인가요?", "sources": [], "clarify": {"question": "어느 쪽?"},
            "evidence": [ev("fact", "세액공제 한도")]})})()
        asking = G.ask("실물이전 어떻게 해?")
    finally:
        G._AGENT = orig_agent
    hit = asking["followups"] == [] and G.FOLLOWUP_HEADER not in asking["answer"]
    print(f"{'✓' if hit else '✗'} 되묻기 턴의 답변에는 추천질문 블록이 붙지 않는다")
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

    # ③ L0 카탈로그가 작게 유지된다. **카드가 늘어도** 여기가 커지면 안 된다 — 그게 계층의
    #    목적이고, 카드 수는 버킷 줄의 "(N장)" 한 자리만 움직인다.
    #
    #    한도를 2000 → 2200 으로 올린 것은 카드가 아니라 **종류가 하나 늘어서**다(9종 → 10종.
    #    팩트가 색인 밖에 있다가 들어왔다). 종류 하나는 머리말 한 줄 + 버킷 줄 몇 개라
    #    약 160자를 쓴다. 카드가 늘어 넘치면 그건 이 테스트가 잡아야 하는 회귀가 맞고,
    #    종류가 늘어 넘치면 여기를 함께 고치는 것이 맞다 — 둘을 구분해 두려고 적는다.
    cat = K.index_catalog(kb)
    hit = len(cat) <= 2200
    print(f"{'✓' if hit else '✗'} L0 카탈로그 {len(cat)}자 ≤ 2200 (종류 {len(K._KIND_ORDER)})")
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
    tools.retrieve, tools.fits_question = spy_retrieve, lambda q, h, kind="", history=None, query=None: h
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
        tools.fits_question = lambda q, h, kind="", history=None, query=None: []
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h

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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
        tools.fits_question = lambda question, h, kind="", history=None, query=None: h
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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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


def check_compose_retry() -> int:
    """점검에 걸린 생성문을 **한 번 다시 쓰게** 한다 (§6 — 처분은 구현이 정한다).

    회귀 대상: 처분이 «근거 원문 덤프» 하나뿐이던 동안, 걸린 자리가 한 문장이어도 답이
    통째로 버려지고 화면에는 카드 원문이 답변처럼 떨어졌다(■ 제목 줄 · 「· 기준시점 … ·
    출처 …」 메타 줄). 직원 쪽에서는 에이전트가 갑자기 다른 말투로 말하는 것으로 보인다.

    걸린 자리를 재작성 프롬프트에 실어야 같은 문장이 다시 나오지 않는다 — 「다시 쓰세요」
    만으로는 처분이 한 바퀴 늘 뿐이다.
    """
    from pension_agent.consult_agent.nodes import plan

    ok = 0
    evidence = [{"tool": "fact", "query": "한도", "text": "■ 세액공제 한도\n\n한도는 900만원이다.",
                 "atomic": [], "notices": [], "notice_scopes": [],
                 "allow": ["한도는 900만원이다."], "sources": [{"id": "f1"}],
                 "related": [], "marks": [], "meta": {}}]
    state = {"question": "한도가 얼마야?", "evidence": evidence}

    # ① 첫 생성문이 원장 밖 수치를 말하면, 두 번째 시도의 결과가 답이 된다.
    seen: list[str] = []

    def twice(prompt, **kw):
        seen.append(prompt)
        return ("한도는 1,234만원이에요." if len(seen) == 1 else "한도는 900만원이에요.")

    orig = plan.generate
    plan.generate = twice
    try:
        answer = plan.compose(dict(state))["answer"]
    finally:
        plan.generate = orig
    hit = len(seen) == 2 and answer.startswith("한도는 900만원이에요")
    print(f"{'✓' if hit else '✗'} 걸린 생성문을 한 번 다시 쓰고, 통과하면 그것이 답이다")
    ok += hit

    # ② 재작성 프롬프트가 **무엇이 걸렸는지**를 싣는다. 안 실으면 같은 문장이 다시 나온다.
    hit = len(seen) == 2 and "1,234" in seen[1] and "다시 쓴다" in seen[1]
    print(f"{'✓' if hit else '✗'} 재작성 프롬프트에 걸린 자리가 실린다")
    ok += hit

    # ③ 두 번째도 걸리면 예전 그대로 근거 원문이 답이다 — 틀린 문장이 나가는 선택지는 없다.
    #    무한히 다시 쓰지 않는다는 것도 여기서 잰다(상한은 코드가 쥔다).
    tries: list[str] = []

    def always_bad(prompt, **kw):
        tries.append(prompt)
        return "한도는 1,234만원이에요."

    plan.generate = always_bad
    try:
        answer = plan.compose(dict(state))["answer"]
    finally:
        plan.generate = orig
    hit = (len(tries) == plan.COMPOSE_RETRIES + 1
           and answer.startswith(evidence[0]["text"]) and "1,234" not in answer)
    print(f"{'✓' if hit else '✗'} 계속 걸리면 상한에서 멈추고 근거 원문이 답이다({len(tries)}회)")
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

    # 출처 터미널 표기는 공용 함수 하나다(tools.source_lines). 운영 CLI 와 디버그 실행기가
    # 표기를 각자 복사해 갖고 있던 동안, URL 을 싣는 변경이 운영 CLI 에만 적용되고 디버그
    # 화면($CAD·$CADR)에는 빠졌다 — 한쪽만 고쳐지는 사고의 재발을 여기서 막는다.
    s_full = {"id": "x.1", "title": "제목", "doc": "문서", "score": 1.0, "url": "https://u"}
    s_bare = {"id": "x.2", "title": "제목", "doc": "문서"}   # 검색으로 오지 않은 재료
    full, bare = tools.source_lines(s_full), tools.source_lines(s_bare)
    compact = tools.source_lines(s_full, compact=True)
    hit = (full[-1] == "     ↗ https://u" and "관련도 1.0" in full[1]
           and "관련도" not in "".join(bare) and "↗" not in "".join(bare)
           and len(compact) == 2 and compact[0].startswith("   · 문서 — 제목 [x.1]"))
    print(f"{'✓' if hit else '✗'} source_lines — URL·관련도는 있을 때만, compact 는 한 줄")
    ok += hit

    # 세 진입점이 전부 그 함수를 부르는가. 운영 CLI 는 모듈 최상위에서 REPL 이 돌아
    # **임포트하면 안 되므로**(스크립트다) 파일 텍스트로 확인한다.
    import inspect

    from tests.debug import __main__ as dbg_main
    from tests.debug import reps as dbg_reps
    ops_src = pathlib.Path(tools.__file__).with_name("__main__.py").read_text(encoding="utf-8")
    hit = ("source_lines" in ops_src
           and "source_lines" in inspect.getsource(dbg_main._print_source)
           and "source_lines" in inspect.getsource(dbg_reps._print_source_line))
    print(f"{'✓' if hit else '✗'} 운영 CLI·$CAD·$CADR 이 같은 출처 표기 함수를 쓴다")
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


def check_architecture_doc() -> int:
    """README 의 아키텍처 다이어그램은 생성물이다 — 코드(그래프 노드·도구·게이트)와
    어긋난 채 남으면 손그림 시절의 사고(lms_send 개명 뒤에도 옛 이름이 그려져 있던 것)가
    재발한다. 재생성 결과와 README 의 마커 구간이 같은지 대조한다."""
    from scripts import render_architecture as RA

    ok = 0
    text = RA.README.read_text(encoding="utf-8")
    hit = RA.MARK_START in text and RA.MARK_END in text
    print(f"{'✓' if hit else '✗'} README 에 생성 구간 마커가 있다")
    ok += hit

    block = text.partition(RA.MARK_START)[2].partition(RA.MARK_END)[0] if hit else ""
    hit = hit and (RA.MARK_START + block + RA.MARK_END) == RA.render_block()
    print(f"{'✓' if hit else '✗'} 다이어그램이 코드와 일치한다"
          + ("" if hit else " — python -m scripts.render_architecture 로 갱신"))
    ok += hit
    return ok


def check_node_label_collision() -> int:
    """그래프 노드 라벨이 상태 키와 겹치지 않는가.

    행내 환경의 langgraph(구버전)는 add_node 에서 라벨이 상태 키와 같으면 거부한다
    ("'answer' is already being used as a state key"). 개발 환경(1.x)은 그 검사가 없어
    여기서만 통과하고 행내에서 임포트가 죽었다 — answer 노드를 compose 로 개명한
    이유다. 같은 충돌이 다시 들어오면 행내에 가서야 터지므로 여기서 잡는다."""
    import typing

    from pension_agent.consult_agent.state import AgentState
    nodes = set(G.build_agent().get_graph().nodes) - {"__start__", "__end__"}
    overlap = nodes & set(typing.get_type_hints(AgentState))
    hit = not overlap
    print(f"{'✓' if hit else '✗'} 노드 라벨이 상태 키와 겹치지 않는다 (구버전 langgraph 호환)"
          + ("" if hit else f" — {sorted(overlap)}"))
    return hit


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
    tools.fits_question = lambda q, h, kind="", history=None, query=None: h
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
        check_lms_link_parsing()
        check_knowledge_intents()
        check_screen_link()
        check_briefing_shared()
        check_customer_material()
        check_playbook_material()
        check_context_and_clarify()
        check_adequacy_and_shape()
        check_material_marks()
        check_relations()
        check_turn_cost()
        check_miss_recovery()
        check_clarify_golden()
        check_answer_parallel()
        check_replan_on_empty()
        check_outreach()
        check_prompt_is_quotable()
        check_branch_answer_amount()
        check_screen_registry()
        check_market_material()
        check_product_advice()
        check_caution_roles()
        check_question_echo()
        check_table_row_names()
        check_no_repeat()
        check_suitable_shape()
        check_history_material()
        check_memo()
        check_today_material()
        check_account_state()
        check_labeled_pairs()
        check_tax_credit_calc()
        check_fact_in_index()
        check_history_selection()
        check_followups()
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
        check_compose_retry()
        check_notice_scope()
        check_guard()
        check_architecture_doc()
        check_node_label_collision()
    finally:
        # 위 테스트들(특히 lms_link)이 상담이력 저장소에 기록을 남기므로 **이번 실행이 만든
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
