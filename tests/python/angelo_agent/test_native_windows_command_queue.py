# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""windows_command_queue.ps1 never loses a command: it leaves the bucket only once downloaded whole and parsed, runs
once, and a failed listing or download is said (transcript and cmd-out) and retried, never an idle session.

Found on 2026-11-04 (C6, a T4 desktop session): a command was removed after a download that did not arrive, so it
vanished with no output and the session idled out for 30 minutes. Runs where PowerShell is installed (CI), with a
stand-in gcloud over a local folder that fails listings, downloads and removals on request. Not a skippable test:
the host suite allows no skips for its absence.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

QUEUE = Path(__file__).resolve().parents[3] / "scripts/angelo/windows_command_queue.ps1"
LOADER = Path(__file__).resolve().parents[3] / "scripts/angelo/windows_queue_loader.ps1"
BOOTSTRAP = Path(__file__).resolve().parents[3] / "scripts/angelo/windows_cloud_bootstrap.ps1"
SENDER = Path(__file__).resolve().parents[3] / "scripts/angelo/gcp_windows_validate.sh"

# Errors go to the error stream (Write-Error), as a real gcloud's stderr reaches the caller's 2>&1.
# gcloud storage ls|cp|rm over $env:FAKE_ROOT (gs://bucket/x -> FAKE_ROOT/bucket/x). FAKE_FAIL_<VERB> holds how many
# more calls of that verb fail (FAKE_FAIL_CP counts downloads from gs:// only).
FAKE_GCLOUD = r'''
$verb, $rest = $args[1], @($args | Select-Object -Skip 2 | Where-Object { $_ -ne "--quiet" })
function Local([string]$url) { if ($url -like "gs://*") { Join-Path $env:FAKE_ROOT $url.Substring(5) } else { $url } }
function Fail([string]$name) {
    $count = [int][Environment]::GetEnvironmentVariable($name)
    if ($count -gt 0) { [Environment]::SetEnvironmentVariable($name, [string]($count - 1)); $true } else { $false }
}
$state = Join-Path $env:FAKE_ROOT "fail.json"
if (Test-Path $state) { (Get-Content $state -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, [string]$_.Value) } }
function Save { $o = @{}; foreach ($n in "FAKE_FAIL_LS", "FAKE_FAIL_CP", "FAKE_FAIL_RM") { $o[$n] = [int][Environment]::GetEnvironmentVariable($n) }; $o | ConvertTo-Json | Set-Content $state }
switch ($verb) {
  "ls" { if (Fail "FAKE_FAIL_LS") { Save; Write-Error ("ERROR: (gcloud.storage.ls) network unreachable"); exit 1 }
         Save; $dir = Local $rest[0]
         $items = @(Get-ChildItem $dir -File -ErrorAction SilentlyContinue)
         if (-not $items) { Write-Error ("ERROR: (gcloud.storage.ls) One or more URLs matched no objects."); exit 1 }
         $items | ForEach-Object { $rest[0].TrimEnd("/") + "/" + $_.Name }; exit 0 }
  "cp" { $sources, $dest = $rest[0..($rest.Count - 2)], $rest[-1]
         if ($sources[0] -like "gs://*" -and (Fail "FAKE_FAIL_CP")) { Save; Write-Error ("ERROR: (gcloud.storage.cp) 503"); exit 1 }
         Save; $to = Local $dest
         foreach ($s in $sources) { if (-not (Test-Path (Local $s))) { Write-Error ("ERROR: (gcloud.storage.cp) not found: $s"); exit 1 } }
         foreach ($s in $sources) { if ($dest.EndsWith("/") -or $dest.EndsWith("\")) { New-Item -ItemType Directory -Force $to | Out-Null; Copy-Item (Local $s) (Join-Path $to (Split-Path $s -Leaf)) } else { Copy-Item (Local $s) $to } }
         exit 0 }
  "rm" { if (Fail "FAKE_FAIL_RM") { Save; Write-Error ("ERROR: (gcloud.storage.rm) 503"); exit 1 }
         Save; Remove-Item (Local $rest[0]) -ErrorAction SilentlyContinue; exit 0 }
}
'''


