"""대표 질문 11개 — 실 LLM 으로 한 번 돌려 **답변과 트레이스를 나란히** 본다.

    cd src
    python -m tests.debug.reps              # 전체 (답변 + 근거 + 트레이스)
    python -m tests.debug.reps --brief      # 요약표만 — 이것만 붙여넣어도 진단이 된다
    python -m tests.debug.reps 4 7          # 케이스 골라서
    python -m tests.debug.reps --demo       # 시연 대본 순서대로 (docs/DEMO_SCENARIO.md)
    python -m tests.debug.reps --demo --debug   # 대본 + 재료→답변 로그 (시연에서 띄울 것)
    python -m tests.debug.reps --demo --time    # + 턴별 소요 시간 (리허설 진단용 — 시연에서는 끈다)
    python -m tests.debug.reps --scenario           # 고객별 대표 시나리오 5종 전부
    python -m tests.debug.reps --scenario 김서연     # 이름(또는 번호)으로 골라서 — 옵션은 --demo 와 동일
    python -m tests.debug.reps --final              # 중간점검 시연 확정본 (docs/DEMO_FINAL.md — 기획자 확정 3고객 + 이벤트 턴)

왜 `tests.debug` 와 따로 있나: 저쪽 CLI 는 **한 세션**이라 질문을 여러 개 주면 맥락이
이어진다(멀티턴 재현이 목적이다). 대표 질문 11개는 서로 독립이어야 하므로 케이스마다
세션을 새로 연다 — 8번(모호 → 되묻기)이 앞 케이스의 맥락을 물려받으면 되물을 이유가
사라져 그 케이스가 무의미해진다. 후속 질문을 보는 7번과 제안 → 승낙을 보는 11번만 한 케이스 안에 두 턴이다.

무엇을 재나 — 케이스마다 `sees` 에 적어둔 한 줄이 그 케이스의 존재 이유다. 축은 일곱이다:
**단일 도구**(1·2·3) · **복합**(4·5·6) · **후속 질문**(7) · **모호 → 되묻기**(8) ·
**지식베이스에 없는 것**(9) · **가드·반론**(10) · **제안·연계**(11 — 두 턴이다. 첫 턴에
발송 제안이 붙어야 둘째 턴 「응, 열어줘」가 딥링크로 이어진다). 답변 품질만 보면 1번도 10번도 그냥
"괜찮네"로 읽히지만, 에이전틱한지는 **도구를 몇 개 어떤 순서로 골랐는가**에서만 갈린다.

**채점하지 않는다.** 통과·실패를 코드가 정하면 그건 회귀 테스트지 검토가 아니다
(회귀는 `tests/test_consult_agent.py` 가 이미 315건 재고 있다). 여기는 사람이 읽고
판단하는 자리라, 요약표는 «무엇이 일어났나»만 찍는다.

`--demo` 는 검토가 아니라 **리허설**이다. `docs/DEMO_SCENARIO.md` 의 대본을 그 순서로,
고객 블록마다 한 세션으로 돌린다 — 후속 질문(T2·T3b·T8b·T11b)이 앞 턴을 이어받아야
대본대로이기 때문이다. 화면에 나가는 것만 보여주고, `--debug` 를 붙이면 턴마다
**어떤 재료가 들어가서 LLM 이 뭐라고 썼는지**를 짧게 붙인다(`_log`) — 시연에서 «지어낸 게
아니다»를 보여주는 자리다. 검토용의 전체 트레이스(노드·게이트 트리)는 진단 도구라
청중에게 띄울 것이 아니다.

**상담 기록을 남기지 않는다.** `graph.ask()` 는 턴마다 상담이력을 기록하는데(기준서 §2 —
진입점 한 곳에서 빠짐없이), `session_data/` 에는 시연 픽스처가 들어 있다
(`scripts/seed_sessions.py`). 그대로 돌리면 리허설 턴이 픽스처에 덧붙고, 대본 T5
(「지난번엔 무슨 얘기 했지?」)가 **10개월 전 고객 발언 대신 방금 리허설을 읽는다** —
돌릴수록 시연이 망가진다. 그래서 실행 전후로 디렉터리를 통째로 되돌린다. 기록 기능
자체는 그대로 돈다(끄면 그 경로를 예행하지 못한다) — 남은 것만 지운다.

**오늘은 2026-08-24 로 고정된다**(`tests/__init__.py` — 원장 스냅샷 기준일). 만기
잔여일수·미접촉 일수가 실행일마다 달라지면 두 번의 실행을 비교할 수 없다.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager

from pension_agent import config
from pension_agent import llm as LLM
from tests.debug import trace as TR
from tests.debug.runner import session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


#: (번호, 무엇을 보나, 고객, 질문들). 고객 id 는 `strategy_agent/customers.json` 의 9케이스.
CASES: tuple[tuple[int, str, str | None, tuple[str, ...]], ...] = (
    (1, "단일 도구 — fact 한 번으로 끝나나 (기준선)",
     None, ("IRP 세액공제 한도가 얼마야?",)),

    (2, "단일 도구 — screen 을 고르나 · 화면 연계 제안이 붙나",
     None, ("IRP 계좌 해지는 몇 번 화면에서 하지?",)),

    (3, "단일 도구 — channel(비대면) 과 screen(단말) 을 갈라 보나",
     None, ("고객이 스타뱅킹에서 직접 추가납입 하려면 어디로 들어가?",)),

    (4, "복합 — 고객 재료 + 화법. 성향-운용 불일치(공격투자형인데 예금 92%)",
     "181245-3097614", ("이 고객 예금만 들고 있는데 뭐라고 말해야 하지?",)),

    (5, "복합 — suitable(적합성 범위) 을 부르나. 특정 상품을 짚되 권유 표현 없이 답하나 (gap 27)",
     "165932-8741205", ("이 고객한테 뭘 추천해주면 좋을까?",)),

    (6, "복합 — 빗나가도 다른 도구로 갈아타나 (gap 23 · plan_misses/plan_retry)",
     "162754-9483106", ("이 고객은 왜 관리 대상으로 뜬 거야?",)),

    (7, "후속 질문 — 2턴째가 1턴 맥락을 이어받나 (gap 1·21)",
     "198734-1205842", ("이 고객 만기 언제야?", "그냥 두면 어떻게 돼?")),

    (8, "모호 — 답 대신 되묻나. 되묻기 턴에 근거가 붙나 (gap 22)",
     None, ("수수료 얼마야?",)),

    (9, "지식베이스 밖 — 지어내지 않고 없다고 하나 (재료 0건 경로)",
     None, ("타행 IRP 수수료는 우리보다 싼가?",)),

    (10, "가드·반론 — 고객 대사에 화법으로 답하고 하지 말 것이 걸리나 (§8)",
     "188406-7352194", ("고객이 '손실만 나는데 그냥 해지하겠다'는데 어떻게 대응하지?",)),

    (11, "제안·연계 — outreach 재료를 다룬 턴에 «발송 화면 열까요?»가 붙고, 승낙 턴이 딥링크·문구를 주나 (§10)",
     "188406-7352194", ("이 고객한테 안내할 만한 이벤트나 세미나 있어?", "응, 열어줘")),
)


#: 시연 대본 — `docs/DEMO_SCENARIO.md`. 고객 블록마다 한 세션이므로 블록 안에서는 맥락이
#: 이어진다(T2 는 T1 을, T3b 는 T3 을 이어받는다). 블록이 갈리는 자리가 곧 시연에서
#: 「고객 화면을 바꾸는」 자리다.
DEMO: tuple[tuple[int, str, str | None, tuple[tuple[str, str], ...]], ...] = (
    (0, "0막 기본기 — 출처 · 후속 질문 · 화면 연계", None, (
        ("T1",  "IRP 세액공제 한도가 얼마야?"),                       # 근거가 있다
        ("T2",  "총급여 6천만원이면 얼마 돌려받아?"),                  # 맥락을 이어받는다
        ("T3",  "IRP 계좌 해지는 몇 번 화면에서 하지?"),               # 연계 제안
        ("T3b", "응, 열어줘"),                                         # 딥링크
    )),
    (1, "1~2막 상담 전·중 — 송도윤(방치현금 54% · ISA 만기 · 322일 미접촉)",
     "188406-7352194", (
        ("T4",  "이 고객 왜 관리 대상이야?"),                          # 타겟 근거
        ("T5",  "지난번엔 무슨 얘기 했지?"),                            # 상담 이력
        ("T6",  "이 고객한테 하면 안 되는 게 뭐야?"),                   # 금지·주의
        ("T7",  "고객이 '그 돈 그냥 둬도 되지 않나요' 하는데 뭐라고 하지?"),   # 반론 대응
        ("T8",  "수수료 얼마야?"),                                     # 되묻기
        ("T8b", "사용자부담금(퇴직금), 대면이요"),                     # 되물은 선택지를 고른다 —
        # 이 고객 원장과 맞는 갈래다(퇴직급여 5.2억 · 개인부담금 0원). 가입자부담금을 고르면
        # 이후 턴이 «이 고객은 가입자부담금 계좌»라고 원장에 없는 속성을 굳힌다(3차 리허설).
        ("T9",  "우리 수수료가 얼마고, 증권사는 무료라는데 뭐라고 답하지?"),   # 복합 — 핵심
        ("T10", "그럼 이 고객한테 뭘 권할 수 있어?"),                   # 적합성 «범위»
        # 실화면에서는 추천 질문 칩이다(suggest.outreach_chips — 종류는 오늘 날짜의 계산값.
        # 8/24 고정에서 송도윤은 이벤트·세미나). 답변이 콘텐츠 이름을 인용해야 «발송 화면 열까요?»가
        # 붙는다(act._propose_lms 조건 ③) — 안 붙으면 T11b 가 공중에 뜬다.
        ("T11", "이 고객한테 안내할 만한 이벤트나 세미나 있어?"),
        ("T11b", "응, 열어줘"),                                       # 발송 화면 딥링크 + 문구
        ("T12", "타행 IRP 수수료는 우리보다 싼가?"),                    # 없다고 말한다
    )),
    (2, "3막 대조 — 정민석(공격투자형인데 원리금보장 100%)",
     "181245-3097614", (
        ("T13", "이 고객한테는 뭘 권할 수 있어?"),                      # 같은 질문, 다른 답
    )),
)


#: 고객별 대표 시나리오 — `docs/DEMO_CUSTOMER_SCENARIOS.md`. 고객마다 한 세션이라 블록
#: 안에서는 맥락이 이어진다(K4 는 K3 의 되묻기에 답하는 턴이다). 실행 경로·화면은 --demo
#: 리허설과 같고, 도는 목록만 다르다. 골라 돌리기: `--scenario 김서연` / `--scenario 1 5`.
SCENARIOS: tuple[tuple[int, str, str | None, tuple[tuple[str, str], ...]], ...] = (
    (1, "김서연 — 타행 ISA 8,000만원 · 되묻기 (골든 케이스 01)", "171203-4815062", (
        ("K1", "이 고객 어떤 상황이야?"),
        ("K2", "ISA 만기자금을 IRP로 옮기면 뭐가 좋아?"),
        ("K3", "고객이 8천만원 전부는 부담스럽다는데, 일부만 옮기면 세액공제는 어떻게 돼?"),
        # ↑ 핵심 장면 — 공제율이 갈리는 미확인 값(총급여 구간)을 답 대신 되묻는다
        # 되물은 갈래를 고르는 답은 **금액 없이** 쓴다. `tax_credit` 는 계획 LLM 의 질의가
        # 아니라 직원이 친 말에서 금액을 뽑으므로(tools._tax_credit 머리말), "5,500만원
        # 초과야"라고 쓰면 그 5,500만원을 «추가 납입액»으로 읽는다.
        ("K4", "초과야"),
        ("K5", "입금은 몇 번 화면에서 해?"),
        ("K5b", "응, 열어줘"),                                  # 화면 연계 승낙 → 딥링크
    )),
    (2, "박정호 — 퇴직금 1.5억 통장 수령 · 순서 경고 (골든 케이스 03)", "168450-7293815", (
        # P1 은 세션 저장소에 8/20 기록이 있어야 성립한다 — 없으면 칩도 재료도 0건이다
        # (scripts/seed_sessions.py). 실화면에서는 타이핑하지 말고 추천 질문 칩을 누른다.
        ("P1", "지난 상담에서 무슨 얘기 했지?"),
        ("P2", "퇴직금을 이미 통장으로 받았다는데, 지금이라도 IRP로 되돌릴 수 있어?"),
        ("P3", "절차가 어떻게 돼? 서류는 뭐가 필요해?"),
        # P3 이 화면번호를 인용하면 연계 제안이 붙고, P4 는 승낙이 아니라 새 질문이라
        # 그 제안이 무효가 된다(기준서 §10). 정상 동작이다 — 딥링크는 K5b 에서 본다.
        # ↓ 핵심 장면 — 환급 완료 전 지급·연금설계 등록 제한과의 선후 충돌을 먼저 세운다.
        # 「권할까?」였을 때는 계획이 화법 축으로만 가서(customer → playbook → pitch) 순서
        # 경고가 든 카드 둘(fact.k04.f47 · proc.043)이 원장에 없었다 — 답변이 그 얘기를 할
        # 수 없었다. 「걸리는 거 없어?」가 계획을 제약 확인 축으로 돌린다. 환급·과세이연을
        # 직원이 먼저 말하지 않으므로 찾아내는 것은 여전히 에이전트 몫이다.
        ("P4", "이 고객 연금개시 요건도 충족했던데, 개시까지 같이 진행하면 걸리는 거 없어?"),
    )),
    # 이수민은 **날짜를 탄다** — 만기 2026-09-26 이고 만기 요건(mat)은 30일 전부터 선다.
    # tests/__init__.py 의 고정값(2026-08-24)은 D-33 이라 요건이 안 서고, L1·L2 가 「만기
    # 임박」·「지금이 그 구간」을 말하지 못한다. PENSION_TODAY=2026-09-01 이상으로 돈다.
    (3, "이수민 — 만기 임박 · 디폴트옵션 미등록 (골든 케이스 05)", "175926-3048171", (
        ("L1", "이 고객 왜 관리 대상이야?"),
        ("L2", "만기 전에 미리 정해둘 방법 있어?"),
        ("L3", "디폴트옵션을 등록하면 지금 있는 현금 1,000만원도 자동으로 굴러가?"),
        # ↑ 핵심 장면 — 등록만으로 기존 현금성자산은 이동하지 않는다(교체매매 세트)
        ("L4", "고객이 원치 않는 상품에 강제 가입되는 거 아니냐고 하면?"),
        ("L4b", "네"),          # 고객 상태에 걸린 화법 제안을 승낙 → 화면 ⑥⑦⑧ 의 나머지 후보
        ("L5", "300만원 더 넣으면 얼마 돌려받아?"),             # 계산기 — 잔여한도 900만·납입 0
    )),
    (4, "송도윤 — 복합 3종 · 10개월 전 기록이 명분 (기존 9케이스)", "188406-7352194", (
        ("S1", "이 고객 왜 관리 대상이야?"),
        ("S2", "지난 상담에서 무슨 얘기 했지?"),                # 실화면에서는 추천 질문 칩
        ("S3", "고객이 '그 돈 그냥 둬도 되지 않나요' 하는데 뭐라고 하지?"),
        ("S4", "그럼 이 고객한테 뭘 권할 수 있어?"),
        # S5 도 실화면에서는 칩이다(suggest.outreach_chips) — 직원은 지금 어떤 세미나가
        # 열려 있는지 모르면 물어볼 생각조차 못 하므로 칩이 떴다는 것 자체가 알림이다.
        ("S5", "이 고객한테 안내할 만한 이벤트나 세미나 있어?"),   # 칩 문구 그대로
        # 승낙해도 나가는 것은 **발송 화면 딥링크와 넣을 문구**뿐이다. 보낼지는 직원이 그
        # 화면에서 정한다(기준서 §10 — 되돌릴 수 없는 대외 행위는 수행하지 않는다).
        ("S5b", "네"),
        ("S6", "타행 IRP 수수료는 우리보다 싼가?"),             # 없는 것은 없다고 한다
    )),
    (5, "정민석 — 같은 질문, 다른 답 (기존 9케이스)", "181245-3097614", (
        ("J1", "이 고객한테 뭘 권할 수 있어?"),                 # 핵심 장면 — S4 직후의 대비축
        ("J2", "고객이 원금 잃는 건 싫다는데, 그래도 권해야 해?"),
        ("J3", "예금만 하겠다는 고객, 뭐라고 설득하지?"),
    )),
)


#: 중간점검 시연 확정본 — `docs/DEMO_FINAL.md`. 기획자가 `SCENARIOS` 에서 세 고객을 추려
#: 문구·순서를 확정한 ①~⑧ 에, 안내 콘텐츠 → 발송 화면 연계(E1·E2)를 김서연 뒤에 더한
#: 것이다. 번호는 기획자 문서의 것을 그대로 쓴다(E 는 이 저장소가 더한 턴). 실행 경로·
#: 화면은 --demo 와 같다. 골라 돌리기: `--final 이수민` / `--final 2 3`.
FINAL: tuple[tuple[int, str, str | None, tuple[tuple[str, str], ...]], ...] = (
    (1, "김서연 — 제도 적용 (타행 ISA 8,000만원 만기 예정)", "171203-4815062", (
        ("①", "ISA 만기자금을 IRP로 옮기면 뭐가 좋아?"),
        ("②", "8천만원 전부는 부담스럽다는데, 일부만 옮겨도 돼?"),
        # ↑ 공제율이 갈리는 총급여 구간을 되물을 수 있다 — 되물으면 E1 로 넘어가기 전에
        # 「초과야」로 닫는다(DEMO_FINAL.md — 금액을 붙이면 계산기가 납입액으로 읽는다). 여기서는 다음 질문으로 바로 간다.
        # 실화면에서는 안내 콘텐츠 칩(suggest.outreach_chips — 김서연은 이벤트만 걸린다, 8/24·9/2
        # 모두 같다). 답변이 이벤트 이름을 인용해야 «발송 화면 열까요?»가 붙는다(act._propose_lms ③).
        ("E1", "이 고객한테 안내할 만한 이벤트 있어?"),
        ("E2", "응, 열어줘"),                                          # 발송 화면 딥링크 + 문구
    )),
    (2, "이수민 — 상담 판단 및 화법 (예금 7,000만원 만기 + 현금성 1,000만원 대기)", "175926-3048171", (
        ("③", "이 고객한테 뭘 권할 수 있어?"),
        ("④", "고객이 '그 돈 그냥 둬도 되지 않나요?' 하는데 뭐라고 하지?"),
        ("⑤", "디폴트옵션 등록하면 지금 있는 1,000만원도 알아서 굴러가?"),
        # ↑ 등록만으로 기존 현금성자산은 이동하지 않는다 — 교체매매 세트(방법론 83)
    )),
    (3, "박정호 — 상담기억에서 업무 실행까지 (퇴직급여 1.5억 일반계좌 수령)", "168450-7293815", (
        ("⑥", "지난 상담에서 무슨 얘기 했지?"),                    # 실화면에서는 추천 질문 칩
        ("⑦", "다시 IRP로 되돌릴 수 있어?"),                        # ⑥ 맥락(퇴직금 통장 수령)을 잇는다
        ("⑧", "과세이연 등록은 어떻게 해?"),                        # proc.041 — 5단계 + 화면번호
    )),
)


def _tools(turn: TR.Turn) -> str:
    """이 턴이 부른 도구를 순서대로. 계획 노드의 한 줄에서 도구 이름만 뽑는다."""
    out = []
    for node in turn.nodes:
        if node.name != "plan_step" or "→" not in node.note:
            continue
        # 마지막 화살표가 구분자다 — LLM 이 쓴 질의 안에 "→" 가 들어올 수 있다(실측:
        # «DB/DC → IRP 소급 적용»을 담은 질의가 첫 화살표에서 잘려 질의 낱말이 카드처럼 찍혔다).
        signature, _, result = node.note.rpartition("→")
        name = signature.split(":")[0].strip()
        out.append(name + ("✗" if "자료 없음" in result else ""))
    return " → ".join(out) or "(없음)"


def _stopped(node: TR.Node | None) -> TR.Gate | None:
    """생성문을 실제로 버린 게이트. 없으면 None.

    **같은 게이트가 한 턴에 여러 번 찍힌다** — 걸린 생성문을 한 번 다시 쓰기 때문이다
    (`plan.COMPOSE_RETRIES`). 목록을 앞에서부터 훑으면 «첫 시도가 걸렸다»가 그대로
    처분으로 읽혀, 다시 써서 통과한 턴이 폐기된 턴으로 보고된다. 마지막 판정이 처분이다
    (`trace.Trace.gates` 도 같은 규약이다 — 이름으로 덮어쓴다).
    """
    if node is None:
        return None
    return next((g for g in {g.name: g for g in node.gates}.values() if not g.passed), None)


def _retries(node: TR.Node | None) -> int:
    """이 턴이 답변을 다시 쓴 횟수. compose LLM 호출 수에서 첫 시도를 뺀 값이다."""
    if node is None:
        return 0
    return max(0, sum(1 for c in node.calls if c.stage == "compose") - 1)


def _log(turn: TR.Turn, result: dict, show_llm: bool = False) -> str:
    """시연용 한 줄 로그 — **어떤 재료가 들어가서 LLM 이 뭐라고 썼나**, 그것만.

    트레이스 전체(`TR.render`)는 노드 순서와 게이트 트리까지 그리는 진단 도구다. 시연에서
    청중이 알고 싶은 건 그게 아니라 «지어낸 게 아니라 이 자료를 보고 쓴 것»이라는 사실
    하나이고, 그건 도구가 무엇을 어떤 질의로 찾아왔는지와 그 카드가 뭔지면 다 보인다.
    검토용(`CASES`)은 폐기 사유를 봐야 하므로 그쪽은 여전히 전체 트레이스를 쓴다.
    """
    titles = {s["id"]: (s.get("title") or "") for s in result.get("sources") or []}
    out = ["   ┌ 무엇을 찾아봤나"]
    step = 0
    for node in turn.nodes:
        if node.name != "plan_step" or "→" not in node.note:
            continue
        step += 1
        # 마지막 화살표가 구분자다(_tools 와 같은 이유 — 질의 안의 "→" 에 잘리지 않게).
        signature, _, found = node.note.rpartition("→")
        tool, _, query = signature.strip().partition(":")
        out.append(f"   │ {step}. {tool.strip()} «{query.strip()}»")
        if "자료 없음" in found:
            out.append("   │      → 없음 (다른 도구로 넘어감)")
            continue
        for token in found.split():
            if token in ("채택",):
                continue
            cid = token.split("(")[0]
            out.append(f"   │      → {cid}  {titles.get(cid, '')}".rstrip())

    node = next((n for n in turn.nodes if n.name == TR.ANSWER_NODE), None)
    stopped = _stopped(node)
    if node is not None and node.delta.get("clarify"):
        out.append("   └ 질문의 갈래가 나뉘어 답 대신 선택지를 되물음 (써 둔 답은 폐기)")
        return "\n".join(out)
    verdict = (f"검증에서 걸림({stopped.name}) — 생성문 폐기" if stopped else
               "근거와 대조 통과" if (node and node.gates) else "대조할 수치 없음")
    if _retries(node):
        # 걸린 자리를 실어 다시 쓴 턴이다(plan.COMPOSE_RETRIES). 이걸 안 적으면 위 «통과»가
        # 첫 시도부터 통과한 것으로 읽히고, 리허설에서 무엇이 아슬아슬했는지가 사라진다.
        verdict = f"{verdict} (한 번 걸려 다시 씀)"
    out.append(f"   └ 위 자료만 보고 LLM 이 {len(result.get('answer') or '')}자 작성 · {verdict}")
    # 폐기된 턴에서 **무엇이 걸렸는지**까지 적는다. 이름만 남기면 화면에 떨어진 원문
    # 덤프를 보고도 «왜 잘렸나»를 알 수 없어, 고칠 것이 질문인지 자료인지 검증기인지
    # 가려지지 않는다 — 리허설에서 제일 먼저 알아야 하는 값이다.
    # span 게이트의 detail 첫 칸은 판정 상수(discard/append)라 사람이 읽을 값이 아니다 —
    # 걸린 스팬·카드만 남긴다.
    if stopped is not None and stopped.detail:
        readable = [str(d) for d in stopped.detail if str(d) not in ("discard", "append", "ok")]
        shown = readable[:6]
        more = f" 외 {len(readable) - len(shown)}건" if len(readable) > len(shown) else ""
        if shown:
            out.append(f"     ↳ 자료에 없다고 본 것: {' · '.join(shown)}{more}")
    if show_llm and stopped is not None:
        draft = next((c.text for n in turn.nodes for c in n.calls
                      if c.stage == "compose" and c.text), "")
        if draft:
            out.append("     ↳ 버려진 생성문:")
            out += [f"       {line}" for line in draft.splitlines()]
    return "\n".join(out)


@contextmanager
def _fixtures_intact():
    """`session_data/` 를 실행 전 상태로 되돌린다.

    골라서 지우지 않고 **통째로 스냅샷·복원**하는 이유는, 무엇이 픽스처이고 무엇이 이번
    실행이 만든 것인지 이 스크립트가 알 필요가 없어서다. 새 고객이 픽스처에 추가돼도
    여기는 손대지 않아도 된다.
    """
    src = config.SESSION_DATA_DIR
    backup = pathlib.Path(tempfile.mkdtemp(prefix="reps-session-"))
    if src.exists():
        shutil.copytree(src, backup / "session_data")
    try:
        yield
    finally:
        if (backup / "session_data").exists():
            shutil.rmtree(src, ignore_errors=True)
            shutil.move(str(backup / "session_data"), str(src))
        shutil.rmtree(backup, ignore_errors=True)


def _pad(text: str, width: int) -> str:
    """한글은 두 칸을 차지한다 — `str.ljust` 로 맞추면 표가 어긋난다."""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - used)


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _row(no: object, sees: str, turn: TR.Turn, secs: float) -> list[str]:
    """요약표 한 줄. 판정하지 않고 «무엇이 일어났나»만 적는다.

    시간 칸은 항상 채워 두고 표시 여부는 출력 쪽이 정한다(--time) — 여기서 칸을 넣다 뺐다
    하면 예외 행과 열 수가 어긋난다.
    """
    names = [n.name for n in turn.nodes]
    node = next((n for n in turn.nodes if n.name == TR.ANSWER_NODE), None)
    stopped = _stopped(node)          # 마지막 판정이 처분이다(재작성 턴 — _stopped 머리말)
    blocked = stopped.name if stopped else None

    # 되묻기는 답변 작성과 **같은 노드**에서 끝난다(nodes/answer.py) — 노드 이름으로는
    # 갈리지 않으므로 상태 차분을 본다. `_compose_note` 는 이 갈래를 따로 적지 않는다.
    if node is not None and node.delta.get("clarify"):
        end = "되묻기로 끝남"
    elif node is not None:
        end = node.note
    elif "llm_down" in names:
        end = "LLM 실패 안내"
    else:
        end = names[-1] if names else "?"

    offer = next((n for n in turn.nodes if n.name == "offer"), None)
    return [
        str(no),
        _tools(turn),
        (f"✗ {blocked}" if blocked else
         f"통과(재작성 {_retries(node)})" if _retries(node) else
         "통과" if (node and node.gates) else "안 걸림"),
        "제안" if (offer and offer.delta) else "",
        f"{secs:.1f}초",
        end,
        sees,
    ]


def _print_answer(r: dict) -> None:
    print(r["answer"])
    ground = [s for s in r["sources"] if s.get("role", "근거") == "근거"]
    caution = [s for s in r["sources"] if s.get("role") == "주의"]
    print("\n─ 근거" + ("" if ground else ": 없음"))
    for s in ground:
        _print_source_line(s)
    if caution:
        print("\n─ 이 고객 상담에서 지켜야 할 것")
        for s in caution:
            _print_source_line(s)


def _print_source_line(s: dict) -> None:
    # 표기는 운영 CLI 와 같은 공용 함수가 정한다(tools.source_lines) — compact 는 이
    # 묶음 화면의 한 줄 표기. 각자 복사하면 한쪽만 고쳐지는 사고가 재현된다.
    from pension_agent.consult_agent.tools import source_lines  # noqa: PLC0415 — graph 적재 뒤

    for line in source_lines(s, compact=True):
        print(line)


def main(argv: list[str]) -> int:
    """검토(`CASES`)와 리허설(`--demo`)이 **같은 실행 경로**를 쓰고 화면만 갈린다 —
    리허설이 다른 경로로 돌면 그 리허설은 시연을 예행한 것이 아니다."""
    demo = "--demo" in argv
    scenario = "--scenario" in argv
    final = "--final" in argv
    brief = "--brief" in argv
    debug = "--debug" in argv
    show_llm = "--show-llm" in argv
    timing = "--time" in argv
    picked = {a for a in argv if a[0].isdigit()}
    #: --scenario · --final 에서만 쓴다 — 고객 이름으로 블록을 고른다(`--scenario 김서연 정민석`).
    names = {a for a in argv if not a.startswith("--") and not a[0].isdigit()}

    unknown = [a for a in argv if a.startswith("--")
               and a not in ("--demo", "--scenario", "--final", "--brief", "--debug",
                             "--show-llm", "--time")]
    if unknown:
        print(f"모르는 옵션입니다: {' '.join(unknown)}")
        print("  옵션: --demo · --scenario [고객명·번호] · --final [고객명·번호] · --brief · "
              "--debug · --show-llm · --time · 케이스 번호")
        return 1
    if demo + scenario + final > 1:
        print("--demo · --scenario · --final 은 함께 쓸 수 없습니다 — 도는 대본이 다릅니다.")
        return 1

    if not LLM.available():
        print("LLM 이 설정돼 있지 않습니다 — 이 스크립트는 실 LLM 으로 도는 것이 목적입니다.")
        print("  genai:     LLM_BASE_URL · LLM_API_KEY   (src/.env 에 두면 됩니다)")
        print("  gemma:     GEMINI_API_KEY")
        print("  anthropic: ANTHROPIC_API_KEY")
        return 1

    # 모드의 차이는 셋뿐이다: 어떤 목록을 도는가 · 턴 라벨을 데이터가 주는가 ·
    # 트레이스를 기본으로 붙이는가. --scenario · --final 은 도는 목록만 다르고 화면·실행
    # 경로는 --demo 리허설과 같다 — 다른 경로로 돌면 시연을 예행한 것이 아니다.
    blocks = FINAL if final else SCENARIOS if scenario else DEMO if demo else CASES
    scenario = scenario or final      # 블록 고르기(이름·번호)는 둘이 같다
    demo = demo or scenario
    trace_by_default = not demo

    rows: list[list[str]] = []
    with _fixtures_intact():
        for no, sees, customer, turns in blocks:
            if picked and not demo and str(no) not in picked:
                continue
            if scenario and (picked or names) and str(no) not in picked \
                    and not any(n in sees for n in names):
                continue
            labelled = (turns if demo else
                        tuple((str(no) if i == 0 else f"{no}b", q) for i, q in enumerate(turns)))
            if demo and not brief:
                print(f"\n{'━' * 70}\n{sees}"
                      + (f"\n(고객 화면 열림: {customer})" if customer else "\n(고객 화면 없음)"))

            # 시연 리허설에서는 화면이 대기 중에 보여주는 진행 줄("⋯ ○○을 찾고 있어요")까지
            # 대본에 나와야 한다 — 응답 대기를 UX 로 보완한 것 자체가 시연 포인트다.
            # ask() 가 도는 동안 콜백이 그 자리에서 찍으므로 질문 줄과 답변 사이에 흐른다.
            show_progress = demo and not brief
            on_progress = (lambda text: print(f"   ⋯ {text}")) if show_progress else None
            with session(customer_id=customer, on_progress=on_progress) as (ask, tr):
                # 고객 화면을 **여는 순간**을 재현한다 — 실서비스·Streamlit 화면은 브리핑을
                # 열 때 AI 브리핑(LLM 11회)을 생성하고(app.py), 대화 턴의 customer 도구는
                # 그 캐시를 읽는다(strategy_agent.propose 의 브리핑 캐시). 여기서 건너뛰면
                # 첫 고객 질문(T4·T13)이 그 생성을 통째로 떠안아 수십 초 걸린다 — 그건
                # 시연에는 없는 대기다. 화면을 여는 시점의 비용은 화면을 여는 자리에 둔다.
                #
                # 그 비용은 **리허설을 돌릴 때마다** 든다(캐시는 프로세스와 함께 사라진다).
                # `python -m scripts.prebuild_briefings` 를 한 번 돌려 두면 여기서 읽어 쓴다 —
                # 건너뛰는 것이 아니라 같은 자리에서 같은 산출을 읽는 것이라, 리허설이
                # 예행하는 경로는 그대로다.
                if demo and customer:
                    from pension_agent.strategy_agent import agent as SA        # noqa: PLC0415
                    from pension_agent.strategy_agent import customer as SC    # noqa: PLC0415
                    t0 = time.monotonic()
                    prof = SC.get_profile(customer)
                    if prof is not None:
                        SA.propose(prof)
                    if timing and not brief:
                        from pension_agent.strategy_agent import briefing_store  # noqa: PLC0415
                        how = ("미리 만들어 둔 것을 읽음" if briefing_store.enabled()
                               else "이번에 생성 — scripts.prebuild_briefings 로 미리 만들 수 있다")
                        print(f"   (브리핑 화면 생성 {time.monotonic() - t0:.1f}초 · {how} — "
                              "화면을 열 때의 일이라 대화 턴에는 들어가지 않는다)")
                for i, (label, question) in enumerate(labelled):
                    if not brief:
                        if demo:
                            print(f"\n{'─' * 70}\n[{label}] > {question}\n")
                        else:
                            who = f"  [고객 {customer}]" if customer else ""
                            print(f"\n{'═' * 70}\n[{label}] {sees}{who}\n> {question}\n")
                    asked = time.monotonic()
                    try:
                        result = ask(question)
                    except Exception as exc:                       # noqa: BLE001 — 한 턴이 죽어도
                        print(f"   실행 중단 — {type(exc).__name__}: {exc}")    # 나머지는 돈다
                        rows.append([label, "—", "—", "", "—", f"예외 {type(exc).__name__}", sees])
                        break
                    took = time.monotonic() - asked
                    if not brief:
                        if show_progress:
                            if timing:
                                print(f"   ⋯ 답변까지 {took:.1f}초")
                            print()   # 진행 줄과 답변을 가른다
                        _print_answer(result)
                    rows.append(_row(label, sees if i == 0 else "└ 이어서", tr.turns[-1], took))
                    if demo and debug and not brief:
                        print()
                        print(_log(tr.turns[-1], result, show_llm=show_llm))
                else:
                    if trace_by_default and not brief:
                        print()
                        print(TR.render(tr))

    print(f"\n{'═' * 70}\n요약 — 도구를 무엇을 어떤 순서로 골랐나\n")
    head = ["#", "도구(순서)", "게이트", "연계", "시간", "처분", "무엇을 보나"]
    table = [head, *rows]
    if not timing:
        drop = head.index("시간")
        table = [[c for i, c in enumerate(r) if i != drop] for r in table]
    widths = [max(_width(r[i]) for r in table) for i in range(len(table[0]))]
    for i, r in enumerate(table):
        print(("  " + "  ".join(_pad(c, w) for c, w in zip(r, widths, strict=True))).rstrip())
        if i == 0:
            print("  " + "  ".join("─" * w for w in widths))
    print("\n  도구 뒤의 ✗ 는 그 호출이 자료를 못 찾은 것 — 다음 칸에서 다른 도구로 옮겨갔는지가 요점입니다.")
    if final:
        print("  리허설에서 볼 것: ② 가 되묻기로 끝나는가(끝나면 「초과야」로 닫는다) ·")
        print("                    E1 에 «발송 화면 열까요?»가 붙고 E2 가 딥링크·문구를 주는가 ·")
        print("                    ③ 이 만기 7천만·대기 1천만·디폴트옵션 미등록을 함께 보는가 ·")
        print("                    ⑤ 가 «이동하지 않는다»로 답하는가 · ⑦ 이 환급 전 제한까지 말하는가 ·")
        print("                    ⑧ 이 5단계와 화면번호를 순서대로 주는가.")
    elif scenario:
        print("  리허설에서 볼 것: K3 이 되묻기로 끝나는가 · K5b 가 딥링크를 주는가 ·")
        print("                    P4 도구 줄에 procedure·fact 가 찍히고 순서 경고를 세우는가 ·")
        print("                    L3 이 «이동하지 않는다»로 답하는가 ·")
        print("                    L4b 가 화법 제안을 승낙받아 카드를 보여주는가 ·")
        print("                    S2 가 10개월 전 기록을 꺼내는가 · S5b 가 발송 «화면»을 여는가 ·")
        print("                    S6 이 «없다»로 끝나는가 · J1 이 S4 와 같은 질문에 다른 답을 내는가.")
        print("  ※ 이수민(3번)은 PENSION_TODAY=2026-09-01 이상이라야 만기 요건이 선다.")
    elif demo:
        print("  리허설에서 볼 것: T9 가 도구를 여러 개 부르는가 · T10 이 suitable 을 부르는가 ·")
        print("                    T3 에 연계가 붙는가 · T11 에 «발송 화면 열까요?»가 붙는가 ·")
        print("                    T12 가 «없다»로 끝나는가 ·")
        print("                    T9 가 «비대면 전환 시 면제»(F53)를 대면 0.38% 와 모순 없이 잇는가.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
