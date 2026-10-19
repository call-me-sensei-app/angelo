# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Yielding admission cannot relabel an old delivery with a new warm turn."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_delivery, controller as module
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.providers import CodexProvider


class RPC:
    def __init__(self):
        self.closed, self.responses = threading.Event(), []
    def request(self, method, parameters, **kwargs):
        return {}
    def respond(self, identity, value):
        self.responses.append((identity, value))
    def close(self):
        self.closed.set()


class Executor:
    def __init__(self):
        self.applied = []
    def precondition(self, name, arguments):
        return "same-fixture-document"
    def execute(self, name, arguments):
        self.applied.append(name)
        return {"success": True, "objects": []}


class AdmissionFenceTests(unittest.TestCase):
    def race(self, phase, *, stop=False):
        with tempfile.TemporaryDirectory() as directory:
            c = Controller(directory)
            c.set_document(("fixture-document", 17))
            c.state["native_id"] = "same-warm-thread"
            c.state["options"]["approval"] = "AUTO_BOUNDED"
            c._begin_metrics()
            c.busy = True
            old = c.runtime_context()
            p = CodexProvider(old, {})
            p.rpc, p.thread_id = RPC(), "same-warm-thread"
            p.bind_request(old)
            p._scope.bind_turn("turn-a")
            c.provider = p
            original_rpc, old_metrics = p.rpc, c._request_metrics
            entered, release, new_bound, finish_new = (threading.Event() for _ in range(4))
            captured, errors = [], []
            def pause(function):
                def wrapped(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("Admission fixture release timed out")
                    return function(*args, **kwargs)
                return wrapped
            def fresh_run(*args, **kwargs):
                context = c.runtime_context()
                c._connect_runtime(context)  # Rebind the actual warm provider.
                p._scope.bind_turn("turn-b")
                new_bound.set()
                finish_new.wait(5)
            original_journal = c._journal_tool
            def journal(call, marker):
                captured.append((call.epoch, call.generation, call.metrics))
                return original_journal(call, marker)
            if phase == "guard":
                blocker = patch.object(module, "check_invocation", side_effect=pause(module.check_invocation))
            elif phase == "reserve":
                blocker = patch.object(module, "reserve_invocation", side_effect=pause(module.reserve_invocation))
            else:
                blocker = patch.object(c.store, "save", side_effect=pause(c.store.save))
            def late_request():
                try:
                    p._request("late-rpc", "item/tool/call", {"threadId": "same-warm-thread", "turnId": "turn-a",
                        "callId": "unseen-old-call", "tool": "angelo_create_cube",
                        "arguments": {"name": "Old Turn Cube", "size": 1, "location": [0, 0, 0]}})
                except Exception as error:
                    errors.append(error)
            late = threading.Thread(target=late_request)
            try:
                with blocker, patch.object(c, "_journal_tool", side_effect=journal), patch.object(c, "_run", side_effect=fresh_run):
                    idle_before_guard = not stop and phase == "guard"
                    if idle_before_guard:
                        # Real completion closes the request's delivery owner
                        # before a late RPC can reach mutation admission.
                        old.emit("finished", {"status": "completed"})
                        c.busy = False
                        c._finish_metrics("completed")
                    late.start()
                    if idle_before_guard:
                        late.join(5)
                        self.assertFalse(late.is_alive())
                        self.assertFalse(entered.is_set(), "Closed delivery never reaches mutation guard lookup")
                    else:
                        self.assertTrue(entered.wait(5))
                    if stop:
                        c.stop()
                        # Prior worker retirement: completion flags precede
                        # its optional UI save; no native/provider process here.
                        c.busy = False
                        c._finish_metrics("interrupted")
                    elif not idle_before_guard:
                        # Enter the real admission barrier while the request is
                        # active; the earlier closed-scope gate must not hide it.
                        # Like worker retirement in the other phases, completion
                        # flags precede UI saving while the guard holds host.lock.
                        old.program_completions.clear()
                        c.busy = False
                        c._finish_metrics("completed")
                    c.send("A genuinely new unrelated human request")
                    self.assertTrue(new_bound.wait(5))
                    self.assertEqual(c._request_generation, old.request_fence.generation + 1)
                    self.assertTrue(old.cancelled)
                    self.assertIs(c.provider, p)
                    self.assertIs(p.rpc, original_rpc)
                    self.assertTrue(p.has_warm_runtime())
                    self.assertIsNot(c._request_metrics, old_metrics)
                    release.set()
                    late.join(5)
                    self.assertFalse(late.is_alive())
                    self.assertFalse(errors)
                    executor = Executor()
                    c.pump_tools(executor, c.document,
                        apply_mutation=lambda call: c.execute_tool(call, executor, c.document),
                        budget=ToolDispatchBudget(seconds=60))
                    self.assertFalse(executor.applied)
                    self.assertFalse(c.pending)
                    if idle_before_guard:
                        # Never admitted, but answered: codex app-server waits for every tool-call reply.
                        self.assertEqual(original_rpc.responses, [("late-rpc", {"success": False, "contentItems": [
                            {"type": "inputText", "text": codex_delivery.STALE_CALL}]})])
                    else:
                        # Admitted before its owner closed: no later phase, the reply included.
                        self.assertEqual(original_rpc.responses, [], "Closed native delivery sends no stale reply")
                    self.assertFalse(c.media_jobs or c.delegate_jobs)
                    key = c.state["id"] + "/codex_subscription/same-warm-thread/unseen-old-call"
                    if phase == "guard":
                        self.assertNotIn(key, c.state["invocations"]["entries"])
                        self.assertNotIn(key, c.state["results"])
                        self.assertFalse(captured)
                    else:
                        self.assertIn(key, c.state["invocations"]["entries"])
                        result = c.state["results"][key]["result"]
                        self.assertIn("no operation was applied", result["error"])
                        self.assertFalse(result["scene_changed"])
                        self.assertTrue(captured)
                        persisted = c.store.load()
                        self.assertEqual(persisted["results"][key]["result"], result)
                    self.assertNotIn("tool_invocations", c._request_metrics.snapshot()["counts"])
                    self.assertTrue(all(generation == old.request_fence.generation and metrics is old_metrics
                                        for _, generation, metrics in captured))
            finally:
                release.set()
                finish_new.set()
                late.join(5)
                if c._worker:
                    c._worker.join(5)
                c.close()

    def test_idle_completion_suppresses_before_guard_lookup_on_same_warm_provider(self):
        self.race("guard")

    def test_stop_new_send_guard_lookup_race_rejects_old_call_on_same_warm_provider(self):
        self.race("guard", stop=True)

    def test_reservation_yield_never_relabels_published_call_or_metrics(self):
        self.race("reserve")

    def test_journal_yield_rechecks_before_queueing_and_preserves_admitted_fence(self):
        self.race("journal")


if __name__ == "__main__":
    unittest.main()
