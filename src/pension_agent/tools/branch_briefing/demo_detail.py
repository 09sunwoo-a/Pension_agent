"""합성 고객의 상세 원장(시연값) — 부점 목업 DB 한 줄에서 customers.json 꼴 레코드를 만든다.

목록(관리 대상 추천·고객 검색)에서 고른 합성 고객도 시연 고객 12명처럼 상세 질문(보유상품·만기·자산 구성·
납입·연금 요건)에 답하게 하려는 것이다(2026-10-23 사용자 결정). 레코드는 시연 고객과 같은 매핑
(`customer.from_record`)을 거쳐 Profile 이 되므로 브리핑·요건 판정 코드는 그대로다.

지키는 것
- **DB 한 줄의 값과 같은 숫자.** 평가금액 · 1년 수익률 · 원리금보장 비중 · 고유계정대 · ETF · 만기(1년 안 최근접
  1건의 날짜·금액·유형) · 세액공제 잔여한도 · 마지막 접촉 · 디폴트옵션 · 연금 요건 · ISA · 연금저축이 행 그대로다.
- **목록과 같은 관리 사유.** 요건 판정(`customer.conditions`)이 목록의 판정(`roster.row_profile`)과 같은 결과를
  내도록 만든다 — 채권형 펀드를 두지 않고(4분류 둘째 칸), 판매중단 펀드를 두지 않으며, 운용지시는 5개월 안이다.
  1,392명 전원을 테스트가 대조한다(`tests.test_branch::check_demo_detail`).
- **같은 고객이면 같은 값.** 난수 시드가 내부 행 식별자다. 실행할 때마다 바뀌지 않는다.
- **상품명은 시연 고객 원장에 쓰인 이름만.** 답변 검증기의 상품 등록부 밖 이름을 만들지 않는다.

만들지 않는 것 — 과거 상담 기록(상담이력은 «기록 없음»으로 답한다 · 지어낸 상담을 «지난 상담»으로 세우지 않는다).
실데이터로 바꾸면 이 모듈은 행내 원장 조회로 대체된다(`customer.DETAIL_SOURCES` 에 다른 공급자를 등록).
"""

from __future__ import annotations

import random
import re
from dataclasses import replace
from datetime import date, timedelta

from pension_agent.common import clock
from pension_agent.tools.customer_solution import customer as C

#: 합성 고객의 내부 행 식별자(부점코드:행번호). 시연 고객은 자기 원장이 있어 여기로 오지 않는다.
_REF = re.compile(r"^(\d{4}):(\d{1,6})$")
_STEP = 10_000
_BOND_GRADES = ("매우낮은위험", "낮은위험")


def _pool(kind: str) -> list[dict]:
    """시연 고객 원장의 상품(이름·위험등급) — 판매중단·채권형 제외(목록의 판정과 같게)."""
    seen: dict[str, dict] = {}
    for p in C.PERSONAS:
        for h in p.holdings:
            if h["type"] == kind and not h.get("discontinued") and h.get("grade") not in (
                    _BOND_GRADES if kind == "수익증권" else ()):
                seen.setdefault(h["name"], h)
    return list(seen.values())


def _won(x: float) -> int:
    return int(round(x / _STEP)) * _STEP


def _split(total: int, parts: int, rnd: random.Random) -> list[int]:
    """total 을 parts 개로(만원 단위, 합 보존)."""
    if parts <= 1 or total < _STEP * parts:
        return [total]
    cuts = sorted(_won(total * rnd.uniform(0.25, 0.75)) for _ in range(parts - 1))
    out, prev = [], 0
    for c in cuts + [total]:
        out.append(c - prev)
        prev = c
    return [a for a in out if a > 0]


def _product(name: str, kind: str, amount: int, grade: str, opened: date, *, matures: date | None = None,
             rate: float | None = None, ret_1y: float | None = None, ret_own: float | None = None,
             bal: int) -> dict:
    return {"상품명": name, "상품유형": kind,
            "매입원금잔액": amount if ret_own is None else _won(amount / (1 + ret_own)) or amount,
            "평가금액": amount, "최근1년수익률": ret_1y, "고객보유수익률": ret_own or 0,
            "PF비중": amount / bal, "약정신규년월일": opened.isoformat(),
            "약정만기년월일": matures.isoformat() if matures else None,
            "퇴직연금상품위험등급구분코드": grade, "적용금리": rate,
            "판매상태": "정상" if kind in ("고유계정대", "예금", "GIC") else "판매중", "판매중단여부": "N"}


def _peer(age: int) -> dict:
    """동연령대 비교 — 나이가 가장 가까운 시연 고객의 값(외부 조인값 자리)."""
    near = min((p for p in C.PERSONAS if p.peer), key=lambda p: abs(p.ag - age), default=None)
    q = (near.peer if near else None) or {}
    out = {"동연령대평균수익률": (q.get("avg_ret") or 0) / 100,
           "동연령대상위1%평균수익률": (q.get("top1_ret") or 0) / 100,
           "동연령대상위1%평균원리금보장비중": (q.get("top1_guaranteed_pct") or 0) / 100}
    for i, name in enumerate(q.get("top1_funds") or [], 1):
        out[f"상위1%펀드TOP{i}"] = name
    for i, name in enumerate(q.get("top1_etfs") or [], 1):
        out[f"상위1%ETF_TOP{i}"] = name
    return out


