"""현행 목업 대비 개선 산출물 비교.

현행 목업(`0. 기획/최종_퇴직연금_에이전트_v0.4.html`)의 생성 로직을 재현하여 동일 고객에
대한 산출물을 나란히 출력한다. 재현 대상은 다음 두 가지이다.

    · AI 전략 제안 문장 = 최우선 요건 1건에 대해 사전 작성된 템플릿(A[k].str)
    · 전략 제안 TOP3    = 요건 우선순위 상위 3건. 3건 미만인 경우 고정 목록에서 보충한다.

목업이 판정하지 않는 요건(위험자산 한도 초과)은 목업 측 재현에서 제외하고, 미판정 사실을
표시한다. 재현 결과를 임의로 보정하지 않기 위함이다.

LLM 미설정 환경에서는 개선 측 문장이 규칙 기반 폴백으로 생성된다. 두 경로 모두 동일한
재료(engine.prepare)를 사용하므로 수치·상품·근거는 동일하다.

실행: python demo.py
"""

from __future__ import annotations

import sys

import engine
from agent import propose
from customer import CONDS, PERSONAS, Profile, conditions

# ─────────────────────────────────────────────────────────────
# 현행 목업 재현
# ─────────────────────────────────────────────────────────────

# 목업의 A[k].top(c).t — 요건별 TOP3 항목 제목
MOCKUP_TOP = {
    "dep": "글로벌 채권형 펀드 전환", "nod": "안정형 디폴트옵션 설정",
    "low": "TDF 편입으로 수익 구조 개선", "dor": "계좌 진단 리포트 공유 후 접촉",
    "nch": "반기 자동 리밸런싱 등록", "mat": "고금리 원리금보장 재예치",
    "sec": "특정 섹터 ETF 비중 축소", "mis": "성향 맞춤 리밸런싱",
    "add": "추가입금 분산 투자", "tax": "세액공제 한도 납입",
    "chn": "진단 리포트 선발송 후 접촉",
}

# 목업의 resolve() — TOP3 정원 미달 시 보충에 사용하는 고정 목록
MOCKUP_FILL = ["low", "nch", "add", "tax", "dep"]

# 목업에 판정 로직이 없는 요건. 재현 대상에서 제외한다.
MOCKUP_BLIND = ["lim"]


def mockup_conditions(p: Profile) -> list[str]:
    return [c for c in conditions(p) if c not in MOCKUP_BLIND]


def mockup_sentence(p: Profile, prim: str) -> str:
    """목업의 A[prim].str(c) 재현. 최우선 요건 1건만으로 문장을 구성한다."""
    if prim == "dep":
        tail = "안정형 디폴트옵션 설정" if p.dopt == "미설정" else "안정형 MP 운용"
        return (f"예금 {p.port[0]}%를 글로벌 채권형 펀드로 전환하고, 추가입금은 {tail}으로 "
                "장기 수익률과 리스크 관리를 함께 추진하세요.")
    if prim == "sec":
        return (f"특정 섹터 ETF {p.port[3]}%를 30% 이내로 분할 조정하고, 조정분은 "
                "글로벌 분산형과 채권형으로 나눠 변동성을 낮추세요.")
    if prim == "chn":
        head = (f"만기 D-{p.matDD} 이전에 선제 접촉해 재예치를 확정하고"
                if p.matDD is not None and p.matDD <= 90
                else "진단 리포트를 먼저 발송한 뒤 유선 접촉하고")
        tail = "디폴트옵션 설정" if p.dopt == "미설정" else "채권형 전환"
        return f"이탈 위험이 가장 높은 구간이므로 {head}, {tail}으로 방치 상태를 한 번에 정리하세요."
    if prim == "mis":
        return (f"투자성향({p.rk}) 대비 위험자산 {p.risk_asset}%로 과도하므로 성향 맞춤 "
                "리밸런싱을 먼저 진행하고, 조정분은 채권형과 원리금보장 상품으로 옮겨 정리하세요.")
    return f"[{prim}] 템플릿 미재현"


SPEC_BY_COND = {s["when"]: s for s in engine.SPECS if s.get("when")}


