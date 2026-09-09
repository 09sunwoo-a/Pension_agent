#!/bin/bash
# 수동 확인 — /health 로 설정을 보고, /chat 으로 한 턴 돌린다.
#     ./test_local.sh "IRP 수수료 부담된다고 하시는데 뭐라고 답하죠?"
#     RAW=1 ./test_local.sh "…"     # 그리지 않고 이벤트 JSON 을 한 줄씩 그대로 — 프론트가 받는 원문
#     RAW=2 ./test_local.sh "…"     # 게이트웨이 없이 에이전트가 내보내는 SSE 줄 그대로(data: …)
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
# 고객 화면이 열린 상태를 흉내 내려면 CUSTOMER_ID=198734-1205842 로 넘긴다.
# 같은 상담을 이어가려면 SESSION_ID 를 같은 값으로 넘긴다(에이전트가 그 값으로 맥락을 되찾는다).
INPUT_VALUE=$("$PYTHON" -c '
import json, sys
payload = {"message": sys.argv[1], "x_client_user": sys.argv[2], "session_id": sys.argv[4] or "default"}
if sys.argv[3]:
    payload["customer_id"] = sys.argv[3]
print(json.dumps(payload, ensure_ascii=False))
' "$MESSAGE" "$CLIENT_USER" "${CUSTOMER_ID:-}" "${SESSION_ID:-}")

# 응답은 CHUNK 마다 content 에 JSON 이벤트 하나다(main.py 머리말 «출력 형식»). type 별로 그린다 —
# 프론트가 할 일과 같다. SSE 프레임(data: {...})이 기본이고 CHAT_SSE_FRAMING=0 이면 JSON 줄이다.
BODY=$("$PYTHON" -c '
import json, sys
print(json.dumps({"input_value": sys.argv[1], "message_hists": None}, ensure_ascii=False))
' "$INPUT_VALUE")

if [ "${RAW:-}" = "2" ]; then
  curl -s -X POST "$BASE_URL/chat" -H "Content-Type: application/json" -d "$BODY" --no-buffer
  exit 0
fi

curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  --no-buffer | RAW="${RAW:-}" "$PYTHON" -c "
import sys, json, os
raw = os.environ.get('RAW') == '1'
dec = json.JSONDecoder()
def events_in(text):
    i = 0
    while True:
        i = text.find('{', i)
        if i < 0:
            return
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            yield obj
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line.startswith('data:'):
        line = line[len('data:'):].strip()
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get('event') != 'CHUNK':
        continue
    for ev in events_in(d.get('content') or ''):
        if raw:
            print(json.dumps(ev, ensure_ascii=False), flush=True)
            continue
        t = ev.get('type')
        if t == 'progress':
            print(f'  ⋯ {ev.get(\"text\")}', file=sys.stderr, flush=True)
        elif t == 'answer':
            print(ev.get('text', ''), flush=True)
        elif t == 'action':
            print(f'  [연계 제안 · {ev.get(\"label\")}] — 다음 턴에 «네» 또는 «아니오»로 답한다', flush=True)
        elif t == 'clarify':
            print('  [되묻기 선택지] ' + ' / '.join(ev.get('options') or []), flush=True)
        elif t == 'sources':
            items = ev.get('items') or []
            ground = [s for s in items if s.get('role', '근거') == '근거']
            caution = [s for s in items if s.get('role') == '주의']
            print('\n─ 근거' + ('' if ground else ': 없음'))
            for s in ground:
                score = f' · 관련도 {s[\"score\"]}' if s.get('score') is not None else ''
                print(f'  · {s.get(\"doc\") or \"\"} — {s.get(\"title\") or \"\"} [{s.get(\"id\")}{score}]')
            if caution:
                print('\n─ 이 고객 상담에서 지켜야 할 것 (근거 카드)')
                for s in caution:
                    print(f'  · {s.get(\"doc\") or \"\"} — {s.get(\"title\") or \"\"} [{s.get(\"id\")}]')
        elif t == 'followups':
            if ev.get('items'):
                print('\n── 이어서 물어보실 수 있어요')
                for q in ev['items']:
                    print(f'  · {q}')
        elif t == 'error':
            print(f'[오류] {ev.get(\"text\")}', file=sys.stderr, flush=True)
        elif t == 'done':
            print()
"
