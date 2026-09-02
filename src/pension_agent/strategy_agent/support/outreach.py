"""⑨ 고객님께 안내해보세요 — 고객에게 실제로 나가는 콘텐츠.

여기 있는 자산만 LMS 발송 대상이 된다. 더미 콘텐츠의 발송은 텍스트 표시가 아니라
`pension_agent/tools.py::open_lms_screen()` 의 게이트가 막는다(CLAUDE.md 5번).

━━ 콘텐츠 DB 는 「무엇이 열려 있나」만 말한다 ━━
`data/assets.json` 의 세미나·이벤트 9건은 연금사업부가 확인해준 「IRP 세미나·이벤트 DB」에서
왔고, 그 DB 는 **추천대상·추천문구·우선순위를 저장하지 않는다.** 대신 콘텐츠마다 고객상태와
맞대볼 `keywords` 3개를 준다. 그래서 이 모듈이 하는 일은 셋이다.

  1. 종료된 콘텐츠를 뺀다(`end_date >= today()`)          — 규칙
  2. 이 고객의 문제상황에 걸린 것을 앞세운다(`KEYWORD_CONDS`) — 규칙
  3. LMS 문구의 **골격**을 조립한다(`lms_frame`)           — 규칙

«그중 어느 것을 안내할지»와 «문구 본문»만 LLM 몫이다(agent._select_db_sections ·
agent._write_lms_messages). REQUIREMENTS.md §15 의 'DB(Rule) + 선별(LLM)' 분업 그대로다.
"""

from __future__ import annotations

from datetime import date

from pension_agent.strategy_agent.customer import today as _today
from pension_agent.strategy_agent.support.matching import ASSETS

# ─────────────────────────────────────────────────────────────
# 콘텐츠 keywords → 요건(CONDS)
#
# DB 는 콘텐츠마다 keywords 3개를 주는데, 그중 **고객상태를 가리키는 것**만 요건과 맞물린다
# (나머지 둘은 주제어다 — "절세"·"TDF"·"리밸런싱"). 여기 적는 것은 새 판정 규칙이 아니라
# 이름의 대응이다: 임계값은 전부 customer.CONDS 가 이미 정한 것을 그대로 쓴다
# (situations.py 가 «새 판정 로직을 만들지 않는다»고 못 박은 것과 같은 이유).
#
# 대응이 없는 키워드는 여기 적지 않는다. `타기관IRP잔액보유`(EVT-002)가 그렇다 — 원장
# (customers.json)에 타기관 IRP 잔액 컬럼이 없어 성립 여부를 판정할 근거가 아예 없다.
# 지어내지 않고 비워 두면 그 콘텐츠는 관련도 0 으로 뒤에 서고, 다른 후보가 없을 때만
# 노출된다. 데이터딕셔너리에서 대응 컬럼이 확인되면 그때 한 줄 더한다.
# ─────────────────────────────────────────────────────────────
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

#: 광고성 문자의 고정 표기. **콘텐츠가 아니라 채널 규약이라 코드가 붙인다** — LLM 이 쓰게
#: 두면 수신거부 번호를 지어내거나 빠뜨릴 수 있고, 둘 다 발송하면 안 되는 문자가 된다.
#: 번호는 실제 회선이 아직 정해지지 않아 DB 문서의 자리표시자를 그대로 쓴다
#: (docs/DEMO_STATUS.md 가 보고한다).
AD_PREFIX = "(광고)"
OPT_OUT = "무료수신거부 080-XXX-XXXX"

_WEEKDAY = "월화수목금토일"


def schedule_text(a: dict) -> str:
    """일정을 사람이 읽는 표기로. 세미나는 개최 일시, 이벤트는 종료일이다.

    표기를 코드가 만드는 이유는 **검증 때문**이기도 하다. `verify` 는 날짜를 통짜 정규형으로
    대조하는데(pension_agent/verify.py) "9/8" 은 날짜 꼴로 읽히지 않아 `9`·`8` 두 토큰이
    된다. 원장에 `2026-09-08` 만 있으면 그 토큰들은 «재료 밖 수치»가 되어 **맞는 문구가
    통째로 버려진다**. 이 표기를 재료에 함께 실어 그 자리를 막는다.
    """
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
    """LMS 문구의 골격. 가운데 `body` 한 덩이만 고객마다 달라진다.

    골격을 코드가 잡는 이유는 셋이다 — ① 광고 표기·수신거부는 법정 표기라 빠지면 안 되고,
    ② URL 은 한 글자만 달라도 링크가 죽는데 LLM 이 옮겨 적으면 그 위험이 생기며,
    ③ 검증(`engine.verify`)을 **본문에만** 걸 수 있게 된다. 문구 전체를 LLM 이 쓰면
    수신거부 번호·URL 의 숫자까지 재료 대조를 통과해야 하고, 그 대조는 콘텐츠 DB 밖의
    값이라 늘 실패한다.
    """
    return "\n".join([f"{AD_PREFIX} {name} 고객님, KB국민은행입니다.", body.strip(),
                      f"▶ {url}", OPT_OUT])