def record(row: dict, ref: str, branch_name: str) -> dict:
    """부점 DB 고객 행 한 줄 → customers.json 꼴 레코드(id = 내부 행 식별자)."""
    rnd = random.Random(f"detail:{ref}")
    today = clock.today()
    from pension_agent.tools.branch_briefing import roster  # noqa: PLC0415 — 순환 임포트 회피
    a = roster.amounts(row)                     # 목록 판정과 같은 금액
    bal, idle, mat_amt, etf, fund = a["bal"], a["idle"], a["mat"], a["etf"], a["fund"]
    mat_date = date.fromisoformat(a["mat_date"]) if a["mat_date"] else None
    rest = a["safe"] - idle - mat_amt
    # 100만원 미만 자투리(만기 금액을 만원 단위로 줄이며 남은 몫)는 고유계정대로 — 비중 판정이 그대로일 때만
    if 0 < rest < 100 * _STEP and round((idle + rest) * 100 / bal) == round(idle * 100 / bal):
        idle, rest = idle + rest, 0
    ret = float(row["return_rate"] or 0) / 100
    joined = date.fromisoformat(row["joined_date"]) if row.get("joined_date") else today - timedelta(days=1500)

    deposits, gics = _pool("예금"), _pool("GIC")
    products: list[dict] = []
    if idle:
        products.append(_product("고유계정대", "고유계정대", idle, "매우낮은위험", joined, bal=bal))
    if mat_amt:
        kind = row.get("next_maturity_type") or "예금"
        years = 3 if kind == "GIC" else 1
        src = rnd.choice(gics if kind == "GIC" else deposits)
        products.append(_product(src["name"], kind, mat_amt, "매우낮은위험",
                                 mat_date - timedelta(days=365 * years), matures=mat_date,
                                 rate=(rate := round(rnd.uniform(0.028, 0.036), 3)), ret_own=rate, bal=bal))
    if rest:
        # 나머지 원리금보장 — 1년 안 최근접 만기는 행의 그 1건이다(최근접 만기·이탈 위험 점수가 목록과 같게).
        # 그 만기가 아직 안 왔거나 1년 안 만기가 없으면 1년 뒤에, 이미 지났으면 그보다 더 앞서 지난 예금으로 둔다
        if mat_date and mat_date < today:
            when = mat_date - timedelta(days=rnd.randint(5, 40))
        else:
            when = max(mat_date or today, today + timedelta(days=365)) + timedelta(days=rnd.randint(30, 400))
        kind = "GIC" if rnd.random() < 0.3 else "예금"
        years = 3 if kind == "GIC" else 2
        src = rnd.choice(gics if kind == "GIC" else deposits)
        products.append(_product(src["name"], kind, rest, "매우낮은위험",
                                 when - timedelta(days=365 * years), matures=when,
                                 rate=(rate := round(rnd.uniform(0.028, 0.036), 3)), ret_own=rate, bal=bal))
    for kind, amount in (("수익증권", fund), ("ETF", etf)):
        pool = _pool(kind)
        if not amount or not pool:
            continue
        picks = rnd.sample(pool, min(len(pool), 2 if amount >= 20_000_000 else 1))
        for src, part in zip(picks, _split(amount, len(picks), rnd)):
            own = round(ret + rnd.uniform(-0.05, 0.05), 3)
            products.append(_product(src["name"], kind, part, src.get("grade") or "높은위험",
                                     today - timedelta(days=rnd.randint(60, 900)),
                                     ret_1y=round(own + rnd.uniform(-0.03, 0.03), 3), ret_own=own, bal=bal))
    by = {k: sum(p["평가금액"] for p in products if p["상품유형"] == k)
          for k in ("고유계정대", "예금", "GIC", "수익증권", "ETF", "기타")}

    room = int(row.get("tax_credit_room") or 0)
    started = row.get("pension_started") == "개시"
    paid = 0 if started else max(0, C.TAX_CREDIT_CAP_WON - room)
    savings = {"보유": "Y", "미보유": "N"}.get(row.get("pension_savings") or "")
    sav_paid = _won(paid * rnd.uniform(0.2, 0.6)) if savings == "Y" and paid else 0
    contact = row.get("last_contact_days")
    last_order = today - timedelta(days=rnd.randint(10, 150))
    severance = _won(bal * rnd.uniform(0.0, 0.8)) if rnd.random() < 0.6 else 0
    tax_isa = {f"{y}년IRP납입액": _won(rnd.uniform(0, C.TAX_CREDIT_CAP_WON))
               for y in range(max(joined.year, today.year - 3), today.year)}
    tax_isa.update({
        "당해년도IRP납입액": paid - sav_paid, "당해년도연금저축납입액": sav_paid,
        "당해년도세액공제인정납입액": paid, "세액공제기준한도": C.TAX_CREDIT_CAP_WON, "세액공제잔여한도": room,
        "ISA만기1개월이내보유여부": "N", "ISA만기금액": int(row.get("isa_maturity_amt") or 0),
        "ISA만기일": row.get("isa_maturity_date"), "ISA기관구분": "당행" if row.get("isa_maturity_amt") else "없음",
        "총급여": _won(rnd.uniform(35_000_000, 120_000_000)), "연금저축보유여부": savings,
    })
    if row.get("isa_maturity_date") and row.get("isa_maturity_amt"):
        dd = (date.fromisoformat(row["isa_maturity_date"]) - today).days
        tax_isa["ISA만기1개월이내보유여부"] = "Y" if 0 <= dd <= 30 else "N"
    return {
        "id": ref,
        "basic": {"고객명": row["customer_name"], "만나이": int(row["age"]), "투자성향": row["risk_profile"],
                  "투자성향분석일": (today - timedelta(days=rnd.randint(30, 700))).isoformat(),
                  "KB스타클럽등급": row.get("star_grade"), "관리점": branch_name,
                  "최근상담일": (today - timedelta(days=int(contact))).isoformat() if contact is not None else None},
        "summary": {
            "전체평가금액": bal,
            **{f"{k}평가금액": v for k, v in by.items()}, **{f"{k}비중": v / bal for k, v in by.items()},
            "실적배당상품비중": (by["수익증권"] + by["ETF"]) / bal,
            # 매핑이 ×100 후 소수 한 자리로 반올림한다 — 행 값을 먼저 한 자리로 맞춰 둔다(목록과 같은 표기)
            "최근1년IRP수익률": round(float(row["return_rate"] or 0), 1) / 100,
            "디폴트옵션등록여부": "Y" if row.get("default_option") == "설정" else "N",
            "디폴트옵션위험등급": None},
        "activity": {
            "최근상품매매일": last_order.isoformat(), "최근운용지시일": last_order.isoformat(),
            "최근입금일": (date(today.year, rnd.randint(1, today.month), 1).isoformat() if paid
                         else date(today.year - 1, rnd.randint(1, 12), 1).isoformat()),
            "최근1년상품매매횟수": rnd.randint(1, 8),
            "ETF현재보유여부": "Y" if etf else "N", "ETF과거매매이력여부": "Y" if etf else "N",
            "수익증권현재보유여부": "Y" if fund else "N", "수익증권과거매매이력여부": "Y" if fund else "N",
            # 목록 판정과 같게 — 1개월 변동 없음(방치/현금화 신호는 행의 고유계정대 비중으로만 갈린다)
            "최근1개월고유계정대증감액": 0,
            "입금예정상품등록여부": "Y" if rnd.random() < 0.3 else "N"},
        "tax_isa": tax_isa,
        "pension": {"기준일": today.isoformat(), "만나이": int(row["age"]), "IRP가입일": joined.isoformat(),
                    "퇴직급여포함여부": "Y" if severance else "N", "퇴직급여금액": severance,
                    "개인부담금금액": bal - severance,
                    "연금개시요건충족여부": "Y" if row.get("pension_eligible") == "충족" else "N",
                    "연금개시여부": "Y" if started else "N", "연금개시일": None, "연금지급설계등록여부": "N"},
        "peer": _peer(int(row["age"])),
        "products": sorted(products, key=lambda p: -p["평가금액"]),
        "history": [],
    }


