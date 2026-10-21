"""시연용 더미 고객 원본(xlsx 9케이스 + demo_cases.json 3명) → customers.json 변환 (멱등).

값 가공 없음 — Profile 매핑은 `customer.py` 담당. 전 시트 보존(미요건화 배지·상담이력 재료).

    python -m scripts.import_customers    # openpyxl 필요(변환 시에만)
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pension_agent.common import config

SOURCE_XLSX = config.REPO_ROOT / "IRP_Agent_더미고객_9Cases_v3.xlsx"
#: 데모 골든 케이스 3명(DEMO_GOLDEN_CASES_V2) — customers.json 직접 수정 시 재생성에 소실. 없으면 9케이스만.
DEMO_JSON = config.SRC_ROOT / "scripts" / "demo_cases.json"
OUTPUT_JSON = config.CUSTOMERS_JSON

AS_OF = "2026-08-24"  # 09_데이터사전 '기준일' — customer.AS_OF 와 일치 필수

#: 시트 → 레코드 키. 고객당 1행은 dict, n행은 list.
_ONE_ROW_SHEETS = {
    "01_BASIC": "basic",
    "02_IRP_SUMMARY": "summary",
    "04_ACTIVITY": "activity",
    "05_TAX_ISA": "tax_isa",
    "06_PENSION": "pension",
    "07_PEER_COMPARE": "peer",
    "08_BADGES": "badges",
}
_MANY_ROW_SHEETS = {
    "03_PRODUCTS": "products",
    "10_상담이력": "history",
}


def _cell(v: Any) -> Any:
    """엑셀 셀 → JSON 값. 날짜는 ISO, 빈 문자열은 None."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _rows(ws) -> list[dict[str, Any]]:
    """1행 제목·2행 헤더 시트 → dict 행 목록."""
    it = ws.iter_rows(values_only=True)
    next(it)  # 제목 행
    header = [str(h).strip() if h is not None else "" for h in next(it)]
    out = []
    for raw in it:
        if not any(v is not None for v in raw):
            continue
        out.append({h: _cell(v) for h, v in zip(header, raw) if h})
    return out


def build() -> dict[str, Any]:
    import openpyxl  # noqa: PLC0415 — 변환 시점에만 필요한 의존성

    wb = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)

    # 00_시연케이스 = 고객 목록·순서 원장. «시연 포인트» 열은 연출 메모라 미포함 — 에이전트 재료로 섞임 방지
    cases = _rows(wb["00_시연케이스"])
    records: dict[str, dict[str, Any]] = {}
    for c in cases:
        pin = c["KB-PIN"]
        records[pin] = {"id": pin, "badge": c.get("Badge")}

    for sheet, key in _ONE_ROW_SHEETS.items():
        for row in _rows(wb[sheet]):
            pin = row.pop("KB-PIN")
            records[pin][key] = row
    for sheet, key in _MANY_ROW_SHEETS.items():
        for pin in records:
            records[pin].setdefault(key, [])
        for row in _rows(wb[sheet]):
            pin = row.pop("KB-PIN")
            records[pin][key].append(row)

    if DEMO_JSON.is_file():
        demo = json.loads(DEMO_JSON.read_text(encoding="utf-8"))
        if demo["meta"]["as_of"] != AS_OF:
            raise ValueError(
                f"demo_cases.json 기준일({demo['meta']['as_of']})이 {AS_OF} 와 다릅니다 — "
                "스냅샷 시점이 다른 고객을 한 원장에 섞을 수 없습니다.")
        for r in demo["records"]:
            if r["id"] in records:
                raise ValueError(f"demo_cases.json 고객 {r['id']} 가 xlsx 케이스와 겹칩니다.")
            records[r["id"]] = r

    dictionary = _rows(wb["09_데이터사전"])
    return {
        "meta": {
            "title": f"시연용 더미 고객 {len(records)}케이스 (xlsx 9 + 데모 골든 케이스)",
            "as_of": AS_OF,
            "source": SOURCE_XLSX.name,
            "note": "실제 고객/상품/수익률이 아닌 목업용 가상 데이터. 실거래·대고객 안내에 "
                    "사용 금지(09_데이터사전). 이 파일은 scripts/import_customers.py 산출물이다 "
                    "— 고칠 값은 원본(9케이스는 xlsx, 데모 골든 케이스 3명은 "
                    "scripts/demo_cases.json)에 넣고 다시 생성한다.",
            "dictionary": dictionary,
        },
        "records": list(records.values()),
    }


def main() -> None:
    data = build()
    OUTPUT_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n = len(data["records"])
    products = sum(len(r["products"]) for r in data["records"])
    history = sum(len(r["history"]) for r in data["records"])
    print(f"[import_customers] {OUTPUT_JSON.relative_to(config.SRC_ROOT)} 갱신 — "
          f"고객 {n}명 · 보유상품 {products}건 · 상담이력 {history}건 (기준일 {data['meta']['as_of']})")


if __name__ == "__main__":
    main()
