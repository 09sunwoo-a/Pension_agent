---
name: aiden-help
description: AIDEN 플랫폼 사용법 질문에 문서 사이트를 조회해 답한다. 로그인·프로젝트 신청·IDE 접속·DB/캐시 붙이기·리소스 상향·S3·배포·CI/CD·LLM 직접 호출·데이터 카탈로그 MCP·GitLab MCP·Mock 서비스·스킬/에이전트 설치(aiden CLI)·의뢰·보안 서약·KB AI DLC·GenAI 플랫폼 에이전트 관련 질문에 사용. "AIDEN 에서 ~ 어떻게 해", "배포가 안 되는데", "GitLab clone 이 안 돼", "LLM 키 어디서 받아", "카탈로그 MCP 연결", "워크스페이스 디스크 늘리기", "스킬 설치" 같은 요청에 대응. 일반 Claude Code 사용법(스킬/훅/서브에이전트 문법)도 이 문서에 포함되어 있다.
---

# AIDEN 사용법 답변

AIDEN 플랫폼 사용법 질문에는 **추측하지 말고 문서를 조회해서** 답한다. 문서에 없는
내용은 "문서에 없다"고 명시한다.

반드시 **한국어**로 답한다.

문서 사이트 base URL: `https://aidc.pay.kbpsandbox.com/docs`

라우트는 **카테고리 폴더 + 파일명** 이다 — `/docs/<카테고리>/<이름>`.
예: `/docs/start/guide`, `/docs/develop/llm-call`.

---

## 도구 두 가지 — 역할이 다르다

| 도구 | 역할 | 언제 |
|---|---|---|
| `WebFetch` (내장) | **URL 지정 본문 조회.** 주 경로. | 지도에서 경로를 골랐을 때 — 대부분의 경우 |
| `web-search` MCP | **키워드 검색.** 보조 경로. URL 을 찾아준다. | 지도로 어느 문서인지 못 고를 때만 |

`web-search` 는 AgentCore 웹 검색으로 **검색 전용이다 — URL 을 지정해 본문을 가져오지
못한다.** 검색으로 얻은 `/docs/...` URL 을 다시 `WebFetch` 로 열어야 본문이 나온다.
따라서 경로를 이미 아는 상황에서 `web-search` 를 쓰는 것은 한 단계 낭비다.

---

## 절차

1. 아래 문서 지도에서 질문에 해당하는 경로를 1~2개 고른다.
2. `WebFetch` 로 `https://aidc.pay.kbpsandbox.com/docs/<카테고리>/<이름>` 을 조회한다.
   prompt 에는 사용자의 원래 질문을 그대로 넣는다.
3. 답변 끝에 **출처를 반드시 남긴다** —
   `https://aidc.pay.kbpsandbox.com/docs/<카테고리>/<이름>#<앵커>`.
   사용자가 30초 안에 자가검증할 수 있어야 한다.
4. 첫 문서에서 답이 안 나오면 지도의 인접 문서를 한 번 더 조회한다.
5. **지도로 문서를 특정할 수 없을 때만** `web-search` MCP 로
   `site:aidc.pay.kbpsandbox.com/docs <질문 키워드>` 를 검색해 후보 URL 을 얻고,
   그 URL 을 `WebFetch` 로 연다. 검색 결과 스니펫 자체를 답변 근거로 쓰지 않는다 —
   반드시 본문을 열어 확인한다.
6. 여기까지 해도 없으면 문서에 없다고 답한다 — 그럴듯한 추측을 만들어내지 않는다.

두 도구 모두 실패하면(네트워크 정책·프록시 미동작) 그 사실을 사용자에게 알리고 문서
URL 을 직접 열어보도록 안내한다. **기억에 의존해 답하지 않는다.**

---

## 문서 지도

### `start/` — 시작하기

