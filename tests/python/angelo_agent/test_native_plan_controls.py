# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode through the real ``Controller`` (doc/angelo/plan_mode.rst, "Contract", "Safety"): planning is Read only
with a flag (never in another mode), only the person ends it, ``angelo_plan`` starts and proposes, and Go runs only in
the person's press: it leaves planning in the setting pressed and sends the chosen approach as their request.

Dependency-free: the C guard is its host twin (``angelo_guard_twin``; ``strict()`` is C exactly), the send path a
recorder; no runtime, account or network."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))

import angelo_guard_twin  # noqa: E402
from angelo_agent import controller_entries, grant_guard, planning_controls as PC, planning_state as S  # noqa: E402,F401
from angelo_agent.chat_settings import clean_options, options_widen, planning, stored_options  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.display_text import Text  # noqa: E402

PLAN = {"summary": "Two ways.", "recommended": 1, "questions": ["For a game?"],
        "approaches": [{"title": "Booleans", "steps": ["Block out", "Cut"], "risks": [], "estimate": "10 min"},
                       {"title": "Modular kit", "steps": ["Make one bay", "Array it"], "risks": ["More objects"],
                        "estimate": "20 min"}]}


def widen(**changes):
    return options_widen({"changes": changes})


class OptionsTests(unittest.TestCase):
    def test_planning_holds_only_in_read_only(self):
        self.assertEqual(stored_options({"approval": "READ_ONLY", "planning": True}),
                         {"model": "", "effort": "", "fast": False, "approval": "READ_ONLY", "planning": True,
                          "plan_return": "ASK"})
        for approval in ("ASK", "AUTO_BOUNDED", None, "nonsense"):
            options = stored_options({"approval": approval, "planning": True, "plan_return": "AUTO_BOUNDED"})
            self.assertNotIn("planning", options, approval)
            self.assertNotIn("plan_return", options, approval)
            self.assertFalse(planning(options))
        for value in (1, "yes", None, False):
            self.assertFalse(planning({"approval": "READ_ONLY", "planning": value}), value)

    def test_a_planning_chat_reads_as_read_only_everywhere(self):
        options = {"approval": "READ_ONLY", "planning": True, "plan_return": "AUTO_BOUNDED"}
        self.assertEqual(clean_options(options)["approval"], "READ_ONLY")
        self.assertEqual(stored_options(options)["plan_return"], "AUTO_BOUNDED")
        self.assertEqual(stored_options(dict(options, plan_return="READ_ONLY"))["plan_return"], "ASK")

    def test_starting_narrows_ending_and_raising_widen(self):
        self.assertFalse(widen(approval="READ_ONLY", planning=True, plan_return="ASK"))
        self.assertTrue(widen(planning=False))
        self.assertTrue(widen(planning=None))
        self.assertTrue(widen(plan_return="AUTO_BOUNDED"))
        self.assertTrue(widen(approval="AUTO_BOUNDED", planning=False))


class ControllerCase(unittest.TestCase):
    def setUp(self):
        angelo_guard_twin.reset()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = Controller(self.directory.name)
        self.addCleanup(self.c.close)
        self.sent = []
        patcher = patch.object(PC, "_send", lambda controller, text: self.sent.append(text))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.c._persons_send = True   # The person's own request is the turn that runs the tool.

    def planning_now(self):
        return planning(self.c.state.get("options"))

    def propose(self, plan=PLAN, call_id="call-1"):
        result = PC.execute(self.c, None, dict(plan, action="PROPOSE"))
        self.c.state["messages"].append({"role": "activity", "call_id": call_id, "tool": "angelo_plan",
                                         "arguments": dict(plan, action="PROPOSE"), "result": result})
        return result

    def notices(self):
        return [m["text"] for m in self.c.state["messages"] if m.get("role") == "system"]


