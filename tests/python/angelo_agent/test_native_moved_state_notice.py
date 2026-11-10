# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An earlier Angelo opened after a newer one moved its chats says so once (``moved_state_notice``).

The folders are laid out as Angelo 1.1's carry-over (angelo-studio #137) leaves them: the moved state in
``<product>/5.3/config/angelo-agent``, its record beside it, and a note in 1.0's emptied state folder. Nothing of
the newer version is ever written, moved or deleted.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import moved_state_notice as notice  # noqa: E402

ALONE = {}   # No ANGELO_STATE_DIR, BLENDER_USER_CONFIG or BLENDER_USER_RESOURCES.


class Layout:
    """``<product>/5.2`` (this version) and ``<product>/5.3`` (the newer one), as 1.1 leaves them."""

    def __init__(self, root, *, note=True, record=True, record_from=None, record_value=None):
        self.product = Path(root) / "Angelo"
        self.config = self.product / "5.2" / "config"
        self.state = self.config / "angelo-agent"
        self.newer = self.product / "5.3" / "config"
        (self.newer / "angelo-agent" / "sessions").mkdir(parents=True)
        (self.newer / "angelo-agent" / "sessions" / ("a" * 36 + ".json")).write_text('{"messages": [1]}')
        self.state.mkdir(parents=True)
        if note:
            (self.state / notice.NOTE_NAME).write_text("Angelo moved this folder's chats, snapshots and workspace to "
                                                      "%s.\n" % (self.newer / "angelo-agent"))
        if record:
            value = record_value if record_value is not None else {
                "version": 1, "status": "done", "from": str(record_from or self.state),
                "into": str(self.newer / "angelo-agent"), "chats": ["a" * 36]}
            (self.newer / notice.RECORD).write_text(json.dumps(value))

    def newer_files(self):
        """Every file of the newer version with its bytes and modification time."""
        return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in sorted(self.product.joinpath("5.3").rglob("*")) if path.is_file()}


class MovedToTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def test_the_note_and_the_record_each_say_the_chats_moved(self):
        for note, record, expected in ((True, True, "5.3"), (False, True, "5.3"), (True, False, "newer")):
            with self.subTest(note=note, record=record), tempfile.TemporaryDirectory() as root:
                layout = Layout(root, note=note, record=record)
                self.assertEqual(notice.moved_to(layout.config, ALONE), expected)

    def test_nothing_moved_tells_nothing(self):
        layout = Layout(self.root.name, note=False, record=False)
        self.assertIsNone(notice.moved_to(layout.config, ALONE))

    def test_a_record_of_another_folder_or_an_older_version_tells_nothing(self):
        layout = Layout(self.root.name, note=False, record_from="/elsewhere/5.1/config/angelo-agent")
        self.assertIsNone(notice.moved_to(layout.config, ALONE))
        older = self.root.name + "/older"
        layout = Layout(older, note=False, record=False)
        (layout.product / "5.1" / "config").mkdir(parents=True)
        (layout.product / "5.1" / "config" / notice.RECORD).write_text(json.dumps({"from": str(layout.state)}))
        self.assertIsNone(notice.moved_to(layout.config, ALONE), "only a newer version moves this one's chats")

    def test_any_record_version_or_status_that_names_this_folder_counts(self):
        # Whatever 1.1's final record looks like, ``from`` naming this folder means its chats left (a move that
        # stopped part-way moved some).
        for value in ({"version": 7, "status": "moving", "from": None}, {"from": None, "status": "whatever"}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as root:
                layout = Layout(root, note=False, record=False)
                value = dict(value, **{"from": str(layout.state)})
                (layout.newer / notice.RECORD).write_text(json.dumps(value))
                self.assertEqual(notice.moved_to(layout.config, ALONE), "5.3")

    def test_a_damaged_or_huge_record_is_ignored_and_the_note_still_counts(self):
        layout = Layout(self.root.name, note=True, record=False)
        (layout.newer / notice.RECORD).write_text("{not json")
        self.assertEqual(notice.moved_to(layout.config, ALONE), "newer")
        (layout.newer / notice.RECORD).write_text(" " * (notice.MAX_RECORD_BYTES + 1))
        self.assertEqual(notice.moved_to(layout.config, ALONE), "newer")

    def test_the_record_matches_through_a_link_in_the_path(self):
        real = Path(self.root.name) / "real"
        layout = Layout(real, note=False, record=False)
        link = Path(self.root.name) / "linked"
        os.symlink(real, link)
        (layout.newer / notice.RECORD).write_text(json.dumps({"from": str(link / "Angelo/5.2/config/angelo-agent")}))
        self.assertEqual(notice.moved_to(layout.config, ALONE), "5.3")

    def test_isolated_state_has_nothing_to_tell(self):
        layout = Layout(self.root.name)
        for variable in ("ANGELO_STATE_DIR", "BLENDER_USER_CONFIG", "BLENDER_USER_RESOURCES"):
            with self.subTest(variable=variable):
                self.assertIsNone(notice.moved_to(layout.config, {variable: self.root.name + "/x"}))
        self.assertIsNone(notice.moved_to(layout.product / "config", ALONE), "not a per-version folder")

    def test_told_once_and_again_after_the_newer_version_moves_again(self):
        layout = Layout(self.root.name)
        before = layout.newer_files()
        self.assertEqual(notice.moved_to(layout.config, ALONE), "5.3")
        notice.mark_told(layout.config)
        self.assertIsNone(notice.moved_to(layout.config, ALONE))
        self.assertEqual(sorted(path.name for path in layout.state.iterdir()),
                         sorted([notice.NOTE_NAME, notice.SHOWN_MARKER]), "only the marker is added, beside the note")
        self.assertEqual(layout.newer_files(), before, "the newer version's state and record stay byte for byte")
        (layout.state / notice.SHOWN_MARKER).unlink()   # What 1.1 does after it brings chats made here over again.
        self.assertEqual(notice.moved_to(layout.config, ALONE), "5.3")

    def test_the_markers_are_the_ones_the_newer_version_reads(self):
        # #137's version_carry_over: NOTE_NAME, NOTE_SEEN and state_paths.CARRY_OVER_RECORD. A newer Angelo ignores
        # both files when it decides whether this folder holds state (``holds_state``).
        self.assertEqual((notice.NOTE_NAME, notice.SHOWN_MARKER, notice.RECORD),
                         ("README-Angelo.txt", ".angelo-moved-notice-shown", "angelo-carry-over.json"))

    def test_an_unreadable_folder_tells_nothing_and_never_raises(self):
        layout = Layout(self.root.name, note=False)
        with mock.patch.object(notice.os, "scandir", side_effect=PermissionError("denied")):
            self.assertIsNone(notice.moved_to(layout.config, ALONE))


class CardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

        class NoticeCard:
            def __init__(self, icon, title, body, buttons=(), on_action=None, lifetime=0):
                self.icon, self.title, self.body, self.lifetime = icon, title, body, lifetime

            def opened(self, host):
                self.host = host
        stub = types.ModuleType("angelo_agent.update_card")
        stub.NoticeCard = NoticeCard
        patcher = mock.patch.dict(sys.modules, {"angelo_agent.update_card": stub})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_card_stays_until_closed_has_its_own_queue_place_and_marks_told_when_shown(self):
        layout = Layout(self.root.name)
        with mock.patch.dict(os.environ, {}, clear=False):
            for variable in ("ANGELO_STATE_DIR", "BLENDER_USER_CONFIG", "BLENDER_USER_RESOURCES"):
                os.environ.pop(variable, None)
            card = notice.card(layout.config)
        self.assertEqual((card.title, card.body, card.lifetime, card.kind), (notice.TITLE, notice.BODY, 0,
                                                                             "moved_chats"))
        self.assertTrue(card.retry_when_lost)
        self.assertTrue(50 < card.priority < 100, "after a crash dialog, before What's New")
        self.assertFalse((layout.state / notice.SHOWN_MARKER).exists(), "not told until it shows")
        card.opened(object())
        self.assertTrue((layout.state / notice.SHOWN_MARKER).exists())

    def test_the_text_says_how_to_get_the_chats_back(self):
        self.assertIn("install it again", notice.BODY)
        self.assertIn("nothing was deleted", notice.BODY)

    def test_the_notice_runs_with_the_startup_notices(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/app_notices.py").read_text(encoding="utf-8")
        self.assertIn("moved_state_notice.card(bpy.utils.user_resource('CONFIG'))", source)
        self.assertLess(source.index("_scan_done = True"), source.index("moved_state_notice.card("))
        self.assertLess(source.index("moved_state_notice.card("), source.index("release_notes.whats_new("))


if __name__ == "__main__":
    unittest.main()
