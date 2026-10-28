# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker Blender hosting its chat's real ``Controller`` (design A′, ``worker_chat_host``), without Blender.

Pinned here: a hosted chat runs a whole turn in the worker (message, a tool call executed by the worker's own
executor, its result to the provider, the reply and ``finished``), every emit reaches the lead as an event in order,
each write of the chat's file is followed by one ``journal`` event and the file holds each row once, and the pending
calls go out as ``pending`` events whose digest is the one an approval must send back. Every ``chat.*`` request is
covered with its refusals: a credential in the settings, a folder that is not the lead's, a file with another writer,
an approval whose card is stale or comes during a merge hold, an older consent lease. Nothing starts without a person
unless the newest lease grants it. No credential source is read anywhere in the worker, and the provider is built
through the worker's one runtime transport. ``ANGELO_WORKER_CHAT_HOST=0`` leaves the worker as it was.
"""

import contextlib
import queue
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import session_journal, worker_chat_events, worker_chat_host, worker_chat_seams, worker_host  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402


class FakeEndpoint:
    def __init__(self):
        self.sent, self.lock, self.limit = [], threading.Lock(), None

    def send(self, value):
        from angelo_agent import external_cli_protocol as protocol
        if self.limit is not None and len(repr(value)) > self.limit:
            raise protocol.FrameError("too large")
        with self.lock:
            self.sent.append(value)

    def events(self, kind=None):
        with self.lock:
            found = [frame["event"] for frame in self.sent if "event" in frame]
        return [event for event in found if kind is None or event["kind"] == kind]

    def responses(self):
        with self.lock:
            return [frame for frame in self.sent if "response" in frame]


class Executor:
    def __init__(self):
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return {"objects": [{"name": "Cube"}], "success": True}

    def precondition(self, name, arguments):
        return "stable"


class FakeBlender:
    """The two operators the pump and an approval reach, run as the window's approve does for these calls."""

    def __init__(self, ui, executor):
        self.data = SimpleNamespace(is_dirty=False)
        self.context = SimpleNamespace(temp_override=lambda **kwargs: contextlib.nullcontext())
        self.approved = []

        def approve(_mode, _undo, call_id, session_id):
            chat = ui._controllers[session_id]
            self.approved.append(call_id)
            chat.execute_tool(chat.pending[call_id], executor, ui.document_key())
            return {"FINISHED"}
        self.ops = SimpleNamespace(angelo=SimpleNamespace(approve=approve, commit_native_job=lambda *a, **k: {"FINISHED"}))


class FakeProvider:
    instances, tools, gate = [], (("angelo_inspect_scene", {}),), None

    @classmethod
    def turned(cls):
        """The providers that ran a turn (an open with no models also lists the catalog through one)."""
        return [provider for provider in cls.instances if hasattr(provider, "results")]

    def __init__(self, context, settings):
        self.controller, self.settings, self.closed = context, dict(settings), False
        self.instances.append(self)

    def connect(self):
        self.process = self.controller.runtime_transport(session=self.controller.host.state["id"], provider_kind="codex")
        native = str(uuid.uuid4())
        self.controller.emit("native_session", {"id": native})
        return native

    def run(self, native_id, text, observation=None):
        self.results = []
        for name, arguments in self.tools:
            done = threading.Event()
            self.controller.request_tool(name, name, arguments, lambda result: (self.results.append(result), done.set()))
            if not done.wait(5):
                raise RuntimeError("The tool never answered")
        if self.gate is not None:
            self.gate.wait(5)
        if self.controller.cancelled:
            return "interrupted"
        self.controller.emit("message_complete", {"item": "reply", "text": "Made a cube for " + text})
        return "completed"

    def stop(self):
        if self.gate is not None:
            self.gate.set()

    def close(self):
        self.closed = True


class Trap:
    """A credential source: any use of it is a failure of the worker's boundary."""

    def __init__(self, name, tripped):
        self._name, self._tripped = name, tripped

    def __getattr__(self, attribute):
        self._tripped.append("%s.%s" % (self._name, attribute))
        raise AssertionError("A worker read a credential source: %s.%s" % (self._name, attribute))


