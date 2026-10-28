# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A new chat's approval: Auto · bounded edits by default, a saved choice kept, Ask where policy requires it.

Real ``Controller``, ``SessionStore`` and ``SessionCollection`` in a temporary
profile with Blender stand-ins; no provider. Contract: doc/angelo/agent_chat.rst,
"Approvals", and doc/angelo/enterprise_policy.rst, ``RequireAskForEdits`` and ``AllowAutoApproval``.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import always_allow, managed_policy
from angelo_agent.chat_settings import APPROVAL_VERSION, APPROVALS, DEFAULT_APPROVAL, clean_options, stored_options
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.external_host import ExternalHost
from angelo_agent.managed_policy import Policy, combine, with_organization
from angelo_agent.sessions import read_transcript, SessionCollection, SessionStore
from angelo_agent.team_view import org_policy
from test_native_dispatch_fixes import program, ui_class
from test_native_external_cli_host import Environment

MACHINE_ASK = combine([("test", {"RequireAskForEdits": True})])
ORGANIZATION_ASK = with_organization(Policy(), org_policy({"requireAskForEdits": True}))
SCRIPTS_REVIEWED = combine([("test", {"AllowAutoApproval": False})])
BOTH = combine([("test", {"RequireAskForEdits": True, "AllowAutoApproval": False})])
SCRIPT = {"description": "Set a scene value", "code": "scene.value = 42\nresult = scene.value"}


class NewChatDefaultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)

    def test_the_default_is_auto_bounded_edits(self):
        self.assertEqual(DEFAULT_APPROVAL, "AUTO_BOUNDED")
        for value in (None, {}, {"approval": None}, {"model": "m", "effort": "high"}):
            with self.subTest(value=value):
                self.assertEqual(clean_options(value)["approval"], "AUTO_BOUNDED")
        # Review should-fix 3: only a missing mode takes the default; any other unknown value reads as Ask.
        for value in ("ask", "READ_ONLY ", "ASK_EVERYTHING", "READ-ONLY", "", 0, True, [], {"x": 1}):
            with self.subTest(unknown=value):
                self.assertEqual(clean_options({"approval": value})["approval"], "ASK")
                self.assertEqual(stored_options({"approval": value})["approval"], "ASK")

    def test_a_marked_chat_with_an_unknown_mode_opens_in_ask(self):
        store = SessionStore(self.directory.name, "6f1c2b9e-0000-4000-8000-0000000000a1")
        store.save(store.new())
        raw = read_transcript(store.path)
        raw["options"]["approval"] = "READ-ONLY"
        store.path.write_text(json.dumps(raw))
        self.assertEqual(store.load()["options"]["approval"], "ASK")

    def test_the_approval_operator_refuses_an_unknown_mode(self):
        controller = Controller(self.directory.name)
        self.addCleanup(controller.close)
        controller.set_options(approval="READ_ONLY")
        operator = ui_class("ANGELO_OT_set_approval", {"__package__": "angelo_agent", "__name__": "angelo_agent.ui",
                                                      "get_controller": lambda: controller, "APPROVALS": APPROVALS})()
        for mode in ("ASK ", "", "AUTO", "auto_bounded"):
            with self.subTest(mode=mode):
                operator.report, operator.mode = Mock(), mode
                self.assertEqual(operator.execute(None), {'CANCELLED'})
                self.assertEqual(controller.state["options"]["approval"], "READ_ONLY")

    def test_every_new_chat_starts_in_auto(self):
        store = SessionStore(self.directory.name)
        self.assertEqual(store.new()["options"]["approval"], "AUTO_BOUNDED")
        collection = SessionCollection(self.directory.name)
        created = collection.create("codex_subscription")
        self.assertEqual(SessionStore(self.directory.name, created).load()["options"]["approval"], "AUTO_BOUNDED")
        controller = Controller(self.directory.name)
        try:
            self.assertEqual(clean_options(controller.state.get("options"))["approval"], "AUTO_BOUNDED")
            controller.set_options(approval="ASK")
            controller.new_chat(("document",))
            self.assertEqual(controller.state["options"]["approval"], "AUTO_BOUNDED", "a new chat never inherits Ask")
        finally:
            controller.close()

    def test_a_saved_chat_without_a_choice_opens_in_auto(self):
        store = SessionStore(self.directory.name, "6f1c2b9e-0000-4000-8000-000000000001")
        state = store.new()
        store.save(state)
        raw = read_transcript(store.path)
        for options in ({}, {"model": "m"}, None):
            with self.subTest(options=options):
                if options is None:
                    raw.pop("options", None)
                else:
                    raw["options"] = options
                store.path.write_text(json.dumps(raw))
                self.assertEqual(store.load()["options"]["approval"], "AUTO_BOUNDED")

    def test_a_saved_choice_is_kept(self):
        for mode in ("ASK", "READ_ONLY", "AUTO_BOUNDED"):
            with self.subTest(mode=mode):
                # A new store each time: the Controller below wrote the file, so a store loaded before would refuse.
                store = SessionStore(self.directory.name, "6f1c2b9e-0000-4000-8000-000000000002")
                state = store.new()
                state["options"] = {"approval": mode}
                store.save(state)
                self.assertEqual(store.load()["options"]["approval"], mode)
                controller = Controller(self.directory.name, session_id=state["id"])
                try:
                    self.assertEqual(clean_options(controller.state.get("options"))["approval"], mode)
                finally:
                    controller.close()

    def test_a_view_without_a_chat_keeps_the_strict_mode(self):
        from angelo_agent import view_policy
        self.assertEqual(view_policy.mode_of(None), "ASK", "a flow run's or library save's capture")
        self.assertEqual(view_policy.mode_of(SimpleNamespace(state={"id": "chat"})), "ASK")
        self.assertEqual(view_policy.mode_of(SimpleNamespace(state={"options": {}})), "AUTO_BOUNDED", "a chat's default")
        self.assertEqual(view_policy.mode_of(SimpleNamespace(state={"options": {"approval": "ASK"}})), "ASK")

    def test_external_conversations_start_like_a_new_chat(self):
        """An app's conversation starts in Approve for me, as a new chat; policy can still force Ask."""
        root = Path(tempfile.mkdtemp(prefix="sapp-"))
        self.addCleanup(shutil.rmtree, root, True)
        state = str(root / "state")
        host = ExternalHost(Environment(state), environ={}, runtime_directory=root / "run")
        from angelo_agent.external_clients import AppRegistry
        AppRegistry(state).connect("codex")
        host.reload_apps(force=True)
        identity = host.create_conversation("codex")
        self.assertEqual(SessionStore(state, identity).load()["options"]["approval"], "AUTO_BOUNDED")
        cli = host.create_conversation("agent")   # A CLI label (no app key) stays in Ask: external_cli.rst.
        self.assertEqual(SessionStore(state, cli).load()["options"]["approval"], "ASK")
        managed_policy.reset(MACHINE_ASK)
        self.assertEqual(clean_options(SessionStore(state, identity).load()["options"])["approval"], "ASK")


