# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it review 2 of the agent-only MCP (2026-11-18), one test per finding, through the real host and launcher.

M2-a: a command admitted before Disconnect never runs after it: every app command is authorized again on the main
thread, the label's queued commands fail, a revoked conversation starts no turn, and "revoked" is read from the app
registry, so it holds after a restart. S-a: an app's new conversation starts no looser than the mode its last one
ended in, a delete included, and an app cannot rotate through `session new`. S-c: a send at the parked-read cap is
answered at once, never "busy" after it went. The total cap holds across reads. A disconnected app's conversation
is never a keyless client's. Contract: doc/angelo/external_mcp.rst, "Revoking access".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
from host_waits import CAP_S  # noqa: E402  A completion wait's cap: only a hang reaches it.

import json
import sys
import threading
import types
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import cli_client, external_access, external_agent  # noqa: E402
from angelo_agent.external_clients import AppRegistry  # noqa: E402
from angelo_agent.external_host import ClientRegistry  # noqa: E402
from test_native_external_agent import LISTENER, AgentFixture, TurnProvider  # noqa: E402


@unittest.skipIf(sys.platform == "win32", LISTENER)
class ReviewTwoTests(AgentFixture):
    def queued(self, arguments):
        """An ``agent`` command the service admitted and queued, not yet drained by the main thread."""
        self.host.pump()
        key = AppRegistry(self.state).key(self.name)
        result = {}
        thread = threading.Thread(target=lambda: result.update(value=cli_client.run(
            {"command": "agent", "args": arguments}, self.state, self.name, 30, key=key)), daemon=True)
        thread.start()
        deadline = time.monotonic() + CAP_S
        while not len(self.host.service.work) and time.monotonic() < deadline:
            time.sleep(0.005)   # No pump: the command waits in the queue.
        self.assertTrue(len(self.host.service.work), "the command was queued")
        return thread, result

    # M2-a -----------------------------------------------------------------------------------------------------------

    def test_a_message_queued_before_disconnect_never_starts_a_turn(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "first"})   # The conversation exists and is the app's.
        controller = self.conversation()
        self.until(lambda: not controller.turn_running())
        thread, result = self.queued({"action": "send", "message": "sent before the disconnect", "wait": 0})
        AppRegistry(self.state).disconnect(self.name)
        for _ in range(5):
            self.tick()
        thread.join(CAP_S)
        self.assertEqual(result["value"]["code"], "unavailable", result)
        self.assertEqual(len(self.env.provider.texts), 1, "nothing admitted before it reached the agent")
        self.assertTrue(external_access.revoked(controller))

    def test_revoked_is_read_from_the_registry_after_a_restart(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "first"})
        controller = self.conversation()
        self.until(lambda: not controller.turn_running())
        self.assertEqual(controller.state["external_app"]["connected_at"],
                         AppRegistry(self.state).load()[self.name]["connected_at"])
        controller.external_revoked = False   # A restart: nothing in memory says it was revoked.
        self.assertFalse(external_access.revoked(controller))
        registry = AppRegistry(self.state)
        registry.disconnect(self.name)
        self.assertTrue(external_access.revoked(controller), "the registry no longer holds its connection")
        self.name = registry.connect("claude_code")
        self.assertTrue(external_access.revoked(controller), "a new connection is not the one its messages came on")
        with self.assertRaises(RuntimeError):
            controller.require_model_chat()
        self.release.set()
        self.call("angelo_send", {"message": "on the new connection"})   # The app's own message lifts it.
        self.assertFalse(external_access.revoked(self.conversation()))

    # S-a ------------------------------------------------------------------------------------------------------------

    def test_deleting_an_unloaded_conversation_keeps_its_mode_as_the_floor(self):
        """Round 3, SHOULD 1: through the real delete, of a conversation no controller holds (after a restart)."""
        from angelo_agent import conversations_delete
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "first"})
        old = self.conversation()
        self.until(lambda: not old.turn_running())
        old.set_options(approval="READ_ONLY")   # The person's choice.
        identity = old.state["id"]
        old.close()   # A restart: nothing holds it now; only its saved state says whose it was.
        del self.env._controllers[identity]
        ui = types.SimpleNamespace(_sessions=self.env._sessions, _controllers=self.env._controllers, _drafts={},
                                   state_directory=lambda: self.state)
        conversations_delete.delete(ui, identity, switch=lambda chat: None)
        self.assertEqual(AppRegistry(self.state).load()[self.name]["approval"], "READ_ONLY")
        self.host.reload_apps(force=True)
        fresh = self.host.create_conversation(self.name)
        self.assertEqual(json.loads((Path(self.state) / "sessions" / (fresh + ".json")).read_text())
                         ["options"]["approval"], "READ_ONLY")

    def test_an_app_cannot_rotate_through_session_new(self):
        self.host.pump()
        key = AppRegistry(self.state).key(self.name)
        value = cli_client.run({"command": "session", "args": {"action": "new"}}, self.state, self.name, 10, key=key)
        self.assertEqual(value["code"], "refused")
        self.assertIn("angelo_new_chat", value["message"])

    # S-c and the total cap ------------------------------------------------------------------------------------------

    def test_a_send_at_the_parked_cap_is_answered_at_once_never_busy(self):
        self.env.provider = TurnProvider(self.release, script=False)
        with mock.patch.object(external_agent, "MAX_PARKED_PER_APP", 0):
            sent = self.call("angelo_send", {"message": "go", "wait_seconds": 30})
        self.assertFalse(sent["isError"], sent)
        self.assertEqual(sent["structuredContent"]["delivered"], "started")
        self.until(lambda: self.env.provider.texts)
        self.assertEqual(len(self.env.provider.texts), 1, "sent once")
        self.release.set()

    def test_the_total_cap_holds_across_reads(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.call("angelo_send", {"message": "go"})
        self.until(lambda: self.env.provider.texts)
        cursor = self.call("angelo_read")["structuredContent"]["cursor"]
        with mock.patch.object(external_agent, "MAX_PARKED", 1), \
                mock.patch.object(external_agent, "MAX_PARKED_PER_APP", 5):
            self.host.pump()
            server, out = self.server()
            server.dispatch({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                             "params": {"name": "angelo_read", "arguments": {"cursor": cursor, "wait_seconds": 30}}})
            self.until(lambda: len(self.host._agent_waiters) == 1)
            refused = self.call("angelo_read", {"cursor": cursor, "wait_seconds": 30})
        self.assertEqual(refused["structuredContent"]["code"], "busy")
        self.release.set()

    # S5 (rest) ------------------------------------------------------------------------------------------------------

    def test_a_disconnected_apps_conversation_is_never_a_keyless_clients(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "first"})
        old = self.conversation()
        self.until(lambda: not old.turn_running())
        AppRegistry(self.state).disconnect(self.name)
        self.host.pump()
        result = {}
        thread = threading.Thread(target=lambda: result.update(value=cli_client.run(
            {"command": "status", "args": {}}, self.state, self.name, 30)), daemon=True)
        thread.start()
        self.until(lambda: "value" in result)   # Ticks the host, which answers on the main thread.
        status = result["value"]
        self.assertIsNone(status["result"]["conversation"], "a keyless client under its name sees none of it")
        self.assertEqual(ClientRegistry(self.state).load()[self.name], old.state["id"], "kept for the app's return")


