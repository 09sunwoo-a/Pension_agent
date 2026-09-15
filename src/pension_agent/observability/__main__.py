"""자가진단 — python -m pension_agent.observability

`observability.py` 에서 갈라낸 모듈 — 공개 표면은 패키지 `__init__` 이다.
"""

from __future__ import annotations

import os
import time

from pension_agent import env

from pension_agent.observability._conf import conf
from pension_agent.observability._trace import record_generation, trace
from pension_agent.observability._transport import flush, last_error, stats


# ─────────────────────────────────────────────────────────────
# 자가진단 — python -m pension_agent.observability
#
# 「대시보드에 안 찍힌다」의 원인은 넷이다: 키가 없다 · `.env` 를 다른 데 뒀다 ·
# 호스트가 틀렸다(Langfuse Cloud 는 EU·US 주소가 다르다) · 망이 막혔다. 전송은 백그라운드라
# 실패가 호출부로 올라오지 않으므로, 넷을 한 번에 갈라주는 자리를 따로 둔다.
# ─────────────────────────────────────────────────────────────

def _mask(value: str) -> str:
    return f"{value[:8]}…{value[-4:]}" if len(value) > 14 else ("설정됨" if value else "없음")


def selftest() -> int:
    """설정을 찍고 이벤트 한 건을 실제로 보낸다. 0 이면 성공."""
    from pension_agent import config  # noqa: PLC0415 — 진단 출력에만 쓴다

    env.load()
    c = conf()
    public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    dotenv = config.DOTENV
    print(f".env            {dotenv} ({'있음' if dotenv.exists() else '없음 — 여기에 둬야 읽는다'})")
    print(f"PUBLIC_KEY      {_mask(public)}")
    print(f"SECRET_KEY      {_mask(secret)}")
    print(f"host            {c.host}")
    print(f"본문 전송       {'예' if c.capture_content else '아니오 (CAPTURE_CONTENT=0)'}")
    print(f"켜짐            {c.enabled}")

    if not c.enabled:
        if not (public and secret):
            print("\n❌ 키가 없어 꺼져 있습니다. src/.env 에 LANGFUSE_PUBLIC_KEY ·"
                  " LANGFUSE_SECRET_KEY 를 넣으십시오(저장소 루트가 아니라 src/ 입니다).")
        else:
            print("\n❌ LANGFUSE_ENABLED 로 꺼져 있습니다.")
        return 1

    print("\n이벤트 한 건 전송 중…")
    with trace("observability.selftest", input="ping", tags=["selftest"]) as tr:
        now = time.time()
        record_generation("observability.selftest", model="(selftest)",
                          input="ping", output="pong", start=now, end=now)
        tr.update(output="pong")
    flush(timeout=15.0)

    print(f"결과            {stats()}")
    if last_error():
        print(f"\n❌ 전송 실패 — {last_error()}")
        # 인증 문제와 «못 닿음»을 갈라 말한다. 프록시·터널 오류에도 403 이 섞여 오므로
        # 숫자만 보고 «키가 틀렸다»고 말하면 엉뚱한 곳을 뒤지게 된다 — HTTP 응답으로
        # 온 401·403 만 인증으로 읽는다.
        if last_error().startswith("HTTPError") and ("401" in last_error() or "403" in last_error()):
            print("   · 키가 이 host 의 것인지 확인하십시오. Langfuse Cloud 는 지역마다"
                  " 주소가 다릅니다 — EU https://cloud.langfuse.com ·"
                  " US https://us.cloud.langfuse.com. 다른 지역 키로는 401 이 납니다.")
            print("   · PUBLIC/SECRET 을 바꿔 넣지 않았는지도 함께 보십시오.")
        else:
            print(f"   · {c.host} 에 이 컴퓨터에서 닿는지 확인하십시오(사내망·프록시·방화벽).")
        return 1
    print("\n✅ 전송 성공 — Langfuse 의 Tracing 에서 «observability.selftest» 를 찾으십시오.")
    print("   안 보이면 대시보드의 프로젝트와 기간 필터를 확인하십시오.")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
