# IRP 세미나·이벤트 DB

> **시연 기준일:** 2026-09-04  
> **용도:** 고객정보와 아래 콘텐츠의 `keywords`를 바탕으로 LLM이 적합한 세미나·이벤트를 직접 선별하고, 추천 사유와 고객 안내 LMS 문구를 생성하기 위한 시연용 DB  
> **주의:** 일정·내용·링크는 시연을 위해 구성한 데이터이며 실제 운영 정보가 아님

---

## 1. 데이터 구조

DB에는 **추천 판단과 생성에 필요한 최소 정보**만 저장합니다.

| 필드 | 설명 |
|---|---|
| `id` | 콘텐츠 고유 ID |
| `type` | `SEMINAR` / `EVENT` |
| `title` | 세미나·이벤트명 |
| `organizer` | 주관 기관 또는 채널 |
| `schedule` | 개최일 또는 이벤트 기간 |
| `keywords` | 고객정보와 매칭할 핵심 키워드 3개 |
| `description` | LLM이 추천 사유·LMS를 작성할 때 참고할 핵심 내용 |
| `url` | 고객 안내용 시연 링크 |
| `golden_dataset` | 목업의 AI 생성 LMS 문구 형식을 바탕으로 만든 평가용 정답 예시 |

> `추천대상`, `추천문구`, `우선순위` 등은 DB에 저장하지 않습니다.  
> 고객의 현재 상태와 `keywords`를 비교해 **추천 여부와 실제 LMS 문구는 LLM이 판단·생성**합니다.  
> `golden_dataset`은 **생성 결과 비교·평가용**이며, 실제 시연 시 LLM 입력 컨텍스트에서는 제외하는 것을 원칙으로 합니다.

---

# 2. 세미나

## SEM-001

- `id`: `SEM-001`
- `type`: `SEMINAR`
- `title`: `연금개시 전 꼭 알아야 할 IRP 수령 방법`
- `organizer`: `KB국민은행 연금사업부`
- `schedule`: `2026-09-08 17:00`
- `keywords`: `연금개시임박`, `연금수령`, `절세`
- `description`: `IRP 연금개시 절차와 일시금·연금 수령 방식의 차이, 수령 시 확인해야 할 세금 관련 주요 사항을 안내하는 온라인 세미나`
- `url`: `https://obank.kbstar.com/demo/seminar/irp-001`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 9/8(화) 17시 'IRP 수령 방법' 온라인 세미나가 열려요.  
  > 연금개시를 앞두고 계시다면 참고해 보세요.  
  > ▶ https://obank.kbstar.com/demo/seminar/irp-001  
  > 무료수신거부 080-XXX-XXXX

---

## SEM-002

- `id`: `SEM-002`
- `type`: `SEMINAR`
- `title`: `퇴직연금 디폴트옵션, 내 계좌도 점검이 필요할까?`
- `organizer`: `KB국민은행 연금사업부`
- `schedule`: `2026-09-10 16:00`
- `keywords`: `미운용현금자산`, `디폴트옵션`, `운용지시`
- `description`: `IRP 계좌 내 운용되지 않고 있는 자금과 디폴트옵션 제도를 이해하고 계좌 운용 상태를 점검하는 방법을 설명하는 온라인 세미나`
- `url`: `https://obank.kbstar.com/demo/seminar/irp-002`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 9/10(목) 16시 '퇴직연금 디폴트옵션 점검' 온라인 세미나를 안내드려요.  
  > 계좌 내 미운용 자금이나 운용지시 상태를 점검하실 때 참고해 보세요.  
  > ▶ https://obank.kbstar.com/demo/seminar/irp-002  
  > 무료수신거부 080-XXX-XXXX

---

## SEM-003

