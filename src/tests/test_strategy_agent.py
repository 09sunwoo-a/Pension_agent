"""agent.py 의 LLM 작성 단계 회귀 테스트 — LLM 을 스텁으로 갈아끼우므로 API 키 없이 돌아간다.

engine.verify() 는 이미 test_engine.py 가 대조 함수 단위로 검증하지만, agent.py 가 실제로
그 결과에 따라 규칙 폴백으로 전환하는지, 그리고 ⑤(_recommend)·⑥(_write_talking_scripts) 의
LLM 접점이 목록 밖 id·재료 이탈을 실제로 거부하는지는 agent.py 를 직접 통해서만 검증된다.

픽스처는 시연용 목업 9케이스(customers.json)다 — 이준호(만기 전략·화법 보유)와
박지민(추천 후보 상품·포트폴리오 보유)을 쓴다. 원본 xlsx 교체로 이 전제가 깨지면
각 검사의 전제조건 체크가 명시적으로 실패한다.

실행: python -m tests.test_strategy_agent
"""

from __future__ import annotations

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")  # llm.available() 만 통과시킨다
# 브리핑 파일 저장소(briefing_cache/)를 끈다 — `scripts.prebuild_briefings` 를 돌린 체크아웃에서는
# 스텁 LLM 으로 만든 propose() 산출 대신 저장분이 읽혀 «폴백·거부» 검사 두 건이 갈린다
# (test_engine · test_consult_agent 와 같은 격리). 스텁을 재는 스위트는 파일 저장소를 보지 않는다.
os.environ.setdefault("PENSION_BRIEFING_CACHE", "0")

from pension_agent.strategy_agent import agent as A
from pension_agent.strategy_agent import engine
from pension_agent import llm
from pension_agent.strategy_agent.customer import PERSONAS

_BY_NAME = {p.nm: p for p in PERSONAS}

_results: list[tuple[bool, str, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label, detail))
    print(f"{'✓' if cond else '✗'} {label}" + (f" — {detail}" if detail and not cond else ""))


def _restore_llm() -> None:
    """각 테스트가 끝나면 llm.generate/available 을 원래 함수로 되돌린다."""
    import importlib

    importlib.reload(llm)
    A.llm = llm


# ─────────────────────────────────────────────────────────────
# ① sentence 경로 — order 불일치 → 규칙 폴백
# ─────────────────────────────────────────────────────────────

