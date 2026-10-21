"""문서가 가리키는 .py 경로 실재 — 파일 이동 시 문서 참조가 조용히 죽는 것 방지.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)

- 대상: 루트 `CLAUDE.md`·`docs/`·`src/` 아래 .md (지식베이스 원문 폴더·`skills/` 제외)
- `a/b.py` 꼴(디렉터리 한 단 이상)을 추출해 저장소 .py 경로의 꼬리 일치로 판정 — 동명 파일은 미구분
- 제외: 변경이력 표 행(`| 2026-08-24 | …`) · 한 단짜리 `foo.py`
"""

from __future__ import annotations

import re
from pathlib import Path

from pension_agent.common import config
from tests.infra._common import check

_ROOT = config.REPO_ROOT
_DOCS = sorted({
    _ROOT / "CLAUDE.md",
    *(_ROOT / "docs").glob("*.md"),
    *config.SRC_ROOT.rglob("*.md"),
})
_DOCS = [p for p in _DOCS if p.exists()]

_ALL_PY = {p.relative_to(_ROOT).as_posix() for p in _ROOT.rglob("*.py") if ".git" not in p.parts}

# 디렉터리 한 단 이상 + 파일명.py, 앞뒤가 경로 문자가 아닐 것(`../` 는 아래에서 제거)
_REF = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.py)\b")
_HISTORY_ROW = re.compile(r"^\s*\|\s*20\d\d-\d\d-\d\d\s*\|")


def _exists(ref: str) -> bool:
    ref = ref.lstrip("./")
    return ref in _ALL_PY or any(p.endswith("/" + ref) for p in _ALL_PY)


_dead: list[str] = []
_seen = 0
for _doc in _DOCS:
    for _line in _doc.read_text(encoding="utf-8").splitlines():
        if _HISTORY_ROW.match(_line):
            continue
        for _m in _REF.finditer(_line):
            _seen += 1
            if not _exists(_m.group(1)):
                _dead.append(f"{_doc.relative_to(_ROOT)}: {_m.group(1)}")

check(_seen > 50, "문서에서 .py 경로 참조를 읽어냈다", str(_seen))
check(not _dead, "문서가 가리키는 .py 경로가 전부 실재한다", "; ".join(sorted(set(_dead))[:10]))
