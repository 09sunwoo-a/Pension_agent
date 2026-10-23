"""WorkB 쪽지 — 꼴(표·마스킹·길이 상한)과 발송·응답 판정. 무엇을 쓸지는 actions/memo.py.

- 발송 함수는 주입(`use_sender` — 등록은 mcp/workb.py). 미등록이면 «미연결» 응답
- `daily_targets_note`: 오늘의 타겟 목록 쪽지(전부 코드 값). 요건 옆에 성립시킨 원장 값 표기
- 고객 id 기본 마스킹(`MASK_ID`) — KB-PIN 앞자리가 생년월일, 쪽지는 받은편지함에 잔존
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from pension_agent.common.clock import today
from pension_agent.tools.customer_solution.customer import AS_OF, CONDS, Profile
from pension_agent.tools.customer_solution.engine.text import dday, won
from pension_agent.tools.customer_solution.target_list import Target, today_targets

#: 쪽지 본문 길이 상한(자, 태그 포함). 초과 시 고객 블록 단위로 자름.
#: 규격 미확인 자리표시자 — 높게 잡음(낮으면 고객이 경고 없이 누락, 높으면 서버 거부로 드러남).
MAX_CHARS = 8000

#: 고객 id 마스킹 여부(행내 정책이 허용하면 False).
MASK_ID = True

#: 고객당 표기 요건 수 상한. 나머지는 "외 N건".
MAX_CONDS = 3


# 요건별 «걸린 값» — Profile 필드 그대로(재계산 금지). 값 없는 요건은 키 없음

def _cash(p: Profile) -> str | None:
    """고유계정대 금액·비중(원장 자산군 표 그대로)."""
    row = next((a for a in p.assets if a["type"] == "고유계정대"), None)
    return f"{won(row['amount'])}({row['pct']}%)" if row else None


def _isa(p: Profile) -> str | None:
    if not p.isa:
        return None
    parts = [won(p.isa["amount"])]
    if p.isa.get("dd") is not None:
        parts.append(dday(p.isa["dd"]))
    if p.isa.get("org"):
        parts.append(p.isa["org"])
    return " · ".join(parts)


def _maturity(p: Profile) -> str | None:
    if p.matDD is None:
        return None
    return f"{dday(p.matDD)} · {won(p.matAmt)}" if p.matAmt else dday(p.matDD)


def _inflow(p: Profile) -> str | None:
    delta = (p.activity or {}).get("cash_delta_1m")
    return f"최근 1개월 +{won(delta)}" if delta else None


def _mismatch(p: Profile) -> str:
    """투자성향 불일치 값 — 적극·공격은 원리금보장 비중, 그 외는 위험자산 비중(mis 판정 갈래)."""
    if p.rk in ("적극투자형", "공격투자형"):
        return f"{p.rk} · 원리금보장 {p.port[0]}%"
    return f"{p.rk} · 위험자산 {p.risk_asset}%"


DETAIL: dict[str, Callable[[Profile], str | None]] = {
    "out": _inflow,
    "dor": lambda p: f"{p.dorm}일" if p.dorm is not None else None,
    "lim": lambda p: f"위험자산 {p.risk_asset}%",
    "mis": _mismatch,
    "sec": lambda p: f"섹터ETF {p.port[3]}%",
    "hlt": lambda p: f"{sum(1 for h in p.holdings if h.get('discontinued'))}건",
    "dep": lambda p: f"{p.port[0]}%",
    "idl": _cash,
    "low": lambda p: f"수익률 백분위 {p.retPct}" if p.retPct is not None else None,
    "mat": _maturity,
    "isa": _isa,
    "tax": lambda p: f"{p.room:,}만원" if p.room else None,
    "add": lambda p: f"{p.room:,}만원" if p.room else None,
    "nch": lambda p: f"{p.nchM:g}개월",
}


def cond_text(target: Target, cond: str) -> str:
    """요건 표기 — «CONDS 이름 + 걸린 값»."""
    name = CONDS.get(cond, cond)
    try:
        value = DETAIL.get(cond, lambda _p: None)(target.profile)
    except Exception:
        value = None
    return f"{name} {value}" if value else name


def display_customer_id(customer_id: str) -> str:
    """고객 id 마스킹 — 뒷자리 가림, 자릿수 유지."""
    if not MASK_ID:
        return customer_id
    head, sep, tail = customer_id.partition("-")
    return f"{head}{sep}{tail[:1]}{'*' * max(0, len(tail) - 1)}" if sep else customer_id


def _block(no: int, target: Target) -> str:
    """고객 한 명 = 머리 두 줄 + 요건 한 줄씩(값에 가운뎃점이 있어 요건은 줄로 구분)."""
    p = target.profile
    attrs = [f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else [])
    shown = target.conds[:MAX_CONDS]
    rest = len(target.conds) - len(shown)
    lines = [f"{no}. {p.nm} ({display_customer_id(p.id)})",
             f"   {' · '.join(attrs)} · 평가금액 {won(p.bal)}"]
    lines += [f"   - {cond_text(target, c)}" for c in shown]
    if rest > 0:
        lines.append(f"   … 외 {rest}건")
    return "\n".join(lines)


#: 꼬리말. ASOF(원장 기준일 vs 오늘)는 원장 값 실린 모든 쪽지, RULE 은 목록 쪽지만.
FOOTER_ASOF = "※ 평가금액·보유 현황은 {as_of} 원장 기준이고, 잔여일수·경과일은 {today} 기준입니다."
FOOTER_RULE = "※ 선정 기준은 사후관리 타겟 룰베이스입니다. 상세 근거는 에이전트 화면에서 확인하세요."
FOOTER = f"{FOOTER_ASOF}\n{FOOTER_RULE}"

#: 타겟 0명일 때 본문(빈 쪽지 금지 — 장애와 구분).
EMPTY_BODY = "오늘 사후관리 타겟으로 선정된 고객이 없습니다."

#: 잘라낸 뒤 남기는 줄(누락 인원 명시).
CUT_LINE = "…외 {n}명은 화면에서 확인하세요."


def _head(count: int) -> str:
    return f"오늘의 타겟 고객 · {today().isoformat()} · {count}명"


def _foot() -> str:
    return FOOTER.format(as_of=AS_OF.isoformat(), today=today().isoformat())


def _fit(head: str, blocks: list[str], foot: str, *, joiner: str,
         cut: Callable[[int], str], max_chars: int) -> tuple[str, int]:
    """머리·블록·꼬리를 상한 안에 맞춤. 블록 단위로 덜어냄(반쪽 수치 방지), 최소 1명."""
    total, shown = len(blocks), len(blocks)
    while shown > 1:
        body = joiner.join([head, *blocks[:shown],
                            *([cut(total - shown)] if shown < total else []), foot])
        if len(body) <= max_chars:
            return body, shown
        shown -= 1
    return joiner.join([head, blocks[0], cut(total - 1), foot]), 1


def render(targets: list[Target], *, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """텍스트 본문과 실린 고객 수."""
    head, foot = f"[{_head(len(targets))}]", _foot()
    if not targets:
        return "\n\n".join([head, EMPTY_BODY, foot]), 0
    blocks = [_block(i, t) for i, t in enumerate(targets, 1)]
    return _fit(head, blocks, foot, joiner="\n\n",
                cut=lambda n: CUT_LINE.format(n=n), max_chars=max_chars)


# HTML 본문 — 이메일 HTML 규율: <style>·클래스·외부 자원 금지, 옛 표 속성 + 인라인 스타일 병기
# (WorkB 뷰어가 style 을 걷어냄), 색으로 뜻 전달 금지

#: 표 스타일(장식). 정렬·선은 옛 속성(align·border)에도 병기.
TABLE = ('border="1" cellspacing="0" cellpadding="9" '
          'style="border-collapse:collapse;font-size:13px;line-height:1.7"')
_TH = 'align="center" style="background:#f4f4f4;text-align:center;white-space:nowrap"'
_TD_NO = 'align="center" style="text-align:center;white-space:nowrap"'
_TD_NUM = 'align="right" style="text-align:right;white-space:nowrap"'
_MUTED = 'style="color:#777"'


def esc(text: str) -> str:
    """HTML 본문용 이스케이프(actions/memo.py 공용)."""
    return html.escape(str(text), quote=False)


def _row(no: int, target: Target) -> str:
    """고객 한 명 = 표 한 행. 요건은 칸 안에서 <br> 로 구분."""
    p = target.profile
    attrs = " · ".join([f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else []))
    shown = target.conds[:MAX_CONDS]
    rest = len(target.conds) - len(shown)
    conds = "<br>".join(esc(cond_text(target, c)) for c in shown)
    if rest > 0:
        conds += f"<br><span {_MUTED}>외 {rest}건</span>"
    return (f"<tr><td {_TD_NO}>{no}</td>"
            f"<td><b>{esc(p.nm)}</b><br>{esc(attrs)}"
            f"<br><span {_MUTED}>{esc(display_customer_id(p.id))}</span></td>"
            f"<td {_TD_NUM}>{esc(won(p.bal))}</td>"
            f"<td>{conds}</td></tr>")


#: 표의 열(「구분」= 순번).
COLS = ("구분", "고객", "평가금액", "선정 요건")


def targets_table(targets: list[Target], *, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """타겟 표(`<table>…</table>`)와 실린 고객 수. 목록 쪽지·actions/memo.py 공용."""
    thead = "<tr>" + "".join(f"<th {_TH}>{esc(c)}</th>" for c in COLS) + "</tr>"
    rows = [_row(i, t) for i, t in enumerate(targets, 1)]
    # 여닫는 태그는 머리·꼬리에 — 행 단위로 덜어내도 표 유지
    return _fit(f"<table {TABLE}>{thead}", rows, "</table>",
                joiner="", cut=lambda n: f'<tr><td colspan="{len(COLS)}" {_MUTED}>'
                                         f'{esc(CUT_LINE.format(n=n))}</td></tr>',
                max_chars=max_chars)


def render_html(targets: list[Target], *, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """HTML 본문과 실린 고객 수. 상한은 태그 포함 길이로 판정."""
    head = f"<b>{esc(_head(len(targets)))}</b>"
    foot = f"<span {_MUTED}>{esc(_foot()).replace(chr(10), '<br>')}</span>"
    if not targets:
        return f"{head}<br><br>{esc(EMPTY_BODY)}<br><br>{foot}", 0
    # 머리·꼬리 길이만큼 표 상한에서 차감
    table, shown = targets_table(targets, max_chars=max_chars - len(head) - len(foot) - 8)
    return f"{head}<br>{table}<br>{foot}", shown


#: 목록 쪽지 형식. 기본 텍스트(HTML 미렌더 뷰어에서 태그 노출 방지).
FORMAT = "text"

RENDERERS: dict[str, Callable[..., tuple[str, int]]] = {"html": render_html, "text": render}


@dataclass(frozen=True)
class Note:
    """쪽지 한 통(제목·본문). 필드명 매핑은 발송 어댑터 몫."""

    title: str
    body: str
    # 아래 셋은 목록 쪽지 전용(기본값 = 잘라낸 것 없음)
    count: int = 0      # 목록에 오른 고객 수(잘라내기 전)
    shown: int = 0      # 본문에 실제로 실린 수
    truncated: bool = False


def daily_targets_note(*, fmt: str = "", max_chars: int = MAX_CHARS) -> Note:
    """오늘의 타겟 고객 쪽지."""
    targets = today_targets()
    body, shown = RENDERERS[fmt or FORMAT](targets, max_chars=max_chars)
    return Note(title=f"오늘의 타겟 고객 {len(targets)}명 ({today().isoformat()})",
                body=body, count=len(targets), shown=shown,
                truncated=shown < len(targets))


# 발송 — 함수는 주입(행내 mcp_sdk 는 저장소 밖 패키지)

#: 발송 함수 모양 — `await send(recipients, title, body)` → 원시 결과(판정은 parse_result).
Sender = Callable[[list[str], str, str], Awaitable[Any]]

#: 등록된 발송 함수. None 이면 «미연결» 응답.
SENDER: Sender | None = None

#: 로그인 사번이 없을 때의 직원 사번 폴백 환경변수. 수신자는 LLM 이 정하지 않음.
EMP_NO_ENV = "WORKB_EMP_NO"

#: 사번 꼴(숫자 7자리). 단일 출처 — nodes/act.py 정규식도 이것으로 생성.
EMP_NO_PATTERN = r"\d{7}"
#: 맨 앞 사번, 뒤는 끝 또는 비숫자.
_EMP_NO_HEAD = re.compile(rf"^({EMP_NO_PATTERN})(?!\d)")


def as_emp_no(value: str | None) -> str | None:
    """직원 식별자(`x_client_user`)에서 맨 앞 7자리 사번 추출. 아니면 None.

    - 접미(`3902172-uuid…`) 허용, 바로 뒤가 숫자면 거부 — 긴 숫자 id 의 오독 방지
    - 사번 아닌 값(`pension-agent` 등)도 들어오므로 그대로 사용 금지
    """
    found = _EMP_NO_HEAD.match((value or "").strip())
    return found.group(1) if found else None


# 보내는 직원(MCP 인증·감사 기록 주체). 주입된 Sender 시그니처를 늘릴 수 없어 ContextVar 로 전달

_ACTING: ContextVar[str] = ContextVar("workb_acting", default="")


@contextmanager
def acting(emp_no: str | None) -> Iterator[None]:
    """블록 안 발송의 보내는 직원 설정."""
    token = _ACTING.set((emp_no or "").strip())
    try:
        yield
    finally:
        _ACTING.reset(token)


def acting_employee() -> str | None:
    """현재 블록의 보내는 직원. 없으면 None(환경변수 폴백)."""
    return _ACTING.get() or None


def use_sender(fn: Sender | None) -> None:
    """발송 함수 등록(앱 시작 시 1회, 보통 `mcp.install()` 이 호출)."""
    global SENDER
    SENDER = fn


def employee_id(explicit: str | None = None) -> str | None:
    """직원 사번(기본 수신자·발송 주체): 로그인 사번 → `acting` → 환경변수. 없으면 None.

    환경변수 값은 꼴 검사 생략(사람이 직접 넣은 값).
    """
    return ((explicit or "").strip() or _ACTING.get()
            or os.getenv(EMP_NO_ENV, "").strip() or None)


def validate_recipients(recipients: Any) -> list[str]:
    """수신자 목록 검증. 문자열 단독 전달 차단(WorkB 가 64;ETC_ERR 로만 거부)."""
    if isinstance(recipients, str):
        raise TypeError("recipients 는 리스트여야 합니다 — 문자열 하나를 넘기면 WorkB 가 "
                        f"64;ETC_ERR 로 거부합니다: [{recipients!r}] 처럼 감싸세요")
    ids = list(recipients or [])
    if not ids or not all(isinstance(r, str) and r.strip() for r in ids):
        raise ValueError(f"recipients 가 비어 있거나 빈 값을 포함합니다: {recipients!r}")
    return ids


def _text_of(raw: Any) -> str:
    """어댑터 결과에서 텍스트 추출(콘텐츠 블록 리스트·문자열·(content, artifact) 튜플)."""
    if isinstance(raw, tuple) and raw:
        raw = raw[0]
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(b.get("text", "") for b in raw
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_result(raw: Any) -> dict[str, Any]:
    """발송 결과 판정. WorkB 는 실패를 본문 `success: false` 로 보냄 — 본문으로 판정.

    판정 불가(JSON 아님·success 없음)는 unknown(성공으로 접지 않음).
    """
    text = _text_of(raw).strip()
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return {"status": "unknown",
                "detail": "발송 결과를 판정하지 못했습니다 — 응답이 JSON 이 아닙니다",
                "raw": text[:200]}
    if not isinstance(body, dict) or "success" not in body:
        return {"status": "unknown",
                "detail": "발송 결과를 판정하지 못했습니다 — 응답에 success 가 없습니다",
                "raw": text[:200]}
    if body.get("success"):
        return {"status": "sent", "detail": "쪽지를 발송했습니다"}
    return {"status": "failed",
            "detail": f"WorkB 가 발송을 거부했습니다: {body.get('error') or '사유 없음'}",
            "error": body.get("error")}


async def send_note(recipients: list[str], note: Note, *, send: Sender | None = None,
                    as_employee: str | None = None) -> dict[str, Any]:
    """WorkB 쪽지 발송. 발송 함수 미등록이면 보내지 않고 not_connected.

    as_employee: 보내는 직원 사번(받는 사람 아님). 직원 승낙 후에만 호출(confirm_action).
    """
    ids = validate_recipients(recipients)
    send = send or SENDER
    if send is None:
        # 화면 노출 문구 — 직원이 읽을 말로
        return {"status": "not_connected",
                "detail": ("이 실행에는 쪽지 발송 연결(행내 MCP)이 설정되어 있지 않아 보내지 "
                           "않았어요 — 초안만 만들었어요. 설정 확인: python -m pension_agent.common.mcp"),
                "recipients": ids, "title": note.title, "body": note.body}
    try:
        with acting(as_employee):
            raw = await send(ids, note.title, note.body)
    except Exception as exc:
        return {"status": "failed", "detail": f"발송 호출이 실패했습니다: {type(exc).__name__}: {exc}",
                "error": type(exc).__name__, "recipients": ids, "title": note.title}
    return {**parse_result(raw), "recipients": ids, "title": note.title}


def send_note_sync(recipients: list[str], note: Note, *, send: Sender | None = None,
                   as_employee: str | None = None) -> dict[str, Any]:
    """동기 발송(대화 그래프용). 실행 중인 루프 안이면 failed(`await send_note` 사용)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(send_note(recipients, note, send=send, as_employee=as_employee))
    return {"status": "failed",
            "detail": ("이벤트 루프 안에서는 동기 발송을 기다릴 수 없습니다 — "
                       "await send_note(...) 를 쓰세요"),
            "error": "RunningLoop", "recipients": list(recipients), "title": note.title}


