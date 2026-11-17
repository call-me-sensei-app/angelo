# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add-ons that break as a newer Blender enables them (doc/angelo/app_identity.rst, "A start that did not finish"),
2.0.0 on Blender 5.3. No Blender.

* A native crash names its culprit: the start record's ``.loading`` file lists the add-ons Blender was enabling,
  outermost first, rewritten in place; the launch that finds the record leads its notice with the innermost one it
  left off. A crash outside any add-on names none, and the file never outlives its record.
* An add-on of the person's that raises as it loads (``PropertyGroup.name`` read-only in 5.3) is collected with a
  one-line cause (its first error, folders shortened) while a start or an enabling runs. Nothing it registered is
  undone and nothing is turned off by itself, as in stock Blender; the notice names each still enabled and not loaded
  since, with **Turn off**. Shipped and absent add-ons are never named. The wrapper never raises out of ``enable``.
* The notices are translatable, translated in every catalogue, and keep their placeholders.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import contextlib
import io
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/modules"), str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import angelo_addon_failures as failures  # noqa: E402
import angelo_safe_start as safe  # noqa: E402
import angelo_start_records as records  # noqa: E402

READ_ONLY = "bpy_struct: attribute \"name\" from \"PropertyGroup\" is read-only\nmore"


class Unprintable(Exception):
    def __str__(self):
        raise ValueError("an add-on's broken __str__")