def rule_body(a: dict) -> str:
    """LLM 이 본문을 쓰지 못했을 때 남는 규칙 본문. DB 값을 잇기만 하고 문장을 만들지 않는다.

    ⑤ 추천처럼 섹션을 통째로 비우지 않는 이유는, ⑨ 는 «무엇이 열려 있나»만으로도 직원에게
    쓸모가 있기 때문이다. 다만 이것이 LLM 산출이 아니라는 사실은 `facts["llm_skipped"]` 에
    남아 화면이 밝힌다(REQUIREMENTS.md 「LLM 미생성 표시」).
    """
    kind = "온라인 세미나" if a.get("content_type") == "세미나" else "이벤트"
    return f"{schedule_text(a)} '{a['name']}' {kind}를 안내드려요."


def conds_of(a: dict) -> list[str]:
    """이 콘텐츠가 가리키는 요건. keywords 에서 대응이 있는 것만 편다."""
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
        # 실제 콘텐츠 캘린더가 아니라 데모용으로 지어낸 일정인지. 지금 9건은 연금사업부가
        # 확인해준 DB 에서 와 dummy 가 아니다 — 게이트(open_lms_screen)가 막지 않는다.
        "dummy": bool(a.get("dummy")),
    }
    # 문구는 골격 + 본문이다. 본문은 평시에 LLM 이 다시 쓰고(agent._write_lms_messages),
    # 못 쓰면 이 규칙 본문이 남는다. 문구를 여기서 만들어 두는 이유는 화면·대화·발송 화면
    # 연계가 **같은 한 문구**를 보게 하기 위해서다(생성 경로를 둘로 만들지 않는다).
    row["lms_message"] = lms_frame(name, rule_body(a), a.get("url") or "")
    return row


def _open_assets(content_type: str, today: date) -> list[dict]:
    """종료되지 않은(end_date >= today) 해당 종류의 콘텐츠."""
    return [
        a for a in ASSETS
        if a.get("content_type") == content_type and a.get("end_date")
        and date.fromisoformat(a["end_date"]) >= today
    ]


def _outreach_order(situations: list[dict] | None):
    """정렬 키 — 이 고객의 문제상황에 걸린 콘텐츠를 먼저, 그다음 임박한 순.

    같은 기간에 여러 콘텐츠가 열려 있으면 '가장 임박한 것'만으로는 이 고객과 상관없는 안내가
    먼저 나온다. 관리 사유에 맞는 콘텐츠를 앞세우고, 그 안에서 임박 순으로 본다.

    맞대는 축은 **요건(CONDS)** 이다. 예전에는 콘텐츠에 세그먼트 id 를 직접 달아 뒀는데,
    콘텐츠 DB 가 세그먼트를 모르고 keywords 만 주므로(그게 맞다 — 콘텐츠 기획자가 세그먼트
    번호를 알 이유가 없다) 요건으로 내려서 맞댄다. 세그먼트도 결국 `conds` 조합으로
    성립하므로(situations.py) 같은 축이다.
    """
    wanted = {c for s in (situations or []) for c in (s.get("conds") or [])}

    def key(a: dict) -> tuple[int, str]:
        overlap = len(wanted & set(conds_of(a)))
        return (-overlap, a["start_date"])

    return key


def outreach_candidates(situations: list[dict] | None = None,
                        today: date | None = None,
                        name: str = "고객") -> dict[str, list[dict]]:
    """⑨ 안내 콘텐츠의 후보군 — 종료되지 않은 이벤트·세미나 전체를 관련도·임박 순으로 돌려준다.

    REQUIREMENTS.md §15 는 세미나/이벤트를 '콘텐츠 DB(Rule) + 선별(LLM)' 로 지정한다. 종료 콘텐츠 제외와
    정렬은 규칙(여기)이 하고, 그중 어느 것이 이 고객에게 맞는지는 LLM 이 고른다
    (agent._select_db_sections). LLM 이 없으면 next_event_and_seminar() 의 첫 건이 그대로 쓰인다.
    """
    # 기준은 원장 스냅샷(AS_OF)이 아니라 **오늘**이다 — 어제 끝난 세미나는 원장이 언제
    # 찍혔든 오늘 안내할 수 없다.
    today = today or _today()
    order = _outreach_order(situations)
    return {key: [_outreach_row(a, name) for a in sorted(_open_assets(content_type, today), key=order)]
            for content_type, key in (("이벤트", "event"), ("세미나", "seminar"))}


def next_event_and_seminar(situations: list[dict] | None = None,
                           today: date | None = None,
                           name: str = "고객") -> dict[str, dict | None]:
    """이 고객에게 안내할 이벤트 1개 + 세미나 1개(REQUIREMENTS.md ⑨ "고객님께 안내해보세요").

    content_type 별로 종료되지 않은 것(end_date >= today) 중 문제상황에 맞는 것을 먼저,
    같으면 start_date 가 빠른 것을 고른다 — 진행 중이거나 미래 일정인 콘텐츠를 우선하고
    종료된 콘텐츠는 노출하지 않는다는 요건을 그대로 코드로 옮긴 것. LLM 은 개입하지 않는다.
    """
    today = today or _today()
    order = _outreach_order(situations)

    def _pick(content_type: str) -> dict | None:
        candidates = _open_assets(content_type, today)
        return _outreach_row(min(candidates, key=order), name) if candidates else None

    return {"event": _pick("이벤트"), "seminar": _pick("세미나")}
