# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A codex launched with Angelo's flags holds a code-mode cell while an Angelo call waits for the person.

Regression guard for 1.0.10's dropped approval: its runtime held a cell only in a step instant interrupt could
preempt, Angelo launches with ``features.instant_interrupt = false``, so a script approval card was cancelled 31 s
after it appeared (the model was handed "Script running", polled, and ended its turn; the end cancelled the call).
``approval_hold_probe`` drives the real ``codex app-server`` with Angelo's launch flags against a local fake
Responses endpoint and answers an ``angelo_execute_python`` call six yield times late. Unbilled; nothing leaves
127.0.0.1.

Runs where a codex is available (``ANGELO_TEST_CODEX``, else the agent runtime); skipped otherwise, as on GitHub CI,
which carries no runtime. Required at every cut against the exact bundled runtime, as the prefix test is: the
Windows release stage runs it on the MSI's codex.exe and the macOS release build on ANGELO_AGENT_RUNTIME_DIR's codex,
and a skip fails both.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from host_test_policy import environment_skip  # noqa: E402
from test_native_codex_prefix_stability import _codex_binary  # noqa: E402

ROOT = HERE.parents[2]


class RequiredAtEveryCutTests(unittest.TestCase):
    """Runs everywhere, CI included: each platform's release checks run this test on the exact bundled runtime."""

    def test_the_windows_release_stage_requires_it_on_the_msis_codex(self):
        stage = (ROOT / "scripts/angelo/windows_release_validate.ps1").read_text(encoding="utf-8")
        block = stage[stage.index('"release: the MSI\'s codex holds a call that waits for the person'):]
        block = block[:block.index("Test-Check ", 1)]
        for part in ('$runtime.files."codex.exe"', "$env:ANGELO_TEST_CODEX = $codex",
                     "test_native_codex_approval_hold.py", '-match "skipped"'):
            self.assertIn(part, block)

    def test_the_macos_release_build_requires_it_on_the_bundled_codex(self):
        build = (ROOT / "scripts/angelo/build_release_macos.sh").read_text(encoding="utf-8")
        check = build.index("release_checks.py\" agent-runtime")
        step = build[check:build.index("cmake -S", check)]
        for part in ('ANGELO_TEST_CODEX="$AGENT_RUNTIME/codex"', "test_native_codex_approval_hold.py", "skipped"):
            self.assertIn(part, step)

    def test_the_launch_it_guards_is_still_angelos(self):
        # The probe is only meaningful while Angelo launches the way 1.0.10 did: every Angelo tool held, instant
        # interrupt off. If either changes, revisit the probe rather than let it pass vacuously.
        from angelo_agent import runtime_launch
        from angelo_agent.tool_specs import TOOLS
        import approval_hold_probe
        self.assertIn(approval_hold_probe.TOOL, runtime_launch.HOLD_DYNAMIC_TOOLS)
        self.assertEqual(set(runtime_launch.HOLD_DYNAMIC_TOOLS), {tool["name"] for tool in TOOLS})
        command, _env = runtime_launch.codex_command("codex", "codex_subscription", tempfile.mkdtemp(), reuse_login=False)
        self.assertIn("features.instant_interrupt=false", command)
        self.assertIn("code_mode_hold_dynamic_tools=" + json.dumps(list(runtime_launch.HOLD_DYNAMIC_TOOLS)), command)


class ProbesWaitForTheirRuntimeTests(unittest.TestCase):
    """Runs everywhere: a probe that stops its codex never leaves the process holding the probe's folder."""

    def test_every_app_server_a_probe_stops_is_waited_for(self):
        # Windows keeps an exiting process's files locked, so a probe that only terminated its codex left the state
        # database open while its test deleted the folder (WinError 32 on a Windows test VM, 2026-11-11: this test's own
        # probe, on both runtimes). JsonRpcProcess.close waits for the exit and kills a process that does not end; a
        # bare terminate must be followed by a wait within the next lines.
        unwaited = []
        for path in sorted(HERE.glob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            if not any("JsonRpcProcess(" in line for line in lines):
                continue
            for number, line in enumerate(lines):
                if "process.terminate()" in line and "rpc" in line.split("process.terminate()")[0]:
                    following = "\n".join(lines[number + 1:number + 4])
                    if "wait" not in following and "stop_process(" not in following:   # .wait(), wait_gone()
                        unwaited.append("%s:%d" % (path.name, number + 1))
        self.assertEqual(unwaited, [], "stop the app-server with rpc.close(), which waits for its exit")


@unittest.skipUnless(_codex_binary(), environment_skip("no codex executable (set ANGELO_TEST_CODEX to the agent runtime's codex)"))
class ApprovalHoldTests(unittest.TestCase):
    def test_a_call_answered_late_reaches_the_model_finished_never_running(self):
        import approval_hold_probe as probe
        with tempfile.TemporaryDirectory() as out:
            code = probe.main([out, "--codex", _codex_binary()])
            report = json.loads(Path(out, "report.json").read_text(encoding="utf-8"))
        # Every failure names what the check looked for and what the model was given, so a release stage's log alone
        # says why.
        seen = json.dumps({key: report[key] for key in ("exec_output", "calls", "model_requests", "stderr_tail")})
        for name, held in report["expectations"].items():
            with self.subTest(expectation=name):
                self.assertTrue(held, "%s: %s" % (name, seen))
        self.assertEqual(code, 0, seen)


if __name__ == "__main__":
    unittest.main()
