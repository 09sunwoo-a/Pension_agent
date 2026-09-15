"""Langfuse 관측(트레이싱) — LLM 호출을 대시보드에서 되짚을 수 있게 남긴다.

무엇을 위한 것인가. 이 저장소는 한 번의 브리핑에 LLM 을 11번, 대화 한 턴에 4~7번
부른다. 답이 이상하게 나왔을 때 **어느 호출이 무엇을 받고 무엇을 뱉었는지**를 화면
로그로 되짚는 것은 사실상 불가능하다. Langfuse 는 그 호출들을 트레이스 하나(= 브리핑
한 건 · 대화 한 턴) 아래 묶어 보여준다.

    from pension_agent import observability as obs

    with obs.trace("consult.turn", input=question, session_id=sid) as tr:
        ...                       # 이 안에서 난 llm.generate 는 전부 이 트레이스 밑에 붙는다
        tr.update(output=answer)

━━ 설계 ━━

**표준 라이브러리만 쓴다.** langfuse SDK 를 넣지 않는 이유는 사내 genai 경로가
urllib 만 쓰는 것과 같다(`llm.py`) — 망분리 환경으로 코드를 들여올 때 설치할 것이
늘지 않아야 한다. Langfuse 의 수집 API(`POST /api/public/ingestion`)는 Basic 인증 +
JSON 배치 한 종류라 SDK 없이 부를 수 있다.

**에이전트를 절대 세우지 않는다.** 관측은 부산물이지 기능이 아니다. 그래서

  - 전송은 백그라운드 워커 스레드가 한다. 호출부는 큐에 넣고 즉시 돌아온다.
  - 큐가 차면 **새 이벤트를 버린다**(호출부를 막지 않는다). 버린 수는 `stats()` 에 남는다.
  - 전송 실패·설정 오류·직렬화 실패는 전부 이 모듈 안에서 삼킨다. 밖으로 예외가 나가지
    않는다 — 관측이 죽어서 상담이 죽는 일은 없어야 한다.

**키가 없으면 통째로 꺼진다.** `enabled()` 가 False 면 `trace()` 는 아무것도 하지 않는
핸들을 주고 `record_generation()` 은 즉시 돌아온다. 테스트·시연은 키 없이 그대로 돈다.

━━ 환경변수 ━━
  LANGFUSE_PUBLIC_KEY   pk-lf-... (없으면 관측 꺼짐)
  LANGFUSE_SECRET_KEY   sk-lf-... (없으면 관측 꺼짐)
  LANGFUSE_HOST         기본 https://cloud.langfuse.com. 자체 호스팅이면 그 주소
  LANGFUSE_ENABLED      0/false 로 두면 키가 있어도 끈다
  LANGFUSE_RELEASE      버전 태그(선택). 배포본 구분용
  LANGFUSE_ENVIRONMENT  기본 demo. 시연/스테이징/운영 구분용
  LANGFUSE_CAPTURE_CONTENT  기본 1. 0 이면 프롬프트·응답 본문을 보내지 않고 길이만 남긴다
  LANGFUSE_MAX_CHARS    본문 한 건의 상한. 기본 20000자. 넘으면 잘라 «…(N자 생략)» 표시
  LANGFUSE_TIMEOUT      전송 타임아웃 초. 기본 10

**개인정보 주의.** 프롬프트에는 고객 원장(이름·잔액·상품)이 실려 있다. 지금 고객은
시연용 목업이라 그대로 보내지만(루트 CLAUDE.md), 실데이터로 바꾸는 순간 이 값이 외부
SaaS 로 나간다. 그때 정해야 할 것은 `docs/PRODUCTION_RISKS.md` §9 에 적혀 있다 —
자체 호스팅으로 돌리거나 `LANGFUSE_CAPTURE_CONTENT=0` 으로 본문을 끊는다.
"""

from __future__ import annotations

# 패키지 밖에서 부르는 이름만 재노출한다 — 상수(MAX_QUEUE 등)와 내부 함수는 _conf·_trace·_transport 가 갖는다.
from pension_agent.observability._conf import (  # noqa: F401
    conf,
    enabled,
    reset,
)
from pension_agent.observability._trace import (  # noqa: F401
    request_id,
    current_request_id,
    Trace,
    trace,
    current_trace_id,
    tag,
    customer_ref,
    span,
    score,
    record_generation,
)
from pension_agent.observability._transport import (  # noqa: F401
    stats,
    last_error,
    flush,
)
