import streamlit as st
import pandas as pd
import csv
import os
from datetime import datetime

# 에이전트 모듈 임포트
from customer import PERSONAS
from agent import propose
import engine

st.set_page_config(page_title="IRP 에이전트 평가 룸", layout="wide")
st.title("📈 IRP 전략 제안 에이전트 평가 대시보드")

# 피드백 저장용 CSV 파일 초기화 (Status 컬럼 포함)
FEEDBACK_FILE = "feedback_log.csv"
if not os.path.exists(FEEDBACK_FILE):
    with open(FEEDBACK_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Customer", "Issue_Type", "Feedback", "Status"])

# 상태 유지를 위한 캐싱 (LLM 호출 비용 및 대기 시간 절약)
@st.cache_data(show_spinner=False)
def load_all_proposals(use_llm=True):
    return {p.nm: propose(p, use_llm=use_llm) for p in PERSONAS}

use_llm = st.sidebar.checkbox("LLM 문장 생성 적용", value=True)
results = load_all_proposals(use_llm)

# 5개의 탭으로 구성
tab_cat, tab1, tab2, tab3, tab4 = st.tabs([
    "🗂 행내 전략 카탈로그",
    "👥 한눈에 보기",
    "📝 상세 조회 및 피드백",
    "⚙️ 시스템 무결성",
    "📋 피드백 관리 보드"
])


# ==========================================
# Tab 0: 행내 전략 카탈로그 (data/strategies.json 한눈에 보기)
# ==========================================

with tab_cat:
    st.subheader("🗂 행내 전략 카탈로그")
    st.caption(f"에이전트가 제안 문장의 근거로 삼는 행내 전략 원장 (총 {len(engine.SPECS)}건)")

    rows = []
    for s in engine.SPECS:
        u = s.get("urgency")
        # 출처: 원문 문서명 + 페이지(p1/p5). 문서 근거가 없으면 규정 근거로 폴백한다.
        src = engine.format_sources(s.get("sources", []))
        if not src and s.get("regulation"):
            src = [s["regulation"]]
        rows.append({
            "전략명": s.get("title", ""),
            "전략 내용": s.get("benefit", ""),
            "출처": " · ".join(src) or "—",
            "유형": s.get("kind", ""),
            "주체": s.get("actor", ""),
            "신뢰도": s.get("confidence", ""),
            "시급성": "동적" if isinstance(u, dict) else u,
            "필수": "✅" if s.get("mandatory") else "",
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "전략 내용": st.column_config.TextColumn("전략 내용", width="large"),
            "출처": st.column_config.TextColumn("출처", width="medium"),
        },
    )

# ==========================================
# Tab 1: 전체 페르소나 요약 데시보드
# ==========================================
with tab1:
    st.subheader("전체 페르소나 산출물 요약")
    
    summary_data = []
    for nm, res in results.items():
        f = res["facts"]
        summary_data.append({
            "고객명": nm,
            "근거 구분": {"행내전략": "행내 전략", "LLM판단": "⚠ LLM 판단(검토)",
                       "미매칭": "미매칭"}.get(res.get("tier"), res.get("tier", "")),
            "생성 경로": res["source"],
            "핵심 제안": " · ".join(it["card"]["headline"] for it in f["items"]) or "제안 항목 없음",
            "제안 항목 수": len(f["items"]),
            "폴백/에러 사유": res["reason"] if res["reason"] else "없음",
            "적합성 차단 건수": len(f["blocked_products"])
        })
    
    st.dataframe(pd.DataFrame(summary_data), use_container_width=True)


# ==========================================
# Tab 2: 상세 조회 및 자연어 피드백
# ==========================================
with tab2:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        target_name = st.selectbox("리뷰할 고객 선택", [p.nm for p in PERSONAS])
        target_res = results[target_name]
        f = target_res["facts"]
        
        # 에이전트 최종 답변 형식(agent.py::_print)을 그대로 재현한다.
        # ── 헤더: ■ 고객 · 요건 N건
        st.markdown(f"#### ■ {target_name} · 요건 {len(f['conditions'])}건")
        st.write(", ".join(c.split(":", 1)[1] for c in f["conditions"]))

        # ── 보유현황
        bf = f["briefing"]
        src = "; ".join(engine.format_sources([bf["source"]]))
        holdings = " | ".join(f"{k} {v}" for k, v in bf.items() if k != "source")
        st.markdown(f"**[보유현황]** {holdings}")
        st.caption(f"근거 · {src}")

        st.divider()

        # ── 전략 제안: 긴 지시 문장 대신 항목별 카드로 제시
        st.markdown("**[전략 제안]**")
        if target_res.get("insight"):
            st.markdown(
                f"<div style='font-size:1.15rem;font-weight:800;margin:4px 0'>"
                f"▷ {target_res['insight']}</div>",
                unsafe_allow_html=True,
            )
        # 행내 전략 미매칭(Tier2/미매칭) — 카드가 없으므로 LLM 참고 의견/안내 문장을 노출한다.
        if not f["items"]:
            if target_res.get("tier") == "LLM판단":
                st.warning(f"⚠ 행내 전략 미매칭 · LLM 참고 의견(검토 필요)\n\n{target_res['sentence']}")
            else:
                st.info(target_res["sentence"])
        for i, it in enumerate(f["items"], 1):
            c = it["card"]
            with st.container(border=True):
                st.markdown(f"**{i}. {c['headline']}** &nbsp;·&nbsp; {c['tag']}")
                if c["product"]:
                    tgt = f" &nbsp;·&nbsp; 대상 {c['target']}" if c["target"] else ""
                    st.markdown(f"추천 상품 {c['product']}{tgt}")
                elif c["action"]:
                    st.markdown(f"실행 {c['action']}")
                st.markdown(c["benefit"])

        # ── 상담 화법
        if f.get("talking_points"):
            st.markdown("**[상담 화법]**")
            for tp in f["talking_points"]:
                st.markdown(f"- ({tp['title']}) {tp['talk']}")

        # ── 판단근거
        if f["rationale"]:
            st.markdown("**[판단근거]**")
            for s in f["rationale"]:
                st.markdown(f"- {s}")

        # ── 근거 문서
        if f["source_titles"]:
            st.markdown(f"**[근거 문서]** {'; '.join(f['source_titles'])}")

        # ── 근거 규정 — 적합성 원칙 등 판단의 규정 출처를 추적 가능하게 노출
        if f.get("regulations"):
            st.markdown("**[근거 규정]**")
            for rg in f["regulations"]:
                st.markdown(f"- ({rg['title']}) {rg['regulation']}")

        # ── 다른 제안 (수익 개선폭 순)
        if f["alternatives"]:
            st.markdown("**[다른 제안]** 수익 개선폭 순")
            for i, a in enumerate(f["alternatives"], 1):
                st.markdown(f"{i}. {a['clause']} &nbsp; [{engine.effect_label(a['effect_grade'])}]")

        # ── 생성 경로 및 리젝 사유
        st.divider()
        _tier = {"행내전략": "행내 전략 근거", "LLM판단": "⚠ LLM 판단 · 행내 근거 없음(검토 필요)",
                 "미매칭": "미매칭 · 제안 없음"}.get(target_res.get("tier"), target_res.get("tier", ""))
        route = target_res["source"] + (f" ({target_res['reason']})" if target_res["reason"] else "")
        st.caption(f"근거 구분: {_tier}  ·  생성 경로: {route}")
        for b in target_res["rejected"]:
            st.markdown(f"- 리젝 사유: {b}")

        # ── 고지 / 확인 / 보류 사항
        for label, key in (("고지 필요", "cautions"), ("확인 필요", "needs_confirm"), ("제안 보류", "unverified")):
            if f[key]:
                st.markdown(f"**[{label}]**")
                for v in f[key]:
                    st.markdown(f"- {v}")
    
    with col2:
        st.write("### 📝 피드백 작성")
        st.caption("어색하거나 논리에 안 맞는 부분을 편하게 자연어로 남겨주세요.")
        
        with st.form("feedback_form", clear_on_submit=True):
            issue_type = st.selectbox("어떤 종류의 문제인가요?", ["상품/규정 오류", "논리 어색함", "문장/말투 이상함", "기타 건의"])
            feedback_text = st.text_area("자연어 피드백 입력", placeholder="예: 박지영 고객은 위험자산 한도 초과인데 디폴트옵션 설정하라고 뜨는게 이상해요.")
            
            submitted = st.form_submit_button("데이터로 저장하기")
            
            if submitted:
                if feedback_text.strip() == "":
                    st.warning("피드백 내용을 입력해주세요.")
                else:
                    # CSV에 즉시 기록 (기본 상태 '대기중' 추가)
                    with open(FEEDBACK_FILE, "a", encoding="utf-8", newline="") as file:
                        writer = csv.writer(file)
                        writer.writerow([
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                            target_name, 
                            issue_type, 
                            feedback_text,
                            "대기중"
                        ])
                    
                    st.success(f"{target_name} 님에 대한 피드백이 성공적으로 기록되었습니다!")


# ==========================================
# Tab 3: 시스템 무결성 검증
# ==========================================
with tab3:
    st.subheader("시스템 무결성 및 정의 검증")
    st.info("💡 피드백 반영 시 이곳에서 자동화된 회귀 테스트를 먼저 통과시켜야 사이드 이펙트를 막을 수 있습니다.")
    
    if st.button("전체 테스트 실행 (`engine.validate()`)"):
        with st.spinner("테스트 엔진 실행 중..."):
            errors, warns = engine.validate()
            if errors:
                st.error(f"정의 검증 에러 {len(errors)}건 발견!")
                for e in errors:
                    st.write(f"- {e}")
            else:
                st.success("✅ 정의 검증 (validate) 완료: ERROR 0건")
            
            if warns:
                st.warning(f"경고 {len(warns)}건")
                for w in warns:
                    st.write(f"- {w}")


# ==========================================
# Tab 4: 피드백 관리 보드
# ==========================================
with tab4:
    st.header("📋 피드백 관리 및 파이프라인 보드")
    
    st.write("### 📌 접수된 피드백 결재 (Triage)")
    st.caption("타당한 피드백만 '🤖 AI 작업 승인'으로 변경하고 저장하세요.")

    if os.path.exists(FEEDBACK_FILE):
        df = pd.read_csv(FEEDBACK_FILE)
        
        # 상태값 초기화 및 옵션 설정
        if "Status" not in df.columns:
            df["Status"] = "새로운 의견"

        status_options = ["새로운 의견", "🤖 AI 작업 승인", "❌ 반려", "✅ 반영 완료"]

        edited_df = st.data_editor(
            df,
            column_config={
                "Timestamp": st.column_config.TextColumn("접수 시간", disabled=True),
                "Customer": st.column_config.TextColumn("대상 고객", disabled=True),
                "Issue_Type": st.column_config.TextColumn("이슈 유형", disabled=True),
                "Feedback": st.column_config.TextColumn("피드백 내용", disabled=True, width="large"),
                "Status": st.column_config.SelectboxColumn(
                    "결재 상태",
                    help="AI에게 수정을 맡길 이슈만 'AI 작업 승인'으로 변경하세요.",
                    options=status_options,
                    required=True
                )
            },
            hide_index=True,
            use_container_width=True
        )

        if st.button("🔄 변경된 결재 상태 저장"):
            edited_df.to_csv(FEEDBACK_FILE, index=False)
            st.success("피드백 결재 상태가 업데이트되었습니다!")

        st.divider()

        # ==========================================
        # 클로드 코드(Claude Code)용 마스터 프롬프트 생성기
        # ==========================================
        st.write("### 🤖 클로드 코드 작업 지시서 발급")
        
        approved_issues = edited_df[edited_df["Status"] == "🤖 AI 작업 승인"]
        
        if not approved_issues.empty:
            st.info(f"현재 승인된 이슈가 {len(approved_issues)}건 있습니다. 아래 지시서를 복사하여 클로드 터미널에 붙여넣으세요.")
            
            # 컨텍스트와 이슈를 묶어서 프롬프트 조립
            issue_list_text = ""
            for idx, row in approved_issues.iterrows():
                issue_list_text += f"- 고객명 [{row['Customer']}]: {row['Feedback']}\n"
            
            master_prompt = f"""당신은 퇴직연금 시스템의 시니어 AI 엔지니어입니다. 아래 승인된 {len(approved_issues)}건의 피드백을 일괄 해결하세요.

[수행해야 할 피드백 목록]
{issue_list_text}
[시스템 설계 규약 (반드시 준수할 것)]
1. 적합성 게이트, 금액 산출 등 로직화 가능한 것은 `engine.py`와 json을 수정합니다. (조회 지시어를 절에 넣지 마세요)
2. 문장 표현/동사 결합은 `strategies.json`의 절(clause) 규약을 따릅니다.
3. LLM의 수치/상품명 환각 제어와 출력 포맷 변경은 `prompts.py`를 수정합니다.

[작업 절차: 반드시 아래 순서대로 사고하고 실행하세요]
1. **요구사항 정제 및 추상화 (Overfitting 방지):** 
   - 사용자의 피드백 텍스트나 예시 문구를 문자 그대로(Literal) 하드코딩하지 마세요. 
   - 예시에 편향되지 말고, "시스템 아키텍처 상 어떤 로직, 프롬프트, JSON 구조를 바꿔야 이 의도를 모든 고객에게 범용적으로 적용할 수 있는지" 핵심 의도만 추출하세요.
2. **테스트 작성 (TDD):** 정제된 의도를 바탕으로 `test_engine.py`에 이 문제를 검증(Fail)하는 코드를 먼저 추가하세요.
3. **코드 수정:** 원인을 분석하고 코드를 수정하세요. 로직을 고치기 귀찮다고 테스트 코드를 무조건 Pass하도록 조작하면 절대 안 됩니다.
4. **회귀 테스트:** `python test_engine.py`를 실행해서 추가된 테스트와 기존 테스트가 모두 ERROR 0건으로 통과하는지 확인하세요.
5. **작업 보고:** 모든 테스트가 통과하면, "피드백을 어떻게 추상화해서 어떤 파일들을 수정했는지" 요약을 출력하세요.
"""
            # 복사하기 쉬운 텍스트 박스 형태로 렌더링 (st.code 대신 사용)
            st.text_area(
                label="👇 아래 텍스트 박스를 클릭한 뒤, 전체 선택(Ctrl+A) 후 복사(Ctrl+C) 하세요.", 
                value=master_prompt, 
                height=350
            )
        else:
            st.warning("'🤖 AI 작업 승인' 상태인 피드백이 없습니다.")
    else:
        st.info("아직 접수된 피드백이 없습니다.")