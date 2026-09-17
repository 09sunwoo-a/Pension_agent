"""05_시황_상품_기반지식 → doc + market + lineup, 그리고 05 의 표 → 관계 선언.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import REPO, clean, first_clause, note, record, redact, topics_of
from scripts.kb_build.docs import doc_title


# ─────────────────────────────────────────────────────────────
# 5-d) 05_시황_상품_기반지식 → doc + market
#
# 05 폴더는 06 추출지식과 달리 **문서 자체가 원문**이다(행내 배포 PDF 를 원문 보존 규칙으로
# 옮긴 변환본 — 폴더 README "원문 그대로 보존이 원칙"). 그래서 항목 인덱스가 없고, 문서의
# `##` 절이 곧 검색 단위다. front-matter(title·category·as_of·trigger_keywords·key_points)는
# 검색되도록 저작자가 붙여둔 메타라 그대로 옮긴다.
#
# 카드 구조: 문서마다 개요 카드 1장(front-matter 의 요점·검색 키워드) + 절 카드 N장(절 본문
# 원문 그대로). 절 본문(content)은 원문이므로 고치지 않는다 — 루트 절대 규칙 1 이 quotes 에
# 적용되는 것과 같은 이유다.
#
# 시효성(§9 규약): 시황·상품 수치는 주·월 단위로 낡는다. 폴더 README 가 스스로 적어둔
# ※ 경고를 `volatile` 로, front-matter 의 `as_of` 를 기준시점으로 모든 카드에 싣는다 —
# 붙일지도 문구도 데이터(원문)가 정한다(screen·channel 과 같은 규약, consult §12 gap 16·18).
#
# 건너뛰는 절 둘: 「Contents」(목차 — 본문이 아니다) · 「추출 노트」(판독·검수 기록 = 저작
# 검증 메모라 직원 답변 재료가 아니다. 역할 어휘로 치면 authoring 이다).
# ─────────────────────────────────────────────────────────────

#: 절 경계 — H1·H2 만 본다. H3(`###`)는 절 안의 소제목이라 본문에 남긴다.
_MARKET_HEAD = re.compile(r"^(#{1,2})\s+(.+?)\s*$")

#: 카드로 만들지 않는 절. Contents 는 목차, 추출 노트는 저작 검증 메모다.
_MARKET_SKIP = ("Contents", "추출 노트")

#: id 에 못 쓰는 문자(`.` 등) 정리. 파일명 "2026.08" → "2026-08".
_MARKET_SLUG = re.compile(r"[^0-9A-Za-z가-힣_-]")


def _market_front_matter(text: str) -> tuple[dict, str]:
    """front-matter 를 (dict, 본문) 으로. 인라인 목록([a, b])과 블록 목록(- 항목)을 받는다.

    기존 _front_matter 는 문자열 값만 다룬다 — 05 의 trigger_keywords·key_points 는
    목록이라 여기서만 넓혀 읽고, 다른 폴더의 파서는 건드리지 않는다.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    body = text[end + 4:].lstrip("\n")
    data: dict = {}
    list_key: str | None = None
    for line in text[3:end].splitlines():
        if list_key is not None and re.match(r"^\s+-\s+", line):
            data[list_key].append(re.sub(r"^\s+-\s+", "", line).strip())
            continue
        key, sep, val = line.partition(":")
        if not sep or not key.strip() or line.startswith((" ", "\t")):
            continue
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            data[key] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
            list_key = None
        elif not val:
            data[key] = []
            list_key = key
        else:
            data[key] = val
            list_key = None
    return data, body


