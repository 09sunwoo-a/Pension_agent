"""고객별 솔루션 도구(plan 호출) — 계산 결과를 원장 근거(Evidence)로 전달, 재판정 금지.

    customer    부분 질문 재료(LLM 미호출)
    briefing    전체 브리핑 문서 — compose 미경유, 그대로 답변
    suitable    투자성향 적합성 범위
    outreach    안내 세미나·이벤트와 발송 문구
    branch_targets  관리 대상 추천 — 오늘의 타겟 고객 목록(고객 화면 열기 전 · CASE 순서 · 첫 추천 유지)

KB-PIN(고객 식별번호)은 재료·출처 id·제목 전부 미포함 — 게이트웨이 개인정보 필터가 주민번호로 보고 차단.
"""

from __future__ import annotations

import json

from pension_agent.common import clock
from pension_agent.evidence.record import Evidence, _ev, _scope
from pension_agent.knowledge import kb as KBMOD
from pension_agent.state import KB, AgentState
from pension_agent.tools.kb_search import kb_index
from pension_agent.tools.kb_search.cards import advisory_mark


def _customer(state: AgentState, query: str) -> Evidence | None:
    """열린 고객의 브리핑 재료(solution 산출 그대로). 고객 재료로 답하는 유일한 경로."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import solution  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        facts = solution.facts(profile)
        # 기존 서술만 수록(부분 질문에 LLM 미호출)
        narrative = solution.peek(profile) or {}
    except Exception:
        return None

    # KB-PIN 미포함 — 게이트웨이 개인정보 필터가 400 응답
    lines = [f"■ 고객 {profile.nm} — 브리핑 자료"]
    lines += [f"· {k} {v}" for k, v in facts["customer"].items()]
    lines += [f"· {k} {v}" for k, v in facts["briefing"].items() if k != "source"]
    # 계좌 상태 — 정상 항목도 수록
    lines += [f"· {k.replace('_', ' ')} {v}" for k, v in (facts.get("account_state") or {}).items()]
    if facts.get("conditions"):
        lines.append(f"· 성립 요건: {', '.join(_cond_labels(facts['conditions']))}")
    # 왜 관리 대상인가 — 근거·문제상황까지 수록(요건 이름만이면 LLM 이 지어냄)
    for label, values in (("왜 이 고객인가", facts.get("why_this_customer")),
                          ("판단근거", facts.get("rationale")),
                          ("고지 필요", facts.get("cautions")),
                          ("확인 필요", facts.get("needs_confirm"))):
        for v in values or []:
            lines.append(f"· {label}: {v}")
    for sit in facts.get("problem_situations") or []:
        lines.append(f"· 문제상황 {sit['no']}: {sit['title']} [{sit['group']}]")
    # 화면 ⑥⑦⑧ 선정 카드 — facts 그대로(재선정 금지)
    card_sources: list[dict] = []
    card_keys: dict[str, list[str]] = {}
    card_lines: list[str] = []
    for label, items, keys in (("이렇게 말해보세요", facts.get("talking_points"), ("title", "talk")),
                               ("예상 반론", facts.get("objections"), ("objection", "response")),
                               ("상담 참고", facts.get("consult_resources"), ("title", "snippet"))):
        for item in items or []:
            head, body = (str(item.get(k) or "").strip() for k in keys)
            if not head and not body:
                continue
            # 카드 id 미포함 — 재료의 id 가 답변에 그대로 노출(id 는 출처에만)
            mark = item.get("situation")
            card_lines.append(f"· {label}: {head} — {body}" + (f" [{mark}]" if mark else ""))
            # 카드 출처(관련도 없음). URL 은 kb 에서 재조회 — solution 항목에는 문서명뿐
            if item.get("card_id"):
                meta = KBMOD.card_source_meta(KB, item["card_id"])
                card_sources.append({"id": item["card_id"], "title": head,
                                     "doc": meta.get("doc") or item.get("source") or "출처 미상",
                                     "url": meta.get("url"),
                                     "score": None, "page": None})
                # 답변 사용 여부 판정 스팬 — 묶음 재료라 미사용 카드는 근거에서 제외
                card_keys.setdefault(item["card_id"], []).extend([head, body])
    lines += card_lines
    # 전체 브리핑 AI 분석(서술, 원문 스팬 아님)
    for key, label in solution.NARRATIVE_FIELDS.items():
        value = narrative.get(key)
        for text in (value if isinstance(value, list) else [value]):
            if text:
                lines.append(f"· 브리핑 {label}: {text}")
    # allow = facts 전체(후보 더미 제외), atomic·notices 없음(원문 강제 시 표 덤프)
    # 출처는 고객 원장(카드 아님, 관련도 없음). ⑥⑧ 중복 카드는 첫 등장만
    seen: set[str] = set()
    deduped = [s for s in card_sources if not (s["id"] in seen or seen.add(s["id"]))]
    # 레이블–값 짝 선언 — 집합 검사만으로는 ⑥⑦⑧ 수치가 엉뚱한 레이블 값으로 통과
    labeled = [{"label": k.replace("_", " "), "value": str(v)}
               for src in (facts["customer"],
                           {k: v for k, v in facts["briefing"].items() if k != "source"},
                           facts.get("account_state") or {})
               for k, v in src.items()]
    return _ev("customer", query, "\n".join(lines),
               # 출처 id·제목 KB-PIN 미포함 — sources 이벤트가 게이트웨이 필터에 차단
               [{"id": "customer",
                 "title": f"{profile.nm} 고객 계좌 현황",
                 "doc": "고객 정보 — 계좌 원장 조회값 (브리핑 화면과 같은 값)",
                 "score": None, "page": None}, *deduped],
               source_keys=card_keys,
               allow=["\n".join(lines), json.dumps(_citable(facts), ensure_ascii=False, default=str)],
               cards=[{"id": "customer", "labeled": labeled,
                       # relations.checkable 용 재료 전문. 밑줄→공백 필수 — 레이블과 표기가 달라
                       # 이름 중복 판정이 꺼지고 카드 원문을 고객 값 주장으로 오독
                       "context": "\n".join(lines).replace("_", " ")}])


#: 인용 허용에서 뺄 facts 가지(선별 전 후보 더미 — 무관 수치가 오답 근거가 됨)
_POOL_KEYS = ("pools",)


def _citable(facts: dict) -> dict:
    """인용 허용용 facts(후보 더미 제외)."""
    return {k: v for k, v in facts.items() if k not in _POOL_KEYS}


def _cond_labels(conditions: list[str]) -> list[str]:
    """성립 요건 `코드:이름` 에서 이름만(코드는 답변에 그대로 노출)."""
    return [c.split(":", 1)[1] if ":" in c else c for c in conditions]


def _briefing(state: AgentState, query: str) -> Evidence | None:
    """전체 브리핑 문서 — compose 미경유, `meta["document"]` 가 그대로 답변."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import solution  # noqa: PLC0415
    profile = strategy_customer.get_profile(customer_id)
    if profile is None:
        return None
    doc = solution.document(profile)
    facts = solution.facts(profile)
    return _ev("briefing", query, doc["markdown"],
               [{"id": "customer", "title": f"{profile.nm} 고객 브리핑",
                 "doc": "고객 정보 — 계좌 원장 조회값과 AI 분석",
                 "score": None, "page": None}],
               allow=[doc["markdown"], json.dumps(_citable(facts), ensure_ascii=False, default=str)],
               meta={"document": doc["markdown"], "tier": doc["tier"],
                     "narrative": bool(doc["narrative"])})

