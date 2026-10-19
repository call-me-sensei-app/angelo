# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After every look and strip the document is compared with its state before, and the result says so honestly.

Review P0: a look's own setting writes dirty the depsgraph, so the render ran
every add-on ``depsgraph_update_*`` handler, unapproved and even in Read-only,
and one changed the world's colour while the look said nothing changed. Per
the mode-aware rule those handlers refuse a look in Read-only
and Ask and run, named, in Auto; after every look the document is compared
(``view_scene_check``) and a result never says the scene is unchanged when that
is not known.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LookCase  # noqa: E402  (puts the add-on on the path)
from rendered_view_fakes import Collection, Struct  # noqa: E402
from angelo_agent import view_scene_check as check  # noqa: E402

IDENTITY = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
MOVED = ((1.0, 0.0, 0.0, 2.0),) + IDENTITY[1:]


class Block:
    """A data-block: identity-hashed, with native settings (``Struct``) and custom properties."""

    def __init__(self, name, uid, **values):
        self.name, self.session_uid, self.settings, self.custom = name, uid, Struct(**values), {}

    @property
    def bl_rna(self):
        return self.settings.bl_rna

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "settings"), name)

    def keys(self):
        return list(self.custom)

    def __getitem__(self, key):
        return self.custom[key]


class Document(unittest.TestCase):
    """Worlds are read a collection at a time (``bpy.data`` names their struct); the rest one by one."""

    def setUp(self):
        self.world = Block("World", 2, color=(0.05, 0.05, 0.05), use_nodes=True)
        self.cube = SimpleNamespace(name="Cube", session_uid=3, data=None, parent=None, material_slots=[],
                                    modifiers=[], hide_render=False, matrix_world=IDENTITY)
        self.scene = Block("Scene", 1, frame_current=1)
        self.scene.settings.values.update(objects=Collection([self.cube]))
        self.scene.settings.values["render"] = Struct(resolution_x=1920, fps=24)
        rna = SimpleNamespace(properties={"worlds": SimpleNamespace(fixed_type=self.world.bl_rna)})
        self.data = SimpleNamespace(worlds=Collection([self.world]), scenes=Collection([self.scene]),
                                    images=Collection(), objects=Collection([self.cube]), bl_rna=rna)
        self.updates = []
        layer = SimpleNamespace(update=lambda: [update() for update in self.updates])
        self.window = SimpleNamespace(scene=self.scene, view_layer=layer)
        bpy = ModuleType("bpy")
        bpy.data = self.data
        self.modules = patch.dict(sys.modules, {"bpy": bpy})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def checked(self, change=None, *, deep=False, own=()):
        scene_check = check.SceneCheck(self.window, deep=deep)
        if change:
            change()
        return scene_check.finish(own)


class CheckTests(Document):
    def test_an_untouched_document_compares_equal(self):
        self.assertEqual(self.checked(deep=True), {"changes": [], "complete": True})

    def test_what_every_check_reads(self):
        def change():
            self.data.images.append(SimpleNamespace(name="Planted", session_uid=9, type="IMAGE"))
            self.scene.render.values["fps"] = 30
            self.cube.matrix_world = MOVED
            self.scene.custom["note"] = "added"
        self.assertEqual(self.checked(change)["changes"],
                         ["added image Planted", "scene setting ['note']", "scene setting render.fps", "object Cube"])

    def test_the_views_own_settings_and_restore_tag_are_left_out(self):
        def change():
            self.scene.render.values["resolution_x"] = 64  # The look's own, put back and reported by its restore.
            self.scene.custom[check.TAG] = "{}"
            self.data.images.append(SimpleNamespace(name="Render Result", session_uid=8, type="RENDER_RESULT"))
        self.assertEqual(self.checked(change, own=["render.resolution_x"])["changes"], [])

    def test_a_deep_check_reads_every_data_block_and_runs_the_pending_update_first(self):
        def handler_on_update():  # What an add-on's depsgraph handler does when the update runs.
            self.world.settings.values["color"] = (1.0, 0.0, 0.0)
        self.updates.append(handler_on_update)
        self.assertEqual(self.checked(deep=True)["changes"], ["data-block World"])
        self.world.settings.values["color"] = (0.05, 0.05, 0.05)
        self.assertEqual(self.checked(deep=False)["changes"], [], "only add-on Python asks for the deep reading")

    def test_a_check_past_its_bounds_says_it_is_partial(self):
        with patch.object(check, "MAX_READS", 1):
            self.assertFalse(self.checked(deep=True)["complete"])

    def test_a_nan_read_twice_is_no_change(self):
        self.world.settings.values["color"] = (float("nan"), 0.0, 0.0)  # Read a collection at a time.
        self.scene.render.values["fps"] = float("nan")                     # Read one by one.
        self.assertEqual(self.checked(deep=True)["changes"], [])


