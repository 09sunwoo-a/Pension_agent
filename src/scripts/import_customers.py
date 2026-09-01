"""시연용 더미 고객 원본(xlsx + demo_cases.json) → customers.json 변환기 (멱등).

원본은 둘이다: ① 저장소 루트의 `IRP_Agent_더미고객_9Cases_v3.xlsx` — 기준일 2026-08-24 의
목업 9케이스(00_시연케이스 시트 참고) ② `scripts/demo_cases.json` — 브리핑 에이전트 데모
골든 케이스 3명(DEMO_GOLDEN_CASES_V2, 같은 기준일·같은 레코드 스키마). 이 스크립트는
xlsx 전 시트를 KB-PIN 단위 레코드로 묶고 데모 3명을 뒤에 이어붙여
`pension_agent/strategy_agent/customers.json` 으로 내린다. 값의 가공은 하지 않는다 —
비중은 원본 그대로 소수(0~1)로 두고, Profile 로의 매핑(4분류 반올림·파생 필드)은 적재
시점에 `strategy_agent/customer.py` 가 한다. 여기서 미리 가공하면 "원본이 무엇이었나"가
JSON 에서 사라진다.

전 시트를 보존하는 이유: 신규 배지 4종(판매중단펀드·ISA만기·연금개시 미개시·이탈위험관찰)·
동연령 비교·상담이력은 아직 엔진 요건(CONDS)이 아니다 — 지식베이스 근거 확인이 선행돼야
요건화할 수 있다(CLAUDE.md "지식베이스에 없는 기준은 만들지 않는다"). 그때 쓸 재료를
잃지 않도록 JSON 에 다 담아둔다.

실행 (src/ 에서):

    python -m scripts.import_customers

openpyxl 이 필요하다(변환할 때만 — 적재·테스트는 산출된 JSON 만 읽으므로 불필요).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pension_agent import config

SOURCE_XLSX = config.REPO_ROOT / "IRP_Agent_더미고객_9Cases_v3.xlsx"
#: 데모 골든 케이스(브리핑 에이전트 시연 3명, DEMO_GOLDEN_CASES_V2) 병합 소스.
#: customers.json 에 직접 넣으면 이 스크립트 재실행 한 번에 증발하므로, 체크인된 JSON 을
#: 여기서 xlsx 9케이스 뒤에 이어붙인다. 없으면 그냥 9케이스만 내린다.
DEMO_JSON = config.SRC_ROOT / "scripts" / "demo_cases.json"
OUTPUT_JSON = config.CUSTOMERS_JSON

AS_OF = "2026-08-24"  # 09_데이터사전 '기준일' — customer.AS_OF 와 일치해야 한다

#: 시트 → 레코드 키. BASIC 류(고객당 1행)는 dict 로, PRODUCTS·상담이력(고객당 n행)은 list 로 담는다.
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
    """엑셀 셀 값을 JSON 친화형으로. 날짜는 ISO 문자열, 빈 문자열은 None."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _rows(ws) -> list[dict[str, Any]]:
    """1행 제목·2행 헤더 규약의 시트를 dict 행 목록으로 읽는다."""
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

    # 00_시연케이스가 고객 목록·순서의 원장이다.
    #
    # 같은 시트의 «시연 포인트» 열은 담지 않는다. 기획자가 "이 케이스로 무엇을 보여줄지"를
    # 적어둔 **연출 메모**이지 고객에 관한 사실이 아니다. 원장에 섞이면 에이전트가 읽는
    # 재료가 되고("현금성자산 장기 방치 + 디폴트옵션 미설정 고객"), 판정이 데이터에서
    # 나온 것인지 메모를 옮긴 것인지 구분되지 않는다 — 요건은 계좌 값에서 코드가 정한다
    # (CLAUDE.md 규칙 2). 시연 의도는 원본 xlsx 에 그대로 남아 있다.
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