class ToolTests(ControllerCase):
    def test_start_makes_the_chat_read_only_and_keeps_the_build_setting(self):
        person(self.c.set_options, approval="AUTO_BOUNDED")
        result = PC.execute(self.c, None, {"action": "START"})
        self.assertEqual(result["status"], "PLANNING")
        self.assertTrue(self.planning_now())
        self.assertEqual(clean_options(self.c.state["options"])["approval"], "READ_ONLY")
        self.assertEqual(self.c.state["options"]["plan_return"], "AUTO_BOUNDED")
        self.assertEqual(PC.execute(self.c, None, {"action": "START"})["status"], "PLANNING")   # Already: no error.

    def test_the_build_setting_is_auto_only_as_c_holds_it(self):
        angelo_guard_twin.strict()   # C exactly: a chat's Auto is what the ledger holds, not its Python state.
        self.addCleanup(angelo_guard_twin.strict, False)
        self.c.set_options(approval="ASK")   # C holds Ask for the chat now (narrowing: anything may).
        self.c.state["options"]["approval"] = "AUTO_BOUNDED"   # A Python write C never saw.
        PC.start(self.c)
        self.assertEqual(self.c.state["options"]["plan_return"], "ASK")

    def test_start_is_refused_where_planning_is_not_the_persons(self):
        cases = (("_persons_send", False), ("voice", object()), ("external_client", "claude_code"))
        for name, value in cases:
            with self.subTest(name), patch.object(self.c, name, value, create=True):
                with self.assertRaises(ValueError):
                    PC.execute(self.c, None, {"action": "START"})
                self.assertFalse(self.planning_now())
        with patch("angelo_agent.goal_probes.worker_chat", lambda chat: True), self.assertRaises(ValueError):
            PC.execute(self.c, None, {"action": "START"})

    def test_propose_only_while_planning_and_its_row_holds_the_plan(self):
        with self.assertRaises(ValueError) as caught:
            self.propose()
        self.assertIn("NOT_PLANNING", str(caught.exception))
        PC.start(self.c)
        result = self.propose()
        self.assertEqual(result["status"], "SHOWN")
        self.assertEqual(result["plan"]["approaches"][1]["title"], "Modular kit")
        self.assertEqual(S.live_plan(self.c.state["messages"], True)[0], "call-1")

    def test_starting_planning_refuses_queued_mutations_as_read_only_does(self):
        calls = []
        with patch.object(Controller, "finish_tool", lambda self, call, result, **kw: calls.append(result)):
            self.c.pending = {"x": type("Call", (), {"executing": False, "staging": False,
                                                    "name": "angelo_scene_program", "id": "x"})()}
            PC.start(self.c)
            self.c.pending = {}
        self.assertEqual([result["error_code"] for result in calls], ["READ_ONLY"])

    def test_the_agent_never_ends_a_goal_the_person_set(self):
        """Coordinator's design ruling (2026-11-18): planning is Read only, which ends a goal; an agent's START is
        refused while the chat holds one (active, paused, proposed or chosen for the next message)."""
        stopped = []
        for goal, pending in (({"status": "active"}, None), ({"status": "paused"}, None),
                              ({"status": "proposed"}, None), (None, {"status": "pending"})):
            with self.subTest(goal=goal, pending=pending), \
                    patch("angelo_agent.goal_loop.goal_of", lambda c, g=goal: g), \
                    patch("angelo_agent.goal_loop.pending_goal", lambda c, p=pending: p), \
                    patch("angelo_agent.goal_controls.stop", lambda c: stopped.append(c)):
                with self.assertRaises(ValueError) as caught:
                    PC.execute(self.c, None, {"action": "START"})
                self.assertIn("+ menu", str(caught.exception))
                self.assertFalse(self.planning_now())
        self.assertEqual(stopped, [])
        with patch("angelo_agent.goal_loop.goal_of", lambda c: {"status": "stalled"}), \
                patch("angelo_agent.goal_loop.pending_goal", lambda c: None):
            self.assertEqual(PC.execute(self.c, None, {"action": "START"})["status"], "PLANNING")   # An ended goal.

    def test_the_persons_press_ends_the_goal_and_says_so(self):
        from angelo_agent import planning_card, planning_text as T
        stopped, discarded = [], []
        with patch("angelo_agent.goal_loop.goal_of", lambda c: {"status": "active"}), \
                patch("angelo_agent.goal_loop.pending_goal", lambda c: None), \
                patch("angelo_agent.goal_controls.stop", lambda c: stopped.append(c)), \
                patch("angelo_agent.goal_controls.discard_pending", lambda c: discarded.append(c)):
            self.assertTrue(PC.start(self.c, person=True))
        self.assertEqual((stopped, discarded), ([self.c], [self.c]))
        self.assertTrue(self.planning_now())
        self.assertEqual(planning_card.chip_parts(self.c)[0], T.CHIP_PLANNING_GOAL_ENDED)
        source = (HERE.parents[2] / "scripts/addons_core/angelo_agent/chat_add_nav.py").read_text(encoding="utf-8")
        self.assertIn("planning_controls.start(controller, person=True)", source)   # The + menu row is the press.

    def test_a_refused_start_never_ends_the_goal(self):
        """Break-it review nit 4: the person's start narrows first and ends their goal only once planning holds. A
        start refused on its way (inside agent code, a widening ``plan_return``) leaves the goal as it was."""
        stopped, discarded = [], []
        with patch("angelo_agent.goal_loop.goal_of", lambda c: {"status": "active"}), \
                patch("angelo_agent.goal_loop.pending_goal", lambda c: None), \
                patch("angelo_agent.goal_controls.stop", lambda c: stopped.append(c)), \
                patch("angelo_agent.goal_controls.discard_pending", lambda c: discarded.append(c)), \
                patch.object(type(self.c), "set_options", side_effect=grant_guard.Refused()), \
                self.assertRaises(grant_guard.Refused):
            PC.start(self.c, person=True)
        self.assertEqual((stopped, discarded), ([], []))
        self.assertFalse(self.planning_now())

    def test_observed_only_while_planning(self):
        self.assertEqual(PC.observed(self.c), {})
        PC.start(self.c)
        self.assertEqual(PC.observed(self.c), {"plan_mode": "PLANNING"})


