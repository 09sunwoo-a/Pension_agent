"""관계 기반 답변 점검 — 값과 조건을 잘못 짝지었는가, 알려진 오답을 말했는가 (§6).

`verify_texts()` 는 수치의 **집합 포함** 검사다. 원장에 있는 숫자를 답변이 썼는지만 보므로
**잘못 짝지은 것을 못 잡는다** — "총급여 5,500만원 이하 16.5%, 초과 13.2%" 가 원장에 있으면
"초과면 16.5%" 도 통과한다(두 숫자가 다 원장에 있으므로). 그 구멍을 막으려고 지금까지는
원문 문장을 통째로 답변에 싣게 강제했고(`atomic`), 그래서 답변이 인용문 나열이 됐다.

여기서는 **데이터가 선언한 관계**로 본다(`knowledge/CLAUDE.md` §1·§2).

  tiers      조건–값 쌍. 답변이 어떤 조건을 말하면서 **다른 조건의 값**을 붙였는지 본다.
  pitfalls   행원들이 적어둔 "자주 틀리는 지점" 중 틀린 표현. 답변이 그것을 **주장하면**
             알려진 오답을 말한 것이다. 주장과 정정을 갈라 보는 이유는 아래에 적는다.
  tables     원문 표를 행 단위로 편 것(05 시황·상품). 답변이 어느 행을 말하면서 **다른 행의
             값**을 붙였는지 본다 — 표는 그 자체가 조건→값 구조라 tiers 와 같은 자리다.

━━ 커버리지는 저작된 범위와 같다 ━━
선언이 없는 카드의 오짝은 못 잡는다. 그래서 이 검사는 원문 강제를 **선언이 있는 카드에
한해** 대신한다 — 선언이 없으면 `atomic` 이 그대로 남는다(tools.py). 저작이 넓어지는 만큼
원문 강제가 물러난다.

━━ 인용은 주장이 아니다 ━━
같은 문구가 정반대 뜻으로 쓰인다. "5,500만원 이상 13.2% 로 안내하세요"는 오답을 **주장**한
것이고, «"5,500만원 이상 13.2%"는 오기예요»는 그것을 **정정**한 것이다. 문자열 포함만 보던
동안 뒤엣것도 위반으로 잡혔고, 답변은 통째로 버려져 근거 원문이 대신 나갔다.

그냥 오탐이 아니라 **데이터가 시킨 일을 했다고 벌하는 것**이었다. 세액공제 카드의
`verify_points` 첫 줄이 «"5,500만원 이상 13.2%" = 오기» 이고, 그 줄은 직원에게 그렇게
짚어주라고 적혀 있으며 작성 프롬프트에도 그대로 실린다. 게다가 폐기 뒤 폴백으로 나가는
카드 원문에는 같은 문구가 그 줄에 들어 있다 — 위험한 문구를 막은 것이 아니라 **그 문구를
위험하다고 설명해주는 코칭만 잃은** 것이다.

그래서 정정으로 보는 조건을 **둘 다** 요구한다(하나만으로는 헐겁다):
  · 그 문구가 인용부호 안에 있을 것 — 틀렸다고 짚을 때는 문구를 따온다. 따옴표 없이
    문장에 녹여 쓴 것은 주장이다("고객님께 5,500만원 이상 13.2% 라고 안내드릴게요").
  · 곁에 정정 표지가 있을 것 — 오기·오타·틀린·잘못·아닙니다… 따옴표만으로는 부족하다.
    고객에게 읽어줄 대사도 따옴표에 담기기 때문이다(작성 지시 §6).

둘 다 요구하므로 "오기 주의하시고, 5,500만원 이상 13.2% 로 안내하세요"는 여전히 잡힌다.

━━ 못 잡는 것을 잡은 척하지 않는다 ━━
판정은 문자열 대조다. 답변이 조건을 다른 말로 풀어 쓰면(“5,500만원을 넘으면”) 이 검사는
아무 말도 하지 않는다. 그건 통과가 아니라 **판정 불가**이고, 판정 불가를 위반으로 바꾸면
맞는 답이 막힌다 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는 것보다
나쁘다(직원은 왜 막혔는지 알 수 없다).
"""

from __future__ import annotations

import re

from pension_agent.verify import first_measure, numbers

#: 조건과 값이 "같은 자리에서" 만났다고 볼 거리(글자 수). 한 문장 안에서 값과 조건이
#: 붙어 나오는 것을 보려는 것이라, 문단 전체로 넓히면 서로 다른 문장의 조건과 값이
#: 우연히 짝지어져 오탐이 난다.
NEAR = 60


