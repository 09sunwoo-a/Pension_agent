"""개인정보 필터 — 행내 게이트웨이가 400 으로 끊는 꼴이 프롬프트로 나가지 않는다

`tests/test_infra.py` 가 번호 순서로 임포트해 돌린다 — 이 파일을 따로 돌리지 않는다.
"""

from __future__ import annotations

from tests.infra._common import check


# ─────────────────────────────────────────────────────────────
# 개인정보 필터 — 행내 게이트웨이가 400 으로 끊는 꼴이 프롬프트로 나가지 않는다
#
# 2026-09-29 행내 실측: 고객 id(KB-PIN `171203-4815062`)가 실린 작성 호출이
# `HTTP 400 {"error": "민감정보 감지됨: ", "rule_name": "주민등록번호_1"}` 으로 끊겼다.
# 400 은 재시도 대상이 아니라(s10) 그 턴은 답을 한 글자도 못 내보낸다 — 지식베이스에도
# 계획에도 문제가 없는데 화면에는 「LLM 호출이 실패했습니다」만 남는다.
#
# 여기서 고정하는 것은 두 겹이다.
#   ① 재료 — 고객 식별번호를 프롬프트 재료(블록 본문·출처 제목)에 싣지 않는다. 데모 12명
#      중 그 필터에 실제로 걸리는 id 는 «생년월일로 읽히는» 하나뿐이라(나머지는 월·일
#      자리가 말이 안 된다) 정규식 검사만으로는 재발을 못 잡는다 — **id 원문이 있는가**로
#      잰다. 실데이터에서는 그 자리가 전부 실제 생년월일이라 12분의 1 이 아니다.
#   ② 문 — `llm.generate()` 가 나가기 직전에 한 번 더 가린다. 프롬프트에는 직원 질문
#      원문·상담 기록처럼 우리가 쓰지 않은 글도 실리기 때문이다.
# ─────────────────────────────────────────────────────────────

from pension_agent import privacy  # noqa: E402

#: 행내 표의 룰 이름 — 게이트웨이 응답의 `rule_name` 과 같은 말이어야 로그를 맞대 볼 수 있다.
_RULE_NAMES = ("신용카드_1", "여권번호_1", "여권번호_2", "여권번호_3", "전화번호_1",
               "외국인등록번호_1", "계좌번호_1", "계좌번호_2", "계좌번호_3",
               "운전면허번호_1", "주민등록번호_1", "이메일_1")

check(tuple(r.name for r in privacy.RULES) == _RULE_NAMES,
      "privacy: 행내 필터 룰 12종을 표기 그대로 갖고 있다",
      str([r.name for r in privacy.RULES]))

# 실측 그대로의 값 — 이 한 건이 이 파일이 생긴 이유다.
check(privacy.findings("김서연 고객 계좌 현황 (KB-PIN 171203-4815062)") == ["주민등록번호_1"],
      "privacy: KB-PIN 꼴이 주민등록번호 룰에 걸린다 (게이트웨이가 400 을 내는 그 값)")

_masked, _hit = privacy.mask("고객 171203-4815062 에게 010-1234-5678 로 연락 · a.b@naver.com")
check(sorted(_hit) == ["이메일_1", "전화번호_1", "주민등록번호_1"],
      "privacy: 걸린 룰 이름을 전부 돌려준다", str(_hit))
check("171203" not in _masked and "1234-5678" not in _masked and "@naver.com" not in _masked,
      "privacy: 가린 자리에 값의 부스러기가 남지 않는다", _masked)
# 가린 글이 다시 걸리면 표시가 게이트웨이에서 또 400 을 부른다 — 표시에 숫자를 남기지
# 않는 이유가 이것이다(privacy.MASK 주석).
check(not privacy.findings(_masked), "privacy: 가린 글은 어느 룰에도 다시 걸리지 않는다",
      str(privacy.findings(_masked)))

# 업무 문장은 건드리지 않는다 — 오탐은 답을 이유 없이 깎는다(§6 「검증기가 옳은 문장을
# 거부하는 것은 틀린 문장을 통과시키는 것보다 나쁘다」와 같은 자리다).
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

