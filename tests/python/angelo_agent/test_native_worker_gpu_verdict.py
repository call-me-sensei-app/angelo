# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted worker chat's first view takes Angelo's offscreen verdict from ``chat.open``, as the lane route passes it
with each view, so no hosted worker starts a probe child Angelo already knows the answer to (R2a latency risk). With no
verdict, or a malformed one, the worker probes as before (fail closed). No Blender.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import remote_executor, worker_chat_lifecycle as lifecycle, worker_host  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.worker_chat_executor import confined  # noqa: E402
from test_native_worker_chat_host import Fixture  # noqa: E402
from test_native_worker_chat_lifecycle import Base, OpWorker, chat  # noqa: E402
from test_native_worker_python_route import FakeBlender, ScriptShadow, ui  # noqa: E402

BINARY = "/Applications/Angelo.app/Contents/MacOS/Angelo"


class LeadSendsItsVerdict(Base):
    def opened(self, verdicts):
        worker = OpWorker()
        worker.binary = BINARY
        lifecycle._hosted.clear()   # Each open is of a chat no other worker hosts (Base patches a fresh table).
        with patch.dict(remote_executor.OFFSCREEN_VERDICTS, verdicts, clear=True):
            lifecycle.open_chat(chat(), worker, resume=False)
        return worker.requests[0]["gpu_verdict"]

    def test_the_open_carries_the_verdict_for_this_workers_binary(self):
        self.assertEqual(self.opened({BINARY: [False, "no display adapter"]}), [False, "no display adapter"])
        self.assertEqual(self.opened({BINARY: [True, ""]}), [True, ""])

    def test_without_one_for_this_binary_the_open_carries_none(self):
        self.assertIsNone(self.opened({}))
        self.assertIsNone(self.opened({"/another/Angelo": [True, ""]}))


class WorkerUsesTheOpensVerdict(unittest.TestCase):
    def view(self, verdict):
        ui._document_serial = "serial"
        host = worker_host.WorkerHost(ui, None, endpoint=None, part="/w/part.blend")
        host.bpy = FakeBlender("/w/part.blend", ScriptShadow(None))
        drawn, probes, inits = [], [], []
        inner = SimpleNamespace(execute=lambda name, arguments: drawn.append(name) or {"pictures": []})
        executor = confined(inner, SimpleNamespace(chat_host=SimpleNamespace(host=host, save_due=False),
                                                   state={"id": "chat-1"}, gpu_verdict=verdict))
        with patch.object(worker_host, "offscreen_draw_usable", lambda bpy: probes.append(1) or (True, "")), \
                patch.dict(sys.modules, {"gpu": SimpleNamespace(init=lambda: inits.append(1))}):
            try:
                executor.execute("angelo_view", {"views": ["ISO"]})
            except RefusalError as error:
                return drawn, probes, inits, error
        return drawn, probes, inits, None

    def test_a_usable_verdict_draws_with_no_probe(self):
        self.assertEqual(self.view([True, ""]), (["angelo_view"], [], [1], None))

    def test_an_unusable_verdict_refuses_with_its_reason_and_no_probe(self):
        drawn, probes, inits, error = self.view([False, "no display adapter"])
        self.assertEqual((drawn, probes, inits), ([], [], []))
        self.assertEqual(error.code, "UNSUPPORTED")
        self.assertIn("no display adapter", str(error))

    def test_no_verdict_or_a_malformed_one_probes_as_before(self):
        for verdict in (None, ["yes", ""], [True], "true", {"usable": True}):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.view(verdict), (["angelo_view"], [1], [1], None))


class WorkerKeepsTheOpensVerdict(Fixture):
    def test_the_hosted_chat_keeps_the_verdict_its_open_carried(self):
        self.assertTrue(self.open(gpu_verdict=[True, ""])["ok"])
        self.assertEqual(self.chats.hosted(self.session).gpu_verdict, [True, ""])

    def test_an_open_without_one_leaves_the_worker_to_probe(self):
        self.assertTrue(self.open()["ok"])
        self.assertIsNone(self.chats.hosted(self.session).gpu_verdict)


if __name__ == "__main__":
    unittest.main()