def _market_emphasis(text: str) -> str:
    """마크다운 강조·코드표기만 걷어낸다. 밑줄은 남긴다.

    공용 `clean()` 은 `_` 도 지우는데, README 의 ※ 안내는 필드 이름을 코드표기로 인용한다
    («인용 전 `as_of` 기준시점을 확인하고»). clean 을 그대로 쓰면 그 이름이 `asof` 로
    깨진 채 답변에 나가고, 직원은 존재하지 않는 필드를 찾게 된다.
    """
    # 이스케이프를 먼저 푼다 — 원문이 각주 표시를 `\\*\\*\\*수협은행…` 처럼 적어서,
    # `*` 만 지우면 백슬래시가 이름 앞에 남는다(행 이름이 원문과 달라진다).
    out = re.sub(r"\\([*_`\[\]<>#|])", r"\1", text)
    out = re.sub(r"[*`]+", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _readme_note(marker: str) -> str | None:
    """폴더 README 가 스스로 적어둔 선언 한 덩이를 읽는다 — `marker` 로 시작하는 줄과,
    이어지는 들여쓴 줄들. 빈 줄에서 끊으므로 그 아래 설명 문단은 딸려오지 않는다.

    문구를 코드가 들고 있으면 README 가 바뀔 때 두 곳이 갈린다(§12 gap 16 과 같은 사고).
    없으면 None — **선언이 없으면 표시도 없다**(tools.stale_mark 규약).
    """
    readme = config.MARKET_DIR / "README.md"
    if not readme.exists():
        return None
    lines = readme.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(marker):
            buf = [ln.strip().lstrip(marker).strip()]
            for nxt in lines[i + 1:]:
                if nxt.strip() and re.match(r"^\s+\S", nxt):
                    buf.append(nxt.strip())
                else:
                    break
            return _market_emphasis(" ".join(buf)) or None
    return None


def _market_warn() -> str | None:
    """※ 시효 경고 — "시황·상품 정보는 빠르게 달라진다"."""
    return _readme_note("※")


def _market_advisory() -> str | None:
    """⚖ 인용 고지 — "정보 제공 목적 · 투자권유 시 자본시장법·당행 규정 준수 의무".

    출처는 `01_시황/` 두 문서가 원문에 스스로 적어둔 「유의사항(고지)」이고, `02_상품/`
    문서에는 같은 고지가 없다. 그래도 **05 카드 전부**에 싣는 것은 운영 판단이며 근거는
    폴더 README 와 `consult_agent/CLAUDE.md` §8 관리대장에 있다 — 원문에 없는 문장을
    원문 문서에 심지 않고, 선언은 폴더가 한 번만 한다.
    """
    return _readme_note("⚖")


def _market_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """본문 → (머리말, [(절 제목, 절 본문)]). 첫 H1 은 문서 제목이라 절이 아니다.

    머리말은 첫 H1 뒤 ~ 첫 절 헤더 앞의 텍스트다(부제·전제 인용 — 04 추천펀드는 본표가
    여기 온다). 뒤이은 H1/H2 는 전부 절 경계다 — 04 는 두 번째 표를 새 H1 로 시작한다.
    """
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    seen_title = False
    current: list[str] | None = None
    for raw in body.splitlines():
        m = _MARKET_HEAD.match(raw)
        if m:
            if not seen_title and m.group(1) == "#":
                seen_title = True
                continue
            current = []
            sections.append((clean(m.group(2)), current))
            continue
        if current is not None:
            current.append(raw.rstrip())
        elif seen_title:
            preamble.append(raw.rstrip())
    return ("\n".join(preamble).strip(),
            [(t, "\n".join(b).strip()) for t, b in sections])


#: 검색 예시로 쓸 front-matter 키워드의 최소 길이(정규화 후). 한 글자 낱말은 버린다 —
#: 주간시황의 `금`(금 시세)이 그렇다. 부분문자열로 절을 고르는 데 쓰이므로 한 글자는 거의
#: 모든 절에 걸리고("자금"·"금리"·"금융"), 그러면 어느 절이 답인지 검색이 못 가른다.
#: config.TOPIC_VOCAB 이 두 글자 낱말을 빼는 것과 같은 이유다.
_MIN_KEYWORD = 2


def _market_keywords(keywords: list[str]) -> list[str]:
    return [kw for kw in keywords
            if len(re.sub(r"[^0-9A-Za-z가-힣]", "", kw)) >= _MIN_KEYWORD]


# ─────────────────────────────────────────────────────────────
# 05 의 표 → 관계 선언 (knowledge/CLAUDE.md §1 값↔성립 조건)
#
# 05 문서의 알맹이는 산문이 아니라 **표**다 — 디폴트옵션 9종의 편입상품·비중·금리, TDF
# 빈티지별 위험자산 비중, 투자성향 5단계의 구성상품. 그 표를 텍스트 덩어리로만 실으면 두
# 가지가 동시에 막힌다.
#
#   ① 검색 입구가 없다. 「1975년생이면 TDF 몇 년짜리」의 답이 표 안에 버젓이 있는데
#      (출생연도 1975년 → TDF 2035) 카드의 검색 예시는 제목·문서 키워드뿐이라 n-gram 이
#      닿지 못했다. 표의 **열 머리말과 행 이름**이 곧 직원이 부르는 말이다.
#   ② 값–조건 오짝을 잡을 재료가 없다. `verify_texts` 는 수치의 집합 포함 검사라, 표 안에
#      있는 숫자를 **다른 행에 갖다 붙인** 답("알파드림 금리는 3.27" — 그건 수협은행 행의
#      값이다)이 그대로 통과한다. 표는 그 자체가 조건→값 구조인데 그걸 안 쓰고 있었다.
#
# 표를 행 단위로 펴서 선언하면 하나의 추출이 둘을 같이 푼다. **내용을 새로 만들지 않는다** —
# 원문 표의 칸을 그대로 옮길 뿐이고, `content` 의 원문도 그대로 남는다(fact 가 `value` 산문과
# `tiers` 쌍을 함께 갖는 것과 같은 구조다).
# ─────────────────────────────────────────────────────────────

#: 표 한 줄. `| a | b |` 형태.
_TABLE_LINE = re.compile(r"^\s*\|(.+)\|\s*$")
#: 머리말과 본문을 가르는 구분선. `|---|:--:|`
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

#: 행 이름 열로 볼 최소 비율. 그 열의 값 중 이만큼이 '글자가 든 이름'이어야 한다.
_KEY_COL_RATIO = 0.6

#: 값 칸으로 볼 최대 길이. 이보다 길면 산문 칸(상품특징 등)이다 — 그 안의 수치는 **다른
#: 행의 값으로 세지 않는다**. 산문에는 그 행을 설명하는 숫자가 섞여 있어서(「정기예금 70,
#: TDF 30 투자하는 포트폴리오」), 그걸 남의 값으로 세면 맞는 답변이 막힌다.
_VALUE_CELL_MAX = 24


def _table_cells(line: str) -> list[str]:
    return [_market_emphasis(c) for c in _TABLE_LINE.match(line).group(1).split("|")]


#: 수치에 붙는 단위. 이게 붙어 있어도 그 칸은 이름이 아니라 **값**이다.
#:
#: 날짜 단위(년·월·일)는 넣지 않는다 — 「20일」·「1975년」은 일정표·매핑표에서 **행 이름**
#: 이기 때문이다(외국인 배당금 지급 일정의 일자 열, TDF 출생연도 열). 넣었더니 지급 일정
#: 표 8행이 통째로 빠졌다. 금액·비율 단위만 값으로 본다.
_UNIT = r"(?:억원|만원|조원|천원|억|조|만|천|원|%|pt|bp|배)"


def _is_name(cell: str) -> bool:
    """이름 칸인가 — 글자가 들어 있고 순수 수치·날짜·«수치+단위»가 아니다.

    단위를 함께 보는 이유: 「+65,469억원」에는 «억원»이라는 글자가 있어서 글자 유무만 보면
    이름으로 읽힌다. 그러면 그 열이 이름 열로 잡히고, 표에 값 열이 하나도 안 남아 **표가
    통째로 버려진다** — 주간 자금 동향 표(코스피·코스닥 순매수)가 그렇게 빠져서 「코스피
    얼마야」가 검색되지 않았다(실측).
    """
    if not cell:
        return False
    if not re.search(r"[가-힣A-Za-z]", cell):
        return False
    if re.fullmatch(r"[\d.,\-~%\s]+", cell):
        return False
    return not re.fullmatch(rf"[+\-]?[\d.,]+\s*{_UNIT}?", cell)


def _key_columns(rows: list[list[str]]) -> int:
    """왼쪽부터 몇 개 열이 '행 이름' 열인가. 처음으로 값 열을 만나면 멈춘다.

    열 개수를 세는 이유는 표마다 이름 열이 다르기 때문이다 — 디폴트옵션 표는 셋
    (위험도·상품·편입상품), 추천펀드 표는 둘(구분·상품명), TDF 매트릭스는 하나(운용사).
    """
    n = max((len(r) for r in rows), default=0)
    for col in range(n):
        vals = [r[col] for r in rows if col < len(r) and r[col].strip()]
        if not vals:
            return col
        if sum(_is_name(v) for v in vals) / len(vals) < _KEY_COL_RATIO:
            return col
    return n


def _carry_keys(body: list[list[str]], ncol: int) -> list[list[str]]:
    """이름 열 ncol 개를 행마다 채운다 — 빈 칸은 **바로 위 행에서 이어받는다.**

    원문이 병합 셀로 적은 자리다(디폴트옵션 표의 「지켜드림」은 편입상품 3행에 걸쳐 한 번만
    적혀 있다). 이어받지 않으면 그 행이 어느 상품의 것인지 잃는다.
    """
    out: list[list[str]] = []
    carry: list[str] = [""] * ncol
    for cells in body:
        row_keys: list[str] = []
        for col in range(ncol):
            val = cells[col].strip() if col < len(cells) else ""
            if val:
                carry[col] = val
            elif carry[col]:
                val = carry[col]
            row_keys.append(val)
        out.append(row_keys)
    return out


def _identifying(filled: list[list[str]], ncol: int) -> set[tuple[int, str]]:
    """행을 **가리킬 수 있는** 이름 칸만 남긴다 — 윗 열이 다른 여러 행에 걸친 이름은 뺀다.

    병합 셀을 이어받으면 합계 행의 이름이 「포트폴리오」가 되는데, 그 말은 지켜드림·알파드림·
    모두드림 밑에 **전부** 달려 있어서 어느 상품의 합계인지 못 가린다. 그런 이름을 행 이름으로
    두면 답변에 「포트폴리오」라는 흔한 말이 한 번 나왔다는 이유로 남의 행까지 «답변이 말한
    행»이 되고, 그러면 다른 상품의 값을 갖다 붙인 답이 그대로 통과한다(실측으로 잡은 자리다 —
    「알파드림 포트폴리오 1년 수익률 8.56」은 알파드림 II 의 값인데 통과했다).

    판정은 «윗 열 조합이 하나뿐인가»로 한다. 「수협은행 노후보장 정기예금 디폴트옵션용(3년)」은
    알파드림 밑에만 있어 그 행을 가리키고, 「포트폴리오」는 아니다.
    """
    ident: set[tuple[int, str]] = set()
    for col in range(ncol):
        parents: dict[str, set[tuple[str, ...]]] = {}
        for row_keys in filled:
            val = row_keys[col]
            if val:
                parents.setdefault(val, set()).add(tuple(row_keys[:col]))
        ident |= {(col, val) for val, ups in parents.items() if len(ups) == 1}
    return ident


def _markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """본문의 마크다운 표를 `(열 머리말, 행들)` 로 훑는다 — 가공 없는 원표기 그대로.

    `_market_tables`(관계 선언)와 `_market_product_names`(상품 등록부)가 같은 표를 서로
    다른 목적으로 읽는다. 훑기를 각자 갖고 있으면 한쪽만 고쳐지는 자리가 생긴다.
    """
    out: list[tuple[list[str], list[list[str]]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _TABLE_LINE.match(lines[i]) or _TABLE_SEP.match(lines[i]):
            i += 1
            continue
        if i + 1 >= len(lines) or not _TABLE_SEP.match(lines[i + 1]):
            i += 1
            continue
        columns = [c.strip() for c in _table_cells(lines[i])]
        body: list[list[str]] = []
        j = i + 2
        while j < len(lines) and _TABLE_LINE.match(lines[j]) and not _TABLE_SEP.match(lines[j]):
            body.append(_table_cells(lines[j]))
            j += 1
        i = j
        out.append((columns, body))
    return out


#: 상품명이 적히는 열 머리말. **이 열의 칸만** 상품 등록부로 인정한다.
#:
#: 등록부는 「답변이 이 이름을 말해도 되는가」의 상한이다(pension_agent/verify.py). 원문
#: 산문에서 «KB ○○» 를 긁어 채우면, LLM 이 지어낸 이름이 우연히 문장과 겹칠 때 그 문장이
#: 스스로의 근거가 된다 — 표의 칸은 저작자가 «이게 상품 이름이다»라고 적어둔 자리라
#: 경계가 분명하다.
_PRODUCT_COLUMNS = ("상품명", "편입상품", "디폴트옵션 상품")


def _market_product_names(text: str) -> list[str]:
    """표의 상품명 칸에 적힌 이름들.

    판정 두 개를 표 파싱과 **같은 함수**로 한다 — 갈리면 등록부와 관계 선언이 서로 다른
    이름을 갖게 된다.

    · `_is_name()` — 병합 셀이 어긋나 흘러든 순수 수치·날짜(「100」·「2022-12-05」)를 뺀다.
    · `_identifying()` — 합계 행의 라벨을 뺀다. 디폴트옵션 표의 편입상품 열에는 상품 이름
      사이사이에 「포트폴리오」(합계 행)가 9번 나오는데, 그건 상품이 아니라 라벨이다.
      횟수로는 못 가린다 — 「지켜드림」도 편입상품 3행에 걸쳐 3번 나온다. 갈리는 것은
      **윗 열 조합이 하나뿐인가**다(지켜드림은 초저위험 밑에만, 포트폴리오는 아홉 상품 밑에
      전부). 등록부에 「포트폴리오」가 들어가도 `KB…` 후보와는 안 겹쳐 무해하지만,
      상품 등록부에 상품 아닌 말이 실려 있으면 읽는 사람이 먼저 속는다.
    """
    out: list[str] = []
    for columns, body in _markdown_tables(text):
        cols = [i for i, c in enumerate(columns) if c in _PRODUCT_COLUMNS]
        if not cols:
            continue
        ncol = _key_columns(body)
        filled = _carry_keys(body, ncol)
        ident = _identifying(filled, ncol)
        for r, cells in enumerate(body):
            for i in cols:
                # 이름 열이면 «행을 가리킬 수 있는가»를 묻고, 값 열이면 칸을 그대로 읽는다.
                val = (filled[r][i] if i < ncol
                       else (cells[i].strip() if i < len(cells) else ""))
                if i < ncol and (i, val) not in ident:
                    continue
                if val and _is_name(val) and val not in out:
                    out.append(val)
    return out


#: 백분율 열의 셀에서 값으로 읽는 꼴 — 마크다운 강조와 `%` 를 걷어낸 순수 수치.
#: 「**100**」·「3.40」·「-9.01」·「30%」가 전부 같은 자리의 값이다.
_PCT_CELL = re.compile(r"^\**\s*(-?\d+(?:\.\d+)?)\s*%?\s*\**$")


def _percent_values(columns: list[str], cells: list[str]) -> list[str]:
    """이 행에서 **백분율 열에 적힌 값**들(단위 없는 순수 수치).

    단위가 열 머리말에만 있는 표를 위한 것이다(`config.PERCENT_COLUMNS` 머리말). 어느 열이
    백분율인지는 **데이터가 선언**하고 여기서는 그 선언을 적용만 한다 — 선언에 없는 열은
    아무것도 하지 않는다.

    열 인덱스로 읽는다. `values` 는 빈 칸을 걸러낸 목록이라 열과 어긋나 있어서, 거기서
    되짚으면 「설정일」의 날짜가 비중 값이 되는 자리가 생긴다.
    """
    out: list[str] = []
    for i, col in enumerate(columns):
        if col.strip() not in config.PERCENT_COLUMNS or i >= len(cells):
            continue
        m = _PCT_CELL.match(cells[i].strip())
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _market_tables(text: str) -> list[dict]:
    """마크다운 표 → `{"columns", "units", "rows":[{"keys", "cells", "values", "percents"}]}`.

    빈 이름 칸은 **바로 위 행에서 이어받는다**. 원문이 병합 셀로 적은 자리라
    (디폴트옵션 표의 `| | | **포트폴리오** | … | **100** |` 합계 행), 이어받지 않으면 그
    행이 어느 상품의 것인지 잃는다 — 그러면 「알파드림 포트폴리오 수익률 4.23」이라는
    **맞는 답변**이 남의 값으로 몰려 막힌다.

    `units` 는 이 표에서 **백분율로 선언된 열**이고, `percents` 는 그 열에 적힌 행의 값이다
    (`config.PERCENT_COLUMNS`). 단위가 열 머리말에만 있어서 셀의 `35` 를 답변이 `35%` 라
    쓰는 순간 «자료에 없는 수치»가 되던 자리를 위한 것이다 — 소비는 대화형이 한다
    (`consult_agent/tools/market.py::market_evidence`). 원문(content)은 바뀌지 않는다.
    """
    out: list[dict] = []
    for columns, body in _markdown_tables(text):
        ncol = _key_columns(body)
        # 이름 열과 값 열이 둘 다 있어야 «어느 행의 값인가»를 말할 수 있다. 한쪽뿐인 표
        # (달력·일정표)는 선언하지 않는다 — 판정할 수 없는 것을 선언해두면 검사가 그것을
        # 근거로 삼는다.
        if ncol == 0 or ncol >= max((len(r) for r in body), default=0):
            continue

        filled = _carry_keys(body, ncol)
        ident = _identifying(filled, ncol)
        units = [c for c in columns if c.strip() in config.PERCENT_COLUMNS]
        rows: list[dict] = []
        for cells, row_keys in zip(body, filled):
            keys: list[str] = []
            for col, val in enumerate(row_keys):
                # 한 글자 이름(「상」 같은 표 머리말 값)과 «행을 못 가리는 이름»은 뺀다.
                if (val and val not in keys and (col, val) in ident
                        and len(re.sub(r"[^0-9A-Za-z가-힣]", "", val)) >= _MIN_KEYWORD):
                    keys.append(val)
            rest = [c.strip() for c in cells[ncol:] if c.strip()]
            if not keys or not rest:
                continue
            row = {"keys": keys, "cells": rest,
                   "values": [c for c in rest if len(c) <= _VALUE_CELL_MAX]}
            pct = _percent_values(columns, cells)
            if pct:
                row["percents"] = pct
            rows.append(row)
        if rows:
            table = {"columns": columns, "rows": rows}
            if units:
                table["units"] = {c: "%" for c in units}
            out.append(table)
    return out


def _table_triggers(tables: list[dict], limit: int = 30) -> list[str]:
    """표에서 나오는 검색 입구 — 열 머리말과 행 이름. 직원이 부르는 말이 여기 있다.

    「1975년생이면 TDF 몇 년짜리」의 `1975년` 은 **열 머리말**이고, 「알파드림 구성상품」의
    `알파드림` 은 **행 이름**이다. 둘 다 원문 표에 그대로 적혀 있는 말이라 지어내는 것이
    아니다.
    """
    out: list[str] = []
    for table in tables:
        for text in list(table.get("columns") or []) + [
                k for row in table.get("rows") or [] for k in row.get("keys") or []]:
            text = text.strip()
            if len(re.sub(r"[^0-9A-Za-z가-힣]", "", text)) < _MIN_KEYWORD:
                continue
            if len(text) > 40 or text in out:
                continue
            out.append(text)
    return out[:limit]


def _market_triggers(title: str, text: str, keywords: list[str], limit: int = 8) -> list[str]:
    """절 카드의 검색 예시 — 절 제목 + **그 절 본문에 실제로 나오는** 문서 키워드만.

    front-matter 키워드는 문서 단위라, 전부 모든 절에 달면 어느 절이 답인지 검색이 못
    가른다. 본문 등장 여부는 표기 차이("위험자산 비중" vs "위험자산비중")를 흡수하려고
    정규화(영숫자·한글만, 소문자) 후 부분문자열로 본다 — topics_of 와 같은 원리다.
    """
    flat = re.sub(r"[^0-9A-Za-z가-힣]", "", f"{title} {text}").lower()
    hits = [kw for kw in _market_keywords(keywords)
            if re.sub(r"[^0-9A-Za-z가-힣]", "", kw).lower() in flat]
    # 절 제목은 싣지 않는다(triggers_of 의 이유와 같다). 키워드가 하나도 안 걸린 절은 본문
    # 첫 절로 입구를 낸다 — 비어 있으면 그 절은 n-gram 폴백에서 제목 하나로만 잡힌다.
    return hits[:limit] or [c for c in (first_clause(text),) if c]


#: 원문 front-matter 의 category → 카드 종류. **시황과 상품은 다른 종류다** — 묻는 것이
#: 다르기 때문이다(시황은 «시장이 어떻게 돌아가나», lineup 은 «우리가 뭘 파나»). 하나로 묶으면
#: 「8월 추천펀드」를 물었는데 환율 전망이 따라 나오고, 계획 LLM 도 도구 하나로 둘을 다 받아야
#: 해서 무엇을 부를지 흐려진다. screen(직원이 단말에서)·channel(고객이 앱에서)을 같은 표에서
#: 나눈 것과 같은 이유다.
MARKET_KINDS: dict[str, str] = {"시황": "market", "상품": "lineup"}


def build_market() -> tuple[list[dict], dict[str, list[dict]]]:
    """05 폴더 → (doc 레코드, 종류별 카드). 하위 폴더 전부를 훑는다 — 주간·월간 정기자료가
    회차별로 쌓이는 폴더라(README 수록 규칙), 파일 목록을 코드에 적으면 다음 회차가 빠진다."""
    warn = _market_warn()
    if not warn:
        note("[05 경고없음] README 의 ※ 시효 안내를 찾지 못함 — market 카드에 시효 표시가 빠진다")
    advisory = _market_advisory()
    if not advisory:
        note("[05 고지없음] README 의 ⚖ 인용 고지를 찾지 못함 — 답변에 정보제공 고지가 빠진다")

    docs: list[dict] = []
    cards: dict[str, list[dict]] = {k: [] for k in MARKET_KINDS.values()}
    for path in sorted(config.MARKET_DIR.rglob("*.md")):
        if path.name == "README.md" or path.name.startswith("_"):
            continue
        fm, body = _market_front_matter(path.read_text(encoding="utf-8"))
        title = fm.get("title") or doc_title(path) or path.stem.replace("_", " ")
        category = fm.get("category")
        as_of = fm.get("as_of")
        if category not in MARKET_KINDS:
            note(f"[05 분류없음] {path.name} — front-matter category 가 시황/상품이 아님: {category!r}, 건너뜀")
            continue
        kind = MARKET_KINDS[category]
        out = cards[kind]
        if not as_of:
            note(f"[05 기준시점없음] {path.name} — front-matter as_of 없음, 건너뜀"
                 " (기준일 없는 시황·상품 수치는 인용 불가 — 폴더 README 수록 규칙)")
            continue

        seed = config.MARKET_DOCS.get(path.stem)
        if seed is None:
            note(f"[05 시드없음] {path.name} — config.MARKET_DOCS 에 부서·발행시점 시드 없음"
                 " (문서는 적재하되 출처 표기가 제목만 남는다)")
            seed = {}

        rel = str(path.relative_to(REPO)).replace("\\", "/")
        slug = _MARKET_SLUG.sub("-", re.sub(r"^\d+_", "", path.stem))
        doc_id = f"doc.k05.{slug}"
        confidentiality = fm.get("confidentiality") or ""
        # 고객 안내 가능 여부는 원문의 confidentiality 표기가 정한다 — "고객용" 이면 가능,
        # "행내" 표기면 내부용, 표기가 없으면 선언하지 않는다(추론하지 않는다, marks.py 규약).
        customer_facing = (True if confidentiality.startswith("고객용")
                           else False if "행내" in confidentiality else None)
        docs.append(record(doc_id, "doc", {
            "title": redact(title),
            "short": seed.get("short"),
            "dept": seed.get("dept"), "published": seed.get("published"),
            "origin": "시황상품", "tier": config.TIER_BY_ORIGIN["시황상품"],
            "customer_facing": customer_facing,
            "origin_file": fm.get("source_file") or path.name,
            "path": rel,
            "note": fm.get("origin") or None,
        }))

        preamble, sections = _market_sections(body)
        common = {
            "category": category, "group": title, "as_of": as_of,
            "volatile": warn, "advisory": advisory,
            "customer_facing": customer_facing,
        }
        prefix = "mkt" if kind == "market" else "lnp"
        overview_id = f"{prefix}.{slug}.00"
        ov_tables = _market_tables(preamble)
        out.append(record(overview_id, kind, {
            "title": title,
            "topic": clean(fm.get("topic") or "") or None,
            "key_points": fm.get("key_points") or None,
            "content": preamble or None,
            "parent": None,
            "tables": ov_tables or None,
            "product_names": _market_product_names(preamble) or None,
            # category(시황·상품)를 topics 에 넣지 않는다 — 두 글자 흔한 말이라 "구성상품"·
            # "편입상품"처럼 그 글자가 든 질문마다 **모든 카드가 똑같이** 가산점을 받아
            # 무더기 동점이 되고, 순위가 사실상 id 사전순으로 정해진다(config.TOPIC_VOCAB
            # 머리말이 금지한 바로 그것 — 실측으로 잡았다). 갈래는 category 필드가 이미 들고
            # 있고, 검색은 trigger_examples 와 표 이름이 한다.
            "tags": {"topics": topics_of(title, fm.get("topic") or "",
                                         " ".join(fm.get("key_points") or []))},
            # 제목은 싣지 않는다(triggers_of 의 이유와 같다) — 문서 키워드와 표 이름이 입구다.
            "trigger_examples": _market_keywords(fm.get("trigger_keywords") or [])[:24]
                                + _table_triggers(ov_tables),
            **common,
        }, source={"doc": doc_id, "locator": f"{rel} § 개요"}))

        nn = 0
        for sec_title, sec_body in sections:
            if any(sec_title.startswith(skip) for skip in _MARKET_SKIP):
                continue
            if not sec_body:
                note(f"[05 빈절] {path.name} § {sec_title} — 본문 없음, 건너뜀")
                continue
            nn += 1
            sec_tables = _market_tables(sec_body)
            out.append(record(f"{prefix}.{slug}.{nn:02d}", kind, {
                "title": sec_title,
                "content": sec_body,
                "parent": overview_id,
                "tables": sec_tables or None,
                "product_names": _market_product_names(sec_body) or None,
                "tags": {"topics": topics_of(sec_title, sec_body)},
                "trigger_examples": _market_triggers(sec_title, sec_body,
                                                     fm.get("trigger_keywords") or [])
                                    + _table_triggers(sec_tables),
                **common,
            }, source={"doc": doc_id, "locator": f"{rel} § {sec_title}"}))
        if nn == 0:
            note(f"[05 절없음] {path.name} — 절 카드가 0장이다(개요 카드만 적재됨)")

    # 백분율 열 선언이 하나도 안 붙은 표를 센다. **새 회차의 안전장치다** — 컬럼 표기가
    # 바뀌면(`1년` → `1개월`) `config.PERCENT_COLUMNS` 가 그 열을 못 알아보고, 그러면
    # 「비중 35%」 라고 쓴 맞는 답변이 다시 폐기돼 근거 원문이 덤프된다(그 사고가 이
    # 선언이 생긴 이유다). 동작은 안전한 쪽으로 실패하므로 — 선언이 없으면 지금과 같은
    # «% 허용 안 함» 이다 — 막지 않고 리포트에만 세운다. 백분율 열이 아예 없는 표
    # (일정표·구성 목록)도 여기 걸리는데, 그건 사람이 보고 판단할 일이다.
    for card in (c for group in cards.values() for c in group):
        for table in card["fields"].get("tables") or []:
            if table.get("units"):
                continue
            note(f"[05 백분율열없음] {card['id']} — 열 {' · '.join(table['columns'])}"
                 f" 중 config.PERCENT_COLUMNS 에 걸린 것이 없다(값에 % 를 붙인 답변은 폐기된다)")
    return docs, cards
