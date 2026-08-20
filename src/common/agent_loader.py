"""strategy_agent·consult_agent 모듈을 한 프로세스에서 함께 안전하게 임포트하는 헬퍼.

두 에이전트 디렉터리는 패키지가 아니라 평평한 스크립트 디렉터리라, 둘 다 `prompts`·`llm`
같은 동일 이름의 모듈을 갖고 있다. 지금까지는 각자 단독 실행(`python engine.py` 등)만
가정했기 때문에 문제가 없었다 — 실행 시점에 그 디렉터리 하나만 `sys.path`에 있으므로 이름이
겹칠 일이 없다. 그런데 대화형 라우터(consult_agent)가 브리핑 조회를 위해 strategy_agent의
함수를 같은 프로세스에서 호출하려면, 두 에이전트의 동명 모듈이 `sys.modules`를 놓고 경합한다
(먼저 임포트되는 쪽이 "prompts"/"llm" 자리를 차지하고, 나중 쪽은 조용히 그 모듈을 재사용하게
되어 엉뚱한 프롬프트·LLM 설정을 쓰게 된다).

`load_agent_modules()`는 각 모듈을 `"{agent_dir}.{module_name}"`처럼 완전정규화된 이름으로
`sys.modules`에 적재해 이 경합을 원천적으로 없앤다. 로딩 도중에만 짧은 이름(`prompts` 등)도
같이 걸어 같은 에이전트 안에서 `import prompts` 같은 기존 상대 스타일 임포트가 깨지지 않게
하고, 로딩이 끝나면 짧은 이름을 원상복구해 다음에 로드하는 다른 에이전트를 오염시키지 않는다.

기존 파일은 전혀 수정하지 않는다 — 단독 실행(`python engine.py`, `python graph.py`)은
지금처럼 계속 동작한다. 이 로더는 "두 에이전트를 같은 프로세스에서 함께 쓰는" 새 용도
(대화형 라우터의 briefing_qa 등)를 위한 것이다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SRC_ROOT = Path(__file__).resolve().parent.parent


def load_agent_modules(agent_dir: str, module_names: list[str]) -> dict[str, ModuleType]:
    """agent_dir(예: "strategy_agent") 안의 module_names 를 적재해 {이름: 모듈} 을 반환한다.

    module_names 는 의존관계상 먼저 와야 하는 것부터 순서대로 나열한다(예: 잎 모듈인
    customer·prompts·llm 을 먼저, 그것들을 쓰는 engine·agent 를 나중에). 이미 이 에이전트로
    적재된 모듈은 재사용한다(멱등) — 매 호출마다 다시 실행하지 않는다.
    """
    agent_path = str(SRC_ROOT / agent_dir)
    saved_short: dict[str, ModuleType | None] = {}
    loaded: dict[str, ModuleType] = {}

    sys.path.insert(0, agent_path)
    try:
        for name in module_names:
            full_name = f"{agent_dir}.{name}"
            if full_name in sys.modules:
                loaded[name] = sys.modules[full_name]
                sys.modules[name] = loaded[name]
                continue

            file_path = SRC_ROOT / agent_dir / f"{name}.py"
            spec = importlib.util.spec_from_file_location(full_name, file_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"모듈을 찾을 수 없음: {file_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[full_name] = module

            # 같은 에이전트 내부의 `import {name}` 이 이 모듈을 찾게, 실행 전에 짧은 이름도 건다.
            if name not in saved_short:
                saved_short[name] = sys.modules.get(name)
            sys.modules[name] = module

            spec.loader.exec_module(module)
            loaded[name] = module
    finally:
        sys.path.remove(agent_path)
        for name, prev in saved_short.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev
    return loaded


def load_strategy_agent() -> dict[str, ModuleType]:
    """strategy_agent 의 공개 진입점(engine·customer·agent)을 완전정규화 이름으로 적재한다."""
    return load_agent_modules(
        "strategy_agent", ["customer", "llm", "prompts", "engine", "agent"]
    )


def load_consult_agent() -> dict[str, ModuleType]:
    """consult_agent 의 화법 검색 KB(kb)를 완전정규화 이름으로 적재한다."""
    return load_agent_modules("consult_agent", ["llm", "prompts", "kb"])
