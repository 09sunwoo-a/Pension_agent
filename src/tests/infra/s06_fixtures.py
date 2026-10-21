"""시연 픽스처 보존 · 신원 추론 문장 저작 검증.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from pathlib import Path

from tests.infra._common import _TEST_CUSTOMERS, check, session_store

import pension_agent


# session_data 의 과거 상담 기록(scripts/seed_sessions.py)을 테스트 정리가 지우면 안 됨

_seeded = ([fp for fp in session_store.SESSION_DATA_DIR.glob("*.json")
            if fp.stem not in _TEST_CUSTOMERS]
           if session_store.SESSION_DATA_DIR.exists() else [])
check(bool(_seeded), "시연 픽스처(과거 상담 세션)가 테스트 후에도 남아 있다 "
                     "— 없으면 python -m scripts.seed_sessions", str(len(_seeded)))


# 저작 검증 — 작성자 신원으로 자료 성격을 추론한 문장 차단(자료 지위는 출처 종류가 결정)

from pension_agent.knowledge import schema  # noqa: E402

_INFERRED = "작성자가 **인재개발부 소속**이라 교육 목적으로 정리된 자료로 보이나 본부 공식 가이드는 아니다."
_FACTUAL = "이 게시글 단독 출처이고 교차확인할 다른 자료가 없다 — 핫팁 게시글이므로 본부 확정 지침이 아니다."

check(bool(schema._IDENTITY_INFERENCE.search(_INFERRED)),
      "신원으로 자료 성격을 추론한 문장을 검증기가 잡는다")
check(not schema._IDENTITY_INFERENCE.search(_FACTUAL),
      "출처 종류로 말한 같은 결론은 잡지 않는다")

# 원문 인용(quotes·source_text)은 수정 불가라 검사 제외
_flat = dict(schema._derived_strings({"cautions": [_INFERRED],
                                      "quotes": [{"text": _INFERRED, "source_text": _INFERRED}]}))
check("cautions" in _flat and "quotes" not in _flat and "source_text" not in _flat,
      "파생 텍스트만 훑고 원문 인용은 건너뛴다", str(sorted(_flat)))

# 적재된 카드에 이 부류 없어야 함
_errs, _warns = schema.validate([Path(pension_agent.__file__).parent])
check(not [w for w in _warns if w.startswith("[신원추론]")],
      "적재된 카드에 신원 추론 문장이 없다",
      str([w for w in _warns if w.startswith("[신원추론]")][:2]))
