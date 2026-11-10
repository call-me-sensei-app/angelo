# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The riskiest callers of ``shared_replace`` with a reader holding their file, as Windows applies it.

Each store saves while its own reader (another window, another Angelo, the launcher) holds the file, or while
another program holds it for a moment or for good (``ntfs_sharing``: real handles on a Windows host, NTFS's rules
on the host's files elsewhere). On the old code (a bare ``os.replace``, a plain ``open``) each was refused with
``[WinError 5]``; where a save is still refused after the retries, the caller says so and keeps what it was saving.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import (connections, engine_link_assets as assets, engine_preflight, fal_video,  # noqa: E402
                          godot_link_assets, job_store, sessions, shared_replace, studio_files, usage_meter,
                          user_copies, worker_routing)
import ntfs_sharing  # noqa: E402
from test_native_usage_meter import receipt  # noqa: E402


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.sharing = ntfs_sharing.Sharing(self)   # After the folder: let go of before it is removed.

    def during_read(self, path, save):
        """``save()`` while a reader holds ``path`` (``while_open``); returns what it returned."""
        results = []
        self.sharing.while_open(path, lambda: results.append(save()))
        return results

    def assert_landed(self, results):
        self.assertEqual(self.sharing.action_errors, [], "the save was refused while the file was read")
        self.assertEqual(len(results), 1, "the reader never opened the file")
        return results[0]


class JobStoreTests(Case):
    def test_a_save_lands_while_another_window_loads_the_records(self):
        store = job_store.JobStore(self.folder)
        self.assertTrue(store.save())
        store.next_number = 7
        results = self.during_read(store.path, store.save)
        job_store.JobStore(self.folder)   # Another window's load: kept_state.read.
        self.assertIs(self.assert_landed(results), True)
        self.assertEqual(json.loads(store.path.read_text())["next"], 7)

    def test_a_save_refused_for_good_is_said_and_saved_again_once_free(self):
        store = job_store.JobStore(self.folder)
        store.save()
        held = self.sharing.hold(store.path)
        store.on_refused = refused = unittest.mock.Mock()   # job_ui: keeps the job timer for the retry.
        store.next_number = 9
        with patch("builtins.print") as said:
            self.assertFalse(store.save())
        self.assertTrue(said.called)
        self.assertEqual(refused.call_count, 1)
        tries = len(self.sharing.refusals)
        self.assertFalse(store.save(throttle=1.0, now=store.refused_at + 1.5))   # A render's progress save:
        self.assertEqual(len(self.sharing.refusals), tries, "never the retries' pauses at every tick")
        self.assertTrue(store.retry_refused(now=store.refused_at))   # Not yet: never at every tick.
        self.sharing.let_go(held)
        self.assertFalse(store.retry_refused(now=store.refused_at + shared_replace.RETRY_SECONDS))
        self.assertEqual(json.loads(store.path.read_text())["next"], 9)

    def test_a_save_that_must_land_is_tried_at_once_after_a_refusal(self):
        """A finished job's or a recovery's save is never held back by an earlier refusal's pause."""
        store = job_store.JobStore(self.folder)
        store.save()
        held = self.sharing.hold(store.path)
        with patch("builtins.print"):
            self.assertFalse(store.save())
        self.sharing.let_go(held)
        store.next_number = 11
        self.assertTrue(store.save(now=store.refused_at + 0.5))
        self.assertEqual((json.loads(store.path.read_text())["next"], store.refused_at), (11, None))


class UsageLedgerTests(Case):
    def ledger(self):
        ledger = usage_meter.UsageLedger(self.folder)
        patcher = patch.object(ledger, "_schedule", lambda: None)   # Written here only, never by its thread.
        patcher.start()
        self.addCleanup(patcher.stop)
        return ledger

    def test_the_totals_land_while_another_angelo_reads_them(self):
        ledger = self.ledger()
        ledger.add("chat-a", receipt())
        self.assertTrue(ledger.flush())
        ledger.add("chat-b", receipt())
        results = self.during_read(ledger.path, ledger.flush)
        usage_meter.UsageLedger(self.folder)   # Another Angelo on this state folder: its load reads the file.
        self.assertIs(self.assert_landed(results), True)
        self.assertEqual(sorted(json.loads(ledger.path.read_bytes())["chats"]), ["chat-a", "chat-b"])

    def test_a_moment_held_by_another_program_is_waited_out(self):
        ledger = self.ledger()
        ledger.add("chat-a", receipt())
        self.assertTrue(ledger.flush())
        self.sharing.hold(ledger.path, refusals=1)
        ledger.add("chat-b", receipt())
        self.assertTrue(ledger.flush())


class EngineLinkTests(Case):
    def asset(self):
        project = self.folder / "game"
        (project / "props").mkdir(parents=True)
        (project / "project.godot").write_text("config_version=5\n")
        path = project / "props" / "chair.glb"
        path.write_bytes(b"glb")
        return assets.Asset("godot", str(project), "res://props/chair.glb", str(path), ".glb", "props/chair.glb")

    def test_a_stamp_lands_while_a_request_card_reads_it(self):
        asset = self.asset()
        assets.ensure_working_folder(asset)
        assets.write_stamp(asset, assets.digest(asset.path))
        Path(asset.path).write_bytes(b"glb, saved again")
        results = self.during_read(assets.stamp_path(asset), lambda: assets.write_stamp(asset, assets.digest(asset.path)))
        assets.read_stamp(asset)   # The card's check (working_is_current) while the save stamps it.
        self.assert_landed(results)
        self.assertEqual(assets.read_stamp(asset), assets.digest(asset.path))

    def test_a_refused_plugin_install_leaves_the_older_plugin_in_place(self):
        project = self.folder / "game"
        project.mkdir()
        (project / "project.godot").write_text("config_version=5\n")
        old = project / "addons" / "angelo_link"
        old.mkdir(parents=True)
        (old / "stale.gd").write_text("old")
        copy_tree = user_copies.copy_tree

        def copied_then_held(source, target, **options):   # The engine's watcher opens the new copy's file.
            copy_tree(source, target, **options)
            self.sharing.hold(next(path for path in Path(target).rglob("*") if path.is_file()))
        with patch.object(user_copies, "copy_tree", copied_then_held), patch("builtins.print"):
            with self.assertRaises(PermissionError):
                assets.install_plugin(godot_link_assets, str(project))
        self.assertEqual((old / "stale.gd").read_text(), "old", "a refused install left the project without it")
        self.assertEqual([path.name for path in (project / "addons").iterdir()], ["angelo_link"],
                         "never a second plugin copy beside the real one")
        self.sharing.let_go_all()   # The engine lets go: the next install lands and removes what was left.
        target = Path(assets.install_plugin(godot_link_assets, str(project)))
        self.assertFalse((target / "stale.gd").exists())
        self.assertEqual(list((project / assets.WORKING_FOLDER).glob(assets.PLUGIN_STAGING + "*")), [])

    def test_a_preflight_report_that_cannot_be_written_is_still_answered(self):
        asset = self.asset()
        assets.ensure_working_folder(asset)
        path = engine_preflight.report_path(asset)
        engine_preflight.write_report(path, {"status": "done", "save_key": "first", "saved_at": 1.0})
        self.sharing.hold(path)
        with self.assertRaises(PermissionError):
            engine_preflight.write_report(path, {"status": "failed", "save_key": "second", "saved_at": 2.0})
        self.assertEqual(engine_preflight.link_report(asset)["save_key"], "second", "never pending for good")
        self.addCleanup(engine_preflight._unwritten.pop, path, None)


class ChatPendingTests(Case):
    """A chat's pending messages (steering) while another program holds their file: never lost at a switch or close."""

    def chat(self, records, version):
        return SimpleNamespace(store=SimpleNamespace(directory=self.folder), state={"id": "chat"},
                               inbox=SimpleNamespace(version=version, records=lambda: list(records)))

    def test_a_final_save_is_tried_within_the_pause_and_a_delete_waits_out_a_holder(self):
        from angelo_agent import chat_pending
        path = self.folder / "pending" / "chat.json"
        first = self.chat([{"text": "a"}], 1)
        chat_pending.save(first)
        held = self.sharing.hold(path)
        newer = self.chat([{"text": "a"}, {"text": "b"}], 2)
        newer._pending_saved = 1
        chat_pending.save(newer)   # Refused past its retries: the pump waits RETRY_SECONDS.
        self.assertIsNotNone(newer._pending_refused_at)
        self.sharing.let_go(held)
        del self.sharing.pauses[:]
        chat_pending.save(newer)   # The pump within the pause: not tried.
        self.assertEqual(json.loads(path.read_text())["pending"], [{"text": "a"}])
        chat_pending.save(newer, final=True)   # A chat switch or close: always tried, lands now.
        self.assertEqual(json.loads(path.read_text())["pending"], [{"text": "a"}, {"text": "b"}])
        self.assertIsNone(newer._pending_refused_at)
        held = self.sharing.hold(path, refusals=2)   # Nothing waits: the file goes, a moment's holder waited out.
        empty = self.chat([], 3)
        chat_pending.save(empty, final=True)
        self.assertFalse(path.exists(), "removed, never left to bring sent messages back after a relaunch")


class FalVideoTests(Case):
    def test_a_paid_result_held_for_good_pauses_its_job_and_never_fails_it(self):
        (self.folder / "video.mp4").write_bytes(b"from an earlier session")
        self.sharing.hold(self.folder / "video.mp4")
        with self.assertRaises(fal_video.MonitoringPaused):   # Resumed from its saved handle in a later session.
            fal_video._write(self.folder, "video.mp4", b"new")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["video.mp4"], "no part left")

    def test_a_moment_held_is_waited_out(self):
        (self.folder / "video.mp4").write_bytes(b"from an earlier session")
        self.sharing.hold(self.folder / "video.mp4", refusals=1)
        fal_video._write(self.folder, "video.mp4", b"new")
        self.assertEqual((self.folder / "video.mp4").read_bytes(), b"new")


    def test_a_permission_error_off_windows_still_fails_the_job(self):
        """Elsewhere a refusal is the folder's own (read-only, an ACL): the job fails, said, never paused for good."""
        with patch.object(shared_replace, "_WINDOWS", False), \
                patch.object(os, "replace", side_effect=PermissionError(13, "read-only folder")):
            with self.assertRaises(PermissionError):
                fal_video._write(self.folder, "video.mp4", b"new")


