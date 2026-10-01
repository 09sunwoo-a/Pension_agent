// 요약 보고서(3장) 생성 — node summary.js <out.docx>
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType,
  ShadingType, AlignmentType, LevelFormat, BorderStyle, Footer, PageNumber, VerticalAlign,
} = require("docx");

const FONT = "맑은 고딕";
const NAVY = "1F3864";
const GRAY = "595959";
const W = 9638;

const t = (text, o = {}) => new TextRun({ text, font: FONT, ...o });
const b = (text) => t(text, { bold: true });
const runs = (x) => (Array.isArray(x) ? x : [x]).map((r) => (typeof r === "string" ? t(r) : r));

// 개조식: □ / ○ / -
const L = (lvl) => (x) =>
  new Paragraph({ numbering: { reference: "gae", level: lvl }, children: runs(x), spacing: { after: lvl === 0 ? 50 : 30, line: 276 } });
const sq = L(0), ci = L(1), da = L(2);

const sec = (text, brk) =>
  new Paragraph({
    pageBreakBefore: !!brk,
    children: [t(text, { bold: true, size: 26, color: NAVY })],
    spacing: { before: 160, after: 80 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: NAVY, space: 2 } },
  });
const note = (text) => new Paragraph({ children: [t(text, { size: 16, color: GRAY })], spacing: { before: 30, after: 40 }, indent: { left: 300 } });

const border = { style: BorderStyle.SINGLE, size: 4, color: "A6A6A6" };
const borders = { top: border, bottom: border, left: border, right: border };
function cell(content, width, o = {}) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    borders,
    verticalAlign: VerticalAlign.CENTER,
    shading: o.fill ? { type: ShadingType.CLEAR, color: "auto", fill: o.fill } : undefined,
    margins: { top: 40, bottom: 40, left: 90, right: 90 },
    children: [
      new Paragraph({
        alignment: o.center ? AlignmentType.CENTER : AlignmentType.LEFT,
        spacing: { after: 0, line: 240 },
        children: (Array.isArray(content) ? content : [content]).map((r) =>
          typeof r === "string" ? t(r, { size: 19, bold: !!o.bold, color: o.color }) : r
        ),
      }),
    ],
  });
}
function table(headers, rows, widths, o = {}) {
  return new Table({
    width: { size: widths.reduce((a, c) => a + c, 0), type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, widths[i], { fill: "D9E2F3", bold: true, center: true })) }),
      ...rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, widths[i], { fill: i === 0 ? "F2F2F2" : undefined, bold: i === 0, center: i === 0 && o.centerFirst })) })),
    ],
  });
}
const indentTable = (tbl) => tbl; // 표는 본문 폭 그대로
const small = (s) => t(s, { size: 17 });

// ───────────── 머리 ─────────────
const head = [
  new Table({
    width: { size: W, type: WidthType.DXA },
    columnWidths: [W],
    rows: [new TableRow({ children: [new TableCell({
      width: { size: W, type: WidthType.DXA },
      borders: { top: { style: BorderStyle.SINGLE, size: 18, color: NAVY }, bottom: { style: BorderStyle.SINGLE, size: 18, color: NAVY }, left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" } },
      margins: { top: 120, bottom: 120, left: 100, right: 100 },
      children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [t("퇴직연금 AI 사후관리 에이전트 PoC 결과 보고", { bold: true, size: 32, color: NAVY })] })],
    })] })],
  }),
  new Paragraph({ alignment: AlignmentType.RIGHT, spacing: { before: 80, after: 0 }, children: [t("2026. 10.   WM고객그룹 WM AI COE", { size: 18, bold: true })] }),
  new Paragraph({ alignment: AlignmentType.RIGHT, spacing: { after: 60 }, children: [t("권현진 과장 · 이선우 대리 · 정석희 대리", { size: 17, color: GRAY })] }),
];

