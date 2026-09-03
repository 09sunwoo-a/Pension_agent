"""공용 인프라 회귀 테스트 — session_store · tools · 패키지 임포트 경계.

외부 의존성 없음(표준 라이브러리만). LLM 호출 없음.

실행: python -m tests.test_infra   (src/ 에서)
"""

from __future__ import annotations

import asyncio
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
# 쪽지는 예외다 — 고객이 아니라 직원 본인에게 가는 행내 메모라 대외 행위가 아니고,
# register_consult_note 와 같은 «내부 기록» 부류다. 승낙 뒤에만 불린다(act.confirm_action).
check("send_memo" in tools.TOOL_REGISTRY,
      "tools.TOOL_REGISTRY: send_memo 등록됨 (직원 본인 쪽지 — 대외 발송이 아니다)")

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

from pension_agent.verify import _judge, numbers as _numbers, verify_texts as _verify_texts

# numbers() 는 **값 보존 정규형**을 돌려준다(verify._canon) — 천단위 쉼표 제거·소수
# 끝자리 0 제거. 같은 값이 표기 차이로 다른 토큰이 되면 맞는 답변이 «원장 밖 수치»로
# 버려진다(아래 15.0%/15% 케이스가 그 사고의 회귀 고정이다).
# 단위를 붙여 쓴 금액과 풀어 쓴 연월은 **접은 값도 함께** 낸다. 원장이 "1,485,000원" 이라
# 적고 LLM 이 "148만 5천원" 이라 쓴 답변이 통째로 버려지던 자리다(같은 부류의 세 번째
# 사고). 원표기 토큰은 그대로 남는다 — 넓히기만 하고 좁히지 않는다.
_TOKENS = [
    ("만기 D-17, 4,050만원", {"17", "4050", "40500000"}),
    ("D-17 · 4,050만원", {"17", "4050", "40500000"}),
    ("연 3.65%", {"3.65%"}),
    ("5,500만원 초과 13.2%", {"5500", "55000000", "13.2%"}),
    ("1,234.5원", {"1234.5"}),
    ("예금 120만원(15.0%)", {"120", "1200000", "15%"}),
    ("금리 3.00%", {"3%"}),
    ("148만 5천원", {"148", "5", "1485000"}),          # 만·천으로 끊어 쓴 금액
    ("1억 2천만원", {"1", "2", "120000000"}),           # 붙여 쓴 단위는 곱한다
    # 날짜는 통짜 정규형과 흩어진 토큰을 **둘 다** 낸다 — 대조는 통짜로 하고(오답 날짜 차단)
    # 허용 집합은 흩어진 것도 유지한다(넓히기만 하고 좁히지 않는다).
    ("2026년 6월", {"2026", "6", "날짜:2026-06"}),
    ("2026-09-10", {"2026", "09", "10", "날짜:2026-09", "날짜:2026-09-10", "날짜:____-09-10"}),
    ("2026.09.10", {"2026", "09", "10", "날짜:2026-09", "날짜:2026-09-10", "날짜:____-09-10"}),
    ("9월 10일", {"9", "10", "날짜:____-09-10"}),
    # 대표번호·화면번호는 날짜가 아니다. 날짜로 읽으면 답변의 그 번호가 통째로 거부된다.
    ("1588-1234", {"1588", "1234"}),
    ("04-12-640", {"04", "12", "640"}),
    # 사이에 글자가 끼면 다른 수치다 — 900만 과 148 을 합치지 않는다.
    ("900만원 넣으면 148", {"900", "9000000", "148"}),
    # 단위가 작아지지 않으면 새 수치다 — 5,500만 과 16.5% 를 합치지 않는다.
    ("5,500만원 이하 16.5%", {"5500", "55000000", "16.5%"}),
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
# 날짜는 통짜로 대조한다 — 흩어 놓으면 오답 날짜가 집합 검사를 통과한다
#
# 이 검증기는 수치의 «집합 포함»을 본다. 그래서 날짜를 연·월·일 토큰으로 흩어 놓으면
# 원장 어딘가에 2026 과 11 과 10 이 있다는 이유만으로 "만기는 2026년 11월 10일"(오답)이
# 통과했다 — 진짜 만기가 2026-09-10 인데도. 날짜야말로 틀리면 고객이 헛걸음하는 수치다.
#
# 반대쪽 위험이 더 크다는 것도 함께 고정한다. **표기가 판정을 뒤집으면 맞는 답변이 버려진다**
# (이 파일이 이미 세 번 겪은 사고). 원장이 "2026-09-10" 이라 적고 답변이 "2026년 9월 10일"
# 이라 쓰는 것은 정상이므로, 표기를 가로질러 같은 날짜로 읽혀야 한다.
# ─────────────────────────────────────────────────────────────

_L = ["· 만기도래 2026-09-10 (D-14) 예금 4,050만원",
      "· 오늘: 2026년 8월 27일 (목) / 2026-08-27",
      "· 연말(2026년 12월 31일)까지: 126일 남음",
      "· 기준시점 2026.06 · 가입일 2025.03.31",
      # 행내 문서는 날짜를 점으로 닫아 쓴다 — 그 끝점이 매치를 통째로 막던 자리.
      "· 준법감시인 심의필 제2026-2508호(2026.06.10., 유효기간 2026.06.10.~2027.06.09.)",
      "· 대표번호 1588-1234 · 화면번호 04-12-640",
      "· 확정금리 연 3.65% · 평가금액 2026.5만원"]

# ① 오답 날짜는 막힌다 — 연·월·일 토큰이 전부 원장 안에 있어도.
for _wrong in ("만기는 2026년 11월 10일이에요.",     # 월만 틀림 (11 도 10 도 원장에 있다)
               "만기는 2026년 9월 11일이에요.",      # 일만 틀림
               "오늘은 2026년 3월 27일이에요.",      # 원장의 다른 날짜에서 조합
               "만기는 2026-09-11 이에요.",
               "만기는 2026.09.11 이에요.",
               "2026년 6월 11일까지 유효해요.",       # 끝점 표기에서 하루 어긋난 것도 막힌다
               "11월 30일까지 넣으셔야 해요."):      # 연도 없는 꼴도 막힌다
    _ok, _why = _verify_texts(_wrong, _L)
    check(not _ok, f"오답 날짜 차단: {_wrong}", str(_why))
    check(any(w.startswith("날짜 ") for w in _why),
          "거부 사유에 날짜를 통째로 남긴다(어느 숫자가 틀렸는지가 아니다)", str(_why))

# ② 표기를 바꿔 쓴 맞는 답변은 통과한다 — 좁히다가 이쪽을 잃으면 고친 게 아니다.
for _right in ("만기는 2026년 9월 10일이에요.",       # 원장은 ISO, 답변은 한글
               "만기는 2026-09-10 이에요.",
               "만기는 2026.09.10 이에요.",
               "만기는 2026년 9월이에요.",            # 원장보다 굵게 말하는 것은 참이다
               "9월 10일이 만기예요.",                # 연도를 빼고 말해도 참이다
               "2026년 6월 기준이에요.",              # 원장은 점 표기(2026.06)
               "2025년 3월 31일에 가입하셨어요.",      # 원장은 2025.03.31
               "2026년 6월 10일까지 유효해요.",        # 원장은 끝점 붙은 2026.06.10.
               "유효기간은 2027년 6월 9일까지예요.",
               "12월 31일까지 넣으셔야 해요.",
               "연말까지 126일 남았어요."):
    _ok, _why = _verify_texts(_right, _L)
    check(_ok, f"표기가 판정을 뒤집지 않는다: {_right}", str(_why))

# ③ 날짜가 아닌 것을 날짜로 읽지 않는다. 은행 문서에는 대표번호·화면번호가 흔하고,
#    그것들이 날짜로 읽히면 맞는 답변이 통째로 거부된다.
for _notdate in ("대표번호는 1588-1234 예요.", "04-12-640 화면에서 조회하세요.",
                 "연 3.65% 예요.", "평가금액은 2026.5만원이에요.",   # 뒤에 단위가 붙으면 금액이다
                 "제2026-2508호 심의필이에요."):                        # 달 25 는 없는 달이다
    _ok, _why = _verify_texts(_notdate, _L)
    check(_ok, f"날짜가 아닌 것은 날짜로 읽지 않는다: {_notdate}", str(_why))

# ④ 재료가 자기 자신을 근거로 통과한다 — 못 하면 그 재료를 인용한 답변이 전부 버려진다.
for _t in _L:
    _ok, _why = _verify_texts(_t, [_t])
    check(_ok, f"재료 자기대조: {_t[:24]}…", str(_why))


# ─────────────────────────────────────────────────────────────
# 연도 없이 말한 날짜 — 사람이 읽는 대로 «오늘 언저리»로 읽는가
#
# "12월 31일" 은 올해다. 올해 그 날이 지났으면 다음 occurrence(내년)로, 지난 일을 되짚는
# 말이면 작년으로 읽힌다 — 어느 쪽이든 오늘 언저리다. 반면 3년 전 납입이력의 월일을 빌려와
# "12월 31일까지 납입하세요" 라고 말하는 것은 사람이 하는 해석이 아니다. 그게 통짜 대조를
# 넣은 뒤에도 오답이 남아 있던 마지막 경로였다.
#
# 좁히는 쪽이라 넉넉히(±1년) 잡는다. 연도를 안 쓰는 것은 정상 어법이고(지난 상담
# "11월 13일에" · 만기 "2월 14일에"), 여기서 잘못 좁히면 맞는 답변이 통째로 버려진다.
# ─────────────────────────────────────────────────────────────

# 오늘을 명시한다 — 이 절이 재는 것은 «오늘로부터 몇 해 떨어졌나»라, 고정해 둔 하루에
# 기대면 해가 바뀔 때 조용히 다른 것을 재게 된다.
_THIS_YEAR = 2026

for _label, _ledger, _bare, _want in (
    ("올해 만기",        "만기도래 2026-09-10",              "9월 10일이 만기예요.",       True),
    ("내년 만기",        "만기도래 2027-02-14",              "2월 14일이 만기예요.",       True),
    ("작년 상담",        "지난 상담 2025-11-13",             "11월 13일에 상담하셨어요.",  True),
    ("원장이 연도 없이", "다음 해 7월 1일부터 발급됩니다",     "7월 1일부터 발급돼요.",      True),
    ("3년 전 납입이력",  "2023년 납입 900만원 (2023-12-31)", "12월 31일까지 납입하세요.",  False),
    ("오래된 카드 시효", "준법감시인 심의필 2013.02.28.",     "2월 28일까지예요.",          False),
):
    _nums = _numbers(_ledger, this_year=_THIS_YEAR)
    _ok, _why = _judge(_bare, _nums, set(), set())
    check(_ok == _want, f"연도 없는 날짜 — {_label}: {_bare}", f"기대 {_want} / 실제 {_ok} {_why}")

# 연도를 밝히면 오래된 날짜도 그대로 인용할 수 있다 — 막는 것은 «연도를 뺀 채»일 때뿐이다.
_old = _numbers("2023년 납입 900만원 (2023-12-31)", this_year=_THIS_YEAR)
check(_judge("2023년 12월 31일 납입이에요.", _old, set(), set())[0],
      "연도를 밝히면 오래된 날짜도 인용된다")


# ─────────────────────────────────────────────────────────────
# 오늘·기준일 — 시간축 두 개가 섞이지 않는가
#
# 여기만 **날짜를 명시해** 검증한다. 나머지 테스트는 tests/__init__.py 가 오늘을 원장
# 기준일로 고정한 채 돌기 때문에, 고정해 둔 그 하루로만 보면 «세는 법»의 오류를 못 잡는다
# (연말 잔여일수를 하루 더 세거나 덜 세는 것이 정확히 그런 오류다 — 화면에서는 129 도
# 126 도 똑같이 그럴듯해 보인다).
# ─────────────────────────────────────────────────────────────

import os
from datetime import date

import tests
from pension_agent.strategy_agent import customer as CUST

check(tests.PINNED_TODAY == CUST.AS_OF.isoformat(),
      "테스트가 고정한 오늘 = 원장 기준일(AS_OF)",
      f"{tests.PINNED_TODAY} vs {CUST.AS_OF}")

# 두 축이 이름부터 갈려 있어야 한다. 옛 이름이 살아 있으면 «원장 기준일»과 «오늘»을
# 같은 것으로 아는 호출부가 조용히 남는다.
check(not hasattr(CUST, "TODAY"),
      "옛 단일 축(customer.TODAY)이 남아 있지 않다")

# 세는 법 — 오늘은 세지 않는다. 12/31 당일이 0 이어야 «오늘 포함 +1» 이 성립한다.
_counts = [(date(2026, 8, 27), 126), (date(2026, 12, 31), 0), (date(2026, 12, 30), 1),
           (date(2026, 1, 1), 364), (date(2024, 8, 27), 126)]  # 2024 는 윤년
for _base, _want in _counts:
    _got = CUST.days_to_year_end(_base)
    check(_got == _want, f"연말 잔여일수 {_base} → {_want}일 (오늘 제외)", f"{_got}")

# 오늘이 움직이면 잔여일수는 음수가 될 수 있다. 그때 화면·요건이 무너지지 않는가 —
# 오늘이 고정돼 있던 동안에는 시연 데이터의 만기가 전부 미래라 한 번도 안 나던 경우다.
from copy import deepcopy

from pension_agent.strategy_agent.engine.text import dday

check(dday(14) == "D-14" and dday(0) == "D-0", "잔여일수 표기: 오늘까지는 D-n", dday(0))
check(dday(-87) == "만기 경과 87일", "지난 만기는 «D--87» 이 아니라 «만기 경과»로 읽는다", dday(-87))
check(dday(None) == "", "만기가 없으면 표기도 없다")

_p = deepcopy(CUST.PERSONAS[0]) if CUST.PERSONAS else None
if _p is not None:
    _p.matDD = -5
    check("mat" not in CUST.conditions(_p),
          "지난 만기는 «만기 1개월 전 안내» 요건을 세우지 않는다", str(CUST.conditions(_p)))
    _p.matDD = 14
    check("mat" in CUST.conditions(_p), "다가오는 만기는 그대로 요건이 된다")

# 지난 만기 뒤의 «진짜 다음 만기»가 가려지지 않는가 — 목록 맨 앞을 그냥 집으면 30일 안에
# 만기가 오는 고객이 요건에서 통째로 빠진다.
_mats = [{"date": "2026-01-10", "dd": -30, "type": "예금", "name": "A", "amount": 100},
         {"date": "2026-03-01", "dd": 20, "type": "GIC", "name": "B", "amount": 200}]
check(CUST.next_maturity(_mats)["date"] == "2026-03-01",
      "다음 만기는 지난 만기를 건너뛰고 잡힌다", str(CUST.next_maturity(_mats)))
# 전부 지났으면 가장 최근에 지난 것 — 제일 오래 전 것을 집으면 화면이 엉뚱한 만기를 말한다.
_past = [dict(m, dd=-40) for m in _mats]
check(CUST.next_maturity(_past)["date"] == "2026-03-01",
      "전부 지났으면 가장 최근에 지난 만기를 집는다", str(CUST.next_maturity(_past)))
check(CUST.next_maturity([]) is None, "만기가 없으면 None")

# 고정 스위치. 형식이 틀리면 조용히 실제 날짜로 넘어가지 않는다.
_saved = os.environ.get(CUST.TODAY_ENV)
try:
    os.environ[CUST.TODAY_ENV] = "2026-11-30"
    check(CUST.today() == date(2026, 11, 30), "PENSION_TODAY 가 오늘을 고정한다", str(CUST.today()))
    check(CUST.ledger_age_days() == (date(2026, 11, 30) - CUST.AS_OF).days,
          "원장 나이 = 오늘 − 기준일")
    os.environ[CUST.TODAY_ENV] = "2026-13-99"
    try:
        CUST.today()
        _raised = False
    except ValueError:
        _raised = True
    check(_raised, "PENSION_TODAY 형식 오류는 즉시 실패한다(조용히 실제 날짜로 넘어가지 않는다)")
    os.environ[CUST.TODAY_ENV] = ""
    check(CUST.today() == date.today(), "고정하지 않으면 실제 오늘이다", str(CUST.today()))
finally:
    if _saved is None:
        os.environ.pop(CUST.TODAY_ENV, None)
    else:
        os.environ[CUST.TODAY_ENV] = _saved


# ─────────────────────────────────────────────────────────────
# llm — 429(속도 제한) 재시도
#
# 행내 게이트웨이가 몰린 호출(브리핑 11연쇄·앱 기동 선생성)에 429 를 냈고, 재시도가
# 없어서 턴이 통째로 죽었다. 429 두 번 뒤 성공하는 서버를 흉내 내 재시도가 실제로
# 도는지, Retry-After 를 기다리는지, 다른 HTTP 에러는 재시도하지 않는지 본다.
# ─────────────────────────────────────────────────────────────

import io
import json
import urllib.error

from pension_agent import llm as _llm

# llm 은 stdlib 의 time.sleep·urllib.request.urlopen 을 모듈 속성으로 부르므로 그 자리를
# 갈아끼우고 finally 에서 원복한다(다른 검사가 진짜 sleep·urlopen 을 쓸 수 있게).
_saved_llm = (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.time.sleep,
              _llm.urllib.request.urlopen)
_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY = "genai", "http://fake", "k"
_sleeps: list[float] = []
_llm.time.sleep = _sleeps.append


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    import email.message
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("http://fake", code, "err", msg, io.BytesIO(b""))


class _FakeResp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self):
        return json.dumps({"choices": [{"message": {"content": "답"}}]}).encode()


