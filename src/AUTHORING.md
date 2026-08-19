# 데이터 소스 추가 가이드

모든 지식/데이터는 **하나의 레코드 형태**를 쓴다. 데이터 종류마다 스키마·프롬프트를 새로
짜지 않는다 — **종류를 판정 → (없으면 한 줄 선언) → 하나의 프롬프트 생성기로 저작 → 하나의
검증기로 확인**. 이게 전부다.

## 1. 단일 레코드 형태

```json
{
  "meta": { "kind": "<종류>", "title": "원본 문서명", "as_of": "2026-08", "confidential": true },
  "records": [
    { "id": "전역 고유 id", "kind": "<종류>",
      "fields": { "...종류별 필드..." },
      "source": { "doc": "doc_id", "page": 1 },
      "refs": ["다른 레코드 id", "..."] }
  ]
}
```

- `fields` — 레코드의 데이터. 각 필드 타입은 종류 스키마가 선언한다.
- `source`·`refs` — 표준 출처/참조. 새 데이터는 이걸 쓴다(레거시는 fields 안에 둔 것도 허용).
- 파일을 **데이터 루트에 떨어뜨리면** 스토어가 `kind` 로 자동 인덱싱한다. 파일별 배선 없음.

**데이터 루트**: 운영 데이터 = `strategy_agent/data/` · 검색 지식 = `pitch_agent/data/`
(폴더명은 같지만 대외비 경계로 물리적으로 분리). `_` 로 시작하는 파일·`_backup/` 은 로더가
건너뛴다(검토 게이트).

## 2. 종류 레지스트리 = 데이터 (`common/kinds.json`)

검증기·저작 프롬프트가 **전적으로 이 선언에서** 나온다. 현재 종류:

| kind | 소비 | 사는 곳 |
|---|---|---|
| `product` | 관계형(적합성 게이트) | `strategy_agent/data/products.json` |
| `strategy` / `system_strategy` | 관계형(전략 로직) | `strategy_agent/data/strategies.json` 등 |
| `baseline` · `capability` · `asset` | 관계형(레지스트리) | `strategy_agent/data/` |
| `fact` | 참조(재사용 사실) | `pitch_agent/data/*.json` |
| `resource` | 참조(자료 목록) | `pitch_agent/data/*.json` |
| `pitch` | 검색(화법·규정·노하우) | `pitch_agent/data/*.json` |

`python -m common.schema kinds` 로 최신 목록을 본다.

## 3. 어디에 넣나 — 소비 모델로 판정

- **코드가 타입드 값으로 결정론 비교**하면(적합성 게이트·필터·금액) → **관계형** →
  `product`/`baseline`/`asset`/`capability`/`strategy`.
- **사람이 읽는 자연어를 의미 검색**하면(화법·규정 설명·노하우) → **검색형** → `pitch`.
- **여러 곳이 참조하는 수치·사실** → `fact`.

예시로 자주 헷갈리는 것:
- **진짜 상품 정보** → 관계형 `product` → `products.json` (지금 12행은 예시, 실데이터로 교체).
- **사내 규정을 "설명·조회"** → 검색형 → `pitch`(또는 별도 `regulation` 종류 신설).
- **사내 규정이 "전략을 발동·정당화"** → `strategy` (`confidence:"규정"` + `regulation`).

## 4. 저작 흐름 (사내 코파일럿 수작업)

사내 문서는 스캔·이미지 PDF·PPT 라 코드로 텍스트를 뽑을 수 없다. 그래서 자동 파이프라인이
아니라 **사내 코파일럿(비전 모델)에 수작업으로** 처리한 뒤, 받은 JSON 을 직접 넣는다.

```
1) 종류 판정   →  없으면 common/kinds.json 에 한 항목 선언(§6)
2) 프롬프트 복사 →  python -m common.schema prompt <kind>     # 터미널 출력을 복사
3) 코파일럿    →  원본 문서를 첨부 + 복사한 프롬프트를 붙여넣고 "이렇게 처리해줘" 로 실행
4) JSON 받기   →  코파일럿이 낸 JSON 을 그대로 복사
5) 저장        →  해당 루트에 _draft_<이름>.json 으로 저장 (로더가 건너뜀 = 검토 게이트)
6) 검토        →  수치·상품명·조항이 원본과 맞는지, null 처리가 옳은지 사람이 확인
7) 활성화      →  파일명에서 '_draft_' 제거
8) 검증        →  python -m common.schema validate <루트>     # ERROR 0 필수
                  + 도메인 검증: pitch `python kb.py` / strategy `python engine.py && python test_engine.py`
```

- **루트**: 관계형(product·strategy·baseline·capability·asset) → `strategy_agent/data/`,
  검색·참조(pitch·fact·resource) → `pitch_agent/data/`.
- 프롬프트에는 환각방지 규칙이 내장돼 있다: **문서에 없거나 안 보이면 지어내지 말고 null**,
  스캔 문서는 보이는 값 그대로 전사, 같은 수치는 `fact` 로 한 번만 두고 `refs` 로 참조,
  출력은 JSON 하나만(코드블록·설명 없이). 그대로 파일로 저장하면 된다.

## 5. 검증기가 잡는 것

`common.schema validate` (통합·종류 무관): 필수필드 누락 · 잘못된 값/enum · id 중복 ·
깨진 refs · **사실충돌**(같은 label 에 다른 value = 개정 반영 누락). 도메인 검증(엔진 게이트·
근거 교차검증·화법 오답 차단)은 각 에이전트의 `engine.py`/`kb.py` 가 계속 담당한다.

## 6. 새 종류를 만났을 때 (내가 놓친 데이터 포함)

닫힌 목록이 아니다. 새 데이터가 기존 종류에 안 맞으면 `common/kinds.json` 에 **선언 한 항목**을
추가한다 — 그러면 검증·저작 프롬프트·적재가 코드 수정 없이 붙는다.

```json
"regulation": {
  "consumed": "retrieval",
  "desc": "사내 규정 조문(설명·조회용)",
  "required": ["title", "clause_no", "text"],
  "fields": {
    "title":     {"type": "text"},
    "clause_no": {"type": "text"},
    "text":      {"type": "text"},
    "effective": {"type": "text", "nullable": true}
  },
  "searchable": ["title", "text"]
}
```

이후 `python -m common.schema prompt regulation` 으로 바로 저작 프롬프트가 나온다. 검색형
종류를 pitch 검색이 함께 다루게 하려면 `pitch_agent/kb.py` 의 로드 종류만 넓히면 된다.

## 7. 주의

`products.json`·`strategies.json`·`pitch_agent/data/*` 는 행내 영업전략·상품조건·
대외비 화법을 담는다. 저장소 접근권한을 통제하고, `customer_facing`(asset·resource) 은 고객
직접 제공이 허용된 배포본에만 `true` 로 둔다.
