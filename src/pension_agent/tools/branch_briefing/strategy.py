"""부점 전략 브리핑 — 화면 진입 시 한 번. 부점 현황(코드) + 오늘의 관리 방향 문장(LLM 1회).

판단 구조: 부점 상태 → 세그먼트 변화 → 고객 프로파일 특징 → 우선순위 → 상담·운용 전략.
CASE 판정·수치는 전부 코드(status.compute). LLM 은 판정된 CASE 를 문장으로만 옮긴다.

숫자 규칙(2026-10-22 결정): 문장에 쓰는 숫자는 판정 근거가 된 세그먼트 인원 변화뿐이다.
잔액·계좌 수·수익률은 화면 패널이 숫자로 보여주므로 문장은 방향만 말한다 — 코드가 검사한다.

    build(emp_no) → {"panel": 현황 dict, "briefing": {"text", "status", "reason"}}

status: ok(LLM 문장) · fallback(검사 두 번 실패 — 표의 전략 문장을 그대로) · no_case(해당 CASE 없음 —
코드 문장) · llm_down(LLM 미연결 — 문장 없음, 기준서 §11).
"""

from __future__ import annotations

from pension_agent.common import observability
from pension_agent.common.llm import LLMError, generate
from pension_agent.evidence import verify
from pension_agent.prompts.branch import (
    BRANCH_BRIEFING_PROMPT,
    BRANCH_BRIEFING_RETRY_BLOCK,
    BRANCH_BRIEFING_SYSTEM,
)
from pension_agent.tools.branch_briefing import status

#: 검사에 걸렸을 때 다시 쓰는 횟수(총 생성 RETRIES + 1 번).
RETRIES = 1

#: 해당 CASE 가 없을 때의 문장(코드). 숫자 없음.
NO_CASE = ("전월말과 비교해 기준표의 관리 방향 조건을 모두 채운 변화는 없습니다. "
           "오늘은 관리 대상 추천 목록의 순서대로 고객을 확인하는 것이 좋겠습니다.")

#: LLM 미연결 안내 — 문장을 대신 만들지 않는다(기준서 §11).
LLM_DOWN = "LLM 연결이 안 되어 있어 오늘의 관리 방향 문장을 만들지 못했습니다."


def _cases_block(st: dict) -> str:
    ind = "; ".join(f"{i['name']} {i['label']}" for i in st["indicators"])
    out = [f"부점 지표(전월말 대비): {ind}"]
    for n, c in enumerate(st["cases"], 1):
        changes = ", ".join(f"{s['name']} {s['delta']:+d}명" for s in c["segment_changes"])
        out += [f"{n}. {c['name']}",
                f"   판정 근거: {' · '.join(c['why'])}",
                f"   세그먼트 변화: {changes}",
                f"   우선 관리할 고객: {c['feature']}",
                f"   상담·운용 전략: {c['strategy']}"]
    return "\n".join(out)


def allowed_numbers(st: dict) -> list[str]:
    """문장에 쓸 수 있는 숫자 — 판정에 쓴 세그먼트의 인원 변화(절댓값)."""
    seen: list[str] = []
    for c in st["cases"]:
        for s in c["segment_changes"]:
            v = f"{abs(s['delta'])}명"
            if v not in seen:
                seen.append(v)
    return seen


def check_text(text: str, st: dict) -> list[str]:
    """브리핑 문장 검사 — 허용 밖 수치·상품명이 있으면 사유 목록."""
    ok, bad = verify.verify_texts(text, allowed_numbers(st))
    return [] if ok else bad


def _fallback(st: dict) -> str:
    """검사를 못 통과했을 때 — 표의 «상담·운용 전략» 원문을 우선순위 순으로(숫자 없음)."""
    return " ".join(c["strategy"] for c in st["cases"])


def write(st: dict) -> dict:
    """현황 → 브리핑 {text, status, reason}."""
    if not st["cases"]:
        return {"text": NO_CASE, "status": "no_case", "reason": None}
    numbers = "\n".join(f"- {n}" for n in allowed_numbers(st)) or "(없음)"
    draft, error = "", ""
    for attempt in range(RETRIES + 1):
        retry = BRANCH_BRIEFING_RETRY_BLOCK.format(draft=draft, error=error) if error else ""
        try:
            draft = generate(BRANCH_BRIEFING_PROMPT.format(cases=_cases_block(st), numbers=numbers,
                                                           retry=retry),
                             system=BRANCH_BRIEFING_SYSTEM, max_tokens=500, temperature=0.2,
                             name="branch.briefing").strip()
        except LLMError as exc:
            return {"text": None, "status": "llm_down", "reason": f"{LLM_DOWN} ({exc})"}
        bad = check_text(draft, st) if draft else ["빈 응답"]
        if not bad:
            return {"text": draft, "status": "ok", "reason": None}
        error = ", ".join(bad)
        observability.step("branch_briefing", "rejected", attempt=attempt + 1, error=error[:120])
    return {"text": _fallback(st), "status": "fallback", "reason": f"생성 문장 검사 실패 — {error}"}


def build(emp_no: str | None) -> dict:
    """사번 → {panel, briefing}. 부점·DB 를 못 읽으면 LookupError · run.DBUnavailable 등 그대로 올린다."""
    st = status.compute(emp_no)
    observability.step("branch_briefing", st["branch"], cases=",".join(st["cases_all"]) or "-")
    return {"panel": st, "briefing": write(st)}