try:
    calls = {"n": 0}

    def _urlopen_429_twice(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(429, {"Retry-After": "1"})
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_429_twice
    out = _llm.generate("q")
    check(out == "답" and calls["n"] == 3, "llm: 429 두 번 뒤 재시도로 성공한다",
          f"calls={calls['n']} out={out!r}")
    check(_sleeps == [1.0, 1.0], "llm: Retry-After 초만큼 기다린다", str(_sleeps))

    calls["n"], _sleeps[:] = 0, []
    _llm.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(
        _http_error(429))
    try:
        _llm.generate("q")
        _raised = None
    except _llm.LLMError as exc:
        _raised = str(exc)
    check(_raised is not None and "429" in _raised,
          "llm: 계속 429 면 상한에서 멈추고 LLMError 로 올린다", str(_raised))

    calls["n"] = 0

    def _urlopen_500(req, timeout=None):
        calls["n"] += 1
        raise _http_error(500)

    _llm.urllib.request.urlopen = _urlopen_500
    try:
        _llm.generate("q")
    except _llm.LLMError:
        pass
    check(calls["n"] == 1, "llm: 429 아닌 HTTP 에러는 재시도하지 않는다", f"calls={calls['n']}")
finally:
    (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.time.sleep,
     _llm.urllib.request.urlopen) = _saved_llm


# ─────────────────────────────────────────────────────────────
# observability — Langfuse 관측
#
# 고정하는 것은 셋이다.
#   ① 키가 없으면 통째로 꺼진다 — 테스트·시연이 키 없이 그대로 돈다.
#   ② 켜지면 LLM 호출 한 건이 이벤트 한 건으로 나가고, 트레이스 안에서 부른 호출은
#      같은 traceId 로 묶인다(브리핑 11연쇄·대화 4~7회를 되짚는 근거가 이 묶음이다).
#   ③ 전송이 깨져도 LLM 호출은 성공한다 — 관측은 부산물이지 기능이 아니다.
# ─────────────────────────────────────────────────────────────

from pension_agent import observability as _obs  # noqa: E402

_obs.reset()
check(not _obs.enabled(), "observability: 키가 없으면 꺼져 있다")
with _obs.trace("noop") as _null:
    _null.update(output="버려진다")
check(_obs.current_trace_id() is None, "observability: 꺼져 있으면 트레이스 id 도 없다")

_saved_env = {k: os.environ.get(k) for k in
              ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
               "LANGFUSE_CAPTURE_CONTENT")}