class Queue(unittest.TestCase):
    def setUp(self):
        self.pwsh = shutil.which("pwsh")
        temp = tempfile.TemporaryDirectory(prefix="angelo-queue-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bucket = self.root / "b"
        (self.bucket / "cmd").mkdir(parents=True)
        (self.root / "gcloud.ps1").write_text(FAKE_GCLOUD, encoding="utf-8")

    def queue(self, name, body):
        (self.bucket / "cmd" / (name + ".ps1")).write_text(body, encoding="utf-8")

    def inject(self, **counts):
        import json
        (self.root / "fail.json").write_text(json.dumps({"FAKE_FAIL_" + key.upper(): value for key, value in counts.items()}))

    def invoke(self, looks=1):
        """``looks`` calls of Invoke-QueuedCommands in one session (the state a real wait keeps between looks)."""
        calls = "; ".join(["$ran += Invoke-QueuedCommands '%s' b cmd '%s' '%s' 5 pwsh" % (
            self.root / "gcloud.ps1", self.root / "logs", self.root / "work")] * looks)
        command = ". '%s'; $ran = 0; %s; Write-Host \"RAN $ran\"" % (QUEUE, calls)
        env = dict(os.environ, FAKE_ROOT=str(self.root))
        done = subprocess.run([self.pwsh, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                              text=True, env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        return done.stdout

    def out(self, name):
        path = self.bucket / "cmd-out" / name
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def test_a_failed_download_keeps_the_command_and_the_next_look_runs_it_once(self):
        if not self.pwsh:
            return
        self.queue("probe", "'ran' | Add-Content '%s'" % (self.root / "marker.txt"))
        self.inject(cp=1)
        said = self.invoke()
        self.assertIn("queue problem", said)
        self.assertIn("download failed", said)
        self.assertTrue((self.bucket / "cmd" / "probe.ps1").exists())   # Never removed before it was fetched.
        self.assertIn("RAN 1", self.invoke(looks=2))   # Fetched now; the second look finds nothing left to run.
        self.assertEqual((self.root / "marker.txt").read_text().split(), ["ran"])
        self.assertEqual(self.out("cmd-cmd-probe.log.exit"), "0")
        self.assertFalse((self.bucket / "cmd" / "probe.ps1").exists())

    def test_a_command_that_cannot_be_fetched_says_so_where_the_sender_waits(self):
        if not self.pwsh:
            return
        self.queue("probe", "'ran' | Add-Content '%s'" % (self.root / "marker.txt"))
        self.inject(cp=5)
        self.assertIn("RAN 0", self.invoke(looks=3))
        self.assertEqual(self.out("cmd-cmd-probe.log.exit"), "fetch-failed")
        self.assertIn("NOT RUN", self.out("cmd-cmd-probe.log"))
        self.assertFalse((self.root / "marker.txt").exists())
        self.assertFalse((self.bucket / "cmd" / "probe.ps1").exists())

    def test_a_command_that_does_not_parse_never_runs(self):
        if not self.pwsh:
            return
        self.queue("broken", "if ($true) { 'unclosed'")
        self.invoke(looks=3)
        self.assertEqual(self.out("cmd-cmd-broken.log.exit"), "fetch-failed")
        self.assertIn("does not parse", self.out("cmd-cmd-broken.log"))

    def test_a_failed_listing_is_said_and_an_empty_one_is_not(self):
        if not self.pwsh:
            return
        self.assertNotIn("queue problem", self.invoke())   # "matched no objects" is an empty queue.
        self.inject(ls=1)
        said = self.invoke()
        self.assertIn("listing gs://b/cmd/ failed", said)
        self.assertIn("network unreachable", self.out("cmd-cmd-queue-problem.txt"))

    def test_a_command_whose_removal_failed_runs_once(self):
        if not self.pwsh:
            return
        self.queue("probe", "'ran' | Add-Content '%s'" % (self.root / "marker.txt"))
        self.inject(rm=1)
        self.assertIn("RAN 1", self.invoke(looks=3))
        self.assertEqual((self.root / "marker.txt").read_text().split(), ["ran"])
        self.assertFalse((self.bucket / "cmd" / "probe.ps1").exists())   # Removed on a later look.


class Loader(unittest.TestCase):
    """windows_queue_loader.ps1's Resolve-QueueTool: the published copy when it checks out, else the tree's, loudly."""

    def setUp(self):
        self.pwsh = shutil.which("pwsh")
        temp = tempfile.TemporaryDirectory(prefix="angelo-queue-tool-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "gcloud.ps1").write_text(FAKE_GCLOUD, encoding="utf-8")
        self.tree = self.root / "src/scripts/angelo"
        self.tree.mkdir(parents=True)
        (self.tree / "windows_command_queue.ps1").write_text("function Tree {}", encoding="utf-8")
        (self.root / "b/tools").mkdir(parents=True)
        (self.root / "state").mkdir()

    def publish(self, body, digest=None):
        import hashlib
        import json
        (self.root / "b/tools/windows_command_queue.ps1").write_bytes(body.encode())
        digest = digest or hashlib.sha256(body.encode()).hexdigest()
        (self.root / "b/tools/manifest.json").write_text(json.dumps({"windows_command_queue.ps1": {"sha256": digest}}))

    def resolve(self):
        text = LOADER.read_text(encoding="utf-8")
        start = text.index("function Resolve-QueueTool")
        function = text[start:text.index("\n}\n", start) + 3]
        command = function + "\n$p = Resolve-QueueTool '%s' b '%s' '%s'; Write-Host \"CHOSE $p\"" % (
            self.root / "gcloud.ps1", self.root / "src", self.root / "state")
        done = subprocess.run([self.pwsh, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                              text=True, env=dict(os.environ, FAKE_ROOT=str(self.root)), timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        chosen = (self.root / "state/queue-tool.txt").read_text(encoding="utf-8").strip()
        return done.stdout, Path(chosen)

    def test_every_send_carries_the_loader_and_the_boot_runs_it_with_the_tree_as_its_default(self):
        sender, boot = SENDER.read_text(encoding="utf-8"), BOOTSTRAP.read_text(encoding="utf-8")
        self.assertEqual(sender.count("windows_cloud_bootstrap.ps1$(queue_loader)"), 2)   # up and redo
        self.assertIn("angelo-queue-loader=$ROOT/scripts/angelo/windows_queue_loader.ps1", sender)
        self.assertIn('Get-Meta "angelo-queue-loader" ". (Join-Path `$src', boot)
        self.assertTrue(LOADER.read_text(encoding="utf-8").rstrip().endswith(". (Resolve-QueueTool $gcloud $bucket $src $state)"))

    def test_the_published_copy_is_loaded_when_its_digest_and_syntax_check_out(self):
        if not self.pwsh:
            return
        self.publish("function Published {}")
        said, chosen = self.resolve()
        self.assertEqual(chosen.parent, self.root / "state/tools")
        self.assertIn("the published copy", said)

    def test_anything_else_falls_back_to_the_trees_copy_and_says_why(self):
        if not self.pwsh:
            return
        cases = {"none published": None, "is not the manifest's": ("function Published {}", "0" * 64),
                 "does not parse": ("function Broken {", None)}
        for why, published in cases.items():
            with self.subTest(why):
                for leftover in (self.root / "b/tools").iterdir():
                    leftover.unlink()
                if published is not None:
                    self.publish(*published)
                said, chosen = self.resolve()
                self.assertEqual(chosen, self.tree / "windows_command_queue.ps1")
                self.assertIn("FALLING BACK", said)
                self.assertIn(why, said)


if __name__ == "__main__":
    unittest.main()
