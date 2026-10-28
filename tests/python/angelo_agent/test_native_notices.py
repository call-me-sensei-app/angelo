# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chat notices: stored English text, a display recipe, and what a model may read.

A system or error chat row keeps its English ``text`` (history, restore, the
handoff to a model and every test read it) and may carry ``i18n``, the
template the canvas translates as it draws (``ui_text.notice``/``display_notice``).
"""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import codex_plan, display_text, sessions, session_validation, snapshot_service, ui_text  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402

JAPANESE = {"Moved to {place}.": "{place}に移動しました。",
            "Snapshot settings could not be read, so automatic snapshot removal is paused. "
            "Save Snapshot Settings again to resume it.": "スナップショットの設定を読めませんでした。"}


def translating(catalogue):
    """Patch the active interface catalogue, as Blender's translation would answer."""
    original = ui_text._pgettext.get("iface")
    ui_text._pgettext["iface"] = lambda message, context=None: catalogue.get(message, message)
    return original


class NoticeTests(unittest.TestCase):
    def tearDown(self):
        ui_text._pgettext.pop("iface", None)

    def test_english_text_is_exactly_the_old_wording(self):
        row = ui_text.notice("Auto asked {model} instead ({reason}); nothing had been applied to the scene.",
                             model="sonnet", reason="plan_rejected before anything was committed")
        self.assertEqual(row["text"], "Auto asked sonnet instead (plan_rejected before anything was committed); "
                                      "nothing had been applied to the scene.")
        self.assertEqual(ui_text.notice("Nothing here.")["text"], "Nothing here.")
        self.assertEqual(ui_text.notice("operations[{index}]:\n{problem}", index=3, problem="bad")["text"],
                         "operations[3]:\nbad")

    def test_a_not_certified_plan_and_a_failed_history_read_as_before(self):
        self.assertEqual(codex_plan.NOT_CERTIFIED, "Native plan completed. Tool read-backs are shown above; scene edits "
                                                   "have Undo, external effects do not.")
        with tempfile.TemporaryDirectory() as directory:
            warning = sessions.SessionStore(directory)._unverified_history()["messages"][-1]
        self.assertEqual(warning["text"], sessions.HISTORY_WARNING)
        self.assertEqual(warning["i18n"], [sessions.HISTORY_WARNING, {}])
        self.assertTrue(warning["text"].startswith("This conversation's stored history could not be verified."))

    def test_snapshot_notices_keep_their_wording_and_post_a_template(self):
        summary = {"removed": ["a/b", "a/c"], "budget_met": True, "blocked": False, "paused": False, "protected": [],
                   "preferred": ["a/b"], "policy": {"max_bytes": 0, "max_count": 0, "max_age_days": 0},
                   "remaining_bytes": 0, "released_bytes": 3 * 1024 ** 2}
        text = snapshot_service.notice_text({"kind": "maintained", "summary": summary})
        self.assertEqual(text, "Snapshot limit applied: removed 2 oldest restore points (3.0 MiB), first those captured before "
                               "the project's latest git commit. Their Restore links now show unavailable; limits are in "
                               "Snapshot Settings.")
        single = snapshot_service.notice_text({"kind": "maintained", "summary": dict(summary, removed=["a/b"], preferred=[])})
        self.assertIn("removed 1 oldest restore point (3.0 MiB). Their Restore", single)
        failed = snapshot_service.notice_text({"kind": "failed", "task": "ingest", "error": "disk full"})
        self.assertEqual(failed, "Snapshot storage could not finish deduplicating a snapshot: disk full. "
                                 "Existing restore points stay available.")
        controller = SimpleNamespace(emit=MagicMock())
        ui = SimpleNamespace(_controllers={"chat": controller}, _controller=controller)
        snapshot_service._shown.clear()
        snapshot_service._post(ui, {"kind": "failed", "task": "ingest", "error": "disk full", "notify": "chat"}, failed)
        kind, data = controller.emit.call_args.args
        self.assertEqual((kind, data["role"], data["text"]), ("message", "system", failed))
        self.assertEqual(data["i18n"][1], {"error": "disk full"})

    def test_a_fault_is_translated_by_its_template_not_its_detail(self):
        error = RuntimeError(display_text.Text("Drop a supported regular file, not a directory"))
        row = display_text.error_notice(error)
        self.assertEqual(row, {"text": "Drop a supported regular file, not a directory",
                               "i18n": ["Drop a supported regular file, not a directory", {}]})
        self.assertEqual(display_text.error_notice(ValueError("plain")), {"text": "plain"})


