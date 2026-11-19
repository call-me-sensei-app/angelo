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

One boot fewer per gate machine (cut-13, 2026-11-04: each of prep, dry and run boots, works and powers off;
prep and dry took 30 of the gates' 67 minutes after the MSI): ``bench prepdry`` runs prep and dry in one request on a
machine with a kept sign-in, and a pre-boot overlaps the boot and the machine's own checks with the MSI's build,
refusing loudly when the sign-in, a stale install or stock Blender is not as the run expects.

Found by a full-depth ``bench dry`` in the bench user's desktop session (a launch-depth rehearsal runs as
SYSTEM):

* The stock Blender jobs (references, start files, scorecard captures) ran with no Windows home folder:
  ``scenario_references.isolated_environment`` set only ``HOME``, which Python ignores on Windows, so
  ``Path.home()`` raised in ``scenario_specs`` and every job exited 1 within seconds, on stock 5.2.0 as on 5.2.2.
* The MCP server's virtualenv ran on uv's interpreter in SYSTEM's profile, which the bench user cannot read, so
  the server exited before its first line. The runtime now brings its own interpreter (``final_runs_mcp``).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import platform_env

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from host_test_policy import environment_skip, required_skip  # noqa: E402
from ps_stderr_lint import stderr_hazards  # noqa: E402

SCRIPTS = HERE.parents[2] / "scripts/angelo"
VM_SCRIPTS = ("windows_final_runs.ps1", "windows_bench_tools.ps1", "windows_bench_preboot.ps1", "windows_session_start.ps1",
              "windows_bootstrap_stages.ps1", "windows_gui_checks.ps1", "windows_cloud_bootstrap.ps1")
# gcp_windows_validate.sh drives the VM from the macOS workstation; its runs against a stand-in gcloud are checked
# there and in the Linux host suite, never on Windows.
WORKSTATION_ONLY = unittest.skipIf(os.name == "nt", environment_skip(
    "gcp_windows_validate.sh is a bash launcher run from the macOS workstation, not on Windows"))


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
        prep = stage[stage.index('if ($mode -in "prep", "prepdry") {'):stage.index("# 6. A desktop session")]
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


@WORKSTATION_ONLY
@unittest.skipUnless(shutil.which("bash"), required_skip("needs bash"))
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


class BenchVerdictTests(unittest.TestCase):
    """Read from the launcher's source, so it holds on every host."""

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
        with tempfile.TemporaryDirectory() as folder, platform_env.cleared(inherited):
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

    @WORKSTATION_ONLY
    @unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), required_skip("needs bash and python3"))
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

    @WORKSTATION_ONLY
    @unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), required_skip("needs bash and python3"))
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



class PrepDryTests(unittest.TestCase):
    """``prepdry``: prep with a kept sign-in, then the dry run's session, in one request; never an RDP wait."""

    def test_a_kept_sign_in_goes_on_to_the_dry_session(self):
        stage = text("windows_final_runs.ps1")
        self.assertIn('if ($mode -notin "prep", "dry", "run", "prepdry")', stage)
        prep = stage[stage.index('if ($mode -in "prep", "prepdry") {'):stage.index("# 6. A desktop session")]
        kept = prep[prep.index('"gs://$Bucket/result/SIGNED-IN-KEPT"'):]
        self.assertLess(kept.index('if ($mode -eq "prep") { exit 0 }'), kept.index('if ($mode -eq "prepdry") { $mode = "dry" }'))
        # The RDP wait (SIGN-IN-READY) is prep's alone: after the switch to dry, prepdry can no longer reach it.
        self.assertLess(prep.index('if ($mode -eq "prepdry") { $mode = "dry" }'), prep.index('"gs://$Bucket/result/SIGN-IN-READY"'))
        session = stage[stage.index("# 6. A desktop session"):]
        self.assertIn("-Mode {2} -Nonce {3}' -f $PSCommandPath, $SourceDir, $mode, $nonce", session)

    def test_without_a_kept_sign_in_it_refuses_loudly(self):
        prep = text("windows_final_runs.ps1")
        prep = prep[prep.index('if ($mode -in "prep", "prepdry") {'):prep.index('if ($mode -eq "prepdry") { $mode = "dry" }')]
        self.assertEqual(prep.count('throw "prepdry refused:'), 2, "an incomplete sign-in and none at all")
        incomplete = prep[prep.index('} elseif ($mode -eq "prepdry") {'):]
        self.assertLess(incomplete.index('throw "prepdry refused:'), incomplete.index("Remove-Item $marker"),
                        "prepdry never removes the marker: only prep, which then waits for -SignIn")

    def test_the_launcher_takes_prepdry_as_unbilled(self):
        bench = text("gcp_windows_validate.sh")
        bench = bench[bench.index("bench() {"):bench.index("down() {")]
        self.assertIn("prep|dry|prepdry) ;;", bench)
        self.assertIn('[[ "$mode" == prep && -z "$candidate" && "$wait_minutes" == 0 ]] && echo release+bench', bench)


