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

import json
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
MAX_CHARS = 2000

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


@dataclass(frozen=True)
class Note:
    """쪽지 한 통. WorkB 가 제목·본문을 어떤 이름으로 받는지는 MCP 규격이 정한다 —
    여기서는 그 둘을 만들기만 하고, 필드명 매핑은 `send_note` 가 한다."""

    title: str
    body: str
    count: int      # 목록에 오른 고객 수(잘라내기 전)
    shown: int      # 본문에 실제로 실린 수
    truncated: bool


def render(targets: list[Target], *, max_chars: int = MAX_CHARS) -> tuple[str, int]:
    """본문과 «실린 고객 수». 상한을 넘으면 뒤에서부터 고객 블록 단위로 덜어낸다."""
    head = f"[오늘의 타겟 고객] {today().isoformat()} · {len(targets)}명"
    foot = FOOTER.format(as_of=AS_OF.isoformat(), today=today().isoformat())
    if not targets:
        return "\n\n".join([head, EMPTY_BODY, foot]), 0

    blocks = [_block(i, t) for i, t in enumerate(targets, 1)]
    shown = len(blocks)
    while shown > 1:
        cut = ([f"…외 {len(targets) - shown}명은 화면에서 확인하세요."]
               if shown < len(targets) else [])
        body = "\n\n".join([head, *blocks[:shown], *cut, foot])
        if len(body) <= max_chars:
            return body, shown
        shown -= 1
    return "\n\n".join([head, blocks[0],
                        f"…외 {len(targets) - 1}명은 화면에서 확인하세요.", foot]), 1


def daily_targets_note(*, max_chars: int = MAX_CHARS) -> Note:
    """오늘의 타겟 고객 쪽지. 이 함수 하나가 «보낼 글»의 전부다."""
    targets = today_targets()
    body, shown = render(targets, max_chars=max_chars)
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
    """WorkB 쪽지 발송. `send` 를 주지 않으면 **보내지 않고** 미연결로 답한다.

        from workb_mcp import MCPClient                     # 행내 클라이언트
        client = MCPClient(emp_no)
        await send_note(["3902172"], note, send=client.send_message)

    ━━ 승낙은 여기서 받지 않는다 ━━
    발송은 되돌릴 수 없다(CLAUDE.md 5번). 이 함수는 **직원이 승낙한 뒤에만** 불려야 하고,
    그 승낙은 대화형의 제안→확인 경로가 받는다(`consult_agent/nodes/act.py::confirm_action`
    — 지금 화면 연계가 쓰는 그 경로). 그래서 여기가 스스로 «보낼까요?»를 묻지 않는다.
    """
    ids = validate_recipients(recipients)
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


if __name__ == "__main__":  # 아웃풋 눈으로 보기: python -m pension_agent.workb
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    n = daily_targets_note()
    print(n.body)
    print(f"\n— {len(n.body)}자 · {n.shown}/{n.count}명"
          f"{' (잘림)' if n.truncated else ''}")
