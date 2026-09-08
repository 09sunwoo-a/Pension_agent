
# LLM Gateway (실제 호출)

가이드 원문: https://aidc.pay.kbpsandbox.com/docs/llm-call/#reveal

> ⚠️ Base URL 은 **클러스터 내부 전용** — 로컬 PC 에서는 접근 불가.
> 로컬 단위 테스트에서는 LLM 클라이언트를 주입하고 test double을 사용한다.

---

## 접속 정보

| 항목 | 값 |
|---|---|
| Base URL | `http://litellm.aidc-prod.svc.cluster.local:4000` |
| Endpoint | `POST {BASE_URL}/v1/messages` |
| API Key | 프로젝트 콘솔 → 작업환경 탭 → LLM Gateway 카드에서 확인 (`sk-...`) |

---

## 환경변수

```bash
LLM_BASE_URL=http://litellm.aidc-prod.svc.cluster.local:4000
LLM_API_KEY=<콘솔에서-발급한-키>
LLM_MODEL=claude-sonnet-4-6            # 생략 시 기본값으로 사용 권장
```

---

## 지원 모델

| Slug | 특징 |
|---|---|
| `claude-sonnet-4-6` | 균형형 (기본값 권장) |
| `claude-opus-4-8` | 고성능, 복잡한 분석 |
| `claude-haiku-4-5` | 빠르고 경량 |

---

## Python — LangChain / LangGraph

```python
import os
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

LLM_KEY = os.environ["LLM_API_KEY"]
# x_client_user 는 input_value JSON 에서 추출 (I/O 스키마 섹션 참조)

llm = ChatOpenAI(
    base_url=os.environ["LLM_BASE_URL"],  # /v1 붙이지 않음
    api_key=LLM_KEY,
    model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
    default_headers={
        "kb-key": LLM_KEY,
        "x-client-user": x_client_user,  # input_value 에서 추출한 값
    },
)

# ReAct 에이전트 예시
agent = create_react_agent(llm, tools=[...])
result = agent.invoke({"messages": [{"role": "user", "content": "..."}]})
print(result["messages"][-1].content)
```

---

## Python — OpenAI SDK (검증됨)

```python
import os
from openai import OpenAI

LLM_KEY = os.environ["LLM_API_KEY"]
# x_client_user 는 input_value JSON 에서 추출 (I/O 스키마 섹션 참조)

client = OpenAI(
    base_url=os.environ["LLM_BASE_URL"],  # 게이트웨이 URL 그대로 — /v1 붙이지 않음
    api_key=LLM_KEY,
    default_headers={
        "kb-key": LLM_KEY,
        "x-client-user": x_client_user,  # input_value 에서 추출한 값
    },
)

resp = client.chat.completions.create(
    model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
    messages=[{"role": "user", "content": "..."}],
)
print(resp.choices[0].message.content)
```

---

## API I/O 스키마 (고정)

> 플랫폼 제약으로 인해 아래 스키마를 **반드시** 준수한다. 임의 변경 금지.

### Input

```python
from typing import Optional, List
from pydantic import BaseModel

class ChatRequest(BaseModel):
    input_value: str              # 구조화된 메시지를 JSON 직렬화한 문자열
    message_hists: Optional[List] = None
```

`input_value` 는 에이전트에 전달할 메시지를 `json.dumps()` 로 직렬화한 문자열이다.
에이전트 내부에서 `json.loads()` 로 파싱해 필요한 필드를 추출한다:

```python
import json
from fastapi import HTTPException

payload = json.loads(req.input_value)
# input_value JSON 구조 예시:
# {"message": "사용자 메시지", "x_client_user": "user-id-123", ...프로젝트별 추가 키}

x_client_user = payload.get("x_client_user")
if not x_client_user:
    raise HTTPException(
        status_code=422,
        detail="input_value 에 'x_client_user' 키가 필요합니다."
    )

message = payload.get("message")
if not message:
    raise HTTPException(
        status_code=422,
        detail="input_value 에 'message' 키가 필요합니다."
    )
```

### Output

응답은 반드시 `StreamingResponse` (Content-Type: `text/event-stream`).
각 청크 포맷:

```json
{"event": "CHUNK", "content": "<텍스트>"}
```

구현 예시:

```python
from fastapi.responses import StreamingResponse
import json

@app.post("/chat")
async def chat(req: ChatRequest):
    payload = json.loads(req.input_value)
    query = payload["message"]  # 프로젝트별 필드명 사용

    async def generate():
        async for chunk in agent.astream({"input": query}):
            text = chunk.get("output") or str(chunk)
            yield json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```
