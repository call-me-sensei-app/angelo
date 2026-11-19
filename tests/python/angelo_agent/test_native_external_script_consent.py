# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Script approvals in a connected app's External chat (2.0.1): Run once, Allow for this session and Always allow.

Before 2.0.1 every script in an External chat asked, whatever the person chose: the person's Always allow scripts
never applied there and Trust scripts for this chat was refused. In 2.0.1 every script there is Angelo's own agent's
(the maintainer, 2026-11-18), so the card's three choices apply exactly as in any chat: Allow for this session is
Trust scripts for this chat (its rule: Ask too), Always allow the person's own Always allow scripts (Auto only).
Every script here is one Angelo's agent writes for the app's message (``angelo_send``): an app never calls a tool.
Real ``ExternalHost``, launcher and ``Controller`` routes, a scripted provider and Blender stand-ins. Contract:
doc/angelo/agent_execution.rst, "Script approvals".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import io
import json
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import angelo_guard_twin  # noqa: E402
from angelo_agent import always_allow, grant_authority, managed_policy
from angelo_agent.controller import ToolDispatchBudget
from angelo_agent.external_clients import AppRegistry
from angelo_agent.external_host import ClientRegistry, ExternalHost
from angelo_agent.managed_policy import Policy, with_organization
from angelo_agent.team_view import org_policy
from test_native_external_cli_host import Executor
from test_native_external_agent import LISTENER, SCRIPT, AppEnvironment
from unittest import mock

class ScriptProvider:
    """One turn of Angelo's agent without a model: it asks for one script (the person decides), then replies."""

    def __init__(self, code):
        self.code, self.turns = code, 0

    def connect(self):
        return "native-turn"

    def run_request(self, context, native_id, text, observation=None):
        self.turns += 1
        done = threading.Event()
        context.request_tool("script-%d" % self.turns, "angelo_execute_python", dict(SCRIPT, code=self.code),
                             lambda result: done.set())
        done.wait(20)
        context.emit("message_complete", {"item": "reply", "text": "Done."})
        return "completed"

    def stop_request(self, fence):
        pass

    def close(self):
        pass


angelo_guard_twin.stand_in_for_the_person(globals())   # Its tests press the card's buttons as the person does.
SCRIPTS_REVIEWED = with_organization(Policy(), org_policy({"allowAutoApproval": False}))


