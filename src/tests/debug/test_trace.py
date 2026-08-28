"""트레이스 회귀 검사 — 계측이 실제 동작을 보고 있는지, 그리고 계측이 새지 않는지.

키 없이 돈다(캔드 시나리오만 쓴다).

    cd src
    python -m tests.debug.test_trace

이 스위트가 고정하는 것은 **어느 게이트가 무엇을 걸고 무엇을 통과시키는가**다. "세액공제
한도가 얼마야?" 가 카드 원문(문어체)으로 답해지던 사고 둘이 여기서 시작했고, 고친 뒤에는
같은 시나리오가 반대 방향을 고정한다:

  · 표기 — "148만 5천원" 이 원장 밖 수치로 걸리던 것 → `verify._measures` (korean_units)
  · 극성 — 오기를 «틀렸다»고 짚는 문장까지 걸리던 것 → `relations._corrects`
           (tax_credit_correction ↔ tax_credit_asserts_wrong)

둘 다 **넓히기만 하고 좁히지 않았다**는 것을 짝 시나리오가 함께 잰다 — 값이 틀리면
(korean_units_wrong), 문구를 주장하면(asserts_wrong) 여전히 폐기된다.
"""

from __future__ import annotations

import contextlib
import io
import sys
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import session_store, verify as V
from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import relations as REL
from pension_agent.consult_agent import tools as T
from pension_agent.consult_agent.nodes import plan as P
from tests.debug import script, trace as TR
from tests.debug.__main__ import main
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


def _evidence_texts(name: str) -> list[str]:
    """그 시나리오가 모으는 근거 원문. 원장에 무엇이 들어 있는지 대조할 때 쓴다."""
    scn = script.SCENARIOS[name]
    with script.installed(scn):
        found = T.run("fact", {"question": scn.question}, "세액공제 한도")
    return [found["text"]] if found else []


# ─────────────────────────────────────────────────────────────
# 1. 노드 순서 — 어느 노드를 거쳐 답이 나왔나
# ─────────────────────────────────────────────────────────────
# clarify 가 사이에 있는 것이 정상이다. 계획 루프가 끝나면 되물을지 한 번 판정하고
# (routing.route_plan → clarify), 되묻지 않기로 하면 compose 로 간다(route_clarify).

r, tr = _run("tax_credit_asserts_wrong")
check(tr.node_names() == ["understand", "plan_step", "clarify", "compose", "offer"],
      "노드 순서: understand → plan → clarify → compose → offer", str(tr.node_names()))
check((tr.node("plan_step") or types.SimpleNamespace(note="")).note.startswith(
          "fact:세액공제 한도 → 채택 fact.k04.f2"),
      "계획 단계가 부른 도구·질의·채택 카드가 보인다",
      (tr.node("plan_step") or types.SimpleNamespace(note="?")).note)


# ─────────────────────────────────────────────────────────────
# 2·3. 알려진 오답 — 주장은 잡고 정정은 통과시킨다
# ─────────────────────────────────────────────────────────────
# 같은 문구가 정반대 뜻으로 쓰인다. 문자열 포함만 보던 동안 «"…이상 13.2%"는 오기예요»
# 까지 잡혀 답변이 통째로 버려졌다 — 카드의 verify_points 가 직원에게 그렇게 짚어주라고
# 적어둔 문구인데도. 이제 인용부호 + 정정 표지를 보고 가른다.

gates = tr.gates()
check(gates.get("verify_texts") is not None and gates["verify_texts"].passed,
      "asserts_wrong: 수치 집합 검사는 통과한다(원장 안의 숫자만 썼다)")
check(tr.blocked_by() == "relations" and "5,500만원 이상 13.2%" in gates["relations"].detail,
      "asserts_wrong: 오답을 주장하면 relations 가 생성문을 버린다",
      str(gates.get("relations")))
check("span" not in gates,
      "asserts_wrong: 앞에서 끊겨 원문 스팬 검사는 실행조차 되지 않는다", str(sorted(gates)))

draft = tr.draft()
check(r["answer"].startswith("■") and draft and draft not in r["answer"],
      "asserts_wrong: 화면에 나간 것은 생성문이 아니라 근거 원문이다(말투가 달라지는 자리)",
      r["answer"][:40])
check((tr.node("compose") or types.SimpleNamespace(note="")).note.startswith("폴백"),
      "asserts_wrong: 처분이 '폴백' 으로 기록된다")

rc, trc = _run("tax_credit_correction")
check(trc.blocked_by() is None and not rc["answer"].startswith("■"),
      "correction: 오기를 «틀렸다»고 짚는 문장은 더는 폐기되지 않는다",
      str(trc.gates().get("relations")))
