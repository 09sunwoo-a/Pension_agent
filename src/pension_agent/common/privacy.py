"""행내 개인정보 필터 대응 — LLM 으로 나가는 글에서 게이트웨이 필터가 잡는 꼴을 마스킹.

- 게이트웨이(APIM)가 주민번호·계좌·전화 꼴을 400 으로 차단(재시도 불가). KB-PIN 도 주민번호 꼴
- `llm.generate()` 한 곳에서만 호출. 재료 쪽 경계(고객 id 미포함)와 별개로 원문 유입분 대비
- 응답(프론트 방향)에는 미적용 — 딥링크·카드 id 손상 우려
- 규칙표는 행내 원문 그대로 — 완화·강화 금지
- 로그에는 룰 이름·건수만, 걸린 값 미기록

진단:

    python -m pension_agent.common.privacy "김서연 고객 171203-4815062"
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Rule(NamedTuple):
    """필터 룰 — 이름(게이트웨이 `rule_name` 과 동일 표기)과 정규식."""

    name: str
    pattern: re.Pattern[str]


def _rule(name: str, pattern: str) -> Rule:
    return Rule(name, re.compile(pattern))


#: 행내 개인정보 필터 룰 12종(원문 그대로). `$`·`(^|\s)` 도 원문대로 — `re.M` 추가 금지.
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

#: 마스킹 표시. 숫자 일부·룰 이름 미포함 — 재검출·LLM 오인용 방지.
MASK = "[개인정보 가림]"


def findings(text: str) -> list[str]:
    """걸리는 룰 이름 목록(중복 없음, 값 미포함)."""
    return [rule.name for rule in RULES if rule.pattern.search(text or "")]


def mask(text: str) -> tuple[str, list[str]]:
    """(마스킹된 글, 걸린 룰 이름). 안 걸리면 원문 객체 그대로."""
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
    """매치를 MASK 로 치환. 매치에 포함된 앞뒤 공백은 유지(여권번호 룰이 경계 공백 포함)."""
    found = m.group(0)
    head = found[:len(found) - len(found.lstrip())]
    tail = found[len(found.rstrip()):]
    return f"{head}{MASK}{tail}"


if __name__ == "__main__":  # 진단: python -m pension_agent.common.privacy "<글>"
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sample = " ".join(sys.argv[1:]) or sys.stdin.read()
    out, rules = mask(sample)
    print(f"룰 {len(RULES)}종 · 걸린 룰: {', '.join(rules) or '없음'}")
    print(out)
