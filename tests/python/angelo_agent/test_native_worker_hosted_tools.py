# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker Blender that hosts its chats (design A′) gives them what a lanes worker chat gets in Angelo.

The worker runs in background, where the machine check never passes, so before R7 its chats were offered no progress
tool ("angelo_workers_progress is not available in this request", test_blender_live_view@hosted), every tool of the
build rather than the worker's set, and no worker briefing. ``parallel_access.in_worker`` (set by the lead for a
hosting worker only) decides it instead. Without Blender: the tool list is checked in a fresh interpreter, since it is
built when the add-on is imported.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import agent_guidance, parallel_access, worker_process, worker_spec  # noqa: E402

FORCED = "ANGELO_ENABLE_PARALLEL_AGENTS"
WORKER = {"ANGELO_PARALLEL_WORKER": "1", "ANGELO_HEADLESS": "1"}


def chat():
    return SimpleNamespace(state={"id": "c"}, checkpoints=SimpleNamespace(session_id="c"))


def declared(environ):
    """``tool_specs.TOOL_MAP``'s names in a fresh interpreter under ``environ`` (no Blender)."""
    code = ("import json, sys; sys.path.insert(0, %r); from angelo_agent.tool_specs import TOOL_MAP; "
            "print(json.dumps(sorted(TOOL_MAP)))" % str(ADDONS))
    env = {key: value for key, value in os.environ.items() if not key.startswith("ANGELO_")}
    out = subprocess.run([sys.executable, "-B", "-I", "-c", code], env=dict(env, **environ), capture_output=True,
                         text=True, timeout=120, check=True)
    return set(json.loads(out.stdout.splitlines()[-1]))


class HostedWorkerTools(unittest.TestCase):
    def test_only_a_hosting_worker_is_a_worker_process(self):
        for environ, expected in ((WORKER, True), ({"ANGELO_PARALLEL_WORKER": "1"}, False),
                                  ({"ANGELO_HEADLESS": "1"}, False),
                                  ({"ANGELO_PARALLEL_WORKER": "0", "ANGELO_HEADLESS": "1"}, False)):
            with patch.dict(os.environ, environ):
                for name in set(WORKER) - set(environ):
                    os.environ.pop(name, None)
                self.assertIs(parallel_access.in_worker(), expected, environ)

    def test_the_lead_marks_only_a_worker_that_hosts_its_chats(self):
        hosting = worker_process.worker_environment({"PATH": "/bin"}, "/p", platform="darwin")
        lanes = worker_process.worker_environment({"PATH": "/bin", "ANGELO_WORKER_CHAT_HOST": "0"}, "/p",
                                                  platform="darwin")
        self.assertEqual((hosting.get("ANGELO_PARALLEL_WORKER"), hosting["ANGELO_HEADLESS"]), ("1", "1"))
        self.assertNotIn("ANGELO_PARALLEL_WORKER", lanes)
        self.assertNotIn(FORCED, hosting)   # Never the lead's family, and never the test-only force switch.

    def test_a_hosted_chat_is_offered_the_worker_set_with_its_progress(self):
        names = {"angelo_scene_program", "angelo_inspect_scene", "angelo_job_start"} | worker_spec.PART_NAMES
        with patch.dict(os.environ, dict(WORKER, **{FORCED: "0"})):
            offered = parallel_access.for_chat(None, names, chat())   # Any chat of this process is a worker's own.
            self.assertTrue(parallel_access.worker_chat(chat()))
        self.assertTrue(worker_spec.PART_NAMES <= offered)
        self.assertIn("angelo_scene_program", offered)
        self.assertNotIn("angelo_job_start", offered)   # Not one a worker runs: never offered.
        self.assertFalse(offered & worker_spec.WORKER_NAMES)
        with patch.dict(os.environ, {FORCED: "0", "ANGELO_PARALLEL_WORKER": "0"}):
            self.assertIsNone(parallel_access.for_chat(None, names, chat()))   # Off and no worker: as before.

    def test_a_hosted_chat_gets_the_worker_briefing(self):
        owner = SimpleNamespace(host=chat(), allowed_tools=frozenset(worker_spec.PART_NAMES))
        with patch.dict(os.environ, dict(WORKER, **{FORCED: "0"})):
            self.assertEqual(agent_guidance.lead_guidance(owner), worker_spec.WORKER_BRIEFING)
        with patch.dict(os.environ, {FORCED: "0", "ANGELO_PARALLEL_WORKER": "0"}):
            self.assertEqual(agent_guidance.lead_guidance(owner), "")

    def test_a_hosting_worker_declares_only_a_workers_own_tool(self):
        worker = declared(dict(WORKER, **{FORCED: "0"}))
        off = declared({FORCED: "0"})
        self.assertEqual(worker - off, set(worker_spec.PART_NAMES))
        self.assertFalse(worker & worker_spec.WORKER_NAMES)
        self.assertFalse(off & worker_spec.FAMILY_NAMES)   # Off and no worker: byte-identical to before.


if __name__ == "__main__":
    unittest.main()