class SavedChatMigrationTests(unittest.TestCase):
    """A chat saved before Auto became the default moves from its old default Ask to Auto, once."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.store = SessionStore(self.directory.name, "6f1c2b9e-0000-4000-8000-000000000003")

    def write_old_chat(self, approval):
        """A chat as the previous build saved it: every option written, no approval_version."""
        state = self.store.new()
        self.store.save(state)
        raw = read_transcript(self.store.path)
        raw.pop("approval_version")
        raw["options"] = {"model": "", "effort": "low", "fast": False, "approval": approval}
        self.store.path.write_text(json.dumps(raw))

    def test_an_old_default_ask_moves_to_auto_and_the_chat_is_marked(self):
        self.write_old_chat("ASK")
        state = self.store.load()
        self.assertEqual((state["options"]["approval"], state["approval_version"]), ("AUTO_BOUNDED", APPROVAL_VERSION))
        self.store.save(state)
        self.assertEqual(read_transcript(self.store.path)["approval_version"], APPROVAL_VERSION)

    def test_an_ask_chosen_after_the_migration_survives_reloads(self):
        self.write_old_chat("ASK")
        controller = Controller(self.directory.name, session_id=self.store.session_id)
        try:
            self.assertEqual(controller.state["options"]["approval"], "AUTO_BOUNDED")
            controller.set_options(approval="ASK")  # Saves.
        finally:
            controller.close()
        for _ in range(2):
            self.assertEqual(self.store.load()["options"]["approval"], "ASK")

    def test_a_later_marker_counts_as_moved(self):
        """Review note 7: a later build's marker (3) is >= 2, so its deliberate Ask is kept, and kept marked."""
        state = self.store.new()
        state["options"], state["approval_version"] = stored_options({"approval": "ASK"}), APPROVAL_VERSION + 1
        self.store.save(state)
        loaded = self.store.load()
        self.assertEqual((loaded["options"]["approval"], loaded["approval_version"]), ("ASK", APPROVAL_VERSION + 1))
        self.store.save(loaded)
        self.assertEqual(read_transcript(self.store.path)["approval_version"], APPROVAL_VERSION + 1)
        for marker in ("2", 2.0, True):
            with self.subTest(marker=marker):
                self.write_old_chat("ASK")
                raw = read_transcript(self.store.path)
                raw["approval_version"] = marker   # Not an int: not a marker.
                self.store.path.write_text(json.dumps(raw))
                self.assertEqual(self.store.load()["options"]["approval"], "AUTO_BOUNDED")

    def test_an_external_cli_conversation_from_an_earlier_build_stays_in_ask(self):
        """Review note 7: an External conversation saved in Ask keeps it (never migrated); only new ones start in Auto."""
        state = self.store.new("codex_subscription")
        state["options"] = stored_options({"approval": "ASK"})
        state["messages"].append({"role": "user", "text": "External: agent", "request_kind": "external_session",
                                  "client": "agent"})
        self.store.save(state)
        raw = read_transcript(self.store.path)
        raw.pop("approval_version")
        self.store.path.write_text(json.dumps(raw))
        self.assertEqual(clean_options(self.store.load()["options"])["approval"], "ASK")

    def test_read_only_and_auto_are_left_alone(self):
        for approval in ("READ_ONLY", "AUTO_BOUNDED"):
            with self.subTest(approval=approval):
                self.write_old_chat(approval)
                state = self.store.load()
                self.assertEqual((state["options"]["approval"], state["approval_version"]), (approval, APPROVAL_VERSION))

    def test_under_a_policy_requiring_ask_the_moved_chat_reads_as_ask_until_it_lifts(self):
        self.write_old_chat("ASK")
        managed_policy.reset(MACHINE_ASK)
        state = self.store.load()
        self.assertEqual((state["options"]["approval"], state["approval_version"]), ("AUTO_BOUNDED", APPROVAL_VERSION))
        self.assertEqual(clean_options(state["options"])["approval"], "ASK", "the policy applies when it is read")
        self.store.save(state)
        managed_policy.reset(Policy())
        self.assertEqual(clean_options(self.store.load()["options"])["approval"], "AUTO_BOUNDED")

    def test_a_policy_forced_ask_is_never_saved(self):
        """Review note 8: an Auto chat touched under RequireAskForEdits keeps the person's Auto on disk, so it is
        back in Auto when the policy lifts."""
        controller = Controller(self.directory.name, session_id=self.store.session_id)
        controller.save()
        controller.close()
        managed_policy.reset(MACHINE_ASK)
        controller = Controller(self.directory.name, session_id=self.store.session_id)
        self.assertEqual(clean_options(controller.state["options"])["approval"], "ASK")
        controller.set_options(effort="high")   # Any save under the policy.
        controller.close()
        self.assertEqual(read_transcript(self.store.path)["options"]["approval"], "AUTO_BOUNDED")
        managed_policy.reset(Policy())   # An MDM profile removed, or an organization sign-out.
        self.assertEqual(clean_options(self.store.load()["options"])["approval"], "AUTO_BOUNDED")


