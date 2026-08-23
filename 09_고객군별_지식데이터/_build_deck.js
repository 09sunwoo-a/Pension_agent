/**
 * 고객군 정의·요건 확정 회의자료 (PPTX) 생성기.
 *
 *   NODE_PATH=<pptxgenjs 설치 경로> node _build_deck.js
 *
 * 수치는 10_세그먼트.yaml · 20/21/22_지식유닛 · 30_시나리오.yaml에서 읽어온다.
 * 슬라이드 문구를 고치더라도 수치는 손으로 쓰지 않는다.
 */
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const HERE = __dirname;
const OUT = path.join(HERE, "회의자료_고객군_요건확정_2026-08.pptx");

// ── 데이터 로드 (yaml 파서 없이 사전 추출된 JSON 사용) ─────────────
const D = JSON.parse(fs.readFileSync(path.join(HERE, "_deck_data.json"), "utf8"));

// ── 팔레트 ────────────────────────────────────────────────────────
const C = {
  navy: "1B2A4A",       // 주색 — 표지·섹션·헤더
  navySoft: "2E4272",   // 보조 남색
  gold: "C8A951",       // 강조 — 결정·수치
  red: "C0453B",        // 위험·금지·상충
  paper: "FFFFFF",
  tint: "EEF2F8",       // 카드 배경
  tint2: "F7F3E8",      // 금색 계열 카드
  ink: "1F2430",
  mute: "6B7280",
  line: "D6DCE6",
};
const SEGC = ["3E6B8C", "1B2A4A", "6E7B8B", "C0453B", "2C6E5B", "8A6D2F"];
const FONT = "Malgun Gothic";
const W = 13.3, H = 7.5;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "연금 에이전트 지식데이터 TF";
pres.title = "개인형IRP 사후관리 고객군 6종 — 정의와 요건 확정";

// ── 공통 헬퍼 ─────────────────────────────────────────────────────
function bodySlide(title, kicker) {
  const s = pres.addSlide();
  s.background = { color: C.paper };
  if (kicker) {
    s.addText(kicker, {
      x: 0.6, y: 0.34, w: 8, h: 0.28, margin: 0,
      fontFace: FONT, fontSize: 11, bold: true, color: C.gold, charSpacing: 1,
    });
  }
  s.addText(title, {
    x: 0.6, y: kicker ? 0.62 : 0.45, w: 12.1, h: 0.72, margin: 0,
    fontFace: FONT, fontSize: 26, bold: true, color: C.navy,
  });
  return s;
}

function srcNote(s, text) {
  s.addText("출처 · " + text, {
    x: 0.6, y: H - 0.52, w: 12.1, h: 0.32, margin: 0,
    fontFace: FONT, fontSize: 9, color: C.mute, valign: "middle",
  });
}

function pageNum(s, n) {
  s.addText(String(n), {
    x: W - 0.85, y: H - 0.52, w: 0.4, h: 0.32, margin: 0,
    fontFace: FONT, fontSize: 10, color: C.mute, align: "right", valign: "middle",
  });
}

function card(s, o) {
  s.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h,
    fill: { color: o.fill || C.tint }, line: { color: o.line || C.line, width: 0.75 },
    rectRadius: 0.06,
  });
}

function badge(s, x, y, label, color, d) {
  const size = d || 0.44;
  s.addShape(pres.ShapeType.ellipse, {
    x, y, w: size, h: size, fill: { color }, line: { color, width: 0 },
  });
  s.addText(label, {
    x, y, w: size, h: size, margin: 0,
    fontFace: FONT, fontSize: size > 0.5 ? 17 : 14, bold: true,
    color: "FFFFFF", align: "center", valign: "middle",
  });
}

function tbl(s, rows, o) {
  s.addTable(rows, Object.assign({
    x: 0.6, y: 1.6, w: 12.1,
    fontFace: FONT, fontSize: 10.5, color: C.ink,
    border: { type: "solid", pt: 0.5, color: C.line },
    align: "left", valign: "middle", autoPage: false,
  }, o));
}

function head(cells, fill) {
  return cells.map((t) => ({
    text: t,
    options: { bold: true, color: "FFFFFF", fill: { color: fill || C.navy }, fontSize: 10.5 },
  }));
}

let pageCounter = 0;
function finish(s, source) {
  pageCounter += 1;
  if (source) srcNote(s, source);
  pageNum(s, pageCounter);
  return s;
}

// ══════════════════════════════════════════════════════════════════
// 1. 표지
// ══════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.navy };
  s.addShape(pres.ShapeType.ellipse, {
    x: 9.7, y: -1.5, w: 6.2, h: 6.2, fill: { color: C.navySoft }, line: { width: 0 },
  });
  s.addShape(pres.ShapeType.ellipse, {
    x: 11.2, y: 4.1, w: 3.4, h: 3.4, fill: { color: C.gold }, line: { width: 0 },
    transparency: 72,
  });
  s.addText("기획 회의 자료 · 요건 확정용", {
    x: 0.9, y: 1.5, w: 8.5, h: 0.34, margin: 0,
    fontFace: FONT, fontSize: 12, bold: true, color: C.gold, charSpacing: 1.5,
  });
  s.addText("개인형IRP 사후관리\n고객군 6종 정의와 요건", {
    x: 0.9, y: 2.05, w: 9.0, h: 1.9, margin: 0,
    fontFace: FONT, fontSize: 38, bold: true, color: "FFFFFF", lineSpacing: 46,
  });
  s.addText(
    "행내 지식베이스 5개 축(고객세그먼트 79 · 방법론 129 · 화법 102 · 팩트 76 · 절차 74)에서\n" +
    "고객군별로 지식을 추려 정규화한 결과와, 이번 회의에서 확정할 11개 안건",
    {
      x: 0.9, y: 4.15, w: 9.2, h: 0.9, margin: 0,
      fontFace: FONT, fontSize: 13, color: "C8D2E4", lineSpacing: 22,
    });
  s.addShape(pres.ShapeType.line, {
    x: 0.9, y: 5.35, w: 3.2, h: 0, line: { color: C.gold, width: 2 },
  });
  s.addText("2026. 08. 23   |   연금 에이전트 지식데이터 TF", {
    x: 0.9, y: 5.6, w: 8, h: 0.34, margin: 0,
    fontFace: FONT, fontSize: 12, color: "9FB0CC",
  });
  s.addText("본 자료는 대외비 행내 자료를 포함합니다 — 외부 반출 및 대고객 제시 금지", {
    x: 0.9, y: 6.55, w: 9.5, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 10, color: "7E8FAD",
  });
  s.addNotes("회의 목적: 고객군 6종의 대상 요건을 확정하고, 임계값·상충 항목 11건을 결정한다.");
}

