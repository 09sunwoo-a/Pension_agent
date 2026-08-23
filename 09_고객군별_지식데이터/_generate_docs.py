#!/usr/bin/env python3
"""고객군별 기획자용 MD 문서 생성기.

10_세그먼트.yaml · 20/21/22_지식유닛 · 30_시나리오.yaml 을 읽어
고객군마다 사람이 읽는 문서 1개(11~16_SEG-NN_*.md)를 만든다.

    python3 _generate_docs.py

데이터를 고치면 이 스크립트를 다시 돌린다 — MD를 손으로 고치지 않는다.
"""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT_FILES = ["20_지식유닛.yaml", "21_지식유닛_화법.yaml", "22_지식유닛_팩트_절차_금지.yaml"]

FILENAME = {
    "SEG-01": "11_SEG-01_수익률저조.md",
    "SEG-02": "12_SEG-02_자산편중.md",
    "SEG-03": "13_SEG-03_방치.md",
    "SEG-04": "14_SEG-04_이탈가능성높음.md",
    "SEG-05": "15_SEG-05_세액공제추가입금.md",
    "SEG-06": "16_SEG-06_예금GIC만기예정.md",
}
CIRCLED = {"SEG-01": "①", "SEG-02": "②", "SEG-03": "③",
           "SEG-04": "④", "SEG-05": "⑤", "SEG-06": "⑥"}
TYPE_KO = {"evidence": "근거", "method": "방법론", "script": "화법",
           "fact": "팩트", "procedure": "절차", "guardrail": "금지"}
OP_KO = {">=": "이상", "<=": "이하", ">": "초과", "<": "미만",
         "==": "= ", "!=": "≠ ", "in": "∈ ", "between": "범위"}


def load():
    units = {}
    for f in UNIT_FILES:
        for u in yaml.safe_load(open(os.path.join(HERE, f)))["units"]:
            units[u["id"]] = u
    segdoc = yaml.safe_load(open(os.path.join(HERE, "10_세그먼트.yaml")))
    segs = segdoc["segments"]
    routing = segdoc.get("routing", {})
    scen = yaml.safe_load(open(os.path.join(HERE, "30_시나리오.yaml")))["scenarios"]
    return units, segs, {s["id"]: s for s in scen}, routing


def cell(v):
    """표 셀 안에서 파이프·줄바꿈이 표를 깨지 않게."""
    if v is None:
        return "—"
    s = str(v).replace("|", "\\|").replace("\n", " ")
    return s


def fmt_value(v):
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float) and 0 < v <= 1:
        return f"{v:.0%}"
    if isinstance(v, int) and abs(v) >= 10000:
        return f"{v:,}원"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def rule_line(r, units):
    """rule dict 하나를 사람이 읽는 한 줄로."""
    if "any_of" in r:
        inner = [rule_line(x, units) for x in r["any_of"]]
        return "**다음 중 하나 이상**<br>" + "<br>".join("· " + i for i in inner)
    field = r.get("field", "")
    op = OP_KO.get(r.get("op"), r.get("op", ""))
    val = fmt_value(r.get("value"))
    if r.get("value") is None:
        cond = f"`{field}` — 임계값 미정"
    elif op in ("= ", "≠ ", "∈ "):
        cond = f"`{field}` {op}{val}"
    else:
        cond = f"`{field}` {val} {op}"
    return cond


def rule_rows(rules, units):
    rows = []
    for r in rules:
        if "any_of" in r:
            rows.append(("**다음 중 하나 이상**", "", "", ""))
            for x in r["any_of"]:
                rows.append(("　└ " + rule_line(x, units), x.get("as_of"),
                             x.get("source"), x.get("note")))
        else:
            rows.append((rule_line(r, units), r.get("as_of"),
                         r.get("source"), r.get("note")))
    return rows


def unit_title(uid, units):
    u = units.get(uid)
    return u["title"] if u else uid


