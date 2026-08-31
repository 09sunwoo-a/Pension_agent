#!/bin/bash
# 로컬 실행 — uvicorn. src/ 에서 돈다(패키지 임포트가 절대경로라 여기가 실행 루트다).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-$HOME/miniconda3/envs/pension_agent/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