class FakeUi(SimpleNamespace):
    @property
    def _memory_keys(self):
        self.tripped.append("ui._memory_keys")
        raise AssertionError("A worker read the lead's keys")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name) / "state"
        self.state.mkdir()
        self.tripped = []
        self.ui = FakeUi(Controller=Controller, _controllers={}, main_window=lambda: "window", tripped=self.tripped,
                         document_key=lambda: ("worker-doc", 1), state_directory=lambda: str(self.state))
        self.executor = Executor()
        self.bpy = FakeBlender(self.ui, self.executor)
        self.endpoint = FakeEndpoint()
        self.host = worker_host.WorkerHost(self.ui, self.bpy, self.endpoint, part=str(Path(self.folder.name) / "p.blend"))
        self.host.save = lambda: (setattr(self.bpy.data, "is_dirty", False), self.host.part)[1]
        self.host.layout = lambda: {"objects": ["Cube"]}
        self.launched = []
        self.claims, self.releases = [], []
        owner = SimpleNamespace(claim=lambda *args: self.claims.append(args) or self.claim_answer,
                                release=lambda *args: self.releases.append(args))
        self.claim_answer = True
        self.chats = self.host.chats = worker_chat_host.WorkerChatHost(
            self.host, ownership=owner, transport=self.transport, executor_factory=lambda: self.executor,
            outbox=worker_chat_events.Outbox(self.endpoint))
        self.snapshots = []
        self.chats._checkpoint = lambda chat: self.snapshots.append(chat) or None   # No Blender to snapshot.
        FakeProvider.instances, FakeProvider.tools, FakeProvider.gate = [], (("angelo_inspect_scene", {}),), None
        self.session = str(uuid.uuid4())
        self.patches = [patch("angelo_agent.controller.CodexProvider", FakeProvider),
                        patch("angelo_agent.controller.ClaudeProvider", FakeProvider)]
        for started in self.patches:
            started.start()

    def tearDown(self):
        self.chats.shutdown("test over")
        for started in self.patches:
            started.stop()
        self.folder.cleanup()

    def transport(self, **launch):
        self.launched.append(launch)
        return SimpleNamespace(pid=4242)

    def ask(self, op, **fields):
        return self.chats.handle(dict(fields, op=op, session=fields.get("session", self.session)))

    def open(self, **fields):
        fields.setdefault("store_directory", str(self.state))
        fields.setdefault("settings", {"model": "", "reuse_codex_login": True})
        return self.ask("chat.open", **fields)

    def run_turn(self, timeout=5):
        chat = self.chats.hosted(self.session)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.chats.pump()
            if not chat.busy and not (chat._worker and chat._worker.is_alive()):
                self.chats.pump()
                self.chats.outbox.flush()
                return chat
            time.sleep(0.002)
        self.fail("The hosted turn did not finish")