def check_order_mismatch_fallback() -> None:
    p = _BY_NAME["이준호"]
    facts = engine.prepare(p)
    ids = [it["id"] for it in facts["items"]]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {"order": ids + ["st.없는전략"], "insight": "테스트", "sentence": "테스트 문장입니다."},
        ensure_ascii=False,
    )
    A.llm = llm

    out = A.propose(p)
    check(
        out["source"] == "미생성" and out["sentence"] == "" and "임의로" in out["reason"],
        "propose(): order 가 항목 id 와 불일치하면 sentence 를 비움(규칙 문장으로 얼버무리지 않음)",
        out["reason"],
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ① sentence 경로 — 재료 밖 수치 포함 → verify 거부 → 규칙 폴백
# ─────────────────────────────────────────────────────────────

def check_sentence_verify_rejects_fabrication() -> None:
    p = _BY_NAME["이준호"]
    facts = engine.prepare(p)
    ids = [it["id"] for it in facts["items"]]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {"order": ids, "insight": "테스트",
         "sentence": "이 고객은 수익률 999.9%가 기대되니 적극 제안하세요."},
        ensure_ascii=False,
    )
    A.llm = llm

    out = A.propose(p)
    check(
        out["source"] == "미생성" and out["sentence"] == "" and out["rejected"],
        "propose(): sentence 에 재료 밖 수치가 있으면 verify 거부 → sentence 비움",
        str(out["rejected"]),
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ② 왜 이 고객님인가요 — 정상 생성 및 재료 이탈 거부
# ─────────────────────────────────────────────────────────────

def check_why_customer_accepts_grounded() -> None:
    p = _BY_NAME["이준호"]
    facts = engine.prepare(p)
    # 재료 안 수치(수익률)만 쓰는 문장 — balPct 는 새 데이터에 모수가 없어 None 이다.
    stub_lines = [f"최근 1년 수익률이 {p.ret}%로 양호해 관리 대상으로 떴어요."]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({"lines": stub_lines}, ensure_ascii=False)
    A.llm = llm

    A._write_why_this_customer(facts)
    check(
        facts["why_this_customer"] == stub_lines,
        "② _write_why_this_customer(): 재료 안 값이면 LLM 문장으로 교체",
        str(facts["why_this_customer"]),
    )
    _restore_llm()


def check_why_customer_rejects_fabrication() -> None:
    p = _BY_NAME["이준호"]
    facts = engine.prepare(p)
    rule_lines = list(facts["why_this_customer"])

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {"lines": ["평가금액이 유사 고객 상위 999% 수준이에요."]}, ensure_ascii=False,
    )
    A.llm = llm

    A._write_why_this_customer(facts)
    check(
        facts["why_this_customer"] == rule_lines,
        "② _write_why_this_customer(): 재료 밖 수치가 있으면 규칙 문장 그대로 유지",
        str(facts["why_this_customer"]),
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ⑥ 대고객 화법 스크립트 — 정상 생성 및 재료 이탈 거부
# ─────────────────────────────────────────────────────────────

def check_talking_script_accepts_grounded() -> None:
    p = _BY_NAME["이준호"]  # talking_points 1개 이상 있는 페르소나
    facts = engine.prepare(p)
    if not facts["talking_points"]:
        check(False, "⑥ 화법 스크립트: 전제조건(talking_points 존재) 불충족 — 페르소나 데이터 확인 필요")
        return

    llm.available = lambda: True
    # 제목에 수치("100%")가 든 화법이 있어 제목 인용 스텁은 verify 에 걸린다 — 수치 없는 문장.
    llm.generate = lambda *a, **k: json.dumps(
        {tp["title"]: "고객님, 상담 때 이 내용을 안내드릴게요." for tp in facts["talking_points"]},
        ensure_ascii=False,
    )
    A.llm = llm

    A._write_talking_scripts(facts)
    check(
        all(tp.get("script") for tp in facts["talking_points"]),
        "⑥ _write_talking_scripts(): 재료 안 값이면 script 채움",
        str(facts["talking_points"]),
    )
    _restore_llm()


def check_talking_script_rejects_fabrication() -> None:
    p = _BY_NAME["이준호"]
    facts = engine.prepare(p)
    if not facts["talking_points"]:
        check(False, "⑥ 화법 스크립트(거부 케이스): 전제조건 불충족")
        return

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps(
        {tp["title"]: "고객님, 99999999원을 KB없는상품에 넣으시면 좋아요." for tp in facts["talking_points"]},
        ensure_ascii=False,
    )
    A.llm = llm

    A._write_talking_scripts(facts)
    check(
        all(not tp.get("script") for tp in facts["talking_points"]),
        "⑥ _write_talking_scripts(): 재료 밖 값이면 script 비움(talk 로 폴백)",
    )
    _restore_llm()


# ─────────────────────────────────────────────────────────────
# ⑤ 상품·포트폴리오 추천 — 정상 선정, 목록 밖 id 거부, 재료 이탈 거부
# ─────────────────────────────────────────────────────────────

def check_recommend_accepts_valid_pick() -> None:
    p = _BY_NAME["박지민"]  # 후보 상품·포트폴리오 모두 있는 페르소나
    pool = engine.candidate_pool_for_recommendation(p)
    if not pool["products"] or not pool["portfolios"]:
        check(False, "⑤ 추천: 전제조건(후보 상품·포트폴리오 존재) 불충족")
        return
    pid, fid = pool["products"][0]["id"], pool["portfolios"][0]["id"]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({
        "product_id": pid, "product_reason": "이 상품은 고객 성향에 적합합니다.",
        "portfolio_id": fid, "portfolio_reason": "이 포트폴리오는 분산 효과가 있습니다.",
        "combined_reason": "성장성과 안정성을 함께 고려했습니다.",
    }, ensure_ascii=False)
    A.llm = llm

    reco = A._recommend(p, engine.prepare(p))
    check(reco is not None and reco["product"]["name"], "⑤ _recommend(): 유효한 id 선택 시 결과 반환",
          str(reco))
    check(reco is not None and reco.get("portfolio") is not None, "⑤ _recommend(): 포트폴리오도 함께 반영")
    _restore_llm()


def check_recommend_rejects_out_of_pool_id() -> None:
    p = _BY_NAME["박지민"]
    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({
        "product_id": "P99-NOT-EXIST", "product_reason": "테스트",
        "portfolio_id": None, "portfolio_reason": "", "combined_reason": "",
    }, ensure_ascii=False)
    A.llm = llm

    reco = A._recommend(p, engine.prepare(p))
    check(reco is None, "⑤ _recommend(): 후보 목록 밖 id 는 거부(None 반환)")
    _restore_llm()


def check_recommend_rejects_fabricated_number() -> None:
    p = _BY_NAME["박지민"]
    pool = engine.candidate_pool_for_recommendation(p)
    if not pool["products"]:
        check(False, "⑤ 추천(재료이탈 케이스): 전제조건 불충족")
        return
    pid = pool["products"][0]["id"]

    llm.available = lambda: True
    llm.generate = lambda *a, **k: json.dumps({
        "product_id": pid, "product_reason": "이 상품은 최근 1년 수익률이 999.9%로 압도적입니다.",
        "portfolio_id": None, "portfolio_reason": "", "combined_reason": "",
    }, ensure_ascii=False)
    A.llm = llm

    reco = A._recommend(p, engine.prepare(p))
    check(reco is None, "⑤ _recommend(): 추천 사유에 재료 밖 수치가 있으면 거부(None 반환)")
    _restore_llm()


def check_outreach_prompt_has_no_condition_codes() -> None:
    """⑨ 선별 프롬프트에 요건 코드(isa·tax·add)가 실리지 않는다.

    회귀 대상(2026-09-03 확정본 E1 실측): 성립 요건을 `코드:이름` 그대로, 후보를 `conds`
    코드 목록 그대로 프롬프트에 실었더니 추천 사유가 「세액공제 활용 가능(tax)과 추가입금
    여력 보유(add) 요건」이라 나왔고, 그 사유가 대화 재료로 실려 답변까지 그대로 나갔다.
    """
    import re

    p = _BY_NAME.get("김서연")
    if p is None:
        check(False, "⑨ 선별 프롬프트: 전제조건 불충족 — 김서연 없음")
        return
    facts = engine.prepare(p)
    pools = (facts.get("pools") or {}).get("outreach") or {}
    candidates = pools.get("event") or []
    if len(candidates) < 2 or not any(c.get("conds") for c in candidates):
        check(False, "⑨ 선별 프롬프트: 전제조건 불충족 — 요건이 걸린 이벤트 후보 2건 이상 필요")
        return
    seen: list[str] = []

    def _capture(prompt, *a, **k):
        seen.append(prompt)
        return json.dumps({"pick": 0, "reason": "세액공제 여력이 있는 고객이에요."}, ensure_ascii=False)

    llm.available = lambda: True
    llm.generate = _capture
    A.llm = llm
    A._select_outreach(p, facts, "outreach_event", "이벤트", candidates)
    _restore_llm()

    code = re.compile(r"(?<![A-Za-z])(isa|tax|add|dep|nod|idl|mat|mis|pen|dor|hlt|nch|out)(?![A-Za-z])")
    hit = bool(seen) and not code.search(seen[0]) and "성립 요건" in seen[0]
    check(hit, "⑨ _select_outreach(): 프롬프트의 성립 요건·후보 목록에 요건 코드가 없다",
          detail=(code.search(seen[0]).group(0) if seen and code.search(seen[0]) else "프롬프트 없음"))


def check_shown_state_is_quotable() -> None:
    """프롬프트가 **보여준** 고객 상태 값을 생성문이 인용할 수 있다.

    회귀 대상(2026-09-07 실측 · 고객 188406-7352194): 선별·생성 프롬프트는 고객 상태를
    `_customer_state` 로 보여주는데 검증기가 펴는 것은 `facts["customer"]` 다. 두 스냅샷이
    같지 않아서 — 포트폴리오 4칸·투자기간·운용이력은 앞쪽에만 있다 — **코드가 보여준
    숫자를 LLM 이 옮겨 적으면 코드가 그 문장을 버렸다.** ⑨ 추천 사유가 「생성 사유가 재료를
    벗어남」으로 반려됐고, 대화 쪽은 그 반려 사실을 원장으로 받아 「구체적인 생성 사유는
    확인되지 않아요」로 답했다(리허설 케이스 11).

    살아남은 판은 LLM 이 우연히 13.7 을 「13개월 이상」으로 반올림한 것이었다 — 통과가
    운에 달려 있었다. 그래서 «반려되던 문장이 이제 통과한다»만 재면 부족하고, **넓힌 것이
    보여준 값에서 멈추는지**를 함께 잰다. 지어낸 값·계산한 값까지 열리면 이 수정은 §6 이
    막으려는 것을 정확히 뚫는다.
    """
    p = _BY_NAME.get("송도윤")
    if p is None:
        check(False, "보여준 값 인용: 전제조건 불충족 — 송도윤 없음")
        return
    facts = engine.prepare(p)
    extra = A._state_blob(p)

    # 프롬프트가 실제로 보여주는 값에서 그대로 뽑는다 — 상수로 적으면 더미가 바뀌었을 때
    # 테스트만 통과하고 회귀는 되살아난다.
    state = A._customer_state(p)
    shown = f"최종 운용변경 이후 {p.nchM}개월이 지났고 투자기간은 {state['투자기간']}이에요."
    ok, bad = engine.verify(shown, facts, extra=extra)
    check(ok, "프롬프트가 보여준 고객 상태 값을 생성문이 인용할 수 있다", detail=str(bad))

    port = state["포트폴리오"]
    label, share = next(iter(port.items()))
    ok, bad = engine.verify(f"{label} 비중이 {share} 입니다.", facts, extra=extra)
    check(ok, "포트폴리오 칸의 비중도 인용할 수 있다(한 겹 더 들어가 있다)", detail=str(bad))

    # 넓힌 폭은 «보여준 값» 하나뿐이다.
    ok, _ = engine.verify("예상 수익률은 연 7.5% 입니다.", facts, extra=extra)
    check(not ok, "보여주지 않은 값은 여전히 막힌다(지어낸 수치)")

    ok, _ = engine.verify(f"{p.nchM}개월 중 9개월은 방치였어요.", facts, extra=extra)
    check(not ok, "보여준 값으로 **계산한** 값도 여전히 막힌다")


def check_owned_products_are_quotable() -> None:
    """이 고객이 **가진** 상품 이름을 생성문이 말할 수 있다.

    회귀 대상(2026-09-07 실측 · 송도윤): briefing 의 «보유상품»·«동연령대비교» 칸이 상품
    이름을 프롬프트로 내보내는데, 검증기의 허용 상품 집합은 `items[*].products`(=이번에
    권할 상품)만 봤다. 그래서 ② 선정 사유의 「판매중단 상품인 KB 퇴직연금 배당 (주식)
    8,000만원을 보유하고 계세요」와 ④ 해석이 «상품명 미등록»으로 폐기됐다 — **화면이 이미
    띄운 사실을 그대로 옮긴 문장**이다.

    허가는 «이 고객의 원장에 이름이 있는 것»까지다. 상품명 상한이 살아 있는지를 같이
    잰다 — 지어낸 이름과, 다른 고객이 가진 이름은 여전히 막혀야 한다. 그 둘까지 열리면
    이 수정은 닫힌 목록이 막으려던 순환을 그대로 되살린다.
    """
    p = _BY_NAME.get("송도윤")
    if p is None:
        check(False, "보유 상품 인용: 전제조건 불충족 — 송도윤 없음")
        return
    facts = engine.prepare(p)
    owned = facts.get("owned_products") or []
    mine = next((n for n in owned if n.startswith("KB ")), "")
    if not mine:
        check(False, "보유 상품 인용: 전제조건 불충족 — 검사기가 보는 'KB ' 이름이 없다")
        return

    ok, bad = engine.verify(f"판매중단 상품인 {mine} 을 보유하고 계세요.", facts)
    check(ok, f"이 고객이 가진 상품 이름을 말할 수 있다 ({mine})", detail=str(bad))

    ok, _ = engine.verify("KB 무지개 성장 펀드를 권해보세요.", facts)
    check(not ok, "지어낸 상품 이름은 여전히 막힌다")

    # 다른 고객의 보유 상품 — facts 는 고객 한 명당 하나이므로 넘어오면 안 된다.
    others = {n for q in PERSONAS if q.nm != p.nm
              for n in (engine.prepare(q).get("owned_products") or [])}
    alien = next((n for n in sorted(others - set(owned)) if n.startswith("KB ")), "")
    if alien:
        ok, _ = engine.verify(f"{alien} 를 보유 중이세요.", facts)
        check(not ok, f"다른 고객이 가진 상품 이름은 막힌다 ({alien})")


# ─────────────────────────────────────────────────────────────
# LLM 호출 실패 — 빈 브리핑을 파일 저장소에 남기지 않는다
# ─────────────────────────────────────────────────────────────

def _with_store(fn) -> None:
    """임시 디렉터리로 파일 저장소를 켠 채 fn(tmp) 을 돌리고 전부 되돌린다."""
    import pathlib
    import shutil
    import tempfile

    from pension_agent import config

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="briefing-store-"))
    saved_dir = config.BRIEFING_CACHE_DIR
    saved_env = os.environ.get("PENSION_BRIEFING_CACHE")
    config.BRIEFING_CACHE_DIR = tmp
    os.environ["PENSION_BRIEFING_CACHE"] = "1"
    try:
        fn(tmp)
    finally:
        config.BRIEFING_CACHE_DIR = saved_dir
        if saved_env is None:
            os.environ.pop("PENSION_BRIEFING_CACHE", None)
        else:
            os.environ["PENSION_BRIEFING_CACHE"] = saved_env
        A.clear_briefing_cache()
        shutil.rmtree(tmp, ignore_errors=True)


