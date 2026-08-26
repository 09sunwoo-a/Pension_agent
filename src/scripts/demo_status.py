"""데모 점검 리포트 생성기 — `docs/DEMO_STATUS.md` 를 코드에서 뽑아 덮어쓴다.

    python -m scripts.demo_status            # 리포트 생성
    python -m scripts.demo_status --check    # 생성만 하고 파일은 안 건드림(요약만 출력)

**손으로 쓰지 않는다.** 이 저장소는 발표용 데모라 화면에는 더미 표시를 붙이지 않기로 했고,
그래서 "무엇이 더미인지"를 아는 방법이 이 리포트뿐이다. 그걸 마크다운으로 손수 적으면
반드시 코드와 어긋나고, 어긋난 순간부터 아무도 믿지 않는다 — 실제로 폴더 재번호 때
`source.locator` 543 건이 조용히 썩는 동안 테스트는 654/654 초록불이었다.

집계하는 것
  1. 안내 콘텐츠 더미 (`assets.json` 의 `dummy: true`)
  2. 데모 금리표 (`market.current()` 의 `dummy` 와 그 값을 쓰는 화법 카드)
  3. 주장 성립 조건 — 격리된 카드 · 확인 요구 카드
  4. 소스 미확정 필드 (데이터딕셔너리에 없는 `Profile` 필드)
  5. 하드코딩된 데모 상수 (기준일·페르소나·자리표시자 데이터)
  6. 시효 미기재 사실 (`fact.as_of` 없음)
  7. 요건 임계값의 근거등급 (타겟 룰베이스 대조)
"""

from __future__ import annotations

import json
import sys

from pension_agent import config, market
from pension_agent.consult_agent import kb as kbmod, screens
from pension_agent.strategy_agent import customer, engine, support

OUT = config.REPO_ROOT / "docs" / "DEMO_STATUS.md"

# 데이터딕셔너리(`퇴직연금_데이터딕셔너리_정리 (1).xlsx`)에 대응 컬럼을 찾지 못한 Profile 필드.
# 데모에서는 전부 더미로 채워 화면을 매끄럽게 보이게 하고, 실데이터 전환 때 여기부터 확인한다.
# 딕셔너리가 아직 정리 중이라 "없다"가 아니라 "**소스 확인 필요**"로 읽어야 한다.
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


# 요건 판정 임계값 → 근거로 삼은 타겟 룰베이스 TARGET_ID.
#
# 근거등급은 여기 적지 않고 targets.json 에서 읽는다. 등급은 기획자가 매기는 값이라
# 여기 복사해두면 표가 갱신돼도 리포트만 옛 등급을 말하게 된다 — 그러면 "D(검증 전
# 제안값)를 A(행내 기준)로 읽는" 정확히 그 사고가 리포트를 통해 일어난다.
COND_THRESHOLDS = [
    ("dep", "customer.PRINCIPAL_HEAVY_PCT", lambda: f"{customer.PRINCIPAL_HEAVY_PCT}%",
     "TG-202", "고유계정대+예금+GIC 합산 비중. TG-003(합산 100%, 등급 A)을 함께 덮으려 상한은 두지 않는다"),
    ("nch", "customer.NO_CHANGE_MONTHS", lambda: f"{customer.NO_CHANGE_MONTHS}개월",
     "TG-201", "최종 운용지시 이후 경과 개월수"),
    ("idl / out", "customer.CASH_IDLE_PCT", lambda: f"{customer.CASH_IDLE_PCT}%",
     "TG-001", "고유계정대 비중. 방치(idl)·현금화 신호(out)의 갈림은 세그먼트 26 이 정한다"),
]


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

    # 1. 안내 콘텐츠 더미
    dummies = [a for a in support.ASSETS if a.get("dummy")]
    n["assets"] = len(dummies)
    L += [f"## 1. 안내 콘텐츠 더미 — {len(dummies)} / {len(support.ASSETS)}건", "",
          "지어낸 이벤트·세미나. 화면 이름과 발송문에 딱지가 없으므로 그대로 실제처럼 보인다.",
          "`pension_agent/tools.py::open_lms_screen()` 이 이 자산의 문구를 발송 화면에 채우는 것을",
          "거부하는 것이 유일한 안전장치다.",
          "실제 콘텐츠로 교체할 때 `dummy` 를 지우면 게이트가 열린다.", ""]
    _rows(L, ["id", "이름", "종류", "기간"],
          [[a["id"], a.get("name", ""), a.get("content_type", ""),
            f"{a.get('start_date', '?')} ~ {a.get('end_date', '?')}"] for a in dummies])

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
        ("customer.TODAY", str(customer.TODAY),
         "데모 고정 기준일. 실배포 시 date.today() 로 바꾸고 assets.json 날짜도 함께 교체"),
        ("customer.PERSONAS",
         f"{len(customer.PERSONAS)}명 (customers.json ← IRP_Agent_더미고객_9Cases_v3.xlsx)"
         if customer.PERSONAS else "0명 (비어 있음 — customers.json 미생성)",
         "시연용 목업 9케이스. scripts/import_customers.py 로 재생성 — 실데이터 조인으로 교체"),
        ("data/portfolios.json", f"{len(engine.PORTFOLIOS)}건",
         "채권40+채권30+주식30 예시를 실제 카탈로그로 재구성한 자리표시자 — 실제 추천 포트폴리오로 교체"),
        ("engine.TOP_N / ALT_N", f"{engine.TOP_N} / {engine.ALT_N}",
         "제안 1개 + 예비 1개 (07_에이전트_기능정의/01 ① 4)"),
        ("consult_agent.screens.MODE", f"{screens.MODE} ({screens.MODES[screens.MODE]})",
         "단말 딥링크의 mode 파라미터. 지금은 개발 모드로 링크를 만든다 — 운영 전환 시 "
         "TERMINAL_SCREEN_MODE=O(스테이징 S). 스킴·scnNo 형식은 단말 연동 규격이고, "
         "화면번호 자체는 지식베이스 절차 카드에서 온다"),
        ("consult_agent.screens.link() 파라미터", "scnNo · mode",
         "규격이 정의한 둘만 싣는다. 고객 식별자·발송 문구는 단말이 받는 이름이 미확정이라 "
         "링크로 넘기지 않고 직원이 화면에서 입력한다 — 규격이 정해지면 "
         "consult_agent/screens.py 의 조립부에 추가"),
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
