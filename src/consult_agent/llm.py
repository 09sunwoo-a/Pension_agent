"""LLM 클라이언트 — 공용 모듈(common.llm)로 위임하는 얇은 shim.

구현·모델·프로바이더 전환·.env 로딩은 전부 common/llm.py 한 곳에 있다.
이 파일은 (1) umbrella 를 import 경로에 올리고 (2) 공용 함수를 재노출만 한다.
그래서 각 노드 파일의 `from llm import generate` 는 그대로 두어도 공용 구현을 쓴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.llm import agenerate, available, generate  # noqa: E402,F401
