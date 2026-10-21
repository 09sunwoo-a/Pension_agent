"""경로·데이터 위치의 단일 출처. `Path(__file__)` 되짚기 금지 — 경로는 여기서 이름으로 가져다 씀.

도메인 임계값은 미포함(판단하는 모듈 소유). LLM 설정은 llm.py 가 환경변수로 직접 읽음.

    from pension_agent import config
    config.KB_DATA_DIR            # 공용 지식 카드
    config.DATA_ROOTS             # Store 가 훑는 루트 전체
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # src/pension_agent
SRC_ROOT = PACKAGE_ROOT.parent                      # src
REPO_ROOT = SRC_ROOT.parent                         # 저장소 최상위(지식베이스 원문 폴더)

DOTENV = SRC_ROOT / ".env"                          # 설정 파일 (env.py)

KB_DATA_DIR = PACKAGE_ROOT / "knowledge" / "data"           # 공용 지식 카드 (kb_build 산출물)
STRATEGY_DATA_DIR = PACKAGE_ROOT / "tools" / "customer_solution" / "data"  # 상품·전략 카탈로그

# 더미 고객 원장 (scripts/import_customers.py 산출물)
# DATA_ROOTS 밖에 둠 — records[] 파일은 knowledge.Store 가 지식 카드로 적재
CUSTOMERS_JSON = PACKAGE_ROOT / "tools" / "customer_solution" / "customers.json"

# 타겟 룰베이스 (scripts/import_targets.py 산출물). 같은 이유로 DATA_ROOTS 밖
TARGETS_JSON = PACKAGE_ROOT / "tools" / "customer_solution" / "targets.json"

#: Store 가 훑는 루트. 앞의 것부터 적재.
DATA_ROOTS: list[Path] = [STRATEGY_DATA_DIR, KB_DATA_DIR]

# 상담 세션 기록 위치 (session_store.py)
SESSION_DATA_DIR = SRC_ROOT / "runtime" / "session_data"

# 메인 브리핑 고정 뷰 SQL (백엔드별 하위 폴더: sqlite · sybase)
BRANCH_SQL_DIR = PACKAGE_ROOT / "tools" / "branch_briefing" / "sql"