// ══════════════════════════════════════════════════════════════════
// 2. 이 회의에서 결정할 것
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("이 회의에서 결정할 것", "AGENDA SUMMARY");
  s.addText(
    "고객군 6종의 정의와 지식은 정리를 마쳤습니다. 남은 것은 숫자와 상충 항목입니다.",
    { x: 0.6, y: 1.42, w: 12.1, h: 0.32, margin: 0, fontFace: FONT, fontSize: 13, color: C.mute });

  const items = [
    ["A", "임계값 확정", "6건", "고객군마다 '어느 숫자부터 대상인가'.\n자료마다 값이 달라 기본값을 정해야 합니다.", C.navy],
    ["B", "자료 간 상충 해소", "3건", "수수료율 4갈래, 디폴트옵션 처리 절차,\nKB증권 보전율 — 본부 확인이 필요합니다.", C.red],
    ["C", "정책·운영 규칙", "2건", "KPI 득점 지식을 어디까지 쓸 것인가,\n검증필요 13건을 어떻게 처리할 것인가.", C.gold],
  ];
  let x = 0.6;
  items.forEach(([k, t, n, d, col]) => {
    card(s, { x, y: 1.95, w: 3.9, h: 2.5, fill: C.tint });
    badge(s, x + 0.32, 2.25, k, col, 0.5);
    s.addText(t, {
      x: x + 0.95, y: 2.22, w: 2.7, h: 0.34, margin: 0,
      fontFace: FONT, fontSize: 15, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(n, {
      x: x + 0.95, y: 2.54, w: 2.7, h: 0.44, margin: 0,
      fontFace: FONT, fontSize: 20, bold: true, color: col, valign: "middle",
    });
    s.addText(d, {
      x: x + 0.32, y: 3.06, w: 3.3, h: 1.1, margin: 0,
      fontFace: FONT, fontSize: 11.5, color: C.ink, lineSpacing: 18,
    });
    x += 4.05;
  });

  card(s, { x: 0.6, y: 4.72, w: 12.1, h: 1.5, fill: C.tint2, line: C.gold });
  s.addText("결정이 미뤄지면 무엇이 막히나", {
    x: 0.95, y: 4.9, w: 5, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy,
  });
  s.addText(
    "임계값이 없으면 타깃 명세를 뽑을 수 없고(A), 상충 수치는 대고객 인용이 불가하며(B), " +
    "정책이 없으면 에이전트가 실적 논리를 고객에게 그대로 노출합니다(C).",
    { x: 0.95, y: 5.22, w: 11.4, h: 0.85, margin: 0, fontFace: FONT, fontSize: 12, color: C.ink, lineSpacing: 19 });
  finish(s, "09_고객군별_지식데이터 미결 과제 20건을 성격별로 재분류");
}

// ══════════════════════════════════════════════════════════════════
// 3. 목차
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("목차", "CONTENTS");
  const toc = [
    ["01", "배경과 작업 방법", "왜 고객군을 나누는가 · 어떤 자료에서 어떻게 만들었나"],
    ["02", "고객군 6종 개요", "정의 · 권장 타겟 · 지식 분포 · 고객군 간 전이"],
    ["03", "고객군별 상세", "① 수익률 저조 ~ ⑥ 예금·GIC 만기예정"],
    ["04", "결정 안건 11건", "임계값 6 · 상충 3 · 정책 2"],
    ["05", "데이터 신뢰도와 다음 단계", "신뢰·공개등급 현황 · 일정 · 출처 일람"],
  ];
  let y = 1.75;
  toc.forEach(([n, t, d]) => {
    s.addText(n, {
      x: 0.7, y, w: 0.9, h: 0.5, margin: 0,
      fontFace: FONT, fontSize: 22, bold: true, color: C.gold, valign: "middle",
    });
    s.addText(t, {
      x: 1.7, y, w: 4.2, h: 0.5, margin: 0,
      fontFace: FONT, fontSize: 16, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(d, {
      x: 6.0, y, w: 6.6, h: 0.5, margin: 0,
      fontFace: FONT, fontSize: 11.5, color: C.mute, valign: "middle",
    });
    s.addShape(pres.ShapeType.line, {
      x: 0.7, y: y + 0.62, w: 11.9, h: 0, line: { color: C.line, width: 0.75 },
    });
    y += 0.86;
  });
  finish(s);
}

// ══════════════════════════════════════════════════════════════════
// 4. 배경
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("배경 — 자료는 이미 있는데, 도달하지 않는다", "01. 배경");
  const stats = [
    ["460건", "원천 지식 항목", "5개 축에서 추출된 검토 항목"],
    ["79개", "원천 고객군 후보", "그대로 쓰면 타깃이 겹치고 흩어진다"],
    ["6개", "확정 고객군", "집중할 대상으로 좁힌 결과"],
  ];
  let x = 0.6;
  stats.forEach(([n, t, d]) => {
    card(s, { x, y: 1.72, w: 3.9, h: 1.72, fill: C.navy, line: C.navy });
    s.addText(n, {
      x: x + 0.35, y: 1.92, w: 3.2, h: 0.62, margin: 0,
      fontFace: FONT, fontSize: 32, bold: true, color: C.gold,
    });
    s.addText(t, {
      x: x + 0.35, y: 2.56, w: 3.2, h: 0.3, margin: 0,
      fontFace: FONT, fontSize: 13, bold: true, color: "FFFFFF",
    });
    s.addText(d, {
      x: x + 0.35, y: 2.88, w: 3.3, h: 0.42, margin: 0,
      fontFace: FONT, fontSize: 10.5, color: "B9C6DC",
    });
    x += 4.05;
  });

  s.addText("현장이 말하는 문제", {
    x: 0.6, y: 3.68, w: 6, h: 0.32, margin: 0,
    fontFace: FONT, fontSize: 14, bold: true, color: C.navy,
  });
  const probs = [
    "행원에게는 Wisenet을 뒤지고 Hot Tip을 검색할 시간이 없다 — 본부 자료의 '위치'를 아는 것 자체가 노하우로 공유된다",
    "같은 고객군에 임계값이 5갈래로 병존한다 — 어느 값으로 명세를 뽑아야 하는지 정해져 있지 않다",
    "관리되지 않은 계좌의 이탈은 막을 수 없다 — 이탈 통보를 받은 뒤에는 카드가 한 장뿐이다",
  ];
  let y = 4.08;
  probs.forEach((p) => {
    s.addShape(pres.ShapeType.ellipse, {
      x: 0.68, y: y + 0.11, w: 0.14, h: 0.14, fill: { color: C.gold }, line: { width: 0 },
    });
    s.addText(p, {
      x: 1.0, y, w: 11.6, h: 0.42, margin: 0,
      fontFace: FONT, fontSize: 11.5, color: C.ink, valign: "middle",
    });
    y += 0.52;
  });

  card(s, { x: 0.6, y: 5.82, w: 12.1, h: 0.78, fill: C.tint2, line: C.gold });
  s.addText(
    "그래서 목표는 '지식을 더 모으는 것'이 아니라 — 타겟을 좁히고, 고객군마다 쓸 지식을 미리 붙여두는 것입니다.",
    { x: 0.95, y: 5.95, w: 11.4, h: 0.52, margin: 0,
      fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy, valign: "middle" });
  finish(s, "07_에이전트_기능정의 구성 원칙 1 · 08_인사이트 현장의 목소리 50건 · 06_주제별_추출지식 5개 축");
}

// ══════════════════════════════════════════════════════════════════
// 5. 작업 방법
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("작업 방법 — 원천 자료에서 고객군별 지식데이터까지", "01. 배경");
  const steps = [
    ["01", "원천 수집", "행내 가이드 12건\n스타런 교육 15건\nHot Tip 50건\nKB Think 5건"],
    ["02", "주제별 추출", "고객세그먼트 79\n방법론 129 · 화법 102\n팩트 76 · 절차 74"],
    ["03", "고객군 확정", "79개 후보 중\n사후관리 6종으로 압축\n(팀 결정)"],
    ["04", "지식 매핑", "고객군마다 쓸 지식만\n선별 → 126개 지식유닛\n중복 없이 ID로 참조"],
    ["05", "시나리오화", "고객 응답에 따라 갈리는\n케이스 19건\n(판별질문·대사·처리)"],
  ];
  let x = 0.6;
  steps.forEach(([n, t, d], i) => {
    const isLast = i === steps.length - 1;
    card(s, { x, y: 1.85, w: 2.24, h: 2.6, fill: i === 2 ? C.tint2 : C.tint, line: i === 2 ? C.gold : C.line });
    badge(s, x + 0.22, 2.05, n, i === 2 ? C.gold : C.navy, 0.42);
    s.addText(t, {
      x: x + 0.22, y: 2.6, w: 1.9, h: 0.32, margin: 0,
      fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy,
    });
    s.addText(d, {
      x: x + 0.22, y: 2.98, w: 1.92, h: 1.3, margin: 0,
      fontFace: FONT, fontSize: 10, color: C.ink, lineSpacing: 15,
    });
    if (!isLast) {
      s.addText("▶", {
        x: x + 2.26, y: 3.0, w: 0.28, h: 0.3, margin: 0,
        fontFace: FONT, fontSize: 12, color: C.gold, align: "center", valign: "middle",
      });
    }
    x += 2.54;
  });

  s.addText("이번 회의는 03단계에서 남은 숫자와 04단계의 상충을 확정하는 자리입니다.", {
    x: 0.6, y: 4.68, w: 12.1, h: 0.34, margin: 0,
    fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy,
  });

  card(s, { x: 0.6, y: 5.12, w: 12.1, h: 1.36, fill: C.tint });
  s.addText("자료를 버리지 않은 원칙", {
    x: 0.95, y: 5.28, w: 4, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12, bold: true, color: C.navy,
  });
  s.addText(
    "값이 서로 다를 때 하나를 골라 나머지를 지우지 않았습니다. 출처·기준시점·'그 값이 만들어진 용도'를 함께 적재하고, " +
    "그중 하나만 기본값으로 표시했습니다 — 오늘 결정이 바뀌어도 근거를 다시 찾을 필요가 없습니다.",
    { x: 0.95, y: 5.62, w: 11.4, h: 0.72, margin: 0, fontFace: FONT, fontSize: 11.5, color: C.ink, lineSpacing: 18 });
  finish(s, "01~04 폴더 원천 자료 · 06_주제별_추출지식 · 09_고객군별_지식데이터");
}