def render_script_body(u):
    """화법 유닛을 인용 블록으로 펼친다."""
    out = []
    if u.get("branch"):
        for b in u["branch"]:
            out.append(f"**［{b['condition']}］**")
            out.append("")
            for line in b["body"].rstrip().split("\n"):
                out.append("> " + line if line.strip() else ">")
            out.append("")
    if u.get("body"):
        for line in u["body"].rstrip().split("\n"):
            out.append("> " + line if line.strip() else ">")
        out.append("")
    return out


def seg_doc(seg, units, scen_by_id):
    sid = seg["id"]
    L = []
    P = L.append

    used_scripts = []   # 본문에 대사를 펼친 화법 (중복 방지)

    # ── 머리말 ────────────────────────────────────────────
    P(f"# {CIRCLED[sid]} {seg['name']} — 고객군 정의와 지식")
    P("")
    P(f"> `{sid}` · 자동 생성 문서 (`_generate_docs.py`) — 수정은 원본 YAML에서 하세요")
    P("")
    P(f"**{seg['one_line']}**")
    P("")
    n_units = sum(1 for u in units.values() if sid in u["segments"])
    P("| 항목 | 내용 |")
    P("|---|---|")
    P(f"| 고객군 ID | `{sid}` |")
    P(f"| 1차 목적 | {', '.join(seg['business_goal'])} |")
    P(f"| 다른 이름 | {', '.join(seg.get('aliases') or ['—'])} |")
    P(f"| 지식 건수 | {n_units}건 |")
    P(f"| 케이스 시나리오 | {len(seg['scenarios'])}건 |")
    P(f"| 미결 과제 | {len(seg['open_questions'])}건 |")
    P("")
    P("---")
    P("")

    # ── 1. 대상 요건 ──────────────────────────────────────
    P("## 1. 어떤 요건이면 이 고객군인가")
    P("")
    P(f"**모집단** — {seg['population']['base']}")
    P("")
    P("### 1-1. 포함 조건 (이 중 하나라도 걸리면 후보)")
    P("")
    P("| 조건 | 기준시점 | 근거 | 비고 |")
    P("|---|---|---|---|")
    for c, a, s, n in rule_rows(seg["population"]["include"], units):
        src = f"`{s}` {unit_title(s, units)}" if s else "—"
        P(f"| {cell(c)} | {cell(a)} | {cell(src)} | {cell(n)} |")
    P("")

    P("### 1-2. 제외 조건 (걸리면 반드시 뺀다)")
    P("")
    P("| 조건 | 근거 | 왜 빼는가 |")
    P("|---|---|---|")
    for c, a, s, n in rule_rows(seg["population"]["exclude"], units):
        src = f"`{s}`" if s else "—"
        why = n
        if not why and s and units.get(s, {}).get("type") == "guardrail":
            why = units[s]["title"]      # 금지 유닛은 제목 자체가 사유
        P(f"| {cell(c)} | {cell(src)} | {cell(why or '해당 근거 문서 참조')} |")
    P("")

    # signal set (SEG-04 전용)
    if seg.get("signal_set"):
        P("### 1-3. 이탈 신호 목록 (2개 이상 겹치면 예방 대상)")
        P("")
        P("| 신호 | 근거의 무게 | 근거 |")
        P("|---|---|---|")
        for s in seg["signal_set"]:
            P(f"| {cell(s['signal'])} | {cell(s['weight'])} | `{s['ku']}` |")
        P("")

    # ── 2. 임계값 ────────────────────────────────────────
    P("## 2. 임계값 — 자료마다 다릅니다")
    P("")
    P("원천 자료가 서로 다른 숫자를 쓰는 항목입니다. **버리지 않고 전부 적재**한 뒤,")
    P("용도가 다른 것끼리 섞이지 않도록 하나만 기본값(✅)으로 골랐습니다.")
    P("")
    P("| 임계값 | 출처 | 기준시점 | 이 값이 만들어진 용도 | 기본값 |")
    P("|---|---|---|---|---|")
    for v in seg["threshold_variants"]:
        mark = "✅" if v["adopted"] else "—"
        P(f"| {cell(v['value'])} | {cell(v['source'])} | {cell(v['as_of'])} "
          f"| {cell(v['purpose'])} | {mark} |")
    P("")
    notes = [v for v in seg["threshold_variants"] if v.get("note")]
    if notes:
        P("**각 값에 붙은 단서**")
        P("")
        for v in notes:
            P(f"- {v['value']} → {v['note']}")
        P("")

    rt = seg["recommended_target"]
    P("### 권장 타겟 — 여기까지 좁혀서 시작하세요")
    P("")
    P(f"> **{rt['rule']}**")
    P("")
    P(f"예상 규모: {rt['expected_scale']}")
    P("")
    P("**왜 이렇게 좁혔나**")
    P("")
    for line in rt["rationale"].rstrip().split("\n"):
        P(line)
    P("")

    # ── 3. 오탐 제거 ─────────────────────────────────────
    P("## 3. 접촉 전 오탐 제거")
    P("")
    P("조건에 걸렸다고 다 대상이 아닙니다. 헛걸음 한 번이 고객 신뢰를 깎습니다.")
    P("")
    P("| 확인할 것 | 걸리면 | 근거 |")
    P("|---|---|---|")
    for f in seg["false_positive_filters"]:
        P(f"| {cell(f['check'])} | {cell(f['action'])} | `{f['source']}` |")
    P("")

    # ── 4. 데이터·화면 ───────────────────────────────────
    P("## 4. 타깃 추출에 필요한 것")
    P("")
    P("**데이터 항목**")
    P("")
    P("| 필요 데이터 |")
    P("|---|")
    for d in seg["data_requirements"]:
        P(f"| `{d}` |")
    P("")
    P("**사전 조회 화면**")
    P("")
    P("| 화면 | 이름 | 무엇을 보나 |")
    P("|---|---|---|")
    for s in seg["lookup_screens"]:
        P(f"| `{s['code']}` | {cell(s['name'])} | {cell(s['purpose'])} |")
    P("")

    # ── 5. 왜 지금 ───────────────────────────────────────
    P("## 5. 왜 지금 접촉하나 (접촉 명분)")
    P("")
    P("행원이 확신을 갖고 전화를 걸게 만드는 근거입니다.")
    P("")
    P("| 근거 | 수치 | 기준시점 | 지식 |")
    P("|---|---|---|---|")
    for w in seg["why_now"]:
        P(f"| {cell(w['claim'])} | {cell(w.get('number'))} | {cell(w.get('as_of'))} "
          f"| `{w.get('ku')}` |")
    P("")
    if seg.get("kpi_link"):
        k = seg["kpi_link"]
        P("### 실적 인정 요건 (사내전용)")
        P("")
        P(f"- **지표**: {k['name']}")
        P(f"- **인정 조건**: {k['recognition']}")
        P(f"- **기준시점**: {k['as_of']}")
        if k.get("caution"):
            P(f"- ⚠ {k['caution']}")
        P("")

    # ── 6. 우선순위 ──────────────────────────────────────
    P("## 6. 누구부터 접촉하나")
    P("")
    for i, k in enumerate(seg["priority"]["sort_keys"], 1):
        P(f"{i}. {k}")
    P("")
    if seg["priority"].get("scoring"):
        sc = seg["priority"]["scoring"]
        P(f"**추가 정렬**: {sc.get('method')} — {sc.get('source', '')}")
        if sc.get("note"):
            P(f"　{sc['note']}")
        P("")

    # ── 7. 브리핑 ────────────────────────────────────────
    P("## 7. 영업 전 — 브리핑 한 장에 무엇이 들어가나")
    P("")
    b = seg["briefing_slots"]
    P("| 브리핑 칸 | 이 고객군에서 채우는 내용 |")
    P("|---|---|")
    P(f"| 왜 대상인가 | {cell(b.get('why_target'))} |")
    P(f"| 계좌 스냅샷 | {cell(b.get('snapshot'))} |")
    P(f"| 고객이 모르는 자기 현황 | {cell(b.get('customer_unknown'))} |")
    P(f"| 오늘의 제안 | {cell(b.get('today_offer'))} |")
    obj = b.get("expected_objections") or []
    P(f"| 예상 반론 | {cell(', '.join(f'{o} {unit_title(o, units)}' for o in obj))} |")
    dn = b.get("do_not") or []
    P(f"| ⚠ 하지 말 것 | {cell(', '.join(f'{d} {unit_title(d, units)}' for d in dn))} |")
    P(f"| 통화 전 확인 화면 | {cell(', '.join(b.get('precheck_screens') or []))} |")
    P("")

    P("### 오늘의 제안 — 우선순위순")
    P("")
    P("| 순위 | 제안 | 어떤 조건일 때 | 화법 |")
    P("|---|---|---|---|")
    for o in seg["offers"]:
        ku = o.get("ku")
        P(f"| {o['rank']} | {cell(o['action'])} | {cell(o.get('condition'))} "
          f"| `{ku}` {cell(unit_title(ku, units))} |")
    P("")

    # ── 8. 시나리오 ──────────────────────────────────────
    P("## 8. 케이스 시나리오 — 상담이 어떻게 갈리나")
    P("")
    for scid in seg["scenarios"]:
        sc = scen_by_id[scid]
        P(f"### {scid} · {sc['name']}")
        P("")
        P("**이 경로로 들어오는 조건**")
        P("")
        for r in sc["entry_condition"]:
            P(f"- {rule_line(r, units)}")
        P("")
        P("**판별 질문 — 고객에게 이걸 물어야 분기가 정해집니다**")
        P("")
        for q in sc["qualifying_questions"]:
            P(f"- 「{q['ask']}」")
            for br in q["branches"]:
                P(f"    - {br}")
        P("")
        P("**진행 흐름**")
        P("")
        P("| 단계 | 무엇을 하나 | 쓰는 지식 |")
        P("|---|---|---|")
        for t in sc["turns"]:
            say = t.get("say")
            ref = f"`{say}` {unit_title(say, units)}" if say else "고객 발화"
            P(f"| {t['step']} | {cell(t.get('purpose'))} | {cell(ref)} |")
        P("")
        # 실제 대사
        scripts = [t["say"] for t in sc["turns"]
                   if t.get("say") and units.get(t["say"], {}).get("type") == "script"]
        fresh = [s for s in scripts if s not in used_scripts]
        if fresh:
            P("<details>")
            P("<summary><b>이 경로에서 실제로 하는 말 (펼치기)</b></summary>")
            P("")
            for s in fresh:
                u = units[s]
                P(f"**`{s}` {u['title']}**")
                P("")
                for line in render_script_body(u):
                    P(line)
                if u.get("cautions"):
                    for c in u["cautions"]:
                        P(f"- ⚠ {c}")
                    P("")
                used_scripts.append(s)
            P("</details>")
            P("")
        P("**예상 반론 — 고객이 이렇게 말하면**")
        P("")
        for o in sc["objection_cards"]:
            P(f"- `{o}` {unit_title(o, units)}")
        P("")
        obj_fresh = [o for o in sc["objection_cards"]
                     if o not in used_scripts and units.get(o, {}).get("type") == "script"]
        if obj_fresh:
            P("<details>")
            P("<summary><b>반론 즉답 카드 원문 (펼치기)</b></summary>")
            P("")
            for o in obj_fresh:
                u = units[o]
                P(f"**`{o}` {u['title']}**")
                P("")
                P(f"*트리거: {u['trigger']}*")
                P("")
                for line in render_script_body(u):
                    P(line)
                if u.get("cautions"):
                    for c in u["cautions"]:
                        P(f"- ⚠ {c}")
                    P("")
                used_scripts.append(o)
            P("</details>")
            P("")
        P("**상담 결과별 처리**")
        P("")
        sa = sc["success_action"]
        P(f"- **합의** — 장바구니: {', '.join(sa.get('cart_items') or [])}")
        if sa.get("procedure_ku"):
            P(f"    - 처리 절차: {', '.join(f'`{p}` ' + unit_title(p, units) for p in sa['procedure_ku'])}")
        if sa.get("memo_template"):
            P(f"    - 상담메모 초안: 「{sa['memo_template']}」")
        pa = sc.get("partial_action") or {}
        if pa:
            P(f"- **부분 합의·보류** — {', '.join(pa.get('cart_items') or [])}")
            if pa.get("note"):
                P(f"    - {pa['note']}")
        fa = sc.get("fail_action") or {}
        if fa:
            say = fa.get("say")
            P(f"- **거절** — {('`' + say + '` ' + unit_title(say, units)) if say else ''}")
            if fa.get("followup"):
                P(f"    - 후속: {fa['followup']}")
        P("")
        P("**이 경로에서 금지**")
        P("")
        for pr in sc["prohibited"]:
            P(f"- `{pr}` {unit_title(pr, units)}")
        P("")
        if sc.get("expected_conversion"):
            ec = sc["expected_conversion"]
            P(f"**기대 전환율 기준선** — {ec.get('baseline')} ({ec.get('as_of')}, `{ec.get('source')}`)")
            P("")
        P("---")
        P("")

    # ── 9. 금지 ──────────────────────────────────────────
    P("## 9. 하지 말 것")
    P("")
    P("이 고객군에서 특히 자주 걸리는 금지 사항입니다. 실수는 곧 민원입니다.")
    P("")
    for g in seg["guardrails"]:
        u = units[g]
        P(f"### `{g}` {u['title']}")
        P("")
        P(f"*발동 시점: {u['trigger']}*")
        P("")
        for line in u["body"].rstrip().split("\n"):
            P(line)
        P("")
        if u.get("cautions"):
            for c in u["cautions"]:
                P(f"- {c}")
            P("")
        if u["status"] != "확정":
            P(f"> 상태: **{u['status']}** — {u['source']['doc']}")
            P("")

    # ── 10. 후속 ─────────────────────────────────────────
    P("## 10. 후속 조치 — 다음 접점을 그 자리에서 심는다")
    P("")
    P("| 언제 | 주기 | 무엇을 |")
    P("|---|---|---|")
    for f in seg["followup"]:
        P(f"| {cell(f['trigger'])} | {cell(f['interval'])} | {cell(f['action'])} |")
    P("")

    # ── 11. 지식 목록 ────────────────────────────────────
    P("## 11. 이 고객군이 쓰는 지식 전체 목록")
    P("")
    P("신뢰도와 공개등급을 함께 봅니다 — **`사내전용`은 고객 앞에서 언급할 수 없고,**")
    P("**`검증필요`는 본부·원본 대조 전에는 인용할 수 없습니다.**")
    P("")
    P("| ID | 유형 | 제목 | 신뢰 | 공개 | 상태 |")
    P("|---|---|---|---|---|---|")
    for uid in sorted(u for u in units if sid in units[u]["segments"]):
        u = units[uid]
        P(f"| `{uid}` | {TYPE_KO[u['type']]} | {cell(u['title'])} "
          f"| {u['trust']} | {u['disclosure']} | {u['status']} |")
    P("")

    # ── 12. 미결 ─────────────────────────────────────────
    P("## 12. 기획 확인이 필요한 것")
    P("")
    P("**팀·본부 확정이 필요한 사항**")
    P("")
    for q in seg["open_questions"]:
        P(f"- [ ] {q}")
    P("")
    unverified = [units[u] for u in sorted(units)
                  if sid in units[u]["segments"] and units[u]["status"] == "검증필요"]
    if unverified:
        P("**원본 대조 전 인용 불가 (검증필요)**")
        P("")
        P("| ID | 제목 | 확인할 것 |")
        P("|---|---|---|")
        for u in unverified:
            cau = (u.get("cautions") or ["—"])[0]
            P(f"| `{u['id']}` | {cell(u['title'])} | {cell(cau)} |")
        P("")

    P("---")
    P("")
    P("*원본 데이터: [`10_세그먼트.yaml`](10_세그먼트.yaml) · "
      "[`20`](20_지식유닛.yaml)·[`21`](21_지식유닛_화법.yaml)·"
      "[`22_지식유닛`](22_지식유닛_팩트_절차_금지.yaml) · "
      "[`30_시나리오.yaml`](30_시나리오.yaml) — "
      "이 문서는 `_generate_docs.py`로 생성됩니다.*")
    return "\n".join(L) + "\n"



