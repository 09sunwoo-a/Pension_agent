"""여의도종합금융센터 시연 고객(부점 0001) — 부점 브리핑 화면 기획안의 수치가 그대로 나오게 맞춘 가짜 고객.

전부 시연값이다(고정 시드). 목업 고객 12명(customers.json)은 그대로 들어가고, 나머지 1,380명을 생성해
아래 «화면 수치»가 부점 현황 계산(status.compute)에서 그대로 나오게 맞춘다. 맞추는 방법은 하나다 —
무작위로 뽑은 뒤 합계·가중평균이 목표에 닿도록 비례 조정하고, 남는 차이를 한 단위씩 나눠 얹는다.

화면 수치 밖의 속성(투자성향 · 원리금보장 비중 · 디폴트옵션 · 접촉 · 만기 · 세액공제 잔여한도 · 연금개시 ·
고유계정대 · ISA)은 «실제 부점이라면 이 정도»의 비율로 뽑는다(아래 «현실 비율» — 시연 가정값이고 행내
통계가 아니다). 관리 대상 수는 그 결과로 정해진다 — 정해 둔 인원에 맞추지 않는다(2026-10-22 결정 변경:
처음에는 화면의 «중점관리 56명»에 맞추려고 나머지 고객을 어떤 요건에도 안 걸리게 만들었는데, 그러면
디폴트옵션 미설정·현금성 장기대기 같은 고객군이 몇 명뿐이라 고객 검색 답이 비현실적이었다).

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
# ── 현실 비율(시연 가정값 — 행내 통계 아님) ────────────────────────────────
#: 투자성향 분포(가중치)
_RISK_MIX = (("안정형", 25), ("안정추구형", 25), ("위험중립형", 25), ("적극투자형", 15), ("공격투자형", 10))
#: 투자성향별 원리금보장 비중 — (100% 보유 비율, 나머지의 하한, 상한). 그 뒤 화면의 가중 69.3% 로 다시 맞춘다
_SAFE_BY_RISK = {"안정형": (0.55, 70, 99), "안정추구형": (0.35, 50, 99), "위험중립형": (0.20, 30, 95),
                 "적극투자형": (0.10, 10, 80), "공격투자형": (0.05, 0, 70)}
NO_DEFAULT_OPTION = 0.20           # 디폴트옵션 미설정
#: 마지막 접촉 후 경과일 구간과 비율 — 180일 이상(장기 미접촉)이 35%
_CONTACT = (((0, 89), 40), ((90, 179), 25), ((180, 364), 20), ((365, 700), 15))
#: 연금 미개시 고객의 올해 세액공제 잔여한도(만원)와 비율 — 아직 납입 안 함(900) · 일부 · 한도 채움(0)
_ROOM = (((900, 900), 40), ((300, 800), 25), ((100, 200), 15), ((0, 0), 20))
HAS_MATURITY = 0.70                # 원리금보장 상품이 있는 고객 중 1년 안 만기가 있는 비율
PENSION_ELIGIBLE = 0.80            # 만 55세 이상 중 연금개시 요건 충족
PENSION_STARTED = 0.30             # 요건 충족 고객 중 연금 개시
TRANSFER_INQUIRY = 0.02            # 최근 30일 계약이전 사전조회 신청
ISA_HOLDER = 0.015                 # ISA 만기(지난 60일 ~ 앞 120일) 자금 보유
#: 고유계정대 50% 이상(미운용 현금성자산)은 소액 계좌가 대부분이라고 둔다. 화면 6.7억에서 목업 고객 몫(약 5.9억)을
#: 뺀 나머지 안에서만 만들 수 있어 인원이 이 정도로 정해진다
IDLE_HEAVY_MAX = 45
IDLE_HEAVY_EVAL = (1_000_000, 6_000_000)
IDLE_HEAVY_SHARE = 0.8             # 나머지 고유계정대 중 50% 이상 고객 몫(나머지는 소액 보유 고객 150명)
IDLE_HEAVY_FLOOR = 0.7             # 50% 이상 고객의 하한 합이 그 몫의 이 비율까지 — 남는 몫으로 비중이 고르게 퍼진다
IDLE_LIGHT = 150
IDLE_LONG = 0.6                    # 50% 이상 고객 중 60일 이상 변동 없음(현금성 장기대기)

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
_INSTITUTIONS = ("증권", "증권", "증권", "은행", "보험")


def _pick(rnd: random.Random, table: tuple) -> int:
    """((하한, 상한), 가중치) 표에서 구간 하나를 뽑고 그 안의 정수."""
    lo, hi = rnd.choices([r for r, _ in table], [w for _, w in table])[0]
    return rnd.randint(lo, hi)


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
           *, bounds: dict[int, tuple[float, float]]) -> None:
    """가중평균을 target 에 붙인다 — 큰 계좌부터 unit 씩 움직인다(반올림 표기가 target 이 되게).

    bounds: 행 번호 → (하한, 상한). 그 구간 밖으로는 움직이지 않는다.
    """
    w = sum(r["eval_amt"] for r in rows)
    for i in sorted(movable, key=lambda i: -rows[i]["eval_amt"]):
        err = _weighted(rows, key) - target
        if abs(err) < unit / 4:
            return
        lo, hi = bounds[i]
        step = -round(err * w / rows[i]["eval_amt"] / unit) * unit
        rows[i][key] = round(min(hi, max(lo, rows[i][key] + step)), 2)
    if abs(_weighted(rows, key) - target) >= unit / 2:
        raise ValueError(f"{key} 가중평균을 {target} 에 맞추지 못했습니다")


def _names(rnd: random.Random, n: int) -> list[str]:
    """겹치지 않는 이름 n 개 — 명부를 이름으로 되짚으므로(첫 추천 목록) 부점 안에서 유일해야 한다."""
    taken = {p.nm for p in C.PERSONAS}
    out: list[str] = []
    while len(out) < n:
        nm = rnd.choice(_SURNAMES) + rnd.choice(_GIVEN)
        if nm not in taken:
            taken.add(nm)
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

    # 고객 속성 — «현실 비율»대로 뽑는다(관리 대상 수에 맞추지 않는다)
    rows: list[dict] = []
    for nm, ev in zip(_names(rnd, n), evals):
        age = rnd.randint(27, 74)
        eligible = age >= 55 and rnd.random() < PENSION_ELIGIBLE
        started = eligible and rnd.random() < PENSION_STARTED
        risk = rnd.choices([k for k, _ in _RISK_MIX], [w for _, w in _RISK_MIX])[0]
        full, lo, hi = _SAFE_BY_RISK[risk]
        safe = 100.0 if rnd.random() < full else rnd.uniform(lo, hi)
        mat = today + timedelta(days=rnd.randint(-15, 365)) if safe > 0 and rnd.random() < HAS_MATURITY else None
        isa = rnd.random() < ISA_HOLDER
        rows.append({
            "customer_name": nm, "age": age, "star_grade": rnd.choice(_GRADES),
            "risk_profile": risk, "branch_code": CODE,
            "manager_emp_no": rnd.choice(MANAGERS),
            "joined_date": (date(2012, 1, 1) + timedelta(days=rnd.randint(0, 4800))).isoformat(),
            "eval_amt": ev, "return_rate": 0.0, "safe_asset_pct": safe, "idle_cash_pct": 0.0,
            "default_option": "미설정" if rnd.random() < NO_DEFAULT_OPTION else "설정",
            "pension_started": "개시" if started else "미개시",
            "pension_eligible": "충족" if eligible else "미충족",
            "next_maturity_date": mat.isoformat() if mat else None,
            "next_maturity_amt": max(_STEP, min(round(ev * safe / 100 / _STEP) * _STEP,
                                                rnd.randint(5, 120) * 1_000_000)) if mat else None,
            "last_contact_days": _pick(rnd, _CONTACT), "target_reasons": "",
            "idle_cash_amt": 0, "idle_cash_days": 0,
            "tax_credit_room": 0 if started else _pick(rnd, _ROOM) * 10_000,
            "transfer_inquiry": "신청" if rnd.random() < TRANSFER_INQUIRY else "없음",
            "risk_mismatch": "일치",
            "isa_maturity_date": (today + timedelta(days=rnd.randint(-60, 120))).isoformat() if isa else None,
            "isa_maturity_amt": rnd.randint(10, 100) * 1_000_000 if isa else None,
        })

    # 고유계정대 — 화면 6.7억에서 목업 고객 몫을 뺀 나머지. 50% 이상(미운용) 소액 계좌 + 조금씩 가진 고객
    budget = IDLE_TOTAL - sum(p["idle_cash_amt"] for p in personas)
    pool = [i for i, r in enumerate(rows) if IDLE_HEAVY_EVAL[0] <= r["eval_amt"] <= IDLE_HEAVY_EVAL[1]]
    rnd.shuffle(pool)
    heavy: list[int] = []
    floor = 0
    for i in pool:
        lo_i = rows[i]["eval_amt"] * 51 // 100 // _STEP * _STEP
        if len(heavy) < IDLE_HEAVY_MAX and floor + lo_i <= budget * IDLE_HEAVY_SHARE * IDLE_HEAVY_FLOOR:
            heavy.append(i)
            floor += lo_i
    taken = set(heavy)
    light = rnd.sample([i for i, r in enumerate(rows) if i not in taken and r["eval_amt"] >= 5_000_000],
                       IDLE_LIGHT)
    # 두 무리를 따로 맞춘다 — 함께 비례 조정하면 50% 이상 고객이 전부 하한(51%)에 붙는다
    h_target = round(budget * IDLE_HEAVY_SHARE / _STEP) * _STEP
    heavy_amt = _fit([round(rows[i]["eval_amt"] * rnd.uniform(0.51, 0.98)) for i in heavy], h_target,
                     [rows[i]["eval_amt"] * 51 // 100 // _STEP * _STEP for i in heavy],
                     [rows[i]["eval_amt"] * 98 // 100 // _STEP * _STEP for i in heavy], rnd)
    light_amt = _fit([round(rows[i]["eval_amt"] * rnd.uniform(0.01, 0.05)) for i in light], budget - h_target,
                     [_STEP] * len(light),
                     [rows[i]["eval_amt"] * 10 // 100 // _STEP * _STEP for i in light], rnd)
    for i, a in zip(heavy + light, heavy_amt + light_amt):
        r = rows[i]
        r["idle_cash_amt"] = a
        r["idle_cash_pct"] = round(a / r["eval_amt"] * 100, 1)
        if i in taken:
            r["idle_cash_days"] = rnd.randint(60, 500) if rnd.random() < IDLE_LONG else rnd.randint(0, 59)
            r["safe_asset_pct"] = max(r["safe_asset_pct"], r["idle_cash_pct"])
        else:
            r["idle_cash_days"] = rnd.randint(0, 240)

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
           bounds={len(personas) + i: (LOW_RETURN + 0.1, 60.0) for i in base})

    # 원리금보장 비중 — 적립금 가중 69.3%. 100% 고객과 고유계정대 50% 이상 고객은 두고 나머지를 옮긴다
    free = [i for i, r in enumerate(rows) if r["safe_asset_pct"] < 100 and i not in taken]
    shift = (SAFE_W * TOTAL_EVAL - sum(r["eval_amt"] * r["safe_asset_pct"] for r in allrows)) / \
        sum(rows[i]["eval_amt"] for i in free)
    for i in free:
        rows[i]["safe_asset_pct"] = round(min(99.0, max(0.0, rows[i]["safe_asset_pct"] + shift)), 1)
    _nudge(allrows, [len(personas) + i for i in free], "safe_asset_pct", SAFE_W, 0.1,
           bounds={len(personas) + i: (0.0, 99.0) for i in free})
    for r in rows:
        r["safe_asset_pct"] = round(max(r["idle_cash_pct"], r["safe_asset_pct"]), 1)
        r["risk_mismatch"] = "불일치" if C.risk_mismatch(
            r["risk_profile"], 100 - round(r["safe_asset_pct"]), round(r["safe_asset_pct"])) else "일치"
        # 만기 상품(예금·GIC)은 원리금보장 몫 안에 있다 — 위에서 비중을 옮긴 뒤라 다시 맞춘다(2026-10-23 — 합성 고객
        # 상세 원장 demo_detail 이 이 행에서 보유상품을 만든다. 넘치면 만기 금액이 원리금보장 보유액보다 커진다)
        if r["next_maturity_date"]:
            room = round(r["eval_amt"] * r["safe_asset_pct"] / 100) - r["idle_cash_amt"]
            if room < _STEP:     # 원리금보장 몫이 없으면 만기 상품도 없다
                r["next_maturity_date"] = r["next_maturity_amt"] = None
            else:
                r["next_maturity_amt"] = min(r["next_maturity_amt"], room // _STEP * _STEP)

    # 이번 달 신규 계좌 — 전월말 표에는 없다
    since = max(1, (today - prev).days)
    new = rnd.sample([i for i, r in enumerate(rows) if r["eval_amt"] < 20_000_000 and i not in taken],
                     NEW_ACCOUNTS)
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
    movable = [i for i in range(len(personas), len(old)) if old[i]["return_rate"] >= LOW_RETURN + 0.1]
    _nudge(old, movable, "return_rate", RETURN_W - RETURN_DELTA, 0.01,
           bounds={i: (LOW_RETURN + 0.1, 60.0) for i in movable})
    synth = [r for r in old[len(personas):] if r["idle_cash_amt"]]
    extra = _fit([r["idle_cash_amt"] for r in synth], PREV_IDLE - sum(p["idle_cash_amt"] for p in personas),
                 [r["idle_cash_amt"] for r in synth],
                 [max(r["idle_cash_amt"], r["eval_amt"] * 45 // 100 // _STEP * _STEP) for r in synth],
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
