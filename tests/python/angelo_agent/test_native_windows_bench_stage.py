# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows bench stage's failure paths, found by rehearsals of the stage (doc/angelo/final_runs.rst).

* Native stderr under Stop: ``windows_final_runs.ps1`` hid gcloud's output with ``2>&1 | Out-Null`` under
  ``$ErrorActionPreference = "Stop"``, so Windows PowerShell 5.1 made its "Copying ..." progress line a terminating
  error and ``bench prep`` died right after SIGN-IN-READY. ``ps_stderr_lint`` reads the VM's scripts for any such
  call (the host suite has no PowerShell).
* The Mac side: ``wait_off`` (gcp_windows_validate.sh) said READY FOR SIGN-IN and ``bench prep`` exited 0 although
  the stage had failed. It runs here against a stand-in ``gcloud`` that plays the bucket and the VM.
* A kept sign-in: ``bench prep`` deleted the SIGNED-IN marker at its start, so a machine signed in before needed
  ``-SignIn`` again; it now checks the marker's accounts again (existence only) and keeps it.

Found by a full-depth ``bench dry`` in the bench user's desktop session (a launch-depth rehearsal runs as
SYSTEM):

* The stock Blender jobs (references, start files, scorecard captures) ran with no Windows home folder:
  ``scenario_references.isolated_environment`` set only ``HOME``, which Python ignores on Windows, so
  ``Path.home()`` raised in ``scenario_specs`` and every job exited 1 within seconds, on stock 5.2.0 as on 5.2.2.
* The MCP server's virtualenv ran on uv's interpreter in SYSTEM's profile, which the bench user cannot read, so
  the server exited before its first line. The runtime now brings its own interpreter (``final_runs_mcp``).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from ps_stderr_lint import stderr_hazards  # noqa: E402

SCRIPTS = HERE.parents[2] / "scripts/angelo"
VM_SCRIPTS = ("windows_final_runs.ps1", "windows_bench_tools.ps1", "windows_cloud_bootstrap.ps1")


