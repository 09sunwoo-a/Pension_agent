"""AI 브리핑을 미리 만들어 파일로 내린다 — 평가 화면의 시작 지연을 없애는 생성기.

    cd src
    PENSION_TODAY=2026-09-02 python -m scripts.build_briefings

━━ 왜 있나 ━━
브리핑 한 건이 LLM 12회이고 시연 로스터가 9명이라, 평가 화면(app.py)은 켤 때마다
**108회를 순차로** 돌고 나서야 첫 화면을 그렸다. 게이트웨이가 호출당 2~3초면 4~5분이고,
캐시는 메모리에만 있어 앱을 다시 켜면 처음부터 다시 돈다. 대화형 탭은 브리핑 결과를
하나도 쓰지 않는데도 그 시간을 함께 기다렸다.

만들어 두고 **커밋해서 공유한다.** 그러면 기획자는 앱을 켜자마자 쓰고, LLM 키가 없어도
브리핑 탭을 볼 수 있다. 그리고 시연 문장이 실행할 때마다 달라지지 않는다 — 속도보다
이쪽이 발표에서는 더 큰 이득일 수 있다.

━━ «오늘»을 반드시 못박는다 ━━
브리핑은 오늘에서 파생된 값을 문장에 싣는다 — 만기 D-day(`matDD`)·미접촉 개월(`nchM`)·
투자기간(`invest_period_years`). 날짜가 다르면 그것들이 전부 다른 값이고, 그래서
캐시 키(Profile 전체의 지문)도 달라진다. 즉 **9월 2일로 만든 저장본은 9월 2일에만
적중한다.** 이 스크립트는 만든 날짜를 메타에 적어 두고, app.py 가 그 날짜로 앱을 켠다.

날짜를 안 주면 오늘로 만든다 — 그러면 내일부터는 안 맞는다는 뜻이므로, 그때는 발표일에
맞춰 다시 돌린다(멱등하다 · 같은 날짜면 같은 키가 나온다).

━━ 낡아도 틀리지는 않는다 ━━
적재는 캐시를 «채울» 뿐 대체하지 않는다(`agent.load_prebuilt`). 키가 안 맞으면 그냥
미스가 나고 평소처럼 실시간으로 만든다. 이 파일이 낡았을 때의 최악은 «느려지는 것»이지
«틀린 D-day 를 화면에 세우는 것»이 아니다. 어긋난 건수는 화면 사이드바가 말한다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime

from pension_agent import clock, config, llm
from pension_agent.strategy_agent import agent, engine
from pension_agent.strategy_agent.customer import AS_OF, PERSONAS

#: 저장본이 어떤 호출 옵션의 산출인지. 화면(app.py)도 이 조합으로 부른다 — 다르면
#: 키가 달라 적중하지 않으므로 한 곳에 적어 둔다.
USE_LLM = True


def main() -> int:
    if not llm.available():
        print("LLM 이 설정되지 않았습니다. src/.env 를 채운 뒤 다시 실행하세요.\n"
              "  · 사내 GenAI  LLM_BASE_URL / LLM_API_KEY\n"
              "  · gemma       GEMINI_API_KEY\n"
              "  · anthropic   ANTHROPIC_API_KEY", file=sys.stderr)
        return 1

    today = clock.today()
    top_n = engine.TOP_N
    print(f"기준일(오늘) {today:%Y-%m-%d} · 원장 기준일(AS_OF) {AS_OF:%Y-%m-%d} · "
          f"고객 {len(PERSONAS)}명 · top_n {top_n}")
    if today != AS_OF:
        print(f"  ※ 오늘이 원장 기준일과 {abs((today - AS_OF).days)}일 차이입니다 — "
              f"만기 D-day·미접촉 개월이 그만큼 진행된 상태로 만들어집니다.")

    # 저장본이 이미 캐시를 채우고 있으면 «다시 만들기»가 아니라 «그대로 옮겨 적기»가 된다.
    # 생성기는 항상 새로 만든다 — 그래야 프롬프트·지식카드를 고친 것이 반영된다.
    agent.clear_briefing_cache()

    briefings: dict[str, dict] = {}
    started = time.perf_counter()
    for i, p in enumerate(PERSONAS, 1):
        t = time.perf_counter()
        result = agent.propose(p, use_llm=USE_LLM, top_n=top_n)
        key = agent._cache_key(p, USE_LLM, top_n)
        briefings[key] = result
        tier = result.get("tier", "")
        print(f"  [{i}/{len(PERSONAS)}] {p.nm:6s} {tier:6s} "
              f"{time.perf_counter() - t:5.1f}s  {result.get('sentence', '')[:40]}")

    payload = {
        "meta": {
            # app.py 가 이 날짜로 앱을 켠다. 이 값이 저장본과 화면을 묶는 유일한 끈이다.
            "today": today.isoformat(),
            "as_of": AS_OF.isoformat(),
            "use_llm": USE_LLM,
            "top_n": top_n,
            "customers": len(PERSONAS),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generator": "scripts.build_briefings",
        },
        # 키는 agent._cache_key() 의 지문 그대로다 — 손으로 읽을 것이 아니라 대조할 것이다.
        "briefings": briefings,
    }
    config.BRIEFINGS_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    size = config.BRIEFINGS_JSON.stat().st_size
    print(f"\n{config.BRIEFINGS_JSON.relative_to(config.SRC_ROOT)} "
          f"· {len(briefings)}건 · {size / 1024:.0f}KB · {time.perf_counter() - started:.0f}s")
    print(f"\n앱은 이 날짜로 켜집니다(app.py 가 메타를 읽어 자동으로 맞춥니다):\n"
          f"  cd src && streamlit run app.py        # 오늘 = {today:%Y-%m-%d}")
    print("커밋해서 공유하면 다른 사람은 LLM 호출 없이 브리핑을 봅니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