class PrebootTests(unittest.TestCase):
    """Pre-boot: the machine boots and checks itself while the candidate builds, then waits for ``bench deliver``."""

    def test_the_checks_come_before_ready_and_the_wait_before_the_candidate(self):
        stage = text("windows_final_runs.ps1")
        body = stage[stage.index("# ------------------------------------------------------------------ the stage"):]
        order = [body.index(marker) for marker in (
            'if (-not $candidateMsi -and $waitMinutes -gt 0) {', "$ready = Assert-PrebootReady $stockUrl",
            '"gs://$Bucket/result/PREBOOT-READY"', "$candidateMsi = Wait-PrebootCandidate $waitMinutes",
            '$candidateSha = Get-Meta "angelo-bench-candidate-sha256" ""', 'if ($candidateMsi -like "gs://*"',
            "$facts = Prepare-Bench")]
        self.assertEqual(order, sorted(order))

    def test_each_check_refuses_saying_what_it_found(self):
        guard = text("windows_bench_preboot.ps1")
        guard = guard[guard.index("function Assert-PrebootReady"):guard.index("function Wait-PrebootCandidate")]
        for refusal in ("pre-boot refused: not signed in", "pre-boot refused: the sign-in (",
                        "pre-boot refused: a stale Angelo install", "pre-boot refused: stock Blender at",
                        "pre-boot refused: the stock URL names no version",
                        "pre-boot refused: final_runs_plan.py needs: no step matches"):
            self.assertIn('throw "' + refusal, guard)
        self.assertIn('"check-sign-in", "--connections-dir", $connections', guard)
        self.assertIn("Get-ChildItem $installDir", guard)
        self.assertIn('$_.DisplayName -like "Angelo*"', guard)
        self.assertIn("if ($needs.mcp_side) {", guard)

    def test_the_wait_shows_progress_and_has_its_own_limit(self):
        wait = text("windows_bench_preboot.ps1")
        wait = wait[wait.index("function Wait-PrebootCandidate"):]
        self.assertIn('Join-Path $state "preboot-waiting"', wait, "bench\\state is in the heartbeat's watched folders")
        self.assertIn('throw "pre-boot: no candidate MSI named within $Minutes minutes', wait)
        bootstrap = text("windows_bootstrap_stages.ps1")
        self.assertIn('if (-not (Get-Meta "angelo-bench-candidate-msi" "")) { $prepMinutes += [int](Get-Meta '
                      '"angelo-bench-candidate-wait-minutes" "0") }', bootstrap)
        cloud = text("windows_cloud_bootstrap.ps1")
        self.assertIn("windows_final_runs.ps1's bench\\state", cloud)

    def test_a_request_keeps_the_candidate_it_took(self):
        stage = text("windows_final_runs.ps1")
        body = stage[stage.index("# ------------------------------------------------------------------ the stage"):]
        order = [body.index(marker) for marker in (
            '$candidateSha = Get-Meta "angelo-bench-candidate-sha256" ""', '$taken = Join-Path $state "candidate-$nonce.json"',
            'throw "the candidate changed within request ${nonce}', 'if ($candidateMsi -like "gs://*"')]
        self.assertEqual(order, sorted(order), "checked against what the request took before any download")

    def test_a_later_boot_of_prepdry_never_checks_the_sign_in_under_its_session(self):
        stage = text("windows_final_runs.ps1")
        skip = 'if ($mode -eq "prepdry" -and (Test-Path (Join-Path $state "session-$nonce"))) { $mode = "dry" }'
        self.assertLess(stage.index(skip), stage.index('if ($mode -in "prep", "prepdry") {'))

    def test_the_wait_is_outside_the_requests_hours(self):
        stage = text("windows_final_runs.ps1")
        wait = stage[stage.index("$candidateMsi = Wait-PrebootCandidate $waitMinutes"):]
        self.assertTrue(wait.split("\n")[1].strip().startswith("Set-Content $startedFile (Get-Date -Format o)"))
        gcp = text("gcp_windows_validate.sh")
        self.assertIn("(( wait_minutes > 0 )) && RUN_HOURS=$(( RUN_HOURS + (wait_minutes + 59) / 60 ))", gcp)

    def test_bytecode_left_by_a_frozen_app_is_no_install(self):
        guard = text("windows_bench_preboot.ps1")
        guard = guard[guard.index("function Assert-PrebootReady"):guard.index("function Wait-PrebootCandidate")]
        self.assertIn('Get-ChildItem $installDir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object '
                      '{ $_.Extension -ne ".pyc" }', guard)

    def test_the_stage_dot_sources_the_preboot_functions(self):
        stage = text("windows_final_runs.ps1")
        self.assertIn('. (Join-Path $PSScriptRoot "windows_bench_preboot.ps1")', stage)
        self.assertNotIn("function Assert-PrebootReady", stage)

    def test_the_launcher_refuses_a_preboot_it_cannot_run(self):
        gcp = text("gcp_windows_validate.sh")
        bench = gcp[gcp.index("bench() {"):gcp.index("down() {")]
        self.assertIn('pre-boot (ANGELO_BENCH_CANDIDATE_WAIT_MINUTES) is for prep or prepdry', bench)
        self.assertIn("pre-boot waits for its candidate: leave ANGELO_BENCH_CANDIDATE_MSI unset", bench)
        self.assertIn('EXTRA_META+=",angelo-bench-candidate-wait-minutes=$wait_minutes"', bench, "always set: never inherited")
        self.assertIn("if (( wait_minutes > 0 )); then wait_preboot; return; fi", bench)


