"""시연 픽스처는 테스트가 지우지 않는다

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from tests.infra._common import _TEST_CUSTOMERS, check, session_store

import pension_agent


# ─────────────────────────────────────────────────────────────
# 시연 픽스처는 테스트가 지우지 않는다
#
# session_data 에는 과거 상담 기록(scripts/seed_sessions.py 가 심는 목업)이 함께 산다.
# 테스트 정리가 «이번 실행이 만든 것»을 넘어서면 그 픽스처가 사라지고, 다음 시연에서
# 상담 이력이 통째로 비는데 원인을 짚기 어렵다.
# ─────────────────────────────────────────────────────────────

_seeded = ([fp for fp in session_store.SESSION_DATA_DIR.glob("*.json")
            if fp.stem not in _TEST_CUSTOMERS]
           if session_store.SESSION_DATA_DIR.exists() else [])
check(bool(_seeded), "시연 픽스처(과거 상담 세션)가 테스트 후에도 남아 있다 "
                     "— 없으면 python -m scripts.seed_sessions", str(len(_seeded)))


# 저작 검증 — 작성자가 누구인지로 자료가 무엇인지를 추론한 문장을 잡는다
#
# 회귀 대상: "작성자가 인재개발부 소속이라 교육 목적으로 정리된 자료로 보이나 본부 공식
# 가이드는 아니다"가 절차 카드의 주의로 실려 답변에 나갔다. 확인할 수단도, 코드가 대조할
# 관계 선언도 없는 판단이 검증된 결론처럼 읽혔다. 자료의 지위는 출처 종류가 정한다
# (knowledge/CLAUDE.md 「자료의 지위는 출처가 정한다」).
# ─────────────────────────────────────────────────────────────

from pension_agent.knowledge import schema  # noqa: E402

_INFERRED = "작성자가 **인재개발부 소속**이라 교육 목적으로 정리된 자료로 보이나 본부 공식 가이드는 아니다."
_FACTUAL = "이 게시글 단독 출처이고 교차확인할 다른 자료가 없다 — 핫팁 게시글이므로 본부 확정 지침이 아니다."

check(bool(schema._IDENTITY_INFERENCE.search(_INFERRED)),
      "신원으로 자료 성격을 추론한 문장을 검증기가 잡는다")
check(not schema._IDENTITY_INFERENCE.search(_FACTUAL),
      "출처 종류로 말한 같은 결론은 잡지 않는다")

# 원문 인용은 훑지 않는다 — 원문은 고치지 않으므로 경고해도 조치할 수 없다(루트 절대 규칙 1).
_flat = dict(schema._derived_strings({"cautions": [_INFERRED],
                                      "quotes": [{"text": _INFERRED, "source_text": _INFERRED}]}))
check("cautions" in _flat and "quotes" not in _flat and "source_text" not in _flat,
      "파생 텍스트만 훑고 원문 인용은 건너뛴다", str(sorted(_flat)))

# 지금 적재된 카드에는 이 부류가 없다 — 있으면 저작에서 걸러야 한다.
_errs, _warns = schema.validate([Path(pension_agent.__file__).parent])
check(not [w for w in _warns if w.startswith("[신원추론]")],
      "적재된 카드에 신원 추론 문장이 없다",
      str([w for w in _warns if w.startswith("[신원추론]")][:2]))
