# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An install check uses only the MSI built from the commit under test, and a failed release stage ends the wait.

Found on the 1.0.1 release run: the release stage refused the tree (source-clean) and built nothing, yet an install
check took the newest ``*.msi`` on the machine, an earlier run's build of commit b026c, and passed 15 checks on it.
``msi_commit.py`` accepts an MSI only when a build manifest in its folder names the file, its SHA-256 and the commit
under test; ``windows_msi_commit.ps1`` puts every install, uninstall and runtime check of the VM's scripts behind it.
``wait_off`` (gcp_windows_validate.sh) now stops on a FAIL in ``release-results.json`` instead of waiting out a hold.
The PowerShell runs only on the Windows VM, so it is checked by contract and by the structure and stderr checks the
other Windows tests use; ``msi_commit.py`` and ``wait_off`` run here.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[2] / "scripts/angelo"
for folder in (HERE, SCRIPTS):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
import msi_commit  # noqa: E402
from ps_stderr_lint import stderr_hazards  # noqa: E402
from test_native_sign_windows import ps_structure_problems  # noqa: E402

COMMIT = "5d1e0c3a9b7f4e2d8c6a1b0f9e8d7c6b5a4f3e2d"
OTHER = "b026c7e1f2a3b4c5d6e7f8091a2b3c4d5e6f7a8b"
TOUCHED = ("windows_msi_commit.ps1", "windows_teams_validate.ps1", "windows_gui_checks.ps1", "windows_validate.ps1",
           "windows_release_validate.ps1", "windows_release_inputs.ps1", "windows_cloud_bootstrap.ps1",
           "windows_source_send.ps1", "windows_agent_validate.ps1", "windows_thumbnailer_build.ps1",
           "windows_final_runs.ps1", "windows_bootstrap_stages.ps1", "windows_sign_bootstrap.ps1")


def text(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def build(folder, commit, payload, name="Angelo-1.0.1-windows-x64.msi", manifest="angelo-release-build.json", **extra):
    """An MSI and the manifest a build writes beside it (build_release_windows.ps1's shape)."""
    folder.mkdir(parents=True, exist_ok=True)
    msi = folder / name
    msi.write_bytes(payload)
    record = {"kind": "angelo-windows-release-build", "source_commit": commit, "build_hash": str(commit)[:12],
              "msi": {"name": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}
    record.update(extra)
    # PowerShell writes it; a BOM must not hide it.
    (folder / manifest).write_bytes(b"\xef\xbb\xbf" + json.dumps(record, indent=4).encode())
    return msi


class MsiCommitTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "msi_commit.py"), *map(str, args)],
                              capture_output=True, text=True)

    def test_an_msi_from_another_commit_is_refused_and_says_so(self):
        old = build(self.root / "build_windows_Release_x64_vc17_Release", OTHER, b"old build")
        done = self.run_tool("--commit", COMMIT, "--msi", old)
        self.assertEqual(done.returncode, 1)
        self.assertIn("says it was built from %s, not the commit under test" % OTHER, done.stdout)
        self.assertIn("no MSI is proven to be the build of %s" % COMMIT, done.stdout.splitlines()[-1])

    def test_the_newest_msi_on_the_disk_is_not_chosen_when_it_is_another_commits(self):
        # The incident: the build was skipped, so the build folder's MSI (newest) was an earlier run's.
        mine = build(self.root / "results", COMMIT, b"this commit", manifest="angelo-identity-build.json")
        stale = build(self.root / "build_windows_Release_x64_vc17_Release", OTHER, b"earlier run")
        os.utime(mine, (time.time() - 3600,) * 2)
        done = self.run_tool("--commit", COMMIT, "--search", self.root, "--pattern", "Angelo-*-windows-x64.msi")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], str(mine))
        self.assertIn("refused %s" % stale, done.stdout)

    def assert_refused(self, msi, reason):
        chosen, lines = msi_commit.choose(COMMIT, [msi])
        self.assertIsNone(chosen)
        self.assertIn(reason, "\n".join(lines))

    def test_proof_needs_the_manifest_the_same_bytes_and_the_same_build_hash(self):
        bare = self.root / "bare" / "Angelo-1.0.1-windows-x64.msi"   # A packaging copy (_CPack_Packages) or a download.
        bare.parent.mkdir()
        bare.write_bytes(b"x")
        self.assert_refused(bare, "no build manifest in its folder names it")
        replaced = build(self.root / "replaced", COMMIT, b"built")
        replaced.write_bytes(b"a later build's bytes under the same name")
        self.assert_refused(replaced, "the file is another build's")
        self.assert_refused(build(self.root / "hash", COMMIT, b"built", build_hash=OTHER[:12]),
                            "the built program reports hash %s" % OTHER[:12])
        self.assert_refused(build(self.root / "no-hash", COMMIT, b"built", build_hash=""), "records no build hash")
        # A manifest for a different file in the same folder proves nothing about this one.
        build(self.root / "neighbour", COMMIT, b"a", name="Angelo-1.0.0-windows-x64.msi")
        neighbour = self.root / "neighbour" / "Angelo-1.0.1-windows-x64.msi"
        neighbour.write_bytes(b"b")
        self.assert_refused(neighbour, "no build manifest in its folder names it")
        proven = build(self.root / "proven", COMMIT, b"built", build_hash=COMMIT[:12])
        self.assertEqual(msi_commit.choose(COMMIT, [proven])[0], proven)
        self.assertEqual(msi_commit.choose(COMMIT[:12], [proven])[0], proven, "an abbreviated commit names it too")

    def test_an_unknown_commit_proves_nothing(self):
        msi = build(self.root / "results", COMMIT, b"built")
        for commit in ("", "   ", "not-a-hash", COMMIT[:6]):
            with self.subTest(commit=commit):
                chosen, lines = msi_commit.choose(commit, [msi])
                self.assertIsNone(chosen)
                self.assertIn("the commit under test is unknown", lines[0])

    def test_the_search_stays_two_folders_deep(self):
        build(self.root / "src" / "a" / "b", COMMIT, b"deep inside a source tree")
        shallow = build(self.root / "results" / "release", COMMIT, b"release stage copy")
        self.assertEqual(msi_commit.candidates([], [self.root], "*.msi"), [shallow])


@unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "the launcher is bash, run from the workstation")
class ReleaseFailureWaitTests(unittest.TestCase):
    FAKE = r"""#!/usr/bin/env bash
step="$(cat "$FAKE/step")"
case "$*" in
  *"compute instances describe"*) if [[ -f "$FAKE/stopped" ]]; then echo TERMINATED; else echo RUNNING; fi ;;
  *"storage ls"*) echo "gs://bucket/result/release/"; echo $((step + 1)) > "$FAKE/step" ;;
  *"storage cat"*/result/release/release-results.json*) [[ "$step" -ge 2 && -f "$FAKE/results.json" ]] || exit 1
     cat "$FAKE/results.json" ;;
  *"compute instances stop"*) touch "$FAKE/stopped" ;;
  *) echo "unexpected gcloud $*" >&2; exit 1 ;;
esac
"""

    def wait_off(self, results, grace="15"):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder)
            (fake / "bin").mkdir()
            (fake / "bin/gcloud").write_text(self.FAKE)
            (fake / "bin/gcloud").chmod(0o755)
            (fake / "step").write_text("0")
            if results is not None:
                (fake / "results.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(results, indent=4).encode())
            env = dict(os.environ, PATH="%s%s%s" % (fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(fake),
                       ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a",
                       ANGELO_GCP_NAME="example-vm", ANGELO_GCP_POLL_SECONDS="0", ANGELO_GCP_WAIT_HOURS="1",
                       ANGELO_GCP_OFF_GRACE_MINUTES=grace)
            started = time.monotonic()
            done = subprocess.run(["bash", str(SCRIPTS / "gcp_windows_validate.sh"), "wait-off"], env=env,
                                  capture_output=True, text=True, timeout=120)
            return done, time.monotonic() - started, (fake / "stopped").exists()

    def test_a_release_fail_ends_the_wait_and_stops_the_machine_during_a_hold(self):
        failed = [{"Check": "release: the source tree is exactly the sent commit (nothing untracked)", "Status": "FAIL",
                   "Detail": "source: 12 file(s) git does not track"},
                  {"Check": "release: build_release_windows.ps1 -Package", "Status": "FAIL", "Detail": "skipped"},
                  {"Check": "release: free disk for the full build", "Status": "PASS", "Detail": "200 GB free"}]
        done, seconds, stopped = self.wait_off(failed)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(stopped, "stopped from here: no DONE comes while the machine holds")
        self.assertIn("release stage FAILED: release: the source tree is exactly the sent commit (nothing untracked); "
                      "release: build_release_windows.ps1 -Package (release-results.json)", done.stderr)
        self.assertNotIn("free disk", done.stderr)
        self.assertIn("confirmed powered off", done.stdout)
        self.assertLess(seconds, 30, "the grace period does not apply")

    def test_a_single_check_result_is_an_object_and_still_counts(self):
        done, _seconds, stopped = self.wait_off({"Check": "release: host Python 3.13", "Status": "FAIL", "Detail": "x"})
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(stopped)
        self.assertIn("release stage FAILED: release: host Python 3.13", done.stderr)


class ScriptContractTests(unittest.TestCase):
    def test_every_install_and_uninstall_goes_through_the_commit_check(self):
        for name in ("windows_teams_validate.ps1", "windows_gui_checks.ps1"):
            with self.subTest(name):
                script = text(name)
                self.assertIn('. (Join-Path $SourceDir "scripts\\angelo\\windows_msi_commit.ps1")', script)
                self.assertNotRegex(script, r"Get-ChildItem[^\n]*-windows-x64\.msi", "never the newest MSI on the disk")
        teams = text("windows_teams_validate.ps1")
        self.assertIn('[string]$Commit = ""', teams)
        install = teams[teams.index('Test-Check "teams: install the MSI"'):]
        self.assertLess(install.index("$script:Commit = Get-SentCommit $SourceDir"), install.index("Get-CommitMsi"))
        self.assertLess(install.index("Get-CommitMsi $SourceDir $Commit $MsiPath"), install.index('"/i `"$MsiPath`"'))
        # A refused install leaves nothing for the later checks to pass on.
        self.assertEqual(teams.count('if (-not $installed) { throw "skipped: no MSI of this commit was installed" }'), 2)
        self.assertIn("if (-not $KeepInstalled -and $installed)", teams)
        gui = text("windows_gui_checks.ps1")
        self.assertIn("$commit = Get-SentCommit $SourceDir", gui)
        self.assertIn('$teamsArgs += @("-Commit", $commit)', gui)
        self.assertIn('Get-CommitMsi $SourceDir $commit ""', gui)
        release = text("windows_release_validate.ps1")
        self.assertIn("Get-CommitMsi $SourceDir (Get-SentCommit $SourceDir) (Join-Path $BuildDir $manifest.msi.name)",
                      release)
        self.assertIn('source-clean $SourceDir --commit "$sent"', release)
        # The commit under test comes from the proof, never from metadata or an environment variable set at boot (a
        # queued sync changes the tree while those keep the old value).
        for name in ("windows_cloud_bootstrap.ps1", "windows_teams_validate.ps1", "windows_gui_checks.ps1",
                     "windows_release_validate.ps1", "windows_validate.ps1", "windows_final_runs.ps1"):
            with self.subTest(name):
                self.assertNotIn("ANGELO_SRC_COMMIT", text(name).replace("ANGELO_SRC_COMMIT is", ""))
                if name != "windows_cloud_bootstrap.ps1":
                    self.assertNotIn('Get-Meta "angelo-src-commit"', text(name))
        self.assertEqual(text("windows_cloud_bootstrap.ps1").count('Get-Meta "angelo-src-commit"'), 1, "read once")

    def test_the_identity_build_never_reuses_an_old_msi_and_records_its_commit(self):
        script = text("windows_validate.ps1")
        package = script[script.index('Test-Check "cpack -G WIX'):script.index("# --- 4. Installing")]
        order = [package.index(marker) for marker in ('-Filter "*.msi" -ErrorAction SilentlyContinue | Remove-Item',
                                                      "cpack -G WIX | Out-Host; $packed = $LASTEXITCODE",
                                                      "if ($packed -ne 0 -or -not $msi)",
                                                      "$sent = Get-SentCommit $SourceDir",
                                                      "throw \"the build reports hash '$reported', not the commit sent",
                                                      'source_commit = "$sent"; build_hash = "$reported"',
                                                      "angelo-identity-build.json")]
        self.assertEqual(order, sorted(order))
        self.assertIn("Copy-Item $script:msiPath, $script:msiManifest $ResultsDir", script)
        build = text("build_release_windows.ps1")
        self.assertIn("source_commit = $commit", build, "the release manifest msi_commit.py reads")
        self.assertIn("$manifest.msi = [ordered]@{ name = $msi.Name; bytes = $msi.Length; sha256 =", build)

    def test_the_touched_scripts_parse_and_never_redirect_stderr_under_stop(self):
        # windows_validate.ps1's probes of the built program predate this check (each inside a Test-Check, where a
        # stderr record fails that check only); they are pinned so that no other appears.
        known = {"windows_validate.ps1": {
            "$output = & (Join-Path $bin $exe) --background --factory-startup --python $script 2>&1 | Out-String",
            "$output = & (Join-Path $installDir $exe) --background --python $script 2>&1 | Out-String",
            "$output = & (Join-Path $installDir $exe) --background --factory-startup --python $probeScript 2>&1 | Out-String"}}
        for name in TOUCHED:
            with self.subTest(name):
                self.assertEqual(ps_structure_problems(text(name)), [])
                found = {line for _number, line in stderr_hazards(text(name))}
                self.assertEqual(found - known.get(name, set()), set())

    def test_every_touched_file_stays_within_500_lines(self):
        for name in TOUCHED + ("source_tree.py", "source_extract.py", "source_send.py", "shipped_sources.py", "msi_commit.py",
                               "gcp_windows_validate.sh", "gcp_windows_source.sh", "gcp_windows_wait.sh"):
            with self.subTest(name):
                self.assertLessEqual(len(text(name).splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
