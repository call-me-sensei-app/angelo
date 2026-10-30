# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Text written for a model is never what a person's chat row shows; the Restore dialog says its facts plainly.

Evidence (1.0.6 GUI dogfood, finding C10): after a relaunch, a chat whose restore ended incomplete showed the
model's instruction ("... a fresh native context and observations are required before continuing. Do not
automatically repeat recovery or earlier edits.") as the person's row, translated. A row may now carry its own
``model_text`` (``ui_text.model_notice``): every reader that hands rows to a model reads it, the person's row shows
the notice. Finding C5: the Restore dialog had eight dense lines of internal terms.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_agent  # noqa: E402,F401
from angelo_agent import (activity_text, checkpoint_images, external_apps, session_rail, session_validation,  # noqa: E402
                          sessions, ui_text)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
MODEL = session_validation.RECOVERY_MODEL_TEXT
PERSON = session_validation.RECOVERY_CHECK
RESTORE = {"role": "system", "text": "Snapshot recovery is starting.", "restore_state": "partial",
           "restore_checkpoint": "0b8c3c1e-9d2f-4a59-9b7e-3f3a8d2f1c11"}


class ModelNoticeTests(unittest.TestCase):
    def tearDown(self):
        ui_text._pgettext.pop("iface", None)

    def test_the_person_and_the_model_read_their_own_words(self):
        row = {"role": "system", **ui_text.model_notice("Model words {not a template}.", "Moved to {place}.",
                                                        place="Library")}
        self.assertEqual((row["text"], row["model_text"]), ("Moved to Library.", "Model words {not a template}."))
        ui_text._pgettext["iface"] = lambda message, context=None: {"Moved to {place}.": "{place}に移動"}.get(message,
                                                                                                           message)
        self.assertEqual(ui_text.display_notice(row), "Libraryに移動")
        self.assertEqual(ui_text.model_text(row), "Model words {not a template}.")
        self.assertEqual(ui_text.model_text({"text": "plain"}), "plain")

    def test_every_reader_that_hands_rows_to_a_model_reads_the_model_text(self):
        row = {"role": "system", **ui_text.model_notice(MODEL, PERSON)}
        text, _ = handoff([{"role": "user", "text": "hi"}, row])
        self.assertIn(json.dumps(MODEL), text)
        self.assertNotIn("didn't fully finish", text)
        self.assertEqual(external_apps._row(row)["text"], MODEL[:external_apps.TASK_TEXT])
        turn = [{"role": "user", "text": "brief"}, row]
        end, problem = session_rail.turn_end(turn, outcome=("failed", False, False, 2))
        self.assertEqual((end, problem), ("failed", session_rail.TURN_FAILED % MODEL[:session_rail.PROBLEM_LIMIT]))

    def test_an_emitted_row_keeps_its_model_text_redacted_and_validation_admits_it_only_where_it_belongs(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(directory)
            try:
                controller.api_key = "sk-secret-123"
                controller.emit("message", {"role": "system", **ui_text.model_notice("key sk-secret-123", "Done.")})
                controller.pump_events()
                row = controller.state["messages"][-1]
            finally:
                controller.close()
        self.assertEqual((row["text"], row["model_text"]), ("Done.", "key [REDACTED]"))
        kept, verified = session_validation.messages([row])
        self.assertTrue(verified)
        self.assertEqual(kept, [row])
        for bad in ({"role": "user", "text": "x", "model_text": "y"}, {"role": "system", "text": "x", "model_text": 3}):
            self.assertEqual(session_validation.messages([bad]), ([], False))


class RecoveryCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def load_after(self, messages):
        store = sessions.SessionStore(self.directory.name)
        state = store.new()
        state["messages"], state["native_id"] = messages, "thread-1"
        store.save(state)
        loaded = sessions.SessionStore(self.directory.name).load()
        return store, loaded

    def test_an_unfinished_restore_adds_one_row_in_the_persons_words(self):
        store, loaded = self.load_after([{"role": "user", "text": "hi"}, dict(RESTORE)])
        self.assertIsNone(loaded["native_id"], "a fresh native context, as before")
        row = loaded["messages"][-1]
        self.assertEqual((row["text"], row["model_text"], row["recovery_check"]), (PERSON, MODEL, True))
        self.assertEqual(ui_text.display_notice(row), PERSON)
        view = activity_text.notice_view(row)
        self.assertEqual((view["tone"], view["headline"]), ("amber", "A restore in this chat didn't fully finish"))
        self.assertNotIn("restore_checkpoint", row, "that restore's own notice keeps Undo restore and its images")
        store.save(loaded)
        again = sessions.SessionStore(self.directory.name).load()
        self.assertEqual(sum(bool(m.get("recovery_check")) for m in again["messages"]), 1, "never repeated per load")

    def test_a_row_an_earlier_build_stored_as_the_models_instruction_becomes_the_persons_row(self):
        legacy = {"role": "system", **ui_text.notice(MODEL)}
        _store, loaded = self.load_after([dict(RESTORE), legacy])
        rows = [m for m in loaded["messages"] if m.get("recovery_check")]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["text"], rows[0]["model_text"]), (PERSON, MODEL))
        self.assertFalse(any(ui_text.display_notice(m) == MODEL for m in loaded["messages"]))

    def test_a_completed_restore_adds_nothing(self):
        _store, loaded = self.load_after([dict(RESTORE, restore_state="complete")])
        self.assertFalse(any(m.get("recovery_check") for m in loaded["messages"]))
        self.assertEqual(loaded["native_id"], "thread-1")

    def test_model_text_never_reaches_the_persons_catalogue(self):
        template = (ROOT / "locale/angelo/angelo.pot").read_text(encoding="utf-8")
        self.assertFalse("incomplete stored outcome" in template, "the model's instruction is not interface text")
        self.assertTrue(json.dumps(PERSON, ensure_ascii=False) in template.replace('"\n"', ""), "the person's line is")


class Layout:
    def __init__(self):
        self.labels = []

    def label(self, text="", translate=True, icon='NONE'):
        self.labels.append(text)


class RestoreDialogTests(unittest.TestCase):
    def test_the_dialog_says_its_facts_plainly(self):
        chat_view = SimpleNamespace(wrap=lambda text, width, size, font: [(text, 0)], _font=0)
        with patch.dict(sys.modules, {"angelo_agent.chat_view": chat_view}), \
                patch("angelo_agent.chat_view", chat_view, create=True):
            layout = Layout()
            checkpoint_images.draw_restore(layout, None)
        shown = "\n".join(layout.labels)
        for fact in (checkpoint_images.RESTORE_SCOPE, checkpoint_images.RESTORE_LIMITS, checkpoint_images.SNAPSHOT_RULE):
            self.assertIn(fact, layout.labels)
        for jargon in ("working copy", "Scoped", "arbitrary", "Python/network", "document"):
            self.assertNotIn(jargon, shown.split("\n", 2)[-1], "the facts after the question and its note")
        self.assertEqual(len(layout.labels), 5, "question, note and three plain paragraphs")


if __name__ == "__main__":
    unittest.main()
