"""금액 · 시급성 · 기대효과 · 점수 — 어떤 제안을 앞세울지 정하는 수치 계층.

전부 산출식 기반이라 같은 입력이면 같은 결과가 나온다. 기대효과를 원화가 아니라 등급
(큼·보통·작음)으로만 표기하는 이유는 catalog.EFFECT_BANDS 주석 참고 — 원화 환산은 원금
이동 가정에 의존해 근거가 불명확하다.
"""

from __future__ import annotations

import math

from pension_agent.strategy_agent.customer import (
    RISK_ASSET_CAP_PCT,
    Profile,
    days_to_year_end,
)
from pension_agent.strategy_agent.engine.catalog import BASELINES, EFFECT_BANDS
from pension_agent.strategy_agent.engine.text import _ret_of, won


# ─────────────────────────────────────────────────────────────

def _amount(p: Profile, spec: dict) -> tuple[int, dict]:
    """전략별 대상 금액(원)과 절 렌더링용 파생값을 산출한다."""
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
    """시급성(0~10). 기한이 정의된 전략은 잔여일수에 따라 감쇠한다."""
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
    """연간 기대효과(원), 미확정 시의 범위, 산식을 반환한다.

    분기가 둘 이상이면 실적배당의 기대수익률과 원리금보장의 확정금리가 함께 존재한다.
    성격이 다른 두 수치를 하나로 합치지 않고, 고객이 분기를 선택하기 전까지는 범위로만
    제시하며 기대효과 합계에 산입하지 않는다.
    """
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
    """선정 점수. 고정 우선순위가 아니라 시급성·기대효과·필수여부의 합으로 산출한다.

    기대효과가 범위로만 확정된 경우 하한을 사용해 보수적으로 채점한다.
    """
    s = urg * 10
    v = impact if impact is not None else (rng[0] if rng else None)
    if v and v > 0:
        s += min(40.0, max(0.0, 10 * (math.log10(v) - 4)))  # 10만원=0점, 1천만원=20점
    if mandatory:
        s += 100
    return round(s, 1)


def _yield_delta(spec: dict, products: dict) -> float | None:
    """제안 상품의 수익률 개선폭(%p) = max(상품 수익률 − 기준선).

    원리금보장은 확정금리, 실적배당은 기대수익률을 사용한다. 세액공제처럼 수익률 축이 없거나
    기준선이 없는 전략은 None 을 반환한다. 대안 제안의 정렬 기준이자 정성 등급의 산출값이다.
    """
    im = spec.get("impact") or {}
    if not im or im.get("kind") == "tax_credit" or not im.get("base_ref"):
        return None
    base = BASELINES[im["base_ref"]]["rate"]
    deltas = [_ret_of(r) - base for r in products.values() if _ret_of(r) is not None]
    return max(deltas) if deltas else None


def _effect_grade(delta: float | None) -> str:
    """수익률 개선폭(%p)을 정성 등급으로 환산한다. 수익률 축이 없으면 '—'."""
    if delta is None:
        return "—"
    for thr, label in EFFECT_BANDS:
        if delta >= thr:
            return label
    return EFFECT_BANDS[-1][1]


def effect_label(grade: str) -> str:
    """수익률 개선폭 등급을 사람이 읽는 라벨로 바꾼다.

    '큼/보통/작음' 은 상품금리와 기준선의 차(개선폭) 등급이고, '—' 는 수익률 축이 없는
    전략(접촉·재진단·디폴트옵션 등)이다. 산출물에는 등급 문자 대신 이 라벨을 노출한다.
    """
    return {
        "큼": "수익 개선폭 큼",
        "보통": "수익 개선폭 보통",
        "작음": "수익 개선폭 작음",
        "—": "수익 개선 해당 없음",
    }.get(grade, grade)


def _value_tag(spec: dict, effect_grade: str) -> str:
    """카드용 핵심가치 라벨. 전략의 성격(유형·위험 조정 여부·기대효과)에서 분류한다.

    _effect_grade 와 같은 성격의 파생 라벨이다. 개별 전략에 문구를 하드코딩하지 않고
    구조적 신호에서 도출하므로, 신규 전략에도 규칙만으로 적용된다.
    """
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
    """전략 제안 카드. 긴 지시 문장 대신 헤드라인·핵심가치·추천상품/대상·한 줄 혜택으로
    압축한다. 상품·금액은 확정된 재료에서만 채워 환각을 차단하고, 분기별 상세는 clause 로 남긴다.

    상품이 있는 전략은 '추천 상품 · 대상' 으로, 상품이 없는 접촉·안내 전략은 행동(action)으로
    구체를 채운다. benefit 은 '왜 이로운가' 한 문장으로, 판단근거(evidence)의 수치 나열과 구분한다.
    """
    product = ", ".join(products.values())
    return {
        "headline": spec["title"],
        "tag": _value_tag(spec, effect_grade),
        "product": product,
        "target": amount or "",
        "action": "" if product else action,
        "benefit": benefit,
    }