_saved_llm2 = (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.urllib.request.urlopen)
_sent: list[dict] = []


def _fake_urlopen(req, timeout=None):
    """LLM 게이트웨이와 Langfuse 수집 API 를 URL 로 갈라 받는다.

    둘 다 같은 `urllib.request.urlopen` 을 쓰므로(같은 모듈 객체) 한 자리에서 갈라야 한다.
    """
    if "langfuse" in req.full_url:
        _sent.append(json.loads(req.data.decode("utf-8")))
        return _FakeResp()
    return _FakeResp()


try:
    os.environ.update({"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
                       "LANGFUSE_HOST": "https://langfuse.invalid"})
    os.environ.pop("LANGFUSE_CAPTURE_CONTENT", None)
    _obs.reset()
    _llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY = "genai", "http://fake", "k"
    _llm.urllib.request.urlopen = _fake_urlopen

    check(_obs.enabled(), "observability: 키가 있으면 켜진다")

    with _obs.trace("test.turn", input="질문", session_id="sess-x") as _tr:
        _inside = _obs.current_trace_id()
        _llm.generate("q", name="test.call")
        _tr.update(output="답")
    check(_obs.flush(timeout=5.0), "observability: flush 가 큐를 비운다")

    _events = [e for batch in _sent for e in batch["batch"]]
    _gens = [e for e in _events if e["type"] == "generation-create"]
    _traces = [e for e in _events if e["type"] == "trace-create"]
    check(len(_gens) == 1, "observability: LLM 호출 한 건이 generation 한 건으로 나간다",
          str(len(_gens)))
    check(bool(_gens) and _gens[0]["body"]["traceId"] == _inside,
          "observability: 트레이스 안의 호출은 그 트레이스에 묶인다")
    check(bool(_gens) and _gens[0]["body"]["name"] == "test.call",
          "observability: 호출부가 준 이름이 그대로 실린다")
    check(bool(_gens) and _gens[0]["body"]["input"] == "q"
          and _gens[0]["body"]["output"] == "답",
          "observability: 프롬프트와 응답이 실린다")
    check(bool(_traces) and all(t["body"]["id"] == _inside for t in _traces)
          and any(t["body"].get("output") == "답" for t in _traces),
          "observability: 트레이스가 열릴 때와 닫힐 때 같은 id 로 나간다")
    check(all(t["body"].get("sessionId") == "sess-x" for t in _traces),
          "observability: 상담 세션 id 가 여는·닫는 이벤트 양쪽에 실린다",
          str([t["body"].get("sessionId") for t in _traces]))

    # span 중첩 — 트레이스가 평면이 아니라 실행 구조를 닮은 트리가 된다
    _sent.clear()
    with _obs.trace("test.turn2") as _tr2:
        with _obs.span("tool:fact", input="세액공제") as _sp:
            _llm.generate("q", name="test.in_span")
            _sp.update(output="카드 1건", found=True)
        _obs.score("compose_passed", True)
        _obs.score("retries", 2)
        _obs.score("outcome", "answer")
    _obs.flush(timeout=5.0)
    _ev = [e for batch in _sent for e in batch["batch"]]
    _span = next((e["body"] for e in _ev if e["type"] == "span-create"), None)
    _gen2 = next((e["body"] for e in _ev if e["type"] == "generation-create"), None)
    _scores = [e["body"] for e in _ev if e["type"] == "score-create"]
    check(bool(_span) and _span["name"] == "tool:fact" and _span["output"] == "카드 1건",
          "observability: span 이 이름과 결과를 싣는다", str(_span))
    check(bool(_span) and _span["metadata"].get("found") is True,
          "observability: span 에 얹은 값은 메타데이터로 실린다")
    check(bool(_gen2) and _gen2.get("parentObservationId") == (_span or {}).get("id"),
          "observability: span 안의 LLM 호출은 그 span 밑에 붙는다")
    check(bool(_span) and _span["traceId"] == _tr2.id,
          "observability: span 이 열려 있는 트레이스에 묶인다")
    check({(s["name"], s["value"], s["dataType"]) for s in _scores} ==
          {("compose_passed", 1, "BOOLEAN"), ("retries", 2, "NUMERIC"),
           ("outcome", "answer", "CATEGORICAL")},
          "observability: 점수가 bool·숫자·문자열별로 형을 갈라 나간다", str(_scores))
    check(all(s["traceId"] == _tr2.id for s in _scores),
          "observability: 점수가 그 트레이스에 붙는다")

    # 트레이스가 없으면 점수는 나가지 않는다 — 붙을 데가 없는 점수는 찾을 방법이 없다
    _sent.clear()
    _obs.score("orphan", 1)
    _obs.flush(timeout=5.0)
    check(not [e for batch in _sent for e in batch["batch"]],
          "observability: 트레이스 밖 점수는 보내지 않는다")

    # 본문 차단 — 개인정보를 외부로 내보내지 않는 스위치
    _sent.clear()
    os.environ["LANGFUSE_CAPTURE_CONTENT"] = "0"
    _obs.reset()
    _secret = "고객 홍길동의 잔액"
    _llm.generate(_secret, name="test.masked")
    _obs.flush(timeout=5.0)
    _masked = [e for batch in _sent for e in batch["batch"] if e["type"] == "generation-create"]
    check(bool(_masked)
          and _masked[0]["body"]["input"] == {"omitted": True, "chars": len(_secret)},
          "observability: CAPTURE_CONTENT=0 이면 본문 대신 길이만 나간다",
          str(_masked[0]["body"]["input"]) if _masked else "이벤트 없음")

    # 전송이 깨져도 LLM 호출은 산다
    os.environ.pop("LANGFUSE_CAPTURE_CONTENT", None)
    _obs.reset()
    _before = _obs.stats()["failed"]

    def _urlopen_langfuse_down(req, timeout=None):
        if "langfuse" in req.full_url:
            raise _http_error(500)
        return _FakeResp()

    _llm.urllib.request.urlopen = _urlopen_langfuse_down
    check(_llm.generate("q", name="test.down") == "답",
          "observability: 관측 전송이 깨져도 LLM 호출은 성공한다")
    _obs.flush(timeout=5.0)
    check(_obs.stats()["failed"] > _before,
          "observability: 전송 실패는 예외 대신 stats 에 쌓인다")
    # 삼키되 침묵하지는 않는다 — 원인이 남아야 «전송이 깨졌다»와 «안 켜졌다»가 갈린다.
    check("HTTP Error 500" in (_obs.last_error() or ""),
          "observability: 실패 원인이 last_error 에 남는다", str(_obs.last_error()))
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    _obs.reset()
    (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.urllib.request.urlopen) = _saved_llm2


