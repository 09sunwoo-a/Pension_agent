"""«오늘»의 단일 출처.

경로가 `config.py` 하나여야 하는 것과 같은 이유다(CLAUDE.md 4번 규칙) — 오늘을 읽는 곳이
둘이면 한쪽만 고정된 채 갈라지고, 그때 화면은 여전히 그럴듯해 보인다. 실제로 이 저장소는
원장 기준일 하나로 오늘까지 세다가 "연말까지 129일"(실제 126일)·"만기 D-17"(실제 D-14)을
말했다.

여기 있는 것은 «지금 몇 시인가»뿐이다. **원장 스냅샷이 언제 찍혔는가**(`customer.AS_OF`)는
시연 데이터에 딸린 값이라 그 데이터를 아는 모듈이 갖는다.

쓰는 곳이 셋이라 공용으로 올렸다 — strategy_agent(잔여일수·경과일) · consult_agent(`date`
도구) · verify(연도 없이 말한 날짜를 어느 해로 읽을지). verify 는 두 에이전트가 함께 쓰는
아래층이라 strategy_agent 를 임포트할 수 없다.
"""

from __future__ import annotations

import json
import os
from datetime import date

from pension_agent import config

#: 오늘을 고정하는 환경변수. 형식이 틀리면 조용히 실제 날짜로 넘어가지 않고 즉시 실패한다 —
#: 오타 하나로 «고정한 줄 알았는데 안 고정된» 산출물이 나오는 것이 가장 나쁜 실패다.
TODAY_ENV = "PENSION_TODAY"


#: 저장된 브리핑이 밝힌 기준일. 파일은 프로세스가 사는 동안 바뀌지 않으므로 한 번만 읽는다
#: (`today()` 는 자주 불린다). 못 읽었으면 빈 문자열, 아직 안 읽었으면 None 이다.
_PREBUILT_TODAY: str | None = None


def _prebuilt_today() -> str:
    """미리 만들어 둔 브리핑(`briefings.json`)이 어느 날짜로 만들어졌는가.

    ━━ 왜 시계가 캐시 파일을 읽나 ━━
    브리핑은 오늘에서 파생된 값을 문장에 싣는다(만기 D-day·미접촉 개월·투자기간). 그래서
    저장본은 **만들어진 그 날짜에만** 적중한다. 처음에는 이 날짜 맞추기를 평가 화면
    (`app.py`)에만 넣었는데, 그러면 화면은 저장본 날짜로 돌고 CLI 대화형은 실제 오늘로
    돌아 **같은 고객에게 서로 다른 D-day 를 말한다** — 저장본을 캐시 계층에 넣어 막으려던
    바로 그 갈라짐을 날짜 층에서 다시 만드는 것이다.

    이 파일 머리말이 적어 둔 실패("오늘을 읽는 곳이 둘이면 한쪽만 고정된 채 갈라지고,
    그때 화면은 여전히 그럴듯해 보인다")가 정확히 그것이므로, 해석을 여기 한 곳으로 모은다.

    ━━ 대가 ━━
    저장본이 묵으면 **프로세스 전체가 그 날짜를 오늘로 믿는다** — 직원에게 오늘이 며칠인지
    답하는 `date` 도구까지. 그래서 `today_source()` 로 출처를 밖에 내주고, 평가 화면
    사이드바와 CLI 시작 줄이 그것을 말한다. 어디서 온 날짜인지 보여야 묵은 파일이 눈에 걸린다.
    """
    global _PREBUILT_TODAY
    if _PREBUILT_TODAY is None:
        try:
            meta = json.loads(
                config.BRIEFINGS_JSON.read_text(encoding="utf-8")).get("meta") or {}
            _PREBUILT_TODAY = str(meta.get("today") or "").strip()
        except (OSError, ValueError, AttributeError):
            _PREBUILT_TODAY = ""
    return _PREBUILT_TODAY


def _prebuilt_off() -> bool:
    return os.environ.get(config.NO_PREBUILT_ENV, "").strip() not in ("", "0", "false", "False")


def resolve() -> tuple[date, str]:
    """오늘과 **그것이 어디서 왔는지**. 우선순위는 명시 → 저장본 → 실제 날짜다.

    해석 자체는 캐시하지 않는다 — `PENSION_TODAY` 를 실행 중에 바꿔가며 검증하는 코드가
    있고(`tests/test_infra.py` 「오늘·기준일」), 캐시하면 그 검증이 «바꿨는데 안 바뀐»
    값을 보게 된다. 캐시하는 것은 파일 읽기뿐이다.
    """
    pinned = os.environ.get(TODAY_ENV, "").strip()
    if pinned:
        try:
            return date.fromisoformat(pinned), f"{TODAY_ENV} 로 고정"
        except ValueError as exc:
            raise ValueError(f"{TODAY_ENV}={pinned!r} 는 YYYY-MM-DD 가 아닙니다") from exc

    if not _prebuilt_off():
        stored = _prebuilt_today()
        if stored:
            try:
                return date.fromisoformat(stored), "저장된 브리핑 기준일"
            except ValueError:
                # 메타가 깨졌다고 화면을 멈추지 않는다 — 저장본은 속도를 위한 것이지
                # «오늘»의 근거가 아니다. 실제 날짜로 가면 키가 안 맞아 미스가 나고,
                # 그 어긋남은 agent.prebuilt_report() 가 화면에서 말한다.
                pass

    return date.today(), "앱을 켠 날"


def today() -> date:
    """오늘. `PENSION_TODAY=YYYY-MM-DD` 가 있으면 그 날짜로 고정한다.

    고정 스위치가 있는 이유는 재현성이다. 테스트는 `tests/__init__.py` 가 원장 기준일로
    고정한 채 돌고(안 그러면 600건 넘는 단언이 실행일마다 흔들린다), 데모를 특정 날짜로
    얼려 보고 싶을 때도 같은 스위치를 쓴다.

    명시가 없으면 미리 만들어 둔 브리핑의 기준일을 따른다(`_prebuilt_today`) — 그래야
    화면과 CLI 가 **같은 저장본을 읽는다**. 그것도 없으면 실제 오늘이다.
    """
    return resolve()[0]


def today_source() -> str:
    """오늘이 어디서 왔는지, 사람이 읽을 한 마디. 화면·CLI 가 그대로 띄운다."""
    return resolve()[1]