def make_addon_utils(registered):
    """A stand-in ``addon_utils.enable`` with Blender's contract: a failure goes to ``handle_error``, then None.
    ``outer`` enables ``raises_inner`` from its ``register``, then raises itself."""
    utils = types.SimpleNamespace()

    def enable(module_name, *, default_set=False, persistent=False, handle_error=None):
        handle_error = handle_error or (lambda error: None)
        if module_name == "absent":
            handle_error(ModuleNotFoundError("No module named 'absent'", name="absent"))
            return None
        if module_name == "outer":
            registered.append("outer.Props")
            utils.enable("raises_inner")
            handle_error(RuntimeError("outer fails after its inner add-on"))
            return None
        if module_name == "unprintable":
            handle_error(Unprintable())
            return None
        if module_name.startswith("raises"):
            registered.append(module_name + ".Props")   # Stays: nothing is undone.
            handle_error(AttributeError(READ_ONLY))
            handle_error(RuntimeError("a later follow-on error"))
            return None
        registered.append(module_name)
        return types.SimpleNamespace(__name__=module_name)
    utils.enable = enable
    return utils


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.registered, self.notes, self.found, self.active = [], [], [], [True]
        self.addon_utils = make_addon_utils(self.registered)
        failures.install(self.addon_utils, lambda: self.active[0], self.notes.append, self.found,
                         lambda module: module != "angelo_agent")

    def enable(self, module, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            result = self.addon_utils.enable(module, **kwargs)
        return result, err.getvalue()

    def test_an_add_on_that_raises_is_collected_left_as_it_is_and_still_reported(self):
        result, printed = self.enable("raises_on_register")
        self.assertIsNone(result)
        self.assertEqual(self.found, [failures.Failure(
            "raises_on_register", 'AttributeError: bpy_struct: attribute "name" from "PropertyGroup" is read-only')],
            "the first error is the cause, not a follow-on")
        self.assertEqual(self.registered, ["raises_on_register.Props"], "nothing it registered is undone")
        self.assertIn("read-only", printed, "Blender's traceback is still printed")
        self.assertEqual(self.notes, [["raises_on_register"], []], "named while it loads, then none")

    def test_nested_enables_keep_the_outer_one_on_file_and_both_are_collected(self):
        self.enable("outer")
        self.assertEqual(self.notes, [["outer"], ["outer", "raises_inner"], ["outer"], []])
        self.assertEqual([f.module for f in self.found], ["raises_inner", "outer"])
        self.assertEqual(self.registered, ["outer.Props", "raises_inner.Props"])

    def test_a_good_add_on_is_no_failure(self):
        result, _ = self.enable("good")
        self.assertIsNotNone(result)
        self.assertEqual((self.found, self.registered), ([], ["good"]))

    def test_an_add_on_not_installed_is_no_failure(self):
        self.enable("absent")
        self.assertEqual(self.found, [])

    def test_a_shipped_add_on_is_never_noted_or_named(self):
        self.enable("angelo_agent")
        self.assertEqual((self.found, self.notes), ([], []))

    def test_outside_a_start_nothing_changes(self):
        self.active[0] = False
        result, _ = self.enable("raises_on_register")
        self.assertIsNone(result)
        self.assertEqual((self.found, self.notes), ([], []))

    def test_the_callers_own_error_handler_is_still_called(self):
        seen = []
        self.enable("raises_on_register", handle_error=seen.append)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(self.found), 1)

    def test_installed_once(self):
        wrapped = self.addon_utils.enable
        failures.install(self.addon_utils, lambda: True, self.notes.append, self.found, lambda module: True)
        self.assertIs(self.addon_utils.enable, wrapped)

    def test_the_wrapper_never_raises_out_of_enable(self):
        result, _ = self.enable("unprintable")
        self.assertIsNone(result)
        self.assertEqual(self.found, [failures.Failure("unprintable", "Unprintable")], "its type is the cause")

        def broken_note(names):
            raise OSError("disk gone")
        utils = make_addon_utils([])
        failures.install(utils, lambda: True, broken_note, self.found, lambda module: 1 / 0)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNotNone(utils.enable("good"), "a watched check that raises: Blender's enable as is")
        utils = make_addon_utils([])
        failures.install(utils, lambda: True, broken_note, self.found, lambda module: True)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNotNone(utils.enable("good"), "a note that cannot be written: the enabling goes on")

    def test_a_cause_is_one_short_line_without_folders(self):
        self.assertEqual(failures.cause(ValueError("bad\nsecond line")), "ValueError: bad")
        self.assertEqual(failures.cause(RuntimeError()), "RuntimeError")
        long = failures.cause(RuntimeError("x" * 500))
        self.assertEqual(len(long), failures.CAUSE_LENGTH)
        self.assertTrue(long.endswith("…"))
        self.assertEqual(failures.cause(OSError("dlopen(/Users/someone/addons/hair/core.so, 2): no image")),
                         "OSError: dlopen(core.so, 2): no image")
        self.assertEqual(failures.cause(FileNotFoundError(r"C:\Users\someone\AppData\x\tool.dll missing")),
                         "FileNotFoundError: tool.dll missing")
        self.assertEqual(failures.redact("see https://example.org/a/b and a/b"), "see https://example.org/a/b and a/b")

    def test_no_home_folder_shows_whatever_its_spelling(self):
        cases = {
            "dlopen(/Users/Jo Smith/addons/core.so, 2)": "dlopen(core.so, 2)",   # A user name with a space.
            r"No such file: 'C:\Users\Jo Smith\addons\x.py'": "No such file: 'x.py'",   # Quoted, whole.
            '"/opt/my tools/x.py" failed': '"x.py" failed',
            r"cannot open \\nas\share\tools\lib.dll now": "cannot open lib.dll now",   # A share.
            "cannot open //nas/share/tools/lib.so now": "cannot open lib.so now",
            "~/addons/hair/core.so is missing": "core.so is missing",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(failures.redact(text, home="/Users/Jo Smith"), expected)
        self.assertEqual(failures.redact(r"C:\Users\Jo Smith\AppData\x.dll gone", home=r"C:\Users\Jo Smith"),
                         "x.dll gone")

    def test_turn_off_disables_it_with_the_preference_and_saves(self):
        calls = []
        addon_utils = types.SimpleNamespace(disable=lambda module, default_set=False: calls.append(
            ("disable", module, default_set)))
        prefs = types.SimpleNamespace(is_dirty=False)
        bpy = types.SimpleNamespace(context=types.SimpleNamespace(preferences=prefs), ops=types.SimpleNamespace(
            wm=types.SimpleNamespace(save_userpref=lambda: calls.append(("save",)))))
        with mock.patch.dict(sys.modules, addon_utils=addon_utils, bpy=bpy):
            failures.turn_off("hair_tool")
        self.assertEqual(calls, [("disable", "hair_tool", True), ("save",)], "saved: the person asked for it")
        self.assertTrue(prefs.is_dirty)

    def test_pending_names_each_once_still_enabled_and_not_loaded_since(self):
        found = [failures.Failure("hair_tool", "E"), failures.Failure("hair_tool", "E2"),
                 failures.Failure("turned_off_meanwhile", "E"), failures.Failure("loaded_on_a_later_try", "E"),
                 failures.Failure("blendkit", "E")]
        enabled = {"hair_tool", "loaded_on_a_later_try", "blendkit"}.__contains__
        left = failures.pending(found, enabled, {"loaded_on_a_later_try"}.__contains__)
        self.assertEqual(left, [failures.Failure("hair_tool", "E"), failures.Failure("blendkit", "E")])


class CulpritTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "angelo-start"

    def begin(self):
        """A held record that is closed whatever the test meets, so its process-wide published record
        (``RECORD_ENV``) never leaks into a later test."""
        held = records.begin(self.folder)
        self.addCleanup(held.close)
        return held

    def test_a_crash_while_an_add_on_loads_names_it(self):
        held = self.begin()
        held.note_loading(["hair_tool"])
        held.close()   # The process ends here: the record stays, unlocked.
        found = records.interrupted(self.folder, wait=0)
        self.assertEqual([record.get("loading") for record in found], [["hair_tool"]])
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), [], "record and its .loading file are gone")
        self.assertEqual(safe.culprit(found, ["blendkit", "hair_tool"]), "hair_tool")
        self.assertIsNone(safe.culprit(found, ["blendkit"]), "only an add-on this launch left off leads")

    def test_nested_the_innermost_one_left_off_leads(self):
        found = [{"loading": ["outer", "inner"]}]
        self.assertEqual(safe.culprit(found, ["outer", "inner"]), "inner")
        self.assertEqual(safe.culprit(found, ["outer"]), "outer", "an inner one the preferences do not list")
        self.assertEqual(safe.culprit([{"loading": "outer"}], ["outer"]), "outer", "an earlier build's one name")

    def test_a_crash_outside_any_add_on_names_none_and_the_file_is_rewritten_not_removed(self):
        held = self.begin()
        held.note_loading(["hair_tool"])
        held.note_loading([])   # Its enabling finished.
        loading = self.folder / (held.path.name + records.LOADING)
        self.assertEqual(json.loads(loading.read_text(encoding="utf-8")), {"loading": []},
                         "rewritten in place (padded), never removed")
        held.close()
        found = records.interrupted(self.folder, wait=0)
        self.assertEqual(len(found), 1)
        self.assertNotIn("loading", found[0])
        self.assertIsNone(safe.culprit(found, ["hair_tool"]))

    def test_a_shorter_name_leaves_nothing_of_a_longer_one(self):
        held = self.begin()
        held.note_loading(["a_rather_long_add_on_name", "and_another"])
        held.note_loading(["x"])
        loading = (self.folder / (held.path.name + records.LOADING)).read_bytes()
        self.assertTrue(loading.startswith(b'{"loading": ["x"]}') and loading.endswith(b" "),
                        "padded with spaces in one write: no tail of the longer text, whatever a crash interrupts")
        held.close()
        self.assertEqual(records.interrupted(self.folder, wait=0)[0]["loading"], ["x"])

    def test_a_completed_start_leaves_no_loading_file(self):
        held = self.begin()
        held.note_loading(["hair_tool"])
        held.release()
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(records.interrupted(self.folder, wait=0), [])

    def test_a_loading_file_cut_short_only_loses_the_name(self):
        held = self.begin()
        held.close()
        (self.folder / (held.path.name + records.LOADING)).write_text('{"loading": ["hair', encoding="utf-8")
        found = records.interrupted(self.folder, wait=0)
        self.assertEqual(len(found), 1, "the record itself is whole: the start is still found")
        self.assertNotIn("loading", found[0])

    def test_a_loading_file_whose_record_went_is_removed(self):
        self.folder.mkdir(parents=True)
        (self.folder / ("1-gone" + records.SUFFIX + records.LOADING)).write_text("{}", encoding="utf-8")
        records.interrupted(self.folder, wait=0)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_isolation_has_one_reason_today(self):
        self.assertTrue(safe.isolate([{"pid": 1}]))
        self.assertFalse(safe.isolate([]))