# ─────────────────────────────────────────────────────────────
# Python 3.10 호환 — 3.11+ 전용 이름을 임포트하지 않는가
#
# 로컬(발표자 노트북)은 3.10, 개발 환경은 3.11+ 라 여기 테스트가 전부 통과해도 로컬에서
# 임포트가 죽을 수 있다 — session_store 의 `from datetime import UTC`(3.11 별칭)가 실제로
# 그랬다. CI 를 3.10 으로 못 돌리는 동안은 3.11+ 에서 생긴 이름의 임포트를 AST 로 잡는다.
# 문법(match 등)은 3.10 에 이미 있으므로 여기서는 **이름**만 본다.
# ─────────────────────────────────────────────────────────────

import ast

#: {모듈: 3.11+ 에서 생긴 이름}. 모듈 자체가 3.11+ 인 것은 이름 "*" 로 적는다.
_PY311_ONLY = {
    "datetime": {"UTC"},
    "enum": {"StrEnum", "ReprEnum", "verify", "member", "nonmember", "property"},
    "typing": {"Self", "LiteralString", "Never", "assert_type", "assert_never",
               "reveal_type", "dataclass_transform", "override"},  # override 는 3.12
    "asyncio": {"TaskGroup", "timeout", "Runner"},
    "contextlib": {"chdir"},
    "itertools": {"batched"},  # 3.12
    "tomllib": {"*"},
    "wsgiref.types": {"*"},
}

