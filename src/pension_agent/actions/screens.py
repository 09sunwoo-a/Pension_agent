"""단말 화면 딥링크(`mystar-link://scnNo=…&mode=…`) 조립 — 화면을 열 뿐 작업 대행 없음.

화면번호는 지식베이스에서만 가져옴(이 파일에 화면번호 없음). 못 찾으면 링크 미생성.
`?` 없는 커스텀 규격이라 `urlencode` 미사용. 파라미터는 `scnNo`·`mode` 만 — 규격 외 키 금지.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote

#: 딥링크 커스텀 스킴. 뒤에 `키=값` 을 `&` 로 연결.
SCHEME = os.getenv("TERMINAL_SCREEN_SCHEME", "mystar-link://")

#: `mode` — 운영 O · 스테이징 S · 개발 D. 모르는 값은 D.
MODES = {"O": "운영", "S": "스테이징", "D": "개발"}
MODE = (os.getenv("TERMINAL_SCREEN_MODE") or "D").strip().upper()
if MODE not in MODES:
    MODE = "D"

#: `scnNo` 자릿수 — 화면호출번호 7 · 단말화면번호 11.
SCN_NO_LENGTHS = (7, 11)

#: LMS 발송 화면 검색어. 제목에서만 검색 — 본문까지 보면 엉뚱한 절차 카드가 걸림
LMS_TITLE_WORDS = ("발송 화면",)

#: 근거 카드의 화면번호 선언 꼴(`[75-08-110]`). `atomic` 스팬 중 화면 판별 기준.
SPAN = re.compile(r"\[\s*[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}\s*\]")

#: 답변 본문의 화면번호 꼴. 대괄호 불요 — 본문은 괄호 없이 쓰는 것이 정상
IN_TEXT = re.compile(
    r"(?<![0-9A-Za-z-])[0-9A-Za-z]{2}-[0-9A-Za-z]{2}-[0-9A-Za-z]{3}(?![0-9A-Za-z-])")


def normalize(screen: str) -> str:
    """`[75-08-110]` → `75-08-110`."""
    return (screen or "").strip().strip("[]").strip()


def scn_no(screen: str) -> str:
    """구분자 뺀 7·11자리 `scnNo`. 자릿수 불일치 시 빈 문자열."""
    number = re.sub(r"[^0-9A-Za-z]", "", normalize(screen))
    return number if len(number) in SCN_NO_LENGTHS else ""


def link(screen: str) -> str | None:
    """화면 딥링크. 화면번호 없음·자릿수 불일치 시 None."""
    number = scn_no(screen)
    if not number:
        return None
    pairs = [("scnNo", number), ("mode", MODE)]
    return SCHEME + "&".join(f"{k}={quote(v, safe='')}" for k, v in pairs)


def declared(evidence: Iterable[Mapping]) -> set[str]:
    """원장 전체 `atomic` 의 화면번호 합집합 — 근거 한 건 단위로 재면 다른 근거의 화면이 누락."""
    return {normalize(s) for e in evidence for s in (e.get("atomic") or [])
            if SPAN.fullmatch(str(s).strip())}


def cited(answer: str, known: Iterable[str]) -> list[str]:
    """답변이 가리킨 화면번호 중 원장이 아는 것만, 등장 순서로."""
    allowed = {normalize(k) for k in known}
    seen: list[str] = []
    for m in IN_TEXT.finditer(answer or ""):
        number = normalize(m.group())
        if number in allowed and number not in seen:
            seen.append(number)
    return seen


def names(kb) -> dict[str, str]:
    """화면번호 → 화면명(지식베이스 screen 카드, 중복 시 카드 id 순 첫 번째)."""
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
    """답변이 가리킨 화면의 `{"screen", "url", "label"}` 목록. URL 은 백엔드가 완성(mode 판정 단일화)."""
    out: list[dict] = []
    for number in cited(answer, known):
        url = link(number)
        if not url:
            continue
        out.append({"screen": number, "url": url,
                    "label": (names_by_screen or {}).get(number) or number})
    return out


def lms_screen(kb) -> tuple[str, str] | None:
    """LMS 발송 화면 (화면번호, 근거 카드 id). 없으면 None — 연계 제안 안 함."""
    for card in sorted((c for c in kb.cards if c.get("_kind") == "procedure"),
                       key=lambda c: c["id"]):
        title = str(card.get("title") or "")
        if any(w in title for w in LMS_TITLE_WORDS) and card.get("screens"):
            return normalize(card["screens"][0]), card["id"]
    return None
