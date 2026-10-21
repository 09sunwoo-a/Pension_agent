"""개인정보 필터 — 행내 게이트웨이 400 패턴이 프롬프트·응답으로 나가지 않음.

실행: python -m tests.test_infra (번호 순서 임포트, 단독 실행 금지)
"""

from __future__ import annotations

from tests.infra._common import check


# 고객 id(KB-PIN)가 실린 호출은 게이트웨이가 400(재시도 대상 아님)으로 차단. 두 겹으로 고정:
#   ① 재료 — 프롬프트 재료(블록 본문·출처 제목)에 id 원문 미포함(정규식만으로는 데모 id 대부분이 안 걸림)
#   ② 문 — `llm.generate()` 전송 직전 마스킹(직원 질문·상담 기록 등 외부 글 대비)

from pension_agent.common import privacy  # noqa: E402

#: 행내 룰 이름 — 게이트웨이 응답 `rule_name` 과 동일 표기
_RULE_NAMES = ("신용카드_1", "여권번호_1", "여권번호_2", "여권번호_3", "전화번호_1",
               "외국인등록번호_1", "계좌번호_1", "계좌번호_2", "계좌번호_3",
               "운전면허번호_1", "주민등록번호_1", "이메일_1")

check(tuple(r.name for r in privacy.RULES) == _RULE_NAMES,
      "privacy: 행내 필터 룰 12종을 표기 그대로 갖고 있다",
      str([r.name for r in privacy.RULES]))

# KB-PIN 꼴이 주민등록번호 룰에 걸림(게이트웨이 400 의 실제 값)
check(privacy.findings("김서연 고객 계좌 현황 (KB-PIN 171203-4815062)") == ["주민등록번호_1"],
      "privacy: KB-PIN 꼴이 주민등록번호 룰에 걸린다 (게이트웨이가 400 을 내는 그 값)")

_masked, _hit = privacy.mask("고객 171203-4815062 에게 010-1234-5678 로 연락 · a.b@naver.com")
check(sorted(_hit) == ["이메일_1", "전화번호_1", "주민등록번호_1"],
      "privacy: 걸린 룰 이름을 전부 돌려준다", str(_hit))
check("171203" not in _masked and "1234-5678" not in _masked and "@naver.com" not in _masked,
      "privacy: 가린 자리에 값의 부스러기가 남지 않는다", _masked)
# 마스킹 표시에 숫자 미포함 — 다시 걸리면 게이트웨이 400
check(not privacy.findings(_masked), "privacy: 가린 글은 어느 룰에도 다시 걸리지 않는다",
      str(privacy.findings(_masked)))

# 업무 문장 오탐 금지
_ORDINARY = (
    "· 만기도래 2026-11-20 (D-60) 예금 2,800만원",
    "· 자산군별 예금 2,800만원(62.2%) · 수익증권 1,500만원(33.3%)",
    "[06-12-604] 포트폴리오 운용현황 조회 화면에서 확인하세요",
    "총급여 5,500만원 이하 16.5% · 초과 13.2%",
    "세액공제 한도 900만원 · 연 납입한도 1,800만원",
    "2026년 3~7월 자료 기준 · 서울 지역 세미나는 9월 10일 16시입니다",
)
for _line in _ORDINARY:
    check(not privacy.findings(_line), f"privacy: 업무 문장에 헛걸리지 않는다 — {_line[:24]}…",
          str(privacy.findings(_line)))

# 여권 룰은 앞뒤 공백까지 매치 — 공백 유지(낱말 붙음 방지)
_sp, _ = privacy.mask("발급된 M12345678 번호로")
check(_sp == f"발급된 {privacy.MASK} 번호로", "privacy: 매치가 머금은 앞뒤 공백은 남긴다", _sp)


# 문 — llm.generate() 전송 직전 마스킹
import json  # noqa: E402

from pension_agent.common import llm as _llm  # noqa: E402

_saved = (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.MIN_INTERVAL,
          _llm.urllib.request.urlopen, _llm.PII_SCRUB)
_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY = "genai", "http://fake", "k"
_llm.MIN_INTERVAL = 0.0
_llm._next_free = 0.0
_sent: list[dict] = []


class _FakeResp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self):
        return json.dumps({"choices": [{"message": {"content": "답"}}]}).encode()


def _capture(req, timeout=None):
    _sent.append(json.loads(req.data.decode("utf-8")))
    return _FakeResp()


try:
    _llm.urllib.request.urlopen = _capture
    _llm.PII_SCRUB = True
    _llm.generate("이 고객 171203-4815062 ISA 만기 언제야?",
                  system="고객 171203-4815062 의 자료다")
    _body = json.dumps(_sent[-1], ensure_ascii=False)
    check("171203-4815062" not in _body,
          "llm: 프롬프트에 남아 있던 개인정보 꼴이 요청 본문까지 나가지 않는다", _body[:120])
    check(_body.count(privacy.MASK) == 2,
          "llm: 사용자 프롬프트와 시스템 프롬프트를 둘 다 본다", str(_body.count(privacy.MASK)))

    # 끄기 가능 — 게이트웨이 rule_name 직접 확인용
    _llm.PII_SCRUB = False
    _llm.generate("이 고객 171203-4815062 ISA 만기 언제야?")
    check("171203-4815062" in json.dumps(_sent[-1], ensure_ascii=False),
          "llm: LLM_PII_SCRUB 를 끄면 원문 그대로 나간다 (진단 경로)")
