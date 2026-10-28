# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Design A′ end to end, without Blender: Angelo's real lead side and a worker's real chat host in one process.

The lead is a real ``WorkerProcess`` whose ``worker_channel.Client`` is built as ``_launch`` builds it
(``worker_chat_wiring.attach``: the event router and the runtime service), a real ``MirrorController`` made by
``mirror_of`` and opened by ``worker_chat_lifecycle.open_chat``, the real consent lane (``worker_consent_lead``)
and the real journal owner. The worker is a real ``WorkerHost`` with a real ``WorkerChatHost`` on the other end of a
Unix socket pair, its model runtime launched through the real relay (``runtime_relay``) by the lead's
``RuntimeService`` as a separate process (``runtime_relay_fake_child``, a JSON-lines stand-in). Only Blender is
faked: the executor and the approve operator, and the provider's Python, which speaks to the relayed child.

Pinned: open, send, a read run by the worker's executor, an approval card drawn in the mirror from the worker's
digest, Approve, the turn finished and the mirror equal to the file; the file has one writer while hosted and
comes back on close; usage reaches the lead's writer once and none is written by the worker; Stop with a wedged
worker kills the model child; a worker crash leaves its admitted call interrupted and the mirror's turn ended, also when the worker dies on its own
with nobody asking it anything (its binding crash-marked);
a consent revocation is acknowledged, and an unacknowledged one kills the child and marks the chat; the key never
crosses to the worker.
"""

import contextlib
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import (cli_runtime, runtime_relay, session_journal, worker_channel, worker_chat_events,  # noqa: E402
                          worker_chat_host, worker_chat_lifecycle as lifecycle, worker_chat_wiring, worker_consent_lead,
                          worker_host, worker_journal_owner, worker_process, worker_usage)
from angelo_agent.chat_settings import normalize_models  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.protocol import JsonRpcProcess  # noqa: E402

KEY = "sk-e2e-" + "q" * 40
POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "The relay's descriptor passing is POSIX; Windows uses pipes")


def wait_for(predicate, timeout=10.0, pump=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pump is not None:
            pump()
        if predicate():
            return True
        time.sleep(0.005)
    return False


class Executor:
    def __init__(self):
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append(name)
        return {"objects": [{"name": "Cube"}], "success": True}

    def precondition(self, name, arguments):
        return "stable"


class FakeBlender:
    """The approve operator a worker's pump and ``chat.approve`` reach, run as the window's does for these calls."""

    def __init__(self, ui, executor):
        self.data = SimpleNamespace(is_dirty=False)
        self.context = SimpleNamespace(temp_override=lambda **kwargs: contextlib.nullcontext())

        def approve(_mode, _undo, call_id, session_id):
            chat = ui._controllers[session_id]
            chat.execute_tool(chat.pending[call_id], executor, ui.document_key())
            return {"FINISHED"}
        self.ops = SimpleNamespace(angelo=SimpleNamespace(approve=approve, commit_native_job=lambda *a, **k: {"FINISHED"}))


class RelayedProvider:
    """The provider Python in the worker: it starts its runtime through ``cli_runtime.launch`` (relayed to Angelo),
    asks the child's tool call of the chat, then one edit that needs the person, and replies."""

    instances, gate = [], None

    def __init__(self, context, settings):
        self.context, self.settings, self.events, self.requests = context, dict(settings), [], []
        self.instances.append(self)

    def start_prewarm(self, owner):
        return False   # The worker's open asks for one (``WorkerChatHost._warm``); this stand-in starts none.

    def connect(self):
        workspace = str(self.context.workspace)
        self.rpc = JsonRpcProcess(["codex", "app-server", "--listen", "stdio://"], env={}, cwd=workspace,
                                  spawn=cli_runtime.launch, on_event=lambda m, p: self.events.append((m, p)),
                                  on_request=lambda identity, m, p: self.requests.append((identity, m, p)))
        self.initialized = self.rpc.request("initialize", {"clientInfo": {"name": "angelo_blender"}})
        self.rpc.notify("initialized")
        self.account = self.rpc.request("account/read", {"refreshToken": False})
        self.rpc.request("thread/start", {"cwd": workspace, "sandbox": "read-only", "approvalPolicy": "on-request",
                                          "modelProvider": "openai"})
        return "native-" + uuid.uuid4().hex

    def _tool(self, name, arguments):
        done, results = threading.Event(), []
        self.context.request_tool(name, name, arguments, lambda result: (results.append(result), done.set()))
        if not done.wait(20):
            raise RuntimeError("The tool never answered")
        return results[0]

    def run(self, native_id, text, observation=None):
        if not wait_for(lambda: self.requests, 10):
            raise RuntimeError("The runtime asked nothing")
        identity, _method, params = self.requests[0]
        self.rpc.respond(identity, self._tool(params["tool"], {}))   # The child's own call, answered over the relay.
        self.edit = self._tool("angelo_create_cube", {"name": "Box", "size": 1.0, "location": [0, 0, 0]})
        if self.gate is not None:
            self.gate.wait(20)
        if self.context.cancelled:
            return "interrupted"
        self.context.emit("message_complete", {"item": "reply", "text": "Made it for " + text})
        return "completed"

    def stop(self):
        if self.gate is not None:
            self.gate.set()

    def close(self):
        rpc = getattr(self, "rpc", None)
        if rpc is not None:
            rpc.close()


