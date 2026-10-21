"""Python 3.10 호환 — 3.11+ 전용 이름 임포트 금지.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from pathlib import Path

from tests.infra._common import check


# 로컬 3.10 / 개발 3.11+ — 3.11+ 신규 이름 임포트를 AST 로 차단(문법은 미검사)

import ast

#: {모듈: 3.11+ 신규 이름}. 모듈 자체가 3.11+ 면 "*"
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
