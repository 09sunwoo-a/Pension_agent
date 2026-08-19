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

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.kb_base import check_duplicate_ids, check_fact_conflicts  # noqa: E402
from common.store import Store  # noqa: E402

REGISTRY_PATH = Path(__file__).resolve().parent / "kinds.json"


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

        for ref in r.get("refs", []):
            if ref not in all_ids:
                errors.append(f"[깨진참조] {rid} refs '{ref}' — 스토어에 없음")

        src = r.get("source")
        if src is not None and not isinstance(src, dict):
            errors.append(f"[잘못된값] {rid}.source — 객체(예: {{'page': 1}})여야 함")

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
