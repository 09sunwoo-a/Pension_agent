# client — 대화형 에이전트 호출 클라이언트와 API 계약

이 디렉토리는 배포된 대화형 에이전트를 행내 GenAI 플랫폼 게이트웨이를 통해 호출하는 코드와,
그 호출의 요청·응답 형식을 정리한 문서다. 에이전트 본체(`src/`)와는 별개이고 `pension_agent`
를 임포트하지 않는다.

| 파일 | 역할 |
|---|---|
| `README.md` | 요청 형식 · 응답 이벤트 · 파싱 규칙 · 멀티턴 규칙. 프론트를 만들 때 이 문서를 기준으로 한다 |
| `call_agent.py` | 참조 구현. 요청을 만들고(`_payload`), 응답에서 이벤트를 꺼내고(`_events_in`), 종류별로 출력한다(`_render`). 프론트는 이 세 함수가 하는 일을 그대로 옮기면 된다 |
| `requirements.txt` | `requests` 하나 |

응답 형식의 원천은 `src/main.py` 머리말 «출력 형식»이다. 이벤트를 바꾸면 이 문서를 함께 고친다.
`src/tests/test_api.py` 가 코드가 내보내는 이벤트 type 이 전부 이 문서에 있는지 검사한다.

## 0. 실행

```bash
pip install -r client/requirements.txt
# call_agent.py 상단의 ENDPOINT_URL · OPENAPI_TOKEN · GENERATIVE_AI_CLIENT · ASSET_ID 를 채운다.
# CUSTOMER_ID 에 열려 있는 고객 id, SESSION_ID 는 비워 두면 실행마다 새로 만든다.
python client/call_agent.py                 # 대화형 — 질문을 여러 번, 한 세션으로
python client/call_agent.py "IRP 세액공제"   # 한 턴만
```

토큰과 URL 은 콘솔에서 받는다. 에이전트를 재배포하면 URL 의 API 식별자가 바뀌고 토큰은 만료되므로
그때마다 다시 확인한다. 값을 채운 파일은 커밋하지 않는다.

## 1. 요청

```
POST {ENDPOINT_URL}/openapi/agent-chat/v1/agent-messages
x-openapi-token: Bearer <토큰>
x-generative-ai-client: <클라이언트 ID>
Content-Type: application/json
```

```json
{
  "agentId": "<assetId>",
  "contents": ["{\"message\": \"질문\", \"x_client_user\": \"사번\", \"customer_id\": \"고객id\", \"session_id\": \"세션id\"}"],
  "llmConfig": {},
  "isStream": true
}
```

`contents[0]` 은 아래 객체를 **JSON 문자열로** 직렬화한 것이다. 게이트웨이가 그대로 에이전트의
`input_value` 로 넘긴다.

| 키 | 필수 | 뜻 |
|---|---|---|
| `message` | 예 | 직원이 입력한 질문 |
| `x_client_user` | 예 | 호출한 직원 식별자(사번). 감사 기록이자 LLM 쿼터 버킷 |
| `customer_id` | 아니오 | **지금 열려 있는 브리핑 화면의 고객 id.** 고객 관련 질문(현황·왜 타겟인가·제안·지난 상담)은 이것이 있어야 답한다. 없으면 에이전트가 «고객 화면을 먼저 열어달라»고 답한다. 지식 질의응답·화법은 없이도 답한다 |
| `session_id` | 아니오 | 상담 세션 구분자. **상담 한 번마다 새로 만들고**(UUID 등) 같은 상담의 턴들은 같은 값을 보낸다. 에이전트가 이 값으로 이전 턴의 맥락을 이어간다 — 후속 질문·되묻기의 답·「네」가 이것으로 해석된다. 없으면 `"default"` |

- 고객을 질문 문장에서 알아내는 기능은 없다. 어느 고객인지는 **호출자가 정한다.**
- 이전 실행(다른 `session_id`)의 대화는 고객 화면이 열려 있었다면 «지난 상담»으로 기록돼 있고, 에이전트가 필요할 때 찾아 읽는다. 호출자가 들고 다닐 것은 없다.
- `isStream: true` 를 쓴다. `false` 는 게이트웨이가 응답 본문 전체를 JSON 하나로 읽는 경로인데, 에이전트가 그 경우를 구분할 신호가 요청에 없다(2026-09-09 실측).

## 2. 응답 — SSE, 이벤트마다 JSON 하나

`text/event-stream`. `data: {...}` 줄마다 게이트웨이 객체가 하나 오고, 그 안의 `content` 문자열이
**에이전트 이벤트 JSON** 이다. `type` 으로 구분한다. 한 턴의 순서:

| type | 필드 | 화면 |
|---|---|---|
| `progress` | `text` | 답변을 기다리는 동안의 상태 문구. 여러 번 온다. 답변보다 먼저 |
| `answer` | `text`, `intent` | 답변 본문. 한 번. 연계 제안 턴이면 마지막 문장이 «… 연계해드릴까요? (네 / 아니오)» 다 |
| `action` | `kind`, `label`, `prompt` (+ 쪽지면 `title`, `text`, `to`) | 연계 제안 턴에만, `answer` 다음. 본문 아래 **네 / 아니오 버튼**을 그린다. 누르면 다음 턴 `message` 로 `"네"` 또는 `"아니오"` 를 보낸다 |
| `clarify` | `question`, `options[]` | 되묻기 턴에만, `answer` 다음. 선택지 버튼. 누른 값을 다음 턴 `message` 로 보낸다 |
| `sources` | `items[]` — `id`, `doc`, `title`, `url`, `score`, `page`, `role` | 근거. **항상** 온다. `role` 은 `"근거"` 와 `"주의"`(이 고객 상담에서 지켜야 할 것) 두 종류라 두 블록으로 나눠 그린다. `score` 는 있을 때만 관련도로 표기. `items` 가 비면 «근거 없음»을 **표시한다**(빼지 않는다) |
| `followups` | `items[]` | 추천질문. 항상 온다(없으면 빈 목록). 누르면 그 문장을 다음 턴 `message` 로 보낸다 |
| `error` | `text` | 실패. `answer` 대신 온다. 이 뒤에 `done` |
| `done` | 없음 | 턴 끝. 항상 마지막. 스피너를 끈다 |

`answer.text` 에 추천질문 블록은 없다(`followups` 로만 온다). 답변 본문은 검증을 거친 뒤 한 번에
오고 토큰 단위로 흐르지 않는다 — 진행 문구가 그 시간을 채운다.

### 실제 출력 예

```
{"type": "progress", "text": "질문 내용을 파악하고 있어요"}
{"type": "progress", "text": "고객 브리핑 자료를 찾고 있어요"}
{"type": "answer", "text": "이준호 고객님은 만기 예금을 보유하고 있어 자산 재배분이 필요한 시점이기 때문에 타겟이에요.\n\n구체적으로는 …", "intent": "situation"}
{"type": "sources", "items": [
  {"id": "customer.198734-1205842", "doc": "고객 정보 — 계좌 원장 조회값", "title": "이준호 고객 계좌 현황", "url": null, "score": null, "page": null, "role": "근거"},
  {"id": "pitch.k03.024", "doc": "연금사업부(상품) 오늘의할일 스크립트", "title": "만기 임박 + 디폴트옵션 미등록 고객에게 …", "url": null, "score": 2.0, "page": null, "role": "근거"}
]}
{"type": "followups", "items": ["이 고객한테 안내할 수 있는 상품 범위는 뭐야?", "이 고객과 지난 상담에서 무슨 얘기 했지?"]}
{"type": "done"}
```

## 3. 파싱 규칙

1. `data:` 로 시작하는 줄만 읽는다. `data: [DONE]` 이면 끝.
2. 줄의 JSON 에서 `content` 를 꺼낸다. 같은 객체의 `status` 가 `"SUCCESS"` 가 아니면 `content` 는
   게이트웨이 오류 문구다(`responseCode` 함께 표시).
3. `content` 를 JSON 으로 파싱해 `type` 을 본다. **하나만 파싱하고 끝내지 말 것** — `content` 하나에
   객체가 연달아 붙어 올 수 있다(게이트웨이가 합쳐 보내지 않는다는 확인이 없다). `call_agent.py` 의
   `_events_in` 처럼 `raw_decode` 를 반복한다.
4. `type` 이 없는 객체는 무시한다. `content` 에서 이벤트를 하나도 못 찾으면 원문을 그대로 보여준다
   (무엇이 왔는지 보이게).

`_events_in` 은 게이트웨이 오류 문구 안에 에이전트 CHUNK 원문이 통째로 실려 오는 경우
(2026-09-09 실측 — `[Errno Extra data] {"event": "CHUNK", "content": …}`)도 풀어 읽는다.

## 4. 멀티턴 — 프론트가 지킬 것

- 상담을 열 때 `session_id` 를 만들고, 그 상담의 모든 턴에 같은 값을 보낸다.
- `action` 의 네/아니오, `clarify` 의 선택지, `followups` 의 문장은 전부 **다음 턴의 `message`** 로
  보낸다. 별도 API 가 없다. 에이전트가 직전 턴의 맥락으로 그 답을 해석한다.
- 고객 화면을 바꾸면 `customer_id` 를 바꾸고 `session_id` 도 새로 만든다.

## 5. 확인·진단

- 에이전트를 직접 띄운 상태(`src/run_local.sh`)에서 `RAW=1 src/test_local.sh "질문"` 을 실행하면
  게이트웨이 없이 위 이벤트 JSON 이 한 줄씩 그대로 찍힌다. `RAW=2` 면 SSE 줄 자체가 나온다.
- 게이트웨이를 거친 결과는 `call_agent.py` 의 `ask()` 가 종류별로 모은 dict 로 돌려준다.
  `raw` 에 무엇인가 들어 있으면 게이트웨이가 이벤트가 아닌 텍스트를 보낸 것이다.
- 에이전트 쪽 로그(Grafana)는 요청마다 8자리 id 로 «요청 → 진행 → 완료» 가 묶여 찍힌다.
  `맥락=N턴(store)` 이 두 번째 턴부터 보이면 세션이 이어지고 있는 것이다.