// ══════════════════════════════════════════════════════════════════
// 6. 고객군 6종 개요 (2x3 카드)
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("고객군 6종 — 무엇이 다른가", "02. 개요");
  s.addText("고객군마다 '왜 지금 접촉하는가'와 '무엇을 제안하는가'가 다르고, 쓰는 화법도 다릅니다.", {
    x: 0.6, y: 1.42, w: 12.1, h: 0.3, margin: 0, fontFace: FONT, fontSize: 12.5, color: C.mute });

  const circ = ["1", "2", "3", "4", "5", "6"];
  D.seg.forEach((sg, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.05, y = 1.85 + row * 2.5;
    card(s, { x, y, w: 3.9, h: 2.32, fill: "FFFFFF", line: C.line });
    badge(s, x + 0.28, y + 0.26, circ[i], SEGC[i], 0.46);
    s.addText(sg.name, {
      x: x + 0.88, y: y + 0.26, w: 2.8, h: 0.46, margin: 0,
      fontFace: FONT, fontSize: 15, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(sg.one, {
      x: x + 0.28, y: y + 0.82, w: 3.36, h: 0.92, margin: 0,
      fontFace: FONT, fontSize: 10, color: C.ink, lineSpacing: 15,
    });
    s.addText(
      [{ text: "지식 " + sg.ku + "건", options: { bold: true } },
       { text: "   시나리오 " + sg.sc + "건   결정 " + sg.oq + "건" }],
      { x: x + 0.28, y: y + 1.82, w: 3.36, h: 0.3, margin: 0,
        fontFace: FONT, fontSize: 10, color: SEGC[i] });
  });
  finish(s, "09_고객군별_지식데이터/10_세그먼트.yaml");
}

// ══════════════════════════════════════════════════════════════════
// 7. 지식 분포 차트
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("고객군마다 기대는 지식의 종류가 다르다", "02. 개요");
  s.addText("어느 고객군이 화법 중심인지, 팩트·절차 중심인지가 에이전트 설계에 그대로 반영됩니다.", {
    x: 0.6, y: 1.42, w: 12.1, h: 0.3, margin: 0, fontFace: FONT, fontSize: 12.5, color: C.mute });

  const labels = D.seg.map((x, i) => ["①", "②", "③", "④", "⑤", "⑥"][i] + " " + x.name);
  const series = [
    { name: "화법", labels, values: D.dist["script"] },
    { name: "방법론", labels, values: D.dist["method"] },
    { name: "팩트", labels, values: D.dist["fact"] },
    { name: "근거", labels, values: D.dist["evidence"] },
    { name: "금지", labels, values: D.dist["guardrail"] },
    { name: "절차", labels, values: D.dist["procedure"] },
  ];
  s.addChart(pres.ChartType.bar, series, {
    x: 0.6, y: 1.85, w: 8.0, h: 4.3,
    barDir: "col", barGrouping: "stacked",
    chartColors: ["1B2A4A", "3E6B8C", "C8A951", "8A6D2F", "C0453B", "A9B4C4"],
    showValue: true, dataLabelPosition: "ctr", dataLabelFontSize: 8.5,
    dataLabelColor: "FFFFFF", dataLabelFontFace: FONT,
    showLegend: true, legendPos: "b", legendFontFace: FONT, legendFontSize: 10,
    catAxisLabelColor: C.ink, catAxisLabelFontFace: FONT, catAxisLabelFontSize: 10,
    valAxisLabelColor: C.mute, valAxisLabelFontFace: FONT, valAxisLabelFontSize: 9,
    valGridLine: { color: C.line, size: 0.5 }, catGridLine: { style: "none" },
    valAxisMaxVal: 45,
  });

  const notes = [
    ["화법 40건", "6개 군 모두 10~12건씩 — 상담은 결국 말이다", C.navy],
    ["② · ⑥ 40건", "편중과 만기가 가장 두껍다 — 제도·상품 팩트가 많이 붙는다", SEGC[1]],
    ["④ 근거 7건", "이탈방어만 정량 근거가 몰려 있다 (1.3배 · 82% · 82.5%)", C.red],
    ["① 팩트 1건", "수익률 저조는 팩트가 아니라 진단 방법론으로 다룬다", SEGC[0]],
  ];
  let y = 2.0;
  notes.forEach(([t, d, col]) => {
    card(s, { x: 8.85, y, w: 3.85, h: 0.95, fill: C.tint });
    s.addText(t, {
      x: 9.1, y: y + 0.12, w: 3.4, h: 0.28, margin: 0,
      fontFace: FONT, fontSize: 12, bold: true, color: col,
    });
    s.addText(d, {
      x: 9.1, y: y + 0.4, w: 3.4, h: 0.46, margin: 0,
      fontFace: FONT, fontSize: 9.5, color: C.ink, lineSpacing: 14,
    });
    y += 1.06;
  });
  finish(s, "09_고객군별_지식데이터/90_추적성_매트릭스.md § 2 유형별 분포");
}

