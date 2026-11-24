# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``update_carry_over_gate.seed_connections`` seeds the accounts as an earlier Angelo stored them: the record names
the isolated native profile in the source account folder (as ``recover_connections.py`` writes it), and the account
store's retained-profile list names that profile too, as the store's own save writes it (``ConnectionStore.save``,
since 1.0.6). Without the list the candidate rightly refuses the profile as outside Angelo's folders
(``storage.ProfileOutside``), which is how 2.0.0's Windows state receipt failed on a seed no Angelo writes."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_carry_over_gate as gate  # noqa: E402
from angelo_agent import connections, private_roots, storage  # noqa: E402


PROVIDER = {"claude_subscription": {"provider": "ANTHROPIC", "method": "SUBSCRIPTION"},
            "codex_subscription": {"provider": "OPENAI", "method": "SUBSCRIPTION"}}


def account_folder(root, modes):
    """A signed-in account folder as a person's earlier install keeps it: records plus each one's profile folder."""
    root.mkdir(parents=True)
    records = []
    for mode in modes:
        identity = str(uuid.uuid4())
        family = "claude-profile" if mode.startswith("claude_") else "codex-profile"
        (root / family / identity).mkdir(parents=True)
        records.append(dict(PROVIDER[mode], id=identity, name=mode, mode=mode, reuse_native=False, group="CODING",
                            native_profile=None, model=None))
    records.append(dict(PROVIDER["codex_subscription"], id=str(uuid.uuid4()), name="reused", mode="codex_subscription",
                        reuse_native=True, group="CODING", native_profile=None, model=None))
    (root / "connections.json").write_text(json.dumps({"version": 3, "active": records[0]["id"],
                                                       "connections": records, "ignored_native_modes": []}))
    return records


class SeedAccounts(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="seed-accounts-"))
        self.source = self.temp / "bench" / "connections"
        self.records = account_folder(self.source, ["claude_subscription", "codex_subscription"])
        self.env = gate.environment("sandbox", self.temp / "home", self.temp / "fake-bin")

    def profiles(self):
        return [str(self.source.absolute() / ("claude-profile" if r["mode"].startswith("claude_") else "codex-profile")
                    / r["id"]) for r in self.records if not r["reuse_native"]]

    def test_records_name_the_source_profiles_and_the_store_retains_them(self):
        folder = gate.seed_connections(self.source, self.env)
        self.assertEqual(folder, storage.connection_directory(self.env, sys.platform, self.env.get("HOME")))
        seeded = json.loads((folder / "connections.json").read_text())["connections"]
        self.assertEqual([r.get("native_profile") for r in seeded if not r["reuse_native"]], self.profiles())
        self.assertTrue(all(r.get("native_profile") is None for r in seeded if r["reuse_native"]))
        self.assertEqual(sorted(private_roots.retained_profiles(folder)), sorted(self.profiles()))

    def test_private_without_the_records_as_the_app_installs_its_roots(self):
        # The app names its private folders to C before any record is consulted: the retained list alone must carry
        # every seeded profile into the private set.
        folder = gate.seed_connections(self.source, self.env)
        state = self.temp / "home" / "state"
        roots = [str(p) for p in private_roots._compute(state, folder, [], self.env)]
        for profile in self.profiles():
            self.assertIn(profile, roots)

    def test_same_as_the_shipped_recovery_writes(self):
        folder = gate.seed_connections(self.source, self.env)
        recovered = self.temp / "recovered"
        recovered.mkdir()
        connections.ConnectionStore(recovered).import_metadata(self.source)
        self.assertEqual(sorted(private_roots.retained_profiles(folder)),
                         sorted(private_roots.retained_profiles(recovered)))
        mine = {r["id"]: r.get("native_profile") for r in json.loads((folder / "connections.json").read_text())["connections"]}
        theirs = {r["id"]: r.get("native_profile")
                  for r in json.loads((recovered / "connections.json").read_text())["connections"]}
        self.assertEqual(mine, theirs)

    def test_the_source_folder_is_never_changed(self):
        before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        names = sorted(str(p) for p in self.source.rglob("*"))
        gate.seed_connections(self.source, self.env)
        self.assertEqual(sorted(str(p) for p in self.source.rglob("*")), names)
        self.assertEqual({p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}, before)


if __name__ == "__main__":
    unittest.main()