def _tokens(when: str) -> list[str]:
    """조건 문장에서 대조에 쓸 조각. 괄호 안 보충 설명은 떼고 앞부분만 본다.

    조건 전체를 통째로 찾으면 답변이 한 글자만 달리 써도 못 찾는다. 그렇다고 잘게 쪼개면
    다른 조건과 겹친다("총급여 5,500만원"은 이하·초과 양쪽에 다 있다). 그래서 **경계를
    가르는 말**(이하·초과·미만·이상)을 포함한 마지막 조각을 쓴다.
    """
    head = when.split("(")[0].strip()
    return [head] if head else []


def _edge(when: str) -> str | None:
    """이 조건을 다른 조건과 가르는 말. 없으면 조건끼리 문자열로 구분되지 않는다는 뜻이다."""
    for word in ("이하", "미만", "초과", "이상"):
        if word in when:
            return word
    return None


def mispaired(answer: str, tiers: list[dict]) -> list[str]:
    """답변이 조건과 값을 잘못 짝지은 자리. 판정할 수 없으면 빈 목록.

    경계어(이하·초과…)로 갈리는 조건만 본다 — 그것이 값을 뒤집는 자리이고, 경계어가 없는
    조건은 답변에서 어느 쪽을 말하는지 문자열로 가릴 수 없다.
    """
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
                    continue          # 이 조건을 말한 자리가 아니다
                # 이 조건을 말하면서 **다른 조건의 값**을 붙였는가.
                for other in others:
                    other_value = (other["value"] or "").strip()
                    if other_value in window and value not in window:
                        bad.append(f"{tier['when']} → {value} (답변은 {other_value})")
                        break
    return bad


# ─────────────────────────────────────────────────────────────
# 표의 행 ↔ 값 — 「다른 행의 값을 갖다 붙였는가」
#
# 05 시황·상품 카드는 알맹이가 **표**다(디폴트옵션 9종의 편입상품·비중·금리, TDF 빈티지별
# 위험자산 비중). 표는 그 자체가 조건→값 구조인데, 텍스트로만 실으면 `verify_texts` 의 집합
# 포함 검사로는 「알파드림 금리는 3.27」 같은 답이 그대로 통과한다 — 3.27 은 표 안에 실제로
# 있는 숫자이기 때문이다(수협은행 행의 값이다). tiers 가 fact 에서 하는 일을 표에서 한다.
#
# ━━ 판정을 비대칭으로 한다 ━━
# 이 검사가 옳은 답을 막지 않도록, 양쪽에 다른 잣대를 쓴다.
#
#   답변이 **말한 행**  → `cells` 전부를 인용 허용으로 본다. 산문 칸까지 포함한다 —
#                        상품특징에 「정기예금 70, TDF 30 투자하는 포트폴리오」처럼 그 행을
#                        설명하는 숫자가 들어 있어서, 그것을 빼면 원문을 그대로 옮긴 답변이
#                        막힌다.
#   답변이 **말하지 않은 행** → `values`(짧은 값 칸)만 남의 값으로 센다. 남의 산문에 우연히
#                        든 숫자까지 세면 오탐이 난다.
#
# 그리고 **어느 행도 못 알아보면 아무 말도 하지 않는다.** 그건 통과가 아니라 판정 불가이고,
# 판정 불가를 위반으로 바꾸면 맞는 답이 막힌다(이 파일 머리말).
# ─────────────────────────────────────────────────────────────

#: 남의 값으로 셀 수치의 최소 자릿수. 한 자리 숫자는 표 곳곳에 흔해서(「정기예금(3년)」의 3,
#: 설정일의 일자) 그것으로 행을 가리면 오탐이 난다.
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
    """답변이 이 이름을 **제 이름으로** 불렀는가.

    형제 이름이 서로를 품는다 — 「모두드림」은 「모두드림 II」·「모두드림 III」의 부분문자열이다.
    부분문자열 일치만 보면 「모두드림 III 의 1년 수익률」이라는 답변에서 «모두드림» 행까지
    «답변이 말한 행»이 되고, 그러면 모두드림의 값을 III 의 값처럼 말한 답이 통과한다.
    그래서 더 긴 형제 이름에 **덮이지 않은** 자리가 한 번이라도 있어야 부른 것으로 본다.
    """
    covers = [sp for longer in siblings for sp in _spans(answer, longer)]
    return any(not any(a <= start and end <= b for a, b in covers)
               for start, end in _spans(answer, key))


