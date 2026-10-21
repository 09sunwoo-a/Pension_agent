# -*- coding: utf-8 -*-
"""질문 리스트를 실 LLM 으로 돌려 xlsx 의 「AI 답변」·「출처」·「앞 질문과 답」 칸 채우기.

    cd src
    python -m scripts.run_question_map --new --grade ◎        # 기존 대본에 없던 1순위 전부
    python -m scripts.run_question_map --rows 17,54,62         # 번호로 골라서
    python -m scripts.run_question_map --rows 10-20            # 구간 (양끝 포함 · 10~20 · 10–20 도 같다)
    python -m scripts.run_question_map --item 세액공제 --full   # 항목으로 · 근거까지 기록
    python -m scripts.run_question_map --new --dry-run         # 무엇을 돌릴지만 본다
    python -m scripts.run_question_map                         # 아직 안 채운 행 전부
    python -m scripts.run_question_map --retry-failed          # 실패로 끝난 칸만 다시
    python -m scripts.run_question_map --auto                  # 답이 대조로 갈리는 항목만
    python -m scripts.run_question_map --manual                # 사람이 읽어야 하는 항목만

기본 기록은 답변 본문만(근거·화면링크는 `--full`·`--sources`·`--links`). 대상 파일 `docs/QUESTION_MAP.xlsx` —
질문 수정은 `scripts/question_map.py`(답·출처·앞 턴 칸은 재생성 시 보존).

돌리기 전:
① 오늘 고정 — 「대상 고객」이 2026-09-29 기준. 코드 기본값 없음, 환경변수로:
       PENSION_TODAY=2026-09-29 python -m scripts.run_question_map --new --grade ◎
② 분당 한도 키는 `--pause=20`.
③ 끝난 뒤 `git status src/runtime/session_data/` 확인.
④ 쪽지 행은 사번 필요(없으면 제안 자체를 안 함). 번호 대신 `--item` 사용(번호는 밀림):
       WORKB_EMP_NO=3902172 PENSION_TODAY=2026-09-29 python -m scripts.run_question_map --item 쪽지
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from pension_agent.common import config
from scripts import question_map as QM

XLSX = QM.XLSX

#: 표에서 읽는 열. 없으면 중단(열 이름 변경 시 엉뚱한 칸 기록 방지).
COLS = ("항목", QM.NUM_COL, "질문", QM.ANS_COL, QM.SRC_COL,
        "시연", "대상 고객", "선행 질문")

#: 실패 칸 표식. 이 글이 있는 칸은 미채움으로 간주(`--retry-failed` 대상).
FAILED = "[실행 실패]"

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
    # 머리글은 1행 아님 — 위에 제목·설명 블록
    head_row, head = QM.find_head(ws)
    idx = {}
    for name in COLS:
        if name not in head:
            raise SystemExit(f"xlsx 에 「{name}」 열이 없다 — 표를 다시 만든다: "
                             "python -m scripts.question_map")
        idx[name] = head.index(name) + 1
    return wb, ws, idx, head_row


def _filled(ws, col: int, first_row: int) -> dict[int, str]:
    """세로 병합 칸을 앞 값으로 채워 {엑셀 행: 값} 반환(병합 칸은 첫 행에만 값)."""
    out, cur = {}, ""
    for r in range(first_row, ws.max_row + 1):
        v = ws.cell(r, col).value
        if v not in (None, ""):
            cur = str(v)
        out[r] = cur
    return out


def _pins(cell: str) -> list[str]:
    """「대상 고객」 칸의 kb-pin 목록. 여럿이면 대조 행."""
    import re
    return re.findall(r"\d{6}-\d{7}", cell or "")


#: kb-pin → 이름. 대조 행 이름표용 — 칸 첫 낱말 사용 금지(항상 첫 고객 이름이 됨).
_NAME_OF = {pin: name for name, pin in QM.PIN.items()}


def _llm_down(tr) -> bool:
    """LLM 장애로 끝난 턴인지 — 장애는 정상 답변으로 나오므로 문구 대신 트레이스로 판정."""
    from tests.debug import trace as TR
    turn = tr.turns[-1] if getattr(tr, "turns", None) else None
    if turn is None:
        return False
    return any(n.delta.get("llm_error") or n.name == TR.LLM_DOWN_NODE for n in turn.nodes)


#: `--rows` 구간 구분자(영문·한글 자판, 복사된 en/em dash).
_RANGE_SEPS = "-~–—"


def parse_rows(spec: str) -> set[int]:
    """`--rows` → 번호 집합(예: 1-9,13). 구간은 양끝 포함, 거꾸로 쓴 구간은 거절(오타 실행 방지)."""
    want: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        cut = next((i for i, ch in enumerate(chunk) if ch in _RANGE_SEPS and i > 0), None)
        if cut is None:
            if not chunk.isdigit():
                raise SystemExit(f"--rows: 번호가 아닙니다 — {chunk!r}")
            want.add(int(chunk))
            continue
        lo, hi = chunk[:cut].strip(), chunk[cut + 1:].strip()
        if not (lo.isdigit() and hi.isdigit()):
            raise SystemExit(f"--rows: 구간이 «숫자-숫자» 가 아닙니다 — {chunk!r}")
        if int(lo) > int(hi):
            raise SystemExit(f"--rows: 구간이 거꾸로입니다 — {chunk!r} (작은 번호를 앞에)")
        want.update(range(int(lo), int(hi) + 1))
    if not want:
        raise SystemExit("--rows: 고른 번호가 없습니다")
    return want


def _select(ws, idx, args, first_row: int) -> list[int]:
    """돌릴 엑셀 행 인덱스 선택."""
    want_rows = parse_rows(args.rows) if args.rows else None
    items = _filled(ws, idx["항목"], first_row)   # 세로 병합
    picked = []
    # 번호로 지목했는데 빠진 행은 사유 출력용으로 수집
    skipped: dict = {}
    for r in range(first_row, ws.max_row + 1):
        num = ws.cell(r, idx[QM.NUM_COL]).value
        item = items[r]
        demo = str(ws.cell(r, idx["시연"]).value or "")
        pre_cell = str(ws.cell(r, idx["선행 질문"]).value or "")
        done = ws.cell(r, idx[QM.ANS_COL]).value

        if want_rows is not None and num not in want_rows:
            continue
        broken = isinstance(done, str) and FAILED in done
        why = None
        if args.grade and not demo.startswith(args.grade):
            why = f"시연 등급이 {args.grade!r} 가 아니다"
        elif args.new and "신규" not in demo:
            why = "신규 행이 아니다"
        elif args.item and args.item not in item:
            why = f"항목이 {args.item!r} 가 아니다"
        elif args.auto and not QM.is_auto(item):
            why = "사람이 읽는 항목이다(--auto 제외)"
        elif args.manual and QM.is_auto(item):
            why = "자동 판정 항목이다(--manual 제외)"
        elif pre_cell.startswith("["):        # 실행 대상이 아닌 행(상황 표기·장애)
            why = f"실행기가 돌리지 않는 행 — {pre_cell.strip('[] ')}"
        # 실패 칸은 미채움 취급. 대조 행 부분 실패는 칸 중간에 표식 — startswith 금지(in 사용)
        elif args.retry_failed and not broken:
            why = "실패로 끝난 칸이 아니다(--retry-failed 제외)"
        elif done and not broken and not args.redo:
            why = "이미 답이 채워져 있다 — 다시 돌리려면 --redo"
        if why:
            if want_rows is not None:
                skipped[num] = why
            continue
        picked.append(r)
        if args.limit and len(picked) >= args.limit:
            break

    for num, why in skipped.items():
        print(f"⚠ {num}번은 건너뛴다 — {why}")

    # 표에 없는 번호 지목 시 경고(조용히 빠지면 원인 불명)
    if want_rows is not None:
        have = {ws.cell(r, idx[QM.NUM_COL]).value for r in range(first_row, ws.max_row + 1)}
        missing = sorted(n for n in want_rows if n not in have)
        if missing:
            shown = ", ".join(str(n) for n in missing[:12])
            print(f"⚠ 표에 없는 번호 {len(missing)}개는 건너뛴다 — {shown}"
                  + (" …" if len(missing) > 12 else ""))
    return picked


def _body(res: dict) -> str:
    """화면 장치(참고 자료·추천질문·제안 문구)를 뗀 답변 본문. 규칙은 `answered._body` 재사용(복사 금지)."""
    from pension_agent.tools.general.answered import _body as strip_devices
    turns = res.get("history") or []
    stored = (turns[-1] or {}).get("answer") if turns else None
    return strip_devices(stored or res.get("answer") or "")


def _answer_text(res: dict, args) -> str:
    """답변 칸에 넣을 글. 기본은 본문만."""
    parts = [_body(res)]
    if args.full or args.links:
        for item in res.get("links") or []:
            parts.append(f"[화면] {item['screen']} {item.get('label') or ''} → {item['url']}")
    if args.full or args.sources:
        from pension_agent.tools import source_lines
        lines = [ln for s in res.get("sources") or [] for ln in source_lines(s)]
        if lines:
            parts.append("─ 근거\n" + "\n".join(lines))
    return "\n\n".join(p for p in parts if p)


def _source_text(res: dict) -> str:
    """「출처」 칸 글 — 답변이 참고한 근거 목록."""
    from pension_agent.tools import source_lines
    lines = [ln for s in res.get("sources") or [] for ln in source_lines(s)]
    return "\n".join(lines)


def _pre_text(res: dict, args) -> str:
    """앞 턴 칸 글 — 본문 + 제안 문구(`pending_action["prompt"]`). 다음 승낙 턴 해석에 필요."""
    text = _answer_text(res, args)
    ask = ((res.get("pending_action") or {}).get("prompt") or "").strip()
    return f"{text}\n— {ask}" if ask and ask not in text else text


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.run_question_map",
        description="질문 리스트를 돌려 docs/QUESTION_MAP.xlsx 의 「AI 답변」을 채운다.")
    pick = ap.add_argument_group("돌릴 행 고르기")
    pick.add_argument("--rows", help="번호 목록·구간 (예: 17,54,62 · 10-20 · 1-9,13)")
    pick.add_argument("--grade", choices=["◎", "○", "–"], help="등급으로")
    pick.add_argument("--new", action="store_true",
                      help="기존 대본·QA 에 없던 질문만 (「시연」 칸이 «신규»)")
    pick.add_argument("--item", help="항목 이름 일부 (예: 세액공제)")
    pick.add_argument("--auto", action="store_true",
                      help="답이 대조로 갈리는 항목만 (question_map.AUTO_ITEMS)")
    pick.add_argument("--manual", action="store_true",
                      help="사람이 읽어야 판정되는 항목만 (--auto 의 나머지)")
    pick.add_argument("--limit", type=int, help="앞에서 N개만")
    pick.add_argument("--redo", action="store_true",
                      help="이미 채워진 칸도 다시 돌려 덮어쓴다 (기본은 건너뛴다)")
    pick.add_argument("--retry-failed", action="store_true",
                      help="[실행 실패] 로 끝난 칸만 다시 돌린다")
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

    # .env 선로드 — 없으면 아래 PENSION_TODAY 표시가 «고정 안 됨»으로 잘못 출력
    from pension_agent.common import env as _env
    _env.load()

    wb, ws, idx, head_row = _load()
    first_row = head_row + 1
    # 질문 칸은 세로 병합 + 앞 턴이 붙어 있을 수 있음 — 질문만 추출
    qs = {r: QM.bare_question(v) for r, v in _filled(ws, idx["질문"], first_row).items()}
    picked = _select(ws, idx, args, first_row)
    if not picked:
        print("돌릴 행이 없다. 조건을 넓히거나 --redo 를 붙인다.")
        return 0

    today = os.environ.get("PENSION_TODAY")
    print(f"■ {len(picked)}행 · 오늘={today or '실제 날짜(고정 안 됨 — 머리말 ①)'} · "
          f"기록={'답변+근거+화면' if args.full else '답변+근거' if args.sources else '답변+화면' if args.links else '답변 본문만'}")

    if args.dry_run:
        for r in picked:
            n = ws.cell(r, idx[QM.NUM_COL]).value
            q = qs[r]
            who = ws.cell(r, idx["대상 고객"]).value or ""
            pre = ws.cell(r, idx["선행 질문"]).value or ""
            print(f"  {n:>4} [{', '.join(_pins(who)) or '고객 없이'}] {q}"
                  + (f"   ↑ {pre}" if pre else ""))
        return 0

    from tests.debug.runner import session   # 운영 진입점(graph.ask) 그대로

    filled = failed = kept = 0
    for i, r in enumerate(picked, 1):
        num = ws.cell(r, idx[QM.NUM_COL]).value
        q = qs[r]
        who = str(ws.cell(r, idx["대상 고객"]).value or "")
        pre = [x.strip() for x in str(ws.cell(r, idx["선행 질문"]).value or "").split("→") if x.strip()]
        pins = _pins(who) or [None]

        chunks, pre_chunks, src_chunks = [], [], []
        for cid in pins:
            label = _NAME_OF.get(cid, cid or "") if cid and len(pins) > 1 else ""
            tag = f"[{i}/{len(picked)}] {num} {q[:40]}" + (f" ({label})" if label else "")
            print(f"\n▶ {tag}")
            on_progress = None if args.quiet else (lambda line: print(f"    · {line}"))
            try:
                with session(customer_id=cid, on_progress=on_progress) as (ask, tr):
                    # 앞 턴 답도 기록 — 승낙 턴 해석에 직전 제안 필요
                    log = []
                    for n, p in enumerate(pre, 1):
                        print(f"    (앞 턴 {n}) {p[:40]}")
                        log.append(f"{n}. {p}\n→ {_pre_text(ask(p), args)}")
                    res = ask(q)
                    down = _llm_down(tr)
                text = _answer_text(res, args)
                pre_log = "\n\n".join(log)
                src_log = _source_text(res)
            except Exception as e:                     # noqa: BLE001 — 한 행이 죽어도 계속
                failed += 1
                text = f"{FAILED} {type(e).__name__}: {e}"
                pre_log = src_log = ""
                print(f"    ✗ {text}")
            else:
                # LLM 장애 턴은 예외 없이 안내 문장으로 반환됨 — 실패로 표시
                if down:
                    failed += 1
                    text = f"{FAILED} LLM 다운 — {text}"
                    print(f"    ✗ LLM 다운으로 끝난 턴 ({len(text)}자)")
                else:
                    print(f"    ✓ {len(text)}자")
                # 셀과 같은 글을 터미널에도 출력
                print("    ┌ 답변")
                for line in text.splitlines():
                    print(f"    │ {line}")
                print("    └")
            chunks.append(f"● {label}\n{text}" if label else text)
            if pre_log:
                pre_chunks.append(f"● {label}\n{pre_log}" if label else pre_log)
            if src_log:
                src_chunks.append(f"● {label}\n{src_log}" if label else src_log)
            if args.pause:
                time.sleep(args.pause)

        # 실패 결과로 기존 정상 답 덮어쓰기 금지(--redo 중 쿼터 소진 대비) — 실패는 출력·종료코드로만
        fresh = "\n\n".join(chunks)
        had = ws.cell(r, idx[QM.ANS_COL]).value
        if FAILED in fresh and had and FAILED not in str(had):
            kept += 1
            print(f"    ↩ 실패 — 이미 있던 답을 그대로 둔다 ({len(str(had))}자)")
        else:
            ws.cell(r, idx[QM.ANS_COL]).value = fresh
        # 앞 턴 없으면 미기록(옛 기록 보존). 앞 턴은 질문 칸에 QM.PRE_MARK 로 붙임
        if pre_chunks:
            ws.cell(r, idx["질문"]).value = (
                QM.bare_question(ws.cell(r, idx["질문"]).value)
                + QM.PRE_MARK + "\n\n".join(pre_chunks))
        if src_chunks:
            ws.cell(r, idx[QM.SRC_COL]).value = "\n\n".join(src_chunks)
        filled += 1
        wb.save(XLSX)                                  # 행마다 저장 — 중단 대비

    print(f"\n■ {filled}행 기록" + (f" · {failed}건 실패" if failed else "")
          + (f" · {kept}행은 옛 답을 지켰다" if kept else "")
          + f" → {XLSX.relative_to(config.REPO_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
