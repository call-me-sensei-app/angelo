# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows build machine's commit is known only from the machine: the delta base is what it proved.

Found twice in one night on another build machine: the launcher wrote its "uploaded commit" marker before the
machine had the commit; a cut-off send left that marker naming a commit the machine never received, and the next
send computed its delta from it and shipped an empty delta. Now the machine alone writes input/confirmed-commit, after
proving its tree is that commit by release_checks.py source-clean's rule (source_send.py); the launcher computes
every delta from it, and every stage and source-dependent script refuses a tree that is not the commit sent.

These tests run the real launcher (``redo`` and its sending code, copied into a throwaway repository) against a
stand-in ``gcloud`` that keeps the bucket in a folder and can cut a send off at any object, and the machine side with
the very tools each send carries. The PowerShell that joins them runs only on the VM; its marker rules are checked by
contract here.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[2] / "scripts/angelo"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import shipped_sources  # noqa: E402

TOOLS = ("gcp_windows_validate.sh", "gcp_windows_source.sh", "gcp_windows_wait.sh", "source_tree.py", "source_extract.py",
         "source_send.py", "shipped_sources.py", "windows_source_send.ps1", "windows_cloud_bootstrap.ps1")
# The filter Git LFS installs (git lfs install), here for every repository: the machine's index holds pointers.
LFS = {"filter.lfs.clean": "git-lfs clean -- %f", "filter.lfs.smudge": "git-lfs smudge -- %f",
       "filter.lfs.process": "git-lfs filter-process", "filter.lfs.required": "true"}
ENV = dict(os.environ, GIT_AUTHOR_NAME="Angelo Tests", GIT_AUTHOR_EMAIL="tests@angelo.invalid",
           GIT_COMMITTER_NAME="Angelo Tests", GIT_COMMITTER_EMAIL="tests@angelo.invalid", GIT_CONFIG_NOSYSTEM="1",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT=str(len(LFS)), COPYFILE_DISABLE="1")
for index, (key, value) in enumerate(LFS.items()):
    ENV["GIT_CONFIG_KEY_%d" % index], ENV["GIT_CONFIG_VALUE_%d" % index] = key, value

FAKE_GCLOUD = r'''#!/usr/bin/env python3
"""A stand-in gcloud: the bucket is $FAKE/bucket; $FAKE_CUT (a glob) cuts a send off at the first object it names;
$FAKE_RACE (a glob) runs $FAKE_RACE_CMD in the checkout while that object goes up, as another session might."""
import fnmatch, json, os, shutil, subprocess, sys
from pathlib import Path
fake = Path(os.environ["FAKE"]); bucket = fake / "bucket"
args = [a for a in sys.argv[1:] if not a.startswith("--project=")]
with open(fake / "log", "a") as log:
    log.write(" ".join(args) + "\n")
def rel(url):
    return url.split("/", 3)[3] if url.count("/") >= 3 else ""
words = [a for a in args if not a.startswith("--")]
if words[:2] == ["storage", "cp"]:
    *sources, target = words[2:]
    for source in sources:
        name = rel(target) + Path(source).name if target.endswith("/") else rel(target)
        if os.environ.get("FAKE_CUT") and fnmatch.fnmatch(name, os.environ["FAKE_CUT"]):
            sys.exit("cut off before %s" % name)
        if os.environ.get("FAKE_RACE") and fnmatch.fnmatch(name, os.environ["FAKE_RACE"]):
            subprocess.run(os.environ["FAKE_RACE_CMD"], shell=True, check=True)
        (bucket / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, bucket / name)
elif words[:2] == ["storage", "cat"]:
    path = bucket / rel(words[2])
    if not path.is_file():
        sys.exit("no such object")
    sys.stdout.write(path.read_text())
elif words[:2] == ["storage", "rm"]:
    for pattern in words[2:]:
        for path in [p for p in bucket.rglob("*") if fnmatch.fnmatch(p.relative_to(bucket).as_posix(), rel(pattern))]:
            shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
elif words[:3] == ["compute", "instances", "add-metadata"]:
    meta = json.loads((fake / "metadata.json").read_text()) if (fake / "metadata.json").exists() else {}
    for arg in args:
        if arg.startswith("--metadata="):
            meta.update(item.split("=", 1) for item in arg[len("--metadata="):].split(",") if "=" in item)
    (fake / "metadata.json").write_text(json.dumps(meta))
elif words[:3] == ["compute", "instances", "describe"]:
    print("TERMINATED")
elif words[:3] in (["compute", "instances", "start"], ["compute", "instances", "reset"]):
    pass
elif words[:3] == ["compute", "instances", "set-scheduling"]:   # redo's per-run limit (the stall guard): recorded
    (fake / "scheduling.json").write_text(json.dumps([a for a in args if a.startswith("--max-run-duration=")]))
else:
    sys.exit("unexpected gcloud %s" % " ".join(args))
'''