def check_call_failure_is_not_persisted() -> None:
    """호출이 죽어서(429) 빈 섹션이 있는 브리핑은 파일로 남지 않는다.

    회귀 대상(2026-09-08 행내 실측): prebuild_briefings 가 429 를 맞으며 만든 브리핑이
    저장됐고, 다음 실행은 「이미 있음(지문 일치)」으로 건너뛰었다 — 빈 섹션은 시연 화면에서야
    보인다. 대조군으로 «LLM 은 답했는데 코드가 거른» 브리핑은 저장한다 — 다시 불러도
    같은 자리로 온다.
    """
    p = _BY_NAME["이준호"]

    def run(tmp) -> None:
        llm.available = lambda: True

        def boom(*a, **k):
            raise llm.LLMError("HTTP 429 — 5회 시도 후에도 실패. 속도 제한.", status=429)

        llm.generate = boom
        A.llm = llm
        A.clear_briefing_cache()
        out = A.propose(p)
        failed = A.llm_failed(out)
        check(bool(failed) and all(f["status"] == 429 for f in failed.values()),
              "propose(): 호출이 죽은 섹션이 facts.llm_failed 에 HTTP 코드와 함께 남는다",
              str(failed)[:120])
        check(A.rate_limited(out), "rate_limited(): 429 를 알아본다")
        check("sentence" in failed and out["sentence"] == "",
              "propose(): 브리핑 문장 호출의 실패도 같은 자리에 남는다", str(sorted(failed)))
        check(not list(tmp.glob("*.json")),
              "propose(): 호출이 죽은 브리핑은 파일 저장소에 쓰지 않는다",
              str(list(tmp.glob("*.json"))))

        # 대조군 — LLM 이 답은 했고 코드가 거른 경우(파싱 실패)는 저장한다.
        llm.generate = lambda *a, **k: "JSON 이 아닌 답"
        A.clear_briefing_cache()
        out2 = A.propose(p)
        check(not A.llm_failed(out2) and bool(out2["facts"]["llm_skipped"]),
              "propose(): 파싱 실패는 호출 실패가 아니다(llm_failed 비어 있음)",
              str(A.llm_failed(out2))[:120])
        check(len(list(tmp.glob("*.json"))) == 1,
              "propose(): 파싱 실패 브리핑은 저장한다 — 다시 불러도 같은 자리로 온다")

    _with_store(run)
    _restore_llm()