_offenders: list[str] = []
for _py in sorted(Path(".").rglob("*.py")):
    if ".venv" in _py.parts or "site-packages" in _py.parts:
        continue
    tree = ast.parse(_py.read_text(encoding="utf-8"), filename=str(_py))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _PY311_ONLY:
            banned = _PY311_ONLY[node.module]
            hits = [a.name for a in node.names if "*" in banned or a.name in banned]
            _offenders += [f"{_py}:{node.lineno} from {node.module} import {n}" for n in hits]
        elif isinstance(node, ast.Import):
            _offenders += [f"{_py}:{node.lineno} import {a.name}"
                           for a in node.names
                           if "*" in _PY311_ONLY.get(a.name.split(".")[0], ())]
check(not _offenders, "3.11+ 전용 이름을 임포트하지 않는다 (로컬 3.10 호환)",
      "; ".join(_offenders))

# ─────────────────────────────────────────────────────────────
# WorkB 쪽지 — 오늘의 타겟 고객 본문
#
# 고정하는 것은 «본문이 무엇을 말하는가»다. 문장을 LLM 이 쓰지 않으므로 같은 입력이면 같은
# 글이 나와야 하고, 자를 때도 값이 반쪽으로 남지 않아야 한다(보내고 나면 못 되돌린다).
# ─────────────────────────────────────────────────────────────

