"""상품 질의와 적합성 게이트 2단 — 정적(위험등급·채널) / 금액(최소가입금액·위험자산 여력, 배분 후 판정)."""

from __future__ import annotations

from pension_agent.tools.customer_solution.customer import PREF, RISK, Profile
from pension_agent.tools.customer_solution.engine.catalog import BASELINES, PRODUCTS
from pension_agent.tools.customer_solution.engine.text import _ret_of, won


def query_products(rows: list[dict], where: dict | None = None) -> list[dict]:
    out = [r for r in rows if r.get("status", "판매중") == "판매중"]
    for k, v in (where or {}).items():
        out = [r for r in out if (r.get(k) in v if isinstance(v, list) else r.get(k) == v)]
    return out


def gate_static(row: dict, p: Profile, cap: str) -> tuple[bool, str]:
    """금액 무관 게이트 — 위험등급 상한·거래채널."""
    if RISK.index(row["risk"]) > RISK.index(cap):
        return False, f"상품 위험등급 {row['risk']} > 허용 상한 {cap}"
    if p.nonface and not row.get("nonface", True):
        return False, "비대면 채널 가입 불가 (고객 거래채널: 비대면)"
    return True, ""


def gate_amount(row: dict, p: Profile, amount: int, headroom: int | None) -> tuple[bool, str]:
    """금액 게이트. 최소가입금액은 적립금이 아니라 배분액과 대조."""
    ref = amount if amount > 0 else p.bal
    if row.get("min_amount", 0) > ref:
        return False, f"최소 가입금액 {won(row['min_amount'])} > 배분액 {won(ref)}"
    # 금액 미특정 전략도 최소 단위 가정으로 위험자산 한도 소진 판정
    need = amount if amount > 0 else 1
    if (
        headroom is not None
        and row.get("risk_asset")
        and not row.get("risk_asset_exempt")
        and need > headroom
    ):
        if amount > 0:
            return False, f"위험자산 한도 여력 {won(headroom)} < 배분액 {won(amount)}"
        return False, f"위험자산 한도 여력 {won(headroom)} — 신규 위험자산 편입 불가"
    return True, ""


def _branch_defs(spec: dict) -> dict[str, dict]:
    if "product_branch" in spec:
        return dict(spec["product_branch"])
    if "product" in spec:
        return {"단일": spec["product"]}
    return {}


def static_candidates(p: Profile, spec: dict, cap: str, blocked: dict) -> dict[str, list[dict]]:
    """금액 무관 게이트까지 통과한 분기별 후보 상품 목록."""
    out: dict[str, list[dict]] = {}
    for label, q in _branch_defs(spec).items():
        rows = []
        for r in query_products(PRODUCTS, q.get("where")):
            ok, why = gate_static(r, p, cap)
            if ok:
                rows.append(r)
            else:
                blocked.setdefault(f"{r['id']}|{why}", f"{r['name']} — {why}")
        out[label] = rows
    return out


def _best(p: Profile, spec: dict, q: dict, rows: list[dict]) -> dict:
    """성향 선호 우선, 수익률 순 1건. 정렬은 _ret_of() 기준(원시 exp_return 금지 — 원리금보장 최하위로 밀림)."""
    pref = PREF.get(p.rk) if spec.get("apply_preference", True) else None
    narrowed = [r for r in rows if RISK.index(r["risk"]) <= RISK.index(pref)] if pref else []
    pool = list(narrowed or rows)
    desc = str(q.get("order", "-exp_return")).startswith("-")
    pool.sort(key=lambda r: (_ret_of(r) if _ret_of(r) is not None else -1e9), reverse=desc)
    return pool[0]


def finalize_products(
    p: Profile, spec: dict, static: dict[str, list[dict]],
    amount: int, headroom: int | None, blocked: dict,
) -> tuple[dict[str, dict], list[str]]:
    """배분액 반영 분기별 상품 확정. 후보 0건 분기 제거."""
    got: dict[str, dict] = {}
    dead: list[str] = []
    base = None
    if spec.get("require_return_over_base") and (spec.get("impact") or {}).get("base_ref"):
        base = BASELINES[spec["impact"]["base_ref"]]["rate"]

    defs = _branch_defs(spec)
    for label, rows in static.items():
        passed = []
        for r in rows:
            ok, why = gate_amount(r, p, amount, headroom)
            if not ok:
                blocked.setdefault(f"{r['id']}|{why}", f"{r['name']} — {why}")
                continue
            ret = _ret_of(r)
            if base is not None and (ret is None or ret <= base):
                why = f"기준선 {base:.2f}% 이하 수익률 — 전환 실익 없음"
                blocked.setdefault(f"{r['id']}|{why}", f"{r['name']} — {why}")
                continue
            passed.append(r)
        if not passed:
            dead.append(f"{spec['title']} · {label} 분기 — 조건 충족 상품 0건")
            continue
        got[label] = _best(p, spec, defs[label], passed)
    return got, dead
