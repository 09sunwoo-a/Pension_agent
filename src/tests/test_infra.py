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

failed = [(label, detail) for ok, label, detail in _results if not ok]
for ok, label, detail in _results:
    mark = "✓" if ok else "✗"
    print(f"{mark} {label}" + (f" — {detail}" if detail and not ok else ""))

print(f"\n총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}")
if failed:
    print("❌ 회귀 발생")
    raise SystemExit(1)
print("✅ 공용 인프라 회귀 테스트 통과")
