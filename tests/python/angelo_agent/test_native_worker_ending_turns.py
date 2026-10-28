# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every route that ends a worker ends its chat's model request as the person's Stop does (``worker_routing.end_turn``).

The maintainer's audit (P1): a document replacement stopped the worker Blenders but left their chats' model requests
running (a real controller after the cleanup: busy, not cancelled, no provider stop, its request context live), so
quota went on parts that could never merge. Here a real ``Controller`` is bound to a stand-in worker and has a request
in flight with a stand-in provider (no process, no model) that only ends when it is stopped. For each route that ends
the worker (a load, a lead invalidated with no load as another scene is shown, a worker that crashed, a merge, the
add-on ending), and for the person's Stop as the reference: the provider's stop is called, the turn ends, no busy
flag stays set, the request context is closed, and the chat says why. The break-it review of 3fe25af2db5 added: a
message of the person's being prepared or queued on the chat's lane when its worker ends comes back where they type
with the reason in the chat (M-1); a turn that already ended keeps how it ended while its thread still closes its
provider (L-1); a Stop that raises still unbinds and stops the worker, and the sweep goes on (L-2); a pending call is
told the ending's reason, not that the person cancelled (L-3). The binding is cleared in one place, which ends the
turn first (pinned below), so a new route cannot forget it.
"""

import ast
import contextlib
import io
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (parallel_sessions, worker_dispatch, worker_pool, worker_queue, worker_resume,  # noqa: E402
                          worker_routing, worker_sessions)
from angelo_agent.controller import Controller  # noqa: E402
from test_native_worker_queue import ParkedFixture, Process  # noqa: E402

UNSTOPPED = 30.0   # How long a request nobody stops runs on (the stand-in's bound; the old code's case).
STOPPED = 3.0      # How long a stopped request may take to end.
SCRIPT = {"description": "Set a value", "code": "result = 1"}


def until(condition, seconds=5.0, step=None):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        if step is not None:
            step()
        time.sleep(0.005)
    return condition()


class Provider:
    """A model request in flight: ``run`` returns once the request is stopped (or after ``UNSTOPPED``)."""

    def __init__(self):
        self.running, self.ended, self.stops, self.context = threading.Event(), threading.Event(), [], None

    def bind_request(self, context):
        self.context = context

    def run(self, native_id, text, observation=None):
        self.running.set()
        self.ended.wait(UNSTOPPED)
        return "interrupted" if self.stops else "completed"

    def stop_request(self, fence):
        self.stops.append(fence)
        self.ended.set()

    def stop(self):
        self.stops.append(None)
        self.ended.set()

    def close(self):
        self.ended.set()


class FailingProvider(Provider):
    """A request that fails, and whose close takes a while (a process being killed): the turn's thread outlives its
    end (break-it L-1)."""

    def __init__(self):
        super().__init__()
        self.closing, self.release = threading.Event(), threading.Event()

    def run(self, native_id, text, observation=None):
        self.running.set()
        raise RuntimeError("provider failed")

    def close(self):
        self.closing.set()
        self.release.wait(5)


class BlockingProcess(Process):
    """A worker whose answer to a turn's preparation waits until it is let go."""

    def __init__(self, part):
        super().__init__(part)
        self.gate, self.entered = threading.Event(), threading.Event()

    def request(self, op, timeout, **fields):
        if op == "prepare_turn":
            self.entered.set()
            self.gate.wait(5)
        return super().request(op, timeout, **fields)