def check_llmless_briefing_is_not_persisted() -> None:
    """LLM 을 안 부른 브리핑은 파일로 남지 않는다 — LLM 섹션이 통째로 빈 산출이다.

    429 방어(`not llm_failed`)가 여기까지 덮지 못한다. 그 방어는 **호출이 죽은** 것을
    보는데, 여기는 부르지 않은 것이다. 두 갈래가 있고 갈래마다 남는 흔적이 다르다.

      · 키가 없다 — 섹션 대부분은 `llm.generate` 까지 가서 LLMError 를 받으므로
        llm_failed 에 남는다. 즉 기존 방어가 이미 막는다. 다만 그건 «그 섹션까지 갔을
        때»의 이야기고, 브리핑 문장처럼 `available()` 을 먼저 보고 부르지 않는 자리는
        아무 흔적도 남기지 않는다 — 재료가 없어 지원 섹션이 전부 건너뛰어진 고객이면
        llm_failed 가 빈 채로 저장될 수 있다.
      · `use_llm=False` — 부르지 않기로 **정한** 것이라 llm_failed 가 확실히 비어 있다.
        예전 방어를 그대로 통과해 저장됐다.

    저장소를 커밋하기로 하면서(briefing_cache/) 이 자리가 위험해졌다. 키 없이 돌린
    체크아웃에서 빈 브리핑이 파일로 생기고, 그것이 커밋되면 배포 이미지가 «출처는 진짜인데
    내용이 빈» 브리핑을 미리 만들어 둔 것으로 읽는다.
    """
    p = _BY_NAME["이준호"]

    def run(tmp) -> None:
        llm.available = lambda: False       # 키 없는 체크아웃
        A.llm = llm
        A.clear_briefing_cache()
        out = A.propose(p)
        check(out["source"] != "LLM" and "LLM 미설정" in out["reason"],
              "propose(): 키가 없으면 브리핑 문장을 비우고 사유를 남긴다",
              f"source={out['source']} · reason={out['reason']}")
        check(not list(tmp.glob("*.json")),
              "propose(): LLM 없이 만든 브리핑은 파일 저장소에 쓰지 않는다",
              str(list(tmp.glob("*.json"))))

        # use_llm=False — llm_failed 가 확실히 비는 갈래다. 여기가 예전 방어의 구멍이었다.
        llm.available = lambda: True
        llm.generate = lambda *a, **k: '{"sentence": "x", "insight": "y", "order": []}'
        A.clear_briefing_cache()
        out2 = A.propose(p, use_llm=False)
        check(not A.llm_failed(out2),
              "propose(use_llm=False): 부르지 않기로 한 것은 «호출 실패»가 아니다",
              str(A.llm_failed(out2))[:120])
        check(not list(tmp.glob("*.json")),
              "propose(use_llm=False): 그래도 저장하지 않는다 — 빈 산출이다",
              str(list(tmp.glob("*.json"))))

    _with_store(run)
    _restore_llm()