class ConnectionsTests(Case):
    def test_a_refused_save_leaves_the_window_showing_what_the_file_holds(self):
        store = connections.ConnectionStore(self.folder)
        store.dismiss_native("codex_subscription")
        self.sharing.hold(store.path)
        with self.assertRaises(PermissionError):
            store.dismiss_native("claude_subscription")
        self.assertEqual(store.ignored_native_modes, {"codex_subscription"}, "memory and the file disagree")
        self.assertEqual(connections.ConnectionStore(self.folder).ignored_native_modes, {"codex_subscription"})

    def test_a_save_lands_while_another_window_reloads(self):
        store = connections.ConnectionStore(self.folder)
        store.dismiss_native("codex_subscription")
        results = self.during_read(store.path, lambda: store.dismiss_native("claude_subscription"))
        connections.ConnectionStore(self.folder)   # Another window's reload (ui.pump: reload_if_changed).
        self.assert_landed(results)
        self.assertEqual(connections.ConnectionStore(self.folder).ignored_native_modes,
                         {"codex_subscription", "claude_subscription"})


class ChatListTests(Case):
    def test_a_refused_save_of_the_chat_list_never_raises_into_the_pump_and_is_written_later(self):
        collection = sessions.SessionCollection(self.folder)
        collection.save()
        held = self.sharing.hold(collection.path)
        with patch("builtins.print"):
            collection.save()   # Owed, never raised (the UI pump calls save_if_due every tick).
        self.assertTrue(collection._dirty)
        collection.save_if_due(interval=0)   # Not at the next tick: the refusal's wait is not over.
        self.assertTrue(collection._dirty)
        self.sharing.let_go(held)
        later = collection._refused_at + shared_replace.RETRY_SECONDS
        with patch.object(sessions.time, "monotonic", return_value=later):
            collection.save_if_due(interval=0)
        self.assertFalse(collection._dirty)


    def test_a_chat_list_owed_at_quit_lists_every_chat_on_disk_at_the_next_load(self):
        collection = sessions.SessionCollection(self.folder)
        collection.save()
        held = self.sharing.hold(collection.path)
        with patch("builtins.print"):
            made = collection.create()   # A new chat while the list is held: its save is refused, and owed.
        self.assertTrue(sessions.session_kept.owed(collection.path))   # Marked beside it, by a name not held.
        self.sharing.let_go(held)   # Quit before a retry: the next launch reads the older list.
        again = sessions.SessionCollection(self.folder)
        self.assertIn(made, again.records, "a chat made while the list was held is missing from the switcher")
        with patch.object(sessions.time, "monotonic", return_value=time.monotonic() + 60):
            again.save()
        self.assertFalse(sessions.session_kept.owed(collection.path))


