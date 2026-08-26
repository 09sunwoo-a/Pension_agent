"""정의 검증 CLI — python -m pension_agent.strategy_agent.engine"""

from __future__ import annotations

import sys

from pension_agent.strategy_agent.engine.catalog import (
    BASELINES,
    CAPS,
    PRODUCTS,
    SPECS,
    SYSTEM_STRATEGIES,
)
from pension_agent.strategy_agent.customer import PRIO
from pension_agent.strategy_agent.engine.validate import validate

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

print(f"플레이북 전략 {len(SPECS)}개 · 시스템 조건부 전략 {len(SYSTEM_STRATEGIES)}개 "
      f"· 상품 {len(PRODUCTS)}행 · 요건 {len(PRIO)}종 "
      f"· 기준선 {len(BASELINES)}건 · 기능 {len(CAPS)}건")
errors, warns = validate()
print("✅ ERROR 없음" if not errors else f"❌ ERROR {len(errors)}건")
for e in errors:
    print("   " + e)
print(f"⚠️  WARN {len(warns)}건")
for w in warns:
    print("   " + w)
raise SystemExit(1 if errors else 0)
