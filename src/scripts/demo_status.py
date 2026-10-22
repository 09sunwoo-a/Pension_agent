"""데모 점검 리포트 생성기 — `docs/DEMO_STATUS.md` 를 코드에서 생성 (손 수정 금지).

    python -m scripts.demo_status            # 리포트 생성
    python -m scripts.demo_status --check    # 파일 미갱신, 요약만 출력

집계: 1 안내 콘텐츠 더미 · 2 데모 금리표 · 3 주장 성립 조건 · 4 소스 미확정 필드 ·
5 데모 상수 · 6 시효 미기재 사실 · 7 요건 임계값 근거등급
"""

from __future__ import annotations

import json
import sys

from pension_agent.common import config
from pension_agent.knowledge import market
from pension_agent.actions import screens
from pension_agent.knowledge import kb as kbmod
from pension_agent.tools.customer_solution import customer, engine, support

OUT = config.REPO_ROOT / "docs" / "DEMO_STATUS.md"

# 데이터딕셔너리에 대응 컬럼이 없는 Profile 필드(«없음» 아닌 «소스 확인 필요»).
UNSOURCED_FIELDS = [
    ("matDD / matAmt", "만기 잔여일·만기금액", "딕셔너리 전체에 '만기' 컬럼 없음",
     "④ 기한 임박 · mat 요건"),
    ("room", "잔여 세액공제 한도", "'한도' 컬럼 없음 (납입누계로 역산 필요)",
     "① 고객이 모르는 자기 현황 · add/tax 요건"),
    ("income_bracket", "총급여 구간", "'가입자기준급여액'은 퇴직급여 추계용이라 공제율 분기에 못 씀",
     "세액공제율 13.2/16.5 판정"),
    ("dorm", "최근 접촉 경과일", "'접촉' 컬럼 없음 ('상담직원번호'만) — CRM 조인 필요",
     "§3.1 상단 · dor 요건"),
    ("retPct", "수익률 백분위", "'평가금액 백분위'는 있으나 다른 지표 — 피어그룹 산출 필요",
     "② 왜 이 고객인가 · low 요건"),
    ("pension_started", "연금수령 개시 여부", "'연금개시가능잔여일'은 만55-연령이라 개시 여부가 아님",
     "§7 추가납 권유 금지 (민원 방지 규칙)"),
    ("grade", "고객 위험등급(가입 상한)", "딕셔너리·목업 v3 모두 컬럼 없음 — 현재 PREF[투자성향] 파생값",
     "적합성 게이트 (상품 위험등급 하드 상한)"),
]


# 요건 판정 임계값 → 근거 TARGET_ID. 근거등급은 targets.json 에서 읽음 — 여기 복사 금지(표 갱신 시 낡은 등급 노출).
COND_THRESHOLDS = [
    ("dep", "customer.PRINCIPAL_HEAVY_PCT", lambda: f"{customer.PRINCIPAL_HEAVY_PCT}%",
     "TG-202", "고유계정대+예금+GIC 합산 비중. TG-003(합산 100%, 등급 A)을 함께 덮으려 상한은 두지 않는다"),
    ("nch", "customer.NO_CHANGE_MONTHS", lambda: f"{customer.NO_CHANGE_MONTHS}개월",
     "TG-201", "최종 운용지시 이후 경과 개월수"),
    ("idl / out", "customer.CASH_IDLE_PCT", lambda: f"{customer.CASH_IDLE_PCT}%",
     "TG-001", "고유계정대 비중. 방치(idl)·현금화 신호(out)의 갈림은 세그먼트 26 이 정한다"),
]


def _outreach_runway() -> str:
    """⑨ 이벤트·세미나 후보 소진 시점. 더미 캘린더가 오늘을 지나면 섹션이 빔."""
    from pension_agent.tools.customer_solution import support  # noqa: PLC0415

    out = []
    for key, label in (("event", "이벤트"), ("seminar", "세미나")):
        rows = support.outreach_candidates().get(key) or []
        last = max((r["end_date"] for r in rows), default=None)
        out.append(f"{label} {len(rows)}건" + (f" (마지막 종료 {last})" if last else " — 후보 없음"))
    return " · ".join(out)