@unittest.skipIf(sys.platform == "win32", LISTENER)
class RoundThreeTests(ReviewTwoTests.__base__):
    """Break-it review 3 (2026-11-18): each SHOULD through the real host and launcher."""

    def queued(self, arguments):
        return ReviewTwoTests.queued(self, arguments)

    def test_a_key_rotation_is_a_revoke_for_the_old_key(self):
        """SHOULD 3: a command proved with the old key never runs after a reconnect, and the running turn stops."""
        self.env.provider = TurnProvider(self.release, script=False)
        self.call("angelo_send", {"message": "a long one"})
        controller = self.conversation()
        self.until(lambda: self.env.provider.texts)
        thread, result = self.queued({"action": "send", "message": "proved with the old key", "wait": 0})
        AppRegistry(self.state).connect("claude_code")   # The person's Connect again: a new key.
        for _ in range(5):
            self.tick()
        thread.join(CAP_S)
        self.assertEqual(result["value"]["code"], "unavailable", result)
        self.until(lambda: not controller.turn_running())
        self.assertEqual(len(self.env.provider.texts), 1, "the old key's message never reached the agent")
        self.assertTrue(external_access.revoked(controller))

    def test_a_new_chat_is_stamped_at_its_creation(self):
        """SHOULD 4: a new chat (or a move) is the app's from its start, so revoked holds across a restart."""
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "first"})
        self.until(lambda: not self.conversation().turn_running())
        self.call("angelo_new_chat")
        fresh = self.conversation()
        self.assertTrue(fresh.state.get("external_app"), "stamped before the app's first message there")
        fresh.external_revoked = False   # A restart.
        AppRegistry(self.state).disconnect(self.name)
        self.assertTrue(external_access.revoked(fresh))

    def test_a_goal_the_person_stopped_stays_proposed_through_a_new_chat(self):
        """SHOULD 2: a new chat never restarts, in Auto, a goal the person stopped."""
        from angelo_agent import goal_controls, goal_loop
        from angelo_agent import goal_state as G
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "keep going until it matches"})
        old = self.conversation()
        self.until(lambda: not old.turn_running())
        goal_controls.execute(old, None, {"action": "SET", "objective": "Match it"})
        self.assertEqual(goal_loop.goal_of(old)["status"], G.ACTIVE)
        goal_controls.stop(old)   # The person's Stop goal.
        self.call("angelo_new_chat")
        fresh = self.conversation()
        self.assertNotEqual(fresh.state["id"], old.state["id"])
        self.call("angelo_send", {"message": "keep going until it matches again"})   # The app's words, there.
        self.until(lambda: not fresh.turn_running())
        goal_controls.execute(fresh, None, {"action": "SET", "objective": "Match it again"})
        self.assertEqual(goal_loop.goal_of(fresh)["status"], G.PROPOSED, "only the person starts it")
        goal_controls.start(fresh)   # The person's Start lifts it for the app's next goals.
        self.assertNotIn("goal_ended", AppRegistry(self.state).load()[self.name])

    def test_revoke_closes_the_apps_pages_and_drops_what_the_host_held(self):
        self.env.provider = TurnProvider(self.release, script=False)
        self.release.set()
        self.call("angelo_send", {"message": "first"})
        identity = self.conversation().state["id"]
        self.until(lambda: not self.conversation().turn_running())
        closed = []
        with mock.patch.object(external_agent, "_end_page", lambda conversation, why: closed.append(conversation)):
            AppRegistry(self.state).disconnect(self.name)
            self.host.pump()
        self.assertIn(identity, closed, "its live page ended with its access")
        for held in (self.host._tasks, self.host._owned, self.host._views, self.host._looks, self.host._sends):
            self.assertNotIn(self.name, held)


