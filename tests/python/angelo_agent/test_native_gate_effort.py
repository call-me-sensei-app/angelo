# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release gates run every model at the app's shipping effort; a head-to-head names one effort for both sides.
No Blender, nothing launched.

Maintainer ruling 2026-11-12, quoted: "Shipping default (high) (Recommended)". Every model defaults to high reasoning
effort in 1.1.0. The gate harness had pinned ``--effort low`` on every step (final_runs_plan.COMMON, final_runs'
eval step), and both drivers defaulted to low and set it in the chat. A green gate then measured an effort no person
gets, and nothing said so.

Pinned here, for every gate step of every platform's campaign:
* no command passes ``--effort``;
* both drivers' default is "default", which sets no effort in the chat, so it runs at the app's own default and
  the session records the effort the chat resolved;
* a head-to-head (an MCP side) names one concrete effort for both sides; ``bench_mcp_vs_angelo`` refuses "default"
  there, since a stock CLI has no shipping default to match.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent  # noqa: E402
import final_runs as runs  # noqa: E402
import final_runs_plan as plan  # noqa: E402
from test_native_final_runs import config  # noqa: E402


def efforts(line):
    """Every value passed with ``--effort`` in one command line."""
    return [line[i + 1] for i, arg in enumerate(line) if arg == "--effort"]


class GateEffortTests(unittest.TestCase):
    def commands(self, platform):
        folder = tempfile.TemporaryDirectory(prefix="gate-effort-")
        self.addCleanup(folder.cleanup)
        settings = config(folder.name, platform=platform)
        for step in plan.campaign(platform):
            if step["tool"] != "pilot":
                yield step, runs.command(step, settings, Path(folder.name) / step["id"], billed=False)

    def test_no_gate_step_forces_an_effort(self):
        for platform in ("macos", "windows"):
            for step, line in self.commands(platform):
                if step["phase"] == "gate":
                    with self.subTest(platform=platform, step=step["id"]):
                        self.assertEqual(efforts(line), [], "a gate measures the effort the app ships")

    def test_a_head_to_head_names_one_concrete_effort_for_both_sides(self):
        for platform in ("macos", "windows"):
            for step, line in self.commands(platform):
                if "mcp" in step["sides"]:
                    with self.subTest(platform=platform, step=step["id"]):
                        found = efforts(line)
                        self.assertEqual(len(found), 1, found)
                        self.assertIn(found[0], ("low", "medium", "high"))

    def test_the_drivers_default_is_the_shipping_effort(self):
        self.assertEqual(eval_live_agent.parse(["--blender", "/A", "--model", "m", "--output", "/o"]).effort,
                         "default")
        self.assertEqual(bench.parse(["--angelo-binary", "/A", "--model", "m", "--sides", "angelo",
                                      "--output", "/o"]).effort, "default")
        self.assertEqual(bench.parse(["--angelo-binary", "/A", "--model", "m", "--sides", "both",
                                      "--output", "/o"]).effort, "low", "a head-to-head keeps one named effort")

    def test_a_head_to_head_refuses_the_shipping_default(self):
        with self.assertRaises(SystemExit):
            bench.parse(["--angelo-binary", "/A", "--model", "m", "--sides", "both", "--effort", "default",
                         "--output", "/o"])

    def test_the_session_sets_no_effort_when_asked_for_the_default(self):
        import eval_effort
        for value, expected in (("", {"fast": False}), ("default", {"fast": False}),
                                ("low", {"fast": False, "effort": "low"})):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"ANGELO_EVAL_EFFORT": value}):
                self.assertEqual(eval_effort.chat_options(fast=False), (expected, value or "default"))
        for name in ("eval_live_agent_session.py", "parallel_live_session.py"):
            with self.subTest(file=name):
                source = (HERE / name).read_text(encoding="utf-8")
                self.assertNotIn("ANGELO_EVAL_EFFORT", source, "the effort is read in eval_effort, nowhere else")
                calls = {node.func.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                         and node.func.value.id == "eval_effort"}
                self.assertEqual(calls, {"chat_options", "report"}, "the report names the effort the chat ran at")


if __name__ == "__main__":
    unittest.main()
