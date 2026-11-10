# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat never says it waits for the person without a card, names the card it waits on, says why a card asks in
Auto, and leads to a card scrolled out of view; a status the host set for one call ends with that call.

Found on 1.0.9 (Mac 2 dogfood, a GPT build turn in "Approve for me · scripts allowed"): an unanswered web-export card
expired after 180 s and the live row then read "Waiting for your approval" through four more minutes of build steps
with no card anywhere, and again after every card the person approved (web exports, PNG renders, Angelo Library
imports), because the controller's status kept "Approval required" until the runtime's next status event, which
Codex does not send between steps. Real ``Controller`` in a temporary profile with Blender stand-ins; no provider.
Contract: doc/angelo/agent_chat.rst, "Approvals".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import always_allow, approval_wait, auto_reasons, call_status, live_progress, managed_policy  # noqa: E402
from angelo_agent import canvas_controls, worker_sessions  # noqa: E402
from angelo_agent import session_rail  # noqa: E402
from angelo_agent.chat_titles import LIVE  # noqa: E402
from angelo_agent.claude_stream import STATUS_THINKING  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.trusted_scripts import TrustedScripts  # noqa: E402

WAITING_WORDS = ("approval", "Approval")


def source(name):
    return (ADDON / name).read_text(encoding="utf-8")


def function(name, module, owner=None):
    tree = ast.parse(source(module))
    scope = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner) if owner else tree
    return ast.get_source_segment(source(module), next(
        node for node in ast.walk(scope) if isinstance(node, ast.FunctionDef) and node.name == name))


class NoStatusClaimsApproval(unittest.TestCase):
    """The class, not the case: no status anywhere says the chat waits for the person; only a card does."""

    def test_no_module_sets_an_approval_status(self):
        claim = re.compile(r'\.status\s*=\s*"(Approval required|Approve collaborator usage)"')
        for path in sorted(ADDON.rglob("*.py")):
            with self.subTest(module=path.name):
                self.assertIsNone(claim.search(path.read_text(encoding="utf-8")))
        for status in ("Approval required", "Approve collaborator usage"):
            self.assertNotIn(status, LIVE, "no live-row label for a status nothing sets")

    def test_the_card_and_the_rail_read_the_same_one_call(self):
        self.assertIn("approval_wait.awaiting(controller)", function("cards", "chat_blocks.py"))
        self.assertIn("approval_wait.awaiting(controller)", function("states", "session_rail_draw.py"))
        self.assertIn("approval_wait.label(controller)", function("states", "session_rail_draw.py"))
        self.assertIn("approval_wait.request_reveal(session_id, approval_wait.awaiting(",
                      function("switch", "session_rail_input.py"))
        self.assertIn("approval_wait.reason_rows(", function("approval_block", "chat_card_draw.py"))
        self.assertIn("approval_wait.reason_text(controller, pending)", function("cards", "chat_blocks.py"))   # Flows.
        self.assertIn('message.get("note", "")', source("chat_view.py"))
        for module, name in (("approval_review.py", "prepare_canvas_reviews"), ("ui.py", None)):
            text = function(name, module) if name else source(module)
            with self.subTest(module=module):   # Every first-waiting-call lookup is the card's own.
                self.assertIn("approval_wait.awaiting(", text)
                self.assertNotIn("if call.approval), None)", text)