class AlwaysAllowTests(Case):
    def test_turning_it_off_is_saved_again_until_the_file_says_off(self):
        from angelo_agent import always_allow
        always_allow.attach(self.folder)
        self.addCleanup(always_allow.detach)
        with patch.object(always_allow.managed_policy, "current",
                          return_value=SimpleNamespace(allow_auto_approval=True)):
            always_allow.set_enabled(True)
            held = self.sharing.hold(self.folder / always_allow.FILE)
            with self.assertRaises(PermissionError):
                always_allow.set_enabled(False)
            self.assertFalse(always_allow.enabled())   # Off at once, for this session.
            self.sharing.let_go(held)
            with patch.object(always_allow.time, "monotonic", return_value=time.monotonic() + 60):
                always_allow.sync()
        self.assertIs(json.loads((self.folder / always_allow.FILE).read_text())["enabled"], False,
                      "a refused off never comes back on at the next launch")

    def test_an_off_still_refused_at_quit_is_off_at_the_next_launch(self):
        from angelo_agent import always_allow
        self.addCleanup(always_allow.detach)
        with patch.object(always_allow.managed_policy, "current",
                          return_value=SimpleNamespace(allow_auto_approval=True)), patch("builtins.print"):
            always_allow.attach(self.folder)
            always_allow.set_enabled(True)
            self.sharing.hold(self.folder / always_allow.FILE)
            with self.assertRaises(PermissionError):
                always_allow.set_enabled(False)
            always_allow.detach()   # Quit while still held: its last try is refused too.
            self.sharing.let_go_all()
            always_allow.attach(self.folder)   # The next launch.
            self.assertFalse(always_allow.enabled(), "a revoked consent came back on")
            with patch.object(always_allow.time, "monotonic", return_value=time.monotonic() + 60):
                always_allow.sync()   # Saved now, and its marker goes.
        self.assertIs(json.loads((self.folder / always_allow.FILE).read_text())["enabled"], False)
        self.assertFalse((self.folder / (always_allow.FILE + always_allow.REVOKED)).exists())


