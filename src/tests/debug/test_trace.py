"""트레이스 회귀 검사 — 계측이 실제 동작을 보고 있는지, 그리고 계측이 새지 않는지.

키 없이 돈다(캔드 시나리오만 쓴다).

    cd src
    python -m tests.debug.test_trace

이 스위트가 고정하는 것은 **지금 이 진단**이다. "세액공제 한도가 얼마야?" 가 카드 원문으로
답해진 것은 `relations` 가 오기를 오기라고 짚은 문장까지 '알려진 오답'으로 보고 생성문을
버렸기 때문이고, 그 사실이 여기 박제된다. 나중에 `relations.known_wrong()` 에 극성 판정이
들어가면 검사 2·3 이 빨개진다 — 그때 빨개지는 것이 맞다(고쳐진 것이다).
"""

from __future__ import annotations

import sys
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import verify as V
from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import relations as REL
from pension_agent.consult_agent import tools as T
from pension_agent.consult_agent.nodes import plan as P
from tests.debug import script, trace as TR
from tests.debug.runner import run

#: 계측 전 원본. 검사 8(복원)이 이것과 대조한다 — import 시점에 잡아둬야 의미가 있다.
_ORIGINALS = {"compose": G.compose, "verify_texts": P.verify_texts,
              "relations": P.relations, "span": P._span_verdict,
              "fits_question": T.fits_question}

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


def _run(name: str):
    scn = script.SCENARIOS[name]
    results, tr = run([scn.question], scenario=scn)
    return results[0], tr


# ─────────────────────────────────────────────────────────────
# 1. 노드 순서 — 어느 노드를 거쳐 답이 나왔나
# ─────────────────────────────────────────────────────────────
# clarify 가 사이에 있는 것이 정상이다. 계획 루프가 끝나면 되물을지 한 번 판정하고
# (routing.route_plan → clarify), 되묻지 않기로 하면 compose 로 간다(route_clarify).

r, tr = _run("tax_credit_known_wrong")
check(tr.node_names() == ["understand", "plan_step", "clarify", "compose", "offer"],
      "노드 순서: understand → plan → clarify → compose → offer", str(tr.node_names()))
check((tr.node("plan_step") or types.SimpleNamespace(note="")).note.startswith(
          "fact:세액공제 한도 → 채택 fact.k04.f2"),
      "계획 단계가 부른 도구·질의·채택 카드가 보인다",
      (tr.node("plan_step") or types.SimpleNamespace(note="?")).note)


# ─────────────────────────────────────────────────────────────
# 2·3. 알려진 오답 — 어느 게이트가 걸었고, 그래서 무엇이 화면에 나갔나
# ─────────────────────────────────────────────────────────────

gates = tr.gates()
check(gates.get("verify_texts") is not None and gates["verify_texts"].passed,
      "known_wrong: 수치 집합 검사는 통과한다(원장 안의 숫자만 썼다)")
check(tr.blocked_by() == "relations" and "5,500만원 이상 13.2%" in gates["relations"].detail,
      "known_wrong: relations 가 '알려진 오답' 으로 생성문을 버린다",
      str(gates.get("relations")))
check("span" not in gates,
      "known_wrong: 앞에서 끊겨 원문 스팬 검사는 실행조차 되지 않는다", str(sorted(gates)))

draft = tr.draft()
check(r["answer"].startswith("■") and draft and draft not in r["answer"],
      "known_wrong: 화면에 나간 것은 생성문이 아니라 근거 원문이다(말투가 달라지는 자리)",
      r["answer"][:40])
check((tr.node("compose") or types.SimpleNamespace(note="")).note.startswith("폴백"),
      "known_wrong: 처분이 '폴백' 으로 기록된다")


# ─────────────────────────────────────────────────────────────
# 4. 통과 경로 — 한 문장 차이로 갈린다
# ─────────────────────────────────────────────────────────────

r2, tr2 = _run("tax_credit_clean")
gates2 = tr2.gates()
check(tr2.blocked_by() is None and set(gates2) == {"verify_texts", "relations", "span"},
      "clean: 게이트 3종이 전부 실행되고 전부 통과한다", str(sorted(gates2)))
check(r2["answer"].startswith(tr2.draft()) and not r2["answer"].startswith("■"),
      "clean: 생성문이 그대로 답변이 된다(해요체 유지)", r2["answer"][:40])


# ─────────────────────────────────────────────────────────────
# 5. 다른 게이트가 걸리면 트레이스도 다르게 말한다
# ─────────────────────────────────────────────────────────────

r3, tr3 = _run("out_of_ledger")
gates3 = tr3.gates()
check(tr3.blocked_by() == "verify_texts" and "relations" not in gates3,
      "out_of_ledger: 원장 밖 수치에서 끊기고 relations 는 실행되지 않는다",
      str(sorted(gates3)))
check(r3["answer"].startswith("■"), "out_of_ledger: 역시 근거 원문 폴백", r3["answer"][:30])


# ─────────────────────────────────────────────────────────────
# 6. LLM 이 죽은 턴은 폴백이 아니다 (§11)
# ─────────────────────────────────────────────────────────────

r4, tr4 = _run("llm_dead")
check(r4["answer"].startswith("지금은 답변을 만들 수 없어요") and not r4["answer"].startswith("■"),
      "llm_dead: 근거 원문을 대신 내보내지 않고 실패를 실패라고 답한다", r4["answer"][:40])
check(tr4.gates() == {}, "llm_dead: 게이트는 하나도 실행되지 않는다", str(tr4.gates()))


# ─────────────────────────────────────────────────────────────
# 7. 계측이 새지 않는다 — 이 스위트가 다른 스위트를 오염시키면 안 된다
# ─────────────────────────────────────────────────────────────

check(G.compose is _ORIGINALS["compose"], "복원: graph.compose 가 원본으로 돌아왔다")
check(P.verify_texts is _ORIGINALS["verify_texts"] is V.verify_texts,
      "복원: plan.verify_texts 가 원본 함수다")
check(P.relations is _ORIGINALS["relations"] is REL,
      "복원: plan.relations 가 진짜 모듈이다(SimpleNamespace 가 남지 않았다)")
check(P._span_verdict is _ORIGINALS["span"], "복원: plan._span_verdict 가 원본이다")
check(T.fits_question is _ORIGINALS["fits_question"],
      "복원: 시나리오 스텁(tools.fits_question)도 원본으로 돌아왔다")
check(G._AGENT is None,
      "복원: 계측된 노드가 박힌 컴파일 그래프를 남기지 않는다", str(G._AGENT))


# ─────────────────────────────────────────────────────────────
# 8. 계측 대상이 사라지면 조용히 덜 보지 않고 크게 실패한다
# ─────────────────────────────────────────────────────────────
# 이 검사가 없으면, 운영 쪽에서 `_span_verdict` 를 인라인한 날 트레이스는 "게이트 통과"를
# 찍는다 — 실은 게이트를 못 본 것인데. 진단 도구가 조용히 틀리는 것이 가장 나쁘다.

_saved_span = P._span_verdict
del P._span_verdict
try:
    with TR.instrument(TR.Trace()):
        check(False, "계측 대상이 없으면 예외를 던진다", "예외 없이 계측이 시작됐다")
except AttributeError as exc:
    check("_span_verdict" in str(exc), "계측 대상이 없으면 예외를 던진다(덜 보지 않는다)", str(exc))
finally:
    P._span_verdict = _saved_span


# ─────────────────────────────────────────────────────────────

failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    print(("✓" if ok else "✗") + f" {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 트레이스 회귀 발생")
    raise SystemExit(1)
print("✅ 트레이스 회귀 검사 통과")
