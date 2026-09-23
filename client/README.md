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
  "contents": ["{\"message\": \"질문\", \"x_client_user\": \"사번-uuid\", \"customer_id\": \"b64:MTcxMjAzLTQ4MTUwNjI=\", \"session_id\": \"세션id\"}"],
  "llmConfig": {},
  "isStream": true
}
```

`contents[0]` 은 아래 객체를 **JSON 문자열로** 직렬화한 것이다. 게이트웨이가 그대로 에이전트의
`input_value` 로 넘긴다.

| 키 | 필수 | 뜻 |
|---|---|---|
| `message` | 예 | 직원이 입력한 질문. **유효한 UTF-8** 이어야 한다 — 대리 문자(U+DCxx)가 섞이면 게이트웨이가 input_value 를 깨뜨려 500 이 난다(터미널이 한글을 바이트 단위로 지운 입력에서 실측 · `call_agent.py::_check_utf8`) |
| `x_client_user` | 예 | 호출한 직원 식별자. 감사 기록이자 LLM 쿼터 버킷이고, 에이전트는 여기서 **사번을 읽어** 쪽지의 받는 사람·보내는 사람을 정한다. **「사번 7자리」로 시작해야 하고**, 뒤에 접미(LLM 호출을 가르는 uuid 등)를 붙일 거면 **구분자로 잇는다**(`3902172-550e8400-…` · `3902172_…`). 사번 뒤에 숫자가 바로 이어지면 사번으로 읽지 않는다 — 사번이 아닌 숫자 id 를 남의 사번으로 오독하지 않기 위해서다 |
| `customer_id` | 아니오 | **지금 열려 있는 브리핑 화면의 고객 id — `b64:` + base64(원장 표기)로 보낸다**(`171203-4815062` → `b64:MTcxMjAzLTQ4MTUwNjI=`). JavaScript 로는 `"b64:" + btoa(customerId)`. 원장 표기는 주민등록번호와 같은 꼴이라 플랫폼 게이트웨이의 «기본필터»(policy 350)가 그 요청을 `FILTER_INVALID` 로 끊는다(질문이 한 글자여도 막힌다). 에이전트가 원장 표기로 되돌린다. 고객 관련 질문(현황·왜 타겟인가·제안·지난 상담)은 이것이 있어야 답한다. 없으면 에이전트가 «고객 화면을 먼저 열어달라»고 답한다. 지식 질의응답·화법은 없이도 답한다 |
| `session_id` | 아니오 | 상담 세션 구분자. **상담 한 번마다 새로 만들고**(UUID 등) 같은 상담의 턴들은 같은 값을 보낸다. 에이전트가 이 값으로 이전 턴의 맥락을 이어간다 — 후속 질문·되묻기의 답·「네」가 이것으로 해석된다. 없으면 `"default"` |
| `employee_id` | 아니오 | 로그인한 직원의 **WorkB 사번**. `x_client_user` 앞자리가 사번이면 **보낼 필요가 없다** — 사번을 다른 데서 받아오거나 `x_client_user` 의 꼴이 바뀌는 배포를 위한 자리다. 여기 넣은 값은 꼴을 검사하지 않고 그대로 쓴다 |

- 고객을 질문 문장에서 알아내는 기능은 없다. 어느 고객인지는 **호출자가 정한다.**
- 쪽지의 받는 사람은 **기본이 본인**(그 사번)이고, 다른 직원에게 보내는 것은 직원이 질문에
  사번을 적었을 때만이다(«사번 3902173한테 보내줘»). 사번을 하나도 못 읽으면 서버의
  `WORKB_EMP_NO` 로 떨어지고, 그것도 없으면 에이전트가 쪽지 발송을 제안하지 않는다.
- 이전 실행(다른 `session_id`)의 대화는 고객 화면이 열려 있었다면 «지난 상담»으로 기록돼 있고, 에이전트가 필요할 때 찾아 읽는다. 호출자가 들고 다닐 것은 없다.
- `isStream: true` 를 쓴다. `false` 는 게이트웨이가 응답 본문 전체를 JSON 하나로 읽는 경로인데, 에이전트가 그 경우를 구분할 신호가 요청에 없다(2026-09-09 실측).

## 2. 응답 — SSE, 이벤트마다 JSON 하나

`text/event-stream`. `data: {...}` 줄마다 게이트웨이 객체가 하나 오고, 그 안의 `content` 문자열이
**에이전트 이벤트 JSON** 이다. `type` 으로 구분한다. 한 턴의 순서:

| type | 필드 | 화면 |
|---|---|---|
| `progress` | `text` | 답변을 기다리는 동안의 상태 문구. 여러 번 온다. 답변보다 먼저 |
| `answer` | `text`, `intent`, `links[]`, `messages[]` | 답변 본문. 한 번. 연계 제안 턴이면 마지막 문장이 «… 연계해드릴까요? (네 / 아니오)» 다. `links` · `messages` 는 아래 |
| `action` | `kind`, `label`, `prompt` (+ 쪽지면 `title`, `text`, `to`) | 연계 제안 턴에만, `answer` 다음. **답변 본문 바로 아래**에 `prompt` 와 **네 / 아니오 버튼**을 그린다(아래 「제안은 근거가 아니다」). 누르면 다음 턴 `message` 로 `"네"` 또는 `"아니오"` 를 보낸다 |
| (예외) | | 쪽지를 이름으로 보내려는데 같은 이름의 직원이 여러 명이면, 그 턴은 `answer` 에 번호 목록과 「어느 분께 보낼까요? 번호나 부서로 답해 주세요.」만 싣고 **`action` 을 보내지 않는다**(네/아니오로 답할 턴이 아니다). 직원이 입력한 「1번」·「WM」은 다음 턴 `message` 로 그대로 보낸다 |
| `clarify` | `question`, `options[]` | 되묻기 턴에만, `answer` 다음. 선택지 버튼. 누른 값을 다음 턴 `message` 로 보낸다 |
| `sources` | `items[]` — `id`, `doc`, `title`, `url`, `score`, `page`, `role` | 근거. **항상** 온다. `role` 은 `"근거"` 와 `"주의"`(이 고객 상담에서 지켜야 할 것) 두 종류라 두 블록으로 나눠 그린다. `score` 는 있을 때만 관련도로 표기. `items` 가 비면 «근거 없음»을 **표시한다**(빼지 않는다). `id` 는 표시·중복 제거용 라벨이고 **고객 번호를 싣지 않는다** — 고객 재료는 `customer`·`suitable`·`outreach`·`session` 처럼 종류 이름만 온다(고객 번호가 실리면 플랫폼 게이트웨이의 개인정보 필터가 응답을 막는다). 어느 고객인지는 프론트가 보낸 `customer_id` 가 정한다 |
| `followups` | `items[]` | 추천질문. 항상 온다(없으면 빈 목록). 누르면 그 문장을 다음 턴 `message` 로 보낸다 |
| `error` | `text` | 실패. `answer` 대신 온다. 이 뒤에 `done` |
| `done` | 없음 | 턴 끝. 항상 마지막. 스피너를 끈다 |

`answer.text` 에 추천질문 블록은 없다(`followups` 로만 온다). 답변 본문은 검증을 거친 뒤 한 번에
오고 토큰 단위로 흐르지 않는다 — 진행 문구가 그 시간을 채운다.

### `action` — 제안은 근거가 아니다

`prompt` 는 에이전트가 직원에게 **묻는 말**이다. 답변 본문 바로 아래, 버튼과 한 덩어리로
세운다 — **출처(`sources`) 블록 안이나 「근거 N건」 목록 옆에 두지 않는다.** 거기 서면 근거
표시의 일부로 읽혀서, 직원은 자기가 대답해야 하는 질문인 줄 모른 채 지나간다(2026-09-17
시연에서 실제로 그렇게 보였다). 출처 블록은 «이 답이 무엇에 근거했나»만 그린다.

`answer.text` 의 마지막 줄도 같은 문장(`— {prompt}`)이다. 버튼을 따로 그리는 프론트는 본문
끝의 그 줄을 떼고 그 자리에 세운다 — 같은 문장이 두 번 서지 않게 하되, **본문과 버튼 사이에
출처·근거 블록이 끼어들지 않게** 한다.

### `answer.links` — 단말 화면 딥링크

답변 본문이 가리킨 **단말 화면**의 링크다. 항목은 `screen` · `url` · `label` 이고, **없으면 빈
목록으로 항상 온다**(키가 있을 때와 없을 때를 가르지 않아도 된다).

```json
{"type": "answer", "text": "MyStar 단말 [04-12-642] 적립금 및 수익률 조회 화면에서 …", "intent": "procedure",
 "links": [{"screen": "04-12-642", "url": "mystar-link://scnNo=0412642&mode=D", "label": "적립금및수익률조회"}]}
