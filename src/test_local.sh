#!/bin/bash
# 수동 확인 — /health 로 설정을 보고, /chat 으로 한 턴 돌린다.
#     ./test_local.sh "IRP 수수료 부담된다고 하시는데 뭐라고 답하죠?"
#     RAW=1 ./test_local.sh "…"     # 그리지 않고 이벤트 JSON 을 한 줄씩 그대로 — 프론트가 받는 원문
#     RAW=2 ./test_local.sh "…"     # 게이트웨이 없이 에이전트가 내보내는 SSE 줄 그대로(data: …)
#
# 게이트웨이가 보낸 것을 흉내 내기 — INPUT_VALUE_RAW 를 주면 JSON 으로 싸지 않고 그 문자열을
# input_value 에 그대로 싣는다. 행내 실측(2026-09-10)에서 같은 세션 4턴째에 게이트웨이가
# JSON 이 아닌 input_value 를 넘겨 422 가 났는데, 그때 무엇이 왔는지는 서버 로그의
# «거부된 요청 모양» 줄로 본다(main.py). 후보 둘을 로컬에서 그대로 만들 수 있다:
#     INPUT_VALUE_RAW="" ./test_local.sh                       # 빈 문자열
#     INPUT_VALUE_RAW="IRP 세액공제 얼마지" ./test_local.sh     # 질문 평문
# 같은 4턴 대화를 에이전트에 직접 넣어 422 가 나지 않으면 문제는 게이트웨이 쪽이다
# (LLM 키가 없어도 된다 — input_value 검사는 LLM 호출 전이라 200 + error 이벤트로 끝난다):
#     export CUSTOMER_ID=198734-1205842 SESSION_ID=repro-$$
#     ./test_local.sh "이 고객 왜 관리 대상이야?"
#     ./test_local.sh "이 고객한테 지금 안내할 이벤트가 있어?"
#     ./test_local.sh "IRP 세액공제 얼마지"
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
if [ -n "${INPUT_VALUE_RAW+x}" ]; then
  # 게이트웨이가 넘긴 input_value 를 그대로 흉내 낸다(머리말). 비어 있어도 그대로 보낸다.
  INPUT_VALUE="$INPUT_VALUE_RAW"
  echo "[input_value 원문 그대로] $INPUT_VALUE"
else
INPUT_VALUE=$("$PYTHON" -c '
import json, sys
payload = {"message": sys.argv[1], "x_client_user": sys.argv[2], "session_id": sys.argv[4] or "default"}
if sys.argv[3]:
    payload["customer_id"] = sys.argv[3]
print(json.dumps(payload, ensure_ascii=False))
' "$MESSAGE" "$CLIENT_USER" "${CUSTOMER_ID:-}" "${SESSION_ID:-}")
fi

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

# -w 로 상태코드를 마지막 줄에 붙인다 — 422 면 본문이 {"detail": …} 하나라 파서가 조용히
# 버리고 아무것도 안 보이던 것을, 상태코드와 본문으로 찍는다.
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  --no-buffer -w '\n__HTTP_STATUS__ %{http_code}\n' | RAW="${RAW:-}" "$PYTHON" -c "
import sys, json, os
raw = os.environ.get('RAW') == '1'
body = []
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
    if line.startswith('__HTTP_STATUS__'):
        status = line.split()[-1]
        if status != '200':
            print(f'[HTTP {status}] ' + ' '.join(body), file=sys.stderr, flush=True)
        continue
    body.append(line)
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