# 제외 상품은 전부 수록 — 머리말 개수와 목록 개수 불일치 시 verify 가 답 폐기


def _suitable(state: AgentState, query: str) -> Evidence | None:
    """적합성 허용 범위·통과 상품·제외 사유(engine 게이트 산출 그대로, 권유 아님). 고객 미선택 시 None."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import engine  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        pool = engine.candidate_pool_for_recommendation(profile)
        passed = pool["products"]
        # 상한은 게이트와 같은 계산 — 다르면 상한 문구와 목록 불일치
        cap = profile.grade
        conds = strategy_customer.conditions(profile)
        if "mis" in conds and strategy_customer.PREF.get(profile.rk):
            cap = strategy_customer.RISK[min(
                strategy_customer.RISK.index(cap),
                strategy_customer.RISK.index(strategy_customer.PREF[profile.rk]))]
        blocked: list[tuple[dict, str]] = []
        for row in engine.query_products(engine.PRODUCTS):
            ok, why = engine.gate_static(row, profile, cap)
            if not ok:
                blocked.append((row, why))
    except Exception:
        return None
    if not passed and not blocked:
        return None
    advice = advisory_mark({"advisory": kb_index.advisory_note(KB)})

    # KB-PIN 미포함 — 게이트웨이 개인정보 필터가 400 응답
    lines = [f"■ 고객 {profile.nm} — 투자성향 {profile.rk} · 위험등급 {profile.grade}",
             f"· 적합성 허용 상한: {cap} (이 등급까지의 상품만 안내할 수 있다)",
             "",
             f"── 안내할 수 있는 상품 {len(passed)}종"]
    for r in passed:
        ret = engine.product_return(r)
        tail = f" · 최근 1년 {ret}%" if ret is not None else ""
        lines.append(f"· {r['name']} — {r['risk']}{tail}"
                     + (f" · {r['category']}" if r.get("category") else ""))
    for pf in pool["portfolios"]:
        lines.append(f"· [포트폴리오] {pf['name']} — {pf.get('description') or ''}".rstrip())
    if blocked:
        # 줄마다 반복되는 « > 허용 상한 Y» 꼬리만 제거(머리말에 1회 명시). 판정 재수행 없음
        cap_tail = f" > 허용 상한 {cap}"
        lines += ["", f"── 안내할 수 없는 상품 {len(blocked)}종 (왜 목록에 없는지 — "
                      f"등급만 적힌 줄은 허용 상한 {cap}을 넘는다는 뜻이다)"]
        lines += [f"· {r['name']} — {why.replace(cap_tail, '')}" for r, why in blocked]
    else:
        # 0건도 명시 — 침묵 시 LLM 이 통과 목록에서 제외 상품을 지어냄
        lines += ["", "── 안내할 수 없는 상품 없음 "
                      f"(허용 상한이 {cap}이라 카탈로그 전부가 범위 안이다)"]
    return _ev("suitable", query, "\n".join(lines),
               # id·제목 KB-PIN 미포함
               [{"id": "suitable",
                 "title": f"{profile.nm} 고객 적합성 판정",
                 "doc": "투자성향 적합성 확인 — 위험등급 상한·거래채널 판정 결과 "
                        "(브리핑 화면 ⑤ 와 같은 후보군)",
                 "score": None, "page": None}],
               # 고지 문구는 지식베이스 선언 그대로(코드 상수 금지)
               notices=[advice] if advice else [],
               scopes=[_scope("적합성 판정", [], [advice])] if advice else [])


def _outreach(state: AgentState, query: str) -> Evidence | None:
    """⑨ 안내 세미나·이벤트 — 화면 ⑨ 선정·후보군·`lms_message` 그대로(재선정·문구 재생성 금지)."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return None
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import solution  # noqa: PLC0415
    try:
        profile = strategy_customer.get_profile(customer_id)
        if profile is None:
            return None
        facts = solution.facts(profile)
    except Exception:
        return None

    picked = facts.get("outreach") or {}
    pools = ((facts.get("pools") or {}).get("outreach")) or {}
    if not (picked.get("event") or picked.get("seminar")):
        return None

    # 요건 일치와 임박 순 폴백 구분 — 미구분 시 폴백을 «적합»으로 답변. 판정은 추천 칩과 같은 함수
    from pension_agent.tools.customer_solution import support as strategy_support  # noqa: PLC0415
    try:
        relevant_ids = {r["id"] for r in strategy_support.relevant_outreach(
            facts.get("problem_situations") or [], name=profile.nm)}
    except Exception:
        relevant_ids = set()
    matched = {key: bool(picked.get(key)) and picked[key]["id"] in relevant_ids
               for key in ("event", "seminar")}

    # 개수 수록 필수 — 답변의 건수·열거 번호가 재료 밖 수치로 폐기됨
    n_picked = sum(1 for key in ("event", "seminar") if matched[key])
    n_fallback = sum(1 for key in ("event", "seminar") if picked.get(key) and not matched[key])
    n_other = {key: max(len(pools.get(key) or []) - (1 if picked.get(key) else 0), 0)
               for key in ("event", "seminar")}
    # 다른 후보 중 요건 일치분도 표시 — 미표시 시 폴백을 적합으로 오인
    n_other_fit = {key: sum(1 for c in (pools.get(key) or [])
                            if c["id"] in relevant_ids and c["id"] != (picked.get(key) or {}).get("id"))
                   for key in ("event", "seminar")}
    wanted = {c for s in (facts.get("problem_situations") or []) for c in (s.get("conds") or [])}

    def _fit_names(item: dict) -> list[str]:
        """콘텐츠와 일치하는 고객 요건 이름."""
        return [strategy_customer.CONDS.get(c, c) for c in (item.get("conds") or []) if c in wanted]
    # KB-PIN 미포함 — 게이트웨이 개인정보 필터가 400 응답
    lines = [f"■ 고객 {profile.nm} — 안내할 이벤트·세미나 (브리핑 ⑨ 와 같은 선정)",
             f"· 지금 안내할 것 {n_picked}건 — "
             + " · ".join(f"{label} {1 if matched[key] else 0}건"
                          for key, label in (("event", "이벤트"), ("seminar", "세미나")))
             + (f" · 이 고객 요건과 무관한 것 {n_fallback}건" if n_fallback else "")
             + f" · 아직 열려 있는 다른 후보 이벤트 {n_other['event']}건 · "
               f"세미나 {n_other['seminar']}건"
             + (f" · 다른 후보 중 이 고객 요건에 맞는 것 "
                f"{n_other_fit['event'] + n_other_fit['seminar']}건"
                if n_other_fit["event"] or n_other_fit["seminar"] else "")]
    if n_picked == 0:
        lines.append("· 이 고객 요건에 맞는 콘텐츠는 없다. 아래는 열려 있는 콘텐츠 중 날짜가 "
                     "가까운 것이며, 이 고객에게 맞는 것으로 안내하지 않는다")
    atomic: list[str] = []
    lms: dict[str, dict] = {}
    messages: list[str] = []
    for key, label in (("event", "이벤트"), ("seminar", "세미나")):
        item = picked.get(key)
        if not item:
            continue
        lines.append(f"· [{label}] {item['name']} — {item['schedule']} · 주관 {item.get('organizer') or '미상'}")
        if item.get("description"):
            lines.append(f"  내용: {item['description']}")
        if matched[key]:
            lines.append(f"  요건 일치: {', '.join(_fit_names(item)) or '있음'}")
            if item.get("reason"):
                lines.append(f"  추천 사유: {item['reason']}")
        else:
            # 폴백은 사유 미수록(«맞춤» 근거로 오용)
            lines.append("  요건 일치: 없음 — 이 고객 요건과 무관. 열려 있는 콘텐츠 중 날짜가 가까운 것")
        if item.get("keywords"):
            # 폴백 키워드는 «매칭» 표기 금지 — 고객 상태로 오인
            lines.append(f"  매칭 키워드: {', '.join(item['keywords'])}" if matched[key]
                         else f"  콘텐츠 대상 키워드(이 고객과 무관): {', '.join(item['keywords'])}")
        if item.get("url"):
            lines.append(f"  안내 링크: {item['url']}")
            # URL 원문 그대로 강제
            atomic.append(item["url"])
        lines.append(f"  발송 문구: {item['lms_message']}")
        if not matched[key]:
            # 폴백 문구 속 고객 상태 표현을 적합 근거로 쓰는 것 차단
            lines.append("  주의: 요건 무관 콘텐츠의 문구다 — 문구 안의 말을 이 고객에게 "
                         "맞는다는 근거로 쓰지 않는다")
        # 화면 «복사할 문구» 판별 원본(actions/messages.py) — 요건 일치 무관
        messages.append(item["lms_message"])
        # 발송 제안은 요건 일치분만(폴백 발송 금지)
        if matched[key]:
            # url 동봉 — act._mentions 가 링크로 지칭 판정
            lms[key] = {"id": item["id"], "name": item["name"], "message": item["lms_message"],
                        "url": item.get("url") or ""}
        # 다른 후보(선정분 제외)
        others = [c for c in (pools.get(key) or []) if c["id"] != item["id"]]
        if others:
            lines.append(f"  다른 {label} 후보 {len(others)}건:")
        for other in others:
            fit = _fit_names(other) if other["id"] in relevant_ids else []
            lines.append(f"  · {other['name']} — {other['schedule']}"
                         + (f" · 요건 일치: {', '.join(fit)}" if fit else ""))

    for label, values in (("문제상황", [s["title"] for s in facts.get("problem_situations") or []]),
                          ("성립 요건", _cond_labels(facts.get("conditions") or []))):
        if values:
            lines.append(f"· {label}: {', '.join(values[:4])}")
    # LLM 미생성 사실 표시
    for key, why in (facts.get("llm_skipped") or {}).items():
        if key.startswith("outreach") or key == "lms_message":
            lines.append(f"· 참고: {key} — {why}")

    return _ev("outreach", query, "\n".join(lines),
               # id KB-PIN 미포함
               [{"id": "outreach",
                 "title": "고객님께 안내해보세요 — 열려 있는 이벤트·세미나",
                 "doc": "안내 콘텐츠 레지스트리 (브리핑 ⑨ 와 같은 산출)",
                 "score": None, "page": None}],
               atomic=atomic,
               # 승낙 턴이 문구를 재생성하지 않도록 이번 턴 산출 동봉
               meta={"lms": lms, "messages": messages})


