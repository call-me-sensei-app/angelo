# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A GPU check that cannot run fails on a machine that has a GPU instead of passing as "not run". No Blender.

test_blender_upstream_fix_backports passes its GPU denoiser checks with a ``not_run`` detail where the machine has
no Cycles GPU device, which is right on a GPU-less builder. On the T4 round (6bb43ee's, a7bce32f502) it passed in
12.6 s, and nothing in the round's report said whether its OptiX check had rendered or not run: a pass that proved
nothing reads the same as one that proved the fix. A kit that runs on a GPU machine sets
``ANGELO_QA_EXPECT_CYCLES_GPU``. The runner hands it to the suite, and there a GPU check that cannot run fails.

Both halves are pinned here. The runner passes the flag through only when the caller sets it. No windowless suite
records a check it did not run as a literal pass: ``check(name, True, {"not_run": ...})``. Such a check goes
through a helper that fails it when the run expects a GPU.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest  # noqa: E402
import run_native_suites as runner  # noqa: E402

FLAG = "ANGELO_QA_EXPECT_CYCLES_GPU"
BACKPORTS = "test_blender_upstream_fix_backports"


def literal_not_run_passes(path):
    """Line numbers of ``check(name, True, {..."not_run": ...})`` calls in one suite's source."""
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check"):
            continue
        arguments = list(node.args) + [keyword.value for keyword in node.keywords]
        passes = any(isinstance(item, ast.Constant) and item.value is True for item in arguments)
        says_not_run = any(isinstance(item, ast.Dict) and any(isinstance(key, ast.Constant) and key.value == "not_run"
                                                              for key in item.keys) for item in arguments)
        if passes and says_not_run:
            lines.append(node.lineno)
    return lines


class GpuExpectedTests(unittest.TestCase):
    def plan_env(self, base):
        with tempfile.TemporaryDirectory(prefix="gpu-expected-") as temp:
            run_dir = Path(temp).resolve()
            suite = next(suite for suite in manifest.SUITES if suite.name == BACKPORTS)
            return runner.plan_suite(suite, run_dir, run_dir / "Angelo", base)["env"]

    def test_the_runner_hands_the_gpu_expectation_to_a_suite_only_when_the_caller_sets_it(self):
        self.assertEqual(runner.EXPECT_CYCLES_GPU, FLAG)
        base = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}
        self.assertNotIn(FLAG, self.plan_env(base))
        self.assertNotIn(FLAG, self.plan_env(dict(base, **{FLAG: ""})))
        self.assertEqual(self.plan_env(dict(base, **{FLAG: "1"})).get(FLAG), "1")

    def test_no_suite_records_a_check_it_did_not_run_as_a_pass(self):
        offenders = {}
        for suite in manifest.SUITES:
            path = HERE / (suite.name + ".py")
            if path.exists() and (lines := literal_not_run_passes(path)):
                offenders[suite.name] = lines
        self.assertEqual(offenders, {}, "record a check that did not run through a helper that fails it where "
                                        "the run expects a GPU (%s), never as a literal pass" % FLAG)

    def test_the_backports_suite_reads_the_gpu_expectation(self):
        source = (HERE / (BACKPORTS + ".py")).read_text(encoding="utf-8")
        self.assertIn(FLAG, source)

    def test_the_scan_sees_a_literal_not_run_pass_and_ignores_a_helper(self):
        with tempfile.TemporaryDirectory(prefix="gpu-expected-scan-") as temp:
            path = Path(temp, "suite.py")
            path.write_text('check("a", True, {"not_run": "no GPU"})\n'
                            'check("b", True, {"difference": 0.0})\n'
                            'check("c", not EXPECTED, {"not_run": "no GPU"})\n'
                            'check("d", passed=True, detail={"not_run": "no GPU"})\n', encoding="utf-8")
            self.assertEqual(literal_not_run_passes(path), [1, 4])


if __name__ == "__main__":
    unittest.main()