def text(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


class NativeStderrTests(unittest.TestCase):
    def test_the_check_finds_a_redirected_stderr_wherever_stop_may_hold(self):
        script = "\n".join([
            '$ErrorActionPreference = "Stop"',
            'if ($SignIn) {',
            '    $ErrorActionPreference = "Continue"   # Holds in this block only.',
            '    & $x 2>&1',
            '}',
            'if ($mode -eq "prep") {',
            '    & $Gcloud storage cp --quiet $ready "gs://b/r" 2>&1 | Out-Null   # the old stage\'s line',
            '}',
            'function F { & $y 2>$null }',
            'function G([string]$a,',
            '           [string]$b) {',
            '    $ErrorActionPreference = "Continue"',
            '    "$(& $z --version 2>$null)"',
            '}',
            '$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"',
            '& $w 2>&1 | Out-Host',
            '$ErrorActionPreference = $old',
            '& $v 2>&1   # after the restore',
            '& $u | Out-Host   # stderr not redirected: passes through',
            '"2>&1 in a string" # 2>&1 in a comment',
            '$job = Start-Job -ScriptBlock { & $t *>&1 }',
        ])
        self.assertEqual([number for number, _ in stderr_hazards(script)], [7, 9, 18, 21])

    def test_the_vm_scripts_redirect_stderr_only_under_continue(self):
        for name in VM_SCRIPTS:
            with self.subTest(script=name):
                self.assertEqual(stderr_hazards(text(name)), [])

    def test_every_gcloud_call_of_the_stage_is_checked_and_never_ends_it_with_progress(self):
        stage = text("windows_final_runs.ps1")
        self.assertNotIn("& $Gcloud", stage, "gcloud runs through Invoke-Gcloud")
        native = stage[stage.index("function Invoke-Native"):stage.index("function Invoke-Gcloud")]
        self.assertIn('$ErrorActionPreference = "Continue"', native)
        self.assertIn("return $LASTEXITCODE", native)
        gcloud = stage[stage.index("function Invoke-Gcloud"):stage.index("function Read-Facts")]
        self.assertIn("throw $failed", gcloud, "a failed copy stops the stage, unless -Optional")
        ready = stage[stage.index('$ready = Join-Path $state "SIGN-IN-READY"'):stage.index("$deadline = (Get-Date)")]
        self.assertIn('Invoke-Gcloud @("storage", "cp", "--quiet", $ready, "gs://$Bucket/result/SIGN-IN-READY")', ready)
        for required in ('"gs://$Bucket/bench/mcp-runtime.tar.gz", $bundle)', "$candidateMsi, $local)"):
            self.assertIn(required, stage)


class KeptSignInTests(unittest.TestCase):
    def test_prep_checks_the_markers_accounts_again_and_keeps_the_sign_in(self):
        stage = text("windows_final_runs.ps1")
        prep = stage[stage.index('if ($mode -eq "prep") {'):stage.index("# 6. A desktop session")]
        order = [prep.index(marker) for marker in (
            "if (Test-Path $marker) {", '"check-sign-in", "--connections-dir", $connections',
            '"gs://$Bucket/result/SIGNED-IN-KEPT"', "exit 0", "Remove-Item $marker",
            '"gs://$Bucket/result/SIGN-IN-READY"')]
        self.assertEqual(order, sorted(order), "checked, kept and done; removed only when incomplete; then READY")
        self.assertIn('@("--account", $_)', prep, "only the accounts the sign-in named")
        self.assertIn('@("claude", "chatgpt")', prep, "a marker from an earlier build names no account: both")
        self.assertNotIn("Remove-Item (Join-Path $connections", stage, "never deleted unchecked")
        sign_in = stage[stage.index("if ($SignIn) {"):stage.index("the measurement session (desktop)")]
        self.assertIn('("accounts: " + ($Accounts -join " "))', sign_in)
        for name in ("auth.json", ".credentials.json"):
            self.assertNotIn(name, stage, "existence is check-sign-in's; the stage names no credential file")


@unittest.skipUnless(shutil.which("bash"), "The launcher is a bash script run from the workstation, not the VM")
class WaitOffTests(unittest.TestCase):
    """``gcp_windows_validate.sh wait-off`` against a stand-in gcloud: each observation is one (VM status, result
    objects) step, read by a describe and a listing; ``DONE`` holds the bootstrap's text."""

    FAKE = r"""#!/usr/bin/env bash
step="$(cat "$FAKE/step")"
pick() { local n="$step"; while [[ ! -f "$FAKE/$1.$n" ]]; do n=$((n - 1)); done; cat "$FAKE/$1.$n"; }
case "$*" in
  *"compute instances describe"*) pick vm ;;
  *"storage ls"*) for name in $(pick ls); do echo "gs://bucket/result/$name"; done; echo $((step + 1)) > "$FAKE/step" ;;
  *"storage cat"*/result/DONE*) cat "$FAKE/DONE" ;;
  *"storage cat"*/result/FAILED*) cat "$FAKE/FAILED" ;;
  *"compute instances stop"*) echo stopped >> "$FAKE/stops" ;;
  *) echo "unexpected gcloud $*" >&2; exit 1 ;;
esac
"""

    def wait_off(self, steps, done=None, failed=None):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder)
            (fake / "bin").mkdir()
            (fake / "bin/gcloud").write_text(self.FAKE)
            (fake / "bin/gcloud").chmod(0o755)
            (fake / "step").write_text("0")
            for index, (vm, objects) in enumerate(steps):
                (fake / ("vm.%d" % index)).write_text(vm + "\n")
                (fake / ("ls.%d" % index)).write_text(objects)
            for name, content in (("DONE", done), ("FAILED", failed)):
                if content is not None:
                    (fake / name).write_text(content)
            env = dict(os.environ, PATH="%s%s%s" % (fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(fake),
                       ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a",
                       ANGELO_GCP_NAME="example-vm", ANGELO_GCP_POLL_SECONDS="0",
                       ANGELO_GCP_READY_SETTLE_SECONDS="0")
            return subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), "wait-off"], env=env,
                                  capture_output=True, text=True, timeout=60)

    def test_rc7s_stage_dying_after_sign_in_ready_is_never_ready_and_fails(self):
        # A stage that died after it: SIGN-IN-READY, then its own exit 1 seconds later (DONE), then the VM off.
        ran = self.wait_off([("RUNNING", ""), ("RUNNING", "SIGN-IN-READY"), ("RUNNING", "DONE SIGN-IN-READY"),
                             ("TERMINATED", "DONE SIGN-IN-READY")], done="validate exit code: 1\r\n")
        self.assertNotEqual(ran.returncode, 0)
        self.assertNotIn("READY FOR SIGN-IN", ran.stdout)
        self.assertIn("stage exit code: 1", ran.stdout)
        self.assertIn("The run FAILED: stage exit code 1", ran.stderr)

    def test_a_stage_failing_after_ready_is_taken_back(self):
        ran = self.wait_off([("RUNNING", "SIGN-IN-READY"), ("RUNNING", "SIGN-IN-READY"),
                             ("RUNNING", "DONE SIGN-IN-READY"), ("TERMINATED", "DONE SIGN-IN-READY")],
                            done="validate exit code: 1\r\n")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(ran.stdout.count("READY FOR SIGN-IN"), 1)
        self.assertIn("NOT READY: the stage failed after READY FOR SIGN-IN (exit code 1)", ran.stderr)

    def test_a_signed_in_prep_is_ready_once_and_passes(self):
        ran = self.wait_off([("RUNNING", ""), ("RUNNING", "SIGN-IN-READY"), ("RUNNING", "SIGN-IN-READY"),
                             ("RUNNING", "SIGN-IN-READY"), ("RUNNING", "DONE SIGN-IN-READY"),
                             ("TERMINATED", "DONE SIGN-IN-READY")], done="validate exit code: 0\r\n")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(ran.stdout.count("READY FOR SIGN-IN"), 1)
        self.assertIn("stage exit code: 0", ran.stdout)
        self.assertIn("confirmed powered off", ran.stdout)

    def test_a_kept_sign_in_needs_no_sign_in(self):
        ran = self.wait_off([("RUNNING", "SIGNED-IN-KEPT"), ("RUNNING", "DONE SIGNED-IN-KEPT"),
                             ("TERMINATED", "DONE SIGNED-IN-KEPT")], done="validate exit code: 0\r\n")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("SIGNED IN ALREADY", ran.stdout)
        self.assertNotIn("READY FOR SIGN-IN", ran.stdout)

    def test_a_failed_bootstrap_or_an_early_stop_fails(self):
        failed = self.wait_off([("RUNNING", "FAILED"), ("TERMINATED", "FAILED")], failed="the stage threw\r\n")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("FAILED (the stage threw)", failed.stdout)
        early = self.wait_off([("RUNNING", ""), ("TERMINATED", "")])
        self.assertNotEqual(early.returncode, 0)
        self.assertIn("stopped before the run ended", early.stdout)

    def test_bench_returns_wait_offs_verdict(self):
        gcp = text("gcp_windows_validate.sh")
        bench = gcp[gcp.index("bench() {"):gcp.index("down() {")]
        self.assertIn("wait_off || code=1", bench)
        self.assertRegex(bench, re.compile(r'return "\$code"\s*\n}'))


