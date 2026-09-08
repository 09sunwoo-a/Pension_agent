"""미리 만들어 둔 브리핑 — 프로세스가 새로 떠도 처음 한 번을 다시 안 만들게 한다.

브리핑 한 편은 순차 LLM 호출 11 회다. 프로세스 안에서는 `agent._BRIEFING_CACHE` 가 그것을
한 번으로 줄이지만, **프로세스가 끝나면 같이 사라진다** — 시연 리허설($CADR --demo)은 매
실행 시작에 고객 블록마다 그 11 회를 다시 치르고, 화면을 여는 자리에 그 비용이 있으니
리허설을 한 번 돌릴 때마다 앞에서 수십 초를 기다리게 된다. 여기 저장해 두면 두 번째
실행부터는 읽어 쓴다.

━━ 이것은 실서비스 캐시가 아니다 ━━
프로세스 경계를 넘어 브리핑을 공유하는 방법은 아직 정해지지 않았다(consult_agent/CLAUDE.md
§13 「실서비스 프론트와 브리핑 공유」 — 무엇을 키로 언제까지 남길지가 그 항목의 미결
사항이다). 여기서 그 결정을 앞질러 내리지 않으려고 셋을 지킨다.

  · **없으면 아무 일도 일어나지 않는다.** 디렉터리가 없으면 저장소는 통째로 꺼진 것이다 —
    `scripts.prebuild_briefings` 를 돌린 사람만 켠 셈이 된다(기본 동작 무변경).
  · **저장소에 커밋하지 않는다**(.gitignore). 브리핑은 LLM 산문이라, 커밋하면 사람이
    검토하지 않은 문장이 자산으로 굳는다.
  · **입력이 달라지면 읽지 않는다.** 아래 지문(fingerprint)이 그 판정이다.

━━ 지문 — 무엇이 달라지면 버리나 ━━
브리핑을 결정하는 입력은 프로파일만이 아니다. 지식·상품 카탈로그, 브리핑을 만드는 코드,
그리고 **오늘 날짜**(잔여일수·미접촉 일수가 오늘 기준이다)가 함께 정한다. 그중 하나라도
달라진 저장본을 읽으면 화면이 낡은 브리핑을 띄우고, 그건 이 저장소가 가장 경계하는
«화면과 값이 갈리는» 실패의 조용한 형태다 — 캐시라서 아무도 안 본다.

내용 해시로 잡는다(mtime 아님). 체크아웃만 바꿔도 mtime 은 흔들리는데, 그 흔들림으로
버리는 것은 안전하지만 **같은 내용에 매번 다시 만드는** 쪽이라 이 파일이 있을 이유가
없어진다.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pension_agent import config
from pension_agent.clock import today

#: 지문에 넣을 파일. 브리핑 산출을 바꾸는 것들이다 — 데이터(카드·카탈로그·고객 원장·타겟)와
#: 그것을 문장으로 만드는 코드(strategy_agent 전부). 하나라도 빠뜨리면 그 축이 바뀌었을 때
#: 낡은 브리핑을 읽는다.
def _fingerprint_files() -> list[Path]:
    out: list[Path] = []
    for root in config.DATA_ROOTS:
        out += sorted(root.rglob("*.json"))
    out += [config.CUSTOMERS_JSON, config.TARGETS_JSON]
    out += sorted((config.PACKAGE_ROOT / "strategy_agent").rglob("*.py"))
    return [p for p in out if p.is_file()]


_FINGERPRINT: str | None = None


def fingerprint() -> str:
    """이번 프로세스의 입력 지문. 한 번 계산해 들고 있는다(실행 중에는 안 바뀐다)."""
    global _FINGERPRINT
    if _FINGERPRINT is None:
        h = hashlib.sha256()
        for path in _fingerprint_files():
            h.update(path.name.encode())
            h.update(path.read_bytes())
        # 오늘이 바뀌면 잔여일수·미접촉 일수가 바뀐다 — 날짜를 빼면 어제 만든 브리핑이
        # 오늘 화면에 그대로 뜬다.
        h.update(str(today()).encode())
        _FINGERPRINT = h.hexdigest()
    return _FINGERPRINT


def enabled() -> bool:
    """저장소를 쓸 것인가. **디렉터리가 있어야 켜진다** — 만든 사람만 쓰는 셈이다."""
    if os.environ.get("PENSION_BRIEFING_CACHE", "").strip() in ("0", "off", "false"):
        return False
    return config.BRIEFING_CACHE_DIR.is_dir()


def _path(key: str) -> Path:
    return config.BRIEFING_CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def load(key: str) -> dict[str, Any] | None:
    """저장된 브리핑. 없거나·지문이 다르거나·읽다 깨지면 None(그러면 평소대로 만든다)."""
    if not enabled():
        return None
    try:
        stored = json.loads(_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("fingerprint") != fingerprint():
        return None
    briefing = stored.get("briefing")
    return briefing if isinstance(briefing, dict) else None


def save(key: str, briefing: dict[str, Any]) -> None:
    """브리핑을 저장한다. 실패는 삼킨다 — 캐시를 못 쓴다고 답변이 막히면 안 된다."""
    if not enabled():
        return
    try:
        config.BRIEFING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"fingerprint": fingerprint(), "briefing": briefing}
        # 통째로 쓴 뒤 옮긴다 — 반쯤 쓰인 파일을 다음 프로세스가 읽으면 그때부터는
        # «깨진 캐시»가 아니라 «깨진 브리핑»이 화면에 뜬다.
        target = _path(key)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(target)
    except (OSError, TypeError, ValueError):
        return


__all__ = ["enabled", "fingerprint", "load", "save"]