class ScopeTests(Document):
    """Without add-on Python the check is the cheap reading; with it, links, positions and every data-block."""

    def test_without_add_on_python_nothing_is_evaluated_early_and_links_are_not_read(self):
        updates = []
        self.updates.append(lambda: updates.append("update"))

        def relink():  # Only Python or the person can relink an object while a view runs.
            self.cube.parent = SimpleNamespace(session_uid=99)
        self.assertEqual((self.checked(relink)["changes"], updates), ([], []))
        self.cube.parent = None
        self.assertEqual((self.checked(relink, deep=True)["changes"], updates), (["object Cube"], ["update"]))

    def test_an_array_without_a_fixed_length_and_add_on_settings_are_never_read(self):
        def prop(name, kind, **extra):
            return SimpleNamespace(identifier=name, type=kind, is_readonly=False, is_runtime=False, **extra)
        rna = SimpleNamespace(identifier="StandInImage", properties=[
            prop("pixels", "FLOAT", is_array=True, array_length=0), prop("generated_color", "FLOAT", is_array=True,
                                                                            array_length=4),
            prop("alpha_mode", "ENUM"), prop("filepath", "STRING"),
            prop("addon_settings", "POINTER", fixed_type=SimpleNamespace(identifier="AddonGroup", base=None)),
            SimpleNamespace(identifier="addon_flag", type="BOOLEAN", is_readonly=False, is_runtime=True)])
        self.assertEqual(check._plan(rna), ((("generated_color", "f", 4), ("alpha_mode", "i", 1)),
                                            (("filepath", False),)))

    def test_mesh_positions_are_summed_once_per_mesh_and_within_the_budget(self):
        points = Collection(SimpleNamespace(vector=(float(index), 0.0, 0.0)) for index in range(4))
        mesh = SimpleNamespace(session_uid=20, vertices=points, attributes={"position": SimpleNamespace(data=points)})
        twin = SimpleNamespace(name="Twin", session_uid=4, type="MESH", data=mesh, parent=None, material_slots=[],
                               modifiers=[], hide_render=False, matrix_world=IDENTITY)
        self.cube.type, self.cube.data = "MESH", mesh
        self.scene.objects.append(twin)
        self.data.objects.append(twin)
        summed = []
        positions = check._positions

        def counted(read):
            summed.append(read.session_uid)
            return positions(read)
        with patch.object(check, "_positions", counted):
            def move():
                points[0].vector = (9.0, 0.0, 0.0)
            self.assertEqual(self.checked(move, deep=True), {"changes": ["object Cube", "object Twin"],
                                                             "complete": True})
        self.assertEqual(summed, [20, 20], "one sum of the shared mesh per reading")
        with patch.object(check, "MAX_VERTICES", 3):
            self.assertFalse(self.checked(deep=True)["complete"])


class ClaimTests(unittest.TestCase):
    def base(self):
        return {"success": True, "read_only": True, "scene_changed": False, "uncertain": False, "uncertainty": [],
                "side_effects": []}

    def test_a_change_is_said(self):
        result = check.claims(self.base(), {"changes": ["data-block World"], "complete": True}, ["addon.on_update"])
        self.assertIs(result["scene_changed"], True)
        self.assertEqual(result["changed_during_view"], ["data-block World"])
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["scene_changed_during_view"])
        self.assertEqual([entry["code"] for entry in result["side_effects"]], ["extension_handlers_ran"])
        self.assertIn("addon.on_update", result["side_effects"][0]["detail"])

    def test_add_on_python_and_no_difference_is_not_unchanged(self):
        result = check.claims(self.base(), {"changes": [], "complete": True}, ["addon.on_update"])
        self.assertIsNone(result["scene_changed"])
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["scene_change_unverified"])
        self.assertTrue(result["uncertain"])

    def test_nothing_ran_and_nothing_changed_keeps_the_claim(self):
        self.assertEqual(check.claims(self.base(), {"changes": [], "complete": True}), self.base())
        partial = check.claims(self.base(), {"changes": [], "complete": False})
        self.assertIs(partial["scene_changed"], False)
        self.assertEqual([entry["code"] for entry in partial["uncertainty"]], ["scene_check_partial"])


def on_update(*_args):  # An add-on's depsgraph handler: its code lies outside the shipped scripts.
    return None


