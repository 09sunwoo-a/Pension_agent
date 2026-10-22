"""여의도종합금융센터 시연 고객(부점 0001) — 부점 브리핑 화면 기획안의 수치가 그대로 나오게 맞춘 가짜 고객.

전부 시연값이다(고정 시드). 목업 고객 12명(customers.json)은 그대로 들어가고, 나머지 1,380명을 생성해
아래 «화면 수치»가 부점 현황 계산(status.compute)에서 그대로 나오게 맞춘다. 맞추는 방법은 하나다 —
무작위로 뽑은 뒤 합계·가중평균이 목표에 닿도록 비례 조정하고, 남는 차이를 한 단위씩 나눠 얹는다.

전월말 스냅샷도 화면의 증감 화살표(▲1.3억 · ▲0.12%p · ▼0.4억)가 나오게 되짚는다. 그 결과 여의도에서 성립하는
관리 방향은 CASE 3(잔액 증가 + 세액공제 여유 고객 증가) 하나다 — 화면 브리핑 문구(추가납입·세제혜택)와 같다.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from pension_agent.common import clock
from pension_agent.tools.customer_solution import customer as C

CODE = "0001"
NAME = "여의도종합금융센터"
#: 부점 직원(관리 직원 사번 후보). 3902172 는 문서·테스트용 개발 사번.
MANAGERS = ("3902172", "3902173")

# ── 화면 수치(부점 브리핑 화면 기획안, 2026-10-22) ─────────────────────────────
TOTAL_CUSTOMERS = 1_392            # 우리부점 관리 고객
TOTAL_EVAL = 52_680_000_000        # 부점 잔액 526.8억
HIGH = 100_000_000                 # 적립금 1억 이상
HIGH_COUNT = 120                   # 적립금 1억 이상 고객 120명
HIGH_SHARE = 0.453                 # 그 고객의 적립금 비중 45.3%
LOW_RETURN = 1.0                   # 수익률 저조 = 1년 수익률 1% 미만
LOW_COUNT = 210                    # 수익률 저조 고객 210명
RETURN_W = 18.96                   # 부점 수익률(적립금 가중) 18.96%
SAFE_W = 69.3                      # 원리금보장 비중(적립금 가중) 69.3%
IDLE_TOTAL = 670_000_000           # 고유계정대 잔액 6.7억
TRANSFER_COUNT = 89                # 올해 계약이전 89좌
TRANSFER_AMT = 6_280_000_000       # 올해 계약이전 62.8억
# ── 전월말 대비(화면의 증감 화살표) ──────────────────────────────────────────
PREV_EVAL = TOTAL_EVAL - 130_000_000     # ▲1.3억
RETURN_DELTA = 0.12                      # ▲0.12%p
PREV_IDLE = IDLE_TOTAL + 40_000_000      # ▼0.4억
NEW_ACCOUNTS = 3                         # 이번 달 신규 — 계좌 수는 늘었다(CASE 1·5 미성립)
TAX_ROOM_NEW = 8                         # 이번 달 세액공제 여유가 생긴 고객 — CASE 3 성립(BP-01 5명 이상)
TRANSFER_PREV_SHARE = 0.9                # 올해 계약이전 중 전월말까지의 몫

_SEED = 20261022
_STEP = 10_000                     # 금액 단위(원)

_SURNAMES = "김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허유남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"
_GIVEN = ("민준", "서연", "도윤", "하은", "지호", "수아", "예준", "지유", "현우", "채원",
          "건우", "소율", "우진", "다은", "선우", "하린", "연우", "윤서", "정우", "가은",
          "시우", "지민", "주원", "서윤", "하준", "예린", "유준", "수빈", "은우", "지안",
          "준서", "민서", "태윤", "나연", "도현", "예은", "재원", "서현", "성민", "유진",
          "영호", "미경", "정훈", "은정", "상철", "혜진", "동현", "수진", "광수", "경희",
          "종민", "미영", "재훈", "선희", "병철", "현숙", "기태", "순자", "명수", "영자")
_GRADES = ("패밀리", "베스트", "그랜드", "로얄", "MVP")
_RISKS = ("안정형", "안정추구형", "위험중립형", "적극투자형", "공격투자형")
_REASONS = ("디폴트옵션 미설정", "원리금보장상품 편중(80% 이상)", "장기 미접촉",
            "만기예금 보유", "미운용 현금성자산", "연금개시 요건충족 후 미개시",
            "수익률 하위 30%", "운용변경 없음(12개월+)")
_INSTITUTIONS = ("증권", "증권", "증권", "은행", "보험")


def _fit(vals: list[int], target: int, lo: list[int], hi: list[int], rnd: random.Random) -> list[int]:
    """비례 조정 후 남는 차이를 _STEP 단위로 나눠 얹어 합계를 target 에 정확히 맞춘다(구간 [lo, hi] 유지)."""
    k = target / sum(vals)
    out = [min(h, max(l, round(v * k / _STEP) * _STEP)) for v, l, h in zip(vals, lo, hi)]
    rest = target - sum(out)
    idx = list(range(len(out)))
    while rest:
        rnd.shuffle(idx)
        moved = False
        for i in idx:
            if not rest:
                break
            room = (hi[i] - out[i]) if rest > 0 else (out[i] - lo[i])
            d = min(room, abs(rest))
            d -= d % _STEP
            if d <= 0:
                continue
            out[i] += d if rest > 0 else -d
            rest += -d if rest > 0 else d
            moved = True
        if not moved:
            raise ValueError(f"합계 {target} 를 구간 안에서 맞출 수 없습니다")
    return out


def _weighted(rows: list[dict], key: str) -> float:
    w = sum(r["eval_amt"] for r in rows)
    return sum(r["eval_amt"] * r[key] for r in rows) / w


def _nudge(rows: list[dict], movable: list[int], key: str, target: float, unit: float,
           lo: float, hi: float) -> None:
    """가중평균을 target 에 붙인다 — 큰 계좌부터 unit 씩 움직인다(반올림 표기가 target 이 되게)."""
    w = sum(r["eval_amt"] for r in rows)
    for i in sorted(movable, key=lambda i: -rows[i]["eval_amt"]):
        err = _weighted(rows, key) - target
        if abs(err) < unit / 4:
            return
        step = -round(err * w / rows[i]["eval_amt"] / unit) * unit
        rows[i][key] = round(min(hi, max(lo, rows[i][key] + step)), 2)
    if abs(_weighted(rows, key) - target) >= unit / 2:
        raise ValueError(f"{key} 가중평균을 {target} 에 맞추지 못했습니다")


def _names(rnd: random.Random, n: int) -> list[str]:
    taken = {p.nm for p in C.PERSONAS}
    out = []
    while len(out) < n:
        nm = rnd.choice(_SURNAMES) + rnd.choice(_GIVEN)
        if nm not in taken:
            out.append(nm)
    return out


def build(personas: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """목업 고객(dict 행) → (오늘 고객 행, 전월말 고객 행, 계약이전 행). 전부 부점 0001."""
    rnd = random.Random(_SEED)
    today, prev = clock.today(), clock.prev_month_end()
    n = TOTAL_CUSTOMERS - len(personas)
    p_big = [p for p in personas if p["eval_amt"] >= HIGH]
    n_big = HIGH_COUNT - len(p_big)
    big_target = round(TOTAL_EVAL * HIGH_SHARE / _STEP) * _STEP - sum(p["eval_amt"] for p in p_big)
    small_target = TOTAL_EVAL - sum(p["eval_amt"] for p in personas) - big_target

    # 잔액 — 1억 이상 / 미만을 따로 맞춘다
    # 무작위 기준값을 목표 평균 둘레로 뽑는다 — 평균이 어긋나면 비례 조정이 많은 계좌를 경계(1억)에 붙인다
    excess = big_target / n_big - HIGH
    big = _fit([round(HIGH + min(550_000_000, rnd.expovariate(1 / excess))) for _ in range(n_big)], big_target,
               [HIGH] * n_big, [700_000_000] * n_big, rnd)
    n_small = n - n_big
    mean = small_target / n_small - 2_000_000
    small = _fit([round(2_000_000 + min(90_000_000, rnd.expovariate(1 / mean)))
                  for _ in range(n_small)], small_target,
                 [1_000_000] * n_small, [HIGH - _STEP] * n_small, rnd)
    evals = big + small
    rnd.shuffle(evals)

    rows: list[dict] = []
    for nm, ev in zip(_names(rnd, n), evals):
        age = rnd.randint(27, 74)
        eligible = age >= 55 and rnd.random() < 0.8
        started = eligible and rnd.random() < 0.4
        mat = today + timedelta(days=rnd.randint(-20, 200)) if rnd.random() < 0.5 else None
        reasons = sorted(rnd.sample(_REASONS, k=rnd.choice((0, 1, 1, 2, 3))))
        dopt = "미설정" if "디폴트옵션 미설정" in reasons else rnd.choice(("설정", "설정", "미설정"))
        if dopt == "미설정" and "디폴트옵션 미설정" not in reasons:
            reasons = sorted([*reasons, "디폴트옵션 미설정"])
        rows.append({
            "customer_name": nm, "age": age, "star_grade": rnd.choice(_GRADES),
            "risk_profile": rnd.choice(_RISKS), "branch_code": CODE,
            "manager_emp_no": rnd.choice(MANAGERS),
            "joined_date": (date(2012, 1, 1) + timedelta(days=rnd.randint(0, 4800))).isoformat(),
            "eval_amt": ev, "return_rate": 0.0, "safe_asset_pct": rnd.uniform(30, 100),
            "idle_cash_pct": 0.0,
            "default_option": dopt, "pension_started": "개시" if started else "미개시",
            "pension_eligible": "충족" if eligible else "미충족",
            "next_maturity_date": mat.isoformat() if mat else None,
            "next_maturity_amt": min(ev, rnd.randint(5, 120) * 1_000_000) if mat else None,
            "last_contact_days": rnd.randint(0, 400), "target_reasons": ", ".join(reasons),
            "idle_cash_amt": 0, "idle_cash_days": 0,
            "tax_credit_room": 0 if started else rnd.choice((0, 0, 0, 1, 3, 5, 9)) * 1_000_000,
            "transfer_inquiry": "신청" if rnd.random() < 0.02 else "없음",
            "risk_mismatch": "일치", "isa_maturity_date": None, "isa_maturity_amt": None,
        })

    # 고유계정대 — 목업 고객 몫을 뺀 나머지를 생성 고객 일부에 나눈다(비중 30% 이하)
    holders = rnd.sample([i for i, r in enumerate(rows) if r["eval_amt"] >= 5_000_000], 160)
    idle_target = IDLE_TOTAL - sum(p["idle_cash_amt"] for p in personas)
    amounts = _fit([rnd.randint(1, 10) for _ in holders], idle_target, [_STEP] * len(holders),
                   [rows[i]["eval_amt"] * 3 // 10 // _STEP * _STEP for i in holders], rnd)
    for i, a in zip(holders, amounts):
        rows[i]["idle_cash_amt"], rows[i]["idle_cash_days"] = a, rnd.randint(0, 240)
        rows[i]["idle_cash_pct"] = round(a / rows[i]["eval_amt"] * 100, 1)

    # 수익률 — 저조 고객 수를 먼저 정하고, 나머지로 적립금 가중 수익률을 맞춘다
    allrows = personas + rows
    low = set(rnd.sample(range(n), LOW_COUNT - sum(1 for p in personas if p["return_rate"] < LOW_RETURN)))
    base = {i: rnd.uniform(2.0, 36.0) for i in range(n) if i not in low}
    for i in low:
        rows[i]["return_rate"] = round(rnd.uniform(-8.0, 0.9), 2)
    fixed = sum(r["eval_amt"] * r["return_rate"] for r in personas) + \
        sum(rows[i]["eval_amt"] * rows[i]["return_rate"] for i in low)
    wh = sum(rows[i]["eval_amt"] for i in base)
    shift = (RETURN_W * TOTAL_EVAL - fixed - sum(rows[i]["eval_amt"] * b for i, b in base.items())) / wh
    for i, b in base.items():
        rows[i]["return_rate"] = round(max(LOW_RETURN + 0.1, b + shift), 2)
    _nudge(allrows, [len(personas) + i for i in base], "return_rate", RETURN_W, 0.01,
           LOW_RETURN + 0.1, 60.0)

    # 원리금보장 비중 — 적립금 가중 69.3%. 고유계정대 비중보다 작아지지 않게
    shift = (SAFE_W * TOTAL_EVAL - sum(r["eval_amt"] * r["safe_asset_pct"] for r in allrows)) / \
        sum(r["eval_amt"] for r in rows)
    for r in rows:
        r["safe_asset_pct"] = round(min(100.0, max(r["idle_cash_pct"], r["safe_asset_pct"] + shift)), 1)
    _nudge(allrows, list(range(len(personas), len(allrows))), "safe_asset_pct", SAFE_W, 0.1, 0.0, 100.0)
    for r in rows:
        r["safe_asset_pct"] = round(max(r["idle_cash_pct"], r["safe_asset_pct"]), 1)
        r["risk_mismatch"] = "불일치" if C.risk_mismatch(
            r["risk_profile"], 100 - r["safe_asset_pct"], r["safe_asset_pct"]) else "일치"

    # 이번 달 신규 계좌 — 전월말 표에는 없다
    since = max(1, (today - prev).days)
    new = rnd.sample([i for i, r in enumerate(rows) if r["eval_amt"] < 20_000_000], NEW_ACCOUNTS)
    for i in new:
        rows[i]["joined_date"] = (prev + timedelta(days=rnd.randint(1, since))).isoformat()

    return allrows, _prev(personas, rows, set(new), rnd, today, prev), _transfers(rnd, today)


def _prev(personas: list[dict], rows: list[dict], new: set[int], rnd: random.Random,
          today: date, prev: date) -> list[dict]:
    """전월말 고객 행 — 신규 계좌 제외 · 잔액 ▼1.3억 · 수익률 −0.12%p · 고유계정대 +0.4억 · 세액공제 여유 −8명."""
    old = [dict(r) for r in personas] + [dict(r) for i, r in enumerate(rows) if i not in new]
    evals = _fit([r["eval_amt"] for r in old], PREV_EVAL, [_STEP] * len(old),
                 [r["eval_amt"] * 2 for r in old], rnd)
    for r, ev in zip(old, evals):
        r["eval_amt"] = ev
        r["return_rate"] = round(r["return_rate"] - RETURN_DELTA, 2)
        mat = r["next_maturity_date"]
        if mat:
            # 남은 일수가 전월말 기준으로도 같게 — 만기 고객 수는 그대로
            r["next_maturity_date"] = (prev + (date.fromisoformat(mat) - today)).isoformat()
    # 수익률 −0.12%p 를 적립금 가중으로 맞춘다(신규 계좌가 빠지고 잔액이 바뀌어 단순 차감은 0.01 어긋난다)
    _nudge(old, [i for i in range(len(personas), len(old)) if old[i]["return_rate"] >= LOW_RETURN + 0.1],
           "return_rate", RETURN_W - RETURN_DELTA, 0.01, LOW_RETURN + 0.1, 60.0)
    synth = [r for r in old[len(personas):] if r["idle_cash_amt"]]
    extra = _fit([r["idle_cash_amt"] for r in synth], PREV_IDLE - sum(p["idle_cash_amt"] for p in personas),
                 [r["idle_cash_amt"] for r in synth], [r["eval_amt"] * 45 // 100 // _STEP * _STEP for r in synth],
                 rnd)
    for r, a in zip(synth, extra):
        r["idle_cash_amt"], r["idle_cash_pct"] = a, round(a / r["eval_amt"] * 100, 1)
        r["safe_asset_pct"] = max(r["safe_asset_pct"], r["idle_cash_pct"])
    roomy = [r for r in old[len(personas):]
             if r["tax_credit_room"] >= 3_000_000 and r["pension_started"] == "미개시"]
    for r in rnd.sample(roomy, TAX_ROOM_NEW):
        r["tax_credit_room"] = 0
    return old


def _transfers(rnd: random.Random, today: date) -> list[tuple]:
    """올해 계약이전(타사 유출) 89좌 · 62.8억. 전월말까지가 그중 TRANSFER_PREV_SHARE."""
    prev = clock.prev_month_end()
    start = date(today.year, 1, 1)
    n_prev = round(TRANSFER_COUNT * TRANSFER_PREV_SHARE) if prev.year == today.year else 0
    span_prev = max(0, (prev - start).days)
    days = sorted([rnd.randint(0, span_prev) for _ in range(n_prev)]) + \
        sorted(rnd.randint(span_prev + 1, max(span_prev + 1, (today - start).days))
               for _ in range(TRANSFER_COUNT - n_prev))
    amts = _fit([rnd.randint(10, 200) for _ in days], TRANSFER_AMT, [5_000_000] * len(days),
                [400_000_000] * len(days), rnd)
    names = _names(rnd, len(days))
    return [(nm, CODE, (start + timedelta(days=d)).isoformat(), a, rnd.choice(_INSTITUTIONS))
            for nm, d, a in zip(names, days, amts)]