# 이름 발송(`search_emp_and_send_memo`) — 검색·발송이 한 호출: 1명이면 즉시 발송(사전 확인 불가),
# 여러 명이면 후보 목록. 후보 선택 후엔 사번 발송(send_note) — 이름 재검색 금지

#: 이름 발송 함수 모양 — `await send(user_name, group_name, title, body)` → 원시 결과.
NameSender = Callable[[str, "str | None", str, str], Awaitable[Any]]

#: 등록된 이름 발송 함수. None 이면 «미연결».
NAME_SENDER: NameSender | None = None


def use_name_sender(fn: NameSender | None) -> None:
    """이름 발송 함수 등록(보통 `mcp.install()` 이 호출)."""
    global NAME_SENDER
    NAME_SENDER = fn


def parse_name_result(raw: Any) -> dict[str, Any]:
    """이름 발송 결과 판정 — sent · candidates · not_found · failed · unknown.

        sent        {"data": {"recipients": [사번], ...}}
        candidates  {"data": {"resultCode": 200, "resultData": [{user_id, group_name, dsgt}]}}
        not_found   {"data": {"resultCode": 204, ...}}

    세 응답 모두 `success: true` — success 만으로 «보냈다» 판정 금지.
    """
    text = _text_of(raw).strip()
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return {"status": "unknown",
                "detail": "발송 결과를 판정하지 못했습니다 — 응답이 JSON 이 아닙니다",
                "raw": text[:200]}
    if not isinstance(body, dict) or "success" not in body:
        return {"status": "unknown",
                "detail": "발송 결과를 판정하지 못했습니다 — 응답에 success 가 없습니다",
                "raw": text[:200]}
    if not body.get("success"):
        return {"status": "failed",
                "detail": f"WorkB 가 발송을 거부했습니다: {body.get('error') or '사유 없음'}",
                "error": body.get("error")}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    sent_to = data.get("recipients")
    if isinstance(sent_to, list) and sent_to:
        return {"status": "sent", "detail": "쪽지를 발송했습니다",
                "recipients": [str(r) for r in sent_to]}
    code = data.get("resultCode")
    found = data.get("resultData")
    if code == 204 or (code == 200 and not found):
        return {"status": "not_found", "detail": "이름으로 찾은 직원이 없습니다"}
    if isinstance(found, list) and found:
        cands = [{"user_id": str(c.get("user_id") or ""), "group_name": str(c.get("group_name") or ""),
                  "dsgt": str(c.get("dsgt") or "")}
                 for c in found if isinstance(c, dict) and c.get("user_id")]
        if cands:
            return {"status": "candidates", "detail": f"같은 이름의 직원이 {len(cands)}명입니다",
                    "candidates": cands}
    return {"status": "unknown",
            "detail": "발송 결과를 판정하지 못했습니다 — 발송도 후보 목록도 아닙니다",
            "raw": text[:200]}