def person(function, *args, **kwargs):
    """The person's own press (C's harness scope stands in for their ticketed click)."""
    return angelo_guard_twin.harness(function, *args, **kwargs)


class GoTests(ControllerCase):
    def setUp(self):
        super().setUp()
        person(self.c.set_options, approval="AUTO_BOUNDED")
        PC.start(self.c)
        self.propose()

    def test_go_leaves_planning_in_the_setting_pressed_and_sends_the_approach(self):
        self.assertTrue(person(PC.go, self.c, "call-1", 1, "ASK", "Use 6 bays"))
        self.assertFalse(self.planning_now())
        self.assertEqual(clean_options(self.c.state["options"])["approval"], "ASK")
        text = self.sent[-1]
        self.assertIn("approach 2 of the plan (angelo_plan call call-1)", text)
        self.assertIn('Approach: "Modular kit"', text)
        self.assertIn('1. "Make one bay"', text)
        self.assertIn('Risks: "More objects"', text)
        self.assertIn('(their words): "Use 6 bays"', text)
        self.assertIsNone(self.c._plan_choice)   # Only that request's row carries it.

    def test_readers_of_the_persons_words_read_only_their_notes(self):
        """Break-it review S2: a Go row's text is the model's plan; memory evidence, a worker's brief, search and a
        goal's objective read only what the person typed."""
        from angelo_agent import conversation_search, planning_state, request_rows, worker_request
        choice = S.choice_fields("call-1", 1, PLAN, "ASK", "use six bays")
        row = {"role": "user", "text": S.go_text("call-1", 1, PLAN, "ASK", "use six bays") + " https://example.com/a",
               "plan_choice": choice}
        self.assertEqual(request_rows.persons_text(row), "use six bays")
        self.assertEqual(request_rows.persons_text({"role": "user", "text": "plain"}), "plain")
        self.assertEqual(worker_request.taken(SimpleNamespace(state={"messages": [row]})), ["use six bays"])
        self.assertEqual(conversation_search.requests({"messages": [row]}), ["use six bays"])
        self.assertEqual(planning_state.goal_objective(choice, row["text"]), "use six bays")
        self.assertEqual(planning_state.goal_objective(S.choice_fields("call-1", 1, PLAN, "ASK"), "x"),
                         "Go with approach 2: Modular kit")
        source = (HERE.parents[2] / "scripts/addons_core/angelo_agent/memory_host.py").read_text(encoding="utf-8")
        self.assertIn("value = persons_text(message) if person_request(message) else None", source)

    def test_a_go_sent_again_keeps_its_choice_and_put_back_gives_only_the_notes(self):
        """Break-it review N2: Resend sends a failed Go's text again: its row still carries the choice. Put message
        back and an unsent Go put back the person's notes, never the model's plan."""
        from angelo_agent import drafts, request_rows
        text = S.go_text("call-1", 1, PLAN, "ASK", "six bays")
        self.c.state["messages"].append({"role": "user", "text": text,
                                         "plan_choice": S.choice_fields("call-1", 1, PLAN, "ASK", "six bays")})
        self.c._request_text, self.c._plan_choice = text, None
        self.assertEqual(request_rows.request_fields(self.c)["plan_choice"]["notes"], "six bays")
        self.c.unsent_draft, written = text, []
        drafts.restore_unsent(self.c, active=True, spaces=[0], saved={}, read=lambda s: ("", 0),
                              write=lambda s, d: written.append(d))
        self.assertEqual(written, [("six bays", 8)])

    def test_edits_mark_lives_on_the_draft(self):
        """N2b: Edit marks the chat's draft (saved with the chat); a send that still holds those lines is the model's
        words (from_plan), one retyped is the person's, and every send clears the mark."""
        from angelo_agent import request_rows
        from angelo_agent.sessions import SessionStore
        mark = S.edit_mark("call-1", PLAN, 1)
        person(self.c.set_options, plan_draft=mark)
        store = SessionStore(self.directory.name)
        state = dict(store.new(), options=dict(self.c.state["options"]))
        SessionStore(self.directory.name, state["id"]).save(state)
        stored = SessionStore(self.directory.name, state["id"]).load()["options"]
        self.assertEqual(stored["plan_draft"], mark)   # Saved with the chat: across a restart.
        self.c._request_text, self.c._plan_choice = "Make one bay, but in brick", None
        self.assertEqual(request_rows.request_fields(self.c), {"from_plan": True})
        self.assertNotIn("plan_draft", self.c.state["options"])
        self.assertEqual(request_rows.persons_text({"role": "user", "text": "x", "from_plan": True}), "")
        person(self.c.set_options, plan_draft=mark)
        self.c._request_text = "Something else entirely"   # Emptied and retyped: the person's own words.
        self.assertEqual(request_rows.request_fields(self.c), {})
        self.assertNotIn("plan_draft", self.c.state["options"])

    def test_go_carries_its_choice_on_the_request_row(self):
        from angelo_agent import request_rows
        seen = []
        with patch.object(PC, "_send", lambda c, text: seen.append(request_rows.request_fields(c))):
            person(PC.go, self.c, "call-1", 0, "AUTO_BOUNDED")
        self.assertEqual(seen, [{"plan_choice": {"call_id": "call-1", "approach": 0, "title": "Booleans",
                                                 "approval": "AUTO_BOUNDED", "notes": "", "edited": False}}])
        kept = request_rows.kept_fields(dict(seen[0], role="user"))
        self.assertEqual(kept["plan_choice"]["title"], "Booleans")
        self.assertEqual(request_rows.kept_fields({"role": "user", "plan_choice": {"call_id": 3}}), {})

    def test_the_card_knows_it_was_chosen(self):
        person(PC.go, self.c, "call-1", 1, "ASK")
        self.c.state["messages"].append({"role": "user", "text": self.sent[-1],
                                         "plan_choice": S.choice_fields("call-1", 1, PLAN, "ASK")})
        self.assertEqual(S.card_state(self.c.state["messages"], "call-1", False), (S.CHOSEN, 1))
        self.assertEqual(S.chosen(self.c.state["messages"]), ("call-1", "Modular kit"))

    def test_go_refusals_change_nothing(self):
        cases = (("call-0", 0, "ASK", Text), ("call-1", 2, "ASK", ValueError), ("call-1", 0, "READ_ONLY", ValueError),
                 ("call-1", "0", "ASK", ValueError))
        for call_id, approach, approval, kind in cases:
            with self.subTest(call_id=call_id, approach=approach, approval=approval):
                with self.assertRaises(ValueError) as caught:
                    person(PC.go, self.c, call_id, approach, approval)
                if kind is Text:
                    self.assertIsInstance(caught.exception.args[0], Text)
                self.assertTrue(self.planning_now())
        self.propose(call_id="call-2")   # A newer plan supersedes the first.
        with self.assertRaises(PC.Refusal):
            person(PC.go, self.c, "call-1", 0, "ASK")
        self.assertEqual(S.card_state(self.c.state["messages"], "call-1", True)[0], S.SUPERSEDED)
        with patch.object(self.c, "turn_running", lambda: True), self.assertRaises(PC.Refusal):
            person(PC.go, self.c, "call-2", 0, "ASK")
        self.assertEqual(self.sent, [])

    def test_a_refused_send_puts_planning_back(self):
        def refuse(controller, text):
            raise RuntimeError(Text("The document changed; start a new chat"))
        with patch.object(PC, "_send", refuse), self.assertRaises(PC.Refusal) as caught:
            person(PC.go, self.c, "call-1", 0, "AUTO_BOUNDED")
        self.assertTrue(self.planning_now())
        self.assertEqual(self.c.state["options"]["plan_return"], "AUTO_BOUNDED")
        self.assertEqual(caught.exception.args[0].fields["reason"], "The document changed; start a new chat")

    def test_stop_returns_to_ask_and_sends_nothing(self):
        """Break-it review M1: Stop planning's press shows no setting, so it never widens: Ask, even from Auto."""
        self.assertTrue(person(PC.stop, self.c))
        self.assertFalse(self.planning_now())
        self.assertEqual(clean_options(self.c.state["options"])["approval"], "ASK")
        self.assertEqual(self.sent, [])
        self.assertFalse(person(PC.stop, self.c))

    def test_a_written_build_setting_never_widens_stop(self):
        """M1 from Ask: a script writes the options in place (plan_return Auto); the person's Stop stays Ask in C."""
        angelo_guard_twin.strict()
        self.addCleanup(angelo_guard_twin.strict, False)
        person(PC.stop, self.c)
        person(self.c.set_options, approval="ASK")
        PC.start(self.c)
        self.c.state["options"].update(approval="READ_ONLY", planning=True, plan_return="AUTO_BOUNDED")   # In place.
        person(PC.stop, self.c)
        from angelo_agent import grant_authority
        self.assertEqual(clean_options(self.c.state["options"])["approval"], "ASK")
        self.assertFalse(grant_authority.chat_in_auto(self.c.state["id"]))

    def test_go_waits_for_the_persons_tool(self):
        """A send under the person's tool waits in the tray without its choice (break-it NIT): Go refuses instead."""
        with patch("angelo_agent.edit_tool_hold.running", lambda: True), self.assertRaises(PC.Refusal) as caught:
            person(PC.go, self.c, "call-1", 0, "ASK")
        self.assertEqual(caught.exception.args[0], "Finish the tool you are using, then press Go with this.")
        self.assertTrue(self.planning_now())
        self.assertEqual(self.sent, [])

    def test_notes_that_began_with_edit_are_labelled_and_never_the_persons_words(self):
        from angelo_agent import request_rows
        person(self.c.set_options, plan_draft=S.edit_mark("call-1", PLAN, 1))   # The card's Edit copied approach 2.
        seen = []
        with patch.object(PC, "_send", lambda c, text: seen.append((text, request_rows.request_fields(c)))):
            person(PC.go, self.c, "call-1", 1, "ASK", "A modular kit\n1. Make one bay\nsix bays")
        text, fields = seen[0]
        self.assertIn("copied into their message box with Edit", text)
        self.assertTrue(fields["plan_choice"]["edited"])
        self.assertEqual(request_rows.persons_text(dict(fields, role="user", text=text)), "")

    def test_the_approval_control_ends_planning(self):
        person(self.c.set_options, approval="ASK")
        self.assertFalse(self.planning_now())


