# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: the read-only git cleanup signal for snapshot retention.

A fake ``git`` records every invocation, so the tests prove which commands run,
with which environment, and that any doubt means "no signal".
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import snapshot_git  # noqa: E402
from angelo_agent.snapshot_git import committed_project_times  # noqa: E402

READ_ONLY = {"rev-parse", "cat-file", "log"}
FAKE_GIT = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    config = json.load(open({config!r}))
    with open({log!r}, "a") as log:
        log.write(json.dumps({{"argv": sys.argv[1:], "env": {{key: os.environ.get(key) for key in
            ("GIT_DIR", "GIT_WORK_TREE", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT")}}}}) + "\\n")
    time.sleep(config.get("sleep", 0))
    args = sys.argv[1:]
    assert args[0] == "-C" and args[2:4] == ["-c", "core.fsmonitor=false"], args
    command = args[4:]
    def reply(value):
        sys.stdout.write(value)
        sys.exit(0)
    if command == ["rev-parse", "--show-toplevel"] and config.get("top"):
        reply(config["top"] + "\\n")
    if command == ["rev-parse", "--verify", "--quiet", "HEAD"] and config.get("head"):
        reply(config["head"] + "\\n")
    if command[:3] == ["rev-parse", "--verify", "--quiet"] and command[3] in config.get("blobs", {{}}):
        reply(config["blobs"][command[3]]["id"] + "\\n")
    if command == ["rev-parse", "--show-object-format"]:
        reply("sha1\\n")
    for blob in config.get("blobs", {{}}).values():
        if command == ["cat-file", "-s", blob["id"]]:
            reply(str(blob["size"]) + "\\n")
        if command == ["cat-file", "blob", blob["id"]]:
            reply(blob["content"])
    if command[:3] == ["log", "-1", "--format=%ct"] and "time" in config:
        reply(str(config["time"]) + "\\n")
    sys.exit(128)
''')


def git_blob_id(content):
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


class GitSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-git-signal-")
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "scenes").mkdir(parents=True)
        self.project = self.repo / "scenes" / "shot.blend"
        self.content = b"BLENDER17-01v0502" + os.urandom(40000)
        self.project.write_bytes(self.content)
        self.config, self.log = self.root / "fake.json", self.root / "calls.jsonl"
        self.git = self.root / "git"
        self.git.write_text(FAKE_GIT.format(python=sys.executable, config=str(self.config), log=str(self.log)))
        self.git.chmod(self.git.stat().st_mode | stat.S_IXUSR)
        if os.name == "nt":   # No shebang there: git.cmd starts the fake (its arguments are plain tokens).
            script, self.git = self.git.with_suffix(".py"), self.root / "git.cmd"
            (self.root / "git").rename(script)
            self.git.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), newline="")

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, *, blob=None, **values):
        blob = blob or {"id": git_blob_id(self.content), "size": len(self.content), "content": ""}
        config = {"top": str(self.repo), "head": "a" * 40, "blobs": {"HEAD:scenes/shot.blend": blob}, "time": 1700000000}
        config.update(values)
        self.config.write_text(json.dumps(config))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def signal(self, **kwargs):
        return committed_project_times([str(self.project)], git=str(self.git), **kwargs)

    def test_identical_committed_project_reports_its_commit_time_using_read_only_commands(self):
        self.configure()
        with patch.dict(os.environ, {"GIT_DIR": str(self.root / "elsewhere"), "GIT_WORK_TREE": "/"}):
            self.assertEqual(self.signal(), {str(self.project): 1700000000})
        calls = self.calls()
        self.assertTrue(calls)
        for call in calls:
            self.assertIn(call["argv"][4], READ_ONLY)
            self.assertEqual(call["env"], {"GIT_DIR": None, "GIT_WORK_TREE": None,
                                           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        self.assertFalse(any("hash-object" in call["argv"] for call in calls))

    def test_modified_untracked_or_outside_projects_give_no_signal(self):
        self.configure(blob={"id": git_blob_id(b"older committed version"), "size": len(self.content), "content": ""})
        self.assertEqual(self.signal(), {})
        self.configure(blobs={})
        self.assertEqual(self.signal(), {})
        self.configure(top=None)
        self.assertEqual(self.signal(), {})
        self.configure(head=None)
        self.assertEqual(self.signal(), {})
        self.assertEqual(committed_project_times(["relative.blend", "", str(self.root / "missing.blend")],
                                                 git=str(self.git)), {})

    def test_git_lfs_pointers_compare_sha256_and_size(self):
        oid = hashlib.sha256(self.content).hexdigest()
        pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize %d\n" % (oid, len(self.content))
        self.configure(blob={"id": "b" * 40, "size": len(pointer), "content": pointer})
        self.assertEqual(self.signal(), {str(self.project): 1700000000})
        wrong = pointer.replace("size %d" % len(self.content), "size 5")
        self.configure(blob={"id": "c" * 40, "size": len(wrong), "content": wrong})
        self.assertEqual(self.signal(), {})

    def test_missing_git_failures_and_timeouts_are_silent(self):
        self.configure()
        with patch.object(shutil, "which", return_value=None):
            self.assertEqual(committed_project_times([str(self.project)]), {})
        self.assertEqual(committed_project_times([str(self.project)], git=str(self.root / "no-such-git")), {})
        self.configure(sleep=3)
        self.assertEqual(self.signal(timeout=0.3), {})

    def test_results_are_cached_until_the_file_or_head_changes(self):
        self.configure()
        cache = {}
        self.assertEqual(self.signal(cache=cache), {str(self.project): 1700000000})
        first = len(self.calls())
        self.assertEqual(self.signal(cache=cache), {str(self.project): 1700000000})
        self.assertEqual(len(self.calls()) - first, 2, "only work-tree and HEAD lookups on a cache hit")
        self.project.write_bytes(self.content + b"edited")
        self.assertEqual(self.signal(cache=cache), {})

    @unittest.skipUnless(shutil.which("git"), "git executable not installed")
    def test_real_git_repository_round_trip(self):
        git = shutil.which("git")
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

        def run(*args):
            subprocess.run([git, "-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@invalid",
                            "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, env=environment)

        run("init", "-q")
        run("add", "scenes/shot.blend")
        run("commit", "-q", "-m", "fixture")
        times = committed_project_times([str(self.project)], git=git)
        self.assertEqual(list(times), [str(self.project)])
        self.project.write_bytes(self.content + b"unsaved change")
        self.assertEqual(committed_project_times([str(self.project)], git=git), {})


if __name__ == "__main__":
    unittest.main()
