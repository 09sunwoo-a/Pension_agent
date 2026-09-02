"""경로·데이터 위치의 단일 출처.

모듈이 자기 위치에서 `Path(__file__).parent...` 로 경로를 되짚으면, 파일이 한 칸만
움직여도 조용히 엉뚱한 곳을 가리킨다(CLAUDE.md 4번 규칙 — 실제로 두 번 겪었다).
경로를 아는 곳은 이 파일 하나이고, 나머지는 여기서 이름으로 가져다 쓴다.

여기 있는 것은 **위치와 설정**뿐이다. 도메인 임계값(위험자산 한도·세액공제율·상위 N)은
각각 그 판단을 하는 모듈이 갖는다 — 지식베이스에서 나온 기준을 코드 한 곳에 몰아두면
어느 근거에서 온 숫자인지 추적이 끊긴다.

    from pension_agent import config
    config.KB_DATA_DIR            # 공용 지식 카드
    config.DATA_ROOTS             # Store 가 훑는 루트 전체

LLM 설정은 환경변수이므로 llm.py 가 직접 읽는다. 그 파일이 찾는 .env 위치만 여기서 준다.
"""

from __future__ import annotations

from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 루트
# ─────────────────────────────────────────────────────────────

PACKAGE_ROOT = Path(__file__).resolve().parent      # src/pension_agent
SRC_ROOT = PACKAGE_ROOT.parent                      # src
REPO_ROOT = SRC_ROOT.parent                         # 저장소 최상위(지식베이스 원문 폴더)

DOTENV = SRC_ROOT / ".env"                          # 두 에이전트가 공유하는 단일 설정 파일

# ─────────────────────────────────────────────────────────────
# 데이터 루트
#
# 두 갈래인 이유: 지식 카드(화법·팩트·절차·세그먼트)는 두 에이전트가 함께 읽는 공용
# 자산이고, 상품·전략 카탈로그는 strategy_agent 만 읽는다. 소유가 다르므로 폴더도 나눈다.
# ─────────────────────────────────────────────────────────────

KB_DATA_DIR = PACKAGE_ROOT / "knowledge" / "data"           # 공용 지식 카드 (kb_build 산출물)
STRATEGY_DATA_DIR = PACKAGE_ROOT / "strategy_agent" / "data"  # 상품·전략 카탈로그

# 시연용 더미 고객 원장 (scripts/import_customers.py 산출물 — 원본은 저장소 루트의 xlsx).
# DATA_ROOTS 밖에 두는 이유: records[] 를 가진 파일은 knowledge.Store 가 지식 카드로
# 훑어버린다. 고객 레코드는 카드가 아니므로 스캔 경로 밖, 소유자(customer.py) 옆에 둔다.
CUSTOMERS_JSON = PACKAGE_ROOT / "strategy_agent" / "customers.json"

# 사후관리 타겟 룰베이스 (scripts/import_targets.py 산출물 — 원본은 저장소 루트의 xlsx).
# 기획자가 행내 원문을 정규화해 확인해준 타겟 14종·조건·액션표다. 요건 임계값의 상위
# 기준이며, customers.json 과 같은 이유로 DATA_ROOTS 밖에 둔다.
TARGETS_JSON = PACKAGE_ROOT / "strategy_agent" / "targets.json"

# 미리 만들어 둔 AI 브리핑 (scripts/build_briefings.py 산출물).
#
# 브리핑 한 건이 LLM 12회이고 로스터가 9명이라, 화면을 켤 때마다 108회를 순차로 도는 것이
# 시작 지연의 거의 전부였다. 미리 만들어 커밋해 두고 캐시를 채우는 자리다.
# **캐시를 «채울» 뿐 대체하지 않는다** — 키가 Profile 전체의 지문이라 고객 원장이나
# «오늘»이 바뀌면 그냥 미스가 나고 평소처럼 실시간 생성으로 간다(agent.load_prebuilt).
# customers.json 과 같은 이유로 DATA_ROOTS 밖에 둔다.
BRIEFINGS_JSON = PACKAGE_ROOT / "strategy_agent" / "briefings.json"

#: Store 가 훑는 루트. 앞의 것이 먼저 적재된다.
DATA_ROOTS: list[Path] = [STRATEGY_DATA_DIR, KB_DATA_DIR]

# 상담 세션 기록 위치 (session_store.py). 데모는 파일, 실서비스는 DB 로 바뀔 자리다.
SESSION_DATA_DIR = SRC_ROOT / "session_data"
