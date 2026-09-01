"""답변을 **텍스트 한 덩어리**로 펴는 곳 — 출처 표기 규약의 단일 출처.

화면이 여럿이다: CLI(`__main__.py`), 행내 플랫폼 API(`main.py`), 개발용 Streamlit
(`app.py`). 앞의 둘은 «글자만» 낼 수 있고, 그래서 같은 것을 두 번 조립하게 된다 —
그러다 한쪽만 관련도를 찍거나 한쪽만 문서명을 빠뜨리면, **같은 질문에 같은 답인데
근거가 다르게 보인다.** 신뢰 표시가 화면마다 다른 것은 신뢰 표시가 없는 것보다 나쁘다.

Streamlit 은 여기를 쓰지 않는다. 접기·열기와 링크를 가진 진짜 UI 라서 텍스트로 펴는 것이
손해다 — 다만 **무엇을 어떤 라벨로 가르는지**는 같아야 하므로 그 규약(아래 두 상수)만
공유한다.
"""

from __future__ import annotations

#: 답이 나온 재료 / 표현을 제한한 재료. 한 목록에 섞으면 질문과 무관한 고객 상태 가드가
#: 답의 근거처럼 보인다(nodes/plan.py::_sources 주석).
GROUND_HEADER = "─ 근거"
CAUTION_HEADER = "─ 이 고객 상담에서 지켜야 할 것 (근거 카드)"


def source_line(s: dict) -> str:
    """출처 한 건 — 두 줄.

    근거는 **원문 문서명**으로 읽어준다. 카드 id 는 역추적용으로 뒤에 남긴다 — id 만
    찍으면 사내 json 안의 코드가 근거처럼 보인다.

    관련도는 **있을 때만** 찍는다. 검색으로 오지 않은 재료(고객 브리핑·상담 기록·고객
    상태에 걸린 가드)에는 관련도라는 것이 없고, 그 자리에 None 을 찍으면 "관련도를 못 잰
    재료"가 "관련도가 없는 재료"로 읽힌다.
    """
    tail = f" · 관련도 {s['score']}" if s.get("score") is not None else ""
    return (f"   · {s.get('doc') or '출처 미상 — 확인 필요'}\n"
            f"     — {s.get('title') or ''} [{s['id']}{tail}]")


def sources_block(sources: list[dict] | None) -> str:
    """출처 전체. 앞에 빈 줄을 두고 시작한다(답변 본문과 붙지 않게).

    근거가 하나도 없으면 «없음»이라고 **적는다.** 블록을 통째로 빼면 "근거 없이 답했다"와
    "근거를 못 실었다"가 화면에서 같아 보인다.
    """
    items = sources or []
    ground = [s for s in items if s.get("role", "근거") == "근거"]
    caution = [s for s in items if s.get("role") == "주의"]

    lines = ["", GROUND_HEADER + ("" if ground else ": 없음")]
    lines += [source_line(s) for s in ground]
    if caution:
        lines += ["", CAUTION_HEADER]
        lines += [source_line(s) for s in caution]
    return "\n".join(lines)