// ───────────── Ⅰ. 추진 개요 ─────────────
const s1 = [
  sec("Ⅰ. 추진 개요"),
  sq([b("목적 : "), t("행내 환경에서 AI 에이전트가 실제로 작동하는지, 어디까지 구현 가능한지, 업무에 AI를 적용하려면 무엇이 필요한지 직접 확인")]),
  ci([b("과제 : "), t("개인형IRP 보유 고객의 사후관리를 돕는 영업점 직원용 AI 에이전트 (PoC)")]),
  da("선정 이유 : 현금성자산 방치·원리금 편중·만기 후 미운용 등 사후관리 공백이 크고, 관련 행내 지식은 있으나 흩어져 있어 현장에 닿지 않음"),
  sq([b("추진 체계")]),
  ci([b("기간 : "), t("2026. 7. 14.(화) ~ 10. 2.(금), 12주 (AI 교육 2주 + 프로젝트 10주)")]),
  ci([b("인력 : "), t("WM AI COE 3명 (기획 1명 · 개발 2명)")]),
  ci([b("활용 지식 : "), t("행내 가이드 · 스타런 교육자료 · 영업점 Hot Tip 등 86건 → 지식카드 약 720장으로 구조화")]),
];

// ───────────── Ⅱ. 추진 결과 ─────────────
const s2 = [
  sec("Ⅱ. 추진 결과"),
  sq([b("주요 구현 내용")]),
  ci([b("(영업 전) AI 고객 브리핑 : "), t("관리 대상 사유 · 운용 현황 · 오늘의 제안 · 화법 · 예상 반론 · 안내 콘텐츠 등 9개 섹션을 고객별 자동 생성")]),
  ci([b("(영업 중) 대화형 상담 에이전트 : "), t("제도·상품 지식, 고객별 개인화 제안, 과거 상담 맥락을 근거와 함께 답변")]),
  ci([b("(영업 후) 업무 연계 : "), t("MyStar 단말 화면 원클릭 연동, 상담이력 작성·등록·재접촉 일정 관리, 고객 안내 문자 발송 화면 연계")]),
  sq([b("실현 수준 : "), t("PoC 완료 및 스타뱅킹 개발서버 배포, 행내 시스템과 실제 연동까지 확인")]),
  table(
    ["구분", "내용"],
    [
      ["실제 작동", "행내 GenAI 플랫폼 호출 · 스타뱅킹 개발서버 배포 · WorkB 쪽지(MCP) · MyStar 단말 화면 연동 · 상담이력 관리"],
      ["작동 (품질 보정 중)", "지식 기반 질의응답(164문항 실측) · AI 브리핑 9개 섹션"],
      ["시연 수준", "고객 안내 문자 — 발송 화면과 문구만 제공, 발송 여부는 직원이 결정"],
      ["더미 · 미구현", "고객 데이터(목업 12명) · 금리·시황 · 세미나·이벤트 일정은 시연값 / 평상시 대시보드 미구현"],
    ],
    [2000, W - 2000]
  ),
];

// ───────────── Ⅲ. 도입 효과 ─────────────
const s3 = [
  sec("Ⅲ. 도입 효과"),
  sq([b("핵심 효과는 시간 단축보다 «할 수 없던 일을 할 수 있게 하는 것»")]),
  ci("사후관리를 하지 못하던 직원도 수행 가능 → 사후관리 수행 인력 확대"),
  ci("직원 역량에 따라 편차가 크던 상담 → 일관된 고품질 상담으로 상향평준화"),
  table(
    ["구분", "현재", "에이전트 활용 시"],
    [
      ["상담 준비시간", "약 42분 (현황 조회·자료 검색·상품 비교 직접 수행)", "약 18분 (약 57% 단축)"],
      ["업무 화면 이동", "화면번호 확인 후 단말에서 직접 찾아 이동", "답변에서 MyStar 단말 화면 원클릭 연동"],
      ["상담이력 관리", "이력 작성·등록, 재접촉 일정 관리를 직원이 수기 처리", "에이전트가 작성 → 등록 → 재접촉 일정 관리까지 수행"],
      ["수행 가능 인력", "연금 경험이 많은 일부 직원에 의존", "신입~중견 직원도 사후관리 상담 수행"],
      ["상담 품질", "직원별 편차가 크고 품질 보장 곤란", "행내 지식 기반 근거·화법·주의사항 일관 제공"],
    ],
    [1700, 3700, W - 5400]
  ),
  note("※ 준비시간은 PoC 환경 산정치이며, 실데이터 파일럿에서 실측 검증 예정"),
];

