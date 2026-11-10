# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link comparison grades a turn by what Godot's recorder saw during it (``engine_link_compare`` checks,
``engine_link_compare_session``), never by how far the recorder had written when the turn ended.

Covered (design run of 2026-11-03, Opus tools arm, trials 2 and 3): Godot showed the 1.2x crate 0.15 s and 0.10 s
before the turn ended, and the push was acknowledged, but the recorder writes every 0.25 s and the turn was graded at
once, so both pushes read as never shown. Grading now waits until the recorder has written past the turn's end, bounds
each event to the turn by the recorder's own times, and a recorder that never catches up makes the task invalid.

Also covered (the same run): Angelo's engine watcher never runs in an automation run, so every brief in both arms told
the agent no engine ran, which a person with Godot open is never told. The session now runs one real engine scan
after Godot starts, before any brief, in both arms alike.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import angelo_agent  # noqa: E402
import engine_link_compare as compare  # noqa: E402
import engine_link_compare_session as compare_session  # noqa: E402
from angelo_agent import engine_detect  # noqa: E402

SENT, END = 1000.0, 1013.8


def preview(at, height):
    return {"asset": compare.CRATE, "version": 2, "height": height, "width": 2.0, "at": at}


class CheckTests(unittest.TestCase):
    def test_a_push_shown_just_before_the_turn_ended_passes(self):
        result = compare.check_push({"previews": [preview(END - 0.15, 2.4)]}, SENT, 2.0, END)
        self.assertTrue(result["passed"])

    def test_only_the_turns_own_previews_count(self):
        """Before the message, or the next brief's setup after the turn (it pushes a 1.2x crate too), never count."""
        godot = {"previews": [preview(SENT - 2, 2.4), preview(END + 0.4, 2.0), preview(END + 1.2, 2.4)]}
        result = compare.check_push(godot, SENT, 2.0, END)
        self.assertFalse(result["passed"])
        self.assertEqual(result["previews_after_send"], 0)


class Session(compare_session.EngineLinkSession):
    def __init__(self, report):
        super().__init__("engine-link-compare", tempfile.mkdtemp())
        self.written = report

    def godot_report(self):
        return dict(self.written)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.record = {"sent_at_unix": SENT, "send_to_idle_ms": (END - SENT) * 1000}

    def session(self, written_at):
        session = Session({"at": written_at, "previews": [preview(END - 0.15, 2.4)]})
        session.record, session.attached, session.prepared = self.record, 1, 0
        return session

    def test_the_turn_is_held_until_the_recorder_has_written_past_its_end(self):
        self.assertTrue(self.session(END - 0.1).jobs_pending())
        self.assertFalse(self.session(END + 0.05).jobs_pending())

    def test_the_grade_uses_the_turns_end_not_the_clock_when_graded(self):
        session = self.session(END + 0.05)
        session.shape["height"] = 2.0
        with mock.patch.object(compare_session.time, "time", return_value=END + 30), \
                mock.patch.object(session, "turn_rows", return_value=[]):
            passed = session.grade(None, None, {"id": "push"}, self.record)
        self.assertTrue(passed)
        self.assertEqual(self.record["host_check"]["heights"], [2.4])

    def test_a_recorder_that_never_caught_up_makes_the_task_invalid_not_failed(self):
        session = self.session(END - 5)
        session.shape["height"] = 2.0
        with mock.patch.object(session, "turn_rows", return_value=[]):
            session.grade(None, None, {"id": "push"}, self.record)
        self.assertIn("recorder", self.record["host_check"]["invalid"])
        self.assertIn("push", session.invalid)

    def test_a_turn_that_never_went_idle_is_graded_at_once(self):
        session = self.session(0)
        session.record = {"sent_at_unix": SENT}
        with mock.patch.object(compare_session.time, "time", return_value=0):
            self.assertTrue(session.recorded())


class EngineFactsTests(unittest.TestCase):
    def test_the_scan_is_the_real_one_and_feeds_what_the_agent_reads(self):
        """The real ``engine_detect`` (a running Godot's process name reads as Godot) fills the watcher's state that
        ``engine_suggest_ui.facts`` and ``agent_note`` read (their shape: test_native_engine_detect)."""
        watcher = types.SimpleNamespace(_running=frozenset(), _installed=None)
        watcher.facts = lambda: {"running": sorted(watcher._running), "installed": sorted(watcher._installed)}
        with mock.patch.object(angelo_agent, "engine_suggest_ui", watcher, create=True), \
                mock.patch.object(engine_detect, "process_names", return_value=["launchd", "Godot", "Angelo"]), \
                mock.patch.object(engine_detect, "installed", return_value=frozenset({"godot"})):
            facts = Session({}).seed_engine_facts()
        self.assertEqual(facts, {"running": ["godot"], "installed": ["godot"]})

    def test_the_scan_runs_after_godot_starts_and_a_miss_stops_the_trial(self):
        source = Path(compare_session.__file__).read_text(encoding="utf-8")
        begin = source[source.index("    def begin("):source.index("    def seed_engine_facts(")]
        self.assertLess(begin.index("subprocess.Popen(["), begin.index("self.seed_engine_facts()"))
        self.assertIn('raise RuntimeError("Angelo\'s engine scan didn\'t find the running Godot', begin)


if __name__ == "__main__":
    unittest.main()
