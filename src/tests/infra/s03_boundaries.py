"""패키지 임포트 경계 — 층 사이의 의존이 한 방향이다

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from pathlib import Path
import sys

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# 짧은 이름 오염 · sys.path 조작 금지
#
# 패키지는 `pension_agent` 하나이고 임포트는 전부 절대 경로다. 짧은 이름(`prompts`·`llm`)이
# sys.modules 에 등장하면 누군가 sys.path 를 조작했다는 뜻이다.
# ─────────────────────────────────────────────────────────────

import pension_agent  # noqa: E402
from pension_agent import prompts as _prompts  # noqa: E402,F401

check(not {"prompts", "llm", "customer", "engine", "kb"} & set(sys.modules),
      "짧은 이름이 sys.modules 를 오염시키지 않는다",
      str(sorted({"prompts", "llm", "customer", "engine", "kb"} & set(sys.modules))))

check(not any("sys.path" in (f.read_text(encoding="utf-8"))
              for f in Path(pension_agent.__file__).parent.rglob("*.py")),
      "패키지 안에 sys.path 조작이 남아 있지 않다")

_PKG = Path(pension_agent.__file__).parent

# 본문이 같은 함수가 서로 다른 모듈에 있지 않다. 이름이 아니라 본문(독스트링 제외)을 비교한다 —
# 이름이 같아도 논리가 다른 함수(_norm 셋)는 중복이 아니고, 이름이 달라도 본문이 같으면
# 중복이다(_parse·_json_obj 가 그랬다 — 2026-09-15 에 llm.json_object 로 합쳤다). 한쪽만
# 고쳐지는 것이 이 중복의 실제 비용이라, 다시 생기면 여기서 잡는다. 사소한 한 줄짜리는
# 우연히 같을 수 있어 본문 길이 하한을 둔다.
import ast as _ast  # noqa: E402
from collections import defaultdict as _defaultdict  # noqa: E402

def _body_key(fn) -> str | None:
    body = fn.body
    if body and isinstance(body[0], _ast.Expr) and isinstance(getattr(body[0], "value", None), _ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]                       # 독스트링은 본문이 아니다
    if len(body) == 1 and isinstance(body[0], _ast.Pass):
        return None
    key = _ast.dump(_ast.Module(body=body, type_ignores=[]), annotate_fields=False)
    return key if len(key) >= 60 else None

_same_body: dict[str, list[str]] = _defaultdict(list)
for _py in sorted((*_PKG.rglob("*.py"), *Path("scripts").rglob("*.py"))):
    for _node in _ast.walk(_ast.parse(_py.read_text(encoding="utf-8"))):
        if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            _k = _body_key(_node)
            if _k:
                _same_body[_k].append(f"{_py}:{_node.lineno} {_node.name}")
_dupes = [v for v in _same_body.values() if len({x.split(":")[0] for x in v}) > 1]
check(not _dupes, "본문이 같은 함수가 서로 다른 모듈에 없다 — 소유자 한 곳으로 합친다",
      "; ".join(" == ".join(v) for v in _dupes))



# ─────────────────────────────────────────────────────────────
# 층 — 폴더(또는 최상단 파일) 하나가 한 단위다. 단위는 아래 표에 적힌 단위만 임포트한다.
#
#   common ← knowledge ← state ← evidence ← tools ← actions ← nodes ← graph
#   (prompts 는 아무것도 임포트하지 않는 문구 모음이고, 위 층 누구나 쓴다)
#
# 함수 안의 지연 임포트도 센다 — 방향이 거꾸로면 지연이어도 거꾸로다. 예외는 표에 적고
# 이유를 한 줄 남긴다. 새 폴더·최상단 파일을 만들면 표에 층을 적어야 이 검사가 통과한다.
# ─────────────────────────────────────────────────────────────

import ast as _ast  # noqa: E402

_BELOW_TOOLS = {"common", "knowledge", "state", "prompts", "evidence"}
_LAYERS: dict[str, set[str]] = {
    "common": {"tools"},          # 예외 하나: common/note.py 가 쪽지 본문의 타겟·잔여일수를
                                  # tools/customer_solution 에서 읽는다(아래 검사가 그 파일로 한정한다)
    "knowledge": {"common"},
    "prompts": set(),
    "state": {"knowledge"},
    "evidence": {"common", "knowledge", "state"},
    "tools": _BELOW_TOOLS,
    "actions": _BELOW_TOOLS | {"tools", "nodes"},   # 쪽지 초안(memo)이 화면 답변과 같은 검사(nodes/plan.screen)를 쓴다
    "nodes": _BELOW_TOOLS | {"tools", "actions", "routing"},
    "routing": {"state"},
    "context_store": {"state"},
    "turn_trace": {"evidence"},
    "graph": _BELOW_TOOLS | {"tools", "actions", "nodes", "routing", "turn_trace"},
    "__main__": {"common", "actions", "graph"},
    "__init__": {"graph"},
}


def _unit_of(f: Path) -> str:
    rel = f.relative_to(_PKG)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _imported_units(tree) -> set[str]:
    out: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom) and node.module and node.module.startswith("pension_agent"):
            if node.module == "pension_agent":
                out |= {a.name for a in node.names}
            else:
                out.add(node.module.split(".")[1])
        elif isinstance(node, _ast.Import):
            out |= {a.name.split(".")[1] for a in node.names if a.name.startswith("pension_agent.")}
    return out


_units = {_unit_of(f) for f in _PKG.rglob("*.py")}
check(_units == set(_LAYERS), "패키지의 단위가 전부 층 표에 있다 (새 폴더·파일은 표에 층을 적는다)",
      str(sorted(_units ^ set(_LAYERS))))

_back: list[str] = []
for _f in sorted(_PKG.rglob("*.py")):
    _u = _unit_of(_f)
    for _v in _imported_units(_ast.parse(_f.read_text(encoding="utf-8"))) - {_u}:
        if _v not in _LAYERS.get(_u, set()):
            _back.append(f"{_f.relative_to(_PKG)} → {_v}")
        elif _u == "common" and _v == "tools" and _f.name != "note.py":
            _back.append(f"{_f.relative_to(_PKG)} → tools (예외는 common/note.py 하나)")
check(not _back, "층 사이 임포트가 표 안에 있다 — 아래 층이 위 층을 임포트하지 않는다", str(_back))

# ─────────────────────────────────────────────────────────────
# common/ 안의 층 — 파일마다 책임이 하나라 폴더로 더 나누지 않는 대신 표로 고정한다
#
# 층이 문서에만 있으면 새 임포트 한 줄이 조용히 무너뜨린다 — env 가 llm 을 모듈 수준에서
# 임포트하는 순간 순환이다. 표는 **모듈 수준** 임포트만 본다(함수 안의 지연 임포트는 그
# 함수의 결정이다). 간선을 더할 때는 여기 표에 적고, 왜 그 방향이어야 하는지 한 줄 남긴다.
# ─────────────────────────────────────────────────────────────

_COMMON = _PKG / "common"
_ALLOWED_EDGES: dict[str, set[str]] = {
    "config": set(), "clock": set(),                       # 단일 출처 — 아무것도 임포트하지 않는다
    "env": {"config"},                                     # .env 위치만 config 에서 받는다
    "observability": {"env"},                              # 키·호스트는 .env 에서 (llm 을 모르면서 관측한다)
    "privacy": set(),                                      # 행내 개인정보 필터 규칙표 — 정규식만 안다
    "llm": {"env", "observability", "privacy"},            # 클라이언트가 관측을 부른다 — 반대는 순환
    "session_store": {"config"},
    "note": {"clock", "tools"},                            # 위 층 예외(유일) — 쪽지 본문의 타겟·잔여일수
    "mcp": {"env", "note"},                                # 어댑터는 note 의 발송 함수에 자기를 등록한다
    "progress": set(),
}

def _unit_files(name: str) -> list[Path]:
    p = _COMMON / f"{name}.py"
    return [p] if p.exists() else sorted((_COMMON / name).rglob("*.py"))     # 하위 패키지까지

def _is_main_guard(node) -> bool:
    """`if __name__ == "__main__":` — 모듈로 임포트될 때는 돌지 않으므로 임포트 시점이 아니다."""
    return (isinstance(node, _ast.If) and isinstance(node.test, _ast.Compare)
            and isinstance(node.test.left, _ast.Name) and node.test.left.id == "__name__")

def _import_time_nodes(tree):
    """임포트 시점에 실행되는 구문만 — 함수·클래스 본문과 __main__ 가드는 빼고, 모듈 수준의
    try/if/with 안은 포함한다(거기 있는 임포트도 적재 때 돈다)."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)) or _is_main_guard(node):
            continue
        yield node
        stack.extend(_ast.iter_child_nodes(node))