// ══════════════════════════════════════════════════════════════════
// 8. 전이 도식 (핵심 슬라이드)
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("고객군은 고정된 속성이 아니라 '상태'다", "02. 개요");
  s.addText("손대지 않으면 아래 화살표를 따라 흘러갑니다. 이탈은 결과이지 원인이 아닙니다.", {
    x: 0.6, y: 1.42, w: 12.1, h: 0.3, margin: 0, fontFace: FONT, fontSize: 12.5, color: C.mute });

  // 좌측 3개 → ② → ④ 로 수렴하는 흐름
  const NW = 2.75, NH = 0.78;
  const LX = 0.75, MX = 5.6, RX = 10.15;
  const MY = 3.34;                       // ② 중앙
  const left = [
    { y: 2.16, label: "6  만기예정", c: SEGC[5], cap: "만기 후 운용지시 없음" },
    { y: 3.34, label: "3  방치",     c: SEGC[2], cap: "퇴직금 입금 후 미운용" },
    { y: 4.52, label: "1  수익률 저조", c: SEGC[0], cap: "매도만 하고 매수 미실행" },
  ];

  // 화살표 먼저 (뒤에 깔리게)
  left.forEach((n) => {
    const y1 = n.y + NH / 2, y2 = MY + NH / 2;
    const x1 = LX + NW + 0.12, x2 = MX - 0.12;
    if (Math.abs(y1 - y2) < 0.02) {
      s.addShape(pres.ShapeType.line, {
        x: x1, y: y1, w: x2 - x1, h: 0,
        line: { color: "9AA6B8", width: 1.75, endArrowType: "triangle" },
      });
    } else {
      s.addShape(pres.ShapeType.line, {
        x: x1, y: Math.min(y1, y2), w: x2 - x1, h: Math.abs(y2 - y1),
        line: { color: "9AA6B8", width: 1.75, endArrowType: "triangle" },
        flipV: y1 > y2,
      });
    }
  });
  // ② → ④
  s.addShape(pres.ShapeType.line, {
    x: MX + NW + 0.12, y: MY + NH / 2, w: RX - (MX + NW) - 0.24, h: 0,
    line: { color: "9AA6B8", width: 1.75, endArrowType: "triangle" },
  });
  s.addText("편중 상태 방치", {
    x: MX + NW + 0.1, y: MY + NH / 2 - 0.36, w: RX - (MX + NW) - 0.2, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 9.5, color: C.mute, align: "center", valign: "middle",
  });

  // 노드
  function node(x, y, label, c) {
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: NW, h: NH,
      fill: { color: c }, line: { color: c, width: 0 }, rectRadius: 0.08,
    });
    s.addText(label, {
      x, y, w: NW, h: NH, margin: 0,
      fontFace: FONT, fontSize: 13.5, bold: true, color: "FFFFFF",
      align: "center", valign: "middle",
    });
  }
  left.forEach((n) => {
    node(LX, n.y, n.label, n.c);
    s.addText(n.cap, {
      x: LX, y: n.y + NH + 0.04, w: NW, h: 0.28, margin: 0,
      fontFace: FONT, fontSize: 9, color: C.mute, align: "center", valign: "middle",
    });
  });
  node(MX, MY, "2  자산편중", SEGC[1]);
  node(RX, MY, "4  이탈", SEGC[3]);

  // 근거 배지
  card(s, { x: RX, y: MY + NH + 0.42, w: 2.75, h: 1.0, fill: C.tint2, line: C.gold });
  s.addText("이탈률 1.3배", {
    x: RX + 0.18, y: MY + NH + 0.55, w: 2.4, h: 0.32, margin: 0,
    fontFace: FONT, fontSize: 13, bold: true, color: C.red,
  });
  s.addText("편중 14.8% vs 일반 11.8%\n('25.11~'26.4 이탈고객 분석)", {
    x: RX + 0.18, y: MY + NH + 0.87, w: 2.45, h: 0.46, margin: 0,
    fontFace: FONT, fontSize: 8.5, color: C.ink, lineSpacing: 12,
  });

  card(s, { x: 0.6, y: 5.98, w: 12.1, h: 0.7, fill: C.navy, line: C.navy });
  s.addText(
    "기획 함의 — 예산과 인력을 ④에 몰면 늦습니다. ⑥·③·②의 예방 트랙이 실제 이탈 감소 지점입니다.",
    { x: 0.95, y: 6.07, w: 11.4, h: 0.52, margin: 0,
      fontFace: FONT, fontSize: 12.5, bold: true, color: "FFFFFF", valign: "middle" });
  finish(s, "10_세그먼트.yaml routing.transitions · KU-E-001(Series1 2026.05) · KU-E-005(Hot Tip 203753)");
}

