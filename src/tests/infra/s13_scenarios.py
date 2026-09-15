"""시연 대본의 판 이력 — tests/debug/scenarios.py

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# 시연 대본의 판 이력 — tests/debug/scenarios.py
#
# 판은 첫 판에 변경을 접어서 만든다. 변경이 가리키는 라벨이 그 시점 판에 없으면 이력이
# 조용히 거짓말을 하므로 `_apply` 가 예외를 낸다 — 여기서 **모든 판을 한 번 만들어** 그
# 예외를 잡는다. 이 모듈은 langgraph 를 타지 않아 키·설치 없이 돈다.
# ─────────────────────────────────────────────────────────────

from tests.debug import scenarios as _SCEN  # noqa: E402 — 위 임포트 경계 검사 뒤에 온다

_built: dict[str, tuple] = {}
_build_error = ""
try:
    _built = {v.name: _SCEN.review_blocks(v.name) for v in _SCEN.REVIEW}
except ValueError as exc:
    _build_error = str(exc)
check(not _build_error, "시연 대본: 모든 판이 만들어진다 (변경이 가리키는 라벨이 실재한다)",
      _build_error)

check(_SCEN.LATEST == _SCEN.REVIEW[-1].name, "시연 대본: 지금 판은 이력의 맨 끝이다")

_dupes = []
for _name, _blocks in _built.items():
    _labels = [label for _, _, _, turns in _blocks for label, _ in turns]
    if len(_labels) != len(set(_labels)):
        _dupes.append(_name)
check(not _dupes, "시연 대본: 판마다 턴 라벨이 유일하다 (변경이 라벨로 대상을 찾는다)",
      " ".join(_dupes))

_bad_edits = []
for _v in _SCEN.REVIEW:
    for _e in _v.edits:
        if _e.op not in ("고침", "뺌", "더함"):
            _bad_edits.append(f"{_v.name} {_e.label}: 모르는 op {_e.op}")
        if _e.op in ("고침", "더함") and not _e.question:
            _bad_edits.append(f"{_v.name} {_e.label}: 바꾼 질문이 비었다")
        if _e.op == "더함" and not _e.after:
            _bad_edits.append(f"{_v.name} {_e.label}: 넣을 자리(after)가 없다")
        if not _e.why:
            _bad_edits.append(f"{_v.name} {_e.label}: 왜 바꿨는지가 없다")
check(not _bad_edits, "시연 대본: 변경마다 무엇을·왜 가 채워져 있다", " · ".join(_bad_edits))

# 판이 실제로 갈라져 있어야 «이전 것과 비교»가 뜻을 갖는다 — 변경을 적고 질문은 그대로면
# 이력만 늘어난다.
_same = [v.name for v in _SCEN.REVIEW[1:]
         if _built[v.name] == _built[_SCEN.REVIEW[_SCEN.REVIEW.index(v) - 1].name]]
check(not _same, "시연 대본: 변경을 적은 판은 직전 판과 실제로 다르다", " ".join(_same))

check(_SCEN.questions_of()["④"] == "고객이 '그 돈 그냥 예금으로 둬도 되지 않나요?' 하는데 뭐라고 하지?",
      "시연 대본: 이수민 ④ 는 «예금으로» 반론이다 (v6)")
