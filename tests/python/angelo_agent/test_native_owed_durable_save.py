# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A durable change the pump's save throttle skips is owed, and written by a later pump, never dropped.

1.0.8 dogfood (B-004): in a chat bound to a saved file, a save runs just before the person's request row is added;
the row's own save then fell inside the pump's 0.5 s throttle and was skipped, and while the model only thinks the
pump sees display events alone, so the row reached disk only at the first tool row or the turn's end. A crash or a
force quit in between (seconds to minutes of thinking) lost the request and its snapshot link on relaunch. The
journal of that run, copied just before the crash, held no record with the row.

Dependency-free: the controller's own pump and journal, no Blender, provider process or model.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_journal  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402

REQUEST = "Now add a tall stone lighthouse behind the cube."


class OwedSave(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.set_document(("owed-save", 1))
        self.closed = False

    def tearDown(self):
        if not self.closed:
            self.controller.close()
        self.directory.cleanup()

    def on_disk(self):
        """Whether the journal, read as a relaunch reads it, holds the request row."""
        state = session_journal.read(self.controller.store.path)[0]
        return any(message.get("text") == REQUEST for message in state.get("messages", ()))

    def send_right_after_a_save(self):
        """A save, then the request row inside the throttle (as a file-bound chat's send does)."""
        self.controller.save()
        self.controller.emit("message", {"role": "user", "text": REQUEST})
        self.controller.pump_events()
        self.assertFalse(self.on_disk(), "The throttle holds the row back for a moment")

    def later(self, seconds=1.0):
        """As if ``seconds`` passed since the last save."""
        self.controller._last_save -= seconds

    def test_an_idle_pump_writes_the_owed_row(self):
        self.send_right_after_a_save()
        self.later()
        self.controller.pump_events()   # No event at all: the UI pumps every 0.1 s while idle.
        self.assertTrue(self.on_disk())

    def test_display_events_alone_still_write_it(self):
        self.send_right_after_a_save()
        self.later()
        self.controller.emit("status", {"text": "Thinking"})   # What a thinking model sends: display only.
        self.controller.pump_events()
        self.assertTrue(self.on_disk())

    def test_nothing_is_written_twice_once_saved(self):
        self.send_right_after_a_save()
        self.later()
        self.controller.pump_events()
        with mock.patch.object(self.controller.store, "save", wraps=self.controller.store.save) as save:
            self.later()
            self.controller.pump_events()
            self.controller.pump_events()
        self.assertEqual(save.call_count, 0, "Nothing owed: an idle pump writes nothing")

    def test_close_writes_an_owed_row(self):
        self.send_right_after_a_save()
        self.controller.close()   # Quit, chat close and add-on unregister: a compacting save of everything.
        self.closed = True
        self.assertTrue(self.on_disk())

    def test_a_failing_save_stays_owed_and_waits_out_the_throttle(self):
        self.send_right_after_a_save()
        self.later()
        with mock.patch.object(self.controller.store, "save", side_effect=OSError(28, "No space left")) as save:
            for _ in range(5):
                self.controller.pump_events()   # Pumps 0.1 s apart: one attempt, not five.
        self.assertEqual(save.call_count, 1)
        self.later()
        self.controller.pump_events()
        self.assertTrue(self.on_disk(), "Written once a save can succeed")


if __name__ == "__main__":
    unittest.main()
