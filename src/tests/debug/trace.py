"""계측 본체 — 원본 호출·반환값 무변경, 기록만.

  노드        `graph` 모듈 전역 — 컴파일 전에 교체해야 포착
  LLM         `understand`·`plan`·`clarify`·`tools` 의 generate · `select.llm_pick`
  게이트      `plan.verify_texts` · `plan.relations`(plan 쪽 바인딩만 교체 — 모듈 공유 오염 방지) · `plan._span_verdict`
대상 속성이 하나라도 없으면 `instrument()` 즉시 예외 — 조용히 덜 보는 것 방지.
"""

from __future__ import annotations

import time
import types
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field

from pension_agent import graph as G
from pension_agent import prompts as PROMPTS
from pension_agent.tools.kb_search import select as S
from pension_agent import tools as T
from pension_agent.nodes import clarify as CL
from pension_agent.nodes import plan as P
from pension_agent.nodes import understand as U

#: `graph.build_agent` 가 `add_node` 에 넘기는 노드 함수 이름(그래프 라벨과 다를 수 있음 — answer 의 라벨은 compose)
NODE_NAMES = ("understand", "plan_step", "answer", "agent_help",
              "lms_link", "correction", "llm_down", "confirm_action", "offer")

#: 답변 노드 — 게이트 트리·처분이 붙는 자리
ANSWER_NODE = "answer"

#: LLM 실패로 분류도 못 한 턴의 노드 — 답변으로 집계 금지(reps._observed)
LLM_DOWN_NODE = "llm_down"

#: compose 게이트 순서 — «실행 안 됨» 표시용
GATES = ("verify_texts", "relations", "span")

# LLM 호출 구분 표식은 프롬프트 상수에서 추출 — 손으로 적으면 문구 변경 시 오분류
def _head(template: str) -> str:
    """포맷 자리표시자 앞 첫 줄."""
    return template.split("{")[0].strip().splitlines()[0].strip()


_PROMPT_MARKS: tuple[tuple[str, str], ...] = (
    (_head(PROMPTS.COMPOSE_PROMPT), "compose"),
    (_head(PROMPTS.PLAN_PROMPT), "plan"),
    (_head(PROMPTS.ROUTE_PROMPT), "route"),
    (_head(PROMPTS.ADEQUACY_PROMPT), "adequacy"),
    (_head(PROMPTS.CLARIFY_PROMPT), "clarify"),
)

if len({mark for mark, _ in _PROMPT_MARKS}) != len(_PROMPT_MARKS):
    raise AssertionError(
        "프롬프트 첫 줄이 서로 겹칩니다 — LLM 호출을 갈라 볼 수 없습니다: "
        + str([m for m, _ in _PROMPT_MARKS]))


def _stage(prompt: str) -> str:
    for mark, name in _PROMPT_MARKS:
        if mark and mark in prompt:
            return name
    return "기타"


def is_compose_prompt(prompt: str) -> bool:
    """답변 작성 호출 여부(script.py 공용)."""
    return _stage(prompt) == "compose"


@dataclass
class Call:
    """LLM 호출 한 건."""
    stage: str
    text: str = ""          # 응답 원문(compose 면 폐기됐을 수 있는 생성문)
    error: str = ""
    seconds: float = 0.0    # 소요 시간


@dataclass
class Gate:
    """compose 검사 한 건. `passed=False` 면 생성문 폐기."""
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
    note: str = ""                                 # 요약 한 줄
    seconds: float = 0.0                           # 노드 전체 소요 시간(LLM 대기 포함)


@dataclass
class Turn:
    question: str
    nodes: list[Node] = field(default_factory=list)


class Trace:
    """한 실행의 턴별 기록."""

    def __init__(self) -> None:
        self.turns: list[Turn] = []
        # 실행 단위 경고(예: 없는 고객 id) — 트레이스 맨 위 출력
        self.notes: list[str] = []
        self._calls: list[Call] = []   # 다음 노드가 가져갈 LLM 호출
        self._gates: list[Gate] = []   # 다음 노드가 가져갈 게이트 판정

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

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
        # 노드 종료 시점까지 쌓인 LLM·게이트를 그 노드에 귀속
        node.calls, self._calls = self._calls, []
        node.gates, self._gates = self._gates, []
        self._turn().nodes.append(node)

    def node_names(self, turn: int = -1) -> list[str]:
        return [n.name for n in self.turns[turn].nodes]

    def node(self, name: str, turn: int = -1) -> Node | None:
        for n in self.turns[turn].nodes:
            if n.name == name:
                return n
        return None

    def gates(self, turn: int = -1) -> dict[str, Gate]:
        node = self.node(ANSWER_NODE, turn)
        return {g.name: g for g in (node.gates if node else [])}

    def blocked_by(self, turn: int = -1) -> str | None:
        """생성문을 폐기한 게이트 이름, 없으면 None."""
        for gate in self.gates(turn).values():
            if not gate.passed:
                return gate.name
        return None

    def draft(self, turn: int = -1) -> str:
        """compose 가 받은 LLM 문장(폐기 포함)."""
        node = self.node(ANSWER_NODE, turn)
        for call in (node.calls if node else []):
            if call.stage == "compose":
                return call.text
        return ""


