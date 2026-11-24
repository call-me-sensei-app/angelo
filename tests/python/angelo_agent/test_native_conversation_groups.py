# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Conversations list (2.1.0): search finds what Angelo replied too, and the unsearched list is grouped by day.

``conversation_search.replies`` (bounded like requests) and ``conversations_layout`` headings (local calendar days;
never a keyboard stop). No Blender.
"""
import suite_isolation  # noqa: F401
from host_waits import CAP_S  # noqa: E402  Only a hang reaches it.

import datetime
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import change_clock, conversation_search as search, conversations_layout as layout  # noqa: E402


def at(days_ago, hour=12):
    day = datetime.date.today() - datetime.timedelta(days=days_ago)
    return time.mktime(datetime.datetime(day.year, day.month, day.day, hour).timetuple())


class ReplyTests(unittest.TestCase):
    def test_replies_are_indexed_newest_first_within_the_limit(self):
        document = {"messages": [{"role": "assistant", "text": "Retopology   keeps\nquads."},
                                 {"role": "system", "text": "A notice"},
                                 {"role": "assistant", "text": "  "},
                                 {"role": "assistant", "text": "y" * 5000}]}
        self.assertEqual(search.replies(document), ["Retopology keeps quads.", "y" * search.LINE])
        many = {"messages": [{"role": "assistant", "text": "r%d " % i + "x" * 200} for i in range(40)]}
        kept = search.replies(many)
        self.assertLessEqual(sum(map(len, kept)), search.LIMIT)
        self.assertTrue(kept[-1].startswith("r39 "), "The newest replies are the ones kept")
        self.assertEqual(search.replies({"messages": "bad"}), [])

    def test_a_search_finds_a_chat_by_its_reply(self):
        with tempfile.TemporaryDirectory() as root:
            sessions = Path(root, "sessions")
            sessions.mkdir()
            chat = {"version": 1, "id": "chat-1", "messages": [
                {"role": "user", "text": "How do I clean this mesh?"},
                {"role": "assistant", "text": "Use Merge by Distance, then recalculate normals."}]}
            (sessions / "chat-1.json").write_text(json.dumps(chat), encoding="utf-8")
            index = search.SearchIndex(Path(root))
            records = [{"id": "chat-1", "title": "Mesh"}]
            # The file was written just now: a coarse file-system clock (not APFS's) vouches for its stat only once
            # the clock has passed its change time (change_clock; test_native_conversation_search_clock), and an
            # utime sets the change time to now. The clock stands ten seconds on, as a later poll would see it.
            later = change_clock.system_time() + 10 * 10_000_000
            with patch.object(change_clock, "system_time", return_value=later):
                self.assertTrue(index.refresh(["chat-1"], budget=CAP_S), "One step reads the one chat")
            found = index.search(records, "recalculate normals", lambda record: record["title"])
            self.assertEqual([(record["id"], snippet) for record, snippet in found],
                             [("chat-1", "Use Merge by Distance, then recalculate normals.")])

    def test_a_reply_is_indexed_where_a_paths_stat_reports_the_creation_time(self):
        """VM CI on 70142a371dd (win-3): Windows' CPython reports a path's ``st_ctime`` as the creation time and a
        descriptor's as the change time (angelo_change_clock), so the read's descriptor never matched the path's
        stat there and no reply was ever indexed. The read binds on what both report: device, file, size and
        modification time where a path's change time is the creation time; all five elsewhere."""
        from angelo_agent import session_journal
        real_read = session_journal.read

        def windows_read(path, *args, **kwargs):   # The descriptor's change time differs from the path's ctime.
            document, opened = real_read(path, *args, **kwargs)
            if opened.identity is not None:
                opened.identity = opened.identity[:4] + (opened.identity[4] + 1234,) + opened.identity[5:]
            return document, opened
        for creation, indexed in ((True, True), (False, False)):
            with self.subTest(path_ctime_is_creation=creation), tempfile.TemporaryDirectory() as root:
                sessions = Path(root, "sessions")
                sessions.mkdir()
                chat = {"version": 1, "id": "chat-1", "messages": [
                    {"role": "assistant", "text": "Use Merge by Distance, then recalculate normals."}]}
                (sessions / "chat-1.json").write_text(json.dumps(chat), encoding="utf-8")
                index = search.SearchIndex(Path(root))
                later = change_clock.system_time() + 10 * 10_000_000
                with patch.object(change_clock, "system_time", return_value=later), \
                        patch.object(session_journal, "read", windows_read), \
                        patch.object(search, "PATH_CTIME_IS_CREATION", creation):
                    index.refresh(["chat-1"], budget=CAP_S)
                found = index.search([{"id": "chat-1", "title": "Mesh"}], "recalculate normals", lambda r: r["title"])
                self.assertEqual(bool(found), indexed, "a ctime mismatch binds only where both report the change time")

    def test_an_older_index_is_read_again(self):
        self.assertGreaterEqual(search.VERSION, 3)


class GroupTests(unittest.TestCase):
    def test_days_map_to_headings(self):
        now = at(0, 18)
        self.assertEqual([layout.group(at(days), now) for days in (0, 1, 2, 7, 8, 30, 31, 400)],
                         ["Today", "Yesterday", "Previous 7 days", "Previous 7 days", "Previous 30 days",
                          "Previous 30 days", "Earlier", "Earlier"])
        self.assertEqual(layout.group(float("nan"), now), "Earlier")

    def test_the_unsearched_list_has_a_heading_per_group_and_headings_are_not_stops(self):
        now = at(0, 18)
        records = [{"id": "a", "updated": at(0)}, {"id": "b", "updated": at(0, 9)}, {"id": "c", "updated": at(1)},
                   {"id": "d", "updated": at(90)}]
        found = layout.rows(records, None, {}, now=now)
        self.assertEqual([(row["kind"], row.get("label") or row["id"]) for row in found],
                         [("new", None), ("heading", "Today"), ("chat", "a"), ("chat", "b"), ("heading", "Yesterday"),
                          ("chat", "c"), ("heading", "Earlier"), ("chat", "d")])
        self.assertEqual([found[i]["kind"] for i in layout.stops(found)], ["new", "chat", "chat", "chat", "chat"])

    def test_search_results_and_the_deleted_page_are_not_grouped(self):
        records = [{"id": "a", "updated": at(0)}, {"id": "b", "updated": at(40)}]
        found = layout.rows(records, [(records[1], "x"), (records[0], None)], {}, now=at(0, 18))
        self.assertNotIn("heading", [row["kind"] for row in found])

    def test_headings_fit_the_geometry(self):
        found = layout.rows([{"id": "a", "updated": at(0)}], None, {}, now=at(0, 18))
        geometry = layout.place(found, 500, 600)
        self.assertEqual(len(geometry["rows"]), len(found))


if __name__ == "__main__":
    unittest.main()