from pension_agent import workb  # noqa: E402
from pension_agent.strategy_agent import customer as _customer  # noqa: E402
from pension_agent.strategy_agent.target_list import today_targets  # noqa: E402

_targets = today_targets()
check(bool(_targets), "target_list: 오늘의 타겟 고객이 산출된다", str(len(_targets)))
check(all(t.conds for t in _targets),
      "target_list: 요건이 하나도 없는 고객은 목록에 오르지 않는다")
check([t.rank for t in _targets] == sorted(t.rank for t in _targets),
      "target_list: PRIO → 요건수 → id 순으로 정렬된다(결정론)")
check(all(c in _customer.CONDS for t in _targets for c in t.conds),
      "target_list: 요건 코드가 customer.CONDS 안에서만 나온다 — 새 판정을 만들지 않는다")

# 형식 둘(텍스트·HTML)은 **같은 사실을 말해야 한다** — 표로 바꾸면서 정보가 빠지면
# 직원이 보는 목록이 형식에 따라 달라진다.
# 잘라내기 상한은 형식마다 다르게 잡는다 — HTML 은 표 뼈대(머리·머리행·꼬리)만 700자를
# 넘어서, 텍스트 기준 상한을 그대로 쓰면 «한 명은 담는다» 하한에 걸려 상한을 넘게 된다.
for _fmt, _cap in (("text", 700), ("html", 1800)):
    _n = workb.daily_targets_note(fmt=_fmt)
    check(_n.body == workb.daily_targets_note(fmt=_fmt).body,
          f"workb[{_fmt}]: 같은 입력이면 같은 본문 (LLM 을 타지 않는다)")
    check(_n.count == len(_targets) and not _n.truncated,
          f"workb[{_fmt}]: 기본 상한에서는 전원이 실린다", f"{_n.shown}/{_n.count}")
    check(_customer.AS_OF.isoformat() in _n.body,
          f"workb[{_fmt}]: 원장 기준일이 본문에 남는다 — 평가금액이 오늘 값으로 읽히지 않게")
    check(all(t.profile.nm in _n.body for t in _targets),
          f"workb[{_fmt}]: 목록에 오른 고객이 본문에서 빠지지 않는다")
    check(all(t.profile.id not in _n.body for t in _targets),
          f"workb[{_fmt}]: 고객 id 원문이 본문에 실리지 않는다 (MASK_ID 기본값)")
    _cb, _cs = workb.RENDERERS[_fmt](_targets, max_chars=_cap)
    check(0 < _cs < len(_targets) and len(_cb) <= _cap,
          f"workb[{_fmt}]: 상한을 넘으면 고객 수가 줄고 본문이 상한 안에 든다",
          f"{_cs}/{len(_targets)} · {len(_cb)}자 (상한 {_cap})")
    check(f"외 {len(_targets) - _cs}명" in _cb,
          f"workb[{_fmt}]: 몇 명이 빠졌는지 본문이 밝힌다")
    check(workb.RENDERERS[_fmt]([], max_chars=_cap)[1] == 0,
          f"workb[{_fmt}]: 타겟이 0명이어도 렌더가 죽지 않는다")

