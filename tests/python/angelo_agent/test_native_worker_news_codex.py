# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker news through the real Codex dynamic-call path (``providers`` packing, ``codex_delivery`` reply): told only
when the payload carrying it was written, never when the write fails or an error payload replaces it (break-it
OVH2-M-2). No Blender or provider: the RPC is a recorder (its ``respond`` is the transport write)."""

import json
import sys
from pathlib import Path
from unittest import mock
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import parallel_sessions, worker_news, worker_sessions  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget  # noqa: E402
from test_native_codex_launch import ProviderFixture  # noqa: E402

FIELD = worker_news.FIELD


class CodexNewsDeliveryTests(ProviderFixture):
    def setUp(self):
        super().setUp()
        lead = self.c.state["id"]
        self.done = {"w1": True, "w2": False}   # Worker 1 finished; worker 2 still builds.
        patches = [mock.patch.object(worker_sessions, "_started", {lead: ["w1", "w2"]}),
                   mock.patch.object(worker_sessions, "_parts", {"w1": (1, "Sofa", Path("/a")),
                                                                 "w2": (2, "Chair", Path("/b"))}),
                   mock.patch.object(worker_sessions, "finished", lambda s: self.done[s]),
                   mock.patch.object(parallel_sessions, "_merged", {}),
                   mock.patch.object(parallel_sessions, "_merging", set())]
        for name in ("_told", "_told_cards", "_turn", "_staged"):
            if hasattr(worker_news, name):   # Each table this build has.
                patches.append(mock.patch.object(worker_news, name, {}))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lead = lead

    def execute(self, name, arguments):
        return {"scene": "S", "object_count": 0, "objects": [], "read_only": True, "truncated": False}

    def precondition(self, *args):
        return None

    def call(self, identity="search"):
        self.p._request(identity, "item/tool/call", {"threadId": "native-thread", "turnId": "turn-a",
                        "callId": identity, "tool": "angelo_scene_search",
                        "arguments": {"coverage": "PAGE", "query": "Cube", "limit": 4}})
        self.c.pump_tools(self, self.c.document, budget=ToolDispatchBudget(seconds=60))
        return [payload for _identity, payload in self.p.rpc.responses]

    def test_a_written_reply_carries_the_news_and_tells_it_once(self):
        payload, = self.call()
        self.assertEqual(json.loads(payload["contentItems"][0]["text"])[FIELD], ["1 Sofa"])
        self.assertEqual(worker_news.fresh(self.lead), [])
        _first, second = self.call("again")
        self.assertNotIn(FIELD, json.loads(second["contentItems"][0]["text"]))   # Exactly once.

    def test_a_reply_whose_write_fails_tells_nothing(self):
        def broken(identity, payload):
            raise OSError("Broken pipe")
        self.p.rpc.respond = broken
        self.call()
        self.assertEqual(worker_news.fresh(self.lead), ["w1"])

    def test_an_error_payload_that_replaces_the_packed_copy_tells_nothing(self):
        with mock.patch("angelo_agent.codex_delivery.prepare_observation", side_effect=OSError("unreadable image")):
            payload, = self.call()
        self.assertFalse(payload["success"])
        self.assertNotIn(FIELD, json.dumps(payload))
        self.assertEqual(worker_news.fresh(self.lead), ["w1"])


if __name__ == "__main__":
    unittest.main()
