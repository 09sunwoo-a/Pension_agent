"""계측 본체 — 밖에서 감싸서 보기만 한다.

━━ 원칙 ━━
래퍼는 **원본을 그대로 부르고 반환값에 손대지 않는다.** 계측이 동작을 바꾸면 그 트레이스는
진단이 아니라 다른 실행의 기록이다. 기록만 하고 값은 통과시킨다.

━━ 무엇을 감싸나 ━━
  노드 10개       `graph` 모듈 전역 이름. `build_agent()` 가 `add_node` 에 넘기는 것이
                  이 이름이라, **컴파일 전에** 갈아끼워야 잡힌다.
  LLM 5자리       `understand.generate` · `plan.generate` · `clarify.generate` ·
                  `tools.generate` · `select.llm_pick`
  compose 게이트  `plan.verify_texts` · `plan.relations` · `plan._span_verdict`

━━ 비공개 이름에 붙는다는 것 ━━
`_span_verdict` 도 `_AGENT` 도 비공개다. 운영 쪽에서 이름이 바뀌거나 검사가 인라인되면
래퍼는 **조용히 덜 보게 된다** — 트레이스가 "게이트 통과"를 찍는데 실은 게이트를 못 본
것이면, 이 도구는 없는 것만 못하다. 그래서 `instrument()` 는 대상 속성이 하나라도 없으면
즉시 예외를 던진다. 덜 보는 쪽이 아니라 크게 실패하는 쪽으로 기운다.

━━ relations 는 모듈이라 조심한다 ━━
`plan.relations` 는 모듈 객체다. 진짜 모듈의 `check` 를 갈아끼우면 같은 모듈을 쓰는
`tools.py`(`import relations as REL`)까지 오염된다. 그래서 **plan 쪽 바인딩만**
`SimpleNamespace` 로 바꾼다.
"""

from __future__ import annotations

import types
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field

from pension_agent.consult_agent import graph as G
from pension_agent.consult_agent import select as S
from pension_agent.consult_agent import tools as T
from pension_agent.consult_agent.nodes import clarify as CL
from pension_agent.consult_agent.nodes import plan as P
from pension_agent.consult_agent.nodes import understand as U

#: 그래프에 실리는 노드 이름 — `graph.py::build_agent` 가 `add_node` 에 넘기는 전역들.
NODE_NAMES = ("understand", "plan_step", "compose", "clarify", "agent_help",
              "lms_send", "correction", "llm_down", "confirm_action", "offer")

#: compose 가 순서대로 거는 검사. 이 순서를 여기 적어두는 이유는 **실행되지 않은 게이트**를
#: 말하기 위해서다 — 앞에서 끊기면 뒤는 아예 안 불리고, 그 사실이 진단의 핵심이다.
GATES = ("verify_texts", "relations", "span")

#: 프롬프트로 LLM 호출의 정체를 가른다. 호출부마다 다른 함수를 감싸지만, 한 함수(plan.generate)가
#: 계획과 작성 둘 다 쓰므로 프롬프트를 봐야 갈린다.
_PROMPT_MARKS = (
    ("<지식베이스>", "compose"),
    ("쓸 수 있는 도구:", "plan"),
    ("어떤 기능으로 보낼지", "route"),
    ("직접 답이 되는 것만", "adequacy"),
    ("되물어야 하는지만", "clarify"),
)


def _stage(prompt: str) -> str:
    for mark, name in _PROMPT_MARKS:
        if mark in prompt:
            return name
    return "기타"


@dataclass
class Call:
    """LLM 호출 한 건."""
    stage: str
    text: str = ""          # 응답 원문. compose 면 이것이 '폐기됐을지도 모르는 생성문'이다
    error: str = ""


@dataclass
class Gate:
    """compose 검사 한 건. `passed=False` 면 이 자리에서 생성문이 버려졌다."""
    name: str
    passed: bool
    detail: list = field(default_factory=list)