def overview_doc(segs, units, scen_by_id, routing):
    """01_고객군_전체지도.md — 6개 고객군을 한 장에서 비교하는 진입 문서."""
    L = []
    P = L.append
    by_id = {s["id"]: s for s in segs}

    P("# 고객군 전체 지도 — 6종 한눈에 보기")
    P("")
    P("> 자동 생성 문서 (`_generate_docs.py`) — 수정은 원본 YAML에서 하세요")
    P("")
    P("사후관리 대상 개인형IRP 고객을 6개 군으로 나눴습니다.")
    P("고객군마다 **왜 지금 접촉하는가**와 **무엇을 제안하는가**가 다르고, 쓰는 화법도 다릅니다.")
    P("")

    P("## 1. 6개 고객군 비교")
    P("")
    P("| # | 고객군 | 한 줄 정의 | 1차 목적 |")
    P("|---|---|---|---|")
    for s in segs:
        P(f"| {CIRCLED[s['id']]} | **[{s['name']}]({FILENAME[s['id']]})** "
          f"| {cell(s['one_line'])} | {', '.join(s['business_goal'])} |")
    P("")

    P("### 권장 타겟 — 각 고객군을 어디까지 좁혔나")
    P("")
    P("| 고객군 | 권장 타겟 조건 |")
    P("|---|---|")
    for s in segs:
        P(f"| {CIRCLED[s['id']]} {s['name']} | {cell(s['recommended_target']['rule'])} |")
    P("")

    P("### 규모")
    P("")
    P("| 고객군 | 지식 | 시나리오 | 임계값 변형 | 미결 과제 |")
    P("|---|---|---|---|---|")
    for s in segs:
        n = sum(1 for u in units.values() if s["id"] in u["segments"])
        P(f"| {CIRCLED[s['id']]} {s['name']} | {n} | {len(s['scenarios'])} "
          f"| {len(s['threshold_variants'])} | {len(s['open_questions'])} |")
    P("")

    P("## 2. 한 고객이 여러 군에 걸릴 때 — 무엇을 먼저 보나")
    P("")
    P("실제 고객은 대부분 2개 이상에 해당합니다. 아래 순서로 판정합니다.")
    P("")
    P("| 순위 | 고객군 | 이 조건이면 | 왜 먼저인가 |")
    P("|---|---|---|---|")
    for i, r in enumerate(routing.get("precedence", []), 1):
        sname = by_id[r["segment"]]["name"]
        P(f"| {i} | {CIRCLED[r['segment']]} {sname} | {cell(r['when'])} | {cell(r['reason'])} |")
    P("")
    P("원칙은 **기한이 붙은 것 먼저**입니다 — 전출 알림·ISA 60일·퇴직금 60일·만기 D-30은")
    P("지나면 기회가 소멸하지만, 편중·수익률·방치는 다음 달에도 그대로 있습니다.")
    P("")

    P("## 3. 고객군은 서로 옮겨간다 (방치했을 때의 전이)")
    P("")
    P("고객군은 고정된 속성이 아니라 **상태**입니다. 손대지 않으면 아래로 흘러갑니다.")
    P("")
    P("```mermaid")
    P("flowchart LR")
    P("    S6[\"⑥ 만기예정\"] -->|만기 후 운용지시 없음| S2")
    P("    S3[\"③ 방치\"] -->|퇴직금 입금 후 미운용| S2")
    P("    S1[\"① 수익률 저조\"] -->|매도만 하고 매수 미실행| S2")
    P("    S5[\"⑤ 추가입금\"] -->|입금 후 운용지시 없음| S3")
    P("    S2[\"② 자산편중\"] -->|방치| S4")
    P("    S3 -->|미접촉 지속| S4")
    P("    S4[\"④ 이탈\"] -->|방어 성공 & 퇴직금 포함| S5")
    P("```")
    P("")
    P("| 출발 | 도착 | 무엇이 방아쇠인가 | 설명 |")
    P("|---|---|---|---|")
    for t in routing.get("transitions", []):
        fn, tn = by_id[t["from"]]["name"], by_id[t["to"]]["name"]
        P(f"| {CIRCLED[t['from']]} {fn} | {CIRCLED[t['to']]} {tn} "
          f"| {cell(t['trigger'])} | {cell(t.get('note'))} |")
    P("")
    P("이 표가 말하는 것은 하나입니다 — **이탈방어의 진짜 시점은 이탈 통보 전이고,**")
    P("**그 앞단이 만기·방치·편중입니다.**")
    P("")

    P("## 4. 어느 고객군에서도 공통으로 빼는 것")
    P("")
    P("| 제외 조건 | 적용 고객군 | 근거 |")
    P("|---|---|---|")
    for c in routing.get("common_exclusions", []):
        segs_s = ", ".join(CIRCLED[x] for x in c["applies_to"])
        P(f"| {cell(c['rule'])} | {segs_s} | `{c['source']}` {unit_title(c['source'], units)} |")
    P("")

    P("## 5. 전 고객군 공통 금지")
    P("")
    common_g = sorted(
        (u for u in units.values() if u["type"] == "guardrail" and len(u["segments"]) >= 3),
        key=lambda u: -len(u["segments"]))
    P("| 금지 | 적용 고객군 수 | 요지 |")
    P("|---|---|---|")
    for u in common_g:
        head = u["body"].strip().split("\n")[0]
        P(f"| `{u['id']}` {cell(u['title'])} | {len(u['segments'])} | {cell(head)} |")
    P("")

    P("## 6. 데이터 요건 통합 — 어느 필드가 어느 고객군에 쓰이나")
    P("")
    P("타깃 추출 시스템에 필요한 필드 목록입니다.")
    P("")
    field_map = {}
    for s in segs:
        for d in s["data_requirements"]:
            field_map.setdefault(d, []).append(s["id"])
    P("| 데이터 필드 | 쓰는 고객군 |")
    P("|---|---|")
    for f in sorted(field_map, key=lambda k: (-len(field_map[k]), k)):
        P(f"| `{f}` | {' '.join(CIRCLED[x] for x in field_map[f])} |")
    P("")

    P("## 7. 조회 화면 통합")
    P("")
    screen_map = {}
    for s in segs:
        for sc in s["lookup_screens"]:
            screen_map.setdefault((sc["code"], sc["name"]), []).append(s["id"])
    P("| 화면 | 이름 | 쓰는 고객군 |")
    P("|---|---|---|")
    for (code, name) in sorted(screen_map, key=lambda k: (-len(screen_map[k]), k[0])):
        P(f"| `{code}` | {cell(name)} | {' '.join(CIRCLED[x] for x in screen_map[(code, name)])} |")
    P("")

    P("## 8. 기획 확인이 필요한 것 (전체)")
    P("")
    for s in segs:
        P(f"**{CIRCLED[s['id']]} {s['name']}**")
        P("")
        for q in s["open_questions"]:
            P(f"- [ ] {q}")
        P("")

    P("---")
    P("")
    P("**고객군별 상세 문서**")
    P("")
    for s in segs:
        P(f"- {CIRCLED[s['id']]} [{s['name']}]({FILENAME[s['id']]}) — {s['one_line']}")
    P("")
    P("*원본 데이터: [`10_세그먼트.yaml`](10_세그먼트.yaml) 외 — "
      "이 문서는 `_generate_docs.py`로 생성됩니다.*")
    return "\n".join(L) + "\n"


def main():
    units, segs, scen_by_id, routing = load()
    with open(os.path.join(HERE, "01_고객군_전체지도.md"), "w") as f:
        f.write(overview_doc(segs, units, scen_by_id, routing))
    print("generated 01_고객군_전체지도.md")
    for seg in segs:
        path = os.path.join(HERE, FILENAME[seg["id"]])
        with open(path, "w") as f:
            f.write(seg_doc(seg, units, scen_by_id))
        print(f"generated {FILENAME[seg['id']]}")


if __name__ == "__main__":
    main()
