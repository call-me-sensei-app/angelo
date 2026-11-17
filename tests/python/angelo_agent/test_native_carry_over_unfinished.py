# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A carry-over's record rewrites are finished or named, never silently skipped: audit findings on 08e2b0f7842 and the
break-it review of 6f32008ba58.

* A record it cannot read or rewrite now (a Windows sharing violation, an antivirus scan holding ``jobs.json``, a full
  disk) once left old paths in it for good while the move said ``done``. The move is ``done`` once its renames are
  (the earlier version gets its note and choices then), and the record names what is ``pending``: later launches try
  it again quietly, at most ``PENDING_TRIES`` times, then say once which files. A damaged record (not UTF-8, not
  JSON, nested past Python's limit; a damaged snapshot record) is left to its owner and never stops the others; one
  holding a lone surrogate (a Windows file name) is rewritten.

Dependency-free; the fixtures are ``test_native_version_carry_over``'s (the real stores write the earlier profile).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_native_version_carry_over as base
from test_native_version_carry_over import carry, carry_over_records  # noqa: F401
from angelo_agent import carry_over_finish as finish  # noqa: E402

Base = base.VersionCarryOverTests


def refusing(name, error, real):
    """``real`` (a reader or writer of records), refusing the record ``name`` with ``error``."""
    def call(path, *args, **kwargs):
        if Path(path).name == name:
            raise error
        return real(path, *args, **kwargs)
    return call


def sharing_violation():
    return PermissionError(errno.EACCES, "The process cannot access the file because another process is using it")


@unittest.skipUnless(base.anchored_files_supported(), "descriptor-relative storage required")
class UnfinishedRecordsTests(unittest.TestCase):
    setUp, carry, seed = Base.setUp, Base.carry, Base.seed

    def jobs(self):
        return json.loads((self.new / "jobs.json").read_text())["jobs"][0]["detail"]

    def refused_read(self):
        return patch.object(carry_over_records.shared_replace, "read_bytes", refusing(
            "jobs.json", sharing_violation(), carry_over_records.shared_replace.read_bytes))

    def test_a_record_that_cannot_be_read_now_is_named_and_rewritten_by_a_later_launch(self):
        self.seed()
        with self.refused_read():
            moved = self.carry()
        self.assertEqual(moved.status, "migrated", moved.message)
        record = carry.read_record(self.config)
        self.assertEqual((record["status"], record["pending"]), (carry.DONE, ["jobs.json"]))
        self.assertEqual(self.jobs()["staging"], str(self.old / "workspace/render-staging"), "not rewritten yet")
        workers = json.loads((self.new / "worker-chats.json").read_text())   # Parsed: JSON escapes a Windows path.
        self.assertEqual(workers["c1"], str(self.new / "parallel/lead/w1/part.blend"), "the others are rewritten")
        self.assertTrue((self.old / carry.NOTE_NAME).is_file(), "done: the earlier version has its note already")
        self.assertIsNone(carry.deferred_source)
        self.assertEqual(self.carry().status, "nothing", "tried again quietly")
        self.assertEqual(carry.read_record(self.config)["pending"], [])
        self.assertEqual(self.jobs()["staging"], str(self.new / "workspace/render-staging"))
        self.assertEqual(self.jobs()["recovery"]["root"], str(self.new / "workspace"))

    def test_a_record_that_cannot_be_rewritten_now_is_rewritten_by_a_later_launch(self):
        self.seed()
        with patch.object(carry_over_records, "write_json", refusing(
                "jobs.json", OSError(errno.ENOSPC, "No space left"), carry_over_records.write_json)):
            self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(carry.read_record(self.config)["pending"], ["jobs.json"])
        self.assertEqual(self.carry().status, "nothing")
        self.assertEqual(self.jobs()["staging"], str(self.new / "workspace/render-staging"))
        self.assertEqual(self.carry().status, "nothing", "idempotent once nothing is pending")

    def test_a_record_refused_for_good_is_tried_a_bounded_number_of_times_then_named_once(self):
        self.seed()
        with self.refused_read():
            results = [self.carry() for _ in range(finish.PENDING_TRIES + 3)]
        self.assertEqual(results[0].status, "migrated")
        statuses = [result.status for result in results[1:]]
        self.assertEqual(statuses.count(finish.RECORDS), 1, statuses)
        told = results[statuses.index(finish.RECORDS) + 1]
        self.assertIn("jobs.json", told.message)
        self.assertEqual(told.message.template, finish.RECORDS_LEFT, "a translatable template with its fields")
        self.assertTrue(all(status == "nothing" for status in statuses[statuses.index(finish.RECORDS) + 1:]))
        record = carry.read_record(self.config)
        self.assertEqual((record["status"], record["pending"], record["pending_left"]), (carry.DONE, [], ["jobs.json"]))
        for name in carry.LEFT_BEHIND:   # The earlier version kept its choices from the first launch on.
            if (self.new / name).exists():
                self.assertTrue((self.old / name).exists(), name)

    def test_a_damaged_record_is_left_to_its_owner_and_the_move_finishes(self):
        self.seed()
        self.old.joinpath("damaged.json").write_text('{"staging": "%s/workspace", ' % self.old)
        self.old.joinpath("not-utf8.json").write_bytes(b'{"root": "' + bytes(self.old) + b'\xff"}')
        deep = "[" * 5000 + json.dumps(str(self.old)) + "]" * 5000
        self.old.joinpath("deep.json").write_text(deep)
        before = {name: (self.old / name).read_bytes() for name in ("damaged.json", "not-utf8.json", "deep.json")}
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual({name: (self.new / name).read_bytes() for name in before}, before)
        self.assertEqual(carry.read_record(self.config)["pending"], [])

    def test_a_damaged_snapshot_record_never_stops_the_others(self):
        self.seed()
        damaged = sorted(self.old.glob("checkpoints/*/*/checkpoint.json"))[0]
        damaged.write_text("{ not json")
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(carry.read_record(self.config)["pending"], [])
        self.assertEqual(self.jobs()["staging"], str(self.new / "workspace/render-staging"))

    def test_a_lone_surrogate_from_a_windows_name_is_rewritten(self):
        self.seed()
        name = "a\ud800b.blend"   # An unpaired surrogate, as a Windows file name can give.
        (self.old / "names.json").write_text(json.dumps({"file": os.path.join(str(self.old), "workspace", name)}))
        self.assertEqual(self.carry().status, "migrated")
        value = json.loads((self.new / "names.json").read_text())
        self.assertEqual(value["file"], os.path.join(str(self.new), "workspace", name))

    def test_a_state_folder_that_could_not_be_listed_has_every_record_rewritten_later(self):
        self.seed()
        with patch.object(carry_over_records, "relocate_top_records", side_effect=PermissionError(errno.EACCES, "x")):
            self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(carry.read_record(self.config)["pending"], ["."])
        self.assertEqual(self.carry().status, "nothing")
        self.assertEqual(carry.read_record(self.config)["pending"], [])
        self.assertEqual(self.jobs()["staging"], str(self.new / "workspace/render-staging"), "every record, not a file '.'")

    def test_nothing_pending_is_tried_while_another_angelo_runs(self):
        self.seed()
        with self.refused_read():
            self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(self.carry(others=lambda: True).status, "nothing")
        record = carry.read_record(self.config)
        self.assertEqual((record["pending"], record["pending_tries"]), (["jobs.json"], 0), "no try is spent either")

    def test_a_later_try_never_takes_a_yes_the_earlier_version_wrote_since(self):
        self.seed()
        self.old.mkdir(parents=True, exist_ok=True)
        updates = {"accepted_line": "1.0", "channel": "stable"}
        self.new.mkdir(parents=True, exist_ok=True)
        (self.old / "updates.json").write_text(json.dumps(updates))   # Written by 1.0.x since the move.
        self.assertTrue(carry._leave_behind(self.old, self.new, [], again=True))
        self.assertEqual(json.loads((self.old / "updates.json").read_text()), updates)
        (self.new / "updates.json").write_text(json.dumps(updates))   # The same bytes: our own earlier copy.
        self.assertTrue(carry._leave_behind(self.old, self.new, [], again=True))
        self.assertNotIn("accepted_line", json.loads((self.old / "updates.json").read_text()), "ours loses the yes")

    def test_a_chat_list_that_cannot_be_read_now_is_pending_and_the_records_still_move(self):
        current = Path(self.temp.name) / "current"
        current.mkdir()
        (current / carry_over_records.CHAT_LIST).write_text(json.dumps({"sessions": []}))
        (current / "jobs.json").write_text(json.dumps({"root": str(Path(self.temp.name) / "old" / "x")}))
        reader = carry_over_records.shared_replace.read_bytes
        with patch.object(carry_over_records.shared_replace, "read_bytes",
                          refusing(carry_over_records.CHAT_LIST, sharing_violation(), reader)):
            count, pending = carry_over_records.update(current, Path(self.temp.name) / "old",
                                                       kept_root=Path(self.temp.name) / "kept")
        # Both the merge and the list's own rewrite wait: the list is a top-level record too.
        self.assertEqual(pending, [carry_over_records.CHAT_LISTS, carry_over_records.CHAT_LIST])
        self.assertEqual(json.loads((current / "jobs.json").read_text())["root"], str(current / "x"))
        self.assertEqual(carry_over_records.combine_chat_lists(Path(self.temp.name) / "none", current), 0)


if __name__ == "__main__":
    unittest.main()