class CardIsTheOnlyWaitingSign(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        always_allow.detach()
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.busy = True
        self.controller.set_document(("isolated",))
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executor = SimpleNamespace(precondition=Mock(return_value="unchanged"),
                                        execute=Mock(return_value={"success": True}))
        self.count = 0

    def pump(self):
        self.controller.pump_tools(self.executor, self.controller.document,
            apply_mutation=lambda call: self.controller.execute_tool(call, self.executor, self.controller.document),
            budget=ToolDispatchBudget(seconds=60))

    def ask(self):
        """A file write: it asks in every mode (doc/angelo/agent_chat.rst), so it raises a card in Auto too."""
        self.count += 1
        identity, replies = "write-%d" % self.count, []
        self.controller.status = STATUS_THINKING
        self.controller.request_tool(identity, "angelo_write_file", {"path": "notes.txt", "content": "x"},
                                     replies.append)
        self.pump()
        return self.controller.pending.get(self.controller.state["id"] + "/" + identity), replies

    def phase(self):
        return live_progress.phase(live_progress.facts(self.controller))

    def assert_nothing_says_it_waits(self):
        self.assertIsNone(approval_wait.awaiting(self.controller))
        self.assertEqual(approval_wait.label(self.controller), "")
        self.assertFalse(any(word in self.phase() for word in WAITING_WORDS), self.phase())
        self.assertFalse(any(word in str(self.controller.status) for word in WAITING_WORDS), self.controller.status)

    def test_a_card_in_auto_names_itself_and_says_why_it_asks(self):
        self.assertEqual(self.controller.state["options"]["approval"], "AUTO_BOUNDED")
        call, replies = self.ask()
        self.assertTrue(call is not None and call.approval and not replies, "the write waits for its card")
        self.assertIs(approval_wait.awaiting(self.controller), call)
        self.assertEqual(self.controller.status, STATUS_THINKING, "the status never claims the card")
        from angelo_agent.approval_review import preview_text
        card_title = preview_text(call).partition("\n")[0]
        self.assertEqual(approval_wait.title(call), card_title)
        self.assertEqual(approval_wait.label(self.controller), "Waiting for your approval: " + card_title)
        # A text file always asks (the maintainer, 2026-11-05): its own line, from the rule's own code.
        self.assertEqual(approval_wait.auto_reason(self.controller, call), line(auto_reasons.WRITE_FILE))
        rows = approval_wait.reason_rows(self.controller, call, lambda text, *_: [(text, 0)], 400, 13, 0)
        self.assertEqual(rows, ((auto_reasons.LINES[auto_reasons.WRITE_FILE], 0),))

    def test_declined_expired_or_answered_cards_leave_nothing_saying_it_waits(self):
        call, replies = self.ask()
        self.controller.decline_tool(call.id)
        self.assertEqual(replies[-1]["error_code"], "DECLINED")
        self.assert_nothing_says_it_waits()
        call, replies = self.ask()
        call.expires = 0   # Nobody answered in time: 1.0.9's web-export card.
        self.pump()
        self.assertEqual(replies[-1]["error_code"], "EXPIRED")
        self.assert_nothing_says_it_waits()
        call, replies = self.ask()
        self.controller.finish_tool(call, {"success": True})   # Answered and done (the file tool's own path aside).
        self.assert_nothing_says_it_waits()

    def test_the_real_finish_tool_ends_the_status_its_call_set(self):
        first, _ = self.ask()
        second, _ = self.ask()
        first.shown_status = "Generating media"   # As controller.py's sites write it: call.shown_status = self.status.
        second.shown_status = self.controller.status = "Waiting for a render"
        self.controller.finish_tool(second, {"success": True})
        self.assertEqual(self.controller.status, "Generating media", "the other call's work still runs")
        self.controller.finish_tool(first, {"success": True})
        self.assertEqual(self.controller.status, STATUS_THINKING)

    def test_a_connected_apps_status_says_it_waits_only_while_a_card_waits(self):
        # Compatibility with 1.0.9's task replies (external_apps): the same words, derived from the card.
        self.assertIn('"status": status_for_apps(controller)', source("external_apps.py"))
        self.assertEqual(approval_wait.status_for_apps(self.controller), self.controller.status)
        call, _ = self.ask()
        self.assertEqual(approval_wait.status_for_apps(self.controller), "Approval required")
        self.controller.decline_tool(call.id)
        self.assertEqual(approval_wait.status_for_apps(self.controller), self.controller.status)
        self.assertNotEqual(self.controller.status, "Approval required", "never after its card")
        collaborator = SimpleNamespace(name="angelo_delegate", approval=True)
        self.assertEqual(approval_wait.status_for_apps(SimpleNamespace(pending={"c": collaborator}, status="x")),
                         "Approve collaborator usage")

    def test_ask_mode_needs_no_reason_line(self):
        self.controller.set_options(approval="ASK")
        call, _ = self.ask()
        self.assertIsNone(approval_wait.auto_reason(self.controller, call))
        self.assertEqual(approval_wait.reason_rows(self.controller, call, lambda text, *_: [(text, 0)], 400, 13, 0), ())


def line(code):
    """The ``(msgid, fields)`` a card reads for one of the rule's codes."""
    return auto_reasons.LINES[code], auto_reasons.line_fields(code)


def rows(chat, call):
    return approval_wait.reason_rows(chat, call, lambda text, *_: [text], 400, 13, 0)


class AutoReasons(unittest.TestCase):
    """Why a card asks in Auto is the reason code of the rule that made it ask, never a list of this case's tools."""

    def setUp(self):
        requests = patch.dict(worker_sessions._requests, clear=True)
        requests.start()
        self.addCleanup(requests.stop)

    @staticmethod
    def chat(mode="AUTO_BOUNDED", automatic=False, always=False, trusted=False, gate=True):
        """A chat whose Auto decision is the controller's own rule (``gate``: a worker chat's extra admission)."""
        chat = SimpleNamespace(state={"id": "lead-why", "options": {"approval": mode}, "messages": []},
                               _automatic_script=lambda call: automatic, always_allow_scripts=always,
                               trusted_scripts=trusted, explains_auto=True)
        chat._auto_bounded = lambda call, mode=None: gate and Controller._auto_bounded(chat, call, mode)
        return chat

    @staticmethod
    def call(name, code=None, recovery=False, **arguments):
        if code is not None:
            arguments["code"] = code
        return SimpleNamespace(name=name, arguments=arguments, recovery_review=recovery)

    def test_kinds_of_step_auto_never_runs_say_so(self):
        # The web export and job start pins change when the new-output-file rule lands (a ruled behaviour change,
        # the maintainer 2026-11-05: their decision's own reason, or None).
        for name in ("angelo_web_export", "angelo_job_start", "angelo_delete_file", "angelo_studio_import_3d"):
            with self.subTest(tool=name):
                self.assertEqual(approval_wait.auto_reason(self.chat(), self.call(name)), line(auto_reasons.OUTSIDE))
        self.assertEqual(approval_wait.auto_reason(self.chat(), self.call("angelo_write_file")),
                         line(auto_reasons.WRITE_FILE))
        # A library import runs in Approve for me (one Undo step; a paid Fab asset is refused): the maintainer,
        # 2026-11-09. Ask still asks.
        self.assertIsNone(auto_reasons.bounded_reason(self.chat(), self.call("angelo_library_import"), "AUTO_BOUNDED"))
        self.assertEqual(auto_reasons.bounded_reason(self.chat(), self.call("angelo_library_import"), "ASK"),
                         auto_reasons.NOT_AUTO)

    def test_every_code_the_rule_returns_in_auto_has_its_line_or_a_stated_reason_for_none(self):
        # The class: a new branch of Auto's rule (the new-output-file rule's overwrite, say) cannot ship a card
        # that says nothing, or another branch's words, about why it asks.
        tree = ast.parse(source("auto_reasons.py"))
        rule = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "bounded_reason")
        returned = {node.value.id for node in ast.walk(rule) if isinstance(node, ast.Return)
                    and isinstance(node.value, ast.Name)}
        self.assertTrue(returned, "the rule returns named codes")
        returned |= {node.body.id for node in ast.walk(rule) if isinstance(node, ast.IfExp)
                     and isinstance(node.body, ast.Name)} | {node.orelse.id for node in ast.walk(rule)
                                                             if isinstance(node, ast.IfExp)
                                                             and isinstance(node.orelse, ast.Name)}
        for name in sorted(returned):
            with self.subTest(code=name):
                code = getattr(auto_reasons, name)
                self.assertTrue(code in auto_reasons.LINES or code in auto_reasons.NO_LINE, name)
        self.assertEqual(set(auto_reasons.LINES) & set(auto_reasons.NO_LINE), set())
        for code, text in auto_reasons.LINES.items():
            with self.subTest(line=code):
                self.assertNotIn("{", text.replace("{count}", ""), "only placeholders line_fields fills")
                self.assertEqual("{count}" in text, "count" in auto_reasons.line_fields(code))

    def test_the_line_is_selected_by_what_made_it_ask(self):
        # The interface the new-output-file rule plugs into (design auto_outputs, 4.3): a branch returns a code, or
        # its own (msgid, fields) for a denied decision; the card says exactly that, and it is what made the
        # controller ask. Undecided: it asks, with no line yet.
        chat, export = self.chat(), self.call("angelo_job_start", kind="RENDER_STILL")
        taken = ("{path} already exists, so it asks.", {"path": "render.png"})
        for reason, shown in ((auto_reasons.OUTSIDE, (auto_reasons.LINES[auto_reasons.OUTSIDE],)),
                              (taken, ("render.png already exists, so it asks.",)), (auto_reasons.UNDECIDED, ())):
            with self.subTest(reason=reason), patch.object(auto_reasons, "bounded_reason", return_value=reason):
                self.assertFalse(Controller._auto_bounded(chat, export, "AUTO_BOUNDED"))
                self.assertEqual(rows(chat, export), shown)
        with patch.object(auto_reasons, "bounded_reason", return_value=None):   # Allowed: it runs, no card.
            self.assertTrue(Controller._auto_bounded(chat, export, "AUTO_BOUNDED"))
            self.assertIsNone(approval_wait.auto_reason(chat, export))

    def test_bounded_edits_that_ask_say_which_rule_held_them(self):
        chat = self.chat()
        too_many = self.call("angelo_workers_start", parts=[{}] * (worker_sessions.MAX_AUTO_STARTS + 1))
        self.assertEqual(approval_wait.auto_reason(chat, too_many), line(auto_reasons.WORKER_START_CAP))
        self.assertEqual(rows(chat, too_many), (auto_reasons.LINES[auto_reasons.WORKER_START_CAP].format(
            count=worker_sessions.MAX_AUTO_STARTS),))
        self.assertIsNone(approval_wait.auto_reason(chat, self.call("angelo_workers_start", parts=[{}])), "it runs")
        with patch("angelo_agent.worker_merge.auto_merge_allowed", return_value=False):
            self.assertEqual(approval_wait.auto_reason(chat, self.call("angelo_workers_merge", parts=[
                {"worker": 1, "again": True}])), line(auto_reasons.WORKER_MERGE_AGAIN))
        with patch("angelo_agent.render_extensions.engine_edits", return_value={"OCTANE": "Octane"}):
            self.assertEqual(approval_wait.auto_reason(chat, self.call("angelo_scene_program", operations=[])),
                             line(auto_reasons.ADDON_ENGINE))
        with patch("angelo_agent.render_extensions.engine_edits", return_value={}):
            self.assertIsNone(approval_wait.auto_reason(chat, self.call("angelo_scene_program", operations=[])))

    def test_no_reason_where_auto_runs_it_or_the_card_says_its_own(self):
        cases = {"Auto runs it (a standing consent)": (self.chat(automatic=True), self.call("angelo_execute_python",
                                                                                            "x = 1")),
                 "a bounded edit": (self.chat(), self.call("angelo_set_transform")),
                 "a worker chat's own gate held a bounded edit": (self.chat(gate=False),
                                                                  self.call("angelo_set_transform")),
                 "a collaborator's usage": (self.chat(), self.call("angelo_delegate")),
                 "a recovery review": (self.chat(), self.call("angelo_web_export", recovery=True)),
                 "Ask": (self.chat("ASK"), self.call("angelo_web_export")),
                 "Read only": (self.chat("READ_ONLY"), self.call("angelo_web_export")),
                 "an ordinary script before Always allow (its card offers it)": (self.chat(), self.call(
                     "angelo_execute_python", "x = 1")),
                 "an app's script file (its card offers to allow the file)": (self.chat(), self.call(
                     "angelo_run_script_file")),
                 "a chat without the controller's decision (a test double)": (SimpleNamespace(
                     state={"options": {"approval": "AUTO_BOUNDED"}}, explains_auto=True),
                     self.call("angelo_web_export")),
                 "a chat whose Auto decision is a worker's own verdict": (SimpleNamespace(
                     **dict(vars(self.chat()), explains_auto=False)), self.call("angelo_memory_update"))}
        for label, (chat, call) in cases.items():
            with self.subTest(case=label):
                self.assertIsNone(approval_wait.auto_reason(chat, call))
                self.assertEqual(rows(chat, call), ())

    def test_a_script_naming_preferences_or_the_add_on_says_it_always_asks(self):
        # True whatever is allowed: a real Controller and its own consent rule (script_consent_kind), with Always
        # allow scripts really off, then on, then Trust scripts for this chat.
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        always_allow.detach()
        self.addCleanup(always_allow.detach)
        for consent in ("none", "always", "chat"):
            with tempfile.TemporaryDirectory() as directory:
                controller = Controller(directory)
                try:
                    controller.busy = True
                    controller.set_document(("isolated",))
                    controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
                    always_allow.set_enabled(consent == "always")
                    controller.set_trusted_scripts(consent == "chat")   # Trust scripts for this chat, as its UI does.
                    for index, (code, excluded) in enumerate((("bpy.context.preferences.view.ui_scale = 2", True),
                                                              ("import angelo_agent", True), ("x = 1", False))):
                        identity = "script-%d" % index
                        controller.request_tool(identity, "angelo_execute_python",
                                                {"description": "A step", "code": code}, lambda result: None)
                        call = controller.pending[controller.state["id"] + "/" + identity]
                        with self.subTest(consent=consent, code=code):
                            self.assertEqual(controller._automatic_script(call), not excluded and consent != "none")
                            self.assertEqual(approval_wait.auto_reason(controller, call),
                                             line(auto_reasons.SCRIPT_EXCLUDED) if excluded else None)
                finally:
                    always_allow.set_enabled(False)
                    controller.close()

    def test_the_rule_decides_exactly_as_the_1_0_10_rule_did(self):
        # Auto's rule moved from Controller._auto_bounded to auto_reasons.bounded_reason: for every tool, mode and
        # limit, what runs with no card is unchanged (the 1.0.10 body, verbatim, as the reference).
        from angelo_agent import render_extensions, worker_merge, worker_resume
        from angelo_agent.chat_settings import clean_options
        from angelo_agent.engine_link_tool_spec import EDIT, asks_only
        from angelo_agent.tool_specs import BOUNDED_MUTATIONS, TOOL_MAP

        def legacy(self, call, mode=None):
            mode = mode or clean_options(self.state.get("options"))["approval"]
            if mode in {"ASK", "AUTO_BOUNDED"} and asks_only(call.name, getattr(call, "arguments", None)):
                return True
            if mode == "AUTO_BOUNDED" and call.name == "angelo_library_import":
                return True   # The one ruled change since 1.0.10 (the maintainer, 2026-11-09).
            if mode != "AUTO_BOUNDED" or call.name not in BOUNDED_MUTATIONS:
                return mode == "ASK" and call.name == "angelo_workers_merge" and worker_resume.event_merge(
                    self, call.arguments)
            if call.name in {"angelo_workers_start", "angelo_workers_merge"}:
                allowed = (worker_sessions.auto_start_allowed if call.name.endswith("start")
                           else worker_merge.auto_merge_allowed)
                return allowed(self, call.arguments)
            return call.name != "angelo_scene_program" or not render_extensions.engine_edits(call.arguments)

        calls = [self.call(name) for name in sorted(set(TOOL_MAP) | BOUNDED_MUTATIONS | {EDIT})]
        calls += [self.call(EDIT, action="OPEN"), self.call(EDIT, action="CLOSE"),
                  self.call("angelo_workers_start", parts=[{}]),
                  self.call("angelo_workers_start", parts=[{}] * (worker_sessions.MAX_AUTO_STARTS + 1)),
                  self.call("angelo_workers_merge", parts=[{"worker": 1}]),
                  self.call("angelo_workers_merge", parts=[{"worker": 1, "again": True}]),
                  self.call("angelo_scene_program", operations=["engine"]),
                  self.call("angelo_scene_program", operations=[])]
        merges = patch.object(worker_merge, "auto_merge_allowed", lambda chat, arguments: not any(
            part.get("again") for part in (arguments or {}).get("parts") or ()))
        events = patch.object(worker_resume, "event_merge", lambda chat, arguments: bool((arguments or {}).get("parts")))
        engines = patch.object(render_extensions, "engine_edits", lambda arguments: {"OCTANE": "Octane"} if (
            arguments or {}).get("operations") == ["engine"] else {})
        with merges, events, engines:
            for stored in ("ASK", "AUTO_BOUNDED", "READ_ONLY"):
                chat = self.chat(stored)
                for mode in (None, "ASK", "AUTO_BOUNDED", "READ_ONLY"):
                    for call in calls:
                        with self.subTest(stored=stored, mode=mode, tool=call.name, arguments=call.arguments):
                            self.assertEqual(Controller._auto_bounded(chat, call, mode), legacy(chat, call, mode))

    def test_the_controllers_decision_is_the_rules_code(self):
        self.assertIn("auto_reasons.bounded_reason(self, call, mode) is None",
                      function("_auto_bounded", "controller.py", "Controller"))
        self.assertIn("auto_reasons.bounded_reason(controller, call, ", function("auto_reason", "approval_wait.py"))
        self.assertIn("consent_excluded(call)", function("script_consent_kind", "trusted_scripts.py", "TrustedScripts"))

    def test_only_a_chat_deciding_by_the_base_rule_explains_auto(self):
        from angelo_agent.worker_consent import LeasedConsent
        from angelo_agent.worker_mirror import MirrorController
        self.assertTrue(Controller.explains_auto)
        self.assertFalse(MirrorController.explains_auto)
        self.assertFalse(LeasedConsent.explains_auto)
        self.assertIn("LeasedConsent", [base.__name__ for base in type("Worker", (LeasedConsent, Controller), {}).__mro__])
        self.assertFalse(type("Worker", (LeasedConsent, Controller), {}).explains_auto)


