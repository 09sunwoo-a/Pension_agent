"""패키지 임포트 경계 — 두 에이전트를 한 프로세스에서 함께 써도 이름이 겹치지 않는다

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from pathlib import Path
import sys

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# 패키지 임포트 경계 — 두 에이전트를 한 프로세스에서 함께 써도 이름이 겹치지 않는다
#
# 회귀 대상: 예전에는 두 에이전트가 평평한 스크립트 디렉터리라 `prompts`·`llm` 같은 동명
# 모듈이 sys.modules 를 놓고 경합했고(먼저 임포트한 쪽이 자리를 차지), 이를 피하려 전용
# 로더(common/agent_loader.py)가 sys.path 와 짧은 이름을 저장·복원했다. 패키지화로 그
# 경합 자체가 없어졌다 — 이 테스트가 고정하는 것은 "짧은 이름이 sys.modules 에 등장하지
# 않는다"는 사실이다. 다시 등장하면 sys.path 조작이 되살아났다는 뜻이다.
# ─────────────────────────────────────────────────────────────

from pension_agent.consult_agent import prompts as consult_prompts  # noqa: E402
from pension_agent.strategy_agent import prompts as strategy_prompts  # noqa: E402

check(consult_prompts is not strategy_prompts,
      "동명 모듈(prompts)이 에이전트별로 각각 적재된다")
check(consult_prompts.__name__ == "pension_agent.consult_agent.prompts",
      "완전정규화 이름으로 등록된다", consult_prompts.__name__)
check(not {"prompts", "llm", "customer", "engine", "kb"} & set(sys.modules),
      "짧은 이름이 sys.modules 를 오염시키지 않는다",
      str(sorted({"prompts", "llm", "customer", "engine", "kb"} & set(sys.modules))))

import pension_agent  # noqa: E402

check(not any("sys.path" in (f.read_text(encoding="utf-8"))
              for f in Path(pension_agent.__file__).parent.rglob("*.py")),
      "패키지 안에 sys.path 조작이 남아 있지 않다")

# 에이전트 사이의 의존은 한 방향이다 — knowledge ← strategy_agent ← consult_agent (루트 CLAUDE.md
# 「구조 규칙」). 예전에는 strategy_agent.support 가 consult_agent.kb 를 거꾸로 임포트했고, 그
# 간선 하나 때문에 공용 모듈에 순환 회피용 지연 임포트가 늘었다. 공용 카드 지식베이스를
# knowledge/kb.py 로 옮겨 없앤 간선이 다시 생기지 않게 여기서 고정한다.
_PKG = Path(pension_agent.__file__).parent
# consult_agent 를 뺀 **전부**다 — 폴더를 손으로 나열하면 새 패키지(observability/ 가 그랬다)가
# 검사 밖에 남는다. 함수 안의 지연 임포트도 잡는다: 방향이 거꾸로면 지연이어도 거꾸로다.
_ONE_WAY = sorted(f for f in _PKG.rglob("*.py") if "consult_agent" not in f.relative_to(_PKG).parts)
_back_edges = sorted(
    str(f.relative_to(_PKG)) for f in _ONE_WAY
    if any(line.lstrip().startswith(("from pension_agent.consult_agent", "import pension_agent.consult_agent"))
           for line in f.read_text(encoding="utf-8").splitlines()))
check(not _back_edges, "strategy_agent·공용 모듈이 consult_agent 를 임포트하지 않는다", str(_back_edges))

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
# 최상단 공용 모듈의 층 — 폴더로 묶지 않는 대신 표로 고정한다 (pension_agent/__init__.py 지도)
#
# 최상단 모듈·패키지는 각자 책임이 하나라 평평하게 두기로 했다(2026-09-15). 층이 문서에만 있으면
# 새 임포트 한 줄이 조용히 무너뜨린다 — env 가 llm 을 모듈 수준에서 임포트하는 순간 순환이고,
# 그 다음은 «순환 회피용 지연 임포트»가 늘어나는 길이다(env.py 머리말이 그 이유로 갈라졌다).
# 표는 **모듈 수준** 임포트만 본다(함수 안의 지연 임포트는 그 함수의 결정이다). 간선을
# 더할 때는 여기 표에 적고, 왜 그 방향이어야 하는지 한 줄 남긴다.
# ─────────────────────────────────────────────────────────────

_ALLOWED_EDGES: dict[str, set[str]] = {
    "config": set(), "clock": set(),                       # 단일 출처 — 아무것도 임포트하지 않는다
    "env": {"config"},                                     # .env 위치만 config 에서 받는다
    "observability": {"env"},                              # 키·호스트는 .env 에서 (llm 을 모르면서 관측한다)
    "llm": {"env", "observability"},                       # 클라이언트가 관측을 부른다 — 반대는 순환
    "verify": {"clock"},                                   # 연도 없는 날짜를 «오늘 언저리»로 읽는다
    "session_store": {"config"},
    "note": {"clock", "strategy_agent"},                   # 공용 → 에이전트 간선(유일) 쪽지 본문의 타겟·잔여일수
    "mcp": {"env", "note"},                                # 어댑터는 위층(note)의 발송 함수에 자기를 등록한다
    "market": set(),
    "knowledge": {"config", "market"},
}

def _unit_files(name: str) -> list[Path]:
    p = _PKG / f"{name}.py"
    return [p] if p.exists() else sorted((_PKG / name).rglob("*.py"))     # 하위 패키지까지

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
                out |= {a.name for a in node.names} if len(parts) == 1 else {parts[1]}
            elif isinstance(node, _ast.Import):
                out |= {a.name.split(".")[1] for a in node.names if a.name.startswith("pension_agent.")}
    return out - {name}

_shared_units = sorted(
    {p.stem for p in _PKG.glob("*.py") if p.stem != "__init__"}
    | {p.name for p in _PKG.iterdir() if p.is_dir() and (p / "__init__.py").exists()
       and p.name not in ("consult_agent", "strategy_agent")})
check(set(_shared_units) == set(_ALLOWED_EDGES),
      "공용 모듈·패키지가 의존 표에 전부 있다 (새 모듈은 표에 층을 적는다)",
      str(sorted(set(_shared_units) ^ set(_ALLOWED_EDGES))))
_bad_edges = sorted(f"{u} → {e}" for u in _shared_units if u in _ALLOWED_EDGES
                    for e in _module_level_edges(u) - _ALLOWED_EDGES[u])
check(not _bad_edges, "공용 모듈 사이의 모듈 수준 임포트가 의존 표 안에 있다 — 층이 무너지지 않았다",
      str(_bad_edges))

# 지도(pension_agent/__init__.py 머리말)에 최상단 모듈·패키지가 전부 올라 있다 — 지도가 낡으면
# 새 사람이 파일을 찾지 못한다. note.py 가 목록에 빠진 채 며칠 있었다.
_map = (_PKG / "__init__.py").read_text(encoding="utf-8")
_top_entries = sorted({f"{p.stem}.py" for p in _PKG.glob("*.py") if p.stem != "__init__"}
                      | {f"{p.name}/" for p in _PKG.iterdir() if p.is_dir() and (p / "__init__.py").exists()})
_unmapped = [e for e in _top_entries if e not in _map]
check(not _unmapped, "pension_agent/__init__.py 지도에 최상단 모듈·패키지가 전부 있다", str(_unmapped))


# consult_agent 안의 층 — tools/(근거를 찾는다) 는 nodes/(그래프 노드) 아래다. 2026-09-15 까지
# nodes/ 에 노드가 아닌 넷(facts_qa·procedure_qa·segment_qa·pitch 슬롯 분해)이 있어 tools/ 가
# 위 층을 임포트했다. tools/ 로 내렸고, 다시 생기지 않게 여기서 잡는다(지연 임포트도 포함 —
# 방향이 거꾸로면 지연이어도 거꾸로다).
_tools_to_nodes = sorted(
    str(f.relative_to(_PKG)) for f in (_PKG / "consult_agent" / "tools").rglob("*.py")
    if any(line.lstrip().startswith(("from pension_agent.consult_agent.nodes", "import pension_agent.consult_agent.nodes"))
           for line in f.read_text(encoding="utf-8").splitlines()))
check(not _tools_to_nodes, "consult_agent/tools/ 가 nodes/ 를 임포트하지 않는다 — 도구는 노드 아래 층이다",
      str(_tools_to_nodes))


# consult_agent 의 층은 셋이고 방향은 하나다 — evidence/(근거를 찾고·맞추고·검사) ← tools/(LLM 이
# 고르는 도구) ← effects/(답변 뒤·그래프 밖). 2026-09-15 재배치 때 세운 경계다: 그 전에는 최상위에
# 열여섯 파일이 한 층으로 있었고 tools/ 에 도구와 보조가 섞여 있어 «이게 도구인가»를 폴더가
# 답하지 못했다. 방향이 거꾸로면 지연 임포트여도 거꾸로다. (FENCE 가 그 예다 — 떼는 쪽 tools/history
# 가 쓰는 쪽 effects/memo 를 임포트하고 있어서 state.py 로 올렸다.)
_CA = _PKG / "consult_agent"
def _imports_from(folder: str, *forbidden: str) -> list[str]:
    heads = tuple(f"{p}pension_agent.consult_agent.{x}" for x in forbidden for p in ("from ", "import "))
    return sorted(str(f.relative_to(_PKG)) for f in (_CA / folder).rglob("*.py")
                  if any(line.lstrip().startswith(heads) for line in f.read_text(encoding="utf-8").splitlines()))
_bad = _imports_from("evidence", "tools", "effects", "nodes")
check(not _bad, "consult_agent/evidence/ 가 tools/·effects/·nodes/ 를 임포트하지 않는다 — 근거 층이 맨 아래다", str(_bad))
_bad = _imports_from("tools", "effects")
check(not _bad, "consult_agent/tools/ 가 effects/ 를 임포트하지 않는다 — 도구는 효과 아래 층이다", str(_bad))
