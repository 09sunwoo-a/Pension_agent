#!/bin/bash
# 수동 확인 — /health 로 설정을 보고, /chat 으로 한 턴 돌린다.
#     ./test_local.sh "IRP 수수료 부담된다고 하시는데 뭐라고 답하죠?"
set -uo pipefail
cd "$(dirname "$0")"

BASE_URL="${BASE_URL:-http://localhost:8000}"
# 지금 활성화된 파이썬을 쓴다. 행내 컨테이너에는 conda 가 없다 —
# 다른 인터프리터를 쓰려면 PYTHON=/경로/python 으로 넘긴다.
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"

MESSAGE="${1:-IRP 수수료가 부담된다고 하시는데 뭐라고 답하면 좋을까요?}"
CLIENT_USER="${CLIENT_USER:-test-user}"

echo "[health]"
curl -s "$BASE_URL/health" | "$PYTHON" -m json.tool
echo ""

echo "[chat] $MESSAGE"
# stream_progress 를 켠다 — 사람이 터미널에서 보는 테스트라, 기다리는 동안 «지금 무엇을
# 하고 있는지»가 보여야 한다. 플랫폼 UI 가 부르는 기본 호출에서는 꺼진다(main.py 주석).
# 고객 화면이 열린 상태를 흉내 내려면 CUSTOMER_ID=198734-1205842 로 넘긴다.
INPUT_VALUE=$("$PYTHON" -c '
import json, sys
payload = {"message": sys.argv[1], "x_client_user": sys.argv[2], "stream_progress": True}
if sys.argv[3]:
    payload["customer_id"] = sys.argv[3]
print(json.dumps(payload, ensure_ascii=False))
' "$MESSAGE" "$CLIENT_USER" "${CUSTOMER_ID:-}")

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
