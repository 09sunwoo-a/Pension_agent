"""⑨ 고객님께 안내해보세요 — LMS 발송 대상 세미나·이벤트(더미 발송은 open_lms_screen 게이트가 차단).

규칙: 종료 콘텐츠 제외 · 문제상황 관련 콘텐츠 우선(`KEYWORD_CONDS`) · LMS 골격 조립(`lms_frame`).
선별과 문구 본문만 LLM.
"""

from __future__ import annotations

from datetime import date

from pension_agent.common.clock import today as _today
from pension_agent.tools.customer_solution.support.matching import ASSETS

# 콘텐츠 keyword(고객상태형만) → 요건 코드. 새 판정 아닌 이름 대응
# 원장 근거 없는 키워드 미등록(예: 타기관IRP잔액보유 — 원장 컬럼 없음)
KEYWORD_CONDS: dict[str, tuple[str, ...]] = {
    "연금개시임박": ("pen",),          # CONDS['pen'] 연금개시 요건충족 후 미개시
    "미운용현금자산": ("idl",),        # CONDS['idl'] 미운용 현금성자산
    "장기미운용": ("nch",),            # CONDS['nch'] 운용변경 없음(12개월+)
    "원리금편중": ("dep",),            # CONDS['dep'] 원리금보장상품 편중(80% 이상)
    "투자성향불일치": ("mis",),        # CONDS['mis'] 투자성향 불일치
    "예금만기예정": ("mat",),          # CONDS['mat'] 만기예금 보유
    "세액공제한도여유": ("tax", "add"),  # CONDS['tax'] 세액공제 활용 가능 · ['add'] 추가입금 여력
    "ISA만기": ("isa",),               # CONDS['isa'] ISA 만기자금 보유
    "디폴트옵션": ("nod",),            # CONDS['nod'] 디폴트옵션 미설정
}

#: 광고 문자 법정 표기(코드 부착, LLM 작성 금지). 수신거부 번호는 자리표시자
AD_PREFIX = "(광고)"
OPT_OUT = "무료수신거부 080-XXX-XXXX"

_WEEKDAY = "월화수목금토일"


def schedule_text(a: dict) -> str:
    """일정 표기(세미나 개최 일시 · 이벤트 종료일). 재료에도 실어야 함 — "9/8" 이 verify 에서 재료 밖 수치로 걸림."""
    d = date.fromisoformat(a["start_date"] if a.get("content_type") == "세미나" else a["end_date"])
    head = f"{d.month}/{d.day}"
    if a.get("content_type") != "세미나":
        return f"{head}까지"
    head += f"({_WEEKDAY[d.weekday()]})"
    if not a.get("start_time"):
        return head
    hh, _, mm = a["start_time"].partition(":")
    return f"{head} {int(hh)}시" + (f" {int(mm)}분" if mm and int(mm) else "")


def lms_frame(name: str, body: str, url: str) -> str:
    """LMS 골격(광고 표기·URL·수신거부는 코드 고정). `body` 만 고객별, verify 는 body 에만."""
    return "\n".join([f"{AD_PREFIX} {name} 고객님, KB국민은행입니다.", body.strip(),
                      f"▶ {url}", OPT_OUT])


def rule_body(a: dict) -> str:
    """LLM 미생성 시 규칙 본문 — DB 값 연결만."""
    kind = "온라인 세미나" if a.get("content_type") == "세미나" else "이벤트"
    return f"{schedule_text(a)} '{a['name']}' {kind}를 안내드려요."


def conds_of(a: dict) -> list[str]:
    """콘텐츠 keywords 의 대응 요건 코드."""
    out: list[str] = []
    for kw in a.get("keywords") or []:
        for cond in KEYWORD_CONDS.get(kw, ()):
            if cond not in out:
                out.append(cond)
    return out


def _outreach_row(a: dict, name: str = "고객") -> dict:
    row = {
        "id": a["id"], "name": a["name"], "content_type": a.get("content_type"),
        "organizer": a.get("organizer"), "start_date": a["start_date"], "end_date": a["end_date"],
        "schedule": schedule_text(a), "description": a.get("description"), "url": a.get("url"),
        "channel": a.get("channel"), "keywords": list(a.get("keywords") or []),
        "conds": conds_of(a),
        # 데모용 지어낸 일정 여부 — open_lms_screen 게이트 기준
        "dummy": bool(a.get("dummy")),
    }
    # 화면·대화·발송이 같은 문구를 보도록 여기서 생성(본문은 이후 LLM 이 교체)
    row["lms_message"] = lms_frame(name, rule_body(a), a.get("url") or "")
    return row


def _open_assets(content_type: str, today: date) -> list[dict]:
    """종료 전(end_date >= today) 해당 종류 콘텐츠."""
    return [
        a for a in ASSETS
        if a.get("content_type") == content_type and a.get("end_date")
        and date.fromisoformat(a["end_date"]) >= today
    ]


def _outreach_order(situations: list[dict] | None):
    """정렬 키 — 문제상황 요건과 겹치는 수 많은 순, 그다음 임박 순."""
    wanted = {c for s in (situations or []) for c in (s.get("conds") or [])}

    def key(a: dict) -> tuple[int, str]:
        overlap = len(wanted & set(conds_of(a)))
        return (-overlap, a["start_date"])

    return key


def outreach_candidates(situations: list[dict] | None = None,
                        today: date | None = None,
                        name: str = "고객") -> dict[str, list[dict]]:
    """⑨ 후보군 — 종료 전 이벤트·세미나 전체(관련도·임박 순, LLM 선별용)."""
    # 기준은 AS_OF 가 아니라 오늘
    today = today or _today()
    order = _outreach_order(situations)
    return {key: [_outreach_row(a, name) for a in sorted(_open_assets(content_type, today), key=order)]
            for content_type, key in (("이벤트", "event"), ("세미나", "seminar"))}


def relevant_outreach(situations: list[dict] | None = None,
                      today: date | None = None,
                      name: str = "고객") -> list[dict]:
    """문제상황 요건에 실제로 걸린 콘텐츠만(관련도 0 폴백 미포함). 없으면 빈 목록."""
    wanted = {c for s in (situations or []) for c in (s.get("conds") or [])}
    if not wanted:
        return []
    rows = outreach_candidates(situations, today, name)
    return [r for key in ("event", "seminar") for r in rows[key] if wanted & set(r["conds"])]


def next_event_and_seminar(situations: list[dict] | None = None,
                           today: date | None = None,
                           name: str = "고객") -> dict[str, dict | None]:
    """⑨ 이벤트 1개 + 세미나 1개 — 종료 전, 관련도·임박 순 첫 건."""
    today = today or _today()
    order = _outreach_order(situations)

    def _pick(content_type: str) -> dict | None:
        candidates = _open_assets(content_type, today)
        return _outreach_row(min(candidates, key=order), name) if candidates else None

    return {"event": _pick("이벤트"), "seminar": _pick("세미나")}