class StateTests(unittest.TestCase):
    def setUp(self):
        saved = dict(safe._state, failures=list(safe._state["failures"]))
        self.addCleanup(lambda: (safe._state.clear(), safe._state.update(saved)))

    def test_an_enabling_without_a_record_collects_nothing_and_stays_inactive(self):
        safe._state.update(folder=Path("/nowhere"), held=None, active=False)
        with mock.patch.object(safe, "begin", return_value=None):
            with safe.enabling():
                self.assertFalse(safe._state["active"], "no record held: no watch would ever end it")
        self.assertFalse(safe._state["active"])

    def test_an_enabling_with_no_record_during_a_start_keeps_the_starts_completion(self):
        started = safe.Completion(0.0)
        safe._state.update(folder=Path("/nowhere"), held=None, active=True, completion=started)
        with mock.patch.object(safe, "begin", return_value=None), mock.patch.object(safe, "_watch"):
            with safe.enabling():
                pass
        self.assertIs(safe._state["completion"], started, "the start still completes on its own watch")

    def test_a_start_with_nothing_to_wait_for_completes(self):
        safe._state.update(completion=None)
        with mock.patch.object(safe, "_ready", return_value=False), mock.patch.object(safe, "_completed") as done:
            self.assertIsNone(safe._tick())
        done.assert_called_once()

    def test_causes_are_those_collected_so_far(self):
        safe._state["failures"][:] = [failures.Failure("hair_tool", "E: x")]
        self.assertEqual(safe.causes(), {"hair_tool": "E: x"})