- `id`: `SEM-003`
- `type`: `SEMINAR`
- `title`: `예금만으로 괜찮을까? 퇴직연금 자산배분 전략`
- `organizer`: `KB자산운용`
- `schedule`: `2026-09-15 17:00`
- `keywords`: `원리금편중`, `자산배분`, `TDF`
- `description`: `원리금보장상품 비중이 높은 IRP 고객이 TDF 등 분산형 상품을 활용해 자산을 배분하는 기본 원칙을 소개하는 세미나`
- `url`: `https://obank.kbstar.com/demo/seminar/irp-003`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 9/15(화) 17시 '퇴직연금 자산배분 전략' 온라인 세미나가 열려요.  
  > 예금 중심으로 운용 중이시라면 TDF 등 분산운용 방법을 살펴보실 수 있어요.  
  > ▶ https://obank.kbstar.com/demo/seminar/irp-003  
  > 무료수신거부 080-XXX-XXXX

---

## SEM-004

- `id`: `SEM-004`
- `type`: `SEMINAR`
- `title`: `내 투자성향에 맞는 퇴직연금 포트폴리오 찾기`
- `organizer`: `KB국민은행 WM투자부`
- `schedule`: `2026-09-17 17:00`
- `keywords`: `투자성향불일치`, `포트폴리오`, `리밸런싱`
- `description`: `고객의 투자성향과 실제 IRP 운용자산이 다른 경우 포트폴리오를 점검하고 리밸런싱할 때 고려할 사항을 설명하는 세미나`
- `url`: `https://obank.kbstar.com/demo/seminar/irp-004`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 9/17(목) 17시 '내 투자성향에 맞는 퇴직연금 포트폴리오 찾기' 세미나를 안내드려요.  
  > 투자성향과 현재 운용자산을 함께 점검하고 싶으시다면 참고해 보세요.  
  > ▶ https://obank.kbstar.com/demo/seminar/irp-004  
  > 무료수신거부 080-XXX-XXXX

---

## SEM-005

- `id`: `SEM-005`
- `type`: `SEMINAR`
- `title`: `금리 변화기, 퇴직연금 예금 만기 이후 운용 전략`
- `organizer`: `KB자산운용`
- `schedule`: `2026-09-22 17:00`
- `keywords`: `예금만기예정`, `금리변화`, `재투자`
- `description`: `퇴직연금 정기예금 만기를 앞둔 고객이 금리 환경과 다양한 운용상품을 비교해 만기자금의 재투자 방향을 검토하는 세미나`
- `url`: `https://obank.kbstar.com/demo/seminar/irp-005`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 9/22(화) 17시 '금리 변화기 퇴직연금 운용 전략' 세미나를 안내드려요.  
  > 퇴직연금 예금 만기를 앞두고 계시다면 만기 이후 운용 방향을 살펴보세요.  
  > ▶ https://obank.kbstar.com/demo/seminar/irp-005  
  > 무료수신거부 080-XXX-XXXX

---

# 3. 이벤트

## EVT-001

- `id`: `EVT-001`
- `type`: `EVENT`
- `title`: `IRP 추가입금하고 절세혜택 챙기기 이벤트`
- `organizer`: `KB스타뱅킹`
- `schedule`: `2026-09-07 ~ 2026-09-30`
- `keywords`: `세액공제한도여유`, `추가입금`, `연말정산`
- `description`: `개인형IRP에 추가입금한 고객을 대상으로 추첨을 통해 모바일 쿠폰을 제공하는 시연용 이벤트`
- `url`: `https://obank.kbstar.com/demo/event/irp-001`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 9/30까지 IRP 추가입금 이벤트가 진행돼요.  
  > 올해 세액공제 한도가 남아 있다면 추가입금과 이벤트 내용을 함께 확인해 보세요.  
  > ▶ https://obank.kbstar.com/demo/event/irp-001  
  > 무료수신거부 080-XXX-XXXX

---

## EVT-002

