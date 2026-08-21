"""agent.py 의 LLM 작성 단계 회귀 테스트 — LLM 을 스텁으로 갈아끼우므로 API 키 없이 돌아간다.

engine.verify() 는 이미 test_engine.py 가 대조 함수 단위로 검증하지만, agent.py 가 실제로
그 결과에 따라 규칙 폴백으로 전환하는지, 그리고 ⑤(_recommend)·⑥(_write_talking_scripts) 의
LLM 접점이 목록 밖 id·재료 이탈을 실제로 거부하는지는 agent.py 를 직접 통해서만 검증된다
(기존에는 이 파일이 없어 이 경로가 결정론적 스텁 테스트로 커버되지 않았다 —
consult_agent/test_agent.py 의 스텁 패턴을 그대로 옮겼다).

실행: python test_agent.py
"""

from __future__ import annotations

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")  # llm.available() 만 통과시킨다

import agent as A  # noqa: E402
import engine  # noqa: E402
import llm  # noqa: E402
from customer import PERSONAS  # noqa: E402

_BY_NAME = {p.nm: p for p in PERSONAS}

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))
    print(f"{'✓' if cond else '✗'} {label}" + (f" — {detail}" if detail and not cond else ""))


def _restore_llm() -> None:
    """각 테스트가 끝나면 llm.generate/available 을 원래 함수로 되돌린다."""
    import importlib

    importlib.reload(llm)
    A.llm = llm


# ─────────────────────────────────────────────────────────────
# ① sentence 경로 — order 불일치 → 규칙 폴백
# ─────────────────────────────────────────────────────────────

def check_order_mismatch_fallback() -> None:
    p = _BY_NAME["김민수"]
    facts = engine.prepare(p)
    ids = [it["id"] for it in facts["items"]]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {"order": ids[:-1], "insight": "테스트", "sentence": "테스트 문장입니다."}, ensure_ascii=False
    )
    A.llm = llm

    out = A.propose(p)
    check(
        out["source"] == "미생성" and out["sentence"] == "" and "임의로" in out["reason"],
        "propose(): order 가 항목 id 와 불일치하면 sentence 를 비움(규칙 문장으로 얼버무리지 않음)",
        out["reason"],
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ① sentence 경로 — 재료 밖 수치 포함 → verify 거부 → 규칙 폴백
# ─────────────────────────────────────────────────────────────

def check_sentence_verify_rejects_fabrication() -> None:
    p = _BY_NAME["김민수"]
    facts = engine.prepare(p)
    ids = [it["id"] for it in facts["items"]]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {"order": ids, "insight": "테스트",
         "sentence": "이 고객은 수익률 999.9%가 기대되니 적극 제안하세요."},
        ensure_ascii=False,
    )
    A.llm = llm

    out = A.propose(p)
    check(
        out["source"] == "미생성" and out["sentence"] == "" and out["rejected"],
        "propose(): sentence 에 재료 밖 수치가 있으면 verify 거부 → sentence 비움",
        str(out["rejected"]),
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ② 왜 이 고객님인가요 — 정상 생성 및 재료 이탈 거부
# ─────────────────────────────────────────────────────────────

def check_why_customer_accepts_grounded() -> None:
    p = _BY_NAME["김민수"]  # balPct 값이 있는 페르소나
    facts = engine.prepare(p)
    stub_lines = [f"평가금액이 유사 고객 상위 {p.balPct}% 수준이라 관리 대상으로 떴어요."]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({"lines": stub_lines}, ensure_ascii=False)
    A.llm = llm

    A._write_why_this_customer(facts)
    check(
        facts["why_this_customer"] == stub_lines,
        "② _write_why_this_customer(): 재료 안 값이면 LLM 문장으로 교체",
        str(facts["why_this_customer"]),
    )
    _restore_llm()


def check_why_customer_rejects_fabrication() -> None:
    p = _BY_NAME["김민수"]
    facts = engine.prepare(p)
    rule_lines = list(facts["why_this_customer"])

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {"lines": ["평가금액이 유사 고객 상위 1% 수준이에요."]}, ensure_ascii=False,
    )
    A.llm = llm

    A._write_why_this_customer(facts)
    check(
        facts["why_this_customer"] == rule_lines,
        "② _write_why_this_customer(): 재료 밖 수치가 있으면 규칙 문장 그대로 유지",
        str(facts["why_this_customer"]),
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ⑥ 대고객 화법 스크립트 — 정상 생성 및 재료 이탈 거부
# ─────────────────────────────────────────────────────────────

def check_talking_script_accepts_grounded() -> None:
    p = _BY_NAME["이현우"]  # talking_points 1개 이상 있는 페르소나
    facts = engine.prepare(p)
    if not facts["talking_points"]:
        check(False, "⑥ 화법 스크립트: 전제조건(talking_points 존재) 불충족 — 페르소나 데이터 확인 필요")
        return

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {tp["title"]: f"고객님, {tp['title']} 관련 안내드릴게요." for tp in facts["talking_points"]},
        ensure_ascii=False,
    )
    A.llm = llm

    A._write_talking_scripts(facts)
    check(
        all(tp.get("script") for tp in facts["talking_points"]),
        "⑥ _write_talking_scripts(): 재료 안 값이면 script 채움",
        str(facts["talking_points"]),
    )
    _restore_llm()


