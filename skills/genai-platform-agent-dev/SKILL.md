---
name: genai-platform-agent-dev
description: KB GenAI 플랫폼 기반 AI 에이전트를 개발한다. LLM Gateway, RAG API, Dockerfile 등 내부 플랫폼 규격을 준수하며, CLAUDE.md 의 이슈 → 워크트리 → PR 흐름으로 구현한다.
---

# Construction Skill

이 스킬은 완료된 기획을 기반으로 **실제 코드를 개발**하기 위한 스킬이다.
모든 작업은 **CLAUDE.md 의 작업 흐름 규칙**을 엄격히 준수한다.

반드시 **한국어**로 진행한다.

---

## 0. 사전 확인 (가장 먼저 실행)

### 0-0. Python 환경 확인

```bash
conda --version
```

- conda 없으면 사용자에게 설치 여부를 묻는다:
  ```
  conda 가 설치되어 있지 않습니다.
  Miniconda 를 자동으로 설치할까요? (Y/N)
  ```
  - Y: OS/아키텍처에 맞는 Miniconda 인스톨러를 다운로드해 설치하고 셸을 초기화한다.
    ```bash
    # Linux x86_64 예시
    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
    eval "$($HOME/miniconda3/bin/conda shell.bash hook)"
    conda init bash
    ```
  - N: 안내 메시지를 출력하고 중단:
    ```
    https://docs.anaconda.com/miniconda/ 에서 직접 설치 후 다시 진행해주세요.
    ```
- conda 있으면 사용할 Python 버전을 사용자에게 확인:
  ```
  Q. 사용할 Python 버전을 알려주세요. (기본값: 3.10)
  → 3.10 외 버전 선택 시: 내부망 Nexus 에 등록된 패키지가
    Python 3.10 기준이므로, pip install 실패 가능성이 있습니다.
  ```
- 확인된 버전으로 conda 환경 자동 생성 및 활성화:
  ```bash
  conda create -n <프로젝트명> python=<버전> --yes
  conda activate <프로젝트명>
  ```

### 0-1. 기획 문서 탐색

기획 문서가 어디 있는지 파악한다. 존재할 수 있는 위치 예시:
- `aidlc-docs/inception/` (AI-DLC 산출물)
- `planning/`, `docs/`, `spec/`, `PRD.md` 등 프로젝트별 위치

```bash
find . -maxdepth 3 -name "*.md" | grep -E "(prd|spec|requirement|design|planning|final-report)" | head -20
```

기획 문서를 로드한다. 문서가 없으면 사용자에게 요청:
```
기획 문서를 찾을 수 없습니다.
개발할 내용을 직접 설명해 주시거나, 문서 경로를 알려주세요.
```

### 0-2. 기존 이슈/진행 상황 확인

```bash
gh issue list --state all --limit 50
```

이미 생성된 이슈가 있으면 중복 생성하지 않는다.

---

## 1. 환경 설정 확인 (개발 시작 전 필수)

개발 계획 제시 전에 아래를 사용자에게 먼저 확인한다.

```
개발 환경을 확인합니다.

기본 환경은 다음과 같습니다:
- Python: 3.10
- 패키지: refs/requirements.md 의 버전 목록 참조

Q1. 선호하는 Python 버전이나 패키지 버전이 있으신가요?
    (없으면 위 기본값으로 진행합니다)

Q2. 내부 RAG(Retrieval) API 를 사용하나요?
    (사용하면 새 API를 만들지 않고 refs/rag-retriever.md 패턴으로 기존 API를 호출합니다)

Q3. Q2에서 사용한다고 답한 경우, 내부 Retrieval API에 접근할 수 없는
    로컬 환경용 Mock이 필요한가요?
    (필요하다고 답한 경우에만 refs/mock-retrieval.md 패턴을 추가합니다)
```

사용자가 기본값과 다른 버전을 요청하는 경우, 변경 유형에 따라 아래를 안내한다:

**패키지(requirements.txt) 변경 시:**
```
⚠️  기본 패키지 버전을 벗어나는 경우, 내부망 Nexus 에 미등록 상태일 수 있습니다.

개발은 진행할 수 있지만, 내부망 배포 전에:
1. 라이선스 검사 (GPL 계열 등 제한 라이선스 여부)
2. 취약점 검사 (pip-audit 또는 safety 로 CVE 스캔)
3. 검사 결과와 함께 플랫폼팀에 Nexus 등록 요청

이 절차가 완료되어야 내부망에서 pip install 이 가능합니다.
```

**base image(Dockerfile FROM) 변경 시:**
```
⚠️  base image 를 변경하는 경우, 내부 Harbor 에 미등록 상태일 수 있습니다.

개발은 진행할 수 있지만, 내부망 배포 전에:
1. 라이선스 검사 및 취약점 검사
2. 플랫폼팀에 Harbor 등록 요청 (STG / PRD 각각)

이 절차가 완료되어야 내부망에서 이미지 pull 이 가능합니다.
```

requirements.txt 또는 Dockerfile 에서 기본 규격을 벗어난 항목이 있을 때마다 위 안내를 반복한다.

---

## 2. 개발 계획 수립 및 사용자 확인

로드한 기획 문서를 기반으로 **개발 단위(이슈 후보)** 를 도출하고 사용자에게 제시한다.
**사용자 확인 없이 이슈 생성이나 코드 작성을 시작하지 않는다.**

```
## Construction 계획

### 대상
<기획 문서에서 파악한 프로젝트 한 줄 요약>

### 개발 단위 (이슈 후보)
각 이슈는 독립적으로 개발·리뷰 가능한 단위다.

| 순서 | 이슈 제목 | 설명 |
|---|---|---|
| 1 | <기능/컴포넌트 이름> | <한 줄 설명> |
| ... | | |

### 개발 순서 근거
<의존성 또는 우선순위 설명>

이 순서로 진행할까요? 순서 변경 또는 특정 이슈만 먼저 진행도 가능합니다.
```

---

## 2. 이슈별 개발 루프

사용자가 확인하면 각 이슈에 대해 아래 순서를 반복한다.

### Step 1 — GitHub 이슈 생성

```bash
gh issue create \
  --title "<기능/컴포넌트 이름> 구현" \
  --body "$(cat <<'EOF'
## 목표
<이슈 범위 1~3줄>

## 완료 조건
- [ ] <핵심 구현 항목>
- [ ] <테스트 작성>
EOF
)"
```

- **assignee 는 지정하지 않는다** (이슈 생성 시 빈 상태로 둠).
- 생성된 이슈 번호를 기록한다.

### Step 2 — 셀프어사인

```bash
gh issue edit <이슈번호> --add-assignee @me
```

### Step 3 — main 최신화 + 워크트리 생성

```bash
git pull origin main
```

**EnterWorktree 도구**로 워크트리를 생성한다:
- 브랜치 명: `feat/issue-<번호>-<기능-이름-kebab-case>`
- 예: `feat/issue-3-search-faq-tool`

### Step 4 — 코드 구현

기획 문서를 단일 진실(source of truth)로 사용한다.

**구현 시 준수 사항 (CLAUDE.md):**
- 프로젝트 파일은 현재 작업 디렉토리(프로젝트 루트)에 직접 생성한다. `main.py`, `requirements.txt`, `Dockerfile`, `Dockerfile.local`, `app/`, `tests/` 모두 루트에 위치. 별도 서브디렉토리(예: `weather-agent/`) 안에 중첩 금지.
- YAGNI: 불필요한 추상화·feature flag·backwards-compat shim 금지
- 기존 파일 편집 우선, 신규 파일 생성 최소화
- 에러 핸들링은 실제로 발생 가능한 경우만
- 코드 주석은 WHY 가 비자명한 경우에만 작성 (WHAT 설명 주석 금지)
- 시크릿은 환경변수로 처리 (하드코딩 금지)
- 절대경로 사용 (상대경로로 인한 오작동 방지)

**LLM 호출 규격 (LLM call 이 포함된 컴포넌트에만 적용):**

