# demo/ — 현재 Demo HTML에서 역추출한 Demo Product / Case Specification

## Purpose

`demo/` 는 현재 발표용 Demo HTML(`퇴직연금 AI 대시보드 (standalone)`, 업로드 파일명
`76352690-_____AI______standalone.html`)을 분석해 **지금 실제로 구현된 Demo 서비스의 모습과
고객 Case 3종**을 사람이 읽을 수 있는 Markdown으로 옮긴 것이다.

이 폴더는 HTML을 **Source of Truth** 로 본다. HTML에 실제로 있는 값·문구·흐름을 그대로 기록하고,
기존 설계나 일반 금융지식과 다르더라도 **고치지 않고 `TO REVIEW` 로 남긴다**.

| 파일 | 역할 |
|---|---|
| `DEMO_TARGET_SPEC.md` | HTML 전체를 기준으로 서비스가 지향하는 경험(화면·정보모델·Brief 구조·Chat·업무화면) 정리 |
| `HTML_EXTRACTION_AUDIT.md` | 추출 범위, 발견한 고객 전수, HTML 내부 충돌, HTML↔Repo 차이, 사람 검토 항목 |
| `seed_cases/DEMO-01_KIM_SEOYEON.md` | 김서연 — 타행 ISA 만기 D-3 · ETF 조회 Case |
| `seed_cases/DEMO-02_LEE_SUMIN.md` | 이수민 — IRP 정기예금 만기 D-22 · 현금성 대기 · DO 미등록 Case |
| `seed_cases/DEMO-03_PARK_JEONGHO.md` | 박정호 — 퇴직급여 일반통장 수령 · 과세이연 60일 시한 Case |

## Authority

새로운 시연 Case를 설계할 때 참조 우선순위:

```text
demo/
→ 현재 Demo Target / UX / Case Story 기준

sources + knowledge
→ Case를 뒷받침하는 실제 기반지식
   (이 저장소에서는 01~06 원문 폴더 · src/pension_agent/knowledge/ 가 이에 해당)

references
→ 새로운 Case 아이디어 발굴
   (이 저장소에서는 03_스타런_영업점_Hottip · 08_인사이트 · IRP_세미나이벤트_DB_v1.md 등)

golden + cases
→ 판단 다양성 / Failure Pattern 참고
   (이 저장소에서는 src/scripts/demo_cases.json · IRP_Agent_더미고객_9Cases_v3.xlsx ·
    src/pension_agent/strategy_agent/data/outreach_golden.json 등)

design
→ 필요할 때만 Architecture 참고
   (이 저장소에서는 docs/REQUIREMENTS.md · 07_에이전트_기능정의 · src/pension_agent/consult_agent/CLAUDE.md)
```

> 참고: 작업 지시서는 `design/`, `golden/`, `cases/`, `sources/`, `knowledge/`, `references/` 폴더를
> 전제했으나 이 저장소(`09sunwoo-a/pension_agent`)에는 그 이름의 최상위 폴더가 없다. 위 괄호 안이
> 이 저장소에서의 대응 위치다. 이번 작업에서 그 폴더들은 **읽기만** 했고 수정하지 않았다.

## Important Rule

`demo/` 는 금융제도나 상품 Fact의 공식 Source가 **아니다**.

- Demo Story(어떤 고객에게 어떤 화면·Brief·상담 흐름을 보여주는가)의 Source of Truth다.
- 세제 수치·상품·업무 절차 같은 업무지식의 Grounding Source는 `01~06` 원문 폴더와
  `src/pension_agent/knowledge/` 를 써야 한다.
- HTML 값이 기반지식과 다른 곳은 각 문서의 `TO REVIEW` / Audit 항목에 있다. 여기서 값을 고치지 않았다.

## 표기 규약

- **HTML AS-IS** — HTML(JS 객체·마크업)에 실제 존재하는 값. 가능하면 위치(`DATA.ksy.hold`,
  `BRIEFS.ksy.s2` 등)를 함께 적는다.
- **Case Intent** — HTML 요소를 종합한 기획적 해석. Fact가 아니다.
- **TO REVIEW** — HTML 내부 충돌, 기반지식과의 정합성 확인 필요, 사람이 결정할 사항.

## 이 HTML의 구조 (읽는 사람을 위한 메모)

HTML은 번들러 포맷이다. `<script type="__bundler/template">` 안의 React 앱(`class Component extends
DCLogic`) 하나에 모든 데이터가 하드코딩돼 있다. 데이터 계층은 다음과 같다.

| 객체 | 내용 | 화면 렌더 여부 |
|---|---|---|
| `DATA` (+`OVR`/`EXT`/`EXT2`/`SCR`/`RC`) | 대시보드 고객 18명의 기본정보·보유상품·(구형) headline/metric/act/script | 기본정보·보유상품은 렌더. `head`/`metrics`/`act`/`why`/`tags`/`ai`/`pick`/`docs`/`cmp`/`refs` 등은 **현재 마크업에 바인딩되지 않은 dormant 데이터** |
| `QMETA` | 대시보드 리스트 행(스타클럽·관리단계·신호 태그·잔액·수익률) | 렌더 |
| `profileOf()` `FIX` | 3명 고정 프로필(나이·성별·등급·DO·수익률·계좌신규일·최근상품) — 나머지는 해시로 생성 | 렌더 |
| `BRIEFS` | 3명의 AI 브리핑 S1~S5 | 렌더 (`hasAiBrief`) |
| `QA` | 3명의 실시간 상담 챗(추천질문·답변·근거·CTA) | 렌더 (`agentOn`) |
| `SCR`/`COMMON`/`GEN` | 15명(대시보드 전용)의 «대응 가이드» 시뮬레이션 챗 | 렌더 (`agentOff`) |
| `RECO`/`FUNDS` | 디폴트옵션 4종·펀드 17종 카탈로그 | **현재 마크업에 바인딩되지 않음** |
| `BRIEF_SEGS` | 코스피 하락 브리핑 문구 | **미사용** — 화면의 부점 브리핑은 마크업 고정 텍스트 |
