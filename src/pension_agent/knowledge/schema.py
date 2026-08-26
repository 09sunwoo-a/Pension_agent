"""레지스트리 구동 검증 + 단일 저작 프롬프트 생성기.

검증기·저작 프롬프트가 전적으로 common/kinds.json 의 선언에서 나온다. 데이터 종류마다
코드/프롬프트를 새로 짜지 않는다 — 새 종류는 kinds.json 에 항목 하나만 추가하면 된다.

CLI:
    python -m common.schema validate <루트...>     # 통합 검증 (ERROR/WARN)
    python -m common.schema prompt <kind>          # 그 종류의 저작 프롬프트 출력
    python -m common.schema kinds                  # 등록된 종류 목록
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


import re

from pension_agent.knowledge.checks import check_duplicate_ids, check_fact_conflicts
from pension_agent.knowledge import Store

REGISTRY_PATH = Path(__file__).resolve().parent / "kinds.json"

# 사내 게시판 특유의 "이름(부점/직급)" 저자 표기 — 원본을 저작할 때 옮기면 안 되는 개인 식별정보.
_AUTHOR_ATTRIBUTION = re.compile(
    r"[가-힣]{2,4}\s*\([^)]{0,40}(지점|센터|영업부|출장소)[^)]{0,20}"
    r"(팀원|팀장|과장|대리|차장|조사역|지점장)[^)]{0,10}\)"
)

# 작성자가 **누구인지**로 자료가 **무엇인지**를 추론한 문장.
#
# 실제로 나갔던 답변: "작성자가 인재개발부 소속이라 교육 목적으로 정리된 자료로 보이나
# 본부 공식 가이드는 아니다." 소속은 자료의 성격을 말해주지 않는다. 이런 문장은 ① 확인할
# 수단이 없고 ② 데이터가 판정할 재료도 없어(관계 선언이 아니다) 코드 대조를 통과해버리며
# ③ 직원에게는 검증된 결론처럼 읽힌다. 자료의 지위는 문서 레지스트리(출처 종류·등급)가
# 정하고, 카드는 그것을 다시 추론하지 않는다 — `CLAUDE.md` 「주의 ↔ 역할·적용 조건」.
#
# 원문 인용(quotes·source_text)은 훑지 않는다. 원문은 고치지 않으므로 경고해도 조치할 수
# 없고, 저자 표기가 거기 남는 것은 출처라서다(루트 절대 규칙 1).
_IDENTITY = r"(?:소속|부점|직급|본부|사업부|개발부|컨설팅부|지점|센터|팀장|차장|과장|대리|조사역|팀원)"
_GUESS = r"(?:보인다|보이나|보이며|보이므로|듯하다|듯한|추정|일 것|것으로 판단|짐작)"
_IDENTITY_INFERENCE = re.compile(
    rf"(?:작성자|필자|글쓴이|작성 부서)[^.\n]{{0,40}}{_IDENTITY}[^.\n]{{0,80}}{_GUESS}"
)

#: 원문 인용 필드 — 저작이 만든 파생 텍스트가 아니라 옮겨온 원문이다.
_QUOTE_FIELDS = ("quotes", "source_text", "quote")


def _derived_strings(value, key: str = "") -> list[tuple[str, str]]:
    """저작이 쓴 파생 텍스트만 (필드경로, 문자열) 로 펼친다. 원문 인용은 건너뛴다."""
    if key in _QUOTE_FIELDS:
        return []
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _derived_strings(v, k)]
    if isinstance(value, list):
        return [s for v in value for s in _derived_strings(v, key)]
    return []


def load_registry() -> dict[str, dict]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


REGISTRY = load_registry()


# ─────────────────────────────────────────────────────────────
# 검증 — 종류 선언 기준
# ─────────────────────────────────────────────────────────────

def _type_ok(value, spec: dict) -> bool:
    """선언된 타입과 대략 일치하는지. 관대하게 — enum·명백한 타입 오류만 잡는다."""
    if value is None:
        return bool(spec.get("nullable"))
    t = spec.get("type")
    if t == "enum":
        return value in (spec.get("values") or [])
    if t == "bool":
        return isinstance(value, bool)
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "list":
        return isinstance(value, list)
    if t == "obj":
        return isinstance(value, dict)
    return True  # text 등은 강제하지 않는다


def validate(roots: list[Path | str], registry: dict | None = None) -> tuple[list[str], list[str]]:
    """통합 검증. 파일의 kind 로 디스패치해 필수필드·타입·enum·참조·사실충돌을 검사한다."""
    reg = registry or REGISTRY
    st = Store(roots)
    records = st.records()
    errors: list[str] = []
    warns: list[str] = []

    all_ids = {r["id"] for r in records if r.get("id")}
    errors += check_duplicate_ids([r["id"] for r in records if r.get("id")])
    # 출처 레지스트리(doc kind)가 스토어에 있으면 레코드 source.doc 는 그 id 를 가리켜야 한다 —
    # 레지스트리와 카드가 조용히 어긋나는 것을 막는다. doc kind 가 전혀 없는 루트(레거시 데이터만)는
    # source.doc 가 파일 doc_id 를 가리키는 옛 규약이므로 검사하지 않는다.
    doc_ids = {r["id"] for r in st.records("doc") if r.get("id")}

    # 파일 단위 원천 문서 선언(meta.source_doc)도 레지스트리를 가리켜야 한다. 레코드가
    # 자기 source.doc 를 갖지 않는 손저작 파일의 출처가 여기 하나에 걸려 있어서,
    # 오타 하나면 그 파일 카드 전체가 "출처 미상"으로 나간다.
    for f in st.files():
        sdoc = (f.get("meta") or {}).get("source_doc")
        if doc_ids and sdoc and sdoc not in doc_ids:
            errors.append(f"[깨진참조] {Path(f['path']).name} meta.source_doc '{sdoc}' — "
                          f"원천 문서 레지스트리에 없음")

    for r in records:
        rid = r.get("id") or "?"
        if not r.get("id"):
            errors.append(f"[필수누락] {r.get('_source_file', '?')} → 레코드 id 없음")
        kind = r.get("kind")
        if not kind:
            errors.append(f"[필수누락] {rid} → kind 없음")
            continue
        kspec = reg.get(kind)
        if kspec is None:
            errors.append(f"[미등록종류] {rid} kind={kind} — kinds.json 에 선언 없음")
            continue

        fields = r.get("fields") or {}
        for req in kspec.get("required", []):
            if fields.get(req) in (None, "", [], {}):
                errors.append(f"[필수누락] {rid}({kind}) → fields.{req}")

        for fname, fspec in (kspec.get("fields") or {}).items():
            if fname in fields and not _type_ok(fields[fname], fspec):
                exp = fspec.get("values") if fspec.get("type") == "enum" else fspec.get("type")
                errors.append(f"[잘못된값] {rid}({kind}).{fname}={fields[fname]!r} — 기대 {exp}")
            if fname in fields and fspec.get("type") == "text" and isinstance(fields[fname], str):
                if _AUTHOR_ATTRIBUTION.search(fields[fname]):
                    warns.append(
                        f"[개인정보의심] {rid}({kind}).{fname} — 작성자 실명·부점·직급으로 보이는 "
                        f"패턴 감지, 저작 시 옮기지 않았는지 확인"
                    )

        for fname, text in _derived_strings(fields):
            if _IDENTITY_INFERENCE.search(text):
                warns.append(
                    f"[신원추론] {rid}({kind}).{fname} — 작성자가 누구인지로 자료의 성격을 "
                    f"추론한 문장으로 보임. 자료의 지위는 출처 종류가 정한다(문서 레지스트리) — "
                    f"확인 가능한 사실로 다시 쓸 것"
                )

        if kind == "fact" and not fields.get("as_of"):
            warns.append(f"[최신성미기재] {rid}(fact) → fields.as_of 없음 — 근거 기준시점 확인 필요")

        for ref in r.get("refs", []):
            if ref not in all_ids:
                errors.append(f"[깨진참조] {rid} refs '{ref}' — 스토어에 없음")

        src = r.get("source")
        if src is not None and not isinstance(src, dict):
            errors.append(f"[잘못된값] {rid}.source — 객체(예: {{'page': 1}})여야 함")
        elif doc_ids and isinstance(src, dict) and src.get("doc") and kind != "doc":
            sdoc = src["doc"]
            # doc 레지스트리 id(doc.*)를 가리키는 새 규약만 검사한다. 레거시 파일 doc_id 인용은 그대로 둔다.
            if sdoc.startswith("doc.") and sdoc not in doc_ids:
                errors.append(f"[깨진참조] {rid}.source.doc '{sdoc}' — doc 레지스트리에 없음")

    # 사실충돌 — 같은 label 에 다른 value (개정 반영 누락)
    fact_pairs = [(f.get("fields", {}).get("label", ""), f.get("fields", {}).get("value", ""))
                  for f in st.records("fact")]
    errors += check_fact_conflicts([(lab, val) for lab, val in fact_pairs if lab])

    return errors, warns


# ─────────────────────────────────────────────────────────────
# 저작 — 단일 스키마 구동 프롬프트 생성기
# ─────────────────────────────────────────────────────────────

def _field_spec_text(kspec: dict) -> str:
    lines = []
    req = set(kspec.get("required", []))
    for fname, fspec in (kspec.get("fields") or {}).items():
        t = fspec.get("type")
        desc = f"enum{fspec.get('values')}" if t == "enum" else t
        flags = []
        if fname in req:
            flags.append("필수")
        if fspec.get("nullable"):
            flags.append("없으면 null")
        tail = f"  ({', '.join(flags)})" if flags else ""
        lines.append(f"    - {fname}: {desc}{tail}")
    return "\n".join(lines)


def authoring_prompt(kind: str, registry: dict | None = None) -> str:
    """그 종류의 저작 프롬프트. 사내 LLM 에 (이 프롬프트 + 원문)을 주면 규격 JSON 이 나온다."""
    reg = registry or REGISTRY
    kspec = reg.get(kind)
    if kspec is None:
        raise KeyError(f"등록되지 않은 종류: {kind} (등록: {sorted(reg)})")
    return f"""너는 퇴직연금 사후관리 에이전트의 데이터 저작 도우미다. **첨부한 문서**(또는 아래
붙여넣은 원문)를 읽고 '{kind}' 레코드를 추출해 지정한 JSON 하나로만 출력하라.

