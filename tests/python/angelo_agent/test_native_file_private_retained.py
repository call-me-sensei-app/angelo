# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Break-it r2m4c round 5 on file-tool privacy: privacy that never depends on what happens to be loaded or listed now.

* M-1: a process folder a crashed child left is private in a fresh process that has imported no launcher module:
  ``private_roots`` declares every process-folder family itself.
* M-2: a native CLI profile folder a removed connection named stays private while it exists on disk.
Synthetic folders only.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent.files import FileWorkspace  # noqa: E402

COLD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from angelo_agent.files import FileWorkspace
loaded = sorted(name for name in ("angelo_agent.worker_process", "angelo_agent.headless_session") if name in sys.modules)
files = FileWorkspace(Path(sys.argv[2]), excluded=[])
outcome = {"loaded": loaded, "listed": [e["name"] for e in files.execute("angelo_list_files", {"path": "."})["entries"]]}
for relative in ("angelo-worker-crashed/worker.log", "angelo-headless-crashed/state/sessions/chat.json"):
    try:
        files.execute("angelo_read_file", {"path": relative, "start": 0, "length": 64})
        outcome[relative] = "read"
    except ValueError as error:
        outcome[relative] = str(error)
print(json.dumps(outcome))
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-private-retained-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    @staticmethod
    def synthetic(folder, name="synthetic.txt"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("SYNTHETIC")


class ColdStartTests(Fixture):
    def test_a_crashed_childs_folder_is_private_before_any_launcher_loads(self):
        """Review probe 1: a fresh process, the project in the temporary folder holding the leftovers."""
        self.synthetic(self.base / "angelo-worker-crashed", "worker.log")
        self.synthetic(self.base / "angelo-headless-crashed" / "state" / "sessions", "chat.json")
        self.synthetic(self.base / "public")
        environment = dict(os.environ, TMPDIR=str(self.base), TEMP=str(self.base), TMP=str(self.base))
        result = subprocess.run([sys.executable, "-I", "-c", COLD, str(ADDONS), str(self.base)], env=environment,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(outcome["loaded"], [], "no launcher module was imported")
        self.assertEqual(outcome["listed"], ["public"])
        for relative in ("angelo-worker-crashed/worker.log", "angelo-headless-crashed/state/sessions/chat.json"):
            with self.subTest(relative=relative):
                self.assertIn("Application profiles", outcome[relative])

    def test_the_launchers_take_their_prefixes_from_the_one_table(self):
        from angelo_agent import headless_session, private_roots, worker_process
        self.assertEqual(worker_process.PRIVATE_PREFIX, private_roots.WORKER_PREFIX)
        self.assertEqual(headless_session.PRIVATE_PREFIX, private_roots.HEADLESS_PREFIX)
        self.assertLessEqual({private_roots.WORKER_PREFIX, private_roots.HEADLESS_PREFIX},
                             {prefix for prefix, _ in private_roots.FAMILIES})


class RemovedConnectionTests(Fixture):
    def test_a_removed_connections_retained_profile_stays_private(self):
        """Review probe 2: an imported account's profile outside every default root; the connection is removed and
        the window starts again (a fresh store and registry read)."""
        from angelo_agent.connections import ConnectionStore
        from angelo_agent.private_roots import private_roots
        state, accounts = self.base / "state", self.base / "accounts"
        store = ConnectionStore(accounts)
        record = store.add("OPENAI", "SUBSCRIPTION", name="Synthetic")
        profile = self.base / "retained-accounts" / "codex-profile" / record["id"]
        self.synthetic(profile, "synthetic-auth.txt")
        store.records[record["id"]]["native_profile"] = str(profile)
        store.save()
        store.remove(record["id"])
        records = ConnectionStore(accounts).records.values()
        self.assertEqual(list(records), [])
        files = FileWorkspace(self.base, excluded=private_roots(state, accounts, records, environ={}))
        with self.assertRaisesRegex(ValueError, "Application profiles"):
            files.execute("angelo_read_file", {"path": "retained-accounts/codex-profile/%s/synthetic-auth.txt"
                                               % record["id"], "start": 0, "length": 64})
        with self.assertRaisesRegex(ValueError, "Application profiles"):   # A profile-family folder (round 7).
            files.execute("angelo_list_files", {"path": "retained-accounts/codex-profile"})
        self.assertEqual([entry["name"] for entry in files.execute(
            "angelo_list_files", {"path": "retained-accounts"})["entries"]], [])

    def test_a_deleted_profile_folder_is_forgotten(self):
        from angelo_agent import private_roots
        accounts = self.base / "accounts"
        accounts.mkdir()
        gone, kept = self.base / "gone" / "codex-profile" / "a", self.base / "kept" / "codex-profile" / "b"
        kept.mkdir(parents=True)
        gone.mkdir(parents=True)
        private_roots.remember_profiles(accounts, [{"native_profile": str(gone)}, {"native_profile": str(kept)}])
        gone.rmdir()
        private_roots.remember_profiles(accounts, [])
        self.assertEqual(private_roots.retained_profiles(accounts), [str(kept)])


if __name__ == "__main__":
    unittest.main()
