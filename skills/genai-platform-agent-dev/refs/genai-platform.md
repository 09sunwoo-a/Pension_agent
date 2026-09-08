# 내부 GenAI 플랫폼 호출 규격

출처: `aidlc-rules/aws-aidlc-rule-details/extensions/genai-agent/genai-agent.md`

---

내부 GenAI 플랫폼은 **vLLM, OpenAI-compatible** 이다. 호출 시 두 개의 헤더가 필요하다:

- `kb-key` — 플랫폼 키. **OpenAI `api_key` 와 동일한 값을 사용한다** (클라이언트가 `api_key` 를 요구하고, 플랫폼은 `kb-key` 헤더로 인증하며 값이 같다).
- `x-client-user` — 호출 사용자 식별자. **`input_value` JSON 에서 추출** (환경변수 아님).

## 규칙

- `base_url` 은 제공된 URL 그대로 사용 — **`/v1` 절대 붙이지 않는다**.
- `model` 은 생략이 기본값 (게이트웨이가 라우팅). 특정 신규 모델(gpt-5.2 이상 등)이 명시적으로 필요할 때만 지정한다.
- `LLM_API_KEY`, `LLM_BASE_URL` 은 모두 환경변수로 처리 — **하드코딩 금지**.
- 내부 API(RAG, 조회 등)의 인증은 `kb-key` 와 **별도** — 재사용 금지.

## A) LangChain / LangGraph (기본)

```python
import os
from langchain_openai import ChatOpenAI

LLM_KEY = os.environ["LLM_API_KEY"]  # kb-key == api_key (동일한 값)
# x_client_user 는 input_value JSON 에서 추출 (I/O 스키마 섹션 참조)

llm = ChatOpenAI(
    base_url=os.environ["LLM_BASE_URL"],  # URL 그대로 — /v1 붙이지 않음
    api_key=LLM_KEY,
    # model="<MODEL_NAME>",  # 생략이 기본 (게이트웨이 라우팅)
    #                        # 특정 신규 모델 필요 시에만 명시
    default_headers={
        "kb-key": LLM_KEY,
        "x-client-user": x_client_user,  # input_value 에서 추출한 값
    },
)
```

## B) OpenAI SDK

```python
import os
from openai import OpenAI

LLM_KEY = os.environ["LLM_API_KEY"]
# x_client_user 는 input_value JSON 에서 추출 (I/O 스키마 섹션 참조)

client = OpenAI(
    base_url=os.environ["LLM_BASE_URL"],  # /v1 붙이지 않음
    api_key=LLM_KEY,
    default_headers={
        "kb-key": LLM_KEY,
        "x-client-user": x_client_user,  # input_value 에서 추출한 값
    },
)
```

## C) curl

```bash
curl "${LLM_BASE_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -H "kb-key: ${LLM_API_KEY}" \
  -H "x-client-user: <input_value에서-추출한-값>" \
  -d '{"messages": [{"role": "user", "content": "..."}]}'
# 특정 신규 모델 필요 시에만 body 에 "model": "<MODEL_NAME>" 추가
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
