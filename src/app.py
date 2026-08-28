import streamlit as st
import pandas as pd
import csv
import os
import re
from datetime import datetime

# 에이전트 모듈 임포트
from pension_agent.strategy_agent.customer import PERSONAS
from pension_agent.strategy_agent.agent import propose
from pension_agent.strategy_agent import engine
from pension_agent.strategy_agent import sections

from pension_agent import llm
from pension_agent.consult_agent import graph as consult_graph
from pension_agent.consult_agent import screens
from pension_agent.consult_agent import suggest

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

# 6개의 탭으로 구성
tab_cat, tab1, tab2, tab_chat, tab3, tab4 = st.tabs([
    "🗂 행내 전략 카탈로그",
    "👥 한눈에 보기",
    "📝 상세 조회 및 피드백",
    "💬 대화형 에이전트 테스트",
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

    if not PERSONAS:
        st.info(
            "등록된 고객이 없습니다. 시연용 고객 데이터가 정해지면 "
            "`pension_agent/strategy_agent/customer.py` 의 `PERSONAS` 에 채웁니다."
        )

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
    if not PERSONAS:
        # 로스터가 비어 있는 동안은 리뷰할 산출물 자체가 없다. 대화형 탭은 고객 없이도
        # 지식 질의를 받으므로 여기서 화면 전체를 멈추지 않는다(st.stop 금지).
        st.info(
            "등록된 고객이 없습니다. 시연용 고객 데이터가 정해지면 "
            "`pension_agent/strategy_agent/customer.py` 의 `PERSONAS` 에 채웁니다."
        )
    else:
        col1, col2 = st.columns([2, 1])
    
        with col1:
            target_name = st.selectbox("리뷰할 고객 선택", [p.nm for p in PERSONAS])
            target_res = results[target_name]
            f = target_res["facts"]
        
            # 에이전트 최종 답변 형식(agent.py::_print)을 그대로 재현한다 — REQUIREMENTS.md ①~⑨ +
            # §14 상담이력 전체를 화면 순서(①AI브리핑→②근거→③운용상태→...→⑨안내)로 노출한다.
            # ── 헤더: ■ 고객 · 요건 N건
            st.markdown(f"#### ■ {target_name} · 요건 {len(f['conditions'])}건")
            st.caption(engine.customer_header_line(f["customer"]))  # 상단 항목(REQUIREMENTS.md §3.1)
            st.write(", ".join(c.split(":", 1)[1] for c in f["conditions"]))

            # ── ② 왜 이 고객님인가요 (최대 3줄, 코드 산출)
            if f.get("why_this_customer"):
                st.markdown(f"**[{sections.title('why_this_customer')}]**")
                for line in f["why_this_customer"]:
                    st.markdown(f"- {line}")

            # ── ③ 보유현황 (3분류 운용현황 포함)
            bf = f["briefing"]
            src = "; ".join(engine.format_sources([bf["source"]]))
            holdings = " | ".join(f"{k} {v}" for k, v in bf.items() if k != "source")
            st.markdown(f"**[{sections.title('current_state')}]** {holdings}")
            st.caption(f"근거 · {src}")

            st.divider()

            # ── ① AI 브리핑 (sentence — 종합 제안 문장, 2~3문장) + 근거 해설(insight)
            st.markdown(f"**[{sections.title('ai_briefing')}]**")
            if target_res.get("tier") == "LLM판단":
                st.warning(f"⚠ 행내 전략 미매칭 · LLM 참고 의견(검토 필요)\n\n{target_res['sentence']}")
            elif not f["items"]:
                st.info(target_res["sentence"])
            else:
                st.markdown(
                    f"<div style='font-size:1.15rem;font-weight:800;margin:4px 0'>"
                    f"▷ {target_res['sentence']}</div>",
                    unsafe_allow_html=True,
                )
            if target_res.get("insight"):
                st.caption(f"근거 해설 · {target_res['insight']}")

            st.markdown("**[전략 카드]**")
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

            st.divider()

            # ── ④ 수익률 상위 1% 고객님들이 많이 담은 상품은? (비개인화·참고용)
            if f.get("top_holdings"):
                st.markdown(f"**[{sections.BY_KEY['top_holdings'].full_title}]** *({sections.BY_KEY['top_holdings'].note})*")
                for th in f["top_holdings"]:
                    st.markdown(f"- {th['product_name']} — {th['description']} (최근 1년 {th['return_1y']}%)")

            # ── ⑤ 이런 상품이 적합할 수 있어요 (LLM 상품 1개 + 포트폴리오 1개, 폐쇄 후보군에서 선택)
            reco = f.get("recommendation")
            st.markdown(f"**[{sections.title('recommendation')}]**")
            if reco:
                with st.container(border=True):
                    prod = reco["product"]
                    st.markdown(f"**추천 상품** · {prod['name']} (최근 1년 {prod['return_1y']}%)")
                    if prod.get("description"):
                        st.caption(prod["description"])
                    st.markdown(f"AI 추천 사유: {prod['reason']}")
                if reco.get("portfolio"):
                    pf = reco["portfolio"]
                    with st.container(border=True):
                        alloc = ", ".join(f"{a['product_name']} {a['weight_pct']}%" for a in pf["allocation"])
                        st.markdown(f"**추천 포트폴리오** · {pf['name']}")
                        st.caption(alloc)
                        st.markdown(f"AI 추천 사유: {pf['reason']}")
                if reco.get("combined_reason"):
                    st.markdown(f"*종합 AI 추천 사유: {reco['combined_reason']}*")
            else:
                st.caption("LLM 미가용 또는 적합 후보 없음 — 이 섹션은 근거 없는 추천 대신 비워둔다.")

            # ── 이 고객의 문제상황 (⑥⑦⑧ 후보군의 출발점 — 06/01 고객세그먼트 매칭 결과)
            if f.get("problem_situations"):
                names = ", ".join(s["title"] for s in f["problem_situations"][:3])
                more = len(f["problem_situations"]) - 3
                st.caption(f"이 고객의 문제상황: {names}" + (f" 외 {more}건" if more > 0 else ""))

            # ── ⑥ 이렇게 말해보세요 (대고객 화법, script 있으면 그것을 우선 노출)
            if f.get("talking_points"):
                st.markdown(f"**[{sections.title('talking_points')}]**")
                for tp in f["talking_points"]:
                    st.markdown(f"- ({tp['title']}) {tp.get('script') or tp['talk']}")
                    if tp.get("source"):
                        st.caption(f"— 출처 {tp['source']}")

            # ── ⑦ 예상 반론 및 대응 화법
            if f.get("objections"):
                st.markdown(f"**[{sections.title('objections')}]**")
                for ob in f["objections"]:
                    st.markdown(f"- \"{ob['objection']}\" → {ob['response']}")
                    if ob.get("source"):
                        st.caption(f"— 출처 {ob['source']}")

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

            # ── ⑧ 상담에 참고하세요 (노하우/가이드 스니펫)
            if f.get("consult_resources"):
                st.markdown(f"**[{sections.title('consult_resources')}]**")
                for res in f["consult_resources"]:
                    st.markdown(f"- {res['title']} — {res['snippet']}")
                    if res.get("screens"):
                        st.caption("확인 화면 " + " ".join(res["screens"]))
                    if res.get("source"):
                        st.caption(f"— 출처 {res['source']}")

            # ── 다른 제안 (수익 개선폭 순)
            if f["alternatives"]:
                st.markdown("**[다른 제안]** 수익 개선폭 순")
                for i, a in enumerate(f["alternatives"], 1):
                    st.markdown(f"{i}. {a['clause']} &nbsp; [{engine.effect_label(a['effect_grade'])}]")

            # ── ⑨ 고객님께 안내해보세요 (가장 임박한 이벤트 1개 + 세미나 1개)
            outreach = f.get("outreach") or {}
            if outreach.get("event") or outreach.get("seminar"):
                st.markdown(f"**[{sections.title('outreach')}]**")
                for label, item in (("이벤트", outreach.get("event")), ("세미나", outreach.get("seminar"))):
                    if item:
                        st.markdown(f"- **[{label}]** {item['name']} ({item['start_date']}~{item['end_date']})")
                        if item.get("lms_message"):
                            st.caption(f"LMS 문구: {item['lms_message']}")

            # ── §14 상담 이력
            if f.get("consult_history"):
                st.markdown("**[상담 이력]**")
                for line in f["consult_history"]:
                    st.markdown(f"- {line}")

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
                feedback_text = st.text_area("자연어 피드백 입력", placeholder="예: 이 고객은 위험자산 한도 초과인데 디폴트옵션 설정하라고 뜨는게 이상해요.")
            
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
# Tab (신규): 대화형 에이전트 테스트 (consult_agent)
# ==========================================
with tab_chat:
    st.subheader("💬 대화형 에이전트 테스트 (consult_agent)")

    #: 답변에 실린 단말 화면 딥링크 — consult_agent/screens.py 가 만드는 형식.
    SCREEN_LINK = re.compile(re.escape(screens.SCHEME) + r"\S+")
    st.caption(
        "화법 코칭뿐 아니라 브리핑/고객정보 질의·화면 연계·브리핑 수정 요청까지 "
        "한 채팅창에서 테스트합니다. 아래에서 고객을 선택하면 브리핑질의·화면 연계·수정 "
        "요청이 그 고객을 대상으로 동작합니다(화법·업무절차·메타 질문에는 필요 없음)."
    )

    if not llm.available():
        st.warning(
            "⚠️ LLM 이 설정되지 않았습니다. `src/.env`에 "
            "`LLM_BASE_URL`/`LLM_API_KEY`(사내 GenAI) 또는 `ANTHROPIC_API_KEY`(테스트용)를 "
            "설정한 뒤 앱을 다시 시작하세요 — understand(의도분류)·plan(도구 선택)·compose(문장생성) 등은 "
            "LLM 이 반드시 필요해, 지금 상태로는 질문마다 오류 메시지가 표시됩니다."
        )

    chat_customer = st.selectbox(
        "브리핑질의·화면 연계·수정 요청 대상 고객",
        ["(선택 안 함)"] + [f"{p.nm} ({p.id})" for p in PERSONAS],
        key="chat_customer_select",
    )
    customer_id = chat_customer.split("(")[-1].rstrip(")") if chat_customer != "(선택 안 함)" else None

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []  # [{"role": "user"/"assistant", "text": ...}]
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []  # graph.ask() 의 history(Turn 목록) — 후속질문 맥락
    if "chat_session_id" not in st.session_state:
        import uuid
        st.session_state.chat_session_id = f"streamlit-{uuid.uuid4().hex[:8]}"
    if "pending_question" not in st.session_state:
        st.session_state.pending_question = None

    # ── 이 고객 관련 추천 질문 (consult_agent/suggest.py — 코드 조립, 상황 기반)
    # 과거 상담이 있는 고객에게만 뜬다. 칩 문구 자체가 "지난 상담이 있었다"는 알림이고,
    # 누르면 아래 pending_question 경로로 계획 루프(history 도구)를 탄다.
    _chips = suggest.history_chips(customer_id)
    if _chips:
        st.markdown("**💡 이 고객 관련 추천 질문**")
        _chip_cols = st.columns(len(_chips))
        for _i, _chip in enumerate(_chips):
            if _chip_cols[_i].button(_chip, key=f"chip-{customer_id}-{_i}", use_container_width=True):
                st.session_state.pending_question = _chip

    QUESTION_GROUPS = {
        "화법 — 특정 상담 상황(situation)": [
            "사업자 고객인데 수수료 부담된다고 하시네요. 뭐라고 답변하죠?",
            "고객이 주식이 더 나을 것 같다는데 어떻게 말하지?",
            "이미 증권사에 IRP 만들어놨다고 하시는데요",
            "나중에 돈 필요하면 못 빼는거 아니냐고 하세요",
        ],
        "화법 — 직원 업무 절차(guide)": [
            "이탈 위험이 높은 고객을 어떻게 골라내나요",
            "리밸런싱 콜은 어떤 순서로 하나요",
        ],
        "메타 질문(agent_help)": [
            "네가 답변할 수 있는 화법은 뭐가 있어?",
            "어떤 상황을 도와줄 수 있어?",
        ],
        "브리핑/고객정보 질의 (customer 도구) — 고객 선택 필요": [
            "이 고객 평가금액 얼마야?",
            "이 고객 왜 대상이야?",
            "이 고객한테 지금 추천할 상품이 뭐야?",
            "이 고객 최근 상담 이력 있어?",
            "이 고객 예상 반론이 뭐가 있어?",
        ],
        "LMS 발송 화면 연계(lms_send) — 고객 선택 필요": [
            '"만기 예금 재예치 관련해서 안내드려요" 이 문구로 LMS 보내줘',
            "방금 화법 그대로 문자 발송해줘",
        ],
        "브리핑 수정(correction) — 고객 선택 필요": [
            "AI브리핑 문장이 너무 딱딱해. 더 부드럽게 고쳐줘",
            "이 고객 평가금액을 2억으로 바꿔줘",
        ],
    }
    with st.expander("📋 테스트 질문 리스트 (클릭하면 바로 전송)", expanded=not st.session_state.chat_messages):
        for group, qs in QUESTION_GROUPS.items():
            st.markdown(f"**{group}**")
            cols = st.columns(2)
            for i, q in enumerate(qs):
                if cols[i % 2].button(q, key=f"qbtn-{group}-{i}", use_container_width=True):
                    st.session_state.pending_question = q

    def render_answer(text: str) -> None:
        """답변을 그리고, 화면 연계 URL 이 있으면 바로 누를 수 있는 링크로도 띄운다.

        `mystar-link://` 는 커스텀 스킴이라 마크다운 링크로 적으면 Streamlit 이 href 를
        지운다. 그래서 본문(URL 문자열 포함)은 그대로 두고 버튼을 따로 붙인다 — 단말이
        깔려 있지 않은 개발 PC 에서는 눌러도 열리지 않으므로 URL 자체가 남아 있어야 한다.
        """
        st.markdown(text)
        for url in dict.fromkeys(SCREEN_LINK.findall(text)):
            st.link_button("🔗 단말 화면 열기", url)

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                render_answer(msg["text"])
            else:
                st.markdown(msg["text"])

    typed = st.chat_input("직원처럼 자연어로 질문해보세요")
    question = st.session_state.pending_question or typed
    st.session_state.pending_question = None

    if question:
        st.session_state.chat_messages.append({"role": "user", "text": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            # 진행 표시 — "생각 중..." 하나로 수 초를 버티는 대신, 지금 무엇을 하고 있는지를
            # 단계별로 흘린다(문구는 전부 코드가 정한다 — consult_agent/progress.py).
            # 특히 마지막 검증 단계가 보이는 것이 핵심이다: 지연이 «생각이 느린 것»이 아니라
            # «근거 대조를 하는 것»으로 읽혀야 한다. 끝나면 접어서 답변만 남긴다.
            with st.status("답변을 준비하고 있어요…", expanded=True) as _status:
                try:
                    result = consult_graph.ask(
                        question,
                        history=st.session_state.chat_history,
                        customer_id=customer_id,
                        session_id=st.session_state.chat_session_id,
                        on_progress=_status.write,
                    )
                    _status.update(label="답변 완료", state="complete", expanded=False)
                except Exception as e:
                    msg = str(e)
                    if "LLM 미설정" in msg:
                        answer = ("⚠️ LLM 이 설정되지 않아 응답을 생성할 수 없어요. "
                                  "`.env` 설정 후 앱을 다시 시작해주세요.")
                    else:
                        answer = f"오류 발생: {type(e).__name__}: {e}"
                    result = {"answer": answer, "sources": [], "history": st.session_state.chat_history}
                    _status.update(label="답변을 만들지 못했어요", state="error", expanded=False)
            render_answer(result["answer"])
            # 근거는 원문 문서명으로 읽어준다(카드 id 는 역추적용으로 뒤에). 답이 나온
            # 재료(근거)와 표현을 제한한 재료(주의)는 갈라 보여준다 — 섞으면 질문과 무관한
            # 고객 상태 가드가 답의 근거처럼 보인다(consult_agent/nodes/plan.py::_sources).
            for label, role in (("근거", "근거"), ("이 고객 상담에서 지켜야 할 것", "주의")):
                picked = [s for s in result.get("sources") or []
                          if s.get("role", "근거") == role]
                if picked:
                    st.caption(f"{label}\n\n" + "\n\n".join(
                        f"· {s.get('doc') or '출처 미상 — 확인 필요'}"
                        f" — {s.get('title') or ''} `{s['id']}`"
                        for s in picked))
        st.session_state.chat_history = result["history"]
        st.session_state.chat_messages.append({"role": "assistant", "text": result["answer"]})

    if st.session_state.chat_messages and st.button("🗑 대화 초기화"):
        st.session_state.chat_messages = []
        st.session_state.chat_history = []
        st.rerun()


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