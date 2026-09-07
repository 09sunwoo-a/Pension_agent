# 데이터 소스 추가 가이드

모든 지식/데이터는 **하나의 레코드 형태**를 쓴다. 데이터 종류마다 스키마·프롬프트를 새로
짜지 않는다 — **종류를 판정 → (없으면 한 줄 선언) → 하나의 프롬프트 생성기로 저작 → 하나의
검증기로 확인**. 이게 전부다.

## 1. 단일 레코드 형태

```json
{
  "meta": { "kind": "<종류>", "title": "이 파일의 이름표", "as_of": "2026-08", "confidential": true,
            "source_doc": "doc_id" },
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

**데이터 루트**: 상품·전략 카탈로그 = `pension_agent/strategy_agent/data/` · 공용 지식 = `pension_agent/knowledge/data/`
(폴더명은 같지만 대외비 경계로 물리적으로 분리). `_` 로 시작하는 파일·폴더는 로더가 건너뛴다
(검토 게이트 — `pension_agent/knowledge/store.py::iter_knowledge_files`). 작업 중인 초안이나 보관본을 데이터
루트에 두어야 할 때 `_` 를 붙이면 적재 대상에서 빠진다.

## 2. 종류 레지스트리 = 데이터 (`pension_agent/knowledge/kinds.json`)

검증기·저작 프롬프트가 **전적으로 이 선언에서** 나온다. 현재 종류:

| kind | 소비 | 사는 곳 |
|---|---|---|
| `product` | 관계형(적합성 게이트) | `strategy_agent/data/products.json` |
| `strategy` / `system_strategy` | 관계형(전략 로직) | `strategy_agent/data/strategies.json` 등 |
| `baseline` · `capability` · `asset` | 관계형(레지스트리) | `strategy_agent/data/` |
| `fact` | 참조(재사용 사실·제도 팩트) | `knowledge/data/*.json` |
| `resource` | 참조(자료 목록) | `knowledge/data/*.json` |
| `doc` | 참조(원천 문서 레지스트리) | `knowledge/data/kb_docs.json` |
| `pitch` | 검색(화법·규정·노하우) | `knowledge/data/*.json` |
| `segment` | 검색(고객군 정의 = 문제상황) | `knowledge/data/kb_segments.json` |
| `method` | 검색(관리 방법론 — 상황→액션 판단 규칙) | `knowledge/data/kb_methods.json` |
| `procedure` | 검색(업무 처리 절차·화면번호) | `knowledge/data/kb_procedures.json` |
| `screen` | 검색(단말 화면 레지스트리 — 어느 화면인가) | `knowledge/data/kb_screens.json` |
| `channel` | 검색(비대면 채널 경로 — 고객이 앱·웹에서 어디로 가나) | `knowledge/data/kb_channels.json` |
| `fieldtip` | 검색(현장의 목소리 — 영업점 관찰) | `knowledge/data/kb_fieldtips.json` |
| `market` | 검색(시황 — 시장이 어떻게 돌아가나) | `knowledge/data/kb_market.json` |
| `lineup` | 검색(운용 상품 — 우리가 뭘 파나. `product` 와 다르다: 그쪽은 게이트용 관계형 카탈로그) | `knowledge/data/kb_lineup.json` |

`python -m pension_agent.knowledge.schema kinds` 로 최신 목록을 본다.

**`market`·`lineup` 은 05 폴더가 자기 규칙을 선언한다.** 카드마다 손으로 적는 것이 아니라
`05_시황_상품_기반지식/README.md` 의 표지 줄 두 개에서 변환기가 읽어 모든 카드에 싣는다 —
`※` 줄은 시효 경고(`volatile`), `⚖` 줄은 인용 고지(`advisory`, "정보 제공 목적 · 투자권유
시 자본시장법·당행 규정 준수 의무")다. 문구를 코드나 카드에 베껴 쓰면 README 가 바뀔 때
갈리고, 갈리면 답변이 틀린 표시를 단다. **표시를 새로 만들 때도 여기에 줄을 추가한다.**

`product_names`(답변이 말해도 되는 상품 이름의 등록부)도 저작자가 따로 적지 않는다 —
문서 표의 «상품명»·«편입상품»·«디폴트옵션 상품» 칸을 변환기가 걷는다. 상품명을 산문에만
적고 표에 안 넣으면 그 상품은 등록부에 없고, 그 이름을 말한 답변은 거부된다.

**`doc` 은 다른 종류와 역할이 다르다** — 그 자체가 지식이 아니라 "이 카드가 어느 원본 문서에서
왔는가"를 한 곳에서 관리하는 레지스트리다. 모든 카드가 `source.doc` 으로 여길 가리키고, 화면과
대화형 답변은 조인해서 원천 문서명·부서·게시시점을 함께 보여준다. 스토어에 `doc` 레코드가
하나라도 있으면 검증기가 `source.doc` 의 깨진 참조를 ERROR 로 잡는다.

**`doc` 의 제목은 원문에서 읽는다.** 01~04 폴더 문서의 `title` 은 원문 `.md` 의 H1(+판·차수
부제)을 `build_kb.doc_title()` 이 파싱한 값이다 — 사람이 다시 타이핑하지 않는다. 원문 제목만으로
문서를 특정할 수 없을 때만 `config.GUIDE_DOCS` 의 `title_override` 로 대체하고, 원문과 다르면
`title_override_reason` 으로 이유를 남긴다(없으면 변환 리포트가 [제목불일치]로 알리고
`test_paths` 가 실패한다). 부서·시점은 문서 표기가 일정하지 않아 시드가 계속 갖는다.

**`meta.title` 은 출처가 아니다.** 그건 적재 파일의 이름표라서 "영업 화법 — 06/03 영업화법"
처럼 변환본 이름인 경우가 있다. 출처를 만드는 곳은 `knowledge/kb.py::origin_of()` 하나이고,
`원천 문서 → 원문 표기(source_text) → 추출지식 절 → "확인 필요"` 순으로 물러선다 —
어느 단계에서도 파일 이름표로는 물러서지 않는다. 파일 하나가 통째로 원천 문서 하나면
레코드마다 `source.doc` 를 반복하지 말고 **`meta.source_doc` 에 한 번만** 적는다(레코드가
자기 `source.doc` 를 가지면 그쪽이 이긴다). 이것도 레지스트리 id 여야 하고, 아니면 ERROR 다.

## 3. 어디에 넣나 — 소비 모델로 판정

- **코드가 타입드 값으로 결정론 비교**하면(적합성 게이트·필터·금액) → **관계형** →
  `product`/`baseline`/`asset`/`capability`/`strategy`.
- **사람이 읽는 자연어를 의미 검색**하면(화법·규정 설명·노하우) → **검색형** → `pitch`.
- **여러 곳이 참조하는 수치·사실** → `fact`.

예시로 자주 헷갈리는 것:
- **진짜 상품 정보** → 관계형 `product` → `products.json` (지금 12행은 예시, 실데이터로 교체).
- **사내 규정을 "설명·조회"** → 검색형 → `pitch`(또는 별도 `regulation` 종류 신설).
- **사내 규정이 "전략을 발동·정당화"** → `strategy` (`confidence:"규정"` + `regulation`).

## 4. 저작 흐름

원본의 형태에 따라 두 갈래다.

### 4-a. 이미 마크다운으로 정리된 것 → 변환 스크립트 (`src/scripts/kb_build/`)

`06_주제별_추출지식/` 의 다섯 문서(고객세그먼트·IRP관리방법론·영업화법·제도상품팩트·업무처리절차)와
`08_인사이트/` 는 사람이 읽는 검토용 마크다운이라 코드로 옮길 수 있다. 수백 건을 손으로 옮기면
누락·오타가 생기고 원본이 개정될 때 다시 대조할 방법이 없으므로 **결정론 변환기**를 쓴다.

```
python -m scripts.kb_build.build_kb              # _draft_kb_*.json 생성 + 변환 리포트
#   → 리포트의 '출처 미해석'·'변환 알림' 을 사람이 확인
python -m scripts.kb_build.build_kb --activate   # '_draft_' 접두 제거(= 검토 완료)
```

- **생성된 JSON 은 손으로 고치지 않는다.** 고칠 값이 있으면 `config.py`(문서 메타·그룹 매핑·
  세그먼트 CONDS·개인정보 패턴)를 고치고 다시 생성한다. 손으로 고치면 다음 실행에 지워진다.
- 출처는 변환본 경로·글번호·약칭·제목 유사도 순으로 해석하고, **못 찾으면 지어내지 않고**
  원문 표기를 `source_text` 로 남긴 뒤 리포트에 올린다.
- 원문이 "확인 필요"로 표시한 팩트는 `_hold_` 파일로 분리해 적재하지 않는다 — 지식베이스에
  들어가는 순간 에이전트가 그걸 사실로 답하기 때문이다.

### 4-b. 스캔·이미지 PDF·PPT → 사내 코파일럿 수작업

코드로 텍스트를 뽑을 수 없는 원본은 **사내 코파일럿(비전 모델)에 수작업으로** 처리한 뒤,
받은 JSON 을 직접 넣는다.

```
1) 종류 판정   →  없으면 pension_agent/knowledge/kinds.json 에 한 항목 선언(§6)
2) 프롬프트 복사 →  python -m pension_agent.knowledge.schema prompt <kind>   # 터미널 출력을 복사
3) 코파일럿    →  원본 문서를 첨부 + 복사한 프롬프트를 붙여넣고 "이렇게 처리해줘" 로 실행
4) JSON 받기   →  코파일럿이 낸 JSON 을 그대로 복사
5) 저장        →  해당 루트에 _draft_<이름>.json 으로 저장 (로더가 건너뜀 = 검토 게이트)
6) 검토        →  수치·상품명·조항이 원본과 맞는지, null 처리가 옳은지 사람이 확인
7) 활성화      →  파일명에서 '_draft_' 제거
8) 검증        →  python -m pension_agent.knowledge.schema validate <루트>   # ERROR 0 필수
                  + 도메인 검증: 지식 `python -m pension_agent.knowledge.kb`
                                 전략 `python -m pension_agent.strategy_agent.engine && python -m tests.test_engine`
```

- **루트**: 관계형(product·strategy·baseline·capability·asset) → `strategy_agent/data/`,
  검색·참조(pitch·fact·resource) → `knowledge/data/`.
- 프롬프트에는 환각방지 규칙이 내장돼 있다: **문서에 없거나 안 보이면 지어내지 말고 null**,
  스캔 문서는 보이는 값 그대로 전사, 같은 수치는 `fact` 로 한 번만 두고 `refs` 로 참조,
  출력은 JSON 하나만(코드블록·설명 없이). 그대로 파일로 저장하면 된다.
- **`fact` 는 `as_of` 를 되도록 채운다**: 근거 자료의 기준시점을 원문에서 찾아 넣는다. 원문에
  시점 표기가 없으면 비워도 되지만(검증기가 WARN 으로만 알림), 값이 같아도 시점이 지나면
  조용히 틀려지는 사실(세제·한도·수수료·수익률 등)은 이 필드 없이는 최신성을 추적할 방법이
  없으므로 6번 검토 단계에서 최대한 보완한다.
- **자료의 성격은 출처로 말하고, 작성자로 추론하지 않는다**: "작성자가 ○○부 소속이라
  교육용으로 보인다" 같은 문장을 주의·요약에 넣지 않는다. 소속·직급·조회수는 그 자료가
  무엇인지 말해주지 않고, 확인할 수단도 대조할 데이터도 없는 판단이 직원에게는 결론처럼
  읽힌다. 같은 결론이 필요하면 **출처 종류**로 말한다("핫팁 게시글이므로 본부 확정 지침이
  아니다") — 문서 레지스트리가 아는 사실이라 어긋나면 데이터에서 드러난다. 확인이 필요한
  판단은 `authoring` 주의로 **확인 방법과 함께** 남긴다. 검증기가 `[신원추론]` WARN 으로
  훑는다(`knowledge/CLAUDE.md` 「자료의 지위는 출처가 정한다」).
- **개인 식별정보는 저작 산출물에 넣지 않는다**: 원본이 사내 게시판 글처럼 작성자 실명·부점·
  직급이 붙는 자료면, `fields`에는 그 정보를 옮기지 않는다. 출처가 필요하면 `source.doc`에
  원본 문서 id만 남긴다 — 검색·전략 판단에는 불필요한 정보이고, 데이터가 넓게 재사용될수록
  재유출 위험만 커진다. 검증기가 흔한 표기 패턴("이름(부점/직급)")을 걸러 WARN 으로 잡아주지만,
  정규식 하나로 다 잡히지 않으니 사람이 한 번 더 봐야 한다.

## 5. 검증기가 잡는 것

`knowledge.schema validate` (통합·종류 무관): 필수필드 누락 · 잘못된 값/enum · id 중복 ·
깨진 refs · **사실충돌**(같은 label 에 다른 value = 개정 반영 누락, ERROR) · **최신성 미기재**
(`fact.as_of` 없음, WARN) · **개인정보 의심**(작성자 실명·부점·직급 패턴, WARN). 도메인 검증
(엔진 게이트·근거 교차검증·화법 오답 차단)은 `strategy_agent/engine`·`knowledge/kb.py` 가 계속 담당한다.

## 6. 새 종류를 만났을 때 (내가 놓친 데이터 포함)

닫힌 목록이 아니다. 새 데이터가 기존 종류에 안 맞으면 `knowledge/kinds.json` 에 **선언 한 항목**을
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

이후 `python -m pension_agent.knowledge.schema prompt regulation` 으로 바로 저작 프롬프트가 나온다. 검색형
종류를 pitch 검색이 함께 다루게 하려면 `knowledge/kb.py` 의 로드 종류만 넓히면 된다.

## 7. 주의

`products.json`·`strategies.json`·`knowledge/data/*` 는 행내 영업전략·상품조건·
대외비 화법을 담는다. 저장소 접근권한을 통제하고, `customer_facing`(asset·resource) 은 고객
직접 제공이 허용된 배포본에만 `true` 로 둔다.

원본이 사내 게시글(작성자 실명·부점·직급이 붙는 자료)인 경우, **카드 `fields`** 에는 그 개인
식별정보를 옮기지 않는다(§4). `knowledge.schema validate` 실행 시 `[개인정보의심]` WARN 이 뜨면
해당 레코드를 열어 실제로 개인정보인지 확인하고 제거한다.

**출처 표기는 다르다.** 문서 레지스트리(`kb_docs`)의 작성자는 게시글 프론트매터 표기를 그대로
남긴다 — 출처가 누구의 글인지는 직원이 그 말을 얼마나 믿을지 판단하는 재료이고, 지우면 화면이
"작성자 정보 미기재"라고 **사실과 다르게** 말하게 된다. 지우는 것은 카드 본문, 남기는 것은 출처다.
그리고 남긴 작성자 표기를 **자료의 성격을 추론하는 근거로 쓰지 않는다**(§4 첫 항목).