_CACHE: dict[tuple[str, str], C.Profile | None] = {}


def profile(customer_id: str) -> C.Profile | None:
    """내부 행 식별자 → 상세 Profile(목업 DB 의 합성 고객만). 시연 고객·모르는 id·목업 DB 가 아니면 None."""
    from pension_agent.tools.branch_briefing import demo_db, run  # noqa: PLC0415 — 순환 임포트 회피

    m = _REF.match(customer_id or "")
    if not m or run.backend() != "demo":
        return None
    key = (customer_id, clock.today().isoformat())
    if key not in _CACHE:
        found = None
        if not demo_db.detail_id(customer_id):          # 시연 고객은 자기 원장(customers.json)
            row = demo_db.connection().execute(
                "SELECT * FROM irp_customer_base WHERE branch_code = ? AND rowid = ?",
                (m.group(1), int(m.group(2)))).fetchone()
            if row is not None:
                rec = record(dict(row), customer_id, dict(demo_db.BRANCHES).get(m.group(1), m.group(1)))
                found = replace(C.from_record(rec), customer_ref=customer_id)
        _CACHE[key] = found
    return _CACHE[key]


def clear() -> None:
    """캐시 비우기(테스트의 «오늘» 변경 · 목업 DB 재생성)."""
    _CACHE.clear()


if profile not in C.DETAIL_SOURCES:
    C.DETAIL_SOURCES.append(profile)
