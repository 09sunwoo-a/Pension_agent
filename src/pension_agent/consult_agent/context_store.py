"""대화 맥락(history) 보관 — 프로세스 메모리, `(x_client_user, session_id)` 키.

`graph.ask()` 는 상태를 들고 있지 않는다 — 이전 턴의 `history`(Turn 목록)를 호출자가 넘겨야
후속 질문·되묻기 답·연계 확인이 이어진다. 행내 플랫폼 게이트웨이 경로에서는 그것이 불가능하다:
응답 스키마가 CHUNK 텍스트로 고정이라 `history` 를 돌려줄 자리가 없고, 게이트웨이의
`message_hists` 는 `pending_*` 플래그를 실어 오지 못한다. 그래서 진입점(main.py)이 턴이 끝날
때 `history` 를 여기 맡기고 다음 턴에 되찾는다.

━━ 무엇을 · 얼마나 ━━
`history` 는 에이전트가 이미 최근 HISTORY_LIMIT(4)턴으로 자른 목록이고, 한 턴은 질문 문장 ·
슬롯 · pending 플래그 · 도구 이름뿐이다(답변 본문 없음, state.Turn). 세션 하나가 1~2KB 라
상한 500 세션이어도 1MB 안쪽이다.

━━ 왜 메모리인가 ━━
이것은 **진행 중인 대화의 해석 맥락**이다 — 대화가 끝나면 쓸모가 없다. 고객 단위로 상담
내용을 영구 기록하는 `session_store`(브리핑 §14 · `history` 도구가 되읽는다)와 수명·용도가
달라서 같은 저장소에 넣지 않는다. 재기동하면 진행 중이던 맥락은 사라진다 — 재배포 중에는
어차피 대화가 끊긴다. 컨테이너가 여럿이면 갈린다(docs/PRODUCTION_RISKS.md 2-b).

━━ 우선순위 ━━
호출자가 `message_hists` 에 Turn 형식(`question` 키가 있는 dict 목록)을 실어 보내면 **그것이
이긴다**(main.py). 그래야 나중에 프론트나 게이트웨이가 맥락을 들고 다니게 될 때 여기를 고치지
않아도 된다.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from pension_agent.consult_agent.state import Turn

#: 마지막 접근 뒤 이 시간이 지나면 버린다. 상담 한 번을 넉넉히 덮는 시연 기준 임의값이다.
TTL_SEC = 2 * 60 * 60
#: 보관하는 세션 수 상한. 넘으면 가장 오래 안 쓴 것부터 버린다.
MAX_SESSIONS = 500

_lock = threading.Lock()
#: (x_client_user, session_id) → (마지막 접근 시각, history)
_store: "OrderedDict[tuple[str, str], tuple[float, list[Turn]]]" = OrderedDict()


def _key(x_client_user: str | None, session_id: str | None) -> tuple[str, str]:
    return (str(x_client_user or ""), str(session_id or "default"))


def _evict(now: float) -> None:
    """만료된 것과 상한 초과분을 버린다. 잠금 안에서 부른다."""
    expired = [k for k, (seen, _) in _store.items() if now - seen > TTL_SEC]
    for k in expired:
        del _store[k]
    while len(_store) > MAX_SESSIONS:
        _store.popitem(last=False)


def get(x_client_user: str | None, session_id: str | None) -> list[Turn] | None:
    """이 세션의 마지막 history. 없거나 만료됐으면 None."""
    now = time.monotonic()
    with _lock:
        _evict(now)
        item = _store.get(_key(x_client_user, session_id))
        if item is None:
            return None
        _store.move_to_end(_key(x_client_user, session_id))
        _store[_key(x_client_user, session_id)] = (now, item[1])
        return list(item[1])


def put(x_client_user: str | None, session_id: str | None, history: list[Turn] | None) -> None:
    """턴이 끝난 뒤의 history 를 맡긴다. 빈 목록이면 세션을 지운다."""
    now = time.monotonic()
    with _lock:
        key = _key(x_client_user, session_id)
        if not history:
            _store.pop(key, None)
        else:
            _store[key] = (now, list(history))
            _store.move_to_end(key)
        _evict(now)


def drop(x_client_user: str | None, session_id: str | None) -> bool:
    with _lock:
        return _store.pop(_key(x_client_user, session_id), None) is not None


def clear() -> None:
    with _lock:
        _store.clear()


def stats() -> dict[str, Any]:
    """/health 용 — 몇 세션이 살아 있고 설정이 얼마인가."""
    now = time.monotonic()
    with _lock:
        _evict(now)
        return {"sessions": len(_store), "ttl_sec": TTL_SEC, "max_sessions": MAX_SESSIONS}