@dataclass
class Node:
    """노드 실행 한 건."""
    name: str
    delta: dict = field(default_factory=dict)      # 노드가 돌려준 상태 변경(요약)
    calls: list[Call] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    note: str = ""                                 # 사람이 읽을 한 줄


@dataclass
class Turn:
    question: str
    nodes: list[Node] = field(default_factory=list)


class Trace:
    """한 실행의 기록. 턴 단위로 묶는다(멀티턴 시나리오를 한 줄로 재현하므로)."""

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self._calls: list[Call] = []   # 다음 노드가 가져갈 LLM 호출
        self._gates: list[Gate] = []   # 다음 노드가 가져갈 게이트 판정

    # ── 기록 ──────────────────────────────────────────────
    def begin_turn(self, question: str) -> None:
        self.turns.append(Turn(question=question))

    def _turn(self) -> Turn:
        if not self.turns:
            self.begin_turn("(질문 미기록)")
        return self.turns[-1]

    def add_call(self, call: Call) -> None:
        self._calls.append(call)

    def add_gate(self, gate: Gate) -> None:
        self._gates.append(gate)

    def add_node(self, node: Node) -> None:
        # 노드가 끝나는 시점에 그 노드가 부른 LLM·게이트를 붙여준다. 노드 래퍼가 원본을
        # 부른 뒤에 기록하므로, 그 사이에 쌓인 것이 곧 그 노드가 한 일이다.
        node.calls, self._calls = self._calls, []
        node.gates, self._gates = self._gates, []
        self._turn().nodes.append(node)

    # ── 조회 (테스트가 쓴다) ────────────────────────────────
    def node_names(self, turn: int = -1) -> list[str]:
        return [n.name for n in self.turns[turn].nodes]

    def node(self, name: str, turn: int = -1) -> Node | None:
        for n in self.turns[turn].nodes:
            if n.name == name:
                return n
        return None

    def gates(self, turn: int = -1) -> dict[str, Gate]:
        node = self.node("compose", turn)
        return {g.name: g for g in (node.gates if node else [])}

    def blocked_by(self, turn: int = -1) -> str | None:
        """생성문을 버린 게이트 이름. 아무 게이트도 안 걸렸으면 None."""
        for gate in self.gates(turn).values():
            if not gate.passed:
                return gate.name
        return None

    def draft(self, turn: int = -1) -> str:
        """compose 가 LLM 에게 받은 문장. 폐기됐어도 여기 남는다."""
        node = self.node("compose", turn)
        for call in (node.calls if node else []):
            if call.stage == "compose":
                return call.text
        return ""


# ─────────────────────────────────────────────────────────────
# 계측
# ─────────────────────────────────────────────────────────────

#: (모듈, 속성) — 이 중 하나라도 없으면 계측을 시작하지 않는다.
_TARGETS: tuple[tuple[object, str], ...] = (
    *((G, name) for name in NODE_NAMES),
    (G, "_AGENT"),
    (U, "generate"), (P, "generate"), (T, "generate"), (CL, "generate"),
    (P, "verify_texts"), (P, "relations"), (P, "_span_verdict"),
    (T, "llm_pick"), (S, "llm_pick"),
)


def _check_targets() -> None:
    missing = [f"{getattr(mod, '__name__', mod)}.{attr}"
               for mod, attr in _TARGETS if not hasattr(mod, attr)]
    if missing:
        raise AttributeError(
            "계측 대상이 사라졌습니다 — 운영 코드가 바뀌어 트레이스가 덜 보게 됩니다: "
            + ", ".join(missing))


def _summary(delta: dict) -> dict:
    """상태 변경 중 트레이스에 남길 것만. 답변 원문은 길어서 길이만 남긴다."""
    out = {k: v for k, v in delta.items()
           if k in ("intent", "plan_done", "llm_error", "clarify")}
    if delta.get("pending_action"):
        out["제안"] = delta["pending_action"].get("label")
    if "answer" in delta:
        out["answer_len"] = len(delta["answer"] or "")
    if "evidence" in delta:
        out["evidence_n"] = len(delta["evidence"] or [])
    return out


