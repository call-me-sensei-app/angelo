# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script that raised is reported rolled back only when the file was reloaded from its own restore point.

No model, process or Blender: Blender's Undo history is a stand-in whose steps
hold a state, a mode and the restore point's marker, and whose steps in Edit
or Sculpt Mode hold nothing of the rest of the file, as Blender's do. The same
rules run in the app in ``test_blender_script_rollback.py`` (every case against
the scene itself) and ``test_blender_turn_recovery.py`` (through the approval).
"""

import ast
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import script_errors  # noqa: E402
from angelo_agent.script_errors import APPLY, APPLY_SCRIPT, MAX_UNDO, RESTORE, STEP, STOPPED, WHOLE_FILE_MODES  # noqa: E402

BEFORE = {"objects": {"1": {"name": "Cloud Deck Far"}, "2": {"name": "Island"}}, "count": 2, "sun": 3.0}
# The script removed the cloud deck and doubled the sun's strength, then raised.
PARTIAL = {"objects": {"2": {"name": "Island"}}, "count": 1, "sun": 6.0}
SUN_ONLY = dict(BEFORE, sun=6.0)  # Nothing a read-back of object names and a count can see.
CODE = "bpy.data.objects.remove(bpy.data.objects['Cloud Deck Far'])\nscene.cycles.feature_set = 'X'\n"


def raised(code="def build():\n    raise AttributeError('feature_set')\nbuild()\n"):
    try:
        exec(compile(code, script_errors.SCRIPT_FILE, "exec"), {"__name__": "__angelo_reviewed_script__"})
    except Exception as error:
        return error
    raise AssertionError("The script did not raise")


def image(uid, dirty=False, packed=-1):
    return SimpleNamespace(session_uid=uid, is_dirty=dirty, packed_file=SimpleNamespace(size=packed) if packed >= 0 else None)


class Blender:
    """Blender as the rollback uses it: a mode, images, one scene and an Undo history.

    A step pushed in a whole-file mode holds the state, the mode and the
    scene's marker; loading it brings all three back. A step pushed in any
    other mode holds none of them, so stepping onto it changes nothing.
    ``inert`` is an Undo that returns FINISHED and loads nothing.
    """

    def __init__(self, state, *, mode="OBJECT", history=True, inert=False, images=()):
        self.state, self.mode, self.history, self.inert = state, mode, history, inert
        self.calls, self.steps, self.active = [], [], -1
        self.scene = SimpleNamespace(session_uid=7, property_unset=lambda name: setattr(self.scene, name, ""))
        setattr(self.scene, RESTORE, "")
        self.window = SimpleNamespace(scene=self.scene)
        self.data = SimpleNamespace(scenes=[self.scene], images=list(images))
        self.types, self.props = SimpleNamespace(Scene=SimpleNamespace()), SimpleNamespace(StringProperty=lambda **options: options)
        self.ops = SimpleNamespace(ed=SimpleNamespace(undo_push=self.undo_push, undo=self.undo, redo=self.redo),
                                   object=SimpleNamespace(mode_set=self.mode_set))
        outer = self

        class Context:
            mode = property(lambda _self: outer.mode)

            @contextmanager
            def temp_override(self, **_overrides):
                yield

        self.context = Context()

    def undo_push(self, message=""):
        self.calls.append("push " + message)
        if not self.history:
            raise RuntimeError("Operator bpy.ops.ed.undo_push.poll() failed, context is incorrect")
        whole = self.mode in WHOLE_FILE_MODES
        step = {"name": message, "whole": whole}
        if whole:
            step.update(state=self.state, mode=self.mode, marker=getattr(self.scene, RESTORE))
        del self.steps[self.active + 1:]
        self.steps.append(step)
        self.active = len(self.steps) - 1
        return {"FINISHED"}

    def _load(self, index):
        self.active = index
        step = self.steps[index]
        if step["whole"] and not self.inert:
            self.state, self.mode = step["state"], step["mode"]
            setattr(self.scene, RESTORE, step["marker"])

    def undo(self):
        self.calls.append("undo")
        if self.active <= 0:
            return {"CANCELLED"}
        self._load(self.active - 1)
        return {"FINISHED"}

    def redo(self):
        self.calls.append("redo")
        if self.active + 1 >= len(self.steps):
            return {"CANCELLED"}
        self._load(self.active + 1)
        return {"FINISHED"}

    def mode_set(self, mode="OBJECT"):
        self.calls.append("mode " + mode)
        self.mode = mode
        return {"FINISHED"}

    # The executor's side of one script.
    def start(self, before=BEFORE):
        """The person's last step, then the executor's restore point for a script about to run."""
        self.state = before
        if self.history:
            self.undo_push("The person's step")
        self.calls.clear()
        return script_errors.restore_point(self, self.window)

    def stop(self, point, left, *, code=CODE, before=BEFORE, mode="OBJECT"):
        """The script left ``left`` and raised."""
        self.state = left
        return script_errors.failed(self, self.window, code, raised(), "built 1 of 3\n", before, mode,
                                    lambda _scene: {key: self.state[key] for key in ("objects", "count")}, point)


class RestorePointTests(unittest.TestCase):
    def test_it_is_one_step_and_its_marker_is_in_that_step_and_nowhere_else(self):
        blender = Blender(BEFORE)
        point = blender.start()
        self.assertEqual(blender.calls, ["push " + STEP])
        self.assertEqual((blender.steps[-1]["marker"], blender.steps[-1]["state"]), (point[0], BEFORE))
        self.assertEqual(getattr(blender.scene, RESTORE), "", "Unset again: the live scene never carries it")
        self.assertEqual(blender.steps[0]["marker"], "")
        self.assertNotEqual(point[0], Blender(BEFORE).start()[0], "One time only")

    def test_none_is_made_in_a_mode_whose_undo_holds_only_its_own_data(self):
        for mode in ("EDIT_MESH", "EDIT_CURVE", "EDIT_ARMATURE", "SCULPT", "PAINT_TEXTURE", "SCULPT_CURVES"):
            with self.subTest(mode=mode):
                blender = Blender(BEFORE, mode=mode)
                self.assertIsNone(blender.start())
                self.assertEqual(blender.calls, [], "Nothing is pushed there")
        for mode in sorted(WHOLE_FILE_MODES):
            self.assertIsNotNone(Blender(BEFORE, mode=mode).start(), mode)

    def test_without_an_undo_history_there_is_none(self):
        blender = Blender(BEFORE, history=False)
        self.assertIsNone(blender.start())
        self.assertEqual(getattr(blender.scene, RESTORE), "")


class RollbackTests(unittest.TestCase):
    def test_a_raise_reloads_the_file_from_the_restore_point(self):
        blender = Blender(BEFORE)
        result = blender.stop(blender.start(), PARTIAL)
        self.assertEqual(blender.calls, ["push " + STEP, "push " + STOPPED, "undo"])
        self.assertEqual(blender.state, BEFORE, "The removed object and the sun's strength are back")
        self.assertEqual((result["main_data_restored"], result["rolled_back"], result["partial_changes_possible"]), (True, False, True))
        self.assertNotIn("scene_changed", result)
        self.assertIn("restore point the host made immediately before this script", result["rollback"])
        self.assertIn("Image pixels, files, preferences, the window layout", result["rollback"])
        self.assertEqual((result["output"], result["script_error"]), ("built 1 of 3\n", True))
        # Plain about the restore and the next step ("Do not automatically retry" stopped models from fixing a working
        # approach), and never a claim that pixels or files were restored.
        self.assertIn("sending a corrected script is safe", result["recovery"])
        self.assertIn("Image pixels and files it wrote are not restored", result["recovery"])
        self.assertNotIn("retry", result["recovery"])
        self.assertEqual(getattr(blender.scene, RESTORE), "", "The marker does not stay on the restored scene")

    def test_an_undo_that_restores_nothing_is_never_a_rollback(self):
        # Only the sun changed: the object read-back matches whether or not anything was restored.
        blender = Blender(BEFORE, inert=True)
        result = blender.stop(blender.start(), SUN_ONLY)
        self.assertEqual(blender.state, SUN_ONLY)
        self.assertEqual((result["rolled_back"], result["partial_changes_possible"]), (False, True))
        self.assertNotIn("scene_changed", result)
        self.assertIn("did not reach the host's restore point", result["rollback"])
        self.assertIn("still in the scene", result["rollback"])
        self.assertIn("covers objects only", result["rollback"], "The change list would show nothing for a light")
        self.assertIn("still in the scene", result["recovery"])
        self.assertIn("would apply those changes twice", result["recovery"])
        self.assertNotIn("safe", result["recovery"])
        self.assertEqual((blender.active, blender.steps[-1]["name"]), (len(blender.steps) - 1, STOPPED),
                         "Back on the step that holds what the script left")

    def test_the_person_in_edit_or_sculpt_mode_gets_no_rollback_and_is_told(self):
        for mode in ("EDIT_MESH", "SCULPT"):
            with self.subTest(mode=mode):
                blender = Blender(BEFORE, mode=mode)
                result = blender.stop(blender.start(), SUN_ONLY, mode=mode)
                self.assertEqual((blender.calls, blender.state), ([], SUN_ONLY), "No Undo is tried")
                self.assertIs(result["rolled_back"], False)
                self.assertIn("Blender was in %s" % mode, result["rollback"])
                self.assertIn("could make no restore point and undid nothing", result["rollback"])

    def test_a_mode_the_script_entered_is_left_before_the_half_applied_state_is_recorded(self):
        blender = Blender(BEFORE)
        point = blender.start()
        blender.mode = "EDIT_MESH"  # The script entered Edit Mode, then raised.
        result = blender.stop(point, SUN_ONLY)
        self.assertEqual(blender.calls, ["push " + STEP, "mode OBJECT", "push " + STOPPED, "undo"])
        self.assertEqual((result["main_data_restored"], blender.state, blender.mode), (True, BEFORE, "OBJECT"))

    def test_steps_the_script_left_in_between_are_stepped_over(self):
        blender = Blender(BEFORE)
        point = blender.start()
        for name in ("An operator's step", "Another"):
            blender.state = PARTIAL
            blender.undo_push(name)
        result = blender.stop(point, PARTIAL)
        self.assertEqual((result["main_data_restored"], blender.state, blender.calls.count("undo")), (True, BEFORE, 3))

    def test_the_search_for_the_restore_point_is_bounded_and_put_back(self):
        blender = Blender(BEFORE)
        point = blender.start()
        for index in range(MAX_UNDO):
            blender.undo_push("Step %d" % index)
        result = blender.stop(point, PARTIAL)
        self.assertEqual((blender.calls.count("undo"), blender.calls.count("redo")), (MAX_UNDO, MAX_UNDO))
        self.assertEqual((result["rolled_back"], blender.state), (False, PARTIAL))

    def test_a_restore_point_that_is_not_this_scripts_is_not_one(self):
        blender = Blender(BEFORE)
        stale = blender.start()
        blender.undo_push("Something else")
        result = blender.stop((stale[0][::-1], stale[1], stale[2]), PARTIAL)
        self.assertIs(result["rolled_back"], False)

    def test_the_object_read_back_still_has_to_match(self):
        blender = Blender(BEFORE)
        result = blender.stop(blender.start(), PARTIAL, before=dict(BEFORE, count=3))
        self.assertEqual((result["rolled_back"], blender.state), (False, PARTIAL))
        self.assertIn("did not read back as the scene before the script", result["rollback"])
        self.assertEqual(blender.calls[-1], "redo")

    def test_without_an_undo_history_nothing_is_claimed(self):
        blender = Blender(BEFORE, history=False)
        result = blender.stop(blender.start(), PARTIAL)
        self.assertEqual((result["rolled_back"], result["partial_changes_possible"]), (False, True))
        self.assertIn("held no restore point", result["rollback"])
        self.assertNotIn("undo", blender.calls)

    def test_a_script_that_names_the_undo_history_or_a_file_load_is_left_alone(self):
        for code in ("bpy.ops.ed.undo_push(message='half')\nraise ValueError('x')", "bpy.ops.ed.undo()",
                     "bpy.ops.wm.open_mainfile(filepath=path)", "getattr(bpy.ops.ed, 'redo')()",
                     "bpy.ops.wm.read_factory_settings(use_empty=True)", "def broken(:\n"):
            with self.subTest(code=code):
                blender = Blender(BEFORE)
                result = blender.stop(blender.start(), PARTIAL, code=code)
                self.assertEqual(blender.calls, ["push " + STEP], "Only the restore point; no Undo is tried")
                self.assertIs(result["rolled_back"], False)
                self.assertIn("used the Undo history or loaded a file itself", result["rollback"])
        blender = Blender(BEFORE)
        harmless = "# The person can undo this.\nlabel = 'Redo the roof'\nobj.scale = (2, 2, 2)\n" + CODE
        self.assertIs(blender.stop(blender.start(), PARTIAL, code=harmless)["main_data_restored"], True)

    def test_a_script_that_names_image_pixels_or_image_files_is_left_alone(self):
        for code in ("image.pixels[:] = values", "image.pixels.foreach_set(values)", "getattr(image, 'pixels')",
                     "bpy.data.images['Wood'].pack()", "image.reload()", "image.scale(64, 64)", "image.save()",
                     "image.save_render(path)", "bpy.ops.image.save_as(filepath=path)", "bpy.ops.paint.image_paint(stroke=[])",
                     "bpy.ops.object.bake(type='DIFFUSE')"):
            with self.subTest(code=code):
                blender = Blender(BEFORE)
                result = blender.stop(blender.start(), PARTIAL, code=code)
                self.assertEqual(blender.calls, ["push " + STEP])
                self.assertEqual((result["rolled_back"], blender.state), (False, PARTIAL))
                self.assertIn("image pixels or image files, which Blender's Undo does not restore", result["rollback"])

    def test_a_pixel_write_the_script_does_not_name_is_seen_on_the_image(self):
        cases = {"unsaved pixels": lambda images: setattr(images[0], "is_dirty", True),
                 "packed again": lambda images: setattr(images[1].packed_file, "size", 4096),
                 "unsaved pixels gone with their image": lambda images: images.pop(2)}
        for name, write in cases.items():
            with self.subTest(case=name):
                blender = Blender(BEFORE, images=[image(1), image(2, packed=2048), image(3, dirty=True)])
                point = blender.start()
                write(blender.data.images)
                result = blender.stop(point, PARTIAL)
                self.assertEqual(blender.calls, ["push " + STEP], "Seen before any Undo")
                self.assertIs(result["rolled_back"], False)
                self.assertIn("image pixels", result["rollback"])

    def test_images_the_script_did_not_write_do_not_stop_a_rollback(self):
        blender = Blender(BEFORE, images=[image(1), image(2, packed=2048), image(3, dirty=True)])
        point = blender.start()
        blender.data.images.append(image(4, dirty=True))  # One the script made: the restore point has no such image.
        blender.data.images.pop(0)                        # One it removed that had nothing unsaved.
        self.assertIs(blender.stop(point, PARTIAL)["main_data_restored"], True)

    def test_the_error_and_its_location_come_with_either_outcome(self):
        for inert in (False, True):
            blender = Blender(BEFORE, inert=inert)
            result = blender.stop(blender.start(), PARTIAL)
            self.assertEqual(result["main_data_restored"], not inert)
            self.assertEqual(result["error"], "AttributeError: feature_set (line 2)")
            self.assertEqual(result["traceback"][-1].split(":")[0], "line 2, in build")


class FinishedScriptTests(unittest.TestCase):
    def facts(self, start, end=None, point=True):
        blender = Blender(BEFORE, mode=start)
        made = blender.start() if point else None
        blender.mode = end or start
        return (made is not None), script_errors.undo_facts(blender, made, start)

    def test_a_restore_point_and_whole_file_mode_do_not_prove_later_undo_coverage(self):
        expected = {"undoable": False, "main_data_undoable": False, "undo_scope": "MAIN_DATA",
                    "image_pixels_undoable": False, "undo_available": False, "undo": script_errors.UNDO_UNAVAILABLE}
        self.assertEqual(self.facts("OBJECT"), (True, expected))
        self.assertEqual(self.facts("POSE", "OBJECT"), (True, expected))

    def test_it_is_not_where_undo_holds_only_a_modes_own_data_and_the_result_names_the_mode(self):
        for start, end, named in (("EDIT_MESH", None, "EDIT_MESH"), ("SCULPT", None, "SCULPT"),
                                  ("OBJECT", "EDIT_MESH", "EDIT_MESH"), ("EDIT_MESH", "OBJECT", "EDIT_MESH")):
            with self.subTest(start=start, end=end):
                facts = self.facts(start, end)[1]
                self.assertIs(facts["undoable"], False)
                self.assertIn("Blender is in %s, where Undo holds only that mode's own data" % named, facts["undo"])
        self.assertIs(self.facts("OBJECT", point=False)[1]["undoable"], False, "No restore point, no promise")
        self.assertIs(self.facts("OBJECT", point=False)[1]["main_data_undoable"], False)


class ApplyOperatorTests(unittest.TestCase):
    """A finished script is one Undo step because the operator that applies it replaces the restore point."""

    def test_only_a_script_that_gets_a_restore_point_is_applied_by_the_grouped_operator(self):
        for mode in sorted(WHOLE_FILE_MODES):
            self.assertEqual(script_errors.apply_operator(Blender(BEFORE, mode=mode), None, "angelo_execute_python"), APPLY_SCRIPT)
        for mode in ("EDIT_MESH", "SCULPT", "EDIT_CURVE"):
            self.assertEqual(script_errors.apply_operator(Blender(BEFORE, mode=mode), None, "angelo_execute_python"), APPLY)
        for name in ("angelo_scene_program", "angelo_set_transform", "angelo_create_cube"):
            self.assertEqual(script_errors.apply_operator(Blender(BEFORE), None, name), APPLY)

    def test_the_grouped_operator_is_a_sibling_with_the_same_execute_and_the_steps_name(self):
        class Operator:
            pass

        class Apply(Operator):
            bl_idname, bl_label, bl_options = "angelo." + APPLY, STEP, {"UNDO"}
            call_id: str
            session_id: str

            def execute(self, context):
                return {"FINISHED"}

        twin = script_errors.grouped(Apply)
        self.assertEqual((twin.bl_idname, twin.bl_label, twin.bl_options), ("angelo." + APPLY_SCRIPT, STEP, {"UNDO_GROUPED"}))
        self.assertEqual(twin.__bases__, (Operator,), "Not a subclass of the registered operator")
        self.assertIs(twin.execute, Apply.execute)
        self.assertEqual(twin.__annotations__, Apply.__annotations__)
        self.assertIsNot(twin.__annotations__, Apply.__annotations__)

    def test_the_add_on_registers_and_dispatches_both_under_the_steps_name(self):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text(encoding="utf-8"))
        apply = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "ANGELO_OT_apply_approved_tool")
        values = {target.id: ast.literal_eval(statement.value) for statement in apply.body if isinstance(statement, ast.Assign)
                  for target in statement.targets}
        self.assertEqual((values["bl_idname"], values["bl_label"], values["bl_options"]), ("angelo." + APPLY, STEP, {"UNDO"}))
        calls = {ast.unparse(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
        self.assertEqual(ast.unparse(calls["script_errors.grouped"].args[0]), "ANGELO_OT_apply_approved_tool")
        self.assertEqual([ast.unparse(argument) for argument in calls["script_errors.apply_operator"].args],
                         ["bpy", "main_window()", "call.name"])
        self.assertNotIn("bpy.ops.angelo.apply_approved_tool", calls, "No call applies a script past that choice")


class HistoryAndScopeTests(unittest.TestCase):
    """The claim survives only what Undo can be trusted for (script_watch)."""

    def test_a_script_that_replays_its_restore_point_cannot_forge_the_claim(self):
        blender = Blender(BEFORE)
        point = blender.start()
        # The script: Undo and Redo bring the marked state back, then it changes the sun and pushes its own step.
        blender.undo()
        blender.redo()
        blender.state = SUN_ONLY
        blender.undo_push("mine")
        result = blender.stop(point, SUN_ONLY)
        self.assertFalse(result["main_data_restored"])
        self.assertTrue(result["rollback"].startswith(script_errors.OWN_HISTORY))
        self.assertEqual(blender.state, SUN_ONLY, "Nothing was undone and nothing is claimed")

    def test_a_watched_history_move_drops_the_claim_even_when_the_marker_was_cleared(self):
        blender = Blender(BEFORE)
        point = blender.start()
        blender.state = PARTIAL
        claimed, why = script_errors.undo_to(blender, blender.window, CODE, point, BEFORE, "OBJECT",
                                             lambda _scene: {key: blender.state[key] for key in ("objects", "count")},
                                             moved=True)
        self.assertEqual((claimed, why), (False, script_errors.OWN_HISTORY))
        self.assertNotIn("undo", blender.calls)

    def test_changed_linked_data_drops_the_claim_and_puts_back_what_the_script_left(self):
        blender = Blender(BEFORE)
        point = blender.start()
        original = script_errors.linked_state
        script_errors.linked_state = lambda _bpy: {"brush edited by the script": ("changed", False)}
        try:
            result = blender.stop(point, PARTIAL)
        finally:
            script_errors.linked_state = original
        self.assertFalse(result["main_data_restored"])
        self.assertTrue(result["rollback"].startswith(script_errors.LINKED))
        self.assertEqual(blender.state, PARTIAL, "Redo put back the half-applied state")

    def test_linked_ids_must_read_the_same_and_new_ones_be_pristine_assets(self):
        from angelo_agent.script_watch import linked_unchanged
        before = {"linked object": ("a", False), "brush": ("b", True)}
        self.assertTrue(linked_unchanged(before, dict(before)))
        self.assertTrue(linked_unchanged(before, dict(before, **{"brush a paint mode linked": ("c", True)})))
        for after in (dict(before, brush=("edited", False)), {"brush": ("b", True)},
                      dict(before, **{"an edited new brush": ("c", False)}),
                      dict(before, **{"a new linked object": ("d", False)})):
            with self.subTest(after=after):
                self.assertFalse(linked_unchanged(before, after))
        for missing in ((None, before), (before, None), (None, None)):
            self.assertFalse(linked_unchanged(*missing))  # Too much to read is never proof.

    def test_a_restored_result_names_what_main_data_excludes(self):
        blender = Blender(BEFORE)
        result = blender.stop(blender.start(), PARTIAL)
        self.assertTrue(result["main_data_restored"])
        self.assertEqual(result["rollback_scope_excludes"], ["window_manager", "workspaces", "screens"])
        self.assertIn(script_errors.INTERFACE_NOTE, result["rollback"])

    def test_exit_and_interrupt_are_script_failures(self):
        self.assertTrue(all(issubclass(kind, BaseException) for kind in script_errors.SCRIPT_FAILURES))
        for error, name in ((SystemExit(1), "SystemExit: 1"), (KeyboardInterrupt(), "KeyboardInterrupt")):
            with self.subTest(error=name):
                self.assertIsInstance(error, script_errors.SCRIPT_FAILURES)
                self.assertTrue(script_errors.describe(error, "")["error"].startswith(name))
        self.assertNotIsInstance(GeneratorExit(), script_errors.SCRIPT_FAILURES)


class HistoryWatchTests(unittest.TestCase):
    def handlers(self):
        names = ("undo_pre", "undo_post", "redo_pre", "redo_post", "load_pre", "load_post")
        handlers = SimpleNamespace(persistent=lambda function: function, **{name: [] for name in names})
        return SimpleNamespace(app=SimpleNamespace(handlers=handlers)), handlers

    def test_undo_redo_and_loads_are_noticed_and_no_handler_outlives_the_script(self):
        from angelo_agent.script_watch import HISTORY_HANDLERS, HistoryWatch
        for name in HISTORY_HANDLERS:
            with self.subTest(handler=name):
                bpy, handlers = self.handlers()
                watch = HistoryWatch(bpy).start()
                for function in list(getattr(handlers, name)):
                    function(None)
                self.assertTrue(watch.stop())
                self.assertTrue(all(getattr(handlers, other) == [] for other in HISTORY_HANDLERS))

    def test_a_quiet_script_is_not_flagged_and_a_removed_handler_is(self):
        from angelo_agent.script_watch import HistoryWatch
        bpy, handlers = self.handlers()
        self.assertFalse(HistoryWatch(bpy).start().stop())
        watch = HistoryWatch(bpy).start()
        handlers.undo_pre.clear()  # The script took the watch away: what it hid is unknown.
        self.assertTrue(watch.stop())
        self.assertTrue(watch.stop(), "idempotent")


if __name__ == "__main__":
    unittest.main()