class WorkerRoutingTests(Case):
    def test_a_refused_record_leaves_no_worker_chat_this_session_alone_knows(self):
        def controller(identity):
            return SimpleNamespace(store=SimpleNamespace(directory=str(self.folder)), state={"id": identity})
        worker_routing._remember(controller("chat-1"), self.folder / "part-1")
        self.sharing.hold(self.folder / worker_routing.REGISTRY)
        with self.assertRaises(PermissionError):
            worker_routing._remember(controller("chat-2"), self.folder / "part-2")
        self.assertEqual(sorted(worker_routing.registry(self.folder)), ["chat-1"])
        self.assertEqual(sorted(json.loads((self.folder / worker_routing.REGISTRY).read_text())), ["chat-1"])


class StudioCatalogueTests(Case):
    def test_the_catalogue_lands_while_the_asset_shelf_reads_it(self):
        path = self.folder / "blender_assets.cats.txt"
        path.write_text("VERSION 1\n")
        _text, identity = studio_files.shared_read(path)
        results = self.during_read(path, lambda: studio_files.write_if_unchanged(path, "VERSION 1\nnew\n", identity))
        studio_files.shared_read(path)   # The asset shelf's poll (media_shelf.studio_catalogs).
        self.assertIs(self.assert_landed(results), True)
        self.assertEqual(path.read_text(), "VERSION 1\nnew\n")


if __name__ == "__main__":
    unittest.main()