finally:
    (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.MIN_INTERVAL,
     _llm.urllib.request.urlopen, _llm.PII_SCRUB) = _saved
    _llm._next_free = 0.0


# 재료 — 프롬프트로 나가는 블록에 고객 식별번호 미포함
from pension_agent.tools.kb_search import segment_qa as _segment_qa  # noqa: E402
from pension_agent.evidence.ledger import summarize as _summarize  # noqa: E402
from pension_agent.state import KB as _KB  # noqa: E402
from pension_agent.tools.customer_solution.tool import _customer as _t_customer  # noqa: E402
from pension_agent.tools.general.history import _history as _t_history  # noqa: E402
from pension_agent.tools.customer_solution.tool import _outreach as _t_outreach  # noqa: E402
from pension_agent.tools.customer_solution.tool import _suitable as _t_suitable  # noqa: E402
from pension_agent.tools.customer_solution import customer as _sc  # noqa: E402

_TOOLS = (("customer", _t_customer), ("suitable", _t_suitable),
          ("outreach", _t_outreach), ("history", _t_history))
_leaks: list[str] = []
_caught: list[str] = []
_built = 0
for _p in _sc.PERSONAS:
    _state = {"customer_id": _p.id, "session_id": "s-privacy"}
    _evidence = []
    for _name, _fn in _TOOLS:
        _ev = _fn(_state, "이 고객 현황")
        if not _ev:
            continue
        _evidence.append(_ev)
        _built += 1
        _titles = " ".join(s.get("title") or "" for s in _ev["sources"])
        if _p.id in _ev["text"] or _p.id in _titles:
            _leaks.append(f"{_name}/{_p.nm}")
        _caught += [f"{_name}/{_p.nm}:{r}" for r in privacy.findings(_ev["text"] + " " + _titles)]
    # 계획 프롬프트의 «이미 모은 재료»는 출처 제목으로 생성(ledger.summarize)
    _line = _summarize(_evidence)
    if _p.id in _line:
        _leaks.append(f"ledger/{_p.nm}")
    _caught += [f"ledger/{_p.nm}:{r}" for r in privacy.findings(_line)]

# 생성 건수 고정(도구 넷 × 12명) — 0건이면 위 검사가 헛통과
check(_built == len(_TOOLS) * len(_sc.PERSONAS),
      "재료: 12명 전원에 대해 도구 넷이 재료를 내놓았다 (검사가 헛돌지 않았다)",
      f"{_built}/{len(_TOOLS) * len(_sc.PERSONAS)}")
check(not _leaks, "재료: 고객 식별번호가 재료 본문·출처 제목·계획 요약에 실리지 않는다 (12명 전원)",
      str(sorted(set(_leaks))))
check(not _caught, "재료: 그 블록들이 행내 개인정보 필터에 걸리지 않는다", str(sorted(set(_caught))))

# 카드 선택은 LLM 경유라 적재 카드를 그대로 렌더 — «고객 줄에 식별번호가 붙는가»만 측정
_seg_cards = [(1.0, c) for c in _KB.cards if c["_kind"] == "segment"][:2]
_seg = _segment_qa.render(_seg_cards, _sc.PERSONAS[0].id)
check(_sc.PERSONAS[0].id not in _seg and not privacy.findings(_seg),
      "재료: 세그먼트 블록의 «지금 열려 있는 고객» 줄에도 식별번호가 없다")


# 응답 — 이벤트 스트림 전체(main._turn_events)에 식별번호 미포함. 출처 id(`customer.<id>`)가 게이트웨이 FILTER_INVALID 유발
import main as _main  # noqa: E402

from pension_agent.nodes import plan as _plan  # noqa: E402

_out_leaks: list[str] = []
_out_caught: list[str] = []
_out_ids: set[str] = set()
for _p in _sc.PERSONAS:
    _state = {"customer_id": _p.id, "session_id": "s-privacy"}
    _evidence = [_ev for _name, _fn in _TOOLS if (_ev := _fn(_state, "이 고객 현황"))]
    # 본문은 고정 한 줄 — 측정 대상은 출처·이벤트 꼴
    _sources = _plan._sources(_evidence, [], [], f"{_p.nm} 고객 현황입니다.")
    _out_ids |= {s["id"] for s in _sources}
    _events = _main._turn_events({"answer": f"{_p.nm} 고객 현황입니다.", "sources": _sources,
                                  "followups": [], "links": []})
    _wire = "\n".join(json.dumps(e, ensure_ascii=False) for e in _events)
    if _p.id in _wire:
        _out_leaks.append(_p.nm)
    _out_caught += [f"{_p.nm}:{r}" for r in privacy.findings(_wire)]

check(all(not any(ch.isdigit() for ch in i.split(".")[0]) for i in _out_ids)
      and {"customer", "suitable", "outreach", "session"} <= _out_ids,
      "응답: 고객 재료의 출처 id 가 종류 이름만으로 선다 (customer·suitable·outreach·session)",
      str(sorted(_out_ids))[:200])
check(not _out_leaks, "응답: sources 이벤트를 포함한 이벤트 스트림에 고객 식별번호가 없다 (12명 전원)",
      str(_out_leaks))
check(not _out_caught, "응답: 이벤트 스트림이 행내 개인정보 필터에 걸리지 않는다", str(sorted(set(_out_caught))))
