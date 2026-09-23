"""WorkB 쪽지 초안 — **무엇을 쓸지**(CLAUDE.md §10 「쪽지 보내기」).

꼴과 발송은 `pension_agent/note.py` 가 안다(표 속성·마스킹·길이 상한·MCP 클라이언트).
여기는 그 앞 단계다 — 이번 턴의 재료로 제목과 본문을 만들고, 근거를 벗어났으면 만들지
않는다.

━━ 조립식을 그만둔 이유 ━━
예전 본문은 코드가 세 덩이로 조립했다: 「{성명} 고객님 상담 내용 요약입니다」 머리말 +
검증을 통과한 이번 답변 그대로 + 고객 주요 정보 표. 그 꼴은 **상담 요약 한 종류**에만
맞는다. 직원이 쪽지로 보내려는 것은 열린 집합이고(오늘 볼 사람 목록, 옆자리에 넘기는
인수인계, 방금 확인한 제도 수치), 코드가 종류를 열거하면 나머지가 전부 「기타」가 된다.
그리고 화면 답변을 그대로 실으면 **읽는 사람이 바뀐 것을 반영하지 못한다** — 상담 코칭
말투("이 고객에게는 ~하는 게 핵심이에요")가 다른 직원 받은편지함에 그대로 들어간다.

━━ 누가 무엇을 쓰나 (루트 규칙 2) ━━

    재료의 경계          코드 — 이번 턴의 원장 + 화면이 정하는 한 가지(아래)
    문장·제목            LLM — 그 경계 안에서
    근거를 벗어났는지     코드 — `nodes/plan.screen` (화면 답변과 **같은 검사**)
    보이는 꼴(표·굵기)    코드 — LLM 은 태그를 한 글자도 쓰지 않는다
    받는 사람            코드 — `nodes/act` 가 정한다(LLM 이 사번을 만들 자리가 없다)

━━ 재료는 화면이 정한다 ━━
분기는 **둘뿐이고 종류가 아니다.**

    고객 화면이 열려 있나  →  쪽지가 쓸 수 있는 재료 (그 고객 / 오늘의 타겟 목록)
    받는 사람이 누구인가   →  가이드라인 (내 기록 / 남에게 넘기는 설명)

고객 화면이 열려 있으면 브리핑을 재료에 넣는다 — 이번 턴이 `customer` 도구를 부르지
않았더라도. 직원은 그 화면을 보면서 쪽지를 부탁하기 때문이다. 부를 때는 **그 도구를 그대로**
부른다(따로 읽는 경로를 만들지 않는다) — `_citable` 가 인용 허용 집합에서 후보 더미(pools)를
걷어내는 것 같은 판정이 그 도구 안에 있고, 두 번째 경로는 그 판정을 빠뜨린 채 같은 값을
싣게 된다. 고객 화면이 없으면 그 자리가 오늘의 타겟 목록이다(`targets` 도구).

━━ 걸리면 보내지 않는다 ━━
화면 답변은 검사에 걸리면 다시 쓰고, 그래도 걸리면 근거 원문이 나간다. 쪽지에는 그
폴백이 없다 — 근거 원문 덤프를 남의 받은편지함에 넣는 것은 답이 아니고, 보낸 쪽지는
되돌릴 수 없다(루트 규칙 5). 못 만들면 **사유를 말하고 끝낸다.**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pension_agent import note
from pension_agent.clock import today
from pension_agent.consult_agent import tools
from pension_agent.consult_agent.prompts import (
    COMPOSE_RETRY_BLOCK, MEMO_EDIT_ECHO_BLOCK, MEMO_EDIT_PROMPT, MEMO_EDIT_RECIPIENT_BLOCK, MEMO_EDIT_SYSTEM,
    MEMO_OTHER_GUIDE, MEMO_PROMPT, MEMO_SELF_GUIDE, MEMO_SYSTEM, MEMO_TABLE_BLOCK,
)
from pension_agent.consult_agent import state
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.llm import LLMError, generate, json_object

#: 본문 생성 토큰 상한. 쪽지는 훑는 글이라 화면 답변(1500)보다 짧다 — 길면 아무도 안 읽고,
#: 표에 들어갈 값을 본문이 다시 나열하기 시작한다.
MAX_TOKENS = 900

#: 검사에 걸렸을 때 다시 쓰는 횟수. compose 와 같은 이유로 **한 번**이다 — 통과할 때까지
#: 돌면 한 턴의 비용이 열린다. 폴백이 아니라 재작성이고, 두 번째도 걸리면 안 보낸다.
RETRIES = 1

#: 표 아래에 붙는 안내. 원장 기준일과 «오늘»이 갈린다는 사실을 쪽지가 말하지 않으면 직원은
#: 전부 오늘 값으로 읽는다. 문구는 목록 쪽지와 **같은 상수**를 쓴다 — 두 벌이면 곧 갈린다.
#: 뒷줄(선정 기준)은 목록 표에만 붙인다: 고객 한 명을 담은 쪽지에는 고를 목록이 없다.

KEY_INFO_HEADER = "[고객 주요 정보]"

#: 못 만들었을 때의 사유 문구. **무엇에 걸렸는지 남긴다** — "실패했어요" 만으로는 직원이
#: 다시 부탁해야 하는지 다르게 물어야 하는지 알 수 없다.
NO_MATERIAL = "이번 턴에 쪽지로 옮길 근거가 없어요. 먼저 무엇을 정리할지 물어봐 주세요."
LLM_DOWN = "쪽지 본문을 쓰지 못했어요 — {reason}."
#: 여기는 걸린 **자리**를 값까지 적는다 — 화면 답변의 폴백 머리말(`plan.fault_kinds` 는 종류만
#: 적는다)과 갈리는 지점이고, 그쪽은 뒤에 근거 원문이 따라붙어 사유 줄이 부차적인 반면 이쪽은
#: 이 한 줄이 전부다. 직원이 다시 부탁할지 다르게 물을지 정하는 근거가 그 값이다
#: (`tests/consult/memo.py` 가 「1,234」로 못박는다). 바꾸려면 그 결정부터 바꾼다.
SCREENED = ("쪽지 본문이 근거를 벗어나서 보내지 않았어요. 걸린 자리: {faults}. "
            "한 번 더 부탁하시면 다시 써볼게요.")
TOO_LONG = "쪽지 본문이 길이 상한({limit:,}자)을 넘어서 보내지 않았어요."

#: 화면에서 쪽지 초안을 감싸는 코드블록 펜스(act.offer). 초안의 일부가 아니라 **화면 장치**다 —
#: 나가는 본문(`Draft.html`)에는 없고, 세션 기록에서 재료를 만들 때는 뗀다(tools/history._strip_devices).
#: 감싸는 이유는 «여기까지가 쪽지»를 직원이 화면에서 가릴 수 있어야 하기 때문이다 — 초안이
#: 답변 자리를 통째로 차지하므로, 표시가 없으면 에이전트가 하는 말과 구별되지 않는다.
#: 값은 state.py 가 갖는다(떼는 쪽 tools/ 가 effects/ 를 임포트하지 않도록). 여기서 재노출한다.
FENCE = state.FENCE


@dataclass(frozen=True)
class Draft:
    """승낙받기 전의 쪽지 한 통.

    `text` 와 `html` 이 둘 다 있는 이유는 **직원이 읽는 것과 나가는 것이 같아야 하기**
    때문이다(§10). 화면에 태그를 보여줄 수는 없으므로 평문을 보여주고, 나가는 것은 같은
    글을 코드가 옮긴 HTML 이다 — 옮기는 것은 꼴뿐이고 문장은 건드리지 않는다.
    """

    title: str
    text: str
    html: str
    to: str
    recipients: list[str] = field(default_factory=list)
    # 초안을 **다시 조립할 수 있게** 나눠 둔 것. 직원이 초안을 고쳐 달라고 하면(`revise`)
    # 바뀌는 것은 LLM 이 쓴 본문뿐이고, 코드가 붙인 값 표·꼬리말(`tail_html`)은 그대로다 —
    # 표는 원장 값이라 대화로 고칠 대상이 아니다(§3 「화면의 계산값은 대화로 고칠 수 없다」).
    body: str = ""
    tail_html: str = ""
    tail_note: str = ""       # 화면 미리보기에서 표 자리에 서는 한 줄(「(아래에 … 표가 붙습니다)」)
    # 예약 발송 시각(ISO)과 화면 표기. 비어 있으면 즉시 발송이다(§10 「예약 발송」). 받는 사람과
    # 같은 **실행 인자**라 코드가 정하고(`effects/schedule.py`), 쪽지 본문·제목에는 싣지 않는다.
    send_at: str = ""
    send_label: str = ""
    # 이름으로 보내는 쪽지(§10 「이름으로 보내기」). 있으면 `recipients` 는 비어 있고, 사번은
    # 승낙한 뒤 이름 검색 발송이 정한다(1명이면 그 호출에서 발송된다).
    user_name: str = ""
    group_name: str = ""


# ─────────────────────────────────────────────────────────────
# 재료 — 화면이 정하는 한 가지를 원장에 더한다
# ─────────────────────────────────────────────────────────────

def material(state: AgentState) -> list[tools.Evidence]:
    """이 쪽지가 쓸 수 있는 재료. 이번 턴의 원장 + 화면이 정하는 한 가지.

    더하는 것은 **도구를 불러서** 더한다 — 같은 값을 읽는 두 번째 경로를 만들지 않는다
    (모듈 머리말). 도구가 못 찾으면 그냥 원장뿐이고, 그 상태로도 쪽지는 쓸 수 있다.
    """
    ev = list(state.get("evidence") or [])
    used = {e["tool"] for e in ev}
    want, query = (("customer", "고객 브리핑 자료") if state.get("customer_id")
                   else ("targets", "오늘의 타겟 고객"))
    if want not in used:
        try:
            found = tools.run(want, state, query)
        except LLMError:
            found = None      # 재료 하나를 못 더한 것이지 쪽지를 못 쓰는 것이 아니다
        if found is not None:
            ev.append(found)
    return ev


# ─────────────────────────────────────────────────────────────
# 코드가 붙이는 표 — 값은 여기서 새로 계산하지 않는다
# ─────────────────────────────────────────────────────────────

_TD_LABEL = 'align="center" style="text-align:center;white-space:nowrap"'


def _key_info(customer_id: str) -> list[tuple[str, str]]:
    """고객 주요 정보 6항목. 값은 전부 strategy_agent 산출 문자열을 옮긴 것이다.

    같은 항목을 `customer` 도구가 재료로 싣는 dict 에서 읽는다 — 브리핑 화면 상단과 같은
    문자열이라 «화면에는 3억, 쪽지에는 2.9억»이 생길 수 없다.
    """
    from pension_agent.strategy_agent import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.strategy_agent import engine  # noqa: PLC0415

    profile = strategy_customer.get_profile(customer_id)
    if profile is None:
        return []
    facts = engine.prepare(profile)
    header, state = facts["customer"], facts["account_state"]
    # 성립 요건은 `코드:이름` 이다 — 코드(`tax`·`add`)는 직원에게 뜻이 없으므로 이름만 싣는다.
    reasons = [c.split(":", 1)[1] if ":" in c else c for c in facts.get("conditions") or []]
    return [
        ("연령 · 투자성향", f"{header['연령']}세 · {header['투자성향']}"),
        ("평가금액", str(header["평가금액"])),
        ("수익률(1년)", str(header["수익률"])),
        ("연금개시", f"요건 {state['연금개시요건']} · {state['연금개시']}"),
        ("세액공제 잔여한도", str(state["세액공제_잔여한도"])),
        ("관리 사유", " · ".join(reasons) if reasons else "없음"),
    ]


def _key_info_table(customer_id: str) -> str:
    """고객 주요 정보 표. 프로파일이 없거나 산출에 실패하면 **붙이지 않는다** —
    빈 칸을 «미확인»으로 채우면 그 문자열이 쪽지로 나간다."""
    try:
        rows = _key_info(customer_id)
    except Exception:
        return ""
    if not rows:
        return ""
    body = "".join(f"<tr><td {_TD_LABEL}><b>{note.esc(k)}</b></td><td>{note.esc(v)}</td></tr>"
                   for k, v in rows)
    return f"<table {note.TABLE}>{body}</table>"


def table_for(state: AgentState, evidence: list[tools.Evidence]) -> tuple[str, str]:
    """본문 아래에 붙일 표와 «그게 무엇인지». 붙일 것이 없으면 ("", "").

    고르는 축은 재료와 같다 — 고객 화면이 열려 있으면 그 고객의 값, 아니면 오늘의 타겟
    목록이다. 원장에 타겟 재료가 실리지 않은 턴에는 목록 표를 붙이지 않는다: 답변이 쓰지도
    않은 목록을 쪽지가 들고 나가는 셈이 된다.
    """
    if state.get("customer_id"):
        found = _key_info_table(state["customer_id"])
        return (f"<b>{note.esc(KEY_INFO_HEADER)}</b><br>{found}",
                "이 고객의 연령·투자성향·평가금액·수익률·연금개시·세액공제 잔여한도·관리 사유") \
            if found else ("", "")
    if not any(e["tool"] == "targets" for e in evidence):
        return "", ""
    targets = note.today_targets()
    if not targets:
        return "", ""
    table, _shown = note.targets_table(targets)
    return table, "오늘의 타겟 고객 목록(순번·이름·나이·성향·평가금액·선정 요건)"


# ─────────────────────────────────────────────────────────────
# 평문 → HTML
#
# WorkB 쪽지 뷰어는 표는 렌더하지만 **인라인 style 을 걷어낸다**(2026-09-03 실물 확인 —
# 지정한 배경색과 글자 크기가 화면에 나타나지 않았다). 그래서 여백·글자 크기를 style 로
# 만들려는 시도는 전부 무효였고, 블록 요소(<p>·<div>)도 뷰어가 자기 간격을 얹는다.
# 남는 것은 <br>·<b>·표의 옛 속성뿐이고, 여기서 쓰는 것도 그것뿐이다.
# ─────────────────────────────────────────────────────────────

def to_html(text: str) -> str:
    """평문 본문을 쪽지 HTML 로 옮긴다. **문장은 건드리지 않는다** — 꼴만 바꾼다.

    대괄호로만 이뤄진 줄(`[고객 주요 정보]`)은 소제목으로 보고 굵게 세운다. 들여쓴 줄은
    `&nbsp;` 로 폭을 남긴다 — HTML 은 연속 공백을 접기 때문에, 그냥 옮기면 목록의 층이
    통째로 무너진다.
    """
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        marked = note.esc(stripped)
        if stripped.startswith("[") and stripped.endswith("]"):
            marked = f"<b>{marked}</b>"
        out.append("&nbsp;" * indent + marked)
    return "<br>".join(out)


def _footer_html(*, rule: bool) -> str:
    from pension_agent.strategy_agent.customer import AS_OF  # noqa: PLC0415
    lines = [note.FOOTER_ASOF.format(as_of=AS_OF.isoformat(), today=today().isoformat())]
    if rule:
        lines.append(note.FOOTER_RULE)
    return "<br>".join(note.esc(x) for x in lines)


# ─────────────────────────────────────────────────────────────
# 초안 — LLM 이 쓰고 코드가 검사한다
# ─────────────────────────────────────────────────────────────

#: LLM 이 평문에 섞어 쓰는 LaTeX 수식 기호 — WorkB 는 렌더하지 않아 `$\rightarrow$` 가 글자
#: 그대로 남는다(2026-09-23 행내 실측, 초안 고치기의 «개조식으로» 턴). 흔한 것만 글자로 옮기고,
#: 남은 `$…$` 는 달러 기호만 뗀다.
_LATEX = {r"\rightarrow": "→", r"\to": "→", r"\leftarrow": "←", r"\Rightarrow": "⇒",
          r"\times": "×", r"\cdot": "·", r"\ge": "≥", r"\geq": "≥", r"\le": "≤", r"\leq": "≤",
          r"\sim": "~", r"\%": "%"}
_MATH = re.compile(r"\$([^$\n]{1,40})\$")


def _plain_math(text: str) -> str:
    def one(m: re.Match) -> str:
        inner = m.group(1).strip()
        for k, v in _LATEX.items():
            inner = inner.replace(k, v)
        return inner.replace("\\", "").strip()
    return _MATH.sub(one, text)


def _clean_body(body: str) -> str:
    """지시를 어긴 꼴만 걷어낸다 — 마크다운 표·강조·LaTeX 수식. **문장은 고치지 않는다.**

    걷어내는 이유는 그것이 WorkB 에서 렌더되지 않아 `| 항목 | 값 |` 이 글자 그대로 남기
    때문이다. 지시로만 막으면 어겼을 때 아무도 모른다.
    """
    # 표 구분줄만 걷는다 — `|` 가 있는 줄이다. `-----` 만 있는 줄은 직원이 «줄 그어서
    # 정리해줘»로 넣게 한 구분선일 수 있다(초안 수정 — `revise`).
    lines = [ln for ln in body.replace("\r\n", "\n").split("\n")
             if not ("|" in ln and re.match(r"^\s*\|?\s*[-:|\s]{5,}\|?\s*$", ln))]
    out = []
    for ln in lines:
        if ln.strip().startswith("|") and ln.count("|") >= 2:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            ln = " · ".join(c for c in cells if c)
        out.append(re.sub(r"\*\*|^\s*#+\s*", "", ln))
    return _plain_math("\n".join(out).strip())


def _generate(prompt: str, name: str) -> tuple[str, str]:
    raw = generate(prompt, max_tokens=MAX_TOKENS, system=MEMO_SYSTEM, name=name)
    obj = json_object(raw) or {}
    title = " ".join(str(obj.get("title") or "").split())
    body = _clean_body(str(obj.get("body") or ""))
    return title, body


def draft(state: AgentState, *, recipients: list[str], to: str,
          to_self: bool) -> tuple[Draft | None, str]:
    """쪽지 초안 하나. 만들지 못하면 `(None, 사유)` — 사유는 그대로 직원에게 나간다.

    받는 사람은 **인자로 받는다.** 여기서 대화를 읽어 사번을 뽑아내면 LLM 이 쓴 문장 하나로
    수신자가 갈릴 수 있고, 그건 확인 절차로도 못 막는다(직원은 자기가 승낙한 게 누구 앞인지
    안 읽는다). 정하는 것은 `nodes/act` 의 규칙이다.
    """
    evidence = material(state)
    if not evidence:
        return None, NO_MATERIAL

    table, what = table_for(state, evidence)
    listed = not state.get("customer_id")      # 목록 표인가 — 꼬리말 한 줄이 여기서 갈린다
    prompt = MEMO_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        history_block=format_history(state.get("history")),
        question=state.get("question") or "",
        answer=(state.get("answer") or "").strip(),
        guide=MEMO_SELF_GUIDE if to_self else MEMO_OTHER_GUIDE,
        table_block=MEMO_TABLE_BLOCK.format(what=what) if table else "",
    )

    # 화면 답변은 이번 턴에 이미 같은 검사를 통과한 글이라 되받아 적을 수 있다(§6 echoable
    # 규약 — 「직원이 방금 말한 값을 옮겨 적는 것은 지어낸 것이 아니다」의 같은 자리).
    # 통과하지 못한 답변은 애초에 화면에 나가지 않는다.
    echoable = [state.get("question") or "", state.get("answer") or ""]

    from pension_agent.consult_agent.nodes import plan  # noqa: PLC0415 — 순환 임포트 회피

    try:
        title, body = _generate(prompt, "consult.memo")
        faults: list[str] = []
        for attempt in range(RETRIES + 1):
            if not title or not body:
                return None, LLM_DOWN.format(reason="본문을 규격대로 받지 못했어요")
            # 제목과 본문을 **함께** 건다. 본문만 검사하면 근거 밖 수치가 제목으로 새어나간다.
            faults = plan.screen(f"{title}\n{body}", evidence, "", prompt_texts=echoable)
            if not faults:
                break
            if attempt >= RETRIES:
                return None, SCREENED.format(faults=" / ".join(faults[:3]))
            title, body = _generate(
                prompt + COMPOSE_RETRY_BLOCK.format(faults="\n".join(f"- {f}" for f in faults[:8])),
                "consult.memo.retry")
    except LLMError as exc:
        # 프로바이더 응답 본문이 통째로 안내에 실리지 않게 한 줄로 자른다(plan.short_reason
        # 머리말 — 화면 답변 쪽과 같은 규칙이고, 전문은 로그·트레이스에 남는다).
        return None, LLM_DOWN.format(reason=plan.short_reason(f"{type(exc).__name__}: {exc}"))

    tail_html = "<br><br>".join([table, _footer_html(rule=listed)]) if table else ""
    tail_note = f"(아래에 {what} 표가 붙습니다)" if table else ""
    return assemble(title, body, tail_html=tail_html, tail_note=tail_note,
                    to=to, recipients=recipients)


def assemble(title: str, body: str, *, tail_html: str, tail_note: str, to: str,
             recipients: list[str], send_at: str = "", send_label: str = "",
             user_name: str = "", group_name: str = "") -> tuple[Draft | None, str]:
    """제목·본문 + 코드가 붙인 꼬리(값 표·꼬리말) → 초안 한 통. 길이 상한을 넘으면 `(None, 사유)`.

    처음 초안(`draft`)과 고친 초안(`revise`·`verbatim`)이 **같은 조립**을 거친다 — 두 벌이면
    한쪽만 길이 상한을 보거나 한쪽만 표를 붙이는 식으로 곧 갈린다.
    """
    parts = [to_html(body)] + ([tail_html] if tail_html else [])
    markup = "<br><br>".join(parts)
    if len(markup) > note.MAX_CHARS:
        # 조용히 잘라내지 않는다 — 잘린 쪽지는 «전부인 줄» 읽힌다(note.MAX_CHARS 머리말).
        return None, TOO_LONG.format(limit=note.MAX_CHARS)
    preview = f"{body}\n\n{tail_note}" if tail_note else body
    return Draft(title=title, text=preview, html=markup, to=to, recipients=list(recipients),
                 body=body, tail_html=tail_html, tail_note=tail_note,
                 send_at=send_at, send_label=send_label,
                 user_name=user_name, group_name=group_name), ""


# ─────────────────────────────────────────────────────────────
# 초안 고치기 — 직원이 읽은 초안을 직원의 지시대로 (§10 「쪽지 초안은 고칠 수 있다」)
#
# 초안이 걸린 다음 턴에 직원이 「앞에 --를 붙여줘」·「줄바꿈해서 정리해줘」라고 하면, 쪽지를
# 처음부터 다시 쓰지 않고(`draft`) **그 초안을** 고친다. 처음부터 다시 쓰면 직원이 두세 번
# 공들여 고친 문장이 매번 사라진다(2026-09-23 실측 — 「저거 쪽지 내용 사번 …한테 보내줘」가
# 고친 글이 아니라 새로 쓴 글을 내밀었다).
#
#   재료        직전 초안 + 직원의 이번 말 + **이번 상담에서 나간 답변** — 지식베이스를 다시
#               찾지 않는다. 답변을 넣는 이유: 「안내 가능한 상품 6종 내용 담아서」의 6종은
#               초안이 아니라 두 턴 전 답변에 있다. 그 재료가 없던 동안 LLM 은 «6종의 내용을
#               알려달라»고 되물었다(2026-09-23 실측). 그 답변은 이미 검사를 통과해 화면에
#               나간 글이라 지어낸 값이 아니다
#   검사        같은 `plan.screen`. 허용 범위는 «직전 초안 + 직원의 이번 말 + 그 답변들»이다
#   직원이 적은 값  원장과 달라도 직원이 적은 대로 쓴다 — 직원이 직접 확인한 값이다
# ─────────────────────────────────────────────────────────────

#: 수정 지시를 처리하지 못했을 때 — 초안은 직전 것 그대로 남는다.
EDIT_DOWN = "초안 수정 지시를 처리하지 못했어요 — {reason}. 직전 초안을 그대로 둡니다."
#: 고친 초안이 검사에 걸렸을 때. 걸린 자리를 값까지 적는다(`SCREENED` 와 같은 이유).
#: 다시 써도 직원의 말이 본문에 통째로 남았을 때. 초안은 그대로 둔다.
EDIT_ECHO = ("말씀하신 문장이 쪽지 본문에 그대로 들어가서 반영하지 않았어요. 넣을 내용만 "
             "말씀해 주세요(예: «10월 7일 재접촉 예정이라고 넣어줘»). 직전 초안을 그대로 둡니다.")
#: 본문 한 줄이 직원의 말과 이만큼 겹치면 «말을 옮겨 적었다»로 본다(글자·숫자만 센다).
#: 짧은 말(「--붙여줘」)은 보지 않는다 — 직원이 따옴표로 준 짧은 문장을 넣는 지시와 갈리지 않는다.
ECHO_MIN_CHARS = 12
ECHO_RATIO = 0.8
#: 에이전트에게 하는 요청의 끝 — 말의 일부만 옮긴 줄은 이것으로 끝날 때만 옮긴 것이다.
#: 없으면 「안녕하세요 … 쪽지드립니다 넣어줘」에서 넣으라고 한 문장까지 옮긴 것으로 걸린다.
_REQUEST_END = re.compile(r"(?:줘|줄래|주라|해봐|할래)$")


def _squash(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def echoed(instruction: str, title: str, body: str) -> bool:
    """고친 제목·본문의 한 줄이 직원의 말을 통째로 옮긴 것인가(§10 — 2026-09-23 행내 실측).

    「10월 7일에 이 고객에게 재접촉 예정인데 … 쪽지로 보내줘」가 본문 첫 줄에 그대로 들어갔다.
    줄이 말 전체를 담거나, 말의 대부분(`ECHO_RATIO`)이 그 줄이고 그 줄이 요청(「…해줘」)으로
    끝나면 옮긴 것이다. 직원이 넣으라고 준 문장만 들어간 줄은 요청으로 끝나지 않아 걸리지 않는다.
    """
    said = _squash(instruction)
    if len(said) < ECHO_MIN_CHARS:
        return False
    for line in [title, *(body or "").splitlines()]:
        got = _squash(line)
        if len(got) < ECHO_MIN_CHARS:
            continue
        if said in got or (got in said and len(got) >= ECHO_RATIO * len(said)
                           and _REQUEST_END.search(got)):
            return True
    return False


EDIT_SCREENED = ("고친 초안에 근거 밖 내용이 들어가서 반영하지 않았어요. 걸린 자리: {faults}. "
                 "직전 초안을 그대로 둡니다.")


#: 지시 밖에서 «함께 고친 것»의 코드 → 화면 표시(§10 「함께 고친 것」). LLM 은 코드만 돌려주고
#: 표시 문장은 여기가 정한다 — LLM 이 설명을 쓰게 하면 문장 모양이 매번 달라지고 길어진다.
#: 목록 밖 코드는 표시하지 않고 로그에만 남는다(`act._memo_reply`).
ALSO_LABELS = {
    "dup": "겹치는 인사·맺음 정리",
    "address": "호칭을 받는 사람에 맞춤",
    "tone": "말투 통일",
    "ref": "지운 내용을 가리키는 문장 정리",
    "number": "번호 다시 매김",
    "title": "제목을 본문에 맞춤",
}
ALSO_HEAD = "함께 고친 것: {items}"


def also_line(codes: list[str]) -> str:
    """«함께 고친 것» 한 줄. 목록 안 코드가 없으면 ""(줄을 쓰지 않는다)."""
    items = [ALSO_LABELS[c] for c in dict.fromkeys(codes) if c in ALSO_LABELS]
    return ALSO_HEAD.format(items=" · ".join(items)) if items else ""


@dataclass(frozen=True)
class Revision:
    """초안 고치기의 결과 하나.

    kind      edited(고쳤다) · not_edit(고치라는 말이 아니다) · ask(새 값을 되묻는다) ·
              screened(고친 것이 검사에 걸렸다) · echo(직원의 말을 본문에 그대로 옮겼다) ·
              down(LLM 이 죽었다)
    """

    kind: str
    draft: Draft | None = None
    ask: str = ""
    reason: str = ""
    added: list[str] = field(default_factory=list)     # 직원이 적어 새로 들어간 값
    removed: list[str] = field(default_factory=list)   # 그 대신 빠진 직전 초안의 값
    also: list[str] = field(default_factory=list)      # 지시 밖에서 함께 고친 것(LLM 이 밝힌 코드)


def from_pending(pending: dict) -> Draft:
    """걸려 있던 제안(`pending_action`)에서 초안을 되살린다. 조립한 값을 그대로 쓴다 —
    여기서 다시 조립하면 직원이 읽은 것과 한 글자라도 다른 초안이 설 수 있다."""
    body = pending.get("body")
    return Draft(title=pending.get("title") or "", text=pending.get("text") or "",
                 html=pending.get("html") or "", to=pending.get("to") or "",
                 recipients=list(pending.get("recipients") or []),
                 body=body if body is not None else (pending.get("text") or ""),
                 tail_html=pending.get("tail_html") or "", tail_note=pending.get("tail_note") or "",
                 send_at=pending.get("send_at") or "", send_label=pending.get("send_label") or "",
                 user_name=pending.get("user_name") or "", group_name=pending.get("group_name") or "")


def readdress(found: Draft, recipients: list[str], to: str, user_name: str = "",
              group_name: str = "") -> Draft:
    """받는 사람만 바꾼다 — 본문은 한 글자도 안 바뀐다(§10 결정: 고친 글을 지킨다).
    사번으로 바꾸면 이름은 지우고, 이름으로 바꾸면 사번을 비운다(둘 중 하나만 받는 사람이다)."""
    from dataclasses import replace  # noqa: PLC0415
    return replace(found, recipients=list(recipients), to=to, user_name=user_name,
                   group_name=group_name)


def reschedule(found: Draft, send_at: str, send_label: str) -> Draft:
    """발송 시각만 바꾼다(빈 값이면 즉시 발송으로 되돌린다). 본문은 그대로다."""
    from dataclasses import replace  # noqa: PLC0415
    return replace(found, send_at=send_at, send_label=send_label)


def verbatim(found: Draft, body: str) -> tuple[Draft | None, str]:
    """직원이 «이대로·똑같이» 보내라고 붙여넣은 글을 **그대로** 본문으로 삼는다.

    LLM 을 거치지 않고 검사도 하지 않는다 — 글 전체가 직원이 직접 적은 것이다(§10 결정).
    코드가 붙인 값 표·꼬리말은 그대로 두고, 더미 게이트·개인정보 마스킹·길이 상한은
    발송 경로가 그대로 건다(`actions.send_memo` · `note.py`)."""
    return assemble(found.title, body.strip(), tail_html=found.tail_html,
                    tail_note=found.tail_note, to=found.to, recipients=found.recipients,
                    send_at=found.send_at, send_label=found.send_label,
                    user_name=found.user_name, group_name=found.group_name)


#: 초안 고치기에 싣는 이번 상담 답변 — 최근 몇 개 · 전체 몇 자까지. 대화 전체를 싣지 않는
#: 이유는 `last_answer` 가 한 턴만 싣는 것과 같다(인용 허용 집합이 대화 전체가 되면 오래된
#: 답변의 수치가 아무 문장에나 근거를 대준다).
EDIT_ANSWERS = 4
EDIT_ANSWERS_CHARS = 4000


def session_answers(history: list[dict] | None) -> list[str]:
    """이번 상담에서 화면에 나간 답변(최근 것부터 `EDIT_ANSWERS` 개). 쪽지 초안 턴은 뺀다 —
    지금 고치는 초안이 이미 재료이고, 앞선 초안은 직원이 고쳐서 버린 글이다."""
    out: list[str] = []
    total = 0
    for turn in reversed(history or []):
        text = (turn or {}).get("answer") or ""
        if not text or FENCE in text:
            continue
        text = tools._strip_devices(text)
        if total + len(text) > EDIT_ANSWERS_CHARS:
            break
        out.append(text)
        total += len(text)
        if len(out) >= EDIT_ANSWERS:
            break
    return list(reversed(out))


def revise(found: Draft, instruction: str, history: list[dict] | None,
           recipient: tuple[str, str, str] | None = None) -> Revision:
    """직원의 지시로 초안을 고친다. 고치라는 말이 아니면 `not_edit` 을 돌려준다.

    «고치라는 지시인가»의 판정과 고치기를 **한 번의 호출**로 한다 — 둘을 나누면 초안이 걸린
    턴마다 LLM 왕복이 하나 는다. 판정이 애매하면 고치지 않는 쪽이다(프롬프트) — 새 질문을
    초안 수정으로 읽으면 질문에 대한 답 대신 엉뚱한 초안이 서지만, 반대는 초안이 사라질 뿐
    나가는 것이 없다.

    `recipient` 는 받는 사람이 바뀌었고 본문에 옛 받는 사람의 이름이 있을 때만 온다 —
    (옛 표기, 새 표기, 옛 이름). 그때 LLM 은 호칭을 맞춘다(§10 「함께 고친 것」 address).
    이름이 본문에 있는지는 코드가 보고(`act._address_change`), 어떻게 고칠지는 LLM 이 정한다.
    """
    from pension_agent import verify  # noqa: PLC0415
    from pension_agent.consult_agent.nodes import plan  # noqa: PLC0415 — 순환 임포트 회피

    answers = session_answers(history)
    prompt = MEMO_EDIT_PROMPT.format(
        title=found.title, body=found.body, question=instruction,
        answers_block=("<이번 상담에서 나간 답변>\n" + "\n\n---\n\n".join(answers)
                       + "\n</이번 상담에서 나간 답변>\n") if answers else "",
        history_block=format_history(history),
        recipient_block=MEMO_EDIT_RECIPIENT_BLOCK.format(old=recipient[0], new=recipient[1],
                                                         old_name=recipient[2])
        if recipient else "",
        table_note=f"(본문 아래에 코드가 붙이는 표는 고칠 수 없다 — {found.tail_note})"
        if found.tail_note else "")
    # 본문에 직원의 말을 통째로 옮겼으면 한 번만 다시 쓰게 한다(규칙 9). 이 경우에만 왕복이 는다.
    for attempt in range(2):
        try:
            raw = generate(prompt + (MEMO_EDIT_ECHO_BLOCK if attempt else ""), max_tokens=MAX_TOKENS,
                           system=MEMO_EDIT_SYSTEM, name="consult.memo.edit")
        except LLMError as exc:
            return Revision("down", reason=plan.short_reason(f"{type(exc).__name__}: {exc}"))
        obj = json_object(raw) or {}
        if not obj.get("edit"):
            return Revision("not_edit")
        ask = " ".join(str(obj.get("ask") or "").split())
        if ask:
            return Revision("ask", ask=ask)
        title = " ".join(str(obj.get("title") or "").split()) or found.title
        body = _clean_body(str(obj.get("body") or ""))
        if not body:
            return Revision("down", reason="고친 본문을 규격대로 받지 못했어요")
        if not echoed(instruction, title, body):
            break
    else:
        return Revision("echo", reason="직원의 말을 본문에 그대로 옮김")

    # 허용 범위는 «직전 초안 + 직원의 이번 말»이다. 직전 초안은 이미 원장에 대고 검사를
    # 통과한 글이고, 직원의 말에 적힌 값은 직원이 직접 확인한 값이다 — 원장 값과 달라도 이긴다.
    # 그래서 관계 검사(값–조건 짝)도 여기서는 걸지 않는다: 재료에 관계 선언이 없다.
    # 걸리는 것은 **둘 어디에도 없는** 값 — LLM 이 옮기다 틀리거나 지어낸 것이다.
    # 받는 사람 표기(「사번 3902175」·「김국민 님」)도 허용 범위다 — 호칭을 맞추며 옮겨 적는 값이다.
    allowed = "\n".join([found.title, found.body, instruction, *answers, *(recipient or ())])
    ledger = tools.Evidence(
        tool="memo_draft", query="", text=allowed, atomic=[], notices=[], notice_scopes=[],
        source_keys={}, allow=[allowed], related=[], marks=[], sources=[], meta={})
    faults = plan.screen(f"{title}\n{body}", [ledger], instruction)
    if faults:
        return Revision("screened", reason=" / ".join(faults[:3]))
    made, why = assemble(title, body, tail_html=found.tail_html, tail_note=found.tail_note,
                         to=found.to, recipients=found.recipients,
                         send_at=found.send_at, send_label=found.send_label,
                    user_name=found.user_name, group_name=found.group_name)
    if made is None:
        return Revision("screened", reason=why)
    before, after = verify.numbers(f"{found.title}\n{found.body}"), verify.numbers(f"{title}\n{body}")
    said = verify.numbers(instruction)
    also = [str(c).strip().lower() for c in (obj.get("also") or []) if isinstance(c, str)]
    return Revision("edited", draft=made,
                    added=sorted((after - before) & said), removed=sorted(before - after),
                    also=also)
