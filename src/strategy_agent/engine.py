"""결정적 처리 계층 — 전략 제안 문장 생성에 필요한 확정 사실(facts)의 산출.

본 모듈은 LLM 을 호출하지 않는다. 전략 제안 문장의 작성은 agent.py 의 LLM 단계가
담당하며, 본 모듈은 그 입력이 되는 사실과 제약을 확정하는 역할만 수행한다.

    prepare(profile)          요건 판정 → 후보 소집 → 적합성 게이트 → 자금 배분
                              → 기대효과 산출 → 상위 N 선정. 반환값이 LLM 입력 재료.
    compose_rule(facts)       규칙 기반 문장 합성. LLM 미가용·검증 실패 시 폴백.
    verify(sentence, facts)   LLM 문장이 재료 범위를 벗어났는지 검사.
    validate()                정의 자체의 무결성 검사. 지식베이스와의 근거 정합성을 포함한다.

본 계층이 LLM 이 아닌 코드로 구현되어야 하는 근거는 다음과 같다.
    · 요건 판정·금액·기대효과는 산출식 기반 항목으로 재현성이 요구된다.
    · 적합성 판정(위험등급·최소가입금액·거래채널·투자한도)은 규정 사항이며,
      오판은 불완전판매에 해당한다.
    · 자금 풀 배분은 복수 전략이 동일 재원을 중복 사용하는 것을 차단하기 위한 제약이다.

산출물 감사 반영 사항은 README 2절에 정리되어 있다. 특히 다음 두 가지는 설계상의 전제이다.
    · 적합성 게이트 중 금액에 의존하는 항목(최소가입금액·위험자산 한도 여력)은 자금 배분이
      끝난 뒤에 판정한다. 적립금 기준으로 판정하면 실제 투입액이 최소가입금액에 미달하는
      상품을 제안하게 된다.
    · 근거로 인용한 문서가 실제로 그 주제를 다루는지는 validate() 가 지식베이스를 열어
      대조한다. sources 필드의 존재 여부만 검사하면 근거 오인용이 검출되지 않는다.

검증 실행: python engine.py
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# umbrella 를 import 경로에 올려 공용 모듈(common)을 쓴다.
_UMBRELLA = str(Path(__file__).resolve().parent.parent)
if _UMBRELLA not in sys.path:
    sys.path.insert(0, _UMBRELLA)

from common.kb_base import check_broken_refs, check_duplicate_ids  # noqa: E402
from common.store import Store  # noqa: E402
from common.session_store import summarize_for_briefing  # noqa: E402
from common.verify import verify as _verify  # noqa: E402

from customer import (
    CONDS,
    PREF,
    PRIO,
    RISK,
    RISK_ASSET_CAP_PCT,
    TODAY,
    Profile,
    churn,
    conditions,
    days_to_year_end,
)

DIR = Path(__file__).resolve().parent
KB_ROOT = DIR.parent / "consult_agent"


# 통합 스토어 — 자체 data(운영 데이터)와 consult_agent/data(사실·화법)를 함께 적재한다. 모든
# 데이터는 단일 레코드 형태이며, fields_of(kind) 가 기존 flat dict 뷰({id, **fields})를 그대로 준다.
_STORE = Store([DIR / "data", KB_ROOT / "data"])

PRODUCTS: list[dict] = _STORE.fields_of("product")
SPECS: list[dict] = _STORE.fields_of("strategy")
CAPS: dict[str, dict] = {r["id"]: r for r in _STORE.fields_of("capability")}
ASSETS: list[dict] = _STORE.fields_of("asset")
BASELINES: dict[str, dict] = {r["id"]: r for r in _STORE.fields_of("baseline")}
TOP_HOLDINGS: list[dict] = _STORE.fields_of("top_holding")
PORTFOLIOS: list[dict] = _STORE.fields_of("portfolio")

# 시스템 생성 조건부 전략 — 요건(when)이 아니라 엔진 게이트 결과의 판정으로만 발동한다.
# strategies.json 플레이북은 원천 소스·규정에 근거한 전략만 담으므로, 근거가 아니라 게이트
# 결과에 대한 '추론'인 전략(confidence=추정)은 별도 자산 파일로 분리해 관리한다. 데이터는
# 코드가 아니라 data/ 의 JSON 에 둔다는 원칙에 따라 engine 에 리터럴로 박지 않는다.
#   · st.risk_reassess: 적합성 게이트가 실적배당 상품을 전량 차단했을 때만 발동(_perf_branch_blocked).
SYSTEM_STRATEGIES: list[dict] = _STORE.fields_of("system_strategy")

# 조회용 통합 인덱스. 플레이북(SPECS)과 시스템 전략(SYSTEM_STRATEGIES)을 모두 담는다.
BY_ID = {s["id"]: s for s in SPECS + SYSTEM_STRATEGIES}

MIN_ALLOC = 1_000_000  # 배분액 하한. 미만인 전략은 실행 실익이 없는 것으로 보아 제외한다.
TOP_N = 3

# 대안 제안(메인 문장 밖 전략)을 산출물에 노출하는 최대 개수. 수익률 개선폭 순으로 상위만.
ALT_N = 3

# 수익률 개선 효과의 정성 등급 경계(%p, 하한 포함). 원화 기대효과는 원금 이동 가정에 의존해
# 근거가 불명확하므로 산출물에는 수치 대신 이 등급으로만 표기한다. 큰 하한부터 판정한다.
EFFECT_BANDS = ((1.0, "큼"), (0.5, "보통"), (0.0, "작음"))


def _load_kb_index() -> tuple[dict[str, str], dict[str, tuple[str, Any]]]:
    """지식베이스를 색인한다. 출처를 코드용 doc_id 가 아니라 원본 문서명으로 표기하기 위함이다.

    · DOC_TITLES  doc_id → 원본 문서 제목(meta.title)
    · FACT_INDEX  fact id → (doc_id, page). fact 단위 인용도 그 사실이 실린 원본 문서로 묶는다.

    경로가 없으면 빈 맵을 반환해 doc_id 로 폴백한다(단독 실행 가능)."""
    titles = _STORE.doc_titles()
    facts = {r["id"]: (r.get("_doc_id"), (r.get("fields") or {}).get("page"))
             for r in _STORE.records("fact") if r.get("id")}
    return titles, facts


DOC_TITLES, FACT_INDEX = _load_kb_index()

# 퇴직연금 예금자보호 한도(원). 일반 예금과 별도로 1인당 이 금액까지 보호된다.
PROTECTION_LIMIT = 50_000_000

# 상품에 부착 가능한 동작 명사. 절은 이 중 하나 또는 CLAUSE_ENDINGS 로 종결해야 한다.
VERBS = ("매수", "재예치", "예치", "전환", "납입", "재배분", "설정", "편입", "배분")

# 상품 슬롯 없이 종결되는 절의 허용 어미.
CLAUSE_ENDINGS = ("접촉", "발송", "조회", "확인", "안내", "권유", "재진단",
                  "등록", "재구성", "정리", "점검", "조정", "축소")

# actor='고객' 인 절에 부착하는 어미. 산출 문장은 직원이 읽는 문서이므로,
# 고객만 실행할 수 있는 동작은 직원의 행동(제안·안내)으로 감싼다.
ACTOR_SUFFIX = {"운용": "하도록 제안", "납입": "하도록 제안", "설정": "하도록 안내"}

# 자산 구성비(Profile.port) 인덱스별 라벨. 보유 현황 briefing 렌더링에 사용한다.
PORT_LABELS = ("예금", "채권형", "TDF·MP", "섹터ETF")

# 보유 현황 briefing 의 근거. 가이드 p02 '상품 보유 현황 확인'(MyStar 조회)을 코드로 대체한다.
# 조회 데이터가 이미 Profile 에 들어와 있으므로, 조회를 전략 문장의 지시로 남기지 않는다.
BRIEFING_SOURCE = "guide01_yield_mgmt.p02"

# 시스템이 이미 보유한 데이터의 조회·확인 지시어. 절이 아니라 briefing(코드)으로 표현한다.
LOOKUP_MARKERS = ("MyStar", "단말", "조회 화면")

# 데이터로 확정되지 않은 시한(마감) 표현. 절에 넣지 않는다. 시급성은 urgency 필드와 실행
# 순서로 표현하며, 'D-{matDD}' 처럼 Profile 값으로 산출되는 기한만 절에 담긴다.
TIME_PRESSURE_MARKERS = ("금주", "이번 주", "당장", "오늘 중", "지금 바로", "즉시")


# ─────────────────────────────────────────────────────────────
# 표기 유틸
# ─────────────────────────────────────────────────────────────

def won(v: float) -> str:
    v = int(round(v))
    eok, rest = divmod(v, 100_000_000)
    man = rest // 10_000
    if eok and man:
        return f"{eok}억 {man:,}만원"
    if eok:
        return f"{eok}억원"
    if man:
        return f"{man:,}만원"
    return f"{v:,}원"


def _page_num(raw: str) -> int | None:
    """페이지 표기(pNN)를 정수로 정규화한다. 숫자 페이지가 아니면 None."""
    m = re.match(r"p0*(\d+)$", raw)
    return int(m.group(1)) if m else None


def format_sources(sources: list[str]) -> list[str]:
    """근거 id 목록을 원본 문서명 + 페이지 표기로 변환한다.

    출처는 코드용 파일명(doc_id)이 아니라 원본 문서 그대로의 제목으로 노출한다. doc_id.pNN
    페이지 인용과 fact.* 사실 인용을 모두 그 사실이 실린 원본 문서로 묶는다. 제목을 찾지
    못하면 doc_id 로 폴백한다. 예: 'guide01_yield_mgmt.p01/p05'
    → '개인형IRP 고객관리 가이드 Series 1 — IRP 수익률 관리 (p1/p5)'.
    """
    pages: dict[str, set[int]] = {}
    order: list[str] = []
    for sid in sources:
        if sid in FACT_INDEX:
            doc, page = FACT_INDEX[sid]
        else:
            doc, _, raw = sid.partition(".")
            page = _page_num(raw) if raw else None
        if doc not in pages:
            pages[doc] = set()
            order.append(doc)
        if isinstance(page, int):
            pages[doc].add(page)
    out = []
    for doc in order:
        title = DOC_TITLES.get(doc, doc)
        pg = "/".join(f"p{n}" for n in sorted(pages[doc]))
        out.append(f"{title} ({pg})" if pg else title)
    return out


def _has_final(ch: str) -> bool | None:
    """한글 음절의 받침 유무. 한글이 아니면 None 을 반환한다."""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return None


def _ro(s: str) -> str:
    """조사 '로/으로' 를 부착한다. 받침이 없거나 'ㄹ' 이면 '로' 이다."""
    ch = s.strip()[-1]
    if "가" <= ch <= "힣":
        return s + ("로" if (ord(ch) - 0xAC00) % 28 in (0, 8) else "으로")
    return s + "로"


def _eul(s: str) -> str:
    """조사 '을/를' 을 부착한다."""
    fin = _has_final(s.strip()[-1])
    return s + ("을" if fin else "를")


def _pname(r: dict) -> str:
    return f"{r['name']}(연 {r['rate']:.2f}%)" if r.get("rate") is not None else r["name"]


def _ret_of(r: dict) -> float | None:
    """상품 수익률. 실적배당은 기대수익률, 원리금보장은 확정금리를 사용한다."""
    v = r.get("exp_return")
    return v if v is not None else r.get("rate")


class _Ctx(dict):
    """미정의 슬롯을 예외 대신 원문으로 남긴다. 정의 누락을 출력물에서 식별하기 위함이다."""

    def __missing__(self, k: str) -> str:
        return "{" + k + "}"


# ─────────────────────────────────────────────────────────────
# 상품 질의
#
# 표 형태 자료는 검색 단위(카드)가 아니라 질의 단위(행)로 적재한다. 조건에 해당하는
# 행 전체를 대상으로 필터·정렬을 수행하며 상한(top-k)을 두지 않는다. 카드 단위 의미
# 검색은 단건 조회는 가능하나 집합 질의(전부·개수·정렬)를 처리하지 못한다.
# ─────────────────────────────────────────────────────────────

def query_products(rows: list[dict], where: dict | None = None) -> list[dict]:
    out = [r for r in rows if r.get("status", "판매중") == "판매중"]
    for k, v in (where or {}).items():
        out = [r for r in out if (r.get(k) in v if isinstance(v, list) else r.get(k) == v)]
    return out


def gate_static(row: dict, p: Profile, cap: str) -> tuple[bool, str]:
    """금액과 무관한 적합성 게이트. 위험등급 상한과 거래채널을 판정한다."""
    if RISK.index(row["risk"]) > RISK.index(cap):
        return False, f"상품 위험등급 {row['risk']} > 허용 상한 {cap}"
    if p.nonface and not row.get("nonface", True):
        return False, "비대면 채널 가입 불가 (고객 거래채널: 비대면)"
    return True, ""


def gate_amount(row: dict, p: Profile, amount: int, headroom: int | None) -> tuple[bool, str]:
    """실제 투입액에 의존하는 적합성 게이트.

    최소가입금액은 적립금이 아니라 해당 전략에 배분된 금액과 대조해야 한다. 적립금 기준으로
    판정하면 4,680만원을 배분하면서 최소 5,000만원 상품을 제안하는 오류가 발생한다.
    """
    ref = amount if amount > 0 else p.bal
    if row.get("min_amount", 0) > ref:
        return False, f"최소 가입금액 {won(row['min_amount'])} > 배분액 {won(ref)}"
    # 금액이 특정되지 않는 전략(예: 디폴트옵션 설정)도 여력이 없으면 위험자산 상품을
    # 지정할 수 없다. 이 경우 최소 단위를 가정해 한도 소진 여부만 판정한다.
    need = amount if amount > 0 else 1
    if (
        headroom is not None
        and row.get("risk_asset")
        and not row.get("risk_asset_exempt")
        and need > headroom
    ):
        if amount > 0:
            return False, f"위험자산 한도 여력 {won(headroom)} < 배분액 {won(amount)}"
        return False, f"위험자산 한도 여력 {won(headroom)} — 신규 위험자산 편입 불가"
    return True, ""


def _branch_defs(spec: dict) -> dict[str, dict]:
    if "product_branch" in spec:
        return dict(spec["product_branch"])
    if "product" in spec:
        return {"단일": spec["product"]}
    return {}


def static_candidates(p: Profile, spec: dict, cap: str, blocked: dict) -> dict[str, list[dict]]:
    """금액 무관 게이트까지 통과한 분기별 후보 상품 목록."""
    out: dict[str, list[dict]] = {}
    for label, q in _branch_defs(spec).items():
        rows = []
        for r in query_products(PRODUCTS, q.get("where")):
            ok, why = gate_static(r, p, cap)
            if ok:
                rows.append(r)
            else:
                blocked.setdefault(f"{r['id']}|{why}", f"{r['name']} — {why}")
        out[label] = rows
    return out


def _best(p: Profile, spec: dict, q: dict, rows: list[dict]) -> dict:
    """성향 선호를 우선 적용한 뒤 수익률 순으로 1건을 선정한다.

    정렬 기준은 원시 필드가 아니라 _ret_of() 이다. 원시 필드로 정렬하면 확정금리만 보유한
    원리금보장 상품이 exp_return 부재로 최하위로 밀린다.
    """
    pref = PREF.get(p.rk) if spec.get("apply_preference", True) else None
    narrowed = [r for r in rows if RISK.index(r["risk"]) <= RISK.index(pref)] if pref else []
    pool = list(narrowed or rows)
    desc = str(q.get("order", "-exp_return")).startswith("-")
    pool.sort(key=lambda r: (_ret_of(r) if _ret_of(r) is not None else -1e9), reverse=desc)
    return pool[0]


def finalize_products(
    p: Profile, spec: dict, static: dict[str, list[dict]],
    amount: int, headroom: int | None, blocked: dict,
) -> tuple[dict[str, dict], list[str]]:
    """배분액을 반영해 분기별 상품을 확정한다. 후보 0건인 분기는 제거된다."""
    got: dict[str, dict] = {}
    dead: list[str] = []
    base = None
    if spec.get("require_return_over_base") and (spec.get("impact") or {}).get("base_ref"):
        base = BASELINES[spec["impact"]["base_ref"]]["rate"]

    defs = _branch_defs(spec)
    for label, rows in static.items():
        passed = []
        for r in rows:
            ok, why = gate_amount(r, p, amount, headroom)
            if not ok:
                blocked.setdefault(f"{r['id']}|{why}", f"{r['name']} — {why}")
                continue
            ret = _ret_of(r)
            if base is not None and (ret is None or ret <= base):
                why = f"기준선 {base:.2f}% 이하 수익률 — 전환 실익 없음"
                blocked.setdefault(f"{r['id']}|{why}", f"{r['name']} — {why}")
                continue
            passed.append(r)
        if not passed:
            dead.append(f"{spec['title']} · {label} 분기 — 조건 충족 상품 0건")
            continue
        got[label] = _best(p, spec, defs[label], passed)
    return got, dead


# ─────────────────────────────────────────────────────────────
# 금액 · 시급성 · 기대효과 · 점수
# ─────────────────────────────────────────────────────────────

def _amount(p: Profile, spec: dict) -> tuple[int, dict]:
    """전략별 대상 금액(원)과 절 렌더링용 파생값을 산출한다."""
    a = spec.get("amount")
    if not a:
        return 0, {}
    k = a["kind"]
    if k == "mat":
        return p.matAmt, {}
    if k == "room":
        v = p.room * 10_000
        if a.get("credit_limit"):
            v = min(v, max(0, a["credit_limit"] - p.pension_paid_ytd))
        if a.get("contribution_limit"):
            v = min(v, max(0, a["contribution_limit"] - p.pension_paid_ytd))
        return v, {}
    if k == "dep_shift":
        move = max(0, min(a["cap_pct"], p.port[0] - a["floor_pct"]))
        return round(p.bal * move / 100), {"move_pct": move}
    if k == "sector_excess":
        ex = max(0, p.port[3] - a["target"])
        return round(p.bal * ex / 100), {"sec_excess": ex, "target": a["target"]}
    if k == "risk_excess":
        t = a["targets"].get(p.rk, 30)
        return round(p.bal * max(0, p.risk_asset - t) / 100), {"target": t}
    if k == "risk_over_limit":
        ex = max(0, p.risk_asset - RISK_ASSET_CAP_PCT)
        return round(p.bal * ex / 100), {"target": RISK_ASSET_CAP_PCT}
    return 0, {}


def _urgency(p: Profile, spec: dict) -> float:
    """시급성(0~10). 기한이 정의된 전략은 잔여일수에 따라 감쇠한다."""
    u = spec.get("urgency", 0)
    if isinstance(u, (int, float)):
        return float(u)
    if u["kind"] == "deadline":
        v = getattr(p, u["field"]) or 0
    elif u["kind"] == "year_end":
        v = days_to_year_end()
    else:
        return 0.0
    return round(max(u["lo"], min(u["hi"], 10 - v / u["div"])), 1)


def _impact(
    p: Profile, spec: dict, amount: int, products: dict,
) -> tuple[int | None, tuple[int, int] | None, str]:
    """연간 기대효과(원), 미확정 시의 범위, 산식을 반환한다.

    분기가 둘 이상이면 실적배당의 기대수익률과 원리금보장의 확정금리가 함께 존재한다.
    성격이 다른 두 수치를 하나로 합치지 않고, 고객이 분기를 선택하기 전까지는 범위로만
    제시하며 기대효과 합계에 산입하지 않는다.
    """
    im = spec.get("impact")
    if not im or amount <= 0:
        return None, None, ""
    if im["kind"] == "tax_credit":
        rate = p.tax_credit_rate
        return round(amount * rate), None, f"{won(amount)} × {rate:.1%}"

    base = BASELINES[im["base_ref"]]["rate"]
    vals = []
    for label, r in products.items():
        ret = _ret_of(r)
        if ret is not None:
            vals.append((label, round(amount * (ret - base) / 100), ret))
    if not vals:
        return None, None, ""
    if len(vals) == 1:
        return vals[0][1], None, f"{won(amount)} × ({vals[0][2]:.2f}% − {base:.2f}%)"
    lo = min(v[1] for v in vals)
    hi = max(v[1] for v in vals)
    detail = " / ".join(f"{lb} {rt:.2f}%" for lb, _, rt in vals)
    return None, (lo, hi), f"{won(amount)} × (분기별 수익률 − {base:.2f}%) — {detail}"


def _score(urg: float, impact: int | None, rng: tuple[int, int] | None, mandatory: bool) -> float:
    """선정 점수. 고정 우선순위가 아니라 시급성·기대효과·필수여부의 합으로 산출한다.

    기대효과가 범위로만 확정된 경우 하한을 사용해 보수적으로 채점한다.
    """
    s = urg * 10
    v = impact if impact is not None else (rng[0] if rng else None)
    if v and v > 0:
        s += min(40.0, max(0.0, 10 * (math.log10(v) - 4)))  # 10만원=0점, 1천만원=20점
    if mandatory:
        s += 100
    return round(s, 1)


def _yield_delta(spec: dict, products: dict) -> float | None:
    """제안 상품의 수익률 개선폭(%p) = max(상품 수익률 − 기준선).

    원리금보장은 확정금리, 실적배당은 기대수익률을 사용한다. 세액공제처럼 수익률 축이 없거나
    기준선이 없는 전략은 None 을 반환한다. 대안 제안의 정렬 기준이자 정성 등급의 산출값이다.
    """
    im = spec.get("impact") or {}
    if not im or im.get("kind") == "tax_credit" or not im.get("base_ref"):
        return None
    base = BASELINES[im["base_ref"]]["rate"]
    deltas = [_ret_of(r) - base for r in products.values() if _ret_of(r) is not None]
    return max(deltas) if deltas else None


def _effect_grade(delta: float | None) -> str:
    """수익률 개선폭(%p)을 정성 등급으로 환산한다. 수익률 축이 없으면 '—'."""
    if delta is None:
        return "—"
    for thr, label in EFFECT_BANDS:
        if delta >= thr:
            return label
    return EFFECT_BANDS[-1][1]


def effect_label(grade: str) -> str:
    """수익률 개선폭 등급을 사람이 읽는 라벨로 바꾼다.

    '큼/보통/작음' 은 상품금리와 기준선의 차(개선폭) 등급이고, '—' 는 수익률 축이 없는
    전략(접촉·재진단·디폴트옵션 등)이다. 산출물에는 등급 문자 대신 이 라벨을 노출한다.
    """
    return {
        "큼": "수익 개선폭 큼",
        "보통": "수익 개선폭 보통",
        "작음": "수익 개선폭 작음",
        "—": "수익 개선 해당 없음",
    }.get(grade, grade)


def _value_tag(spec: dict, effect_grade: str) -> str:
    """카드용 핵심가치 라벨. 전략의 성격(유형·위험 조정 여부·기대효과)에서 분류한다.

    _effect_grade 와 같은 성격의 파생 라벨이다. 개별 전략에 문구를 하드코딩하지 않고
    구조적 신호에서 도출하므로, 신규 전략에도 규칙만으로 적용된다.
    """
    kind = spec.get("kind")
    if kind == "접촉":
        return "관계 관리"
    if spec.get("reduces_risk"):
        return "위험 조정"
    if (spec.get("impact") or {}).get("kind") == "tax_credit":
        return "세제 혜택"
    if kind == "설정":
        return "운용 공백 차단"
    if kind == "납입":
        return "노후 자산 적립"
    if effect_grade in ("큼", "보통", "작음"):
        return "수익률 개선"
    return "운용 점검"


def _card(spec: dict, products: dict[str, str], amount: str | None,
          effect_grade: str, action: str, benefit: str) -> dict:
    """전략 제안 카드. 긴 지시 문장 대신 헤드라인·핵심가치·추천상품/대상·한 줄 혜택으로
    압축한다. 상품·금액은 확정된 재료에서만 채워 환각을 차단하고, 분기별 상세는 clause 로 남긴다.

    상품이 있는 전략은 '추천 상품 · 대상' 으로, 상품이 없는 접촉·안내 전략은 행동(action)으로
    구체를 채운다. benefit 은 '왜 이로운가' 한 문장으로, 판단근거(evidence)의 수치 나열과 구분한다.
    """
    product = ", ".join(products.values())
    return {
        "headline": spec["title"],
        "tag": _value_tag(spec, effect_grade),
        "product": product,
        "target": amount or "",
        "action": "" if product else action,
        "benefit": benefit,
    }


# ─────────────────────────────────────────────────────────────
# 렌더링 보조
# ─────────────────────────────────────────────────────────────

def customer_facing_asset(branch: str | None = None) -> dict | None:
    """고객 발송이 승인된 자료 1건. 미등록 시 None 을 반환한다.

    content_type 이 있는 레코드(이벤트·세미나 — 요건정의서 ⑨, next_event_and_seminar() 가
    다룬다)는 여기서 제외한다 — 같은 asset kind·같은 customer_facing 필드를 쓰지만 이 함수가
    찾는 '전략에 첨부할 발송 자료'와는 다른 성격의 콘텐츠다."""
    rows = [a for a in ASSETS if a.get("customer_facing") is True and not a.get("content_type")]
    if branch:
        rows = [a for a in rows if a.get("branch") in (branch, None)] or rows
    return rows[0] if rows else None


def _verb(spec: dict, label: str, row: dict) -> str:
    """상품에 부착할 동작 명사.

    분기 기본값보다 상품 유형별 지정을 우선한다. 같은 원리금보장 분기라도 만기 도래한
    정기예금은 '재예치', 신규 가입하는 GIC·ELB 는 '예치' 가 맞는 표현이다.
    """
    q = _branch_defs(spec).get(label, {})
    return (q.get("verb_by_category") or {}).get(row.get("category")) or q.get("verb", "배분")


def _action(spec: dict, products: dict[str, dict]) -> str:
    """상품과 동작을 결합한 구를 만든다.

    실적배당 상품에 '재예치' 를, 원리금보장 신규 가입에 '매수' 를 붙이지 않도록 동작 명사는
    분기·상품 유형 정의에서 가져온다.
    """
    parts = [(lb, f"{_ro(_pname(r))} {_verb(spec, lb, r)}") for lb, r in products.items() if r]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][1]
    return ", ".join(f"{lb} 희망 시 {txt}" for lb, txt in parts)


def _trim_target(p: Profile, spec: dict, conds: list[str], extra: dict) -> str:
    """위험자산 조정 전략의 축소 대상 표기.

    무엇을 얼마의 근거로 줄이는지를 절 안에 남긴다. 섹터 집중 요건이 함께 성립하면 우선
    처분 대상을 명시하되, 조정 근거(규정 한도 / 성향 기준)는 전략별로 구분해 표기한다.
    흡수 과정에서 이 정보가 사라지면 '위험자산을 줄이라' 는 지시만 남아 실행할 수 없다.
    """
    head = f"섹터 ETF {p.port[3]}%를 우선 처분해 " if ("sec" in conds and p.port[3] >= 50) else ""
    if spec["id"] == "st.limit_fix":
        return f"{head}위험자산 {p.risk_asset}% 중 투자한도({RISK_ASSET_CAP_PCT}%) 초과분"
    return f"{head}위험자산 {p.risk_asset}% 중 성향 기준({extra.get('target', '')}%) 초과분"


_COND_OPS = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def _eval_condition(cond: dict | list, p: Profile) -> bool:
    """resolves 의 조건부 흡수(policy=conditional)를 판정하는 선언적 비교식 평가기.

    {"field": "matAmt", "op": ">=", "value_of": "dep_amt", "mul": 0.9} 는
    p.matAmt >= p.dep_amt * 0.9 를 뜻한다. cond 가 리스트면 전부 참이어야 성립한다(AND).
    새 조건부 흡수 규칙은 engine.py 를 열 필요 없이 strategies.json 에 조건식만 선언하면 된다.
    """
    if isinstance(cond, list):
        return all(_eval_condition(c, p) for c in cond)
    lhs = getattr(p, cond["field"])
    rhs = getattr(p, cond["value_of"]) * cond.get("mul", 1) if "value_of" in cond else cond["value"]
    return _COND_OPS[cond["op"]](lhs, rhs)


_BRACKET_LABEL = {"5500이하": "5,500만원 이하", "5500초과": "5,500만원 초과"}


def _three_way_breakdown(p: Profile) -> dict[str, int] | None:
    """3분류 운용현황 — 고유계정대/실적배당형/원리금보장형(요건정의서 ③).

    cash_idle_pct(고유계정대 비중)가 없으면 None — 화면은 이 경우 3분류 표시를 생략한다
    (customer.py::Profile.cash_idle_pct 참고). 고유계정대는 port[0](예금)의 부분집합으로
    다룬다 — port[0] 자체는 위험자산 한도 등 기존 게이팅 로직이 그대로 참조하므로 건드리지
    않는다(port 4분류는 불변, 이 3분류는 순수 표시용 추가 뷰다).
    """
    if p.cash_idle_pct is None:
        return None
    return {
        "고유계정대": p.cash_idle_pct,
        "실적배당형": p.port[1] + p.port[2] + p.port[3],
        "원리금보장형": p.port[0] - p.cash_idle_pct,
    }


def _why_this_customer(p: Profile, conds: list[str]) -> list[str]:
    """AI 브리핑 근거 최대 3개, 정량 중심 1줄씩(요건정의서 ② "왜 이 고객님인가요?").

    결정론적 코드 산출 — LLM 이 만들지 않는다. balPct 가 없는 페르소나는 해당 줄만
    생략한다(상류 조인 값이라 이 엔진이 직접 계산할 수 없다).
    """
    lines: list[str] = []
    if p.balPct is not None:
        lines.append(f"평가금액 {won(p.bal)}으로 유사 고객 상위 {p.balPct}% 수준이에요.")
    lines.append(f"최근 1년 수익률은 {p.ret}%로 유사 고객 하위 {p.retPct}% 수준이에요.")
    if "mis" in conds:
        lines.append(f"{p.rk} 성향 대비 원리금보장형 비중이 {p.port[0]}%로 높아요.")
    return lines[:3]


def _briefing(p: Profile) -> dict:
    """상담 준비용 보유 현황 스냅샷.

    가이드 p02(상품 보유 현황 확인)는 직원에게 'MyStar 단말에서 보유 상품·만기·수익률을
    조회'하도록 지시한다. 그 데이터는 타겟리스트·MyStar 를 조인한 Profile 로 이미 시스템에
    들어와 있으므로, 조회를 전략 문장의 지시로 남기지 않고 시스템이 제시하는 사실로 돌린다.
    전략(clause)은 판단·행동만 담고, 이미 보유한 데이터의 제시는 여기(코드)에서 처리한다.
    """
    comp = " · ".join(f"{lbl} {pct}%" for lbl, pct in zip(PORT_LABELS, p.port) if pct)
    snap = {"보유구성": comp, "운용수익률": f"{p.ret}% (유사 고객 기준 하위 {p.retPct}%)"}
    three_way = _three_way_breakdown(p)
    if three_way:
        snap["운용현황(3분류)"] = " · ".join(f"{k} {v}%" for k, v in three_way.items())
    if p.matDD is not None:
        snap["만기도래"] = f"D-{p.matDD} · {won(p.matAmt)}"
    # 추가납입 여력은 별도 전략(과거 st.add_invest)이 아니라 briefing 사실로 남긴다.
    # 근거 수치는 여기서 확정하고, 실제 제안 여부는 LLM 이 맥락상 판단한다(prompts.py).
    if p.room > 0:
        snap["납입여력"] = f"{won(p.room * 10000)} (연 납입한도 1,800만원 이내)"
    snap["source"] = BRIEFING_SOURCE
    return snap


def _build_ctx(p: Profile, spec: dict, products: dict[str, dict], amount: int,
               action: str, conds: list[str], extra: dict) -> tuple[dict | None, _Ctx]:
    """절 템플릿의 슬롯 컨텍스트를 만든다. 선정 항목과 대안 항목이 동일한 규칙으로
    렌더링되도록 공유한다. asset 은 발송 자료 승인 여부(clause_if_asset 분기)를 함께 돌려준다."""
    asset = customer_facing_asset() if spec.get("clause_if_asset") else None
    ctx = _Ctx(
        product_action=action,
        product_bare=", ".join(_pname(r) for r in products.values()),
        asset=_eul(asset["name"]) if asset else "",
        amount=won(amount), matDD=p.matDD, port0=p.port[0], port3=p.port[3],
        risk_asset=p.risk_asset, rk=p.rk, grade=p.grade, ret=p.ret, retPct=p.retPct,
        dorm=p.dorm, nchM=p.nchM, churn=churn(p), year_end_dd=days_to_year_end(),
        trim_target=_trim_target(p, spec, conds, extra),
        income_bracket=_BRACKET_LABEL.get(p.income_bracket or "", "구간 미확인"),
        tax_rate=f"{p.tax_credit_rate:.1%}", **extra,
    )
    return asset, ctx


# ─────────────────────────────────────────────────────────────
# 재료 산출
# ─────────────────────────────────────────────────────────────

def prepare(p: Profile, top_n: int = TOP_N) -> dict[str, Any]:
    """고객 프로파일로부터 확정 사실을 산출한다. 반환값이 LLM 단계의 유일한 입력이다."""
    conds = conditions(p)
    blocked: dict[str, str] = {}
    dropped: list[str] = []
    unverified: list[str] = []
    needs_confirm: list[str] = []
    cautions: list[str] = []

    # 1) 후보 소집 — 해당 요건이 성립하는 전략만 대상으로 한다.
    #    요건이 성립하지 않는 전략을 정원 충족 목적으로 추가하지 않는다.
    cands = [s for s in SPECS if s.get("when") in conds]

    # 2) 기능 요구사항 — 지원 여부가 확인되지 않은 기능을 전제한 전략은 제안하지 않는다.
    live0 = []
    for s in cands:
        miss = [c for c in s.get("requires", []) if CAPS.get(c, {}).get("status") != "available"]
        if miss:
            labels = ", ".join(CAPS.get(c, {}).get("label", c) for c in miss)
            unverified.append(f"{s['title']} — '{labels}' 지원 여부 미확인으로 제안 보류")
            continue
        live0.append(s)

    # 3) 흡수 — resolves 선언에 따라 겹치는 전략을 하나로 정리한다.
    #    target 방식(always/conditional)은 방향성 있는 흡수, group 방식(max_amount)은
    #    같은 대상을 다투는 전략들 중 조정 폭이 가장 큰 것만 남긴다.
    present = {s["id"] for s in live0}
    absorbed: dict[str, str] = {}
    merged: set[str] = set()
    for s in live0:
        for rule in s.get("resolves", []):
            target = rule.get("target")
            if not target or target not in present:
                continue
            if rule["policy"] == "always":
                absorbed.setdefault(target, s["id"])
            elif rule["policy"] == "conditional" and _eval_condition(rule["condition"], p):
                absorbed.setdefault(target, s["id"])
                merged.add(s["id"])

    # 3-1) 위험자산 목표 비중을 정하는 전략은 조정 폭이 가장 큰 것만 남긴다.
    #      규정 한도(70%)와 성향 기준(예: 30%)이 동시에 성립할 때 둘을 모두 실행하면
    #      합산 축소가 되어 성향 목표를 지나치게 밑돈다. 정적 흡수로 고정하면 반대로
    #      조정 폭이 작은 전략이 큰 전략을 흡수해 목표가 미달된다.
    groups: dict[str, list[dict]] = {}
    for s in live0:
        if s["id"] in absorbed:
            continue
        for rule in s.get("resolves", []):
            if rule["policy"] == "max_amount":
                groups.setdefault(rule["group"], []).append(s)
    mandatory_carry: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        amounts = {s["id"]: _amount(p, s)[0] for s in members}
        win = max(members, key=lambda s: amounts[s["id"]])
        for s in members:
            if s["id"] != win["id"]:
                absorbed.setdefault(s["id"], win["id"])
                if s.get("mandatory"):
                    mandatory_carry.add(win["id"])

    extra_evidence: dict[str, list[str]] = {}
    for tid, by in absorbed.items():
        dropped.append(f"{BY_ID[tid]['title']} — '{BY_ID[by]['title']}' 에 흡수 (동일 원인·재원)")
        extra_evidence.setdefault(by, []).append(tid)
    live = [s for s in live0 if s["id"] not in absorbed]

    # 4) 적합성 상한 — '투자성향 불일치' 요건 성립 시 성향 선호 등급까지 상한을 축소한다.
    cap = p.grade
    if "mis" in conds and PREF.get(p.rk):
        cap = RISK[min(RISK.index(cap), RISK.index(PREF[p.rk]))]

    # 5) 금액 무관 게이트 — 위험등급·거래채널로 후보 상품을 확보한다.
    built = []
    for spec in live:
        static = static_candidates(p, spec, cap, blocked)
        if _branch_defs(spec) and not any(static.values()):
            dropped.append(f"{spec['title']} — 위험등급·거래채널 게이트 통과 상품 0건")
            continue
        amount, extra = _amount(p, spec)
        built.append({"spec": spec, "static": static, "req": amount, "amount": amount,
                      "extra": extra, "urgency": _urgency(p, spec)})

    # 6) 자금 풀 배분 — 동일 재원의 중복 사용을 차단한다.
    pools = {"예금": p.dep_amt, "추가납입": p.room * 10_000, "위험자산": p.risk_amt}
    built.sort(key=lambda b: (-b["spec"].get("pool_priority", 0), -b["urgency"]))
    kept = []
    for b in built:
        pool = b["spec"].get("pool")
        if pool and b["req"] > 0:
            alloc = min(b["req"], pools.get(pool, 0))
            if alloc < MIN_ALLOC:
                dropped.append(f"{b['spec']['title']} — 동일 재원({pool}) 이 선행 전략에 배분 완료")
                continue
            pools[pool] -= alloc
            b["amount"] = alloc
        kept.append(b)

    # 7) 상품 확정 — 배분액 기준으로 최소가입금액·위험자산 한도 여력을 판정한다.
    headroom = p.risk_headroom_amt
    final = []
    for b in kept:
        spec = b["spec"]
        hr = None if spec.get("reduces_risk") else headroom
        products, dead = finalize_products(p, spec, b["static"], b["amount"], hr, blocked)
        dropped += dead
        if _branch_defs(spec) and not products:
            dropped.append(
                f"{spec['title']} — 배분액 {won(b['amount'])} 기준 적합성 게이트 통과 상품 0건")
            if spec.get("pool") and b["amount"]:
                pools[spec["pool"]] += b["amount"]  # 미사용 재원 반환
            continue
        if hr is not None and any(r.get("risk_asset") for r in products.values()):
            headroom = max(0, headroom - b["amount"])
        b["products"] = products
        final.append(b)

    # 8) 조건부 전략 — 상품 게이트 결과에 의존하는 전략을 추가한다.
    if _perf_branch_blocked(final, dropped, blocked):
        spec = BY_ID["st.risk_reassess"]
        final.append({"spec": spec, "static": {}, "products": {}, "req": 0, "amount": 0,
                      "extra": {}, "urgency": _urgency(p, spec)})

    # 9) 기대효과·점수 산출 및 상위 N 선정
    #    기대효과(원)는 선정 점수와 검증에 내부적으로 쓰되, 원금 이동 가정에 의존해 근거가
    #    불명확하므로 산출물에는 노출하지 않는다. 노출용 효과는 수익률 개선폭(yield_delta)이다.
    for b in final:
        b["impact"], b["range"], b["formula"] = _impact(p, b["spec"], b["amount"], b["products"])
        b["yield_delta"] = _yield_delta(b["spec"], b["products"])
        b["mandatory"] = bool(b["spec"].get("mandatory")) or b["spec"]["id"] in mandatory_carry
        b["score"] = _score(b["urgency"], b["impact"], b["range"], b["mandatory"])
    final.sort(key=lambda b: -b["score"])
    selected, rest = final[:top_n], final[top_n:]

    # 메인 문장에 포함되지 않은 전략은 '다른 제안' 으로 노출한다. 수익률 개선폭이 큰 순으로
    # 정렬해 상위 ALT_N 건만 남기고, 그 밖은 제외 항목으로 기록한다.
    rest.sort(key=lambda b: (-(b["yield_delta"] if b["yield_delta"] is not None else -1.0),
                             -b["score"]))
    alternatives, overflow = rest[:ALT_N], rest[ALT_N:]
    dropped += [f"{b['spec']['title']} — 상위 {top_n}건 미포함 (점수 {b['score']})" for b in overflow]

    # 10) 절·근거 렌더링 — 실행 순서(시급성 내림차순)로 정렬한다.
    selected.sort(key=lambda b: (-b["urgency"], -b["score"]))
    prev_action = ""
    for b in selected:
        spec = b["spec"]
        action = _action(spec, b["products"])
        # 동일한 분기 문구가 바로 앞 절에서 이미 제시된 경우에만 축약한다.
        # 인접하지 않은 절을 축약하면 지시 대상이 불명확해진다.
        if action and action == prev_action and len(b["products"]) > 1:
            action = "앞 항목과 동일한 기준으로 배분"
        prev_action = _action(spec, b["products"])

        asset, ctx = _build_ctx(p, spec, b["products"], b["amount"], action, conds, b["extra"])
        tpl = spec["clause_if_asset"] if asset else spec["clause"]
        if spec["id"] in merged and spec.get("clause_if_merged"):
            tpl = spec["clause_if_merged"]
        b["clause"] = tpl.format_map(ctx)

        ev = spec["evidence_if_merged"] if (spec["id"] in merged and spec.get("evidence_if_merged")) \
            else spec["evidence"]
        b["evidence"] = ev.format_map(ctx)
        # 흡수된 전략의 근거는 그 전략 자신의 파생값(초과분·목표 비중 등)으로 렌더링한다.
        # 흡수한 쪽의 값을 쓰면 정의되지 않은 슬롯이 원문 그대로 남는다.
        b["evidence_extra"] = []
        for t in extra_evidence.get(spec["id"], []):
            tspec = BY_ID[t]
            t_amt, t_extra = _amount(p, tspec)
            t_ctx = _Ctx({**ctx, **t_extra, "amount": won(t_amt),
                          "trim_target": _trim_target(p, tspec, conds, t_extra)})
            b["evidence_extra"].append(tspec["evidence"].format_map(t_ctx))

        if spec.get("clause_if_asset") and not asset:
            needs_confirm.append("고객 발송 가능 자료 미등록 — assets.json 의 customer_facing 확인 필요")
        if spec.get("impact", {}) and (spec.get("impact") or {}).get("kind") == "tax_credit" \
                and not p.income_bracket:
            needs_confirm.append(
                f"총급여 구간 미확인 — 공제율을 보수적으로 {p.tax_credit_rate:.1%} 적용")
        for r in b["products"].values():
            if r.get("risk_asset") and r.get("risk_asset_exempt") is None:
                needs_confirm.append(f"{r['name']} — 적격 TDF 위험자산 한도 예외 여부 미확인")
            if r.get("depositor_protection") is False:
                cautions.append(f"{r['name']} — 예금자보호 비대상 상품. 가입 전 고지 필요")
            elif r.get("depositor_protection") and b["amount"] > PROTECTION_LIMIT:
                cautions.append(
                    f"{r['name']} — 배분액 {won(b['amount'])} 중 예금자보호 한도"
                    f"({won(PROTECTION_LIMIT)}) 초과분 {won(b['amount'] - PROTECTION_LIMIT)} 안내 필요")

        # 카드 — 긴 지시 문장 대신 항목별 헤드라인·핵심가치·추천상품/대상·한 줄 혜택으로 압축한다.
        products_fmt = {k: _pname(v) for k, v in b["products"].items() if v}
        clause_action = final_clause(
            {"actor": spec["actor"], "kind": spec["kind"], "clause": b["clause"]})
        b["card"] = _card(spec, products_fmt, won(b["amount"]) if b["amount"] else None,
                          _effect_grade(b["yield_delta"]), clause_action,
                          spec.get("benefit") or b["evidence"])
        # 상담 화법 — pitch_refs 로 연결된 화법 카드를 고객유형에 맞춰 실시간 조회한다
        # (레거시 talk 필드가 있는 전략은 pitch_talk() 안에서 그쪽으로 폴백).
        b["talk"] = pitch_talk(spec, p.customer_type)

    # 11) 다른 제안 — 메인 문장 밖 전략의 절을 렌더링한다. 선정 항목과 동일한 슬롯 규칙을
    #     쓰되, 축약(prev_action)·고지/확인 누적 같은 문장용 부수효과는 적용하지 않는다.
    alt_items = []
    for b in alternatives:
        spec = b["spec"]
        _, ctx = _build_ctx(p, spec, b["products"], b["amount"],
                            _action(spec, b["products"]), conds, b["extra"])
        clause = spec["clause"].format_map(ctx)
        alt_items.append({
            "id": spec["id"], "title": spec["title"],
            "clause": final_clause({"actor": spec["actor"], "kind": spec["kind"], "clause": clause}),
            "effect_grade": _effect_grade(b["yield_delta"]),
            "yield_delta": b["yield_delta"],
            "source_titles": format_sources(spec.get("sources", []))
                             or ([spec["regulation"]] if spec.get("regulation") else []),
        })

    # 판단근거 — 선정 항목의 근거 중 핵심 1~3문장. 메인 제안의 근거를 압축해 제시한다.
    rationale = [b["evidence"] for b in selected if b["evidence"]][:3]

    pending = [b["range"] for b in selected if b["range"]]
    return {
        "customer": {"성명": p.nm, "연령": p.ag, "투자성향": p.rk, "위험등급": p.grade,
                     "적립금": won(p.bal), "수익률": f"{p.ret}% (하위 {p.retPct}%)",
                     "위험자산": f"{p.risk_asset}% (한도 {RISK_ASSET_CAP_PCT}%)",
                     "거래채널": "비대면" if p.nonface else "대면"},
        "briefing": _briefing(p),
        "conditions": [f"{c}:{CONDS[c]}" for c in conds],
        # 왜 이 고객님인가요 — 최대 3개, 정량 중심(요건정의서 ②). 코드 산출, LLM 개입 없음.
        "why_this_customer": _why_this_customer(p, conds),
        # 수익률 상위 1% 고객 상품 사례 — 비개인화, 비교 참고용(요건정의서 ④).
        "top_holdings": top_reference_products(),
        # 고객님께 안내해보세요 — 가장 임박한 이벤트 1개 + 세미나 1개(요건정의서 ⑨).
        "outreach": next_event_and_seminar(),
        "items": [{
            "id": b["spec"]["id"], "title": b["spec"]["title"], "kind": b["spec"]["kind"],
            "actor": b["spec"]["actor"], "clause": b["clause"], "evidence": b["evidence"],
            "evidence_extra": b["evidence_extra"], "card": b["card"], "talk": b["talk"],
            "amount": won(b["amount"]) if b["amount"] else None,
            "products": {k: _pname(v) for k, v in b["products"].items() if v},
            "urgency": b["urgency"], "impact": b["impact"], "impact_range": b["range"],
            "formula": b["formula"], "score": b["score"], "mandatory": b["mandatory"],
            "effect_grade": _effect_grade(b["yield_delta"]), "yield_delta": b["yield_delta"],
            "confidence": b["spec"]["confidence"], "sources": b["spec"].get("sources", []),
            "source_titles": format_sources(b["spec"].get("sources", [])),
            "regulation": b["spec"].get("regulation", ""),
        } for b in selected],
        # 상담 화법 — 정확히 2개를 보장한다(요건정의서 ⑥). 렌더러가 별도 섹션으로 노출한다.
        "talking_points": pick_talking_points(p, selected, alternatives),
        # 예상 반론 — 정확히 2개를 보장한다(요건정의서 ⑦).
        "objections": pick_objections(p, selected),
        # 상담 이력 — consult_agent 가 기록한 대화이력 요약(요건정의서 §14). 읽기만 한다 —
        # strategy_agent 는 세션 저장소에 쓰지 않는다("코드=사실" 경계를 대화이력에도 유지).
        "consult_history": summarize_for_briefing(p.id),
        # 근거 규정 — 규정 근거가 붙은 선정 항목. 적합성 원칙 등 판단의 출처를 추적 가능하게 한다.
        "regulations": [{"title": b["spec"]["title"], "regulation": b["spec"]["regulation"]}
                        for b in selected if b["spec"].get("regulation")],
        "rationale": rationale,
        "alternatives": alt_items,
        "impact_total": sum(b["impact"] for b in selected if b["impact"] and b["impact"] > 0),
        "impact_pending": (sum(r[0] for r in pending), sum(r[1] for r in pending)) if pending else None,
        "risk_cap": cap,
        "needs_slot": [b["spec"]["title"] for b in selected if len(b["products"]) > 1],
        "dropped": dropped,
        "unverified": unverified,
        "needs_confirm": sorted(set(needs_confirm)),
        "cautions": sorted(set(cautions)),
        "blocked_products": sorted(set(blocked.values())),
        "sources": sorted({s for b in selected for s in b["spec"].get("sources", [])}),
        "source_titles": format_sources(
            sorted({s for b in selected for s in b["spec"].get("sources", [])})),
    }


def _perf_branch_blocked(final: list[dict], dropped: list[str], blocked: dict) -> bool:
    """실적배당 경로가 적합성 사유로 전량 차단되었는지 판정한다.

    행내 가이드의 핵심 절차는 실적배당 상품 안내이므로, 이 경로가 통째로 막힌 고객에게는
    성향 재진단이 선행되어야 한다. 목업과 기존 정의에는 이 경로가 없었다.
    """
    if not any("위험등급" in v for v in blocked.values()):
        return False
    if any("실적배당 분기" in d for d in dropped):
        return not any(
            r.get("payout") == "실적배당" for b in final for r in b.get("products", {}).values())
    return False


# ─────────────────────────────────────────────────────────────
# 규칙 기반 문장 합성 — LLM 폴백 및 검증 기준선
# ─────────────────────────────────────────────────────────────

def final_clause(item: dict) -> str:
    """행위 주체를 반영한 최종 절.

    산출 문장은 직원이 읽고 실행하는 문서이다. 재예치·전환·납입·설정은 고객의 운용지시
    사항이므로 직원에 대한 지시로 그대로 쓸 수 없고, 제안·안내 행위로 감싼다.
    """
    if item["actor"] == "직원":
        return item["clause"]
    return item["clause"] + ACTOR_SUFFIX.get(item["kind"], "하도록 제안")


def compose_rule(facts: dict) -> str:
    """절을 연결해 단일 문장을 구성한다.

    절은 명사형 동작으로 종결되므로 접속 규칙만으로 안전하게 결합된다.
    LLM 미가용 또는 검증 실패 시 본 결과를 최종 응답으로 사용한다.
    """
    clauses = [final_clause(it) for it in facts["items"]]
    if not clauses:
        return "제안 가능한 실행 항목이 없습니다. (해당 요건 없음 또는 적합성 게이트에서 전량 제외)"
    if len(clauses) == 1:
        return clauses[0] + "하세요."
    if len(clauses) == 2:
        return f"{clauses[0]}하고, {clauses[1]}하세요."
    mid = ", ".join(c + "하고" for c in clauses[1:-1])
    return f"{clauses[0]}한 뒤, {mid}, {clauses[-1]}하세요."


# ─────────────────────────────────────────────────────────────
# 검증 — LLM 산출물의 재료 이탈 여부 판정 (common/verify.py 로 공용화됨)
# ─────────────────────────────────────────────────────────────


def verify(sentence: str, facts: dict) -> tuple[bool, list[str]]:
    """재료에 없는 수치 또는 게이트 미통과·미등록 상품명이 포함되었는지 검사한다."""
    return _verify(sentence, facts, known_products={r["name"] for r in PRODUCTS})


# ─────────────────────────────────────────────────────────────
# 정의 검증  (python engine.py)
#
# 근거 정합성 검증은 지식베이스 원문을 열어 대조한다. sources 필드의 존재 여부만
# 검사하면, 세액공제 전략이 리밸런싱 콜 스크립트를 근거로 인용해도 통과한다.
# ─────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def load_reference_kb():
    """근거 대조용 지식베이스. 경로가 없거나 적재에 실패하면 None 을 반환한다."""
    try:
        if str(KB_ROOT) not in sys.path:
            sys.path.insert(0, str(KB_ROOT))
        import kb as _kb  # noqa: PLC0415

        return _kb.load_kb(KB_ROOT / "data")
    except Exception:
        return None


_PITCH_KB = None
_PITCH_KB_LOADED = False


def _get_pitch_kb():
    """첫 호출 때만 적재하고 캐싱한다.

    engine.py 를 import 하는 시점에 바로 적재하면 sys.path 에 consult_agent 가 즉시 섞여
    들어가는데, test_engine.py 는 "agent 를 먼저 import 해서 동명 모듈(prompts·kb·llm)이
    consult_agent 쪽으로 잡히기 전에 strategy_agent 쪽을 확정한다"는 순서에 기대고 있다(파일
    상단 주석 참고). 지연 로딩으로 그 전제를 건드리지 않는다 — 실제 요청 처리(prepare())
    시점에만 로드되므로 그때는 이미 필요한 import 가 다 끝난 뒤다.
    """
    global _PITCH_KB, _PITCH_KB_LOADED
    if not _PITCH_KB_LOADED:
        _PITCH_KB = load_reference_kb()
        _PITCH_KB_LOADED = True
    return _PITCH_KB


def pitch_talk(spec: dict, customer_type: str | None) -> str:
    """spec.pitch_refs 로 연결된 화법 카드의 핵심 포인트·주의사항을 그 자리에서 가져온다.

    내용을 strategies.json 에 복사해두지 않는다 — consult_agent 쪽 카드가 나중에 수정돼도 다음
    호출부터 바로 반영되므로, 옛 talk 필드가 갖고 있던 '따로 관리되다 원본과 어긋나는' 문제가
    구조적으로 생기지 않는다. customer_type 이 일치하는 참조가 없으면 "공통" 참조로, 그마저
    없으면 옛 talk 필드(레거시, 마이그레이션 유예 중)로 폴백한다.
    """
    refs = spec.get("pitch_refs") or []
    pick = next((r for r in refs if r.get("customer_type") == customer_type), None) \
        or next((r for r in refs if r.get("customer_type") == "공통"), None)
    kb = _get_pitch_kb()
    if pick is None or kb is None:
        return spec.get("talk", "")
    card = next((c for c in kb.pitches if c["id"] == pick["id"]), None)
    if card is None:
        return spec.get("talk", "")
    parts = list(card.get("key_points") or [])
    parts += [f"(주의) {c}" for c in (card.get("cautions") or [])]
    return " / ".join(parts)


def top_reference_products(n: int = 2) -> list[dict]:
    """수익률 상위 1% 고객 상품 사례 n개(기본 2개) — 요건정의서 ④.

    고객별 필터가 없다 — 이 섹션 자체가 "이 고객에 대한 추천"이 아니라 고성과 고객의 실제
    운용 사례를 비교·참고 정보로 보여주는 것이라고 요건정의서가 명시한다(§7). 수익률 내림차순
    상위만 뽑는 순수 데이터 조회이며 LLM 이 개입하지 않는다.
    """
    ranked = sorted(TOP_HOLDINGS, key=lambda r: r["return_1y"], reverse=True)
    return [
        {"product_name": r["product_name"], "description": r["description"],
         "return_1y": r["return_1y"]}
        for r in ranked[:n]
    ]


def next_event_and_seminar(today: date | None = None) -> dict[str, dict | None]:
    """가장 임박한 이벤트 1개 + 세미나 1개(요건정의서 ⑨ "고객님께 안내해보세요").

    content_type 별로 종료되지 않은 것(end_date >= today) 중 start_date 오름차순 첫 건을
    고른다 — 진행 중이거나 미래 일정인 콘텐츠를 우선하고 종료된 콘텐츠는 노출하지 않는다는
    요건을 그대로 코드로 옮긴 것. LLM 은 개입하지 않는다.
    """
    today = today or TODAY

    def _pick(content_type: str) -> dict | None:
        candidates = [
            a for a in ASSETS
            if a.get("content_type") == content_type and a.get("end_date")
            and date.fromisoformat(a["end_date"]) >= today
        ]
        if not candidates:
            return None
        best = min(candidates, key=lambda a: a["start_date"])
        return {
            "name": best["name"], "start_date": best["start_date"], "end_date": best["end_date"],
            "channel": best.get("channel"), "lms_message": best.get("lms_message"),
        }

    return {"event": _pick("이벤트"), "seminar": _pick("세미나")}


def pick_talking_points(p: Profile, selected: list[dict], alternatives: list[dict], n: int = 2) -> list[dict]:
    """대고객 TM 화법을 정확히 n개(기본 2개) 보장한다(요건정의서 ⑥ "이렇게 말해보세요").

    선정 항목(selected) 중 화법이 있는 것만으로는 개수가 보장되지 않는다 — 접촉 성격의
    전략만 pitch_refs/talk 를 갖기 때문이다(예: C5·C6 페르소나는 0개). 모자라면 대안
    항목(alternatives)에서 pitch_talk() 을 다시 조회해 보충한다. 반환 항목은 대고객
    스크립트 생성(agent.py)의 재료가 된다 — amount·products 를 함께 담아 스크립트가
    이 고객의 실제 금액·상품명을 반영하게 한다.
    """
    def _entry(b: dict, talk: str) -> dict:
        return {
            "title": b["spec"]["title"],
            "talk": talk,
            "amount": won(b["amount"]) if b.get("amount") else None,
            "products": [_pname(r) for r in b["products"].values() if r],
        }

    picked = [_entry(b, b["talk"]) for b in selected if b.get("talk")]
    if len(picked) >= n:
        return picked[:n]

    used_ids = {b["spec"]["id"] for b in selected}
    for b in alternatives:
        if len(picked) >= n:
            break
        if b["spec"]["id"] in used_ids:
            continue
        talk = pitch_talk(b["spec"], p.customer_type)
        if talk:
            picked.append(_entry(b, talk))
    return picked[:n]


def _objection_entry(card: dict) -> dict:
    """반론 카드를 화면 표시용 {objection, response} 로 압축한다.

    objection 은 고객이 실제로 할 법한 말(trigger_examples 첫 문장, 없으면 카드 제목),
    response 는 카드의 대사(dialogue 중 '행원' 발화 첫 줄)를 그대로 쓴다 — key_points 는
    직원용 요약 불릿이라 '자연스러운 문장'을 요구하는 요건정의서 ⑦ 표시에는 부적합하다.
    """
    triggers = card.get("trigger_examples") or []
    reply = next((d["text"] for d in (card.get("dialogue") or []) if d.get("speaker") == "행원"), None)
    return {
        "objection": triggers[0] if triggers else card["title"],
        "response": reply or " / ".join(card.get("key_points") or []),
    }


def pick_objections(p: Profile, selected: list[dict], n: int = 2) -> list[dict]:
    """예상 반론 카드를 정확히 n개(기본 2개) 선별한다(요건정의서 ⑦ "예상 반론 및 대응 화법").

    1차: 선정 항목(selected)에 저작된 objection_refs 를 customer_type 매칭으로 취합한다
    (pitch_talk() 과 같은 방식 — 내용을 복사하지 않고 매 요청마다 consult_agent 쪽 원본을
    실시간 조회). objection_refs 저작이 아직 부족해 n개에 못 미치면, 2차로 지식베이스의
    objection 카드 전체를 customer_type 만으로 걸러 id 순 결정론적으로 보충한다 — 발화 단서가
    없는 프로파일 입력이라 consult_agent.retrieve() 수준의 정밀 매칭은 못 하지만, 카드를 아예
    노출하지 못하는 것보다는 낫다. 저작(objection_refs)이 쌓일수록 1차 경로가 더 채운다.
    """
    if not selected:
        return []  # 추천 전략 자체가 없으면(Tier2/미매칭) 반박할 대상도 없다
    kb = _get_pitch_kb()
    if kb is None:
        return []
    by_id = {c["id"]: c for c in kb.pitches if c["type"] == "objection"}

    picked_ids: list[str] = []
    for b in selected:
        for ref in b["spec"].get("objection_refs") or []:
            if len(picked_ids) >= n:
                break
            if ref.get("customer_type") not in (p.customer_type, "공통"):
                continue
            if ref["id"] in by_id and ref["id"] not in picked_ids:
                picked_ids.append(ref["id"])
        if len(picked_ids) >= n:
            break

    if len(picked_ids) < n:
        for cid in sorted(by_id):
            if len(picked_ids) >= n:
                break
            if cid in picked_ids:
                continue
            types = by_id[cid]["tags"].get("customer_type") or []
            if p.customer_type and p.customer_type not in types and "공통" not in types:
                continue
            picked_ids.append(cid)

    return [_objection_entry(by_id[cid]) for cid in picked_ids[:n]]


def _source_blob(kb, sid: str) -> str | None:
    """인용 대상 문서의 검색용 텍스트. 존재하지 않으면 None."""
    pit = next((x for x in kb.pitches if x["id"] == sid), None)
    if pit:
        parts = [pit["title"], *(pit["tags"].get("topics") or []), *pit.get("key_points", []),
                 *(pit.get("tips") or []), *(pit.get("cautions") or [])]
        return _norm(" ".join(parts))
    fct = kb.facts.get(sid)
    if fct:
        return _norm(" ".join([fct["label"], fct.get("value") or "", fct.get("detail") or ""]))
    return None


def validate() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    valid_conds = set(PRIO)
    ids = {s["id"] for s in SPECS + SYSTEM_STRATEGIES}

    errors += check_duplicate_ids(s.get("id", "?") for s in SPECS + SYSTEM_STRATEGIES)

    # 구조 검증은 플레이북(SPECS)과 시스템 전략(SYSTEM_STRATEGIES)에 함께 적용한다. 근거 정합성
    # (sources 교차검증·요건 미대응)은 원천 근거를 전제하는 SPECS 에만 적용한다(아래 별도 루프).
    for s in SPECS + SYSTEM_STRATEGIES:
        sid = s.get("id", "?")

        for req in ("id", "title", "kind", "actor", "clause", "evidence", "confidence"):
            if not s.get(req):
                errors.append(f"[필수누락] {sid} → {req}")
        if s.get("actor") not in ("직원", "고객"):
            errors.append(f"[잘못된값] {sid} actor={s.get('actor')} — 행위 주체 미지정")
        if s.get("actor") == "고객" and s.get("kind") not in ACTOR_SUFFIX:
            errors.append(f"[주체불일치] {sid} — actor=고객 이나 kind={s.get('kind')} 에 부착 어미 미정의")
        if not s.get("when") and not s.get("trigger"):
            errors.append(f"[필수누락] {sid} → when 또는 trigger")
        if s.get("when") and s["when"] not in valid_conds:
            errors.append(f"[잘못된요건] {sid} when={s['when']}")

        for key in ("clause", "clause_if_asset", "clause_if_merged"):
            tpl = s.get(key)
            if not tpl:
                continue
            if any(m in tpl for m in LOOKUP_MARKERS):
                errors.append(
                    f"[로직화대상] {sid}.{key} — 시스템이 보유한 데이터의 조회 지시. "
                    "절이 아니라 briefing 으로 표현한다")
            if any(m in tpl for m in TIME_PRESSURE_MARKERS):
                errors.append(
                    f"[시급성문구] {sid}.{key} — 데이터로 확정되지 않은 시한 표현. "
                    "시급성은 urgency 필드와 실행 순서로 표현한다")
            if tpl.endswith("{product_action}"):
                verbs = [q.get("verb") for q in _branch_defs(s).values()]
                if not verbs or any(v not in VERBS for v in verbs):
                    errors.append(f"[절규약위반] {sid}.{key} — 분기 verb 미정의 또는 허용 외: {verbs}")
            elif not tpl.endswith(CLAUSE_ENDINGS):
                errors.append(f"[절규약위반] {sid}.{key} — 명사형 동작 종결 아님: …{tpl[-14:]}")

        # 상담 화법(talk) — 직원 접촉 전략의 콜스크립트 요약. 고객 대상 동작(actor=고객)에는
        # 두지 않으며, 내부 조회 지시어를 담지 않는다(briefing 과 동일 규약).
        talk = s.get("talk")
        if talk:
            if s.get("actor") != "직원":
                errors.append(f"[화법대상] {sid} — talk 은 직원(접촉) 전략에만 정의한다")
            if any(m in talk for m in LOOKUP_MARKERS):
                errors.append(f"[로직화대상] {sid}.talk — 조회 지시어 포함. 화법에 담지 않는다")

        errors += check_broken_refs(
            (r["target"] for r in s.get("resolves", []) if r.get("target")), ids,
            owner=f"{sid} resolves")
        for rule in s.get("resolves", []):
            policy = rule.get("policy")
            if policy not in ("always", "conditional", "max_amount"):
                errors.append(f"[잘못된값] {sid} resolves.policy={policy}")
            elif policy == "max_amount" and not rule.get("group"):
                errors.append(f"[필수누락] {sid} resolves(max_amount) → group")
            elif policy in ("always", "conditional") and not rule.get("target"):
                errors.append(f"[필수누락] {sid} resolves({policy}) → target")
            if policy == "conditional" and not rule.get("condition"):
                errors.append(f"[필수누락] {sid} resolves(conditional) → condition")
        errors += check_broken_refs(s.get("requires", []), set(CAPS), owner=f"{sid} requires")

        conf = s.get("confidence")
        if conf not in ("행내가이드", "규정", "추정"):
            errors.append(f"[잘못된값] {sid} confidence={conf}")
        if conf == "행내가이드" and not s.get("sources"):
            errors.append(f"[근거없음] {sid} — confidence=행내가이드 이나 sources 미기재")
        if conf == "규정" and not s.get("regulation"):
            errors.append(f"[근거없음] {sid} — confidence=규정 이나 regulation 미기재")
        if conf == "추정" and s.get("sources"):
            warns.append(f"[등급불일치] {sid} — confidence=추정 이나 sources 존재")
        if s.get("sources") and not s.get("topic_keys"):
            errors.append(f"[검증불가] {sid} — sources 가 있으나 topic_keys 미기재로 대조 불가")
        if s.get("pool") and not s.get("amount"):
            errors.append(f"[정의모순] {sid} — pool 정의됨, amount 미정의")
        if (s.get("impact") or {}).get("base_ref") not in (None, *BASELINES):
            errors.append(f"[깨진참조] {sid} impact.base_ref={s['impact']['base_ref']}")

    errors += check_duplicate_ids(f"products {r['id']}" for r in PRODUCTS)
    for r in PRODUCTS:
        if r["risk"] not in RISK:
            errors.append(f"[잘못된값] {r['id']} risk={r['risk']}")
        if _ret_of(r) is None:
            errors.append(f"[수익률누락] {r['id']} — exp_return·rate 모두 없음")
        if "risk_asset" not in r:
            errors.append(f"[필수누락] {r['id']} → risk_asset (위험자산 한도 산입 여부)")
        if r.get("payout") == "원리금보장" and r.get("depositor_protection") is None:
            errors.append(f"[필수누락] {r['id']} → depositor_protection")

    for cid, c in CAPS.items():
        if c.get("status") not in ("available", "unavailable", "unknown"):
            errors.append(f"[잘못된값] capabilities {cid} status={c.get('status')}")

    # 근거 정합성 — 지식베이스 원문 대조
    kb = _get_pitch_kb()
    if kb is None:
        warns.append(f"[교차검증생략] 지식베이스 경로 없음({KB_ROOT / 'data'}) — sources 정합성 미검증")
    else:
        for s in SPECS:
            keys = [_norm(k) for k in s.get("topic_keys", []) if _norm(k)]
            for sid in s.get("sources", []):
                blob = _source_blob(kb, sid)
                if blob is None:
                    errors.append(f"[깨진참조] {s['id']} sources '{sid}' — 지식베이스에 없음")
                    continue
                if keys and not any(k in blob for k in keys):
                    errors.append(
                        f"[근거오인용] {s['id']} → '{sid}' 는 {s['topic_keys']} 를 다루지 않음")
            pitch_ids = {c["id"] for c in kb.pitches}
            for ref in s.get("pitch_refs", []) or []:
                if ref.get("customer_type") not in ("직장인", "사업자", "공통"):
                    errors.append(
                        f"[잘못된값] {s['id']} pitch_refs customer_type={ref.get('customer_type')}")
                if ref.get("id") not in pitch_ids:
                    errors.append(f"[깨진참조] {s['id']} pitch_refs '{ref.get('id')}' — pitch에 없음")
        if _source_blob(kb, BRIEFING_SOURCE) is None:
            errors.append(f"[깨진참조] briefing source '{BRIEFING_SOURCE}' — 지식베이스에 없음")
        for b in BASELINES.values():
            if b.get("source") not in kb.facts:
                errors.append(f"[깨진참조] baselines {b['id']} source '{b.get('source')}'")
        for a in ASSETS:
            rid = a.get("source_resource")
            if rid and rid not in kb.resources:
                errors.append(f"[깨진참조] assets {a['id']} source_resource '{rid}'")
            elif rid and kb.resources[rid].get("customer_facing") is False \
                    and a.get("customer_facing") is True:
                errors.append(
                    f"[제공범위위반] assets {a['id']} — 원본 자료 '{rid}' 는 고객 직접 제공 금지")

    warns += [f"[미대응] 요건 '{c}({CONDS[c]})' 에 대응하는 전략 정의 없음"
              for c in sorted(valid_conds - {s.get("when") for s in SPECS})]
    warns += [f"[제안보류] {cid} status={c['status']} — {c['label']}"
              for cid, c in CAPS.items() if c.get("status") != "available"]
    warns += [f"[발송보류] assets {a['id']} customer_facing 미확정 — {a['name']}"
              for a in ASSETS if a.get("customer_facing") is not True]
    return errors, warns


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print(f"플레이북 전략 {len(SPECS)}개 · 시스템 조건부 전략 {len(SYSTEM_STRATEGIES)}개 "
          f"· 상품 {len(PRODUCTS)}행 · 요건 {len(PRIO)}종 "
          f"· 기준선 {len(BASELINES)}건 · 기능 {len(CAPS)}건")
    errors, warns = validate()
    print("✅ ERROR 없음" if not errors else f"❌ ERROR {len(errors)}건")
    for e in errors:
        print("   " + e)
    print(f"⚠️  WARN {len(warns)}건")
    for w in warns:
        print("   " + w)
    raise SystemExit(1 if errors else 0)
