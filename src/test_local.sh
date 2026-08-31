#!/bin/bash
# 수동 확인 — /health 로 설정을 보고, /chat 으로 한 턴 돌린다.
#     ./test_local.sh "IRP 수수료 부담된다고 하시는데 뭐라고 답하죠?"
set -uo pipefail
cd "$(dirname "$0")"

BASE_URL="${BASE_URL:-http://localhost:8000}"
PYTHON="${PYTHON:-$HOME/miniconda3/envs/pension_agent/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

MESSAGE="${1:-IRP 수수료가 부담된다고 하시는데 뭐라고 답하면 좋을까요?}"
CLIENT_USER="${CLIENT_USER:-test-user}"

echo "[health]"
curl -s "$BASE_URL/health" | "$PYTHON" -m json.tool
echo ""

echo "[chat] $MESSAGE"
INPUT_VALUE=$("$PYTHON" -c '
import json, sys
print(json.dumps({"message": sys.argv[1], "x_client_user": sys.argv[2]}, ensure_ascii=False))
' "$MESSAGE" "$CLIENT_USER")

curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "$("$PYTHON" -c '
import json, sys
print(json.dumps({"input_value": sys.argv[1], "message_hists": None}, ensure_ascii=False))
' "$INPUT_VALUE")" \
  --no-buffer | "$PYTHON" -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get('event') == 'CHUNK':
            print(d.get('content', ''), end='', flush=True)
    except Exception:
        pass
print()
"
