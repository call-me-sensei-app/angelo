# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The carry-over when the earlier version stays in use, and the break-it review's cases (``version_carry_over``).

1.0.x users may stay on 1.0 or go back to it (updates_and_reports.rst, "Release lines"), so the earlier version's
folder stays alive after the move. Each test here failed on the code before the review's fixes.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_native_version_carry_over as base  # noqa: E402
from angelo_agent import (carry_over_moves, carry_over_records, folder_moves, state_paths,  # noqa: E402
                          version_carry_over as carry)
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.sessions import SessionCollection, SessionStore  # noqa: E402
from test_native_version_carry_over import ALONE, inventory  # noqa: E402


class ReturnCase(base.VersionCarryOverTests):
    """The base fixtures; only this module's tests run here (``load_tests``)."""

    def chat_in(self, folder, text="made in the earlier version", provider="claude_subscription"):
        identity = str(uuid.uuid4())
        store, chats = SessionStore(folder, identity), SessionCollection(folder)
        state = store.new(provider)
        state.update(native_id=str(uuid.uuid4()), updated=time.time(), messages=[{"role": "user", "text": text}])
        store.save(state)
        chats.update(state)
        chats.save()
        return identity


class BackToTheEarlierVersion(ReturnCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.assertEqual(self.carry().status, "migrated")

    def test_it_is_told_where_the_state_went_once(self):
        result = carry.carry_over(self.old_config, {}, others_running=ALONE)
        self.assertEqual(result.status, carry.MOVED_ON)
        self.assertIn(str(self.new), (self.old / carry.NOTE_NAME).read_text())
        carry.told_moved_on(self.old_config)
        self.assertEqual(carry.carry_over(self.old_config, {}, others_running=ALONE).status, "nothing")

    def test_its_choices_stay_for_it(self):
        self.new.joinpath("updates.json")   # Written before the move in a real install; the seed has none.
        carry._leave_behind(self.old, self.new, [])
        (self.new / "updates.json").write_text('{"automatic": false}')
        (self.old / "updates.json").unlink(missing_ok=True)
        carry._leave_behind(self.old, self.new, [])
        self.assertEqual(json.loads((self.old / "updates.json").read_text()), {"automatic": False})

    def test_a_chat_made_there_comes_over_at_the_next_launch(self):
        chat = self.chat_in(self.old)
        here = SessionCollection(self.new).records
        first = next(iter(here))
        result = self.carry()
        self.assertEqual(result.status, carry.RETURNED)
        listed = SessionCollection(self.new).records
        self.assertIn(chat, listed)
        self.assertIn(first, listed)
        self.assertEqual(SessionStore(self.new, chat).load()["native_id"], None, "it started in the other folder")
        self.assertEqual(self.carry().status, "nothing")

    def test_an_opened_but_unused_earlier_version_brings_nothing(self):
        store = SessionStore(self.old, str(uuid.uuid4()))
        store.save(store.new("claude_subscription"))
        before = inventory(self.new)
        self.assertEqual(self.carry().status, "nothing")
        self.assertEqual(inventory(self.new), before)

    def test_nothing_here_is_replaced_and_its_defaults_stay_behind(self):
        (self.new / "updates.json").write_text('{"automatic": false}')
        self.chat_in(self.old)
        (self.old / "updates.json").write_text('{"automatic": true}')
        (self.old / "intro.json").write_text('{"seen": false}')
        os.utime(self.old / "intro.json", (time.time() + 60, time.time() + 60))
        self.assertEqual(self.carry().status, carry.RETURNED)
        self.assertEqual(json.loads((self.new / "updates.json").read_text()), {"automatic": False})
        self.assertEqual(json.loads((self.new / "intro.json").read_text())["seen"], True)
        self.assertEqual(json.loads((self.old / "updates.json").read_text()), {"automatic": True})

    def test_another_angelo_open_waits_and_uses_this_folder(self):
        self.chat_in(self.old)
        self.assertEqual(self.carry(others=lambda: True).status, "nothing")
        self.assertIsNone(carry.deferred_source)

    def test_a_native_context_used_since_the_first_move_stays(self):
        state = SessionStore(self.new, self.session)
        chat = state.load()
        fresh = str(uuid.uuid4())
        chat.update(native_id=fresh, updated=time.time())
        state.save(chat)
        self.chat_in(self.old)
        self.assertEqual(self.carry().status, carry.RETURNED)
        self.assertEqual(SessionStore(self.new, self.session).load()["native_id"], fresh)


class ResumeTests(ReturnCase):
    def test_a_stopped_move_never_tears_the_snapshot_system(self):
        self.seed()
        self.new.mkdir(parents=True)
        (self.new / "updates.json").write_text("{}")   # Per-entry moves.
        real, calls = folder_moves.move_entry, []

        def failing(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)

        with patch.object(folder_moves, "move_entry", failing):
            self.assertEqual(self.carry().status, "failed")
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(carry.read_record(self.config)["status"], "done")
        kept = [path.name for path in self.new.glob(carry.KEPT_PREFIX + "*")]
        self.assertEqual(kept, [])
        self.assertEqual(CheckpointStore(self.new, self.session).inspect()["issues"], [])

    def test_the_winner_of_a_clash_is_settled_before_anything_moves(self):
        self.seed()
        self.new.mkdir(parents=True)
        (self.new / "checkpoint-timeline.json").write_text('{"small": "another store"}')
        real, calls = folder_moves.move_entry, []

        def failing(source, target):
            calls.append(source)
            if len(calls) == 3:
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)

        with patch.object(folder_moves, "move_entry", failing):
            self.assertEqual(self.carry().status, "failed")
        self.assertEqual(carry.read_record(self.config)["unit"], "move", "the other store went aside first")
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(CheckpointStore(self.new, self.session).inspect()["issues"], [])

    def test_a_failed_launch_with_nothing_moved_stays_on_the_earlier_folder(self):
        self.seed()
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.EACCES, "Denied")), \
                patch.object(carry, "_others_running", ALONE), patch.object(state_paths, "session_redirect", None):
            result = carry.carry_over_on_startup(self.config)
            self.assertEqual(result.status, "failed")
            self.assertEqual(state_paths.session_redirect, self.old)

    def test_a_default_written_by_a_failed_launch_never_beats_the_person(self):
        self.seed()
        (self.old / "updates.json").write_text('{"automatic": false}')
        os.utime(self.old / "updates.json", (time.time() - 3600, time.time() - 3600))
        self.new.mkdir(parents=True)
        (self.new / "a-file.json").write_text("{}")
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.ENOSPC, "No space")):
            self.assertEqual(self.carry().status, "failed")
        (self.new / "updates.json").write_text('{"automatic": true}')
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(json.loads((self.new / "updates.json").read_text()), {"automatic": False})

    def test_one_file_under_two_names_ends_as_one(self):
        self.seed()
        self.new.mkdir(parents=True)
        (self.new / "other.json").write_text("{}")
        os.link(self.old / "jobs.json", self.new / "jobs.json")   # A crash between link and unlink.
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual((self.new / "jobs.json").stat().st_nlink, 1)
        self.assertFalse((self.old / "jobs.json").exists())


