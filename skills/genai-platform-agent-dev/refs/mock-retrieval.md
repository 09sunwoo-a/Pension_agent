# 선택적 로컬 Retrieval Mock 규격

내부 Retrieval API에 접근할 수 없는 로컬 환경에서만 사용하는 테스트 대역이다.
사용자가 로컬 Mock이 필요하다고 명시한 경우에만 이 문서를 적용한다.

## 적용 조건

- 내부 RAG(Retrieval) API를 사용하는 프로젝트다.
- 로컬 환경에서 기존 Retrieval API에 접근할 수 없다.
- 사용자가 로컬 Mock 추가를 요청했다.

조건을 하나라도 만족하지 않으면 `lib/mock_retrieval.py`와 Mock 샘플 데이터를 만들지 않는다.
검증·운영 환경에서는 항상 `refs/rag-retriever.md`의 실제 API 호출을 사용한다.

## 파일 구성

별도 서버를 만들지 않고 `lib/mock_retrieval.py` 한 파일만 추가한다.
도메인 전용 모듈에 의존하지 않으며, 프로젝트에 맞는 테스트 문서는 이 파일 안에 둔다.

```python
"""내부 Retrieval API에 접근할 수 없는 로컬 테스트용 함수형 Mock."""

MOCK_DOCUMENTS = [
    {
        "id": "mock-1",
        "title": "샘플 안내 문서",
        "content": "프로젝트에 맞는 도메인 중립적인 테스트 내용을 입력하세요.",
        "keywords": ["샘플", "안내"],
    },
]


def mock_retrieval_call(payload: dict) -> dict:
    """{retrieval_id, query} -> {results: [{id, content, title}]}"""
    query = payload.get("query", "")
    scored = []
    for doc in MOCK_DOCUMENTS:
        score = sum(1 for kw in doc["keywords"] if kw in query)
        score += sum(1 for tok in doc["title"].split() if tok in query)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        scored = [(0, doc) for doc in MOCK_DOCUMENTS[:2]]
    results = [
        {"id": doc["id"], "title": doc["title"], "content": doc["content"]}
        for _, doc in scored[:3]
    ]
    return {"results": results}
```

`MOCK_DOCUMENTS`의 값은 프로젝트 도메인에 맞게 교체하되
특정 도메인 모듈을 공통 구성으로 가정하지 않는다.