async def send_note_by_name(user_name: str, group_name: str | None, note: Note, *,
                            send: NameSender | None = None,
                            as_employee: str | None = None) -> dict[str, Any]:
    """이름(과 부서)으로 찾아 발송 — 1명이면 즉시 발송. 규약은 `send_note` 와 동일."""
    name = (user_name or "").strip()
    if not name:
        raise ValueError("user_name 이 비어 있습니다")
    send = send or NAME_SENDER
    if send is None:
        return {"status": "not_connected",
                "detail": ("이 실행에는 쪽지 발송 연결(행내 MCP)이 설정되어 있지 않아 보내지 "
                           "않았어요 — 초안만 만들었어요. 설정 확인: python -m pension_agent.common.mcp"),
                "user_name": name, "title": note.title}
    try:
        with acting(as_employee):
            raw = await send(name, (group_name or "").strip() or None, note.title, note.body)
    except Exception as exc:
        return {"status": "failed", "detail": f"발송 호출이 실패했습니다: {type(exc).__name__}: {exc}",
                "error": type(exc).__name__, "user_name": name, "title": note.title}
    return {**parse_name_result(raw), "user_name": name, "title": note.title}


def send_note_by_name_sync(user_name: str, group_name: str | None, note: Note, *,
                           send: NameSender | None = None,
                           as_employee: str | None = None) -> dict[str, Any]:
    """동기 이름 발송(`send_note_sync` 와 동일 규약)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(send_note_by_name(user_name, group_name, note, send=send,
                                             as_employee=as_employee))
    return {"status": "failed",
            "detail": ("이벤트 루프 안에서는 동기 발송을 기다릴 수 없습니다 — "
                       "await send_note_by_name(...) 를 쓰세요"),
            "error": "RunningLoop", "user_name": user_name, "title": note.title}


if __name__ == "__main__":  # 출력 확인: python -m pension_agent.common.note
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    n = daily_targets_note()
    print(n.body)
    print(f"\n— {len(n.body)}자 · {n.shown}/{n.count}명"
          f"{' (잘림)' if n.truncated else ''}")
