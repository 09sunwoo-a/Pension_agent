"""WorkB 쪽지 초안 — 이번 턴 재료로 제목·본문 작성 [LLM], 근거 이탈 시 미생성.

꼴·발송은 `common/note.py`. 재료 = 원장 + (고객 화면 있으면 브리핑 / 없으면 오늘의 타겟).
검사 걸리면 폴백 없이 사유만 반환 — 쪽지는 되돌릴 수 없음.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pension_agent.common import note
from pension_agent.common.clock import today
from pension_agent import tools
from pension_agent.prompts import (
    COMPOSE_RETRY_BLOCK, MEMO_ADDRESSEE_LINE, MEMO_EDIT_ECHO_BLOCK, MEMO_EDIT_PROMPT,
    MEMO_EDIT_RECIPIENT_BLOCK, MEMO_EDIT_SYSTEM,
    MEMO_OTHER_GUIDE, MEMO_PROMPT, MEMO_SELF_GUIDE, MEMO_SYSTEM, MEMO_TABLE_BLOCK,
)
from pension_agent import state
from pension_agent.state import AgentState, format_history
from pension_agent.common.llm import LLMError, generate, json_object

#: 본문 생성 토큰 상한(화면 답변 1500보다 짧게)
MAX_TOKENS = 900

#: 검사 실패 시 재작성 횟수. 두 번째도 걸리면 미발송
RETRIES = 1

KEY_INFO_HEADER = "[고객 주요 정보]"

#: 미생성 사유 문구 — 무엇에 걸렸는지 명시
NO_MATERIAL = "이번 턴에 쪽지로 옮길 근거가 없어요. 먼저 무엇을 정리할지 물어봐 주세요."
LLM_DOWN = "쪽지 본문을 쓰지 못했어요 — {reason}."
#: 걸린 자리를 값까지 표기(화면 폴백과 다름) — tests/agent/memo.py 가 고정
SCREENED = ("쪽지 본문이 근거를 벗어나서 보내지 않았어요. 걸린 자리: {faults}. "
            "한 번 더 부탁하시면 다시 써볼게요.")
TOO_LONG = "쪽지 본문이 길이 상한({limit:,}자)을 넘어서 보내지 않았어요."

#: 화면에서 초안을 감싸는 펜스(화면 장치, 발송 본문 미포함). 값은 state.py 소유 — 재노출
FENCE = state.FENCE


@dataclass(frozen=True)
class Draft:
    """승낙 전 쪽지 한 통. `text` 는 화면용 평문, `html` 은 같은 글의 발송용 변환."""

    title: str
    text: str
    html: str
    to: str
    recipients: list[str] = field(default_factory=list)
    # 재조립용 분리 — revise 는 body 만 변경, 값 표·꼬리말(tail_html)은 원장 값이라 불변
    body: str = ""
    tail_html: str = ""
    tail_note: str = ""       # 화면 미리보기의 표 자리 안내 한 줄
    # 예약 발송 시각(ISO)·화면 표기. 빈 값은 즉시 발송. 본문·제목 미포함
    send_at: str = ""
    send_label: str = ""
    # 이름 발송 시 `recipients` 는 빈 값 — 사번은 승낙 뒤 이름 검색 발송이 결정
    user_name: str = ""
    group_name: str = ""


def material(state: AgentState) -> list[tools.Evidence]:
    """쪽지 재료 — 이번 턴 원장 + customer/branch_targets 도구 결과(별도 읽기 경로 금지)."""
    ev = list(state.get("evidence") or [])
    used = {e["tool"] for e in ev}
    want, query = (("customer", "고객 브리핑 자료") if state.get("customer_id")
                   else ("branch_targets", "오늘의 관리 대상 추천"))
    if want not in used:
        try:
            found = tools.run(want, state, query)
        except LLMError:
            found = None
        if found is not None:
            ev.append(found)
    return ev


_TD_LABEL = 'align="center" style="text-align:center;white-space:nowrap"'


def _key_info(customer_id: str) -> list[tuple[str, str]]:
    """고객 주요 정보 6항목 — engine 산출 문자열 그대로(브리핑 화면과 동일 값)."""
    from pension_agent.tools.customer_solution import customer as strategy_customer  # noqa: PLC0415
    from pension_agent.tools.customer_solution import engine  # noqa: PLC0415

    profile = strategy_customer.get_profile(customer_id)
    if profile is None:
        return []
    facts = engine.prepare(profile)
    header, state = facts["customer"], facts["account_state"]
    # 성립 요건 `코드:이름` 중 이름만
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
    """고객 주요 정보 표. 산출 실패 시 "" — «미확인» 채움 금지."""
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
    """본문 아래 (표 HTML, 표 설명). 고객 정보 표 또는 타겟 목록(원장에 branch_targets 있을 때만).

    타겟 목록은 그 도구가 실은 순서 그대로(CASE 순 · 첫 추천) — 여기서 다시 고르지 않는다.
    """
    if state.get("customer_id"):
        found = _key_info_table(state["customer_id"])
        return (f"<b>{note.esc(KEY_INFO_HEADER)}</b><br>{found}",
                "이 고객의 연령·투자성향·평가금액·수익률·연금개시·세액공제 잔여한도·관리 사유") \
            if found else ("", "")
    found = next((e for e in evidence if e["tool"] == "branch_targets"), None)
    if found is None:
        return "", ""
    from pension_agent.tools.customer_solution import target_list  # noqa: PLC0415
    from pension_agent.tools.customer_solution.tool import roster_targets  # noqa: PLC0415
    names = (found.get("meta") or {}).get("names")
    _scope, pool = roster_targets(state)
    refs = (found.get("meta") or {}).get("refs")
    targets = (target_list.by_refs(refs, pool) if refs and all(refs) else
               target_list.by_names(names, pool) if names is not None else pool)
    if not targets:
        return "", ""
    table, _shown = note.targets_table(targets)
    return table, "오늘의 타겟 고객 목록(순번·이름·나이·성향·평가금액·선정 요건)"


# WorkB 뷰어는 인라인 style 제거·블록 요소 간격 추가 — <br>·<b>·표 옛 속성만 사용

def to_html(text: str) -> str:
    """평문 → 쪽지 HTML(문장 불변). `[…]` 줄은 굵게, 들여쓰기는 `&nbsp;` 로 보존."""
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
    from pension_agent.tools.customer_solution.customer import AS_OF  # noqa: PLC0415
    lines = [note.FOOTER_ASOF.format(as_of=AS_OF.isoformat(), today=today().isoformat())]
    if rule:
        lines.append(note.FOOTER_RULE)
    return "<br>".join(note.esc(x) for x in lines)


#: LaTeX 수식 기호 → 글자(WorkB 미렌더). 나머지 `$…$` 는 달러 기호만 제거
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
    """마크다운 표·강조·LaTeX 제거(WorkB 미렌더). 문장 불변."""
    # `|` 있는 구분줄만 제거 — `-----` 단독 줄은 직원이 요청한 구분선일 수 있음
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
          to_self: bool, addressee: str = "") -> tuple[Draft | None, str]:
    """쪽지 초안. 실패 시 `(None, 사유)`.

    - 받는 사람은 인자로만(nodes/act 가 결정) — 대화에서 사번 추출 금지
    - `addressee`: 본문 인사의 호칭(「이선우 대리님」). 사번·본인이면 빈 값
    """
    evidence = material(state)
    if not evidence:
        return None, NO_MATERIAL

    table, what = table_for(state, evidence)
    listed = not state.get("customer_id")      # 목록 표 여부 — 꼬리말 선정 기준 줄
    prompt = MEMO_PROMPT.format(
        context="\n\n".join(e["text"] for e in evidence),
        history_block=format_history(state.get("history")),
        question=state.get("question") or "",
        answer=(state.get("answer") or "").strip(),
        guide=MEMO_SELF_GUIDE if to_self else MEMO_OTHER_GUIDE
        + (MEMO_ADDRESSEE_LINE.format(addressee=addressee) if addressee else ""),
        table_block=MEMO_TABLE_BLOCK.format(what=what) if table else "",
    )

    # 화면 답변은 이미 같은 검사 통과 — 인용 허용
    echoable = [state.get("question") or "", state.get("answer") or ""]

    from pension_agent.nodes import plan  # noqa: PLC0415 — 순환 임포트 회피

    try:
        title, body = _generate(prompt, "consult.memo")
        faults: list[str] = []
        for attempt in range(RETRIES + 1):
            if not title or not body:
                return None, LLM_DOWN.format(reason="본문을 규격대로 받지 못했어요")
            # 제목·본문 함께 검사 — 본문만 보면 근거 밖 수치가 제목으로 샘
            faults = plan.screen(f"{title}\n{body}", evidence, "", prompt_texts=echoable)
            if not faults:
                break
            if attempt >= RETRIES:
                return None, SCREENED.format(faults=" / ".join(faults[:3]))
            title, body = _generate(
                prompt + COMPOSE_RETRY_BLOCK.format(faults="\n".join(f"- {f}" for f in faults[:8])),
                "consult.memo.retry")
    except LLMError as exc:
        # 프로바이더 응답 전문 노출 방지 — 한 줄로 축약
        return None, LLM_DOWN.format(reason=plan.short_reason(f"{type(exc).__name__}: {exc}"))

    tail_html = "<br><br>".join([table, _footer_html(rule=listed)]) if table else ""
    tail_note = f"(아래에 {what} 표가 붙습니다)" if table else ""
    return assemble(title, body, tail_html=tail_html, tail_note=tail_note,
                    to=to, recipients=recipients)


def assemble(title: str, body: str, *, tail_html: str, tail_note: str, to: str,
             recipients: list[str], send_at: str = "", send_label: str = "",
             user_name: str = "", group_name: str = "") -> tuple[Draft | None, str]:
    """제목·본문 + 꼬리(값 표·꼬리말) → 초안. 길이 초과 시 `(None, 사유)`. draft·revise·verbatim 공용."""
    parts = [to_html(body)] + ([tail_html] if tail_html else [])
    markup = "<br><br>".join(parts)
    if len(markup) > note.MAX_CHARS:
        # 잘라내기 금지 — 잘린 쪽지가 전부로 읽힘
        return None, TOO_LONG.format(limit=note.MAX_CHARS)
    preview = f"{body}\n\n{tail_note}" if tail_note else body
    return Draft(title=title, text=preview, html=markup, to=to, recipients=list(recipients),
                 body=body, tail_html=tail_html, tail_note=tail_note,
                 send_at=send_at, send_label=send_label,
                 user_name=user_name, group_name=group_name), ""


# 초안 고치기 — 새로 쓰지 않고 직전 초안을 수정(재작성 시 직원이 고친 문장 소실)
# 재료: 직전 초안 + 직원의 말 + 이번 상담 답변. 직원이 적은 값이 원장보다 우선

#: 수정 실패 — 직전 초안 유지
EDIT_DOWN = "초안 수정 지시를 처리하지 못했어요 — {reason}. 직전 초안을 그대로 둡니다."
#: 재작성 후에도 직원의 말이 본문에 그대로 남음 — 초안 유지
EDIT_ECHO = ("말씀하신 문장이 쪽지 본문에 그대로 들어가서 반영하지 않았어요. 넣을 내용만 "
             "말씀해 주세요(예: «10월 7일 재접촉 예정이라고 넣어줘»). 직전 초안을 그대로 둡니다.")
#: 말 옮겨 적기 판정 기준(글자·숫자만). 짧은 말은 판정 제외 — 넣으라고 준 문장과 구분 불가
ECHO_MIN_CHARS = 12
ECHO_RATIO = 0.8
#: 요청 어미 — 부분 일치 줄은 이 어미로 끝날 때만 옮긴 것으로 판정
_REQUEST_END = re.compile(r"(?:줘|줄래|주라|해봐|할래)$")


def _squash(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def echoed(instruction: str, title: str, body: str) -> bool:
    """제목·본문 한 줄이 직원의 말(요청 문장)을 통째로 옮겼는지 여부."""
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


#: «함께 고친 것» 코드 → 화면 표시. LLM 은 코드만 반환, 목록 밖 코드는 미표시
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
    """«함께 고친 것» 한 줄. 표시할 코드 없으면 ""."""
    items = [ALSO_LABELS[c] for c in dict.fromkeys(codes) if c in ALSO_LABELS]
    return ALSO_HEAD.format(items=" · ".join(items)) if items else ""


@dataclass(frozen=True)
class Revision:
    """초안 고치기 결과. kind: edited · not_edit · ask · screened · echo · down."""

    kind: str
    draft: Draft | None = None
    ask: str = ""
    reason: str = ""
    added: list[str] = field(default_factory=list)     # 직원이 적어 새로 들어간 값
    removed: list[str] = field(default_factory=list)   # 그 대신 빠진 직전 초안의 값
    also: list[str] = field(default_factory=list)      # 지시 밖에서 함께 고친 것(코드)


def from_pending(pending: dict) -> Draft:
    """`pending_action` 에서 초안 복원. 재조립 금지 — 직원이 읽은 것과 달라짐."""
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
    """받는 사람만 교체(본문 불변). 사번·이름 중 하나만 유지."""
    from dataclasses import replace  # noqa: PLC0415
    return replace(found, recipients=list(recipients), to=to, user_name=user_name,
                   group_name=group_name)


def reschedule(found: Draft, send_at: str, send_label: str) -> Draft:
    """발송 시각만 교체(빈 값은 즉시 발송)."""
    from dataclasses import replace  # noqa: PLC0415
    return replace(found, send_at=send_at, send_label=send_label)


def verbatim(found: Draft, body: str) -> tuple[Draft | None, str]:
    """직원이 붙여넣은 글을 그대로 본문으로(LLM·검사 없음). 더미 게이트·마스킹은 발송 경로 담당."""
    return assemble(found.title, body.strip(), tail_html=found.tail_html,
                    tail_note=found.tail_note, to=found.to, recipients=found.recipients,
                    send_at=found.send_at, send_label=found.send_label,
                    user_name=found.user_name, group_name=found.group_name)


#: 초안 고치기에 싣는 답변 개수·글자 상한 — 전부 실으면 오래된 수치가 인용 허용됨
EDIT_ANSWERS = 4
EDIT_ANSWERS_CHARS = 4000


def session_answers(history: list[dict] | None) -> list[str]:
    """이번 상담 답변(최근 `EDIT_ANSWERS` 개, 쪽지 초안 턴 제외)."""
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
    """직원 지시로 초안 수정. 수정 지시 판정·수정을 LLM 1회로(애매하면 `not_edit`).

    `recipient`: (옛 표기, 새 표기, 옛 이름) — 받는 사람이 바뀌고 본문에 옛 이름이 있을 때만.
    """
    from pension_agent.evidence import verify  # noqa: PLC0415
    from pension_agent.nodes import plan  # noqa: PLC0415 — 순환 임포트 회피

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
    # 직원의 말을 통째로 옮겼으면 1회 재작성
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

    # 허용 범위: 직전 초안 + 직원의 말 + 답변 + 받는 사람 표기. 관계 검사 없음(선언 없음)
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
