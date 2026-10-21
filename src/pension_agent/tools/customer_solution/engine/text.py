"""표기 유틸 — 금액·조사(받침 판정)·출처 문자열. 판단 없음."""

from __future__ import annotations

import re

from pension_agent.tools.customer_solution.engine.catalog import CARD_INDEX, DOC_TITLES


def dday(dd: int | None) -> str:
    """잔여일수 표기. 음수는 «만기 경과 N일»(f"D-{dd}" 직접 사용 금지 — "D--87")."""
    if dd is None:
        return ""
    return f"D-{dd}" if dd >= 0 else f"만기 경과 {-dd}일"


def won(v: float) -> str:
    v = int(round(v))
    eok, rest = divmod(v, 100_000_000)
    man = rest // 10_000
    if eok and man:
        return f"{eok}억 {man:,}만원"
    if eok:
        return f"{eok}억원"
    if man:
        return f"{man:,}만원"
    return f"{v:,}원"


def _page_num(raw: str) -> int | None:
    """pNN → 정수. 숫자 페이지가 아니면 None."""
    m = re.match(r"p0*(\d+)$", raw)
    return int(m.group(1)) if m else None


def format_sources(sources: list[str]) -> list[str]:
    """근거 id 목록 → 원본 문서명 + 페이지. 색인 밖 id 는 'doc_id.pNN' 으로 해석."""
    pages: dict[str, set[int]] = {}
    order: list[str] = []
    for sid in sources:
        if sid in CARD_INDEX:
            doc, page = CARD_INDEX[sid]
        else:
            doc, _, raw = sid.partition(".")
            page = _page_num(raw) if raw else None
        if doc not in pages:
            pages[doc] = set()
            order.append(doc)
        if isinstance(page, int):
            pages[doc].add(page)
    out = []
    for doc in order:
        title = DOC_TITLES.get(doc, doc)
        pg = "/".join(f"p{n}" for n in sorted(pages[doc]))
        out.append(f"{title} ({pg})" if pg else title)
    return out


def _has_final(ch: str) -> bool | None:
    """한글 음절 받침 유무. 한글이 아니면 None."""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return None


def _ro(s: str) -> str:
    """조사 '로/으로' 부착(받침 없음·ㄹ → '로')."""
    ch = s.strip()[-1]
    if "가" <= ch <= "힣":
        return s + ("로" if (ord(ch) - 0xAC00) % 28 in (0, 8) else "으로")
    return s + "로"


def _eul(s: str) -> str:
    """조사 '을/를' 부착."""
    fin = _has_final(s.strip()[-1])
    return s + ("을" if fin else "를")


def _pname(r: dict) -> str:
    return f"{r['name']}(연 {r['rate']:.2f}%)" if r.get("rate") is not None else r["name"]


def _ret_of(r: dict) -> float | None:
    """상품 수익률 — 실적배당은 기대수익률, 원리금보장은 확정금리."""
    v = r.get("exp_return")
    return v if v is not None else r.get("rate")


class _Ctx(dict):
    """미정의 슬롯은 원문 유지(정의 누락 식별용)."""

    def __missing__(self, k: str) -> str:
        return "{" + k + "}"