def git(folder, *args, **kwargs):
    return subprocess.run(["git", "-C", str(folder), *args], env=ENV, check=True, capture_output=True, **kwargs).stdout


def git_lfs_works():
    return shutil.which("git") and shutil.which("bash") and subprocess.run(
        ["git", "lfs", "version"], capture_output=True).returncode == 0


class LauncherFixture:
    """A throwaway repository holding the launcher, a stand-in gcloud, and the machine's tree and state folders."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo, self.fake, self.tree = self.root / "repo", self.root / "fake", self.root / "machine/src"
        self.state, self.bucket = self.root / "machine/state", self.root / "fake/bucket"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin/gcloud").write_text(FAKE_GCLOUD)
        (self.fake / "bin/gcloud").chmod(0o755)
        (self.repo / "scripts/angelo").mkdir(parents=True)
        for name in TOOLS:
            shutil.copy(SCRIPTS / name, self.repo / "scripts/angelo" / name)
        git(self.repo, "init", "-q")
        self.commit({".gitattributes": "assets/*.bin filter=lfs diff=lfs merge=lfs -text\n",
                     "assets/large.bin": "large v1\n", "keep.txt": "keep\n", "edit.txt": "v1\n", "gone.txt": "gone\n",
                     "Ελληνικά/αρχείο.txt": "greek\n"})
        self.first = self.head()

    def commit(self, files, removed=()):
        for name in removed:
            git(self.repo, "rm", "-q", name)
        for name, text in files.items():
            Path(self.repo, name).parent.mkdir(parents=True, exist_ok=True)
            Path(self.repo, name).write_text(text, encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change")
        return self.head()

    def head(self):
        return git(self.repo, "rev-parse", "HEAD").decode().strip()

    def redo(self, cut=None, *extra, race=None):
        env = dict(ENV, PATH="%s%s%s" % (self.fake / "bin", os.pathsep, os.environ["PATH"]), FAKE=str(self.fake),
                   ANGELO_GCP_PROJECT="example-project", ANGELO_GCP_ZONE="example-zone-a", ANGELO_GCP_NAME="example-vm",
                   ANGELO_GCP_BUCKET="example-bucket", GIT_LFS_SKIP_SMUDGE="1")
        if cut:
            env["FAKE_CUT"] = cut
        if race:
            env["FAKE_RACE"], env["FAKE_RACE_CMD"] = race
        return subprocess.run(["bash", str(self.repo / "scripts/angelo/gcp_windows_validate.sh"), "redo", *extra],
                              env=env, capture_output=True, text=True, timeout=120)

    def marker(self):
        path = self.bucket / "input/confirmed-commit"
        return path.read_text().strip() if path.exists() else ""

    def send(self):
        nonce = json.loads((self.fake / "metadata.json").read_text())["angelo-src-nonce"]
        return json.loads((self.bucket / ("input/send-%s/send.json" % nonce)).read_text())

    def tool(self, folder, *args):
        return subprocess.run([sys.executable, "-I", "-B", str(Path(folder, "source_send.py")), *map(str, args)],
                              env=ENV, capture_output=True, text=True)

    def boot(self):
        """The bootstrap's section 3 with windows_source_send.ps1's marker rules: receive a new send, else re-prove."""
        meta = json.loads((self.fake / "metadata.json").read_text())
        nonce, have = meta.get("angelo-src-nonce", ""), self.state / "src.nonce"
        marker = self.bucket / "input/confirmed-commit"
        if nonce and (not have.exists() or have.read_text().strip() != nonce):
            folder = self.root / ("machine/download/send-" + nonce)
            shutil.rmtree(folder, ignore_errors=True)
            shutil.copytree(self.bucket / ("input/send-" + nonce), folder)
            self.tools = folder
            done = self.tool(folder, "receive", "--folder", folder, "--tree", self.tree, "--state", self.state,
                             "--commit", meta["angelo-src-commit"])
            if done.returncode == 0:
                marker.write_text(meta["angelo-src-commit"])
                have.write_text(nonce)
            elif done.returncode == 3:
                marker.unlink(missing_ok=True)
            elif done.returncode == 4:
                self.publish()
            return done
        return self.publish()

    def publish(self):
        done = self.tool(self.tools, "confirmed", "--tree", self.tree, "--state", self.state)
        marker = self.bucket / "input/confirmed-commit"
        if done.returncode == 0:
            marker.write_text((self.state / "confirmed.commit").read_text().strip())
        else:
            marker.unlink(missing_ok=True)
        return done

    def source_clean(self, commit):
        """release_checks.py source-clean here, with the machine's Git LFS filter (as every tool run gets it)."""
        with mock.patch.dict(os.environ, ENV):
            return shipped_sources.source_clean(self.tree, commit, ignore_submodules=True)

    def assert_tree_is(self, commit):
        self.assertEqual(self.source_clean(commit), [])
        self.assertEqual((self.state / "confirmed.commit").read_text().strip(), commit)
        self.assertEqual(self.marker(), commit)

    def changed(self, base, head):
        listed = git(self.repo, "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", base, head)
        return sorted(listed.decode("utf-8").split())


