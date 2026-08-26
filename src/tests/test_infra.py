"""공용 인프라 회귀 테스트 — session_store · tools · 패키지 임포트 경계.

외부 의존성 없음(표준 라이브러리만). LLM 호출 없음.

실행: python -m tests.test_infra   (src/ 에서)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import session_store, tools

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


def _clean_session_data() -> None:
    if session_store.SESSION_DATA_DIR.exists():
        shutil.rmtree(session_store.SESSION_DATA_DIR)


# ─────────────────────────────────────────────────────────────
# session_store — 왕복 테스트
# ─────────────────────────────────────────────────────────────

_clean_session_data()
session_store.append_turn("TEST01", "sess-a", {"role": "user", "text": "질문1"}, employee_id="E01")
session_store.append_turn("TEST01", "sess-a", {"role": "agent", "text": "답변1"})
session_store.append_turn("TEST01", "sess-b", {"role": "user", "text": "질문2"})

sessions = session_store.list_sessions("TEST01")
check(len(sessions) == 2, "session_store: 세션 2개(sess-a·sess-b) 기록됨", str(len(sessions)))
sess_a = next(s for s in sessions if s["session_id"] == "sess-a")
check(len(sess_a["turns"]) == 2, "session_store: sess-a 에 턴 2개", str(len(sess_a["turns"])))
check(sess_a["employee_id"] == "E01", "session_store: employee_id 보존")
check(all("ts" in t for t in sess_a["turns"]), "session_store: 각 턴에 ts 자동 부여")

summary = session_store.summarize_for_briefing("TEST01")
check(len(summary) == 2, "session_store: summarize_for_briefing 최신순 2건", str(summary))

empty_summary = session_store.summarize_for_briefing("NO_SUCH_CUSTOMER")
check(empty_summary == [], "session_store: 없는 고객은 빈 목록(에러 아님)")

_clean_session_data()


# ─────────────────────────────────────────────────────────────
# tools — 발송 화면 연계 게이트 (발송하지 않는다, 세션이력에만 기록)
# ─────────────────────────────────────────────────────────────

result = tools.open_lms_screen("TEST02", "테스트 문구입니다")
check(result["status"] == "ok", "tools.open_lms_screen: 더미가 아니면 통과", str(result))
check(result["message"] == "테스트 문구입니다", "tools.open_lms_screen: 요청 문구 그대로 반환")

logged = session_store.list_sessions("TEST02")
check(len(logged) == 1 and logged[0]["turns"][0]["role"] == "tool",
      "tools.open_lms_screen: 호출 사실이 세션이력에 tool 턴으로 기록됨")
check("open_lms_screen" in tools.TOOL_REGISTRY,
      "tools.TOOL_REGISTRY: open_lms_screen 등록됨")
check("send_lms" not in tools.TOOL_REGISTRY,
      "tools.TOOL_REGISTRY: 발송 수행 도구는 남아 있지 않음 (§10 — 화면만 연다)")

_clean_session_data()


# ─────────────────────────────────────────────────────────────
# 패키지 임포트 경계 — 두 에이전트를 한 프로세스에서 함께 써도 이름이 겹치지 않는다
#
# 회귀 대상: 예전에는 두 에이전트가 평평한 스크립트 디렉터리라 `prompts`·`llm` 같은 동명
# 모듈이 sys.modules 를 놓고 경합했고(먼저 임포트한 쪽이 자리를 차지), 이를 피하려 전용
# 로더(common/agent_loader.py)가 sys.path 와 짧은 이름을 저장·복원했다. 패키지화로 그
# 경합 자체가 없어졌다 — 이 테스트가 고정하는 것은 "짧은 이름이 sys.modules 에 등장하지
# 않는다"는 사실이다. 다시 등장하면 sys.path 조작이 되살아났다는 뜻이다.
# ─────────────────────────────────────────────────────────────

from pension_agent.consult_agent import prompts as consult_prompts  # noqa: E402
from pension_agent.strategy_agent import prompts as strategy_prompts  # noqa: E402

check(consult_prompts is not strategy_prompts,
      "동명 모듈(prompts)이 에이전트별로 각각 적재된다")
check(consult_prompts.__name__ == "pension_agent.consult_agent.prompts",
      "완전정규화 이름으로 등록된다", consult_prompts.__name__)
check(not {"prompts", "llm", "customer", "engine", "kb"} & set(sys.modules),
      "짧은 이름이 sys.modules 를 오염시키지 않는다",
      str(sorted({"prompts", "llm", "customer", "engine", "kb"} & set(sys.modules))))

import pension_agent  # noqa: E402

check(not any("sys.path" in (f.read_text(encoding="utf-8"))
              for f in Path(pension_agent.__file__).parent.rglob("*.py")),
      "패키지 안에 sys.path 조작이 남아 있지 않다")


# ─────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────

failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    mark = "✓" if ok else "✗"
    print(f"{mark} {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ 공용 인프라 회귀 테스트 통과")