def check_talking_script_rejects_fabrication() -> None:
    p = _BY_NAME["이현우"]
    facts = engine.prepare(p)
    if not facts["talking_points"]:
        check(False, "⑥ 화법 스크립트(거부 케이스): 전제조건 불충족")
        return

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {tp["title"]: "고객님, 99999999원을 KB없는상품에 넣으시면 좋아요." for tp in facts["talking_points"]},
        ensure_ascii=False,
    )
    A.llm = llm

    A._write_talking_scripts(facts)
    check(
        all(not tp.get("script") for tp in facts["talking_points"]),
        "⑥ _write_talking_scripts(): 재료 밖 값이면 script 비움(talk 로 폴백)",
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ⑤ 상품·포트폴리오 추천 — 정상 선정, 목록 밖 id 거부, 재료 이탈 거부
# ─────────────────────────────────────────────────────────────

def check_recommend_accepts_valid_pick() -> None:
    p = _BY_NAME["박지영"]  # 후보 상품·포트폴리오 모두 있는 페르소나
    pool = engine.candidate_pool_for_recommendation(p)
    if not pool["products"] or not pool["portfolios"]:
        check(False, "⑤ 추천: 전제조건(후보 상품·포트폴리오 존재) 불충족")
        return
    pid, fid = pool["products"][0]["id"], pool["portfolios"][0]["id"]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({
        "product_id": pid, "product_reason": "이 상품은 고객 성향에 적합합니다.",
        "portfolio_id": fid, "portfolio_reason": "이 포트폴리오는 분산 효과가 있습니다.",
        "combined_reason": "성장성과 안정성을 함께 고려했습니다.",
    }, ensure_ascii=False)
    A.llm = llm

    reco = A._recommend(p, engine.prepare(p))
    check(reco is not None and reco["product"]["name"], "⑤ _recommend(): 유효한 id 선택 시 결과 반환",
          str(reco))
    check(reco is not None and reco.get("portfolio") is not None, "⑤ _recommend(): 포트폴리오도 함께 반영")
    _restore_llm()


def check_recommend_rejects_out_of_pool_id() -> None:
    p = _BY_NAME["박지영"]
    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({
        "product_id": "P99-NOT-EXIST", "product_reason": "테스트",
        "portfolio_id": None, "portfolio_reason": "", "combined_reason": "",
    }, ensure_ascii=False)
    A.llm = llm

    reco = A._recommend(p, engine.prepare(p))
    check(reco is None, "⑤ _recommend(): 후보 목록 밖 id 는 거부(None 반환)")
    _restore_llm()


def check_recommend_rejects_fabricated_number() -> None:
    p = _BY_NAME["박지영"]
    pool = engine.candidate_pool_for_recommendation(p)
    if not pool["products"]:
        check(False, "⑤ 추천(재료이탈 케이스): 전제조건 불충족")
        return
    pid = pool["products"][0]["id"]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({
        "product_id": pid, "product_reason": "이 상품은 최근 1년 수익률이 999.9%로 압도적입니다.",
        "portfolio_id": None, "portfolio_reason": "", "combined_reason": "",
    }, ensure_ascii=False)
    A.llm = llm

    reco = A._recommend(p, engine.prepare(p))
    check(reco is None, "⑤ _recommend(): 추천 사유에 재료 밖 수치가 있으면 거부(None 반환)")
    _restore_llm()


def main() -> int:
    check_order_mismatch_fallback()
    check_sentence_verify_rejects_fabrication()
    check_why_customer_accepts_grounded()
    check_why_customer_rejects_fabrication()
    check_talking_script_accepts_grounded()
    check_talking_script_rejects_fabrication()
    check_recommend_accepts_valid_pick()
    check_recommend_rejects_out_of_pool_id()
    check_recommend_rejects_fabricated_number()

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} 통과")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