class SecondReviewTests(ReturnCase):
    def test_a_state_folder_linked_to_the_earlier_one_is_never_touched(self):
        self.seed()
        before = inventory(self.old)
        self.config.mkdir(parents=True)
        self.new.symlink_to(self.old)
        self.assertEqual(self.carry().status, "nothing")
        self.assertEqual(inventory(self.old), before)

    def test_both_linked_to_one_folder_is_never_touched(self):
        shared = self.product.parent / "shared"
        shared.mkdir(parents=True)
        (shared / "sessions").mkdir()
        (shared / "sessions" / ("a" * 36 + ".json")).write_text('{"messages": [1]}')
        self.old_config.mkdir(parents=True)
        self.config.mkdir(parents=True)
        self.old.symlink_to(shared)
        self.new.symlink_to(shared)
        self.assertEqual(self.carry().status, "nothing")
        self.assertTrue((shared / "sessions" / ("a" * 36 + ".json")).is_file())

    def test_bytes_written_during_a_copy_to_another_disk_stay(self):
        self.seed()
        real = carry_over_moves._compare
        target = self.old / "sessions" / (self.session + ".json")
        added = []

        def appending(first, second):
            result = real(first, second)
            if not added:
                added.append(True)
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(" ")
            return result

        self.new.mkdir(parents=True)
        mover = carry_over_moves.Mover(self.new / "kept")
        with patch.object(carry_over_moves, "_compare", appending), self.assertRaises(OSError):
            mover.copy_across(self.old / "sessions", self.new / "sessions")
        self.assertTrue(target.is_file())

    def test_chats_made_on_an_older_line_come_to_a_later_one(self):
        self.seed()
        self.assertEqual(self.carry().status, "migrated")
        chat = self.chat_in(self.old)
        newest = self.product / "5.4/config"
        self.assertEqual(carry.carry_over(newest, {}, others_running=ALONE).status, "migrated")
        self.assertEqual(carry.carry_over(newest, {}, others_running=ALONE).status, carry.RETURNED)
        self.assertIn(chat, SessionCollection(newest / state_paths.STATE_FOLDER).records)

    def test_a_second_stints_chats_stay_listed_after_a_stop(self):
        self.seed()
        self.assertEqual(self.carry().status, "migrated")
        first = self.chat_in(self.old)
        self.assertEqual(self.carry().status, carry.RETURNED)
        second = self.chat_in(self.old)
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.ENOSPC, "No space")):
            self.assertEqual(self.carry().status, "failed")   # A stop in the renames (records alone never stop one).
        self.assertEqual(self.carry().status, carry.RETURNED)
        listed = SessionCollection(self.new).records
        self.assertIn(first, listed)
        self.assertIn(second, listed)

    def test_a_stopped_return_waits_for_the_earlier_version_to_close(self):
        self.seed()
        self.assertEqual(self.carry().status, "migrated")
        chat = self.chat_in(self.old)
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.ENOSPC, "No space")):
            self.assertEqual(self.carry().status, "failed")
        self.assertEqual(self.carry(others=lambda: True).status, "nothing")
        self.assertTrue((self.old / "sessions" / (chat + ".json")).is_file())

    def test_every_launch_that_moved_nothing_stays_on_the_earlier_folder(self):
        self.seed()
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.EACCES, "Denied")):
            for _attempt in range(2):
                carry.deferred_source = None
                self.assertEqual(self.carry().status, "failed")
                self.assertEqual(carry.deferred_source, self.old)

    def test_a_dead_link_older_than_the_source_or_newer_than_this_version_blocks_nothing(self):
        self.seed()
        (self.product / "4.5").symlink_to(self.product.parent / "gone")
        (self.product / "9.9").symlink_to(self.product.parent / "gone")
        self.assertEqual(self.carry().status, "migrated")

    def test_a_store_started_by_a_stopped_session_never_mixes_with_the_moved_one(self):
        self.seed()
        self.new.mkdir(parents=True)
        (self.new / "updates.json").write_text("{}")
        real, calls = folder_moves.move_entry, []

        def failing(source, target):
            calls.append(Path(source).name)
            if Path(source).name == "snapshot-store" and "snapshot-store" not in calls[:-1]:
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)

        with patch.object(folder_moves, "move_entry", failing):
            self.assertEqual(self.carry().status, "failed")
        (self.new / "snapshot-store").mkdir()
        (self.new / "snapshot-store" / "index.sqlite").write_text("a store of its own")
        self.assertEqual(self.carry().status, "migrated")
        kept = list(self.new.glob(carry.KEPT_PREFIX + "*/snapshot-store/index.sqlite"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), "a store of its own")
        self.assertEqual(CheckpointStore(self.new, self.session).inspect()["issues"], [])

    def test_the_earlier_version_running_during_a_stop_never_costs_the_moved_history(self):
        record, data, _attached = self.seed()
        self.new.mkdir(parents=True)
        (self.new / "updates.json").write_text("{}")
        real = folder_moves.move_entry

        def failing(source, target):
            if Path(source).name == "snapshot-store":
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)

        with patch.object(folder_moves, "move_entry", failing):
            self.assertEqual(self.carry().status, "failed")
        (self.old / "checkpoints").mkdir()   # The earlier version opened again and started its records afresh.
        (self.old / "checkpoints" / "stint.json").write_text("{}")
        time.sleep(0.01)
        self.assertEqual(self.carry().status, "migrated")
        store = CheckpointStore(self.new, self.session)
        self.assertEqual(store.inspect()["issues"], [])
        _record, copy = store.recovery_copy(record["id"])
        self.assertEqual(copy.read_bytes(), data)
        self.assertEqual(len(list(self.new.glob(carry.KEPT_PREFIX + "*/checkpoints/stint.json"))), 1)

    def test_both_running_during_a_stop_leaves_one_consistent_history(self):
        record, data, _attached = self.seed()
        self.new.mkdir(parents=True)
        (self.new / "updates.json").write_text("{}")
        real = folder_moves.move_entry

        def failing(source, target):
            if Path(source).name == "snapshot-store":
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)

        with patch.object(folder_moves, "move_entry", failing):
            self.assertEqual(self.carry().status, "failed")
        (self.new / "snapshot-store").mkdir()   # The stopped session started its own store and rewrote the timeline.
        (self.new / "snapshot-store" / "index.sqlite").write_text("a store of its own")
        os.utime(self.new / "checkpoint-timeline.json")
        (self.old / "checkpoints").mkdir()   # The earlier version opened again.
        (self.old / "checkpoints" / "stint.json").write_text("{}")
        (self.old / "checkpoint-timeline.json").write_text("{}")
        self.assertEqual(self.carry().status, "migrated")
        store = CheckpointStore(self.new, self.session)
        self.assertEqual(store.inspect()["issues"], [])
        _record, copy = store.recovery_copy(record["id"])
        self.assertEqual(copy.read_bytes(), data)

    def test_a_yes_to_the_newer_version_stays_with_it(self):
        self.seed()
        (self.old / "updates.json").write_text('{"accepted_line": "1.1", "automatic": false}')
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(json.loads((self.old / "updates.json").read_text()), {"automatic": False})

    def test_the_note_never_travels_and_is_told_after_every_move(self):
        self.seed()
        self.assertEqual(self.carry().status, "migrated")
        carry.told_moved_on(self.old_config)
        self.chat_in(self.old)
        self.assertEqual(self.carry().status, carry.RETURNED)
        self.assertEqual(carry.carry_over(self.old_config, {}, others_running=ALONE).status, carry.MOVED_ON)
        self.assertFalse((self.new / carry.NOTE_NAME).exists())

    def test_a_return_keeps_the_first_moves_time(self):
        self.seed()
        self.assertEqual(self.carry().status, "migrated")
        first = carry.read_record(self.config)["at"]
        self.chat_in(self.old)
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.ENOSPC, "No space")):
            self.assertEqual(self.carry().status, "failed")
        self.assertEqual(self.carry().status, carry.RETURNED)
        self.assertEqual(carry.read_record(self.config)["at"], first)


