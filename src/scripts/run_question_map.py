# -*- coding: utf-8 -*-
"""질문 리스트를 실 LLM 으로 돌려 xlsx 의 「실측 답변」 칸을 채운다.

    cd src
    python -m scripts.run_question_map --new --grade ◎        # 아직 안 해본 1순위 전부
    python -m scripts.run_question_map --rows 17,54,62         # 번호로 골라서
    python -m scripts.run_question_map --item 세액공제 --full   # 항목으로 · 근거까지 기록
    python -m scripts.run_question_map --new --dry-run         # 무엇을 돌릴지만 본다

**기록하는 것은 답변 본문뿐이다.** 진행 표시(「제도·상품 수치 찾는 중…」)와 근거 목록·
화면 딥링크는 화면에만 찍고 칸에는 안 넣는다 — 셀 하나가 근거 덤프로 길어지면 표를
읽을 수 없게 되고, 근거는 어차피 트레이스(`python -m tests.debug --debug`)에서 본다.
함께 남기고 싶으면 `--full`(근거+화면링크) · `--sources`(근거만) · `--links`(화면만).

읽고 쓰는 파일은 `docs/QUESTION_MAP.xlsx` 하나다. 질문·대상 고객·선행 질문을 그 표에서
읽고, 같은 행의 「실측 답변」 칸에 답을 써서 다시 저장한다. **질문 자체를 고치려면 여기가
아니라 `scripts/question_map.py` 다**(xlsx 는 산출물이다 — 루트 CLAUDE.md 규칙 3).
생성기는 실측 답변을 보존하므로 이 순서로 섞어 써도 된다.

    python -m scripts.run_question_map ...     # 답을 채우고
    python -m scripts.question_map             # 질문을 고쳐 다시 만들어도 답은 남는다

━━ 돌리기 전에 ━━
① **오늘을 고정한다.** 「대상 고객」 칸이 2026-09-29 기준 계산값이라, 다른 날로 돌리면
   만기·연금개시 요건이 달라져 기대와 어긋난다. 이 스크립트가 `PENSION_TODAY` 를 안 잡아
   주지는 않는다 — 환경변수로 준다(기본값을 코드에 박으면 실데이터 전환 때 조용히 틀린
   날짜로 돈다).

       PENSION_TODAY=2026-09-29 python -m scripts.run_question_map --new --grade ◎

② **브리핑을 미리 만든다.** 고객을 여는 첫 턴은 브리핑 생성(LLM 11회)을 치른다. 분당
   한도가 있는 키면 그 11회가 한도를 소진해 답변 작성이 429 로 죽는다.
   `python -m scripts.prebuild_briefings` 를 한 번 돌려 두면 읽어 쓴다. 그래도 나면
   `--pause=20`.

③ **상담 기록을 더럽히지 않는다.** 실행은 `tests.debug.runner.session()` 을 그대로 쓰므로
   세션마다 기록이 남았다가 나갈 때 걷힌다(그 함수 주석). `git status src/session_data/`
   가 깨끗한지 끝나고 한 번 본다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from pension_agent import config
from scripts import question_map as QM

XLSX = QM.XLSX

#: 표에서 읽는 열. 없으면 그 자리에서 멈춘다 — 열 이름이 바뀐 것을 조용히 넘기면
#: 엉뚱한 칸에 답을 쓴다.
COLS = ("항목", "번호", "질문", "실측 답변", "시연", "대상 고객", "선행 질문")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load():
    """(워크북, 시트, 열이름→인덱스(1부터))."""
    try:
        from openpyxl import load_workbook
    except ModuleNotFoundError:
        raise SystemExit(
            "openpyxl 이 필요하다: pip install openpyxl\n"
            "(배포 이미지에는 안 들어간다 — 이 스크립트는 개발용이다)") from None
    if not XLSX.is_file():
        raise SystemExit(f"{XLSX} 가 없다. 먼저: python -m scripts.question_map")
    wb = load_workbook(XLSX)
    ws = wb.active
    head = [c.value for c in next(ws.rows)]
    idx = {}
    for name in COLS:
        if name not in head:
            raise SystemExit(f"xlsx 에 「{name}」 열이 없다 — 표를 다시 만든다: "
                             "python -m scripts.question_map")
        idx[name] = head.index(name) + 1
    return wb, ws, idx


def _pins(cell: str) -> list[str]:
    """「대상 고객」 칸에서 kb-pin 을 뽑는다. 여러 개면 대조 행이라 전부 돈다."""
    import re
    return re.findall(r"\d{6}-\d{7}", cell or "")


def _select(ws, idx, args) -> list[int]:
    """돌릴 행 번호(엑셀 행 인덱스)를 고른다."""
    want_rows = {int(x) for x in args.rows.split(",")} if args.rows else None
    picked = []
    for r in range(2, ws.max_row + 1):
        num = ws.cell(r, idx["번호"]).value
        item = str(ws.cell(r, idx["항목"]).value or "")
        q = str(ws.cell(r, idx["질문"]).value or "")
        demo = str(ws.cell(r, idx["시연"]).value or "")
        pre_cell = str(ws.cell(r, idx["선행 질문"]).value or "")
        done = ws.cell(r, idx["실측 답변"]).value

        if want_rows is not None and num not in want_rows:
            continue
        if args.grade and not demo.startswith(args.grade):
            continue
        if args.new and "안 해본 질문" not in demo:
            continue
        if args.item and args.item not in item:
            continue
        if pre_cell.startswith("["):        # 실행 대상이 아닌 행(상황 표기·장애)
            continue
        if done and not args.redo:
            continue
        picked.append(r)
        if args.limit and len(picked) >= args.limit:
            break
    return picked


def _answer_text(res: dict, args) -> str:
    """칸에 넣을 글. 기본은 답변 본문만 — 그게 이 스크립트의 요점이다."""
    parts = [res["answer"].strip()]
    if args.full or args.links:
        for item in res.get("links") or []:
            parts.append(f"[화면] {item['screen']} {item.get('label') or ''} → {item['url']}")
    if args.full or args.sources:
        from pension_agent.consult_agent.tools import source_lines
        lines = [ln for s in res.get("sources") or [] for ln in source_lines(s)]
        if lines:
            parts.append("─ 근거\n" + "\n".join(lines))
    return "\n\n".join(p for p in parts if p)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.run_question_map",
        description="질문 리스트를 돌려 docs/QUESTION_MAP.xlsx 의 「실측 답변」을 채운다.")
    pick = ap.add_argument_group("돌릴 행 고르기")
    pick.add_argument("--rows", help="번호 목록 (예: 17,54,62)")
    pick.add_argument("--grade", choices=["◎", "○", "–"], help="등급으로")
    pick.add_argument("--new", action="store_true", help="아직 안 해본 질문만")
    pick.add_argument("--item", help="항목 이름 일부 (예: 세액공제)")
    pick.add_argument("--limit", type=int, help="앞에서 N개만")
    pick.add_argument("--redo", action="store_true",
                      help="이미 채워진 칸도 다시 돌려 덮어쓴다 (기본은 건너뛴다)")
    rec = ap.add_argument_group("무엇을 칸에 적나 (기본: 답변 본문만)")
    rec.add_argument("--sources", action="store_true", help="근거 목록도 함께")
    rec.add_argument("--links", action="store_true", help="화면 딥링크도 함께")
    rec.add_argument("--full", action="store_true", help="근거 + 화면 딥링크 둘 다")
    run = ap.add_argument_group("실행")
    run.add_argument("--pause", type=float, default=0,
                     help="턴 사이 N초 쉬기 (분당 한도가 있는 키)")
    run.add_argument("--dry-run", action="store_true", help="무엇을 돌릴지만 찍고 끝낸다")
    run.add_argument("--quiet", action="store_true", help="진행 표시를 안 찍는다")
    args = ap.parse_args(argv)

    wb, ws, idx = _load()
    picked = _select(ws, idx, args)
    if not picked:
        print("돌릴 행이 없다. 조건을 넓히거나 --redo 를 붙인다.")
        return 0

    today = os.environ.get("PENSION_TODAY")
    print(f"■ {len(picked)}행 · 오늘={today or '실제 날짜(고정 안 됨 — 머리말 ①)'} · "
          f"기록={'답변+근거+화면' if args.full else '답변+근거' if args.sources else '답변+화면' if args.links else '답변 본문만'}")

    if args.dry_run:
        for r in picked:
            n = ws.cell(r, idx["번호"]).value
            q = ws.cell(r, idx["질문"]).value
            who = ws.cell(r, idx["대상 고객"]).value or ""
            pre = ws.cell(r, idx["선행 질문"]).value or ""
            print(f"  {n:>4} [{', '.join(_pins(who)) or '고객 없이'}] {q}"
                  + (f"   ↑ {pre}" if pre else ""))
        return 0

    from tests.debug.runner import session   # 운영 진입점(graph.ask)을 그대로 쓴다

    filled = failed = 0
    for i, r in enumerate(picked, 1):
        num = ws.cell(r, idx["번호"]).value
        q = str(ws.cell(r, idx["질문"]).value)
        who = str(ws.cell(r, idx["대상 고객"]).value or "")
        pre = [x.strip() for x in str(ws.cell(r, idx["선행 질문"]).value or "").split("→") if x.strip()]
        pins = _pins(who) or [None]

        chunks = []
        for cid in pins:
            label = who.split()[0] if cid and len(pins) > 1 else ""
            tag = f"[{i}/{len(picked)}] {num} {q[:40]}" + (f" ({label})" if label else "")
            print(f"\n▶ {tag}")
            on_progress = None if args.quiet else (lambda line: print(f"    · {line}"))
            try:
                with session(customer_id=cid, on_progress=on_progress) as (ask, _tr):
                    for p in pre:                      # 앞 턴들 — 답은 버린다
                        print(f"    (앞 턴) {p[:40]}")
                        ask(p)
                    res = ask(q)
                text = _answer_text(res, args)
            except Exception as e:                     # noqa: BLE001 — 한 행이 죽어도 계속
                failed += 1
                text = f"[실행 실패] {type(e).__name__}: {e}"
                print(f"    ✗ {text}")
            else:
                print(f"    ✓ {len(text)}자")
            chunks.append(f"● {label}\n{text}" if label else text)
            if args.pause:
                time.sleep(args.pause)

        ws.cell(r, idx["실측 답변"]).value = "\n\n".join(chunks)
        filled += 1
        wb.save(XLSX)                                  # 턴마다 저장 — 중간에 끊겨도 남는다

    print(f"\n■ {filled}행 기록" + (f" · {failed}건 실패" if failed else "")
          + f" → {XLSX.relative_to(config.REPO_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
