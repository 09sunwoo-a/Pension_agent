"""통합 레코드 스토어 — `{meta, records:[{id, kind?, fields, source?, refs?}]}` 파일의 단일 로더.

`_` 로 시작하는 파일(미검토 `_draft_*`·`_TEMPLATE`)은 건너뜀. meta.title 은 파일 이름표라 출처 표기에 미사용.
접근: fields_of(kind) → flat dict 뷰 · records(kind) → 원본 레코드(+ _doc_id·_doc_title·_source_file).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


def iter_knowledge_files(knowledge_dir: Path) -> Iterator[Path]:
    """'_' 로 시작하지 않는 *.json 을 정렬 순으로 반환."""
    for f in sorted(Path(knowledge_dir).glob("*.json")):
        if not f.name.startswith("_"):
            yield f


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
                    # 파일 단위 원천 문서 기본값 — 레코드의 source.doc 가 우선
                    rec["_doc_ref"] = meta.get("source_doc")
                    rec["_source_file"] = fp.name
                    self._records.append(rec)

    def files(self) -> list[dict]:
        """적재 파일 목록(doc_id·title·path·meta)."""
        return list(self._files)

    def records(self, kind: str | None = None) -> list[dict]:
        """원본 레코드(부가 메타 포함). kind 미지정 시 전체."""
        if kind is None:
            return list(self._records)
        return [r for r in self._records if r.get("kind") == kind]

    def fields_of(self, kind: str) -> list[dict]:
        """flat dict 뷰: {"id": ..., **fields}."""
        return [{"id": r.get("id"), **(r.get("fields") or {})} for r in self.records(kind)]

    def by_id(self) -> dict[str, dict]:
        return {r["id"]: r for r in self._records if r.get("id")}

    def doc_titles(self) -> dict[str, str]:
        """doc_id → 원본 문서 제목."""
        return {f["doc_id"]: f["title"] for f in self._files if f.get("doc_id") and f.get("title")}

    def kinds(self) -> set[str]:
        return {r.get("kind") for r in self._records if r.get("kind")}