LLM 을 호출하는 코드를 작성하기 전에 아래 두 파일을 읽고, 사용할 플랫폼을 사용자에게 확인한다:

| 플랫폼 | 참조 파일 | 클라이언트 | 인증 |
|---|---|---|---|
| 내부 GenAI 플랫폼 (vLLM, OpenAI-compatible) | `refs/genai-platform.md` | `ChatOpenAI` | `kb-key` + `x-client-user` |
| LLM Gateway (LiteLLM, OpenAI-compatible) | `refs/llm-gateway.md` | `ChatOpenAI` / `OpenAI` SDK | `kb-key` + `x-client-user` |

```
.claude/skills/genai-platform-agent-dev/refs/genai-platform.md
.claude/skills/genai-platform-agent-dev/refs/llm-gateway.md
```

**환경변수 네이밍 규칙:**

| 용도 | 환경변수명 | 필수 여부 |
|---|---|---|
| LLM 엔드포인트 URL | `LLM_BASE_URL` | 필수 |
| LLM 인증 키 | `LLM_API_KEY` | 필수 |
| 사용 모델명 | `LLM_MODEL` | 필수 |

`ANTHROPIC_*`, `KB_KEY`, `GENAI_BASE_URL` 같은 SDK/플랫폼 특정 이름은 사용하지 않는다.
이유: 내부망 배포 시 플랫폼 교체 또는 설정 수정 때 혼동 방지.

**.env.example 필수 포함:**
```
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

**.env 생성 및 키 입력 안내:**

`.env.example` 을 복사해 `.env` 를 만들고, 사용자에게 키 발급 위치를 안내한 뒤 입력을 요청한다:

```bash
cp .env.example .env
```

```
.env 파일을 생성했습니다. 아래 항목을 채워주세요.

  LLM_BASE_URL
    · 내부 GenAI 플랫폼: 프로젝트 콘솔 → 작업환경 탭 → GenAI 플랫폼 카드에서 확인
    · LLM Gateway: http://litellm.aidc-prod.svc.cluster.local:4000

  LLM_API_KEY
    · 프로젝트 콘솔 → 작업환경 탭 → 해당 플랫폼 카드에서 발급 (sk-... 형태)

  LLM_MODEL
    · 사용할 모델 슬러그 (예: claude-sonnet-4-6)

값을 알려주시면 대신 입력해드립니다.
```

사용자가 값을 알려주면 `.env` 에 직접 입력한다. `.env` 는 `.gitignore` 에 추가한다:

```bash
echo ".env" >> .gitignore
```

두 플랫폼 모두 실제 호출 코드를 작성한다. 로컬에서 클러스터 내부 URL에 접근할 수 없으면
LLM 클라이언트를 주입 가능한 구조로 만들고 테스트에서 test double을 사용한다.
Retrieval Mock은 아래 RAG 규격에 따라 사용자가 요청한 경우에만 추가한다.

**Dockerfile 규격 (Dockerfile 작성 시 적용):**

Dockerfile 작성 전에 반드시 아래 파일을 읽고 그 규격을 따른다:

```
.claude/skills/genai-platform-agent-dev/refs/dockerfile.md
```

이 프로젝트는 내부망/외부망이 분리되어 있으므로 `Dockerfile` 과 `Dockerfile.local` 두 개를 항상 함께 작성한다.

**requirements.txt 규격 (의존성 작성 시 적용):**

```
.claude/skills/genai-platform-agent-dev/refs/requirements.md
```

기본 패키지 버전을 벗어나는 패키지를 추가할 경우 사용자에게 플랫폼팀 등록 절차를 안내한다 (1. 환경 설정 확인 섹션 참조).

**내부 RAG(Retrieval) API 규격 (1. 환경 설정 확인에서 사용 여부 확인 후 적용):**

사용자가 RAG API 사용을 확인한 경우에만, RAG 검색 코드를 작성하기 전에 아래 파일을 읽고 그 패턴을 따른다:

```
.claude/skills/genai-platform-agent-dev/refs/rag-retriever.md
```

사용자가 RAG API 불필요하다고 답한 경우 이 규격은 적용하지 않는다.

**로컬 Retrieval Mock 규격 (사용자가 필요하다고 답한 경우에만 적용):**

사용자가 내부 Retrieval API에 접근할 수 없는 로컬 환경에서 Mock이 필요하다고 명시한 경우에만
아래 파일을 읽고 `lib/mock_retrieval.py`를 추가한다:

```
.claude/skills/genai-platform-agent-dev/refs/mock-retrieval.md
```

사용자가 Mock이 불필요하다고 답했거나 RAG를 사용하지 않는 경우 Mock 파일과 샘플 데이터는 만들지 않는다.
내부 Retrieval API 인증(`x-openapi-token`, `x-generative-ai-client`)은
`refs/rag-retriever.md`를 따르며 GenAI 플랫폼 인증과 혼용하지 않는다.

### Step 5 — 테스트 작성

핵심 로직에 대한 테스트를 같은 워크트리 내에 작성한다.
테스트가 불가능한 경우(UI, 외부 시스템 연동 등) 이유를 명시한다.

### Step 5-b — 로컬 실행 및 수동 테스트 스크립트 생성

프로젝트 루트에 아래 두 파일을 생성한다.

**`run_local.sh`** — uvicorn 로컬 실행:

```bash
#!/bin/bash
cd "$(dirname "$0")"
CONDA_PYTHON="$HOME/miniconda3/envs/<프로젝트명>/bin/python"
$CONDA_PYTHON -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**`test_local.sh`** — `/health` + `/chat` curl 테스트 (pretty print):

