"""통합 레코드 스토어 — 모든 데이터의 단일 로더.

모든 지식/데이터 파일은 하나의 형태를 쓴다:

    { "meta": {"kind": "<종류>", "title", "as_of", "confidential", "doc_id"?, ...},
      "records": [ {"id", "kind"?, "fields": {...}, "source"?, "refs"?}, ... ] }

Store 는 지정한 루트들의 레코드 파일을 적재하고 `kind` 로 인덱싱한다. `_` 로 시작하는
파일·디렉토리는 건너뛴다(사람 검토 게이트 + 백업 격리).

소비 계층은 두 접근을 쓴다:
  · fields_of(kind)  → [{"id", **fields}]  — 기존 flat dict 소비부(engine)와 호환되는 뷰
  · records(kind)    → 원본 레코드(+ _doc_id·_doc_title·_source_file 부가) — 재구성용(kb)

새 데이터 추가는 루트에 레코드 파일 하나를 떨어뜨리면 끝이다. 파일별 배선이 없다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.kb_base import iter_knowledge_files  # noqa: E402


class Store:
    def __init__(self, roots: list[Path | str]):
        self._records: list[dict] = []
        self._files: list[dict] = []
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for fp in iter_knowledge_files(root):
                doc = json.loads(fp.read_text(encoding="utf-8"))
                meta = doc.get("meta") or {}
                doc_id = doc.get("doc_id") or meta.get("doc_id")
                title = meta.get("title")
                default_kind = meta.get("kind")
                self._files.append({"doc_id": doc_id, "title": title, "path": str(fp), "meta": meta})
                for rec in doc.get("records", []):
                    rec = dict(rec)
                    if not rec.get("kind"):
                        rec["kind"] = default_kind
                    rec["_doc_id"] = doc_id
                    rec["_doc_title"] = title
                    rec["_source_file"] = fp.name
                    self._records.append(rec)

    def records(self, kind: str | None = None) -> list[dict]:
        """원본 레코드(부가 메타 포함). kind 미지정 시 전체."""
        if kind is None:
            return list(self._records)
        return [r for r in self._records if r.get("kind") == kind]

    def fields_of(self, kind: str) -> list[dict]:
        """flat dict 뷰: {"id": ..., **fields}. 기존 row/strategy dict 소비부와 호환된다."""
        return [{"id": r.get("id"), **(r.get("fields") or {})} for r in self.records(kind)]

    def by_id(self) -> dict[str, dict]:
        return {r["id"]: r for r in self._records if r.get("id")}

    def doc_titles(self) -> dict[str, str]:
        """doc_id → 원본 문서 제목. 출처를 코드용 id 가 아니라 문서명으로 표기하기 위함."""
        return {f["doc_id"]: f["title"] for f in self._files if f.get("doc_id") and f.get("title")}

    def kinds(self) -> set[str]:
        return {r.get("kind") for r in self._records if r.get("kind")}