class NoticeTests(unittest.TestCase):
    MESSAGES = {"safe_start_notice.py": {"BODY_CULPRIT": ("{culprit}", "{names}"), "FAILED_TITLE": (),
                                         "FAILED_INTRO": (), "TURNED_OFF": (), "MORE": ("{count}",)},
                "settings_import_card.py": {"FAILED_COUNT": ("{count}",), "MORE_NAMES": ("{count}",)},
                "ui_formats.py": {"LIST_ITEM": ("{list}", "{item}")}}

    def messages(self, name):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent" / name).read_text(encoding="utf-8"))
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and getattr(node.value.func, "id", "") == "n_":
                found[node.targets[0].id] = ast.literal_eval(node.value.args[0])
        return found

    def catalogues(self):
        """Every catalogue, Arabic's built one (po/ar.po, shaped from src/ar.po) included: a stale build is caught."""
        found = sorted((ROOT / "locale/angelo/po").glob("*.po")) + [ROOT / "locale/angelo/src/ar.po"]
        self.assertEqual(len(found), 21, "20 languages, Arabic's source beside its built catalogue")
        return found

    def test_the_notices_are_marked_and_translated_everywhere_with_their_placeholders(self):
        template = (ROOT / "locale/angelo/angelo.pot").read_text(encoding="utf-8")
        for source, wanted in self.MESSAGES.items():
            messages = self.messages(source)
            for name, placeholders in wanted.items():
                msgid = messages[name]
                self.assertIn('msgid "%s"' % msgid, template, name)
                for catalogue in self.catalogues():
                    text = catalogue.read_text(encoding="utf-8")
                    match = re.search(r'msgid "%s"\nmsgstr "((?:[^"\\]|\\.)+)"' % re.escape(msgid), text)
                    with self.subTest(message=name, catalogue=str(catalogue.relative_to(ROOT))):
                        self.assertIsNotNone(match, "translated")
                        for placeholder in placeholders:
                            self.assertIn(placeholder, match.group(1))

    def test_the_import_notice_names_the_absent_ones_capped_and_only_counts_the_failed_ones(self):
        from angelo_agent import settings_import_card as words
        absent = ["Gone %d" % index for index in range(7)]
        line = words.not_loaded({"failed": absent + ["Broken Tool"], "causes": {"Broken Tool": "RuntimeError: x"}})
        self.assertIn("Gone 0, Gone 1, Gone 2, Gone 3, Gone 4, 2 more", line)
        self.assertNotIn("Gone 5", line)
        self.assertIn("Add-ons that failed as they loaded: 1.", line)
        self.assertNotIn("Broken Tool", line, "named once: on its own notice, with its cause")
        self.assertEqual(words.not_loaded({"failed": ["Broken Tool"], "causes": {"Broken Tool": "E"}}),
                         "Add-ons that failed as they loaded: 1. Their own notice says why and can turn each off.")

    def test_a_list_is_built_one_item_at_a_time(self):
        from angelo_agent import ui_formats
        self.assertEqual(ui_formats.format_list(["A", "B", "C"]), "A, B, C")
        self.assertEqual(ui_formats.format_list([]), "-")
        with mock.patch.object(ui_formats, "iface_", lambda message, **fields: "{list}、{item}".format(**fields)):
            self.assertEqual(ui_formats.format_list(["A", "B", "C"]), "A、B、C")

    def test_the_failures_card_lists_five_then_how_many_more_and_turns_one_off_on_a_click(self):
        from test_native_notice_cards import VIEW
        from angelo_agent import card_kit as kit, safe_start_notice as notice
        addon_utils = types.SimpleNamespace(modules=lambda refresh=False: [], module_bl_info=lambda module: {})
        found = [failures.Failure("addon_%d" % index, "RuntimeError: %d" % index) for index in range(8)]
        with mock.patch.dict(sys.modules, addon_utils=addon_utils):
            rows, more = notice.failure_rows(found)
        self.assertEqual(([row[0] for row in rows], more), (["addon_%d" % i for i in range(5)], 3))
        off = []
        card = notice.FailuresCard(rows, more, off.append)
        plan = card.layout(VIEW, 1400, 900, 1.0, kit.UiState())
        actions = [(action, value) for _rect, action, value in plan.hits]
        self.assertEqual([value for action, value in actions if action == "turn_off"], [row[0] for row in rows])
        host = types.SimpleNamespace(redraw=lambda: None, close=lambda how: None)
        card.act("turn_off", "addon_2", host)
        self.assertEqual(off, ["addon_2"])
        plan = card.layout(VIEW, 1400, 900, 1.0, kit.UiState())
        self.assertNotIn(("turn_off", "addon_2"), [(a, v) for _r, a, v in plan.hits], "a turned-off row is done")
        card.act("turn_off", "addon_2", host)
        self.assertEqual(off, ["addon_2"], "turned off once")


if __name__ == "__main__":
    unittest.main()