def _plan_note(state: dict, delta: dict) -> str:
    """이 계획 단계가 무엇을 했는지 한 줄. 도구·질의·채택 카드는 상태 차분에서 읽는다 —
    `tools.run` 을 따로 감싸지 않아도 여기 다 나온다."""
    before = len(state.get("plan_calls") or [])
    calls = delta.get("plan_calls") or []
    if len(calls) <= before:
        if delta.get("llm_error"):
            return f"중단 — {delta['llm_error'][:60]}"
        return "done" if delta.get("plan_done") else "변화 없음"

    signature = calls[-1]
    ev_before = len(state.get("evidence") or [])
    ev_after = delta.get("evidence")
    if ev_after is None or len(ev_after) <= ev_before:
        return f"{signature} → 재료 없음"
    found = ev_after[-1]
    cards = " ".join(
        f"{s['id']}({s['score']})" if s.get("score") is not None else str(s["id"])
        for s in found.get("sources") or [])
    return f"{signature} → 채택 {cards or '(출처 미상)'}"


def _compose_note(state: dict, delta: dict) -> str:
    """최종 답이 생성문인지 폴백인지. **말투가 달라지는 자리가 여기다.**

    판정은 답변이 근거 원문으로 시작하는지로 한다(plan.compose 의 폴백이
    `parts = [e["text"] for e in evidence]` 라 첫 근거 블록이 그대로 앞에 온다).
    """
    answer = delta.get("answer") or ""
    evidence = state.get("evidence") or []
    if evidence and answer.startswith(evidence[0]["text"]):
        return f"폴백 — 근거 원문 {len(evidence)}건을 그대로 출력 (말투가 달라지는 자리)"
    if delta.get("llm_error") or answer.startswith("지금은 답변을 만들 수 없어요"):
        return "LLM 실패 안내 (§11 — 근거 원문을 대신 내보내지 않는다)"
    if not evidence:
        return "재료 0건 — 없다고 답함"
    return "생성문 그대로"


@contextmanager
def instrument(trace: Trace):
    """계측을 걸고, 나갈 때 무조건 원래대로 돌려놓는다."""
    _check_targets()
    saved = [(mod, attr, getattr(mod, attr)) for mod, attr in _TARGETS]

    def node_wrapper(name, fn):
        def wrapped(state):
            delta = fn(state) or {}
            note = ""
            if name == "plan_step":
                note = _plan_note(state, delta)
            elif name == "compose":
                note = _compose_note(state, delta)
            trace.add_node(Node(name=name, delta=_summary(delta), note=note))
            return delta
        return wrapped

    def llm_wrapper(fn):
        def wrapped(prompt, **kw):
            stage = _stage(prompt)
            try:
                text = fn(prompt, **kw)
            except Exception as exc:                     # noqa: BLE001 — 기록하고 그대로 올린다
                trace.add_call(Call(stage=stage, error=f"{type(exc).__name__}: {exc}"))
                raise
            trace.add_call(Call(stage=stage, text=text))
            return text
        return wrapped

    def pick_wrapper(fn):
        def wrapped(kinds, query):
            hits = fn(kinds, query)
            trace.add_call(Call(stage="pick", text=f"{list(kinds)} → {len(hits)}건"))
            return hits
        return wrapped

    real_verify = P.verify_texts

    def verify_wrapper(answer, texts, **kw):
        ok, bad = real_verify(answer, texts, **kw)
        trace.add_gate(Gate(name="verify_texts", passed=bool(ok), detail=list(bad or [])))
        return ok, bad

    real_relations = P.relations

    def relations_wrapper(answer, cards):
        broken = real_relations.check(answer, cards)
        trace.add_gate(Gate(name="relations", passed=not broken, detail=list(broken or [])))
        return broken

    real_span = P._span_verdict

    def span_wrapper(found, answer):
        verdict, gaps = real_span(found, answer)
        trace.add_gate(Gate(name="span", passed=verdict != P.DISCARD,
                            detail=[verdict, *[label for label, _ in gaps]]))
        return verdict, gaps

    try:
        for name in NODE_NAMES:
            setattr(G, name, node_wrapper(name, getattr(G, name)))
        # 컴파일된 그래프에는 감싸기 전 노드가 박혀 있다. 비워서 다시 만들게 한다.
        G._AGENT = None
        U.generate = llm_wrapper(U.generate)
        P.generate = llm_wrapper(P.generate)
        CL.generate = llm_wrapper(CL.generate)
        T.generate = llm_wrapper(T.generate)
        T.llm_pick = pick_wrapper(T.llm_pick)
        S.llm_pick = pick_wrapper(S.llm_pick)
        P.verify_texts = verify_wrapper
        # 모듈 자체를 건드리지 않는다 — plan 의 바인딩만 갈아끼운다(tools.py 오염 방지).
        P.relations = types.SimpleNamespace(check=relations_wrapper)
        P._span_verdict = span_wrapper
        yield trace
    finally:
        for mod, attr, value in saved:
            setattr(mod, attr, value)
        # 계측된 노드가 박힌 그래프를 다음 실행에 물려주지 않는다.
        G._AGENT = None