def check_prebuild_stops_on_rate_limit() -> None:
    """prebuild_briefings 는 429 를 만나면 다음 고객으로 넘어가지 않고 멈춘다.

    넘어가 봐야 섹션마다 재시도 횟수를 다 쓰며 같은 429 를 맞는다 — 고객 한 명에 수십 분이
    사라지고 남는 것은 없다. ✓ 가 아니라 ✗ 로 적고, 종료 코드로도 «다 못 했다»를 알린다.
    """
    import contextlib
    import io

    from scripts import prebuild_briefings as PB

    calls: list[str] = []

    def fake_propose(profile, **kw):
        calls.append(profile.nm)
        return {"customer": profile.nm, "sentence": "", "facts": {"llm_skipped": {},
                "llm_failed": {"coaching": {"error": "LLMError", "status": 429,
                                            "detail": "HTTP 429 — 5회 시도 후에도 실패"}}}}

    def run(tmp) -> None:
        saved = (A.propose, llm.available)
        A.propose, llm.available = fake_propose, (lambda: True)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = PB.main([])
        finally:
            A.propose, llm.available = saved
        text = buf.getvalue()
        check(len(calls) == 1, "prebuild: 429 뒤 다음 고객을 부르지 않는다", str(calls))
        check("✗" in text and "속도 제한" in text and "✓" not in text,
              "prebuild: 호출이 죽은 고객은 ✗ 로 적고 멈춘 이유를 말한다", text[-300:])
        check(rc != 0, "prebuild: 다 못 만들었으면 종료 코드가 0 이 아니다", str(rc))
        check(not list(tmp.glob("*.json")), "prebuild: 그 고객은 파일로 남지 않는다")

    _with_store(run)


def main() -> int:
    check_call_failure_is_not_persisted()
    check_llmless_briefing_is_not_persisted()
    check_prebuild_stops_on_rate_limit()
    check_outreach_prompt_has_no_condition_codes()
    check_shown_state_is_quotable()
    check_owned_products_are_quotable()
    check_order_mismatch_fallback()
    check_sentence_verify_rejects_fabrication()
    check_why_customer_accepts_grounded()
    check_why_customer_rejects_fabrication()
    check_talking_script_accepts_grounded()
    check_talking_script_rejects_fabrication()
    check_recommend_accepts_valid_pick()
    check_recommend_rejects_out_of_pool_id()
    check_recommend_rejects_fabricated_number()

    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} 통과")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
