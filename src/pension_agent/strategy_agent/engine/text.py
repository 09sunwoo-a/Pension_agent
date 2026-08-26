"""표기 유틸 — 금액·조사·출처 문자열. 판단은 하지 않고 모양만 만든다.

한국어 조사(을/를·으로/로)는 앞 글자의 받침으로 갈리므로 문자열을 이어 붙일 때마다
판정이 필요하다. 그 판정을 여기 한 곳에 두어, 문장을 만드는 쪽(compose·render)이
받침 규칙을 알지 않아도 되게 한다.
"""

from __future__ import annotations

import re

from pension_agent.strategy_agent.engine.catalog import CARD_INDEX, DOC_TITLES


# 표기 유틸
# ─────────────────────────────────────────────────────────────

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
    """페이지 표기(pNN)를 정수로 정규화한다. 숫자 페이지가 아니면 None."""
    m = re.match(r"p0*(\d+)$", raw)
    return int(m.group(1)) if m else None


def format_sources(sources: list[str]) -> list[str]:
    """근거 id 목록을 원본 문서명 + 페이지 표기로 변환한다.

    출처는 코드용 파일명(doc_id)이 아니라 원본 문서 그대로의 제목으로 노출한다. 카드 id 는
    종류를 가리지 않고 CARD_INDEX 로 그 카드가 실린 원본 문서에 묶는다. 색인에 없는 id 는
    'doc_id.pNN' 형태로 보고 앞토막을 문서 키로 쓴다. 예: 'proc.001'
    → '개인형IRP 고객관리 가이드 「IRP야, KB를 떠나지 마오!」 [Series 1] IRP 수익률 관리'.
    """
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
    """한글 음절의 받침 유무. 한글이 아니면 None 을 반환한다."""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return None


def _ro(s: str) -> str:
    """조사 '로/으로' 를 부착한다. 받침이 없거나 'ㄹ' 이면 '로' 이다."""
    ch = s.strip()[-1]
    if "가" <= ch <= "힣":
        return s + ("로" if (ord(ch) - 0xAC00) % 28 in (0, 8) else "으로")
    return s + "로"


def _eul(s: str) -> str:
    """조사 '을/를' 을 부착한다."""
    fin = _has_final(s.strip()[-1])
    return s + ("을" if fin else "를")


def _pname(r: dict) -> str:
    return f"{r['name']}(연 {r['rate']:.2f}%)" if r.get("rate") is not None else r["name"]


def _ret_of(r: dict) -> float | None:
    """상품 수익률. 실적배당은 기대수익률, 원리금보장은 확정금리를 사용한다."""
    v = r.get("exp_return")
    return v if v is not None else r.get("rate")


class _Ctx(dict):
    """미정의 슬롯을 예외 대신 원문으로 남긴다. 정의 누락을 출력물에서 식별하기 위함이다."""

    def __missing__(self, k: str) -> str:
        return "{" + k + "}"


# ─────────────────────────────────────────────────────────────
# 상품 질의
#
# 표 형태 자료는 검색 단위(카드)가 아니라 질의 단위(행)로 적재한다. 조건에 해당하는
# 행 전체를 대상으로 필터·정렬을 수행하며 상한(top-k)을 두지 않는다. 카드 단위 의미