@WORKSTATION_ONLY
@unittest.skipUnless(shutil.which("bash"),
                     environment_skip("The launcher is a bash script run from the workstation, not the VM"))
class LauncherUsageTests(unittest.TestCase):
    def test_usage_prints_the_whole_header(self):
        ran = subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), "no-such-command"], capture_output=True,
                             text=True, timeout=60, env=dict(os.environ, ANGELO_GCP_PROJECT="p", ANGELO_GCP_ZONE="z",
                                                              ANGELO_GCP_NAME="n"))
        self.assertEqual(ran.returncode, 2)
        for line in ("bench deliver MSI", "oneshot-sources DIR", "`down` deletes everything"):
            self.assertIn(line, ran.stdout)
        self.assertNotIn("set -euo pipefail", ran.stdout)


@WORKSTATION_ONLY
@unittest.skipUnless(shutil.which("bash"),
                     environment_skip("The launcher is a bash script run from the workstation, not the VM"))
class PrebootLauncherTests(unittest.TestCase):
    """``wait-preboot`` and ``bench deliver`` against a stand-in gcloud: each step is one (VM status, result objects)
    observation, advanced by a listing; every other call is recorded."""

    FAKE = r"""#!/usr/bin/env bash
step="$(cat "$FAKE/step")"
pick() { local n="$step"; while [[ ! -f "$FAKE/$1.$n" ]]; do n=$((n - 1)); done; cat "$FAKE/$1.$n"; }
echo "$*" >> "$FAKE/calls"
case "$*" in
  *"compute instances describe"*"maxRunDuration"*) echo 28800 ;;
  *"compute instances describe"*"angelo-bench-candidate-msi"*) cat "$FAKE/named" 2>/dev/null || true ;;
  *"compute instances describe"*) pick vm ;;
  *"storage ls"*) for name in $(pick ls); do echo "gs://bucket/result/$name"; done; echo $((step + 1)) > "$FAKE/step" ;;
  *"storage cat"*/result/PREBOOT-READY*) echo "ready: signed in (chatgpt); no Angelo installed" ;;
  *"storage cat"*/result/DONE*) cat "$FAKE/DONE" ;;
  *"storage cat"*/result/FAILED*) echo "the stage threw" ;;
  *"storage cat"*/live/HEARTBEAT*) echo "at: $(cat "$FAKE/beat" 2>/dev/null || date +%s)" ;;
  *"storage cp"*|*"add-metadata"*|*"compute instances stop"*|*"set-scheduling"*) ;;
  *) echo "unexpected gcloud $*" >&2; exit 1 ;;
esac
"""

    def run_launcher(self, args, steps, done=None, named="", beat=None, **extra):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder)
            (fake / "bin").mkdir()
            (fake / "bin/gcloud").write_text(self.FAKE)
            (fake / "bin/gcloud").chmod(0o755)
            (fake / "step").write_text("0")
            (fake / "calls").write_text("")
            for index, (vm, objects) in enumerate(steps):
                (fake / ("vm.%d" % index)).write_text(vm + "\n")
                (fake / ("ls.%d" % index)).write_text(objects)
            if done is not None:
                (fake / "DONE").write_text(done)
            (fake / "named").write_text(named)
            if beat is not None:
                (fake / "beat").write_text(str(beat))
            msi = fake / "Angelo-1.1.0-windows-x64.msi"
            msi.write_bytes(b"candidate")
            env = dict(os.environ, PATH="%s%s%s" % (fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(fake),
                       ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a",
                       ANGELO_GCP_NAME="example-vm", ANGELO_GCP_POLL_SECONDS="0", ANGELO_GCP_READY_SETTLE_SECONDS="0",
                       **extra)
            argv = [str(msi) if arg == "MSI" else arg for arg in args]
            ran = subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), *argv], env=env,
                                 capture_output=True, text=True, timeout=60)
            return ran, (fake / "calls").read_text()

    def test_a_ready_machine_is_left_running_for_its_candidate(self):
        ran, calls = self.run_launcher(["wait-preboot"], [("RUNNING", ""), ("RUNNING", ""), ("RUNNING", "PREBOOT-READY")])
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("PREBOOT READY: ready: signed in (chatgpt)", ran.stdout)
        self.assertNotIn("instances stop", calls)

    def test_a_refused_preboot_fails_and_is_seen_off(self):
        ran, _ = self.run_launcher(["wait-preboot"], [("RUNNING", ""), ("RUNNING", "FAILED"), ("TERMINATED", "FAILED")])
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("PRE-BOOT FAILED: the stage ended before it was ready", ran.stderr)
        self.assertIn("confirmed powered off", ran.stdout)

    def test_deliver_names_the_candidate_then_waits_for_the_end(self):
        ran, calls = self.run_launcher(["bench", "deliver", "MSI"], [
            ("RUNNING", "PREBOOT-READY"), ("RUNNING", "PREBOOT-READY SIGNED-IN-KEPT"),
            ("RUNNING", "DONE PREBOOT-READY SIGNED-IN-KEPT"), ("TERMINATED", "DONE PREBOOT-READY SIGNED-IN-KEPT")],
            done="validate exit code: 0\r\n")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        meta = [line for line in calls.splitlines() if "add-metadata" in line]
        self.assertEqual(len(meta), 1)
        self.assertRegex(meta[0], r"angelo-bench-candidate-msi=gs://example-project-example-vm/bench/candidate/"
                                  r"[0-9a-f]{12}-Angelo-1\.1\.0-windows-x64\.msi,angelo-bench-candidate-sha256=[0-9a-f]{64}")
        self.assertLess(calls.index("storage cp --no-clobber"), calls.index("add-metadata"), "uploaded before it is named")
        self.assertIn("confirmed powered off", ran.stdout)

    def test_deliver_refuses_a_second_candidate_for_one_request(self):
        ran, calls = self.run_launcher(["bench", "deliver", "MSI"], [("RUNNING", "PREBOOT-READY")],
                                       named="gs://example-project-example-vm/bench/candidate/0123456789ab-older.msi")
        self.assertEqual(ran.returncode, 1)
        self.assertIn("already took its candidate", ran.stderr)
        self.assertNotIn("add-metadata", calls)
        self.assertNotIn("storage cp", calls)

    def test_a_dry_run_deliver_changes_nothing(self):
        ran, calls = self.run_launcher(["bench", "deliver", "MSI"], [("RUNNING", "PREBOOT-READY")], ANGELO_GCP_DRY_RUN="1")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("DRY RUN: nothing is uploaded or named.", ran.stdout)
        self.assertNotIn("add-metadata", calls)
        self.assertNotIn("storage cp", calls)

    def test_the_stall_guard_stays_on_after_a_kept_sign_in(self):
        # prepdry says SIGNED-IN-KEPT and then runs a dry session for as long as it takes: a hung one is still stopped.
        kept = "PREBOOT-READY SIGNED-IN-KEPT"
        ran, calls = self.run_launcher(["bench", "deliver", "MSI"], [
            ("RUNNING", "PREBOOT-READY"), ("RUNNING", kept), ("RUNNING", kept), ("RUNNING", kept), ("TERMINATED", kept)],
            beat=1000, ANGELO_GCP_HEARTBEAT_MINUTES="1", ANGELO_GCP_STALL_CONFIRM="2")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("SIGNED IN ALREADY", ran.stdout)
        self.assertIn("STALLED (2 of 2)", ran.stderr)
        self.assertIn("compute instances stop", calls)

    def test_deliver_refuses_a_machine_that_is_not_waiting(self):
        for steps in ([("TERMINATED", "")], [("RUNNING", "")], [("RUNNING", "PREBOOT-READY DONE")]):
            with self.subTest(steps=steps):
                ran, calls = self.run_launcher(["bench", "deliver", "MSI"], steps)
                self.assertEqual(ran.returncode, 1)
                self.assertIn("bench deliver refused", ran.stderr)
                self.assertNotIn("add-metadata", calls)



