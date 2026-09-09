# client — 배포된 에이전트를 부르는 쪽

`src/` 는 GenAI 플랫폼에 **올라가는 것**이고, 여기는 그것을 **밖에서 부르는 것**이다.
KB 통합 웹앱 파이프라인이 짜야 하는 호출 코드가 여기 있다.

경계가 셋으로 갈린다.

- **배포 이미지에 안 들어간다.** `src/Dockerfile` 이 담는 것은 `main.py`·`pension_agent`·
  `session_data`·`briefing_cache` 뿐이다.
- **`src/scripts/` 와 다른 부류다.** 그쪽은 에이전트가 자기 데이터를 만드는 내부
  도구고(`import_customers`·`build_kb`·`demo_status`), `src/` 에서 `python -m scripts.X`
  로 돈다. 여기는 임포트 루트에 얽매이지 않는다.
- **`pension_agent` 을 임포트하지 않는다.** 웹앱이 `call()` 을 그대로 떼어다 써야 하므로
  저장소 밖에서도 돌아야 한다 — 표준 라이브러리만 쓴다. 그래서 서버와 상수를 공유하지
  못하는 자리가 생기고, 그런 곳은 주석으로 짝을 밝힌다.

## 어디에 서 있나 — 인증이 구간마다 다르다

```
통합 웹앱 ──▶ 파이프라인 ──POST /chat──▶ 에이전트 ──▶ GenAI 플랫폼
                          x-openapi-token         kb-key
                          x-generative-ai-client  x-client-user
                          〈이 스크립트가 서는 자리〉  〈서버 안에서 일어난다〉
```

**두 구간을 혼용하지 않는다.** 이 스크립트는 파이프라인 자리에 서므로
`x-openapi-token`·`x-generative-ai-client` 를 보낸다. `kb-key` 는 에이전트가 GenAI
플랫폼을 부를 때 쓰는 값이라 여기서 보내지 않는다.

`x-client-user` 도 헤더로 보내지 않는다 — 그 값은 `input_value` 안에 실려 가고, 에이전트가
꺼내어 GenAI 호출 헤더로 넘긴다(사용량을 사용자별로 귀속시키는 값이다).

## 쓰는 법

어디서 실행해도 된다.

```bash
export AGENT_BASE_URL=https://<배포된-에이전트-주소>
export AGENT_OPENAPI_TOKEN=<토큰>  AGENT_AI_CLIENT=<클라이언트>

python client/call_agent.py --check                    # 배포 직후 첫 명령
python client/call_agent.py --check --live             # + SSE 규격 검사 (LLM 호출이 나간다)
python client/call_agent.py "IRP 수수료 부담된다는데?"   # 한 턴 — 답변이 흘러나온다
python client/call_agent.py -c 198734-1205842 "이 고객 뭐가 문제죠?" --progress
python client/call_agent.py --raw "..."                # 서버가 준 줄을 가공 없이
```

로컬 uvicorn(`src/run_local.sh`)을 두드릴 때는 `AGENT_BASE_URL` 이 기본값
(`http://localhost:8000`)이라 그냥 실행하면 된다. `src/test_local.sh` 와 겹치는 것이
아니라, 그쪽은 curl 한 방이고 이쪽은 **규약을 항목별로 재고 종료코드로 답한다** —
CI 나 배포 후 스모크에 그대로 건다.

## 설정

| 환경변수 | 기본값 | |
|---|---|---|
| `AGENT_BASE_URL` | `http://localhost:8000` | 에이전트 주소 (파이프라인 Valves 의 `ENDPOINT_URL`) |
| `AGENT_CHAT_PATH` | `/chat` | |
| `AGENT_HEALTH_PATH` | `/health` | 없는 배포면 `--no-health` |
| `AGENT_OPENAPI_TOKEN` | (없음) | `x-openapi-token: Bearer <값>` 으로 실린다 |
| `AGENT_AI_CLIENT` | (없음) | `x-generative-ai-client` |
| `AGENT_CLIENT_USER` | `test-user` | `input_value` 의 `x_client_user` |
| `AGENT_EXTRA_HEADERS` | (없음) | JSON 객체. 헤더가 더 필요하면 |
| `AGENT_TIMEOUT` | `120` | 초. 한 턴에 LLM 호출이 4~7회 나간다 |

값이 비면 그 헤더를 **아예 안 붙인다** — 빈 값을 보내면 「미설정」과 「거부」가 응답에서
갈리지 않는다.

## 규격 — 플랫폼 문서 「KB 통합 웹앱 연동」

### 요청

파이프라인은 사용자 메시지를 JSON 문자열로 감싸 `input_value` 하나에 담는다.

```json
{"input_value": "{\"message\": \"...\", \"x_client_user\": \"...\"}", "message_hists": null}
```

`input_value` 안의 키는 프로젝트가 정한다. 이 에이전트가 읽는 것은 `src/main.py` 기준으로
`message`(필수) · `x_client_user`(필수) · `customer_id` · `session_id` · `stream_progress` 다.

