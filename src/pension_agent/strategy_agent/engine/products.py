"""상품 질의와 적합성 게이트.

게이트가 두 단계로 나뉘어 있는 것은 의도다. 금액에 의존하지 않는 조건(위험등급·거래채널)은
후보를 모을 때 바로 걸러내지만, 최소가입금액·위험자산 한도 여력은 **자금 배분이 끝난 뒤**에
판정한다. 적립금 기준으로 미리 판정하면 실제 투입액이 최소가입금액에 미달하는 상품을
제안하게 된다.
"""

from __future__ import annotations

from pension_agent.strategy_agent.customer import PREF, RISK, Profile
from pension_agent.strategy_agent.engine.catalog import BASELINES, PRODUCTS
from pension_agent.strategy_agent.engine.text import _ret_of, won


# 검색은 단건 조회는 가능하나 집합 질의(전부·개수·정렬)를 처리하지 못한다.
# ─────────────────────────────────────────────────────────────

def query_products(rows: list[dict], where: dict | None = None) -> list[dict]:
    out = [r for r in rows if r.get("status", "판매중") == "판매중"]
    for k, v in (where or {}).items():
        out = [r for r in out if (r.get(k) in v if isinstance(v, list) else r.get(k) == v)]
    return out


def gate_static(row: dict, p: Profile, cap: str) -> tuple[bool, str]:
    """금액과 무관한 적합성 게이트. 위험등급 상한과 거래채널을 판정한다."""
    if RISK.index(row["risk"]) > RISK.index(cap):
        return False, f"상품 위험등급 {row['risk']} > 허용 상한 {cap}"
    if p.nonface and not row.get("nonface", True):
        return False, "비대면 채널 가입 불가 (고객 거래채널: 비대면)"
    return True, ""


def gate_amount(row: dict, p: Profile, amount: int, headroom: int | None) -> tuple[bool, str]:
    """실제 투입액에 의존하는 적합성 게이트.

    최소가입금액은 적립금이 아니라 해당 전략에 배분된 금액과 대조해야 한다. 적립금 기준으로
    판정하면 4,680만원을 배분하면서 최소 5,000만원 상품을 제안하는 오류가 발생한다.
    """
    ref = amount if amount > 0 else p.bal
    if row.get("min_amount", 0) > ref:
        return False, f"최소 가입금액 {won(row['min_amount'])} > 배분액 {won(ref)}"
    # 금액이 특정되지 않는 전략(예: 디폴트옵션 설정)도 여력이 없으면 위험자산 상품을
    # 지정할 수 없다. 이 경우 최소 단위를 가정해 한도 소진 여부만 판정한다.
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
    """성향 선호를 우선 적용한 뒤 수익률 순으로 1건을 선정한다.

    정렬 기준은 원시 필드가 아니라 _ret_of() 이다. 원시 필드로 정렬하면 확정금리만 보유한
    원리금보장 상품이 exp_return 부재로 최하위로 밀린다.
    """
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
    """배분액을 반영해 분기별 상품을 확정한다. 후보 0건인 분기는 제거된다."""
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