def _aliases(key: str) -> list[str]:
    """이 행을 부르는 표기들 — 원문 표기 + 괄호 안팎.

    행 이름이 「사용자부담금(퇴직금)」처럼 **두 이름을 괄호로 묶은** 한 덩이일 때가 있다.
    직원도 답변도 그것을 「사용자부담금」·「퇴직금」·「퇴직금(사용자부담금)」 어느 쪽으로도
    부른다. 원문 표기 한 덩이로만 대조하면 **그 행을 말한 줄 모르고**, 그러면 그 행이
    «답변이 말하지 않은 행»으로 분류돼 그 값이 남의 값이 된다 — 표의 네 구간을 전부 정확히
    옮긴 답변이 그래서 폐기됐다(실측: fact.k04.f50, 두 부담금을 함께 말하면 거부되고 한쪽만
    말하면 통과했다. 카드의 pitfalls 는 반대로 «구간을 확인하지 않은 단일 수치 답변은 오답»
    이라고 적혀 있다).

    **이름을 못 알아본 것은 판정 불가이지 위반이 아니다**(이 파일 머리말 · 기준서 §6).
    별칭을 늘리는 것은 «답변이 말한 행»을 늘리는 쪽이라, 판정을 넓히지 않고 좁힌다 —
    잘못 늘어나도 맞는 답을 거부하는 방향으로는 가지 않는다.
    """
    out = [key]
    m = re.match(r"^(.*?)\((.*?)\)$", key.strip())
    if m:
        out += [x for x in (g.strip() for g in m.groups()) if x]
    return out


def _said_rows(answer: str, rows: list[dict]) -> list[dict]:
    """답변이 말하고 있는 행들. 어느 행도 못 알아보면 빈 목록(판정 불가)."""
    def names_of(row: dict) -> list[str]:
        return [a for k in row.get("keys") or [] if k for a in _aliases(k)]

    all_names = {a for r in rows for a in names_of(r)}
    said = []
    for row in rows:
        mine = names_of(row)
        # 형제는 **다른 행의** 더 긴 이름만이다. 제 행의 원문 표기(「사용자부담금(퇴직금)」)가
        # 제 별칭(「퇴직금」)을 덮으면 어느 행도 못 불린 것이 된다.
        if any(_called_by_name(answer, a,
                               [x for x in all_names if x != a and a in x and x not in mine])
               for a in mine):
            said.append(row)
    return said


def table_mispaired(answer: str, tables: list[dict]) -> list[str]:
    """답변이 다른 행의 값을 갖다 붙인 자리. 판정할 수 없으면 빈 목록."""
    bad: list[str] = []
    for table in tables or []:
        rows = table.get("rows") or []
        said = _said_rows(answer, rows)
        if not said or len(said) == len(rows):
            continue                      # 어느 행인지 못 가린다 → 판정 불가
        allowed: set[str] = set()
        for row in said:
            allowed |= _nums(row.get("cells"))
        others: set[str] = set()
        for row in rows:
            if row not in said:
                others |= _nums(row.get("values"))
        foreign = {n for n in others - allowed
                   if len(n.rstrip("%").lstrip("-")) >= MIN_VALUE_CHARS}
        for n in sorted(numbers(answer) & foreign):
            label = " / ".join(said[0].get("keys") or [])
            bad.append(f"{label} 의 값이 아닌 {n} — 같은 표의 다른 행 값이다")
    return bad


# ─────────────────────────────────────────────────────────────
# 레이블 ↔ 값 — 「이 항목의 값이라며 남의 수치를 붙였는가」
#
# 고객 재료는 표가 아니라 **이름표 붙은 값의 나열**이다(· 평가금액 1억 2,500만원 · 세액공제
# 잔여한도 0만원 …). 그런데 그 재료의 허용 집합에는 화면 값 말고도 ⑥⑦⑧ 에 실린 화법·반론·
# 참고자료의 수치가 함께 들어 있다 — 직원이 그것도 묻기 때문에 뺄 수 없다. 그래서 집합 포함
# 검사로는 **"세액공제 잔여한도는 300만원이에요"(실제 0만원)가 통과한다** — 300 은 화법 문구
# 「적립금 300만원 이상…」에 실제로 있는 숫자다. tiers 가 fact 에서, tables 가 05 표에서 하는
# 일을 여기서 한다.
#
# ━━ 레이블 바로 뒤 첫 수치만 본다 ━━
# 창 안의 수치를 전부 보면 맞는 답이 막힌다 — "잔여한도는 0만원이라 900만원 한도를 다
# 채우셨어요" 의 900 은 그 항목의 값이 아니라 뒤따르는 다른 말이다. 사람이 "A 는 얼마"라고
# 말할 때 A 바로 뒤에 오는 것이 A 의 값이다.
#
# ━━ 이름이 겹치는 항목은 아예 보지 않는다 ━━
# 「수익률」은 다른 항목의 값 안에도 있다(동연령대비교 「평균 수익률 12.1%」). 그 자리를
# 「수익률」 항목이라고 읽으면 원문을 그대로 옮긴 답변이 위반으로 잡힌다.
#
# 겹치는 범위는 **재료 전체**다. 항목 목록만 보고 판정 대상을 골랐더니, 문제상황 제목
# 「세액공제 잔여한도 보유 고객 (최근 3년 납입 이력…)」을 그대로 옮긴 답변이 «잔여한도가
# 3이라고 한다»로 잡혔다 — 재료를 그대로 옮긴 문장을 위반으로 만든 것이라 가장 나쁜 실패다.
# 그래서 `context`(그 도구가 내놓은 재료 전문)에 제 이름이 두 번 이상 나오면 뺀다.
# 판정 불가를 위반으로 바꾸지 않는다는 이 파일의 원칙 그대로다.
# ─────────────────────────────────────────────────────────────