// ───────────── Ⅳ. 주요 시사점 ─────────────
const s4 = [
  sec("Ⅳ. 주요 시사점", true),
  sq([b("동일 모델에서 답변 품질을 좌우한 것은 «지식 구조화»와 «통제 설계»")]),
  ci("지식이 정리된 질문은 정확, 흩어지거나 빠진 질문은 부정확 → 문서의 추출·정규화와 출처·기준시점 부여가 선행되어야 함"),
  ci("수치·상품·적합성 판단은 코드가, 문장은 AI가 담당 → 근거 밖 수치는 자동 차단, 고객 발송 등 되돌릴 수 없는 행위는 직원이 최종 결정"),
  sq([b("행내 인프라는 문서와 실제가 다름 → 개발서버 조기 실측 필수")]),
  ci("게이트웨이 응답 형식 불일치, 고객번호 형식의 보안필터 차단, 호출 제한(429) 등을 실측으로 해결"),
  ci("MCP로 행내 시스템(WorkB) 연결 성공 → «말하는 AI»에서 «일하는 AI»로 갈 수 있는 통로 확인"),
  sq([b("현장은 «완성형»을 원함 (영업점 Hot Tip 50건 분석)")]),
  ci("비율보다 금액(«16.5% 공제»보다 «148만원 환급»), 찾아가는 자료가 아니라 «도착해 있는» 자료, 완성 대사·화면번호까지 제공"),
];

// ───────────── Ⅴ. 한계 및 넘어야 할 산 ─────────────
const s5 = [
  sec("Ⅴ. 한계 및 넘어야 할 산"),
  sq([b("한계")]),
  ci("답변 표현의 비결정성 (수치 오류는 차단하나 표현의 일관성은 완전히 보장되지 않음)"),
  ci("응답 지연·호출 비용 (질문 1건당 AI 호출 3~14회), 단일 서버 전제 구조 (다중 서버 전환 시 보완 필요)"),
  ci("일부 타겟 기준(원리금 편중 80%, 리밸런싱 미실시 12개월)은 행내 문서에 없는 설계 제안값 → 실데이터로 보정 필요"),
  sq([b("넘어야 할 산")]),
  ci([b("(지식데이터화) "), t("흩어진 문서와 현장 암묵지의 구조화, 본부 공식 / 현장 팁 / 고객 공개 가능 여부 등 신뢰등급 관리")]),
  ci([b("(현업 사후관리) "), t("제도·금리·상품 변경 시 지식 최신화 → 현업 부서의 지식 오너 지정과 갱신·검수 체계(R&R) 필요")]),
  ci([b("(실데이터 연동) "), t("고객 원장·CRM·시세 연동, 데이터딕셔너리에서 확인되지 않은 7개 필드(만기·세액공제 한도 등) 소스 확정")]),
  ci([b("(운영·품질·정착) "), t("다중 서버 대응, 개인정보 마스킹, 현업이 정의한 평가셋 기반 품질 관리, 교육·사용 원칙 수립")]),
];