check("오기니까" in rc["answer"] and '"초과"' in rc["answer"],
      "correction: 그 코칭이 화면까지 간다", rc["answer"][-40:])


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
# 5-2. 실제 실행에서 걸린 자리 — 값은 맞는데 표기가 달라서 버려진다
# ─────────────────────────────────────────────────────────────
# 실제 LLM 이 쓴 문장은 "148만 5천원"·"2026년 6월" 이었고, 원장은 "1,485,000원"·
# "148.5만원"·"2026.06" 이다. 같은 값인데 숫자 토큰 집합이 달라 폐기됐다 — 지어낸 수치를
# 막은 것이 아니라 맞는 답변을 표기 때문에 버린 것이다. verify._measures() 가 덩이로
# 읽게 되면서 통과한다. **표기는 통과시키되 값은 여전히 따진다**는 것까지 함께 고정한다.

r5, tr5 = _run("korean_units")
check(tr5.blocked_by() is None and not r5["answer"].startswith("■"),
      "korean_units: 만·천으로 끊어 쓴 금액이 더는 '원장 밖 수치' 로 걸리지 않는다",
      str(tr5.gates().get("verify_texts")))
check("148만 5천원" in r5["answer"] and "2026년 6월" in r5["answer"],
      "korean_units: 직원이 말하는 표기 그대로 화면에 나간다", r5["answer"][:40])

r6, tr6 = _run("korean_units_wrong")
check(tr6.blocked_by() == "verify_texts" and r6["answer"].startswith("■"),
      "korean_units_wrong: 같은 표기라도 값이 틀리면 여전히 폐기된다",
      str(tr6.gates().get("verify_texts")))

# 버려진 값이 실제로는 원장 안에 있다 — 표기만 달랐다는 것을 못박는다.
#
# 연월은 **날짜 정규형**으로 찾는다. 원장은 "2026.06" 이라 적지만, 날짜를 통짜로 대조하게
# 되면서(verify._CHUNK) 그 표기는 맨 토큰 "2026.06" 이 아니라 `날짜:2026-06` 으로 나온다 —
# 여기서 옛 표기를 찾으면 «표기가 판정을 뒤집으면 안 된다»는 이 검사 자체가 표기에 걸린다.
_ledger = " ".join(_evidence_texts("korean_units"))
check({"1485000", "148.5", "1188000", V._dform("2026", "06")} <= V.numbers(_ledger),
      "korean_units: 같은 값이 원장에 다른 표기로 들어 있다(오답이 아니었다)",
      str(sorted(V.numbers(_ledger))))


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
# 8. 없는 고객 id 를 조용히 넘기지 않는다
# ─────────────────────────────────────────────────────────────
# `-c` 값은 손대지 않고 graph.ask 로 그대로 간다. 그래서 없는 id 를 넣으면 에러 없이
# 재료만 0건이 되고, **id 를 잘못 넣은 것과 그 고객에게 재료가 없는 것이 구분되지 않는다.**
# 진단 도구에서 그 둘이 같아 보이면 안 된다.

_UNKNOWN = "999999-9999999"


def _capture(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


code, out = _capture(["-c", _UNKNOWN, "세액공제 한도가 얼마야?"])
check(code == 2 and "없습니다" in out, "없는 고객 id 면 실행 전에 끊는다", f"code={code}")
# 특정 페르소나를 이름으로 박지 않는다 — 고객 데이터는 이미 한 번 통째로 갈렸다
# (C1~C6 → PIN 9케이스). 안내가 **지금 적재된 것**을 말하는지만 본다.
from pension_agent.strategy_agent import customer as SC  # noqa: E402, PLC0415

_first = SC.PERSONAS[0]
check(f"{_first.id} {_first.nm}" in out and "--any-customer" in out,
      "안내에 이 체크아웃의 실제 고객 id 와 우회 방법이 들어 있다", out.strip()[:80])

code, out = _capture(["--script", "tax_credit_clean", "-c", _UNKNOWN, "--any-customer", "--debug"])
check(code == 0 and f"⚠ {_UNKNOWN}" in out,
      "--any-customer 면 진행하되 트레이스 맨 위에 경고가 남는다", f"code={code}")

# 고객 id 를 준 실행은 상담이력을 남긴다(.gitignore 대상). 이번 실행이 만든 것만 지운다.
_leftover = session_store.SESSION_DATA_DIR / f"{_UNKNOWN}.json"
if _leftover.exists():
    _leftover.unlink()
check(not _leftover.exists(), "검사가 남긴 상담이력 파일을 치운다")


# ─────────────────────────────────────────────────────────────
# 9. 계측 대상이 사라지면 조용히 덜 보지 않고 크게 실패한다
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
