# 내부 RAG(Retrieval) API 규격

이미 제공되는 사내 Retrieval API를 LangChain 검색기로 래핑하는 패턴이다.
별도의 Retrieval API나 Mock 서버를 새로 만들지 않는다.

## 구현 패턴

```python
"""기존 사내 Retrieval API를 LangChain 검색기로 래핑한다."""
import os
from typing import List

import requests
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class CustomRetriever(BaseRetriever):
    """사내 Retrieval API용 검색기. query -> Document 리스트."""

    retrieval_id: int = 0

    def _call_api(self, query: str) -> dict:
        payload = {"retrieval_id": self.retrieval_id, "query": query}
        headers = {
            "x-openapi-token": f"Bearer {os.environ['OPENAPI_TOKEN']}",
            "x-generative-ai-client": os.environ["GENERATIVE_AI_CLIENT"],
        }
        response = requests.post(
            os.environ["RETRIEVAL_URL"],
            json=payload,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        data = self._call_api(query)
        docs: List[Document] = []
        for result in data.get("results", []):
            docs.append(
                Document(
                    page_content=result.get("content", ""),
                    metadata={
                        "id": result.get("id", ""),
                        "title": result.get("title", ""),
                    },
                )
            )
        return docs
```

## 핵심 규칙

- 내부 Retrieval API는 반드시 이 `BaseRetriever` 래퍼 패턴으로 구현한다.
- 이미 제공되는 Retrieval API를 호출하며 별도의 API 서버를 구현하지 않는다.
- `retrieval_id`로 검색 대상을 식별한다. 값은 환경변수 또는 설정에서 주입한다.
- 요청 payload: `{"retrieval_id": <int>, "query": <str>}`
- 응답 구조: `{"results": [{"id": ..., "title": ..., "content": ...}, ...]}`
- `RETRIEVAL_URL`은 환경변수로 처리한다. 하드코딩하지 않는다.
- Retrieval 인증은 `OPENAPI_TOKEN`, `GENERATIVE_AI_CLIENT` 환경변수로 주입한다.
- `x-openapi-token` 값은 `Bearer <OPENAPI_TOKEN>` 형식을 사용한다.
- Retrieval 인증은 GenAI 플랫폼의 `kb-key`/`x-client-user`와 별도이므로 재사용하지 않는다.
- `requests`를 직접 사용하는 경우 프로젝트의 `requirements.txt`에도 승인된 버전을 명시한다.

## 로컬 Mock이 필요한 경우

기본 구현은 모든 환경에서 위의 기존 Retrieval API를 호출한다.
내부 API에 접근할 수 없는 로컬 환경이고 사용자가 Mock을 요청한 경우에만
`refs/mock-retrieval.md`를 읽고 `lib/mock_retrieval.py`를 추가한다.

그 경우에만 로컬 설정에서 `_call_api`의 호출 대상을
`mock_retrieval_call(payload)`로 주입하거나 교체한다.
검증·운영 코드에서는 Mock을 사용하지 않는다.

```
.claude/skills/genai-platform-agent-dev/refs/mock-retrieval.md
```
