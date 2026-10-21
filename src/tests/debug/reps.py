"""대본 리허설 — 실 LLM 으로 돌려 답변과 트레이스를 나란히 표시. 채점 없음(회귀는 tests/test_agent.py).

    cd src
    python -m tests.debug.reps --help       # 대본·옵션 전부
    python -m tests.debug.reps                     # cases — 검토 12케이스 (기본)
    python -m tests.debug.reps cases 4 7           # 케이스 골라서
    python -m tests.debug.reps demo                # 전체 시연 대본  (docs/DEMO_SCENARIO.md)
    python -m tests.debug.reps library 김서연       # 시나리오 라이브러리 (docs/DEMO_CUSTOMER_SCENARIOS.md)
    python -m tests.debug.reps review              # 중간점검 시연본 지금 판 (docs/DEMO_REVIEW.md)
    python -m tests.debug.reps review@v5 이수민     # 그 판으로 · 고객 골라서
    python -m tests.debug.reps qa 김현수 윤가영      # 고객 12명 예상질문 (docs/QA_CUSTOMER_QUESTIONS.md)
    python -m tests.debug.reps --versions          # 중간점검본 판 이력
    python -m tests.debug.reps --diff v5 v6        # 두 판의 질문 차이와 바꾼 이유

- 위치 인자 = 대본, `--` 옵션 = 표시(--brief · --why · --show-llm · --time · --pause=N · --retry-down=N)
- cases 는 케이스마다 새 세션 + 전체 트레이스, 리허설(demo·library·review·qa)은 고객 블록마다 한 세션
- 기대값(`scenarios.EXPECT`)이 달린 턴만 코드가 아는 사실로 판정 · qa 는 턴마다 «기대» 한 줄 표시
- 실행 전후 `runtime/session_data/` 통째 복원 — 리허설 턴이 시연 픽스처에 남지 않도록
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import textwrap
import time
import unicodedata
from contextlib import contextmanager

from pension_agent.common import config
from pension_agent.common import llm as LLM
from tests.debug import scenarios as SCEN
from tests.debug import trace as TR
from tests.debug.runner import session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _tools(turn: TR.Turn) -> str:
    """턴이 부른 도구 순서(계획 노드 요약에서 추출)."""
    out = []
    for node in turn.nodes:
        if node.name != "plan_step" or "→" not in node.note:
            continue
        # 마지막 화살표로 분리 — 질의 안에 "→" 가 들어올 수 있음
        signature, _, result = node.note.rpartition("→")
        name = signature.split(":")[0].strip()
        out.append(name + ("✗" if "자료 없음" in result else ""))
    return " → ".join(out) or "(없음)"


def _stopped(node: TR.Node | None) -> TR.Gate | None:
    """생성문을 최종 폐기한 게이트, 없으면 None. 재작성으로 게이트가 여러 번 기록되므로 마지막 판정 기준."""
    if node is None:
        return None
    return next((g for g in {g.name: g for g in node.gates}.values() if not g.passed), None)


def _retries(node: TR.Node | None) -> int:
    """답변 재작성 횟수(compose 호출 수 − 1)."""
    if node is None:
        return 0
    return max(0, sum(1 for c in node.calls if c.stage == "compose") - 1)


def _log(turn: TR.Turn, result: dict, show_llm: bool = False) -> str:
    """시연용 로그 — 찾아본 재료와 LLM 작성 결과만(전체 트레이스는 cases 전용)."""
    titles = {s["id"]: (s.get("title") or "") for s in result.get("sources") or []}
    out = ["   ┌ 무엇을 찾아봤나"]
    step = 0
    for node in turn.nodes:
        if node.name != "plan_step" or "→" not in node.note:
            continue
        step += 1
        # 마지막 화살표로 분리(_tools 와 동일)
        signature, _, found = node.note.rpartition("→")
        tool, _, query = signature.strip().partition(":")
        out.append(f"   │ {step}. {tool.strip()} «{query.strip()}»")
        if "자료 없음" in found:
            out.append("   │      → 없음 (다른 도구로 넘어감)")
            continue
        for token in found.split():
            if token in ("채택",):
                continue
            cid = token.split("(")[0]
            out.append(f"   │      → {cid}  {titles.get(cid, '')}".rstrip())

    node = next((n for n in turn.nodes if n.name == TR.ANSWER_NODE), None)
    stopped = _stopped(node)
    if node is not None and node.delta.get("clarify"):
        out.append("   └ 질문의 갈래가 나뉘어 답 대신 선택지를 되물음 (써 둔 답은 폐기)")
        return "\n".join(out)
    verdict = (f"검증에서 걸림({stopped.name}) — 생성문 폐기" if stopped else
               "근거와 대조 통과" if (node and node.gates) else "대조할 수치 없음")
    if _retries(node):
        # 재작성 표시 — 없으면 첫 시도 통과로 오독
        verdict = f"{verdict} (한 번 걸려 다시 씀)"
    out.append(f"   └ 위 자료만 보고 LLM 이 {len(result.get('answer') or '')}자 작성 · {verdict}")
    # 폐기 사유 표시 — span detail 의 판정 상수(discard/append/ok)는 제외
    if stopped is not None and stopped.detail:
        readable = [str(d) for d in stopped.detail if str(d) not in ("discard", "append", "ok")]
        shown = readable[:6]
        more = f" 외 {len(readable) - len(shown)}건" if len(readable) > len(shown) else ""
        if shown:
            out.append(f"     ↳ 자료에 없다고 본 것: {' · '.join(shown)}{more}")
    if show_llm and stopped is not None:
        draft = next((c.text for n in turn.nodes for c in n.calls
                      if c.stage == "compose" and c.text), "")
        if draft:
            out.append("     ↳ 버려진 생성문:")
            out += [f"       {line}" for line in draft.splitlines()]
    return "\n".join(out)


@contextmanager
def _fixtures_intact():
    """`runtime/session_data/` 통째 스냅샷·복원 — 픽스처 구분 불필요."""
    src = config.SESSION_DATA_DIR
    backup = pathlib.Path(tempfile.mkdtemp(prefix="reps-session-"))
    if src.exists():
        shutil.copytree(src, backup / "session_data")
    try:
        yield
    finally:
        if (backup / "session_data").exists():
            shutil.rmtree(src, ignore_errors=True)
            shutil.move(str(backup / "session_data"), str(src))
        shutil.rmtree(backup, ignore_errors=True)


def _pad(text: str, width: int) -> str:
    """한글 두 칸 폭 고려 패딩(`str.ljust` 사용 금지)."""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - used)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _observed(result: dict, turn: TR.Turn) -> dict:
    """턴의 «코드가 아는 사실» — 기대값 판정 입력(scenarios.Expect). 답변 문장은 미사용.

    출처: `graph.ask` 반환값(도구·출처·연계) + 계측 상태 차분(판정·재계획·게이트).
    """
    node = next((n for n in turn.nodes if n.name == TR.ANSWER_NODE), None)
    delta = node.delta if node is not None else {}
    # LLM 실패 턴은 별도 결말 — 답변으로 집계 금지. 어느 노드에서든 남을 수 있음
    failed = any(n.delta.get("llm_error") or n.name == TR.LLM_DOWN_NODE for n in turn.nodes)
    gates = [g for n in turn.nodes for g in n.gates]
    return {
        # state.Turn 의 tools 사용
        "tools": ((result.get("history") or [{}])[-1] or {}).get("tools") or [],
        "outcome": "llm_down" if failed else "clarify" if result.get("clarify") else "answer",
        "verdict": delta.get("judge_verdict") or "",
        "replanned": any(n.delta.get("plan_retry") for n in turn.nodes),
        # 게이트 미실행 턴은 통과로 집계 금지
        "gates_passed": bool(gates) and _stopped(node) is None,
        "sources": bool(result.get("sources")),
        "offered": bool(result.get("pending_action")),
        # 갈래는 plan_step 반환값에 실림 — 턴 전체 탐색
        "branches": any(n.delta.get("branches") for n in turn.nodes),
    }


def _expect_line(script: str, label: str, result: dict, turn: TR.Turn) -> tuple[str, list[str]]:
    """기대값 판정 한 건. 반환: (요약 한 줄, 어긋난 항목). 기대가 없으면 ("", [])."""
    want = SCEN.expect_for(script, label)
    if want is None:
        return "", []
    misses = want.diff(_observed(result, turn))
    return ("✗ 기대 어긋남" if misses else "✓ 기대 통과"), misses

def _print_expectations(judged: list[tuple[str, str, list[str]]]) -> None:
    """기대값 판정 결과 — 어긋난 항목을 맨 아래 재집계. 기대 단 턴 없으면 미출력."""
    if not judged:
        return
    bad = [(label, misses) for label, verdict, misses in judged if misses]
    print(f"\n  기대값 — {len(judged) - len(bad)}/{len(judged)} 통과"
          + ("" if bad else " (전부 통과)"))
    for label, misses in bad:
        print(f"    ✗ {label}")
        for m in misses:
            print(f"        {m}")
    if bad:
        print("    어긋난 것이 구현 쪽인지 기대 쪽인지는 사람이 정합니다 —"
              " 기대가 틀렸으면 scenarios.EXPECT 를 고칩니다.")


def _row(no: object, sees: str, turn: TR.Turn, secs: float) -> list[str]:
    """요약표 한 줄. 시간 칸은 항상 채움(표시는 --time 이 결정 — 예외 행과 열 수 일치)."""
    names = [n.name for n in turn.nodes]
    node = next((n for n in turn.nodes if n.name == TR.ANSWER_NODE), None)
    stopped = _stopped(node)          # 마지막 판정 기준
    blocked = stopped.name if stopped else None

    # 되묻기는 답변과 같은 노드 — 상태 차분으로 구분
    if node is not None and node.delta.get("clarify"):
        end = "되묻기로 끝남"
    elif node is not None:
        end = node.note
    elif "llm_down" in names:
        end = "LLM 실패 안내"
    else:
        end = names[-1] if names else "?"

    offer = next((n for n in turn.nodes if n.name == "offer"), None)
    return [
        str(no),
        _tools(turn),
        (f"✗ {blocked}" if blocked else
         f"통과(재작성 {_retries(node)})" if _retries(node) else
         "통과" if (node and node.gates) else "안 걸림"),
        "제안" if (offer and offer.delta) else "",
        f"{secs:.1f}초",
        end,
        sees,
    ]


def _print_answer(r: dict) -> None:
    print(r["answer"])
    ground = [s for s in r["sources"] if s.get("role", "근거") == "근거"]
    caution = [s for s in r["sources"] if s.get("role") == "주의"]
    print("\n─ 근거" + ("" if ground else ": 없음"))
    for s in ground:
        _print_source_line(s)
    if caution:
        print("\n─ 이 고객 상담에서 지켜야 할 것")
        for s in caution:
            _print_source_line(s)


def _print_source_line(s: dict) -> None:
    # 운영 CLI 와 같은 공용 함수 사용(복사 금지)
    from pension_agent.tools import source_lines  # noqa: PLC0415 — graph 적재 뒤

    for line in source_lines(s, compact=True):
        print(line)


#: 대본 이름 → (목록, 문서). `review` 목록은 판별로 생성(`SCEN.review_blocks`)
SCRIPTS: dict[str, tuple[object, str]] = {
    "cases":   (SCEN.CASES,   "(문서 없음 — 케이스마다 `sees` 한 줄이 존재 이유다)"),
    "demo":    (SCEN.DEMO,    "docs/DEMO_SCENARIO.md"),
    "library": (SCEN.LIBRARY, "docs/DEMO_CUSTOMER_SCENARIOS.md"),
    "review":  (None,         "docs/DEMO_REVIEW.md"),
    "qa":      (SCEN.QA,      "docs/QA_CUSTOMER_QUESTIONS.md"),
}

DISPLAY_FLAGS = ("--brief", "--why", "--show-llm", "--time")
#: `--pause=20` — 턴 사이 대기(초). 분당 토큰 한도 키의 429 회피
PAUSE_FLAG = "--pause="
#: `--retry-down=2` — «LLM 호출 실패» 턴을 같은 질문으로 재질의(llm.py 는 5xx 미재시도). 실패 턴도 기록에 남음
RETRY_DOWN_FLAG = "--retry-down="
#: 재질의 전 대기(초)
RETRY_DOWN_WAIT = 15.0

#: 대본별 «리허설에서 볼 것» — 요약표 아래 출력. `review` 는 지금 판(`SCEN.LATEST`) 기준
CHECKS: dict[str, tuple[str, ...]] = {
    "review": (
        "  리허설에서 볼 것: ① 이 중도인출 사유와 세율(16.5%)로 답하는가 ·",
        "                    ② 가 되묻기로 끝나는가(끝나면 「초과야」로 닫는다) ·",
        "                    E1 에 «발송 화면 열까요?»가 붙고 E2 가 딥링크·문구를 주는가 ·",
        "                    ③ 이 타행 대비 당행 IRP 수익률(26.1Q)을 기준시점·«1위»의 한정과 함께 말하는가 ·",
        "                    ④ 가 «예금으로 두겠다»는 반론에 예금 편중 대비 화법으로 답하는가 ·",
        "                    ⑤ 가 «이동하지 않는다»로 답하는가 · ⑦ 이 계좌 3단 분리로 답하는가 ·",
        "                    M1 이 코드블록 안에 머리말(박정호 고객님·날짜) → ⑥⑦ 요약 항목 → [고객 주요 정보] 표로 나오고",
        "                    코드블록 밖에 «쪽지로 보낼까요?»가 붙는가 · 항목 줄 아닌 도입 문장이 본문에 없는가 ·",
        "                    M2 가 «쪽지를 보냈어요 — 받는 사람: 본인.» 한 줄로 끝나는가(본문 반복 없음).",
        "  ※ 이수민은 PENSION_TODAY=2026-09-17 이상이라야 만기 요건이 선다(만기 10/17).",
    ),
    "library": (
        "  리허설에서 볼 것: K3 이 되묻기로 끝나는가 · K5b 가 딥링크를 주는가 ·",
        "                    P4 도구 줄에 procedure·fact 가 찍히고 순서 경고를 세우는가 ·",
        "                    L3 이 «이동하지 않는다»로 답하는가 ·",
        "                    L4b 가 화법 제안을 승낙받아 카드를 보여주는가 ·",
        "                    S2 가 10개월 전 기록을 꺼내는가 · S5b 가 발송 «화면»을 여는가 ·",
        "                    S6 이 «없다»로 끝나는가 · J1 이 S4 와 같은 질문에 다른 답을 내는가.",
        "  ※ 이수민(3번)은 PENSION_TODAY=2026-09-17 이상이라야 만기 요건이 선다(만기 10/17).",
    ),
    "demo": (
        "  리허설에서 볼 것: T9 가 도구를 여러 개 부르는가 · T10 이 suitable 을 부르는가 ·",
        "                    T3 에 연계가 붙는가 · T11 에 «발송 화면 열까요?»가 붙는가 ·",
        "                    T12 가 «없다»로 끝나는가 ·",
        "                    T9 가 «비대면 전환 시 면제»(F53)를 대면 0.38% 와 모순 없이 잇는가.",
    ),
    "qa": (
        "  볼 것: 턴마다 붙은 «기대»의 값이 그 뜻으로 답에 들어 있는가 ·",
        "         상태 질문(JH7 잔여한도 0 · GY6 미설정 · DY1 요건 미충족 · SY7 콘텐츠 없음)이 «없다·아니다»로 답해지는가 ·",
        "         다건 질문(MS2 · SH2)이 한 건만 답하지 않는가 · 기록 없는 고객(HS6 · SE5 · SM7)이 «기록 없음» 한 줄로 끝나는가 ·",
        "         계산 턴(JM3 · JW4 · DY4 · PJ2 · SM5)이 «어느 계좌에» «어느 재원으로»를 적고 한도 축을 섞지 않는가.",
        "  ※ 기대는 PENSION_TODAY=2026-09-07 기준이다(scenarios.QA 머리말). 다른 날짜면 잔여일수·ISA 상태가 달라진다.",
    ),
}

#: 바뀐 옵션 이름 안내
RENAMED = {
    "--demo": "대본 이름 demo",
    "--scenario": "대본 이름 library",
    "--final": "대본 이름 review",
    "--debug": "--why  (전체 트레이스를 뜻하는 `python -m tests.debug --debug` 와 갈랐다)",
}


def _usage() -> None:
    print("""사용법: python -m tests.debug.reps [대본[@판]] [고객명·번호 …] [표시옵션 …]

  대본 — 하나만 고른다 (없으면 cases)
    cases              검토 12케이스. 케이스마다 새 세션 · 전체 트레이스를 붙인다
    demo               전체 시연 대본            docs/DEMO_SCENARIO.md
    library            고객별 시나리오 5종        docs/DEMO_CUSTOMER_SCENARIOS.md
    review             중간점검 시연본 지금 판     docs/DEMO_REVIEW.md
    review@v3          중간점검본의 그 판
    qa                 고객 12명 예상질문 (턴마다 «기대» 표시)   docs/QA_CUSTOMER_QUESTIONS.md

  골라 돌리기 — 블록 번호나 고객 이름을 뒤에 붙인다
    cases 4 7 · library 김서연 정민석 · review 이수민 · qa 김현수 윤가영

  표시옵션 — 겹쳐 쓸 수 있다
    --brief            요약표만 (붙여넣기 좋은 형태)
    --why              턴마다 «무엇을 찾아봤나 → LLM 이 몇 자 썼나»
    --show-llm         폐기된 생성문까지 (--why 를 함께 켠다)
    --time             턴별 소요 시간 (리허설 진단용 — 시연에서는 끈다)
    --pause=N          턴 사이 N초 대기 (분당 한도가 있는 키로 길게 돌릴 때)
    --retry-down=N     «LLM 호출 실패»로 끝난 턴을 같은 질문으로 N번까지 다시 묻는다 (간헐 5xx)

  중간점검본의 판
    --versions         판 이력 — 무엇을 왜 바꿨나
    --diff [판] [판]    두 판의 질문 차이 (생략하면 직전 판과 지금 판)""")


def _print_versions() -> int:
    print(f"\n중간점검 시연본 판 이력 — 지금 도는 판은 {SCEN.LATEST} "
          f"({SCRIPTS['review'][1]})\n")
    for v in SCEN.REVIEW:
        turns = sum(len(b[3]) for b in SCEN.review_blocks(v.name))
        print(f"  {v.name}  {v.date}  {v.summary}")
        print(f"       턴 {turns}개" + (f" · 직전 판에서 {len(v.edits)}건 바꿈" if v.edits else ""))
    print(f"\n  무엇을 왜 바꿨는지: python -m tests.debug.reps --diff "
          f"{SCEN.REVIEW[-2].name if len(SCEN.REVIEW) > 1 else SCEN.LATEST} {SCEN.LATEST}")
    print(f"  그 판으로 돌리기:   python -m tests.debug.reps review@{SCEN.REVIEW[0].name}")
    return 0


def _print_diff(older: str, newer: str) -> int:
    names = SCEN.version_names()
    lo, hi = sorted((names.index(older), names.index(newer)))
    print(f"\n중간점검 시연본 — {names[lo]} → {names[hi]}\n")
    if lo == hi:
        print("  같은 판입니다.")
        return 0
    for v in SCEN.REVIEW[lo + 1:hi + 1]:
        before = SCEN.questions_of(names[names.index(v.name) - 1])
        print(f"  {v.name}  {v.date}  {v.summary}")
        for e in v.edits:
            print(f"    [{e.op}] {e.label}")
            if e.op in ("고침", "뺌"):
                print(f"       전: {before.get(e.label, '(없음)')}")
            if e.op in ("고침", "더함"):
                print(f"       후: {e.question}")
            for i, line in enumerate(textwrap.wrap(e.why, 76)):
                print(("       왜: " if i == 0 else "           ") + line)
        print()
    print(f"  {names[hi]} 의 질문 구성")
    for _, sees, _, turns in SCEN.review_blocks(names[hi]):
        print(f"    {sees}")
        for label, question in turns:
            print(f"      {label}  {question}")
    return 0


def _versions_in(argv: list[str]) -> list[str]:
    return [a for a in argv if a in SCEN.version_names()]


def main(argv: list[str]) -> int:
    """검토·리허설 공용 진입 — 실행 경로 동일, 화면만 다름."""
    if "-h" in argv or "--help" in argv:
        _usage()
        return 0

    if "--versions" in argv:
        return _print_versions()

    if "--diff" in argv:
        picked_versions = _versions_in(argv)
        if len(picked_versions) > 2:
            print(f"--diff 는 판 둘까지입니다: {' '.join(picked_versions)}")
            return 1
        every = SCEN.version_names()
        older, newer = {
            0: (every[-2] if len(every) > 1 else every[0], SCEN.LATEST),
            1: (picked_versions[0] if picked_versions else "", SCEN.LATEST),
            2: tuple(picked_versions[:2]),
        }[len(picked_versions)]
        return _print_diff(older, newer)

    flags = [a for a in argv if a.startswith("-")]
    renamed = [a for a in flags if a in RENAMED]
    if renamed:
        print("이름이 바뀐 옵션입니다:")
        for a in renamed:
            print(f"  {a}  →  {RENAMED[a]}")
        print()
        _usage()
        return 1
    valued = {PAUSE_FLAG: 0.0, RETRY_DOWN_FLAG: 0.0}     # 값을 받는 옵션과 기본값
    unknown = [a for a in flags if a not in DISPLAY_FLAGS
               and not any(a.startswith(k) for k in valued)]
    if unknown:
        print(f"모르는 옵션입니다: {' '.join(unknown)}\n")
        _usage()
        return 1
    for a in flags:
        for key in valued:
            if a.startswith(key):
                try:
                    valued[key] = float(a[len(key):])
                except ValueError:
                    print(f"{key} 뒤에는 숫자를 씁니다: {a}")
                    return 1

    brief = "--brief" in argv
    show_llm = "--show-llm" in argv
    why = "--why" in argv or show_llm       # 폐기 생성문은 --why 로그 안에 표시
    timing = "--time" in argv
    pause = valued[PAUSE_FLAG]
    retry_down = int(valued[RETRY_DOWN_FLAG])

    # 첫 위치 인자 = 대본(없으면 cases), 나머지 = 블록 번호·고객 이름
    positional = [a for a in argv if not a.startswith("-")]
    script, version = "cases", ""
    if positional and positional[0].partition("@")[0] in SCRIPTS:
        script, _, version = positional.pop(0).partition("@")
    if version and script != "review":
        print(f"판(@{version})은 review 에만 있습니다 — {script} 는 판이 없는 대본입니다.")
        return 1
    if version and version not in SCEN.version_names():
        print(f"그런 판이 없습니다: {version}  (있는 판: {' · '.join(SCEN.version_names())})")
        return 1

    picked = {a for a in positional if a[0].isdigit()}
    names = {a for a in positional if not a[0].isdigit()}

    if not LLM.available():
        print("LLM 이 설정돼 있지 않습니다 — 이 스크립트는 실 LLM 으로 도는 것이 목적입니다.")
        print("  genai:     LLM_BASE_URL · LLM_API_KEY   (src/.env 에 두면 됩니다)")
        print("  gemma:     GEMINI_API_KEY")
        print("  anthropic: ANTHROPIC_API_KEY")
        return 1

    # 대본 차이: 목록 · 턴 라벨 출처 · 기본 트레이스 여부
    blocks = SCEN.review_blocks(version or None) if script == "review" else SCRIPTS[script][0]
    rehearsal = script != "cases"     # cases 만 라벨 없는 독립 케이스다
    trace_by_default = not rehearsal

    # 블록을 못 고른 값은 실패 — 대본 오타가 고객 이름으로 읽혀 빈 요약표가 됨
    missed = ([p for p in picked if not any(str(b[0]) == p for b in blocks)]
              + [n for n in names if not any(n in b[1] for b in blocks)])
    if missed:
        print(f"{script} 대본에 없는 값입니다: {' '.join(missed)}")
        print("  이 대본의 블록:")
        for b in blocks:
            print(f"    {b[0]}  {b[1]}")
        return 1

    if rehearsal and not brief:
        where = f"review@{version or SCEN.LATEST}" if script == "review" else script
        print(f"\n대본: {where}  ({SCRIPTS[script][1]})")

    rows: list[list[str]] = []
    judged: list[tuple[str, str, list[str]]] = []   # (라벨, 판정, 어긋난 항목)
    with _fixtures_intact():
        for no, sees, customer, turns in blocks:
            if (picked or names) and str(no) not in picked \
                    and not any(n in sees for n in names):
                continue
            labelled = (turns if rehearsal else
                        tuple((str(no) if i == 0 else f"{no}{chr(ord('a') + i)}", q) for i, q in enumerate(turns)))
            if rehearsal and not brief:
                print(f"\n{'━' * 70}\n{sees}"
                      + (f"\n(고객 화면 열림: {customer})" if customer else "\n(고객 화면 없음)"))

            # 리허설은 진행 줄까지 출력(질문과 답변 사이)
            show_progress = rehearsal and not brief
            on_progress = (lambda text: print(f"   ⋯ {text}")) if show_progress else None
            with session(customer_id=customer, on_progress=on_progress) as (ask, tr):
                for i, (label, question) in enumerate(labelled):
                    if pause and i:
                        # 블록 첫 턴은 대기 생략
                        time.sleep(pause)
                    if not brief:
                        if rehearsal:
                            print(f"\n{'─' * 70}\n[{label}] > {question}\n")
                            # qa 대본만 «기대» 표시(질문 바로 아래)
                            expect = SCEN.QA_EXPECT.get(label) if script == "qa" else None
                            if expect:
                                print(f"   기대: {expect}\n")
                        else:
                            who = f"  [고객 {customer}]" if customer else ""
                            print(f"\n{'═' * 70}\n[{label}] {sees}{who}\n> {question}\n")
                    asked = time.monotonic()
                    try:
                        result = ask(question)
                        # --retry-down — 요약표에는 마지막 시도 기록
                        for attempt in range(retry_down):
                            if not any(n.name == "llm_down" for n in tr.turns[-1].nodes):
                                break
                            if not brief:
                                print(f"   ⟳ LLM 호출 실패 — {RETRY_DOWN_WAIT:.0f}초 뒤 같은 질문을 "
                                      f"다시 묻는다 ({attempt + 1}/{retry_down})")
                            time.sleep(RETRY_DOWN_WAIT)
                            result = ask(question)
                    except Exception as exc:                       # noqa: BLE001 — 나머지 블록 계속
                        print(f"   실행 중단 — {type(exc).__name__}: {exc}")
                        rows.append([label, "—", "—", "", "—", f"예외 {type(exc).__name__}", sees])
                        break
                    took = time.monotonic() - asked
                    if not brief:
                        if show_progress:
                            if timing:
                                print(f"   ⋯ 답변까지 {took:.1f}초")
                            print()   # 진행 줄과 답변 구분
                        _print_answer(result)
                    rows.append(_row(label, sees if i == 0 else "└ 이어서", tr.turns[-1], took))
                    # 기대값 판정(scenarios.EXPECT) — 기대 없는 턴은 미출력
                    verdict, misses = _expect_line(script, label, result, tr.turns[-1])
                    if verdict:
                        judged.append((label, verdict, misses))
                        if not brief:
                            print(f"\n   {verdict}")
                            for m in misses:
                                print(f"       {m}")
                    if rehearsal and why and not brief:
                        print()
                        print(_log(tr.turns[-1], result, show_llm=show_llm))
                else:
                    if trace_by_default and not brief:
                        print()
                        print(TR.render(tr))

    print(f"\n{'═' * 70}\n요약 — 도구를 무엇을 어떤 순서로 골랐나\n")
    head = ["#", "도구(순서)", "게이트", "연계", "시간", "처분", "무엇을 보나"]
    table = [head, *rows]
    if not timing:
        drop = head.index("시간")
        table = [[c for i, c in enumerate(r) if i != drop] for r in table]
    widths = [max(_width(r[i]) for r in table) for i in range(len(table[0]))]
    for i, r in enumerate(table):
        print(("  " + "  ".join(_pad(c, w) for c, w in zip(r, widths, strict=True))).rstrip())
        if i == 0:
            print("  " + "  ".join("─" * w for w in widths))
    print("\n  도구 뒤의 ✗ 는 그 호출이 자료를 못 찾은 것 — 다음 칸에서 다른 도구로 옮겨갔는지가 요점입니다.")
    _print_expectations(judged)
    for line in CHECKS.get(script, ()):
        print(line)
    if script == "review" and version and version != SCEN.LATEST:
        print(f"  ※ 지금 돈 것은 {version} 이고 위 체크는 {SCEN.LATEST} 기준입니다 — "
              f"차이는 `--diff {version} {SCEN.LATEST}`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