class RecipeValidationTests(unittest.TestCase):
    def test_a_recipe_is_a_template_and_small_json_scalars(self):
        good = ["Moved {n} of {name}", {"n": 3, "name": "Cube", "ok": True, "none": None, "ratio": 0.5}]
        self.assertEqual(ui_text.clean_recipe(good), good)
        for bad in (None, "text", ["only one"], [1, {}], ["x", []], ["x", {"a": [1]}], ["x", {"a": {"b": 1}}],
                    ["x", {1: "a"}], ["x", {"not valid": "a"}], ["x", {"a": float("nan")}], ["x", {"a": float("inf")}],
                    ["x", {"a": object()}], ["x" * 20000, {}], ["x", {"a": "y" * 20000}],
                    ["x", {f"f{i}": i for i in range(40)}]):
            with self.subTest(bad=repr(bad)[:40]):
                self.assertIsNone(ui_text.clean_recipe(bad))

    def test_notice_turns_awkward_values_into_text_and_never_breaks_a_save(self):
        row = ui_text.notice("{path} {nan}", path=Path("/tmp/x"), nan=float("nan"))
        self.assertEqual(row["text"], "%s nan" % Path("/tmp/x"))
        json.dumps(row, allow_nan=False)
        self.assertNotIn("i18n", ui_text.notice("{big}", big="y" * 20000))
        self.assertEqual(ui_text.notice("{big}", big="y" * 20000)["text"], "y" * 20000)


class DisplayTests(unittest.TestCase):
    def tearDown(self):
        ui_text._pgettext.pop("iface", None)

    def test_display_translates_while_the_stored_text_stays_english(self):
        row = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
        self.assertEqual(ui_text.display_notice(row), "Moved to Library.")
        translating(JAPANESE)
        self.assertEqual(ui_text.display_notice(row), "Libraryに移動しました。")
        self.assertEqual(row["text"], "Moved to Library.")
        self.assertEqual(display_text.shown(display_text.Text("Moved to {place}.", place="Library")), "Libraryに移動しました。")

    def test_a_row_rewritten_after_it_was_built_shows_its_own_text(self):
        row = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
        row["text"] = "Restored instead."
        translating(JAPANESE)
        self.assertEqual(ui_text.display_notice(row), "Restored instead.")
        self.assertEqual(ui_text.display_notice({"role": "system", "text": "No recipe."}), "No recipe.")
        self.assertEqual(ui_text.display_notice({"role": "error", "text": "x", "i18n": ["{a}", {"a": 1}]}), "x")

    def test_a_catalogue_entry_with_a_wrong_placeholder_never_breaks_a_draw(self):
        row = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
        translating({"Moved to {place}.": "{wrong} {place}"})
        self.assertEqual(ui_text.display_notice(row), "Moved to Library.")

    def test_a_model_reads_the_english_text_whatever_language_is_active(self):
        notice = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
        summary = {"role": "system", "text": "Host summary.", "host_summary": True}
        translating(JAPANESE)
        text, _ = handoff([{"role": "user", "text": "Move it."}, notice, summary])
        self.assertIn("Moved to Library.", text)
        self.assertIn("Host summary.", text)
        for leaked in ("に移動", "i18n", "Moved to {place}"):
            self.assertNotIn(leaked, text)

    def test_console_draws_system_and_error_rows_in_the_active_language(self):
        previous = getattr(angelo_agent, "console", None)
        with patch.dict(sys.modules, {"bpy": MagicMock()}):
            sys.modules.pop("angelo_agent.console", None)
            console = importlib.import_module("angelo_agent.console")
            try:
                notice = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
                error = {"role": "error", **ui_text.notice("Moved to {place}.", place="Library")}
                stored = dict(notice)
                self.assertEqual(console.display_text(notice), "Moved to Library.")
                translating(JAPANESE)
                self.assertEqual(console.display_text(notice), "Libraryに移動しました。")
                self.assertEqual(console.display_text(error), "Libraryに移動しました。")
                self.assertEqual(console.display_text({"role": "user", "text": "Moved to Library.", "i18n": notice["i18n"]}),
                                 "Moved to Library.", "only system and error rows are recipes")
                self.assertEqual(console.display_text({"role": "tool", "text": "Approval required: x"}), "Approval required: x")
                self.assertEqual(notice, stored)
            finally:
                ui_text._pgettext.pop("iface", None)
                sys.modules.pop("angelo_agent.console", None)
                if previous is None:
                    delattr(angelo_agent, "console")
                else:
                    angelo_agent.console = previous


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def save_and_load(self, messages):
        store = sessions.SessionStore(self.directory.name)
        state = store.new()
        state["messages"] = messages
        store.save(state)
        return store.load()

    def test_a_recipe_survives_a_save_and_a_load(self):
        row = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library")}
        error = {"role": "error", **ui_text.notice("Voice playback: {detail}", detail="no speaker")}
        loaded = self.save_and_load([{"role": "user", "text": "hi"}, row, error])
        self.assertEqual(loaded["messages"][1], row)
        self.assertEqual(loaded["messages"][2], error)
        self.assertEqual(len(loaded["messages"]), 3, "no history warning: the rows verified")

    def test_a_malformed_recipe_is_dropped_but_the_row_is_kept(self):
        good = ui_text.notice("Moved to {place}.", place="Library")
        rows = [
            {"role": "system", "text": "a", "i18n": "not a recipe"},
            {"role": "system", "text": "b", "i18n": ["b", {"x": [1, 2]}]},
            {"role": "error", "text": "c", "i18n": ["c", {"bad name": 1}]},
            {"role": "system", "text": "Other words.", "i18n": good["i18n"]},
            {"role": "user", "text": good["text"], "i18n": good["i18n"]},
            {"role": "assistant", "text": good["text"], "i18n": good["i18n"]},
            {"role": "system", **good},
        ]
        loaded = self.save_and_load(rows)
        self.assertEqual([m["text"] for m in loaded["messages"]],
                         ["a", "b", "c", "Other words.", good["text"], good["text"], good["text"]])
        self.assertEqual([("i18n" in m) for m in loaded["messages"]], [False] * 6 + [True])
        self.assertEqual(len(loaded["messages"]), 7, "dropping a recipe is not corruption")

    def test_validation_keeps_other_message_fields(self):
        row = {"role": "system", **ui_text.notice("Moved to {place}.", place="Library"), "notice": True, "restore_state": "pending"}
        kept, verified = session_validation.messages([row])
        self.assertTrue(verified)
        self.assertEqual(kept, [row])


class ControllerEmitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.api_key = "sk-secret-123"

    def emit(self, data):
        self.controller.emit("message", data)
        self.controller.pump_events()
        return self.controller.state["messages"][-1]

    def test_system_and_error_rows_keep_the_recipe(self):
        for role in ("system", "error"):
            row = self.emit({"role": role, **ui_text.notice("Moved to {place}.", place="Library")})
            self.assertEqual(row, {"role": role, "text": "Moved to Library.", "i18n": ["Moved to {place}.", {"place": "Library"}]})

    def test_redaction_applies_to_the_text_and_to_every_string_field(self):
        row = self.emit({"role": "error", **ui_text.notice("Rejected {detail}: {count} tries", detail="key sk-secret-123 failed", count=2)})
        self.assertEqual(row["text"], "Rejected key [REDACTED] failed: 2 tries")
        self.assertEqual(row["i18n"], ["Rejected {detail}: {count} tries", {"detail": "key [REDACTED] failed", "count": 2}])
        self.assertNotIn("sk-secret-123", json.dumps(row))
        self.assertEqual(ui_text.display_notice(row), row["text"])

    def test_other_roles_and_malformed_recipes_are_not_stored(self):
        recipe = ui_text.notice("Moved to {place}.", place="Library")
        for role in ("user", "assistant"):
            self.assertNotIn("i18n", self.emit({"role": role, "text": recipe["text"], "i18n": recipe["i18n"]}))
        for bad in ("junk", ["x"], ["x", {"a": {"b": 1}}], ["Moved to {place}.", {"place": float("nan")}]):
            row = self.emit({"role": "system", "text": "Moved to Library.", "i18n": bad})
            self.assertNotIn("i18n", row)
            self.assertEqual(row["text"], "Moved to Library.")

    def test_a_notice_the_host_appends_directly_is_saved_and_loaded_with_its_recipe(self):
        self.controller.state["messages"].append({"role": "system", **ui_text.notice("Moved to {place}.", place="Library")})
        self.controller.save()
        loaded = sessions.SessionStore(self.directory.name).load()
        self.assertEqual(loaded["messages"][-1]["i18n"], ["Moved to {place}.", {"place": "Library"}])


if __name__ == "__main__":
    unittest.main()
