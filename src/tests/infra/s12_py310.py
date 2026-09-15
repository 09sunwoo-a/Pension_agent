"""Python 3.10 호환 — 3.11+ 전용 이름을 임포트하지 않는가

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# Python 3.10 호환 — 3.11+ 전용 이름을 임포트하지 않는가
#
# 로컬(발표자 노트북)은 3.10, 개발 환경은 3.11+ 라 여기 테스트가 전부 통과해도 로컬에서
# 임포트가 죽을 수 있다 — session_store 의 `from datetime import UTC`(3.11 별칭)가 실제로
# 그랬다. CI 를 3.10 으로 못 돌리는 동안은 3.11+ 에서 생긴 이름의 임포트를 AST 로 잡는다.
# 문법(match 등)은 3.10 에 이미 있으므로 여기서는 **이름**만 본다.
# ─────────────────────────────────────────────────────────────

import ast

#: {모듈: 3.11+ 에서 생긴 이름}. 모듈 자체가 3.11+ 인 것은 이름 "*" 로 적는다.
_PY311_ONLY = {
    "datetime": {"UTC"},
    "enum": {"StrEnum", "ReprEnum", "verify", "member", "nonmember", "property"},
    "typing": {"Self", "LiteralString", "Never", "assert_type", "assert_never",
               "reveal_type", "dataclass_transform", "override"},  # override 는 3.12
    "asyncio": {"TaskGroup", "timeout", "Runner"},
    "contextlib": {"chdir"},
    "itertools": {"batched"},  # 3.12
    "tomllib": {"*"},
    "wsgiref.types": {"*"},
}

_offenders: list[str] = []
for _py in sorted(Path(".").rglob("*.py")):
    if ".venv" in _py.parts or "site-packages" in _py.parts:
        continue
    tree = ast.parse(_py.read_text(encoding="utf-8"), filename=str(_py))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _PY311_ONLY:
            banned = _PY311_ONLY[node.module]
            hits = [a.name for a in node.names if "*" in banned or a.name in banned]
            _offenders += [f"{_py}:{node.lineno} from {node.module} import {n}" for n in hits]
        elif isinstance(node, ast.Import):
            _offenders += [f"{_py}:{node.lineno} import {a.name}"
                           for a in node.names
                           if "*" in _PY311_ONLY.get(a.name.split(".")[0], ())]
check(not _offenders, "3.11+ 전용 이름을 임포트하지 않는다 (로컬 3.10 호환)",
      "; ".join(_offenders))
