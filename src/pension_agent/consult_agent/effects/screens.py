"""화면 연계 — 어느 단말 화면을, 어떤 파라미터로 열 것인가 (CLAUDE.md §10).

에이전트는 **화면을 열어줄 뿐 작업을 대신 수행하지 않는다.** 예전에는 LMS 를 발송까지
수행하는 스텁이었고(`pension_agent/tools.py::send_lms`), 화면 URL·파라미터라는 개념이
아예 없었다(§12 gap 15). 발송은 직원이 그 화면에서 한다.

━━ 화면번호는 지식베이스가 갖는다 ━━
"없는 화면번호로 링크를 만들지 않는다 — 직원이 엉뚱한 화면에서 작업하게 된다"(§10).
그래서 이 파일에는 화면번호가 **하나도 없다.** 절차 안내의 화면번호는 그 답변의 근거
카드에서 오고, LMS 발송 화면은 지식베이스를 뒤져서 찾는다(`lms_screen`). 코드가 갖는
것은 "어디를 뒤질까"뿐이다 — guard.py 의 TRIGGERS 와 같은 형태다.

찾지 못하면 링크를 만들지 않는다. 채울 값이 모자라면 연계 대신 화면번호만 안내한다(§10).

━━ URL 형식은 단말 연동 규격이다 ━━
단말은 커스텀 스킴 딥링크로 화면을 연다 — `mystar-link://scnNo=0612604&mode=D`.
`?` 없이 스킴 뒤에 바로 `키=값` 이 오고 구분자는 `&` 다(일반적인 URL 쿼리가 아니므로
`urlencode` 로 만들지 않는다).

- `scnNo` — 화면호출번호 7자리 또는 단말화면번호 11자리. 지식베이스의 화면번호 표기
  (`[06-12-604]`)에서 구분자를 뺀 것이 7자리 화면호출번호다. 길이가 둘 중 하나가
  아니면 링크를 만들지 않는다 — 없는 화면을 여는 링크보다 안 만드는 편이 낫다(§10).
- `mode` — 운영 `O` · 스테이징 `S` · 개발 `D`. **지금은 개발(`D`)이다.** 전환은
  `TERMINAL_SCREEN_MODE` 환경변수로 하고, 지금 무엇으로 도는지는
  `docs/DEMO_STATUS.md` 가 전담한다(루트 CLAUDE.md).

**규격에 없는 파라미터는 싣지 않는다.** 링크에 들어가는 것은 `scnNo` 와 `mode` 뿐이다 —
고객 식별자·발송 문구를 단말이 어떤 이름으로 받는지 정해지지 않았고, 받지도 않는 키를
붙이면 단말이 링크를 통째로 못 읽을 수 있다. 그 값들은 직원이 열린 화면에서 입력한다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote

#: 단말 화면 딥링크의 커스텀 스킴. 뒤에 `키=값` 이 `&` 로 이어진다.
SCHEME = os.getenv("TERMINAL_SCREEN_SCHEME", "mystar-link://")

#: `mode` 파라미터 — 운영 O · 스테이징 S · 개발 D. 모르는 값이면 개발로 떨어뜨린다.
MODES = {"O": "운영", "S": "스테이징", "D": "개발"}
MODE = (os.getenv("TERMINAL_SCREEN_MODE") or "D").strip().upper()
if MODE not in MODES:
    MODE = "D"

#: `scnNo` 로 받는 자릿수 — 화면호출번호 7자리 · 단말화면번호 11자리.
SCN_NO_LENGTHS = (7, 11)

#: LMS 발송 화면을 지식베이스에서 찾을 때 볼 말. **문장이 아니라 검색어다** — 화면번호는
#: 절차 카드가 갖고 있다. 제목에서만 찾는 이유는, 본문에 화면 이야기가 스치듯 나오는
#: 카드(예: "모바일브랜치 링크로 안내한다")까지 걸리면 엉뚱한 화면을 열게 되기 때문이다.
LMS_TITLE_WORDS = ("발송 화면",)

#: 근거 카드가 화면번호를 **선언하는** 꼴(`[75-08-110]`). 도구가 `atomic` 스팬에 이 꼴
#: 그대로 싣는다 — 원장이 아는 화면을 걷을 때 다른 값 스팬과 가르는 기준이 이것이다.
SPAN = re.compile(r"\[\s*[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}\s*\]")

#: 답변 본문에서 화면번호를 **찾는** 꼴. **대괄호를 요구하지 않는다** — 직원이 읽는
#: 문장에서는 「04-12-646 지급/해지조회」처럼 괄호 없이 쓰는 것이 정상이고, 표기 차이로
#: 링크를 빠뜨리거나 옳은 답변을 버리지 않는다(기준서 §6 「이름 표기도 같다」와 같은 자리).
IN_TEXT = re.compile(
    r"(?<![0-9A-Za-z-])[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}(?![0-9A-Za-z-])")


def normalize(screen: str) -> str:
    """근거 카드의 화면번호 표기(`[75-08-110]`)에서 번호만 꺼낸다."""
    return (screen or "").strip().strip("[]").strip()


def scn_no(screen: str) -> str:
    """딥링크의 `scnNo` — 화면번호에서 구분자를 뺀 7자리·11자리. 아니면 빈 문자열.

    자릿수가 맞지 않는 것은 화면번호가 아니라고 본다. 그대로 실어 보내면 단말이 엉뚱한
    화면을 열거나 아무것도 열지 못한다(§10).
    """
    number = re.sub(r"[^0-9A-Za-z]", "", normalize(screen))
    return number if len(number) in SCN_NO_LENGTHS else ""


def link(screen: str) -> str | None:
    """화면 딥링크. 화면번호가 없거나 자릿수가 맞지 않으면 만들지 않는다(§10).

    화면번호 말고는 받지 않는다 — 링크에 실리는 값은 근거 카드의 화면번호와 코드가 정한
    `mode` 둘뿐이고, 이 함수가 만들어내는 값은 없다.
    """
    number = scn_no(screen)
    if not number:
        return None
    pairs = [("scnNo", number), ("mode", MODE)]
    return SCHEME + "&".join(f"{k}={quote(v, safe='')}" for k, v in pairs)


def declared(evidence: Iterable[Mapping]) -> set[str]:
    """이번 턴 원장이 아는 화면번호(정규형) — 도구가 `atomic` 으로 선언한 것.

    근거 한 건이 아니라 **원장 전체의 합집합**이다. 근거 한 건씩 재면 다른 근거가 아는
    화면이 «없는 화면»이 된다 — 절차 카드의 번호를 인용한 답변이 화면 카드 차례에서
    통째로 폐기된 실측이 있다(`nodes/plan.py::_ledger_screens` 머리말).
    """
    return {normalize(s) for e in evidence for s in (e.get("atomic") or [])
            if SPAN.fullmatch(str(s).strip())}


def cited(answer: str, known: Iterable[str]) -> list[str]:
    """답변이 가리킨 화면번호 — **원장이 아는 것만**, 답변에 나온 순서로.

    답변에서만 찾으면 LLM 이 지어낸 번호로 링크를 만들게 되고, 원장에서만 찾으면 답변이
    언급하지도 않은 화면의 링크가 붙는다(§10 「없는 화면번호로 링크를 만들지 않는다」).
    답변이 화면에 나갈 때 이 대조는 이미 한 번 끝나 있다 — `plan._span_verdict` 가 원장
    밖 화면을 가리킨 생성문을 폐기하므로, 여기 걸리는 번호는 전부 근거 카드의 것이다.
    """
    allowed = {normalize(k) for k in known}
    seen: list[str] = []
    for m in IN_TEXT.finditer(answer or ""):
        number = normalize(m.group())
        if number in allowed and number not in seen:
            seen.append(number)
    return seen


def names(kb) -> dict[str, str]:
    """화면번호 → 화면명. **이 파일은 번호도 이름도 갖지 않는다** — 지식베이스가 준다.

    같은 번호를 여러 카드가 말하면 먼저 걸린 것을 쓴다(카드 id 순). 이름을 못 찾은
    번호는 비워 두고, 링크의 표시 이름은 호출부가 번호로 떨어뜨린다.
    """
    out: dict[str, str] = {}
    for card in sorted((c for c in kb.cards if c.get("_kind") == "screen"),
                       key=lambda c: c["id"]):
        number = normalize(str(card.get("screen") or ""))
        title = str(card.get("title") or "").strip()
        if number and title:
            out.setdefault(number, title)
    return out


def links_in(answer: str, known: Iterable[str],
             names_by_screen: Mapping[str, str] | None = None) -> list[dict]:
    """답변이 가리킨 화면들의 딥링크 — 프론트가 본문의 번호를 링크로 감싸는 재료.

    **URL 을 백엔드가 완성해 준다.** 프론트가 조립하면 `mode`(운영·개발 구분)와 `scnNo`
    자릿수 판정이 두 곳에 생기고, 운영 전환 때 한쪽만 고쳐지면 운영 단말에서 개발 화면을
    여는 링크가 나간다(루트 CLAUDE.md 규칙 4 가 기록한 사고와 같은 종류다).

    항목은 `{"screen", "url", "label"}` 이고 `screen` 은 **답변 본문에 그대로 있는 문자열**
    이다 — 프론트는 그것을 찾아 감싸기만 한다. 자릿수가 규격에 맞지 않는 번호는 링크를
    만들지 않으므로 목록에서 빠진다(§10).
    """
    out: list[dict] = []
    for number in cited(answer, known):
        url = link(number)
        if not url:
            continue
        out.append({"screen": number, "url": url,
                    "label": (names_by_screen or {}).get(number) or number})
    return out


def lms_screen(kb) -> tuple[str, str] | None:
    """LMS·안내 발송 화면 — (화면번호, 근거 카드 id). 지식베이스에 없으면 None.

    None 이면 연계를 제안하지 않는다. 발송 화면이 어디인지 모르는 채로 링크를 만드는
    것보다, 제안하지 않는 편이 낫다.
    """
    for card in sorted((c for c in kb.cards if c.get("_kind") == "procedure"),
                       key=lambda c: c["id"]):
        title = str(card.get("title") or "")
        if any(w in title for w in LMS_TITLE_WORDS) and card.get("screens"):
            return normalize(card["screens"][0]), card["id"]
    return None
