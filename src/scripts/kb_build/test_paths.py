"""경로 회귀 테스트 — 폴더 재번호 시 locator·경로가 조용히 썩는 것 방지(생성물만 읽는 테스트로는 못 잡음).

    python -m scripts.kb_build.test_paths
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from scripts.kb_build import common, config, docs, market

_fail: list[str] = []
_ok = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _ok
    if cond:
        _ok += 1
    else:
        _fail.append(f"{label}{' — ' + detail if detail else ''}")


# 1. 변환기 입력 폴더 실재
for name in ("EXTRACT_DIR", "INSIGHT_DIR", "GUIDE_DIR", "STARLEARN_DIR",
             "HOTTIP_DIR", "KBTHINK_DIR", "FEATURE_DIR", "MARKET_DIR"):
    p: Path = getattr(config, name)
    check(p.is_dir(), f"config.{name} 실재", str(p))

# 2. locator 대상 파일 실재
_LOC = re.compile(r"^([^§]+?)(?:\s*§|$)")
missing: dict[str, int] = {}
seen = 0
for f in sorted(config.OUT_DIR.glob("*.json")):
    if f.name.startswith("_"):
        continue
    for rec in json.loads(f.read_text(encoding="utf-8")).get("records", []):
        loc = ((rec.get("source") or {}).get("locator") or "").strip()
        if not loc or "/" not in loc:
            continue
        seen += 1
        rel = _LOC.match(loc).group(1).strip()
        if not (config.REPO / rel).exists():
            missing[rel] = missing.get(rel, 0) + 1
check(not missing, f"locator 실재 ({seen}건 검사)",
      "; ".join(f"{k} ×{v}" for k, v in sorted(missing.items())[:5]))

# 3. 레지스트리 제목 = 원문 제목(손으로 적은 제목의 낡음 방지)
_docs = {r["id"]: r["fields"]
         for r in json.loads((config.OUT_DIR / "kb_docs.json").read_text(encoding="utf-8"))["records"]}
_drift: list[str] = []
for stem, seed in config.GUIDE_DOCS.items():
    fields = _docs.get(f"doc.{stem}")
    if not fields:
        _drift.append(f"{stem} — 레지스트리에 없음")
        continue
    path = config.REPO / fields["path"]
    origin = docs.doc_title(path) if path.exists() else None
    override = seed.get("title_override")
    if override:
        # override 는 허용, 원문과 다르면 사유 선언 필수
        if not seed.get("title_override_reason") and origin and \
                docs._norm(origin) not in docs._norm(override):
            _drift.append(f"{stem} — override 가 원문과 다른데 사유 선언 없음")
    elif fields["title"] != origin:
        _drift.append(f"{stem} — 레지스트리 '{fields['title']}' vs 원문 '{origin}'")
check(not _drift, f"레지스트리 제목 = 원문 제목 ({len(config.GUIDE_DOCS)}건 대조)",
      "; ".join(_drift[:3]))


# 3-b. 시황·상품 문서마다 시드 존재 + 레지스트리 제목 = front-matter 제목(새 회차 시드 누락 검출)
_market_docs = {r["id"]: r["fields"] for r in
                json.loads((config.OUT_DIR / "kb_docs.json").read_text(encoding="utf-8"))["records"]
                if r["fields"].get("origin") == "시황상품"}
_m_drift: list[str] = []
_m_files = [p for p in sorted(config.MARKET_DIR.rglob("*.md"))
            if p.name != "README.md" and not p.name.startswith("_")]
for path in _m_files:
    if path.stem not in config.MARKET_DOCS:
        _m_drift.append(f"{path.name} — config.MARKET_DOCS 에 시드 없음")
        continue
    fm, _ = market._market_front_matter(path.read_text(encoding="utf-8"))
    fields = next((f for f in _market_docs.values() if f.get("origin_file") in
                   (fm.get("source_file"), path.name)), None)
    if fields is None:
        _m_drift.append(f"{path.name} — 레지스트리에 없음")
    elif fields["title"] != fm.get("title"):
        _m_drift.append(f"{path.name} — 레지스트리 '{fields['title']}' vs 원문 '{fm.get('title')}'")
check(not _m_drift, f"05 문서 시드·제목 = 원문 ({len(_m_files)}건 대조)", "; ".join(_m_drift[:3]))

# 3-c. 시황 카드 시효 경고가 README 원문에서 오는지
_warn = market._market_warn()
_readme = (config.MARKET_DIR / "README.md").read_text(encoding="utf-8")
check(bool(_warn) and market._market_emphasis(_warn) in market._market_emphasis(_readme),
      "05 시효 경고가 README 원문에서 온다", str(_warn)[:40])


# 3-d. 팩트 절의 원문 표가 카드에 실렸는지(관계 선언 여부와 무관) — 슬롯 파서의 표 누락 회귀
_facts_src = config.EXTRACT_DIR / "04_제도상품팩트.md"
_fact_cards = {r["fields"]["no"]: r["fields"]
               for r in json.loads((config.OUT_DIR / "kb_facts.json").read_text(encoding="utf-8"))
               ["records"]}
_lost: list[str] = []
_checked = 0
for _part in re.split(r"\n(?=### F\d)", _facts_src.read_text(encoding="utf-8")):
    _m = re.match(r"### (F\d+)[.．]", _part)
    if not _m:
        continue
    # 절 본문만(뒤의 `## 확인 필요 목록` 제외)
    _body = _part.split("\n## ")[0]
    _rows = [ln for ln in _body.splitlines() if market._TABLE_LINE.match(ln.strip())]
    if len(_rows) < 3:
        continue
    _card = _fact_cards.get(_m.group(1))
    if _card is None:
        continue
    _checked += 1
    # 표 본문 첫 행 셀이 카드에 존재
    _cells = [c.strip() for c in _rows[2].strip("|").split("|") if len(c.strip()) > 5]
    _blob = json.dumps(_card, ensure_ascii=False)
    if _cells and not any(c[:12] in _blob for c in _cells):
        _lost.append(f"{_m.group(1)} ({len(_rows) - 2}행)")
check(not _lost, f"팩트 절의 원문 표가 카드에 실린다 ({_checked}개 절 대조)", ", ".join(_lost))


# 5. 항목 상호참조 — 「항목 41·48」의 단말 항목번호 오독 방지(「지식항목」 표기 · refs 실재)
_cards: list[dict] = []
for _f in sorted((config.KB_DATA if hasattr(config, "KB_DATA") else common.OUT_DIR).glob("kb_*.json")):
    _cards += json.loads(_f.read_text(encoding="utf-8")).get("records", [])
_ids = {c["id"] for c in _cards}

_bare, _arrow, _broken = [], [], []
for _c in _cards:
    _fields = _c.get("fields") or {}
    _derived = json.dumps({k: _fields.get(k) for k in common._XREF_FIELDS}, ensure_ascii=False)
    if common._XREF_WORD.search(_derived):
        _bare.append(_c["id"])
    if common._XREF_ARROW.search(_derived):
        _arrow.append(_c["id"])
    _broken += [f"{_c['id']}→{r}" for r in _c.get("refs", []) if r not in _ids]
check(not _bare, "파생 텍스트에 맨 「항목 N」이 없다 (전부 「지식항목 N」)",
      ", ".join(_bare[:5]))
check(not _arrow, "화살표 참조는 「→ 관련 지식항목」으로 못박혀 있다", ", ".join(_arrow[:5]))
check(not _broken, "refs 가 실재하는 카드를 가리킨다", ", ".join(_broken[:5]))

# 원문 불변 — 원문·인용 필드에는 「항목 N」 유지
_src_has = common._XREF_WORD.search(
    (config.EXTRACT_DIR / "05_업무처리절차.md").read_text(encoding="utf-8"))
check(_src_has is not None, "06 원문의 「항목 N」 표기는 그대로다 (원문 불변)")
# 손대는 필드 목록에 인용 필드 혼입 금지
check(not ({"quotes", "source_text"} & set(common._XREF_FIELDS)),
      "상호참조 표기는 파생 텍스트만 고친다 (인용 필드는 손대지 않는다)",
      str(common._XREF_FIELDS))

# 대표 대조 — 표A [01-12-213] → proc.041·proc.048
_isa_screen = next((c for c in _cards if c["id"] == "screen.01-12-213"), None)
check(_isa_screen is not None
      and sorted(_isa_screen.get("refs", [])) == ["proc.041", "proc.048"]
      and "관련 지식항목 41·48" in (_isa_screen["fields"].get("summary") or ""),
      "표A [01-12-213] 의 「항목 41·48」이 proc.041·proc.048 로 이어진다",
      json.dumps((_isa_screen or {}).get("refs"), ensure_ascii=False))


# 4. 최상위 폴더 번호 하드코딩 금지(저장소 규칙 4)
_HARD = re.compile(r"""(?:REPO|parents\[3\])\s*/\s*["']0\d_""")
for py in sorted(Path(__file__).parent.glob("*.py")):
    if py.name == Path(__file__).name:
        continue
    hits = _HARD.findall(py.read_text(encoding="utf-8"))
    check(not hits, f"{py.name} 폴더번호 하드코딩 없음", f"{len(hits)}곳")

print(f"\n총 {_ok + len(_fail)}건 · 통과 {_ok} · 실패 {len(_fail)}")
for m in _fail:
    print(f"  ✗ {m}")
print("✅ 경로 회귀 테스트 통과" if not _fail else "❌ 경로 회귀 테스트 실패")
sys.exit(1 if _fail else 0)