class Pointer(unittest.TestCase):
    """A card whose buttons are out of view is named by a pill that brings them back; a rail tile's click too."""

    def setUp(self):
        self.call = SimpleNamespace(id="chat/c1", approval=True, name="angelo_write_file", arguments={})
        self.controller = SimpleNamespace(state={"id": "chat"}, pending={"chat/c1": self.call})
        # Newest last: a reply, the card (200 px), then a job card (100 px) under it.
        self.blocks = [("reply", {}, (), 300, "r1", 0), ("approval", {}, (), 200, "chat/c1", 0),
                       ("native_job", {}, (), 100, "j1", 0)]
        title = patch.object(approval_wait, "title", return_value="Write notes.txt")
        title.start()
        self.addCleanup(title.stop)
        self.addCleanup(approval_wait._reveal.clear)

    def place(self, scroll, visible=400, blocks=None):
        state = {}
        return approval_wait.place(state, self.controller, self.blocks if blocks is None else blocks, scroll,
                                   visible), state.get("approval_pointer")

    def test_in_view_no_pointer_out_of_view_a_pointer_naming_the_card(self):
        self.assertEqual(self.place(0), (0, None))
        scroll, pointer = self.place(500)   # Scrolled up into history: the card is below the view.
        self.assertEqual(scroll, 500)
        self.assertEqual(pointer, ("chat/c1", "Waiting for your approval: Write notes.txt", True))

    def test_the_pointers_click_and_a_rail_tiles_click_bring_the_buttons_into_view(self):
        approval_wait.reveal({}, self.controller)
        self.assertEqual(self.place(500), (100, None), "the card's bottom edge at the view's bottom")
        approval_wait.request_reveal("chat", self.call)
        self.assertEqual(self.place(0, visible=120), (100, None))

    def test_a_reveal_waits_for_its_card_and_lapses_with_it(self):
        approval_wait.request_reveal("chat", self.call)
        self.assertEqual(self.place(500, blocks=self.blocks[:1]), (500, None), "rows still being prepared")
        self.assertIn("chat", approval_wait._reveal)
        self.controller.pending = {}
        self.assertEqual(self.place(500), (500, None))
        self.assertNotIn("chat", approval_wait._reveal, "nothing waits any more")

    def test_a_reveal_is_for_its_own_card_only(self):
        approval_wait.request_reveal("chat", SimpleNamespace(id="chat/earlier"))   # Asked for a card since answered.
        self.assertEqual(self.place(500)[0], 500, "a later card is never scrolled to unasked")
        self.assertNotIn("chat", approval_wait._reveal)

    def test_the_pointer_is_judged_where_the_view_will_be_after_its_clamp(self):
        # 600 px of content, 400 visible: a scroll of 500 is drawn at 200, still above the card's buttons (100 px
        # up from the newest row): pointed at. 300 px of content, 250 visible: drawn at 50, the buttons in view.
        self.assertEqual(self.place(500), (500, ("chat/c1", "Waiting for your approval: Write notes.txt", True)))
        self.assertEqual(self.place(500, blocks=self.blocks[1:], visible=250)[1], None)

    def test_a_failure_never_takes_the_chat_down(self):
        with patch.object(approval_wait, "label", side_effect=RuntimeError("broken")):
            self.assertEqual(self.place(500), (500, None))
        state = {"approval_pointer": ("chat/c1", "x", True), "hits": []}
        with patch("angelo_agent.chat_card_draw._view", side_effect=RuntimeError("no canvas")):
            approval_wait.draw_pointer(state, 600, 500)
        self.assertEqual(state["hits"], [])

    def test_a_double_click_on_the_pill_never_answers_the_card_it_reveals(self):
        # Break-it P1: in a short transcript the revealed card's Approve or Always allow can lie under the pill. For
        # every height, the pill's spot takes no click for SHIELD_SECONDS after its click, over any hit drawn there.
        spot = (100, 360, 200, 35)
        for under in ("approve", "script_always_allow", "decline"):
            with self.subTest(button=under):
                state = {"hits": [(spot, "approval_reveal", "chat/c1")]}
                approval_wait.reveal(state, self.controller)
                self.assertIsNone(state["approval_shield"][1], "timed from the draw that shows the revealed card")
                state["hits"] = [((90, 350, 120, 40), under, "chat/c1")]   # The revealed card, drawn under the pill.
                approval_wait.draw_pointer(state, 400, 500)
                pad = approval_wait.PAD
                self.assertEqual(state["hits"][0][:2], ((spot[0] - pad, spot[1] - pad, spot[2] + 2 * pad,
                                                         spot[3] + 2 * pad), canvas_controls.INERT), "shield first")
                self.assertGreater(pad, 3, "past Blender's default drag threshold: a hand drifting between presses")
                # Inert: never hovered, focused or drawn as a button over the card it revealed (break-it round 3).
                self.assertEqual(canvas_controls.focus_order(state["hits"]), [(under, "chat/c1")])
                window = SimpleNamespace(cursor_set=Mock())
                canvas_controls.pointer(window, Mock(), state, 110, 380, state["hits"])
                self.assertIsNone(state.get("hover"))
                window.cursor_set.assert_not_called()
        # A stall of any length between the click and the draw that reveals the card never shortens the shield.
        stalled = {"hits": [(spot, "approval_reveal", "chat/c1")]}
        approval_wait.reveal(stalled, self.controller)
        with patch("time.monotonic", return_value=5000.0):
            stalled["hits"] = []
            approval_wait.draw_pointer(stalled, 400, 500)
        self.assertEqual(stalled["hits"][0][1:], (canvas_controls.INERT, 5000.0 + approval_wait.SHIELD_SECONDS))
        clipped = {"approval_shield": ((88, 348, 224, 59), 10 ** 12), "hits": []}
        approval_wait.draw_pointer(clipped, 400, 380)   # Never past the transcript's top edge (the usage bar's).
        self.assertEqual(clipped["hits"][0][0], (88, 348, 224, 32))
        # Nor past its bottom (the changes pill and the composer under a short transcript), nor outside it at all.
        for viewport, expected in (((0, 360, 400, 20), (88, 360, 224, 20)), ((0, 340, 400, 40), (88, 348, 224, 32)),
                                   ((0, 100, 400, 200), None)):
            with self.subTest(viewport=viewport):
                clipped = {"approval_shield": ((88, 348, 224, 59), 10 ** 12), "hits": [], "message_viewport": viewport}
                approval_wait.draw_pointer(clipped, 400, viewport[1] + viewport[3])
                self.assertEqual(clipped["hits"][0][0] if clipped["hits"] else None, expected)
        with patch("time.monotonic", return_value=10 ** 9):   # Past the double-click time: the card's buttons work.
            state["hits"] = [((90, 350, 120, 40), "approve", "chat/c1")]
            approval_wait.draw_pointer(state, 400, 500)
        self.assertEqual([hit[1] for hit in state["hits"]], ["approve"])
        self.assertNotIn("approval_shield", state)
        self.assertIn("action in {DISABLED, INERT, INFO}", source("canvas_controls.py"), "draw skips it")
        self.assertGreaterEqual(approval_wait.SHIELD_SECONDS, 1.0, "Blender's longest double-click time")
        # A lapsed shield still drawn (no redraw since) is no hit at all: the press, the hover and the hand all reach
        # the card's button under it, never one without the others (break-it round 4).
        lapsed = [((88, 348, 224, 59), canvas_controls.INERT, 5.0), ((90, 350, 120, 40), "approve", "chat/c1")]
        with patch("time.monotonic", return_value=4.0):
            self.assertIs(canvas_controls.hit_at(lapsed, 110, 380), lapsed[0], "live: it swallows the press")
        with patch("time.monotonic", return_value=6.0):
            self.assertFalse(canvas_controls.live(lapsed[0]))
            self.assertIs(canvas_controls.hit_at(lapsed, 110, 380), lapsed[1])
            hovered, window = {}, SimpleNamespace(cursor_set=Mock())
            canvas_controls.pointer(window, Mock(), hovered, 110, 380, lapsed)
            self.assertEqual(hovered["hover"], ("approve", "chat/c1"))
            window.cursor_set.assert_called_once_with('HAND_POINT')
        self.assertIn("canvas_controls.live((rect, action, value))", source("chat_view.py"), "the press reads live()")
        # A live shield's press and release dispatch nothing: no branch of dispatch_action takes its action.
        dispatch = source("chat_view.py").split("\ndef dispatch_action(", 1)[1].split("\ndef dispatch_popup(", 1)[0]
        handled = re.findall(r'action == "([^"]+)"', dispatch) + [name for group in re.findall(
            r'action(?:\.startswith\(\(?| in \{)([^)}]*)', dispatch) for name in re.findall(r'"([^"]+)"', group)]
        self.assertIn("approve", handled)
        self.assertEqual([name for name in handled if canvas_controls.INERT.startswith(name)], [])

    def test_only_a_call_that_waits_for_the_person_is_waiting(self):
        for flag in ("authorized", "executing", "approved", "automatic", "revoked"):
            with self.subTest(flag=flag):
                setattr(self.call, flag, True)
                self.assertIsNone(approval_wait.awaiting(self.controller), "it no longer waits for the person")
                self.assertEqual(approval_wait.place({}, self.controller, self.blocks, 500, 400), 500)
                setattr(self.call, flag, False)
        self.assertIs(approval_wait.awaiting(self.controller), self.call)
        self.call.needs_person = False   # A mirror of a worker chat: its worker runs it without the person.
        self.assertIsNone(approval_wait.awaiting(self.controller))
        self.call.needs_person = True
        self.assertIs(approval_wait.awaiting(self.controller), self.call)
        self.assertIn("approval_wait.awaiting(controller)", function("cards", "chat_blocks.py"))
        # The apps' own waiting signals read the same predicate (break-it round 3).
        self.assertIn("unseen(controllers, active_id, chat_shown)", function("waiting", "external_apps.py"))
        self.assertIn("awaiting(controller) is not None", function("unseen", "approval_wait.py"))
        self.assertIn("waiting = awaiting(controller) is not None", source("external_apps.py"))
        self.assertIn('"approval" if awaiting_person(call) else "queued"', source("external_host.py"))

    def test_a_connected_apps_call_state_and_waiting_signal_follow_the_card(self):
        from angelo_agent import external_apps
        from angelo_agent.external_host import ExternalHost
        call = SimpleNamespace(id="s/external/c1", approval=True, executing=False, name="angelo_write_file")
        app = SimpleNamespace(state={"id": "s", "results": {}}, pending={call.id: call}, external_client="Claude")
        self.assertEqual(ExternalHost.call_status(app, "c1")["state"], "approval")
        self.assertEqual(external_apps.waiting([app], "other"), [("Claude", "s")])
        self.assertEqual(external_apps.waiting([app], "s"), [], "the person is looking at it")
        for flag in ("authorized", "approved", "automatic", "revoked", "consent_approved"):
            with self.subTest(flag=flag):   # Answered or never the person's: no card, so nothing says it waits.
                setattr(call, flag, True)
                self.assertEqual(ExternalHost.call_status(app, "c1")["state"], "queued")
                self.assertEqual(external_apps.waiting([app], "other"), [])
                setattr(call, flag, False)
        call.executing = True
        self.assertEqual(ExternalHost.call_status(app, "c1")["state"], "executing")
        self.assertEqual(external_apps.waiting([app], "other"), [])

    def test_only_the_persons_tile_click_scrolls_to_the_card(self):
        self.assertIn("if reveal:", function("switch", "session_rail_input.py"))
        self.assertIn("switch(found[1], window, reveal=True)", source("session_rail_input.py"))
        self.assertIn("switch(parallel_sessions.lead, window)\n", source("session_rail_input.py"))

    def test_the_rail_tile_names_the_card(self):
        self.assertEqual(session_rail.status_parts(session_rail.APPROVAL, "Waiting for your approval: Write x"),
                         ("Waiting for your approval: Write x", ""))
        self.assertEqual(session_rail.status_parts(session_rail.APPROVAL), ("Waiting for your approval", ""))


