"""문서가 가리키는 .py 경로가 실재한다 — 파일을 옮길 때 문서 참조가 조용히 죽지 않게

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.

루트 CLAUDE.md 규칙 4 의 사고는 데이터 `locator` 에서 났고 `scripts/kb_build/test_paths.py`
가 그것을 막는다. 같은 일이 **문서**에서도 난다 — 모듈을 옮기면 임포트는 테스트가 잡지만,
`README.md`·`CLAUDE.md` 가 적어둔 `consult_agent/relations.py` 같은 경로는 아무도 잡지 않아서
«문서가 기준»(루트 CLAUDE.md 「무엇이 어디 있나」)인 파일이 없는 자리를 가리키게 된다.
2026-09-15 에 `consult_agent/relations.py` 를 `tools/` 아래로 옮기면서 이 검사를 두었다.

━━ 무엇을 보나 ━━
저장소의 마크다운 중 **코드를 설명하는 문서**만 본다 — 루트 `CLAUDE.md`, `docs/`, `src/`
아래 전부. 지식베이스 원문 폴더(`01_`~`09_`)는 보지 않는다(절대 규칙 1 — 원문은 고치지
않으므로 거기서 잡아도 고칠 수 없다). `skills/` 는 이 저장소 코드가 아닌 외부 스킬이다.

본문에서 `a/b.py` 꼴(디렉터리 한 단 이상 + `.py`)을 전부 꺼내 **저장소 어딘가의 .py 파일
경로가 그 꼬리와 일치하는지** 본다. 문서는 `nodes/plan.py` 처럼 앞을 생략해 적으므로 완전
경로를 요구하지 않는다 — 꼬리 일치면 실재로 친다. 이 완화 때문에 «다른 폴더의 동명 파일»은
못 잡지만, 지금 잡으려는 것은 «옮겨서 없어진 경로»다.

━━ 보지 않는 것 ━━
· 변경이력 표의 행(`| 2026-08-24 | …`) — 그때 있던 파일을 적은 기록이라 지금 없는 것이
  맞다. 역사를 고쳐 쓰지 않는다.
· 한 단짜리 `foo.py` — 파일명만으로는 어느 폴더인지 정할 수 없어 검사 대상이 아니다.
"""

from __future__ import annotations

import re
from pathlib import Path

from pension_agent import config
from tests.infra._common import check

_ROOT = config.REPO_ROOT
_DOCS = sorted({
    _ROOT / "CLAUDE.md",
    *(_ROOT / "docs").glob("*.md"),
    *config.SRC_ROOT.rglob("*.md"),
})
_DOCS = [p for p in _DOCS if p.exists()]

_ALL_PY = {p.relative_to(_ROOT).as_posix() for p in _ROOT.rglob("*.py") if ".git" not in p.parts}

# 디렉터리 한 단 이상 + 파일명.py — 앞뒤가 경로 문자가 아니어야 한다(`../a/b.py` 의 `../` 는
# 아래에서 뗀다). 마크다운 코드 스팬·산문·표 어디에 있든 같은 규칙이다.
_REF = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.py)\b")
_HISTORY_ROW = re.compile(r"^\s*\|\s*20\d\d-\d\d-\d\d\s*\|")


def _exists(ref: str) -> bool:
    ref = ref.lstrip("./")
    return ref in _ALL_PY or any(p.endswith("/" + ref) for p in _ALL_PY)


_dead: list[str] = []
_seen = 0
for _doc in _DOCS:
    for _line in _doc.read_text(encoding="utf-8").splitlines():
        if _HISTORY_ROW.match(_line):
            continue
        for _m in _REF.finditer(_line):
            _seen += 1
            if not _exists(_m.group(1)):
                _dead.append(f"{_doc.relative_to(_ROOT)}: {_m.group(1)}")

check(_seen > 50, "문서에서 .py 경로 참조를 읽어냈다", str(_seen))
check(not _dead, "문서가 가리키는 .py 경로가 전부 실재한다", "; ".join(sorted(set(_dead))[:10]))