class ReRegisterTests(ReturnCase):
    def test_a_second_registration_never_moves_under_a_live_session(self):
        self.seed()
        with patch.object(state_paths, "session_redirect", None), patch.object(carry, "last_result", None):
            with patch.object(carry, "_others_running", return_value=True):
                self.assertEqual(carry.carry_over_on_startup(self.config).status, carry.DEFERRED)
            with patch.object(carry, "_others_running", ALONE):
                self.assertEqual(carry.carry_over_on_startup(self.config).status, carry.DEFERRED)
            self.assertEqual(state_paths.session_redirect, self.old)
        self.assertTrue((self.old / "sessions").is_dir(), "the next launch moves it")


class DisksAndLinks(ReturnCase):
    def test_a_version_folder_on_a_disk_not_attached_is_never_taken_for_empty(self):
        self.product.mkdir(parents=True)
        (self.product / "5.2").symlink_to(self.product.parent / "missing-disk")
        self.assertEqual(self.carry().status, "failed")
        self.assertIsNone(carry.read_record(self.config))

    def test_a_dangling_state_link_is_never_taken_for_empty(self):
        self.old_config.mkdir(parents=True)
        self.old.symlink_to(self.product.parent / "missing-disk")
        self.assertEqual(self.carry().status, "failed")
        self.assertIsNone(carry.read_record(self.config))

    def test_a_non_ascii_user_name_is_followed_in_the_top_records(self):
        folder = self.product.parent / "홍길동" / "Angelo"
        old, new = folder / "5.2/config/angelo-agent", folder / "5.3/config/angelo-agent"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        (new / "jobs.json").write_text(json.dumps({"path": str(old / "workspace/x")}, ensure_ascii=False),
                                       encoding="utf-8")
        from angelo_agent import carry_over_records
        self.assertEqual(carry_over_records.relocate_top_records(new, [str(old)]), 1)
        self.assertEqual(json.loads((new / "jobs.json").read_text(encoding="utf-8"))["path"], str(new / "workspace/x"))

    def test_a_linked_user_folder_is_followed_in_both_spellings(self):
        self.product.mkdir(parents=True)
        link = self.product.parent / "linked-product"
        link.symlink_to(self.product)
        self.seed()
        (self.old / "jobs.json").write_text(json.dumps({"path": str(link / "5.2/config/angelo-agent/workspace/x")}))
        self.assertEqual(carry.carry_over(link / "5.3/config", {}, others_running=ALONE).status, "migrated")
        self.assertEqual(json.loads((self.new / "jobs.json").read_text())["path"], str(self.new / "workspace/x"))

    def test_a_linked_state_folder_keeps_its_sign_in_profiles(self):
        elsewhere = self.product.parent / "elsewhere"
        (elsewhere / "claude-profile").mkdir(parents=True)
        (elsewhere / "conversations.json").write_text('{"sessions": []}')
        self.old_config.mkdir(parents=True)
        self.old.symlink_to(elsewhere)
        self.new.mkdir(parents=True)
        (self.new / "a.json").write_text("{}")
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue((elsewhere / "claude-profile").is_dir())

    def test_a_file_added_during_a_copy_to_another_disk_stays(self):
        self.seed()
        real = carry_over_moves._compare
        added = []

        def adding(first, second):
            if not added:
                (self.old / "sessions" / "late.json").write_text("{}")
                added.append(True)
            return real(first, second)

        mover = carry_over_moves.Mover(self.new / "kept")
        self.new.mkdir(parents=True)
        with patch.object(carry_over_moves, "_compare", adding), self.assertRaises(OSError):
            mover.copy_across(self.old / "sessions", self.new / "sessions")
        self.assertTrue((self.old / "sessions" / "late.json").is_file())

    def test_a_listing_left_by_cycles_is_no_state(self):
        self.seed()
        self.new.mkdir(parents=True)
        (self.new / carry.RENDER_DEVICE).write_text("{}")
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(carry.read_record(self.config)["moved"], 1, "one rename of the whole folder")


def load_tests(loader, tests, pattern):
    import unittest
    found = unittest.TestSuite()
    for case in (BackToTheEarlierVersion, ResumeTests, SecondReviewTests, ReRegisterTests, DisksAndLinks):
        names = [name for name in loader.getTestCaseNames(case) if name not in dir(base.VersionCarryOverTests)]
        found.addTests(case(name) for name in names)
    return found


if __name__ == "__main__":
    import unittest
    unittest.main()
