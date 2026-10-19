# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's Always allow scripts: off by default, Auto chats only, revocable, and below policy.

Real ``Controller`` approval routes with Blender stand-ins; no provider or
profile. Contract: doc/angelo/agent_execution.rst, "Always allow scripts".
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from agent_runtime_fixtures import AgentRuntimeFixture, HANDLE, program
from angelo_agent import always_allow, display_text, managed_policy, ui_text
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.managed_policy import Policy, with_organization
from angelo_agent.team_view import org_policy
from test_native_refusal_recovery import B, TOOL, ran, snapshot

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
# AllowAutoApproval off from the organization: no script runs unreviewed; the chat stays in Auto.
SCRIPTS_REVIEWED = with_organization(Policy(), org_policy({"allowAutoApproval": False}))
SCRIPT = {"description": "Set a scene value", "code": "scene.value = 42\nresult = scene.value"}


def ui_function(name, namespace):
    """A module-level function of trusted_scripts_ui (which imports bpy) run with stand-ins."""
    path = ADDON / "trusted_scripts_ui.py"
    definition = next(node for node in ast.parse(path.read_text()).body
                      if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = {"__package__": "angelo_agent", "__name__": "angelo_agent.trusted_scripts_ui",
             "Text": display_text.Text, "shown": display_text.shown, "iface_": ui_text.iface_, **namespace}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), "exec"), scope)
    return scope[name]


def ui_constant(name):
    """A module-level ``n_("...")`` constant of trusted_scripts_ui, without importing it."""
    tree = ast.parse((ADDON / "trusted_scripts_ui.py").read_text())
    node = next(node for node in tree.body if isinstance(node, ast.Assign) and node.targets[0].id == name)
    return node.value.args[0].value


class AlwaysAllowTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        always_allow.detach()  # Off, as a new install has it.
        self.addCleanup(always_allow.detach)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.busy = True
        self.controller.set_document(("isolated",))
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executed = []

        def execute(name, arguments):
            self.executed.append(name)
            namespace = {"scene": SimpleNamespace(value=0), "result": None}
            exec(compile(arguments["code"], "<angelo-reviewed-script>", "exec"), namespace)
            return {"success": True, "result": namespace["result"]}
        self.executor = SimpleNamespace(precondition=Mock(return_value="current"), execute=execute)

    def enqueue(self, identity="script"):
        results = []
        self.controller.request_tool(identity, "angelo_execute_python", dict(SCRIPT), results.append)
        return self.controller.pending[self.controller.state["id"] + "/" + identity], results

    def pump(self, apply=None):
        self.controller.pump_tools(self.executor, self.controller.document,
            apply_mutation=apply or (lambda call: self.controller.execute_tool(call, self.executor, self.controller.document)),
            budget=ToolDispatchBudget(seconds=60))

    def assert_asks(self, call, results):
        self.assertTrue(call.approval)
        self.assertFalse(call.automatic)
        self.assertFalse(self.executed)
        self.assertFalse(results)

    def test_off_by_default_so_scripts_ask_in_auto(self):
        self.assertEqual(self.controller.state["options"]["approval"], "AUTO_BOUNDED")
        self.assertFalse(always_allow.enabled())
        call, results = self.enqueue()
        self.pump()
        self.assert_asks(call, results)
        self.assertTrue(self.controller.offers_always_allow(call), "the card offers it in Auto")

    def test_opted_in_scripts_run_unasked_in_auto_and_record_the_consent(self):
        always_allow.set_enabled(True)
        call, results = self.enqueue()
        self.assertIs(call.script_consent, always_allow.token())
        self.pump()
        self.assertEqual(self.executed, ["angelo_execute_python"])
        self.assertEqual(results[0]["result"], 42)
        self.assertEqual(results[0]["consent"], "always_allow")
        self.assertEqual(results[0]["execution_mode"], "trusted_unconfined")
        self.assertFalse(results[0]["confined"])
        self.assertFalse(results[0]["external_effects_undoable"])

    def test_ask_still_asks_and_read_only_still_refuses(self):
        always_allow.set_enabled(True)
        self.controller.set_options(approval="ASK")
        call, results = self.enqueue("ask")
        self.pump()
        self.assert_asks(call, results)
        self.assertFalse(self.controller.offers_always_allow(call), "Ask means ask: no Always allow on its card")
        self.controller.set_options(approval="READ_ONLY")  # Also cancels the queued script.
        self.assertEqual(results[0]["error_code"], "READ_ONLY")
        call, results = self.enqueue("read-only")
        self.pump()
        self.assertEqual(results[0]["error_code"], "READ_ONLY")
        self.assertFalse(self.executed)

    def test_per_chat_trust_keeps_its_own_consent(self):
        always_allow.set_enabled(True)
        self.controller.set_trusted_scripts(True)
        call, results = self.enqueue()
        self.pump()
        self.assertEqual(results[0]["consent"], "chat_session")

    def test_revoking_reaches_the_next_script_queued_ones_included(self):
        always_allow.set_enabled(True)
        queued, queued_results = self.enqueue("queued")
        always_allow.set_enabled(False)
        self.pump()
        self.assert_asks(queued, queued_results)
        later, later_results = self.enqueue("later")
        self.assertIsNone(later.script_consent)
        self.pump()
        self.assertFalse(later.automatic)
        self.assertFalse(self.executed)

    def test_revoking_after_admission_checks_never_runs_the_script(self):
        always_allow.set_enabled(True)
        call, results = self.enqueue()
        journal = self.controller._journal_tool

        def revoke(call, phase):
            journal(call, phase)
            if phase == "execution_started":
                always_allow.set_enabled(False)
        self.controller._journal_tool = revoke
        self.pump()
        self.assertFalse(self.executed)
        self.assertIn("consent changed", results[0]["error"])

    def test_turning_it_on_again_never_revives_an_old_consent(self):
        always_allow.set_enabled(True)
        call, results = self.enqueue()
        always_allow.set_enabled(False)
        always_allow.set_enabled(True)
        self.pump()
        self.assert_asks(call, results)

    def test_policy_wins_over_an_opt_in_made_before_it(self):
        always_allow.set_enabled(True)
        call, results = self.enqueue("before")
        with patch("angelo_agent.managed_policy.current", return_value=SCRIPTS_REVIEWED):
            self.assertEqual(self.controller.state["options"]["approval"], "AUTO_BOUNDED", "edits stay in Auto")
            self.assertIsNone(always_allow.token())
            self.assertFalse(self.controller.always_allow_scripts)
            self.assertFalse(self.controller.offers_always_allow(call))
            self.pump()
            self.assert_asks(call, results)
            during, during_results = self.enqueue("during")
            self.assertIsNone(during.script_consent)
        always_allow.sync()  # The policy lifted: a new consent, never the one from before the policy.
        self.pump()
        self.assert_asks(call, results)
        after, after_results = self.enqueue("after")
        self.pump()
        self.assertEqual(after_results[0]["consent"], "always_allow")
        self.assertEqual(self.executed, ["angelo_execute_python"])

    def test_a_script_changed_after_admission_is_refused(self):
        always_allow.set_enabled(True)
        call, results = self.enqueue()
        call.arguments["code"] = "result = 'changed'"
        self.pump()
        self.assertFalse(self.executed)
        self.assertIn("review the exact script", results[0]["error"])

    def test_the_card_button_covers_this_script_through_the_same_checks(self):
        call, results = self.enqueue()
        self.pump()
        self.assert_asks(call, results)
        self.assertFalse(self.controller.always_allow_script(call), "nothing is allowed before the person opts in")
        always_allow.set_enabled(True)  # What the button's preference write does.
        self.assertTrue(self.controller.always_allow_script(call))
        self.pump()
        self.assertEqual(results[0]["consent"], "always_allow")
        self.assertEqual(self.executed, ["angelo_execute_python"])

    def test_the_card_never_offers_it_for_a_recovery_review(self):
        call, results = self.enqueue()
        self.pump()
        call.recovery_review = True
        always_allow.set_enabled(True)
        self.assertFalse(self.controller.offers_always_allow(call))
        self.assertFalse(self.controller.always_allow_script(call))

    def card_handler(self):
        return ui_function("always_allow_from_card", {"agent_script_running": lambda: False})

    def test_the_card_handler_saves_the_choice_and_covers_this_script(self):
        call, results = self.enqueue()
        self.pump()
        with tempfile.TemporaryDirectory() as state:
            always_allow.attach(state)
            with patch.dict(sys.modules, {"angelo_agent.ui": SimpleNamespace(_controllers={})}):
                self.assertTrue(self.card_handler()(None, self.controller, call.id))
            self.assertEqual(json.loads((Path(state) / always_allow.FILE).read_text()), {"version": 1, "enabled": True})
        self.pump()
        self.assertEqual(results[0]["consent"], "always_allow")

    def test_a_repeated_click_keeps_scripts_already_allowed(self):
        """Review note 6: the button never re-mints the consent, so a script admitted under it still runs."""
        always_allow.set_enabled(True)
        first = always_allow.token()
        self.controller.set_options(approval="ASK")
        asking, asking_results = self.enqueue("admitted-in-ask")
        self.pump()
        self.controller.set_options(approval="AUTO_BOUNDED")
        allowed, allowed_results = self.enqueue("already-allowed")
        self.assertIs(allowed.script_consent, first)
        with patch.dict(sys.modules, {"angelo_agent.ui": SimpleNamespace(_controllers={})}):
            self.assertTrue(self.card_handler()(None, self.controller, asking.id))
        self.assertIs(always_allow.token(), first)
        for _ in range(3):   # One script per tick.
            self.pump()
        self.assertEqual((asking_results[0]["consent"], allowed_results[0]["consent"]), ("always_allow", "always_allow"))

    def test_a_revocation_is_saved_at_once_and_survives_a_relaunch(self):
        """Review should-fix 2: on and off are both written before they take effect; a crash can't revive a revoked
        choice, and a missing or unreadable file is off."""
        with tempfile.TemporaryDirectory() as state:
            always_allow.attach(state)
            self.assertFalse(always_allow.enabled(), "a new install: no file, off")
            always_allow.set_enabled(True)
            always_allow.attach(state)   # A relaunch after a crash: nothing else was written.
            self.assertTrue(always_allow.enabled())
            always_allow.set_enabled(False)
            always_allow.attach(state)
            self.assertFalse(always_allow.enabled(), "the revocation survived")
            call, results = self.enqueue("after-relaunch")
            self.pump()
            self.assert_asks(call, results)
            for broken in ("", "{", '{"version": 1, "enabled": "yes"}', '{"enabled": true}'):
                with self.subTest(broken=broken):
                    (Path(state) / always_allow.FILE).write_text(broken)
                    always_allow.attach(state)
                    self.assertFalse(always_allow.enabled())

    def test_a_revocation_that_cant_be_saved_still_holds_for_the_session(self):
        always_allow.set_enabled(True)
        with tempfile.TemporaryDirectory() as state:
            always_allow.attach(state)
            always_allow.set_enabled(True)
            with patch("angelo_agent.always_allow._write", side_effect=OSError("read-only")):
                with self.assertRaises(OSError):
                    always_allow.set_enabled(False)
                self.assertFalse(always_allow.enabled())
                self.assertIsNone(always_allow.token())
                with self.assertRaises(OSError):
                    always_allow.set_enabled(True)
                self.assertFalse(always_allow.enabled(), "an opt-in that can't be saved never takes effect")

    def test_a_trusted_script_cannot_grant_itself_always_allow(self):
        """Review should-fix 1a: a script that refers to preferences or to Angelo's own add-on never runs under a
        standing consent; it asks (the card warns about preferences) and the card offers no Always allow."""
        from angelo_agent.approvals import approval_message
        self.controller.set_trusted_scripts(True)
        always_allow.set_enabled(True)
        for identity, code in (
                ("grant", "bpy.context.preferences.addons['angelo_agent'].preferences.always_allow_scripts = True"),
                ("module", "import angelo_agent.always_allow as a\na.set_enabled(True)")):
            with self.subTest(identity=identity):
                results = []
                self.controller.request_tool(identity, "angelo_execute_python",
                                             {"description": "Tidy the add-on settings", "code": code}, results.append)
                call = self.controller.pending[self.controller.state["id"] + "/" + identity]
                self.pump()
                self.assert_asks(call, results)
                self.assertIsNone(self.controller.script_consent_kind(call))
                self.assertFalse(self.controller.offers_always_allow(call))
        message = approval_message("angelo_execute_python", {"description": "x", "code": "bpy.context.preferences"})
        self.assertIn("preferences", message["warning"])

    def test_only_the_person_changes_the_preference(self):
        """Review should-fix 1b: the property's setter accepts the Preferences editor while no script runs; a
        script, an add-on or a keymap entry changes nothing and the chat says so."""
        setter = ui_function("always_allow_set", {"agent_script_running": lambda: running[0],
                                                  "NOT_YOURS": ui_constant("NOT_YOURS")})
        running, emitted = [False], []
        controller = SimpleNamespace(emit=lambda kind, payload: emitted.append(payload))
        fake_ui = SimpleNamespace(_controller=controller)
        for space, script, expected in (("VIEW_3D", False, False), ("PREFERENCES", True, False),
                                        (None, False, False), ("PREFERENCES", False, True)):
            with self.subTest(space=space, script=script):
                running[0], emitted[:] = script, []
                bpy = SimpleNamespace(context=SimpleNamespace(space_data=SimpleNamespace(type=space) if space else None))
                with patch.dict(sys.modules, {"bpy": bpy, "angelo_agent.ui": fake_ui}):
                    setter(None, True)
                self.assertIs(always_allow.enabled(), expected)
                self.assertEqual(bool(emitted), not expected, "an ignored change says so in the chat")
                if emitted:
                    self.assertIn("only you can change it", emitted[0]["text"])
        getter = ui_function("always_allow_get", {})
        self.assertIs(getter(None), True, "the property shows the saved choice")

    def test_preferences_show_the_toggle_or_the_organizations_lock(self):
        rows = []
        layout = SimpleNamespace(prop=lambda owner, name: rows.append(("prop", name)), row=lambda: layout,
                                 label=lambda **kwargs: rows.append(("label", kwargs["text"])))
        draw = ui_function("draw_always_allow", {})
        draw(layout, object())
        self.assertEqual(rows, [("prop", "always_allow_scripts")])
        rows.clear()
        with patch("angelo_agent.managed_policy.current", return_value=SCRIPTS_REVIEWED):
            draw(layout, object())
        self.assertEqual(rows, [("label", "Always allow scripts"), ("label", "Turned off by your organization.")])