### 응답 — SSE

조각마다 `data: ` 로 시작하는 한 줄 + 빈 줄, 스트림 끝에 `data: [DONE]`.
본문 JSON 은 다섯 키를 **항상** 갖는다(값이 없으면 `null` 로 둔다 — 키를 빠뜨리지 않는다).

```
data: {"event":"CHUNK","content":"...","status":null,"references":null,"recommend_queries":null}

data: [DONE]

```

`content` 의 접두사가 웹앱이 그 조각을 어떤 UI 로 그릴지 정한다.

| 접두사 | 웹앱 렌더링 |
|---|---|
| 없음 | 채팅 말풍선에 이어쓰기 |
| `hitl:` | 선택 버튼 또는 입력창 |
| `report:` | 마크다운 보고서 뷰어 |
| `file:` | 파일 첨부 |

`call()` 은 평문만 이어 붙이고 접두사 붙은 조각은 `on_ui` 콜백으로 넘긴다 — UI 조각을
말풍선 텍스트에 섞으면 화면에 JSON 이 그대로 뜬다.

## `--check` 가 재는 것

- `/health` — 200 인가. 그리고 **LLM 엔드포인트 호스트가 이름이 풀리는가 · 키가 잡혔는가.**
  행내 첫 연결에서 실제로 걸린 자리가 인증도 쿼터도 아니고 DNS 였는데, 그 실패는 첫 대화
  턴에 가서야 「LLM 호출이 실패했습니다」로 나타나 원인이 안 보인다(`src/main.py` 주석).
- **미리 만들어 둔 브리핑이 읽히는가**(`briefing_cache`). 여기는 실패가 전부 조용하다 —
  한 건도 안 읽히면 고객 질문마다 순차 LLM 11회를 새로 치르는데, STG 는 분당 10회라 그
  한 편이 한도를 넘는다(`src/README.md` §4·§5). **호출이 성공해도 배포는 성립하지 않는
  자리**라, 화면의 «느리다»가 되기 전에 여기서 짚는다.
- 잘못된 요청 넷이 **422** 인가 — `input_value` 가 JSON 문자열이 아닐 때 · 객체가 아닐 때 ·
  `message` 누락 · `x_client_user` 누락.
- `--live` 면 SSE 규격까지 — 200 · `Content-Type` 이 `text/event-stream` ·
  **모든 조각이 `data: ` 로 시작** · 모든 조각이 JSON · **끝에 `data: [DONE]`** ·
  **다섯 키를 모두 가짐** · **한글이 `\uXXXX` 로 이스케이프되지 않음**(`ensure_ascii=False`) ·
  평문 답변이 비어 있지 않음 · 출처 블록이 붙음(근거 없는 답은 이 시스템의 산출물이
  아니다 — 루트 `CLAUDE.md` §2).

## ⚠ 지금 서버는 SSE 규격을 지키지 않는다

`src/main.py` 의 `_chunk()` 는 이렇게 내보낸다.

```python
json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False) + "\n"
```

`--check --live` 를 현재 서버에 대고 돌리면 세 항목이 FAIL 이다.

| 규격 | 지금 | 통합 웹앱에서 무엇이 깨지나 |
|---|---|---|
| `data: ` 접두사 | 없음 | 파이프라인의 `line.startswith("data: ")` 가 **모든 줄을 건너뛴다** — 화면에 아무것도 안 뜬다 |
| 끝에 `data: [DONE]` | 없음 | 파이프라인 루프가 종료 신호 없이 연결 끊김에 의존한다 |
| 다섯 키 | `event`·`content` 둘뿐 | 지금은 `obj.get()` 이라 동작이 같지만, 웹앱이 키를 직접 참조하도록 바뀌면 깨진다 |

이 형식은 `skills/genai-platform-agent-dev/refs/genai-platform.md` 의 「API I/O 스키마
(고정)」를 따른 것이고, 그 문서는 `data: ` 접두사도 `[DONE]` 도 적지 않는다. 두 문서가
어긋나는 자리이며, **통합 웹앱에 붙이려면 「KB 통합 웹앱 연동」 쪽이 기준이다.**

고치는 것은 `_chunk()` 하나와 `generate()` 끝의 `[DONE]` 한 줄이지만, `tests/test_api.py`
34건이 현재 형식을 재고 있어 함께 고쳐야 한다. 아직 안 했다 — 배포되는 출력 형식을
바꾸는 일이라 결정이 필요하다.

## 알려진 제약 — 멀티턴은 이어지지 않는다

`/chat` 응답은 CHUNK 텍스트뿐이라 다음 턴에 넘길 `history` 를 받을 방법이 없다. 플랫폼
스키마의 `message_hists` 자리는 있지만, 서버가 받는 것은 `graph.ask()` 의 내부 Turn
모양이고 그것은 응답에 실리지 않는다. 여러 턴을 주면 스크립트가 그 사실을 경고로 찍는다.