- **`start/guide`** — 플랫폼 사용 가이드. 로그인부터 배포까지 전체 한 바퀴.
  **어느 문서인지 모르겠으면 여기서 시작.**
  앵커: `#login` 사내 SSO 로그인 · `#apply` 새 프로젝트 신청 · `#detail` 프로젝트 콘솔 ·
  `#ide` 클라우드 IDE 접속 · `#backing` DB·캐시 붙이기 ·
  `#resource-upgrade` 디스크·메모리 상향 · `#s3` S3 버킷 ·
  `#claude` IDE 안에서 Claude Code 사용 · `#claude-cli` CLI · `#claude-extension` 익스텐션 ·
  `#catalog` 도구 허브 · `#commission` 의뢰(AI 에이전트에게 맡기기) ·
  `#mock` Mock 서비스 · `#deploy` 배포 · `#approvals` 결재 · `#usage` 사용량 ·
  `#feedback` 피드백

- **`start/security`** — 보안 서약·준수사항.
  앵커: `#pledge-gate` 로그인 시 서약 동의 · `#clauses` 3대 준수사항 ·
  `#clause-1` `#clause-2` `#clause-3` 각 조항 ·
  `#pii-masking` 개인정보 자동 마스킹 · `#violation` 위반 시

### `develop/` — 개발

- **`develop/llm-call`** — 워크스페이스에서 LLM 직접 호출 (API 키·엔드포인트·모델).
  앵커: `#endpoint` 접속 정보 3가지 · `#reveal` 키 확인 · `#env` 환경변수 설정 ·
  `#call` 호출 예시 · `#openai-compat` OpenAI 호환 호출 · `#contract` 요청 규약 ·
  `#models` 지원 모델 · `#troubleshooting`

- **`develop/gitlab-mcp`** — GitLab MCP 연결.
  **`/mcp` 자동 승인 실패·clone 문제는 여기.**
  앵커: `#why` 왜 자동 승인이 안 되나 · `#connect` `--no-browser` 연결 방법 ·
  `#troubleshooting` · `#next` 연결한 다음

- **`develop/aiden-cli`** — `aiden` CLI 로 스킬·에이전트 설치.
  앵커: `#concepts` 스킬 vs 에이전트 · `#catalog` 콘솔 카탈로그 ·
  `#where` 실행 위치(저장소 루트) · `#skills` 스킬 사용 · `#skill-list` 목록 ·
  `#skill-add` 설치 · `#skill-add-local-edits` 로컬 수정본 · `#skill-log` 변경이력 ·
  `#skill-remove` 삭제 · `#agents` 에이전트 사용 · `#after` 설치 후 인식

### `deploy/` — 배포

- **`deploy/guide`** — 배포 설정·CI/CD·로그·환경변수 상세.
  앵커: `#setup` cicd-setup 도우미 · `#logs` stdout/stderr 로그 규약 ·
  `#port-health` 포트·헬스체크 · `#env` 환경변수 주입 · `#backing` 상시 DB·캐시 ·
  `#push` main push 자동 배포 · `#approvals` 배포 전 보안 결재

### `integration/` — 사내 연동

- **`integration/catalog`** — 데이터 카탈로그 사용 + MCP 연결.
  앵커: `#flow` 전체 흐름 · `#browse` 데이터셋 둘러보기 · `#request` 새 데이터셋 요청 ·
  `#token` MCP 토큰 발급 · `#connect-agent` 에이전트에 연결 · `#tools` MCP 도구 사용 ·
  `#filter` 필터 · `#paging` 페이징 · `#troubleshooting`

- **`integration/mock-api`** — 사내 시스템 Mock 레퍼런스.
  앵커: `#gateway` 게이트웨이 · `#sso-saml` OnePass SSO(SAML 2.0) ·
  `#sso-legacy` 레거시 secureToken · `#workb` WorkB 쪽지 · `#workb-mcp` WorkB MCP

### `workflow/` — 워크플로

- **`workflow/aidlc`** — KB AI DLC (기획부터 목업·리포트까지).
  앵커: `#what` KB AI DLC 란 · `#start` 시작 · `#start-install` 설치 · `#start-run` 실행 ·
  `#principles` 핵심 원칙 · `#inputs` 비전·기술환경 입력 · `#flow` 질문→문서→승인 ·
  `#deliverables` 산출물 · `#no-hand-edit` 생성물 직접 수정 금지 ·
  `#extensions` 도메인 지식 익스텐션 · `#extension-kinds` 종류 · `#extension-behavior` 동작