@unittest.skipIf(sys.platform == "win32", LISTENER)
class ExternalScriptConsentTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.root = Path(tempfile.mkdtemp(prefix="sxc-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = str(self.root / "state")
        self.env = AppEnvironment(self.state)
        self.executor = Executor(self.state)
        self.host = ExternalHost(self.env, environ={}, runtime_directory=self.root / "run")
        self.addCleanup(self.host.stop)
        self.addCleanup(lambda: [c.close() for c in self.env._controllers.values()])
        self.name = AppRegistry(self.state).connect("claude_code")
        always_allow.detach()   # Off, as a new install has it; in memory only (no state folder attached).
        self.addCleanup(always_allow.detach)
        self.asked = []   # Every script that waited for the person, by call id.
        self.choice = None   # What the person presses on the card: None (Run once), "session" or "always".
        # The app's message goes through the person's send path (``console.submit``, bpy in the app), reduced to the
        # environment's, on both the module table and the package attribute (the package imports lazily).
        import angelo_agent
        console = types.SimpleNamespace(submit=self.env.submit)
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.console": console}),
                        mock.patch.object(angelo_agent, "console", console, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- the app, the host and the person ----------------------------------------------------------------------------

    def tick(self):
        self.host.pump()
        for controller in list(self.env._controllers.values()):
            controller.pump_events()
            controller.pump_tools(self.executor, self.env.document, budget=ToolDispatchBudget(seconds=60),
                                  apply_mutation=lambda call, c=controller: c.execute_tool(call, self.executor,
                                                                                            c.document))
            for call in list(controller.pending.values()):
                if call.approval and not call.executing and not call.automatic and call.id not in self.asked:
                    self.asked.append(call.id)
                    self.person(controller, call)

    def person(self, controller, call):
        """The card's buttons, as their handlers run them (trusted_scripts_ui, which needs bpy, is a thin shell over
        these: ``test_the_card_handlers_go_through_these_routes``)."""
        if self.choice == "session":
            self.assertTrue(controller.session_allow_script(call))
        elif self.choice == "always":
            if not always_allow.enabled():
                always_allow.set_enabled(True)
            self.assertTrue(controller.always_allow_script(call))
        else:   # Run once: the exact text, this time.
            controller.review_script(call)
            controller.execute_tool(call, self.executor, controller.document)

    def run_script(self, code="result = 1"):
        """The app asks Angelo's agent for a task; the agent's turn writes one script, which the person's card (or a
        standing consent) decides. The ``angelo_send`` result, once the turn has ended."""
        from angelo_agent.external_mcp import Server
        self.env.provider = ScriptProvider(code)
        self.host.pump()
        out = io.StringIO()
        Server(self.name, self.state, stdout=out).dispatch({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "angelo_send", "arguments": {"message": "Run the check", "wait_seconds": 20}}})
        deadline = time.monotonic() + CAP_S
        while time.monotonic() < deadline:
            answers = [json.loads(line) for line in out.getvalue().splitlines() if line]
            answers = [line for line in answers if line.get("id") == 7]
            if answers and not self.conversation().turn_running():
                self.tick()
                return answers[0]["result"]
            self.tick()
            time.sleep(0.005)
        self.fail("no MCP answer")

    def agent_script(self, identity, code="result = 1"):
        """A script Angelo's own agent writes in this External chat, held at its card (the test acts as the person)."""
        from angelo_agent.external_mcp import Server
        if not self.env._controllers:   # The app's conversation, as its first look makes it (never a tool call).
            self.host.pump()
            out = io.StringIO()
            Server(self.name, self.state, stdout=out).dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "angelo_settings", "arguments": {}}})
            deadline = time.monotonic() + CAP_S
            while not any(json.loads(line).get("id") == 3 for line in out.getvalue().splitlines() if line) \
                    and time.monotonic() < deadline:
                self.tick()
                time.sleep(0.005)
        controller = self.conversation()
        if getattr(self, "fence", None) is None:   # An open request on the host's side, as a turn of the agent has.
            self.fence = controller.open_external_read()
            self.addCleanup(lambda: controller.close_external_read(self.fence) if not controller.pending else None)
        results = []
        controller.request_tool(identity, "angelo_execute_python", dict(SCRIPT, code=code), results.append)
        call = controller.pending[controller.state["id"] + "/" + identity]
        self.asked.append(call.id)   # Its card waits: the test acts as the person itself.
        for _ in range(5):
            self.tick()
        return call, results

    def conversation(self):
        return self.env._controllers[ClientRegistry(self.state).load()[self.name]]

    def last_receipt(self):
        controller = self.conversation()
        rows = [row for row in controller.state["messages"]
                if row.get("role") == "activity" and row.get("tool") == "angelo_execute_python"]
        return rows[-1].get("result") or {}

    # -- the card's three choices ------------------------------------------------------------------------------------

    def test_a_script_asks_by_default_and_the_card_offers_all_three_choices(self):
        offered = []

        def look(controller, call):
            offered.append((controller.offers_session_allow(call), controller.offers_always_allow(call)))
            controller.review_script(call)
            controller.execute_tool(call, self.executor, controller.document)
        self.person = look
        result = self.run_script()
        self.assertFalse(result["isError"], result)
        self.assertEqual(len(self.asked), 1, "with no consent, the script asks")
        self.assertEqual(offered, [(True, True)])
        self.assertEqual(self.last_receipt().get("consent"), "exact_code_review")

    def test_allow_for_this_session_is_this_chats_trust(self):
        self.choice = "session"
        self.assertFalse(self.run_script()["isError"])
        self.assertEqual(len(self.asked), 1)
        controller = self.conversation()
        self.assertTrue(controller.trusted_scripts)
        self.assertTrue(grant_authority.holds(grant_authority.TRUSTED_CHATS, controller.state["id"]))
        self.assertEqual(controller.standing_script_consent(), ("session", self.name))
        self.choice = "fail"
        self.assertFalse(self.run_script()["isError"])
        self.assertEqual(len(self.asked), 1, "the next script ran without asking")
        receipt = self.last_receipt()
        self.assertEqual((receipt.get("consent"), receipt.get("consent_app")), ("chat_session", self.name))
        self.assertNotIn("always_allow_scripts", AppRegistry(self.state).load()[self.name], "nothing is saved")

    def test_the_session_trust_covers_ask_as_the_menus_does_and_never_read_only(self):
        self.choice = "session"
        self.run_script()
        controller = self.conversation()
        controller.state["options"]["approval"] = "ASK"
        self.assertTrue(controller.trusted_scripts, "2.0.0's rule: Trust scripts for this chat covers Ask too")
        controller.state["options"]["approval"] = "READ_ONLY"
        self.assertFalse(controller.trusted_scripts)

    def test_the_session_trust_ends_with_its_conversation(self):
        self.choice = "session"
        self.run_script()
        controller = self.conversation()
        self.assertTrue(controller.trusted_scripts)
        controller.set_document(("another document",))
        self.assertFalse(controller.trusted_scripts, "a changed document ends it, as in any chat")

    def test_no_external_path_runs_code_the_app_sends(self):
        """Break-it review M2: an app's own code is never run, whatever the person allowed. Its MCP tool call of
        angelo_execute_python is refused before anything is queued, the CLI's call command is refused for an app,
        so only Angelo's agent writes scripts here."""
        from angelo_agent.external_mcp import Server
        from angelo_agent.trusted_scripts import app_supplied
        always_allow.set_enabled(True)
        self.host.pump()
        out = io.StringIO()
        Server(self.name, self.state, stdout=out).dispatch({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "angelo_execute_python", "arguments": dict(SCRIPT)}})
        deadline = time.monotonic() + CAP_S
        answers = []
        while not answers and time.monotonic() < deadline:
            self.tick()
            answers = [json.loads(line) for line in out.getvalue().splitlines() if line]
            answers = [line for line in answers if line.get("id") == 5]
            time.sleep(0.005)
        self.assertTrue(answers and answers[0]["result"]["isError"], "the tool is never offered to an app")
        from angelo_agent import cli_client
        key = AppRegistry(self.state).key(self.name)
        refused = cli_client.run({"command": "call", "args": {"tool": "angelo_execute_python",
                                                              "arguments": dict(SCRIPT)}}, self.state, self.name, 10,
                                 key=key)
        self.assertEqual(refused["code"], "refused")
        self.assertFalse(app_supplied(types.SimpleNamespace(id="chat/script-1")), "Angelo's agent's calls never match")
        self.assertEqual(self.asked, [], "nothing asked: nothing of the app's was queued")

    def session_trust_ends(self, end):
        """Break-it review S3: the trust given for an app's conversation holds only while that connection does."""
        self.choice = "session"
        self.run_script()
        controller = self.conversation()
        self.assertTrue(controller.trusted_scripts)
        registry = AppRegistry(self.state)
        if end == "disconnect":
            registry.disconnect(self.name)
        else:
            registry.connect("claude_code")   # A new key, within the same second too.
        self.assertFalse(controller.trusted_scripts, end)

    def test_the_session_trust_ends_with_a_disconnect(self):
        self.session_trust_ends("disconnect")

    def test_the_session_trust_ends_with_a_new_key(self):
        self.session_trust_ends("reconnect")

    def test_always_allow_is_the_persons_own_and_covers_external_chats_in_auto(self):
        self.choice = "always"
        self.assertFalse(self.run_script()["isError"])
        self.assertTrue(always_allow.enabled())
        self.assertTrue(grant_authority.holds(grant_authority.ALWAYS_ALLOW, "on"))
        self.choice = "fail"
        self.assertFalse(self.run_script()["isError"])
        self.assertEqual(len(self.asked), 1)
        receipt = self.last_receipt()
        self.assertEqual((receipt.get("consent"), receipt.get("consent_app")), ("always_allow", self.name))
        controller = self.conversation()
        self.assertEqual(controller.standing_script_consent(), ("always", self.name))
        controller.state["options"]["approval"] = "ASK"
        self.assertFalse(controller.always_allow_scripts, "Always allow covers Auto only, as today")

    def test_turning_it_off_reaches_a_script_already_queued(self):
        self.choice = "always"
        self.run_script()
        controller = self.conversation()
        fence = controller.open_external_read()   # An open request on the host's side, for a script admitted now.
        self.addCleanup(controller.close_external_read, fence)
        results = []
        controller.request_tool("queued", "angelo_execute_python", dict(SCRIPT), results.append)
        call = controller.pending[controller.state["id"] + "/queued"]
        self.assertIsNotNone(call.script_consent, "admitted under the person's consent")
        always_allow.set_enabled(False)
        self.assertIsNone(controller.script_consent_kind(call))

    def test_policy_read_only_and_exclusions_still_ask(self):
        self.choice = "always"
        self.run_script()
        controller = self.conversation()
        for name, setting in (("read only", "READ_ONLY"), ("policy", None), ("excluded", None)):
            with self.subTest(name=name):
                results = []
                code = "bpy.context.preferences.view.ui_scale = 2" if name == "excluded" else "result = 1"
                if setting:
                    controller.state["options"]["approval"] = setting
                if name == "policy":
                    managed_policy.reset(SCRIPTS_REVIEWED)
                controller.request_tool(name, "angelo_execute_python", dict(SCRIPT, code=code), results.append)
                call = controller.pending.get(controller.state["id"] + "/" + name)
                if call is not None:
                    self.assertIsNone(controller.script_consent_kind(call), name)
                    self.assertFalse(controller.offers_always_allow(call), name)
                    self.assertFalse(controller.offers_session_allow(call), name)
                controller.state["options"]["approval"] = "AUTO_BOUNDED"
                managed_policy.reset(Policy())

    def saved_consent(self, external):
        """The person's Always allow as a file saved by 2.0.0 (``external`` None: no scope recorded) or 2.0.1+."""
        folder = Path(self.state) / "consent"
        folder.mkdir(parents=True, exist_ok=True)
        saved = {"version": 1, "enabled": True}
        if external is not None:
            saved["external"] = external
        (folder / always_allow.FILE).write_text(json.dumps(saved))
        always_allow.attach(folder)
        return folder

    def test_a_2_0_0_consent_keeps_normal_chats_and_asks_once_in_an_external_chat(self):
        """The review's round-2 SHOULD: a consent saved by 2.0.0 never silently starts covering connected apps'
        chats. The External chat's script asks; one press of Always allow on its card widens the saved consent."""
        folder = self.saved_consent(None)
        self.assertTrue(always_allow.enabled() and not always_allow.covers_external())
        call, results = self.agent_script("old-consent")
        controller = self.conversation()
        self.assertFalse(call.automatic or results, "a 2.0.0 consent does not cover a connected app's chat")
        self.assertFalse(controller.always_allow_scripts)
        self.assertTrue(controller.offers_always_allow(call), "its card offers Always allow, naming those chats")
        self.assertTrue(always_allow.cover_external())   # The person's press (the card handler's widening).
        self.assertTrue(controller.always_allow_script(call))
        self.assertTrue(json.loads((folder / always_allow.FILE).read_text())["external"])
        self.assertTrue(controller.always_allow_scripts)
        later, ran = self.agent_script("after-widening")
        self.assertTrue(later.automatic and ran)

    def test_a_2_0_1_consent_covers_both_and_turning_it_on_now_says_so(self):
        folder = self.saved_consent(True)
        self.assertTrue(always_allow.covers_external())
        call, results = self.agent_script("new-consent")
        self.assertTrue(call.automatic and results)
        always_allow.set_enabled(False)
        always_allow.set_enabled(True)   # Given now: it covers connected apps' chats.
        self.assertTrue(json.loads((folder / always_allow.FILE).read_text())["external"])

    def test_a_2_0_0_consent_still_covers_a_chat_of_the_persons_own(self):
        self.saved_consent(None)
        from angelo_agent.controller import Controller
        own = Controller(str(Path(self.state) / "own"))
        self.addCleanup(own.close)
        own.set_document(("own",))
        self.assertTrue(own.always_allow_scripts, "chats of the person's own as before")

    def test_the_card_handlers_go_through_these_routes(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/trusted_scripts_ui.py"
                  ).read_text(encoding="utf-8")
        session = source[source.index("def session_allow_from_card"):source.index("def turn_off_standing")]
        self.assertIn("controller.session_allow_script(call)", session)
        self.assertIn("agent_script_running()", session)


if __name__ == "__main__":
    unittest.main()