#: (모듈, 속성) — 하나라도 없으면 계측 거부
_TARGETS: tuple[tuple[object, str], ...] = (
    *((G, name) for name in NODE_NAMES),
    (G, "_AGENT"),
    (U, "generate"), (P, "generate"), (T, "generate"), (CL, "generate"),
    (P, "verify_texts"), (P, "relations"), (P, "_span_verdict"),
    (T, "llm_pick"), (S, "llm_pick"),
)


def missing_targets() -> list[str]:
    """사라진 계측 대상 목록 — 예외 대신 «트레이스만 끄기»를 고를 호출자(app.py)용."""
    return [f"{getattr(mod, '__name__', mod)}.{attr}"
            for mod, attr in _TARGETS if not hasattr(mod, attr)]


def _check_targets() -> None:
    missing = missing_targets()
    if missing:
        raise AttributeError(
            "계측 대상이 사라졌습니다 — 운영 코드가 바뀌어 트레이스가 덜 보게 됩니다: "
            + ", ".join(missing))


def _summary(delta: dict) -> dict:
    """트레이스에 남길 상태 변경 요약. 답변은 길이만."""
    # judge_verdict 필수 — 없으면 리허설이 판정 네 갈래를 구분 못 함
    out = {k: v for k, v in delta.items()
           if k in ("intent", "plan_done", "llm_error", "clarify", "judge_verdict",
                    "plan_retry")}
    # 갈래(tools.record_branches) — 판정 입력의 가장 센 신호
    if delta.get("branches"):
        out["branches"] = " / ".join(b.get("axis", "?") for b in delta["branches"])
    # 전제 블록은 60자로 절단
    if delta.get("judge_note"):
        out["judge_note"] = " ".join(delta["judge_note"].split())[:60] + "…"
    if delta.get("pending_action"):
        out["제안"] = delta["pending_action"].get("label")
    if "answer" in delta:
        out["answer_len"] = len(delta["answer"] or "")
    if "evidence" in delta:
        out["evidence_n"] = len(delta["evidence"] or [])
    return out


def _plan_note(state: dict, delta: dict) -> str:
    """계획 단계 요약 한 줄 — 도구·질의·채택 카드를 상태 차분에서 추출."""
    before = len(state.get("steps") or [])
    steps = delta.get("steps") or []
    if len(steps) <= before:
        if delta.get("llm_error"):
            return f"중단 — {delta['llm_error'][:60]}"
        return "done" if delta.get("plan_done") else "변화 없음"

    step = steps[-1]
    signature = f"{step.get('tool')}:{step.get('query')}"
    # 도구 고장 ≠ 자료 없음
    if step.get("outcome") == "failed":
        return f"{signature} → 도구 고장: {step.get('reason') or '원인 미상'}"
    ev_before = len(state.get("evidence") or [])
    ev_after = delta.get("evidence")
    if ev_after is None or len(ev_after) <= ev_before:
        return f"{signature} → 자료 없음"
    found = ev_after[-1]
    cards = " ".join(
        f"{s['id']}({s['score']})" if s.get("score") is not None else str(s["id"])
        for s in found.get("sources") or [])
    return f"{signature} → 채택 {cards or '(출처 미상)'}"


def _compose_note(state: dict, delta: dict) -> str:
    """최종 답 처분(생성문/폴백/LLM 실패/자료 0건). 머리말 문구는 plan 상수에서 참조 — 복사 금지."""
    answer = delta.get("answer") or ""
    evidence = state.get("evidence") or []
    if answer.startswith(P.RAW_EVIDENCE.split("{", 1)[0]):
        return f"폴백 — 근거 원문 {len(evidence)}건을 그대로 출력 (문체가 달라지는 자리)"
    if delta.get("llm_error") or answer.startswith(P.LLM_FAILED.split("{", 1)[0]):
        return "LLM 실패 안내 (§11 — 근거 원문을 대신 내보내지 않는다)"
    if not evidence:
        return "자료 0건 — 없다고 답함"
    return "생성문 그대로"


