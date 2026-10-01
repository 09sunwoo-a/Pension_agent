const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType,
  ShadingType, AlignmentType, HeadingLevel, LevelFormat, BorderStyle, PageBreak,
  Footer, Header, PageNumber, TableOfContents, VerticalAlign,
} = require("docx");

const FONT = "맑은 고딕";
const NAVY = "1F3864";
const KB = "7F6000"; // muted gold accent
const GRAY = "595959";
const W = 9638; // A4 text width with ~2cm margins (11906 - 2*1134)

const t = (text, o = {}) => new TextRun({ text, font: FONT, ...o });
const p = (runs, o = {}) =>
  new Paragraph({
    children: (Array.isArray(runs) ? runs : [runs]).map((r) => (typeof r === "string" ? t(r) : r)),
    spacing: { after: 100, line: 300 },
    ...o,
  });
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [t(text)], spacing: { before: 360, after: 160 } });
const h2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [t(text)], spacing: { before: 240, after: 120 } });
const bullet = (runs, level = 0) =>
  new Paragraph({
    numbering: { reference: "bul", level },
    children: (Array.isArray(runs) ? runs : [runs]).map((r) => (typeof r === "string" ? t(r) : r)),
    spacing: { after: 60, line: 290 },
  });
const b = (text) => t(text, { bold: true });
const note = (text) => p([t(text, { size: 18, color: GRAY, italics: true })]);

const border = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
const borders = { top: border, bottom: border, left: border, right: border };

function cell(content, width, o = {}) {
  const lines = Array.isArray(content) ? content : [content];
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    borders,
    verticalAlign: VerticalAlign.CENTER,
    shading: o.fill ? { type: ShadingType.CLEAR, color: "auto", fill: o.fill } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: lines.map(
      (line) =>
        new Paragraph({
          alignment: o.center ? AlignmentType.CENTER : AlignmentType.LEFT,
          spacing: { after: 20, line: 260 },
          children: (Array.isArray(line) ? line : [line]).map((r) =>
            typeof r === "string" ? t(r, { size: 18, bold: !!o.bold, color: o.color }) : r
          ),
        })
    ),
  });
}

function table(headers, rows, widths, o = {}) {
  const total = widths.reduce((a, c) => a + c, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: headers.map((h, i) => cell(h, widths[i], { fill: NAVY, bold: true, color: "FFFFFF", center: true })),
      }),
      ...rows.map(
        (r, ri) =>
          new TableRow({
            children: r.map((c, i) =>
              cell(c, widths[i], {
                fill: i === 0 && o.firstColFill ? "EEF2F8" : ri % 2 === 1 ? "F7F7F7" : undefined,
                bold: i === 0 && o.firstColBold,
                center: o.centerCols && o.centerCols.includes(i),
              })
            ),
          })
      ),
    ],
  });
}

// 강조 박스 (1셀 표)
function box(title, lines, fill = "FFF8E5", edge = "C9A227") {
  const e = { style: BorderStyle.SINGLE, size: 8, color: edge };
  return new Table({
    width: { size: W, type: WidthType.DXA },
    columnWidths: [W],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: W, type: WidthType.DXA },
            borders: { top: e, bottom: e, left: e, right: e },
            shading: { type: ShadingType.CLEAR, color: "auto", fill },
            margins: { top: 140, bottom: 140, left: 200, right: 200 },
            children: [
              new Paragraph({ spacing: { after: 100 }, children: [t(title, { bold: true, size: 22, color: NAVY })] }),
              ...lines.map(
                (l) =>
                  new Paragraph({
                    numbering: { reference: "bul", level: l && l.lvl ? l.lvl : 0 },
                    spacing: { after: 60, line: 290 },
                    children: ((l && l.runs) || (Array.isArray(l) ? l : [l])).map((r) => (typeof r === "string" ? t(r) : r)),
                  })
              ),
            ],
          }),
        ],
      }),
    ],
  });
}
const gap = () => new Paragraph({ spacing: { after: 80 }, children: [] });

