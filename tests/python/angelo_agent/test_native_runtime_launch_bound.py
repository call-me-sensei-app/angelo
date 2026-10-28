# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""R2b's launch answer (``runtime: {kind, version}``, FE D1) survives R2e's bound launches: through the service's own
``plan``, its binding claim and a prewarmed child the launch adopts, the answer still says which Codex Angelo
launched, and the worker's relay accepts exactly that answer. Fake children only (``test_native_runtime_bindings``:
negative pids, ``group=False``, no signal).
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_runtime_bindings import A, Case, FakeProcess, chat  # noqa: E402
from angelo_agent import agent_runtime, runtime_relay, runtime_service  # noqa: E402

LAUNCHED = {"kind": "bundled", "version": agent_runtime.PIN["version"]}


class LaunchAnswerThroughBindings(Case):
    def make(self, **keywords):
        service = runtime_service.RuntimeService(lambda s: self.hosted.get(s), spawn=lambda c, e, w: FakeProcess(),
                                                 platform="linux", secrets=lambda: (), hosted=lambda: list(self.hosted),
                                                 threads=lambda found: self.record,
                                                 executable=lambda kind, settings: "/x/codex", **keywords)
        service._codex = lambda request, found, settings, executable: (["/x/codex", "app-server"], {})
        service._relay = lambda child: ("fd:passed", None)
        self.addCleanup(service.close)
        return service

    def request(self):
        return {"op": "runtime.launch", "session": A, "provider_kind": "codex", "workspace": self.hosted[A].workspace}

    def test_a_bound_launch_and_an_adopted_prewarm_both_say_which_codex_was_launched(self):
        self.hosted[A] = chat()
        with mock.patch.object(agent_runtime, "launched", lambda path, environ=None: dict(LAUNCHED)):
            cold = self.service.launch(self.request(), self.hosted[A])
            self.assertTrue(self.service.prewarm(self.request()))
            warm = self.service.launch(self.request(), self.hosted[A])
        self.assertEqual((cold["warm"], cold["runtime"]), (False, LAUNCHED))
        self.assertEqual((warm["warm"], warm["runtime"]), (True, LAUNCHED))
        for answer in (cold, warm):
            self.assertEqual(runtime_relay.lead_runtime(answer["runtime"]), LAUNCHED)   # The worker takes it.

    def test_a_codex_angelo_cannot_vouch_for_launches_nothing(self):
        self.hosted[A] = chat()
        refused = runtime_service.RpcError("incomplete")
        with mock.patch.object(agent_runtime, "launched", mock.Mock(side_effect=refused)):
            with self.assertRaises(runtime_service.RpcError):
                self.service.launch(self.request(), self.hosted[A])
        self.assertEqual(self.service.children(), [])


if __name__ == "__main__":
    unittest.main()