class BenchUserTests(unittest.TestCase):
    def test_stock_blender_jobs_get_a_complete_windows_profile_inside_their_scratch(self):
        import scenario_references as references
        inherited = {"PATH": "C:\\Windows", "SYSTEMROOT": "C:\\Windows", "COMSPEC": "cmd.exe",
                     "NUMBER_OF_PROCESSORS": "16", "USERPROFILE": "C:\\Users\\someone",
                     "APPDATA": "C:\\Users\\someone\\AppData\\Roaming", "ANTHROPIC_API_KEY": "not passed"}
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, inherited, clear=True):
            scratch = Path(folder) / "scratch"
            env = references.isolated_environment(scratch, platform="win32")
            home = scratch / "home"
            self.assertEqual(env["USERPROFILE"], str(home), "Path.home() on Windows: never the person's own")
            self.assertEqual(env["APPDATA"], str(home / "AppData/Roaming"))
            self.assertEqual(env["LOCALAPPDATA"], str(home / "AppData/Local"))
            self.assertEqual((env["TEMP"], env["TMP"]), (str(scratch / "tmp"),) * 2)
            for name in ("SYSTEMROOT", "COMSPEC", "NUMBER_OF_PROCESSORS", "PATH"):
                self.assertEqual(env[name], inherited[name])
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertTrue((home / "AppData/Local").is_dir())
            self.assertTrue(all(str(scratch) in env[name] for name in env if name.startswith("BLENDER_USER")))
            mac = references.isolated_environment(Path(folder) / "mac", platform="darwin")
            self.assertEqual(sorted(set(mac) - set(env)), [], "the Windows names are added on Windows only")
            for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "SYSTEMROOT"):
                self.assertNotIn(name, mac)

    def test_the_mcp_side_runs_the_current_stock_release_wherever_a_default_is_named(self):
        stock = "https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip"
        launcher = re.search(r'^BENCH_STOCK_URL="\$\{ANGELO_BENCH_STOCK_URL:-([^}]+)\}"$',
                             text("gcp_windows_validate.sh"), re.M)
        self.assertEqual(launcher.group(1), stock)
        # windows_final_runs.ps1: the -StockUrl parameter's default and the metadata fallback of the bench stage.
        self.assertEqual(re.findall(r"https://download\.blender\.org/\S+?\.zip", text("windows_final_runs.ps1")),
                         [stock, stock])

    def test_an_mcp_server_that_ends_at_once_is_reported_with_its_own_stderr(self):
        import bench_mcp_trial as trial
        script = "import sys; sys.stderr.write('cannot start: no interpreter'); sys.exit(3)"
        server = {"command": sys.executable, "args": ["-c", script], "env": {}}
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError) as raised:
                trial.probe_mcp_server(server, dict(os.environ), folder)
            self.assertTrue((Path(folder) / trial.MCP_PROBE_STDERR).is_file())
        message = str(raised.exception)
        self.assertIn("before answering initialize (exit code 3)", message)
        self.assertIn("cannot start: no interpreter", message)

    def test_an_mcp_runtime_from_before_its_own_interpreter_is_installed_again(self):
        tools = text("windows_bench_tools.ps1")
        install = tools[tools.index("function Install-McpRuntime"):tools.index("function Freeze-MsiCandidate")]
        check = install.index(".interpreter_dir)")
        self.assertLess(check, install.index("Remove-Item $mcp -Recurse -Force"))
        self.assertIn("if (-not $selfContained) {", install)


