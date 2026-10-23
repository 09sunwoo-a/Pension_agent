"""타겟 고객 룰베이스 원본(xlsx) → targets.json 변환 (멱등).

기획자 확인표(타겟 14종)가 코드보다 상위 기준. 근거등급(A·B·C·D)·조건식은 원문 그대로 적재 — 판정은 `customer.py::conditions()`.
부점 브리핑 시트 6종(세그먼트·CASE·중점관리 조건·임계값)도 같은 파일에서 내린다 — 판정은 `tools/branch_briefing/status.py`.
이 5종은 에이전트 초안(기획자 위임 2026-10-22, 검토 전)이다 — 시트의 «적용상태» 칸이 그 사실을 적는다.

    python -m scripts.import_targets    # openpyxl 필요(변환 시에만)
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pension_agent.common import config

SOURCE_XLSX = config.REPO_ROOT / "IRP_타겟고객_룰베이스_v1.xlsx"
OUTPUT_JSON = config.TARGETS_JSON

#: 시트 → 산출 JSON 키. «요약» 시트는 집계라 미포함(중복 원장 금지).
_SHEETS = {
    "타겟_Master": "targets",
    "선정조건_Detail": "conditions",
    "액션_Master": "actions",
    "IF_THEN_Rule": "rules",
    "코드_거버넌스": "codes",
    "원천문서": "sources",
    # 부점 브리핑(세그먼트 변화 → CASE). 에이전트 초안(기획자 위임 2026-10-22) — 시트의 «적용상태» 참고
    "부점세그먼트_Master": "branch_segments",
    "부점세그먼트_조건": "branch_segment_conditions",
    "부점CASE_Rule": "branch_cases",
    "부점CASE_조건": "branch_case_conditions",
    "부점CASE_중점조건": "branch_case_focus",
    "부점_임계값": "branch_params",
}

#: 헤더 행 탐색 열쇠말 — 시트마다 제목 행 수가 달라 행 번호 고정 금지.
_HEADER_KEYS = {
    "targets": "TARGET_ID",
    "conditions": "COND_ID",
    "actions": "ACTION_ID",
    "rules": "RULE_ID",
    "codes": "코드",
    "sources": "SOURCE_ID",
    # Master·조건 시트가 같은 ID 열을 가지므로 조건 시트는 자기 행 ID 로 찾는다
    "branch_segments": "SEG_ID",
    "branch_segment_conditions": "SCOND_ID",
    "branch_cases": "CASE_ID",
    "branch_case_conditions": "CCOND_ID",
    "branch_case_focus": "FCOND_ID",
    "branch_params": "PARAM_ID",
}


def _cell(v: Any) -> Any:
    """엑셀 셀 → JSON 값. 날짜는 ISO, 공백뿐인 문자열은 None."""
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def _table(ws, header_key: str) -> list[dict[str, Any]]:
    """열쇠말로 헤더 행을 찾아 아래 행을 레코드로 반환. 빈 행 제외."""
    rows = [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
    head_at = next(i for i, r in enumerate(rows) if header_key in r)
    header = [h for h in rows[head_at] if h is not None]
    out: list[dict[str, Any]] = []
    for r in rows[head_at + 1:]:
        if not any(c is not None for c in r):
            continue
        out.append({h: r[i] for i, h in enumerate(header)})
    return out


def main() -> None:
    from openpyxl import load_workbook  # 변환 시에만 필요

    if not SOURCE_XLSX.is_file():
        print(f"원본이 없습니다: {SOURCE_XLSX}")
        raise SystemExit(1)

    wb = load_workbook(SOURCE_XLSX, data_only=True)
    doc: dict[str, Any] = {
        "meta": {
            "source": SOURCE_XLSX.name,
            "note": "기획자가 행내 원문(IRP 텐션 UP-②③④⑤)을 정규화해 확인해준 "
                    "사후관리 타겟 룰베이스. 요건 임계값의 상위 기준이다.",
            "grades": {
                "A": "문서 직접명시 — 원문에 대상·임계값이 그대로 있다",
                "B": "직접조건 통합 — A 를 상위 캠페인군으로 묶은 것",
                "C": "조사결과 기반 — 이탈고객 조사 비중이며 개인 임계값이 아니다",
                "D": "설계 제안 포함 — 원문에 없는 임계값을 기획자가 제안했다(Pilot)",
            },
        },
    }
    for sheet, key in _SHEETS.items():
        doc[key] = _table(wb[sheet], _HEADER_KEYS[key])

    OUTPUT_JSON.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_grade: dict[str, int] = {}
    for t in doc["targets"]:
        g = str(t.get("근거등급") or "?")[0]
        by_grade[g] = by_grade.get(g, 0) + 1
    print(f"[import_targets] 타겟 {len(doc['targets'])}종 · 조건 {len(doc['conditions'])}행 · "
          f"액션 {len(doc['actions'])}행 · 룰 {len(doc['rules'])}행 · "
          f"원천문서 {len(doc['sources'])}건 → {OUTPUT_JSON.relative_to(config.SRC_ROOT)}")
    print(f"            근거등급 " + " · ".join(f"{g} {n}종" for g, n in sorted(by_grade.items())))
    print(f"            부점 세그먼트 {len(doc['branch_segments'])}종 · CASE {len(doc['branch_cases'])}종 · "
          f"중점관리 조건 {len(doc['branch_case_focus'])}행 · 임계값 {len(doc['branch_params'])}행")


if __name__ == "__main__":
    main()