[종류] {kind} — {kspec.get('desc', '')}

[규칙]
- 문서에 있는 내용만 쓴다. 수치·상품명·조항·날짜를 새로 지어내지 않는다.
- 스캔·이미지 문서면 표·본문에 보이는 값을 그대로 옮긴다. 안 보이거나 불확실하면 null.
- 확인 안 되는 값은 추정하지 말고 null. (엔진·검증기가 보수적으로 처리한다)
- id 는 전역 고유. 관례: 상품 Pnn · 전략 st.snake · 사실 fact.snake · 화법 {{doc_id}}.pNN.
- 같은 사실(수치)은 fact 로 한 번만 정의하고 다른 레코드는 refs 로 참조한다(값 복붙 금지).
- 출처는 source={{"doc","page"}}, 다른 레코드 참조는 refs=["id", ...] 로 남긴다.
- **출력은 아래 형태의 JSON 하나만.** 설명·주석·코드블록(```) 없이 JSON 그대로 낸다.

[필드 스키마]
{_field_spec_text(kspec)}

[출력 형태]
{{
  "meta": {{"kind": "{kind}", "title": "<원본 문서명>", "as_of": "YYYY-MM", "confidential": true}},
  "records": [
    {{"id": "<고유 id>", "kind": "{kind}",
      "fields": {{ <위 스키마의 필드들> }},
      "source": {{"doc": "<doc_id>", "page": <n>}},
      "refs": [] }}
  ]
}}

[문서]  (파일을 첨부했다면 이 줄은 비워 둔다. 텍스트면 아래에 붙여넣는다)
<<< >>>"""


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = sys.argv[1:]
    cmd = args[0] if args else "help"

    if cmd == "validate":
        roots = args[1:] or ["."]
        errs, warns = validate(roots)
        print(f"검증 루트: {', '.join(str(r) for r in roots)}")
        print("✅ ERROR 없음" if not errs else f"❌ ERROR {len(errs)}건")
        for e in errs:
            print("   " + e)
        if warns:
            print(f"⚠️  WARN {len(warns)}건")
            for w in warns:
                print("   " + w)
        raise SystemExit(1 if errs else 0)

    if cmd == "prompt":
        if len(args) < 2:
            print("사용법: python -m common.schema prompt <kind>")
            raise SystemExit(2)
        print(authoring_prompt(args[1]))
        raise SystemExit(0)

    if cmd == "kinds":
        for k, v in REGISTRY.items():
            print(f"  {k:<16} {v.get('consumed', ''):<11} {v.get('desc', '')}")
        raise SystemExit(0)

    print(__doc__)
    raise SystemExit(0)