class EndingFixture(ParkedFixture):
    def worker_chat(self, lead="lead", number=1, process=None):
        """A real chat bound to a stand-in worker (``worker_routing.adopt``, as a start binds it), in its place."""
        folder = tempfile.mkdtemp()
        chat = Controller(folder, session_id=str(uuid.uuid4()))
        self.addCleanup(chat.close)
        process = process(Path(folder) / "part.blend") if process else Process(Path(folder) / "part.blend")
        worker_routing.adopt(chat, process)
        session = chat.state["id"]
        self.ui._controllers[session] = chat
        parallel_sessions._order.append(session)
        worker_sessions._started.setdefault(lead, []).append(session)
        worker_sessions._parts[session] = (number, "Part %d" % number, process.part)
        return chat, process

    def in_flight(self, chat, provider=None):
        """The chat's turn, its model request running (sent as the brief is: ``Controller.send``)."""
        provider = provider or Provider()
        self.addCleanup(lambda: (provider.ended.set(), chat._worker and chat._worker.join(5)))
        chat.checkpoint_factory = lambda: None   # The stand-in worker keeps no part file to snapshot.
        chat.provider, chat.state["native_id"] = provider, "warm-thread"
        chat.send("Build the sofa.")
        self.assertTrue(provider.running.wait(5))
        return provider

    def notices(self, chat):
        chat.pump_events()
        return [(m.get("i18n") or [m.get("text")])[0] for m in chat.state["messages"] if m.get("role") == "system"]

    def assert_turn_ended(self, chat, provider, reason):
        self.assertTrue(provider.ended.wait(STOPPED) and provider.stops, "the provider's request was never stopped")
        chat._worker.join(STOPPED)
        self.assertFalse(chat._worker.is_alive())
        chat.pump_events()
        self.assertEqual((chat.busy, chat.cancelled, chat.status), (False, True, "Interrupted"))
        context = provider.context   # The request's own context: cancelled, its deliveries closed.
        self.assertTrue(context.cancelled)
        self.assertFalse(context.delivery_owner.current(chat))
        if reason is not None:   # The chat says why, in the person's language (a notice row).
            self.assertIn(reason, self.notices(chat))


class EndingRoutesTests(EndingFixture):
    def test_a_load_that_replaces_the_leads_document(self):
        chat, process = self.worker_chat()
        provider = self.in_flight(chat)
        self.lead.document_invalid = True
        worker_queue.document_replaced()
        self.assertIsNone(worker_routing.bound(chat))
        self.assert_turn_ended(chat, provider, worker_routing.END_REPLACED)
        self.assertTrue(process.stopped.wait(5))

    def test_a_lead_invalidated_with_no_load_as_another_scene_is_shown_in_a_background_angelo(self):
        from angelo_agent import document_scene
        lead = Controller(tempfile.mkdtemp(), session_id=str(uuid.uuid4()))
        self.addCleanup(lead.close)
        lead.set_document(("angelo-scene", "Scene"))
        lead.state["messages"].append({"role": "user", "text": "Build a living room with workers."})
        self.ui._controllers[lead.state["id"]] = lead
        chat, process = self.worker_chat(lead=lead.state["id"])
        provider = self.in_flight(chat)
        with mock.patch.object(document_scene, "unattended", lambda: False):   # A person's window: only displayed.
            lead.set_document(("angelo-scene", "Other scene"))
        self.assertFalse(lead.document_invalid)
        worker_queue.tick()
        self.assertIsNotNone(worker_routing.bound(chat))   # Its worker goes on (break-it E2E-20 R5-M-1).
        with mock.patch.object(document_scene, "unattended", lambda: True):   # No person shows a scene back there.
            lead.set_document(("angelo-scene", "Third scene"))   # ``Controller.set_document`` on another scene's key.
        self.assertTrue(lead.document_invalid)
        worker_queue.tick()   # The queue's next step finds it (worker_ending.leads_ended).
        self.assertIsNone(worker_routing.bound(chat))
        self.assertEqual(worker_sessions.died[chat.state["id"]], worker_queue.REPLACED)
        self.assert_turn_ended(chat, provider, worker_routing.END_REPLACED)

    def test_a_worker_that_crashed(self):
        chat, process = self.worker_chat()
        provider = self.in_flight(chat)
        process.running = False   # Its Blender exited; the queue's step reconciles it as a confirmed death.
        self.assertEqual(worker_queue.reap(self.ui), 1)
        self.assertIsNone(worker_routing.bound(chat))
        self.assertIn("exit code -9", worker_sessions.died[chat.state["id"]])
        self.assert_turn_ended(chat, provider, worker_routing.END_CRASHED)

    def test_a_worker_whose_part_was_merged(self):
        chat, process = self.worker_chat()
        provider = self.in_flight(chat)
        worker_routing.unbind(chat, worker_routing.END_MERGED)   # What the merge does to each merged part's chat.
        self.assert_turn_ended(chat, provider, worker_routing.END_MERGED)
        self.assertTrue(process.stopped.wait(5))

    def test_the_add_on_ending(self):
        chat, process = self.worker_chat()
        provider = self.in_flight(chat)
        with mock.patch.object(worker_pool, "release_all", lambda: None):   # Blender's timers: none here.
            worker_routing.stop_all()
        self.assertTrue(process.stopped.is_set())
        self.assert_turn_ended(chat, provider, worker_routing.END_CLOSING)

    def test_the_persons_stop_is_the_same_route(self):
        chat, _process = self.worker_chat()
        provider = self.in_flight(chat)
        worker_resume.stop(chat)   # ``ANGELO_OT_stop``: the worker stays bound, ready for the person's next message.
        self.assertIsNotNone(worker_routing.bound(chat))
        self.assert_turn_ended(chat, provider, None)

    def test_a_pending_call_is_told_why_not_that_the_person_cancelled(self):
        chat, _process = self.worker_chat()
        provider = self.in_flight(chat)
        results = []
        provider.context.request_tool("c1", "angelo_execute_python", dict(SCRIPT), results.append)
        self.assertEqual(len(chat.pending), 1)   # Admitted, not run yet: a card or a queued call.
        self.lead.document_invalid = True
        worker_queue.document_replaced()
        self.assertEqual(chat.pending, {})
        self.assertTrue(results and results[0].get("error_code") == "CANCELLED", results)
        self.assertIn(worker_routing.END_REPLACED, results[0]["error"])
        self.assertNotIn("user cancelled", results[0]["error"])
        self.assert_turn_ended(chat, provider, worker_routing.END_REPLACED)