def first_targets(history: list[dict] | None) -> dict | None:
    """이 대화의 첫 추천 목록(`Turn.first_targets` — graph.ask 가 턴마다 이어 붙인다)."""
    for turn in reversed(history or []):
        if turn.get("first_targets"):
            return turn["first_targets"]
    return None


def roster_targets(state: AgentState | None) -> tuple[str | None, list]:
    """(부점명, 관리 대상 Target 목록). 모집단은 직원 부점의 부점 DB 관리 대상(roster).

    사번·부점을 모르거나 DB 를 못 읽으면 (None, 목업 고객 명부) — 부점을 가리지 않는 예전 목록이다.
    """
    from pension_agent.common import note  # noqa: PLC0415
    from pension_agent.tools.branch_briefing import roster, run  # noqa: PLC0415
    from pension_agent.tools.customer_solution import target_list  # noqa: PLC0415

    emp_no = ((state or {}).get("employee_id") or "").strip() or None
    if run.backend() != "off" and emp_no:
        try:
            scope, profiles = roster.branch_profiles(emp_no)
            return scope, target_list.today_targets(profiles)
        except (LookupError, run.DBUnavailable):
            pass
    return None, note.today_targets()


def _today_focus(state: AgentState, scope: str | None) -> tuple[list[dict], set[str] | None]:
    """(오늘 성립한 CASE, 중점관리 고객 이름). 부점 명부가 아니거나 현황을 못 구하면 ([], None)."""
    from pension_agent.tools.branch_briefing import roster, run, status  # noqa: PLC0415

    if not scope:
        return [], None
    emp_no = ((state or {}).get("employee_id") or "").strip() or None
    try:
        cases = status.compute(emp_no)["cases"]
        return cases, roster.focus_names(emp_no, [c["id"] for c in cases])
    except (LookupError, run.DBUnavailable, status.RuleError):
        return [], None


