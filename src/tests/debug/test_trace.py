"""트레이스 회귀 검사 — 게이트별 차단·통과 판정, 계측 복원. 캔드 시나리오만 사용(키 불필요).

실행: cd src && python -m tests.debug.test_trace
"""

from __future__ import annotations

import contextlib
import io
import sys
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent.common import session_store
from pension_agent.evidence import verify as V
from pension_agent import graph as G
from pension_agent.evidence import relations as REL
from pension_agent import tools as T
from pension_agent.nodes import plan as P
from tests.debug import script, trace as TR
from tests.debug.__main__ import main
from tests.debug.runner import run

#: 폴백 머리말 고정 문구 앞부분 — 베껴 적기 금지
_RAW_HEAD = P.RAW_EVIDENCE.split("{", 1)[0]

#: 계측 전 원본(복원 검사용) — import 시점에 확보
_ORIGINALS = {"answer": G.answer, "verify_texts": P.verify_texts,
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
    """시나리오가 모으는 근거 원문(원장 대조용)."""
    scn = script.SCENARIOS[name]
    with script.installed(scn):
        found = T.run("fact", {"question": scn.question}, "세액공제 한도")
    return [found["text"]] if found else []


# 1. 노드 순서 — plan → answer(되묻기·작성) → offer

r, tr = _run("tax_credit_asserts_wrong")
check(tr.node_names() == ["understand", "plan_step", "answer", "offer"],
      "노드 순서: understand → plan → answer → offer", str(tr.node_names()))
check((tr.node("plan_step") or types.SimpleNamespace(note="")).note.startswith(
          "fact:세액공제 한도 → 채택 fact.k04.f2"),
      "계획 단계가 부른 도구·질의·채택 카드가 보인다",
      (tr.node("plan_step") or types.SimpleNamespace(note="?")).note)


# 2·3. 알려진 오답 — 주장은 차단, 정정(인용부호 + 정정 표지)은 통과

gates = tr.gates()
check(gates.get("verify_texts") is not None and gates["verify_texts"].passed,
      "asserts_wrong: 수치 집합 검사는 통과한다(원장 안의 숫자만 썼다)")
check(tr.blocked_by() == "relations" and "5,500만원 이상 13.2%" in gates["relations"].detail,
      "asserts_wrong: 오답을 주장하면 relations 가 생성문을 버린다",
      str(gates.get("relations")))
check("span" not in gates,
      "asserts_wrong: 앞에서 끊겨 원문 스팬 검사는 실행조차 되지 않는다", str(sorted(gates)))

draft = tr.draft()
# 폴백 머리말 필수 — 없으면 카드 덤프를 답변으로 오독
check(r["answer"].startswith(_RAW_HEAD) and "■" in r["answer"]
      and draft and draft not in r["answer"],
      "asserts_wrong: 화면에 나간 것은 생성문이 아니라 근거 원문이다(말투가 달라지는 자리)",
      r["answer"][:40])
_head = r["answer"].split("\n\n", 1)[0]
check("틀린 표현" in _head and "5,500만원 이상 13.2%" not in _head,
      "asserts_wrong: 폴백이 걸린 «종류»는 밝히고 버려진 값은 화면에 싣지 않는다",
      _head)
check((tr.node(TR.ANSWER_NODE) or types.SimpleNamespace(note="")).note.startswith("폴백"),
      "asserts_wrong: 처분이 '폴백' 으로 기록된다")

rc, trc = _run("tax_credit_correction")
check(trc.blocked_by() is None and not rc["answer"].startswith("■"),
      "correction: 오기를 «틀렸다»고 짚는 문장은 더는 폐기되지 않는다",
      str(trc.gates().get("relations")))
check("오기니까" in rc["answer"] and '"초과"' in rc["answer"],
      "correction: 그 코칭이 화면까지 간다", rc["answer"][-40:])


# 4. 통과 경로

r2, tr2 = _run("tax_credit_clean")
gates2 = tr2.gates()
check(tr2.blocked_by() is None and set(gates2) == {"verify_texts", "relations", "span"},
      "clean: 게이트 3종이 전부 실행되고 전부 통과한다", str(sorted(gates2)))
check(r2["answer"].startswith(tr2.draft()) and not r2["answer"].startswith("■"),
      "clean: 생성문이 그대로 답변이 된다(해요체 유지)", r2["answer"][:40])


# 5. 다른 게이트 차단 시 트레이스 구분

r3, tr3 = _run("out_of_ledger")
gates3 = tr3.gates()
check(tr3.blocked_by() == "verify_texts" and "relations" not in gates3,
      "out_of_ledger: 원장 밖 수치에서 끊기고 relations 는 실행되지 않는다",
      str(sorted(gates3)))
check(r3["answer"].startswith(_RAW_HEAD) and "■" in r3["answer"],
      "out_of_ledger: 역시 근거 원문 폴백", r3["answer"][:30])


# 5-2. 표기만 다른 같은 값은 통과, 값이 다르면 폐기

r5, tr5 = _run("korean_units")
check(tr5.blocked_by() is None and not r5["answer"].startswith(_RAW_HEAD),
      "korean_units: 만·천으로 끊어 쓴 금액이 더는 '원장 밖 수치' 로 걸리지 않는다",
      str(tr5.gates().get("verify_texts")))
check("148만 5천원" in r5["answer"] and "2026년 6월" in r5["answer"],
      "korean_units: 직원이 말하는 표기 그대로 화면에 나간다", r5["answer"][:40])

r6, tr6 = _run("korean_units_wrong")
check(tr6.blocked_by() == "verify_texts" and r6["answer"].startswith(_RAW_HEAD),
      "korean_units_wrong: 같은 표기라도 값이 틀리면 여전히 폐기된다",
      str(tr6.gates().get("verify_texts")))

# 같은 값이 원장에 다른 표기로 존재. 연월은 날짜 정규형(`날짜:2026-06`)으로 조회
_ledger = " ".join(_evidence_texts("korean_units"))
check({"1485000", "148.5", "1188000", V._dform("2026", "06")} <= V.numbers(_ledger),
      "korean_units: 같은 값이 원장에 다른 표기로 들어 있다(오답이 아니었다)",
      str(sorted(V.numbers(_ledger))))


# 6. LLM 실패 턴은 폴백 아님

r4, tr4 = _run("llm_dead")
check(r4["answer"].startswith(P.LLM_FAILED.split("{", 1)[0])
      and not r4["answer"].startswith(_RAW_HEAD) and "■" not in r4["answer"],
      "llm_dead: 근거 원문을 대신 내보내지 않고 실패를 실패라고 답한다", r4["answer"][:40])
check(tr4.gates() == {}, "llm_dead: 게이트는 하나도 실행되지 않는다", str(tr4.gates()))


# 7. 계측 복원 — 다른 스위트 오염 방지

check(G.answer is _ORIGINALS["answer"], "복원: graph.answer 가 원본으로 돌아왔다")
check(P.verify_texts is _ORIGINALS["verify_texts"] is V.verify_texts,
      "복원: plan.verify_texts 가 원본 함수다")
check(P.relations is _ORIGINALS["relations"] is REL,
      "복원: plan.relations 가 진짜 모듈이다(SimpleNamespace 가 남지 않았다)")
check(P._span_verdict is _ORIGINALS["span"], "복원: plan._span_verdict 가 원본이다")
check(T.fits_question is _ORIGINALS["fits_question"],
      "복원: 시나리오 스텁(tools.fits_question)도 원본으로 돌아왔다")
check(G._AGENT is None,
      "복원: 계측된 노드가 박힌 컴파일 그래프를 남기지 않는다", str(G._AGENT))


# 8. 없는 고객 id 차단 — 오타와 재료 0건이 구분 안 됨

_UNKNOWN = "999999-9999999"


def _capture(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


code, out = _capture(["-c", _UNKNOWN, "세액공제 한도가 얼마야?"])
check(code == 2 and "없습니다" in out, "없는 고객 id 면 실행 전에 끊는다", f"code={code}")
# 페르소나 이름 하드코딩 금지 — 현재 적재분 기준
from pension_agent.tools.customer_solution import customer as SC  # noqa: E402, PLC0415

_first = SC.PERSONAS[0]
check(f"{_first.id} {_first.nm}" in out and "--any-customer" in out,
      "안내에 이 체크아웃의 실제 고객 id 와 우회 방법이 들어 있다", out.strip()[:80])

code, out = _capture(["--script", "tax_credit_clean", "-c", _UNKNOWN, "--any-customer", "--debug"])
check(code == 0 and f"⚠ {_UNKNOWN}" in out,
      "--any-customer 면 진행하되 트레이스 맨 위에 경고가 남는다", f"code={code}")

# 이번 실행이 만든 상담이력만 삭제
_leftover = session_store.SESSION_DATA_DIR / f"{_UNKNOWN}.json"
if _leftover.exists():
    _leftover.unlink()
check(not _leftover.exists(), "검사가 남긴 상담이력 파일을 치운다")


# 9. 계측 대상 소멸 시 예외 — 없으면 못 본 게이트를 «통과»로 기록

_saved_span = P._span_verdict
del P._span_verdict
try:
    with TR.instrument(TR.Trace()):
        check(False, "계측 대상이 없으면 예외를 던진다", "예외 없이 계측이 시작됐다")
except AttributeError as exc:
    check("_span_verdict" in str(exc), "계측 대상이 없으면 예외를 던진다(덜 보지 않는다)", str(exc))
finally:
    P._span_verdict = _saved_span



failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    print(("✓" if ok else "✗") + f" {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 트레이스 회귀 발생")
    raise SystemExit(1)
print("✅ 트레이스 회귀 검사 통과")
