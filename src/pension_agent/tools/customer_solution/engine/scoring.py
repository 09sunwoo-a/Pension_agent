"""금액·시급성·기대효과·점수 — 제안 우선순위 수치 계층(결정론). 기대효과는 등급으로만 표기."""

from __future__ import annotations

import math

from pension_agent.tools.customer_solution.customer import (
    RISK_ASSET_CAP_PCT,
    Profile,
    days_to_year_end,
)
from pension_agent.tools.customer_solution.engine.catalog import BASELINES, EFFECT_BANDS
from pension_agent.tools.customer_solution.engine.text import _ret_of, won


def _amount(p: Profile, spec: dict) -> tuple[int, dict]:
    """전략별 대상 금액(원)과 절 렌더링용 파생값."""
    a = spec.get("amount")
    if not a:
        return 0, {}
    k = a["kind"]
    if k == "mat":
        return p.matAmt, {}
    if k == "room":
        v = p.room * 10_000
        if a.get("credit_limit"):
            v = min(v, max(0, a["credit_limit"] - p.pension_paid_ytd))
        if a.get("contribution_limit"):
            v = min(v, max(0, a["contribution_limit"] - p.pension_paid_ytd))
        return v, {}
    if k == "dep_shift":
        move = max(0, min(a["cap_pct"], p.port[0] - a["floor_pct"]))
        return round(p.bal * move / 100), {"move_pct": move}
    if k == "sector_excess":
        ex = max(0, p.port[3] - a["target"])
        return round(p.bal * ex / 100), {"sec_excess": ex, "target": a["target"]}
    if k == "risk_excess":
        t = a["targets"].get(p.rk, 30)
        return round(p.bal * max(0, p.risk_asset - t) / 100), {"target": t}
    if k == "risk_over_limit":
        ex = max(0, p.risk_asset - RISK_ASSET_CAP_PCT)
        return round(p.bal * ex / 100), {"target": RISK_ASSET_CAP_PCT}
    return 0, {}


def _urgency(p: Profile, spec: dict) -> float:
    """시급성(0~10). 기한 전략은 잔여일수에 따라 감쇠."""
    u = spec.get("urgency", 0)
    if isinstance(u, (int, float)):
        return float(u)
    if u["kind"] == "deadline":
        v = getattr(p, u["field"]) or 0
    elif u["kind"] == "year_end":
        v = days_to_year_end()
    else:
        return 0.0
    return round(max(u["lo"], min(u["hi"], 10 - v / u["div"])), 1)


def _impact(
    p: Profile, spec: dict, amount: int, products: dict,
) -> tuple[int | None, tuple[int, int] | None, str]:
    """연간 기대효과(원)·범위·산식. 다분기는 범위로만(합계 미산입)."""
    im = spec.get("impact")
    if not im or amount <= 0:
        return None, None, ""
    if im["kind"] == "tax_credit":
        rate = p.tax_credit_rate
        return round(amount * rate), None, f"{won(amount)} × {rate:.1%}"

    base = BASELINES[im["base_ref"]]["rate"]
    vals = []
    for label, r in products.items():
        ret = _ret_of(r)
        if ret is not None:
            vals.append((label, round(amount * (ret - base) / 100), ret))
    if not vals:
        return None, None, ""
    if len(vals) == 1:
        return vals[0][1], None, f"{won(amount)} × ({vals[0][2]:.2f}% − {base:.2f}%)"
    lo = min(v[1] for v in vals)
    hi = max(v[1] for v in vals)
    detail = " / ".join(f"{lb} {rt:.2f}%" for lb, _, rt in vals)
    return None, (lo, hi), f"{won(amount)} × (분기별 수익률 − {base:.2f}%) — {detail}"


def _score(urg: float, impact: int | None, rng: tuple[int, int] | None, mandatory: bool) -> float:
    """선정 점수 = 시급성 + 기대효과(범위면 하한) + 필수여부."""
    s = urg * 10
    v = impact if impact is not None else (rng[0] if rng else None)
    if v and v > 0:
        s += min(40.0, max(0.0, 10 * (math.log10(v) - 4)))  # 10만원=0점, 1천만원=20점
    if mandatory:
        s += 100
    return round(s, 1)


def _yield_delta(spec: dict, products: dict) -> float | None:
    """수익률 개선폭(%p) = max(상품 수익률 − 기준선). 수익률 축·기준선 없으면 None."""
    im = spec.get("impact") or {}
    if not im or im.get("kind") == "tax_credit" or not im.get("base_ref"):
        return None
    base = BASELINES[im["base_ref"]]["rate"]
    deltas = [_ret_of(r) - base for r in products.values() if _ret_of(r) is not None]
    return max(deltas) if deltas else None


def _effect_grade(delta: float | None) -> str:
    """개선폭 → 정성 등급. 없으면 '—'."""
    if delta is None:
        return "—"
    for thr, label in EFFECT_BANDS:
        if delta >= thr:
            return label
    return EFFECT_BANDS[-1][1]


def effect_label(grade: str) -> str:
    """개선폭 등급 → 표시 라벨."""
    return {
        "큼": "수익 개선폭 큼",
        "보통": "수익 개선폭 보통",
        "작음": "수익 개선폭 작음",
        "—": "수익 개선 해당 없음",
    }.get(grade, grade)


def _value_tag(spec: dict, effect_grade: str) -> str:
    """카드 핵심가치 라벨 — 전략 유형·위험 조정·기대효과에서 분류."""
    kind = spec.get("kind")
    if kind == "접촉":
        return "관계 관리"
    if spec.get("reduces_risk"):
        return "위험 조정"
    if (spec.get("impact") or {}).get("kind") == "tax_credit":
        return "세제 혜택"
    if kind == "설정":
        return "운용 공백 차단"
    if kind == "납입":
        return "노후 자산 적립"
    if effect_grade in ("큼", "보통", "작음"):
        return "수익률 개선"
    return "운용 점검"


def _card(spec: dict, products: dict[str, str], amount: str | None,
          effect_grade: str, action: str, benefit: str) -> dict:
    """전략 제안 카드 — 상품·금액은 확정 재료에서만, 상품 없는 전략은 action 으로."""
    product = ", ".join(products.values())
    return {
        "headline": spec["title"],
        "tag": _value_tag(spec, effect_grade),
        "product": product,
        "target": amount or "",
        "action": "" if product else action,
        "benefit": benefit,
    }
