"""대표 질문 10개 — 실 LLM 으로 한 번 돌려 **답변과 트레이스를 나란히** 본다.

    cd src
    python -m tests.debug.reps              # 전체 (답변 + 근거 + 트레이스)
    python -m tests.debug.reps --brief      # 요약표만 — 이것만 붙여넣어도 진단이 된다
    python -m tests.debug.reps 4 7          # 케이스 골라서
    python -m tests.debug.reps --demo       # 시연 대본 순서대로 (docs/DEMO_SCENARIO.md)
    python -m tests.debug.reps --demo --debug   # 대본 + 재료→답변 로그 (시연에서 띄울 것)

왜 `tests.debug` 와 따로 있나: 저쪽 CLI 는 **한 세션**이라 질문을 여러 개 주면 맥락이
이어진다(멀티턴 재현이 목적이다). 대표 질문 10개는 서로 독립이어야 하므로 케이스마다
세션을 새로 연다 — 8번(모호 → 되묻기)이 앞 케이스의 맥락을 물려받으면 되물을 이유가
사라져 그 케이스가 무의미해진다. 후속 질문을 보는 7번만 한 케이스 안에 두 턴이다.

무엇을 재나 — 케이스마다 `sees` 에 적어둔 한 줄이 그 케이스의 존재 이유다. 축은 다섯이다:
**단일 도구**(1·2·3) · **복합**(4·5·6) · **후속 질문**(7) · **모호 → 되묻기**(8) ·
**지식베이스에 없는 것**(9) · **가드·반론**(10). 답변 품질만 보면 1번도 10번도 그냥
"괜찮네"로 읽히지만, 에이전틱한지는 **도구를 몇 개 어떤 순서로 골랐는가**에서만 갈린다.

**채점하지 않는다.** 통과·실패를 코드가 정하면 그건 회귀 테스트지 검토가 아니다
(회귀는 `tests/test_consult_agent.py` 가 이미 315건 재고 있다). 여기는 사람이 읽고
판단하는 자리라, 요약표는 «무엇이 일어났나»만 찍는다.

`--demo` 는 검토가 아니라 **리허설**이다. `docs/DEMO_SCENARIO.md` 의 대본을 그 순서로,
고객 블록마다 한 세션으로 돌린다 — 후속 질문(T2·T3b·T8b·T11)이 앞 턴을 이어받아야
대본대로이기 때문이다. 화면에 나가는 것만 보여주고, `--debug` 를 붙이면 턴마다
**어떤 재료가 들어가서 LLM 이 뭐라고 썼는지**를 짧게 붙인다(`_log`) — 시연에서 «지어낸 게
아니다»를 보여주는 자리다. 검토용의 전체 트레이스(노드·게이트 트리)는 진단 도구라
청중에게 띄울 것이 아니다.

**상담 기록을 남기지 않는다.** `graph.ask()` 는 턴마다 상담이력을 기록하는데(기준서 §2 —
진입점 한 곳에서 빠짐없이), `session_data/` 에는 시연 픽스처가 들어 있다
(`scripts/seed_sessions.py`). 그대로 돌리면 리허설 턴이 픽스처에 덧붙고, 대본 T5
(「지난번엔 무슨 얘기 했지?」)가 **10개월 전 고객 발언 대신 방금 리허설을 읽는다** —
돌릴수록 시연이 망가진다. 그래서 실행 전후로 디렉터리를 통째로 되돌린다. 기록 기능
자체는 그대로 돈다(끄면 그 경로를 예행하지 못한다) — 남은 것만 지운다.

**오늘은 2026-08-24 로 고정된다**(`tests/__init__.py` — 원장 스냅샷 기준일). 만기
잔여일수·미접촉 일수가 실행일마다 달라지면 두 번의 실행을 비교할 수 없다.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unicodedata
from contextlib import contextmanager

from pension_agent import config
from pension_agent import llm as LLM
from tests.debug import trace as TR
from tests.debug.runner import session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


#: (번호, 무엇을 보나, 고객, 질문들). 고객 id 는 `strategy_agent/customers.json` 의 9케이스.
CASES: tuple[tuple[int, str, str | None, tuple[str, ...]], ...] = (
    (1, "단일 도구 — fact 한 번으로 끝나나 (기준선)",
     None, ("IRP 세액공제 한도가 얼마야?",)),

    (2, "단일 도구 — screen 을 고르나 · 화면 연계 제안이 붙나",
     None, ("IRP 계좌 해지는 몇 번 화면에서 하지?",)),

    (3, "단일 도구 — channel(비대면) 과 screen(단말) 을 갈라 보나",
     None, ("고객이 스타뱅킹에서 직접 추가납입 하려면 어디로 들어가?",)),

    (4, "복합 — 고객 재료 + 화법. 성향-운용 불일치(공격투자형인데 예금 92%)",
     "181245-3097614", ("이 고객 예금만 들고 있는데 뭐라고 말해야 하지?",)),

    (5, "복합 — suitable(적합성 범위) 을 부르나. 권유가 아니라 범위로 답하나 (gap 27)",
     "165932-8741205", ("이 고객한테 뭘 추천해주면 좋을까?",)),

    (6, "복합 — 빗나가도 다른 도구로 갈아타나 (gap 23 · plan_misses/plan_retry)",
     "162754-9483106", ("이 고객은 왜 관리 대상으로 뜬 거야?",)),

    (7, "후속 질문 — 2턴째가 1턴 맥락을 이어받나 (gap 1·21)",
     "198734-1205842", ("이 고객 만기 언제야?", "그냥 두면 어떻게 돼?")),

    (8, "모호 — 답 대신 되묻나. 되묻기 턴에 근거가 붙나 (gap 22)",
     None, ("수수료 얼마야?",)),

    (9, "지식베이스 밖 — 지어내지 않고 없다고 하나 (재료 0건 경로)",
     None, ("타행 IRP 수수료는 우리보다 싼가?",)),

    (10, "가드·반론 — 고객 대사에 화법으로 답하고 하지 말 것이 걸리나 (§8)",
     "188406-7352194", ("고객이 '손실만 나는데 그냥 해지하겠다'는데 어떻게 대응하지?",)),
)


#: 시연 대본 — `docs/DEMO_SCENARIO.md`. 고객 블록마다 한 세션이므로 블록 안에서는 맥락이
#: 이어진다(T2 는 T1 을, T3b 는 T3 을 이어받는다). 블록이 갈리는 자리가 곧 시연에서
#: 「고객 화면을 바꾸는」 자리다.
DEMO: tuple[tuple[int, str, str | None, tuple[tuple[str, str], ...]], ...] = (
    (0, "0막 기본기 — 출처 · 후속 질문 · 화면 연계", None, (
        ("T1",  "IRP 세액공제 한도가 얼마야?"),                       # 근거가 있다
        ("T2",  "총급여 6천만원이면 얼마 돌려받아?"),                  # 맥락을 이어받는다
        ("T3",  "IRP 계좌 해지는 몇 번 화면에서 하지?"),               # 연계 제안
        ("T3b", "응, 열어줘"),                                         # 딥링크
    )),
    (1, "1~2막 상담 전·중 — 송도윤(방치현금 54% · ISA 만기 · 322일 미접촉)",
     "188406-7352194", (
        ("T4",  "이 고객 왜 관리 대상이야?"),                          # 타겟 근거
        ("T5",  "지난번엔 무슨 얘기 했지?"),                            # 상담 이력
        ("T6",  "이 고객한테 하면 안 되는 게 뭐야?"),                   # 금지·주의
        ("T7",  "고객이 '그 돈 그냥 둬도 되지 않나요' 하는데 뭐라고 하지?"),   # 반론 대응
        ("T8",  "수수료 얼마야?"),                                     # 되묻기
        ("T8b", "가입자부담금, 대면이요"),                             # 되물은 선택지를 고른다
        ("T9",  "우리 수수료가 얼마고, 증권사는 무료라는데 뭐라고 답하지?"),   # 복합 — 핵심
        ("T10", "그럼 이 고객한테 뭘 권할 수 있어?"),                   # 적합성 «범위»
        ("T11", "그 중에 ISA 만기자금이랑 같이 가져갈 만한 건?"),        # 후속
        ("T12", "타행 IRP 수수료는 우리보다 싼가?"),                    # 없다고 말한다
    )),
    (2, "3막 대조 — 정민석(공격투자형인데 원리금보장 100%)",
     "181245-3097614", (
        ("T13", "이 고객한테는 뭘 권할 수 있어?"),                      # 같은 질문, 다른 답
    )),
)


def _tools(turn: TR.Turn) -> str:
    """이 턴이 부른 도구를 순서대로. 계획 노드의 한 줄에서 도구 이름만 뽑는다."""
    out = []
    for node in turn.nodes:
        if node.name != "plan_step" or "→" not in node.note:
            continue
        signature, _, result = node.note.partition("→")
        name = signature.split(":")[0].strip()
        out.append(name + ("✗" if "자료 없음" in result else ""))
    return " → ".join(out) or "(없음)"


def _log(turn: TR.Turn, result: dict, show_llm: bool = False) -> str:
    """시연용 한 줄 로그 — **어떤 재료가 들어가서 LLM 이 뭐라고 썼나**, 그것만.

    트레이스 전체(`TR.render`)는 노드 순서와 게이트 트리까지 그리는 진단 도구다. 시연에서
    청중이 알고 싶은 건 그게 아니라 «지어낸 게 아니라 이 자료를 보고 쓴 것»이라는 사실
    하나이고, 그건 도구가 무엇을 어떤 질의로 찾아왔는지와 그 카드가 뭔지면 다 보인다.
    검토용(`CASES`)은 폐기 사유를 봐야 하므로 그쪽은 여전히 전체 트레이스를 쓴다.
    """
    titles = {s["id"]: (s.get("title") or "") for s in result.get("sources") or []}
    out = ["   ┌ 무엇을 찾아봤나"]
    step = 0
    for node in turn.nodes:
        if node.name != "plan_step" or "→" not in node.note:
            continue
        step += 1
        signature, _, found = node.note.partition("→")
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
    stopped = next((g for g in node.gates if not g.passed), None) if node else None
    if node is not None and node.delta.get("clarify"):
        out.append("   └ 질문의 갈래가 나뉘어 답 대신 선택지를 되물음 (써 둔 답은 폐기)")
        return "\n".join(out)
    verdict = (f"검증에서 걸림({stopped.name}) — 생성문 폐기" if stopped else
               "근거와 대조 통과" if (node and node.gates) else "대조할 수치 없음")
    out.append(f"   └ 위 자료만 보고 LLM 이 {len(result.get('answer') or '')}자 작성 · {verdict}")
    # 폐기된 턴에서 **무엇이 걸렸는지**까지 적는다. 이름만 남기면 화면에 떨어진 원문
    # 덤프를 보고도 «왜 잘렸나»를 알 수 없어, 고칠 것이 질문인지 자료인지 검증기인지
    # 가려지지 않는다 — 리허설에서 제일 먼저 알아야 하는 값이다.
    # span 게이트의 detail 첫 칸은 판정 상수(discard/append)라 사람이 읽을 값이 아니다 —
    # 걸린 스팬·카드만 남긴다.
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
    """`session_data/` 를 실행 전 상태로 되돌린다.

    골라서 지우지 않고 **통째로 스냅샷·복원**하는 이유는, 무엇이 픽스처이고 무엇이 이번
    실행이 만든 것인지 이 스크립트가 알 필요가 없어서다. 새 고객이 픽스처에 추가돼도
    여기는 손대지 않아도 된다.
    """
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
    """한글은 두 칸을 차지한다 — `str.ljust` 로 맞추면 표가 어긋난다."""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - used)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _row(no: object, sees: str, turn: TR.Turn) -> list[str]:
    """요약표 한 줄. 판정하지 않고 «무엇이 일어났나»만 적는다."""
    names = [n.name for n in turn.nodes]
    node = next((n for n in turn.nodes if n.name == TR.ANSWER_NODE), None)
    blocked = next((g.name for g in node.gates if not g.passed), None) if node else None

    # 되묻기는 답변 작성과 **같은 노드**에서 끝난다(nodes/answer.py) — 노드 이름으로는
    # 갈리지 않으므로 상태 차분을 본다. `_compose_note` 는 이 갈래를 따로 적지 않는다.
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
         "통과" if (node and node.gates) else "안 걸림"),
        "제안" if (offer and offer.delta) else "",
        end,
        sees,
    ]


def _print_answer(r: dict) -> None:
    print(r["answer"])
    ground = [s for s in r["sources"] if s.get("role", "근거") == "근거"]
    caution = [s for s in r["sources"] if s.get("role") == "주의"]
    print("\n─ 근거" + ("" if ground else ": 없음"))
    for s in ground:
        print(f"   · {s.get('doc') or '출처 미상'} — {s.get('title') or ''} [{s['id']}]")
    if caution:
        print("\n─ 이 고객 상담에서 지켜야 할 것")
        for s in caution:
            print(f"   · {s.get('doc') or '출처 미상'} — {s.get('title') or ''} [{s['id']}]")


def main(argv: list[str]) -> int:
    """검토(`CASES`)와 리허설(`--demo`)이 **같은 실행 경로**를 쓰고 화면만 갈린다 —
    리허설이 다른 경로로 돌면 그 리허설은 시연을 예행한 것이 아니다."""
    demo = "--demo" in argv
    brief = "--brief" in argv
    debug = "--debug" in argv
    show_llm = "--show-llm" in argv
    picked = {a for a in argv if a[0].isdigit()}

    unknown = [a for a in argv if a.startswith("--")
               and a not in ("--demo", "--brief", "--debug", "--show-llm")]
    if unknown:
        print(f"모르는 옵션입니다: {' '.join(unknown)}")
        print("  옵션: --demo · --brief · --debug · --show-llm · 케이스 번호")
        return 1

    if not LLM.available():
        print("LLM 이 설정돼 있지 않습니다 — 이 스크립트는 실 LLM 으로 도는 것이 목적입니다.")
        print("  genai:     LLM_BASE_URL · LLM_API_KEY   (src/.env 에 두면 됩니다)")
        print("  anthropic: ANTHROPIC_API_KEY")
        return 1

    # 두 모드의 차이는 셋뿐이다: 어떤 목록을 도는가 · 턴 라벨을 데이터가 주는가 ·
    # 트레이스를 기본으로 붙이는가.
    blocks = DEMO if demo else CASES
    trace_by_default = not demo

    rows: list[list[str]] = []
    with _fixtures_intact():
        for no, sees, customer, turns in blocks:
            if picked and not demo and str(no) not in picked:
                continue
            labelled = (turns if demo else
                        tuple((str(no) if i == 0 else f"{no}b", q) for i, q in enumerate(turns)))
            if demo and not brief:
                print(f"\n{'━' * 70}\n{sees}"
                      + (f"\n(고객 화면 열림: {customer})" if customer else "\n(고객 화면 없음)"))

            # 시연 리허설에서는 화면이 대기 중에 보여주는 진행 줄("⋯ ○○을 찾고 있어요")까지
            # 대본에 나와야 한다 — 응답 대기를 UX 로 보완한 것 자체가 시연 포인트다.
            # ask() 가 도는 동안 콜백이 그 자리에서 찍으므로 질문 줄과 답변 사이에 흐른다.
            show_progress = demo and not brief
            on_progress = (lambda text: print(f"   ⋯ {text}")) if show_progress else None
            with session(customer_id=customer, on_progress=on_progress) as (ask, tr):
                for i, (label, question) in enumerate(labelled):
                    if not brief:
                        if demo:
                            print(f"\n{'─' * 70}\n[{label}] > {question}\n")
                        else:
                            who = f"  [고객 {customer}]" if customer else ""
                            print(f"\n{'═' * 70}\n[{label}] {sees}{who}\n> {question}\n")
                    try:
                        result = ask(question)
                    except Exception as exc:                       # noqa: BLE001 — 한 턴이 죽어도
                        print(f"   실행 중단 — {type(exc).__name__}: {exc}")    # 나머지는 돈다
                        rows.append([label, "—", "—", "", f"예외 {type(exc).__name__}", sees])
                        break
                    if not brief:
                        if show_progress:
                            print()   # 진행 줄과 답변을 가른다
                        _print_answer(result)
                    rows.append(_row(label, sees if i == 0 else "└ 이어서", tr.turns[-1]))
                    if demo and debug and not brief:
                        print()
                        print(_log(tr.turns[-1], result, show_llm=show_llm))
                else:
                    if trace_by_default and not brief:
                        print()
                        print(TR.render(tr))

    print(f"\n{'═' * 70}\n요약 — 도구를 무엇을 어떤 순서로 골랐나\n")
    head = ["#", "도구(순서)", "게이트", "연계", "처분", "무엇을 보나"]
    table = [head, *rows]
    widths = [max(_width(r[i]) for r in table) for i in range(len(head))]
    for i, r in enumerate(table):
        print(("  " + "  ".join(_pad(c, w) for c, w in zip(r, widths, strict=True))).rstrip())
        if i == 0:
            print("  " + "  ".join("─" * w for w in widths))
    print("\n  도구 뒤의 ✗ 는 그 호출이 자료를 못 찾은 것 — 다음 칸에서 다른 도구로 옮겨갔는지가 요점입니다.")
    if demo:
        print("  리허설에서 볼 것: T9 가 도구를 여러 개 부르는가 · T10 이 suitable 을 부르는가 ·")
        print("                    T3 에 연계가 붙는가 · T12 가 «없다»로 끝나는가.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