// ══════════════════════════════════════════════════════════════════
// 9. 판정 순서
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("한 고객이 여러 군에 걸릴 때 — 판정 순서", "02. 개요");
  s.addText("실제 고객은 대부분 2개 이상에 해당합니다. 원칙은 '기한이 붙은 것 먼저'입니다.", {
    x: 0.6, y: 1.42, w: 12.1, h: 0.3, margin: 0, fontFace: FONT, fontSize: 12.5, color: C.mute });

  const rows = [head(["순위", "고객군", "이 조건이면", "왜 먼저인가", "성격"])];
  D.routing.forEach((r, i) => {
    rows.push([
      { text: String(i + 1), options: { bold: true, color: C.gold, align: "center" } },
      { text: r.seg, options: { bold: true, color: C.navy } },
      { text: r.when },
      { text: r.reason },
      { text: i < 3 ? "기한형" : "조회형",
        options: { bold: true, align: "center", color: i < 3 ? C.red : C.mute } },
    ]);
  });
  tbl(s, rows, { y: 1.9, colW: [0.7, 2.5, 3.1, 4.7, 1.1], rowH: 0.52 });

  card(s, { x: 0.6, y: 5.5, w: 12.1, h: 1.0, fill: C.tint2, line: C.gold });
  s.addText(
    "전출 알림 · ISA 60일 · 퇴직금 60일 · 만기 D-30은 지나면 기회가 소멸하지만, " +
    "편중·수익률·방치는 다음 달에도 그대로 있습니다. 접촉 리스트 정렬의 1순위 키가 기한이어야 하는 이유입니다.",
    { x: 0.95, y: 5.68, w: 11.4, h: 0.68, margin: 0,
      fontFace: FONT, fontSize: 11.5, color: C.ink, lineSpacing: 18 });
  finish(s, "10_세그먼트.yaml routing.precedence · 07_에이전트_기능정의 ④ '이벤트·기한 > 조회형'");
}

// ══════════════════════════════════════════════════════════════════
// 10~15. 고객군별 상세
// ══════════════════════════════════════════════════════════════════
D.segDetail.forEach((sg, i) => {
  const circ = String(i + 1);
  const col = SEGC[i];
  const s = pres.addSlide();
  s.background = { color: C.paper };

  badge(s, 0.6, 0.42, circ, col, 0.56);
  s.addText(sg.name, {
    x: 1.3, y: 0.4, w: 6, h: 0.6, margin: 0,
    fontFace: FONT, fontSize: 26, bold: true, color: C.navy, valign: "middle",
  });
  s.addText(sg.goal.join(" · "), {
    x: 1.32, y: 1.0, w: 6, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 10.5, bold: true, color: col,
  });
  s.addText("03. 고객군별 상세", {
    x: 9.2, y: 0.5, w: 3.5, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 10.5, color: C.mute, align: "right",
  });

  // 정의
  s.addText(sg.one, {
    x: 0.6, y: 1.4, w: 12.1, h: 0.4, margin: 0,
    fontFace: FONT, fontSize: 13, bold: true, color: C.ink,
  });

  // 좌: 대상 요건
  card(s, { x: 0.6, y: 1.9, w: 7.5, h: 2.62, fill: C.tint });
  s.addText("대상 요건 (권장 타겟)", {
    x: 0.9, y: 2.05, w: 5, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12, bold: true, color: C.navy,
  });
  s.addText(sg.target, {
    x: 0.9, y: 2.38, w: 6.95, h: 1.0, margin: 0,
    fontFace: FONT, fontSize: 11, color: C.ink, lineSpacing: 17,
  });
  s.addText("반드시 제외", {
    x: 0.9, y: 3.48, w: 5, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 11, bold: true, color: C.red,
  });
  s.addText(sg.exclude, {
    x: 0.9, y: 3.78, w: 6.95, h: 0.62, margin: 0,
    fontFace: FONT, fontSize: 10.5, color: C.ink, lineSpacing: 16,
  });

  // 우: 왜 지금
  card(s, { x: 8.35, y: 1.9, w: 4.35, h: 2.62, fill: C.navy, line: C.navy });
  s.addText("왜 지금 접촉하나", {
    x: 8.62, y: 2.05, w: 3.8, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12, bold: true, color: C.gold,
  });
  s.addText(sg.whyNum, {
    x: 8.62, y: 2.38, w: 3.85, h: 0.5, margin: 0,
    fontFace: FONT, fontSize: 19, bold: true, color: "FFFFFF", valign: "middle",
  });
  s.addText(sg.whyText, {
    x: 8.62, y: 2.92, w: 3.85, h: 1.44, margin: 0,
    fontFace: FONT, fontSize: 10, color: "C3CFE2", lineSpacing: 15,
  });

  // 하단: 시나리오 + 결정
  card(s, { x: 0.6, y: 4.62, w: 7.5, h: 1.55, fill: "FFFFFF", line: C.line });
  s.addText("케이스 시나리오 " + sg.sc + "건", {
    x: 0.9, y: 4.75, w: 5, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 11.5, bold: true, color: C.navy,
  });
  s.addText(sg.scenarios, {
    x: 0.9, y: 5.02, w: 6.95, h: 1.02, margin: 0,
    fontFace: FONT, fontSize: 10, color: C.ink, lineSpacing: 15,
  });

  card(s, { x: 8.35, y: 4.62, w: 4.35, h: 1.55, fill: C.tint2, line: C.gold });
  s.addText("이 고객군에서 결정할 것 " + sg.oq + "건", {
    x: 8.62, y: 4.75, w: 3.9, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 11.5, bold: true, color: C.navy,
  });
  s.addText(sg.decide, {
    x: 8.62, y: 5.02, w: 3.9, h: 1.02, margin: 0,
    fontFace: FONT, fontSize: 9.5, color: C.ink, lineSpacing: 14,
  });

  finish(s, sg.source);
  s.addNotes(sg.note || "");
});

