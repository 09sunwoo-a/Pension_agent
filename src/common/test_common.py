"""common/ 공용 인프라 회귀 테스트 — session_store · tools · agent_loader.

외부 의존성 없음(표준 라이브러리만). LLM 호출 없음.

실행: python test_common.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common.agent_loader as agent_loader  # noqa: E402
import common.session_store as session_store  # noqa: E402
import common.tools as tools  # noqa: E402

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
# tools — send_lms 스텁 (실제 발송 없음, 세션이력에만 기록)
# ─────────────────────────────────────────────────────────────

result = tools.send_lms("TEST02", "테스트 문구입니다", phone="010-0000-0000")
check(result["status"] == "stubbed", "tools.send_lms: 스텁 상태 반환(실제 발송 없음)", str(result))
check(result["message"] == "테스트 문구입니다", "tools.send_lms: 요청 문구 그대로 반환")

logged = session_store.list_sessions("TEST02")
check(len(logged) == 1 and logged[0]["turns"][0]["role"] == "tool",
      "tools.send_lms: 호출 사실이 세션이력에 tool 턴으로 기록됨")
check("send_lms" in tools.TOOL_REGISTRY, "tools.TOOL_REGISTRY: send_lms 등록됨")

_clean_session_data()


# ─────────────────────────────────────────────────────────────
# agent_loader — 두 에이전트 동시 로드 시 짧은 이름(prompts/llm) 격리
#
# 회귀 대상: 초판 구현은 "이미 적재됨(재사용)" 분기에서 짧은 이름 복원 기록을 건너뛰어,
# 두 번째 호출부터 짧은 이름이 원상복구되지 않고 그 에이전트 쪽에 계속 남는 버그가 있었다
# (예: consult_agent 모듈이 나중에 `import prompts` 하면 strategy_agent.prompts 를 잘못
# 집는다). 이 테스트는 그 버그를 고정한다 — load_strategy_agent() 를 반복 호출해도 매번
# 끝난 뒤 "prompts"/"llm" 짧은 이름이 남지 않아야 한다.
# ─────────────────────────────────────────────────────────────

_short_names_before = {n: sys.modules.get(n) for n in ("prompts", "llm", "customer", "engine")}

sa1 = agent_loader.load_strategy_agent()
check("prompts" not in sys.modules or sys.modules.get("prompts") is _short_names_before.get("prompts"),
      "agent_loader: 1차 호출 후 짧은 이름 'prompts' 누수 없음")

sa2 = agent_loader.load_strategy_agent()  # 재사용 분기 — 버그가 있었다면 여기서 누수
check("prompts" not in sys.modules or sys.modules.get("prompts") is _short_names_before.get("prompts"),
      "agent_loader: 2차(재사용) 호출 후에도 짧은 이름 'prompts' 누수 없음")
check(sa1["engine"] is sa2["engine"], "agent_loader: 재사용 시 동일 모듈 객체 반환(멱등)")

check(sys.modules["strategy_agent.prompts"].__name__ == "strategy_agent.prompts",
      "agent_loader: 완전정규화 이름으로 sys.modules 에 영구 등록됨")


# ─────────────────────────────────────────────────────────────

failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    mark = "✓" if ok else "✗"
    print(f"{mark} {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ common 인프라 회귀 테스트 통과")
