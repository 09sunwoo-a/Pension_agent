"""관계 기반 답변 점검 — 값–조건 오짝(tiers·tables·labeled), 알려진 오답 주장(pitfalls).

수치 집합 검사(verify_texts)가 못 잡는 오짝용. 선언 없는 카드는 미검사. 판정 불가는 위반으로 취급 금지.
정정 인정 조건: 인용부호 + 곁의 정정 표지, 또는 문구 바로 뒤 부정.
"""

from __future__ import annotations

import re

from pension_agent.evidence.verify import first_measure, numbers

#: 조건과 값이 같은 자리로 볼 거리(글자 수). 넓히면 다른 문장끼리 짝지어져 오탐.
NEAR = 60


def _tokens(when: str) -> list[str]:
    """조건 문장의 대조 조각 — 괄호 앞부분."""
    head = when.split("(")[0].strip()
    return [head] if head else []


def _edge(when: str) -> str | None:
    """조건을 가르는 경계어. 없으면 None."""
    for word in ("이하", "미만", "초과", "이상"):
        if word in when:
            return word
    return None


def mispaired(answer: str, tiers: list[dict]) -> list[str]:
    """조건과 값을 잘못 짝지은 자리. 경계어(이하·초과…)로 갈리는 조건만 판정."""
    edged = [t for t in tiers if _edge(t.get("when") or "")]
    if len(edged) < 2:
        return []

    bad: list[str] = []
    for tier in edged:
        edge = _edge(tier["when"])
        value = (tier.get("value") or "").strip()
        others = [t for t in edged if t is not tier and (t.get("value") or "").strip() != value]
        if not value or not others:
            continue
        for token in _tokens(tier["when"]):
            start = 0
            while True:
                at = answer.find(token, start)
                if at < 0:
                    break
                start = at + len(token)
                window = answer[at:at + NEAR]
                if edge not in window:
                    continue
                # 이 조건을 말하면서 다른 조건의 값을 붙였는가
                for other in others:
                    other_value = (other["value"] or "").strip()
                    if other_value in window and value not in window:
                        bad.append(f"{tier['when']} → {value} (답변은 {other_value})")
                        break
    return bad


# 표의 행 ↔ 값. 비대칭 판정 — 말한 행은 cells 전부 허용, 말하지 않은 행은 values 만 남의 값으로 계수
# 어느 행도 못 알아보면 판정 불가

#: 남의 값으로 셀 수치의 최소 자릿수 — 한 자리 숫자는 표 곳곳에 흔해 오탐.
MIN_VALUE_CHARS = 2


def _nums(cells: list) -> set[str]:
    return numbers(" ".join(str(c) for c in cells or []))


def _spans(answer: str, needle: str) -> list[tuple[int, int]]:
    out, at = [], answer.find(needle)
    while at >= 0:
        out.append((at, at + len(needle)))
        at = answer.find(needle, at + 1)
    return out


def _called_by_name(answer: str, key: str, siblings: list[str]) -> bool:
    """이 이름을 제 이름으로 불렀는가 — 더 긴 형제 이름(「모두드림 III」)에 덮이지 않은 자리 필요."""
    covers = [sp for longer in siblings for sp in _spans(answer, longer)]
    return any(not any(a <= start and end <= b for a, b in covers)
               for start, end in _spans(answer, key))


def _aliases(key: str) -> list[str]:
    """행을 부르는 표기 — 원문 + 괄호 안팎(「사용자부담금(퇴직금)」 → 「사용자부담금」·「퇴직금」)."""
    out = [key]
    m = re.match(r"^(.*?)\((.*?)\)$", key.strip())
    if m:
        out += [x for x in (g.strip() for g in m.groups()) if x]
    return out


def _said_rows(answer: str, rows: list[dict]) -> list[dict]:
    """답변이 말하는 행들. 못 알아보면 빈 목록(판정 불가)."""
    def names_of(row: dict) -> list[str]:
        return [a for k in row.get("keys") or [] if k for a in _aliases(k)]

    all_names = {a for r in rows for a in names_of(r)}
    said = []
    for row in rows:
        mine = names_of(row)
        # 형제는 다른 행의 더 긴 이름만 — 제 원문 표기가 제 별칭을 덮으면 안 됨
        if any(_called_by_name(answer, a,
                               [x for x in all_names if x != a and a in x and x not in mine])
               for a in mine):
            said.append(row)
    return said


def _bare(nums: set[str]) -> set[str]:
    """`%` 제거형 — 이 검사 한정. 같은 표 셀끼리라 단위 동일, 안 지우면 「3.27%」 오짝이 통과."""
    return {n.rstrip("%") for n in nums}


def _row_names(table: dict) -> set[str]:
    """표의 행을 지목하는 이름 전부(별칭 포함)."""
    return {a for row in (table.get("rows") or [])
            for k in (row.get("keys") or []) if k for a in _aliases(k)}


def _ambiguous_names(tables: list[dict]) -> frozenset[str]:
    """표 둘 이상에 나오는 행 이름. 어느 표인지 가릴 수 없어 그 표는 통째로 판정 제외."""
    seen: dict[str, int] = {}
    for table in tables or []:
        for name in _row_names(table):
            seen[name] = seen.get(name, 0) + 1
    return frozenset(n for n, c in seen.items() if c > 1)


