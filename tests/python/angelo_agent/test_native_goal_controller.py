# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A held goal through the real ``Controller`` on both runtimes (doc/angelo/goal_mode.rst, "Rounds"): the person's
turn ends, the window's step measures it and Angelo's own request reaches the runtime with the continuation; its row
is Angelo's; the person's Stop ends the goal; a worker started in a goal round opens no worker-done batch.

Dependency-free: a scripted stand-in runtime replaces Claude Code and Codex alike; no process, account or network.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))

from angelo_agent import goal_controls, goal_loop, goal_probes, goal_state as G, goal_store, worker_resume  # noqa: E402
from angelo_agent.chat_settings import normalize_models  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.request_rows import person_request  # noqa: E402

REF = "0b5e8d1c-0000-4000-8000-000000000001"
CAMERA = {"location": [0.0, -8.0, 2.0], "look_at": [0.0, 0.0, 1.0], "lens": 50.0}


class ScriptedRuntime:
    """Either runtime's stand-in: records each request's words and answers it."""
    prompts = []

    def __init__(self, context, settings):
        self.controller = context

    def connect(self):
        native = self.controller.state.get("native_id") or "native-" + str(uuid.uuid4())[:8]
        self.controller.emit("native_session", {"id": native})
        return native

    def run_request(self, context, native_id, text, observation=None):
        self.prompts.append(text)
        context.record_provider_turn()
        context.record_provider_accepted()
        context.emit("message_complete", {"item": "reply", "text": "Worked on it"})
        return "completed"

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        pass


RUNTIMES = (("claude_subscription", "claude", "ClaudeProvider", "claude-opus-5-5"),
            ("codex_subscription", "codex", "CodexProvider", "gpt-6.1-sol"))


class GoalThroughControllerTests(unittest.TestCase):
    def start(self, mode, runtime, provider, model):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        goal_loop._reset()
        goal_store._STORES.clear()
        self.addCleanup(goal_store._STORES.clear)
        self.addCleanup(lambda: [store.drain() for store in list(goal_store._STORES.values())])
        ScriptedRuntime.prompts = []
        for patcher in (patch("angelo_agent.controller." + provider, ScriptedRuntime),
                        patch("angelo_agent.goal_measure.measure", lambda chat, goal, window: dict(self.measurement)),
                        patch.object(goal_probes, "blender_busy", lambda window: False),
                        patch.object(goal_probes, "away", lambda chat, ui: False),
                        patch.object(goal_probes, "worker_chat", lambda chat: False),
                        patch("angelo_agent.worker_routing.deliver", lambda chat, window, message, send, failed: send(
                            SimpleNamespace(observation={"objects": []}, recovery_evidence={})))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.c = Controller(self.directory.name)
        self.addCleanup(self.c.close)
        self.c.set_document(("scratch", 1))
        catalog = normalize_models([{"value": model, "displayName": model, "isDefault": True}], runtime)
        self.c.choose_connection(mode, {"model": ""}, "", str(uuid.uuid4()), catalog, "")
        self.ui = SimpleNamespace(_controllers={self.c.state["id"]: self.c}, prepare_controller=lambda c=None: None,
                                  main_window=lambda: None, document_key=lambda: ("scratch", 1), _drafts={})
        self.measurement = {"measured": True, "iou": 0.55, "drawn": 2, "mean_px": 12.0}

    def wait(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.c.pump_events()
            if not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.pump_events()
                return
            time.sleep(0.002)
        self.fail("The scripted runtime did not finish")

    def test_a_round_reaches_the_runtime_as_angelos_request_on_both_runtimes(self):
        for case in RUNTIMES:
            with self.subTest(runtime=case[1]):
                self.start(*case)
                self.c.send("Build a hut like the picture; don't stop until it matches", {"objects": []})
                goal_controls.execute(self.c, None, {"action": "SET", "objective": "Match the hut in the picture",
                                                 "measure": {"reference": REF, "camera": CAMERA}})
                self.wait()
                self.assertEqual(len(ScriptedRuntime.prompts), 1)
                self.measurement["iou"] = 0.61
                goal_loop.step(self.ui)
                self.wait()
                self.assertEqual(len(ScriptedRuntime.prompts), 2)
                self.assertIn("From Angelo, not the person: round 2 of the goal held in this chat",
                              ScriptedRuntime.prompts[-1])
                self.assertIn("overlap 61.0%", ScriptedRuntime.prompts[-1])
                rows = [m for m in self.c.state["messages"] if m.get("role") == "user"]
                self.assertEqual(rows[-1]["request_kind"], goal_loop.REQUEST_KIND)
                self.assertEqual((rows[-1]["goal"]["round"], rows[-1]["goal"]["score"]), (2, 61))
                self.assertFalse(person_request(rows[-1]))
                self.assertTrue(person_request(rows[0]))
                goal = goal_loop.goal_of(self.c)
                self.assertEqual([r["kind"] for r in goal["rounds"]], [G.PERSON])
                self.assertGreater(goal["rounds"][0]["working_ms"], 0)
                # Round 2 ended: the step records it and goes on.
                goal_loop.step(self.ui)
                self.wait()
                self.assertEqual([r["kind"] for r in goal_loop.goal_of(self.c)["rounds"]], [G.PERSON, G.AUTOMATIC])

    def test_the_persons_stop_ends_the_goal(self):
        self.start(*RUNTIMES[1])
        self.c.send("Keep going until it matches", {"objects": []})
        goal_controls.execute(self.c, None, {"action": "SET", "objective": "Match it"})
        goal_controls.person_stopped(self.c)   # ANGELO_OT_stop calls it before Controller.stop.
        self.c.stop()
        self.wait()
        goal_loop.step(self.ui)
        self.assertEqual(goal_loop.goal_of(self.c)["status"], G.STOPPED)
        self.assertLessEqual(len(ScriptedRuntime.prompts), 1)   # The person's request may or may not have run.
        self.assertFalse(any(m.get("request_kind") == goal_loop.REQUEST_KIND for m in self.c.state["messages"]))

    def test_a_worker_started_in_a_goal_round_opens_no_batch(self):
        self.start(*RUNTIMES[0])
        self.c.send("Keep going until it matches", {"objects": []})
        goal_controls.execute(self.c, None, {"action": "SET", "objective": "Match it"})
        self.wait()
        goal_loop.step(self.ui)
        self.wait()
        worker_resume._batches.clear()
        self.addCleanup(worker_resume._batches.clear)
        worker_resume.started(self.c, [1])   # A start in Angelo's own turn (break-it EVENT H-3).
        self.assertFalse(worker_resume.pending(self.c.state["id"]))


if __name__ == "__main__":
    unittest.main()
