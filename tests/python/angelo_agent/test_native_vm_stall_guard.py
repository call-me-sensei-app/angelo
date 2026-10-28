# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The VM sender's stall guard and disk floor on the 1.0.4 gates line (ported from bugfix/v1-vm-stall-guard):
gcp_windows_validate.sh wait-off against a stand-in gcloud, and pins of the bootstrap's preparation deadline,
heartbeat, disk cleanup and floor, and of windows_final_runs.ps1's session and grant rules."""

import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[2] / "scripts/angelo"


def text(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


class StallGuardTests(unittest.TestCase):
    FAKE = r"""#!/usr/bin/env bash
step="$(cat "$FAKE/step")"
pick() { local n="$step"; while [[ ! -f "$FAKE/$1.$n" ]]; do n=$((n - 1)); done; cat "$FAKE/$1.$n"; }
case "$*" in
  *"compute instances describe"*"--format=json"*)
    printf '{"metadata": {"items": [{"key": "angelo-src-nonce", "value": "%s"}]}}' "$(cat "$FAKE/nonce" 2>/dev/null)" ;;
  *"compute instances describe"*"maxRunDuration"*) cat "$FAKE/limit" 2>/dev/null || echo 28800 ;;
  *"compute instances describe"*) if [[ -f "$FAKE/stops" ]]; then echo TERMINATED; else pick vm; fi ;;
  *"compute instances set-scheduling"*) echo "$*" >> "$FAKE/scheduling" ;;
  *"storage ls"*) for name in $(pick ls); do echo "gs://bucket/result/$name"; done; echo $((step + 1)) > "$FAKE/step" ;;
  *"storage cat"*/result/DONE*) cat "$FAKE/DONE" ;;
  *"storage cat"*/result/FAILED*) cat "$FAKE/FAILED" ;;
  *"storage cat"*/live/HEARTBEAT*)
    fails="$(cat "$FAKE/HEARTBEAT_FAILS" 2>/dev/null || echo 0)"
    if (( fails > 0 )); then echo $((fails - 1)) > "$FAKE/HEARTBEAT_FAILS"; echo "ERROR: token expired" >&2; exit 1; fi
    if [[ ! -f "$FAKE/HEARTBEAT" ]]; then echo "ERROR: One or more URLs matched no objects." >&2; exit 1; fi
    cat "$FAKE/HEARTBEAT" ;;
  *"compute instances stop"*) echo stopped >> "$FAKE/stops" ;;
  *) echo "unexpected gcloud $*" >&2; exit 1 ;;
esac
"""

    def wait_off(self, steps, done=None, failed=None, command=("wait-off",), nonce=None, env_extra=None, stops=None,
                 heartbeat=None, fails=0):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder)
            (fake / "bin").mkdir()
            (fake / "bin/gcloud").write_text(self.FAKE)
            (fake / "bin/gcloud").chmod(0o755)
            (fake / "step").write_text("0")
            for index, (vm, objects) in enumerate(steps):
                (fake / ("vm.%d" % index)).write_text(vm + "\n")
                (fake / ("ls.%d" % index)).write_text(objects)
            if fails:
                (fake / "HEARTBEAT_FAILS").write_text(str(fails))
            for name, content in (("DONE", done), ("FAILED", failed), ("nonce", nonce), ("HEARTBEAT", heartbeat)):
                if content is not None:
                    (fake / name).write_text(content)
            env = dict(os.environ, PATH="%s%s%s" % (fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(fake),
                       ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a",
                       ANGELO_GCP_NAME="example-vm", ANGELO_GCP_POLL_SECONDS="0",
                       ANGELO_GCP_READY_SETTLE_SECONDS="0", ANGELO_GCP_OFF_GRACE_MINUTES="0", **(env_extra or {}))
            env.pop("ANGELO_GCP_RUN_SINCE", None) if not (env_extra or {}).get("ANGELO_GCP_RUN_SINCE") else None
            ran = subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), *command], env=env,
                                 capture_output=True, text=True, timeout=60)
            if stops is not None:
                stops.append((fake / "stops").read_text().count("stopped") if (fake / "stops").exists() else 0)
            return ran

    def stalled(self, heartbeat_age, progress_age, stages="bench", sent_ago=7200, deadline=False):
        """A run sent ``sent_ago`` seconds ago, still RUNNING, whose heartbeat is ``heartbeat_age`` seconds old (None:
        none) with output last written ``progress_age`` seconds ago."""
        now = int(time.time())
        since = now - sent_ago
        beat = None if heartbeat_age is None else (
            "at: %d\nprogress: %d\nnewest: C:\\angelo\\bench\\download\\x\nlog: bench.log\n--\n== bench stage: ab\n"
            % (now - heartbeat_age, now - progress_age))
        stops, env = [], {"ANGELO_GCP_RUN_SINCE": str(since), "ANGELO_GCP_STAGES": stages}
        if deadline:
            env["ANGELO_GCP_WAIT_HOURS"] = "0"
        ran = self.wait_off([("RUNNING", ""), ("RUNNING", ""), ("RUNNING", "")], nonce=str(since), stops=stops,
                            heartbeat=beat, env_extra=env)
        return ran, stops

    def test_a_hung_bench_is_stopped_at_its_stalled_step_not_at_its_maximum_run_time(self):
        """aids-sol-b 2026-10-21: a bench's preparation hung 8 h before its 4 h cap, silent (review: pilot-g). No
        deadline here: only the stall can stop it (a missed stall would hang the test until its timeout)."""
        ran, stops = self.stalled(heartbeat_age=60, progress_age=3600)
        self.assertEqual(stops, [1])
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("STALLED (2 of 2): no output for 60 minutes", ran.stderr)
        self.assertIn("== bench stage: ab", ran.stderr)   # The machine's last log lines name the step.
        ran, stops = self.stalled(heartbeat_age=1800, progress_age=1800)
        self.assertEqual(stops, [1])
        self.assertIn("heartbeat 30 minutes old", ran.stderr)
        ran, stops = self.stalled(heartbeat_age=None, progress_age=0)
        self.assertEqual(stops, [1])
        self.assertIn("no heartbeat", ran.stderr)

    def test_one_failed_heartbeat_read_never_stops_a_run(self):
        """census-directtools 2026-10-22: one empty read of a live heartbeat stopped a healthy dry run."""
        now = int(time.time())
        since = now - 7200
        beat = "at: %d\nprogress: %d\nnewest: x\nlog: x\n--\n" % (now - 60, now - 60)
        for fails in (1, 2):   # Failed reads (a token expiry, the network) are unknown, never a stall.
            ran = self.wait_off([("RUNNING", "")], nonce=str(since), heartbeat=beat, fails=fails,
                                env_extra={"ANGELO_GCP_RUN_SINCE": str(since), "ANGELO_GCP_STAGES": "bench",
                                           "ANGELO_GCP_WAIT_HOURS": "0"})   # Ends at the deadline, not as stalled.
            self.assertIn("heartbeat unreadable this poll", ran.stderr)
            self.assertNotIn("STALLED", ran.stderr)

    def test_a_heartbeat_with_its_fields_on_one_line_still_reads(self):
        """The first live heartbeat (aids-sol-b, 2026-10-21) wrote its fields on one line (a comma binds tighter than
        + in PowerShell); the wait reads either form."""
        now = int(time.time())
        since = now - 7200
        beat = "at: %d progress: %d newest: C:\\angelo\\logs\\x.log log: x.log --\n== test: hangs\n" % (now - 60, now - 3600)
        stops = []
        ran = self.wait_off([("RUNNING", "")], nonce=str(since), stops=stops, heartbeat=beat,
                            env_extra={"ANGELO_GCP_RUN_SINCE": str(since), "ANGELO_GCP_STAGES": "bench"})
        self.assertEqual(stops, [1])
        self.assertIn("STALLED (2 of 2): no output for 60 minutes", ran.stderr)
        self.assertIn("== test: hangs", ran.stderr)

    def test_a_moving_bench_and_a_quiet_build_are_never_stopped_as_stalled(self):
        for heartbeat_age, progress_age, stages in ((60, 60, "bench"), (60, 3600, "release"), (None, 0, "bench")):
            sent_ago = 7200 if heartbeat_age is not None else 300   # No heartbeat yet, minutes after the send.
            ran, stops = self.stalled(heartbeat_age, progress_age, stages, sent_ago, deadline=True)
            self.assertNotIn("STALLED", ran.stderr, (heartbeat_age, progress_age, stages))

    def test_the_heartbeat_outlives_every_stage(self):
        boot = text("windows_cloud_bootstrap.ps1")
        self.assertIn("$heartbeat = Start-Job", boot)
        self.assertIn('"gs://$bucket/live/HEARTBEAT"', boot)
        self.assertIn('@(("at: " + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()), "progress: $progress"', boot)
        self.assertEqual(boot.count("Stop-Job $heartbeat"), 1)   # Only when the script ends, never by a stage.
        self.assertGreater(boot.index("Stop-Job $heartbeat"), boot.index("== stage: results"))

    def test_progress_watches_every_folder_the_bench_stage_writes_and_no_load_sample(self):
        """C3 Windows Sol gate 2026-10-26: trials wrote only under bench\\campaign-<candidate>, which the heartbeat never
        watched, so a gate over ~47 min was stopped as hung mid-trial (SOL-GATE-STALL). Every output folder
        windows_final_runs.ps1 names is watched, wildcards resolved each pass, and a load sample is never progress."""
        boot, runs = text("windows_cloud_bootstrap.ps1"), text("windows_final_runs.ps1")
        block = boot[boot.index("$heartbeat = Start-Job"):boot.index("Sort-Object LastWriteTimeUtc -Descending")]
        for var, name in (("state", "state"), ("out", "out")):
            self.assertIn('$%s = Join-Path $bench "%s"' % (var, name), runs)
            self.assertIn('"bench\\%s"' % name, block)
        self.assertIn('campaign = (Join-Path $bench ("campaign-"', runs)
        self.assertIn('"bench\\campaign-*"', block)
        self.assertIn("Get-Item $_ -ErrorAction SilentlyContinue", block)   # A wildcard leaf never filters the recursion.
        self.assertIn("Get-ChildItem -LiteralPath $_.FullName -Recurse -File", block)
        self.assertIn('Where-Object { $_.Name -notlike "*load.jsonl" }', boot)
        self.assertIn('Path(str(folder) + "-load.jsonl")', (HERE / "final_runs.py").read_text())

    def test_the_tree_stages_are_loaded_after_the_proof_and_only_the_startup_script_restarts(self):
        boot, stages = text("windows_cloud_bootstrap.ps1"), text("windows_bootstrap_stages.ps1")
        proof = boot.index("    Assert-SentTree $src\n    # The stages below")
        self.assertLess(proof, boot.index('. (Join-Path $src "scripts\\angelo\\windows_bootstrap_stages.ps1")'))
        for name in ("Invoke-ToolkitStage", "Invoke-DesktopStage", "Invoke-BenchStage"):
            self.assertIn("function %s([hashtable]$Reboot)" % name, stages)
            self.assertEqual(boot.count("Invoke-StageStep %s" % name), 1)
        # An exit in a dot-sourced file ends only that file: a stage asks, and the startup script restarts.
        code = "\n".join(line.split("#", 1)[0] for line in stages.split("#>", 1)[1].splitlines())
        self.assertNotRegex(code, r"(?m)(^|[;{])\s*exit\b|Restart-Computer")   # exit as a statement
        self.assertEqual(stages.count("$Reboot.Now = $true"), 3)
        self.assertIn("if ($reboot.Now) { Sync-Logs; Restart-Computer -Force; exit 0 }", boot)

    def test_the_preparation_has_its_own_deadline_inside_the_machine(self):
        """Before the desktop session exists the stage is ended at angelo-bench-prep-minutes and the run fails saying
        so (result/FAILED), not only stopped from outside."""
        stages = text("windows_bootstrap_stages.ps1")   # 4c, dot-sourced by the bootstrap from the sent tree.
        stage = stages[stages.index('Write-Host "== stage: bench"'):]
        self.assertIn('Get-Meta "angelo-bench-prep-minutes" "90"', stage)
        self.assertIn('"bench\\state\\session-"', stage)   # Windows_final_runs.ps1's own marker of a prepared session.
        self.assertIn("taskkill.exe /T /F /PID $process.Id", stage)
        self.assertRegex(stage, r'throw "bench preparation did not finish within')
        self.assertIn('if ("$_" -like "bench preparation did not finish*") { throw }', stage)
        runs = text("windows_final_runs.ps1")
        self.assertIn('$prepared = Join-Path $state "session-$nonce"', runs)   # The marker the deadline waits for.
        gcp = text("gcp_windows_validate.sh")
        self.assertIn("angelo-bench-prep-minutes=${ANGELO_GCP_BENCH_PREP_MINUTES:-90}", gcp)

    def test_the_injected_hang_is_off_unless_a_test_asks_for_it(self):
        runs = text("windows_final_runs.ps1")
        self.assertIn('[int](Get-Meta "angelo-bench-test-prep-hang-minutes" "0")', runs)
        self.assertIn("if ($testHang -gt 0)", runs)
        self.assertIn("angelo-bench-test-prep-hang-minutes=${ANGELO_GCP_TEST_PREP_HANG_MINUTES:-0}",
                      text("gcp_windows_validate.sh"))

    def test_the_disk_cleanup_removes_only_other_sends_arm_copies_on_a_new_send(self):
        """aids-sol-b 2026-10-22: per-label arm copies filled C: (index-pack failed, no log could be written). The
        cleanup runs before anything writes, only on a new send whose nonce was read, only on bench\\ab's own entries
        named like arm copies, keeping this send's exact copies, never following a link, and nothing in it can end
        the script (break-it review 2026-10-22)."""
        boot = text("windows_cloud_bootstrap.ps1")
        start, transcript = boot.index("# --- 0. Room on C:"), boot.index("Start-Transcript")
        self.assertLess(start, transcript)   # Before anything writes.
        block = boot[start:boot.index('$hygiene | ForEach-Object { Write-Host "disk: $_" }')]
        self.assertIn("$newSend = $nonceNow -and $nonceNow -ne $lastNonce", block)   # An unread nonce: no cleanup.
        self.assertIn("Get-Content -LiteralPath (Join-Path $state 'src.nonce') -ErrorAction SilentlyContinue", block)
        self.assertIn("Get-ChildItem -LiteralPath $abRoot -Force", block)   # Only bench\\ab's own entries.
        self.assertIn('"^(src|overlay|candidate)-."', block)
        self.assertIn('"^arm-.+-overlay\\.tar\\.gz$"', block)
        self.assertIn('$keep += "candidate-$label-$leaf"', block)   # This send's exact app copies.
        self.assertIn("[IO.FileAttributes]::ReparsePoint", block)
        deletes = [line.strip() for line in block.splitlines() if "Delete(" in line or "rd /s /q" in line]
        self.assertEqual(len(deletes), 3)
        self.assertTrue(all("$item.FullName" in line for line in deletes))
        self.assertNotIn("Remove-Item", block)
        self.assertIn('$ErrorActionPreference = "Continue"', block)
        self.assertIn('$hygiene += "before the stages: $_"', block)   # Nothing here ends the script.

    def test_a_metadata_key_that_fails_to_read_is_tried_again(self):
        boot = text("windows_cloud_bootstrap.ps1")
        meta = boot[boot.index("function Get-Meta"):boot.index("# --- 0. Room on C:")]
        self.assertIn("[int]$response.StatusCode -eq 404) { return $Default }", meta)
        self.assertIn("for ($try = 1; $try -le 3; $try++)", meta)

    def test_a_send_refuses_to_start_below_its_floor_but_a_held_cleanup_send_never_does(self):
        boot = text("windows_cloud_bootstrap.ps1")
        self.assertIn('[double](Get-Meta "angelo-min-free-gb" "30")', boot)
        self.assertIn('$needsRoom = -not ([int](Get-Meta "angelo-hold-minutes" "0") -gt 0 -and $working.Count -eq 0)', boot)
        self.assertIn("ANGELO_GCP_STAGES=none ANGELO_GCP_HOLD_MINUTES=30", boot)   # The exact way out, named.
        self.assertIn("if ($newSend -and $needsRoom -and (Get-FreeGb) -lt $minFree)", boot)
        self.assertIn("Largest folders:", boot)
        self.assertIn('"free_gb: $free"', boot)
        self.assertIn("angelo-min-free-gb=${ANGELO_GCP_MIN_FREE_GB:-30}", text("gcp_windows_validate.sh"))   # Always set.
        # A failure is uploaded from memory when the file cannot be written, and the machine still powers off.
        self.assertIn('$failedText | & $gcloud storage cp --quiet - "gs://$bucket/result/FAILED"', boot)
        self.assertIn("-or $failedRun)", boot)

    def test_the_bench_grant_goes_to_a_group_once_per_machine(self):
        runs = text("windows_final_runs.ps1")
        self.assertIn('Add-LocalGroupMember -Group $benchGroup -Member $benchUser', runs)
        self.assertIn('if (-not (Test-Path $granted))', runs)
        self.assertIn('& icacls.exe $bench /grant "${benchGroup}:(OI)(CI)M" /T /C\n', runs)   # Listed: progress.
        self.assertIn("if ($walkExit -eq 0) { Set-Content $granted", runs)   # Refused items: walked again.
        self.assertIn('& icacls.exe $connectionsDir /grant "${benchGroup}:(OI)(CI)M" /T /C /Q', runs)   # Every send.
        self.assertIn("if (Test-Path $walking) { $prepDeadline = (Get-Date).AddMinutes($prepMinutes) }",
                      text("windows_bootstrap_stages.ps1"))
        self.assertNotIn('icacls.exe $bench /grant "${benchUser}', runs)

    def test_a_sends_short_limit_goes_back_to_the_default_once_the_machine_is_off(self):
        """aids-sol-b 2026-10-22: a test send's 20 minute limit stayed on the machine after the send (a start refused
        for want of a GPU), where a later sender would have inherited it."""
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder)
            (fake / "bin").mkdir()
            (fake / "bin/gcloud").write_text(self.FAKE)
            (fake / "bin/gcloud").chmod(0o755)
            (fake / "step").write_text("0")
            (fake / "vm.0").write_text("TERMINATED\n")
            (fake / "ls.0").write_text("")
            (fake / "limit").write_text("1200\n")
            env = dict(os.environ, PATH="%s%s%s" % (fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(fake),
                       ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a",
                       ANGELO_GCP_NAME="example-vm", ANGELO_GCP_POLL_SECONDS="0")
            subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), "wait-off"], env=env,
                           capture_output=True, text=True, timeout=60)
            self.assertIn("--max-run-duration=8h", (fake / "scheduling").read_text())
        gcp = text("gcp_windows_validate.sh")
        self.assertIn('g compute instances start "$NAME" --zone="$ZONE" || { restore_limit; exit 1; }', gcp)

    def test_a_stale_session_never_runs_beside_a_new_send(self):
        runs = text("windows_final_runs.ps1")
        self.assertIn("if ($current -and $Nonce -ne $current) {", runs)   # Unread: never ends a run.
        self.assertIn("bench-session-stale.log", runs)
        self.assertIn("-TimeoutSec 5", runs)

    def test_each_start_sets_its_own_maximum_run_time_and_a_bench_its_hours_plus_one(self):
        gcp = text("gcp_windows_validate.sh")
        redo = gcp[gcp.index("redo() {"):gcp.index("status() {")]
        self.assertLess(redo.index('set-scheduling "$NAME"'), redo.index('instances start "$NAME"'))
        self.assertIn('--max-run-duration="${ANGELO_GCP_RUN_DURATION:-${RUN_HOURS}h}"', redo)
        self.assertIn('"gs://$BUCKET/live/"', redo)   # An earlier send's heartbeat never counts for this one.
        self.assertIn('export ANGELO_GCP_RUN_SINCE="${nonce%%-*}"', redo)
        bench = gcp[gcp.index("bench() {"):]
        self.assertIn("RUN_HOURS=$(( BENCH_HOURS + 1 ))", bench)


if __name__ == "__main__":
    unittest.main()
