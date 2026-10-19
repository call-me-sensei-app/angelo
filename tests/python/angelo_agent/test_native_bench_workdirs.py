# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Both sides' runtimes work where no project context reaches them (``bench_workdirs``).

A run folder inside a checkout put the checkout's ``CLAUDE.md`` and git status
into the MCP side's first message, and the git status into Angelo's. These
tests build a stand-in checkout (a ``.git`` folder and a ``CLAUDE.md``) in a
temporary folder and check where each harness starts its runtimes, and that
a trial is refused before anything starts when its folder would see context.
No Blender, client, account or model is started.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_codex as codex_mcp  # noqa: E402
import bench_mcp_trial as mcp  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import bench_workdirs as workdirs  # noqa: E402
import eval_live_agent  # noqa: E402


class Checkout:
    """A temporary stand-in checkout: ``.git``, a ``CLAUDE.md`` and a run folder deep inside it."""

    def __init__(self, test):
        self.temp = tempfile.TemporaryDirectory()
        test.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name).resolve() / "checkout"
        (self.top / ".git").mkdir(parents=True)
        (self.top / "CLAUDE.md").write_text("# Someone else's project rules\n")
        self.run = self.top / "benchmark" / "run-01"
        self.run.mkdir(parents=True)
        # Runtime folders a run makes go under this run's own temp folder: point the temp base inside the test.
        self.clean_base = Path(self.temp.name).resolve() / "clean-temp"
        self.clean_base.mkdir()
        context = mock.patch.dict(os.environ, {workdirs.ROOT_ENV: str(self.clean_base)})
        context.start()
        test.addCleanup(context.stop)


class VisibleTests(unittest.TestCase):
    def test_context_in_the_folder_or_any_parent_is_visible(self):
        checkout = Checkout(self)
        found = workdirs.visible(checkout.run / "mcp-cwd")
        self.assertIn(str(checkout.top / ".git"), found)
        self.assertIn(str(checkout.top / "CLAUDE.md"), found)
        self.assertEqual(workdirs.visible(checkout.clean_base / "anything"), [])

    def test_a_run_inside_a_checkout_keeps_its_runtime_folders_outside_it(self):
        checkout = Checkout(self)
        self.assertTrue(str(workdirs.root(checkout.run)).startswith(str(checkout.clean_base)))
        self.assertEqual(workdirs.root(checkout.clean_base / "run"), checkout.clean_base / "run",
                         "A clean run folder keeps its runtime folders")
        with self.assertRaisesRegex(workdirs.Unclean, "would read project context"):
            workdirs.fresh(checkout.run / "mcp-cwd")

    def test_a_memory_file_in_the_clients_own_home_is_refused(self):
        checkout = Checkout(self)
        home = checkout.clean_base / "claude-profile"
        home.mkdir()
        (home / "CLAUDE.md").write_text("Always answer in French.\n")
        with self.assertRaisesRegex(workdirs.Unclean, "memory file"):
            workdirs.fresh(checkout.clean_base / "mcp-cwd", home, "claude")
        (checkout.clean_base / "codex-home").mkdir()
        (checkout.clean_base / "codex-home" / "AGENTS.md").write_text("x")
        self.assertTrue(workdirs.problems(checkout.clean_base / "cwd", checkout.clean_base / "codex-home", "codex"))


class BenchTests(unittest.TestCase):
    def setUp(self):
        from test_native_addon_guard import matching_app
        matching_app(self)   # The fixture app bundles no add-on; the bench's add-on pin has its own tests.

    def args(self, **values):
        defaults = dict(stock_blender="", mcp_server_dir="", mcp_addon_dir="", claude="claude",
                        claude_config_dir="", model="claude-opus-5-5", effort="low", task_timeout=600)
        return SimpleNamespace(**dict(defaults, **values))

    def test_the_mcp_sides_client_works_outside_the_checkout(self):
        checkout = Checkout(self)
        config = bench.mcp_config(self.args(), checkout.run)
        self.assertEqual(workdirs.visible(config["claude_cwd"]), [], config["claude_cwd"])

    def test_an_mcp_trial_in_an_unclean_folder_is_refused_before_anything_starts(self):
        checkout = Checkout(self)
        for module, base in ((mcp, mcp), (codex_mcp, codex_mcp.trial_base)):
            with self.subTest(runtime=module.__name__):
                folder = checkout.clean_base / ("trial-" + module.__name__)
                config = {"claude_cwd": str(checkout.run / "mcp-cwd"), "task_set": "default", "claude_config_dir": "",
                          "codex_home": ""}
                with mock.patch.object(base, "launch_blender", side_effect=AssertionError("Blender was started")):
                    output = module.run_trial(config, 1, folder)
                self.assertIn("would read project context", output.get("error", ""))

    def test_angelos_runtimes_work_outside_the_checkout_and_their_state_comes_back(self):
        checkout = Checkout(self)
        output = checkout.run / "angelo"
        output.mkdir()
        seen = {}

        def run(command, cwd, env, **kwargs):
            state = Path(env["ANGELO_STATE_DIR"])
            seen["state"] = state
            (state / "workspace").mkdir(parents=True, exist_ok=True)
            (state / "workspace" / "made.txt").write_text("made by the trial")
            return SimpleNamespace(returncode=0)

        args = SimpleNamespace(stable_state_dir="", task_timeout=60, task_set="default", demo_record=False,
                               input_isolation="on", connection_id="x", model="", effort="low")
        with mock.patch.object(eval_live_agent.subprocess, "run", run), \
                mock.patch.object(eval_live_agent, "trial_environment",
                                  lambda folder, accounts, args, trial, state=None, **k: {
                                      "ANGELO_STATE_DIR": str(state or folder / "state")}):
            try:
                eval_live_agent.run_trial(Path("/bin/true"), output, output / "accounts", args, 1, 1)
            except Exception:
                pass   # Only where the runtime worked and what came back is checked here.
        self.assertEqual(workdirs.visible(seen["state"]), [], seen["state"])
        self.assertEqual((output / "trial-01" / "state" / "workspace" / "made.txt").read_text(), "made by the trial")


if __name__ == "__main__":
    unittest.main()
