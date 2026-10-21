"""Langfuse 관측(트레이싱) — LLM 호출을 트레이스 하나(브리핑 한 건·대화 한 턴) 아래 묶어 기록.

    from pension_agent import observability as obs

    with obs.trace("consult.turn", input=question, session_id=sid) as tr:
        ...                       # 이 안에서 난 llm.generate 는 전부 이 트레이스 밑에 붙는다
        tr.update(output=answer)

- 표준 라이브러리만 사용(SDK 미설치). `POST /api/public/ingestion` 에 Basic 인증 + JSON 배치
- 백그라운드 워커 전송. 큐가 차면 새 이벤트 버림(`stats()`). 모든 예외는 내부에서 삼킴
- 키 없으면 전부 no-op. `step()` 단계 로그(stdout)는 키와 무관하게 출력
- 프롬프트에 고객 원장 포함 — 실데이터 전환 시 자체 호스팅 또는 `LANGFUSE_CAPTURE_CONTENT=0`

환경변수:
  LANGFUSE_PUBLIC_KEY   pk-lf-... (없으면 관측 꺼짐)
  LANGFUSE_SECRET_KEY   sk-lf-... (없으면 관측 꺼짐)
  LANGFUSE_HOST         기본 https://cloud.langfuse.com. 자체 호스팅이면 그 주소
  LANGFUSE_ENABLED      0/false 면 키가 있어도 꺼짐
  LANGFUSE_RELEASE      버전 태그(선택). 배포본 구분용
  LANGFUSE_ENVIRONMENT  기본 demo. 시연/스테이징/운영 구분용
  LANGFUSE_CAPTURE_CONTENT  기본 1. 0 이면 본문 대신 길이만
  LANGFUSE_MAX_CHARS    본문 한 건의 상한. 기본 20000자. 넘으면 잘라 «…(N자 생략)» 표시
  LANGFUSE_TIMEOUT      전송 타임아웃 초. 기본 10
"""

from __future__ import annotations

# 패키지 밖에서 쓰는 이름만 재노출
from pension_agent.common.observability._conf import (  # noqa: F401
    conf,
    enabled,
    reset,
)
from pension_agent.common.observability._trace import (  # noqa: F401
    request_id,
    current_request_id,
    journal,
    wall_clock,
    tally_llm,
    llm_tally,
    Trace,
    trace,
    current_trace_id,
    tag,
    customer_ref,
    span,
    score,
    step,
    STEP_KEYS,
    STEP_VALUES,
    STEP_TEXT_MAX,
    record_generation,
)
from pension_agent.common.observability._transport import (  # noqa: F401
    stats,
    last_error,
    flush,
)