class HostedTurnTests(Fixture):
    def test_a_hosted_chat_runs_a_whole_turn_in_the_worker_and_the_lead_hears_all_of_it(self):
        self.assertEqual(self.open()["ok"], True)
        self.assertEqual(self.ask("chat.send", text="a cube", attachments=[]), {"ok": True})
        chat = self.run_turn()
        self.assertEqual(self.executor.calls, [("angelo_inspect_scene", {})])   # Run here, by the local executor.
        self.assertEqual(FakeProvider.turned()[0].results[0]["success"], True)   # Its result reached the provider.
        kinds = [event["kind"] for event in self.endpoint.events() if event["session"] == self.session]
        for kind in ("message", "native_session", "activity", "message_complete", "finished"):
            self.assertIn(kind, kinds)
        self.assertLess(kinds.index("message_complete"), kinds.index("finished"))
        finished = self.endpoint.events("finished")[-1]
        self.assertEqual(finished["data"]["status"], "completed")
        # The pending calls: the inspect call while it waited, then none.
        pending = [event["data"]["calls"] for event in self.endpoint.events("pending")]
        self.assertIn("angelo_inspect_scene", [call["name"] for calls in pending for call in calls])
        self.assertEqual(pending[-1], [])
        # Each write of the file is one journal event, in order, and the file holds each row exactly once.
        journal = self.endpoint.events("journal")
        self.assertEqual([event["data"]["seq"] for event in journal], list(range(1, len(journal) + 1)))
        self.assertEqual(journal[-1]["data"]["path"], str(chat.store.path))
        raw = session_journal.read(chat.store.path)[0]
        texts = [row.get("text") for row in raw["messages"]]
        self.assertEqual(texts.count("a cube"), 1)
        self.assertEqual(len([text for text in texts if str(text).startswith("Made a cube for a cube")]), 1)
        self.assertEqual(raw["messages"], chat.state["messages"])   # What the lead reads is what the chat holds.
        self.assertEqual(chat.store.path.parent, self.state / "sessions")   # The lead's own file.

    def test_the_provider_is_built_through_the_one_transport_and_holds_no_key(self):
        self.open()
        self.ask("chat.send", text="go", attachments=[])
        self.run_turn()
        provider = FakeProvider.instances[0]
        self.assertEqual(provider.controller.runtime_transport, self.transport)
        self.assertEqual(self.launched, [{"session": self.session, "provider_kind": "codex"}])
        self.assertEqual(provider.controller.api_key, "")
        self.assertNotIn("api_key", provider.settings)

    def test_no_credential_source_is_read_anywhere_in_a_hosted_turn(self):
        names = ("saved_keys", "key_store", "keychain_macos", "credential_manager_windows", "secret_service_linux",
                 "team_account", "team_reads", "cache_affinity_secret")
        traps = {name: Trap(name, self.tripped) for name in names}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"angelo_agent." + name: trap for name, trap in traps.items()}))
            for name, trap in traps.items():
                stack.enter_context(patch.object(angelo_agent, name, trap, create=True))
            self.open(consent={"epoch": 1, "grants": {"auto": True}})
            self.ask("chat.send", text="go", attachments=[])
            self.run_turn()
            self.ask("chat.close", reason="done")
        self.assertEqual(self.tripped, [])

    def test_a_hosted_chat_never_binds_a_key(self):
        self.open()
        with self.assertRaises(RuntimeError):
            self.chats.hosted(self.session).api_key = "sk-live"
        self.chats.hosted(self.session).api_key = ""   # Unbinding stays possible.

    def test_reopening_loads_the_lead_file_and_a_second_open_changes_nothing(self):
        lead = Controller(str(self.state), session_id=self.session)
        lead.state["messages"].append({"role": "user", "text": "earlier"})
        lead.save()
        lead.close()
        self.assertEqual(self.open(resume=True)["opened"], True)
        self.assertEqual(self.open(resume=True)["opened"], False)
        self.assertIn("earlier", [row.get("text") for row in self.chats.hosted(self.session).state["messages"]])
        self.assertEqual(self.claims, [(self.session, str(self.state / "sessions" / (self.session + ".json")), True)])
        self.assertIs(self.ui._controllers[self.session], self.chats.hosted(self.session))


class RequestRefusalTests(Fixture):
    def refused(self, op, **fields):
        with self.assertRaises(RefusalError) as caught:
            self.ask(op, **fields)
        self.assertIs(caught.exception.partial_changes_possible, False)
        return caught.exception

    def test_open_refuses_a_credential_in_the_settings(self):
        for settings in ({"api_key": "sk"}, {"model": "", "nested": {"apiKey": "sk"}}, {"Team-Grant": "g"},
                         {"auth": [{"token": "t"}]}):
            with self.subTest(settings=settings):
                self.refused("chat.open", store_directory=str(self.state), settings=settings)
        self.assertEqual((self.chats.chats, self.ui._controllers, self.claims), ({}, {}, []))

    def test_open_refuses_a_folder_that_is_not_the_lead_state_folder_or_a_bad_id(self):
        for directory in ("", "relative/state", str(self.state / "missing"), None):
            with self.subTest(directory=directory):
                self.refused("chat.open", store_directory=directory, settings={})
        self.refused("chat.open", session="not-a-chat-id", store_directory=str(self.state), settings={})
        self.assertEqual(self.chats.chats, {})

    def test_open_on_the_sessions_folder_means_its_state_folder(self):
        (self.state / "sessions").mkdir()
        self.open(store_directory=str(self.state / "sessions"))
        self.assertEqual(self.chats.hosted(self.session).store.path.parent, self.state / "sessions")

    def test_open_refuses_a_file_with_another_writer(self):
        self.claim_answer = False
        self.refused("chat.open", store_directory=str(self.state), settings={})
        self.assertEqual(self.chats.chats, {})

    def test_every_request_needs_a_hosted_chat_and_a_known_op(self):
        for op in ("chat.send", "chat.approve", "chat.decline", "chat.stop", "chat.options", "chat.consent",
                   "chat.hold", "chat.release"):
            with self.subTest(op=op):
                self.refused(op, text="x")
        # A close of a chat not hosted here (a retry whose first answer was lost) is answered: the lead takes it back.
        self.assertEqual(self.ask("chat.close", reason="retry"), {"ok": True, "closed": False})
        for op in ("chat.bogus", "chat._end", "chat.", "open", None):
            with self.subTest(op=op), self.assertRaises(ValueError):
                self.chats.handle({"op": op, "session": self.session})

    def test_send_refuses_what_is_not_a_message(self):
        self.open()
        self.refused("chat.send", text=7, attachments=[])
        self.refused("chat.send", text="x", attachments="file.png")
        self.refused("chat.options", options=["fast"])


