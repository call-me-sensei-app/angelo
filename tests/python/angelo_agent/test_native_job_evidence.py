# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a finished production job tells the model, on both runtimes, with stand-in providers.

A job's result reaches a model in the jobs block of the chat's next message. A
real ``Controller`` composes that message from finished job records shaped as
the runners write them (a high-to-low bake, a UDIM bake, a stereo render with
File Output nodes, a simulation's disk cache); the message then goes through
each runtime's real provider to its stand-in: ``ClaudeProvider`` to a fixture
Claude Code process (stream-json), ``CodexProvider`` to a scripted app-server
(``turn/start``). Checked on what each stand-in received: every output with
its identity (image resource ID and map, tile, view, node and item, cache), the
output folder a still of several files really has, and no local path. The start
result both providers hand a model is checked the same way.

No executable, account, network or inference. The records' content against
real Blender jobs is covered by the ``test_blender_production_*``,
``test_blender_udim_bakes`` and ``test_blender_disk_simulation`` suites.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger, job_manager  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.job_store import JobStore, new_record  # noqa: E402
from angelo_agent.provider_registry import runtime_kind  # noqa: E402
from angelo_agent.providers import CodexProvider, tool_content  # noqa: E402
from claude_protocol_fixtures import ClaudeAdapterFixture  # noqa: E402
from test_native_codex_plan import ScriptedAppServer  # noqa: E402

IMAGE = "dat:" + "c" * 32 + ":IMAGE:41"
LOCAL = "/Users/someone/Projects/shot"  # Where the job really wrote: never part of what a model reads.
RECEIPTS = {
    "j1": ("BAKE_TEXTURE", {"output": "bakes/normal", "bake_type": "NORMAL"},
           {"images": ["Low Normal"], "files": 1,
            "outputs": [{"image": IMAGE, "name": "Low Normal", "map": "NORMAL", "file": "Low_Normal.png"}]}),
    "j2": ("BAKE_TEXTURE", {"output": "bakes/skin", "bake_type": "EMIT"},
           {"images": ["Skin"], "files": 3, "outputs": [
               {"image": IMAGE, "name": "Skin", "tile": 1001, "map": "EMIT", "file": "Skin.1001.png"},
               {"image": IMAGE, "name": "Skin", "tile": 1002, "map": "EMIT", "file": "Skin.1002.png"},
               {"image": IMAGE, "name": "Skin", "tile": 1003, "file": "Skin.1003.png"}]}),
    "j3": ("RENDER_STILL", {"output": "renders/shot.png", "format": "PNG"},
           {"output": "renders/shot", "files": 5, "missing": ["Constant/Flat0001_L.png"], "outputs": [
               {"file": "frame_0001_L.png", "view": "left"}, {"file": "frame_0001_R.png", "view": "right"},
               {"file": "Passes/Mask0001_L.png", "node": "Passes", "slot": "Mask", "view": "left"},
               {"file": "Layers/layers0001.exr", "node": "Layers", "layers": ["Beauty", "Mask"],
                "views": ["left", "right"]}]}),
    "j4": ("BAKE_SIMULATION", {"output": "caches/smoke"},
           {"objects": 1, "files": 20, "outputs": [
               {"file": "Simulation/861001734", "cache": "Simulation/861001734", "name": "Smoke", "frames": [1, 10]}]}),
}


class FinishedJobs:
    """The application's job manager as the jobs block reads it: a store of finished records."""

    def __init__(self, store):
        self.store, self.queue = store, []

    def active(self):
        return None

    def record(self, identity):
        return self.store.get(identity)

    def locks_interface(self):
        return False


class EvidenceFixture:
    """A real controller whose chat started the four jobs (no TestCase: each runtime has its own cases)."""

    def set_up_jobs(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = Controller(self.temp.name)
        self.addCleanup(self.c.close)
        self.c.set_document(("factory-runtime", 17))
        store, now = JobStore(Path(self.temp.name) / "jobs"), time.time()
        for identity, (kind, request, receipt) in RECEIPTS.items():
            record = new_record(identity, self.c.state["id"], "call-" + identity,
                                dict(request, kind=kind, minutes=30.0), now=now - 90)
            record.update(status="done", started=now - 60, ended=now, receipt=receipt,  # A message lists a day's jobs.
                          detail={"output_path": LOCAL + "/" + request["output"], "staging": LOCAL + "/.angelo-job-" + identity})
            store.add(record)
        job_manager.install(FinishedJobs(store))
        self.addCleanup(job_manager.install, None)

    def message(self):
        """The next message of the chat as the controller composes it for its runtime's native context."""
        return self.c._context_blocks(self.c.runtime_context(), "What did the jobs write?", None, None, None)

    def assert_evidence(self, text):
        self.assertIn(context_ledger.JOBS_HEADER, text)
        block = text[text.index(context_ledger.JOBS_HEADER) + len(context_ledger.JOBS_HEADER):]
        entries = {entry["job"]: entry for entry in json.JSONDecoder().raw_decode(block)[0]}
        self.assertEqual(sorted(entries), ["j1", "j2", "j3", "j4"])
        for identity, (_kind, _request, receipt) in RECEIPTS.items():
            with self.subTest(job=identity):
                self.assertEqual(entries[identity]["outputs"], receipt["outputs"])
                self.assertEqual(entries[identity]["status"], "done")
        self.assertEqual(entries["j1"]["outputs"][0]["image"], IMAGE)       # Usable in the next node edit.
        self.assertEqual([output.get("tile") for output in entries["j2"]["outputs"]], [1001, 1002, 1003])
        self.assertNotIn("map", entries["j2"]["outputs"][2])                 # A tile the bake did not write.
        self.assertEqual((entries["j3"]["output"], entries["j3"]["missing"]),
                         ("renders/shot", ["Constant/Flat0001_L.png"]))      # The folder, not the asked file.
        self.assertEqual(entries["j4"]["outputs"][0]["frames"], [1, 10])
        self.assertNotIn("images", entries["j1"])                            # Named once, with each output.
        self.assertNotIn(LOCAL, text)
        self.assertNotIn(".angelo-job-", text)


class ClaudeEvidenceTests(EvidenceFixture, ClaudeAdapterFixture, unittest.TestCase):
    def setUp(self):
        ClaudeAdapterFixture.setUp(self)
        self.set_up_jobs()

    def test_the_jobs_block_reaches_claude_with_every_output(self):
        self.c.state["provider"] = "claude_subscription"
        self.assertEqual(runtime_kind(self.c.state["provider"]), "claude")
        text = self.message()
        self.start("native-claude")
        self.turn("native-claude", text)
        sent = [event for event in self.process.stdin.messages if event["type"] == "user"]
        self.assertEqual(len(sent), 1)
        content = sent[0]["message"]["content"]
        received = content if isinstance(content, str) else "".join(part.get("text", "") for part in content)
        self.assert_evidence(received)


class CodexEvidenceTests(EvidenceFixture, unittest.TestCase):
    def setUp(self):
        self.set_up_jobs()
        self.c.state["native_id"] = "native-thread"
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.context = self.c.runtime_context()
        self.p = CodexProvider(self.context, {})
        self.addCleanup(self.p.close)
        self.p.thread_id = "native-thread"
        self.p.bind_request(self.context)

    def test_the_jobs_block_reaches_codex_with_every_output(self):
        self.assertEqual(runtime_kind(self.c.state["provider"]), "codex")  # The runtime of the GPT models.
        text = self.message()
        self.p.rpc = ScriptedAppServer(self.p, [[("message", "Four jobs finished.")]])
        self.assertEqual(self.p.run("native-thread", text), "completed")
        (start,) = [parameters for method, parameters in self.p.rpc.requests if method == "turn/start"]
        received = "".join(item.get("text", "") for item in start["input"])
        self.assert_evidence(received)

    def test_the_start_result_names_the_real_output_on_both_runtimes(self):
        """Both providers hand a tool result to the model through ``tool_content``."""
        result = {"job": "j3", "status": "running", "kind": "RENDER_STILL", "output": "renders/shot",
                  "note": "Runs in the background. Its progress and result arrive in a later message's jobs block; "
                          "do not wait or poll. Scene changes are refused until it ends."}
        (item,) = tool_content(self.context, "angelo_job_start", result)
        self.assertEqual(item["type"], "inputText")
        self.assertEqual(json.loads(item["text"]), result)


if __name__ == "__main__":
    unittest.main()