class EndedTurnTests(EndingFixture):
    """A turn that already ended keeps how it ended: no Stop, no row (break-it L-1)."""

    def test_a_completed_turn_is_left_as_it_ended(self):
        chat, _process = self.worker_chat()
        provider = self.in_flight(chat)
        provider.ended.set()   # The turn ends by itself: nothing stopped it.
        self.assertTrue(until(lambda: not chat.busy, step=chat.pump_events))   # Its end handled; no join.
        before = (chat.stops, chat.status, chat.cancelled, len(chat.state["messages"]))
        self.assertEqual(before[1:3], ("Completed", False))
        worker_routing.unbind(chat, worker_routing.END_DONE, parked=True)
        chat.pump_events()
        self.assertEqual((chat.stops, chat.status, chat.cancelled, len(chat.state["messages"])), before)
        self.assertEqual(provider.stops, [])

    def test_a_failed_turn_still_closing_its_provider_is_left_as_it_ended(self):
        chat, _process = self.worker_chat()
        provider = FailingProvider()
        self.addCleanup(provider.release.set)
        self.in_flight(chat, provider)
        self.assertTrue(provider.closing.wait(5))
        self.assertTrue(until(lambda: not chat.busy, step=chat.pump_events))
        self.assertTrue(chat._worker.is_alive())   # The tail: its provider still closing.
        before = (chat.stops, chat.status, chat.cancelled)
        self.assertEqual(before[1], "Failed")
        self.assertFalse(worker_routing.end_turn(chat, worker_routing.END_DONE))
        worker_routing.detach(chat)   # A done worker's process taken for another part (``worker_pool.reuse``).
        self.assertEqual((chat.stops, chat.status, chat.cancelled, provider.stops), before + ([],))


class SweepTests(EndingFixture):
    def test_a_stop_that_raises_still_unbinds_and_every_worker_stops(self):
        chat1, process1 = self.worker_chat(number=1)
        chat2, process2 = self.worker_chat(number=2)
        self.in_flight(chat1)
        chat1.stop = mock.Mock(side_effect=OSError("disk full"))   # Any failure inside a Stop.
        self.lead.document_invalid = True
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            worker_queue.document_replaced()
        self.assertEqual(chat1.stop.call_count, 1)
        self.assertIn("could not be stopped", log.getvalue())
        self.assertEqual((worker_routing.bound(chat1), worker_routing.bound(chat2)), (None, None))
        self.assertTrue(process1.stopped.wait(5) and process2.stopped.wait(5))


