"""공용 인프라 회귀 테스트 — session_store · tools · 패키지 임포트 경계.

외부 의존성 없음(표준 라이브러리만). LLM 호출 없음.

실행: python -m tests.test_infra   (src/ 에서)
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pension_agent import session_store, tools

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))


#: 이 파일이 만드는 세션 고객 id. 정리는 **이것만** 지운다.
_TEST_CUSTOMERS = ("TEST01", "TEST02", "NO_SUCH_CUSTOMER")


def _clean_session_data() -> None:
    """이 테스트가 만든 세션 파일만 지운다.

    예전에는 디렉터리를 통째로 rmtree 했다. session_data 가 실행 중에만 생기는 임시
    데이터일 때는 맞았지만, 지금은 시연 픽스처(과거 상담 기록 — scripts/seed_sessions.py)가
    거기 함께 산다 — 테스트 한 번 돌리면 그 픽스처가 통째로 날아갔고, 다음 시연에서
    "지난 상담 없음"이 되는데 아무도 그 인과를 짚지 못한다.
    """
    for customer_id in _TEST_CUSTOMERS:
        (session_store.SESSION_DATA_DIR / f"{customer_id}.json").unlink(missing_ok=True)


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
# 수치 토큰화 — 뒤따르는 쉼표를 숫자에 붙이지 않는다
#
# `_NUM` 이 `\d[\d,]*` 이던 시절, "만기 D-17, 4,050만원"에서 `17,` 을 통째로 집었다.
# 원장에는 "D-17 ·" 라 `17` 로 들어가 있어서 **맞는 답변이 '원장 밖 수치'로 거부**됐고,
# compose 는 근거 원문을 통째로 덤프했다 — 직원 화면에 답변 대신 재료 표가 떴다.
# 천단위 쉼표(4,050)는 숫자의 일부이므로 함께 고정한다.
# ─────────────────────────────────────────────────────────────

from pension_agent.verify import numbers as _numbers, verify_texts as _verify_texts

# numbers() 는 **값 보존 정규형**을 돌려준다(verify._canon) — 천단위 쉼표 제거·소수
# 끝자리 0 제거. 같은 값이 표기 차이로 다른 토큰이 되면 맞는 답변이 «원장 밖 수치»로
# 버려진다(아래 15.0%/15% 케이스가 그 사고의 회귀 고정이다).
_TOKENS = [
    ("만기 D-17, 4,050만원", {"17", "4050"}),
    ("D-17 · 4,050만원", {"17", "4050"}),
    ("연 3.65%", {"3.65%"}),
    ("5,500만원 초과 13.2%", {"5500", "13.2%"}),
    ("1,234.5원", {"1234.5"}),
    ("예금 120만원(15.0%)", {"120", "15%"}),
    ("금리 3.00%", {"3%"}),
]
for _text, _want in _TOKENS:
    check(_numbers(_text) == _want, f"수치 토큰화: {_text}", str(sorted(_numbers(_text))))

# 쉼표 하나로 판정이 뒤집히지 않는다 — 같은 값을 말하는 두 문장은 같은 결과여야 한다.
_ledger = ["· 만기도래 2026-09-10 (D-17) · 4,050만원"]
check(_verify_texts("만기 D-17, 4,050만원이에요.", _ledger)[0]
      and _verify_texts("만기 D-17 · 4,050만원이에요.", _ledger)[0],
      "구분자(쉼표/가운뎃점)가 원장 대조 결과를 바꾸지 않는다")
check(not _verify_texts("만기 금액은 7,777만원이에요.", _ledger)[0],
      "원장 밖 수치는 여전히 거부된다")

# 소수점 표기 차이로 판정이 뒤집히지 않는다 — 원장 "15.0%" 를 답변이 "15%" 라 말한 것은
# 같은 값이다. 이게 다르게 취급되던 동안, "예금 비중 몇 프로야?" 의 **맞는 답**이 버려지고
# compose 가 브리핑 재료 표를 통째로 덤프했다(실사고).
_pct_ledger = ["· 자산군별 예금 120만원(15.0%) · 고유계정대 600만원(75.0%)"]
check(_verify_texts("예금 비중은 15%예요.", _pct_ledger)[0],
      "소수 끝자리 0(15.0%↔15%)이 원장 대조 결과를 바꾸지 않는다")
check(not _verify_texts("예금 비중은 15.5%예요.", _pct_ledger)[0],
      "값이 실제로 다른 수치(15.5%)는 여전히 거부된다")
check(not _verify_texts("예금 비중은 15예요.", _pct_ledger)[0],
      "%-유무는 보존된다 — 원장의 15% 로 답변의 맨 15 를 허용하지 않는다")


# ─────────────────────────────────────────────────────────────

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


failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    mark = "✓" if ok else "✗"
    print(f"{mark} {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ 공용 인프라 회귀 테스트 통과")
