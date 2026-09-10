# IRP 상담 대화 에이전트 (LangGraph)

직원이 자연어로 묻는 것을 받아 처리하는 에이전트. 화법 검색을 넘어 지식 질의응답·고객
브리핑 질의·화면 연계·상담이력 기록·브리핑 수정 요청·쪽지 발송까지 다룬다.

**무엇을 할 수 있는지는 도구 목록(`tools/__init__.py::TOOLS`)이 정한다.** 새 종류의 재료로
답하게 하려면 도구 하나를 추가한다 — 의도 enum·분기표·노드를 함께 늘리지 않는다. 있어야 할
동작의 기준은 이 폴더의 [CLAUDE.md](CLAUDE.md) 이고, 구현이 그 문서와 어긋나면 구현이 틀린
것이다. 이 문서는 **사용법과 현재 코드 구조**만 적는다.

## 사용

```python
from pension_agent.consult_agent import ask

r = ask("사업자 고객인데 수수료 부담된다고 하시네요. 뭐라고 답변하죠?")
r["answer"]    # 핵심 포인트 1문장 + 근거 2~3문장, 직원에게 코칭하는 해요체
r["sources"]   # [{"id": "pitch.k03.001", "title": "...", "score": 4.80}]

r2 = ask("그럼 안 된다고 하면요?", history=r["history"])   # 후속 질문 — 이전 맥락 이어받음

# 브리핑질의·LMS발송·수정은 customer_id 가 필요하다(현재 열려 있는 브리핑 화면의 고객).
# 넘기면 모든 턴이 src/session_data 에 상담이력으로도 함께 기록된다(REQUIREMENTS.md §14).
r3 = ask("이 고객 만기 언제야?", customer_id="198734-1205842", session_id="branch-101-2026-08-24")
```

`customer_id` 가 없으면 고객 관련 기능(브리핑 질의·수정·화면 연계)은 "고객 화면을 먼저
열어주세요"라고 답한다. 지식 질의응답과 화법 코칭은 고객 화면 없이도 답한다.

CLI · 디버그 · 대본 실행은 [../../README.md](../../README.md) §3 에 있다(`source ./cli.sh`
→ `$CA` · `$CAD` · `$CADR`). 시연용 고객의 id 와 상태는 코드가 찍어준다:

```bash
python -m pension_agent.strategy_agent.situations   # 고객별 문제상황 · KB-PIN
python -m tests.test_consult_agent                  # 라우팅·도구 루프·가드 (LLM 키 불필요)
python -m pension_agent.knowledge.kb                # 지식베이스 점검 리포트 — ERROR 0건 유지
```

## 파일 구조

```
consult_agent/
├── CLAUDE.md           대화형 기준서 — 있어야 할 동작과 구현 gap 목록. 구현과 어긋나면 문서가 기준
├── graph.py            그래프 조립 · ask() (모든 턴을 session_store 에 기록)
├── __main__.py         REPL — python -m pension_agent.consult_agent -c <KB-PIN>
├── state.py            AgentState/Turn · 대화이력 포맷 · 공용 지식베이스(KB)
├── routing.py          INTENTS · 모든 분기(route_*) predicate — 상태만 보고 다음 노드를 고른다
├── kb.py               LLM 카드 선택용 계층 인덱스(버킷) · 프롬프트 컨텍스트 (적재·검색은 ../knowledge/kb.py)
├── tools/              도구 패키지 — __init__ 레지스트리(능력 표면) · base 근거(Evidence) 규약 · ledger 원장 helper
│                       · 도구별 모듈(cards·market·briefing·history·pitch·playbook·suitability·outreach·targets·dates·tax_credit)
├── select.py           카드 선택 — LLM 버킷→카드 2단, LLM 이 0건일 때만 n-gram (종류 무관)
├── guard.py            「하지 말 것」 — 지식베이스에 있는 금지 문장만 띄운다
├── marks.py            재료 성격 표시 — 신뢰 등급 · 내부용 주의 (§7)
├── screens.py          화면 연계 — mystar-link:// 딥링크 조립 · 발송 화면번호 조회 (§10). 화면번호는 KB 가 갖는다
├── relations.py        관계 기반 점검 — 값–조건 오짝 · 알려진 오답 대조 (§6)
├── memo.py             WorkB 쪽지 초안 — 무엇을 쓸지 (꼴과 발송은 ../workb.py) (§10)
├── suggest.py          추천 질문 칩 — 지난 상담 · 열린 세미나가 있는 고객에게만
├── render.py           답변 + 출처 블록을 텍스트 한 덩어리로 — CLI 와 main.py 가 함께 쓴다
├── prompts.py          LLM 프롬프트 템플릿 (기능별 섹션으로 구분)
├── progress.py         진행 표시 — 답변이 만들어지는 동안 무엇을 하는 중인지 (문구는 코드 소유)
└── nodes/
    ├── understand.py       의도분류 (도메인 어휘 없는 라우팅 전용, 실패하면 답하지 않는다)
    ├── pitch.py            화법 검색 전용 슬롯 분해 — 화법 도구가 필요할 때 부른다
    ├── plan.py             계획 루프 — plan_step(도구 선택·실행) / compose(결합·검증) / llm_down
    ├── answer.py           형태 판정과 답변 작성을 동시에 돌리고 하나를 고른다
    ├── clarify.py          형태 판정 — 답한다·전제를 밝힌다·되묻는다·없다 (§5)
    ├── meta.py             메타 질문("뭘 도와줄 수 있어?") 응답 노드
    ├── lms.py              LMS 화면 연계 요청 — 보내지 않고 발송 화면을 제안한다
    ├── correction.py       브리핑 수정 요청 — 편집 가능 필드만, 이번 범위는 감사로그까지
    ├── facts_qa.py         제도·상품 확정값 — fact 도구의 검색·근거 블록 조립 (노드 아님)
    ├── procedure_qa.py     업무 처리 절차 — procedure 도구의 검색·근거 블록 조립 (노드 아님)
    ├── segment_qa.py       고객군 정의 — segment 도구의 검색·근거 블록 조립 (노드 아님)
    └── act.py              화면 연계 제안(offer)·확인 연계(confirm_action)
```

