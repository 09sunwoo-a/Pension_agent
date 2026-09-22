"""행내 개인정보 필터 — LLM 으로 나가는 글에서 그 필터가 잡는 꼴을 가린다.

━━ 왜 있나 ━━
행내 GenAI 게이트웨이(APIM) 앞단에 **개인정보 탐지 필터**가 서 있다. 요청 본문에 주민등록
번호·계좌번호·전화번호 꼴이 있으면 모델까지 가지 못하고 400 으로 끊긴다:

    HTTP 400 Bad Request — …/trnn/gemma-4/chat/completions
    {"error": "민감정보 감지됨: ", "rule_name": "주민등록번호_1", "catched_text": "171203-4815062"}

2026-09-29 행내 실측이다. 걸린 것은 고객 id(KB-PIN)다 — **앞자리가 생년월일이라 주민등록
번호와 같은 꼴**이고(`note.py` 가 쪽지에서 이 값을 가리는 것과 같은 이유), 그것이 고객
재료 블록의 머리줄과 근거 목록 제목에 실려 프롬프트로 나갔다. 400 은 재시도 대상이 아니라
(`llm._retryable`) 그 턴은 그대로 「LLM 호출이 실패했습니다」로 끝난다 — 지식베이스에도
계획에도 문제가 없는데 답이 한 글자도 안 나간다.

━━ 경계는 두 겹이다 ━━
① **재료에서 긋는다.** 고객 식별번호는 LLM 이 답하는 데 쓰지 않는 값이라 애초에 프롬프트
   재료에 싣지 않는다 — 이름과 «지금 열려 있는 고객»이면 충분하다(consult_agent/CLAUDE.md
   §3 이 오늘의 타겟 목록에 대해 같은 결정을 이미 적어 뒀다: 「경계를 재료에서 긋는 것이
   「쓰지 말라」는 지시보다 확실하다」).
② **나가기 직전에 한 번 더 본다.** 이 모듈이 그 문이고, `llm.generate()` 한 곳에서만
   불린다 — 모든 호출이 그 함수를 지나므로 새 프롬프트가 생겨도 빠지지 않는다.

①이 있는데 ②를 두는 이유는, 프롬프트에 실리는 글이 **우리가 쓴 것만이 아니기 때문**이다.
직원 질문 원문·상담 기록·지식 카드 원문이 그대로 실린다 — 직원이 고객 번호를 타이핑하면
그 턴은 답이 안 나가는 턴이 된다. 가려서 답이 조금 어긋나는 것과, 400 으로 아무 답도 못
하는 것 중에서 앞을 고른다.

━━ 나가는 길은 LLM 쪽만이 아니다 ━━
위 둘을 고친 다음 날 같은 고객의 턴이 다시 답을 못 냈다 — 이번에는 **프론트로 가는 응답**을
플랫폼 게이트웨이(`agent-messages`)가 막았다(`status=FILTER_INVALID` · «The content was
blocked by the filter»). 응답의 `sources` 이벤트가 출처 id 로 `customer.171203-4815062` 를
싣고 있었다. 그쪽도 재료에서 긋는다 — 고객 재료의 출처 id 는 종류 이름만이다
(`consult_agent/tools/briefing.py` 와 이웃들). 이 모듈의 문(②)은 LLM 호출에만 서 있고
응답에는 걸지 않는다: 그 게이트웨이의 룰 표가 아래 12종과 같은지 확인된 바 없고, 구조화된
이벤트 JSON 에 일괄 마스킹을 걸면 화면 딥링크·카드 id 같은 값이 깨질 수 있다. 응답에서
무엇이 나가는지는 `tests/infra/s17_privacy.py` 「응답」 절이 이벤트 스트림 전체를 잰다.

━━ 규칙표는 행내 원문 그대로다 ━━
아래 12종은 행내 필터의 룰 표를 옮긴 것이다. **정규식을 우리 판단으로 손보지 않는다** —
재는 것은 게이트웨이이고, 이쪽에서 완화하면 «가린 줄 알았던 것»이 400 으로 돌아온다.
반대로 조이는 것도 하지 않는다: 여기서만 걸리는 꼴은 답을 이유 없이 깎는다.
행내에서 표가 갱신되면 고칠 자리는 이 표 하나다.

━━ 값은 남기지 않는다 ━━
무엇이 걸렸는지는 **룰 이름과 건수**로만 남긴다(`llm._scrub` 의 단계 로그). 걸린 문자열을
로그에 실으면 가리려던 값이 로그로 새어 나간다 — 게이트웨이 응답이 `catched_text` 로
그 값을 돌려주는 것과 같은 실수를 우리가 반복하지 않는다.

진단:

    python -m pension_agent.privacy "김서연 고객 171203-4815062"
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Rule(NamedTuple):
    """필터 룰 한 줄 — 이름과 정규식. 이름은 행내 표기 그대로다(게이트웨이 응답의
    `rule_name` 과 같은 말이어야 로그를 맞대 볼 수 있다)."""

    name: str
    pattern: re.Pattern[str]


def _rule(name: str, pattern: str) -> Rule:
    return Rule(name, re.compile(pattern))


#: 행내 개인정보 필터 룰 12종 — 원문 표 그대로(위 머리말).
#:
#: 계좌번호_1 의 `$`(문자열 끝)와 여권번호 세 종의 `(^|\s)`(앞이 시작이거나 공백)는 원문에
#: 있는 그대로 둔다. 게이트웨이가 무엇을 한 덩이로 보고 재는지(본문 전체인지 줄 단위인지)는
#: 확인되지 않았고, 우리가 `re.M` 을 얹으면 그쪽보다 넓게 재게 된다.
RULES: tuple[Rule, ...] = (
    _rule("신용카드_1",
          r"(?:4\d{3}|5[1-5]\d{2}|6(?:011|5\d{2})|3[47]\d{2}|3(?:0[0-5]|[68]\d)"
          r"|(?:2131|1800|35\d{3}))([- .]\d{4}){3,4}"),
    _rule("여권번호_1", r"(^|\s)(D|d|M|m|R|r|S|s|G|g|T|t)\d{8}(\s|)"),
    _rule("여권번호_2", r"(^|\s)(D|d|M|m|R|r|S|s|G|g|T|t)\d{3}[A-Z]\d{4}(\s|)"),
    _rule("여권번호_3", r"(^|\s)([A-Z][A-Z])\d{7}(\s|)"),
    _rule("전화번호_1", r"(010[-+ .*,]\d{4}[-+ .*,]\d{4}|8210[-+ .*,]\d{4}[-+ .*,]\d{4})"),
    _rule("외국인등록번호_1",
          r"(?:[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01]))[-+,*. ]([5-8][0-9]{6})"),
    _rule("계좌번호_1", r"\d{4}[- ](06|18)[- ]\d{6}$|\d{4}(06|18)[- ]\d{2}[- ]\d{4}"),
    _rule("계좌번호_2", r"\d{6}[- ]\d{2}[- ]\d{6}"),
    _rule("계좌번호_3", r"\d{4}15[- ]\d{2}[- ]\d{5}"),
    _rule("운전면허번호_1",
          r"((1[0-9]|2[0-6]|28)[-+ .*,]\d{2}[-+ .*,]\d{6}[-+ .*,]\d{2})"
          r"|((서울|부산|경기|강원|충북|충남|전북|전남|경북|경남|제주|대구|인천|광주|대전"
          r"|울산|의정부)[-+ .*,]?\d{2}[-+ .*,]?\d{6}[-+ .*,]?\d{2})"),
    _rule("주민등록번호_1",
          r"(?<![\dA-Za-z])\d{2}(0[1-9]|1[0-2])(0[1-9]|1\d|2\d|3[01])[-+,*. ][1-4]\d{6}"
          r"(?![\dA-Za-z])"),
    _rule("이메일_1",
          r"[a-zA-Z0-9._%+-]+@(gmail.com|naver.com|daum.net|hanmail.net|kakao.com"
          r"|outlook.com|yahoo.com|icloud.com)"),
)

#: 가린 자리에 남기는 표시. **숫자를 남기지 않는다** — 자릿수를 남기면(`171203-4******`)
#: 그 꼴이 다른 룰에 다시 걸릴 수 있고, LLM 이 그 부스러기를 값으로 인용한다.
#: 무엇이 가려졌는지는 룰 이름을 붙이지 않고 한 가지로만 말한다: KB-PIN 은 주민등록번호가
#: 아닌데 룰 이름을 그대로 실으면 LLM 이 «이 고객의 주민등록번호»라고 읽는다.
MASK = "[개인정보 가림]"


def findings(text: str) -> list[str]:
    """이 글에서 걸리는 룰 이름 — 같은 룰이 여러 번 걸려도 이름은 한 번만. 값은 돌려주지
    않는다(머리말 「값은 남기지 않는다」). 게이트웨이가 400 을 낼지를 미리 재는 자리다."""
    return [rule.name for rule in RULES if rule.pattern.search(text or "")]


def mask(text: str) -> tuple[str, list[str]]:
    """가린 글과 걸린 룰 이름. 걸린 것이 없으면 원문을 그대로 돌려준다(같은 객체).

    앞뒤 공백은 남긴다 — 여권번호 룰들이 `(^|\\s)…(\\s|)` 로 **경계까지 매치에 넣기**
    때문이다. 그 공백까지 지우면 앞뒤 낱말이 들러붙어 다른 뜻이 된다.
    """
    if not text:
        return text, []
    hit: list[str] = []
    for rule in RULES:
        masked, n = rule.pattern.subn(_replace, text)
        if n:
            hit.append(rule.name)
            text = masked
    return text, hit


def _replace(m: re.Match[str]) -> str:
    """매치 한 건을 표시로 바꾼다. 매치가 머금은 앞뒤 공백은 그대로 둔다(위 주석)."""
    found = m.group(0)
    head = found[:len(found) - len(found.lstrip())]
    tail = found[len(found.rstrip()):]
    return f"{head}{MASK}{tail}"


if __name__ == "__main__":  # 진단: python -m pension_agent.privacy "<글>"
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sample = " ".join(sys.argv[1:]) or sys.stdin.read()
    out, rules = mask(sample)
    print(f"룰 {len(RULES)}종 · 걸린 룰: {', '.join(rules) or '없음'}")
    print(out)
