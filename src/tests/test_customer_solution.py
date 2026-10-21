"""고객별 솔루션(solution.py) 회귀 테스트 — LLM 1회·캐시 · 서술 검증 · 실패 처리 · 개인정보 · 그래프 직답. LLM 스텁.

실행: python -m tests.test_customer_solution
"""

from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent.tools.customer_solution import engine, solution
from pension_agent.tools.customer_solution.customer import PERSONAS

_BY_NAME = {p.nm: p for p in PERSONAS}

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))
    print(f"{'✓' if cond else '✗'} {label}" + (f" — {detail}" if detail and not cond else ""))


class _Stub:
    """solution.llm 의 available·generate 교체·복원, 호출 기록."""

    def __init__(self, reply=None, *, available: bool = True, raises: Exception | None = None):
        self.reply, self.available, self.raises = reply, available, raises
        self.prompts: list[str] = []

    def __enter__(self) -> _Stub:
        self._saved = (solution.llm.available, solution.llm.generate)
        solution.llm.available = lambda: self.available

        def _gen(prompt, system="", **kw):
            self.prompts.append(prompt)
            if self.raises:
                raise self.raises
            return self.reply

        solution.llm.generate = _gen
        solution.clear_cache()
        return self

    def __exit__(self, *exc) -> None:
        solution.llm.available, solution.llm.generate = self._saved
        solution.clear_cache()


def _reply(**fields) -> str:
    base = {"recent": [], "focus": "", "why_now": "", "confirm": []}
    return json.dumps({**base, **fields}, ensure_ascii=False)


# 호출 횟수 · 캐시

def check_one_call_and_cached() -> None:
    p = _BY_NAME["오세훈"]
    with _Stub(_reply(recent=["연금수령이 가능한 상태이며 아직 연금은 개시하지 않았습니다."],
                      focus="만기 자금의 운용 방향을 확인합니다.",
                      confirm=["당분간 계속 운용할 계획인지"])) as stub:
        first = solution.document(p)
        second = solution.document(p)
        check(len(stub.prompts) == 1, "전체 브리핑 한 편에 LLM 호출은 1회", str(len(stub.prompts)))
        check(first["markdown"] == second["markdown"], "같은 고객·같은 날이면 같은 문서를 돌려준다")
        check(solution.peek(p) == first["narrative"], "peek(): 이미 만든 서술을 돌려준다")
        md = first["markdown"]
        check(all(s in md for s in ("### 고객 정보", "### IRP 계좌 현황", "### AI 분석")),
              "문서에 사실 표 두 개와 AI 분석이 있다")
        check("- [ ] 당분간 계속 운용할 계획인지" in md, "고객과 확인할 것은 체크 목록으로 선다")


def check_facts_are_copied_not_rewritten() -> None:
    """사실 부분 값은 계산 결과 그대로(LLM 미경유)."""
    p = _BY_NAME["오세훈"]
    f = solution.facts(p)
    md = solution.render(p, f, None, "테스트")
    for label, value in (("평가금액", f["customer"]["평가금액"]),
                         ("만기 도래", f["briefing"]["만기도래"]),
                         ("세액공제 잔여한도", f["account_state"]["세액공제_잔여한도"])):
        check(f"| {label} | {value} |" in md, f"문서의 «{label}» 칸이 계산 결과와 같다", value)


# 서술 검증

def check_fabricated_line_dropped() -> None:
    p = _BY_NAME["이준호"]
    with _Stub(_reply(recent=["보유 자산 구성을 점검할 시점입니다.",
                              "KB 특판 정기예금 연 9.99% 상품에 가입할 수 있습니다."],
                      why_now="2027년 3월 31일까지 결정해야 합니다.")):
        doc = solution.document(p)
    nar = doc["narrative"] or {}
    check(nar.get("recent") == ["보유 자산 구성을 점검할 시점입니다."],
          "재료 밖 수치가 든 문장만 버리고 나머지는 남긴다", str(nar.get("recent")))
    check(not nar.get("why_now"), "재료 밖 날짜가 든 문장도 버린다", str(nar.get("why_now")))
    check(len(doc["rejected"]) >= 2, "버린 사유가 남는다", str(doc["rejected"])[:120])


def check_shown_values_are_quotable() -> None:
    """프롬프트에 보여준 값은 허용, 지어낸·계산한 값은 차단."""
    p = _BY_NAME["오세훈"]
    f = solution.facts(p)
    extra = solution._extra(f)
    joined = f["account_state"]["IRP_가입일"]
    ok, bad = engine.verify(f"IRP 가입일은 {joined}입니다.", f, extra=extra)
    check(ok, "계좌 상태 값(가입일)을 인용할 수 있다", str(bad))
    ok, _ = engine.verify("예상 수익률은 연 7.5% 입니다.", f, extra=extra)
    check(not ok, "보여주지 않은 값은 막힌다(지어낸 수치)")