```

- `screen` 은 **본문에 그대로 들어 있는 문자열**이다. 프론트는 본문에서 그것을 찾아 `url` 로
  누를 수 있게 감싼다(대괄호 표기 `[04-12-642]` 안에서도 그대로 찾힌다).
- **URL 을 프론트가 조립하지 않는다.** `mode`(운영 `O` · 스테이징 `S` · 개발 `D`)는 서버 설정이고
  `scnNo` 는 자릿수 판정을 거친 값이라, 프론트가 만들면 운영 전환 때 두 곳이 어긋난다.
- `mystar-link://` 는 **커스텀 스킴**이다. 마크다운 렌더러·HTML sanitizer 의 기본 설정은 이런
  href 를 지우므로 스킴을 허용 목록에 넣어야 한다(`react-markdown` 의 `urlTransform`,
  `DOMPurify` 의 `ALLOWED_URI_REGEXP`). 단말이 없는 PC 에서는 눌러도 열리지 않는다.
- 여기 실리는 번호는 전부 근거 카드에 있는 실재 화면이다 — 답변이 근거 밖 화면을 가리키면
  서버가 그 답변을 폐기한다.

### `answer.messages` — 고객 발송 문구(LMS)

본문의 큰따옴표 인용 중 **고객에게 문자로 보낼 문구**다. 화면은 큰따옴표 인용을 «고객에게
이렇게 말씀해 보세요»(화법) 블록으로 그리는데, 발송 문구는 직원이 **말하는** 것이 아니라 발송
화면에 **붙여 넣는** 것이라 다른 블록으로 그린다. 항목은 `kind` · `label` · `text` · `copy` 이고,
**없으면 빈 목록으로 항상 온다.**

