# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sending during a running turn never errors: the message is held (steered or queued), a connection or model chosen
meanwhile takes over after the turn and says so, and pending messages go out, come back or survive as designed.

1.0.9 evidence: Enter during a long turn showed "Something went wrong" with "The current request is still running;
queue the connection for the next message" (``ui.prepare_controller`` → ``Controller.select``), and nothing could be
steered or queued. The real ``Controller`` runs with stand-in providers; no Blender, process or model.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import chat_pending, context_rebase, request_rows, routing, steering_delivery  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.steering import PAUSED, QUEUE, STEER, WAITING  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
SETTINGS = {"model": "", "reuse_codex_login": True}


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = str(uuid.uuid4())
        self.c = self.chat()

    def chat(self):
        chat = Controller(self.directory.name, session_id=self.session)
        self.addCleanup(chat.close)
        chat.set_document(("document", 1))
        return chat

    def notices(self, text):
        return [m for m in self.c.state["messages"] if m.get("role") == "system" and text in m.get("text", "")]


class ChoiceDuringATurnTests(ChatTests):
    def test_preparing_a_connection_during_a_turn_never_raises_and_takes_over_after_it(self):
        self.c.select("codex_subscription", dict(SETTINGS), "", "conn-a")
        self.c.busy = True
        self.c.select("codex_subscription", dict(SETTINGS), "", "conn-a")   # The same: nothing changes or shows.
        self.assertEqual(self.notices("takes over"), [])
        self.c.select("claude_subscription", dict(SETTINGS), "", "conn-b")
        self.assertEqual(self.c.state["connection_id"], "conn-a", "The running request keeps its connection")
        self.assertEqual(self.c.selection_connection_id, "conn-b")
        self.c.select("claude_subscription", dict(SETTINGS), "", "conn-b")
        notice = self.notices("takes over from your next message")
        self.assertEqual(len(notice), 1, "Said once")
        self.assertEqual(notice[0]["i18n"][0], "Your new choice takes over from your next message; this turn "
                                               "finishes as it started.", "Translated at display")
        self.c.busy = False
        self.assertTrue(self.c.apply_next_connection())
        self.assertEqual((self.c.state["provider"], self.c.state["connection_id"]), ("claude_subscription", "conn-b"))

    def test_a_model_chosen_during_a_turn_says_it_applies_next(self):
        self.c.select("codex_subscription", dict(SETTINGS), "", "conn-a")
        self.c.busy, self.c.turn_options = True, {"model": "gpt-a"}
        self.c.choose_connection("codex_subscription", dict(SETTINGS), "", "conn-a",
                                 models=[{"id": "gpt-b", "default": True, "efforts": [], "default_effort": "",
                                          "fast_tier": False}], model="gpt-b")
        self.assertEqual(len(self.notices("takes over")), 1)
        self.assertEqual(self.c.turn_options["model"], "gpt-a", "The running request's model is untouched")

    def test_the_send_path_holds_before_preparing_anything(self):
        """console.execute sends through ``send_message``, which holds the message during a turn before
        ``ui.prepare_controller`` (pinned in source)."""
        tree = ast.parse((ADDON / "console.py").read_text(encoding="utf-8"))
        functions = {node.name: ast.unparse(node) for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("send_message(controller, text)", functions["execute"])
        self.assertIn("controller.turn_running()", functions["send_message"])
        self.assertIn("chat_pending.hold(controller, text, references, **policy,", functions["send_message"])
        for name in ("execute", "send_message"):
            self.assertNotIn("prepare_controller", functions[name], "Prepared only in submit, never under a turn")


class EscalationEndTests(unittest.TestCase):
    def test_an_escalation_that_could_not_start_is_not_a_normal_end(self):
        """``Controller._after_turn``: the failed re-run marks the turn failed before ``chat_pending.turn_ended``
        reads it, so waiting messages pause instead of sending by themselves (break-it NIT; pinned in source)."""
        source = (ADDON / "controller.py").read_text(encoding="utf-8")
        handler = source[source.index("    def _after_turn(self, data):"):source.index("    def _request_steers(self):")]
        self.assertIn('self.version, data["status"] = self.version + 1, "failed"', handler)
        pump = source[source.index('if data.get("not_sent") is True and request_rows.persons_send(self) and not retry_unsent:'):]
        self.assertIn("if self._turn_pending and not self._turn_escalations:", pump[:600],
                      "A re-run's pending messages reached the first model: never restored as unread (round 2)")
        self.assertIn("self._request_text if self._turn_escalations else self._turn_message", pump[:900],
                      "The person's words back in the box, never the model-facing steer heading")
        pump = source[source.index("self._after_turn(data)"):]
        self.assertTrue(pump.split("\n", 2)[1].strip().startswith('chat_pending.turn_ended(self, data.get("status")'))


class HoldTests(ChatTests):
    """``chat_pending.hold`` itself, on the real controller, for each runtime (break-it B1: an import error made
    every Enter during a turn an error card again, and no test called it)."""

    def test_enter_during_a_turn_holds_the_message_on_either_runtime(self):
        for mode, steers in (("claude_subscription", True), ("codex_subscription", True)):
            with self.subTest(mode=mode):
                self.c.state["provider"], self.c.busy, self.c.provider = mode, True, None   # Not yet connected.
                item = chat_pending.hold(self.c, "make it red")
                self.assertEqual(item.mode, STEER if steers else QUEUE)
                self.assertEqual(chat_pending.steerable(self.c), steers)
                self.c.inbox.take(item.id)
        self.c.provider = SimpleNamespace(steer_ready=False)   # The person's own Codex: no hold, so it queues.
        self.assertEqual(chat_pending.hold(self.c, "then a lamp").mode, QUEUE)
        self.c.provider, self.c.busy = None, False

    def test_an_empty_or_one_too_many_message_is_refused_in_the_persons_words(self):
        from angelo_agent.display_text import Text
        from angelo_agent.steering import MAX_PENDING
        self.c.busy = True
        for text in ("", "   "):
            with self.assertRaises(ValueError) as caught:
                chat_pending.hold(self.c, text)
            self.assertIsInstance(caught.exception.args[0], Text, "Translated, as an idle send's refusal")
        for index in range(MAX_PENDING):
            chat_pending.hold(self.c, "m%d" % index)
        with self.assertRaises(OverflowError) as caught:
            chat_pending.hold(self.c, "one too many")
        self.assertIsInstance(caught.exception.args[0], Text)
        self.c.busy = False

    def test_while_a_lane_prepares_the_previous_message_its_pictures_stay_with_it(self):
        picture = {"name": "box.png", "kind": "image", "bytes": 5, "path": "/nowhere/box.png"}
        self.c.attachments, self.c._delivery_stops = [picture], 1   # A worker's lane preparing the last send.
        item = chat_pending.hold(self.c, "and make it red")
        self.assertEqual((item.references, self.c.attachments), ([], [picture]))
        self.c._delivery_stops = None

    def test_a_picture_alone_while_a_lane_prepares_says_where_it_goes(self):
        from angelo_agent.display_text import Text
        self.c.attachments, self.c._delivery_stops = [{"name": "box.png", "kind": "image"}], 1
        with self.assertRaises(ValueError) as caught:
            chat_pending.hold(self.c, "")
        self.assertEqual(caught.exception.args[0].template, "These pictures go with the message you just sent.")
        self.assertIsInstance(caught.exception.args[0], Text)
        self.c._delivery_stops = None

    def test_the_composer_hook_recalls_the_newest_pending_message(self):
        fake_ui = types.ModuleType("angelo_agent.ui")
        fake_ui.get_controller = lambda: self.c
        with patch.dict(sys.modules, {"angelo_agent.ui": fake_ui}):
            import angelo_agent
            with patch.object(angelo_agent, "ui", fake_ui, create=True):
                line = SimpleNamespace(body="", current_character=0)
                self.assertFalse(chat_pending.recall_newest(line, "session"), "Nothing pending: the key is the table's")
                self.c.inbox.hold("make it red")
                self.assertTrue(chat_pending.recall_newest(line, "session"))
        self.assertEqual((line.body, len(self.c.inbox)), ("make it red", 0))

    def test_send_message_holds_during_a_turn_and_submits_when_idle(self):
        from unittest.mock import MagicMock
        with patch.dict(sys.modules, {"bpy": MagicMock()}):
            sys.modules.pop("angelo_agent.console", None)
            from angelo_agent import console
        self.addCleanup(sys.modules.pop, "angelo_agent.console", None)
        submitted = []
        patcher = patch.object(console, "submit", lambda chat, text, references=None, **_: submitted.append(
            (text, references)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.c.busy = True
        held = console.send_message(self.c, "steer this")
        self.assertEqual((held.text, submitted), ("steer this", []))
        self.c.busy = False
        console.send_message(self.c, "a new request", [{"name": "a.png"}])
        self.assertEqual(submitted, [("a new request", [{"name": "a.png"}])])



class SendTests(ChatTests):
    def request(self, provider, **kwargs):
        self.c.provider, self.c.state["native_id"] = provider, "warm-thread"   # Connected: no connect needed.
        worker = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
        with patch("angelo_agent.controller.threading.Thread", return_value=worker), \
                patch.object(self.c.references, "checked_path", return_value=Path(self.directory.name)), \
                patch.object(self.c.references, "prompt_context", return_value=[]):
            self.c.send("pending words", **kwargs)
            self.c._run("pending words")
        self.c.pump_events()

    def test_a_pending_send_takes_its_own_pictures_and_comes_back_paused_if_never_sent(self):
        from angelo_agent.codex_turns import STUCK, MessageNotSent
        mine = {"name": "box.png", "kind": "image", "bytes": 5, "path": "/nowhere/box.png"}
        theirs = {"name": "ref.png", "kind": "image", "bytes": 5, "path": "/nowhere/ref.png"}
        self.c.attachments = [mine]   # Something the person attached to their next message meanwhile.
        due = [self.c.inbox.hold("pending words", [theirs])]
        self.c.inbox.next_due()

        class Provider:
            def bind_request(self, context):
                pass

            def run(self, native_id, text, observation=None):
                raise MessageNotSent(STUCK)

            def stop_request(self, fence):
                pass

            def close(self):
                pass
        self.c.inbox.sent(due)
        self.request(Provider(), references=[theirs], pending=due)
        self.assertEqual(self.c.attachments, [mine], "The message box keeps its own")
        self.assertIsNone(self.c.unsent_draft, "Never into the message box as well")
        self.assertEqual([(item.text, item.phase, item.reason) for item in self.c.inbox.items()],
                         [("pending words", PAUSED, "not_started")])


class RowTests(unittest.TestCase):
    ROWS = [{"role": "user", "text": "Build a hut"}, {"role": "assistant", "text": "Building"},
            {"role": "user", "text": "make the roof red", "steer": True, "steer_id": 1},
            {"role": "user", "text": "unread", "steer": True, "steer_id": 2, "withdrawn": True},
            {"role": "assistant", "text": "Done"}]

    def test_a_steer_never_starts_a_request(self):
        self.assertFalse(request_rows.starts_request(self.ROWS[2]))
        self.assertTrue(request_rows.starts_request(self.ROWS[0]))
        self.assertEqual(context_rebase.handoff_start(self.ROWS), 0, "A rebased context starts at the request")

    def test_the_handoff_names_a_read_steer_and_never_an_unread_one(self):
        entries = routing.handoff(self.ROWS) if hasattr(routing, "handoff") else None
        text = str(entries)
        self.assertIn("make the roof red", text)
        self.assertIn("sent by the person while the previous request ran", text)
        self.assertNotIn("unread", text)

    def test_an_auto_rerun_asks_the_request_with_the_steers_it_read_in_order(self):
        chat = SimpleNamespace(_request_message_index=0, state={"messages": list(self.ROWS)})
        steers = Controller._request_steers(chat)
        self.assertEqual(steers, ["make the roof red"])
        text = steering_delivery.with_steers("Build a hut", steers)
        self.assertTrue(text.startswith("Build a hut\n\n" + steering_delivery.STEER_HEADING))


class DispatchTests(ChatTests):
    def stub_console(self, submit):
        module = types.ModuleType("angelo_agent.console")
        module.submit = submit
        stub = patch.dict(sys.modules, {"angelo_agent.console": module})
        stub.start()
        self.addCleanup(stub.stop)
        import angelo_agent
        patcher = patch.object(angelo_agent, "console", module, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_everything_waiting_goes_as_one_request_oldest_first(self):
        sent = []
        self.stub_console(lambda chat, text, references=None, pending=(), unsent=None: sent.append((text, references)))
        self.c.inbox.hold("make it red")
        self.c.inbox.hold("then a lamp", steerable=False)
        self.assertTrue(chat_pending.dispatch_due(self.c))
        self.assertEqual(sent, [("make it red\n\nthen a lamp", [])])
        self.assertEqual(len(self.c.inbox), 0)

    def test_angelos_follow_up_waits_behind_the_person_and_goes_as_its_own_request(self):
        sent = []
        self.stub_console(lambda chat, text, references=None, pending=(), unsent=None, **options:
                          sent.append((text, options)))
        self.c.busy = True
        held = chat_pending.send_or_queue(self.c, "Continue the request.", "Continue", host_request=[])
        self.assertIsNotNone(held, "Queued while the turn runs")
        self.c.busy = False
        self.c.inbox.hold("make it blue")   # The person's own, typed after it but before the turn's end.
        self.assertTrue(chat_pending.dispatch_due(self.c))
        self.assertTrue(chat_pending.dispatch_due(self.c))
        self.assertEqual(sent, [("Continue the request.", {"host_request": []}), ("make it blue", {})])
        self.assertIsNone(chat_pending.send_or_queue(self.c, "Continue the request.", "Continue"),
                          "Idle with nothing waiting: sent at once")
        self.assertEqual(sent[-1], ("Continue the request.", {}))
        self.c.inbox.hold("a person's message still waiting")
        self.assertIsNotNone(chat_pending.send_or_queue(self.c, "Continue the request.", "Continue"),
                             "Never ahead of the person's words")
        self.assertTrue(chat_pending.person_waiting(self.c))

    def test_when_idle_a_model_chosen_during_the_turn_takes_over_before_the_pending_messages_go(self):
        order = []
        self.stub_console(lambda chat, text, **_: order.append(("sent", text)))
        self.c.apply_next_connection = lambda: order.append(("connection",)) or True
        self.c.inbox.hold("make it red")
        self.assertTrue(chat_pending.when_idle(self.c))
        self.assertEqual(order, [("connection",), ("sent", "make it red")])
        ui = (ADDON / "ui.py").read_text(encoding="utf-8")
        self.assertIn("chat_pending.when_idle(controller)", ui, "The UI pump sends them")

    def test_a_worker_lanes_refusal_brings_them_back_paused_whenever_it_comes(self):
        later = []
        self.stub_console(lambda chat, text, references=None, pending=(), unsent=None, **_: later.append(unsent))
        item = self.c.inbox.hold("make it red")
        self.assertTrue(chat_pending.dispatch_due(self.c), "The lane took it")
        self.assertEqual(len(self.c.inbox), 0)
        later[0](RuntimeError("The worker's document changed"))   # Its lane refused it afterwards.
        self.assertEqual([(i.text, i.phase) for i in self.c.inbox.items()], [("make it red", PAUSED)],
                         "Never lost (break-it B3)")
        self.stub_console(lambda chat, text, references=None, pending=(), unsent=None, **_: unsent(
            RuntimeError("Refused at once")))
        self.c.inbox.resume(item.id)
        self.assertFalse(chat_pending.dispatch_due(self.c), "Refused before it went: not sent")
        self.assertEqual(item.phase, PAUSED)
        self.assertEqual(self.c.inbox.items(), [item])

    def test_nothing_goes_while_a_turn_runs_and_a_refusal_pauses_without_an_error_card(self):
        from angelo_agent.display_text import Text
        from angelo_agent.ui_text import n_

        def refuse(*_args, **_kwargs):
            raise RuntimeError(Text(n_("Show this chat's scene to send to it.")))
        self.stub_console(refuse)
        item = self.c.inbox.hold("make it red")
        self.c.busy = True
        self.assertFalse(chat_pending.dispatch_due(self.c))
        self.c.busy = False
        self.assertFalse(chat_pending.dispatch_due(self.c))
        self.assertEqual(item.phase, PAUSED)
        self.assertEqual(item.reason["i18n"][0], "Show this chat's scene to send to it.")
        self.assertFalse(any(m.get("role") == "error" for m in self.c.state["messages"]))


class SteerRowTests(ChatTests):
    def test_a_steer_row_is_redacted_like_the_persons_own_row(self):
        self.c.redact = lambda value: str(value).replace("sk-secret", "[REDACTED]")   # Controller.redact's job.
        chat_pending.show_steer(self.c, {"text": "use key sk-secret", "id": 7})
        self.assertEqual(self.c.state["messages"][-1]["text"], "use key [REDACTED]")


class SavedTests(ChatTests):
    def test_pending_messages_belong_to_their_chat_and_come_back_paused(self):
        self.c.inbox.hold("make it red")
        self.c.inbox.hold("then a lamp", steerable=False)
        self.c.pump_events()   # Saved when they changed.
        path = Path(self.directory.name) / "pending" / (self.session + ".json")
        self.assertTrue(path.exists())
        again = self.chat()
        self.assertEqual([(item.text, item.mode, item.phase) for item in again.inbox.items()],
                         [("make it red", STEER, PAUSED), ("then a lamp", QUEUE, PAUSED)])
        for item in again.inbox.items():
            again.inbox.take(item.id)
        again.pump_events()
        self.assertFalse(path.exists(), "Removed once none wait")


if __name__ == "__main__":
    unittest.main()