class CardTitles(unittest.TestCase):
    def test_a_changed_card_names_its_own_title(self):
        # A worker's changed card is a new call object under the same id; a cache keyed by id() could hand a reused
        # address the old card's title (break-it review): the title lives on the call itself.
        with patch.object(approval_wait, "_title", side_effect=lambda call: "Write " + call.arguments["path"]):
            first = SimpleNamespace(id="chat/c1", name="angelo_write_file", arguments={"path": "a.txt"})
            changed = SimpleNamespace(id="chat/c1", name="angelo_write_file", arguments={"path": "b.txt"})
            self.assertEqual(approval_wait.title(first), "Write a.txt")
            self.assertEqual(approval_wait.title(changed), "Write b.txt")
            self.assertEqual(approval_wait.title(first), "Write a.txt")
            with patch("angelo_agent.ui_text.language", return_value="ja"):
                approval_wait.title(first)
            self.assertEqual(approval_wait._title.call_count, 3, "once per call and language")


class CallStatusEndsWithItsCall(unittest.TestCase):
    """The same class for the host's progress statuses: "Waiting for a render" never outlives the render."""

    def chat(self, status, *pending, busy=True):
        return SimpleNamespace(status=status, busy=busy, pending={str(i): call for i, call in enumerate(pending)})

    def test_a_finished_calls_status_reads_as_thinking(self):
        call = SimpleNamespace(shown_status="Waiting for a render")
        chat = self.chat("Waiting for a render")
        call_status.settle(chat, call)
        self.assertEqual(chat.status, STATUS_THINKING)

    def test_it_stays_while_another_call_shows_it_or_after_something_else_set_the_status(self):
        same = SimpleNamespace(shown_status="Generating media")
        for chat, call in ((self.chat("Generating media", same), SimpleNamespace(shown_status="Generating media")),
                           (self.chat("Preparing the next steps"), SimpleNamespace(shown_status="Generating media")),
                           (self.chat("Generating media", busy=False),
                            SimpleNamespace(shown_status="Generating media")),
                           (self.chat("Generating media"), SimpleNamespace(shown_status=""))):
            before = chat.status
            call_status.settle(chat, call)
            self.assertEqual(chat.status, before)

    def test_another_calls_work_takes_the_row_back(self):
        media = SimpleNamespace(shown_status="Generating media")
        render = SimpleNamespace(shown_status="Waiting for a render")
        chat = self.chat("Waiting for a render", media)
        call_status.settle(chat, render)
        self.assertEqual(chat.status, "Generating media", "the media job still runs")

    def test_the_controller_records_each_calls_status_and_settles_it_when_the_call_finishes(self):
        self.assertIn("call_status.settle(self, call)", function("finish_tool", "controller.py", "Controller"))
        text = source("controller.py")
        for status in ('"Staging native program"', '"Waiting for animation playback to stop"'):
            self.assertIn("call.shown_status = self.status = " + status, text)
        self.assertIn('call.shown_status = self.status = getattr(result, "status_text", "")', text)


if __name__ == "__main__":
    unittest.main()
