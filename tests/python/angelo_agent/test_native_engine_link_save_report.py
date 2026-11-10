# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight's report reaches the agent only as the report of the save it describes
(``engine_link_tool_host._describes``; doc/angelo/engine_link.rst, "The agent"), on the real host and link.

Covered (audit of PR #129, 2026-11-03): SAVE read the report when the save began, before that save's own check had
run, so the previous save's report came back as the new save's. A scene version proved no key (break-it 9-H1: it
moves only with a live push, so two saves share one), nor the saved file's identity (10-N1: made-up inodes and coarse
times on FAT, HFS+ or network shares). A report is now a save's only when it echoes the save's exact name
(``engine_link_facts.save_key``, given to the check), and, when both are known, the bytes the save's write-back wrote
(``target_sha256``); otherwise the answer says no check of the save has reported.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_engine_link_tool as base  # noqa: E402  (its stand-in bpy and fixtures)

engine_link, host, assets, facts, EDIT = base.engine_link, base.host, base.assets, base.facts, base.EDIT
engine_link_saves = engine_link.engine_link_saves


class SaveReportTests(unittest.TestCase):
    setUp, link, ack = base.ToolTests.setUp, base.ToolTests.link, base.ToolTests.ack   # Its fixtures alone.

    def linked(self, ident="res://crate.glb"):
        asset = self.link(ident)
        self.blend = Path(asset.editable)
        self.blend.parent.mkdir(parents=True, exist_ok=True)
        self.writes = 0

        def save_mainfile(filepath):   # As Blender's: a new file written beside it and moved over it, then the
            self.writes += 1           # save handlers run (two saves of one size within one second included).
            temporary = self.blend.with_name(self.blend.name + "@")
            temporary.write_bytes(b"blend %d" % self.writes)
            os.utime(temporary, ns=(10 ** 18 + self.writes * 10 ** 6,) * 2)
            os.replace(temporary, self.blend)
            from save_identity_stand_in import saving
            with saving(self.blend):   # As Blender's save names the file it wrote to its handlers.
                engine_link._save_post(filepath)
            return {"FINISHED"}
        base.BPY.ops.wm.save_mainfile = save_mainfile
        base.BPY.data.filepath = asset.editable
        self.written = {"report": None}
        self.enterContext(mock.patch.object(host, "PREFLIGHT", lambda _asset: self.written["report"]))
        return asset

    def report(self, **fields):
        """The check of the save just made, as #131 writes it: the save's name it was given, echoed."""
        return dict({"status": "done", "scene_version": engine_link._state["version"],
                     "save_key": facts.save_key(engine_link._state["facts"]), "counts": {"error": 0}}, **fields)

    def start(self):
        pending = host.execute(None, EDIT, {"action": "SAVE"})
        return pending, engine_link._state["facts"]["save"]["save"]

    def finish(self, pending, number, sha256="a" * 64, write_error=None):
        self.writeback.done.append((self.writeback.requests[-1][0], write_error))
        with mock.patch.object(assets, "written_stamp", return_value=sha256):
            engine_link._written()
        if write_error is None:
            self.ack(1, code="reimported", save=number)
        return pending.poll()

    def test_two_saves_at_one_scene_version_never_share_a_report(self):
        """Break-it 9-H1 (A1): no live push between the saves; the first's check ends after the second began."""
        self.server.editor(1)
        self.linked()
        first, number = self.start()
        first_report = self.report(counts={"error": 3})
        self.finish(first, number)
        second, number = self.start()   # Saved again at the same scene version.
        self.written["report"] = first_report
        self.assertEqual(host.status()["linked"]["preflight"]["state"], "pending")   # A2: while it writes.
        answer = self.finish(second, number)
        self.assertEqual((answer["save"], answer["preflight"]["state"]), (2, "pending"))
        self.written["report"] = self.report(counts={"error": 5}, target_sha256="a" * 64)
        self.assertEqual(host.status()["linked"]["preflight"]["counts"], {"error": 5})

    def test_its_own_report_is_given_done_or_failed(self):
        self.server.editor(1)
        self.linked()
        pending, number = self.start()
        self.written["report"] = self.report(counts={"warning": 1})
        self.assertEqual(self.finish(pending, number)["preflight"]["counts"], {"warning": 1})
        failed = {"status": "failed", "scene_version": 0, "error": "the check of /Users/someone/x.blend stopped",
                  "save_key": facts.save_key(engine_link._state["facts"])}   # #131's failed report: no digest.
        self.written["report"] = failed
        shown = host.status()["linked"]["preflight"]
        self.assertEqual(shown["status"], "failed")
        self.assertNotIn("/Users/someone", shown["error"])

    def test_a_report_of_other_bytes_or_unverifiable_bytes_is_not_this_saves(self):
        """Break-it 9-H1 (A3): the stamp failed, so this save's bytes are unknown: a digest can't be checked."""
        self.server.editor(1)
        self.linked()
        pending, number = self.start()
        self.written["report"] = self.report(target_sha256="b" * 64)
        self.assertEqual(self.finish(pending, number, sha256="c" * 64)["preflight"]["state"], "pending")
        pending, number = self.start()
        self.written["report"] = self.report(target_sha256="d" * 64)
        self.assertEqual(self.finish(pending, number, sha256=None)["preflight"]["state"], "pending")

    def test_a_report_left_by_another_run_or_missing_its_key_is_never_this_saves(self):
        self.server.editor(1)
        self.linked()
        pending, number = self.start()
        key = facts.save_key(engine_link._state["facts"])
        for left in ({"status": "done", "scene_version": 99, "saved_at": 1e12},   # #131 before save_key.
                     dict(self.report(), save_key="0123456789abcdef:%d" % number),   # Another run's save 1.
                     dict(self.report(), save_key=key + "0"), dict(self.report(), save_key=None),
                     dict(self.report(), save_key=[key])):
            with self.subTest(report=left):
                self.written["report"] = left
                self.assertEqual(host.status()["linked"]["preflight"]["state"], "pending")
        self.finish(pending, number)

    def test_two_saves_one_file_identity_still_get_their_own_reports(self):
        """Break-it 10-N1: a volume with made-up inodes and coarse times (FAT, HFS+, some shares) gives two saves of
        one size within one tick the same file identity; their names still differ."""
        self.server.editor(1)
        self.linked("res://crate.blend")

        def in_place(filepath):   # Written in place, with the same time and size each save.
            self.writes += 1
            self.blend.write_bytes(b"same size")
            os.utime(self.blend, ns=(2 * 10 ** 18, 2 * 10 ** 18))
            from save_identity_stand_in import saving
            with saving(self.blend):
                engine_link._save_post(filepath)
            return {"FINISHED"}
        base.BPY.ops.wm.save_mainfile = in_place
        first, number = self.start()
        first_report = self.report(counts={"error": 7})
        self.ack(1, code="reimported", save=number)
        first.poll()
        second, number = self.start()
        self.written["report"] = first_report
        self.ack(1, code="reimported", save=number)
        self.assertEqual(second.poll()["preflight"]["state"], "pending")

    def test_a_saves_own_name_is_given_by_its_number(self):
        """A write-back's end names its own save, never the last one (engine_link_saves.written, for #131)."""
        self.server.editor(1)
        self.linked()
        _first, first = self.start()
        _second, second = self.start()
        known = engine_link._state["facts"]
        self.assertEqual(facts.save_key(known), facts.save_key(known, second))
        self.assertNotEqual(facts.save_key(known, first), facts.save_key(known, second))
        self.assertTrue(facts.save_key(known, first).startswith(facts.RUN + ":"))

    def test_the_preflight_is_started_with_each_saves_own_name(self):
        """The end of a write-back names its own save to the preflight, also when a newer save has begun since."""
        self.server.editor(1)
        self.linked()
        _first, first = self.start()
        _second, second = self.start()
        known = engine_link._state["facts"]
        with mock.patch.object(engine_link_saves.engine_preflight_link, "after_writeback") as started:
            self.writeback.done.append((self.writeback.requests[0][0], None))
            self.writeback.done.append((self.writeback.requests[1][0], None))
            with mock.patch.object(assets, "written_stamp", return_value="a" * 64):
                engine_link._written()
        self.assertEqual([call.kwargs["save_key"] for call in started.call_args_list],
                         [facts.save_key(known, first), facts.save_key(known, second)])

    def test_a_blend_saves_preflight_is_named_too(self):
        self.server.editor(1)
        self.linked("res://crate.blend")
        with mock.patch.object(engine_link_saves.engine_preflight_link, "after_blend_save") as started:
            _pending, number = self.start()
        self.assertEqual(started.call_args.kwargs["save_key"], facts.save_key(engine_link._state["facts"], number))

    def test_a_blend_assets_save_is_matched_the_same_way(self):
        """Held out: a .blend asset has no write-back and #131 writes no digest for it."""
        self.server.editor(1)
        self.linked("res://crate.blend")
        pending, number = self.start()
        self.written["report"] = self.report(counts={"error": 2})
        self.ack(1, code="reimported", save=number)
        self.assertEqual(pending.poll()["preflight"]["counts"], {"error": 2})

    def test_no_report_before_a_save_after_a_failed_write_back_or_while_writing(self):
        self.server.editor(1)
        self.linked()
        self.written["report"] = {"status": "done", "blend_identity": None}
        self.assertNotIn("preflight", host.status()["linked"])   # No save of this link: no report is its.
        reader = mock.Mock(return_value=None)
        with mock.patch.object(host, "PREFLIGHT", reader):
            pending, number = self.start()
            self.assertIsNone(pending.poll())
            reader.assert_not_called()   # Never read while its write-back runs: only with the save's answer.
        answer = self.finish(pending, number, write_error="the exporter failed")
        self.assertEqual(answer["state"], "failed")
        self.assertNotIn("preflight", answer)
        self.assertNotIn("preflight", host.status()["linked"])   # Nothing was written: nothing to check.


if __name__ == "__main__":
    unittest.main()