def _targets(state: AgentState, query: str) -> Evidence | None:
    """관리 대상 추천(branch_targets) — 오늘의 중점관리 고객을 오늘의 부점 CASE 순서로.

    선정은 target_list(요건 판정·PRIO) 그대로이고, 그중 오늘 성립한 CASE 의 중점관리 조건(룰베이스 부점 시트
    «부점CASE_중점조건» — AND)을 전부 만족하는 고객이 오늘의 중점관리 고객이다(2026-10-22 결정). 순서는 CASE 가
    정한다(target_list.prioritize). 인원 상한은 부점_임계값 표(status.target_limits — 2026-10-22 사용자 결정
    중점관리 100명)이고, 넘으면 우선순위 앞쪽만 싣고 전체 인원을 밝힌다. 성립한 CASE 가 없거나 CASE 조건을
    모두 갖춘 고객이 0명이면, 중점관리 고객이 아님을 밝히고 관리 대상을 우선순위 앞쪽부터 상한만큼 싣는다.
    이 대화에서 이미 추천했으면 그 첫 목록을 다시 계산하지 않고 그대로 싣는다(날짜가 바뀌었으면 새로).
    고객 화면 없이 사용. LLM 재료·첫 목록은 PIN 미포함, 최종 표만 마스킹된 PIN 표시.
    """
    from pension_agent.common import note  # noqa: PLC0415 — 순환 임포트 회피
    from pension_agent.tools.branch_briefing import status  # noqa: PLC0415
    from pension_agent.tools.customer_solution import target_list, target_table  # noqa: PLC0415
    from pension_agent.tools import customer_selection  # noqa: PLC0415

    day = clock.today().isoformat()
    stored = first_targets((state or {}).get("history"))
    replay = bool(stored and stored.get("date") == day)
    today: list[dict] = []
    focus: set[str] | None = None
    try:
        scope, pool = roster_targets(state)
        if replay:
            found = (target_list.by_refs(stored["refs"], pool) if stored.get("refs") and all(stored["refs"])
                     else target_list.by_names(list(stored.get("names") or []), pool))
            codes = list(stored.get("codes") or [])
        else:
            today, focus = _today_focus(state, scope)
            codes = []
            for c in today:
                codes += [x for x in c["codes"] if x not in codes]
            chosen = [t for t in pool if t.profile.nm in focus] if focus else pool
            found = target_list.prioritize(chosen, codes)
    except Exception:
        return None
    total = len(found)
    if not replay:
        focus_max, plain_max = status.target_limits()
        found = found[:focus_max if focus else plain_max]
    # 오늘 CASE 는 섰는데 그 조건을 모두 갖춘 고객이 없다 — 다른 목록을 중점관리처럼 세우지 않는다
    none_fit = not replay and bool(today) and not focus
    cases = {c: n for c, n in note.CONDS.items()}
    where = f"{scope} · " if scope else ""
    if replay:
        head = f"■ 관리 대상 추천 — {where}이 대화의 첫 추천({day})을 그대로 보여드립니다 · {len(found)}명"
    elif focus:
        head = f"■ 관리 대상 추천(오늘의 중점관리 고객) — {where}{day} 기준 · {total}명"
    else:
        head = f"■ 관리 대상 추천 — {where}{day} 기준 · 관리 대상 {total}명"
    lines = [head]
    if focus:
        # 왜 이 사람들인가 — 관리 대상 중 오늘의 관리 방향 «우선관리 고객 특징»을 모두 갖춘 고객
        lines.append(f"· 관리 대상 {len(pool)}명 중 오늘의 관리 방향의 우선관리 고객 조건을 모두 갖춘 고객입니다.")
    if focus or none_fit:
        for c in today:
            notes = "; ".join(n for n in c.get("focus_notes") or [] if n)
            lines.append(f"· 오늘의 관리 방향 «{c['name']}» — 우선관리 고객: {c['feature']}"
                         + (f" (조건: {notes})" if notes else ""))
    if focus and total > len(found):
        lines.append(f"· 조건을 모두 갖춘 고객 {total}명 중 우선순위 앞쪽 {len(found)}명만 실었습니다"
                     f"(하루 상한 {len(found)}명).")
    if none_fit:
        lines.append(f"· 오늘의 관리 방향의 우선관리 고객 조건을 모두 갖춘 고객은 없습니다(중점관리 고객 0명). "
                     f"아래는 중점관리 고객이 아니라, 관리 대상 {total}명을 우선순위 순으로 본 앞의 {len(found)}명입니다.")
    elif not replay and scope and not focus:
        lines.append("· 오늘은 전월말 대비 변화로 정해진 관리 방향이 없어, 중점관리 고객을 따로 고르지 않고 "
                     "관리 대상 전체를 우선순위 순으로 보여드립니다."
                     + (f" 앞의 {len(found)}명만 실었습니다." if total > len(found) else ""))
    if codes and not focus:
        # 순서의 이유 — 오늘의 부점 관리 방향(CASE)이 가리키는 요건(중점관리면 전원 해당이라 싣지 않는다 —
        # 실으면 답변이 그 줄을 «선정 이유»로 읽는다)
        lines.append("· 순서: 오늘의 부점 관리 방향과 연결된 요건(" +
                     " · ".join(cases.get(c, c) for c in codes) + ")을 가진 고객을 앞에 두었습니다.")
    if not found:
        # 0건도 명시 — «질의 빗나감»과 구별
        lines.append("· 오늘 사후관리 타겟으로 선정된 고객이 없습니다.")
    intro = [ln.removeprefix("· ") for ln in lines[1:]]
    for i, t in enumerate(found, 1):
        p = t.profile
        attrs = " · ".join([f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else []))
        focus = " · 오늘의 관리 방향 대상" if codes and set(t.conds) & set(codes) else ""
        lines.append(f"· {i}. {p.nm} — {attrs} · 평가금액 {note.won(p.bal)}{focus}")
        # 요건별 원장 값 병기
        lines += [f"    - {note.cond_text(t, c)}" for c in t.conds]
    text = "\n".join(lines)
    listed = customer_selection.from_targets(state, found, query)
    # 표는 코드 값 그대로 응답 — LLM 재작성에 의한 순서·수치·행 누락 방지.
    # PIN 이 든 표시 문서는 meta 에만 두고 모델·관측으로 전달되는 text 에는 싣지 않는다.
    document = "\n\n".join(x for x in [
        f"목록 {listed['number']} · " + head.removeprefix("■ "),
        *intro,
        (f"모든 고객에게 같은 사유({', '.join(target_table.common(found))})는 표에서 뺐습니다."
         if target_table.common(found) else ""),
        target_table.render(found) if found else "",
        *(["공제한도 소진율은 연금저축·IRP 합산 연 900만원 기본한도 기준입니다. "
           "고객 PIN은 원장에 없으면 미확인으로 표시합니다."] if found else []),
        *(["번호나 이름으로 이어서 질문해 주세요. 예: ‘2번 고객 자세히’, ‘이 고객 만기 상품은?’"] if found else []),
    ] if x)
    names = [t.profile.nm for t in found]
    return _ev("branch_targets", query, text,
               [{"id": "branch_targets.today",
                 "title": f"관리 대상 추천 {len(found)}명 ({where}{day})",
                 "doc": "고객 정보 — 계좌 원장 조회값 · 선정 기준은 사후관리 타겟 룰베이스 · "
                        "순서는 부점 CASE(타겟 룰베이스 부점 시트)",
                 "score": None, "page": None}],
               allow=[text],
               meta={"document": document, "customer_list": listed,
                     "count": len(found), "total": total, "names": names,
                     "refs": [t.profile.customer_ref for t in found], "codes": codes, "replayed": replay,
                     # 이 대화의 첫 추천 — graph.ask 가 Turn 에 남긴다(이미 있으면 그것을 잇는다)
                     "first_targets": stored if replay else {"date": day, "names": names,
                                                             "refs": [t.profile.customer_ref for t in found],
                                                             "codes": codes}})
