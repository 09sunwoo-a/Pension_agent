"""행내 GenAI 플랫폼용 HTTP 진입점 — FastAPI.

플랫폼이 요구하는 I/O 스키마는 **고정**이라 여기서 임의로 바꾸지 않는다
(skills/genai-platform-agent-dev/refs/genai-platform.md «API I/O 스키마 (고정)»):

    POST /chat   {"input_value": "<JSON 문자열>", "message_hists": null}
      → text/event-stream, 이벤트마다 {"event": "CHUNK", "content": "..."}

━━ 응답 프레이밍 — 문서와 게이트웨이가 어긋난다 (2026-09-09 행내 실측) ━━
위 문서는 «줄마다 JSON» 이라고 적었지만, 게이트웨이(/openapi/agent-chat/v1/agent-messages)
를 거쳐 부르면 그 형식은 읽히지 않았다:

  isStream=true  → 게이트웨이가 content "" · status SUCCESS 인 이벤트 하나만 돌려준다.
                   SSE 파서는 `data:` 로 시작하지 않는 줄을 버리므로, 우리 줄이 전부
                   무시된 것과 정확히 같은 결과다(추정 — 파서 코드는 못 봤다).
  isStream=false → status ERROR · responseCode R40000 · content 에
                   "[Errno Extra data] {우리 응답 원문} : 92" — 게이트웨이가 응답 본문
                   전체를 json.loads 했고 첫 줄(92자) 뒤에서 죽었다(확정 — 오류 문구가
                   파이썬 JSONDecodeError 그대로다). 비스트림은 **JSON 하나**를 기대한다.

그래서 스트림은 SSE 프레임(`data: {...}\n\n`)으로 내보내고(CHAT_SSE_FRAMING=0 이면 문서
형식으로 되돌린다), 호출이 비스트림임을 알 수 있으면 JSON 하나로 답한다. 게이트웨이가
비스트림을 어떻게 알려오는지는 아직 못 봤다 — 그래서 요청마다 헤더·키를 로그에 찍는다
(아래 «로그»). 지금 보는 신호: Accept 가 application/json 뿐이거나, 본문·input_value 에
stream/isStream/is_stream 이 false 로 있으면 비스트림.

`input_value` 는 **JSON 을 문자열로 직렬화한 것**이다. 이 프로젝트가 그 안에서 읽는 키:

    message         (필수) 직원이 입력한 질문
    x_client_user   (필수) 호출한 직원 식별자. 플랫폼의 감사 기록이자 쿼터 버킷이다.
                           **사번 7자리로 시작하면** 그것이 쪽지의 받는 사람·보내는
                           사람이 된다(뒤에 접미가 붙어도 구분자로 이었으면 읽는다 —
                           `3902172-550e8400-…`). 아래 employee_id
    customer_id     (선택) 지금 열려 있는 브리핑 화면의 고객 id. 고객 관련 기능은
                           이것이 있어야 성립한다 — 없으면 에이전트가 그렇게 답한다.
                           **`b64:` + base64(원장 표기)로 보낸다**(`b64:MTcxMjAzLTQ4MTUwNjI=`).
                           원장 표기(`171203-4815062`)는 주민등록번호와 같은 꼴이라 플랫폼
                           게이트웨이의 «기본필터»가 그 요청을 `FILTER_INVALID` 로 끊는다.
                           원장 표기·하이픈 없는 13자리·b64 셋 다 받아 원장 표기로
                           되돌린다(`strategy_agent/customer.py::normalize_id`)
    session_id      (선택) 상담 세션 구분자. 없으면 "default". 같은 값으로 이어 보내면
                           이전 턴의 맥락이 이어진다(아래 «대화 맥락»)
    employee_id     (선택) 로그인한 직원의 **WorkB 사번**. 쪽지의 기본 수신자이자 발송
                           주체이고 상담이력에 «누가 상담했나»로 남는다. `x_client_user`
                           가 사번으로 시작하면 넘길 필요가 없다
                           (`pension_agent/note.py::as_emp_no`). 사번을 다른 데서
                           받아오거나 그 값의 꼴이 다른 배포를 위한 자리이고, 여기 실은
                           값은 꼴을 검사하지 않고 그대로 쓴다. 사번을 하나도 못 읽으면
                           `WORKB_EMP_NO` 환경변수로 떨어지고, 그것도 없으면 쪽지 발송을
                           제안하지 않는다

━━ 출력 형식 — CHUNK 의 content 는 JSON 이벤트 하나다 ━━
프론트가 답변·근거·진행·추천질문을 **다른 자리에** 그려야 하는데 플랫폼 스키마는 CHUNK 텍스트
하나뿐이다. 그래서 content 에 JSON 객체 문자열을 싣고 `type` 으로 가른다(플랫폼 자체 채팅창은
JSON 원문을 보게 되므로 포기했다 — 2026-09-09 결정). 한 턴의 순서:

    {"type": "progress",  "text": "질문 내용을 파악하고 있어요"}            0개 이상 · 답변 전에
    {"type": "answer",    "text": "<본문>", "intent": "situation",
                          "links": [{"screen","url","label"}]}             1개 · links 는 항상(없으면 [])
    {"type": "action",    "kind", "label", "prompt", ...}                  연계 제안 턴에만 — 네/아니오 버튼용.
                                                                          본문 끝의 제안 문장은 그대로 둔다
    {"type": "clarify",   "question": "...", "options": ["..."]}           되묻기 턴에만 — 선택지 버튼용
    {"type": "sources",   "items": [{"id","doc","title","url","score","page","role"}]}
                                                                          항상 · 0건이면 [] (근거 없음을 화면이 말해야 한다)
    {"type": "followups", "items": ["..."]}                                항상 · 없으면 []
    {"type": "done"}                                                       항상 마지막
    {"type": "error",     "text": "LLMError: ..."}                         실패 시 answer 대신 · 그 뒤 done
    {"type": "log",       "level", "logger", "text"}                        로그 이벤트를 켰을 때만 · 아무 자리

answer.links 는 **본문이 인용한 단말 화면의 딥링크**다(`mystar-link://scnNo=…&mode=…`).
프론트는 본문에서 `screen` 문자열(「04-12-642」)을 찾아 `url` 로 누를 수 있게 감싼다 — URL 을
프론트가 조립하지 않는 이유는 `mode`(운영·개발)와 `scnNo` 자릿수 판정이 백엔드에만 있어야
하기 때문이다(consult_agent/effects/screens.py). 커스텀 스킴이라 프론트의 링크 sanitizer 가
href 를 지울 수 있다 — 스킴을 허용 목록에 넣어야 한다.

answer.text 에서 추천질문 블록(graph.FOLLOWUP_HEADER)은 뗀다 — followups 로만 간다. 연계 제안
문장(«… 보여드릴까요? / 연계해드릴까요? (네 / 아니오)»)은 답변의 마지막 문장으로 남긴다 — 직원이
「네」로 답하는 대화 경로가 그 문장을 전제로 한다. 프론트는 action 이벤트로 버튼만 그린다.
그 버튼과 문구는 **답변 본문 바로 아래**에 서야 한다 — 출처 블록 안에 들어가면 직원에게 하는
질문이 근거 표시의 일부로 읽힌다(client/README.md 「제안은 근거가 아니다」).
진행(progress)은 항상 흘린다 — 별도 type 이라 답변과 섞일 일이 없다.
프론트 파서는 content 하나에 JSON 객체가 연달아 있어도 읽어야 한다(게이트웨이가 이벤트를 합쳐
보내지 않는다는 확인이 아직 없다) — client/call_agent.py 의 `_events_in` 이 참조 구현이다.
계약 문서는 client/README.md — 이벤트 type 을 바꾸면 함께 고친다. tests/test_api.py
가 여기서 내보내는 type 전부가 그 문서에 있는지 검사한다.

━━ 대화 맥락 — 멀티턴 ━━
후속 질문("그럼 안 된다고 하면요?")·되묻기의 답·연계 확인("네")은 이전 턴의 `history`
(Turn 목록, state.Turn)가 있어야 해석된다. 게이트웨이 경로는 그것을 돌려줄 자리가 없으므로
진입점이 `(x_client_user, session_id)` 키로 메모리에 맡겨 두고 다음 턴에 되찾는다
(consult_agent/context_store.py — 최근 HISTORY_LIMIT(12)턴 · 2시간 · 500세션 · 디스크에 안 쓴다).
호출자가 `message_hists` 에 Turn 형식(`question` 키가 있는 dict 목록)을 실어 보내면 그것이
저장본보다 우선한다. 다른 형식(OpenAI 식 messages 등)은 버린다 — Turn 이 아닌 것을 넘기면
`format_history` 가 `turn['question']` 에서 죽어 500 이 난다.

이 파일은 **얇다.** 판단·검증·문장 생성은 전부 consult_agent 안에서 끝나고, 여기서는
파싱·스트리밍·오류 형태만 맡는다. 화면(Streamlit app.py)과 이 API 는 같은 `ask()` 하나를
부른다 — 두 경로가 갈리면 «화면에서는 되는데 API 에서는 다르게 나오는» 자리가 생긴다.

━━ 무엇을 흘리는가 ━━
  답변      항상, 한 이벤트로. 토큰 단위로 흘리지 않는다 — compose 의 생성문은 검증 게이트
            (verify_texts · relations · 원문 스팬)에서 **통째로 폐기**될 수 있어서, 토큰을
            흘려보내면 직원이 이미 읽은 문장이 사라진다. "근거 밖 수치를 내보내지 않는다"는
            보증이 화면에서 뒤집히는 것이다(progress.py 주석).
  출처      **항상.** 이 에이전트의 답은 «근거 안에서만» 나오고, 그 근거를 보여주는 것이
            존재 이유다(루트 CLAUDE.md §2). 출처 없는 답변은 이 시스템의 산출물이 아니다.
            0건이면 빈 목록을 보낸다 — «근거 없이 답했다»와 «근거를 못 실었다»가 화면에서
            같아 보이면 안 된다(render.sources_block 주석과 같은 이유).
  진행 표시 항상. 답변이 만들어지는 동안 «지금 무엇을 하고 있는지». 문구는 전부 코드가
            정한다(progress.py).

━━ 로그 ━━
행내 플랫폼은 컨테이너의 stdout 을 모아 Grafana 에 보여준다. uvicorn 은 제 로거만
설정하고 루트 로거에는 핸들러를 달지 않으므로, 여기서 설정하지 않으면 이 파일과
pension_agent 의 로그는 **어디에도 나가지 않는다** — 행내에서 보인 것이 접속 로그
(`POST /chat 200 OK`) 한 줄뿐이었던 이유다. 그래서 루트 로거를 stdout·INFO 로 잡는다.

로거는 둘이다. 한 요청은 `[api]` 두 줄 사이에 `[agent]` 줄이 단계마다 하나씩 선다:

    INFO:     [api]   [ab12cd34] request     사용자=3902172 사번=3902172 고객=… 세션=s1 맥락=0턴(없음) 질문=14자 '…'
    INFO:     [agent] [ab12cd34]  0.9s understand  의도=situation
    INFO:     [agent] [ab12cd34]  2.0s plan        회차=1/4 도구=fact 질의='세액공제 한도'
    INFO:     [agent] [ab12cd34]  3.4s tool        fact 결과=성공 후보=3 채택=1 카드=fact.k04.f2(0.37)
    …
    INFO:     [agent] [ab12cd34]  9.2s turn        결과=답변 근거=1건 LLM=8회
    INFO:     [api]   [ab12cd34] done        소요=9.3초 응답=sse 답변=632자

  api    이 파일. 요청 경계에서 HTTP 만 아는 것 — 누가 무엇을 보냈나(request) · 거부(reject) ·
         소요·응답 형식(done) · 연결 끊김(disconnect) · 뒤늦은 실패(error). 이름을 `__name__`
         이 아니라 고정 문자열로 두는 이유는 실행 방식(uvicorn 임포트 / python -m)에 따라
         `main`·`__main__` 으로 갈리기 때문이다.
  agent  에이전트 안에서 일어난 일 — observability.step() 이 단계 하나에 한 줄씩, 요청 id 와
         턴 시작 뒤 경과초를 붙여 찍는다(observability/_trace.py 「단계 로그」). Langfuse
         활성 여부와 무관하게 남는다. 직원이 받는 답이 실패·축소로 바뀐 사실만 WARNING 이다.

진행 표시(progress.emit)는 로그에 싣지 않는다 — 화면(progress 이벤트)으로만 가고, 같은
시점은 `[agent]` 단계 줄이 더 많은 정보로 찍는다.

━━ 로그 이벤트 — 같은 로그 줄을 응답에도 싣는다 ━━
Grafana 가 불안정할 때 프론트의 개발자 콘솔에서 로그를 보려고 둔다. 켜면 이 요청의 로그 줄
(`[api]`·`[agent]`·그 요청 안에서 찍힌 pension_agent 경고 등)이 stdout 에 찍히는 것과 **같은
글자 그대로** `{"type": "log", "level": "INFO", "logger": "agent", "text": "INFO:     [agent] …"}`
이벤트로도 나간다. stdout 로그는 그대로 찍힌다 — 응답에 싣는 것은 사본이다.

  켜는 법   환경변수 `CHAT_LOG_EVENTS=1`(모든 요청) · 또는 input_value 에 `"log_events": true`
            (그 요청만). 둘 다 없으면 끔 — 기본은 지금까지와 같은 응답이다.
  순서      스트림은 찍히는 즉시 흘린다(progress 사이사이 · answer 앞뒤). 비스트림은 JSON 하나의
            content 맨 앞에 모아 싣는다.
  범위      요청 id 가 같은 줄만 싣는다. 다른 직원의 요청 줄은 섞이지 않는다. 422 로 거부된
            요청은 스트림이 없으므로 싣지 않는다.
  가림      개인정보 필터가 잡는 꼴(고객 원장 표기 등)은 `[개인정보 가림]` 으로 바꿔 싣는다 —
            그대로 실으면 게이트웨이가 응답 전체를 막는다(privacy.py). stdout 줄은 원래대로다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Callable, List, Optional

import socket
import urllib.parse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from pension_agent import config, llm, mcp, observability, privacy
from pension_agent.consult_agent import context_store
from pension_agent.consult_agent import graph as consult_graph
from pension_agent.consult_agent.nodes import act as consult_act
from pension_agent.strategy_agent import briefing_store
from pension_agent.strategy_agent import customer as strategy_customer


#: INFO 를 끄는 바깥 라이브러리 로거 — 목록과 이유는 `mcp/client.py::NOISY_LOGGERS`. `httpx`
#: 는 요청마다 «HTTP Request: GET <주소>» 를 INFO 로 찍는데 MCP 주소는 `/workb/{MCP_USER_ID}`
#: 라 클라이언트 id 가 경로에 있고, 행내 SDK 의 감사 로거는 client_id·사번·사용자 키를 JSON
#: 으로 찍는다. 루트를 INFO 로 잡는 순간 그 줄이 전부 stdout(Grafana)으로 나간다. 우리 로그
#: (`api`·`agent`·`pension_agent.*`)가 무엇을 찍는지는 우리가 정하지만 저쪽은 아니다.
_QUIET_LOGGERS = mcp.NOISY_LOGGERS


def _setup_logging() -> None:
    """루트 로거 → stdout · INFO. 바깥(테스트 러너 등)이 이미 잡아 두었으면 손대지 않는다.

    타임스탬프는 넣지 않는다 — 플랫폼 수집기가 줄마다 붙인다(행내 화면에서 확인).
    형식은 uvicorn 의 접속 로그(`INFO:     …`)와 나란히 읽히게 맞춘다.
    바깥 라이브러리의 INFO 는 루트를 누가 잡았든 끈다(`_QUIET_LOGGERS`) — SDK 가 설치되며
    다시 켜는 것은 `mcp.client._setup_system` 이 그 직후에 한 번 더 끈다.
    """
    mcp.quiet_loggers()
    if logging.getLogger().handlers:
        return
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)


#: stdout 로그 줄의 형식. 로그 이벤트(`_LogTap`)도 같은 형식으로 글자를 만든다 — 콘솔에서 보는
#: 줄과 Grafana 에서 보는 줄이 같아야 둘을 나란히 대조할 수 있다.
LOG_FORMAT = "%(levelname)s:     [%(name)s] %(message)s"


class _LogTap(logging.Handler):
    """요청 id 별로 로그 줄을 가로채 그 요청의 응답에 싣는다(머리말 «로그 이벤트»).

    루트 로거에 붙는 핸들러 하나다. stdout 핸들러는 건드리지 않으므로 로그는 원래대로 찍히고,
    여기서는 사본을 만든다. 어느 요청의 줄인지는 둘로 가린다:
      · `[agent]` 줄과 에이전트 안의 다른 로거 — 워커 스레드가 연 `observability.request_id`
      · `[api]` 줄 — 요청 코루틴에서 찍혀 위 컨텍스트 밖이다. 전부 `"[%s] …"` 에 요청 id 를
        첫 인자로 넘기므로 그것을 읽는다.
    """

    #: 동시에 열어 둘 수 있는 요청 수. 스트림이 시작되기 전에 호출자가 끊으면 떼는 자리
    #: (generate 의 finally)에 닿지 못한다 — 그렇게 남은 것이 쌓이지 않게 오래된 것부터 버린다.
    MAX_SINKS = 200

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self._sinks: dict[str, Callable[[dict[str, Any]], None]] = {}

    def attach(self, rid: str, sink: Callable[[dict[str, Any]], None]) -> None:
        while len(self._sinks) >= self.MAX_SINKS:
            self._sinks.pop(next(iter(self._sinks)), None)
        self._sinks[rid] = sink

    def detach(self, rid: str) -> None:
        self._sinks.pop(rid, None)

    @staticmethod
    def _rid_of(record: logging.LogRecord) -> Optional[str]:
        rid = observability.current_request_id()
        if rid:
            return rid
        args = record.args
        if isinstance(record.msg, str) and record.msg.startswith("[%s]") \
                and isinstance(args, tuple) and args and isinstance(args[0], str):
            return args[0]
        return None

    def emit(self, record: logging.LogRecord) -> None:
        if not self._sinks:
            return
        try:
            rid = self._rid_of(record)
            sink = self._sinks.get(rid) if rid else None
            if sink is not None:
                # 응답은 플랫폼 게이트웨이의 개인정보 필터를 지난다. 요청 줄의 `고객=` 은 원장
                # 표기(주민등록번호 꼴)라 그대로 실으면 응답 전체가 FILTER_INVALID 로 막힌다
                # (privacy.py 「나가는 길은 LLM 쪽만이 아니다」). stdout 줄은 가리지 않는다.
                text, _ = privacy.mask(self.format(record))
                sink({"type": "log", "level": record.levelname, "logger": record.name,
                      "text": text})
        except Exception:                                  # noqa: BLE001 — 기록은 흐름을 막지 않는다
            self.handleError(record)


_setup_logging()
_log_tap = _LogTap()
logging.getLogger().addHandler(_log_tap)
#: HTTP 경계 로거. 이름은 고정 문자열이다(머리말 «로그»).
log = logging.getLogger("api")

#: 요청 로그에 싣는 질문 미리보기 길이. 전문은 싣지 않는다 — 로그는 상담 내용의 저장소가 아니다.
QUESTION_PREVIEW = 60


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # 행내 MCP(쪽지 발송)를 이 프로세스에 붙인다 — 설정이 없으면 아무것도 하지 않고,
    # 그때 쪽지는 «미연결»로 답한다(보내지 않고 본문만 만든다 — pension_agent/mcp).
    # 여기서 붙이는 이유는 **붙는 시점이 한 곳이어야 하기 때문**이다: 첫 발송 때 붙이면
    # 그 요청 하나만 토큰 발급 왕복을 물고, 설정이 틀린 것도 그때서야 드러난다.
    mcp.install()
    # 이 컨테이너가 어떤 설정으로 떴는지 한 줄 — /health 와 같은 내용이다. «키를 넣었는데
    # 왜 안 되나 / train URL 을 보고 있나»를 Grafana 에서 로그 첫 줄로 끝내려고 둔다.
    log.info("[-] startup     설정=%s", json.dumps(health(), ensure_ascii=False))
    # 단계와 어긋난 키는 첫 호출에서 401 로만 드러난다. 시작 줄에서 먼저 말한다 —
    # 설정 한 줄 안에 묻히면 «키는 들어 있다»로 읽힌다(llm.key_stage_mismatch 주석).
    if llm.key_stage_mismatch():
        log.warning("[-] startup     LLM 키가 단계와 어긋납니다 — URL=%s · 키=%s(접미사 없음) · 단계=%s"
                    " · %s 에 이 단계의 키를 넣으십시오(지금 상태로는 게이트웨이가"
                    " 401 invalid subscription key 로 끊습니다)",
                    llm.BASE_URL_SRC, llm.API_KEY_SRC, llm.STAGE, llm.wanted_key_name())
    yield


app = FastAPI(title="퇴직연금 AI 사후관리 에이전트", lifespan=_lifespan)


class ChatRequest(BaseModel):
    # 게이트웨이가 스키마 밖 키(stream 같은)를 실어 보내는지 봐야 한다 — 버리지 않고 남긴다.
    model_config = ConfigDict(extra="allow")

    input_value: str
    message_hists: Optional[List] = None


#: 스트림 프레이밍. 기본 SSE(`data: {...}\n\n`) — 머리말 «응답 프레이밍». 0 이면 문서의
#: «줄마다 JSON» 으로 되돌린다.
SSE_FRAMING = os.getenv("CHAT_SSE_FRAMING", "1").strip().lower() not in ("0", "false", "no")

#: 로그 이벤트 — 모든 요청에 켠다(머리말 «로그 이벤트»). 꺼져 있어도 input_value 의
#: `log_events` 로 요청마다 켤 수 있다.
LOG_EVENTS = os.getenv("CHAT_LOG_EVENTS", "0").strip().lower() in ("1", "true", "yes")


def _wants_logs(payload: dict[str, Any]) -> bool:
    return LOG_EVENTS or payload.get("log_events") in (True, 1, "1", "true", "True")


#: 비스트림 신호로 보는 키 이름들(본문 최상위 · input_value 안 어느 쪽이든).
_STREAM_KEYS = ("stream", "isStream", "is_stream")


def _chunk(text: str) -> str:
    line = json.dumps({"event": "CHUNK", "content": text}, ensure_ascii=False)
    return f"data: {line}\n\n" if SSE_FRAMING else line + "\n"


def _event_json(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False)


def _event(event: dict[str, Any]) -> str:
    """이벤트 하나를 CHUNK 로 — content 가 JSON 객체 문자열이다(머리말 «출력 형식»)."""
    return _chunk(_event_json(event))


def _strip_followups(answer: str) -> str:
    """graph.ask 가 답변 끝에 붙인 추천질문 블록을 뗀다 — followups 이벤트로만 간다.

    붙이는 쪽(graph.ask)은 건드리지 않는다. 상담이력·`history` 도구가 되읽는 텍스트는 그대로다.
    """
    head, sep, _ = answer.rpartition("\n\n" + consult_graph.FOLLOWUP_HEADER + "\n")
    return head if sep else answer


#: action 이벤트에 싣는 pending_action 의 키. html·recipients·params 같은 실행 인자는
#: 화면이 알 필요가 없고(실행은 대화의 「네」가 한다), 쪽지 초안(title·text·to)은 미리보기용이다.
_ACTION_KEYS = ("kind", "label", "prompt", "title", "text", "to")


def _turn_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    """ask() 결과를 이벤트 목록으로 — answer → (action | clarify) → sources → followups → done."""
    events: list[dict[str, Any]] = [{
        "type": "answer",
        "text": _strip_followups(result.get("answer", "")),
        "intent": result.get("intent"),
        # 본문이 가리킨 단말 화면의 딥링크. **별도 이벤트가 아니라 answer 의 필드다** —
        # 다른 type 들은 화면의 독립된 블록(버튼·출처·추천질문)인데 이것은 본문 문단 안의
        # 화면번호를 감싸는 재료라, 본문 없이는 그릴 수 없고 본문과 함께 도착해야 한다.
        # 없으면 빈 목록을 보낸다(sources·followups 와 같은 규약).
        "links": list(result.get("links") or []),
    }]
    action = result.get("pending_action")
    # 동명이인 목록을 띄운 쪽지 턴(`candidates`)은 네/아니오로 답할 턴이 아니다 — 버튼을 그리게
    # 하지 않으려고 action 을 내지 않는다. 고를 후보는 세션에 남고 직원은 번호·부서를 입력한다.
    if action and not action.get("candidates"):
        ev = {k: action[k] for k in _ACTION_KEYS if action.get(k) is not None}
        # 본문에 붙는 문장과 버튼 위 문장은 **같은 함수가 만든 같은 문장**이다. 여기에
        # 폴백 문자열을 따로 적어 두면 제안 갈래가 하나 늘 때 두 곳이 어긋난다 — 버튼
        # 위에는 「연계해드릴까요」, 본문에는 「보여드릴까요」가 서는 식이다.
        ev.setdefault("prompt", consult_act.offer_prompt(action))
        events.append({"type": "action", **ev})
    clarify = result.get("clarify")
    if clarify:
        events.append({"type": "clarify", "question": clarify.get("question"),
                       "options": list(clarify.get("options") or [])})
    events.append({"type": "sources", "items": list(result.get("sources") or [])})
    events.append({"type": "followups", "items": list(result.get("followups") or [])})
    events.append({"type": "done"})
    return events


def _error_events(exc: BaseException) -> list[dict[str, Any]]:
    # 실패도 이벤트로 나간다 — 클라이언트가 빈 응답을 받고 «답이 없다»로 오해하는 것보다,
    # 무엇이 깨졌는지 화면에서 읽는 편이 진단이 빠르다(LLMError 주석과 같은 취지).
    return [{"type": "error", "text": f"{type(exc).__name__}: {exc}"}, {"type": "done"}]


def _wants_stream(request: Request, req: ChatRequest, payload: dict[str, Any]) -> bool:
    """호출자가 비스트림을 원한다는 신호가 하나라도 있으면 거짓. 없으면 스트림(기본)."""
    for src in (req.model_extra or {}, payload):
        for key in _STREAM_KEYS:
            if key in src and src[key] in (False, 0, "false", "False", "0"):
                return False
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/event-stream" not in accept and "*/*" not in accept:
        return False
    return True


_SECRET_HINTS = ("token", "key", "auth", "cookie", "secret")


#: 거부된 요청의 input_value 를 로그에 얼마나 보여주나(글자 수). JSON 으로 풀리지 않은
#: 문자열이라 키를 뽑을 수 없으므로 앞부분을 그대로 남긴다 — 질문 미리보기(QUESTION_PREVIEW)와
#: 같은 수준의 노출이다.
INPUT_VALUE_PREVIEW = 120


def _request_shape(request: Request, req: ChatRequest, payload: Optional[dict[str, Any]]) -> str:
    """게이트웨이가 실제로 무엇을 보내는지 — 값이 아니라 **모양**만. 비밀 헤더는 값을 가린다.

    payload 가 None 이면 input_value 가 JSON 으로 풀리지 않은 요청이다 — 키 대신 원문의
    길이와 앞부분을 남긴다. 행내 실측(2026-09-10): 같은 세션 4턴째에 게이트웨이가
    «Expecting value: line 1 column 1» 인 input_value 를 보내 422 가 났는데, 그때 이 로그가
    거부 뒤에만 찍혀 무엇이 왔는지(빈 문자열인지 · 질문 원문인지) 알 수 없었다.
    """
    headers = {
        k: ("***" if any(h in k.lower() for h in _SECRET_HINTS) else v)
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "user-agent")
    }
    hists = req.message_hists
    shape: dict[str, Any] = {
        "headers": headers,
        "body_extra": sorted((req.model_extra or {}).keys()),
        "message_hists": None if hists is None else f"{type(hists).__name__}[{len(hists)}]",
    }
    if payload is None:
        raw = req.input_value
        shape["input_value_raw"] = {
            "len": len(raw),
            "head": raw[:INPUT_VALUE_PREVIEW] + ("…" if len(raw) > INPUT_VALUE_PREVIEW else ""),
        }
    else:
        shape["input_value_keys"] = sorted(payload.keys())
    return json.dumps(shape, ensure_ascii=False)


def _reject(rid: str, detail: str) -> HTTPException:
    """422 — 접속 로그에는 상태코드만 남으므로 무엇이 빠졌는지는 여기서 찍는다."""
    log.warning("[%s] reject      상태=422 사유=%r", rid, detail)
    return HTTPException(status_code=422, detail=detail)


def _parse(req: ChatRequest, rid: str = "-") -> dict[str, Any]:
    """input_value(JSON 문자열)를 풀고 필수 키를 확인한다. 어긋나면 422."""
    try:
        payload = json.loads(req.input_value)
    except json.JSONDecodeError as exc:
        raise _reject(rid, f"input_value 가 JSON 문자열이 아닙니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise _reject(rid, "input_value 는 JSON 객체를 직렬화한 문자열이어야 합니다.")

    x_client_user = payload.get("x_client_user")
    if not x_client_user:
        raise _reject(rid, "input_value 에 'x_client_user' 키가 필요합니다.")
    message = payload.get("message")
    if not message:
        raise _reject(rid, "input_value 에 'message' 키가 필요합니다.")

    # message_hists 는 플랫폼 스키마의 자리이고, 이 에이전트의 대화 맥락은 ask() 가
    # 돌려준 history(Turn 목록)다. Turn 형식(question 키)일 때만 넘긴다 — 플랫폼이 다른
    # 것을 실어 보내도 턴이 깨지지 않아야 한다(맥락이 없어지는 것과 500 이 나는 것은
    # 다르다). None 이면 진입점이 저장해 둔 맥락을 쓴다(머리말 «대화 맥락»).
    hists = req.message_hists
    history = hists if isinstance(hists, list) and hists and all(
        isinstance(h, dict) and "question" in h for h in hists) else None

    return {
        "question": str(message),
        "history": history,
        # b64·13자리로 와도 원장 표기로 되돌린다(머리말 customer_id). 형식을 아는 곳은
        # customer.py 하나다.
        "customer_id": strategy_customer.normalize_id(payload.get("customer_id")),
        "session_id": str(payload.get("session_id") or "default"),
        "x_client_user": str(x_client_user),
        # 사번을 따로 실어 보내는 게이트웨이·프론트를 위한 자리(머리말). 없으면 ask() 가
        # x_client_user 에서 «사번 꼴일 때만» 가져온다 — 여기서 판정하지 않는다.
        "employee_id": str(payload.get("employee_id") or "").strip() or None,
        "payload": payload,
    }


def _host_check() -> dict[str, Any]:
    """LLM_BASE_URL 의 호스트가 이 컨테이너에서 이름이 풀리는가.

    행내 첫 연결에서 실제로 걸린 자리다 — 인증도 쿼터도 아니고 DNS 였다
    (`URLError: [Errno -2] Name or service not known`). LLM Gateway 의 base_url 은
    `*.svc.cluster.local` 이라 **그 쿠버네티스 클러스터 안에서만** 풀리는데, 개발용
    컴퓨트 인스턴스는 그 밖이다. 그런데 실패는 첫 대화 턴에 가서야 «LLM 호출이
    실패했습니다»로 나타나 원인이 안 보인다.

    조회는 이름 해석까지만 한다(연결·인증은 하지 않는다) — /health 는 싸고 빨라야 하고,
    붙는지까지는 실제 턴이 답한다.
    """
    if not llm.BASE_URL:
        return {"host": None, "resolves": None}
    host = urllib.parse.urlparse(llm.BASE_URL).hostname or ""
    try:
        socket.getaddrinfo(host, None)
        return {"host": host, "resolves": True}
    except socket.gaierror as exc:
        return {"host": host, "resolves": False, "error": str(exc)}


@app.get("/health")
def health() -> dict[str, Any]:
    """기동 확인 + LLM 설정 진단.

    행내에서 처음 붙일 때 «키가 안 잡혔나 / 어디를 보고 있나 / 게이트 설정이 얼마인가»를
    로그 뒤지지 않고 한 번에 보려고 둔다. 키 값은 절대 내보내지 않는다 — 설정 여부만
    참/거짓으로 준다. 엔드포인트 호스트는 내보낸다(비밀이 아니고, 이것이 안 보이면
    «어느 주소를 보고 있는지»를 알 방법이 없다).
    """
    return {
        "status": "ok",
        # 어느 파일이 읽혔나 — «키를 넣었는데 왜 안 되나»의 첫 질문이다.
        # 자세한 것은 `python -m pension_agent.env` 가 터미널에 찍는다.
        "env": {"dotenv": str(config.DOTENV), "exists": config.DOTENV.is_file()},
        "llm": {
            "provider": llm.PROVIDER,
            # 어느 단계의 URL 을 읽었나 — train(…/trnn/…) 인지 serving(…/serv/…) 인지.
            # 배포된 컨테이너가 train URL 을 보고 있으면 여기서 바로 드러난다.
            "stage": llm.STAGE,
            "available": llm.available(),
            "base_url_set": bool(llm.BASE_URL),
            "api_key_set": bool(llm.API_KEY),
            # 값이 아니라 **어느 변수에서 읽었나.** `api_key_set: true` 는 「키가 있다」까지만
            # 말하고 「그 단계의 키인가」는 말하지 못한다 — URL 과 키가 각자 폴백하므로
            # 서빙계 URL 에 접미사 없는 분석계 키가 실리는 짝이 조용히 생기고, 그러면
            # APIM 이 401 invalid subscription key 로 끊는다(llm.key_stage_mismatch 주석).
            "base_url_from": llm.BASE_URL_SRC or None,
            "api_key_from": llm.API_KEY_SRC or None,
            "key_stage_mismatch": llm.key_stage_mismatch(),
            "model": llm.MODEL or "(게이트웨이 기본 라우팅)",
            "timeout_sec": llm.TIMEOUT,
            **_host_check(),
        },
        # 미리 만들어 둔 브리핑을 **지금 실제로 읽고 있나.** 대화형은 브리핑이 이미 있다고
        # 보고 답하는데(고객 재료 도구가 `strategy_agent.propose()` 를 부른다), 그것을 이
        # 컨테이너가 직접 만들면 고객당 순차 LLM 11 회다. 미리 구워 넣었는지, 그게 지금
        # 지문으로 읽히는지, 런타임에 만든 것을 저장할 수 있는지 — 셋 다 어긋나도 답변은
        # 정상으로 나가고 «느리다»로만 보인다(briefing_store.stats 머리말).
        "briefing_cache": briefing_store.stats(),
        # 진행 중인 대화 맥락이 몇 세션 살아 있나(메모리 · 머리말 «대화 맥락»).
        "context_store": context_store.stats(),
        # 행내 MCP(쪽지 발송)가 붙었나. 안 붙었으면 무엇이 비어 있는지까지 말한다 —
        # 「보낸다고 했는데 왜 미연결이지」가 여기서 끝나야 한다. 키·토큰은 내보내지 않는다.
        "mcp": mcp.stats(),
        # 429 를 만났을 때 무엇을 조일지 바로 보이도록 게이트 설정을 함께 노출한다.
        "rate_gate": {
            "max_concurrency": llm.MAX_CONCURRENCY,
            "min_interval_sec": llm.MIN_INTERVAL,
            "retry_attempts": llm.RETRY_ATTEMPTS,
            "cooldown_sec": llm.COOLDOWN,
            # 사번 하나에 쿼터가 몰리지 않게 버킷을 나누고 있나(0=끔). 「.env 를 고쳤는데
            # 먹었나」가 여기서 끝나야 한다 — 안 먹은 것과 안 듣는 것은 처방이 정반대다.
            "client_user_spread": llm.CLIENT_USER_SPREAD,
        },
    }


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    rid = uuid.uuid4().hex[:8]   # 이 요청의 로그 줄을 한데 묶는 id
    try:
        args = _parse(req, rid)
    except HTTPException:
        # 거부된 요청도 모양을 남긴다 — 게이트웨이가 무엇을 보냈는지는 여기서만 보이고,
        # 422 사유 한 줄로는 «왜 JSON 이 아니었나»를 되짚을 수 없다(_request_shape 머리말).
        log.warning("[%s] reject      모양=%s", rid, _request_shape(request, req, None))
        raise
    started = time.monotonic()
    question = args["question"]
    x_client_user, session_id = args["x_client_user"], args["session_id"]
    streaming = _wants_stream(request, req, args["payload"])
    # 로그 이벤트 — 요청 줄보다 먼저 붙여야 첫 줄부터 실린다. 스트림은 진행 표시와 같은 큐로
    # 흘리고(찍힌 순서대로 나간다), 비스트림은 모았다가 content 앞에 싣는다.
    with_logs = _wants_logs(args["payload"])
    log_events: list[dict[str, Any]] = []
    if streaming:
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        # 진행 표시·로그는 답변을 만드는 **워커 스레드**에서 나오고, 흘리는 것은 이벤트 루프다.
        # 큐로 건네야 «기다리는 동안» 나간다 — 다 끝난 뒤 몰아서 주면 진행 표시가 아니다.
        lines: asyncio.Queue = asyncio.Queue()

        def _push(ev: dict[str, Any]) -> None:
            # 루프 스레드에서 찍힌 줄(`[api]`)은 바로 넣는다 — call_soon 으로 미루면 뒤따르는
            # 이벤트(error 등)보다 늦게 나간다.
            if threading.get_ident() == loop_thread:
                lines.put_nowait(ev)
            else:
                loop.call_soon_threadsafe(lines.put_nowait, ev)
    if with_logs:
        _log_tap.attach(rid, _push if streaming else log_events.append)
    # 대화 맥락 — 호출자가 Turn 형식으로 실어 보낸 것이 우선, 없으면 저장해 둔 것.
    if args["history"] is not None:
        history, history_from = args["history"], "호출자"
    else:
        history = context_store.get(x_client_user, session_id)
        history_from = "저장" if history else "없음"
    log.info(
        "[%s] request     사용자=%s 사번=%s 고객=%s 세션=%s 맥락=%d턴(%s) 질문=%d자 %r",
        rid, x_client_user,
        # 이 턴의 쪽지가 누구 앞으로 · 누구 이름으로 나갈지가 여기서 정해진다. 값이 «-»
        # 이면 환경변수 폴백으로 떨어졌다는 뜻이고, 그건 여러 직원이 쓰는 배포에서
        # 남의 이름으로 나가는 상태다(docs/PRODUCTION_RISKS.md 10).
        consult_graph.employee_no(args["employee_id"], x_client_user) or "-",
        args["customer_id"] or "-", session_id,
        len(history or []), history_from, len(question),
        question[:QUESTION_PREVIEW] + ("…" if len(question) > QUESTION_PREVIEW else ""),
    )

    def _remember(result: dict[str, Any]) -> None:
        # 다음 턴이 이어받을 맥락. ask() 가 이미 HISTORY_LIMIT 으로 잘라 돌려준다.
        context_store.put(x_client_user, session_id, result.get("history"))

    def _log_done(result: dict[str, Any]) -> None:
        # HTTP 가 아는 것만 — 의도·근거·되묻기·제안은 `[agent]` 의 turn 줄이 이미 말했다.
        log.info("[%s] done        소요=%.1f초 응답=%s 답변=%d자",
                 rid, time.monotonic() - started, "sse" if streaming else "json",
                 len(result.get("answer", "")))
    # 게이트웨이가 무엇을 보내는지는 여기서만 보인다(머리말 «응답 프레이밍») — 모양만 찍는다.
    log.info("[%s] request     응답=%s 모양=%s", rid, "sse" if streaming else "json",
             _request_shape(request, req, args["payload"]))

    if not streaming:
        # 비스트림 — 게이트웨이가 본문 전체를 json.loads 한다. JSON 하나의 content 에 같은
        # 이벤트들을 줄바꿈으로 이어 싣는다(진행은 뺀다 — 기다리는 동안 보여줄 수 없다).
        def run_once() -> dict[str, Any]:
            with observability.request_id(rid):
                return consult_graph.ask(
                    question, history,
                    customer_id=args["customer_id"], session_id=session_id,
                    x_client_user=x_client_user, employee_id=args["employee_id"],
                )
        try:
            result = await asyncio.to_thread(run_once)
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] error       시점=처리중 소요=%.1f초 사유=%r",
                          rid, time.monotonic() - started, f"{type(exc).__name__}: {exc}")
            events = _error_events(exc)
        else:
            _remember(result)
            _log_done(result)
            events = _turn_events(result)
        finally:
            _log_tap.detach(rid)
        events = log_events + events
        return JSONResponse({"event": "CHUNK",
                             "content": "\n".join(_event_json(e) for e in events)})

    async def generate():
        DONE = object()

        def on_progress(text: str) -> None:
            # 화면으로만 간다 — 로그의 같은 시점은 `[agent]` 단계 줄이 찍는다(머리말 «로그»).
            loop.call_soon_threadsafe(lines.put_nowait, {"type": "progress", "text": text})

        def _drain():
            # 큐에 남은 이벤트(DONE 뒤에 찍힌 로그 줄)를 기다리지 않고 비운다.
            while not lines.empty():
                item = lines.get_nowait()
                if item is not DONE:
                    yield _event(item)

        def run() -> dict[str, Any]:
            try:
                with observability.request_id(rid):
                    result = consult_graph.ask(
                        question, history,
                        customer_id=args["customer_id"], session_id=session_id,
                        x_client_user=x_client_user, employee_id=args["employee_id"],
                        on_progress=on_progress,
                    )
                # 맥락은 **여기서** 기억한다 — 답을 다 흘려보낸 뒤가 아니라. 호출자가 중간에
                # 끊으면(게이트웨이 타임아웃·취소) 아래 generate 는 더 돌지 않지만 이 스레드는
                # 끝까지 돈다. 그때도 다음 턴이 이 턴을 이어받아야 한다 — 상담이력은 ask() 안에서
                # 이미 남았는데 맥락(연계 제안·되묻기)만 빠지면 다음 «네»가 갈 곳을 잃는다.
                _remember(result)
                _log_done(result)
                return result
            finally:
                # 성공이든 실패든 반드시 닫는다 — 안 닫으면 아래 루프가 영원히 기다린다.
                loop.call_soon_threadsafe(lines.put_nowait, DONE)

        # ask() 는 동기 호출이고 그 안에서 LLM I/O 로 오래 막힌다. 이벤트 루프에서 직접
        # 부르면 이 워커가 다른 요청을 하나도 못 받는다. to_thread 는 컨텍스트를 복사해
        # 넘기므로 x-client-user 도 스레드 안까지 따라간다.
        task = asyncio.create_task(asyncio.to_thread(run))
        finished = False
        closed = False

        def _settle(t: "asyncio.Task[dict[str, Any]]") -> None:
            # 호출자가 끊긴 뒤 ask() 가 죽으면 아무도 await 하지 않는다 — 여기서 거둬 남긴다.
            if closed and not t.cancelled() and t.exception() is not None:
                log.error("[%s] error       시점=끊긴뒤 사유=%r", rid,
                          f"{type(t.exception()).__name__}: {t.exception()}")

        task.add_done_callback(_settle)
        try:
            while True:
                item = await lines.get()
                if item is DONE:
                    break
                yield _event(item)

            try:
                result = await task
            except Exception as exc:  # noqa: BLE001
                # 스트리밍이 이미 시작돼 상태코드를 바꿀 수 없다 — 실패도 이벤트로 나간다.
                log.exception("[%s] error       시점=처리중 소요=%.1f초 사유=%r",
                              rid, time.monotonic() - started, f"{type(exc).__name__}: {exc}")
                for chunk in _drain():
                    yield chunk
                for ev in _error_events(exc):
                    yield _event(ev)
                finished = True
                return

            for chunk in _drain():
                yield chunk
            for ev in _turn_events(result):
                yield _event(ev)
            finished = True
        finally:
            _log_tap.detach(rid)
            if not finished:
                closed = True
                # 호출자가 다 받기 전에 끊었다(게이트웨이 타임아웃 등). 접속 로그에는
                # 200 으로만 남아 «답이 비었다»와 구분이 안 되므로 여기서 갈라 찍는다.
                log.warning("[%s] disconnect  소요=%.1f초 답변전송=아니오",
                            rid, time.monotonic() - started)

    return StreamingResponse(generate(), media_type="text/event-stream")
