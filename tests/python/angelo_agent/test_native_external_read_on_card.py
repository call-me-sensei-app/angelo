# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every waiting call of an app returns while the turn's card waits for the person: angelo_read, angelo_status and a
waiting angelo_send, each with ``turn: waiting_for_approval``, the card's tool and ``needs``.

The 2.0.1 GUI round on 12484412217 (test_blender_external_mcp, two suites at a time) read ``turn: idle`` 178 ms
after the send: that suite's person approved each card on its first heartbeat after it appeared, so under load the
card was approved and the turn done before the app's next read reached the host. Alone the same build read the card
in about 45 ms (three runs of three). A parked read is answered on the next tick after the card shows; a read in
flight between two of the launcher's long-poll reads is answered when it arrives, from the state then. So a person who
answers within a frame can leave nothing for the app to see, and the suite's person now reads the card first: it
approves once the app's waiting read has returned. Here the person answers once the app's call returned, on each path.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from test_native_external_agent import LISTENER, AgentFixture, TurnProvider  # noqa: E402


@unittest.skipIf(sys.platform == "win32", LISTENER)
class ReturnsOnTheCardTests(AgentFixture):
    def setUp(self):
        super().setUp()
        self.env.provider = TurnProvider(self.release)   # Asks for a script: a card only the person answers.
        self.release.set()   # Once approved, the turn ends at once.

    def waiting(self, tool):
        sent = self.call("angelo_send", {"message": "Make a cube", **({"wait_seconds": 20} if tool == "send" else {})})
        if tool == "send":
            return sent["structuredContent"]
        arguments = {"cursor": sent["structuredContent"]["cursor"], "wait_seconds": 20}
        if tool == "status":
            arguments = {"task": sent["structuredContent"]["task"], "wait_seconds": 20}
        return self.call("angelo_" + tool, arguments)["structuredContent"]

    def check(self, tool):
        value = self.waiting(tool)
        self.assertEqual(value["turn"], "waiting_for_approval", value)
        self.assertEqual(value["approval"], {"tool": "angelo_execute_python"})
        self.assertIn("Only they can do this", value["needs"])
        self.assertEqual(self.executor.calls.count("angelo_execute_python"), 0, "the call returned before any press")
        self.approve = self.person_approves   # The person reads the card, then approves.
        self.until(lambda: not self.conversation().turn_running())
        self.assertEqual(self.executor.calls.count("angelo_execute_python"), 1)

    def test_angelo_read(self):
        self.check("read")

    def test_angelo_status(self):
        self.check("status")

    def test_a_waiting_angelo_send(self):
        self.check("send")


if __name__ == "__main__":
    unittest.main()