def _module_level_edges(name: str) -> set[str]:
    out: set[str] = set()
    for f in _unit_files(name):
        for node in _import_time_nodes(_ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, _ast.ImportFrom) and node.module and node.module.startswith("pension_agent"):
                parts = node.module.split(".")
                if parts[1:2] == ["common"]:
                    out |= {a.name for a in node.names} if len(parts) == 2 else {parts[2]}
                else:
                    out.add(parts[1])
            elif isinstance(node, _ast.Import):
                for a in node.names:
                    parts = a.name.split(".")
                    if parts[0] == "pension_agent" and len(parts) > 2:
                        out.add(parts[2] if parts[1] == "common" else parts[1])
    return out - {name}

_common_units = sorted(
    {p.stem for p in _COMMON.glob("*.py") if p.stem != "__init__"}
    | {p.name for p in _COMMON.iterdir() if p.is_dir() and (p / "__init__.py").exists()})
check(set(_common_units) == set(_ALLOWED_EDGES),
      "common/ 의 모듈·패키지가 의존 표에 전부 있다 (새 모듈은 표에 층을 적는다)",
      str(sorted(set(_common_units) ^ set(_ALLOWED_EDGES))))
_bad_edges = sorted(f"{u} → {e}" for u in _common_units if u in _ALLOWED_EDGES
                    for e in _module_level_edges(u) - _ALLOWED_EDGES[u])
check(not _bad_edges, "common/ 모듈 사이의 모듈 수준 임포트가 의존 표 안에 있다 — 층이 무너지지 않았다",
      str(_bad_edges))

# 지도(pension_agent/__init__.py 머리말)에 최상단 모듈·패키지가 전부 올라 있다 — 지도가 낡으면
# 새 사람이 파일을 찾지 못한다.
_map = (_PKG / "__init__.py").read_text(encoding="utf-8")
_top_entries = sorted({f"{p.stem}.py" for p in _PKG.glob("*.py") if p.stem != "__init__"}
                      | {f"{p.name}/" for p in _PKG.iterdir() if p.is_dir() and (p / "__init__.py").exists()})
_unmapped = [e for e in _top_entries if e not in _map]
check(not _unmapped, "pension_agent/__init__.py 지도에 최상단 모듈·패키지가 전부 있다", str(_unmapped))
