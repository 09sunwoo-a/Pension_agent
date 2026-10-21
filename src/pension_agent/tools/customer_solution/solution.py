"""고객별 솔루션 — 계산 결과와 전체 브리핑 문서.

    facts(p)      계산 결과(engine.prepare). LLM 을 부르지 않는다
    document(p)   전체 브리핑 문서(마크다운). LLM 은 서술 부분에만 **1회** 쓴다
    peek(p)       이미 만든 서술이 있으면 돌려준다(없으면 None, LLM 을 부르지 않는다)

━━ 문서의 두 부분 ━━
  사실   태그 · 고객 정보 · IRP 계좌 현황 — 계산 결과를 코드가 그대로 표로 옮긴다
  서술   최근 금융상황 · 이번 상담에서 확인할 점 · 왜 지금 · 고객과 확인할 것 — LLM 1회

숫자·날짜를 LLM 이 다시 옮겨 쓰면 화면 안에서 값이 갈린다(상단 태그는 만기 D-10 인데
서술은 이미 지난 날짜를 만기로 적는 식). 그래서 사실 부분은 LLM 을 거치지 않고, 서술의
문장은 계산 결과에 없는 숫자·날짜가 있으면 그 문장을 버린다(`engine.verify`).

━━ 같은 고객이면 같은 서술 ━━
서술은 LLM 산출이라 부를 때마다 다르다. 전체 브리핑을 본 직원이 이어서 묻는 질문에
`customer` 도구가 같은 서술을 싣도록, 고객 프로파일·오늘 날짜를 키로 프로세스 안에
한 번만 만든다. 프로세스를 넘는 공유는 하지 않는다(파일 캐시 없음).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import threading
from collections import OrderedDict
from typing import Any

from pension_agent.common import llm, observability
from pension_agent.prompts.briefing import NARRATIVE_PROMPT, NARRATIVE_SYSTEM
from pension_agent.tools.customer_solution import engine
from pension_agent.tools.customer_solution import customer as C
from pension_agent.tools.customer_solution.customer import PERSONAS, Profile

log = logging.getLogger(__name__)

#: 서술 항목 — 대화로 고칠 수 있는 것은 이것뿐이다(nodes/correction.py). 계산 결과(값·상품·
#: 판정)는 대화로 고치지 않는다.
NARRATIVE_FIELDS = {
    "recent": "최근 금융상황",
    "focus": "이번 상담에서 확인할 점",
    "why_now": "왜 지금?",
    "confirm": "고객과 확인할 것",
}
EDITABLE_FIELDS = {
    **{f"narrative.{k}": v for k, v in NARRATIVE_FIELDS.items()},
    "card_benefit": "facts['items'][i]['card']['benefit'] — 전략 카드의 한 줄 혜택",
}

_LIST_FIELDS = ("recent", "confirm")
_MAX_LINES = {"recent": 3, "confirm": 4}

_CACHE_MAX = 128
_FACTS: OrderedDict[str, dict] = OrderedDict()
_DOCS: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()


def _key(p: Profile) -> str:
    """프로파일 내용 + 오늘 날짜. id 로 잡으면 같은 id 에서 파생시킨 합성 프로파일이
    서로의 결과를 받는다. 날짜는 잔여일수가 오늘 기준이라 키에 넣는다."""
    return json.dumps([dataclasses.asdict(p), str(C.today())],
                      ensure_ascii=False, sort_keys=True, default=str)


def _remember(store: OrderedDict, key: str, value: dict) -> dict:
    with _LOCK:
        kept = store.setdefault(key, value)
        store.move_to_end(key)
        while len(store) > _CACHE_MAX:
            store.popitem(last=False)
    return kept


def clear_cache() -> None:
    """캐시를 비운다 — 고객 원장을 다시 적재했거나 테스트가 LLM 스텁을 바꿀 때."""
    with _LOCK:
        _FACTS.clear()
        _DOCS.clear()


# ─────────────────────────────────────────────────────────────
# 계산 결과
# ─────────────────────────────────────────────────────────────

def facts(p: Profile) -> dict:
    """계산 결과의 복사본. 호출부가 고쳐도 다음 호출자에게 번지지 않는다."""
    key = _key(p)
    with _LOCK:
        hit = _FACTS.get(key)
    if hit is None:
        hit = _remember(_FACTS, key, engine.prepare(p))
    return copy.deepcopy(hit)


def tier(f: dict) -> str:
    """행내 전략(strategies.json)이 매칭됐는지. 매칭이 없으면 서술도 실행 항목 없이 쓴다."""
    return "행내전략" if f.get("items") else "미매칭"


# ─────────────────────────────────────────────────────────────
# 서술 (LLM 1회)
# ─────────────────────────────────────────────────────────────

def _cond_names(f: dict) -> list[str]:
    """성립 요건의 이름만. 코드(`isa`·`tax`)는 직원에게 뜻이 없고, 재료에 실으면 답변에 나온다."""
    return [c.split(":", 1)[1] if ":" in c else c for c in f.get("conditions") or []]


def _material(p: Profile, f: dict) -> dict:
    """서술 프롬프트에 싣는 재료. 전부 계산 결과에서 그대로 옮긴다."""
    return {
        "기준일": {"원장 기준일": str(C.AS_OF), "오늘": str(C.today())},
        "고객": f["customer"],
        "계좌": {k: v for k, v in f["briefing"].items() if k != "source"},
        "계좌 상태": f.get("account_state") or {},
        "관리 사유": _cond_names(f),
        "문제상황": [s["title"] for s in f.get("problem_situations") or []],
        "오늘 제안할 실행 항목": [{
            "제목": it["title"], "내용": it["clause"], "근거": it["evidence"],
            "금액": it["amount"], "상품": it["products"], "시급성": it["urgency"],
        } for it in f.get("items") or []],
        "지난 상담": f.get("consult_history") or [],
    }


def _extra(f: dict) -> list[str]:
    """검증 허용에 더할 텍스트 — 프롬프트로 보여준 값 중 facts 스키마 밖인 것.
    보여준 값은 인용할 수 있어야 한다(옮겨 적은 문장을 코드가 버리지 않게)."""
    out = [f"{k} {v}" for k, v in (f.get("account_state") or {}).items()]
    out += [str(x) for x in f.get("consult_history") or []]
    out += [s["title"] for s in f.get("problem_situations") or []]
    out += [str(C.AS_OF), str(C.today())]
    return out


def _lines(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(x).strip().strip("*").strip() for x in value if str(x).strip()]


def _narrative(p: Profile, f: dict) -> tuple[dict | None, str, list[str]]:
    """서술 네 항목을 한 번에 받는다. (서술, 사유, 버린 문장 사유)."""
    if not llm.available():
        return None, "LLM 미설정", []
    try:
        raw = llm.generate(
            NARRATIVE_PROMPT.format(material=json.dumps(_material(p, f), ensure_ascii=False, indent=1)),
            system=NARRATIVE_SYSTEM, max_tokens=900, name="solution.narrative")
    except Exception as exc:  # 게이트웨이 장애·타임아웃 등
        return None, f"LLM 호출 실패 ({type(exc).__name__})", []
    data = llm.json_object(raw)
    if not isinstance(data, dict):
        return None, "LLM 응답 파싱 실패", []

    extra = _extra(f)
    out: dict[str, Any] = {}
    rejected: list[str] = []
    for key in NARRATIVE_FIELDS:
        kept = []
        for line in _lines(data.get(key)):
            ok, bad = engine.verify(line, f, extra=extra)
            if ok:
                kept.append(line)
            else:
                rejected += [f"{NARRATIVE_FIELDS[key]}: {b}" for b in bad]
        if key in _LIST_FIELDS:
            out[key] = kept[:_MAX_LINES[key]]
        else:
            out[key] = " ".join(kept)
    if not any(out.values()):
        return None, "서술이 전부 계산 결과 밖의 값을 포함함", rejected
    return out, "", rejected


# ─────────────────────────────────────────────────────────────
# 문서
# ─────────────────────────────────────────────────────────────

def _table(rows: list[tuple[str, Any]]) -> str:
    body = [f"| {k} | {v} |" for k, v in rows if v not in (None, "")]
    return "\n".join(["| 항목 | 값 |", "|---|---|", *body]) if body else ""


def render(p: Profile, f: dict, narrative: dict | None, reason: str = "") -> str:
    """전체 브리핑 마크다운. 사실 부분은 계산 결과를 그대로 옮긴다."""
    cust, brief, state = f["customer"], f["briefing"], f.get("account_state") or {}
    out = [f"## {p.nm} 고객님"]
    tags = _cond_names(f)
    if tags:
        out.append(" ".join(f"`{t}`" for t in tags))
    out.append(f"원장 기준일 {C.AS_OF} · 오늘 {C.today()}")

    out += ["### 고객 정보", _table([
        ("나이", f"{cust.get('연령')}세" if cust.get("연령") is not None else None),
        ("스타클럽 등급", cust.get("스타클럽등급")),
        ("투자성향", cust.get("투자성향")),
        ("거래채널", cust.get("거래채널")),
        ("IRP 가입일", state.get("IRP_가입일")),
        ("디폴트옵션", state.get("디폴트옵션")),
        ("연금개시", state.get("연금개시")),
        ("최근 접촉", cust.get("최근접촉")),
    ])]

    out += ["### IRP 계좌 현황", _table([
        ("평가금액", cust.get("평가금액")),
        ("운용 구성", brief.get("운용현황(3분류)")),
        ("자산군별", brief.get("자산군별")),
        ("수익률", brief.get("운용수익률") or cust.get("수익률")),
        ("만기 도래", brief.get("만기도래")),
        ("세액공제 잔여한도", state.get("세액공제_잔여한도")),
        ("당해 납입액", brief.get("당해_납입액")),
    ])]

    out.append("### AI 분석")
    if narrative:
        if narrative.get("recent"):
            out.append(f"**{p.nm} 고객님의 최근 금융상황**")
            out.append("\n".join(f"{i}. {x}" for i, x in enumerate(narrative["recent"], 1)))
        if narrative.get("focus"):
            out += ["**이번 상담에서 확인할 점**", narrative["focus"]]
        if narrative.get("why_now"):
            out.append(f"> **왜 지금?** {narrative['why_now']}")
        if narrative.get("confirm"):
            out.append("**고객과 확인**")
            out.append("\n".join(f"- [ ] {x}" for x in narrative["confirm"]))
    else:
        if not f.get("items"):
            out.append(engine.compose_rule(f))
        out.append(f"AI 분석을 만들지 못했어요({reason or '사유 미상'}).")
    return "\n\n".join(x for x in out if x)


def document(p: Profile) -> dict[str, Any]:
    """전체 브리핑. 같은 고객·같은 날이면 한 번만 만든다(머리말).

    반환: {"markdown", "narrative", "tier", "reason", "rejected"}
    """
    key = _key(p)
    with _LOCK:
        hit = _DOCS.get(key)
    if hit is None:
        f = facts(p)
        who = observability.customer_ref(p.id, p.nm, C.conditions(p))
        with observability.trace("solution.document", input={"customer": p.nm},
                                 user_id=who["user_id"], metadata=who["metadata"],
                                 tags=["briefing", *who["tags"]]) as span:
            narrative, reason, rejected = _narrative(p, f)
            doc = {"markdown": render(p, f, narrative, reason), "narrative": narrative,
                   "tier": tier(f), "reason": reason, "rejected": rejected}
            span.update(output={"narrative": narrative}, reason=reason or None,
                        rejected=rejected[:5] or None)
            observability.step("briefing", p.id, tier=doc["tier"],
                               narrative=bool(narrative), reason=reason or None,
                               level=logging.WARNING if not narrative else logging.INFO)
        # 서술을 못 만든 문서는 남기지 않는다 — 다음 호출이 다시 시도한다.
        hit = _remember(_DOCS, key, doc) if narrative else doc
    return copy.deepcopy(hit)


def peek(p: Profile) -> dict | None:
    """이미 만든 서술. 없으면 None — LLM 을 부르지 않는다(부분 질문이 쓰는 자리)."""
    with _LOCK:
        hit = _DOCS.get(_key(p))
    return copy.deepcopy(hit["narrative"]) if hit else None


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    name = " ".join(sys.argv[1:]).strip()
    targets = [p for p in PERSONAS if not name or p.nm == name]
    if not targets:
        print(f"'{name}' 에 해당하는 고객이 없습니다. 대상: {', '.join(p.nm for p in PERSONAS)}")
        raise SystemExit(1)
    for p in targets:
        print(document(p)["markdown"], end="\n\n")