def table_mispaired(answer: str, tables: list[dict]) -> list[str]:
    """다른 행의 값을 갖다 붙인 자리. 판정 불가면 빈 목록."""
    bad: list[str] = []
    ambiguous = _ambiguous_names(tables)
    for table in tables or []:
        if _row_names(table) & ambiguous:
            continue                      # 형제 표와 이름 공유 → 판정 불가
        rows = table.get("rows") or []
        said = _said_rows(answer, rows)
        if not said or len(said) == len(rows):
            continue                      # 행 특정 불가 → 판정 불가
        allowed: set[str] = set()
        for row in said:
            allowed |= _bare(_nums(row.get("cells")))
        others: set[str] = set()
        for row in rows:
            if row not in said:
                others |= _bare(_nums(row.get("values")))
        foreign = {n for n in others - allowed
                   if len(n.lstrip("-")) >= MIN_VALUE_CHARS}
        for n in sorted(_bare(numbers(answer)) & foreign):
            label = " / ".join(said[0].get("keys") or [])
            bad.append(f"{label} 의 값이 아닌 {n} — 같은 표의 다른 행 값이다")
    return bad


# 레이블 ↔ 값 (고객 재료). 허용 집합에 ⑥⑦⑧ 수치가 섞여 집합 검사로는 오짝이 통과
# 레이블 바로 뒤 첫 수치만 비교. 재료 전문(context)에 이름이 두 번 이상 나오면 판정 제외

#: 레이블 뒤 그 항목의 값이 나올 자리(글자 수).
LABEL_NEAR = 24


def checkable(rows: list[dict], context: str = "") -> list[dict]:
    """이름으로 가릴 수 있는 항목만. context(재료 전문) 미전달 시 판정이 넓어짐."""
    labels = [str(r.get("label") or "") for r in rows]
    values = " ".join(str(r.get("value") or "") for r in rows)
    return [r for r in rows
            if (lab := str(r.get("label") or ""))
            and lab not in values
            and not any(lab != other and lab in other for other in labels)
            and context.count(lab) <= 1]


def labeled_mispaired(answer: str, rows: list[dict], context: str = "") -> list[str]:
    """항목을 이름으로 부르면서 그 항목 값이 아닌 수치를 붙인 자리."""
    bad: list[str] = []
    for row in checkable(rows, context):
        label, value = str(row["label"]), str(row.get("value") or "")
        allowed = numbers(value)
        if not allowed:
            continue
        at = answer.find(label)
        while at >= 0:
            end = at + len(label)
            said = first_measure(answer[end:end + LABEL_NEAR])
            at = answer.find(label, end)
            if said is None or said[1] & allowed:
                continue                  # 값 미언급(판정 불가) 또는 제 값
            bad.append(f"{label} 은(는) {value} 인데 답변은 {said[0]} 라고 한다")
            break
    return bad


#: 인용부호.
_OPEN, _CLOSE = "\"'「『“‘", "\"'」』”’"

#: 정정 표지 — 곁에 있으면 그 인용은 정정. 카드 verify_points 가 쓰는 낱말(「오답」 등) 포함 필수.
CORRECTIONS = ("오기", "오타", "표기 오류", "틀린", "틀립", "틀려", "잘못", "오답", "오해",
               "아닙니다", "아니에요", "아니라", "아니고", "정정", "주의")

#: 정정 표지 탐색 거리. 넓히면 다른 문장의 «주의»가 엉뚱한 인용을 정정으로 만듦.
CORRECTION_NEAR = 40


#: 오답 문구 바로 뒤의 부정(「…공제 대상이 아니에요」). 문장 부호는 넘지 않음.
_NEGATED = re.compile(r"^[^.。!?,\n]{0,10}?(아니|않)")
_NEGATION_NEAR = 14


def _corrects(answer: str, at: int, wrong: str) -> bool:
    """이 자리의 오답 문구가 정정인가 — ① 인용부호 + 정정 표지 ② 문구 바로 뒤 부정."""
    end = at + len(wrong)
    if _NEGATED.search(answer[end:end + _NEGATION_NEAR]):
        return True
    quoted = (at > 0 and answer[at - 1] in _OPEN
              and end < len(answer) and answer[end] in _CLOSE)
    if not quoted:
        return False
    window = answer[max(0, at - CORRECTION_NEAR):end + CORRECTION_NEAR]
    return any(mark in window for mark in CORRECTIONS)


def known_wrong(answer: str, pitfalls: list[dict]) -> list[str]:
    """답변이 주장한 알려진 오답. 정정용 인용은 제외, 한 번이라도 주장하면 포함."""
    hit: list[str] = []
    for item in pitfalls or []:
        for wrong in item.get("wrong") or []:
            if not wrong or wrong in hit:
                continue
            spots = [m.start() for m in re.finditer(re.escape(wrong), answer)]
            if spots and not all(_corrects(answer, at, wrong) for at in spots):
                hit.append(wrong)
    return hit


def declared(card: dict) -> bool:
    """관계 선언 여부 — 있어야 관계 검사가 원문 강제를 대신함."""
    return bool(card.get("tiers")) or bool(card.get("tables")) or bool(
        card.get("labeled")) or bool(
        any(p.get("wrong") for p in card.get("pitfalls") or []))


def check(answer: str, cards: list[dict]) -> list[str]:
    """관계 위반 전부. 빈 목록 = 위반 없음 또는 판정 불가."""
    out: list[str] = []
    for card in cards:
        out += mispaired(answer, card.get("tiers") or [])
        out += table_mispaired(answer, card.get("tables") or [])
        out += labeled_mispaired(answer, card.get("labeled") or [], card.get("context") or "")
        out += known_wrong(answer, card.get("pitfalls") or [])
    return out


__all__ = ["CORRECTIONS", "CORRECTION_NEAR", "LABEL_NEAR", "MIN_VALUE_CHARS", "NEAR",
           "check", "checkable", "declared", "known_wrong", "labeled_mispaired",
           "mispaired", "numbers", "table_mispaired"]