# HTML 은 뷰어·위생처리기가 걷어내는 것을 처음부터 쓰지 않는다(이메일 HTML 규율).
_html = workb.daily_targets_note(fmt="html").body
check("<table" in _html and _html.count("<tr") == len(_targets) + 1,
      "workb[html]: 고객 한 명이 표의 한 줄이다(머리행 포함)", str(_html.count("<tr")))
check("<style" not in _html and "class=" not in _html,
      "workb[html]: <style> 블록·클래스를 쓰지 않는다 — 위생처리기가 걷어낸다")
check("http://" not in _html and "https://" not in _html,
      "workb[html]: 바깥 자원을 부르지 않는다 — 막히면 표가 무너진다")
# 행 단위로 덜어내도 표가 깨지지 않아야 한다.
# WorkB 쪽지 뷰어는 **인라인 style 을 걷어낸다**(2026-09-03 실물 확인). 그래서 여백·크기를
# style 로 만들려는 시도는 무효였고, 블록 요소도 뷰어가 자기 간격을 얹는다 — 남는 것은
# <br>·<b>·표의 옛 속성뿐이다. 여기가 다시 늘면 화면에서 조용히 어긋난다.
check(not any(t in _html for t in ("<p ", "<p>", "<div", "<h1", "<h2", "<ul", "<li")),
      "workb[html]: 블록 요소를 쓰지 않는다 — 뷰어가 자기 간격을 얹는다")

# 표를 만드는 곳은 하나다. 둘이 되면 한쪽만 마스킹하거나 한쪽만 잘라내는 상태가 곧 생긴다.
_bare, _bare_shown = workb.targets_table(_targets)
check(_bare.startswith("<table ") and _bare.endswith("</table>") and _bare in _html
      and _bare_shown == len(_targets),
      "workb.targets_table: 표만 따로 내고, 목록 쪽지는 그 표를 그대로 쓴다")

_cut_html = workb.render_html(_targets, max_chars=1200)[0]
check(_cut_html.count("<table") == _cut_html.count("</table") == 1,
      "workb[html]: 잘라내도 표가 열고 닫힌다")
# 속성이 중복되면 뒤엣것이 통째로 무시된다(실제로 style 이 두 번 붙어 font-size 가 죽었다).
import re as _re
check(not [t for t in _re.findall(r"<[^>]+>", _html) if t.count("style=") > 1],
      "workb[html]: 한 태그에 같은 속성을 두 번 쓰지 않는다")

_note = workb.daily_targets_note()

