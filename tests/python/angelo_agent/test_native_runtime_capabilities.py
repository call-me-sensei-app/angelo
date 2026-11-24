# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release checks prove every provider capability the agent runtime pin lists (``provider_capabilities``), and the
pin lists every capability Angelo writes into a model provider (scripts/angelo/runtime_capabilities.py).

A custom provider's ``capabilities.<name>`` is in no app-server schema and Codex ignores a capability it does not
read, so a stale build would load the team's provider with ``request_compression = "zstd"`` and silently send plain
JSON. Measured on real binaries (2026-11-16, again on the 0.170.0 build 2026-11-18 and the 0.171.0 build 2026-11-20): the pinned fork's build loads ``unsupported`` and ``zstd`` and
refuses ``angelo-unpinned-value`` ("unknown variant"); stock Codex 0.163.0 loads the unknown value, and the check
refuses it. Dependency-free: a fake runner stands in for the binary.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_capabilities as capabilities  # noqa: E402

PIN = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))
ADDON = ROOT / "scripts/addons_core/angelo_agent"
WINDOWS = {name: (ROOT / "scripts/angelo" / name).read_text(encoding="utf-8")
           for name in ("windows_agent_runtime_check.ps1", "windows_release_toolchain.ps1")}


def fake_features_list(argv, read):
    """What a codex that reads ``read`` ({name: accepted values}) answers to ``capability_check_argv``: like Codex, it
    ignores a capability it does not read and refuses a value it does not know of one it reads."""
    declared = next(part for part in argv if ".capabilities." in part)
    name, value = declared.split(".capabilities.", 1)[1].split("=", 1)
    value = value.strip('"')
    if name in read and value not in read[name]:
        return SimpleNamespace(returncode=1, stdout="", stderr="Error: failed to load bootstrap configuration\n\n"
                               "Caused by:\n    unknown variant `%s`, expected one of %s" % (value, read[name]))
    return SimpleNamespace(returncode=0, stdout="request_compression  stable  true\n", stderr="")


def runner(read):
    calls = []

    def run(argv, check=False, timeout=None, env=None):
        calls.append((argv, env))
        return fake_features_list(argv, read)
    run.calls = calls
    return run


class CapabilityCheckTests(unittest.TestCase):
    codex = Path("/runtime/codex")
    env = {"CODEX_HOME": "/tmp/throwaway"}

    def test_a_build_reading_every_pinned_value_passes(self):
        pinned = capabilities.pinned_capabilities(PIN)
        run = runner(pinned)
        self.assertEqual(capabilities.capability_problems(self.codex, PIN, run, self.env), [])
        expected = sum(len(values) + 1 for values in pinned.values())
        self.assertEqual(len(run.calls), expected, "every pinned value of every capability, and one unpinned each")
        for argv, env in run.calls:
            self.assertEqual(argv[-2:], ["features", "list"], "offline: it only loads the configuration")
            self.assertIs(env, self.env, "the check's throwaway CODEX_HOME")
            self.assertNotIn("model_provider=", " ".join(argv), "the probe provider is never selected")
            self.assertIn('base_url="http://127.0.0.1:9/v1"', " ".join(argv), "and never reachable")

    def test_every_capability_and_value_is_checked_not_only_one(self):
        pin = {"provider_capabilities": {"request_compression": ["unsupported", "zstd"],
                                         "remote_compaction": ["unsupported", "responses"], "_about": "ignored"}}
        read = capabilities.pinned_capabilities(pin)
        self.assertEqual(capabilities.capability_problems(self.codex, pin, runner(read), self.env), [])
        for name, values in read.items():
            for value in values:
                narrow = dict(read, **{name: [v for v in values if v != value]})
                with self.subTest(name=name, value=value):
                    (problem,) = capabilities.capability_problems(self.codex, pin, runner(narrow), self.env)
                    self.assertIn("refused the pinned provider capability %s = %r" % (name, value), problem)
            stale = {other: vals for other, vals in read.items() if other != name}
            with self.subTest(stale=name):
                (problem,) = capabilities.capability_problems(self.codex, pin, runner(stale), self.env)
                self.assertIn("does not read the pinned provider capability %s" % name, problem)

    def test_a_build_that_ignores_the_pinned_capability_is_refused(self):
        """Stock Codex 0.163.0 loaded every value, the unknown one too."""
        problems = capabilities.capability_problems(self.codex, PIN, runner({}), self.env)
        self.assertEqual(len(problems), len(capabilities.pinned_capabilities(PIN)))
        self.assertIn("does not read the pinned provider capability request_compression", problems[0])

    def test_an_unpinned_value_must_be_refused_as_unknown_not_for_another_reason(self):
        def broken(argv, check=False, timeout=None, env=None):
            if capabilities.UNPINNED_VALUE in " ".join(argv):
                return SimpleNamespace(returncode=1, stdout="", stderr="config.toml: permission denied")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        (problem,) = capabilities.capability_problems(self.codex, PIN, broken, self.env)
        self.assertIn("does not read", problem)

    def test_release_checks_run_it_with_the_runtime_check(self):
        text = (ROOT / "scripts/angelo/release_checks.py").read_text(encoding="utf-8")
        self.assertIn("capability_problems(codex, pin, runner, env)", text)


class PinCoversWhatAngeloWritesTests(unittest.TestCase):
    """Every ``capabilities.<name>`` Angelo writes into a model provider, and every value it writes there, is pinned, so
    the release checks prove the bundled build reads it."""

    def written(self):
        names = set()
        for path in sorted(ADDON.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.update(re.findall(r"(?:^|\.)capabilities\.([a-z_]+)(?:$|[=.\s])", node.value))
        return names

    def test_every_capability_angelo_writes_is_pinned(self):
        written = self.written()
        self.assertIn("request_compression", written, "team_runtime writes it for a zstd gateway")
        self.assertLessEqual(written, set(capabilities.pinned_capabilities(PIN)))

    def test_every_request_compression_value_angelo_writes_is_pinned(self):
        tree = ast.parse((ADDON / "team_runtime.py").read_text(encoding="utf-8"))
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef) and node.name == "request_compression")
        returned = {node.value for ret in ast.walk(function) if isinstance(ret, ast.Return) and ret.value
                    for node in ast.walk(ret.value) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertEqual(returned, {"zstd"})
        self.assertLessEqual(returned, set(capabilities.pinned_capabilities(PIN)["request_compression"]))


class WindowsRunsTheSameCheckTests(unittest.TestCase):
    def test_both_windows_runtime_checks_probe_each_pinned_capability(self):
        for name, script in WINDOWS.items():
            with self.subTest(script=name):
                for part in ("provider_capabilities", capabilities.CAPABILITY_CHECK_PROVIDER, capabilities.UNPINNED_VALUE,
                             "unknown variant", "features list", "127.0.0.1:9/v1"):
                    self.assertIn(part, script)

    def test_the_windows_release_build_asserts_them_beside_the_wires(self):
        build = (ROOT / "scripts/angelo/build_release_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("Assert-RuntimeCapabilities $codexExe $runtimePin.provider_capabilities", build)


if __name__ == "__main__":
    unittest.main()