class ApprovalTests(Fixture):
    def pending_call(self, name="angelo_create_cube", arguments=None):
        """A call waiting for a person, as the pump leaves one in Ask."""
        self.open(options={"approval": "ASK"})
        chat = self.chats.hosted(self.session)
        chat.checkpoint_factory = None
        FakeProvider.tools = ((name, arguments or {"name": "Box", "size": 1.0, "location": [0, 0, 0]}),)
        FakeProvider.gate = threading.Event()
        self.ask("chat.send", text="a box", attachments=[])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.chats.pump()
            waiting = [call for call in chat.pending.values() if call.approval]
            if waiting:
                self.chats.outbox.flush()
                return chat, waiting[0]
            time.sleep(0.002)
        self.fail("No call waited for approval")

    def drawn_digest(self, call):
        cards = [call_ for event in self.endpoint.events("pending") for call_ in event["data"]["calls"]
                 if call_["id"] == call.id]
        self.assertTrue(cards[-1]["needs_person"])
        return cards[-1]["digest"]

    def finish(self, chat):
        for call in list(chat.pending.values()):   # Whatever still waits for a person is declined.
            self.ask("chat.decline", call_id=call.id)
        FakeProvider.gate.set()
        self.run_turn()

    def test_an_approval_with_the_drawn_digest_runs_the_call_once(self):
        chat, call = self.pending_call()
        self.assertEqual(self.ask("chat.approve", call_id=call.id, digest=self.drawn_digest(call)),
                         {"ok": True, "approved": True})
        self.assertEqual(self.bpy.approved, [call.id])
        self.assertEqual([name for name, _ in self.executor.calls], ["angelo_create_cube"])
        self.finish(chat)

    def test_a_forged_digest_runs_nothing_and_ends_the_call_stale(self):
        """worker_consent.check_approval: a digest that is not the drawn one ends the call STALE_REVISION (the model
        gets a fresh observation), and nothing can approve it afterwards."""
        for forged in ("0" * 64, "", None, 7):
            with self.subTest(digest=forged):
                chat, call = self.pending_call()
                drawn = self.drawn_digest(call)
                with self.assertRaises(RefusalError) as caught:
                    self.ask("chat.approve", call_id=call.id, digest=forged)
                self.assertEqual(caught.exception.code, "STALE_REVISION")
                with self.assertRaises(RefusalError):
                    self.ask("chat.approve", call_id=call.id, digest=drawn)   # The call is no longer waiting.
                self.assertEqual((self.bpy.approved, self.executor.calls), ([], []))
                self.assertEqual(FakeProvider.instances[-1].results[0]["error_code"], "STALE_REVISION")
                self.finish(chat)
                self.ask("chat.close", reason="next")

    def test_a_call_changed_after_its_card_was_drawn_is_not_approved(self):
        chat, call = self.pending_call()
        drawn = self.drawn_digest(call)
        call.arguments["size"] = 9.0   # Changed after its card was drawn.
        with self.assertRaises(RefusalError) as caught:
            self.ask("chat.approve", call_id=call.id, digest=drawn)
        self.assertEqual(caught.exception.code, "STALE_REVISION")
        self.assertEqual((self.bpy.approved, self.executor.calls), ([], []))
        self.finish(chat)

    def test_decline_finishes_the_call_declined(self):
        chat, call = self.pending_call()
        self.ask("chat.decline", call_id=call.id)
        self.assertNotIn(call.id, chat.pending)
        self.assertEqual(FakeProvider.turned()[0].results[0]["error_code"], "DECLINED")
        self.assertEqual(self.executor.calls, [])
        self.finish(chat)

    def test_a_merge_hold_waits_for_a_call_under_way_and_takes_no_approval(self):
        chat, call = self.pending_call()
        drawn = self.drawn_digest(call)
        call.executing = True   # Under way: the hold is answered only once it is not.
        self.assertTrue(self.chats.defer(7, self.ask("chat.hold")))
        self.chats.pump()
        self.assertEqual(self.endpoint.responses(), [])
        with self.assertRaises(RefusalError):
            self.ask("chat.approve", call_id=call.id, digest=drawn)
        self.assertFalse(chat._auto_bounded(call, "AUTO_BOUNDED"))   # Nothing starts by itself meanwhile.
        call.executing = False
        self.chats.pump()
        answer = self.endpoint.responses()[-1]
        self.assertEqual((answer["seq"], answer["response"]["ok"], chat.held), (7, True, True))
        self.ask("chat.release")
        self.assertFalse(chat.holding or chat.held)
        self.ask("chat.approve", call_id=call.id, digest=drawn)
        self.assertEqual(self.bpy.approved, [call.id])
        self.finish(chat)

    def test_a_hold_that_cannot_be_taken_in_time_is_refused_and_not_kept(self):
        chat, call = self.pending_call()
        call.executing = True
        self.chats.defer(3, self.ask("chat.hold"))
        self.chats._waiting[3].deadline = time.monotonic() - 1
        self.chats.pump()
        answer = self.endpoint.responses()[-1]["response"]
        self.assertEqual(answer["error"]["code"], "WRITER_BUSY")
        self.assertFalse(chat.holding or chat.held)
        call.executing = False
        self.finish(chat)