@contextmanager
def instrument(trace: Trace):
    """계측 설치, 종료 시 무조건 복원."""
    _check_targets()
    saved = [(mod, attr, getattr(mod, attr)) for mod, attr in _TARGETS]

    def node_wrapper(name, fn):
        def wrapped(state):
            started = time.perf_counter()
            delta = fn(state) or {}
            elapsed = time.perf_counter() - started
            note = ""
            if name == "plan_step":
                note = _plan_note(state, delta)
            elif name == ANSWER_NODE:
                note = _compose_note(state, delta)
            trace.add_node(Node(name=name, delta=_summary(delta), note=note, seconds=elapsed))
            return delta
        return wrapped

    def llm_wrapper(fn):
        def wrapped(prompt, **kw):
            stage = _stage(prompt)
            started = time.perf_counter()
            try:
                text = fn(prompt, **kw)
            except Exception as exc:                     # noqa: BLE001 — 기록 후 재발생
                trace.add_call(Call(stage=stage, error=f"{type(exc).__name__}: {exc}",
                                    seconds=time.perf_counter() - started))
                raise
            trace.add_call(Call(stage=stage, text=text,
                                seconds=time.perf_counter() - started))
            return text
        return wrapped

    def pick_wrapper(fn):
        def wrapped(kinds, query):
            started = time.perf_counter()
            hits = fn(kinds, query)
            trace.add_call(Call(stage="pick", text=f"{list(kinds)} → {len(hits)}건",
                                seconds=time.perf_counter() - started))
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

    def span_wrapper(found, answer, *args, **kwargs):
        # 인자 전부 전달 — 누락 시 계측 실행만 판정이 달라짐
        verdict, gaps = real_span(found, answer, *args, **kwargs)
        trace.add_gate(Gate(name="span", passed=verdict != P.DISCARD,
                            detail=[verdict, *[label for label, _ in gaps]]))
        return verdict, gaps

    try:
        for name in NODE_NAMES:
            setattr(G, name, node_wrapper(name, getattr(G, name)))
        # 컴파일된 그래프 폐기 — 감싸기 전 노드가 박혀 있음
        G._AGENT = None
        U.generate = llm_wrapper(U.generate)
        P.generate = llm_wrapper(P.generate)
        CL.generate = llm_wrapper(CL.generate)
        T.generate = llm_wrapper(T.generate)
        T.llm_pick = pick_wrapper(T.llm_pick)
        S.llm_pick = pick_wrapper(S.llm_pick)
        P.verify_texts = verify_wrapper
        # 모듈 대신 plan 바인딩만 교체(공유 모듈 오염 방지)
        P.relations = types.SimpleNamespace(check=relations_wrapper)
        P._span_verdict = span_wrapper
        yield trace
    finally:
        for mod, attr, value in saved:
            setattr(mod, attr, value)
        # 계측 노드가 박힌 그래프 폐기
        G._AGENT = None


_TREE = ("├", "└")


def _secs(seconds: float) -> str:
    """소요 시간 문자열. 0.0 도 표시."""
    return f"{seconds:.2f}s"


def _pad(text: str, width: int) -> str:
    """한글 두 칸 폭 고려 패딩(`str.ljust` 사용 금지)."""
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


def render(trace: Trace, show_llm: bool = False, last_only: bool = False) -> str:
    """트레이스 텍스트 출력. `last_only` 는 REPL 용(마지막 턴만)."""
    turns = list(enumerate(trace.turns))
    if last_only:
        turns = turns[-1:]
    out: list[str] = ["━━ 트레이스 ━━"]
    out += [f"  ⚠ {n}" for n in trace.notes]
    for index, turn in turns:
        if len(turns) > 1:
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
                f"LLM {c.stage}"
                + (f" 실패({c.error})" if c.error else f" {len(c.text)}자")
                + f" {_secs(c.seconds)}"
                for c in node.calls)
            fields = " ".join(f"{k}={v}" for k, v in node.delta.items())
            # 답변 노드 처분은 게이트 트리 마지막 줄에 표시 — 중복 금지
            note = fields if node.name == ANSWER_NODE else (node.note or fields or "변화 없음")
            out.append(f"  {step} {_pad(label, 13)}[{_secs(node.seconds)}] {note}"
                       + (f"   ({calls})" if calls else ""))
            if node.name == ANSWER_NODE:
                out += _gate_lines(node)
        out.append(f"  ⏱ 턴 합계 {_secs(sum(n.seconds for n in turn.nodes))}"
                   f" (LLM {sum(c.seconds for n in turn.nodes for c in n.calls):.2f}s)")
        if show_llm:
            draft = trace.draft(index)
            if draft:
                out.append("\n── compose 가 LLM 에게 받은 문장" +
                           ("  (폐기됨)" if trace.blocked_by(index) else ""))
                out += [f"   {line}" for line in draft.splitlines()]
    return "\n".join(out)