class OneshotSourcesTests(unittest.TestCase):
    """The tree and cottage one-shots' source picture on the Windows machines: from the private bucket only."""

    FAKE = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE/calls"
case "$*" in
  *"buckets describe"*) cat "$FAKE/bucket.json" ;;
  *"get-iam-policy"*) cat "$FAKE/policy.json" ;;
  *"storage cp"*|*"storage ls"*) ;;
  *) echo "unexpected gcloud $*" >&2; exit 1 ;;
esac
"""
    PRIVATE = {"uniform_bucket_level_access": True, "public_access_prevention": "enforced"}
    POLICY = {"bindings": [{"role": "roles/storage.objectAdmin", "members": ["serviceAccount:vm@example.com"]}]}

    def upload(self, bucket, policy, files):
        import json
        with tempfile.TemporaryDirectory() as folder:
            fake, pictures = Path(folder), Path(folder) / "pictures"
            (fake / "bin").mkdir()
            pictures.mkdir()
            (fake / "bin/gcloud").write_text(self.FAKE)
            (fake / "bin/gcloud").chmod(0o755)
            (fake / "bucket.json").write_text(json.dumps(bucket))
            (fake / "policy.json").write_text(json.dumps(policy))
            for name, data in files.items():
                (pictures / name).write_bytes(data)
            env = dict(os.environ, PATH="%s%s%s" % (fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(fake),
                       ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a",
                       ANGELO_GCP_NAME="example-vm")
            ran = subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), "oneshot-sources", str(pictures)],
                                 env=env, capture_output=True, text=True, timeout=60)
            calls = (fake / "calls").read_text() if (fake / "calls").is_file() else ""
        return ran, calls

    @unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "The upload runs on the workstation")
    def test_a_bucket_that_could_be_public_gets_nothing(self):
        public = {"bindings": self.POLICY["bindings"] + [{"role": "roles/storage.objectViewer", "members": ["allUsers"]}]}
        for bucket, policy, reason in ((self.PRIVATE, public, "allUsers or allAuthenticatedUsers"),
                                       (dict(self.PRIVATE, uniform_bucket_level_access=False), self.POLICY,
                                        "uniform bucket-level access is off"),
                                       (dict(self.PRIVATE, public_access_prevention="inherited"), self.POLICY,
                                        "public access prevention is not enforced")):
            with self.subTest(reason=reason):
                ran, calls = self.upload(bucket, policy, {"maintainer-reference-2026-10-11.png": b"x"})
                self.assertNotEqual(ran.returncode, 0)
                self.assertIn(reason, ran.stderr)
                self.assertNotIn("storage cp", calls, "nothing is uploaded")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "The upload runs on the workstation")
    def test_a_picture_other_than_the_pinned_one_is_never_uploaded(self):
        ran, calls = self.upload(self.PRIVATE, self.POLICY, {"maintainer-reference-2026-10-11.png": b"not it"})
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("does not hold the pinned pictures", ran.stderr)
        self.assertNotIn("storage cp", calls)

    def test_the_vm_downloads_checks_and_points_the_session_at_them(self):
        stage, tools = text("windows_final_runs.ps1"), text("windows_bench_tools.ps1")
        download = stage.index('"gs://$Bucket/bench/oneshot-sources/*"')
        self.assertLess(download, stage.index("$facts = Prepare-Bench $claudeVersion"))
        self.assertIn("$bundle $candidateMsi $candidateSha $sources", stage)
        self.assertIn("Install-OneshotSources $Sources $python313 $SourceDir", stage)
        self.assertIn('$env:ANGELO_ONESHOT_SOURCES = $facts.oneshot_sources', stage)
        install = tools[tools.index("function Install-OneshotSources"):tools.index("function Freeze-MsiCandidate")]
        self.assertIn("final_runs_sources.py", install)
        self.assertIn("if ($LASTEXITCODE -ne 0) { throw", install)
        clone = text("gcp_windows_validate.sh")
        clone = clone[clone.index("clone() {"):clone.index("down() {")]
        self.assertIn('g storage cp "gs://$BUCKET/bench/oneshot-sources/*" "gs://$bucket/bench/oneshot-sources/"', clone)
        self.assertIn("--uniform-bucket-level-access --public-access-prevention", clone)
        self.assertNotRegex(clone + stage, r"signurl|sign-url|allUsers")


if __name__ == "__main__":
    unittest.main()