@unittest.skipUnless(git_lfs_works(), "the launcher is bash with git and Git LFS, run from the workstation")
class ConfirmedCommitTests(LauncherFixture, unittest.TestCase):
    def test_one_send_is_one_commit_even_when_head_moves_during_it(self):
        # The 2026-10-20 11:18 send named bfcf73f4296 in send.json and the metadata but packed 4416202f87d: a commit
        # landed while the launcher read HEAD step by step. One reading now feeds every step, and a send whose
        # checkout moved before send.json went up is refused, with nothing naming it.
        done = self.redo()
        self.assertEqual(done.returncode, 0, done.stderr)
        meta = json.loads((self.fake / "metadata.json").read_text())
        self.assertEqual(self.send()["commit"], self.first)
        self.assertEqual(meta["angelo-src-commit"], self.first)
        self.assertEqual(self.boot().returncode, 0)
        self.assert_tree_is(self.first)
        for label, command in (("commit", "git commit -q --allow-empty -m race"),
                               ("edit", "printf 'changed under the send\\n' > edit.txt")):
            with self.subTest(label):
                (self.fake / "metadata.json").unlink(missing_ok=True)
                done = self.redo(race=("input/send-*/angelo-commit.pack", command))
                self.assertNotEqual(done.returncode, 0, "a send whose checkout changed under it must not be named")
                self.assertRegex(done.stderr, "HEAD moved from|A tracked file changed during the send")
                self.assertEqual(list(self.bucket.glob("input/send-*/send.json")), [], "no send.json for it")
                self.assertFalse((self.fake / "metadata.json").exists(), "the machine is told nothing")
                self.assertEqual(self.marker(), self.first, "the confirmed commit stands")
                git(self.repo, "checkout", "-q", "--", "edit.txt")

    def test_a_cut_off_send_keeps_the_confirmed_commit_and_the_next_delta_covers_everything_since_it(self):
        done = self.redo()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.send()["mode"], "full", "nothing confirmed yet: all of HEAD")
        self.assertEqual(self.marker(), "", "the launcher never writes the confirmed commit")
        self.assertEqual(self.boot().returncode, 0)
        self.assert_tree_is(self.first)
        second = self.commit({"edit.txt": "v2\n", "new/新しい.txt": "new\n", "assets/large.bin": "large v2\n"},
                             removed=["gone.txt"])
        for cut in ("input/send-*/angelo-commit.pack", "input/send-*/send.json"):
            with self.subTest(cut=cut):
                self.assertNotEqual(self.redo(cut=cut).returncode, 0)
                self.assertEqual(self.marker(), self.first, "a cut-off send changes nothing the machine proved")
                self.assertEqual((self.bucket / "input/pending-commit").read_text().strip(), second)
                self.assertEqual(self.boot().returncode, 0, "the machine still proves its own commit")
                self.assert_tree_is(self.first)
        # The incident: the same commit sent again after the cut-off one. Its delta is from what the machine has.
        done = self.redo()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.send()["mode"], self.send()["base"]), ("delta", self.first))
        self.assertEqual(self.boot().returncode, 0)
        self.assert_tree_is(second)
        # And a later commit, one of whose changes undoes an earlier one, after another cut-off send.
        third = self.commit({"edit.txt": "v1\n", "later.txt": "later\n"})
        self.assertNotEqual(self.redo(cut="input/send-*/send.json").returncode, 0)
        fourth = self.commit({"keep.txt": "kept, changed\n"})
        self.assertEqual(self.redo().returncode, 0)
        nonce = json.loads((self.fake / "metadata.json").read_text())["angelo-src-nonce"]
        sent = self.bucket / ("input/send-%s" % nonce)
        with tarfile.open(sent / "angelo-delta.tar.gz") as archive:
            delta = archive.getnames()
        self.assertEqual(self.send()["base"], second)
        self.assertEqual(sorted(delta + (sent / "angelo-deleted.txt").read_text(encoding="utf-8").split()),
                         self.changed(second, fourth), "every change since the confirmed commit, %s's too" % third[:7])
        self.assertEqual(self.boot().returncode, 0)
        self.assert_tree_is(fourth)

    def test_a_machine_stopped_mid_receive_converges_on_the_next_send(self):
        self.redo()
        self.boot()
        self.commit({"edit.txt": "v2\n", "half.txt": "half\n"}, removed=["gone.txt"])
        self.redo()
        # The machine stops after unpacking (no commit installed, nothing proven): only the archive's files changed.
        nonce = json.loads((self.fake / "metadata.json").read_text())["angelo-src-nonce"]
        import source_tree
        source_tree.extract(self.tree, [str(self.bucket / ("input/send-%s/angelo-delta.tar.gz" % nonce))])
        self.assertEqual((self.state / "confirmed.commit").read_text().strip(), self.first)
        # The next commit undoes one of those changes; its delta, from the confirmed commit, does not carry edit.txt.
        last = self.commit({"edit.txt": "v1\n"})
        self.assertEqual(self.redo().returncode, 0)
        self.assertEqual(self.send()["base"], self.first)
        done = self.boot()
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("repair: 1 tracked file(s) restored from the commit", done.stdout)
        self.assert_tree_is(last)
        self.assertEqual(Path(self.tree, "edit.txt").read_text(), "v1\n")

    def test_an_empty_delta_is_impossible_when_the_confirmed_commit_differs(self):
        self.redo()
        self.boot()
        self.commit({"edit.txt": "v2\n"})
        script = ('set -euo pipefail; ROOT="$1"; source "$2"; cd "$1"; '
                  'git() { if [[ "$1" == "-c" ]]; then return 0; fi; command git "$@"; }; delta_lists "$3" "$4" "$5"')
        work = self.root / "work"
        work.mkdir()
        done = subprocess.run(["bash", "-c", script, "x", str(self.repo), str(self.repo / "scripts/angelo/gcp_windows_source.sh"),
                               self.first, str(work), self.head()], env=ENV, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0, "lists that came back empty for differing trees are not sent")
        self.assertIn("is empty although its tree differs from HEAD's", done.stderr)

    def test_a_run_refuses_a_tree_that_is_not_the_commit_it_was_sent(self):
        self.redo()
        self.boot()
        verify = lambda: self.tool(self.tools, "verify", "--tree", self.tree, "--state", self.state)  # noqa: E731
        self.assertEqual(verify().returncode, 0)
        Path(self.tree, "edit.txt").write_text("edited on the machine\n")
        Path(self.tree, "stray.txt").write_text("left over\n")
        done = verify()
        self.assertEqual(done.returncode, 3)
        expected = self.source_clean(self.first)
        self.assertEqual([line for line in done.stdout.splitlines() if line.startswith("source: ")],
                         ["source: " + problem for problem in expected], "source-clean's own words")
        self.assertIn("git does not track", done.stdout)
        self.assertIn("differ from the commit", done.stdout)
        # Another commit's tree is refused for its HEAD too, and a tree the send did not make proves nothing (so
        # Get-SentCommit never returns a commit for it); only a machine no send made has nothing to check.
        (self.state / "sent.commit").write_text("0" * 40 + "\n")
        self.assertIn("not the commit sent", verify().stdout)
        other = self.root / "elsewhere"
        other.mkdir()
        done = self.tool(self.tools, "verify", "--tree", other, "--state", self.state)
        self.assertEqual(done.returncode, 3)
        self.assertIn("is not the tree a send made", done.stdout)
        self.assertEqual(self.tool(self.tools, "verify", "--tree", self.tree, "--state", self.root / "none").returncode, 0)

    def test_the_marker_is_written_only_after_confirmation(self):
        self.redo()
        self.assertEqual(self.boot().returncode, 0)
        self.commit({"edit.txt": "v2\n"})
        self.redo()
        # A tree that cannot be proven (a nested repository git clean keeps) is never confirmed, and the marker goes.
        nested = self.tree / "nested"
        nested.mkdir()
        git(nested, "init", "-q")
        (nested / "inside.txt").write_text("x\n")
        done = self.boot()
        self.assertEqual(done.returncode, 3, done.stdout)
        self.assertFalse((self.state / "confirmed.commit").exists())
        self.assertEqual(self.marker(), "", "the next send is all of HEAD")
        shutil.rmtree(nested)
        self.assertEqual(self.redo().returncode, 0)
        self.assertEqual(self.send()["mode"], "full")
        self.assertEqual(self.boot().returncode, 0)
        self.assert_tree_is(self.head())
        # A delta from another base changes nothing; a receive that fails half-way keeps the proven commit.
        proven = self.head()
        later = self.commit({"edit.txt": "v3\n"})
        self.assertEqual(self.redo().returncode, 0)
        send = self.bucket / ("input/send-%s" % json.loads((self.fake / "metadata.json").read_text())["angelo-src-nonce"])
        record = json.loads((send / "send.json").read_text())
        before = {path: path.stat().st_mtime_ns for path in self.tree.rglob("*") if ".git" not in path.parts}
        for change, code in (({"mode": "delta", "base": self.first}, 4), ({}, 1)):
            folder = self.root / ("variant-%d" % code)
            shutil.copytree(send, folder)
            (folder / "send.json").write_text(json.dumps(dict(record, **change)))
            if code == 1:
                (folder / "angelo-commit.pack").unlink()   # the machine stops (or fails) after unpacking
            done = self.tool(folder, "receive", "--folder", folder, "--tree", self.tree, "--state", self.state,
                             "--commit", later)
            self.assertEqual(done.returncode, code, done.stdout)
            self.assertEqual((self.state / "confirmed.commit").read_text().strip(), proven)
            if code == 4:
                self.assertEqual(before, {path: path.stat().st_mtime_ns for path in self.tree.rglob("*")
                                          if ".git" not in path.parts}, "a send that does not apply touches nothing")
        writes = [line for line in (self.fake / "log").read_text().splitlines()
                  if "confirmed-commit" in line and not line.startswith(("storage cat", "storage rm"))]
        self.assertEqual(writes, [], "the launcher reads the confirmed commit and never writes it")


