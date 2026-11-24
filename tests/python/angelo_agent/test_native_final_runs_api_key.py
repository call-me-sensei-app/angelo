# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gates of record on a pay-per-token API-key account: Gemini 4 Carbon (1.1.0, "Full gate like others") and Gemini 4
Neon (2.1.0: the maintainer's gates of record are Sol, Opus and Neon), both on the one Gemini API account, Windows only.

The campaign's subscriptions (Claude, ChatGPT) share one connections folder. An API-key connection runs in place
from a folder holding only it (its key is in that folder's key-store item: ``eval_live_agent.isolated_connection``),
billed with ``--allow-api-key-connection`` and never with the ChatGPT ``--codex-home``. Every coding API-key mode of
the registry is one the harness accepts. Nothing is launched and no key is read.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_vs_angelo as bench  # noqa: E402
import eval_live_agent as live_agent  # noqa: E402
import final_runs as runs  # noqa: E402
import final_runs_plan as plan  # noqa: E402
import final_runs_report as runs_report  # noqa: E402
from angelo_agent import provider_registry as registry  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from test_native_final_runs import CONNECTIONS, config  # noqa: E402


def gemini_folder(root, extra=False):
    """A folder holding one Gemini API coding connection (no key: nothing reads one here)."""
    folder = Path(root) / "gemini-connections"
    store = ConnectionStore(folder)
    record = store.add("GOOGLE", "API_KEY", name="Gemini (bench)")
    if extra:
        store.add("OPENAI", "SUBSCRIPTION", name="ChatGPT")
    store.save()
    return folder, record["id"]


class CarbonPlanTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_each_gemini_model_is_gated_like_the_others_on_its_own_account(self):
        for key, model in (("carbon", "gemini-4-carbon"), ("neon", "gemini-4-neon")):
            with self.subTest(model=key):
                self.assertIn(key, plan.GATED)
                steps = plan.select(plan.campaign("windows"), ["gate-%s-*" % key, "smoke-%s-*" % key])
                self.assertEqual([s["id"] for s in steps], [name % key for name in (
                    "gate-%s-default", "gate-%s-long", "smoke-%s-gate7", "smoke-%s-survey", "smoke-%s-ask")])
                self.assertEqual({(s["model"], s["runtime"], s["account"]) for s in steps}, {(model, "codex", "gemini")})
                self.assertFalse(plan.mcp_side(steps))
                self.assertEqual(plan.accounts(steps), ["gemini"])
                self.assertEqual(plan.select(plan.campaign("macos"), ["gate-%s-*" % key, "smoke-%s-*" % key]), [],
                                 "the Gemini gates of record are the Windows MSI's")
        self.assertEqual(plan.API_KEY_ACCOUNTS["gemini"], "gemini_api")
        self.assertIn("gemini_api", registry.SPECS, "the account's mode is the product's own")

    def test_every_api_key_gate_runs_a_model_its_mode_offers(self):
        """A gate on an API-key account measures a model the product offers on that account's connection."""
        for key in plan.GATED:
            facts = plan.MODELS[key]
            if facts["account"] in plan.API_KEY_ACCOUNTS:
                with self.subTest(model=key):
                    spec = registry.SPECS[plan.API_KEY_ACCOUNTS[facts["account"]]]
                    self.assertIn(facts["model"], [m["id"] for m in spec["models"]])
                    self.assertEqual(facts["runtime"], spec["runtime"])

    def test_an_api_key_step_runs_in_place_and_never_with_the_chatgpt_profile(self):
        folder, identity = gemini_folder(self.root)
        settings = config(self.root, api_key_dirs={"gemini": str(folder)},
                          connections=dict(CONNECTIONS, gemini=identity))
        for key, model in (("carbon", "gemini-4-carbon"), ("neon", "gemini-4-neon")):
            with self.subTest(model=key):
                gate = plan.select(plan.campaign("windows"), ["gate-%s-long" % key])[0]
                line = runs.command(gate, settings, self.root / "out")
                self.assertEqual(line[line.index("--connection-id") + 1], identity)
                self.assertEqual(line[line.index("--source-connections-dir") + 1], str(folder))
                self.assertIn("--angelo-allow-api-key-connection", line)
                self.assertNotIn("--codex-home", line)
                self.assertEqual(line[line.index("--model") + 1], model)
                smoke = plan.select(plan.campaign("windows"), ["smoke-%s-gate7" % key])[0]
                line = runs.command(smoke, settings, self.root / "out")
                self.assertIn("--allow-api-key-connection", line)
                self.assertNotIn("--codex-home", line)
                self.assertEqual(line[line.index("--model") + 1], model)
        sol = plan.select(plan.campaign("windows"), ["gate-sol-long"])[0]
        line = runs.command(sol, settings, self.root / "out")
        self.assertEqual(line[line.index("--codex-home") + 1], "/codex-profile/x", "subscriptions unchanged")
        self.assertNotIn("--angelo-allow-api-key-connection", line)

    def test_a_step_without_its_folder_refuses_to_build(self):
        gate = plan.select(plan.campaign("windows"), ["gate-carbon-default"])[0]
        with self.assertRaises(SystemExit):
            runs.command(gate, config(self.root, connections=dict(CONNECTIONS, gemini="g"), api_key_dirs={}),
                         self.root / "out")

    def test_the_folder_must_hold_only_its_one_connection(self):
        folder, identity = gemini_folder(self.root)
        self.assertEqual(runs_report.api_key_connection(str(folder), "gemini_api"), identity)
        crowded, _ = gemini_folder(self.root / "crowded", extra=True)
        for wrong in (str(crowded), str(self.root / "missing"), ""):
            with self.assertRaises(SystemExit):
                runs_report.api_key_connection(wrong, "gemini_api")

    def test_check_sign_in_asks_only_the_machines_accounts(self):
        folder, identity = gemini_folder(self.root)
        result = runs_report.check_sign_in(str(self.root / "no-subscriptions"), accounts=("gemini",),
                                           api_key_dirs={"gemini": str(folder)})
        self.assertEqual((result["ready"], result["connections"]), (True, {"gemini": identity}))
        self.assertFalse(runs_report.check_sign_in("", accounts=("gemini",), api_key_dirs={})["ready"])

    def test_a_gemini_only_campaign_inits_with_its_folder_alone(self):
        folder, identity = gemini_folder(self.root)
        candidate = Path(config(self.root)["candidate"])
        for key in ("carbon", "neon"):
            with self.subTest(model=key):
                argv = ["init", "--output", str(self.root / ("camp-" + key)), "--candidate", str(candidate),
                        "--platform", "windows", "--discover-connections", "--gemini-connections-dir", str(folder),
                        "--only", "gate-%s-*" % key, "--only", "smoke-%s-*" % key]
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    runs.main(argv)
                saved = json.loads((self.root / ("camp-" + key) / runs.CONFIG).read_text())
                self.assertEqual(saved["connections"], {"gemini": identity})
                self.assertEqual(saved["api_key_dirs"], {"gemini": str(folder.resolve())})


def after_script(line, script):
    """The arguments a command line passes to ``script`` (the harness's own parser reads them back)."""
    return line[next(i for i, arg in enumerate(line) if arg.endswith(script)) + 1:]


class GeminiTrialRequestsItsModelTests(unittest.TestCase):
    """Each Gemini gate's trial requests its own model, not the connection's default (Carbon): from the plan's step
    through the bench and the live eval to the chat's receipt route, the value gates_pass checks."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_every_gemini_gate_step_reaches_the_trial_with_its_model(self):
        folder, identity = gemini_folder(self.root)
        settings = config(self.root, api_key_dirs={"gemini": str(folder)}, connections=dict(CONNECTIONS, gemini=identity))
        steps = [s for s in plan.campaign("windows") if s["phase"] == "gate" and s["account"] == "gemini"]
        self.assertEqual({s["model"] for s in steps}, {"gemini-4-carbon", "gemini-4-neon"})
        for step in steps:
            with self.subTest(step=step["id"]):
                line = runs.command(step, settings, self.root / "out" / step["id"], billed=False)
                if step["tool"] == "bench":
                    args = bench.parse(after_script(line, "bench_mcp_vs_angelo.py"))
                    self.assertEqual(args.model, step["model"])
                    line, _ = bench.angelo_command(args, self.root / "out", self.root / "out" / "trial")
                trial = live_agent.parse(after_script(line, "eval_live_agent.py"))
                environment = live_agent.trial_environment(self.root / "t", self.root / "a", trial, 1)
                self.assertEqual(environment["ANGELO_EVAL_MODEL"], step["model"])

    def test_the_receipt_route_names_the_requested_model_over_the_connections_default(self):
        from angelo_agent.controller import Controller
        models = registry.SPECS["gemini_api"]["models"]
        self.assertEqual(next(m["id"] for m in models if m["default"]), "gemini-4-carbon")
        for model in ("gemini-4-neon", "gemini-4-carbon"):
            with self.subTest(model=model):
                chat = Controller(str(self.root / model))
                self.addCleanup(chat.close)
                chat.set_document(("scratch", 1))
                chat.choose_connection("gemini_api", {"model": ""}, "", "g", models, "")   # The connection's default.
                chat.set_options(model=model)   # As eval_live_agent_session.configure does with ANGELO_EVAL_MODEL.
                chat.turn_options = dict(chat.state["options"])
                chat._begin_metrics()
                chat._record_route()
                route = chat._request_metrics.snapshot()["route"]
                self.assertEqual((route["mode"], route["requested_model"]), ("gemini_api", model))


class HarnessAcceptsEveryApiKeyModeTests(unittest.TestCase):
    def test_every_coding_api_key_mode_of_the_registry(self):
        expected = {m for m, s in registry.SPECS.items() if s["group"] == registry.CODING and s["method"] == "API_KEY"}
        self.assertEqual(live_agent.API_KEY_MODES, expected)
        self.assertIn("gemini_api", live_agent.API_KEY_MODES)

    def test_a_gemini_connection_runs_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            folder, identity = gemini_folder(root)
            found = live_agent.isolated_connection(folder, identity, Path(root) / "copy", allow_api_key=True)
            self.assertEqual((found["mode"], found["in_place"]), ("gemini_api", str(folder.resolve())))
            with self.assertRaises(live_agent.Refusal):
                live_agent.isolated_connection(folder, identity, Path(root) / "copy2")

    def test_the_bench_passes_the_api_key_allowance_and_no_profile(self):
        args = bench.parse(["--sides", "angelo", "--runtime", "codex", "--connection-id", "g",
                                 "--angelo-allow-api-key-connection", "--source-connections-dir", "/g",
                                 "--angelo-binary", "/Angelo", "--model", "gemini-4-carbon", "--output", "/o"])
        command, _ = bench.angelo_command(args, Path("/o"), Path("/o/trial"))
        self.assertIn("--allow-api-key-connection", command)
        self.assertNotIn("--codex-home", command)
        for wrong in (["--sides", "both"], ["--codex-home", "/codex-profile/x"]):
            bad = bench.parse(["--runtime", "codex", "--connection-id", "g", "--angelo-allow-api-key-connection",
                                    "--sides", "angelo", "--output", "/o"] + wrong)
            problems, _ = bench.preflight(bad)
            self.assertTrue(any("--angelo-allow-api-key-connection" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