class LaneDeliveryTests(EndingFixture):
    """The person's message to a worker chat, on its lane (``console.execute``'s ``failed``), when the worker ends."""

    def deliver(self, chat, text):
        shown = []

        def failed(error):   # console.execute's handler for a worker chat.
            worker_routing.unsent(chat, text, error) or shown.append(error)
        worker_routing.deliver(chat, None, text, lambda turn: chat.send(
            text, observation=turn.observation, recovery_evidence=turn.recovery_evidence), failed)
        return shown

    def test_a_message_being_prepared_comes_back_and_the_chat_says_why(self):
        chat, process = self.worker_chat(process=BlockingProcess)
        self.addCleanup(worker_dispatch.close_all)
        self.addCleanup(process.gate.set)
        chat.state["messages"].append({"role": "user", "text": "Build the sofa."})
        shown = self.deliver(chat, "Make it blue.")
        worker_dispatch.pump(chat)
        self.assertTrue(process.entered.wait(5))   # Its lane waits on the worker for the turn's observation.
        self.lead.document_invalid = True
        worker_queue.document_replaced()
        process.gate.set()
        self.assertTrue(until(lambda: chat.unsent_draft is not None, step=lambda: worker_dispatch.pump(chat)))
        self.assertEqual(chat.unsent_draft, "Make it blue.")   # Back where the person types (drafts.restore_unsent).
        self.assertEqual(shown, [])
        notices = self.notices(chat)
        self.assertIn(worker_routing.END_REPLACED, notices)
        self.assertIn(worker_routing.NOT_SENT, notices)
        self.assertFalse(chat.busy)   # Nothing was sent.
        self.assertEqual([m["text"] for m in chat.state["messages"] if m.get("role") == "user"], ["Build the sofa."])

    def test_a_message_queued_on_its_lane_comes_back_when_its_worker_ends(self):
        chat, _process = self.worker_chat()
        self.addCleanup(worker_dispatch.close_all)
        shown = self.deliver(chat, "Make it blue.")   # Queued: its lane has not run it yet.
        worker_routing.unbind(chat, worker_routing.END_CRASHED)
        self.assertEqual(chat.unsent_draft, "Make it blue.")
        self.assertEqual([type(error) for error in shown], [worker_dispatch.WorkerGone])   # Its row: nothing sent.


class OneRouteTests(unittest.TestCase):
    """A chat leaves its worker in one place (``worker_routing.unbind``), which ends its turn before anything else;
    every other ending of a worker chat calls the same helper, with a reason."""

    ALLOWED = {("controller", "__init__"), ("worker_routing", "bind"), ("worker_routing", "unbind")}

    def functions(self):
        for path in sorted(ADDON.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield path.stem, node

    @staticmethod
    def writes_binding(node):
        """Any write of a chat's binding: an assignment (a tuple's too), ``setattr``, ``del`` or a ``__dict__`` key."""
        def named(target):
            if isinstance(target, (ast.Tuple, ast.List)):
                return any(named(item) for item in target.elts)
            if isinstance(target, ast.Attribute):
                return target.attr == "remote_executor"
            return isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and \
                target.slice.value == "remote_executor"
        for item in ast.walk(node):
            targets = (item.targets if isinstance(item, (ast.Assign, ast.Delete))
                       else [item.target] if isinstance(item, (ast.AugAssign, ast.AnnAssign)) else [])
            if any(named(target) for target in targets):
                return True
            if (isinstance(item, ast.Call) and ast.unparse(item.func) in {"setattr", "delattr"} and len(item.args) > 1
                    and isinstance(item.args[1], ast.Constant) and item.args[1].value == "remote_executor"):
                return True
        return False

    def test_only_unbind_clears_a_binding_and_it_ends_the_turn_first(self):
        writers = {(module, node.name) for module, node in self.functions() if self.writes_binding(node)}
        self.assertLessEqual(writers, self.ALLOWED)
        self.assertIn(("worker_routing", "unbind"), writers)
        unbind = next(node for module, node in self.functions() if (module, node.name) == ("worker_routing", "unbind"))
        first = next(statement for statement in unbind.body if not isinstance(statement, ast.Expr)
                     or not isinstance(statement.value, ast.Constant))
        self.assertIsInstance(first, ast.Try)   # A Stop that fails never keeps the chat bound (break-it L-2).
        self.assertEqual(ast.unparse(first.body[0]), "end_turn(controller, reason)")

    def test_the_other_endings_call_the_same_helper(self):
        calls = {(module, node.name): {ast.unparse(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)}
                 for module, node in self.functions()}
        self.assertIn("end_turn", calls[("worker_routing", "stop_all")])
        self.assertIn("worker_routing.end_turn", calls[("worker_ending", "end_parked")])


if __name__ == "__main__":
    unittest.main()
