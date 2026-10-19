# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import ast
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import input_policy
from angelo_agent.input_policy import operator_identity, active_interactions, interaction_active, follows_pointer, moves_focus
from angelo_agent.program_jobs import ProgramJob, PreviewStore, WriterLease


class CanvasInputPolicyTests(unittest.TestCase):
    def test_passive_canvas_handlers_are_not_foreground_tools(self):
        operators = [SimpleNamespace(bl_idname=n) for n in ("ANGELO_OT_chat_input", "ANGELO_OT_studio_input")]
        self.assertEqual(active_interactions(SimpleNamespace(modal_operators=operators)), [])
        self.assertFalse(interaction_active(SimpleNamespace(modal_operators=operators)))

    def test_view_navigation_keys_follow_the_pointer_even_while_the_chat_has_focus(self):
        for key in ("NUMPAD_0", "NUMPAD_1", "NUMPAD_3", "NUMPAD_7", "NUMPAD_PERIOD", "NUMPAD_PLUS", "NUMPAD_MINUS",
                    "NUMPAD_SLASH", "HOME", "ACCENT_GRAVE", "NDOF_BUTTON_FRONT", "NDOF_MOTION"):
            self.assertTrue(follows_pointer(key), key)
        # Typing and editing keys stay with a focused chat; Numpad Enter sends the message.
        for key in ("G", "R", "S", "X", "A", "TAB", "DEL", "BACK_SPACE", "RET", "NUMPAD_ENTER", "ONE", "Z"):
            self.assertFalse(follows_pointer(key), key)

    def test_pointer_use_elsewhere_moves_keyboard_focus_out_of_the_chat(self):
        for kind, value in (("MIDDLEMOUSE", "PRESS"), ("RIGHTMOUSE", "PRESS"), ("WHEELUPMOUSE", "PRESS"),
                            ("WHEELDOWNMOUSE", "PRESS"), ("TRACKPADPAN", "NOTHING"), ("TRACKPADZOOM", "NOTHING"),
                            ("MOUSEROTATE", "NOTHING"), ("NDOF_MOTION", "NOTHING")):
            self.assertTrue(moves_focus(kind, value), kind)
        for kind, value in (("MIDDLEMOUSE", "RELEASE"), ("MOUSEMOVE", "NOTHING"), ("G", "PRESS"), ("TIMER", "NOTHING")):
            self.assertFalse(moves_focus(kind, value), kind)

    def test_director_adds_no_input_handler_of_its_own(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/director.py").read_text()
        self.assertNotIn("modal_handler_add", source)
        self.assertNotIn("RUNNING_MODAL", source)
        self.assertNotIn("director_input", (ROOT / "scripts/startup/angelo_default_layout.py").read_text())

    def test_native_transform_and_property_drags_get_priority(self):
        for name in ("TRANSFORM_OT_resize", "transform.translate", "SCREEN_OT_area_move", "WM_OT_context_modal_mouse"):
            window = SimpleNamespace(modal_operators=[SimpleNamespace(bl_idname="angelo.studio_input"), SimpleNamespace(bl_idname=name)])
            self.assertTrue(interaction_active(window))
            self.assertEqual(len(active_interactions(window)), 1)

    def test_unknown_modal_tools_fail_closed_and_missing_collection_is_compatible(self):
        self.assertTrue(interaction_active(SimpleNamespace(modal_operators=[SimpleNamespace()])))
        self.assertFalse(interaction_active(SimpleNamespace()))
        operator = SimpleNamespace(bl_rna=SimpleNamespace(identifier="TRANSFORM_OT_rotate"))
        self.assertEqual(operator_identity(operator), "transform.rotate")

    def test_both_input_handlers_yield_before_focus_or_key_filtering(self):
        for name in ("studio.py", "chat_view.py"):
            source = (ROOT / "scripts/addons_core/angelo_agent" / name).read_text()
            ast.parse(source)
            modal = source.split("    def modal(self, context, event):", 1)[1]
            guard = modal.index("if interaction_active(window):")
            self.assertLess(guard, modal.index("mouse"))
            self.assertIn("return {'PASS_THROUGH'}", modal[guard:guard + 170])
            self.assertIn("self.input_generation = _input_generation", source)
            self.assertIn('getattr(self, "input_generation", _input_generation) != _input_generation', source)

    def test_programmatic_startup_and_editor_changes_wait_for_native_interactions(self):
        launcher = (ROOT / "scripts/angelo/launch_studio.py").read_text()
        self.assertIn("if interaction_active(window):\n        return .2", launcher)
        startup = (ROOT / "scripts/startup/angelo_default_layout.py").read_text()
        self.assertIn("if interaction_active(window):", startup)
        studio = (ROOT / "scripts/addons_core/angelo_agent/studio.py").read_text()
        setup = studio.split("def setup_area(window, area):", 1)[1].split("def banner", 1)[0]
        self.assertLess(setup.index("interaction_active(window)"), setup.index("select_console(window"))
        self.assertNotIn("cursor_warp", studio)
        self.assertNotIn("GRAB_CURSOR", studio)



class ProgramWaitTests(unittest.TestCase):
    """A staged program waits, doing no stage work, while the backend says its next operation must
    (an operator_run transform during an artist's modal tool, whose call would take over the window's status
    bar and Blender's global transform state); it continues once the tool ends."""

    def test_a_waiting_operation_runs_only_after_the_tool_ends(self):
        from test_native_program_jobs import Backend, program
        backend, waiting = Backend(), [True]
        backend.operation_waits = lambda operation: waiting[0] and operation["kind"] == "transform"
        job = ProgramJob(backend, program(), owner="chat-a", writer=WriterLease(), previews=PreviewStore())
        results = [job.poll() for _ in range(8)]
        self.assertNotIn("apply", backend.calls)
        self.assertTrue(results[-1]["waiting"] and not results[-1]["final"] and not job.ready_to_commit)
        self.assertEqual(results[-1]["progress"]["operation_index"], 0)
        waiting[0] = False
        for _ in range(20):
            if job.poll().get("ready_to_commit"):
                break
        self.assertTrue(job.ready_to_commit)
        self.assertEqual(backend.calls.count("apply"), 1)
        job.cancel()

    def test_native_backend_waits_only_for_transform_operators_during_any_windows_tool(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/native_programs.py").read_text()
        waits = source.split("def operation_waits(self, operation):", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('needs_window(operation["parameters"]["operator"])', waits)
        self.assertIn("interaction_running()", waits)
        running = source.split("def interaction_running():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("any(interaction_active(window) for window in", running)


if __name__ == '__main__':
    unittest.main()


class RenderOperatorTests(unittest.TestCase):
    """Blender's render operator stays on the modal stack until the next event after its job ended."""

    def window(self, *names):
        return SimpleNamespace(modal_operators=[SimpleNamespace(bl_idname=name) for name in names])

    def blender(self, rendering):
        bpy = ModuleType("bpy")
        bpy.app = SimpleNamespace(is_job_running=lambda kind: rendering and kind == "RENDER")
        return patch.dict(sys.modules, {"bpy": bpy})

    def test_a_running_render_is_an_interaction_and_a_finished_one_is_not(self):
        with self.blender(rendering=True):
            self.assertTrue(input_policy.interaction_active(self.window("RENDER_OT_render")))
        with self.blender(rendering=False):
            self.assertFalse(input_policy.interaction_active(self.window("RENDER_OT_render")))
            self.assertEqual(input_policy.active_interactions(self.window("RENDER_OT_render", "TRANSFORM_OT_translate")),
                             ["transform.translate"], "any other modal tool still counts")

    def test_outside_blender_a_render_operator_still_counts(self):
        self.assertTrue(input_policy.interaction_active(self.window("RENDER_OT_render")))