class FakeProcess:
    def __init__(self):
        self.pid, self.code = os.getpid(), None

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


@POSIX_ONLY
class Fixture(unittest.TestCase):
    """The lead and the worker over a socket pair (module docstring); its tests are ``EndToEnd`` and
    ``test_native_worker_chat_e2e_flows``."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.state = self.folder / "state"
        self.state.mkdir()
        fake = self.folder / "fake-runtime"
        fake.write_text("#!%s\n" % sys.executable + (Path(__file__).parent / "runtime_relay_fake_child.py").read_text())
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.clock = [1000.0]
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.applied = []
        for patcher in (mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}),
                        mock.patch.object(lifecycle, "_hosted", {}),
                        mock.patch.object(lifecycle, "host_available", lambda: True),
                        mock.patch.object(worker_chat_wiring, "key_of", lambda mirror: KEY),   # Angelo's own key.
                        mock.patch.object(worker_usage, "apply", lambda event, directory: self.applied.append(
                            (json.loads(json.dumps(event)), directory))),
                        mock.patch("angelo_agent.controller.CodexProvider", RelayedProvider),
                        mock.patch("angelo_agent.controller.ClaudeProvider", RelayedProvider)):
            stack.enter_context(patcher)
        worker_consent_lead.reset()
        worker_consent_lead.bind(connection=lambda controller: True, clock=lambda: self.clock[0])
        self.addCleanup(worker_consent_lead.reset)
        RelayedProvider.instances, RelayedProvider.gate = [], None
        self.session = str(uuid.uuid4())
        # The worker: a real host and chat host on one end of the pair.
        listener = worker_channel.SocketPairListener()
        worker_end = worker_channel.connect("fd:%d" % os.dup(listener.pass_fds()[0]))
        listener.started()
        lead_end = listener.accept(None)
        self.executor = Executor()
        self.ui = SimpleNamespace(Controller=Controller, _controllers={}, main_window=lambda: "window",
                                  document_key=lambda: ("worker-doc", 1), state_directory=lambda: str(self.folder))
        self.host = worker_host.WorkerHost(self.ui, FakeBlender(self.ui, self.executor), worker_end,
                                           part=str(self.folder / "part.blend"))
        self.host.save = lambda: self.host.part
        self.host.layout = lambda: {"objects": ["Cube"], "revision": 0}
        self.chats = self.host.chats = worker_chat_host.WorkerChatHost(
            self.host, executor_factory=lambda: self.executor, outbox=worker_chat_events.Outbox(worker_end))
        self.chats._checkpoint = lambda chat: None   # No Blender to snapshot.
        runtime_relay.install(worker_end, sessions=lambda: list(self.chats.chats))
        worker_usage.install(lambda event: self.chats.outbox.put(dict(event, t=time.time())))
        self.chats.usage_required = True
        self.addCleanup(worker_usage.uninstall)
        self.addCleanup(runtime_relay.uninstall)
        self.host.start_reader()
        self.wedge = threading.Event()
        self.wedge.set()
        pump, handle = self.chats.pump, self.host.handle   # Cleared: a frozen worker, which answers nothing.
        self.chats.pump = lambda: (self.wedge.wait(), pump())
        self.host.handle = lambda request: (self.wedge.wait(), handle(request))[1]
        self.reader_wedge = threading.Event()   # Cleared too: the reader thread frozen as well (nothing arrives).
        self.reader_wedge.set()
        arrived = self.chats.arrivals.arrived
        self.chats.arrivals.arrived = lambda seq, request: (self.reader_wedge.wait(), arrived(seq, request))[1]
        self.loop = threading.Thread(target=self.host.run, daemon=True, name="FakeWorkerMain")
        self.loop.start()
        # The lead: a real WorkerProcess whose Client is built as ``_launch`` builds it.
        self.worker = worker_process.WorkerProcess(self.folder / "part.blend")
        self.worker.process = FakeProcess()
        self.worker.client = worker_channel.Client(lead_end, on_ended=self.worker._connection_ended,
                                                   **self.worker._chat_channel())
        self.worker.runtime.executable = lambda kind, settings: str(fake)
        self.addCleanup(self._clean)

    def _clean(self):
        self.wedge.set()
        self.reader_wedge.set()
        if self.worker.alive():
            with contextlib.suppress(Exception):
                lifecycle.close_chat(self.session, "test over")
        self.worker.runtime.close()
        self.worker.client.close()
        self.loop.join(5)

    # The lead's side, as the window runs it --------------------------------------------------------------------

    def open(self, approval="ASK"):
        lead = Controller(str(self.state), session_id=str(uuid.uuid4()))
        chat = Controller(str(self.state), session_id=self.session)
        connection = str(uuid.uuid4())
        chat.state.update(provider="codex_api", connection_id=connection, options=dict(chat.state["options"],
                                                                                     approval=approval))
        chat.settings = {"auth_directory": str(self.folder), "connection_id": connection, "reuse_codex_login": False,
                         "model": ""}
        chat.save()
        chat.models = normalize_models([{"model": "m", "isDefault": True}])   # As a chat of Angelo's loaded it.
        self.mirror = worker_chat_wiring.mirror_of(chat, lead)
        self.mirror.remote_executor = SimpleNamespace(part_layout=None, note_layout=lambda layout: None)
        lifecycle.open_chat(self.mirror, self.worker, resume=False)
        return self.mirror

    def pump(self):
        self.mirror.pump_events()
        worker_consent_lead.tick()

    def card(self):
        self.assertTrue(wait_for(lambda: any(c.needs_person for c in self.mirror.pending.values()), pump=self.pump),
                        "No card was drawn")
        return next(c for c in self.mirror.pending.values() if c.needs_person)

    def child(self):
        children = self.worker.runtime.children()
        self.assertEqual(len(children), 1)
        return children[0]


@POSIX_ONLY
class EndToEnd(Fixture):

    def test_a_hosted_turn_runs_in_the_worker_and_the_lead_mirrors_it(self):
        mirror = self.open()
        self.assertTrue(worker_journal_owner.hosted(self.session))
        with self.assertRaises(session_journal.JournalHosted):   # One writer while hosted: never the lead.
            Controller(str(self.state), session_id=self.session).store.save(mirror.state)
        built = len(RelayedProvider.instances)
        mirror.refresh_models()   # The window's picker and sign-in sweep ask every chat: nothing starts here.
        self.assertEqual(len(RelayedProvider.instances), built)
        mirror.send("a box")
        card = self.card()
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene"])   # The child's call, run by the worker.
        self.assertEqual(card.name, "angelo_create_cube")
        worker_chat = self.chats.hosted(self.session)
        from angelo_agent import worker_consent
        self.assertEqual(card.approval_digest, worker_consent.approval_digest(worker_chat.pending[card.id]))
        worker_consent_lead.drew(mirror, card.id)   # The card is drawn: its digest is what Approve sends.
        self.assertIsNone(worker_consent_lead.approve(mirror, card.id))   # The card's digest goes back.
        self.assertTrue(wait_for(lambda: not mirror.busy and mirror.turn_outcome, pump=self.pump), "Never finished")
        self.assertEqual(self.executor.calls, ["angelo_inspect_scene", "angelo_create_cube"])
        self.assertEqual(mirror.turn_outcome[0], "completed")
        self.assertTrue(wait_for(lambda: any(str(m.get("text")).startswith("Made it for a box") for m in
                                             session_journal.read(mirror.store.path)[0]["messages"]), pump=self.pump))
        # The file's last write reaches the mirror with its ``journal`` event, which may still be on its way.
        self.assertTrue(wait_for(lambda: mirror.state["messages"] == session_journal.read(mirror.store.path)[0][
            "messages"], pump=self.pump), "The mirror never matched its file")
        # The key stayed in Angelo: the worker's provider and chat never saw it, though the lead signed the child in.
        provider = next(p for p in RelayedProvider.instances if hasattr(p, "rpc"))   # The turn's (the open's prewarm asks one too).
        self.assertNotIn(KEY, json.dumps([provider.initialized, provider.account, provider.events]))
        self.assertEqual(worker_chat.api_key, "")
        self.assertNotIn(KEY, json.dumps(provider.settings))
        # Usage: forwarded to the lead's writer, in Angelo's own folder, each record once; none written here.
        self.assertTrue(self.applied, "No usage or telemetry reached the lead")
        ids = [event["data"]["id"] for event, _directory in self.applied]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({directory for _event, directory in self.applied}, {str(self.state)})
        self.assertFalse((self.state / "usage.json").exists() or (self.state / "performance.json").exists())
        # Close hands the file back: a store that loads again may write it.
        self.assertTrue(lifecycle.close_chat(self.session, "done"))
        self.assertFalse(worker_journal_owner.hosted(self.session))
        again = Controller(str(self.state), session_id=self.session)
        again.save()
        again.close()

    def test_checks_that_need_the_key_run_in_the_lead(self):
        from angelo_agent import claude_transport
        from angelo_agent.protocol import RpcError
        mirror = self.open()
        mirror._pins["provider"] = "claude_api"   # As Angelo's own record names the chat's connection.
        claude_transport.check_access("claude_api", "", "claude", {})   # The worker has no key; Angelo has.
        with mock.patch.object(worker_chat_wiring, "key_of", lambda mirror: ""), self.assertRaises(RpcError) as caught:
            claude_transport.check_access("claude_api", "", "claude", {})
        self.assertIn("Claude API key", str(caught.exception))

    def test_stop_with_a_wedged_worker_kills_the_model_child(self):
        mirror = self.open()
        RelayedProvider.gate = threading.Event()
        mirror.send("a box")
        self.card()
        child = self.child()
        self.wedge.clear()   # The worker stops answering, as a frozen worker does (its reader too).
        self.reader_wedge.clear()
        mirror.stop()
        self.assertTrue(child.alive())
        self.clock[0] += 1.99
        worker_consent_lead.tick()
        self.assertTrue(child.alive())   # Not before the deadline.
        self.clock[0] += 0.02
        worker_consent_lead.tick()
        self.assertTrue(wait_for(lambda: not child.alive()), "The model child outlived the Stop deadline")

    def test_a_worker_crash_leaves_its_admitted_call_interrupted_and_ends_the_turn(self):
        mirror = self.open()
        RelayedProvider.gate = threading.Event()
        mirror.send("a box")
        card = self.card()
        self.wedge.clear()   # Frozen, then gone: it writes nothing more.
        self.worker.process.code = 1
        self.worker.stop(grace=0.1)
        self.assertTrue(wait_for(lambda: not mirror.busy, pump=self.pump), "The mirror's turn never ended")
        self.assertFalse(worker_journal_owner.hosted(self.session))
        results = session_journal.read(mirror.store.path)[0]["results"]
        self.assertIn(card.id, results)
        self.assertNotEqual(results[card.id].get("phase"), "completed")
        self.assertEqual(mirror.turn_outcome[0], "interrupted")
        self.assertEqual(mirror.pending, {})

    def test_a_worker_that_dies_on_its_own_ends_the_turn_and_crash_marks_it_with_no_further_contact(self):
        mirror = self.open()
        died = []
        mirror.remote_executor = SimpleNamespace(part_layout=None, note_layout=lambda layout: None, worker=self.worker,
                                                 worker_id="w1", _died=died.append)
        self.worker.watch()   # As ``_launch`` starts it for a worker that hosts chats.
        RelayedProvider.gate = threading.Event()
        mirror.send("a box")
        card = self.card()
        self.wedge.clear()   # Frozen, then gone; nobody asks it anything or stops it (FE D5).
        self.worker.process.code = 1
        self.assertTrue(wait_for(lambda: not mirror.busy and died, pump=self.pump), "The dead worker kept its turn")
        self.assertFalse(worker_journal_owner.hosted(self.session))
        self.assertNotEqual(session_journal.read(mirror.store.path)[0]["results"][card.id].get("phase"), "completed")
        self.assertEqual(mirror.turn_outcome[0], "interrupted")
        self.assertEqual(len(died), 1)
        self.assertIn("ended on its own", died[0])
        self.assertEqual(mirror.remote_executor.document_key, ("angelo-worker", "w1", "ended"))
        self.assertTrue(self.worker.runtime.closed)
        self.worker.stop(grace=0.1)   # A later stop of Angelo's: the ended path already ran, so nothing runs twice.
        self.assertEqual(len(died), 1)

    def test_a_consent_revocation_is_acknowledged_and_an_unacknowledged_one_fails_closed(self):
        mirror = self.open(approval="AUTO_BOUNDED")
        worker_chat = self.chats.hosted(self.session)
        self.assertTrue(worker_chat.consent_lease.granted_auto())   # The open's lease, acknowledged by the open.

        settled = worker_consent_lead.tick   # The UI timer: a widening is sent once the worker confirmed it.

        def held(auto):
            """The worker holds ``auto`` and the lead has read every answer: a lease's acknowledgement is read on the
            lead after the worker applied it, so a wait on the worker alone may leave an older one as the newest the
            lead read (the round 2 flake: a later widening then looked acknowledged before it was sent)."""
            return (worker_chat.consent_lease.granted_auto() is auto and not worker_consent_lead._inflight.get(
                self.session) and worker_consent_lead._acked[self.session][1].get("auto") is auto)
        mirror.set_options(approval="ASK")   # The person narrows it: the toggle returns at once.
        worker_consent_lead.changed()
        self.assertTrue(wait_for(lambda: held(False), pump=settled))
        self.assertFalse(worker_consent_lead.needs_approval(self.session))
        mirror.set_options(approval="AUTO_BOUNDED")
        worker_consent_lead.changed()   # A widening, acknowledged too.
        self.assertTrue(wait_for(lambda: held(True), pump=settled))
        RelayedProvider.gate = threading.Event()
        mirror.send("a box")
        self.assertTrue(wait_for(lambda: self.worker.runtime.children(), pump=self.pump))
        child = self.child()
        self.wedge.clear()   # Its loop busy (an edit, a save): the revocation still holds and is acknowledged as it
        mirror.set_options(approval="ASK")   # arrives (worker_chat_arrival, break-it S2).
        worker_consent_lead.changed()   # Returns at once (FC2); the reader applies it while the loop is busy.
        self.assertTrue(wait_for(lambda: held(False), pump=settled))
        self.assertFalse(worker_consent_lead.needs_approval(self.session))
        self.wedge.set()
        mirror.set_options(approval="AUTO_BOUNDED")
        worker_consent_lead.changed()
        self.assertTrue(wait_for(lambda: held(True), pump=settled))   # Acknowledged, as read.
        self.wedge.clear()   # Frozen whole, its reader too: the next revocation is never acknowledged.
        self.reader_wedge.clear()
        mirror.set_options(approval="ASK")
        worker_consent_lead.changed()   # Returns at once; the UI timer finds its 2 s passed unanswered.
        self.clock[0] += worker_consent_lead.ACK_SECONDS
        worker_consent_lead.tick()
        self.assertTrue(worker_consent_lead.needs_approval(self.session))
        self.assertTrue(wait_for(lambda: not child.alive()), "An unacknowledged revocation left the child running")
        self.assertTrue(worker_chat.consent_lease.granted_auto())   # The frozen worker never applied it ...
        self.assertTrue(worker_consent_lead.stopping(self.session))   # ... so its turn was stopped (fail closed).


if __name__ == "__main__":
    unittest.main()