class SessionStartTests(unittest.TestCase):
    """Both desktop stages prove their session started within minutes, or fail saying why (2026-11-04: a held GUI
    session's boot log, read while it held, was taken for a session that never started; a real autologon failure would
    have looked the same for 35+ minutes)."""

    def test_each_session_writes_its_started_marker_first(self):
        gui = text("windows_gui_checks.ps1")
        self.assertLess(gui.index('Set-Content (Join-Path $ResultsDir "STARTED")'), gui.index('Add-Result "desktop session" "PASS"'))
        bench = text("windows_final_runs.ps1")
        session = bench[bench.index("if ($Session) {"):bench.index("if (-not $Stage)")]
        self.assertLess(session.index('Set-Content (Join-Path $out "STARTED-$Nonce")'), session.index("Start-Transcript"))
        self.assertLess(session.index("$Nonce -ne $current"), session.index('"STARTED-$Nonce"'), "a stale session never marks a new send")

    def test_the_gui_stage_waits_minutes_for_the_start_and_fails_the_run_without_it(self):
        stages = text("windows_bootstrap_stages.ps1")
        desk = stages[stages.index('Write-Host "== stage: desktop session checks (waiting for the signed-in task)"'):]
        order = [desk.index(m) for m in ('if (Wait-SessionStarted $startedFile $startMinutes', 'Write-Host ("desktop session started: "',
                                         "while (-not (Test-Path (Join-Path $guiDir \"DONE\"))", "} else {\n            # The run fails, saying why",
                                         'Write-Host "desktop session NOT STARTED: $why"', 'Set-Content $exitFile 1',
                                         'Set-ItemProperty $winlogon AutoAdminLogon "0"')]
        self.assertEqual(order, sorted(order), "started: its checks; not started: the run fails; either way the sign-in goes")
        self.assertIn('. (Join-Path $PSScriptRoot "windows_session_start.ps1")', stages)

    def test_the_bench_stage_fails_at_once_without_a_started_session(self):
        bench = text("windows_final_runs.ps1")
        wait = bench[bench.index('Write-Host "== waiting for the desktop session (until $deadline)"'):]
        self.assertLess(wait.index('$started = Wait-SessionStarted (Join-Path $out "STARTED-$nonce") $startMinutes'), wait.index("while ($started -and"))
        self.assertIn('elseif (-not $started) { "NOT STARTED: " + (Get-SessionNotStarted $benchUser "AngeloFinalRuns" $startMinutes) }', wait)
        self.assertIn('if ($result -notmatch "exit code: 0$") { exit 1 }', wait, "a session that never started fails the run")

    def test_a_marker_from_before_this_boot_never_counts_and_the_reason_names_the_sign_in(self):
        helper = text("windows_session_start.ps1")
        self.assertIn("(Get-Item $Marker).LastWriteTime -ge $boot", helper)
        for fact in ("AutoAdminLogon=", "DefaultUserName=", "password set=", "user exists=", "profiles=[", "Run key ",
                     "sessions=[", "Winlogon events=["):
            self.assertIn(fact, helper)
        self.assertNotIn("DefaultPassword)", helper.replace("[bool]$w.DefaultPassword)", ""), "never the password itself")

if __name__ == "__main__":
    unittest.main()