class ModelListTests(unittest.TestCase):
    """The v201 end-to-end run: before any message, a fresh profile's settings listed no models, which an app could
    not tell from "nothing connected". The catalogue is fetched and the answer says it is loading."""

    def test_an_empty_catalogue_is_fetched_and_said_to_be_loading(self):
        from angelo_agent.external_agent import AgentUi
        calls = []
        ui = AgentUi()
        ui.ui = types.SimpleNamespace(model_catalog_notice=lambda controller: (False, ""),
                                      model_choices=lambda controller: [],
                                      refresh_connected_models=lambda: calls.append("refresh"))
        self.assertEqual(ui.models_loading(object()), (True, ""))
        self.assertEqual(calls, ["refresh"])

    def test_a_connection_whose_sign_in_is_not_checked_yet_is_loading_and_none_says_so(self):
        """The v201 run on 2e0ba87e9f0: right after a start a subscription's sign-in check has not answered, so no
        connection is ready yet (Angelo's own welcome says so too); that is "loading", never "nothing connected"."""
        from angelo_agent.external_agent import AgentUi
        ui = AgentUi()
        checking = types.SimpleNamespace(running={"c1"})
        ui.ui = types.SimpleNamespace(ready_connections=lambda: [], model_catalog_notice=lambda controller: (False, ""),
                                      model_choices=lambda controller: [], refresh_connected_models=lambda: None,
                                      connection_records=lambda: {"c1": {"id": "c1", "mode": "claude_subscription"}},
                                      _authentication=checking)
        self.assertEqual(ui.models_loading(object()), (True, ""), "its sign-in check still runs")
        checking.running = set()   # Answered "Not connected" (the v201 isolated profile): never loading forever.
        loading, error = ui.models_loading(object())
        self.assertFalse(loading)
        self.assertIn("confirmed sign-in", error)
        ui.ui.connection_records = lambda: {}
        self.assertEqual(ui.models_loading(object()), (False, "No AI connection is connected in Angelo"))

    def test_a_failing_catalogue_says_why(self):
        from angelo_agent.external_agent import AgentUi
        ui = AgentUi()
        ui.ui = types.SimpleNamespace(model_catalog_notice=lambda controller: (False, "Mine: signed out"),
                                      model_choices=lambda controller: [], refresh_connected_models=lambda: None)
        self.assertEqual(ui.models_loading(object()), (False, "Mine: signed out"))


