# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``steering`` gate set (``eval_steer``): its tasks, when the person's message goes in, when grading waits,
and how each steer's outcome is read. Stand-ins for the chat; no Blender, model or account."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import eval_live_tasks as live  # noqa: E402
import eval_steer  # noqa: E402
import scenario_session  # noqa: E402
from angelo_agent.steering import Inbox, QUEUE  # noqa: E402


class Chat:
    def __init__(self):
        self.state, self.inbox, self.running = {"messages": [{"role": "user", "text": "prompt"}]}, Inbox(), True

    def turn_running(self):
        return self.running


class SteeringSetTests(unittest.TestCase):
    def setUp(self):
        self.timers = []
        fake_bpy = SimpleNamespace(app=SimpleNamespace(timers=SimpleNamespace(
            register=lambda fn, first_interval=0: self.timers.append(fn))))
        pending = types.ModuleType("angelo_agent.chat_pending")
        pending.hold = lambda chat, text: chat.inbox.hold(text)
        for name, module in (("bpy", fake_bpy), ("angelo_agent.chat_pending", pending)):
            stub = patch.dict(sys.modules, {name: module})
            stub.start()
            self.addCleanup(stub.stop)
        # A previous suite may have imported the real module onto the package.
        # Relative imports use that attribute even when sys.modules is patched.
        import angelo_agent
        stub = patch.object(angelo_agent, "chat_pending", pending, create=True)
        stub.start()
        self.addCleanup(stub.stop)
        self.chat, self.session = Chat(), eval_steer.SteerSession(Path("."))
        self.chat.state["messages"] = []

    def attach(self, task):
        self.record = {}
        self.chat.state["messages"].append({"role": "user", "text": task["prompt"]})
        self.session.attach(self.chat, task, self.record)
        self.session.first_row = len(self.chat.state["messages"]) - 1

    def test_the_set_is_known_everywhere_and_every_task_is_closed_checked(self):
        self.assertIn("steering", live.TASK_SETS)
        tasks = live.tasks_for("steering")
        self.assertEqual([t["id"] for t in tasks], ["steer_resize", "steer_extend", "steer_after_turn"])
        for task in tasks:
            self.assertTrue(task["checks"] and task["steer"]["text"] and set(task["targets"]) >= {
                c["target"] for c in task["checks"]}, task["id"])
        self.assertIsInstance(scenario_session.for_trial("steering", Path(".")), eval_steer.SteerSession)
        self.assertEqual(scenario_session.grading_route(tasks[0], True), "scenario")

    def test_the_person_types_after_the_first_tool_step_and_grading_waits_for_their_message(self):
        task = live.tasks_for("steering")[0]
        self.attach(task)
        poll = self.timers[-1]
        self.assertEqual(poll(), 0.2, "Not before the turn's first step")
        self.assertTrue(self.session.jobs_pending())
        self.chat.state["messages"].append({"role": "activity", "status": "completed"})
        self.assertIsNone(poll())
        self.assertEqual([i.text for i in self.chat.inbox.items()], [task["steer"]["text"]])
        self.assertTrue(self.record["steer"]["held_during_turn"])
        self.chat.running = False
        self.assertTrue(self.session.jobs_pending(), "Its message still waits")
        self.chat.inbox.sent(self.chat.inbox.items())
        self.assertFalse(self.session.jobs_pending())

    def test_a_turn_with_no_tool_step_still_gets_the_message_after_it(self):
        self.attach(live.tasks_for("steering")[0])
        self.chat.running = False
        self.assertIsNone(self.timers[-1]())
        self.assertFalse(self.record["steer"]["held_during_turn"])

    def test_after_this_turn_is_chosen_as_the_bubble_does(self):
        self.attach(live.tasks_for("steering")[2])
        self.chat.state["messages"].append({"role": "activity", "status": "completed"})
        self.timers[-1]()
        self.assertEqual(self.chat.inbox.items()[0].mode, QUEUE)

    def test_each_outcome_is_read_from_the_rows(self):
        task = live.tasks_for("steering")[0]
        text = task["steer"]["text"]
        self.attach(task)
        rows = self.chat.state["messages"]
        self.assertEqual(self.session.outcome(), "left")
        rows.append({"role": "user", "text": text, "steer": True, "withdrawn": True})
        self.assertEqual(self.session.outcome(), "left", "A withdrawn steer was never read")
        rows.append({"role": "user", "text": text})
        self.assertEqual(self.session.outcome(), "sent_after_turn")
        rows.append({"role": "user", "text": text, "steer": True})
        self.assertEqual(self.session.outcome(), "read_mid_turn")


if __name__ == "__main__":
    unittest.main()