class PolicyRequiresAskTests(unittest.TestCase):
    """``RequireAskForEdits`` (machine or organization) forces Ask; ``AllowAutoApproval`` governs scripts only."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.busy = True
        self.controller.set_document(("isolated",))
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executor = SimpleNamespace(precondition=Mock(return_value="unchanged"),
                                        execute=Mock(return_value={"success": True}))

    def pump(self):
        self.controller.pump_tools(self.executor, self.controller.document,
            apply_mutation=lambda call: self.controller.execute_tool(call, self.executor, self.controller.document),
            budget=ToolDispatchBudget(seconds=60))

    def enqueue(self, identity="sculpt"):
        replies = []
        self.controller.request_tool(identity, "angelo_sculpt_program", program(), replies.append)
        return replies, self.controller.pending.get(self.controller.state["id"] + "/" + identity)

    def test_the_default_runs_bounded_edits_without_asking(self):
        replies, call = self.enqueue()
        self.pump()
        self.assertEqual(self.executor.execute.call_count, 1)
        self.assertTrue(call.automatic)
        self.assertTrue(replies[0]["success"])

    def test_machine_and_organization_policy_turn_auto_into_ask(self):
        for name, policy in (("machine", MACHINE_ASK), ("organization", ORGANIZATION_ASK)):
            with self.subTest(policy=name):
                self.assertTrue(policy.require_ask_for_edits)
                self.assertTrue(policy.allow_auto_approval, "a separate switch")
                with patch("angelo_agent.managed_policy.current", return_value=policy):
                    self.assertEqual(clean_options({})["approval"], "ASK", "a new chat")
                    self.assertEqual(clean_options({"approval": "AUTO_BOUNDED"})["approval"], "ASK", "a saved Auto")
                    self.assertEqual(clean_options({"approval": "READ_ONLY"})["approval"], "READ_ONLY")
                    self.controller.set_options(approval="AUTO_BOUNDED")
                    self.assertEqual(self.controller.state["options"]["approval"], "AUTO_BOUNDED", "the person's")
                    self.assertEqual(clean_options(self.controller.state["options"])["approval"], "ASK", "read")

    def test_a_policy_set_through_the_signed_in_organization_reaches_a_new_chat(self):
        managed_policy.set_organization_source(lambda: org_policy({"requireAskForEdits": True}))
        self.assertEqual(clean_options(SessionStore(self.directory.name).new()["options"])["approval"], "ASK")
        managed_policy.set_organization_source(lambda: None)  # Signed out: the machine policy alone.
        self.assertEqual(clean_options(SessionStore(self.directory.name).new()["options"])["approval"], "AUTO_BOUNDED")

    def test_bounded_edits_ask_under_the_policy_even_in_a_chat_saved_in_auto(self):
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"
        with patch("angelo_agent.managed_policy.current", return_value=ORGANIZATION_ASK):
            replies, call = self.enqueue()
            self.pump()
            self.assertTrue(call.approval)
            self.assertFalse(call.automatic)
            self.assertFalse(self.controller.has_dispatchable_tools)
        self.executor.execute.assert_not_called()
        self.assertFalse(replies)

    def test_choosing_auto_under_the_policy_is_refused_with_its_reason(self):
        operator = ui_class("ANGELO_OT_set_approval", {"__package__": "angelo_agent", "__name__": "angelo_agent.ui",
                                                      "get_controller": lambda: self.controller, "APPROVALS": APPROVALS})()
        operator.report, operator.mode = Mock(), "AUTO_BOUNDED"
        self.controller.set_options(approval="ASK")
        with patch("angelo_agent.managed_policy.current", return_value=MACHINE_ASK):
            self.assertEqual(operator.execute(None), {'CANCELLED'})
        self.assertIn("organization", operator.report.call_args.args[1])
        self.assertEqual(self.controller.state["options"]["approval"], "ASK")
        self.assertEqual(operator.execute(None), {'FINISHED'})
        self.assertEqual(self.controller.state["options"]["approval"], "AUTO_BOUNDED")

    def test_allow_auto_approval_off_alone_keeps_auto_edits_and_reviews_every_script(self):
        with patch("angelo_agent.managed_policy.current", return_value=SCRIPTS_REVIEWED):
            self.assertEqual(clean_options({})["approval"], "AUTO_BOUNDED")
            replies, call = self.enqueue()
            self.pump()
            self.assertTrue(call.automatic, "bounded edits still run in Auto")
            with self.assertRaises(RuntimeError):
                self.controller.set_trusted_scripts(True)
            always_allow.set_enabled(True)
            self.addCleanup(always_allow.detach)
            self.assertIsNone(always_allow.token())
            script = []
            self.controller.request_tool("script", "angelo_execute_python", dict(SCRIPT), script.append)
            self.pump()
            pending = self.controller.pending[self.controller.state["id"] + "/script"]
            self.assertTrue(pending.approval and not pending.automatic)
            self.assertFalse(self.controller.offers_always_allow(pending), "the card offers nothing the policy blocks")

    def test_require_ask_alone_leaves_script_trust_to_the_person(self):
        with patch("angelo_agent.managed_policy.current", return_value=MACHINE_ASK):
            self.assertEqual(clean_options({})["approval"], "ASK")
            self.controller.set_trusted_scripts(True)  # Allowed: only edits must ask.
            self.assertTrue(self.controller.trusted_scripts)
            always_allow.set_enabled(True)
            self.addCleanup(always_allow.detach)
            self.assertFalse(self.controller.always_allow_scripts, "Always allow scripts covers Auto chats only")

    def test_both_switches_together(self):
        with patch("angelo_agent.managed_policy.current", return_value=BOTH):
            self.assertEqual(clean_options({"approval": "AUTO_BOUNDED"})["approval"], "ASK")
            with self.assertRaises(RuntimeError):
                self.controller.set_trusted_scripts(True)
            replies, call = self.enqueue()
            self.pump()
            self.assertTrue(call.approval and not call.automatic)

    def test_the_switch_is_read_restrictively_from_both_sources(self):
        unusable = combine([("profile", {"RequireAskForEdits": "maybe"})])
        self.assertTrue(unusable.require_ask_for_edits)
        self.assertEqual(unusable.problems, ("RequireAskForEdits",))
        self.assertFalse(Policy().require_ask_for_edits and combine([]).require_ask_for_edits)
        self.assertNotIn("requireAskForEdits", org_policy({}), "an older console leaves the machine policy alone")
        self.assertNotIn("requireAskForEdits", org_policy({"requireAskForEdits": None}))
        for sent, required in ((True, True), (False, False), ("yes", True), (0, True)):
            with self.subTest(sent=sent):
                self.assertIs(org_policy({"requireAskForEdits": sent})["requireAskForEdits"], required)
        machine = combine([("profile", {"RequireAskForEdits": True})])
        self.assertTrue(with_organization(machine, {"requireAskForEdits": False}).require_ask_for_edits,
                        "the organization can't lift what the machine requires")
        joined = with_organization(Policy(), {"requireAskForEdits": True})
        self.assertTrue(joined.require_ask_for_edits and joined.managed)
        self.assertEqual(joined.organization_keys, frozenset({"requireAskForEdits"}))
        self.assertTrue(joined.allow_auto_approval, "a separate switch")

    def test_scripts_still_ask_in_the_default_mode(self):
        always_allow.detach()  # The person's Always allow scripts is off (its default).
        replies = []
        self.controller.request_tool("script", "angelo_execute_python", dict(SCRIPT), replies.append)
        self.pump()
        call = next(iter(self.controller.pending.values()))
        self.assertEqual(clean_options(self.controller.state.get("options"))["approval"], "AUTO_BOUNDED")
        self.assertTrue(call.approval)
        self.assertFalse(call.automatic)
        self.assertFalse(self.controller.trusted_scripts or self.controller.always_allow_scripts)
        self.executor.execute.assert_not_called()
        self.assertFalse(replies)


if __name__ == "__main__":
    unittest.main()
