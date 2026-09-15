"""지식베이스 원문 폴더 → knowledge/data 레코드 변환기.

06 폴더의 추출지식 문서는 사람이 읽는 검토용 마크다운이다. 그 안의 항목을 지식베이스 레코드
(kinds.json 선언)로 결정론적으로 옮긴다. 손으로 옮기지 않는 이유는 두 가지다 — 항목이 수백 건이라
누락·오타가 생기고, 원문이 개정될 때마다 다시 대조할 방법이 없어진다.

05_시황_상품_기반지식은 구조가 다르다 — 항목 인덱스가 없고 **문서 자체가 원문**이라,
front-matter 와 `##` 절을 그대로 카드로 옮긴다(build_market).

원칙
  · 결정론·멱등 — 같은 입력이면 같은 출력. 생성 JSON 을 손으로 고치지 않는다(고칠 값은 config.py 로).
  · 무손실 출처 — 원문의 출처 표기 줄을 `source_text` 로 그대로 보존하고, 해석에 성공한 것만
    `doc` 레지스트리 id 로 함께 남긴다. 해석 실패는 값을 지어내지 않고 리포트로 알린다.
  · 검토 게이트 — `_draft_` 접두로 쓴다(common/kb_base.iter_knowledge_files 가 `_` 파일을 건너뛴다).
    사람이 확인한 뒤 `--activate` 로 접두를 뗀다.
  · 개인정보 미이관 — 작성자 실명·부점·직급은 옮기지 않는다(AUTHORING.md §4). 출처 추적은
    doc 레지스트리의 글번호·게시일로 한다.

실행 (src/ 에서)
    python -m scripts.kb_build.build_kb             # _draft_kb_*.json 생성 + 변환 리포트
    python -m scripts.kb_build.build_kb --activate  # 검토 끝난 _draft_ 파일을 활성화(접두 제거)

구성 — 이 파일은 CLI(순서·리포트)만 갖고, 변환은 종류별 모듈이 한다:
    common.py      경로 상수 · 리포트 · 텍스트 유틸 · 역할 분류 · 레코드 · 상호참조 · 쓰기
    docs.py        원천 문서 레지스트리 · 출처 해석(DocResolver)
    parse.py       05 계열 문서 공통 파싱(인덱스 · 항목 블록 · 필드)
    segments.py · pitches.py · methods.py · fieldtips.py · facts.py   종류별 변환기
    market.py      05 시황·상품 → doc + market + lineup, 표 → 관계 선언
    procedures.py  06/05 업무처리절차 → screen(표A) · channel(표B) · procedure
"""

from __future__ import annotations

import json
import sys

from scripts.kb_build import config

