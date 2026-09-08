"""경로 회귀 테스트 — 인용이 조용히 썩는 것을 막는다.

이 테스트가 생긴 이유: 최상위 폴더가 재번호(`05_시황_상품_기반지식` 신설로 05~07 → 06~08)
되자 변환기가 FileNotFoundError 로 죽고 생성 JSON 의 `source.locator` 543 건이 존재하지 않는
경로를 가리키게 됐는데, **기존 테스트는 654/654 전부 통과했다**. 생성물만 읽고 원문을 안 봤기
때문이다. 초록불과 실제 상태가 어긋나는 그 구간을 이 파일이 메운다.

    python -m scripts.kb_build.test_paths
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from scripts.kb_build import build_kb, config

_fail: list[str] = []
_ok = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _ok
    if cond:
        _ok += 1
    else:
        _fail.append(f"{label}{' — ' + detail if detail else ''}")


# ── 1. 변환기가 읽는 폴더가 실제로 있는가
for name in ("EXTRACT_DIR", "INSIGHT_DIR", "GUIDE_DIR", "STARLEARN_DIR",
             "HOTTIP_DIR", "KBTHINK_DIR", "FEATURE_DIR", "MARKET_DIR"):
    p: Path = getattr(config, name)
    check(p.is_dir(), f"config.{name} 실재", str(p))

# ── 2. locator 가 가리키는 파일이 실제로 있는가 (543건이 썩었던 그 자리)
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

# ── 3. 레지스트리 제목이 원문 제목인가
#
# 같은 성격의 사고를 다른 축에서 막는다. 경로가 썩는 것처럼 **제목도 조용히 썩는다** —
# 손으로 적어둔 제목은 원문이 개정돼도 그대로 남고, 그 이름으로는 원문을 찾을 수 없다.
# 실제로 Series1 의 「IRP야, KB를 떠나지 마오!」가 그렇게 빠진 채 답변 출처로 나갔다.
_docs = {r["id"]: r["fields"]
         for r in json.loads((config.OUT_DIR / "kb_docs.json").read_text(encoding="utf-8"))["records"]}
_drift: list[str] = []
for stem, seed in config.GUIDE_DOCS.items():
    fields = _docs.get(f"doc.{stem}")
    if not fields:
        _drift.append(f"{stem} — 레지스트리에 없음")
        continue
    path = config.REPO / fields["path"]
    origin = build_kb.doc_title(path) if path.exists() else None
    override = seed.get("title_override")
    if override:
        # 원문만으로 문서를 특정 못 해 대체한 것은 허용하되, 왜 다른지는 코드에 남아야 한다.
        if not seed.get("title_override_reason") and origin and \
                build_kb._norm(origin) not in build_kb._norm(override):
            _drift.append(f"{stem} — override 가 원문과 다른데 사유 선언 없음")
    elif fields["title"] != origin:
        _drift.append(f"{stem} — 레지스트리 '{fields['title']}' vs 원문 '{origin}'")
check(not _drift, f"레지스트리 제목 = 원문 제목 ({len(config.GUIDE_DOCS)}건 대조)",
      "; ".join(_drift[:3]))


# ── 3-b. 05 문서마다 시드가 있고, 레지스트리 제목이 front-matter 제목인가
#
# 05 는 정기자료가 회차별로 쌓이는 폴더다(README 수록 규칙 — 주간·월간). 다음 회차 파일이
# 들어오면 시드가 없어 부서·발행시점이 비고, 그 사실은 리포트 한 줄로만 지나간다.
# 여기서 잡으면 파일을 넣은 사람이 그 자리에서 안다.
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
    fm, _ = build_kb._market_front_matter(path.read_text(encoding="utf-8"))
    fields = next((f for f in _market_docs.values() if f.get("origin_file") in
                   (fm.get("source_file"), path.name)), None)
    if fields is None:
        _m_drift.append(f"{path.name} — 레지스트리에 없음")
    elif fields["title"] != fm.get("title"):
        _m_drift.append(f"{path.name} — 레지스트리 '{fields['title']}' vs 원문 '{fm.get('title')}'")
check(not _m_drift, f"05 문서 시드·제목 = 원문 ({len(_m_files)}건 대조)", "; ".join(_m_drift[:3]))

# ── 3-c. 05 카드의 시효 경고가 README 원문에서 온 것인가
#
# 시황 수치는 주·월 단위로 낡는다. 경고 문구를 코드가 들고 있으면 README 가 바뀔 때 두 곳이
# 갈리고, 갈리면 답변이 틀린 안내를 한다(consult §12 지워진 gap 16·18 과 같은 사고).
_warn = build_kb._market_warn()
_readme = (config.MARKET_DIR / "README.md").read_text(encoding="utf-8")
check(bool(_warn) and build_kb._market_emphasis(_warn) in build_kb._market_emphasis(_readme),
      "05 시효 경고가 README 원문에서 온다", str(_warn)[:40])


# ── 3-d. 원문 절의 표가 카드에 실렸는가
#
# 이 테스트가 이 파일에 있는 이유는 §1 머리말과 같다 — **생성물만 읽으면 초록불인데 원문과
# 어긋나는** 자리다. 팩트 변환기는 `**팩트**:` 한 줄과 `- **키**: 값` 슬롯만 줍고 `| … |`
# 줄은 어느 쪽에도 안 걸려 조용히 버렸다. 그래서 F40 은 label 이 「인출순서 4단계 × 세제」를
# 약속하는데 본문은 "인출순서와 원천별 세제:" 에서 끊긴 카드가 됐고, 직원이 그 표를 물으면
# «자료가 없다»가 나갔다 — 원문에는 있는데도. 654건 전부 통과하던 그 구간이다.
#
# 표를 **싣는 것**과 관계로 **선언하는 것**은 다른 일이라, 여기서는 싣는 쪽만 본다. 선언은
# 이름 열과 값 열이 갈리는 표만 할 수 있고(`_market_tables`), 갈리지 않는 표도 재료로는
# 실려야 한다.
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
    # 절 본문만 본다 — 뒤따르는 `## 확인 필요 목록` 은 그 절의 것이 아니다.
    _body = _part.split("\n## ")[0]
    _rows = [ln for ln in _body.splitlines() if build_kb._TABLE_LINE.match(ln.strip())]
    if len(_rows) < 3:
        continue
    _card = _fact_cards.get(_m.group(1))
    if _card is None:
        continue
    _checked += 1
    # 표 본문 첫 행의 셀이 카드 어딘가에 남아 있어야 한다.
    _cells = [c.strip() for c in _rows[2].strip("|").split("|") if len(c.strip()) > 5]
    _blob = json.dumps(_card, ensure_ascii=False)
    if _cells and not any(c[:12] in _blob for c in _cells):
        _lost.append(f"{_m.group(1)} ({len(_rows) - 2}행)")
check(not _lost, f"팩트 절의 원문 표가 카드에 실린다 ({_checked}개 절 대조)", ", ".join(_lost))


# ── 5. 항목 상호참조 — 「항목 41·48」이 단말 값으로 읽히지 않는가
#
# 표A 의 「거래구분 ① 과세이연/계약이전입금 ② ISA 만기자금 입금 → 항목 41·48」에서 41·48 은
# 지식베이스의 항목 번호인데, 답변이 그것을 단말에서 고르는 항목번호로 옮겨 적었다
# ("'ISA 만기자금 입금'(항목 48)을 선택하면"). 앞에 실제 순번 ①② 가 서 있어서 화살표 뒤의
# 번호가 그 연장으로 읽힌 것이고, 검증기는 "48" 이 근거에 있으므로 통과시킨다.
_cards: list[dict] = []
for _f in sorted((config.KB_DATA if hasattr(config, "KB_DATA") else build_kb.OUT_DIR).glob("kb_*.json")):
    _cards += json.loads(_f.read_text(encoding="utf-8")).get("records", [])
_ids = {c["id"] for c in _cards}

_bare, _arrow, _broken = [], [], []
for _c in _cards:
    _fields = _c.get("fields") or {}
    _derived = json.dumps({k: _fields.get(k) for k in build_kb._XREF_FIELDS}, ensure_ascii=False)
    if build_kb._XREF_WORD.search(_derived):
        _bare.append(_c["id"])
    if build_kb._XREF_ARROW.search(_derived):
        _arrow.append(_c["id"])
    _broken += [f"{_c['id']}→{r}" for r in _c.get("refs", []) if r not in _ids]
check(not _bare, "파생 텍스트에 맨 「항목 N」이 없다 (전부 「지식항목 N」)",
      ", ".join(_bare[:5]))
check(not _arrow, "화살표 참조는 「→ 관련 지식항목」으로 못박혀 있다", ", ".join(_arrow[:5]))
check(not _broken, "refs 가 실재하는 카드를 가리킨다", ", ".join(_broken[:5]))

# **원문은 고치지 않는다**(루트 절대 규칙 1). 06 원문과 카드의 인용 필드에는 「항목 N」이
# 그대로 남아 있어야 한다 — 파생 텍스트만 바꾼 것이 맞는지 여기서 가른다.
_src_has = build_kb._XREF_WORD.search(
    (config.EXTRACT_DIR / "05_업무처리절차.md").read_text(encoding="utf-8"))
check(_src_has is not None, "06 원문의 「항목 N」 표기는 그대로다 (원문 불변)")
# 변환기가 손대는 필드에 인용 필드가 섞이면 그때부터 원문이 조용히 고쳐진다. 지금 06 의
# 「항목 N」은 표 셀·도출문에만 있어 인용에 걸린 것이 없지만, 원문이 바뀌면 걸릴 수 있다 —
# 막을 자리는 «걸렸는지»가 아니라 **손대는 필드 목록**이다.
check(not ({"quotes", "source_text"} & set(build_kb._XREF_FIELDS)),
      "상호참조 표기는 파생 텍스트만 고친다 (인용 필드는 손대지 않는다)",
      str(build_kb._XREF_FIELDS))

# 대표 대조 — 표A 의 그 행이 실제로 두 절차 항목으로 이어졌는가.
_isa_screen = next((c for c in _cards if c["id"] == "screen.01-12-213"), None)
check(_isa_screen is not None
      and sorted(_isa_screen.get("refs", [])) == ["proc.041", "proc.048"]
      and "관련 지식항목 41·48" in (_isa_screen["fields"].get("summary") or ""),
      "표A [01-12-213] 의 「항목 41·48」이 proc.041·proc.048 로 이어진다",
      json.dumps((_isa_screen or {}).get("refs"), ensure_ascii=False))


# ── 4. 최상위 폴더 번호를 코드에 하드코딩하지 않았는가
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