지식 카드는 이 폴더가 아니라 `../knowledge/data/` 에 있다 — strategy_agent 도 함께 읽는
공용 자산이라 한쪽 에이전트가 소유하지 않는다(`kb_*.json` 은 `scripts/kb_build` 생성물,
루트 `CLAUDE.md` 절대 규칙 3). 목록과 건수는 `python -m pension_agent.knowledge.kb` 가 찍는다.

## 그래프 구조

<!-- generated:architecture:start — python -m scripts.render_architecture 가 갱신한다. 손으로 고치지 않는다 -->
```mermaid
%%{init: {"flowchart": {"wrappingWidth": 800, "nodeSpacing": 35, "rankSpacing": 80}}}%%
flowchart TD
    __start__([START])
    understand["understand — 질문 이해<br/>무엇을 원하는 질문인지 가려 보낸다"]
    agent_help["agent_help — 기능 안내<br/>무엇을 도와줄 수 있는지 답한다"]
    plan["plan — 근거 수집 루프<br/>질문에 필요한 자료를 도구로 찾아 모은다"]
    compose["compose — 답변 작성<br/>모은 근거 안에서만 답을 쓰고,<br/>질문이 모호하면 선택지를 되묻는다"]
    lms_link["lms_link — LMS 발송 화면 연계<br/>요청받은 문구로 발송 화면 열기를 제안한다"]
    correction["correction — 브리핑 수정<br/>화면의 AI 작성 문구를 고친다"]
    llm_down["llm_down — 장애 안내<br/>LLM 연결이 안 되면 답 대신 상태를 알린다"]
    confirm_action["confirm_action — 제안 실행<br/>직전 턴에 제안한 화면 연계를 승낙받아 실행한다"]
    offer["offer — 화면 연계 제안<br/>답변과 이어지는 업무 화면을 열지 묻는다"]
    __end__([END])
    __start__ --> understand
    compose -.-> __end__
    compose -.-> offer
    confirm_action -.-> __end__
    confirm_action -.-> compose
    correction -.-> __end__
    correction -.-> plan
    plan -.-> compose
    plan -.-> plan
    understand -.-> agent_help
    understand -.-> confirm_action
    understand -.-> correction
    understand -.-> llm_down
    understand -.-> lms_link
    understand -.-> plan
    agent_help --> __end__
    llm_down --> __end__
    lms_link --> __end__
    offer --> __end__
    tools[["자료 도구 20종 — 답변의 근거는 모두 이 도구로 조회한다<br/>지식베이스: 상담 화법 · 제도·상품 수치 · 업무 처리 절차 · 단말 화면번호<br/>&nbsp;&nbsp;&nbsp;&nbsp;비대면 채널 경로 · 고객군 정의 · 관리 방법론 · 영업점 현장 관찰<br/>&nbsp;&nbsp;&nbsp;&nbsp;시황 자료 · 운용 상품 자료<br/>현재 고객: 고객 브리핑 자료 · 적합성 범위 · 지난 상담 기록 · 이번 상담 대화 기록<br/>&nbsp;&nbsp;&nbsp;&nbsp;이 고객 상태에 걸린 참고자료 · 안내할 이벤트·세미나<br/>오늘의 목록: 오늘의 타겟 고객 목록<br/>계산: 세액공제 환급액 · 오늘 날짜·기한<br/>이번 대화: 이전 답변"]]
    plan -. "필요한 자료를 골라 조회" .-> tools
    gates[["답변 점검 — 근거를 벗어난 답변은 화면에 내보내지 않는다<br/>① 근거에 없는 숫자·상품명 → 내보내지 않음<br/>② 값과 조건을 잘못 짝지은 문장 → 내보내지 않음<br/>③ 빠진 필수 안내 문구·원문 인용 → 보완해서 내보냄"]]
    compose -. "내보내기 전 검사" .-> gates
```