from scripts.kb_build.common import OUT_DIR, _report, inherit_parent_source, link_xrefs, note, write, xref_index
from scripts.kb_build.docs import DocResolver, build_docs
from scripts.kb_build.facts import build_facts
from scripts.kb_build.fieldtips import build_fieldtips
from scripts.kb_build.market import build_market
from scripts.kb_build.methods import build_methods
from scripts.kb_build.pitches import build_pitches, useful_trigger
from scripts.kb_build.procedures import build_channels, build_procedures, build_screens
from scripts.kb_build.segments import build_segments


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def activate() -> None:
    moved = 0
    for path in sorted(OUT_DIR.glob("_draft_kb_*.json")):
        target = path.with_name(path.name.removeprefix("_draft_"))
        path.replace(target)
        print(f"  활성화 {path.name} → {target.name}")
        moved += 1
    print(f"{moved}개 파일 활성화" if moved else "활성화할 _draft_ 파일이 없습니다")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if "--activate" in sys.argv:
        activate()
        return 0

    docs, by_base = build_docs()
    # 05 는 문서 자체가 원문이라 doc 레지스트리에 함께 실린다 — 카드가 source.doc 로
    # 그 문서를 가리키고, 신뢰 표시(tier)·고객 안내 가능 여부도 거기서 한 번만 관리된다.
    market_docs, market_by_kind = build_market()
    market_cards = market_by_kind["market"]
    lineup_cards = market_by_kind["lineup"]
    docs += market_docs
    resolver = DocResolver(by_base, docs)
    segments = inherit_parent_source(build_segments(resolver))
    pitches = build_pitches(resolver)
    facts, pending = build_facts(resolver)
    procedures = build_procedures(resolver)
    screens_ = build_screens(resolver)
    channels = build_channels(resolver)
    methods = inherit_parent_source(build_methods(resolver))
    fieldtips = build_fieldtips(resolver)

    # 항목 상호참조는 **전 종류가 만들어진 뒤**에 잇는다 — 05 의 표A(screen)가 05 의 절차
    # 항목을 가리키듯, 번호는 같은 원문 파일 안의 다른 카드를 가리킨다. 가리키는 종류는
    # 원문 파일이 정한다(06/01 → seg · 02 → m · 03 → pitch · 05 → proc).
    xrefs = xref_index(segments, methods, pitches, procedures)
    xref_report: list[str] = []
    for group, target in ((segments, "seg"), (methods, "m"), (pitches, "pitch"),
                          (procedures, "proc"), (screens_, "proc"), (channels, "proc")):
        link_xrefs(group, target, xrefs, xref_report)

    write("kb_docs", "doc", "원천 문서 레지스트리 (01~05·08 폴더)", docs, "2026-08")
    write("kb_segments", "segment", "고객 세그먼트 — 06/01 고객세그먼트", segments, "2026-08")
    write("kb_methods", "method", "IRP 관리 방법론 — 06/02 IRP관리방법론", methods, "2026-08")
    write("kb_pitches", "pitch", "영업 화법 — 06/03 영업화법", pitches, "2026-08")
    write("kb_facts", "fact", "제도·상품 팩트 — 06/04 제도상품팩트", facts, "2026-08")
    write("kb_procedures", "procedure", "업무 처리 절차 — 06/05 업무처리절차", procedures, "2026-08")
    write("kb_screens", "screen", "단말 화면번호 일람 — 06/05 업무처리절차 표A", screens_, "2026-08")
    write("kb_channels", "channel", "비대면 채널 처리 경로 — 06/05 업무처리절차 표B",
          channels, "2025-03-31")
    write("kb_fieldtips", "fieldtip", "현장의 목소리 — 08_인사이트", fieldtips, "2026-08",
          origin_dir=config.INSIGHT_DIR)
    # 시황·상품은 카드마다 as_of 가 다르다(주간 8월 3주차 · 월간 9월 · 상품 8월). 파일
    # meta.as_of 하나로 접으면 어느 회차 수치인지가 사라지므로 여기는 "폴더 기준"만 적고,
    # 답변에 나가는 기준시점은 카드의 as_of 다(tools.stale_mark).
    write("kb_market", "market", "시황 기반지식 — 05_시황_상품_기반지식/01_시황",
          market_cards, "2026-09", origin_dir=config.MARKET_DIR)
    write("kb_lineup", "lineup", "운용 상품 기반지식 — 05_시황_상품_기반지식/02_상품",
          lineup_cards, "2026-08", origin_dir=config.MARKET_DIR)
    # 보류 팩트는 활성화하지 않는다 — 04 규칙상 확인 전에는 검증 기준으로 쓸 수 없다.
    path = OUT_DIR / "_hold_kb_facts_pending.json"
    path.write_text(json.dumps(
        {"meta": {"kind": "fact", "title": "제도·상품 팩트(확인 필요) — 활성화 금지",
                  "as_of": "2026-08", "confidential": True,
                  "note": "06/04 가 '⚠ 확인 필요' 로 표시한 팩트. 근거가 확인되기 전에는 지식베이스에 "
                          "넣지 않는다 — 적재되는 순간 에이전트가 사실로 답하기 때문이다. "
                          "해소되면 06/04 원문을 고치고 변환기를 다시 돌린다."},
         "records": pending}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    built = (segments + methods + pitches + facts + procedures + screens_
             + channels + fieldtips + market_cards + lineup_cards)

    # 검색 예시 보강표(config.TRIGGER_EXTRA) 검증 — 없는 카드를 가리키거나, 그 카드의 주제어를
    # 담지 않은 문장은 알린다. 표는 원문 주제어를 옮긴 파생 텍스트여야 하고 지어낸 것이면 안 된다.
    by_id = {r["id"]: r for r in built}
    for cid, extras in config.TRIGGER_EXTRA.items():
        r = by_id.get(cid)
        if r is None:
            note(f"[보강표 대상없음] TRIGGER_EXTRA {cid} — 그런 카드가 없다")
            continue
        title = r["fields"].get("title") or ""
        for extra in extras:
            if not useful_trigger(extra, title):
                note(f"[보강표 주제어없음] {cid} '{extra[:30]}…' — 제목·주제 어휘와 겹치는 말이 없다")
    print(f"doc {len(docs)}건 · segment {len(segments)}건 · method {len(methods)}건 "
          f"· pitch {len(pitches)}건 · fact {len(facts)}건(보류 {len(pending)}건) "
          f"· procedure {len(procedures)}건 · screen {len(screens_)}건 "
          f"· channel {len(channels)}건 · fieldtip {len(fieldtips)}건 "
          f"· market {len(market_cards)}건 · lineup {len(lineup_cards)}건 → {OUT_DIR}")
    matched = sum(1 for s in built if (s.get("source") or {}).get("doc"))
    print(f"출처 해석: {matched}/{len(built)}건에 원천 문서 연결")

    # 주의 역할 분류 리포트 — caution 은 답변에 강제 표시되는 문장이므로 활성화 전에 전부
    # 눈으로 검토한다. 규칙이 틀린 항목은 config 의 *_ROLES 예외표에 넣고 다시 생성한다.
    counts: dict[str, dict[str, int]] = {}
    forced: list[str] = []
    for kind_name, cards, field in (("segment", segments, "note"),
                                    ("method", methods, "cautions"),
                                    ("procedure", procedures, "cautions"),
                                    ("screen", screens_, "note"),
                                    ("channel", channels, "note")):
        for r in cards:
            for e in r["fields"].get(field) or []:
                counts.setdefault(kind_name, {})[e["role"]] = \
                    counts.setdefault(kind_name, {}).get(e["role"], 0) + 1
                if e["role"] == "caution":
                    forced.append(f"{r['id']:<18} {e['text'][:70]}")
    print("주의 역할 분류: " + " · ".join(
        f"{k}({', '.join(f'{role} {n}' for role, n in sorted(v.items()))})"
        for k, v in counts.items()))
    print(f"caution(답변 강제 표시) {len(forced)}건 — 활성화 전 검토 대상:")
    for line in forced:
        print("   " + line)
    conds = sum(1 for s in segments if s["fields"]["conds"])
    print(f"CONDS 매핑: {conds}건 (나머지는 conds=[] — 자동 매칭 제외, 검색에는 남음)")

    linked = sum(1 for s in built if s.get("refs"))
    edges = sum(len(s.get("refs") or []) for s in built)
    print(f"항목 상호참조(refs): {linked}장 · {edges}건 연결 — 파생 텍스트 표기는 「지식항목 N」")
    if xref_report:
        print(f"⚠ 미해소 참조 {len(xref_report)}건 — 번호가 가리키는 항목이 없다(확인 필요):")
        for line in xref_report[:12]:
            print("   " + line)

    if resolver.unresolved:
        uniq = sorted(set(resolver.unresolved))
        print(f"\n⚠ 출처 미해석 {len(resolver.unresolved)}건 (고유 {len(uniq)}종) — source_text 로 원문 보존됨")
        for u in uniq[:12]:
            print(f"   · {u}")
        if len(uniq) > 12:
            print(f"   … 외 {len(uniq) - 12}종")
    if _report:
        print(f"\n⚠ 변환 알림 {len(_report)}건")
        for r in _report[:15]:
            print("   " + r)
        if len(_report) > 15:
            print(f"   … 외 {len(_report) - 15}건")

    print("\n검토 후 활성화: python build_kb.py --activate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