# ─────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────

_TREE = ("├", "└")


def _pad(text: str, width: int) -> str:
    """한글은 두 칸을 차지한다. `str.ljust` 로 맞추면 트리가 어긋난다."""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - used)


def _gate_lines(node: Node) -> list[str]:
    seen = {g.name: g for g in node.gates}
    rows: list[tuple[str, str]] = []
    stopped = False
    for name in GATES:
        gate = seen.get(name)
        if gate is None:
            rows.append((name, "실행 안 됨 (앞에서 끊김)" if stopped else "실행 안 됨"))
            continue
        if gate.passed:
            rows.append((name, "통과" + (f" {gate.detail}" if name == "span" else "")))
        else:
            rows.append((name, f"✗ {gate.detail}  → 생성문 폐기"))
            stopped = True
    rows.append(("처분", node.note))
    return [f"      {_TREE[i == len(rows) - 1]} {_pad(name, 14)}{text}"
            for i, (name, text) in enumerate(rows)]


def render(trace: Trace, show_llm: bool = False) -> str:
    out: list[str] = ["━━ 트레이스 ━━"]
    for index, turn in enumerate(trace.turns):
        if len(trace.turns) > 1:
            out.append(f"\n> {turn.question}")
        step = 0
        plan_step = 0
        for node in turn.nodes:
            step += 1
            label = node.name
            if node.name == "plan_step":
                plan_step += 1
                label = f"plan #{plan_step}"
            calls = " · ".join(
                f"LLM {c.stage}" + (f" 실패({c.error})" if c.error else f" {len(c.text)}자")
                for c in node.calls)
            fields = " ".join(f"{k}={v}" for k, v in node.delta.items())
            # compose 의 한 줄 요약(처분)은 아래 게이트 트리의 마지막 줄에 있다 — 같은 말을
            # 두 번 세우지 않는다.
            note = fields if node.name == "compose" else (node.note or fields or "변화 없음")
            out.append(f"  {step} {_pad(label, 13)}{note}" + (f"   ({calls})" if calls else ""))
            if node.name == "compose":
                out += _gate_lines(node)
        if show_llm:
            draft = trace.draft(index)
            if draft:
                out.append("\n── compose 가 LLM 에게 받은 문장" +
                           ("  (폐기됨)" if trace.blocked_by(index) else ""))
                out += [f"   {line}" for line in draft.splitlines()]
    return "\n".join(out)
