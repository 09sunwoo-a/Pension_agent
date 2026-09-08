# client — 배포된 에이전트를 부르는 쪽

`src/` 는 GenAI 플랫폼에 **올라가는 것**이고, 여기는 그것을 **밖에서 부르는 것**이다.
통합 웹앱이 짜야 하는 호출 코드가 여기 있다.

경계가 셋으로 갈린다.

- **배포 이미지에 안 들어간다.** `src/Dockerfile` 이 담는 것은 `main.py`·`pension_agent`·
  `session_data` 뿐이다.
- **`src/scripts/` 와 다른 부류다.** 그쪽은 에이전트가 자기 데이터를 만드는 내부
  도구고(`import_customers`·`build_kb`·`demo_status`), `src/` 에서 `python -m scripts.X`
  로 돈다. 여기는 임포트 루트에 얽매이지 않는다.
- **`pension_agent` 을 임포트하지 않는다.** 웹앱이 `call()` 을 그대로 떼어다 써야 하므로
  저장소 밖에서도 돌아야 한다 — 표준 라이브러리만 쓴다. 그래서 서버와 상수를 공유하지
  못하는 자리가 생기고, 그런 곳은 주석으로 짝을 밝힌다.

## 쓰는 법

어디서 실행해도 된다.

```bash
export AGENT_BASE_URL=https://<배포된-에이전트-주소>  AGENT_API_KEY=<키>

python client/call_agent.py --check                    # 배포 직후 첫 명령
python client/call_agent.py --check --live             # + 정상 호출 한 턴 (LLM 호출이 나간다)
python client/call_agent.py "IRP 수수료 부담된다는데?"   # 한 턴 — 답변이 흘러나온다
python client/call_agent.py -c 198734-1205842 "이 고객 뭐가 문제죠?" --progress
python client/call_agent.py --raw "..."                # 서버가 준 줄을 가공 없이
```

로컬 uvicorn(`src/run_local.sh`)을 두드릴 때는 `AGENT_BASE_URL` 이 기본값
(`http://localhost:8000`)이라 그냥 실행하면 된다. `src/test_local.sh` 와 겹치는 것이
아니라, 그쪽은 curl 한 방이고 이쪽은 **규약을 항목별로 재고 종료코드로 답한다** —
CI 나 배포 후 스모크에 그대로 건다.

## 설정

경로 접두와 인증 헤더 이름은 플랫폼 문서(「통합 웹앱 연동」)가 정한다. 코드에 박지 않고
환경변수로 뺐으니, 확인한 값을 넣으면 코드를 고칠 필요가 없다.

| 환경변수 | 기본값 | |
|---|---|---|
| `AGENT_BASE_URL` | `http://localhost:8000` | 배포된 에이전트 주소 |
| `AGENT_CHAT_PATH` | `/chat` | |
| `AGENT_HEALTH_PATH` | `/health` | 없는 배포면 `--no-health` |
| `AGENT_API_KEY` | (없음) | 비우면 인증 헤더를 아예 안 붙인다 |
| `AGENT_KEY_HEADER` | `kb-key` | 키를 실을 헤더 이름 |
| `AGENT_CLIENT_USER` | `test-user` | 호출자 식별자 |
| `AGENT_EXTRA_HEADERS` | (없음) | JSON 객체. 헤더가 더 필요하면 |
| `AGENT_TIMEOUT` | `120` | 초. 한 턴에 LLM 호출이 4~7회 나간다 |

## `--check` 가 재는 것

호출 규약은 플랫폼이 고정한 것이고
(`skills/genai-platform-agent-dev/refs/genai-platform.md` «API I/O 스키마 (고정)»),
그것을 구현한 것이 `src/main.py` 다. 검증 항목은 거기서 나왔다.

- `/health` — 200 인가. 그리고 **LLM 엔드포인트 호스트가 이름이 풀리는가 · 키가 잡혔는가.**
  행내 첫 연결에서 실제로 걸린 자리가 인증도 쿼터도 아니고 DNS 였는데, 그 실패는 첫 대화
  턴에 가서야 「LLM 호출이 실패했습니다」로 나타나 원인이 안 보인다(`src/main.py` 주석).
- 잘못된 요청 넷이 **422** 인가 — `input_value` 가 JSON 문자열이 아닐 때 · 객체가 아닐 때 ·
  `message` 누락 · `x_client_user` 누락.
- `--live` 면 정상 호출까지 — 200 인가 · `Content-Type` 이 `text/event-stream` 인가 ·
  **모든 줄이 `{"event":"CHUNK",...}` 형태인가** · 답변이 비어 있지 않은가 ·
  출처 블록이 붙었는가(근거 없는 답은 이 시스템의 산출물이 아니다 — 루트 `CLAUDE.md` §2).

## 알려진 제약 — 멀티턴은 이어지지 않는다

`/chat` 응답은 CHUNK 텍스트뿐이라 다음 턴에 넘길 `history` 를 받을 방법이 없다. 플랫폼
스키마의 `message_hists` 자리는 있지만, 서버가 받는 것은 `graph.ask()` 의 내부 Turn
모양이고 그것은 응답에 실리지 않는다. 여러 턴을 주면 스크립트가 그 사실을 경고로 찍는다.

통합 웹앱에서 후속 질문의 맥락을 이으려면 응답 스키마 쪽 결정이 필요하다.
