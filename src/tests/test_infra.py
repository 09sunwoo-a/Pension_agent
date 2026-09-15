"""공용 인프라 회귀 테스트 — session_store · tools · 패키지 임포트 경계 · env · llm · 관측 · 쪽지 · MCP.

외부 의존성 없음(표준 라이브러리만). LLM 호출 없음.

검사 본문은 `tests/infra/sNN_*.py` 에 번호 순서대로 있다. 이 파일은 그것들을 **그 순서로**
임포트해 돌리고(임포트가 곧 실행이다 — 구간은 모듈 수준 스크립트다) 결과를 출력한다.
순서가 뜻이 있다 — 앞 구간이 갈아끼운 것을 뒤 구간이 이어받거나 되돌리는 자리가 있어서,
구간을 더할 때는 번호를 뒤에 붙인다.

실행: python -m tests.test_infra   (src/ 에서)
"""

from __future__ import annotations

from tests.infra._common import _results
from tests.infra import (  # noqa: F401 — 임포트 순서가 실행 순서다
    s01_sessions,
    s02_send_gate,
    s03_boundaries,
    s04_env_priority,
    s05_numbers,
    s06_fixtures,
    s07_dates_whole,
    s08_dates_bare,
    s09_today,
    s10_llm_retry,
    s11_observability,
    s12_py310,
    s13_scenarios,
    s14_note_body,
    s15_mcp,
    s16_doc_paths,
)

failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    mark = "✓" if ok else "✗"
    print(f"{mark} {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ 공용 인프라 회귀 테스트 통과")