class PressTests(ControllerCase):
    """Go and Stop planning take effect only in the person's press (C exactly: ``strict``)."""

    def setUp(self):
        super().setUp()
        person(self.c.set_options, approval="AUTO_BOUNDED")
        PC.start(self.c)
        self.propose()
        angelo_guard_twin.strict()
        self.addCleanup(angelo_guard_twin.strict, False)

    def test_go_outside_any_scope_is_refused(self):
        for function, args in ((PC.go, (self.c, "call-1", 0, "AUTO_BOUNDED")), (PC.stop, (self.c,))):
            with self.subTest(function.__name__), self.assertRaises(PermissionError):
                function(*args)
        self.assertTrue(self.planning_now())
        self.assertEqual(self.sent, [])

    def test_angelo_s_own_automatic_decision_cannot_press_go(self):
        for function, args in ((PC.go, (self.c, "call-1", 0, "AUTO_BOUNDED")), (PC.stop, (self.c,))):
            with self.subTest(function.__name__), self.assertRaises(PermissionError):
                angelo_guard_twin.automatic(function, *args)
        self.assertTrue(self.planning_now())

    def test_an_agent_script_cannot_press_go_or_end_planning(self):
        with angelo_guard_twin.confine(), grant_guard.agent_script():
            for function, args in ((PC.go, (self.c, "call-1", 0, "ASK")), (PC.stop, (self.c,))):
                with self.subTest(function.__name__), self.assertRaises(PermissionError):
                    function(*args)
            for changes in ({"approval": "ASK"}, {"planning": False}, {"approval": "AUTO_BOUNDED"},
                            {"plan_return": "AUTO_BOUNDED"}):
                with self.subTest(changes=changes), self.assertRaises(PermissionError):
                    self.c.set_options(**changes)
            self.assertFalse(PC.start(self.c))   # Already planning: no change.
        self.assertTrue(self.planning_now())
        self.assertEqual(self.c.state["options"]["plan_return"], "AUTO_BOUNDED")
        self.assertEqual(self.sent, [])

    def test_the_person_s_press_goes(self):
        angelo_guard_twin.press(PC.GO_OPERATOR, "ticket")
        self.assertTrue(angelo_guard_twin.grant(PC.GO_OPERATOR, "ticket", PC.go, self.c, "call-1", 1, "AUTO_BOUNDED"))
        self.assertFalse(self.planning_now())
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