class AlwaysAllowRecoveryTests(AgentRuntimeFixture, unittest.TestCase):
    """A recovery hold refuses a script whatever the person allowed; once its targets are read, Always allow applies."""

    def setUp(self):
        super().setUp()
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        always_allow.set_enabled(True)
        self.addCleanup(always_allow.detach)

    def hold(self):
        self.c.recovery_barrier.observe(snapshot())
        self.c.recovery_barrier.refused(TOOL, program(), ran())

    def test_a_script_while_a_hold_is_open_is_refused_never_run(self):
        self.hold()
        answers = []
        self.c.request_tool("script", "angelo_execute_python", dict(SCRIPT), answers.append)
        self.pump()
        self.assertEqual(answers[0]["error_code"], "RECOVERY_REQUIRED")
        self.assertNotIn("angelo_execute_python", [name for _, name in self.executor.direct])
        self.assertFalse(self.c.pending)

    def test_after_the_targets_are_read_a_script_runs_under_always_allow(self):
        self.hold()
        self.c.recovery_barrier.read("angelo_scene_object", snapshot(B, location=563.125), {"object": HANDLE})
        refused = []
        self.c.request_tool("first", "angelo_execute_python", dict(SCRIPT), refused.append)
        self.assertEqual(refused[0]["error_code"], "RECOVERY_REQUIRED")  # The step that read, carrying what changed.
        self.c.recovery_barrier.step()  # The model's next response.
        answers = []
        self.c.request_tool("script", "angelo_execute_python", dict(SCRIPT), answers.append)
        for _ in range(10):
            self.pump()
            if answers:
                break
        self.assertIn("angelo_execute_python", [name for _, name in self.executor.direct])


if __name__ == "__main__":
    unittest.main()