- **`workflow/genai-agent`** — GenAI 플랫폼 에이전트 만들기(RAG·Dockerfile 포함).
  앵커: `#platforms` 두 플랫폼 · `#skill` 스킬 설치·발동 · `#structure` 프로젝트 구성 ·
  `#genai` GenAI 플랫폼 호출 · `#rag` RAG Retriever · `#docker` Dockerfile ·
  `#requirements` 검증된 requirements · `#mock` 로컬 Mock · `#checklist` 완료 체크리스트

### `claude-code/` — Claude Code 자체 사용법

- **`claude-code/basics`** — 개념·기본 사용 흐름·슬래시 명령·CLAUDE.md·`.claude` 폴더 구조
- **`claude-code/advanced`** — 스킬/서브에이전트/훅/MCP/rules 작성법, 실전 플레이북

### `lecture/` — 워크샵 강의

- **`lecture/foundations`** — Ⅰ. 기초와 원리: 에이전트형의 의미, 컨텍스트 윈도우, CLAUDE.md
- **`lecture/extending`** — Ⅱ. 확장·자동화: SKILL.md·서브에이전트·훅·MCP·rules 5개 레이어
- **`lecture/ecosystem`** — Ⅲ. 생태계·플러그인: superpowers, oh-my-claudecode, gstack

### 목차

지도에서 못 찾으면 `https://aidc.pay.kbpsandbox.com/docs` (경로 없이) 를 조회해
전체 문서 목록을 확인한다. `/docs/index` 는 404 이므로 쓰지 않는다.

---

## 라우팅 힌트

질문에 아래 표현이 보이면 해당 문서부터 조회한다.
여기서 걸리면 `web-search` 는 부르지 않는다.

| 사용자 표현 | 문서 |
|---|---|
| 로그인 안 됨, SSO, 서약서가 뜬다 | `start/guide#login` → `start/security#pledge-gate` |
| 프로젝트 신청, 승인 대기 | `start/guide#apply` |
| IDE 안 열림, 작업환경 접속 | `start/guide#ide` |
| 디스크 꽉 찼다, 메모리 늘려줘 | `start/guide#resource-upgrade` |
| DB 붙이기, Postgres/MySQL/Redis | `start/guide#backing` → `deploy/guide#backing` |
| S3, 파일 저장소, 버킷 | `start/guide#s3` |
| 사용량, 비용 얼마 썼나 | `start/guide#usage` |
| 배포 실패, CI/CD, 로그 안 보임 | `deploy/guide` |
| 환경변수 넣기 | `deploy/guide#env` |
| LLM 호출, API 키, 모델 목록 | `develop/llm-call` |
| clone 실패, git push 403, `/mcp` | `develop/gitlab-mcp` |
| 데이터셋, 카탈로그, MCP 토큰 | `integration/catalog` |
| 스킬/에이전트 설치, `aiden` 명령 | `develop/aiden-cli` |
| 의뢰, AI 에이전트에게 맡기기 | `start/guide#commission` |
| Mock, OnePass, WorkB 쪽지 | `integration/mock-api` |
| 개인정보 마스킹, 보안 위반 | `start/security` |
| 기획 문서, 목업 만들기 | `workflow/aidlc` |
| RAG, GenAI 플랫폼 에이전트 | `workflow/genai-agent` |
| 스킬 만드는 법, 훅, 서브에이전트 문법 | `claude-code/advanced` → `lecture/extending` |

---

## 답변 규칙

- **출처 링크 필수.** 링크 없는 답변은 검증 불가하므로 하지 않는다.
- **문서에 없으면 없다고 한다.** 그럴듯하게 채우지 않는다. 없을 때는 콘솔 문의
  경로를 안내한다.
- 문서에 나온 명령어·경로·URL 은 **그대로 인용**한다. 기억으로 재구성하지 않는다.
- 사용자가 겪는 오류 메시지가 있으면 해당 문서의 `#troubleshooting` 섹션을 먼저 본다.
- `web-search` 결과 스니펫만 보고 답하지 않는다. 반드시 `WebFetch` 로 본문을 확인한다.
- **구 URL 을 쓰지 않는다.** `/docs/platform-guide` 같은 카테고리 없는 경로는 옛 구조다.
  meta-refresh stub 으로 넘어가긴 하지만, 위 지도의 새 경로를 직접 쓴다.