실선은 고정된 흐름, 점선은 질문에 따라 갈리는 분기다. 자료 도구와 답변 점검 상자는
LangGraph 노드가 아니라 근거 수집·답변 작성 **안**에서 도는 것을 꺼내 그린 것이다 —
`get_graph()` 출력에 도구가 보이지 않는 이유가 그것이다. 어떤 도구가 있는지와 답변을
내보낼지는 코드가 정하고, 이번 질문에 무엇을 쓸지는 LLM 이 정한다(루트 CLAUDE.md 규칙 2).
코드 대응: 도구 레지스트리 `tools.TOOLS` · 점검 `verify_texts`/`relations`/`span` ·
분기 `routing.py`.
<!-- generated:architecture:end -->

| 노드 | 하는 일 | LLM |
|---|---|:---:|
| `understand` | 질문(+이전 대화) → `intent`·`utterance`만 판단(도메인 어휘 없는 라우팅 전용) | ○ |
| `agent_help` | "뭘 도와줄 수 있어?" 같은 메타 질문에 KB 메타데이터로 안내 | ✕ |
| `plan` | 다음에 부를 도구 하나를 고르고 실행해 원장에 쌓음. 상한은 `plan.MAX_STEPS` | ○ |
| `answer` | 형태 판정(`clarify`)과 답변 작성(`compose`)을 **동시에** 돌리고 하나를 고름. `assume`·`none` 이면 블록을 얹어 한 번 다시 씀 | ○×2 |
| ├ `clarify` | 답의 형태를 넷으로 판정 — 답한다 / 전제를 밝히고 답한다 / 되묻는다 / 핵심 대상이 없다 | ○ |
| └ `compose` | 원장만으로 답변 하나를 씀 → 원장 밖 수치·원문 스팬을 코드가 집행. 원장 0건이면 정직하게 없다고 답변 | ○ |
| `llm_down` | LLM 이 죽어 분류조차 못 한 턴 — "LLM 연결이 안 되어 있다"고 원인과 함께 답변 | ✕ |
| `lms_link` | 인용부호로 명시된 문구로 **발송 화면 연계를 제안**(보내지 않는다) | ✕ |
| `correction` | 수정 요청을 편집 가능 필드로 분류 → 편집 가능하면 재작성+검증, 아니면 거절. 「화면 문장이 아니라 방금 한 답변을 고쳐 달라는 것」이면 답을 내지 않고 `plan` 으로 넘긴다(`last_answer` 도구가 그 답변을 재료로 싣는다) | ○ |
| `offer` | 답변이 가리키는 화면이 있으면 "연계해드릴까요?" 를 덧붙이고 `pending_action` 설정 | ✕ |
| `confirm_action` | 직전 **한 턴**의 제안에 대한 "네"/"아니오" → 화면 URL 연계 또는 철회 | ✕ |

`intent` 는 `situation`(기본값 — 지식·고객 재료로 답하는 질문 전부) / `guide`(직원 업무
기준 질문, 응답 톤만 다름) / `agent_help` / `lms_link` / `correction` / `confirm_action` /
`llm_down` 이다. 목록 밖 값은 기본값으로 떨어진다 — 분류가 어긋나도 능력이 잘리지 않는다.
값·절차·고객군·브리핑 질의에 전용 노드는 없고 전부 계획 루프가 답한다(`CLAUDE.md` §3·§11).

## 튜닝 포인트

| 위치 | 값 | 의미 |
|---|---|---|
| `tools/pitch.py` `PITCH_TOP_K` | 3 | 프롬프트에 넣을 화법 카드 수. 늘리면 맥락↑ 토큰↑ |
| `knowledge/kb.py` `MIN_TOPICAL` | 0.5 | 낮추면 n-gram 폴백이 줄고 오답이 늘어남 (실측: 유관 0.55~2.1 / 무관 0.00~0.42) |
| `state.py` `HISTORY_LIMIT` | 12 | 프롬프트에 싣고 다음 턴에 넘기는 최근 턴 수. `last_answer` 가 되짚을 수 있는 범위이기도 하다. 12턴 블록이 455자다 |
| `state.py` `HISTORY_VERBATIM` / `HISTORY_OLD_CHARS` | 4 / 40 | 최근 4턴은 질문 원문, 그 앞은 40자에서 접는다(프롬프트 한 줄만 — 기록은 안 자른다) |

## 주의

원본 PDF 는 당행 영업전략·타사대응 노하우가 포함된 대외비 자료다. `../knowledge/data/` 에 그
내용이 그대로 들어 있으므로 저장소 접근권한을 통제한다.
