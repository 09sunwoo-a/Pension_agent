#!/bin/bash
# 로컬 실행 — uvicorn. src/ 에서 돈다(패키지 임포트가 절대경로라 여기가 실행 루트다).
set -euo pipefail
cd "$(dirname "$0")"

# 지금 활성화된 파이썬을 쓴다. 행내 컨테이너에는 conda 가 없다 —
# 다른 인터프리터를 쓰려면 PYTHON=/경로/python 으로 넘긴다.
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"

exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
