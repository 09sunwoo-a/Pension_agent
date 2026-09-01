"""WorkB(행내 직원용 작업툴) 쪽지 — 본문 생성.

직원이 "오늘 타겟 고객 쪽지로 보내줘" 라고 하면 보내는 그 글이다. 여기가 만드는 것은
**본문 텍스트 하나**이고, 실제 발송은 MCP 클라이언트가 한다(`send_note` — 아직 미연결).

━━ 본문은 LLM 이 쓰지 않는다 ━━
이 쪽지에 실리는 것은 전부 코드가 이미 아는 값이다 — 요건 이름은 `customer.CONDS`,
수치는 원장 필드, 순서는 `target_list` 다. 문장을 LLM 에 맡기면 그 순간 «원장에 없는 수치가
직원 받은편지함에 남는» 경로가 하나 생기고, 화면 답변과 달리 쪽지는 verify 를 거치지도
못한다(보내고 나면 되돌릴 수 없다). CLAUDE.md 2번 규칙의 «코드 = 사실» 쪽에 통째로 둔다.

━━ 값은 요건 옆에 붙인다 ━━
"장기 미접촉"만 적으면 직원은 결국 화면을 열어봐야 한다. 무엇 때문에 걸렸는지(322일)를
같이 적어야 쪽지만 보고 오늘 누구부터 볼지 정할 수 있다. 붙이는 값은 **그 요건을 성립시킨
원장 값**뿐이고, 값이 없는 요건(디폴트옵션 미설정 등)에는 아무것도 붙이지 않는다.

━━ 확정되지 않은 것 ━━
`MAX_CHARS`(쪽지 한 통의 길이 상한)는 아직 규격을 못 받아 넉넉히 잡아 둔 자리표시자다.
`MASK_ID`(고객 id 마스킹)는 규격이 아니라 정책 선택이다(아래 참고).

**고객 id 는 기본으로 가린다.** KB-PIN 은 생년월일이 앞자리에 그대로 드러나는 형식이고,
쪽지는 화면과 달리 받은편지함에 남는다. 직원이 목록에서 고객을 특정하는 데 필요한 것은
이름과 순번이며, 실제 조회는 에이전트 화면에서 한다. 행내 정책이 원문 노출을 허용하면
`MASK_ID = False` 하나로 열린다.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable

from pension_agent.clock import today
from pension_agent.strategy_agent.customer import AS_OF, CONDS, Profile
from pension_agent.strategy_agent.engine.text import dday, won
from pension_agent.strategy_agent.target_list import Target, today_targets

# ─────────────────────────────────────────────────────────────
# 규격 자리표시자 (WorkB·MCP 스펙 확정 시 교체)
# ─────────────────────────────────────────────────────────────

#: 쪽지 본문 길이 상한(자). 넘치면 **고객 블록 단위로** 잘라낸다 — 줄 중간에서 자르면
#: "5억 2,00" 같은 반쪽 수치가 남고, 그건 틀린 값을 보낸 것과 같다.
#:
#: **실측 미확인이다.** WorkB 가 본문을 몇 자까지 받는지 규격을 아직 못 받았다. 그래서
#: 값을 정할 때 «틀리는 방향»을 골랐다 — **높게 틀리면 시끄럽게 실패하고, 낮게 틀리면
#: 조용히 잘못된다.** 상한이 실제보다 높으면 서버가 거부하고 그 사유가 직원 화면에 뜨지만
#: (`parse_result`), 실제보다 낮으면 아무 경고 없이 고객 몇 명이 목록에서 사라진다.
#: 직원은 그 목록이 전부인 줄 알고, 빠진 고객은 그날 아무도 안 본다.
#:
#: 9명 기준 실측: 텍스트 약 1,300자 · HTML 약 3,100자(태그 포함 — 상한 판정도 태그를
#: 포함한 문자열 길이로 한다. 서버가 받는 것이 그 문자열이다). 실데이터에서 고객 수가
#: 늘면 이 값이 먼저 걸리므로, 규격을 받으면 **제일 먼저 여기를 고친다.**
MAX_CHARS = 8000

#: 고객 id 마스킹. 모듈 docstring 참고.
MASK_ID = True

#: 고객 한 명에게 몇 건까지 요건을 적나. 나머지는 "외 N건"으로 접는다 — 쪽지는 훑는
#: 글이고, 전체 요건은 화면이 갖는다.
MAX_CONDS = 3


# ─────────────────────────────────────────────────────────────
# 요건별 «무엇 때문에 걸렸나» — 원장 값 한 조각
#
# 여기 있는 것은 전부 Profile 필드를 그대로 옮긴 것이다. 계산해서 만드는 값을 두지 않는다
# (요건 판정이 이미 그 계산을 했고, 두 번째 구현은 곧 갈린다).
# 값이 없는 요건은 아예 키를 두지 않는다 — 억지로 채우면 "디폴트옵션 미설정 0건" 이 된다.
# ─────────────────────────────────────────────────────────────

def _cash(p: Profile) -> str | None:
    """고유계정대 — 금액·비중은 원장 자산군 표에서 그대로 온다(비중을 곱해 만들지 않는다)."""
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
    """투자성향 불일치 — **어느 갈래로 걸렸는지의 값**을 보여준다.

    `conditions()` 의 mis 판정은 성향에 따라 보는 쪽이 갈린다. 보수 성향은 위험자산이
    많아서, 적극·공격 성향은 반대로 원리금보장이 많아서 걸린다. 늘 위험자산 비중만 적으면
    공격투자형 고객 옆에 "위험자산 0%" 가 붙어, 왜 걸렸는지가 아니라 왜 안 걸렸는지처럼
    읽힌다(실제로 정민석이 그렇게 나왔다).
    """
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


def _cond_text(target: Target, cond: str) -> str:
    """요건 한 건의 표기 — 「요건 이름 + 걸린 값」. 이름은 CONDS 원문 그대로 쓴다."""
    name = CONDS.get(cond, cond)
    try:
        value = DETAIL.get(cond, lambda _p: None)(target.profile)
    except Exception:
        value = None  # 값 하나가 비어도 쪽지 전체가 죽지 않는다
    return f"{name} {value}" if value else name


def _customer_id(customer_id: str) -> str:
    """마스킹 표기. 뒷자리를 가리고 자릿수는 남긴다 — 자릿수까지 지우면 «어떤 형식의
    번호였는지»를 직원이 화면에서 대조할 수 없다."""
    if not MASK_ID:
        return customer_id
    head, sep, tail = customer_id.partition("-")
    return f"{head}{sep}{tail[:1]}{'*' * max(0, len(tail) - 1)}" if sep else customer_id


def _block(no: int, target: Target) -> str:
    """고객 한 명 = 머리 두 줄 + 요건 한 줄씩.

    **요건을 한 줄에 이어 붙이지 않는다.** 요건에 딸린 값 자체가 여러 조각인 경우가 있어
    (「D-17 · 7,040만원」), 가운뎃점으로 요건을 잇고 값도 가운뎃점으로 이으면 한 요건이
    세 건처럼 읽힌다. 줄바꿈이 가장 싼 구분자다.
    """
    p = target.profile
    attrs = [f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else [])
    shown = target.conds[:MAX_CONDS]
    rest = len(target.conds) - len(shown)
    lines = [f"{no}. {p.nm} ({_customer_id(p.id)})",
             f"   {' · '.join(attrs)} · 평가금액 {won(p.bal)}"]
    lines += [f"   - {_cond_text(target, c)}" for c in shown]
    if rest > 0:
        lines.append(f"   … 외 {rest}건")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 본문
# ─────────────────────────────────────────────────────────────

#: 꼬리말. **원장 기준일을 반드시 적는다** — 평가금액·보유 현황은 원장이 찍힌 날의 값이고,
#: 요건의 잔여일수·경과일은 오늘 기준이다(customer.AS_OF vs clock.today). 둘이 갈린다는
#: 사실을 쪽지가 말하지 않으면 직원은 전부 오늘 값으로 읽는다.
FOOTER = ("※ 평가금액·보유 현황은 {as_of} 원장 기준이고, 잔여일수·경과일은 {today} 기준입니다.\n"
          "※ 선정 기준은 사후관리 타겟 룰베이스입니다. 상세 근거는 에이전트 화면에서 확인하세요.")

#: 타겟이 한 명도 없을 때. 빈 쪽지를 보내지 않는다 — 받는 쪽이 «장애인지 진짜 0명인지»를
#: 가릴 수 있어야 한다.
EMPTY_BODY = "오늘 사후관리 타겟으로 선정된 고객이 없습니다."

#: 잘라낸 뒤 남기는 줄. 몇 명이 빠졌는지 밝히지 않으면 직원은 그 목록이 전부인 줄 안다.
CUT_LINE = "…외 {n}명은 화면에서 확인하세요."


def _head(count: int) -> str:
    return f"오늘의 타겟 고객 · {today().isoformat()} · {count}명"


def _foot() -> str:
    return FOOTER.format(as_of=AS_OF.isoformat(), today=today().isoformat())


def _fit(head: str, blocks: list[str], foot: str, *, joiner: str,
         cut: Callable[[int], str], max_chars: int) -> tuple[str, int]:
    """머리·고객 블록·꼬리를 상한 안에 맞춘다. **덜어내는 단위는 고객 블록이다.**

    줄 중간에서 자르면 "5억 2,00" 같은 반쪽 수치가 남고, 그건 틀린 값을 보낸 것과 같다.
    상한이 아무리 작아도 한 명은 담는다 — 아무도 없는 목록보다 낫다.
    """
    total, shown = len(blocks), len(blocks)
    while shown > 1:
        body = joiner.join([head, *blocks[:shown],
                            *([cut(total - shown)] if shown < total else []), foot])
        if len(body) <= max_chars:
            return body, shown
        shown -= 1
    return joiner.join([head, blocks[0], cut(total - 1), foot]), 1


# ─────────────────────────────────────────────────────────────
# 본문 — 텍스트
# ─────────────────────────────────────────────────────────────

def render(targets: list[Target], *, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """본문과 «실린 고객 수». HTML 이 렌더되지 않는 뷰어를 위한 형식이다."""
    head, foot = f"[{_head(len(targets))}]", _foot()
    if not targets:
        return "\n\n".join([head, EMPTY_BODY, foot]), 0
    blocks = [_block(i, t) for i, t in enumerate(targets, 1)]
    return _fit(head, blocks, foot, joiner="\n\n",
                cut=lambda n: CUT_LINE.format(n=n), max_chars=max_chars)


# ─────────────────────────────────────────────────────────────
# 본문 — HTML
#
# WorkB 쪽지는 HTML 로 볼 수 있다. 표로 만들면 훑기 좋아지지만, **쪽지 뷰어가 무엇까지
# 렌더하는지는 확인된 바 없다.** 그래서 이메일 HTML 의 규율을 그대로 따른다 — 뷰어·위생
# 처리기가 가장 많이 걷어내는 것들을 처음부터 쓰지 않는다:
#
#   · `<style>` 블록도 클래스도 쓰지 않는다 → 스타일은 전부 인라인
#   · 바깥 자원(CSS·폰트·이미지)을 부르지 않는다 → 막히면 표가 통째로 무너진다
#   · `border`·`cellpadding` 같은 옛 표 속성을 인라인 스타일과 **함께** 쓴다 → 스타일이
#     걷혀도 표의 선은 남는다
#   · 색으로 뜻을 나르지 않는다 → 흑백으로 떨어져도 읽히는 정보만 색으로 강조한다
#
# 그래도 뷰어가 태그를 그대로 보여줄 가능성은 남는다. 그 경우 직원은 태그 범벅을 보게
# 되므로 텍스트 형식(`render`)을 지우지 않고 남겨 둔다 — `FORMAT` 하나로 되돌린다.
# ─────────────────────────────────────────────────────────────

#: 표 스타일. 뜻을 나르지 않는 장식이라 걷혀도 정보가 사라지지 않는다.
_TABLE = ('border="1" cellspacing="0" cellpadding="6" '
          'style="border-collapse:collapse;font-size:13px;line-height:1.5"')
_TH = 'style="background:#f4f4f4;text-align:left;white-space:nowrap"'
_TD_NUM = 'align="right" style="white-space:nowrap"'
_MUTED = 'style="color:#777"'


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _row(no: int, target: Target) -> str:
    """고객 한 명 = 표의 한 줄. 요건은 한 칸 안에서 줄바꿈으로 나눈다 — 요건마다 행을
    나누면 고객 하나가 여러 줄이 되어 «몇 명인지»가 안 읽힌다."""
    p = target.profile
    attrs = " · ".join([f"{p.ag}세", p.rk] + ([p.club_grade] if p.club_grade else []))
    shown = target.conds[:MAX_CONDS]
    rest = len(target.conds) - len(shown)
    conds = "<br>".join(_esc(_cond_text(target, c)) for c in shown)
    if rest > 0:
        conds += f"<br><span {_MUTED}>외 {rest}건</span>"
    return (f"<tr><td {_TD_NUM}>{no}</td>"
            f"<td><b>{_esc(p.nm)}</b><br>{_esc(attrs)}"
            f"<br><span {_MUTED}>{_esc(_customer_id(p.id))}</span></td>"
            f"<td {_TD_NUM}>{_esc(won(p.bal))}</td>"
            f"<td>{conds}</td></tr>")


def render_html(targets: list[Target], *, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """HTML 본문과 «실린 고객 수».

    상한 판정은 **태그를 포함한 문자열 길이**로 한다 — 서버가 받는 것이 그 문자열이기
    때문이다. 그래서 같은 인원이라도 텍스트보다 훨씬 길다(대략 두 배 반).
    """
    head = f"<p><b>{_esc(_head(len(targets)))}</b></p>"
    foot = ('<p style="color:#777;font-size:12px">'
            + _esc(_foot()).replace("\n", "<br>") + "</p>")
    if not targets:
        return f"{head}<p>{_esc(EMPTY_BODY)}</p>{foot}", 0

    cols = ("#", "고객", "평가금액", "선정 요건")
    thead = "<tr>" + "".join(f"<th {_TH}>{_esc(c)}</th>" for c in cols) + "</tr>"
    rows = [_row(i, t) for i, t in enumerate(targets, 1)]

    # 표의 열고 닫는 태그는 «머리»와 «꼬리»에 붙여 둔다 — 행 단위로 덜어내도 표가 깨지지
    # 않아야 하고, 그러려면 잘라내기가 보는 조각이 곧 행이어야 한다.
    body, shown = _fit(f"{head}<table {_TABLE}>{thead}", rows, f"</table>{foot}",
                       joiner="", cut=lambda n: f'<tr><td colspan="{len(cols)}" {_MUTED}>'
                                                f'{_esc(CUT_LINE.format(n=n))}</td></tr>',
                       max_chars=max_chars)
    return body, shown


#: 어느 형식으로 보낼까. 행내 WorkB 쪽지가 HTML 을 렌더하므로 기본은 표다. 뷰어가 태그를
#: 그대로 보여주면 `"text"` 로 되돌린다 — 그때 직원이 보는 것은 태그 범벅이라, 되돌릴
#: 스위치가 없으면 기능 자체를 못 쓴다.
FORMAT = "html"

RENDERERS: dict[str, Callable[..., tuple[str, int]]] = {"html": render_html, "text": render}


@dataclass(frozen=True)
class Note:
    """쪽지 한 통. WorkB 가 제목·본문을 어떤 이름으로 받는지는 MCP 규격이 정한다 —
    여기서는 그 둘을 만들기만 하고, 필드명 매핑은 `send_note` 가 한다."""

    title: str
    body: str
    count: int      # 목록에 오른 고객 수(잘라내기 전)
    shown: int      # 본문에 실제로 실린 수
    truncated: bool


def daily_targets_note(*, fmt: str = "", max_chars: int = MAX_CHARS) -> Note:
    """오늘의 타겟 고객 쪽지. 이 함수 하나가 «보낼 글»의 전부다."""
    targets = today_targets()
    body, shown = RENDERERS[fmt or FORMAT](targets, max_chars=max_chars)
    return Note(title=f"오늘의 타겟 고객 {len(targets)}명 ({today().isoformat()})",
                body=body, count=len(targets), shown=shown,
                truncated=shown < len(targets))


# ─────────────────────────────────────────────────────────────
# 발송 — WorkB MCP
#
# 클라이언트는 **주입받는다.** 행내 `mcp_sdk` 는 저장소 밖 패키지라 여기서 임포트하면
# 테스트가 그 패키지 없이는 돌지 않게 되고(지금 전 테스트가 LLM 키도 사내 패키지도 없이
# 돈다), 망분리 밖에서는 아예 설치할 수도 없다. 그래서 이 모듈이 아는 것은 «어떤 모양의
# 함수를 부르면 쪽지가 나간다»뿐이고, 그 함수가 무엇인지는 부르는 쪽이 정한다.
# ─────────────────────────────────────────────────────────────

#: 주입받는 발송 함수의 모양 — `send(recipients, title, body)` 를 await 하면 원시 결과가
#: 온다. 행내 클라이언트의 `MCPClient.send_message` 가 그대로 이 모양이다.
Sender = Callable[[list[str], str, str], Awaitable[Any]]

#: 앱이 등록한 발송 함수. 등록 전에는 None 이고, 그동안 발송 시도는 «미연결»로 답한다 —
#: 조용히 성공처럼 끝나지 않는다. 등록은 앱 시작 시 한 번이다(`use_sender`).
SENDER: Sender | None = None

#: 쪽지 수신자 — **직원 본인**이다. 그래서 LLM 이 수신자를 정하는 자리가 아예 없다.
#: 대화에서 사번을 뽑아내게 두면 엉뚱한 사람에게 고객 목록이 나갈 수 있고, 그건 확인
#: 절차로도 못 막는다(직원은 자기가 승낙한 게 누구 앞인지 안 읽는다).
#: 로그인 사번이 없을 때의 폴백은 환경변수 하나뿐이고, 그것도 없으면 발송하지 않는다.
EMP_NO_ENV = "WORKB_EMP_NO"


def use_sender(fn: Sender | None) -> None:
    """행내 WorkB 클라이언트를 등록한다(앱 시작 시 1회).

        from pension_agent import workb
        workb.use_sender(MCPClient(emp_no).send_message)

    여기서 임포트하지 않고 등록받는 이유는 `mcp_sdk` 가 저장소 밖 패키지이기 때문이다 —
    임포트하면 그 패키지 없이는 테스트도 임포트도 안 된다(망분리 밖에서는 설치도 못 한다).
    """
    global SENDER
    SENDER = fn


def employee_id(explicit: str | None = None) -> str | None:
    """쪽지를 받을 직원 사번. 로그인 사번이 우선이고, 없으면 환경변수, 그것도 없으면 None.

    None 이면 발송을 제안하지 않는다 — 받을 사람을 모르는 채로 «보낼까요?» 를 묻는 것은
    승낙받을 대상이 없는 제안이다.
    """
    return (explicit or os.getenv(EMP_NO_ENV, "")).strip() or None


def validate_recipients(recipients: Any) -> list[str]:
    """수신자 목록 검증. **문자열 하나를 리스트 대신 넘기는 것을 막는다.**

    WorkB 의 `send_memo` 는 RECIPIENT 를 리스트로 받는다. 문자열 `"3902172"` 를 그대로
    넘기면 서버가 거부하는데, 사유를 분류하지 못하고 `64;ETC_ERR`(기타 오류)로만 답한다 —
    파이썬은 문자열도 시퀀스라 타입 오류 없이 그 자리까지 가고, 서버 응답도 «기타»라 어디가
    틀렸는지 아무 데서도 안 나온다. 그래서 나가기 전에 여기서 막는다.
    """
    if isinstance(recipients, str):
        raise TypeError("recipients 는 리스트여야 합니다 — 문자열 하나를 넘기면 WorkB 가 "
                        f"64;ETC_ERR 로 거부합니다: [{recipients!r}] 처럼 감싸세요")
    ids = list(recipients or [])
    if not ids or not all(isinstance(r, str) and r.strip() for r in ids):
        raise ValueError(f"recipients 가 비어 있거나 빈 값을 포함합니다: {recipients!r}")
    return ids


def _text_of(raw: Any) -> str:
    """어댑터가 돌려준 결과에서 본문 텍스트를 꺼낸다.

    형태가 버전·응답에 따라 갈린다 — 콘텐츠 블록 리스트(`[{"type":"text","text":...}]`),
    문자열, `(content, artifact)` 튜플. 어느 쪽이든 텍스트만 이어 붙인다.
    """
    if isinstance(raw, tuple) and raw:
        raw = raw[0]
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(b.get("text", "") for b in raw
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_result(raw: Any) -> dict[str, Any]:
    """발송 결과 판정 — 「보냈다」고 말해도 되는지.

    **어댑터의 성공은 서버의 성공이 아니다.** WorkB 는 실패를 `isError` 로 세우지 않고
    본문에 `{"success": false, "error": "64;ETC_ERR"}` 로 담아 보낸다. 어댑터는 `isError`
    만 보므로 그 응답을 `status="success"` 로 넘겨준다 — 본문을 까지 않으면 **거부당한
    호출이 «발송 완료»로 보고된다.**

    **판정하지 못하면 성공이 아니다.** 본문이 JSON 이 아니거나 `success` 키가 없으면
    `unknown` 이다. 모르는 것을 성공 쪽으로 접으면, 그게 바로 안 한 일을 했다고 말하는
    경로다(루트 CLAUDE.md 5번이 막으려는 것).
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