```json
{"type": "answer", "text": "… 고객님께 보낼 발송 문구는 다음과 같습니다.\n\n“(광고) 오세훈 고객님, KB국민은행입니다. 12/30까지 … ▶ https://obank.kbstar.com/demo/event/irp-004 무료수신거부 080-XXX-XXXX”", "intent": "situation",
 "links": [],
 "messages": [{"kind": "lms", "label": "고객 발송 문구",
               "text": "(광고) 오세훈 고객님, KB국민은행입니다. 12/30까지 … ▶ https://obank.kbstar.com/demo/event/irp-004 무료수신거부 080-XXX-XXXX",
               "copy": "(광고) 오세훈 고객님, KB국민은행입니다.\n12/30까지 …\n▶ https://obank.kbstar.com/demo/event/irp-004\n무료수신거부 080-XXX-XXXX"}]}
```

- `text` 는 **본문의 인용 안쪽 내용 그대로**다(따옴표 `“ ”` 또는 `" "` 는 빼고). 프론트는 본문의
  큰따옴표 인용을 화법 블록으로 바꾸기 전에, 인용 안쪽이 어떤 항목의 `text` 와 같으면 그 인용을
  화법 블록 대신 `label` 을 머리에 단 **발송 문구 블록**으로 그린다. 같은 인용이 두 블록으로
  서지 않게 한다.
- 복사 버튼에는 `copy` 를 넣는다. 본문은 줄바꿈을 한 줄로 이어 쓸 수 있어서, 원래 문구와 공백만
  다르면 서버가 줄바꿈이 살아 있는 원본을 `copy` 에 싣는다. 다르면 `text` 와 같다.
- 판정은 서버가 한다 — `(광고)` 로 시작하는 인용만 실린다(광고 표기는 발송 문구를 조립하는
  코드가 붙인다). 프론트가 따로 추측하지 않는다.
- 발송 문구 블록은 **보내지 않는다.** 복사만 한다. 보낼지는 직원이 발송 화면에서 정한다.

### 실제 출력 예

```
{"type": "progress", "text": "질문 내용을 파악하고 있어요"}
{"type": "progress", "text": "고객 브리핑 자료를 찾고 있어요"}
{"type": "answer", "text": "이준호 고객님은 만기 예금을 보유하고 있어 자산 재배분이 필요한 시점이기 때문에 타겟이에요.\n\n구체적으로는 …", "intent": "situation", "links": [], "messages": []}
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

- 에이전트를 직접 띄운 상태(`src/bin/run_local.sh`)에서 `RAW=1 src/bin/test_local.sh "질문"` 을 실행하면
  게이트웨이 없이 위 이벤트 JSON 이 한 줄씩 그대로 찍힌다. `RAW=2` 면 SSE 줄 자체가 나온다.
- 게이트웨이를 거친 결과는 `call_agent.py` 의 `ask()` 가 종류별로 모은 dict 로 돌려준다.
  `raw` 에 무엇인가 들어 있으면 게이트웨이가 이벤트가 아닌 텍스트를 보낸 것이다.
- 에이전트 쪽 로그(Grafana)는 요청마다 8자리 id 로 묶여 찍힌다 — `[api] request` 와
  `[api] done` 사이에 `[agent]` 단계 줄(understand · plan · tool · compose · verify · turn)이
  경과초와 함께 선다(`src/main.py` 머리말 «로그»). request 줄의 `맥락=N턴(저장)` 이 두 번째
  턴부터 보이면 세션이 이어지고 있는 것이다.
