# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's window: a call it already has runs once, and every later answer about it is honest.

Fresh break-it review FR-H1 (2026-10-23) and the coordinator's class rule: a call keeps its identity across the
launcher's re-asks (the same call ID every round, ``Server.call``), so asking again about a call Angelo has, or has
finished, returns its pending or final result and never runs it again. The window path shares the launcher loop with
the headless path (test_native_headless_in_flight.py). Here the real ``ExternalHost``, ``RequestService`` and External
conversation (test_native_external_mcp's harness) answer an MCP app whose call holds Angelo's main thread longer
than the launcher's round plus the main thread's pickup time, as a long script does. Covered:

* a call longer than a round runs exactly once and its result reaches the app (never "not admitted");
* the app's levels revoked mid-call: the call is not run again, and the answer says Angelo had it, never a bare
  refusal that reads as "nothing ran";
* Angelo's listener ending mid-call: the answer says whether it ran is unknown, never "no window" (an app told
  that would repeat the call elsewhere).

Contract: doc/angelo/external_mcp.rst, "Tool level", "One call, one run".
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import external_mcp  # noqa: E402
from angelo_agent.external_clients import READ, AppRegistry  # noqa: E402
import test_native_external_mcp as mcp_harness  # noqa: E402  (a module: its tests are not collected again here)
from test_native_headless_in_flight import ROUND, SlowExecutor  # noqa: E402

CUBE = {"name": "Box", "size": 1, "location": [0, 0, 0]}


@unittest.skipIf(sys.platform == "win32", "Needs the Unix-socket listener (Windows named pipe: a later phase)")
class WindowInFlightTests(unittest.TestCase):
    tick, server, rpc, call = (mcp_harness.AppTests.tick, mcp_harness.AppTests.server, mcp_harness.AppTests.rpc,
                               mcp_harness.AppTests.call)

    def setUp(self):
        mcp_harness.AppTests.setUp(self)
        self.executor = SlowExecutor(self.state)
        rounds = patch.object(external_mcp, "ROUND_WAIT", ROUND)
        rounds.start()
        self.addCleanup(rounds.stop)
        self.host.pump()   # Starts the listener; its pickup time shortened like the round.
        self.host.service.main_thread_timeout = ROUND

    def ran(self):
        return [name for name in self.executor.calls if name.endswith("create_cube")]

    def test_a_call_longer_than_a_round_runs_once_and_its_result_reaches_the_app(self):
        result = self.call("angelo_create_cube", CUBE)
        self.assertFalse(result["isError"], result)
        self.assertNotIn("not admitted", json.dumps(result))
        self.assertEqual(len(self.ran()), 1, self.executor.calls)

    def test_levels_revoked_mid_call_never_run_it_again_and_the_answer_says_angelo_had_it(self):
        def revoke():   # The person revokes the level; Angelo's listener learns it at once.
            AppRegistry(self.state).set_levels(self.name, (READ,))
            self.host.reload_apps(force=True)
        self.executor.during = revoke
        result = self.call("angelo_create_cube", CUBE)
        self.executor.during = None
        for _ in range(20):
            self.tick()
        self.assertEqual(len(self.ran()), 1, self.executor.calls)
        self.assertTrue(result["isError"])
        self.assertIn("Angelo already had this call", json.dumps(result))
        self.assertIn("nothing was run again", json.dumps(result))

    def test_the_listener_ending_mid_call_is_said_as_such_never_no_window(self):
        self.executor.during = lambda: self.host.stop()
        result = self.call("angelo_create_cube", CUBE)
        self.executor.during = None
        self.assertEqual(len(self.ran()), 1, self.executor.calls)
        self.assertTrue(result["isError"])
        text = json.dumps(result)
        self.assertIn("is unknown, and nothing was", text)
        self.assertNotIn("no_file", text)


if __name__ == "__main__":
    unittest.main()