- `id`: `EVT-002`
- `type`: `EVENT`
- `title`: `다른 금융기관 IRP, KB로 이전하고 혜택받기`
- `organizer`: `KB스타뱅킹`
- `schedule`: `2026-09-10 ~ 2026-10-16`
- `keywords`: `타기관IRP잔액보유`, `IRP이전`, `퇴직연금`
- `description`: `다른 금융기관에서 보유 중인 개인형IRP를 KB국민은행으로 이전한 고객을 대상으로 경품 혜택을 제공하는 시연용 이벤트`
- `url`: `https://obank.kbstar.com/demo/event/irp-002`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > 타 금융기관 IRP를 KB로 이전하는 고객 대상 이벤트를 10/16까지 진행해요.  
  > IRP 이전을 검토 중이시라면 자세한 내용을 확인해 보세요.  
  > ▶ https://obank.kbstar.com/demo/event/irp-002  
  > 무료수신거부 080-XXX-XXXX

---

## EVT-003

- `id`: `EVT-003`
- `type`: `EVENT`
- `title`: `ISA 만기자금, IRP로 이어가는 절세 이벤트`
- `organizer`: `KB스타뱅킹`
- `schedule`: `2026-09-14 ~ 2026-10-30`
- `keywords`: `ISA만기`, `IRP전환`, `세액공제`
- `description`: `ISA 만기자금을 개인형IRP로 이전하는 고객에게 관련 절세제도 안내와 경품 혜택을 제공하는 시연용 이벤트`
- `url`: `https://obank.kbstar.com/demo/event/irp-003`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > ISA 만기자금을 IRP로 이전할 때 참고하실 이벤트를 안내드려요.  
  > ISA 만기를 앞두고 계시다면 관련 절세제도와 이벤트 내용을 확인해 보세요.  
  > ▶ https://obank.kbstar.com/demo/event/irp-003  
  > 무료수신거부 080-XXX-XXXX

---

## EVT-004

- `id`: `EVT-004`
- `type`: `EVENT`
- `title`: `잠자는 IRP 자금 깨우기 운용 이벤트`
- `organizer`: `KB스타뱅킹`
- `schedule`: `2026-09-21 ~ 2026-11-13`
- `keywords`: `미운용현금자산`, `운용상품`, `장기미운용`
- `description`: `IRP 계좌 내 장기간 운용되지 않은 자금을 보유한 고객이 운용상품을 선택하거나 변경할 경우 추첨 혜택을 제공하는 시연용 이벤트`
- `url`: `https://obank.kbstar.com/demo/event/irp-004`
- `golden_dataset`:
  > (광고) [고객명] 고객님, KB국민은행입니다.  
  > IRP 계좌의 미운용 자금을 운용하면 참여할 수 있는 이벤트를 안내드려요.  
  > 장기간 운용되지 않은 자금이 있다면 운용상품과 이벤트 내용을 함께 확인해 보세요.  
  > ▶ https://obank.kbstar.com/demo/event/irp-004  
  > 무료수신거부 080-XXX-XXXX

---

# 4. LLM 활용 원칙

LLM은 고객정보와 이 DB를 함께 전달받아 다음 순서로 판단합니다.

1. 고객의 핵심 상태·이슈를 파악한다.
2. 각 콘텐츠의 `keywords`와 고객 상태의 관련성을 비교한다.
3. 관련성이 높은 콘텐츠를 우선 추천한다. 종료일정은 추천하지 않는다.
4. 추천 근거는 고객정보와 DB에 존재하는 정보 안에서만 작성한다.
5. LMS 문구는 고객 상황에 맞춰 새로 생성하되, DB에 없는 혜택·조건·수익률 등은 임의로 만들지 않는다.
6. 적합한 콘텐츠가 없으면, 누구에게나 적용될 수 있는 통용적인 콘텐츠(시장전략 세미나 등)를 추천한다.
7. `golden_dataset`은 생성 결과의 품질을 비교하기 위한 평가 기준으로만 사용하며, 실제 생성 시 LLM 입력값에는 포함하지 않는다.