class ContractTests(unittest.TestCase):
    def text(self, name):
        return (SCRIPTS / name).read_text(encoding="utf-8")

    def test_the_machine_publishes_only_what_it_proved(self):
        helper = self.text("windows_source_send.ps1")
        receive = helper[helper.index("function Receive-Send"):helper.index("function Assert-SentTree")]
        self.assertIn("0 { Set-ConfirmedMarker $Gcloud $Bucket $Commit; return }", receive)
        self.assertIn('3 { Set-ConfirmedMarker $Gcloud $Bucket ""; throw', receive)
        self.assertIn("4 { Publish-Confirmed $Tree $Gcloud $Bucket; throw", receive)
        self.assertNotIn("Set-ConfirmedMarker", receive[receive.index("default"):])
        publish = helper[helper.index("function Publish-Confirmed"):helper.index("function Receive-Send")]
        self.assertIn('if ($done.Code -eq 0) { (Get-Content (Join-Path $sendState "confirmed.commit")).Trim() } else { "" }',
                      publish)
        for name in ("gcp_windows_validate.sh", "gcp_windows_source.sh"):
            self.assertNotIn("uploaded-commit", self.text(name), "no launcher-written base remains")

    def test_every_stage_and_source_script_refuses_a_tree_that_is_not_the_commit_sent(self):
        boot = self.text("windows_cloud_bootstrap.ps1")
        stages = self.text("windows_bootstrap_stages.ps1")   # The sent tree's stages, dot-sourced after the proof.
        for stage, script in (('Write-Host "== stage: validate"', boot),
                              ('Write-Host "== stage: desktop session setup"', stages),
                              ('Write-Host "== stage: bench"', stages)):
            self.assertIn(stage + "\n" + " " * (script[:script.index(stage)].split("\n")[-1].count(" ")) +
                          "Assert-SentTree $src", script)
        gate = '. (Join-Path $SourceDir "scripts\\angelo\\windows_source_send.ps1"); Assert-SentTree $SourceDir'
        for name in ("windows_validate.ps1", "windows_agent_validate.ps1", "windows_thumbnailer_build.ps1"):
            self.assertIn(gate, self.text(name), name)
        # The bench reads its commit only through the proof (every mode), and the stage proves the tree again.
        bench = self.text("windows_final_runs.ps1")
        self.assertIn('. (Join-Path $SourceDir "scripts\\angelo\\windows_source_send.ps1")', bench)
        self.assertIn("$script:commit = Get-SentCommit $SourceDir", bench[bench.index("function Read-SentCommit"):])
        self.assertLess(bench.index('if (-not $Stage) { throw'), bench.index("Read-SentCommit   # proven here"))
        # And before the toolkit installer and the release inputs read the tree, not only at the stages.
        self.assertLess(boot.index("    Assert-SentTree $src\n"), boot.index("Invoke-StageStep Invoke-ToolkitStage"))


if __name__ == "__main__":
    unittest.main()
