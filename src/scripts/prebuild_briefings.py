"""브리핑을 미리 만들어 둔다 — 시연·리허설이 앞에서 기다리지 않게.

    cd src
    python -m scripts.prebuild_briefings              # 전원 (customers.json)
    python -m scripts.prebuild_briefings 188406-7352194 181245-3097614
    python -m scripts.prebuild_briefings --clear      # 저장분을 지우고 저장소를 끈다
    python -m scripts.prebuild_briefings --status     # 무엇이 저장돼 있나

브리핑 한 편은 순차 LLM 호출 11 회다. 프로세스 안에서는 한 번으로 줄지만(agent 의 캐시)
**프로세스가 끝나면 사라진다** — 리허설($CADR --demo)은 고객 블록마다 화면을 여는 자리에서
그 11 회를 다시 치르고, 그래서 매 실행이 앞에서 수십 초를 먹는다. 여기서 한 번 만들어 두면
그 다음부터는 읽어 쓴다.

**이 스크립트를 돌린 적이 없으면 아무것도 달라지지 않는다.** 저장소는 디렉터리가 있어야
켜지고(`briefing_store.enabled`), 디렉터리를 만드는 것이 이 스크립트다. 실서비스에서
브리핑을 프로세스 밖으로 공유하는 방법은 아직 정해지지 않았다 —
`consult_agent/CLAUDE.md` §13 이 그 미결 사항을 들고 있고, 여기서 앞질러 정하지 않는다.

**입력이 바뀌면 저장분은 자동으로 버려진다**(지문 대조 — `briefing_store` 머리말). 지식
카드를 다시 만들었거나 프롬프트를 고쳤거나 날짜가 바뀌면 다시 돌리면 된다. 굳이 지울
필요는 없다 — 안 맞는 저장분은 읽히지 않는다.
"""

from __future__ import annotations

import shutil
import sys
import time

from pension_agent import config
from pension_agent import llm as LLM
from pension_agent.strategy_agent import agent as SA
from pension_agent.strategy_agent import briefing_store, customer as SC

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _status() -> int:
    files = sorted(config.BRIEFING_CACHE_DIR.glob("*.json")) \
        if config.BRIEFING_CACHE_DIR.is_dir() else []
    print(f"저장 위치: {config.BRIEFING_CACHE_DIR}")
    print(f"저장소: {'켜짐' if briefing_store.enabled() else '꺼짐 (디렉터리 없음)'}")
    print(f"저장분: {len(files)}건")
    if not files:
        return 0
    # 지문이 맞는지까지 본다 — 파일이 있는데 안 읽히는 상태("왜 여전히 느리지")가
    # 화면에서 끝나야 한다.
    fresh = 0
    for persona in SC.PERSONAS:
        profile = SC.get_profile(persona.id)
        if profile is None:
            continue
        key = SA._cache_key(profile, True, SA.engine.TOP_N)
        hit = briefing_store.load(key) is not None
        fresh += hit
        print(f"  {'✓' if hit else '·'} {persona.id} {persona.nm}"
              + ("" if hit else "  (지문 불일치 또는 미저장 — 다시 돌리면 됩니다)"))
    print(f"\n지금 그대로 읽어 쓸 수 있는 것: {fresh}/{len(SC.PERSONAS)}명")
    return 0


def _clear() -> int:
    if config.BRIEFING_CACHE_DIR.is_dir():
        shutil.rmtree(config.BRIEFING_CACHE_DIR)
        print(f"지웠습니다: {config.BRIEFING_CACHE_DIR}")
        print("저장소가 꺼졌습니다 — 브리핑은 예전처럼 그때그때 만듭니다.")
    else:
        print("저장된 것이 없습니다.")
    return 0


def main(argv: list[str]) -> int:
    if "--status" in argv:
        return _status()
    if "--clear" in argv:
        return _clear()

    unknown = [a for a in argv if a.startswith("--")]
    if unknown:
        print(f"모르는 옵션입니다: {' '.join(unknown)}")
        print("  옵션: --status · --clear · 고객 id")
        return 1

    if not LLM.available():
        print("LLM 이 설정돼 있지 않습니다 — 브리핑 산문은 LLM 이 씁니다.")
        print("  genai: LLM_BASE_URL · LLM_API_KEY  (src/.env)")
        return 1

    picked = set(argv)
    personas = [p for p in SC.PERSONAS if not picked or p.id in picked]
    missing = picked - {p.id for p in SC.PERSONAS}
    if missing:
        print(f"그런 고객이 이 체크아웃에 없습니다: {', '.join(sorted(missing))}")
        print("  있는 고객: " + " · ".join(f"{p.id} {p.nm}" for p in SC.PERSONAS))
        return 1

    # 디렉터리를 먼저 만든다 — 저장소는 이것이 있어야 켜지고, 켜져 있어야 propose 가
    # 결과를 써 둔다(만들기만 하고 저장이 꺼져 있으면 이 스크립트가 헛돈다).
    config.BRIEFING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"저장 위치: {config.BRIEFING_CACHE_DIR}")

    made = 0
    for persona in personas:
        profile = SC.get_profile(persona.id)
        if profile is None:
            print(f"  ✗ {persona.id} {persona.nm} — 프로파일을 못 읽었습니다")
            continue
        key = SA._cache_key(profile, True, SA.engine.TOP_N)
        if briefing_store.load(key) is not None:
            print(f"  · {persona.id} {persona.nm} — 이미 있음(지문 일치)")
            continue
        started = time.monotonic()
        try:
            SA.propose(profile)
        except Exception as exc:                      # noqa: BLE001 — 한 명이 죽어도 나머지는 돈다
            print(f"  ✗ {persona.id} {persona.nm} — {type(exc).__name__}: {exc}")
            continue
        made += 1
        print(f"  ✓ {persona.id} {persona.nm}  ({time.monotonic() - started:.1f}초)")

    print(f"\n{made}명 새로 만들었습니다. 이제 $CADR --demo 가 브리핑 생성을 건너뜁니다.")
    print("입력(지식 카드·프롬프트·날짜)이 바뀌면 저장분은 자동으로 버려집니다 — 다시 돌리세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