def _scheduler_state() -> str:
    """예약 실행기 표시(생성 환경 값, `.env` 포함)."""
    from pension_agent.common import env  # noqa: PLC0415
    from pension_agent.actions import actions  # noqa: PLC0415

    env.load()
    name = actions.scheduler_name()
    if name == "demo":
        return "demo (시연용 — 접수 즉시 발송)"
    return name or "없음 (미연결 — 예약 요청은 보내지 않는다)"


def _branch_db_state() -> str:
    """부점 DB 백엔드 표시(생성 환경 값, `.env` 포함)."""
    from pension_agent.common import env  # noqa: PLC0415
    from pension_agent.tools.branch_briefing import run  # noqa: PLC0415

    env.load()
    return {"demo": "demo (행외 목업 DB — 전부 시연값)",
            "sybase": "sybase (행내 Sybase IQ)",
            "off": "off (조회하지 않는다)"}[run.backend()]


def _rows(lines: list[str], header: list[str], rows: list[list[str]]) -> None:
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    lines.append("")


def build() -> tuple[str, dict[str, int]]:
    kb = kbmod.load_kb()
    snap = market.current()
    L: list[str] = []
    n: dict[str, int] = {}

    L += [
        "# 데모 점검 — 실데이터 전환 시 손봐야 할 자리",
        "",
        "> **이 파일은 생성물이다.** 손으로 고치지 말고 `python -m scripts.demo_status` 를",
        "> 다시 돌린다. 값을 바꾸려면 코드·데이터를 고쳐야 한다 — 그게 이 리포트의 존재 이유다.",
        "",
        "이 저장소는 발표용 데모다. 화면에는 더미 표시를 붙이지 않기로 했으므로",
        "(발송문 포함), **무엇이 더미인지 아는 방법은 이 리포트뿐이다.**",
        "",
    ]

    # 1. 안내 콘텐츠 — 게이트가 막는 더미 + 게이트 통과하는 시연값 둘 다 집계(더미만 세면 0건)
    outreach = [a for a in support.ASSETS if a.get("content_type") in ("이벤트", "세미나")]
    dummies = [a for a in support.ASSETS if a.get("dummy")]
    n["assets"] = len(dummies)
    L += [f"## 1. 안내 콘텐츠 — 시연용 {len(outreach)}건 · 게이트가 막는 더미 {len(dummies)}건", "",
          "⑨ 「고객님께 안내해보세요」의 이벤트·세미나다. 출처가 있는 콘텐츠(`source`)는",
          "`dummy` 를 달지 않으므로 `actions/actions.py::open_lms_screen()` 게이트가 막지 않고,",
          "발송 화면 연계까지 이어진다. **일정·링크·수신거부 번호는 시연을 위해 구성된 값이다** —",
          "화면과 발송문에는 그 표시가 없으므로(발표용 데모라 딱지를 붙이지 않는다) 여기가",
          "유일한 기록이다. 실제 콘텐츠 캘린더가 붙으면 이 표가 그대로 실제 일정으로 바뀐다.", "",
          f"- 안내 링크: `https://obank.kbstar.com/demo/...` — 시연용 경로",
          f"- 수신거부 번호: `{support.OPT_OUT}` — 실제 회선 미정, 자리표시자",
          "- LMS 문구의 골격(광고 표기·인사말·링크·수신거부)은 코드가 조립하고",
          "  (`support/assets.py::lms_frame`), 가운데 본문만 LLM 이 쓴다", ""]
    _rows(L, ["id", "이름", "종류", "일정", "매칭 키워드", "게이트"],
          [[a["id"], a.get("name", ""), a.get("content_type", ""),
            f"{a.get('start_date', '?')} ~ {a.get('end_date', '?')}",
            ", ".join(a.get("keywords") or []) or "—",
            "차단(더미)" if a.get("dummy") else "통과"] for a in outreach])

    # 요건으로 매핑 안 된 keywords — 해당 콘텐츠는 관련도 0
    unmapped = sorted({kw for a in outreach for kw in (a.get("keywords") or [])
                       if kw not in support.KEYWORD_CONDS and not support.conds_of(a)})
    if unmapped:
        L += ["", "### 고객 상태로 판정할 수 없는 키워드", "",
              "요건(`customer.CONDS`)에 대응이 없어 관련도를 잴 수 없는 키워드다. 그 콘텐츠는",
              "다른 후보가 없을 때만 노출된다 — 대응 컬럼이 확인되면",
              "`support/assets.py::KEYWORD_CONDS` 에 한 줄 더한다.", ""]
        _rows(L, ["키워드", "쓰는 콘텐츠"],
              [[kw, ", ".join(a["id"] for a in outreach if kw in (a.get("keywords") or []))]
               for kw in unmapped])
    L += [""]

    # 발송 자료(customer_facing) 미등록 전략 — 엔진 «확인 필요» 목록에 넣으면 직원 안내로 새므로 여기서 집계
    from pension_agent.tools.customer_solution.engine import catalog as _catalog  # noqa: PLC0415
    from pension_agent.tools.customer_solution.engine import render as _render  # noqa: PLC0415
    no_asset = [s for s in _catalog.BY_ID.values()
                if s.get("clause_if_asset") and _render.customer_facing_asset(s.get("branch")) is None]
    n["no_asset"] = len(no_asset)
    L += [f"### 발송 자료 미등록 전략 — {len(no_asset)}건", "",
          "`clause_if_asset`(자료를 첨부해 보내는 문구)를 선언했지만 `assets.json` 에 고객 발송",
          "승인(`customer_facing: true`) 자료가 없어 자료 없는 문구(`clause`)로 내려가는 전략이다.",
          "자료를 등록하면 그 전략의 실행 문구가 첨부 문구로 바뀐다.", ""]
    if no_asset:
        _rows(L, ["전략", "자료 있을 때의 문구"],
              [[s["id"], s["clause_if_asset"]] for s in no_asset])
    else:
        L += ["- 없음", ""]
    L += [""]

    # 2. 데모 금리표
    n["rates"] = len(snap["rates"]) if snap.get("dummy") else 0
    L += [f"## 2. 데모 금리표 — {n['rates']}종", "",
          f"`market.current()` 가 자리표시자다 (`as_of` {snap.get('as_of')}, "
          f"`dummy` {snap.get('dummy')}). 실제 피드가 붙으면 `market/__init__.py` 의 본문만",
          "교체하고 `rates_demo.json` 을 지운다.", ""]
    if snap.get("dummy"):
        _rows(L, ["키", "항목", "데모값"],
              [[k, v.get("label", ""), f"{v['value']}{v.get('unit', '')}"]
               for k, v in snap["rates"].items()])

    slotted = [c for c in kb.pitches if c.get("_rate_slots_applied") or c.get("_rate_notes")]
    n["slotted"] = len(slotted)
    L += [f"### 이 금리로 문장이 바뀌는 화법 카드 — {len(slotted)}건", "",
          "원문(quotes)은 그대로 보존되고 파생 텍스트만 바뀐다.", ""]
    _rows(L, ["카드", "원문", "현재", "무엇", "처리"],
          [[c["id"], s["was"], s["now"], s["what"],
            "치환" if c.get("_rate_slots_applied") and s in c["_rate_slots_applied"] else "참고 표시"]
           for c in slotted
           for s in (c.get("_rate_slots_applied") or []) + (c.get("_rate_notes") or [])])

    # 3. 주장 성립 조건
    sup, ver = kb.suppressed, [c for c in kb.pitches if c.get("_verify_first")]
    n["suppressed"], n["verify"] = len(sup), len(ver)
    L += [f"## 3. 주장 성립 조건 — 격리 {len(sup)}건 · 확인 요구 {len(ver)}건", "",
          "수치를 갈아끼우면 결론이 거짓이 될 수 있는 카드들. 조건이 깨지면 후보군에서 빼고,",
          "판정 근거가 시스템에 없으면 지어내지 않고 행원에게 확인을 넘긴다.", ""]
    if sup:
        _rows(L, ["카드", "사유"], [[c["id"], c["_suppressed"]] for c in sup])
    _rows(L, ["카드", "확인이 필요한 이유"],
          [[c["id"], c["_verify_first"]] for c in sorted(ver, key=lambda x: x["id"])])

    # 4. 소스 미확정 필드
    n["fields"] = len(UNSOURCED_FIELDS)
    L += [f"## 4. 소스 미확정 필드 — {len(UNSOURCED_FIELDS)}건", "",
          "데이터딕셔너리에서 대응 컬럼을 찾지 못한 `Profile` 필드. 데모에서는 목업 원장",
          "(customers.json)의 값·파생값으로 채워 화면이 매끄럽게 보이지만, 실데이터 전환 때는",
          "**여기부터** 확인한다.",
          "딕셔너리가 아직 정리 중이므로 '없다'가 아니라 '소스 확인 필요'로 읽는다.", ""]
    _rows(L, ["필드", "무엇", "딕셔너리 상태", "무엇이 걸려 있나"],
          [list(r) for r in UNSOURCED_FIELDS])

    # 5. 하드코딩된 데모 상수
    consts = [
        ("customer.AS_OF", str(customer.AS_OF),
         "목업 원장 스냅샷 기준일(잔액·수익률이 찍힌 날). customers.json meta.as_of 와 "
         "묶여 있어 실데이터 조인 전까지는 안 움직인다"),
        ("customer.today()", f"{customer.today()} (원장보다 {customer.ledger_age_days()}일 뒤)",
         "상담 시점의 오늘 — 잔여일수·경과일의 기준. 기본은 실제 날짜이고 "
         "PENSION_TODAY=YYYY-MM-DD 로 고정한다(테스트는 AS_OF 로 고정한 채 돈다)"),
        ("data/assets.json 이벤트·세미나 창", _outreach_runway(),
         "⑨ 후보는 종료되지 않은 콘텐츠만 남는다. 시연용 캘린더라 오늘이 창을 지나가면 "
         "후보가 마른다 — 발표일을 창 안으로 고정하려면 PENSION_TODAY 를 쓰고, "
         "실제 콘텐츠 캘린더가 붙으면 이 자리가 사라진다"),
        ("customer.PERSONAS",
         f"{len(customer.PERSONAS)}명 (customers.json ← IRP_Agent_더미고객_9Cases_v3.xlsx "
         "9케이스 + scripts/demo_cases.json 3명)"
         if customer.PERSONAS else "0명 (비어 있음 — customers.json 미생성)",
         "시연용 목업. scripts/import_customers.py 로 재생성 — 실데이터 조인으로 교체"),
        ("data/portfolios.json", f"{len(engine.PORTFOLIOS)}건",
         "채권40+채권30+주식30 예시를 실제 카탈로그로 재구성한 자리표시자 — 실제 추천 포트폴리오로 교체. "
         "교체할 원문은 05_시황_상품_기반지식/02_상품/01_퇴직연금펀드_포트폴리오 다(대화형에는 "
         "market 카드로 이미 적재돼 있다). 옮기려면 그 문서의 펀드 40여 종을 먼저 product "
         "레코드로 저작해야 한다 — allocation[].product_id 가 그것을 참조하고, 적합성 게이트가 "
         "타입드 필드로 비교하기 때문이다"),
        ("engine.TOP_N / ALT_N", f"{engine.TOP_N} / {engine.ALT_N}",
         "제안 1개 + 예비 1개 (07_에이전트_기능정의/01 ① 4)"),
        ("actions.screens.MODE", f"{screens.MODE} ({screens.MODES[screens.MODE]})",
         "단말 딥링크의 mode 파라미터. 지금은 개발 모드로 링크를 만든다 — 운영 전환 시 "
         "TERMINAL_SCREEN_MODE=O(스테이징 S). 스킴·scnNo 형식은 단말 연동 규격이고, "
         "화면번호 자체는 지식베이스 절차 카드에서 온다"),
        ("actions.actions 예약 실행기",
         _scheduler_state(),
         "예약 쪽지(«10월 5일에 보내줘»)를 누가 실행하나. **기본값이 demo 다** — 예약을 접수하는 순간 "
         "한 통을 즉시 보낸다. 화면은 «예약했어요»라고 말하지만 실제로는 이미 나간 것이다(시연에서 "
         "쪽지가 도착하는 것을 보이려는 것). 실서비스로 옮길 때는 PENSION_MEMO_SCHEDULER=off(보내지 "
         "않고 «미연결»)이나 actions.SCHEDULERS 에 더한 실제 실행기 이름을 넣는다"),
        ("tools/branch_briefing 부점 DB", _branch_db_state(),
         "부점 브리핑(부점 현황 · 고객 검색 · 화면 진입 패널)이 조회하는 DB. **기본값이 demo 다** — 행외 목업 DB"
         "(sqlite 메모리, 부점 4곳 · 직원 5명). **여의도종합금융센터 1,392명은 부점 브리핑 화면 기획안의 수치"
         "(526.8억 · 부점 수익률 18.96% · 고유계정대 6.7억 · 원리금보장 69.3% · 올해 계약이전 89좌·62.8억 · 수익률 1% "
         "미만 210명 · 1억 이상 120명/45.3% · 오늘의 중점관리 고객 56명 · 전월말 대비 ▲1.3억·▲0.12%p·▼0.4억)가 그대로 나오게 맞춘 가짜 고객이다**. 56명이 되도록 나머지 1,336명은 어떤 룰베이스 요건에도 걸리지 않게 만들었다 — 그래서 디폴트옵션 미설정 같은 고객군 인원이 실제 부점보다 훨씬 적다"
         "(목업 고객 12명 + 합성 1,380명, demo_yeouido.py). 다른 세 부점은 고정 시드 합성 72명씩이고 전월말 스냅샷·"
         "계약이전도 전부 가짜다 — 여의도는 CASE 3, 판교 CASE 1, 부산 CASE 2, 강남 없음이 서도록 맞췄다(§8). "
         "행내로 옮길 때는 BRANCH_DB=sybase 로 바꾸고 sql/sybase/ 의 고정 뷰 SQL 다섯 개"
         "(사번→부점 · 고객 뷰 · 전월말 고객 뷰 · 계약이전 뷰 · 부점 집계 뷰)를 실제 테이블로 채운다 — 채우기 전에는 «DB 미연결»로 답한다. "
         "MCP 서버 sybase(MCP_SERVERS · MCP_CONN_ID)와 sybaseiq_query 의 인자 이름·응답 꼴도 행내 실측이 필요하다"),
        ("actions.screens.link() 파라미터", "scnNo · mode",
         "규격이 정의한 둘만 싣는다. 고객 식별자·발송 문구는 단말이 받는 이름이 미확정이라 "
         "링크로 넘기지 않고 직원이 화면에서 입력한다 — 규격이 정해지면 "
         "actions/screens.py 의 조립부에 추가"),
    ]
    n["consts"] = len(consts)
    L += [f"## 5. 데모 상수 — {len(consts)}건", ""]
    _rows(L, ["자리", "현재값", "전환 시"], [list(c) for c in consts])

    # 6. 시효 미기재 사실
    stale = sorted(f["id"] for f in kb.facts.values() if not f.get("as_of"))
    n["stale"] = len(stale)
    L += [f"## 6. 기준시점 미기재 사실 — {len(stale)} / {len(kb.facts)}건", "",
          "구성 원칙 4(수치는 기준시점 병기)를 아직 못 지키는 사실들. 인용 전 원문 확인이 필요하다.",
          "", "<details><summary>목록</summary>", ""]
    L += [f"- `{i}`" for i in stale]
    L += ["", "</details>", ""]

    # 7. 요건 임계값의 근거등급
    tdoc = (json.loads(config.TARGETS_JSON.read_text(encoding="utf-8"))
            if config.TARGETS_JSON.is_file() else {"targets": []})
    by_id = {t.get("TARGET_ID"): t for t in tdoc.get("targets", [])}
    trows, pilot = [], 0
    for cond, const, value, tid, note in COND_THRESHOLDS:
        t = by_id.get(tid) or {}
        grade = t.get("근거등급") or "— (룰베이스 미적재)"
        if str(grade).startswith("D"):
            pilot += 1
        trows.append([f"`{cond}`", f"`{const}`", value(), tid,
                      str(t.get("타겟명") or "—"), str(grade), note])
    n["pilot_thresholds"] = pilot
    L += [f"## 7. 요건 임계값의 근거등급 — 검증 전 제안값 {pilot} / {len(trows)}건", "",
          "판정 임계값이 타겟 룰베이스(`targets.json` ← 기획자 확인표)의 어느 타겟에서 왔는지와, "
          "그 타겟이 스스로 밝힌 근거등급이다. **D 는 행내 문서에 없는 기획자 설계 제안값**이라 "
          "Pilot 로 운영하고 실데이터에서 보정해야 한다 — 실전환 때 가장 먼저 흔들릴 자리다. "
          "A 는 원문에 임계값이 그대로 적힌 것이다.", ""]
    _rows(L, ["요건", "상수", "현재값", "TARGET_ID", "타겟명", "근거등급", "비고"], trows)

    # 8. 부점 브리핑 기준(룰베이스 부점 시트) — 에이전트 초안
    segs = tdoc.get("branch_segments") or []
    cases = tdoc.get("branch_cases") or []
    params = tdoc.get("branch_params") or []
    rows8 = ([[s.get("SEG_ID"), "세그먼트", s.get("세그먼트명"), s.get("조건식"), s.get("근거등급"),
               s.get("적용상태")] for s in segs]
             + [[c.get("CASE_ID"), "CASE", c.get("CASE명"), c.get("판정조건 요약"), c.get("근거등급"),
                 c.get("적용상태")] for c in cases]
             + [[q.get("PARAM_ID"), "임계값", q.get("항목"), f"{q.get('값')} {q.get('단위') or ''}".strip(),
                 q.get("근거등급"), "—"] for q in params])
    drafts = sum(1 for r in rows8 if "초안" in str(r[5])) + len(params)
    n["branch_rules_draft"] = drafts
    L += [f"## 8. 부점 브리핑 기준 — 에이전트 초안 {drafts} / {len(rows8)}건", "",
          "룰베이스 xlsx 의 부점 시트 5종(세그먼트·CASE·임계값). **기획자가 «합리적인 기준하에 작성해 달라»고 "
          "위임해(2026-10-22) 에이전트가 쓴 초안이고 기획자 검토 전이다.** 개발 후 정의·임계값을 고치고, 실제 "
          "테이블에 맞추는 일은 뒤로 미뤘다. D 는 원문에 없는 설계 제안이다 — 화면 패널은 `grade` 로 그 사실을 "
          "받는다. 세그먼트 조건 필드 중 `idle_cash_amt`·`idle_cash_days`·`tax_credit_room`·`transfer_inquiry`·"
          "`risk_mismatch`·`isa_maturity_*` 는 원천 테이블이 정해지지 않은 시연 컬럼이고, «계좌 수»는 고객 수로 "
          "센다(개인형IRP 1인 1계좌 가정). 수익률 저조(1% 미만)·고액 적립금(1억 이상) 기준은 화면 기획안에서 온 값(BP-08·09)이다.", ""]
    _rows(L, ["ID", "종류", "이름", "조건", "근거등급", "적용상태"], [[str(c or "—") for c in r] for r in rows8])

    return "\n".join(L).rstrip() + "\n", n


def main() -> int:
    text, n = build()
    summary = " · ".join(f"{k} {v}" for k, v in n.items())
    if "--check" in sys.argv:
        print(f"[demo_status] {summary} (파일 미갱신)")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"[demo_status] {OUT.relative_to(config.REPO_ROOT)} 갱신 — {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
