"""부점 브리핑 시험 질문 — 직원이 실제로 물을 법한 질문과 그 정답.

한 질문에 둘을 적는다.
- 오프라인(LLM 없음, `tests.test_branch` 가 매번 검사): 정답 조회문이 검사·실행을 통과하는지, 행 수가 맞는지,
  «조회 조건» 줄이 조건을 빠짐없이 옮기는지, SQL 프롬프트에 기준이 실려 있는지(없으면 LLM 이 기준을 지어낸다),
  이상적인 답(`ideal`)을 검증기가 통과시키는지.
- 라이브(실제 LLM, `python -m tests.live_branch`): 에이전트가 고른 도구 · 조회문 결과가 정답과 같은지 ·
  답변에 있어야 할 말(`must`)과 없어야 할 말(`must_not`).

행 수·값은 행외 목업(`tools/branch_briefing/demo_db.py`)의 고정 기준일(`tests/__init__.py` PINNED_TODAY) 값이다 — 목업을 바꾸면 여기 값도 고친다.
질문 하나를 더하는 것은 `QUESTIONS` 에 한 줄을 더하는 일이다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

EMP = "3902172"          # 시연 사번 — 여의도종합금융센터(0001)
BRANCH_CODE = "0001"


@dataclass(frozen=True)
class Q:
    id: str
    question: str
    tool: str                        # 기대 도구(라이브: 원장에 이 도구의 재료가 있어야 한다). «a|b» 는 둘 중 하나
    sql: str | None = None           # 정답 조회문(branch_search). None 이면 «조회할 수 없음»이 정답
    rows: int | None = None          # 정답 조회문의 행 수(목업 기준, 상한 100 에 걸리면 100)
    truncated: bool = False          # 정답 조회문이 행 수 상한에 걸리는가
    criteria: tuple[str, ...] = ()   # «조회 조건» 줄에 나와야 할 말(순서대로, 빠짐없이)
    prompt_has: tuple[str, ...] = () # SQL 프롬프트에 있어야 할 기준 — 없으면 LLM 이 기준을 지어낸다
    ideal: str = ""                  # 이상적인 답의 첫머리 — 검증기가 통과시켜야 한다
    must: tuple[str, ...] = ()       # 답변에 있어야 할 말. «a|b» 는 둘 중 하나
    must_not: tuple[str, ...] = ()   # 답변에 있으면 안 되는 말
    names: bool = True               # 명단 질문이면 정답 명단의 이름이 답변에 모두 나와야 한다(30명 이하일 때)
    note: str = field(default="", compare=False)  # 왜 넣은 질문인가(과거 실측 결함)


QUESTIONS: list[Q] = [
    Q("deposit_focus",
      "정기예금 만기가 한달 이내이면서 원리금 보장으로 상품이 편중 되었고, DO 미등록 고객 알려줘",
      "branch_search",
      sql="SELECT customer_name, days_to_maturity, safe_asset_pct, eval_amt FROM v_irp_customer "
          "WHERE next_maturity_type = '예금' AND days_to_maturity BETWEEN 0 AND 30 "
          "AND safe_asset_pct >= 80 AND default_option = '미설정'",
      rows=4,
      criteria=("만기 상품: 정기예금", "만기까지 남은 일수 0일~30일", "원리금보장·현금성 자산 비중 80% 이상",
                "디폴트옵션: 미설정"),
      prompt_has=("next_maturity_type = '예금'", "safe_asset_pct >= 80", "디폴트옵션 미등록"),
      ideal="여의도종합금융센터 부점에서 정기예금 만기가 30일 이내이고, 원리금보장·현금성 자산 비중이 80% 이상이며, "
            "디폴트옵션이 미설정인 고객은 총 4명이에요.\n- 민지유 — 만기까지 20일 · 평가금액 12,450,000원",
      must=("정기예금", "30일", "80%", "디폴트옵션", "4명"),
      must_not=("GIC",),
      note="2026-10-23 — GIC 를 섞어 6명으로 답함 · 답변 첫머리에서 정기예금 조건을 뺌"),
    Q("dormant_big_maturity",
      "만기 1억 이상 고객 중 장기 미접촉 고객 알려줘",
      "branch_search",
      sql="SELECT customer_name, next_maturity_amt, last_contact_days, eval_amt FROM v_irp_customer "
          "WHERE next_maturity_amt >= 100000000 AND target_reasons LIKE '%장기 미접촉%'",
      rows=3,
      criteria=("만기 금액 1억원 이상", "관리 대상 요건 «장기 미접촉»(마지막 접촉 후 180일 이상)"),
      prompt_has=("target_reasons LIKE '%장기 미접촉%'", "180일"),
      ideal="여의도종합금융센터 부점에서 만기 금액 1억 원 이상이며 마지막 접촉 후 180일 이상 지난 장기 미접촉 "
            "고객은 총 3명이에요.\n- 정민석 — 만기 금액 161,000,000원 · 미접촉 185일 · 평가금액 260,000,000원",
      must=("1억", "180일", "3명"),
      must_not=("90일",),
      note="2026-10-23 — 장기 미접촉을 LLM 이 «90일 이상»으로 지어내 7명으로 답함"),
    Q("past_deposit",
      "만기 지난 정기예금 있는 고객 보여줘",
      "branch_search",
      sql="SELECT customer_name, days_to_maturity, eval_amt FROM v_irp_customer "
          "WHERE next_maturity_type = '예금' AND days_to_maturity < 0",
      rows=16,
      criteria=("만기 상품: 정기예금", "만기가 지남"),
      prompt_has=("음수면 만기가 지난 것",),
      ideal="여의도종합금융센터 부점에서 정기예금 만기가 지난 고객은 총 16명이에요.\n"
            "- 모시우 — 만기 7일 지남 · 평가금액 32,880,000원",
      must=("정기예금", "지난|지남", "16명"),
      must_not=("0일 미만", "만기까지 -"),
      note="2026-10-23 — «만기까지 -13일» · «만기가 0일 미만인 고객»"),
    Q("etf_low_return",
      "ETF 1천만원 이상 투자했는데 수익률 5% 미만인 고객 보여줘",
      "branch_search",
      sql="SELECT customer_name, etf_eval_amt, return_rate, eval_amt FROM v_irp_customer "
          "WHERE etf_eval_amt >= 10000000 AND return_rate < 5",
      rows=8,
      criteria=("ETF 평가금액 1,000만원 이상", "운용 수익률 5% 미만"),
      prompt_has=("etf_eval_amt",),
      ideal="여의도종합금융센터 부점에서 ETF 평가금액이 1,000만원 이상이고 운용 수익률이 5% 미만인 고객은 "
            "총 8명이에요.\n- 최서윤 — ETF 평가금액 12,500,000원 · 수익률 -9.4% · 평가금액 58,000,000원",
      must=("ETF", "5%", "8명"),
      note="2026-10-22 — 수익률 셀이 맨숫자여서 답변의 «-9.4%»가 폐기됨"),
    Q("idle_cash_sum",
      "납입금 미운용 고객 몇 명이고 현금 합계 얼마야?",
      "branch_search",
      sql="SELECT COUNT(*) AS 고객수, SUM(idle_cash_amt) AS 현금합계 FROM v_irp_customer WHERE idle_cash_pct >= 50",
      rows=1,
      criteria=("미운용 현금성자산 비중 50% 이상",),
      prompt_has=("납입금 미운용", "idle_cash_pct >= 50"),
      ideal="여의도종합금융센터 부점에서 미운용 현금성자산 비중이 50% 이상인 고객은 26명이고, "
            "미운용 현금 합계는 600,000,000원이에요.",
      must=("50%", "26명", "6억|600,000,000"),
      names=False),
    Q("big_balance_truncated",
      "IRP 잔액 7천만원 이상 고객 보여줘",
      "branch_search",
      sql="SELECT customer_name, eval_amt FROM v_irp_customer WHERE eval_amt >= 70000000",
      rows=100, truncated=True,
      criteria=("평가금액 7,000만원 이상",),
      ideal="여의도종합금융센터 부점에서 평가금액 7,000만원 이상인 고객은 100명이 넘어 앞의 100명만 보여드려요.",
      must=("7,000만|7천만", "100"),
      must_not=("총 100명",),
      names=False,
      note="행 수 상한 — 전체 인원을 모르면서 «총 100명»이라고 하면 안 된다"),
    Q("pension_60s_count",
      "연금 개시 요건은 되는데 아직 개시 안 한 60대 고객 몇 명이야?",
      "branch_search",
      sql="SELECT COUNT(*) AS 고객수 FROM v_irp_customer "
          "WHERE age BETWEEN 60 AND 69 AND pension_eligible = '충족' AND pension_started = '미개시'",
      rows=1,
      criteria=("나이 60세~69세", "연금개시 요건: 충족", "연금 개시 여부: 미개시"),
      prompt_has=("pension_eligible = '충족' AND pension_started = '미개시'",),
      ideal="여의도종합금융센터 부점에서 60대(60세~69세)이면서 연금개시 요건을 충족했지만 아직 연금을 개시하지 않은 "
            "고객은 145명이에요.",
      must=("60", "145명"),
      names=False),
    Q("tax_room_aggressive",
      "세액공제 한도 여유 있는 공격투자형 고객 몇 명이야?",
      "branch_search",
      sql="SELECT COUNT(*) AS 고객수 FROM v_irp_customer "
          "WHERE tax_credit_room >= 3000000 AND pension_started = '미개시' AND risk_profile = '공격투자형'",
      rows=1,
      criteria=("세액공제 잔여한도 300만원 이상", "연금 개시 여부: 미개시", "투자성향: 공격투자형"),
      prompt_has=("tax_credit_room >= 3000000 AND pension_started = '미개시'",),
      ideal="여의도종합금융센터 부점에서 세액공제 잔여한도가 300만원 이상이고 연금을 개시하지 않은 공격투자형 "
            "고객은 78명이에요.",
      must=("300만", "공격투자형", "78명"),
      names=False,
      note="세그먼트 «세액공제 한도 여유»의 정의(연금 미개시 포함)를 그대로 써야 한다"),
    Q("gic_maturity_sum",
      "60일 안에 만기 오는 GIC 고객 몇 명이고 만기 금액 합계는 얼마야?",
      "branch_search",
      sql="SELECT COUNT(*) AS 고객수, SUM(next_maturity_amt) AS 만기금액합계 FROM v_irp_customer "
          "WHERE next_maturity_type = 'GIC' AND days_to_maturity BETWEEN 0 AND 60",
      rows=1,
      criteria=("만기 상품: GIC(이율보증형보험)", "만기까지 남은 일수 0일~60일"),
      prompt_has=("'GIC'",),
      ideal="여의도종합금융센터 부점에서 60일 안에 GIC(이율보증형보험) 만기가 오는 고객은 34명이고, "
            "만기 금액 합계는 559,320,000원이에요.",
      must=("GIC", "60일", "34명", "559,320,000|5억 5,932만|5억5,932만"),
      names=False),
    Q("branch_compare",
      "우리 부점 평균 수익률이 다른 부점보다 높아?",
      "branch_search",
      sql="SELECT branch_name, avg_return_rate FROM v_branch_summary",
      rows=4,
      ideal="여의도종합금융센터 부점의 평균 수익률은 17.48%로, 강남(7.94%)과 판교(7.35%)보다 높아요.",
      must=("17.48%", "높"),
      names=False),
    Q("transfer_this_year",
      "올해 계약이전으로 나간 계좌 몇 건이야?",
      "branch_search",
      sql="SELECT COUNT(*) AS 건수 FROM v_transfer_out WHERE transfer_date >= '2026-01-01'",
      rows=1,
      criteria=("계약이전 일자 2026-01-01부터",),
      prompt_has=("v_transfer_out",),
      ideal="여의도종합금융센터 부점에서 2026년 1월 1일부터 다른 금융기관으로 계약이전된 IRP 계좌는 89건이에요.",
      must=("89건",),
      names=False,
      note="날짜 조건이 «조회 조건» 줄에서 빠졌었다(오프라인 발견) · SQL 프롬프트에 오늘이 없어 «올해»를 2024 로 "
           "조회해 0건으로 답함(2026-10-23 실측)"),
    Q("transfer_destination",
      "계약이전 어디로 많이 나갔어?",
      "branch_search",
      sql="SELECT to_institution, COUNT(*) AS 건수 FROM v_transfer_out GROUP BY to_institution",
      rows=3,
      ideal="여의도종합금융센터 부점에서 계약이전된 계좌는 증권 54건, 은행 18건, 보험 17건 순으로 많아요.",
      must=("증권", "54건"),
      names=False),
    Q("default_option_vs_prev",
      "디폴트옵션 미설정 고객이 지난달보다 늘었어?",
      "branch_search|branch_status",  # 부점 현황의 고객군 인원(전월말 → 오늘)으로 답해도 맞다
      sql="SELECT (SELECT COUNT(*) FROM v_irp_customer WHERE default_option = '미설정') AS 오늘, "
          "(SELECT COUNT(*) FROM v_irp_customer_prev WHERE default_option = '미설정') AS 전월말",
      rows=1,
      criteria=("디폴트옵션: 미설정",),
      prompt_has=("v_irp_customer_prev",),
      ideal="여의도종합금융센터 부점의 디폴트옵션 미설정 고객은 전월말 275명에서 오늘 276명으로 1명 늘었어요.",
      must=("275명", "276명"),
      names=False),
    Q("cannot_children",
      "자녀가 있는 고객 알려줘",
      "branch_search",
      ideal="자녀 여부는 부점 고객 데이터에 없는 항목이라 조회할 수 없어요.",
      must=("수 없|없는 항목|없어",),
      must_not=("명이에요", "명입니다"),
      names=False),
    Q("cannot_partial",
      "만기 1억 이상이면서 주택을 보유한 고객 보여줘",
      "branch_search",
      ideal="주택 보유 여부는 부점 고객 데이터에 없는 항목이라 이 질문은 조회할 수 없어요.",
      must=("수 없|없는 항목|없어",),
      must_not=("명이에요", "명입니다"),
      names=False,
      note="조건 일부(만기 1억)만으로 조회하면 다른 질문의 답이 된다(SQL 규칙 14)"),
    Q("deposit10_pension_savings",
      "정기예금 만기 10일 이내이면서 연금 저축 보유 고객 알려줘",
      "branch_search",
      sql="SELECT customer_name, days_to_maturity, eval_amt FROM v_irp_customer "
          "WHERE next_maturity_type = '예금' AND days_to_maturity BETWEEN 0 AND 10 AND pension_savings = '보유'",
      rows=7,
      criteria=("만기 상품: 정기예금", "만기까지 남은 일수 0일~10일", "연금저축: 보유"),
      prompt_has=("pension_savings",),
      ideal="여의도종합금융센터 부점에서 정기예금 만기가 10일 이내이고 연금저축을 보유한 고객은 총 7명이에요.\n"
            "- 문가은 — 만기까지 7일 · 평가금액 3,110,000원",
      must=("정기예금", "10일", "연금저축|연금 저축", "7명"),
      note="연금저축 보유 컬럼이 없어 «조회할 수 없음»이 정답이었다 → 목업에 pension_savings 추가(2026-10-23). "
           "세액공제 잔여한도 설명의 «연금저축» 낱말로 대신하지 않아야 한다"),
    Q("status_today",
      "오늘 부점 현황 말해줘",
      "branch_status",
      must=("여의도종합금융센터|부점",),
      names=False),
    Q("briefing_today",
      "오늘의 부점 브리핑 알려줘",
      "branch_status",
      must=("오늘의 부점 브리핑", "| 부점 현황 | 값 |"),
      must_not=("관리신호 분석", "계약이전 실적", "계약이전 성과"),
      names=False,
      note="2026-10-23 사용자 결정 — 코드가 만든 제목·표 하나(부점 현황 | 값) 안에 LLM 브리핑 문장"),
    Q("targets_today",
      "오늘 중점적으로 관리할 고객은 누구야?",
      "branch_targets",
      must=("명", "| 순번 | 고객 |"),
      must_not=("편중(80% 이상)",),
      names=False),
]


def by_id(qid: str) -> Q:
    return next(q for q in QUESTIONS if q.id == qid)


def compact(text: str) -> str:
    """띄어쓰기 차이를 지운 비교용 문자열(«1억 원» = «1억원»)."""
    return re.sub(r"\s+", "", text or "")


def has(text: str, phrase: str) -> bool:
    """«a|b» 는 둘 중 하나가 있으면 참."""
    body = compact(text)
    return any(compact(p) in body for p in phrase.split("|"))


def answer_faults(q: Q, answer: str, names: list[str] | None = None, count: int | None = None) -> list[str]:
    """답변 문장 검사(라이브 · 이상적인 답 공용). 걸린 것을 사람이 읽는 말로."""
    # 조건·인원은 명단 앞에서 말해야 한다(답변 형태 — 목록 앞에서 조회 조건을 하나도 빼지 않고)
    # 비교·집계 답은 값이 목록 안에 있어도 된다 — 앞머리 검사는 고객 명단 질문만
    head = re.split(r"\n\s*(?:[-·•]|\d+\.)\s", answer, maxsplit=1)[0] if q.names else answer
    out = [f"명단 앞에 있어야 할 말이 없음: «{p}»" for p in q.must if not has(head, p)]
    out += [f"없어야 할 말이 있음: «{p}»" for p in q.must_not if has(answer, p)]
    if names is not None and q.names and len(names) <= 30:
        missing = [n for n in names if n not in answer]
        if missing:
            out.append(f"정답 명단 중 답변에 없는 이름: {', '.join(missing)}")
    if count is not None and q.names and not q.truncated and not has(answer, f"{count}명"):
        out.append(f"인원 «{count}명»을 말하지 않음")
    return out


def llm_reply(q: Q, reason: str = "조회") -> str:
    """SQL 작성 LLM 이 정답을 냈을 때의 응답(오프라인 재료 만들기용)."""
    return json.dumps({"sql": q.sql, "reason": reason}, ensure_ascii=False)
