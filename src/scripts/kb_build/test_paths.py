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