class LookModeTests(LookCase):
    def setUp(self):
        super().setUp()
        self.blender.bpy.app.handlers = SimpleNamespace(depsgraph_update_post=[on_update])

    def mode(self, approval):
        self.controller.state["options"] = {"approval": approval}

    def test_read_only_and_ask_refuse_before_anything_is_written(self):
        for approval in ("READ_ONLY", "ASK"):
            with self.subTest(approval=approval):
                self.mode(approval)
                before = self.blender.scene.snapshot()
                with self.assertRaisesRegex(RuntimeError, "on_update \\(depsgraph_update_post\\)") as caught:
                    self.begin()
                self.assertEqual("Read-only" in str(caught.exception), approval == "READ_ONLY")
                self.assertEqual((self.blender.invoked, self.blender.scene.snapshot()), ([], before))
                self.assertEqual(self.reference_files(), [])

    def test_auto_runs_the_look_names_the_handler_and_reports_what_changed(self):
        self.mode("AUTO_BOUNDED")
        pending = self.begin()
        self.blender.scene.view_settings.exposure = 3.0  # What the handler did while the look rendered.
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.end()
        self.manager.poll()
        result = pending.poll()
        self.assertTrue(result["success"])
        self.assertIs(result["scene_changed"], True)
        self.assertEqual(result["changed_during_view"], ["scene setting view_settings.exposure"])
        self.assertIn("on_update", result["side_effects"][-1]["detail"])
        self.assertIn("scene_changed_during_view", [entry["code"] for entry in result["uncertainty"]])

    def test_auto_with_no_change_found_is_unverified_not_unchanged(self):
        self.mode("AUTO_BOUNDED")
        _pending, result = self.run_look()
        self.assertIsNone(result["scene_changed"])
        self.assertIn("scene_change_unverified", [entry["code"] for entry in result["uncertainty"]])

    def test_a_stopped_turn_does_not_claim_the_scene_untouched(self):
        self.mode("AUTO_BOUNDED")
        pending = self.begin()
        self.assertIsNone(pending.stopped_result["scene_changed"])
        self.assertNotIn("nothing in the scene changed", pending.stopped_result["error"])
        pending.cancel()
        self.blender.end(written=False)
        self.manager.poll()


class MessageBusLookTests(LookCase):
    """A look's own setting writes notify ``bpy.msgbus`` subscribers: like depsgraph handlers, by approval mode."""

    def setUp(self):
        super().setUp()
        self.blender.msgbus.append(("RenderSettings", "resolution_x", on_update))

    def mode(self, approval):
        self.controller.state["options"] = {"approval": approval}

    def test_read_only_and_ask_refuse_before_anything_is_written(self):
        for approval in ("READ_ONLY", "ASK"):
            with self.subTest(approval=approval):
                self.mode(approval)
                before = self.blender.scene.snapshot()
                with self.assertRaisesRegex(RuntimeError, r"on_update \(message bus: RenderSettings.resolution_x\)"):
                    self.begin()
                self.assertEqual((self.blender.invoked, self.blender.scene.snapshot()), ([], before))

    def test_auto_runs_the_look_names_them_and_never_says_unchanged(self):
        self.mode("AUTO_BOUNDED")
        _pending, result = self.run_look()
        self.assertTrue(result["success"])
        self.assertIsNone(result["scene_changed"], "they run again after the look put its settings back")
        self.assertIn("message bus: RenderSettings.resolution_x", result["side_effects"][-1]["detail"])

    def test_a_subscriber_to_a_setting_the_look_leaves_alone_does_not_count(self):
        self.blender.msgbus[:] = [("RenderSettings", "engine", on_update), ("View3DShading", None, on_update)]
        self.mode("READ_ONLY")
        _pending, result = self.run_look()
        self.assertIs(result["scene_changed"], False)

    def test_a_build_that_cannot_list_them_runs_the_look_and_says_so(self):
        del self.blender.native._angelo_jobs.msgbus_subscribers
        self.mode("ASK")
        _pending, result = self.run_look()
        self.assertIsNone(result["scene_changed"])
        self.assertIn("message_bus_unknown", [entry["code"] for entry in result["uncertainty"]])


class OrdinaryLookTests(LookCase):
    def test_an_ordinary_look_still_says_unchanged_and_reports_a_change_it_finds(self):
        _pending, result = self.run_look()
        self.assertIs(result["scene_changed"], False)
        self.assertNotIn("changed_during_view", result)
        pending = self.begin()
        self.blender.scene.view_settings.look = "Punchy"  # The person edits while it renders.
        self.blender.progress = 1.0
        self.manager.poll()
        self.blender.end()
        self.manager.poll()
        result = pending.poll()
        self.assertIs(result["scene_changed"], True)
        self.assertEqual(result["changed_during_view"], ["scene setting view_settings.look"])


if __name__ == "__main__":
    unittest.main()
