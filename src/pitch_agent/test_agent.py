"""검색 정확도 · 그래프 라우팅 테스트.

LLM 노드(understand, respond)를 스텁으로 갈아끼우므로 API 키가 없어도 돌아간다.
새 화법 카드를 추가할 때마다 CASES 에 한 줄씩 넣으면,
카드가 늘어나 검색이 엉키는 것을 회귀 테스트로 잡을 수 있다.

실행:  python test_agent.py
"""

from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")  # 클라이언트 초기화만 통과시킨다

import graph as G  # noqa: E402
import nodes  # noqa: E402

# 거절유형이 null 로 나와(=미탐지) 1·2차 재검색까지 0건인 애매한 질문. broaden 이
# 채택 기준(kb.MIN_TOPICAL)은 건드리지 않으므로 여전히 FALLBACK 이 맞다 — 실수로
# 문턱을 낮춰 무관한 질문까지 통과시키지 않는지 잡아주는 회귀 테스트다.
_AMBIGUOUS_Q = "타사에서 이미 운용중이라고 하시는데 관리가 안되고 있다고 하시네요"

# 챕터가 늘어나며 생긴 스코프 문제(거절유형 라벨이 챕터를 넘어 충돌, guide 인텐트)를
# 검증하는 케이스. 일반 키워드 휴리스틱(_OBJ_HINTS)으로는 챕터를 구분할 수 없어서
# 슬롯을 직접 지정한다. 질문 문구는 해당 카드의 실제 trigger_examples 그대로 — kb.py
# 점수 계산이 진짜로 동작하는지 보려는 것이라 임의로 바꾸면 안 된다.
_OVERRIDES: dict[str, dict] = {
    # ch02(퇴직금)·ch03(계약이전)이 똑같이 "수수료 비교" 라벨을 쓰는데, stage 로 후보를
    # 먼저 좁히면 서로 안 섞이는지 확인한다.
    "증권사는 수수료 무료라는데요": {"stage": "퇴직금", "objection_type": "수수료 비교"},
    "근데 수수료가 비싸잖아요": {"stage": "계약이전", "objection_type": "수수료 비교"},
    "1억원 정도 쓸 곳이 있어서 그냥 해지하려고요": {"stage": "퇴직금", "objection_type": "해지 의향"},
    # guide 인텐트 — 고객 응대가 아니라 직원의 업무 절차 질문
    "이탈 위험이 높은 고객을 어떻게 골라내나요": {"stage": "이탈방어", "intent": "guide"},
}

# (질문, 기대하는 1순위 카드 / FALLBACK / CAPABILITY)
CASES = [
    ("사업자 고객인데 수수료 부담된다고 하시네요", "ch01_new.p02"),
    ("고객이 주식이 더 나을 것 같다는데 어떻게 말하지?", "ch01_new.p04"),
    ("이미 증권사에 IRP 만들어놨다고 하시는데요", "ch01_new.p06"),
    ("직장인 고객한테 처음 IRP 꺼낼 때 뭐라고 시작하지?", "ch01_new.p01"),
    ("나중에 돈 필요하면 못 빼는거 아니냐고 하세요", "ch01_new.p05"),
    ("매달 넣을 여유가 없다고 하시네요", "ch01_new.p03"),
    ("적금이랑 뭐가 다르냐고 물어보세요", "ch01_new.p01"),
    ("주택청약 금리 어떻게 되나요?", "FALLBACK"),
    ("오늘 점심 뭐 먹지", "FALLBACK"),
    (_AMBIGUOUS_Q, "FALLBACK"),
    ("네가 답변할 수 있는 화법은 뭐가 있어?", "CAPABILITY"),
    ("증권사는 수수료 무료라는데요", "ch02_retirement.p05"),
    ("근데 수수료가 비싸잖아요", "ch03_transfer.p05"),
    ("1억원 정도 쓸 곳이 있어서 그냥 해지하려고요", "ch02_retirement.p03"),
    ("이탈 위험이 높은 고객을 어떻게 골라내나요", "guide01_yield_mgmt.p01"),
]

# understand 가 실제로 뽑아낼 법한 슬롯을 규칙으로 흉내낸다
_OBJ_HINTS = [
    ("주식", "수익률 우선"), ("수익률", "수익률 우선"),
    ("여유", "납입여력 부족"), ("돈도 없", "납입여력 부족"),
    ("못 빼", "유동성 우려"), ("못빼", "유동성 우려"),
    ("증권사", "타사 보유"), ("다른 금융", "타사 보유"), ("이미", "타사 보유"),
]