// ───────────────────────── 본문 ─────────────────────────
const cover = [
  new Paragraph({ spacing: { before: 2400 }, children: [] }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [t("WM고객그룹 대표 보고", { size: 26, color: GRAY })], spacing: { after: 240 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [t("퇴직연금 AI 사후관리 에이전트", { size: 44, bold: true, color: NAVY })], spacing: { after: 120 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [t("PoC 수행 결과 보고", { size: 32, bold: true, color: NAVY })], spacing: { after: 480 } }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    border: { top: { style: BorderStyle.SINGLE, size: 12, color: KB, space: 12 } },
    children: [t("우리가 보고 · 배우고 · 만들어 본 것, 그리고 넘어야 할 산", { size: 22, color: GRAY, italics: true })],
    spacing: { after: 2400 },
  }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [t("2026. 10.", { size: 24 })], spacing: { after: 120 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [t("WM고객그룹 WM AI COE", { size: 26, bold: true })], spacing: { after: 120 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [t("권현진 과장 · 이선우 대리 · 정석희 대리", { size: 22, color: GRAY })] }),
  new Paragraph({ children: [new PageBreak()] }),
];

const summary = [
  h1("보고 요지"),
  box("한 줄 결론", [
    [b("행내 환경에서 «AI 에이전트가 실제로 작동한다»는 것을 확인했다. "), t("기획 1명·개발 2명이 퇴직연금(개인형IRP) 사후관리 에이전트를 PoC 단계까지 구현해 스타뱅킹 개발서버에 배포했고, 행내 GenAI 플랫폼·WorkB 쪽지와 실제로 연동했다.")],
    [b("직원 누구나 일관된 고품질 사후관리 상담을 할 수 있게 된다. "), t("상담 준비시간은 42분에서 18분 수준으로 줄어드는 것으로 산정했다. 그러나 더 큰 효과는 그동안 사후관리를 하지 못하던 직원도 할 수 있게 되고, 직원마다 편차가 크던 상담 품질이 높은 수준으로 상향평준화된다는 점이다.")],
    [b("다만 운영 배포까지는 거리가 있다. "), t("고객 데이터는 시연용 목업(12명)이고, 일부 기능은 시연 수준이다. 실데이터 연동·지식 관리 체계·컴플라이언스 검토가 다음 관문이다.")],
    [b("가장 큰 성과: AI의 현업 적용을 위한 선행 조건을 실증적으로 확인")],
    { lvl: 1, runs: [t("답변 품질은 모델 자체보다 ① 행내 지식의 구조화 수준, ② 근거 범위 내 답변을 강제하는 통제 설계에 좌우됨 (동일 모델 기준)")] },
    { lvl: 1, runs: [t("향후 AI 적용 시 지식 자산화 및 통제 체계 선(先)구축 필요")] },
  ]),
  gap(),
  table(
    ["구분", "핵심 내용"],
    [
      ["추진 기간", "2026. 7. 14.(화) ~ 10. 2.(금), 총 12주 (교육 2주 + 프로젝트 10주)"],
      ["추진 목적", "행내 환경에서 무엇이 작동하고, 어디까지 구현 가능하며, 업무에 AI를 녹이려면 무엇이 필요한지 직접 확인 → WM고객그룹 AI 확산의 기준점 확보"],
      ["실현 수준", "PoC 완료 · 스타뱅킹 개발서버 배포 · 행내 GenAI 플랫폼 호출 · WorkB 쪽지 발송(MCP) 실연동. 백엔드 고객 데이터는 더미, 일부 기능은 시연 수준"],
      ["도입 효과", "상담 준비시간 42분 → 18분(약 57% 단축, 산정치) · MyStar 단말 원클릭 연동 · 상담이력 작성~재접촉 일정 관리 수행 · 사후관리 수행 가능 인력 확대 · 상담 품질 상향평준화"],
      ["주요 산출물", "AI 브리핑 화면(9개 섹션) · 대화형 상담 에이전트 · 지식카드 약 720장 · 타겟 룰베이스 14종 · 질문 검증표 164문항"],
      ["핵심 교훈", "① 지식데이터가 성능을 결정 ② LLM은 «근거 안에서만» 말하게 통제해야 함 ③ 행내 인프라는 문서와 실제가 다름(실측 필수) ④ 현업 참여 없이는 지식이 낡음"],
      ["넘어야 할 산", "지식데이터화·최신화 체계, 현업 부서의 지식 오너십·사후관리, 실데이터 연동, 운영 인프라, 품질 평가 체계, 현장 정착"],
      ["고려사항", "금융소비자보호법(적합성·설명의무), 광고심의, 개인정보·신용정보 보호, 망분리·혁신금융서비스, AI 기본법·금융권 AI 가이드라인, 책임 소재"],
      ["요청 사항", "① 현업 지식 오너 지정 ② 실데이터 파일럿 승인·데이터 접근 ③ 컴플라이언스 협의체 ④ 후속 과제 우선순위 결정"],
    ],
    [1800, W - 1800],
    { firstColFill: true, firstColBold: true }
  ),
  new Paragraph({ children: [new PageBreak()] }),
];

const toc = [
  new Paragraph({ children: [t("목차", { size: 28, bold: true, color: NAVY })], spacing: { after: 200 } }),
  ...["1. 추진 배경 및 목적","2. 추진 개요","3. 무엇을 만들었나 — 구현 내용","4. 어디까지 실현했나 · 도입 효과","5. 경험하고 배운 것","6. 한계와 단점","7. 넘어야 할 산","8. 제도 · 혁신금융 · 컴플라이언스 고려사항","9. 향후 추진 방향 (안)","10. 요청 및 건의 사항","부록. 용어 설명"].map((x) =>
    new Paragraph({ spacing: { after: 160 }, indent: { left: 200 }, children: [t(x, { size: 23 })] })),
  new Paragraph({ children: [new PageBreak()] }),
];

const s1 = [
  h1("1. 추진 배경 및 목적"),
  h2("1.1 왜 «퇴직연금 사후관리»였나"),
  bullet([b("관리가 비어 있는 자산. "), t("IRP는 가입 이후 현금성자산 방치, 원리금보장 편중, 만기 후 미운용 등 «사후관리 공백»이 크고, 이는 수익률 저하와 타 금융기관 이전(이탈)으로 이어진다.")]),
  bullet([b("지식은 있는데 현장에 닿지 않는다. "), t("연금사업부·연금컨설팅부의 가이드, 스타런 교육자료, 영업점 Hot Tip 등 좋은 자료가 이미 있지만 흩어져 있어, «자료 위치를 아는 것»이 노하우가 된 상황이다.")]),
  bullet([b("AI 적용의 시험대로 적합. "), t("고객 선정(룰) → 상태 진단(데이터) → 다음 행동 제안(지식) → 실행(화면·발송)까지 한 업무 흐름 안에 AI 적용의 전 단계가 들어 있다.")]),
  h2("1.2 이 과제로 얻고자 한 것"),
  p("이 과제의 목적은 «퇴직연금 에이전트 하나»를 만드는 것에 그치지 않는다. 아래 세 질문에 답을 얻어 WM고객그룹 전반에 적용할 기준점을 만드는 것이다."),
  table(
    ["질문", "확인하고자 한 것"],
    [
      ["무엇이 작동하는가", "행내 GenAI 플랫폼·게이트웨이·내부망·행내 시스템(MCP) 위에서 에이전트가 실제로 도는가"],
      ["어디까지 가능한가", "지식 기반 질의응답, 고객별 개인화 제안, 업무 화면 연계, 메모·발송 등 «행동»까지 어디까지 맡길 수 있는가"],
      ["무엇이 필요한가", "지식데이터·고객데이터·인프라·통제장치·조직 역할 중 무엇이 갖춰져야 실제 업무에 녹일 수 있는가"],
    ],
    [2400, W - 2400],
    { firstColFill: true, firstColBold: true }
  ),
];

const s2 = [
  h1("2. 추진 개요"),
  table(
    ["항목", "내용"],
    [
      ["수행 조직", "WM고객그룹 WM AI COE — 권현진 과장, 이선우 대리, 정석희 대리 (기획 1명 · 개발 2명)"],
      ["추진 기간", "2026. 7. 14.(화) ~ 10. 2.(금), 총 12주 — AI 교육 2주 + 프로젝트 10주"],
      ["사용자", "영업점 직원 (퇴직연금·개인형IRP 상담 담당)"],
      ["대상 업무", "당행 개인형IRP 보유 고객의 사후관리 (신규 유치·기업형 DB/DC 전용 업무는 범위 제외)"],
      ["진행 단계", "기획 → 지식베이스 구축 → 에이전트 개발 → 행내 플랫폼 연동 → 스타뱅킹 개발서버 배포 → 시연(최종시연 2026.9.29)"],
      ["활용 지식", "행내 가이드 12건 · 스타런 교육자료 15건 · 영업점 Hot Tip 50건 · KB Think 5건 · 시황·상품 4건 → 주제별 추출지식 → 지식카드 약 720장"],
      ["개발 방식", "AI 코딩 도구를 적극 활용해 소규모 인원으로 빠르게 반복(시연 리허설 → 실측 → 수정을 판(version) 단위로 관리)"],
    ],
    [2000, W - 2000],
    { firstColFill: true, firstColBold: true }
  ),
  gap(),
  h2("2.1 에이전트가 돕는 영업의 시간축"),
  p("현장 행원 인터뷰와 Hot Tip 50건 분석을 바탕으로, «무엇을»보다 «언제, 어떤 형태로» 주느냐를 설계의 중심에 두었다."),
  table(
    ["영업 시점", "제공 형태", "PoC 구현"],
    [
      ["① 영업 전", "AI 고객 브리핑 — 30초 안에 읽는 한 장", "구현 (9개 섹션)"],
      ["② 영업 중", "채팅형 상담 에이전트 — 지식·화법·고객정보 질의응답", "구현"],
      ["③ 영업 후", "상담이력 작성·등록·재접촉 일정 관리, 고객 안내 문자 발송 화면 연계", "구현 (일부 시연 수준)"],
      ["④ 평상시", "대시보드 — 관리 대상 고객·이달의 갱신 수치", "기획 단계 (미구현)"],
    ],
    [1800, 5338, 2500],
    { firstColFill: true, firstColBold: true }
  ),
];

const s3 = [
  h1("3. 무엇을 만들었나 — 구현 내용"),
  h2("3.1 AI 고객 브리핑 (영업 전)"),
  p("관리 대상 고객 한 명에 대해, 전화 걸기 전 30초 안에 읽을 수 있는 한 장을 자동으로 만든다. 섹션마다 «규칙(코드)이 정하는 것»과 «AI(LLM)가 쓰는 것»을 나눠 두었다."),
  table(
    ["섹션", "내용", "생성 방식"],
    [
      ["① AI 브리핑", "오늘의 제안 1개 + 예비 1개", "규칙이 재료·후보 선정 → AI가 문장화"],
      ["② 왜 이 고객인가", "관리 대상이 된 근거 수치", "규칙(수치) + AI(해석)"],
      ["③ 현재 운용상태", "적립금·구성·수익률·만기 요약", "AI (근거 데이터 안에서)"],
      ["④ 상위 1% 고객 보유상품", "수익률 상위 고객이 많이 담은 상품(참고용)", "규칙(데이터) + AI(한 줄 해석)"],
      ["⑤ 적합 상품", "투자성향 적합성을 통과한 후보 중 선택", "AI — 닫힌 후보군 안에서만 선택"],
      ["⑥ 이렇게 말해보세요", "바로 입에 넣을 수 있는 상담 화법 2개", "규칙 선정 + AI 스크립트화"],
      ["⑦ 예상 반론과 대응", "이 고객에게 나올 법한 반론 2개와 답", "반론 DB + AI 선별"],
      ["⑧ 상담에 참고하세요", "관리 방법론·노하우·업무 화면번호", "자료 DB + AI 선별"],
      ["⑨ 고객님께 안내해보세요", "세미나·이벤트 추천 + 안내 문자(LMS) 초안", "콘텐츠 DB + AI 추천사유·본문"],
    ],
    [2300, 4000, 3338],
    { firstColFill: true, firstColBold: true }
  ),
  gap(),
  h2("3.2 대화형 상담 에이전트 (영업 중·후)"),
  p("직원이 상담 중에 묻는 질문에 지식베이스와 고객 정보를 근거로 답한다. AI가 «어떤 근거를 모을지» 스스로 계획하되, 쓸 수 있는 도구와 반복 횟수, 답변 검증은 코드가 통제한다."),
  table(
    ["능력", "시연 예시"],
    [
      ["지식을 깊게 설명", "«IRP 넣으면 55세까지 못 빼지 않나?» → 법정 중도인출 사유 6가지와 기타소득세 16.5% 영향"],
      ["과거 맥락을 찾음", "«지난 상담에서 무슨 얘기 했지?» → 지난 통화 기록(퇴직급여 1.5억 일반계좌 수령 등) 요약"],
      ["고객별 개인화", "같은 질문도 고객 상태(만기·현금성자산·투자성향)에 따라 다른 제안"],
      ["실제 업무까지 연결", "답변에서 MyStar 단말 업무 화면 원클릭 연결, 고객 안내 문자 발송 화면 열기, 상담이력 작성·등록 및 재접촉 일정 관리"],
    ],
    [2300, W - 2300],
    { firstColFill: true, firstColBold: true }
  ),
  gap(),
  h2("3.3 그 밖의 구성 요소"),
  bullet([b("지식베이스: "), t("행내 문서를 주제별로 추출·정규화한 지식카드 약 720장. 모든 카드가 원문 인용과 출처를 가지며, 원문은 고치지 않는다는 원칙을 지켰다.")]),
  bullet([b("타겟 룰베이스: "), t("행내 원문(IRP 텐션 UP 등)을 기획자가 정규화한 타겟 14종. 각 기준의 근거 등급(A 원문 명시 ~ D 설계 제안)을 함께 관리한다.")]),
  bullet([b("품질 관리 도구: "), t("질문 검증표 164문항(5개 구분·26개 항목), 시연 시나리오 판 관리, 자동 테스트, «무엇이 더미인지»를 자동 집계하는 데모 상태 리포트.")]),
];

const s4 = [
  h1("4. 어디까지 실현했나 · 도입 효과"),
  p("«돌아간다»와 «운영할 수 있다»는 다르다. 실제로 작동한 것, 시연 수준인 것, 아직 더미인 것을 구분해 보고드린다."),
  table(
    ["영역", "상태", "설명"],
    [
      ["행내 GenAI 플랫폼 호출", "실제 작동", "플랫폼 게이트웨이를 통한 호출·응답 성공 (실측 후 응답 형식 조정)"],
      ["스타뱅킹 개발서버 배포", "실제 작동", "내부망 컨테이너 배포 완료, 화면에서 실제 동작 확인"],
      ["WorkB 쪽지 발송(MCP)", "실제 작동", "상담 요약을 직원 쪽지함으로 실제 발송 (직원 승낙 후에만)"],
      ["지식 기반 질의응답", "작동 (검증 진행 중)", "164문항 실측 답변 확보. 답변 품질은 지속 보정 필요"],
      ["AI 브리핑 9개 섹션", "작동", "고객 데이터가 더미이므로 내용은 시연용"],
      ["고객 데이터", "더미", "시연용 목업 고객 12명. 실제 원장·CRM 미연동"],
      ["금리·시황", "더미", "자리표시자 금리표 6종. 실제 시세 피드 미연동"],
      ["세미나·이벤트 콘텐츠", "부분", "연금사업부 확인 DB 기반이나 일정·링크는 시연값"],
      ["MyStar 단말 화면 연동", "작동 (개발 모드)", "대화형 답변에서 업무 화면으로 원클릭 연결. 운영 모드 전환 필요"],
      ["상담이력 관리", "시연 수준", "작성·등록·재접촉 일정 관리 흐름 구현. 이력은 데모 저장소에 저장(행내 CRM 미연동), 재접촉 예약 쪽지는 접수 즉시 발송(시연용)"],
      ["고객 안내 문자", "시연 수준", "발송 화면을 열고 문구만 제공. 발송 여부는 직원이 결정"],
      ["대시보드(평상시)", "미구현", "기획 단계"],
    ],
    [2600, 1700, W - 4300],
    { firstColFill: true, firstColBold: true, centerCols: [1] }
  ),
  gap(),
  h2("4.1 실데이터 전환 시 먼저 확인할 데이터"),
  p("데이터딕셔너리에서 대응 컬럼을 찾지 못해 데모에서는 파생값으로 채운 항목이 7개 있다. 실데이터 전환의 첫 번째 확인 대상이다."),
  table(
    ["필드", "현황", "영향"],
    [
      ["만기 잔여일·만기금액", "«만기» 컬럼 없음", "만기 임박 고객 선정"],
      ["잔여 세액공제 한도", "«한도» 컬럼 없음(납입누계 역산 필요)", "추가납입·절세 제안"],
      ["총급여 구간", "퇴직급여 추계용 컬럼만 존재", "세액공제율(13.2/16.5%) 판정"],
      ["최근 접촉 경과일", "CRM 조인 필요", "장기 미접촉 고객 선정"],
      ["수익률 백분위", "피어그룹 산출 필요", "저수익 고객 선정"],
      ["연금수령 개시 여부", "개시가능 잔여일만 존재", "개시 고객 추가납 권유 금지(민원 방지)"],
      ["고객 위험등급", "컬럼 없음(투자성향 파생값 사용)", "상품 적합성 판정"],
    ],
    [2600, 3600, W - 6200],
    { firstColFill: true, firstColBold: true }
  ),
  gap(),
  h2("4.2 도입 효과"),
  p([b("직접적인 효과는 시간 단축보다 «할 수 없던 일을 할 수 있게 하는 것»이다. "), t("사후관리 상담을 하지 못하던 직원도 수행할 수 있게 되고, 품질을 보장할 수 없던 상담이 일관된 고품질 상담으로 상향평준화된다. 여기에 업무 화면 이동과 상담 후 이력 관리까지 에이전트가 맡아 상담 전·중·후 전 과정의 부담을 줄인다.")]),
  table(
    ["구분", "현재", "에이전트 활용 시"],
    [
      ["상담 준비시간 (고객 1명)", "약 42분 — 고객 현황 조회, 자료·화법 검색, 상품 비교를 직원이 직접 수행", "약 18분 — 브리핑·화법·업무 화면번호가 미리 준비된 상태에서 확인·보완 (약 24분, 57% 단축)"],
      ["업무 화면 이동 (단말 연동)", "답변·자료에서 화면번호를 확인한 뒤 직원이 MyStar 단말에서 직접 찾아 이동", "대화형 답변에서 MyStar 단말 화면으로 원클릭 연동 — 화면 탐색 없이 바로 업무 처리 (사용자 UI 개선)"],
      ["상담이력 관리", "상담 후 직원이 이력 내용을 직접 작성·등록하고 재접촉 일정을 별도로 관리", "에이전트가 상담이력 내용 작성 → 상담이력 등록 → 재접촉 일정 관리까지 수행"],
      ["사후관리 수행 가능 인력", "연금 경험이 많은 일부 직원에 의존. 경험이 부족한 직원은 사후관리 자체를 수행하기 어려움", "신입~중견 직원도 브리핑과 대화형 에이전트의 도움으로 사후관리 상담 수행 가능"],
      ["상담 품질", "직원 역량에 따라 편차가 크고 품질을 보장하기 어려움", "행내 지식베이스 기반의 일관된 근거·화법·주의사항 제공 → 고품질 상담의 상향평준화"],
    ],
    [2300, 3500, W - 5800],
    { firstColFill: true, firstColBold: true }
  ),
  note("※ 준비시간은 PoC 환경에서 업무 단계별로 산정한 값이며, 실데이터 파일럿에서 실측으로 검증할 예정이다."),
];

const s5 = [
  h1("5. 경험하고 배운 것"),
  h2("5.1 동일 모델에서 품질을 좌우한 것은 «지식»과 «통제 설계»였다"),
  bullet("같은 모델이라도 지식카드가 정리된 질문은 정확하게, 지식이 비어 있거나 흩어진 질문은 엉뚱하게 답했다. 품질 개선 작업의 대부분은 프롬프트가 아니라 지식 정리였다."),
  bullet("행내 문서는 사람이 읽기 위한 형식(PPT·공문·게시글)이라 AI가 쓰려면 «추출 → 정규화 → 출처·기준시점 부여»가 필요하다. 이 작업에 개발보다 많은 시간이 들었다."),
  bullet("수치는 시효가 있다. 금리·수익률·한도 같은 숫자는 원문을 고치지 않고 «최신값 + 참고 표시»로 갈아 끼우는 구조가 필요했다. «출처는 진짜인데 수치는 낡은 카드»가 가장 위험하다."),
  h2("5.2 금융 업무의 AI는 «근거 안에서만» 말하게 통제해야 한다"),
  bullet([b("역할 분리: "), t("수치·상품·적합성 계산은 코드가, 문장 표현은 AI가 맡는다. AI가 근거 밖 숫자를 말하면 코드가 검증해 잘라낸다.")]),
  bullet([b("하지 말 것이 할 것만큼 중요: "), t("연금개시 고객에게 추가납입 권유 금지, 금지 용어(«고유계정대»→«현금성자산»), 근거 없는 «업계 1위» 주장 차단 등 금지 규칙을 코드로 강제했다.")]),
  bullet([b("되돌릴 수 없는 행위는 사람이 결정: "), t("고객 문자는 에이전트가 보내지 않고 발송 화면만 열어 준다. 직원 본인에게 가는 쪽지도 승낙을 받은 뒤에만 보낸다.")]),
  h2("5.3 행내 인프라는 «문서»와 «실제»가 다르다 — 실측이 답이다"),
  table(
    ["실측에서 만난 문제", "배운 점"],
    [
      ["플랫폼 문서의 응답 형식대로 보냈더니 게이트웨이가 읽지 못함", "문서만 믿지 말고 개발서버에서 일찍, 자주 실측해야 한다"],
      ["고객번호(주민번호 형태)가 게이트웨이 «기본필터»에 막힘", "보안 필터는 업무 데이터 형식까지 고려한 설계가 필요하다(인코딩해 전달)"],
      ["호출이 몰리면 게이트웨이가 429(호출 제한)를 반환", "한 질문에 AI 호출이 3~14회 일어난다. 동시 사용자 확대 시 호출량 관리가 필수다"],
      ["MCP로 행내 시스템(WorkB)에 실제 연결 성공", "«말하는 AI»에서 «일하는 AI»로 갈 수 있는 통로가 행내에 이미 있다"],
    ],
    [4300, W - 4300]
  ),
  gap(),
  h2("5.4 현장의 목소리 — 행원은 무엇을 원하나 (Hot Tip 50건 분석)"),
  bullet("«이게 몇 점인가» — KPI 인정 여부와 인정 조건까지 함께 알려줘야 현장이 움직인다."),
  bullet("«16.5% 공제»보다 «148만원 환급» — 비율이 아니라 원 단위 금액이 상담을 연다."),
  bullet("«자료는 이미 있다, 못 찾을 뿐» — 찾아가는 게 아니라 필요한 순간에 도착해 있어야 한다."),
  bullet("«일단 챗봇에게 물어봤다» — 묻는 습관은 이미 있다. 새 습관이 아니라 기존 습관의 품질을 올리는 것이 정착의 열쇠다."),
  bullet("«신입 가이드»가 조회 1위 — 1차 수혜자는 신입~중견 직원이며, 완성 대사·화면번호·처리 순서까지 담긴 완성형 출력이 필요하다."),
  h2("5.5 일하는 방식에 대한 교훈"),
  bullet("기획자가 «무엇이 맞는 답인가»를 정의하고 개발자가 그것을 테스트로 고정하는 구조가 품질을 끌어올렸다. 정답 기준(골든셋) 없이는 AI 품질을 관리할 수 없다."),
  bullet("AI 코딩 도구 덕분에 3명이 짧은 기간에 많은 반복을 할 수 있었다. 다만 빠르게 만든 만큼 «무엇이 더미이고 무엇이 진짜인지»를 기록하는 장치가 반드시 함께 있어야 한다."),
];

const s6 = [
  h1("6. 한계와 단점"),
  table(
    ["구분", "내용"],
    [
      ["답변의 비결정성", "같은 질문에도 문장이 매번 달라진다. 통제 장치로 «틀린 수치»는 막았지만, 표현·강조점의 일관성은 완전히 보장되지 않는다."],
      ["지식 의존성", "지식베이스에 없는 내용은 답하지 못한다(의도된 설계). 지식이 낡으면 답도 낡는다 — 지속 관리 없이는 품질이 급격히 떨어진다."],
      ["응답 속도·비용", "한 질문에 AI 호출 3~14회. 응답이 수 초~수십 초 걸릴 수 있고, 사용자 확대 시 호출 비용·한도가 병목이 된다."],
      ["데모 구조의 한계", "단일 프로세스 전제로 만들어 다중 서버 환경에서 상담이력 유실, 화면-대화 답변 불일치, 호출 제한 무력화 등이 생길 수 있다(정리 완료, 조치 필요)."],
      ["판단 기준의 근거 수준", "타겟 기준 일부(원리금보장 편중 80%, 리밸런싱 미실시 12개월)는 행내 문서에 없는 설계 제안값이라 실데이터로 보정해야 한다."],
      ["과신 위험", "자연스러운 문장 때문에 직원이 AI 답을 검증 없이 고객에게 전달할 수 있다. 출처 표시·주의 문구·교육이 함께 필요하다."],
    ],
    [2200, W - 2200],
    { firstColFill: true, firstColBold: true }
  ),
];

const s7 = [
  h1("7. 넘어야 할 산"),
  h2("7.1 지식데이터화 — 가장 크고 오래 걸리는 산"),
  bullet([b("흩어진 지식의 구조화: "), t("본부 공문·가이드·교육자료·게시판이 각기 다른 형식이다. AI가 쓸 수 있도록 카드화하고 출처·기준시점·적용 조건을 붙이는 작업이 상품·업무마다 필요하다.")]),
  bullet([b("암묵지의 명시화: "), t("«이런 고객은 이렇게 한다»는 현장 판단은 문서에 없다. 판단 기준을 표로 정규화하고 근거 등급을 매기는 작업은 현업 전문가 없이는 불가능하다.")]),
  bullet([b("신뢰 등급 관리: "), t("본부 공식 / 현장 팁 / 고객 공개 가능 여부를 모든 지식에 표시해야 한다. 이것이 없으면 AI가 사내용 자료를 고객 안내에 섞을 수 있다.")]),
  h2("7.2 현업 부서의 사후관리 — 지식의 «주인»이 필요하다"),
  bullet("제도·세법·금리·상품 라인업·이벤트는 계속 바뀐다. 지식을 만드는 것보다 «최신으로 유지하는 것»이 더 어렵다."),
  bullet("COE가 모든 지식을 관리할 수는 없다. 연금사업부 등 현업 부서가 지식의 오너가 되어 변경 시 갱신·검수하는 체계(R&R, 갱신 주기, 승인 절차)가 필요하다."),
  bullet("AI 답변 오류 신고 → 원인 지식 수정 → 재검증의 운영 루프와 담당 조직이 정해져야 한다."),
  h2("7.3 실데이터 연동"),
  bullet("고객 원장·CRM 상담이력·상품 마스터·시세 데이터의 연동과 데이터 접근 권한 협의."),
  bullet("데이터딕셔너리에 없는 7개 필드의 소스 확정(만기·세액공제 한도·접촉 이력 등)."),
  h2("7.4 운영 인프라와 확장성"),
  bullet("다중 서버 환경 대응(상담이력 DB화, 브리핑 공유 저장소, 호출 제한 공동 관리), 동시 사용자 처리, 응답 속도 개선."),
  bullet("관측(모니터링) 도구가 프롬프트 전문을 저장하므로, 실데이터 전환 전 행내 자체 구축 또는 개인정보 마스킹이 필수다."),
  h2("7.5 품질 평가 체계"),
  bullet("«좋은 답»의 기준을 현업이 정의한 평가셋(골든셋)으로 만들고, 지식·모델 변경 때마다 회귀 평가하는 체계가 필요하다."),
  h2("7.6 현장 정착(변화관리)"),
  bullet("직원이 실제로 쓰게 하려면 기존 업무 화면 안에 들어가야 하고, KPI·업무 동선과 연결되어야 한다. 교육과 «AI 답은 직원이 최종 확인한다»는 사용 원칙 수립이 함께 필요하다."),
];

const s8 = [
  h1("8. 제도 · 혁신금융 · 컴플라이언스 고려사항"),
  p("아래 항목은 실서비스 전환 전 준법감시·법무·정보보호 부서와 반드시 사전 협의해야 할 사항이다. (본 보고서는 검토 필요 항목을 정리한 것이며, 법적 판단은 소관 부서 검토를 따른다.)"),
  table(
    ["영역", "고려사항", "PoC에서의 대응 / 향후 과제"],
    [
      ["금융소비자보호법", "적합성·적정성 원칙, 설명의무, 부당권유 금지. AI가 투자성향에 맞지 않는 상품을 권하거나 수익을 단정하면 위반 소지", "적합성 판정을 코드로 강제(상품 위험등급 상한), «과거 수익률은 보장이 아님» 표시. 판매 프로세스 내 AI 역할 정의 필요"],
      ["광고·안내 문자 규제", "금융상품 광고 심의, 광고 표기, 수신거부 안내, 야간 발송 제한 등", "문자 골격(광고 표기·수신거부)은 코드가 조립, AI는 본문만. 실제 발송은 직원 결정. 사전 심의 절차 연계 필요"],
      ["개인정보·신용정보", "고객 정보의 AI 처리 목적·범위, 외부 전송, 로그 보관, 가명처리", "현재 목업 데이터만 사용. 관측 로그의 프롬프트 저장 위험 식별, 고객식별번호 응답 노출 제거. 실데이터 전 마스킹 규칙 수립"],
      ["망분리·혁신금융서비스", "내부망에서의 생성형 AI 활용, 외부 클라우드 AI 이용 시 규제 특례(혁신금융서비스 지정) 여부", "행내 GenAI 플랫폼 경유로 내부망 운영. 외부 AI 서비스 활용 확대 시 지정·승인 절차 검토"],
      ["AI 관련 법·가이드라인", "AI 기본법(2026.1 시행)의 고영향 AI·투명성 의무, 금융권 AI 가이드라인(설명가능성·공정성·책임성)", "근거 출처 표시, 판단 기준의 근거 등급 관리. 대고객 서비스 시 «AI 생성» 고지 등 검토 필요"],
      ["책임 소재·기록", "AI 제안을 따른 상담에서 문제 발생 시 책임 주체, 상담 기록·녹취와의 관계", "최종 판단·발송은 직원(Human-in-the-loop). 답변 근거(trace) 기록 기능 구현. 내부통제 기준 마련 필요"],
      ["모델·운영 리스크", "AI 오답(환각), 모델 변경에 따른 품질 변화, 외부 모델 의존", "답변 수치 검증기, 회귀 테스트. 모델 변경 승인·평가 절차 필요"],
    ],
    [1900, 3800, W - 5700],
    { firstColFill: true, firstColBold: true }
  ),
];

const s9 = [
  h1("9. 향후 추진 방향 (안)"),
  table(
    ["단계", "주요 내용", "산출물"],
    [
      ["1단계\nPoC 고도화", "실서비스 구조 보완(상담이력 DB·호출 제한·개인정보 마스킹), 데이터 소스 확정, 컴플라이언스 사전 협의", "운영 전환 설계서, 데이터 연동 명세, 준법 검토 결과"],
      ["2단계\n실데이터 파일럿", "일부 영업점·소수 직원 대상 실데이터 파일럿, 현업 지식 오너 참여, 효과 측정(상담 준비시간·전환율·만족도)", "파일럿 결과 보고, 지식 운영 체계(R&R), 평가셋"],
      ["3단계\n확산", "퇴직연금 운영 배포, 공통 플랫폼화(지식베이스·근거 통제·행내 연동 재사용), WM 타 업무로 확장", "WM AI 공통 플랫폼, 후속 과제 2~3건 착수"],
    ].map((r) => r.map((c) => c.split("\n"))),
    [2000, 4600, W - 6600],
    { firstColFill: true, firstColBold: true }
  ),
];

const s10 = [
  h1("10. 요청 및 건의 사항"),
  table(
    ["#", "요청 사항", "필요 이유"],
    [
      ["1", "현업 부서 지식 오너 지정 (연금사업부 등)", "지식 최신화·검수 없이는 AI 품질 유지 불가"],
      ["2", "실데이터 파일럿 승인 및 데이터 접근 권한 협의 지원", "더미 데이터로는 실제 효과 검증 불가"],
      ["3", "준법감시·법무·정보보호 참여 AI 협의체 구성", "금소법·광고·개인정보 이슈의 사전 정리"],
      ["4", "후속 적용 과제 우선순위 결정 및 인력·예산 지원", "3인 체제로는 운영 전환과 확산 병행이 어려움"],
    ],
    [600, 4400, W - 5000],
    { centerCols: [0] }
  ),
  gap(),
  box("맺음말", [
    "이번 과제로 «행내 환경에서도 AI가 실제 업무를 도울 수 있다»는 가능성을 직접 확인했습니다.",
    "동시에 AI 도입의 성패는 기술보다 지식데이터, 현업의 참여, 그리고 통제 체계에 달려 있다는 점을 배웠습니다.",
    "이 경험을 바탕으로 WM고객그룹의 다양한 업무에 AI를 안전하고 빠르게 녹여 갈 수 있도록 지원을 부탁드립니다.",
  ], "EEF2F8", NAVY),
];

const appendix = [
  h1("부록. 용어 설명"),
  table(
    ["용어", "설명"],
    [
      ["PoC", "Proof of Concept. 개념 검증 — 실제로 가능한지 작게 만들어 확인하는 단계"],
      ["LLM", "대규모 언어모델. 문장을 이해·생성하는 생성형 AI 모델"],
      ["에이전트", "질문에 답하는 것을 넘어 스스로 필요한 정보를 찾고, 도구를 호출해 업무를 수행하는 AI"],
      ["지식카드", "행내 문서에서 추출한 지식 단위. 원문 인용·출처·기준시점을 함께 가진다"],
      ["MCP", "Model Context Protocol. AI가 행내 시스템(예: WorkB 쪽지)을 호출할 수 있게 하는 연결 표준"],
      ["환각(Hallucination)", "AI가 근거 없는 내용을 사실처럼 만들어 내는 현상"],
      ["골든셋", "«정답»으로 합의된 질문·답 묶음. AI 품질을 측정·회귀 점검하는 기준"],
      ["Human-in-the-loop", "최종 판단·실행을 사람이 하도록 AI 흐름 안에 사람의 확인 단계를 두는 방식"],
    ],
    [2400, W - 2400],
    { firstColFill: true, firstColBold: true }
  ),
];

const doc = new Document({
  creator: "WM AI COE",
  title: "퇴직연금 AI 사후관리 에이전트 PoC 결과 보고",
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: FONT, color: NAVY },
        paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: KB, space: 4 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: FONT, color: "2E5597" },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bul", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 400, hanging: 260 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "○", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 800, hanging: 260 } } } },
      ] },
    ],
  },
  sections: [
    {
      properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1300, bottom: 1200, left: 1134, right: 1134 } }, titlePage: true },
      headers: {
        default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [t("퇴직연금 AI 사후관리 에이전트 PoC 결과 보고  |  WM AI COE", { size: 16, color: GRAY })] })] }),
      },
      footers: {
        default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [t("- ", { size: 18, color: GRAY }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: GRAY }), t(" -", { size: 18, color: GRAY })] })] }),
      },
      children: [...cover, ...summary, ...toc, ...s1, ...s2, ...s3, ...s4, ...s5, ...s6, ...s7, ...s8, ...s9, ...s10, ...appendix],
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(process.argv[2], buf);
  console.log("written", process.argv[2]);
});