// ══════════════════════════════════════════════════════════════════
// 16. 섹션 구분 — 결정 안건
// ══════════════════════════════════════════════════════════════════
{
  const s = pres.addSlide();
  s.background = { color: C.navy };
  s.addShape(pres.ShapeType.ellipse, {
    x: 10.4, y: 3.6, w: 5.0, h: 5.0, fill: { color: C.gold }, line: { width: 0 }, transparency: 78,
  });
  s.addText("04", {
    x: 1.0, y: 2.4, w: 2, h: 1.0, margin: 0,
    fontFace: FONT, fontSize: 54, bold: true, color: C.gold,
  });
  s.addText("결정 안건 11건", {
    x: 1.0, y: 3.5, w: 9, h: 0.8, margin: 0,
    fontFace: FONT, fontSize: 36, bold: true, color: "FFFFFF",
  });
  s.addText("각 안건은 [쟁점 → 선택지 → 권고 → 결정 시 영향] 순으로 정리했습니다.\n권고는 정리자 의견이며, 결정 권한은 팀에 있습니다.", {
    x: 1.0, y: 4.45, w: 9, h: 0.8, margin: 0,
    fontFace: FONT, fontSize: 13, color: "B9C6DC", lineSpacing: 22,
  });
  pageCounter += 1;
}

// ══════════════════════════════════════════════════════════════════
// 17. 결정 안건 총괄
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("결정 안건 총괄", "04. 결정 안건");
  const rows = [head(["번호", "안건", "성격", "지금 상태", "결정 주체"])];
  D.decisions.forEach((d) => {
    const kindColor = d.kind === "임계값" ? C.navy : d.kind === "상충" ? C.red : C.gold;
    rows.push([
      { text: d.id, options: { bold: true, color: kindColor, align: "center" } },
      { text: d.title, options: { bold: true } },
      { text: d.kind, options: { align: "center", color: kindColor, bold: true } },
      { text: d.now },
      { text: d.owner, options: { align: "center" } },
    ]);
  });
  tbl(s, rows, { y: 1.66, colW: [0.85, 3.5, 1.0, 5.15, 1.6], rowH: 0.335, fontSize: 9.5 });

  card(s, { x: 0.6, y: 5.9, w: 12.1, h: 0.72, fill: C.tint });
  s.addText(
    "D-01 · D-02는 타깃 명세 발주를 막고 있어 오늘 확정이 필요합니다. D-07 · D-08은 본부 확인 사항이라 오늘은 '질의 발송'까지가 목표입니다.",
    { x: 0.95, y: 6.01, w: 11.4, h: 0.5, margin: 0,
      fontFace: FONT, fontSize: 11, color: C.ink, valign: "middle" });
  finish(s, "각 고객군 open_questions 20건 + 지식유닛 cautions에서 재분류");
}

// ══════════════════════════════════════════════════════════════════
// 18~. 결정 안건 상세
// ══════════════════════════════════════════════════════════════════
D.decisionDetail.forEach((d) => {
  const s = pres.addSlide();
  s.background = { color: C.paper };
  const kindColor = d.kind === "임계값" ? C.navy : d.kind === "상충" ? C.red : C.gold;

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.6, y: 0.42, w: 1.0, h: 0.44,
    fill: { color: kindColor }, line: { color: kindColor, width: 0 }, rectRadius: 0.06,
  });
  s.addText(d.id, {
    x: 0.6, y: 0.42, w: 1.0, h: 0.44, margin: 0,
    fontFace: FONT, fontSize: 13, bold: true, color: "FFFFFF", align: "center", valign: "middle",
  });
  s.addText(d.title, {
    x: 1.78, y: 0.4, w: 9.2, h: 0.48, margin: 0,
    fontFace: FONT, fontSize: 22, bold: true, color: C.navy, valign: "middle",
  });
  s.addText(d.kind + " · " + d.owner, {
    x: 10.2, y: 0.44, w: 2.5, h: 0.4, margin: 0,
    fontFace: FONT, fontSize: 10.5, bold: true, color: kindColor, align: "right", valign: "middle",
  });

  // 쟁점
  card(s, { x: 0.6, y: 1.05, w: 12.1, h: 1.0, fill: C.tint });
  s.addText("쟁점", {
    x: 0.9, y: 1.16, w: 1.2, h: 0.26, margin: 0,
    fontFace: FONT, fontSize: 11, bold: true, color: C.mute,
  });
  s.addText(d.issue, {
    x: 0.9, y: 1.42, w: 11.5, h: 0.56, margin: 0,
    fontFace: FONT, fontSize: 12, color: C.ink, lineSpacing: 18,
  });

  // 선택지
  s.addText("선택지", {
    x: 0.6, y: 2.2, w: 3, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12, bold: true, color: C.navy,
  });
  let x = 0.6;
  const cw = (12.1 - (d.options.length - 1) * 0.25) / d.options.length;
  d.options.forEach((o) => {
    const rec = o.rec === true;
    card(s, {
      x, y: 2.55, w: cw, h: 2.25,
      fill: rec ? C.tint2 : "FFFFFF", line: rec ? C.gold : C.line,
    });
    if (rec) {
      s.addShape(pres.ShapeType.roundRect, {
        x: x + cw - 1.06, y: 2.68, w: 0.9, h: 0.3,
        fill: { color: C.gold }, line: { color: C.gold, width: 0 }, rectRadius: 0.05,
      });
      s.addText("권고", {
        x: x + cw - 1.06, y: 2.68, w: 0.9, h: 0.3, margin: 0,
        fontFace: FONT, fontSize: 9.5, bold: true, color: "FFFFFF",
        align: "center", valign: "middle",
      });
    }
    s.addText(o.label, {
      x: x + 0.22, y: 2.7, w: cw - 1.4, h: 0.34, margin: 0,
      fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(o.detail, {
      x: x + 0.22, y: 3.06, w: cw - 0.44, h: 0.82, margin: 0,
      fontFace: FONT, fontSize: 10.5, color: C.ink, lineSpacing: 16,
    });
    s.addText([
      { text: "장 ", options: { bold: true, color: "2C6E5B" } },
      { text: o.pro + "\n", options: { color: C.ink } },
      { text: "단 ", options: { bold: true, color: C.red } },
      { text: o.con, options: { color: C.ink } },
    ], {
      x: x + 0.22, y: 3.92, w: cw - 0.44, h: 0.78, margin: 0,
      fontFace: FONT, fontSize: 9.5, lineSpacing: 15,
    });
    x += cw + 0.25;
  });

  // 영향 + 결정란
  card(s, { x: 0.6, y: 4.95, w: 8.3, h: 1.28, fill: "FFFFFF", line: C.line });
  s.addText("결정하면 무엇이 바뀌나", {
    x: 0.88, y: 5.06, w: 5, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 11.5, bold: true, color: C.navy,
  });
  s.addText(d.impact, {
    x: 0.88, y: 5.36, w: 7.75, h: 0.78, margin: 0,
    fontFace: FONT, fontSize: 10.5, color: C.ink, lineSpacing: 16,
  });

  card(s, { x: 9.15, y: 4.95, w: 3.55, h: 1.28, fill: C.navy, line: C.navy });
  s.addText("회의 결정", {
    x: 9.42, y: 5.06, w: 3, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 11.5, bold: true, color: C.gold,
  });
  s.addText("□ 선택지 ______    □ 보류\n담당 __________  기한 ________", {
    x: 9.42, y: 5.38, w: 3.1, h: 0.72, margin: 0,
    fontFace: FONT, fontSize: 10, color: "FFFFFF", lineSpacing: 18,
  });

  finish(s, d.source);
  s.addNotes(d.note || "");
});

