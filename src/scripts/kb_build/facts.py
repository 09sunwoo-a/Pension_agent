"""06/04 제도상품팩트 → fact.

`build_kb.py` 에서 갈라낸 모듈이다 — 변환기의 원칙(결정론·멱등 · 무손실 출처 · 검토 게이트 ·
개인정보 미이관)은 그 파일 머리말에 있고, 실행도 거기서 한다: python -m scripts.kb_build.build_kb
"""

from __future__ import annotations

import re

from scripts.kb_build import config

from scripts.kb_build.common import EXTRACT, clean, note, record, redact, topics_of, triggers_of
from scripts.kb_build.docs import DocResolver
from scripts.kb_build.market import _TABLE_LINE, _market_tables
from scripts.kb_build.parse import parse_index, parse_items


# ─────────────────────────────────────────────────────────────
# 6) 06/04 제도상품팩트 → fact
#
# 04 는 "팩트 1개 = 항목 1개 = 확정값 1개" 규격이라 필드가 규칙적이다. 다만 한 불릿에 여러
# 라벨이 ' · ' 로 이어져 있고(`- **상태**: ✅ 확정 · **성격**: 법령·세제 · **기준**: …`),
# 값 안에도 '·' 가 들어가므로("법령·세제") 구분자로 자르면 안 된다 — 라벨 위치로 구간을 자른다.
# ─────────────────────────────────────────────────────────────

_FACT_LABEL = re.compile(r"\*\*([^*]{2,10})\*\*\s*:\s*")


_SCREEN = re.compile(r"\[\d{2}-[0-9A-Z]{2}-[0-9A-Z]{3}\]")


def _labeled_slots(line: str) -> dict[str, str]:
    """`**라벨**: 값 · **라벨**: 값` 한 줄을 {라벨: 값} 으로 자른다."""
    marks = list(_FACT_LABEL.finditer(line))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        value = line[m.end():end].strip().rstrip("·").strip()
        out[re.sub(r"\s+", "", m.group(1))] = value
    return out


#: 검증 포인트를 항목으로 쪼개는 표지. 04 는 ①~⑩ 로 나눠 적는다(하나뿐이면 번호가 없다).
_PITFALL_SPLIT = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]\s*")

#: 검증 포인트 안의 따옴표 인용. 인용 자체는 옳은 말일 수도 있어서, 이것만으로는 오답이 아니다.
_QUOTED = re.compile(r"[\"“]([^\"“”]{4,80})[\"”]")

#: 인용 **뒤에** 이 말이 붙어 있어야 "그 표현은 틀렸다"는 뜻이다.
#
# 이 판정이 필요한 이유: 04 의 검증 포인트는 틀린 표현과 옳은 표현을 **똑같이 따옴표로**
# 인용한다. `기준은 "평가금액"(가장 정확)` 의 평가금액은 옳은 말이고, `9번("퇴직금 포함 시
# 5년 전 가능" → O)` 의 인용문은 참인 진술이다. 따옴표만 보고 오답 목록에 넣으면 **맞는
# 답을 막는** 검사가 된다 — 검증기가 옳은 문장을 거부하는 것은 틀린 문장을 통과시키는
# 것보다 나쁘다(직원은 왜 막혔는지 알 수 없다).
_WRONG_MARKS = ("오답", "오기", "오안내", "→ X", "→X", "말 것", "금지", "틀린", "혼동")

#: 인용 뒤 몇 글자까지 표지를 찾을지. 같은 항목 안의 **다음** 인용에 붙은 표지를 끌어오지
#: 않을 만큼 좁게 둔다.
_MARK_WINDOW = 25

#: 오답 문자열의 최소 길이. 아래 `_is_phrase` 참고.
_MIN_BARE = 8


def _is_phrase(text: str) -> bool:
    """오답 대조에 쓸 수 있는 **구절**인가. 값 하나만 있는 인용은 쓸 수 없다.

    대조는 답변 안에 그 문자열이 있는지로 하는데, 값 하나짜리 인용은 다른 팩트의 **맞는**
    문장에도 그대로 들어간다 — F22 의 오답 `"5천만원"`(2021년 실효된 예금자보호 한도)은
    F53 의 맞는 문장("퇴직금 5천만원 이상이면 수수료 면제")에도 있다. 그걸 오답으로 잡으면
    검증기가 옳은 답을 막는다.

    구절이면(띄어쓰기가 있거나 충분히 길면) 그런 우연한 일치가 사실상 없다. 값 하나짜리
    오답은 여기서 버린다 — 대조할 수 없는 것은 대조하지 않는다(선언이 없는 것과 같다).
    """
    return " " in text or len(text) >= _MIN_BARE