_CAPABILITY_HINTS = ("화법이 뭐가 있", "화법은 뭐가 있", "도와줄", "어떤 상황을 도와", "뭘 할 수 있")


def stub_understand(state):
    q = state["question"]
    if q in _OVERRIDES:
        override = _OVERRIDES[q]
        return {
            "intent": override.get("intent", "situation"),
            "customer_type": override.get("customer_type"),
            "objection_type": override.get("objection_type"),
            "stage": override.get("stage"),
            "utterance": q,
            "broaden_count": 0,
        }
    if q == _AMBIGUOUS_Q:
        objection_type = None  # 미탐지 재현 — "이미"가 있어도 일부러 못 잡은 것으로 취급
    else:
        objection_type = next((v for k, v in _OBJ_HINTS if k in q), None)
    return {
        "intent": "capability" if any(k in q for k in _CAPABILITY_HINTS) else "situation",
        "customer_type": "사업자" if "사업자" in q else ("직장인" if "직장인" in q else None),
        "objection_type": objection_type,
        "stage": "신규",
        "utterance": q,
        "broaden_count": 0,
    }


def stub_respond(state):
    return {
        "answer": "(LLM 생성 화법 — 스텁)",
        "sources": [{"id": p["id"], "score": round(s, 2)} for s, p in state["hits"]],
    }


def stub_verify_yes(state):
    """검색 로직 회귀 테스트에서는 게이트를 항상 통과시켜 retrieve 결과만 본다."""
    return {"verified": True}


def check_broaden_stages() -> bool:
    """nodes.broaden() 이 1차엔 고객유형·단계만, 2차엔 거절유형까지 지우는지 직접 검증한다."""
    state = {"customer_type": "사업자", "objection_type": "수익률 우선", "stage": "신규", "broaden_count": 0}

    stage1 = nodes.broaden(state)
    ok1 = stage1["broaden_count"] == 1 and stage1["customer_type"] is None and stage1["stage"] is None
    ok1 = ok1 and "objection_type" not in stage1  # 1차는 거절유형을 건드리지 않는다

    stage2 = nodes.broaden({**state, **stage1})
    ok2 = stage2["broaden_count"] == 2 and stage2["objection_type"] is None

    ok = ok1 and ok2
    print(f"{'✓' if ok else '✗'} broaden() 1→2단계 필드 완화 동작")
    return ok


def check_verify_gate() -> bool:
    """verify 가 NO 를 내면(의도 불일치) 카드가 검색됐어도 fallback 으로 가는지 검증한다.
    검색은 성공하지만 게이트가 거부하는 상황을 스텁으로 재현한다."""
    G.understand = stub_understand
    G.respond = stub_respond
    G.verify = lambda state: {"verified": False}
    agent = G.build_agent()

    out = agent.invoke({"question": "사업자 고객인데 수수료 부담된다고 하시네요"})
    ok = not out.get("sources") and "화법이 없습니다" in out.get("answer", "")

    G.verify = stub_verify_yes  # 다른 테스트에 영향 없도록 원복
    print(f"{'✓' if ok else '✗'} verify 게이트 NO → fallback")
    return ok


def main() -> int:
    # build_agent() 는 호출 시점에 모듈 전역에서 노드 함수를 찾으므로 치환이 그대로 먹는다
    G.understand = stub_understand
    G.respond = stub_respond
    G.verify = stub_verify_yes
    agent = G.build_agent()

    passed = 0
    for question, expected in CASES:
        out = agent.invoke({"question": question})
        if expected == "CAPABILITY":
            ok = out.get("intent") == "capability" and bool(out.get("answer")) and not out.get("sources")
            found = "capabilities 노드 응답" if ok else f"intent={out.get('intent')!r}"
        else:
            top = out["sources"][0]["id"] if out["sources"] else "FALLBACK"
            ok = top == expected
            found = ", ".join(f"{s['id'].split('.')[-1]}({s['score']})" for s in out["sources"]) or "→ FALLBACK"
        passed += ok
        print(f"{'✓' if ok else '✗'} {question[:32]:<34} {found}")

    passed += check_broaden_stages()
    passed += check_verify_gate()
    total = len(CASES) + 2
    print(f"\n{passed}/{total} 통과")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