// ══════════════════════════════════════════════════════════════════
// 데이터 신뢰도
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("데이터 신뢰도 — 무엇을 그대로 쓸 수 있나", "05. 신뢰도와 다음 단계");
  s.addText("126개 지식유닛 전부에 신뢰등급·공개등급·상태가 붙어 있습니다. 에이전트는 이 값을 읽고 인용 여부를 판단합니다.", {
    x: 0.6, y: 1.42, w: 12.1, h: 0.3, margin: 0, fontFace: FONT, fontSize: 12.5, color: C.mute });

  s.addChart(pres.ChartType.doughnut,
    [{ name: "신뢰등급", labels: D.trust.labels, values: D.trust.values }], {
      x: 0.6, y: 1.9, w: 4.0, h: 3.5,
      chartColors: ["1B2A4A", "3E6B8C", "C8A951", "C0453B"],
      holeSize: 55, showLegend: true, legendPos: "b",
      legendFontFace: FONT, legendFontSize: 9.5,
      showValue: true, dataLabelFontFace: FONT, dataLabelFontSize: 10,
      dataLabelColor: "FFFFFF",
      showTitle: true, title: "신뢰등급", titleFontFace: FONT, titleFontSize: 12,
      titleColor: C.navy,
    });

  s.addChart(pres.ChartType.bar,
    [{ name: "공개등급", labels: D.disclosure.labels, values: D.disclosure.values }], {
      x: 4.8, y: 1.9, w: 4.0, h: 3.5,
      barDir: "bar", chartColors: ["C0453B", "C8A951", "2C6E5B"], varyColors: true,
      showValue: true, dataLabelPosition: "outEnd", dataLabelFontFace: FONT, dataLabelFontSize: 10,
      dataLabelColor: C.ink, showLegend: false,
      catAxisLabelColor: C.ink, catAxisLabelFontFace: FONT, catAxisLabelFontSize: 10,
      valAxisLabelColor: C.mute, valAxisLabelFontFace: FONT, valAxisLabelFontSize: 9,
      valGridLine: { color: C.line, size: 0.5 }, catGridLine: { style: "none" },
      showTitle: true, title: "공개등급", titleFontFace: FONT, titleFontSize: 12, titleColor: C.navy,
      valAxisMaxVal: 75,
    });

  card(s, { x: 9.0, y: 1.9, w: 3.7, h: 3.5, fill: C.tint });
  s.addText("사용 상태", {
    x: 9.3, y: 2.05, w: 3, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12, bold: true, color: C.navy,
  });
  const st = [
    ["104건", "확정", "그대로 사용 가능", "2C6E5B"],
    ["9건", "기준시점 병기 조건부", "수익률·금리·순위 — 시점 없이 인용 금지", C.gold],
    ["13건", "검증필요", "본부·원본 대조 전 인용 금지", C.red],
  ];
  let y = 2.45;
  st.forEach(([n, t, d, col]) => {
    s.addText(n, {
      x: 9.3, y, w: 1.0, h: 0.32, margin: 0,
      fontFace: FONT, fontSize: 16, bold: true, color: col,
    });
    s.addText(t, {
      x: 10.35, y: y + 0.03, w: 2.2, h: 0.28, margin: 0,
      fontFace: FONT, fontSize: 10.5, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(d, {
      x: 9.3, y: y + 0.34, w: 3.2, h: 0.44, margin: 0,
      fontFace: FONT, fontSize: 9, color: C.ink, lineSpacing: 13,
    });
    y += 0.95;
  });

  card(s, { x: 0.6, y: 5.6, w: 12.1, h: 0.95, fill: C.tint2, line: C.gold });
  s.addText(
    "대고객 제시가 가능한 자료는 KB Think 심의필뿐입니다 (KU-G-010). 보물지도·Series1·마스터북은 대외비, " +
    "Hot Tip은 사내 게시글이라 고객 화면에 띄울 수 없습니다 — 화법의 '내용'만 쓰고 자료 자체는 보여주지 않습니다.",
    { x: 0.95, y: 5.75, w: 11.4, h: 0.66, margin: 0,
      fontFace: FONT, fontSize: 11, color: C.ink, lineSpacing: 17 });
  finish(s, "90_추적성_매트릭스.md · 05_업무처리절차 72·73번 (자료 공개등급)");
}

// ══════════════════════════════════════════════════════════════════
// 다음 단계
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("다음 단계", "05. 신뢰도와 다음 단계");
  const steps = [
    ["오늘", "임계값 6건 확정", "D-01~D-06 — 각 안건의 선택지 중 택1.\n확정 즉시 10_세그먼트.yaml의 adopted 플래그를 옮깁니다.", C.navy],
    ["D+3", "본부 질의 발송", "D-07(수수료율)·D-08(디폴트옵션 처리)·KB증권 보전율.\n검증필요 13건 중 본부 확인분을 묶어 한 번에 질의합니다.", C.red],
    ["D+7", "타깃 명세 발주", "확정된 조건으로 맞춤형 타깃 요청.\n조회 경로가 없는 3건(0원 계좌·연금지급설계·전입이력)도 함께 요청.", C.gold],
    ["D+14", "시나리오 현장 검증", "케이스 19건을 영업점 2~3곳에서 실제 상담에 태워보고\n판별 질문과 대사를 다듬습니다.", "2C6E5B"],
  ];
  let y = 1.8;
  steps.forEach(([w, t, d, col]) => {
    s.addShape(pres.ShapeType.roundRect, {
      x: 0.6, y, w: 1.15, h: 0.52,
      fill: { color: col }, line: { color: col, width: 0 }, rectRadius: 0.06,
    });
    s.addText(w, {
      x: 0.6, y, w: 1.15, h: 0.52, margin: 0,
      fontFace: FONT, fontSize: 12, bold: true, color: "FFFFFF", align: "center", valign: "middle",
    });
    s.addText(t, {
      x: 1.95, y: y + 0.02, w: 3.4, h: 0.48, margin: 0,
      fontFace: FONT, fontSize: 14, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(d, {
      x: 5.5, y: y - 0.04, w: 7.2, h: 0.62, margin: 0,
      fontFace: FONT, fontSize: 10.5, color: C.ink, lineSpacing: 16, valign: "middle",
    });
    y += 1.05;
  });

  card(s, { x: 0.6, y: 6.05, w: 12.1, h: 0.75, fill: C.tint });
  s.addText(
    "확정 결과는 데이터에 반영한 뒤 문서를 재생성합니다 (python3 _generate_docs.py) — 회의록과 문서가 어긋나지 않게 합니다.",
    { x: 0.95, y: 6.17, w: 11.4, h: 0.5, margin: 0,
      fontFace: FONT, fontSize: 11, color: C.ink, valign: "middle" });
  finish(s);
}

// ══════════════════════════════════════════════════════════════════
// 출처 일람
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("출처 일람 — 이 자료에 쓰인 원천 데이터", "05. 신뢰도와 다음 단계");
  s.addText("모든 지식유닛은 원천 문서·부서·시점과 06_주제별_추출지식의 항목 번호를 함께 보관합니다 (역추적 가능).", {
    x: 0.6, y: 1.42, w: 12.1, h: 0.3, margin: 0, fontFace: FONT, fontSize: 12, color: C.mute });

  const rows = [head(["원천 자료", "작성 부서·출처", "시점", "신뢰등급", "대고객"])];
  D.sources.forEach((r) => {
    rows.push([
      { text: r[0], options: { bold: true } },
      { text: r[1] },
      { text: r[2], options: { align: "center" } },
      { text: r[3], options: { align: "center", bold: true,
        color: r[3] === "본부공식" ? C.navy : r[3] === "대외공개" ? "2C6E5B" : C.mute } },
      { text: r[4], options: { align: "center", bold: true,
        color: r[4] === "가능" ? "2C6E5B" : C.red } },
    ]);
  });
  tbl(s, rows, { y: 1.85, colW: [3.5, 4.5, 1.5, 1.4, 1.2], rowH: 0.33, fontSize: 9.5 });
  finish(s, "06_주제별_추출지식/04_제도상품팩트.md '출처 약칭' 표 · 각 지식유닛 source 필드");
}

// ══════════════════════════════════════════════════════════════════
// 부록
// ══════════════════════════════════════════════════════════════════
{
  const s = bodySlide("부록 — 산출물 위치와 데이터 구조", "APPENDIX");
  card(s, { x: 0.6, y: 1.7, w: 6.1, h: 4.4, fill: C.tint });
  s.addText("읽는 문서 (기획자용)", {
    x: 0.9, y: 1.88, w: 5, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy,
  });
  const docs = [
    ["01_고객군_전체지도.md", "6종 비교 · 판정 순서 · 전이 · 공통 제외"],
    ["11~16_SEG-NN_*.md", "고객군별 상세 6종 (각 12개 절)"],
    ["  1~3절", "대상 요건 · 임계값 · 오탐 제거"],
    ["  4~7절", "필요 데이터 · 근거 · 우선순위 · 브리핑"],
    ["  8절", "케이스 시나리오 (판별질문 · 실제 대사)"],
    ["  9~12절", "하지 말 것 · 후속 · 지식 목록 · 확인 필요"],
  ];
  let y = 2.28;
  docs.forEach(([n, d]) => {
    const indent = n.startsWith("  ");
    s.addText(n.trim(), {
      x: indent ? 1.15 : 0.9, y, w: 2.6, h: 0.3, margin: 0,
      fontFace: FONT, fontSize: indent ? 9.5 : 10.5,
      bold: !indent, color: indent ? C.mute : C.navy, valign: "middle",
    });
    s.addText(d, {
      x: 3.55, y, w: 3.0, h: 0.3, margin: 0,
      fontFace: FONT, fontSize: 9.5, color: C.ink, valign: "middle",
    });
    y += 0.42;
  });

  card(s, { x: 6.9, y: 1.7, w: 5.8, h: 4.4, fill: "FFFFFF", line: C.line });
  s.addText("원본 데이터 (에이전트용)", {
    x: 7.2, y: 1.88, w: 5, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 12.5, bold: true, color: C.navy,
  });
  const data = [
    ["10_세그먼트.yaml", "고객군 6 — 조건 · 임계값 · 우선순위"],
    ["20/21/22_지식유닛", "126건 — 중복 없이 단일 저장"],
    ["30_시나리오.yaml", "케이스 19건"],
    ["90_추적성_매트릭스.md", "고객군 × 지식 매핑 (자동 생성)"],
    ["00_스키마.md", "필드 딕셔너리 · ID 체계 · 갱신 규칙"],
  ];
  y = 2.28;
  data.forEach(([n, d]) => {
    s.addText(n, {
      x: 7.2, y, w: 2.5, h: 0.3, margin: 0,
      fontFace: FONT, fontSize: 10.5, bold: true, color: C.navy, valign: "middle",
    });
    s.addText(d, {
      x: 9.75, y, w: 2.75, h: 0.3, margin: 0,
      fontFace: FONT, fontSize: 9.5, color: C.ink, valign: "middle",
    });
    y += 0.42;
  });
  s.addText("갱신 주기", {
    x: 7.2, y: 4.5, w: 3, h: 0.28, margin: 0,
    fontFace: FONT, fontSize: 11.5, bold: true, color: C.navy,
  });
  s.addText(
    "매월  원리금보장 특별제공상품 금리 · 고유대 금리 · 추천펀드\n" +
    "매분기  수익률 공시 · 순위 · 환매추천펀드 목록\n" +
    "반기  KPI 인정 요건\n" +
    "수시  화면번호 · 제도 변경",
    { x: 7.2, y: 4.82, w: 5.3, h: 1.1, margin: 0,
      fontFace: FONT, fontSize: 9.5, color: C.ink, lineSpacing: 15 });

  s.addText("저장 위치 — 09_고객군별_지식데이터/  (브랜치 claude/customer-segment-knowledge-data-dattyt)", {
    x: 0.6, y: 6.25, w: 12.1, h: 0.3, margin: 0,
    fontFace: FONT, fontSize: 10.5, bold: true, color: C.mute,
  });
  finish(s);
}

pres.writeFile({ fileName: OUT }).then(() => console.log("작성 완료: " + OUT));