def _wrong_quotes(text: str) -> list[str]:
    """이 항목이 '틀렸다'고 지목한 표현만 뽑는다. 지목이 없으면 빈 목록."""
    out: list[str] = []
    for m in _QUOTED.finditer(text):
        tail = text[m.end():m.end() + _MARK_WINDOW]
        if "→ O" in tail or "→O" in tail:
            continue                       # 참이라고 표시된 인용
        quoted = m.group(1).strip()
        if any(w in tail for w in _WRONG_MARKS) and _is_phrase(quoted):
            out.append(quoted)
    return out


def _tiers(raw: str) -> list[dict]:
    """`조건 → 값; 조건 → 값` 한 줄을 조건–값 쌍으로 쪼갠다(knowledge/CLAUDE.md 관계 1).

    구분자를 `;` 로 둔 이유는 이 문서의 조건·값 텍스트에 `·` 가 흔히 들어가기 때문이다
    ("법령·세제", "운용관리·자산관리"). `·` 로 자르면 한 쌍이 둘로 쪼개진다.
    """
    out: list[dict] = []
    for part in raw.split(";"):
        if "→" not in part:
            continue
        when, _, value = part.partition("→")
        when, value = clean(when).strip(), clean(value).strip()
        if when and value:
            out.append({"when": when, "value": value})
    return out


def _pitfalls(raw: str) -> list[dict]:
    """검증 포인트 산문을 항목으로 쪼갠다(관계 2).

    **내용을 새로 만들지 않는다** — 행원들이 이미 적어둔 "자주 틀리는 지점"을 기계가
    대조할 수 있는 단위로 나눌 뿐이다. 항목이 틀린 표현을 따옴표로 인용하고 있으면
    (`"5,500만원 이상 13.2%" = 오기`) 그 인용문을 `wrong` 으로 뽑는다 — 답변에 그 문자열이
    그대로 나타났는지가 곧 알려진 오답과의 일치다. **옳은 표현의 인용은 뽑지 않는다**
    (`_wrong_quotes` 주석). 지목이 없는 항목은 `wrong` 없이 주의 문장으로만 남는다 —
    대조할 문자열이 없으면 대조하지 않는다.
    """
    parts = [clean(x).strip() for x in _PITFALL_SPLIT.split(raw) if clean(x).strip()]
    out: list[dict] = []
    for text in parts:
        out.append({"wrong": _wrong_quotes(text), "why": text})
    return out


def _fact_tables_text(body: list[str]) -> str:
    """팩트 절에 있는 마크다운 표를 **원문 그대로** 이어 붙인다(표 사이는 빈 줄)."""
    blocks: list[list[str]] = []
    for raw in body:
        if _TABLE_LINE.match(raw.strip()):
            if not blocks or blocks[-1] is None:
                blocks.append([])
            blocks[-1].append(raw.rstrip())
        elif blocks and blocks[-1] is not None and blocks[-1]:
            blocks.append(None)          # 표 하나가 끝났다는 표시
    return "\n\n".join("\n".join(b) for b in blocks if b)


def _fact_status(raw: str) -> tuple[str, list[str]]:
    """상태 표기(✅ 확정 / ⚠ 확인 필요 / ⏳ 시효 민감, 복합 가능) → (대표 상태, 표시 전체)."""
    marks = [m for m in ("✅", "⚠", "⏳") if m in raw]
    label = {"✅": "확정", "⚠": "확인 필요", "⏳": "시효 민감"}
    primary = "확정" if "✅" in marks else ("시효 민감" if "⏳" in marks else "확인 필요")
    return primary, [label[m] for m in marks]


