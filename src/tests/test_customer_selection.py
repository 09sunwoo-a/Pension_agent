"""목록 후속 질문 회귀 검사. 실제 부점 DB + 식별자 해석 + graph.ask/HTTP 세션 연결."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from pension_agent import context_store, graph, tools
from pension_agent.common import config, session_store
from pension_agent.actions import suggest
from pension_agent.state import format_history
from pension_agent.tools import customer_selection as S
from pension_agent.tools.branch_briefing import roster, run, tool as branch_tool
from pension_agent.tools.customer_solution import customer as C, target_list
from tests.test_branch import _Today

EMP = "3902172"


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.day = _Today()
        self.day.__enter__()
        self.state = {"employee_id": EMP, "session_id": "default", "question": "고객 조회"}
        self.p1 = roster.lookup("0001:1", EMP)
        self.p2 = roster.lookup("0001:2", EMP)
        self.empty = S.context([], EMP)
        self.listed = S.snapshot(self.state, [
            {"ref": p.customer_ref, "name": p.nm, "age": p.ag, "grade": p.club_grade}
            for p in (self.p1, self.p2)], "0001", "고객 조회")
        self.saved = S.remember(self.empty, [{"meta": {"customer_list": self.listed, "document": "목록 1 원문"}}])

    def tearDown(self):
        self.day.__exit__(None, None, None)

    def resolve(self, q, saved=None, **kw):
        return S.resolve(q, saved or self.saved, kw.pop("employee_id", EMP), **kw)

    def test_number_name_and_followup(self):
        for q in ("2번 고객 자세히", f"{self.p2.nm} 고객 만기 상품은?", "두 번째 고객 만기 상품은?"):
            with self.subTest(q=q):
                found = self.resolve(q)
                self.assertEqual(found.customer_id, self.p2.id)
                self.assertIn(self.p2.nm, found.notice)
                followup = self.resolve("그 고객 세액공제는?", found.context)
                self.assertEqual(followup.customer_id, self.p2.id)
                self.assertIsNone(followup.notice)

    def test_selection_errors(self):
        cases = [("0번 고객", "out_of_range"), ("-1번 고객", "out_of_range"),
                 ("999번 고객", "out_of_range"), ("2번 고객과 1번 고객 비교", "multiple"),
                 (f"1번 고객 {self.p2.nm} 고객 만기는?", "conflict"),
                 ("김없는 고객 만기?", "name_missing"), ("김없는고객 만기?", "name_missing"),
                 ("이 고객 만기?", "not_selected"),
                 ("김 고객 만기는?", "partial_name"), ("김고객 만기는?", "partial_name"),
                 ("171203-4****** 고객 만기는?", "pin_reference"),
                 ("목록 1과 목록 2의 1번 고객", "multiple_lists")]
        for q, case in cases:
            with self.subTest(q=q):
                out = self.resolve(q)
                self.assertEqual(out.case, case)
                self.assertIsNone(out.customer_id)
                self.assertIsNone(out.context["selected"])
                self.assertTrue(out.answer)

    def test_duplicate_name_clarification(self):
        saved = deepcopy(self.saved)
        for e in saved["lists"][0]["entries"]:
            e["name"] = "홍길동"
        profiles = {p.customer_ref: replace(p, nm="홍길동") for p in (self.p1, self.p2)}
        with patch.object(roster, "lookup", side_effect=lambda ref, _emp: profiles[ref]), \
                patch.object(roster, "find_names", side_effect=lambda names, _emp:
                             ("0001", [p for p in profiles.values() if p.nm in names])):
            first = self.resolve("홍길동 고객 만기 상품은?", saved)
            self.assertEqual(first.case, "duplicate_name")
            self.assertEqual(len(first.clarify["options"]), 2)
            self.assertIn("1번", first.clarify["options"][0])
            self.assertIn(f"- 1번 홍길동 ({self.p1.ag}세", first.answer)
            self.assertIn(f"- 2번 홍길동 ({self.p2.ag}세", first.answer)
            questions = suggest.followup_questions({"clarify": first.clarify,
                "customer_context": first.context, "selection_case": first.case})
            self.assertEqual(questions, [f"{p.ag}세 홍길동 고객으로 확인해줘" for p in (self.p1, self.p2)])
            clicked = self.resolve(questions[1], first.context)
            self.assertEqual(clicked.customer_id, self.p2.id)
            self.assertEqual(clicked.question, "홍길동 고객 만기 상품은?")
            second = self.resolve("2번", first.context)
            self.assertEqual(second.customer_id, self.p2.id)
            self.assertEqual(second.question, "홍길동 고객 만기 상품은?")
            age = self.resolve(f"{self.p1.ag}세 고객", first.context)
            self.assertEqual(age.customer_id, self.p1.id)
            named_age = self.resolve(f"홍길동 고객 중 {self.p1.ag}세 고객 만기 상품은?", saved)
            self.assertEqual(named_age.customer_id, self.p1.id)
            stale = deepcopy(first.context)
            stale["directory"]["created"] = 0
            self.assertEqual(self.resolve("99세 고객", stale).case, "expired")
            for answer in ("3번", "99세 고객"):
                self.assertEqual(self.resolve(answer, first.context).case, "pending_mismatch")

    def test_named_recommendations_disambiguate_grade_and_fail_closed(self):
        saved = deepcopy(self.saved)
        entries = saved["lists"][0]["entries"]
        for entry, grade in zip(entries, ("VIP", "VVIP")):
            entry.update(name="홍길동", age=49, grade=grade)
        profiles = {p.customer_ref: replace(p, nm="홍길동", ag=49, club_grade=e["grade"])
                    for p, e in zip((self.p1, self.p2), entries)}
        with patch.object(roster, "lookup", side_effect=lambda ref, _emp: profiles[ref]), \
                patch.object(roster, "find_names", side_effect=lambda names, _emp:
                             ("0001", [p for p in profiles.values() if p.nm in names])):
            out = {"customer_context": saved, "selection_case": "none"}
            questions = suggest.followup_questions(out)
            self.assertEqual(questions, ["49세 VIP 등급의 홍길동 고객을 자세히 알려줘",
                                         "49세 VVIP 등급의 홍길동 고객을 자세히 알려줘"])
            for q, p in zip(questions, (self.p1, self.p2)):
                self.assertEqual(self.resolve(q, saved).customer_id, p.id)
            first = self.resolve("홍길동 고객 만기는?", saved)
            questions = suggest.followup_questions({"customer_context": first.context, "selection_case": first.case})
            for q, p in zip(questions, (self.p1, self.p2)):
                result = self.resolve(q, first.context)
                self.assertEqual(result.customer_id, p.id)
                self.assertEqual(result.question, "홍길동 고객 만기는?")
            # 구분에 쓴 등급이 원장에서 바뀌면 다른 고객으로 연결하지 않음.
            profiles[self.p2.customer_ref] = replace(profiles[self.p2.customer_ref], club_grade="VIP")
            self.assertEqual(self.resolve(questions[1], first.context).case, "changed")
        entries[1]["grade"] = "VIP"
        self.assertEqual(suggest.followup_questions({"customer_context": saved, "selection_case": "none"}), [])
        with patch.object(roster, "find_names", return_value=("0001", list(profiles.values()))):
            pending = self.resolve("홍길동 고객 만기는?", saved)
            self.assertEqual(suggest.followup_questions({"customer_context": pending.context,
                                                        "selection_case": pending.case}), [])

    def test_pending_named_confirmation_preserves_real_new_question(self):
        saved = deepcopy(self.saved)
        for e in saved["lists"][0]["entries"]:
            e["name"] = "홍길동"
        profiles = {p.customer_ref: replace(p, nm="홍길동") for p in (self.p1, self.p2)}
        with patch.object(roster, "lookup", side_effect=lambda ref, _emp: profiles[ref]), \
                patch.object(roster, "find_names", side_effect=lambda names, _emp:
                             ("0001", [p for p in profiles.values() if p.nm in names])):
            first = self.resolve("홍길동 고객 만기는?", saved)
            new_question = f"{self.p2.ag}세 홍길동 고객의 세액공제 잔여한도를 알려줘"
            result = self.resolve(new_question, first.context)
            self.assertEqual(result.customer_id, self.p2.id)
            self.assertEqual(result.question, new_question)

    def test_latest_previous_and_evicted_list(self):
        state = dict(self.state, customer_context=self.saved)
        listed = S.snapshot(state, list(reversed(self.listed["entries"])), "0001", "새 조건")
        updated = S.remember(self.saved, [{"meta": {"customer_list": listed, "document": "목록 2 원문"}}])
        self.assertEqual(self.resolve("1번 고객", updated).customer_id, self.p2.id)
        for q in ("이전 목록의 1번 고객", "목록 1의 1번 고객"):
            self.assertEqual(self.resolve(q, updated).customer_id, self.p1.id)
        replay = self.resolve("이전 목록 다시 보여줘", updated)
        self.assertEqual(replay.answer, "목록 1 원문")
        self.assertEqual(self.resolve("1번 고객", replay.context).customer_id, self.p1.id)
        self.assertEqual(self.resolve("목록 2", updated).case, "not_selected")
        self.assertEqual(self.resolve("아까 1번 고객", updated).case, "ambiguous_list")
        for _ in range(S.MAX_LISTS):
            listed = S.snapshot(dict(state, customer_context=updated), self.listed["entries"], "0001", "q")
            updated = S.remember(updated, [{"meta": {"customer_list": listed}}])
        self.assertEqual(self.resolve("목록 1의 1번 고객", updated).case, "list_missing")

    def test_branch_name_lookup_without_list_or_displayed_customer(self):
        profile = roster.lookup("0001:3", EMP)
        self.assertNotIn(profile.customer_ref, [e["ref"] for e in self.saved["lists"][0]["entries"]])
        for q in (f"{profile.nm} 고객 자세히 알려줘", f"{profile.nm} 자세히 알려줘",
                  f"{profile.nm}고객 만기는?", f"{profile.nm}의 보유상품은?", f"{profile.nm}를 자세히 알려줘"):
            result = self.resolve(q, self.empty)
            self.assertEqual(result.customer_id, profile.id)
            self.assertEqual(result.context["lists"], [])
            self.assertIsNone(result.context["active"])
        result = self.resolve(f"{profile.nm} 고객 자세히 알려줘")
        self.assertEqual(result.context["lists"], self.saved["lists"])
        self.assertEqual(result.context["active"], 1)
        self.assertEqual(self.resolve("2번 고객", result.context).customer_id, self.p2.id)
        self.assertEqual(self.resolve("그 고객 세액공제는?", result.context).customer_id, profile.id)
        kept = S.context([{"customer_context": result.context}], EMP)
        self.assertEqual(self.resolve("그 고객 만기는?", kept).customer_id, profile.id)

    def test_branch_names_are_not_pronouns_and_multiple_names_are_not_guessed(self):
        for q in (f"{self.p1.nm}와 {self.p2.nm} 고객 자세히 알려줘",
                  f"{self.p1.nm}랑 {self.p2.nm} 고객 자세히 알려줘"):
            self.assertEqual(self.resolve(q, self.empty).case, "multiple")
        profile = replace(self.p1, nm="정송이")
        with patch.object(roster, "find_names", return_value=("0001", [profile])), \
                patch.object(roster, "lookup", return_value=profile):
            self.assertEqual(self.resolve("정송이 고객 자세히 알려줘", self.empty).customer_id, self.p1.id)

    def _non_target(self) -> str:
        """관리 대상이 아니고 부점 안에서 이름이 하나뿐인 합성 고객의 내부 행 식별자."""
        _, rows, _ = run.execute(
            "SELECT customer_ref FROM v_irp_customer WHERE is_target = 0 AND customer_name IN ("
            "SELECT customer_name FROM v_irp_customer GROUP BY customer_name HAVING COUNT(*) = 1) "
            "ORDER BY customer_ref", "0001", EMP)
        return next(r["customer_ref"] for r in rows if not C.get_profile(r["customer_ref"])
                    or C.get_profile(r["customer_ref"]).customer_ref == r["customer_ref"])

    def test_branch_customer_without_detail_gets_actual_available_values(self):
        # 상세 원장 공급자가 없는 배포(행내 DB 등)의 경로 — 목업 DB 의 합성 고객 상세 원장(demo_detail)을 빼고 본다
        ref = self._non_target()
        profile = roster.lookup(ref, EMP)
        _, rows, _ = run.execute(f"SELECT is_target FROM v_irp_customer WHERE customer_ref = '{ref}'", "0001", EMP)
        self.assertEqual(rows[0]["is_target"], 0)
        no_detail = patch.object(C, "DETAIL_SOURCES", [])
        no_detail.start()
        self.addCleanup(no_detail.stop)
        result = self.resolve(f"{profile.nm} 고객 자세히 알려줘", self.empty)
        self.assertEqual(result.case, "detail_unavailable")
        self.assertIn(profile.nm, result.answer)
        self.assertIn(f"{profile.ret:+.1f}%", result.answer)
        self.assertIn("| IRP 잔액 |", result.answer)
        self.assertIn("공제 잔여한도", result.answer)
        self.assertIn("다음 만기일", result.answer)
        self.assertNotIn(profile.customer_ref, result.answer)
        self.assertIn("현재 확인할 수 없어요", result.answer)
        self.assertTrue(result.sources)
        followup = self.resolve("그 고객 만기는?", result.context)
        self.assertEqual(followup.context["selected"]["ref"], profile.customer_ref)
        self.assertIn(profile.nm, followup.answer)
        self.assertEqual(suggest.followup_questions({"customer_context": result.context,
                                                    "selection_case": result.case}), [])

    def test_branch_name_lookup_connects_demo_detail(self):
        """목업 DB 에서는 관리 대상이 아닌 합성 고객도 이름으로 고르면 상세 원장(demo_detail)으로 연결된다."""
        ref = self._non_target()
        profile = roster.lookup(ref, EMP)
        result = self.resolve(f"{profile.nm} 고객 자세히 알려줘", self.empty)
        self.assertEqual(result.case, "selected")
        self.assertEqual(result.customer_id, ref)
        detail = C.get_profile(ref)
        self.assertTrue(detail.holdings)
        self.assertEqual(C.conditions(detail), C.conditions(profile))

    def test_branch_lookup_is_scoped_and_fails_closed(self):
        q = f"{self.p1.nm} 고객 자세히 알려줘"
        for emp, case in (("3902201", "name_missing"), ("9999999", "scope_changed"), (None, "scope_changed")):
            ctx = S.context([], emp)
            result = S.resolve(q, ctx, emp)
            self.assertEqual(result.case, case)
            self.assertIsNone(result.customer_id)
            self.assertIsNone(result.context["selected"])
        with patch.object(roster, "find_names", side_effect=run.DBUnavailable("offline")):
            self.assertEqual(self.resolve(q, self.empty).case, "db_unavailable")
        with patch.object(run, "execute", return_value=("sql", [], True)):
            self.assertEqual(self.resolve(q, self.empty).case, "db_unavailable")

    def test_branch_duplicate_outside_displayed_subset_requires_confirmation(self):
        emp = "3902201"
        code, profiles = roster.find_names(["김지유"], emp)
        self.assertEqual(len(profiles), 2)
        first = profiles[0]
        state = {"employee_id": emp, "question": "일부 고객 조회"}
        listed = S.snapshot(state, [{"ref": first.customer_ref, "name": first.nm,
                                    "age": first.ag, "grade": first.club_grade}], code, "일부 고객 조회")
        saved = S.remember(S.context([], emp), [{"meta": {"customer_list": listed}}])
        result = S.resolve("김지유 고객 만기는?", saved, emp)
        self.assertEqual(result.case, "duplicate_name")
        self.assertEqual(len(result.clarify["options"]), 2)
        self.assertEqual(result.context["lists"], saved["lists"])
        self.assertEqual(result.context["active"], saved["active"])
        confirmed = S.resolve("2번", result.context, emp)
        self.assertEqual(confirmed.context["selected"]["ref"], profiles[1].customer_ref)
        explicit = S.resolve("목록 1의 김지유 고객 만기는?", saved, emp)
        self.assertEqual(explicit.context["selected"]["ref"], first.customer_ref)
        expired = deepcopy(result.context)
        expired["directory"]["created"] = 0
        self.assertEqual(S.resolve("2번", expired, emp).case, "expired")

    def test_name_lookup_uses_fixed_scoped_sql_and_safe_values(self):
        with patch.object(run, "execute", wraps=run.execute) as execute:
            code, profiles = roster.find_names(["이준호", "' OR 1=1 --", "%"], EMP)
        self.assertEqual(code, "0001")
        self.assertEqual([p.nm for p in profiles], ["이준호"])
        sql, branch, employee = execute.call_args.args
        self.assertEqual((branch, employee), ("0001", EMP))
        self.assertNotIn("is_target", sql)
        self.assertNotIn("OR 1=1", sql)
        self.assertIn("customer_name IN ('이준호')", sql)

    def test_empty_new_list_and_aggregate(self):
        selected = self.resolve("1번 고객").context
        aggregate = S.remember(selected, [{"tool": "branch_search", "meta": {"rows": 1}}])
        self.assertEqual(self.resolve("그 고객 만기는?", aggregate).customer_id, self.p1.id)
        listed = S.snapshot(dict(self.state, customer_context=selected), [], "0001", "없는 조건")
        updated = S.remember(selected, [{"meta": {"customer_list": listed}}])
        self.assertEqual(self.resolve("1번 고객", updated).case, "out_of_range")
        self.assertEqual(self.resolve("그 고객 만기는?", updated).case, "not_selected")

    def test_expiry_scope_session_and_missing_row(self):
        for change in ({"created": 0}, {"date": "2020-01-01"}):
            saved = deepcopy(self.saved)
            saved["lists"][0].update(change)
            self.assertEqual(self.resolve("1번 고객", saved).case, "expired")
        self.assertEqual(self.resolve("1번 고객", employee_id="3902201").case, "scope_changed")
        hist = [{"question": "q", "customer_context": self.saved}]
        for emp, session in (("3902201", "default"), (EMP, "another")):
            c = S.context(hist, emp, session)
            self.assertEqual(S.resolve("1번 고객", c, emp).case, "no_list")
        with patch.object(roster, "lookup", return_value=None):
            self.assertEqual(self.resolve("1번 고객").case, "changed")
        with patch.object(roster, "lookup", return_value=replace(self.p1, nm="다른이름")):
            self.assertEqual(self.resolve("1번 고객").case, "changed")
        _, rows, _ = run.execute("SELECT * FROM v_irp_customer WHERE customer_ref = '0001:1'", "0001", EMP)
        with patch.object(run, "execute", return_value=("sql", [dict(rows[0], customer_name="바뀐고객")], False)):
            self.assertEqual(self.resolve("1번 고객").case, "changed")
        with patch.object(run, "my_branch", side_effect=run.DBUnavailable("unavailable")):
            self.assertEqual(self.resolve("1번 고객").case, "db_unavailable")

    def test_identifiers_and_unavailable_detail(self):
        saved = deepcopy(self.saved)
        saved["lists"][0]["entries"][0]["ref"] = None
        self.assertEqual(self.resolve("1번 고객", saved).case, "no_identifier")
        self.assertEqual(self.resolve("1번 고객", customer_id=self.p2.id).case, "screen_conflict")
        self.assertIsNone(roster.lookup("0001:1' OR '1'='1", EMP))
        self.assertIsNone(roster.lookup("0001:1", "3902201"))
        with patch.object(roster, "lookup", return_value=replace(self.p1, id="0001:99999")):
            out = self.resolve("1번 고객")
        self.assertEqual(out.case, "detail_unavailable")
        self.assertIsNone(out.customer_id)
        self.assertIn("상세 원장", out.answer)

    def test_generic_questions_and_clear(self):
        for q in ("수익률 낮은 고객 보여줘", "신규 고객 몇 명이야?", "세액공제 한도는?", "2"):
            self.assertEqual(self.resolve(q).case, "none")
        selected = self.resolve("1번 고객").context
        cleared = self.resolve("고객 선택 해제", selected)
        self.assertEqual(cleared.case, "cleared")
        self.assertEqual(self.resolve("그 고객 만기는?", cleared.context).case, "not_selected")
        self.assertEqual(self.resolve("다른 고객 봐줘", selected).case, "not_selected")
        self.assertEqual(S.resolve("1번 고객", self.empty, EMP).case, "no_list")

    def test_no_identity_in_model_history_and_replay(self):
        hist = [{"question": "q", "customer_context": self.saved}]
        formatted = format_history(hist)
        self.assertNotIn("0001:1", formatted)
        self.assertNotIn(self.p1.id, formatted)
        self.assertEqual(self.resolve("목록 다시 보여줘").answer, "목록 1 원문")
        duplicate = [target_list.Target(self.p1, ["tax"]),
                     target_list.Target(replace(self.p2, nm=self.p1.nm), ["tax"])]
        self.assertEqual(target_list.by_names([self.p1.nm], duplicate), [])
        self.assertEqual(target_list.by_refs([self.p2.customer_ref], duplicate)[0].profile.id, self.p2.id)

    def test_malformed_context_is_discarded(self):
        for change in ({"lists": "invalid"}, {"lists": [{}]}, {"pending": "invalid"},
                       {"directory": "invalid"}, {"directory": {"number": 0}},
                       {"selected": {"list": 1, "number": "bad"}}):
            broken = dict(self.saved, **change)
            kept = S.context([{"question": "q", "customer_context": broken}], EMP)
            self.assertEqual(S.resolve("1번 고객", kept, EMP).case, "no_list")

    def test_search_alias_and_zero_result(self):
        for sql in ("SELECT TOP 2 customer_ref, customer_name AS 고객명, age FROM v_irp_customer ORDER BY customer_ref",
                    "SELECT TOP 2 customer_name AS 고객명, age FROM v_irp_customer ORDER BY customer_ref",
                    "SELECT customer_ref, customer_name, age FROM v_irp_customer WHERE age > 200"):
            with self.subTest(sql=sql), patch.object(branch_tool, "_sql", return_value=(sql, "조회")):
                ev = tools.run("branch_search", dict(self.state, customer_context=self.saved), "조회")
                self.assertTrue(ev["meta"].get("customer_list"))
                self.assertNotIn("0001:1", ev["meta"]["document"])
                updated = S.remember(self.saved, [ev])
                found = self.resolve("1번 고객", updated)
                if "age > 200" in sql:
                    self.assertEqual(found.case, "out_of_range")
                elif "customer_ref," in sql:
                    self.assertEqual(found.customer_id, self.p1.id)
                else:
                    self.assertEqual(found.case, "no_identifier")

    def test_list_recommendations_connect_to_actual_customers(self):
        out = {"question": "고객 조회", "customer_context": self.saved, "selection_case": "none"}
        questions = suggest.followup_questions(out)
        self.assertTrue(questions)
        self.assertLessEqual(len(questions), suggest.MAX_FOLLOWUPS)
        self.assertEqual(questions, [f"{p.nm} 고객을 자세히 알려줘" for p in (self.p1, self.p2)])
        for question in questions:
            self.assertNotRegex(question, r"\d+번 고객")
            self.assertEqual(self.resolve(question).case, "selected")
        saved = deepcopy(self.saved)
        saved["lists"][0]["entries"][0]["ref"] = None
        questions = suggest.followup_questions({**out, "customer_context": saved})
        self.assertTrue(questions)
        # 이름 질문은 목록 식별자가 없더라도 부점 전체를 정확 일치 재조회해 연결 가능.
        self.assertEqual({self.resolve(q, saved).customer_id for q in questions}, {self.p1.id, self.p2.id})
        replay = {**out, "selection_case": "list_replay", "question": "목록 다시 보여줘"}
        questions = suggest.followup_questions(replay)
        self.assertNotIn("목록 다시 보여줘", questions)
        with patch.object(S, "resolve", side_effect=RuntimeError("validation failed")):
            self.assertEqual(suggest.followup_questions(out), [])

    def test_selected_recommendations_use_unasked_material(self):
        selected = self.resolve("2번 고객")
        out = {"question": "2번 고객 만기 상품은?", "customer_context": selected.context,
               "customer_id": selected.customer_id, "selection_case": "selected"}
        questions = suggest.followup_questions(out)
        self.assertTrue(questions)
        self.assertTrue(all("만기" not in q for q in questions))
        self.assertTrue(any("세액공제" in q for q in questions))
        for q in questions:
            self.assertIn(self.p2.nm, q)
            self.assertEqual(self.resolve(q, selected.context).customer_id, self.p2.id)
        self.assertTrue(all(self.p2.id not in q for q in questions))

    def test_named_recommendations_keep_explicit_previous_list(self):
        # 최신 목록에 이름이 같은 다른 고객이 있어도 이전 목록에서 고른 고객을 유지.
        newer = S.snapshot(dict(self.state, customer_context=self.saved), [
            {"ref": self.p2.customer_ref, "name": self.p1.nm, "age": self.p2.ag,
             "grade": self.p2.club_grade}], "0001", "새 조건")
        ctx = S.remember(self.saved, [{"meta": {"customer_list": newer}}])
        selected = self.resolve("목록 1의 1번 고객", ctx)
        questions = suggest.followup_questions({"customer_context": selected.context,
            "customer_id": selected.customer_id, "selection_case": "selected"})
        self.assertTrue(questions)
        for q in questions:
            self.assertIn("목록 1의", q)
            self.assertIn(self.p1.nm, q)
            self.assertEqual(self.resolve(q, selected.context).customer_id, self.p1.id)

    def test_customer_history_does_not_hide_another_customers_questions(self):
        first = self.resolve("1번 고객")
        second = self.resolve("2번 고객")
        base = {"question": "보유상품을 알려줘", "customer_context": second.context,
                "customer_id": second.customer_id, "selection_case": "selected"}
        previous = {"question": "이 고객의 세액공제 잔여한도를 알려줘", "customer_context": first.context}
        other = suggest.followup_questions({**base, "history": [previous]})
        self.assertTrue(any("세액공제" in q for q in other))
        same = suggest.followup_questions({**base, "history": [dict(previous, customer_context=second.context)]})
        self.assertTrue(all("세액공제" not in q for q in same))

    def test_recovery_recommendations_and_gates(self):
        stale = deepcopy(self.saved)
        stale["lists"][0]["created"] = 0
        questions = suggest.followup_questions({"customer_context": stale, "selection_case": "expired"})
        self.assertEqual(questions, ["고객 조회"])
        selected = self.resolve("1번 고객")
        out = {"customer_context": selected.context, "customer_id": selected.customer_id,
               "selection_case": "selected"}
        for fields in ({"pending_action": {"kind": "memo"}}, {"llm_error": "down"},
                       {"selection_case": "db_unavailable"}, {"selection_case": "screen_conflict"}):
            self.assertEqual(suggest.followup_questions({**out, **fields}), [])
        empty = S.snapshot(dict(self.state, customer_context=self.saved), [], "0001", "결과 없는 조건")
        ctx = S.remember(self.saved, [{"meta": {"customer_list": empty}}])
        questions = suggest.followup_questions({"customer_context": ctx, "selection_case": "none"})
        self.assertTrue(all("번 고객" not in q for q in questions))

    def test_list_ambiguity_recommendations_name_the_list(self):
        listed = S.snapshot(dict(self.state, customer_context=self.saved), self.listed["entries"], "0001", "새 조건")
        ctx = S.remember(self.saved, [{"meta": {"customer_list": listed, "document": "목록 2 원문"}}])
        result = self.resolve("아까 1번 고객", ctx)
        questions = suggest.followup_questions({"customer_context": result.context, "selection_case": result.case})
        self.assertEqual(questions, ["목록 1 다시 보여줘", "목록 2 다시 보여줘"])
        self.assertTrue(all(self.resolve(q, ctx).case == "list_replay" for q in questions))

    def test_briefing_recommendations_continue_with_coaching(self):
        selected = self.resolve("2번 고객")
        ev = {"tool": "briefing", "sources": [], "meta": {}}
        questions = suggest.followup_questions({"question": "고객 전체 브리핑", "evidence": [ev],
            "customer_context": selected.context, "selection_case": "selected", "customer_id": selected.customer_id})
        self.assertTrue(questions)
        self.assertTrue(all(not any(topic in q for topic in ("만기 상품", "세액공제 잔여한도", "보유상품과 수익률"))
                            for q in questions))

    def test_graph_and_http_session(self):
        import main
        from fastapi.testclient import TestClient
        seen = []

        class Fake:
            def invoke(inner, state):
                seen.append(state)
                if state["question"] == "고객 조회":
                    with patch.object(branch_tool, "_sql", return_value=(
                            "SELECT TOP 2 customer_ref, customer_name, age FROM v_irp_customer ORDER BY customer_ref", "고객 조회")):
                        ev = tools.run("branch_search", state, "고객 조회")
                    return {**state, "answer": ev["meta"]["document"], "sources": [], "evidence": [ev]}
                return {**state, "answer": "선택 고객의 답변", "sources": [], "evidence": []}

        context_store.clear()
        # 상담이력은 임시 폴더에 쓰고 끝나면 지운다 — session_store 는 경로를 모듈 상수로 들고 있어(config 값을
        # 임포트 때 복사) config 만 바꾸면 실제 runtime/session_data 에 남았다(2026-10-23)
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "SESSION_DATA_DIR", Path(tmp)), \
                patch.object(session_store, "SESSION_DATA_DIR", Path(tmp)), \
                patch.object(graph, "_AGENT", Fake()), TestClient(main.app) as client:
            def chat(q, session="selection-test"):
                r = client.post("/chat", json={"input_value": json.dumps({
                    "message": q, "x_client_user": EMP, "session_id": session, "stream": False})},
                    headers={"Accept": "application/json"})
                self.assertEqual(r.status_code, 200)
                return r
            def recommendations(response):
                events = [json.loads(line) for line in response.json()["content"].splitlines()]
                return next(e["items"] for e in events if e["type"] == "followups")

            listed_response = chat("고객 조회")
            self.assertEqual(len(seen), 1)
            questions = recommendations(listed_response)
            self.assertTrue(questions)
            selected_response = chat(questions[0])
            selected_id = seen[-1]["customer_id"]
            self.assertIsNotNone(C.get_profile(selected_id))
            questions = recommendations(selected_response)
            self.assertTrue(questions)
            chat(questions[0])
            self.assertEqual(seen[-1]["customer_id"], selected_id)
            count = len(seen)
            r = chat("999번 고객")
            self.assertEqual(len(seen), count)
            self.assertIn("999", r.text)
            chat("1번 고객", session="different-session")
            self.assertEqual(len(seen), count)
            # 목록·중점관리 추천 없이 바로 이름으로 상세 고객 연결.
            direct = chat("이준호 고객 자세히 알려줘", session="direct-name")
            self.assertEqual(seen[-1]["customer_id"], roster.lookup("0001:3", EMP).id)
            self.assertTrue(recommendations(direct))
            count = len(seen)
            # 상세 원장 공급자가 없는 배포의 경로 — 부점 DB 기본정보만 코드로 답한다
            with patch.object(C, "DETAIL_SOURCES", []):
                available = chat("오다은 고객 자세히 알려줘", session="branch-values")
            self.assertEqual(len(seen), count)  # 원장에 있는 값만 코드로 표시.
            events = [json.loads(line) for line in available.json()["content"].splitlines()]
            self.assertTrue(next(e["items"] for e in events if e["type"] == "sources"))
            self.assertIn("오다은", available.text)
            self.assertIn("IRP 잔액", available.text)
        context_store.clear()


if __name__ == "__main__":
    unittest.main()
