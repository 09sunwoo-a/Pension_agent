"""대화의 고객 선택 — 표시한 목록의 내부 행 식별자를 해석·재확인. LLM 추측 금지.

목록·선택·되묻기는 직원/세션별 Turn 에 보관하며 프롬프트에는 전달하지 않는다.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import html
import re
import time

from pension_agent.common import clock
from pension_agent.tools.branch_briefing import roster, run
from pension_agent.tools.customer_solution import customer as C

MAX_LISTS = 3
TTL_SEC = 2 * 60 * 60
_ROW = re.compile(r"(?<![\d.-])(-?\d+)\s*번(?:째)?\s*(?:고객|분)")
_PRONOUN = re.compile(r"(?:이|그|해당)\s*고객")
_UNKNOWN_NAME = re.compile(r"(?<![가-힣A-Za-z])([김이박최정강조윤장임한오서신권황안송류홍][가-힣]{1,3})\s*고객")
_GENERIC = {"어떤", "다른", "해당", "전체", "모든", "관리", "대상", "추천", "중점관리", "미접촉", "만기",
            "이탈", "이전", "신규", "신규가입", "정기예금", "이상", "이하", "이내", "이탈징후", "이탈위험", "임박", "한도초과"}
_ORDINALS = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


def _valid(saved: dict) -> bool:
    """호출자가 Turn 을 제공할 수도 있어 선택 맥락의 구조부터 확인."""
    lists = saved.get("lists")
    if not isinstance(lists, list) or len(lists) > MAX_LISTS or not isinstance(saved.get("serial"), int):
        return False
    for listed in lists:
        if (not isinstance(listed, dict) or not isinstance(listed.get("number"), int)
                or not isinstance(listed.get("created"), (int, float))
                or not isinstance(listed.get("entries"), list) or len(listed["entries"]) > roster.ROSTER_MAX):
            return False
        for i, entry in enumerate(listed["entries"], 1):
            if (not isinstance(entry, dict) or entry.get("number") != i
                    or entry.get("name") is not None and not isinstance(entry["name"], str)
                    or entry.get("ref") is not None and not isinstance(entry["ref"], str)):
                return False
    for key in ("selected", "pending"):
        value = saved.get(key)
        if value is not None and (not isinstance(value, dict) or not isinstance(value.get("list"), int)):
            return False
    if saved.get("selected") and not isinstance(saved["selected"].get("number"), int):
        return False
    if saved.get("pending") and (not isinstance(saved["pending"].get("numbers"), list)
                                 or not all(isinstance(n, int) for n in saved["pending"]["numbers"])):
        return False
    return True


def context(history: list[dict] | None, employee_id: str | None, session_id: str = "default") -> dict:
    """최근 저장 맥락. 다른 직원·세션의 선택은 재사용하지 않음."""
    for turn in reversed(history or []):
        saved = turn.get("customer_context")
        if isinstance(saved, dict):
            if saved.get("employee_id") == employee_id and saved.get("session_id") == session_id and _valid(saved):
                return deepcopy(saved)
            break
    return {"employee_id": employee_id, "session_id": session_id, "serial": 0,
            "lists": [], "active": None, "selected": None, "pending": None}


def snapshot(state: dict, entries: list[dict], branch_code: str | None, query: str) -> dict:
    """순번·식별자·구분 정보만 저장. PIN·모델 생성 고객 id 미포함."""
    saved = state.get("customer_context") or context(
        state.get("history"), state.get("employee_id"), state.get("session_id") or "default")
    serial = int(saved.get("serial") or 0) + 1
    return {"number": serial, "created": time.time(), "date": clock.today().isoformat(),
            "employee_id": state.get("employee_id"), "branch_code": branch_code,
            "query": state.get("question") or query,
            "entries": [dict(e, number=i) for i, e in enumerate(entries, 1)]}


def from_targets(state: dict, targets: list, query: str) -> dict:
    """선정 순서 그대로의 목록 스냅샷."""
    try:
        mine = run.my_branch(state.get("employee_id")) if state.get("employee_id") else None
    except run.DBUnavailable:
        mine = None
    entries = [{"ref": t.profile.customer_ref, "name": t.profile.nm,
                "age": t.profile.ag, "grade": t.profile.club_grade,
                "balance": t.profile.bal} for t in targets]
    return snapshot(state, entries, mine[0] if mine else None, query)


def remember(saved: dict, evidence: list[dict]) -> dict:
    """새로 표시한 목록만 교체. 집계·단순 질의는 기존 목록을 유지."""
    out = deepcopy(saved)
    for ev in evidence:
        meta = ev.get("meta") or {}
        found = meta.get("customer_list")
        if not found:
            continue
        listed = dict(found, document=meta.get("document") or "")
        prior = [s for s in (out.get("lists") or []) if s["number"] != listed["number"]]
        out["lists"] = [*prior, listed][-MAX_LISTS:]
        out.update(serial=listed["number"], active=listed["number"], selected=None, pending=None)
    return out


@dataclass
class Resolution:
    customer_id: str | None
    context: dict
    case: str = "none"
    answer: str | None = None
    clarify: dict | None = None
    question: str | None = None
    notice: str | None = None


def _label(entry: dict) -> str:
    attrs = [f"{entry['age']}세"] if entry.get("age") is not None else []
    if entry.get("grade"):
        attrs.append(str(entry["grade"]))
    return f"{entry['number']}번 {entry['name']}" + (f" ({' / '.join(attrs)})" if attrs else "")


def name_reference(entry: dict, entries: list[dict]) -> str | None:
    """목록에서 한 명으로 구분되는 이름 표현. 구분 정보도 겹치면 추천하지 않음."""
    name = entry.get("name")
    if not name:
        return None
    matches = [e for e in entries if e.get("name") == name]
    if len(matches) == 1:
        return f"{name} 고객"
    age = entry.get("age")
    if age is not None:
        matches = [e for e in matches if e.get("age") == age]
        if len(matches) == 1:
            return f"{age}세 {name} 고객"
    grade = entry.get("grade")
    if grade and len([e for e in matches if e.get("grade") == grade]) == 1:
        prefix = f"{age}세 " if age is not None else ""
        return f"{prefix}{grade} 등급의 {name} 고객"
    return None


def _grades(question: str) -> list[str]:
    return [g for g in ("패밀리", "베스트", "그랜드", "로얄", "MVP", "VIP", "VVIP")
            if re.search(r"(?<![가-힣A-Za-z])" + g + r"(?![가-힣A-Za-z])", question)]


def _reply(saved: dict, case: str, text: str, *, entries: list[dict] | None = None,
           listed: dict | None = None, question: str = "") -> Resolution:
    saved["selected"] = None
    options = [_label(e) for e in (entries or [])]
    prompt = text
    if options:
        # 선택 버튼을 지원하지 않는 UI 에서도 후보를 읽고 번호로 답할 수 있게 본문에 수록.
        text += "\n\n" + "\n".join(f"- {html.escape(option, quote=False)}" for option in options)
    saved["pending"] = ({"list": listed["number"], "numbers": [e["number"] for e in entries],
                         "question": question} if entries and listed else None)
    return Resolution(None, saved, case, text,
                      {"question": prompt, "options": options} if options else None)


def resolve(question: str, saved: dict, employee_id: str | None,
            customer_id: str | None = None) -> Resolution:
    """명시 순번·이름·지시어를 해석. 모호함·누락·부점 변경은 답변으로 중단."""
    saved = deepcopy(saved)
    lists = saved.get("lists") or []
    pending = saved.get("pending") or {}
    q = question.strip()
    if re.search(r"(?<!\d)\d{6}[- ]\d[\d*•xX]{6}(?!\d)", q):
        return _reply(saved, "pin_reference", "PIN 전체나 마스킹된 PIN 만으로 고객을 추정하지 않아요. 목록에 표시된 번호나 이름으로 지정해 주세요.")
    q = re.sub(r"(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*번째\s*(고객|분)",
               lambda m: f"{_ORDINALS[m[1]]}번 {m[2]}", q)
    if re.fullmatch(r"(?:고객\s*)?선택\s*(?:해제|취소)(?:해줘)?[.!?]?", q):
        saved.update(selected=None, pending=None)
        return Resolution(None, saved, "cleared", "고객 선택을 해제했어요. 목록 번호나 이름으로 다시 지정해 주세요.")
    if re.search(r"다른\s*고객(?:으로|을|\s*(?:봐|보여|선택))", q):
        return _reply(saved, "not_selected", "다른 고객으로 바꾸려면 목록의 번호나 이름으로 지정해 주세요.")

    explicit_lists = re.findall(r"목록\s*(\d+)\s*(?:번)?", q)
    if len(set(explicit_lists)) > 1:
        return _reply(saved, "multiple_lists", "서로 다른 목록이 함께 지정됐어요. 목록 번호와 고객 번호를 한 쌍으로 말씀해 주세요.")
    explicit_list = re.search(r"목록\s*(\d+)\s*(?:번)?", q)
    requested_list = int(explicit_list[1]) if explicit_list else None
    if requested_list is None and "목록" in q:
        if any(word in q for word in ("첫", "처음")):
            requested_list = 1
        elif any(word in q for word in ("이전", "직전")):
            earlier = [s["number"] for s in lists if s["number"] < (saved.get("active") or 0)]
            requested_list = max(earlier) if earlier else -1
    list_number = requested_list if requested_list is not None else (pending.get("list") or saved.get("active"))
    listed = next((s for s in lists if s["number"] == list_number), None)
    entries = (listed or {}).get("entries") or []

    numbers = [int(m[1]) for m in _ROW.finditer(q)]
    if numbers:
        row_question = re.sub(r"목록\s*\d+\s*(?:번)?", "", q)
        numbers = [int(m[1]) for m in re.finditer(r"(?<![\d.-])(-?\d+)\s*번(?:째)?", row_question)]
    bare = re.fullmatch(r"(-?\d+)\s*번?(?:째)?(?:요)?[.!?]?", q) if pending else None
    if bare:
        numbers = [int(bare[1])]
    # «2번 자세히»도 명시 선택. 고객이란 단어 없는 업무 화면 번호는 건드리지 않음.
    short = re.fullmatch(r"(-?\d+)\s*번\s*(?:자세히|봐줘|보여줘|선택|으로)(?:요)?[.!?]?", q)
    if short:
        numbers = [int(short[1])]
    pending_reply = bool(bare)
    age, grades = None, []
    if pending and not numbers:
        age = re.search(r"(\d{1,3})\s*세", q)
        grades = _grades(q)
    names = sorted({e["name"] for e in entries if isinstance(e.get("name"), str) and e["name"] and re.search(
        r"(?<![가-힣A-Za-z])" + re.escape(e["name"]) + r"(?=\s|고객|님|은|는|의|에게|한테|$)", q)})
    unknown = [m[1] for m in _UNKNOWN_NAME.finditer(q) if m[1] not in _GENERIC and m[1] not in names]
    partial = re.search(r"(?<![가-힣A-Za-z])([김이박최정강조윤장임한오서신권황안송류홍])(?:씨)?\s*고객", q)
    pronoun = bool(_PRONOUN.search(q))
    if pronoun:
        partial = None
    replay = "목록" in q and any(word in q for word in ("다시", "보여", "돌아")) and not numbers and not names
    selecting = bool(numbers or names or unknown or partial or pronoun or requested_list is not None or replay or age or grades)
    if (len(lists) > 1 and requested_list is None and (numbers or names)
            and any(word in q for word in ("아까", "처음", "먼저"))):
        return _reply(saved, "ambiguous_list", "어느 목록의 고객인지 확인이 필요해요. ‘목록 1의 2번 고객’처럼 목록 번호와 고객 번호를 함께 말씀해 주세요.")
    if not selecting and pending:
        saved["pending"] = None  # 별개 질문은 원래 고객 질문의 답으로 취급하지 않음
    if not selecting and not saved.get("selected"):
        return Resolution(customer_id, saved)
    if requested_list is not None and listed is None:
        return _reply(saved, "list_missing", "지정한 목록을 보관하고 있지 않아요. 조건을 다시 말씀해 조회해 주세요.")
    if selecting and listed is None and (numbers or names or unknown or partial or replay):
        return _reply(saved, "no_list", "현재 대화에 선택할 고객 목록이 없어요. 먼저 조건으로 고객을 조회해 주세요.")

    # 기존에 선택한 고객은 명시 선택이 없을 때만 유지.
    if (not numbers and not names and not unknown and not partial and not replay
            and requested_list is None and saved.get("selected")):
        selected = saved["selected"]
        listed = next((s for s in lists if s["number"] == selected["list"]), None)
        entries = (listed or {}).get("entries") or []
        numbers = [selected["number"]]
    if listed:
        if (listed.get("date") != clock.today().isoformat()
                or time.time() - listed.get("created", 0) > TTL_SEC):
            return _reply(saved, "expired", "이 목록은 조회 후 시간이 지나 유효하지 않아요. 같은 조건으로 다시 조회해 주세요.")
        try:
            mine = run.my_branch(employee_id)
        except run.DBUnavailable:
            return _reply(saved, "db_unavailable", "현재 부점 데이터를 확인할 수 없어 고객을 연결하지 않았어요. 잠시 후 다시 시도해 주세요.")
        if not mine or listed.get("employee_id") != employee_id or listed.get("branch_code") != mine[0]:
            return _reply(saved, "scope_changed", "현재 직원의 부점과 목록의 조회 범위를 확인할 수 없어요. 본인 부점 고객을 다시 조회해 주세요.")
    if pending and listed and (age or grades) and not names:
        candidates = [e for e in entries if e["number"] in pending.get("numbers", [])]
        matches = [e for e in candidates if (not age or e.get("age") == int(age[1]))
                   and (not grades or e.get("grade") in grades)]
        if len(matches) != 1:
            return _reply(saved, "pending_mismatch", "그 정보만으로 확인 중인 고객 한 명을 특정할 수 없어요. 목록 번호로 선택해 주세요.",
                          entries=candidates, listed=listed, question=pending.get("question") or question)
        numbers = [matches[0]["number"]]
        pending_reply = bool(re.fullmatch(
            r"(?:\d{1,3}\s*세\s*)?(?:(?:패밀리|베스트|그랜드|로얄|MVP|VIP|VVIP)\s*)?"
            r"(?:고객(?:님)?|분)?(?:이요|요)?[.!?]?", q))
    if partial and not names and not numbers and listed:
        candidates = [e for e in entries if isinstance(e.get("name"), str) and e["name"].startswith(partial[1])]
        return _reply(saved, "partial_name", "이름 일부만으로 고객을 특정하지 않았어요. 목록에 표시된 번호나 전체 이름으로 선택해 주세요.",
                      entries=candidates, listed=listed, question=question)
    if replay and listed:
        saved.update(active=listed["number"], selected=None, pending=None)
        return Resolution(None, saved, "list_replay", listed.get("document") or "보관된 목록을 다시 조회해 주세요.")
    if requested_list is not None and not numbers and not names and not partial:
        saved["active"] = listed["number"]
        return _reply(saved, "not_selected", f"목록 {listed['number']}에서 확인할 고객의 번호나 이름을 지정해 주세요.")
    if len(set(numbers)) > 1 or len(names) > 1:
        return _reply(saved, "multiple", "여러 고객이 지정됐어요. 먼저 확인할 고객 한 명의 목록 번호를 말씀해 주세요.")
    if unknown:
        return _reply(saved, "name_missing", "말씀하신 이름은 해당 목록에서 확인되지 않아요. 표에 표시된 번호나 정확한 이름으로 지정해 주세요.")
    if not numbers and not names:
        if pronoun and customer_id:
            return Resolution(customer_id, saved)
        if pronoun:
            return _reply(saved, "not_selected", "아직 고객이 선택되지 않았어요. 목록의 번호나 이름으로 고객을 지정해 주세요.")
        return Resolution(customer_id, saved)
    if numbers:
        if pending.get("numbers") and numbers[0] not in pending["numbers"]:
            candidates = [e for e in entries if e["number"] in pending["numbers"]]
            return _reply(saved, "pending_mismatch", "확인 중인 동명이인 후보의 목록 번호로 말씀해 주세요.",
                          entries=candidates, listed=listed, question=pending.get("question") or question)
        matches = [e for e in entries if e["number"] == numbers[0]]
        if not matches:
            return _reply(saved, "out_of_range", f"이 목록은 {len(entries)}명이며 {numbers[0]}번 고객은 없어요. 표에 표시된 번호로 지정해 주세요.")
        entry = matches[0]
        if names and entry["name"] != names[0]:
            return _reply(saved, "conflict", f"이 목록의 {numbers[0]}번은 {entry['name']} 고객이에요. 말씀하신 이름과 달라 연결하지 않았어요. 번호나 이름을 다시 확인해 주세요.")
    else:
        matches = [e for e in entries if e["name"] == names[0]]
        named_age = re.search(r"(\d{1,3})\s*세", q)
        if named_age:
            matches = [e for e in matches if e.get("age") == int(named_age[1])]
        named_grades = _grades(q)
        if named_grades:
            matches = [e for e in matches if e.get("grade") in named_grades]
        if not matches:
            return _reply(saved, "conflict", "이름과 구분 조건에 함께 맞는 고객이 해당 목록에 없어요. 목록 번호로 다시 확인해 주세요.")
        if len(matches) > 1:
            return _reply(saved, "duplicate_name", f"{names[0]} 고객이 {len(matches)}명 있어요. 목록에 표시된 번호로 선택해 주세요.",
                          entries=matches, listed=listed, question=question)
        entry = matches[0]
        if pending.get("numbers") and entry["number"] not in pending["numbers"]:
            candidates = [e for e in entries if e["number"] in pending["numbers"]]
            return _reply(saved, "pending_mismatch", "확인 중인 후보의 이름과 구분 정보로 말씀해 주세요.",
                          entries=candidates, listed=listed, question=pending.get("question") or question)
    if not entry.get("ref") or not entry.get("name"):
        return _reply(saved, "no_identifier", "이 조회 결과에는 고객 연결용 식별자가 없어 이름만으로 상세 고객을 연결할 수 없어요. 고객 목록을 다시 조회해 주세요.")
    try:
        profile = roster.lookup(entry["ref"], employee_id)
    except run.DBUnavailable:
        return _reply(saved, "db_unavailable", "현재 고객 원장을 확인할 수 없어 연결하지 않았어요. 잠시 후 다시 시도해 주세요.")
    if (not profile or profile.nm != entry["name"]
            or (entry.get("age") is not None and profile.ag != entry["age"])
            or (entry.get("grade") and profile.club_grade != entry["grade"])):
        return _reply(saved, "changed", "조회 당시 고객과 현재 원장 정보가 일치하지 않아요. 다른 고객으로 연결하지 않았어요. 조건을 다시 말씀해 목록을 조회해 주세요.")
    if customer_id and (numbers or names) and customer_id != profile.id:
        return _reply(saved, "screen_conflict", "화면에서 열린 고객과 말씀하신 고객이 달라요. 화면 고객 선택을 해제한 뒤 목록 번호로 다시 지정해 주세요.")
    was = saved.get("selected")
    saved.update(selected={"list": listed["number"], "number": entry["number"], "ref": entry["ref"]}, pending=None)
    if C.get_profile(profile.id) is None:
        return Resolution(None, saved, "detail_unavailable",
                          f"{_label(entry)} 고객을 확인했어요. 현재는 목록 정보만 있고 상세 원장 연결이 없어 보유상품·만기·상담이력은 확인할 수 없어요.")
    original = pending.get("question")
    only_selector = re.fullmatch(
        r"\d+\s*번(?:째)?\s*고객(?:님)?(?:으로\s*확인해줘|이요|요)?[.!?]?", q)
    reference = name_reference(entry, entries)
    named_confirmation = reference and q.rstrip(".!?") == f"{reference}으로 확인해줘"
    effective = original if (pending_reply or only_selector or named_confirmation) and original else question
    return Resolution(profile.id, saved, "selected", question=effective,
                      notice=f"{_label(entry)} 고객 기준으로 확인했어요." if was != saved["selected"] else None)