```bash
#!/bin/bash
BASE_URL="http://localhost:8000"
PYTHON="$HOME/miniconda3/envs/<프로젝트명>/bin/python"

echo "[health]"
curl -s "$BASE_URL/health" | $PYTHON -m json.tool
echo ""

echo "[chat]"
# input_value 내부 JSON 구조는 프로젝트별로 다름 — \"message\" 값을 실제 테스트 메시지로 교체
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d '{"input_value": "{\"message\": \"테스트 메시지\", \"x_client_user\": \"test-user\"}", "message_hists": null}' \
  --no-buffer | $PYTHON -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get('event') == 'CHUNK':
            print(d.get('content', ''), end='', flush=True)
    except:
        pass
print()
"
```

생성 후 실행 권한 부여:

```bash
chmod +x run_local.sh test_local.sh
```

### Step 6 — commit/push/PR

워크트리(main 이외 브랜치)이므로 **자동 진행** (CLAUDE.md 허용):

```bash
git add <변경된 파일들>
git commit -m "feat: <기능 이름> 구현 (#<이슈번호>)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
git push -u origin feat/issue-<번호>-<기능-이름>
gh pr create \
  --base main \
  --title "feat: <기능 이름> 구현" \
  --body "$(cat <<'EOF'
## 변경 내용
<구현 내용 요약>

Closes #<이슈번호>
EOF
)"
```

PR body 에 반드시 `Closes #<이슈번호>` 를 명시한다.

### Step 7 — 워크트리 정리 (PR 머지 후)

PR 머지 확인 후:
```bash
git worktree remove .claude/worktrees/<워크트리명>
git worktree prune
```

---

## 3. 전체 완료 보고

모든 이슈의 PR 이 머지되면 사용자에게 보고한다:

```
## Construction 완료

| 이슈 | 기능 | PR | 상태 |
|---|---|---|---|
| #N | <이름> | #PR | ✅ merged |
| ... | | | |

모든 구현이 완료되었습니다.
```

---

## 금지 사항

- `main` 브랜치에서 직접 코드 수정 금지
- 이슈 없이 브랜치·워크트리 생성 금지
- assignee 가 이미 있는 이슈 작업 금지 (다른 작업자가 진행 중)
- 사용자 확인 전 이슈 생성·코드 작성 시작 금지
- 워크트리 바깥(main 레포 원본)을 수정한 경우 즉시 보고 후 원상복구