async def send_note(recipients: list[str], note: Note, *,
                    send: Sender | None = None) -> dict[str, Any]:
    """WorkB 쪽지 발송. 클라이언트가 없으면 **보내지 않고** 미연결로 답한다.

        workb.use_sender(MCPClient(emp_no).send_message)    # 앱 시작 시 1회
        await send_note(["3902172"], note)

    ━━ 승낙은 여기서 받지 않는다 ━━
    발송은 되돌릴 수 없다(CLAUDE.md 5번). 이 함수는 **직원이 승낙한 뒤에만** 불려야 하고,
    그 승낙은 대화형의 제안→확인 경로가 받는다(`consult_agent/nodes/act.py::confirm_action`
    — 지금 화면 연계가 쓰는 그 경로). 그래서 여기가 스스로 «보낼까요?»를 묻지 않는다.
    """
    ids = validate_recipients(recipients)
    send = send or SENDER
    if send is None:
        return {"status": "not_connected",
                "detail": "WorkB 클라이언트가 주입되지 않았습니다 — 본문만 생성했습니다",
                "recipients": ids, "title": note.title, "body": note.body}
    try:
        raw = await send(ids, note.title, note.body)
    except Exception as exc:
        # 실패를 성공으로 접지 않는다. 예외 종류까지 남겨야 다음 사람이 재현할 수 있다.
        return {"status": "failed", "detail": f"발송 호출이 실패했습니다: {type(exc).__name__}: {exc}",
                "error": type(exc).__name__, "recipients": ids, "title": note.title}
    return {**parse_result(raw), "recipients": ids, "title": note.title}


def send_note_sync(recipients: list[str], note: Note, *,
                   send: Sender | None = None) -> dict[str, Any]:
    """동기 문맥에서의 발송. 대화형 그래프가 동기라서 있다(`consult_agent/nodes/act.py`).

    이미 이벤트 루프 안이면 **여기서 기다릴 수 없다** — 그때는 실패로 답한다. 조용히
    «보냄»으로 끝내지 않는 것이 핵심이고, 그런 앱은 `await send_note(...)` 를 직접 쓰면 된다.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(send_note(recipients, note, send=send))
    return {"status": "failed",
            "detail": ("이벤트 루프 안에서는 동기 발송을 기다릴 수 없습니다 — "
                       "await send_note(...) 를 쓰세요"),
            "error": "RunningLoop", "recipients": list(recipients), "title": note.title}


if __name__ == "__main__":  # 아웃풋 눈으로 보기: python -m pension_agent.workb
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    n = daily_targets_note()
    print(n.body)
    print(f"\n— {len(n.body)}자 · {n.shown}/{n.count}명"
          f"{' (잘림)' if n.truncated else ''}")