def mockup_tops(conds: list[str]) -> list[tuple[str, str]]:
    """목업의 TOP3 구성을 재현하고 각 항목의 문제 유형을 판정한다.

    판정 근거는 strategies.json 의 resolves·pool 정의이며, 별도 하드코딩을 두지 않는다.
    """
    keys = list(conds[:3])
    padded: list[str] = []
    while len(keys) < 3:
        f = next((k for k in MOCKUP_FILL if k not in keys), None)
        if f is None:
            break
        keys.append(f)
        padded.append(f)

    out: list[tuple[str, str]] = []
    for i, k in enumerate(keys):
        note = ""
        spec = SPEC_BY_COND.get(k, {})
        if k in padded:
            note = "요건 미성립 · 정원 충족용"
        else:
            for j, prev in enumerate(keys[:i], 1):
                pspec = SPEC_BY_COND.get(prev, {})
                if any(r.get("policy") == "always" and r.get("target") == spec.get("id")
                       for r in pspec.get("resolves", [])):
                    note = f"{j}번과 실행 중복"
                    break
                if spec.get("pool") and spec.get("pool") == pspec.get("pool"):
                    note = f"{j}번과 동일 재원({spec['pool']}) 중복 배분"
                    break
        out.append((MOCKUP_TOP.get(k, k), note))
    return out


# ─────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────

def line(ch: str = "─", n: int = 78) -> str:
    return ch * n


def show(p: Profile) -> None:
    conds = conditions(p)
    mconds = mockup_conditions(p)
    print("\n" + line("═"))
    print(f" {p.nm} · {p.ag}세 · {p.rk}/{p.grade} · 적립금 {engine.won(p.bal)} · "
          f"수익률 {p.ret}%(하위 {p.retPct}%) · 위험자산 {p.risk_asset}%"
          + (" · 비대면" if p.nonface else ""))
    print(line("═"))
    print(f"성립 요건 {len(conds)}건: " + ", ".join(CONDS[c] for c in conds))
    blind = [c for c in conds if c in MOCKUP_BLIND]
    if blind:
        print(f"  └ 목업 미판정 요건: {', '.join(CONDS[c] for c in blind)}")

    print("\n[현행 목업]")
    print(f"  전략 제안 문장 (요건 '{CONDS[mconds[0]]}' 1건 기준)")
    print(f"    {mockup_sentence(p, mconds[0])}")
    print("  전략 제안 TOP3")
    for i, (title, note) in enumerate(mockup_tops(mconds), 1):
        print(f"    {i}. {title}" + (f"   ← {note}" if note else ""))

    r = propose(p)
    f = r["facts"]
    print("\n[개선]")
    bf = f["briefing"]
    src0 = "; ".join(engine.format_sources([bf["source"]]))
    print(f"  보유현황  {' | '.join(f'{k} {v}' for k, v in bf.items() if k != 'source')}"
          f"  (시스템 조회 · 근거 {src0})")
    print(f"  전략 제안 문장 (요건 {len(conds)}건 종합 · 생성 경로 {r['source']})")
    print(f"    {r['sentence']}")
    if r["reason"]:
        print(f"    ※ {r['reason']}")
    for b in r["rejected"]:
        print(f"    ※ 리젝: {b}")

    if f["rationale"]:
        print("\n  판단근거")
        for s in f["rationale"]:
            print(f"    · {s}")

    print("\n  선정 내역")
    print(f"    {'항목':<24}{'주체':>5}{'시급성':>7}{'수익률개선효과':>15}{'점수':>7}  근거등급 / 출처")
    for it in f["items"]:
        tag = " [필수]" if it["mandatory"] else ""
        src = "; ".join(it["source_titles"]) or (it["regulation"][:24] if it["regulation"] else "-")
        print(f"    {it['title'] + tag:<24}{it['actor']:>5}{it['urgency']:>7}"
              f"{it['effect_grade']:>15}{it['score']:>7}  {it['confidence']} / {src}")
    for it in f["items"]:
        for ex in it["evidence_extra"]:
            print(f"      (흡수) {it['title']}: {ex}")

    if f["alternatives"]:
        print("\n  다른 제안 (수익률 개선 효과 순)")
        for i, a in enumerate(f["alternatives"], 1):
            print(f"    {i}. {a['clause']}  [효과 {a['effect_grade']}]")
    print(f"\n  적합성 허용 상한    {f['risk_cap']}")
    if f["needs_slot"]:
        print(f"  상담 중 확인 필요    {', '.join(f['needs_slot'])} (실적배당/원리금보장 분기)")
    for label, key in (("고지 필요", "cautions"), ("확인 필요", "needs_confirm"),
                       ("제안 보류", "unverified")):
        if f[key]:
            print(f"  {label}")
            for v in f[key]:
                print(f"    · {v}")
    if f["dropped"]:
        print("  제외 항목")
        for d in f["dropped"]:
            print(f"    · {d}")
    if f["blocked_products"]:
        print("  적합성 게이트 차단 상품")
        for b in f["blocked_products"]:
            print(f"    · {b}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    errors, _ = engine.validate()
    if errors:
        print(f"❌ 정의 검증 ERROR {len(errors)}건 — python engine.py 로 확인하십시오.")
        for e in errors:
            print("   " + e)
        raise SystemExit(1)

    for p in PERSONAS:
        show(p)
    print("\n" + line())