#: 레이블 뒤 «그 항목의 값»이 나올 자리(글자 수). 조사·서술어가 끼는 만큼만 본다.
LABEL_NEAR = 24


def checkable(rows: list[dict], context: str = "") -> list[dict]:
    """이름으로 가릴 수 있는 항목만. 재료 어디에든 제 이름이 다시 나오면 뺀다.

    `context` 는 그 재료의 전문이다. 넘기지 않으면 항목 목록 안에서만 겹침을 본다 —
    그건 판정을 넓히는 쪽이므로, 호출부는 전문을 넘기는 편이 안전하다.
    """
    labels = [str(r.get("label") or "") for r in rows]
    values = " ".join(str(r.get("value") or "") for r in rows)
    return [r for r in rows
            if (lab := str(r.get("label") or ""))
            and lab not in values
            and not any(lab != other and lab in other for other in labels)
            and context.count(lab) <= 1]


def labeled_mispaired(answer: str, rows: list[dict], context: str = "") -> list[str]:
    """답변이 어떤 항목을 이름으로 부르면서 **그 항목의 값이 아닌 수치**를 붙인 자리."""
    bad: list[str] = []
    for row in checkable(rows, context):
        label, value = str(row["label"]), str(row.get("value") or "")
        allowed = numbers(value)
        if not allowed:
            continue                      # 값에 수치가 없으면 짝지을 것이 없다
        at = answer.find(label)
        while at >= 0:
            end = at + len(label)
            said = first_measure(answer[end:end + LABEL_NEAR])
            at = answer.find(label, end)
            if said is None or said[1] & allowed:
                continue                  # 값을 말하지 않았거나(판정 불가) 제 값을 말했다
            bad.append(f"{label} 은(는) {value} 인데 답변은 {said[0]} 라고 한다")
            break
    return bad


#: 인용부호. 틀렸다고 짚을 때는 그 문구를 따온다.
_OPEN, _CLOSE = "\"'「『“‘", "\"'」』”’"

#: 정정 표지 — 곁에 있으면 그 인용은 주장이 아니라 정정이다.
CORRECTIONS = ("오기", "오타", "표기 오류", "틀린", "틀립", "틀려", "잘못",
               "아닙니다", "아니에요", "아니라", "아니고", "정정", "주의")

#: 정정 표지를 찾는 거리. 한 문장 안에서 인용과 표지가 만나는 것을 보려는 것이라,
#: 넓히면 다른 문장의 "주의"가 엉뚱한 인용을 정정으로 만든다.
CORRECTION_NEAR = 40


def _corrects(answer: str, at: int, wrong: str) -> bool:
    """이 자리의 오답 문구가 **정정으로** 쓰였는가 (모듈 주석 「인용은 주장이 아니다」)."""
    end = at + len(wrong)
    quoted = (at > 0 and answer[at - 1] in _OPEN
              and end < len(answer) and answer[end] in _CLOSE)
    if not quoted:
        return False
    window = answer[max(0, at - CORRECTION_NEAR):end + CORRECTION_NEAR]
    return any(mark in window for mark in CORRECTIONS)


def known_wrong(answer: str, pitfalls: list[dict]) -> list[str]:
    """답변이 **주장한** '알려진 오답'. 행원들이 틀렸다고 적어둔 표현만 본다.

    그 문구를 정정하려고 따온 자리는 세지 않는다. 단, 한 번이라도 주장한 자리가 있으면
    잡는다 — 한쪽에서 정정하고 다른 쪽에서 그대로 말한 답변은 오답을 말한 것이다.
    """
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
    """이 카드가 관계를 선언했는가 — 선언이 있어야 관계 검사가 원문 강제를 대신한다."""
    return bool(card.get("tiers")) or bool(card.get("tables")) or bool(
        card.get("labeled")) or bool(
        any(p.get("wrong") for p in card.get("pitfalls") or []))


def check(answer: str, cards: list[dict]) -> list[str]:
    """관계 위반 전부. 비어 있으면 위반이 없거나 판정할 수 없다는 뜻이다."""
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