// ───────────── Ⅵ. 제도·컴플라이언스 ─────────────
const s6 = [
  sec("Ⅵ. 제도 · 혁신금융 · 컴플라이언스 고려사항", true),
  table(
    ["영역", "주요 이슈", "PoC 대응 / 향후 과제"],
    [
      ["금융소비자보호법", "적합성 · 설명의무 · 부당권유 금지", "적합성 판정을 코드로 강제, 수익 단정 금지 표시 / 판매 프로세스 내 AI 역할 정의"],
      ["광고 · 안내 문자", "광고 심의 · 표기 · 수신거부 안내", "문자 골격은 코드가 조립, 발송은 직원 결정 / 사전 심의 절차 연계"],
      ["개인정보 · 신용정보", "처리 범위 · 외부 전송 · 로그 보관", "목업 데이터만 사용 / 실데이터 전 마스킹 규칙·행내 모니터링 체계"],
      ["망분리 · 혁신금융", "내부망 생성형 AI, 외부 AI 활용 특례", "행내 GenAI 플랫폼 경유 / 외부 AI 확대 시 지정 절차 검토"],
      ["AI 기본법 · 가이드라인", "투명성 · 설명가능성 · 책임성", "출처 표시, 판단 기준 근거등급 관리 / 대고객 시 AI 고지 검토"],
      ["책임 소재", "AI 제안에 따른 상담의 책임 주체", "최종 판단·발송은 직원 / 내부통제 기준 마련"],
    ],
    [2000, 3000, W - 5000]
  ),
  note("※ 검토 필요 항목을 정리한 것으로, 법적 판단은 준법감시·법무·정보보호 부서 검토에 따름"),
];

// ───────────── Ⅶ. 향후 추진 방향 및 요청 사항 ─────────────
const s7 = [
  sec("Ⅶ. 향후 추진 방향 및 요청 사항"),
  sq([b("추진 방향(안)")]),
  ci([b("1단계 PoC 고도화 : "), t("다중 서버·개인정보 마스킹 등 운영 구조 보완, 데이터 소스 확정, 컴플라이언스 사전 협의")]),
  ci([b("2단계 실데이터 파일럿 : "), t("일부 영업점 대상 실데이터 적용, 현업 지식 오너 참여, 준비시간·상담 품질 효과 실측")]),
  ci([b("3단계 운영 배포 : "), t("퇴직연금 운영 배포, 지식베이스·통제 체계·행내 연동을 공통 기반으로 재사용")]),
  sq([b("요청 사항")]),
  ci([b("① 현업 지식 오너 지정 : "), t("연금사업부 등 현업 부서가 지식 갱신·검수 담당 (지식 최신화 없이는 품질 유지 불가)")]),
  ci([b("② 실데이터 파일럿 승인 : "), t("일부 영업점 대상 파일럿 및 고객 데이터 접근 협의 지원 (더미 데이터로는 효과 검증 불가)")]),
  ci([b("③ AI 협의체 구성 : "), t("준법감시·법무·정보보호 부서 참여, 금소법·광고·개인정보 이슈 사전 정리")]),
  ci([b("④ 후속 과제 결정 및 지원 : "), t("후속 과제 방향 결정, 운영 전환을 위한 인력·예산 지원")]),
];

const doc = new Document({
  creator: "WM AI COE",
  title: "퇴직연금 AI 사후관리 에이전트 PoC 결과 보고(요약)",
  styles: { default: { document: { run: { font: FONT, size: 21 } } } },
  numbering: {
    config: [{
      reference: "gae",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "□", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 300, hanging: 300 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "○", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 640, hanging: 280 } } } },
        { level: 2, format: LevelFormat.BULLET, text: "-", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 940, hanging: 220 } } } },
      ],
    }],
  },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 850, bottom: 800, left: 1134, right: 1134 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [t("- ", { size: 16, color: GRAY }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: GRAY }), t(" -", { size: 16, color: GRAY })] })] }) },
    children: [...head, ...s1, ...s2, ...s3, ...s4, ...s5, ...s6, ...s7],
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(process.argv[2], buf); console.log("written", process.argv[2]); });