# 요건 이름은 CONDS 원문 그대로 실린다 — 쪽지가 요건 이름을 새로 지어내면 화면과 갈린다.
_lead = _targets[0]
check(_customer.CONDS[_lead.conds[0]] in _note.body,
      "workb: 요건 이름이 CONDS 원문 그대로 실린다", _customer.CONDS[_lead.conds[0]])

# 고객 id 는 기본으로 가린다 (KB-PIN 앞자리가 생년월일이고, 쪽지는 받은편지함에 남는다).
check(all(t.profile.id.partition("-")[0] in _note.body for t in _targets),
      "workb: 마스킹해도 앞자리는 남아 화면과 대조할 수 있다")

# 자를 때는 고객 블록 단위 — 줄 중간에서 끊으면 반쪽 수치가 남고, 그건 틀린 값을 보낸 것이다.

# 한 명도 못 담는 상한이어도 한 명은 담는다 — 빈 쪽지가 «장애»처럼 읽히는 것보다 낫다.
check(workb.EMPTY_BODY in workb.render([])[0],
      "workb: 타겟이 0명이면 빈 쪽지가 아니라 «0명»이라고 적는다")

_min_body, _min_shown = workb.render(_targets, max_chars=1)
check(_min_shown == 1, "workb: 상한이 아무리 작아도 고객 한 명은 담는다", str(_min_shown))


# ── 발송 ───────────────────────────────────────────────────
# 수신자는 리스트다. 문자열 하나를 넘기면 WorkB 가 64;ETC_ERR(기타 오류)로 거부하는데,
# 파이썬은 문자열도 시퀀스라 타입 오류 없이 거기까지 가고 서버 사유도 «기타»라 어디가
# 틀렸는지 아무 데서도 안 나온다(실제로 그렇게 한 번 잡았다).
try:
    workb.validate_recipients("3902172")
    check(False, "workb: 수신자에 문자열 하나를 넘기면 나가기 전에 막는다")
except TypeError as _exc:
    check("리스트" in str(_exc), "workb: 수신자에 문자열 하나를 넘기면 나가기 전에 막는다")
for _bad in ([], ["", "3902172"], None):
    try:
        workb.validate_recipients(_bad)
        check(False, f"workb: 빈 수신자를 막는다 ({_bad!r})")
    except (TypeError, ValueError):
        check(True, f"workb: 빈 수신자를 막는다 ({_bad!r})")

# 어댑터의 성공은 서버의 성공이 아니다 — WorkB 는 실패를 isError 가 아니라 본문에 담는다.
# 아래 두 형태는 실제로 관측된 응답이다.
_REFUSED = [{"type": "text", "text": '{\n  "success": false,\n  "error": "64;ETC_ERR"\n}'}]
check(workb.parse_result(_REFUSED)["status"] == "failed",
      "workb.parse_result: 본문의 success:false 를 «발송 완료»로 보고하지 않는다",
      str(workb.parse_result(_REFUSED)))
check(workb.parse_result(_REFUSED).get("error") == "64;ETC_ERR",
      "workb.parse_result: 서버 오류코드를 그대로 남긴다")
check(workb.parse_result([{"type": "text", "text": '{"success": true}'}])["status"] == "sent",
      "workb.parse_result: success:true 는 발송으로 본다")
check(workb.parse_result('{"success": true}')["status"] == "sent",
      "workb.parse_result: 문자열로 온 응답도 읽는다")
check(workb.parse_result(([{"type": "text", "text": '{"success": true}'}], None))["status"] == "sent",
      "workb.parse_result: (content, artifact) 튜플도 읽는다")
# 판정하지 못한 것을 성공 쪽으로 접지 않는다 — 그게 안 한 일을 했다고 말하는 경로다.
for _amb in ("", "OK", '{"result": 1}', None):
    check(workb.parse_result(_amb)["status"] == "unknown",
          f"workb.parse_result: 판정 불가는 unknown 이다 ({_amb!r})",
          str(workb.parse_result(_amb)))

_sent = asyncio.run(workb.send_note(["E00000"], _note))
check(_sent["status"] == "not_connected" and _sent["body"] == _note.body,
      "workb.send_note: 클라이언트 미주입을 «보냄»으로 보고하지 않는다", str(_sent["status"]))

async def _fake_send(recipients, title, body):
    _fake_send.seen = (recipients, title, body)
    return [{"type": "text", "text": '{"success": true}'}]

_ok = asyncio.run(workb.send_note(["3902172"], _note, send=_fake_send))
check(_ok["status"] == "sent" and _fake_send.seen[0] == ["3902172"],
      "workb.send_note: 주입한 클라이언트로 수신자·제목·본문을 그대로 넘긴다", str(_ok))
check(_fake_send.seen[2] == _note.body, "workb.send_note: 본문을 손대지 않고 넘긴다")

async def _boom(recipients, title, body):
    raise RuntimeError("전송 끊김")

_err = asyncio.run(workb.send_note(["3902172"], _note, send=_boom))
check(_err["status"] == "failed" and "전송 끊김" in _err["detail"],
      "workb.send_note: 호출이 죽으면 실패로 보고한다(삼키지 않는다)", str(_err))


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