class ConsentTests(Fixture):
    def call(self, name="angelo_create_cube"):
        return SimpleNamespace(id="c", name=name, arguments={"connection_id": None, "model": ""}, document=None,
                               script_consent=None)

    def test_nothing_starts_without_a_person_until_the_newest_lease_grants_it(self):
        self.open(options={"approval": "AUTO_BOUNDED"})
        chat = self.chats.hosted(self.session)
        self.assertFalse(chat._auto_bounded(self.call(), "AUTO_BOUNDED"))   # No lease yet.
        self.assertEqual(self.ask("chat.consent", epoch=1, grants={"auto": True}), {"ok": True, "epoch": 1})
        self.assertTrue(chat._auto_bounded(self.call(), "AUTO_BOUNDED"))
        self.assertFalse(chat._automatic_script(self.call("angelo_execute_python")))   # Not granted.
        self.ask("chat.consent", epoch=2, grants={"auto": False})   # Revoked.
        self.assertFalse(chat._auto_bounded(self.call(), "AUTO_BOUNDED"))
        older = self.ask("chat.consent", epoch=1, grants={"auto": True})   # Older than the one held: refused.
        self.assertEqual((older["ok"], older["error_code"], older["epoch"]), (False, "STALE_REVISION", 2))
        self.assertFalse(chat._auto_bounded(self.call(), "AUTO_BOUNDED"))
        self.assertEqual(self.ask("chat.consent", epoch="3", grants={"auto": True})["ok"], False)
        chat.holding = True   # A merge hold: nothing starts by itself whatever the lease says.
        self.ask("chat.consent", epoch=5, grants={"auto": True})
        self.assertFalse(chat._auto_bounded(self.call(), "AUTO_BOUNDED"))

    def test_a_lease_sent_with_open_holds_from_the_start(self):
        self.open(consent={"epoch": 4, "grants": {"auto": True, "scripts": "n-1", "helper": True}})
        chat = self.chats.hosted(self.session)
        self.assertEqual((chat.consent_lease.epoch, chat.consent_lease.grants()), (4, {"auto": True, "scripts": "n-1"}))

    def test_an_open_whose_lease_is_malformed_is_refused(self):
        with self.assertRaises(RefusalError):
            self.open(consent={"epoch": -1, "grants": {"auto": True}})
        self.assertIsNone(self.chats.hosted(self.session))

    def test_options_reach_the_chat(self):
        self.open()
        answer = self.ask("chat.options", options={"approval": "ASK", "effort": "high"})
        self.assertEqual((answer["options"]["approval"], answer["options"]["effort"]), ("ASK", "high"))


