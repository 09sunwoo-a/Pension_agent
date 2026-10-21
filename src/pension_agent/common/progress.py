"""진행 표시 — 답변이 만들어지는 동안 «지금 무엇을 하고 있는지»를 화면에 흘린다.

답변 자체는 스트리밍할 수 없다. compose 의 생성문은 검증 게이트(verify_texts ·
relations · 원문 스팬)에서 **통째로 폐기**될 수 있어서, 토큰을 흘려보내면 직원이 이미
읽은 문장이 사라진다 — "근거 밖 수치를 내보내지 않는다"는 보증이 화면에서 뒤집히는
것이다. 그래서 흘리는 것은 답변이 아니라 **진행**이다.

━━ 규칙 셋 ━━
① 문구는 전부 코드가 정한다. 진행 표시는 새로운 «지어낸 문장»이 될 수 있는 자리라,
   재료의 경계와 같은 취급을 받아야 한다(루트 CLAUDE.md §2). LLM 이 쓰는 문구는 없다.
② 실제로 시작한 일만 찍는다. 검색하지 않는 턴에 "찾고 있어요"를 띄우면 화면이
   거짓말하는 것이다 — emit 은 그 일을 하는 코드 바로 앞에만 둔다.
③ 상태(AgentState)·history·상담이력에 남기지 않는다. 콜백은 ContextVar 로 전달돼
   그래프 상태에 콜러블이 들어가지 않고, 턴이 끝나면 아무 흔적도 없다.

진행 표시가 답변 생성을 깨면 안 된다 — 콜백이 죽어도 emit 은 삼키고 계속 간다.
콜백이 없으면(배치·테스트·CLI 기본) emit 은 아무것도 하지 않는 no-op 이다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: 이번 턴의 진행 콜백. graph.ask() 가 열고 닫는다 — 노드·도구는 emit 만 부른다.
_CALLBACK: ContextVar[Callable[[str], None] | None] = ContextVar(
    "consult_progress_callback", default=None)


@contextmanager
def reporting(callback: Callable[[str], None] | None) -> Iterator[None]:
    """이 블록 안의 emit 을 callback 으로 보낸다. None 이면 emit 은 no-op."""
    token = _CALLBACK.set(callback)
    try:
        yield
    finally:
        _CALLBACK.reset(token)


def object_of(word: str) -> str:
    """목적격 조사를 붙인다 — 받침이 있으면 «을», 없으면 «를».

    "수치을(를) 찾고 있어요" 같은 병기 표기를 화면에 내보내지 않기 위해서다. 받침
    유무는 한글 음절 분해로 결정론으로 갈리므로 추측이 아니다. 마지막 글자가 한글이
    아니면(영문·숫자 라벨) 병기로 물러선다 — 틀린 조사보다 병기가 낫다.
    """
    if not word:
        return word
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        return word + ("을" if (code - 0xAC00) % 28 else "를")
    return word + "을(를)"


def emit(text: str) -> None:
    """진행 한 줄. 콜백이 없으면 아무것도 하지 않고, 콜백이 죽어도 삼킨다 —
    진행 표시는 곁가지라 본류(답변 생성)를 절대 끌고 넘어지면 안 된다."""
    cb = _CALLBACK.get()
    if cb is None:
        return
    try:
        cb(text)
    except Exception:  # noqa: BLE001 — 표시 실패가 답변 실패가 되면 안 된다
        pass