@unittest.skipIf(sys.platform == "win32", LISTENER)
class ModelListThroughTheHostTests(AgentFixture):
    def test_settings_before_any_message_say_the_models_are_loading(self):
        with mock.patch.object(type(self.env), "MODELS", []):
            settings = self.call("angelo_settings")["structuredContent"]
        self.assertEqual(settings["models"], [])
        self.assertTrue(settings["models_loading"], "an empty list says it is loading, never just empty")


class QueuedTrayTests(unittest.TestCase):
    """The v201 end-to-end run: a queued message longer than the tray's rows raised in the draw (a tuple of wrapped
    rows joined with a list) and blanked the whole chat, every waiting card with it. An app's queued message always
    was that long, its frame included. No listener: this runs on Windows too."""

    def view(self):
        def wrap(text, width, size, font):   # As chat_view.wrap: a tuple of (row, offset), a row per 20 characters.
            return tuple((text[index:index + 20], index) for index in range(0, len(text), 20))
        return types.SimpleNamespace(wrap=wrap, elide=lambda text, width, size: text, _font=0)

    def test_a_long_queued_message_shows_its_start_and_never_raises(self):
        from angelo_agent import chat_pending
        item = types.SimpleNamespace(host=False, label="", text="the person's own long message " * 6)
        rows = chat_pending._rows(self.view(), item, 400, 12)
        self.assertEqual(len(rows), chat_pending.MAX_ROWS)
        self.assertTrue(rows[-1][0].endswith(" …"))

    def test_an_apps_queued_message_shows_its_words_not_the_frame(self):
        from angelo_agent import chat_pending
        from angelo_agent import external_transcript as transcript
        item = types.SimpleNamespace(host=False, label="", text=transcript.framed("Codex", "Then a sphere"))
        rows = chat_pending._rows(self.view(), item, 400, 12)
        shown = "".join(row[0] for row in rows)
        self.assertTrue(shown.startswith("Then a sphere"), shown)
        self.assertNotIn("untrusted data", shown)


class ServiceTests(unittest.TestCase):
    """No listener: these run on Windows too."""

    def test_a_stop_is_never_refused_by_the_rate_limit(self):
        import os
        from angelo_agent.external_cli import ClientInfo, Peer
        from angelo_agent.external_requests import RateLimiter
        from angelo_agent.external_service import RequestService
        app = ClientInfo("claude-code", Peer(os.getpid(), os.getpid(), True), True, "k")
        handler = RequestService(lambda: {}, limiter=RateLimiter(limit=0), main_thread_timeout=2)

        def host():
            work = None
            while work is None:
                work = handler.work.take()
                time.sleep(0.005)
            work.claim()
            work.complete({"code": "ok", "message": "Stopping"})
        threading.Thread(target=host, daemon=True).start()
        self.assertEqual(handler.handle({"command": "agent", "args": {"action": "stop"}}, app)["code"], "ok")
        self.assertEqual(handler.handle({"command": "agent", "args": {"action": "read"}}, app)["code"], "busy",
                         "everything else is limited")

    def test_a_goal_whose_budget_ended_makes_the_apps_next_one_proposed(self):
        from angelo_agent import goal_controls
        from angelo_agent import goal_state as G
        app_chat = types.SimpleNamespace(external_client="codex")
        own_chat = types.SimpleNamespace(external_client="")
        self.assertTrue(goal_controls._after_person_end(app_chat, {"status": G.BUDGET}))
        self.assertTrue(goal_controls._after_person_end(app_chat, {"status": G.STOPPED}))
        self.assertFalse(goal_controls._after_person_end(app_chat, {"status": G.PASSED}))
        self.assertFalse(goal_controls._after_person_end(own_chat, {"status": G.BUDGET}), "the person's own chat")


if __name__ == "__main__":
    unittest.main()