class LifecycleTests(Fixture):
    def test_stop_ends_a_running_turn_interrupted(self):
        self.open()
        FakeProvider.tools, FakeProvider.gate = (), threading.Event()
        self.ask("chat.send", text="long", attachments=[])
        self.chats.pump()
        self.assertEqual(self.ask("chat.stop", reason=""), {"ok": True})
        self.run_turn()
        self.assertEqual(self.endpoint.events("finished")[-1]["data"]["status"], "interrupted")

    def test_close_seals_the_file_releases_it_and_answers_the_last_write(self):
        self.open()
        self.ask("chat.send", text="go", attachments=[])
        chat = self.run_turn()
        answer = self.ask("chat.close", reason="parked")
        self.assertEqual(answer, {"ok": True, "closed": True, "seq": chat.journal_writes, "was_busy": False})
        self.assertEqual(self.endpoint.events("journal")[-1]["data"]["seq"], chat.journal_writes)
        self.assertEqual(self.endpoint.events("journal")[-1]["data"]["records"], 0)   # Compacted: one base line.
        self.assertEqual((self.chats.chats, self.ui._controllers), ({}, {}))
        self.assertEqual(self.releases, [(self.session, str(chat.store.path))])
        with self.assertRaises(RefusalError):
            self.ask("chat.send", text="again", attachments=[])

    def test_the_lead_going_away_closes_every_hosted_chat(self):
        self.open()
        self.host.inbox.put(None)
        self.assertIsNone(self.chats.next_request())
        self.assertEqual((self.chats.chats, len(self.releases)), ({}, 1))

    def test_the_part_is_saved_once_nothing_is_under_way_and_its_layout_sent_when_it_changed(self):
        self.open()
        for _ in range(2):
            self.bpy.data.is_dirty = True
            self.chats.pump()
        self.chats.outbox.flush()
        saved = self.endpoint.events("part_saved")
        self.assertEqual([event["data"]["layout"] for event in saved], [{"objects": ["Cube"]}, None])
        self.assertEqual(self.bpy.data.is_dirty, False)


    def test_a_failed_save_is_told_and_tried_again_later(self):
        self.open()
        attempts = []

        def fail():
            attempts.append(1)
            raise RuntimeError("The worker could not save its part file")
        self.host.save = fail
        self.bpy.data.is_dirty = True
        self.chats.pump()
        self.chats.pump()   # Within the retry delay: not tried again.
        self.chats.outbox.flush()
        self.assertEqual(len(attempts), 1)
        self.assertIn("could not save", self.endpoint.events("part_save_failed")[0]["data"]["error"])
        self.chats._save_after = 0.0
        self.chats.pump()
        self.assertEqual(len(attempts), 2)

    def test_an_error_in_one_pump_never_ends_the_loop(self):
        self.open()
        calls = []

        def broken(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("a damaged value")
            return 0
        self.chats.pump_tools = broken
        self.host.inbox.put((1, {"op": "hello"}))
        with patch("angelo_agent.pump_guard.report") as reported:
            self.assertEqual(self.chats.next_request(), (1, {"op": "hello"}))
            self.host.inbox.put((2, {"op": "hello"}))
            self.assertEqual(self.chats.next_request(), (2, {"op": "hello"}))
        self.assertEqual(reported.call_count, 1)
        self.assertEqual(len(calls), 2)


    def test_a_request_snapshot_is_of_the_part_into_the_chats_worker_snapshot_store(self):
        from angelo_agent.worker_routing import worker_snapshot_session
        del self.chats._checkpoint   # The real one, over a stand-in for the worker's capture.
        self.open()
        chat = self.chats.hosted(self.session)
        asked = []
        self.host.checkpoint = lambda request: asked.append(request) or {"id": "snap-1"}
        with patch("angelo_agent.snapshot_service.after_capture") as after:
            self.assertEqual(chat.checkpoint_factory(), {"id": "snap-1"})
        self.assertEqual(asked[0]["session"], worker_snapshot_session(self.session))
        self.assertEqual((asked[0]["purpose"], after.call_args[0]), ("request", (chat, {"id": "snap-1"})))


class WorkerHostTests(Fixture):
    def test_run_answers_chat_requests_and_pumps_between_them(self):
        self.host.inbox.put((1, {"op": "chat.open", "session": self.session, "store_directory": str(self.state),
                                 "settings": {}}))
        self.host.inbox.put((2, {"op": "chat.hold", "session": self.session}))
        self.host.inbox.put((3, {"op": "execute", "session": self.session, "name": "angelo_inspect_scene"}))
        self.host.inbox.put(None)
        self.host.run()
        answers = {frame["seq"]: frame["response"] for frame in self.endpoint.responses()}
        self.assertEqual((answers[1]["ok"], answers[2]["held"]), (True, True))
        self.assertEqual(answers[1]["document"], ["worker-doc", 1])
        self.assertEqual(answers[3]["error"]["code"], "UNSUPPORTED")   # No shadow requests while it hosts the chat.
        self.assertEqual(self.chats.chats, {})   # Closed when the lead went away.

    def test_the_reader_hands_reverse_answers_to_the_relay(self):
        frames = queue.Queue()
        for frame in ({"rseq": 1, "response": {"relay": "fd:9"}}, {"seq": 5, "request": {"op": "hello"}}):
            frames.put(frame)
        from angelo_agent.headless_pipe import PipeClosed

        def receive():
            if frames.empty():
                raise PipeClosed()
            return frames.get()
        self.host.endpoint = SimpleNamespace(receive=receive)
        heard = []
        self.chats.on_reverse = heard.append
        self.host._read()
        self.assertEqual(heard, [{"rseq": 1, "response": {"relay": "fd:9"}}])
        self.assertEqual((self.host.inbox.get(), self.host.inbox.get()), ((5, {"op": "hello"}), None))

    def test_a_shadow_request_still_reaches_a_shadow_with_the_switch_off(self):
        self.host.chats = None
        self.assertEqual(self.host.handle({"op": "hello"}), {"value": True})
        with patch.object(self.host, "executor", return_value=SimpleNamespace(
                precondition=lambda name, arguments: "bound")):
            answer = self.host.handle({"op": "precondition", "session": self.session, "name": "angelo_inspect_scene",
                                       "arguments": {}})
        self.assertIn("value", answer)
        self.assertIn(self.session, self.host.shadows)

    def test_the_switch(self):
        self.assertTrue(worker_chat_seams.enabled({}))
        self.assertTrue(worker_chat_seams.enabled({"ANGELO_WORKER_CHAT_HOST": "1"}))
        self.assertFalse(worker_chat_seams.enabled({"ANGELO_WORKER_CHAT_HOST": "0"}))
        source = (ADDON / "worker_main.py").read_text(encoding="utf-8")
        self.assertIn("if worker_chat_seams.enabled():", source)

    def test_without_the_relay_no_model_child_can_start(self):
        transport = worker_chat_seams.runtime_transport()   # Nothing installed the relay in this process.
        self.assertIs(transport, worker_chat_seams.unavailable_transport)
        with self.assertRaises(RuntimeError) as caught:
            transport(["codex", "app-server"], env={})
        self.assertIs(caught.exception.partial_changes_possible, False)
        self.chats.transport = None   # As worker_main makes it: the relay's launcher, looked up at each open.
        with self.assertRaises(RefusalError):
            self.open()
        self.assertIsNone(self.chats.hosted(self.session))


if __name__ == "__main__":
    unittest.main()