def build_facts(resolver: DocResolver) -> tuple[list[dict], list[dict]]:
    """활성 팩트와 보류 팩트를 나눠 돌려준다.

    04 자체 규칙이 "⚠ 확인 필요 항목은 해소 전까지 검증 기준으로 쓰지 않는다" 이므로, 확정 근거가
    없는 팩트는 활성 데이터에 넣지 않고 별도 보류 파일로 뺀다 — 지식베이스에 들어가는 순간
    에이전트가 그걸 사실로 답하기 때문이다.
    """
    src = EXTRACT / "04_제도상품팩트.md"
    lines = src.read_text(encoding="utf-8").splitlines()
    index = parse_index(lines, stop="## [")
    body_start = next(i for i, ln in enumerate(lines) if ln.startswith("## ["))

    active: list[dict] = []
    pending: list[dict] = []
    for item in parse_items(lines, body_start):
        no = item["no"]
        group = (index.get(no) or {}).get("group") or "미분류"
        slots: dict[str, str] = {}
        statement = ""
        for raw in item["body"]:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("**팩트**"):
                statement = clean(line.split(":", 1)[-1])
            elif line.startswith("- **") or line.startswith("**"):
                slots.update({k: clean(v) for k, v in _labeled_slots(line).items()})
        if not statement:
            note(f"[팩트없음] {no} — **팩트** 문장을 찾지 못해 건너뜀")
            continue

        status, flags = _fact_status(slots.get("상태", ""))
        attribution = redact(slots.get("출처", ""))
        title = clean(item["title"])
        fields = {
            "no": no,
            # 검색 색인에 실리기 위한 필드 셋. 팩트는 오래도록 «id 로 참조되는 값»이기만
            # 해서(kinds.json `consumed: reference`) 카드 색인 밖에 있었고, 그래서 **9종 재료
            # 중 유일하게 LLM 카드 선택의 후보가 못 됐다** — 다른 종류는 LLM 이 버킷→카드로
            # 고르고 못 고를 때만 n-gram 으로 물러서는데(select.pick), 팩트는 n-gram 하나뿐이라
            # 직원 말과 카드 말이 다르면("연말정산 얼마나 돌려받아?" vs 「세액공제」) 0건이 났다.
            # 아래 넷이 그 색인이 요구하는 것이다(title·group·tags.topics·trigger_examples).
            "title": title,
            "group": group,
            "tags": {"topics": topics_of(title, statement)},
            # 트리거는 팩트 문장 첫 절과 검증 포인트 첫 절이다 — 다른 종류와 같은 규약.
            # 검증 포인트는 직원이 실제로 틀리게 묻는 말("900만원이 IRP 단독 한도인가")이라
            # 검색 입구로 맞다.
            "trigger_examples": triggers_of(f"fact.k04.{no.lower()}", statement,
                                            slots.get("검증포인트")),
            # label 은 04 제목 전체를 쓴다. 레거시 fact 는 "연간 납입한도" 처럼 짧은 라벨이라,
            # 같은 주제라도 문자열이 달라 check_fact_conflicts 의 오탐이 나지 않는다. 값이 정말
            # 어긋나는지는 변환 리포트(_draft_kb_fact_review.md)로 사람이 본다.
            "label": title,
            "value": statement,
            "category": group,
            "as_of": slots.get("기준") or None,
            "status": status,
            "nature": slots.get("성격") or None,
            "customer_facing": "⭕" in slots.get("대외안내", ""),
            "verify_points": slots.get("검증포인트") or None,
            # 원문 표를 **그대로** 싣는다. 위 파서는 `**팩트**:` 한 줄과 `- **키**: 값`
            # 슬롯만 줍고 `| … |` 줄은 어느 쪽에도 안 걸려 **조용히 버려졌다** — 그래서 F40 은
            # label 이 「인출순서 4단계 × 세제」를 약속하는데 본문은 "인출순서와 원천별
            # 세제:" 에서 끊긴 카드가 됐고, 직원이 그 표를 물으면 «자료가 없다»가 나갔다.
            # 원문에는 있는데도.
            #
            # **싣는 것과 선언하는 것은 다른 일이다.** 아래 `tables` 는 값–조건 오짝을 대조할
            # 수 있는 표만 선언한다(이름 열과 값 열이 갈리는 표 — `_market_tables`). 갈리지
            # 않는 표(F17 대응표·F40 세제표는 값 칸이 「과세제외」처럼 말이다)는 선언하지
            # 못하지만, **재료로는 실려야 한다** — 판정할 수 없다고 답하지 못할 이유는 없다.
            "content": _fact_tables_text(item["body"]) or None,
            # 관계 선언(knowledge/CLAUDE.md §1·§2) — 답변이 값과 조건을 잘못 짝지었는지,
            # 알려진 오답을 그대로 말했는지 코드가 대조하는 재료다. 선언이 없는 팩트는
            # 대조 대상이 아니다(커버리지 = 저작된 범위).
            "tiers": _tiers(slots.get("조건별값", "")),
            # 원문 표. 팩트 절의 알맹이가 표인 경우가 있는데(F40 인출순서 4단계 × 세제,
            # F17 디폴트옵션 10종 대응표, F75), 위 파서는 `**팩트**:` 한 줄과 `- **키**: 값`
            # 슬롯만 줍고 `| … |` 줄은 어느 쪽에도 안 걸려 **조용히 버려졌다**. 그래서
            # label 은 「감면 30/40/50% 3단」을 약속하는데 본문은 "인출순서와 원천별 세제:"
            # 에서 끊긴 카드가 됐고, 직원이 그 표를 물으면 «자료가 없다»가 나갔다 — 원문에는
            # 있는데. 05 시황·상품이 쓰는 추출기를 그대로 쓴다(이름만 market 이고 범용이다).
            "tables": _market_tables("\n".join(item["body"])) or None,
            "pitfalls": _pitfalls(slots.get("검증포인트", "")),
            "history": slots.get("이력") or None,
            "screens": sorted(set(_SCREEN.findall(statement))),
            "source_text": attribution or None,
            "detail": " · ".join(flags) if len(flags) > 1 else None,
        }
        rec = record(f"fact.k04.{no.lower()}", "fact", fields,
                     source={"doc": resolver.resolve(attribution, f"팩트 {no}") if attribution else None,
                             "locator": f"{config.EXTRACT_REL}/04_제도상품팩트.md § {no}. {title}"})
        (pending if status == "확인 필요" else active).append(rec)

    return active, pending