# 여권 룰은 앞뒤 공백까지 매치에 넣는다 — 그 공백을 지우면 낱말이 들러붙는다.
_sp, _ = privacy.mask("발급된 M12345678 번호로")
check(_sp == f"발급된 {privacy.MASK} 번호로", "privacy: 매치가 머금은 앞뒤 공백은 남긴다", _sp)


# ── 문 — llm.generate() 가 나가기 직전에 가린다 ───────────────────────────────
import json  # noqa: E402

from pension_agent import llm as _llm  # noqa: E402

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

    # 끌 수 있어야 한다 — 게이트웨이가 무엇에 걸리는지(`rule_name`)를 직접 볼 때 쓴다.
    _llm.PII_SCRUB = False
    _llm.generate("이 고객 171203-4815062 ISA 만기 언제야?")
    check("171203-4815062" in json.dumps(_sent[-1], ensure_ascii=False),
          "llm: LLM_PII_SCRUB 를 끄면 원문 그대로 나간다 (진단 경로)")
finally:
    (_llm.PROVIDER, _llm.BASE_URL, _llm.API_KEY, _llm.MIN_INTERVAL,
     _llm.urllib.request.urlopen, _llm.PII_SCRUB) = _saved
    _llm._next_free = 0.0


# ── 재료 — 고객 식별번호가 프롬프트로 나가는 블록에 실리지 않는다 ─────────────
from pension_agent.consult_agent.evidence import segment_qa as _segment_qa  # noqa: E402
from pension_agent.consult_agent.evidence.ledger import summarize as _summarize  # noqa: E402
from pension_agent.consult_agent.state import KB as _KB  # noqa: E402
from pension_agent.consult_agent.tools.briefing import _customer as _t_customer  # noqa: E402
from pension_agent.consult_agent.tools.history import _history as _t_history  # noqa: E402
from pension_agent.consult_agent.tools.outreach import _outreach as _t_outreach  # noqa: E402
from pension_agent.consult_agent.tools.suitability import _suitable as _t_suitable  # noqa: E402
from pension_agent.strategy_agent import customer as _sc  # noqa: E402

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
    # 계획 프롬프트의 「이미 모은 재료」는 출처 **제목**으로 만들어진다(ledger.summarize).
    _line = _summarize(_evidence)
    if _p.id in _line:
        _leaks.append(f"ledger/{_p.nm}")
    _caught += [f"ledger/{_p.nm}:{r}" for r in privacy.findings(_line)]

# 재료가 하나도 안 만들어지면 위 두 검사는 «아무것도 안 봤다»로 통과한다 — 몇 건을 봤는지
# 함께 고정한다(도구 넷 × 12명).
check(_built == len(_TOOLS) * len(_sc.PERSONAS),
      "재료: 12명 전원에 대해 도구 넷이 재료를 내놓았다 (검사가 헛돌지 않았다)",
      f"{_built}/{len(_TOOLS) * len(_sc.PERSONAS)}")
check(not _leaks, "재료: 고객 식별번호가 재료 본문·출처 제목·계획 요약에 실리지 않는다 (12명 전원)",
      str(sorted(set(_leaks))))
check(not _caught, "재료: 그 블록들이 행내 개인정보 필터에 걸리지 않는다", str(sorted(set(_caught))))

# 카드 선택은 LLM 을 타므로(`evidence/select.pick`) 여기서는 고르지 않고 적재된 카드를
# 그대로 렌더한다 — 이 검사가 보는 것은 «고객 줄에 식별번호가 붙는가» 하나다.
_seg_cards = [(1.0, c) for c in _KB.cards if c["_kind"] == "segment"][:2]
_seg = _segment_qa.render(_seg_cards, _sc.PERSONAS[0].id)
check(_sc.PERSONAS[0].id not in _seg and not privacy.findings(_seg),
      "재료: 세그먼트 블록의 «지금 열려 있는 고객» 줄에도 식별번호가 없다")
