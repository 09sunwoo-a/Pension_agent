"""WorkB 쪽지 초안 — **무엇을 쓸지**(CLAUDE.md §10 「쪽지 보내기」).

꼴과 발송은 `pension_agent/workb.py` 가 안다(표 속성·마스킹·길이 상한·MCP 클라이언트).
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

import html
import json
import re
from dataclasses import dataclass, field

from pension_agent import workb
from pension_agent.clock import today
from pension_agent.consult_agent import tools
from pension_agent.consult_agent.prompts import (
    COMPOSE_RETRY_BLOCK, MEMO_OTHER_GUIDE, MEMO_PROMPT, MEMO_SELF_GUIDE, MEMO_SYSTEM,
    MEMO_TABLE_BLOCK,
)
from pension_agent.consult_agent.state import AgentState, format_history
from pension_agent.llm import LLMError, generate

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
SCREENED = ("쪽지 본문이 근거를 벗어나서 보내지 않았어요. 걸린 자리: {faults}. "
            "한 번 더 부탁하시면 다시 써볼게요.")
TOO_LONG = "쪽지 본문이 길이 상한({limit:,}자)을 넘어서 보내지 않았어요."


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


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


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
    body = "".join(f"<tr><td {_TD_LABEL}><b>{_esc(k)}</b></td><td>{_esc(v)}</td></tr>"
                   for k, v in rows)
    return f"<table {workb.TABLE}>{body}</table>"


def table_for(state: AgentState, evidence: list[tools.Evidence]) -> tuple[str, str]:
    """본문 아래에 붙일 표와 «그게 무엇인지». 붙일 것이 없으면 ("", "").

    고르는 축은 재료와 같다 — 고객 화면이 열려 있으면 그 고객의 값, 아니면 오늘의 타겟
    목록이다. 원장에 타겟 재료가 실리지 않은 턴에는 목록 표를 붙이지 않는다: 답변이 쓰지도
    않은 목록을 쪽지가 들고 나가는 셈이 된다.
    """
    if state.get("customer_id"):
        found = _key_info_table(state["customer_id"])
        return (f"<b>{_esc(KEY_INFO_HEADER)}</b><br>{found}",
                "이 고객의 연령·투자성향·평가금액·수익률·연금개시·세액공제 잔여한도·관리 사유") \
            if found else ("", "")
    if not any(e["tool"] == "targets" for e in evidence):
        return "", ""
    targets = workb.today_targets()
    if not targets:
        return "", ""
    table, _shown = workb.targets_table(targets)
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
        marked = _esc(stripped)
        if stripped.startswith("[") and stripped.endswith("]"):
            marked = f"<b>{marked}</b>"
        out.append("&nbsp;" * indent + marked)
    return "<br>".join(out)


def _footer_html(*, rule: bool) -> str:
    from pension_agent.strategy_agent.customer import AS_OF  # noqa: PLC0415
    lines = [workb.FOOTER_ASOF.format(as_of=AS_OF.isoformat(), today=today().isoformat())]
    if rule:
        lines.append(workb.FOOTER_RULE)
    return "<br>".join(_esc(x) for x in lines)


# ─────────────────────────────────────────────────────────────
# 초안 — LLM 이 쓰고 코드가 검사한다
# ─────────────────────────────────────────────────────────────

def _json_obj(text: str) -> dict:
    """LLM 응답에서 JSON 객체만 꺼낸다. 못 찾으면 빈 dict."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        val = json.loads(m.group())
    except ValueError:
        return {}
    return val if isinstance(val, dict) else {}


def _clean_body(body: str) -> str:
    """지시를 어긴 꼴만 걷어낸다 — 마크다운 표·강조. **문장은 고치지 않는다.**

    걷어내는 이유는 그것이 WorkB 에서 렌더되지 않아 `| 항목 | 값 |` 이 글자 그대로 남기
    때문이다. 지시로만 막으면 어겼을 때 아무도 모른다.
    """
    lines = [ln for ln in body.replace("\r\n", "\n").split("\n")
             if not re.match(r"^\s*\|?\s*[-:|\s]{5,}\|?\s*$", ln)]
    out = []
    for ln in lines:
        if ln.strip().startswith("|") and ln.count("|") >= 2:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            ln = " · ".join(c for c in cells if c)
        out.append(re.sub(r"\*\*|^\s*#+\s*", "", ln))
    return "\n".join(out).strip()


def _generate(prompt: str, name: str) -> tuple[str, str]:
    raw = generate(prompt, max_tokens=MAX_TOKENS, system=MEMO_SYSTEM, name=name)
    obj = _json_obj(raw)
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
        return None, LLM_DOWN.format(reason=f"{type(exc).__name__}: {exc}")

    parts = [to_html(body)]
    if table:
        parts += [table, _footer_html(rule=listed)]
    markup = "<br><br>".join(parts)
    if len(markup) > workb.MAX_CHARS:
        # 조용히 잘라내지 않는다 — 잘린 쪽지는 «전부인 줄» 읽힌다(workb.MAX_CHARS 머리말).
        return None, TOO_LONG.format(limit=workb.MAX_CHARS)

    preview = body if not table else f"{body}\n\n(아래에 {what} 표가 붙습니다)"
    return Draft(title=title, text=preview, html=markup, to=to,
                 recipients=list(recipients)), ""