def check_owned_products_are_quotable() -> None:
    """보유 상품 이름 허용, 지어낸 이름 차단."""
    p = _BY_NAME.get("송도윤")
    if p is None:
        check(False, "보유 상품 인용: 전제조건 불충족 — 송도윤 없음")
        return
    f = solution.facts(p)
    mine = next((n for n in f.get("owned_products") or [] if n.startswith("KB ")), "")
    if not mine:
        check(False, "보유 상품 인용: 전제조건 불충족 — 'KB ' 이름이 없다")
        return
    ok, bad = engine.verify(f"{mine} 을 보유하고 계십니다.", f)
    check(ok, f"이 고객이 가진 상품 이름을 말할 수 있다 ({mine})", str(bad))
    ok, _ = engine.verify("KB 무지개 성장 펀드를 권해보세요.", f)
    check(not ok, "지어낸 상품 이름은 막힌다")


# 실패

def check_failure_not_kept() -> None:
    p = _BY_NAME["이준호"]
    with _Stub(raises=RuntimeError("429")) as stub:
        doc = solution.document(p)
        check(doc["narrative"] is None and "호출 실패" in doc["reason"],
              "LLM 호출이 죽으면 서술 없이 사실 표만 나온다", doc["reason"])
        check("### IRP 계좌 현황" in doc["markdown"] and "AI 분석을 만들지 못했어요" in doc["markdown"],
              "그때도 사실 부분은 나오고, 서술이 없다는 사실을 밝힌다")
        solution.document(p)
        check(len(stub.prompts) == 2, "서술을 못 만든 문서는 붙잡아 두지 않는다(다음 호출이 다시 시도)",
              str(len(stub.prompts)))
    with _Stub("JSON 이 아님"):
        doc = solution.document(p)
        check(doc["narrative"] is None and "파싱" in doc["reason"], "응답을 못 읽으면 서술 없음", doc["reason"])
    with _Stub(available=False) as stub:
        doc = solution.document(p)
        check(not stub.prompts and doc["reason"] == "LLM 미설정", "LLM 미설정이면 부르지 않는다")


# 개인정보·개발 용어

def check_no_id_or_codes() -> None:
    p = _BY_NAME["오세훈"]
    with _Stub(_reply(focus="확인합니다.")) as stub:
        doc = solution.document(p)
    prompt = stub.prompts[0] if stub.prompts else ""
    check(p.id not in prompt and p.id not in doc["markdown"],
          "고객 식별번호는 프롬프트에도 문서에도 없다(게이트웨이 개인정보 필터)")
    codes = [c.split(":", 1)[0] for c in solution.facts(p)["conditions"]]
    leaked = [c for c in codes if f'"{c}"' in prompt or f"{c}:" in prompt]
    check(not leaked, "요건 코드(isa·nod …)는 프롬프트에 없다", str(leaked))


# 그래프 — 문서는 그대로 답이 된다

def check_document_passthrough() -> None:
    from pension_agent.nodes import answer as ANS
    from pension_agent.tools.customer_solution.tool import _briefing

    p = _BY_NAME["오세훈"]
    with _Stub(_reply(focus="만기 자금의 운용 방향을 확인합니다.")):
        ev = _briefing({"customer_id": p.id, "question": "이 고객 브리핑 해줘"}, "브리핑")
        check(ev is not None and ev["meta"].get("document"), "briefing 도구가 문서를 meta 에 싣는다")
        called = []
        saved = ANS.compose
        ANS.compose = lambda state: called.append(1) or {"answer": "x"}
        try:
            out = ANS.answer({"customer_id": p.id, "question": "이 고객 브리핑 해줘",
                              "evidence": [ev]})
        finally:
            ANS.compose = saved
    check(out.get("answer") == ev["meta"]["document"], "문서가 그대로 답이 된다")
    check(not called, "답변 작성(compose)을 거치지 않는다")
    check(any(s.get("id") == "customer" for s in out.get("sources") or []), "출처가 실린다")
    ids = {s.get("id", "") for s in out.get("sources") or []}
    check(not any(p.id in i for i in ids), "출처 id 에도 식별번호가 없다", str(ids))


def main() -> int:
    check_one_call_and_cached()
    check_facts_are_copied_not_rewritten()
    check_fabricated_line_dropped()
    check_shown_values_are_quotable()
    check_owned_products_are_quotable()
    check_failure_not_kept()
    check_no_id_or_codes()
    check_document_passthrough()

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} 통과")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
