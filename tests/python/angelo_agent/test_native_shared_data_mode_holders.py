# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Data another object holds in a mode is never bound (``program_admission``; doc/angelo/agent_execution.rst,
"Input admission").

Linked duplicates share one mesh. While one of them is in Edit Mode, Blender evaluates every user of the mesh from
the editing object's buffer, and with a dynamic-topology (or any) Sculpt or paint session the person is changing the
data in a buffer the original arrays do not have yet; a revision, a stage copy and a commit all read those original
arrays. So:

* at bind, an input (read or written, declared or an undeclared dependency) whose data another object holds in such a
  mode is refused, naming that object; data with one user is never scanned for others, shared data once per fence;
* the input's own mode and another object's are ONE refusal (``mode_refusal``): the same code (UNSUPPORTED), text and
  details (``handles``, ``held_by``) at bind, at the fresh precondition before a commit and at verify, which repeats
  it before reading any revision; its words hold at each ("nothing was applied") and it never suggests a script, which
  reads the same original data and whose writes an edit buffer overwrites later;
* what no mode change can fix (a linked input) is refused before the mode.

Host stand-ins; ``test_blender_program_read_inputs.py`` proves both halves in the built app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
from angelo_agent import exact_fence, program_admission  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402

MODE_NAMES = {"OBJECT": "Object Mode", "POSE": "Pose Mode", "EDIT": "Edit Mode", "SCULPT": "Sculpt Mode",
              "WEIGHT_PAINT": "Weight Paint", "VERTEX_PAINT": "Vertex Paint"}
RETURN = "Ask the person to return %r to Object Mode when they are ready, then observe again"


class Data(SimpleNamespace):
    def __init__(self, name, users=1, editing=False):
        super().__init__(name=name, users=users, is_editmode=editing, vertices=[], edges=[], polygons=[])

    def as_pointer(self):
        return id(self)


class Obj(SimpleNamespace):
    bl_rna = SimpleNamespace(properties={"mode": SimpleNamespace(enum_items={
        key: SimpleNamespace(name=value) for key, value in MODE_NAMES.items()})})

    def __init__(self, name, mode="OBJECT", data=None, linked=False, kind="MESH"):
        super().__init__(name=name, mode=mode, data=data, library="lib.blend" if linked else None,
                         is_editable=not linked, instance_collection=None, type=kind)


class Scene:
    """Stands in for ``bpy`` with ``data.objects``, counting each scan of it."""

    def __init__(self, *objects):
        self.scans = 0
        owner = self

        class Objects(list):
            def __iter__(self):
                owner.scans += 1
                return list.__iter__(self)
        self.data = SimpleNamespace(objects=Objects(objects))


def admit(obj, scene=None, dependency=False):
    scene = scene or Scene(obj)
    with patch.object(program_admission, "_bpy", lambda: scene), \
         patch.object(program_admission, "assert_supported_custom_links", lambda _obj: None):
        return program_admission.admit_input(obj, (10, 10, 10), dependency=dependency, handle="o3")


def still(obj, scene=None, dependency=False):
    scene = scene or Scene(obj)
    with patch.object(program_admission, "_bpy", lambda: scene):
        return program_admission.assert_still_admitted(obj, "o3", dependency)


class HeldAtBindTests(unittest.TestCase):
    def tearDown(self):
        exact_fence.end()

    def test_a_mesh_another_object_edits_is_refused_naming_it(self):
        mesh = Data("Shared mesh", users=2, editing=True)
        a, b = Obj("A", data=mesh), Obj("B", "EDIT", mesh)
        with self.assertRaises(ProgramError) as caught:
            admit(a, Scene(a, b))
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertEqual(caught.exception.details, {"handles": ["o3"], "held_by": "B"})
        self.assertEqual(str(caught.exception), "'A' uses data 'Shared mesh' that 'B' has open in Edit Mode; typed "
                         "programs never bind data another object is working on, so nothing was applied. "
                         + RETURN % "B")
        self.assertNotIn("angelo_execute_python", str(caught.exception))

    def test_a_dependency_is_named_as_one(self):
        mesh = Data("Shared mesh", users=2, editing=True)
        a, b = Obj("A", data=mesh), Obj("B", "EDIT", mesh)
        with self.assertRaises(ProgramError) as caught:
            admit(a, Scene(a, b), dependency=True)
        self.assertTrue(str(caught.exception).startswith("'A', which this program's objects depend on, uses data "
                                                         "'Shared mesh' that 'B' has open in Edit Mode"))

    def test_shared_data_in_a_sculpt_or_paint_session_is_refused(self):
        for mode in ("SCULPT", "WEIGHT_PAINT", "VERTEX_PAINT", "TEXTURE_PAINT", "PARTICLE_EDIT"):
            with self.subTest(mode=mode):
                exact_fence.end()
                mesh = Data("Body mesh", users=2)
                a, b = Obj("A", data=mesh), Obj("B", mode, mesh)
                with self.assertRaises(ProgramError) as caught:
                    admit(a, Scene(a, b))
                self.assertEqual(str(caught.exception), "'A' uses data 'Body mesh' that 'B' has open in %s; typed "
                                 "programs never bind data another object is working on, so nothing was applied. %s"
                                 % (MODE_NAMES.get(mode, mode.replace("_", " ").title()), RETURN % "B"))

    def test_a_rig_whose_armature_is_edited_through_another_object_is_refused(self):
        armature = Data("Armature", users=2, editing=True)
        rig, twin = Obj("Rig", data=armature, kind="ARMATURE"), Obj("Rig twin", "EDIT", armature, kind="ARMATURE")
        with self.assertRaisesRegex(ProgramError, "'Rig twin' has open in Edit Mode"):
            admit(rig, Scene(rig, twin))

    def test_shared_data_in_object_or_pose_mode_is_admitted_with_one_scan_per_fence(self):
        mesh = Data("Shared", users=3)
        a, b, c = Obj("A", data=mesh), Obj("B", data=mesh), Obj("C", "POSE", Data("Rig", users=2), kind="ARMATURE")
        scene = Scene(a, b, c)
        with exact_fence.fence():
            for obj in (a, b):
                self.assertEqual(admit(obj, scene), 0)
        self.assertEqual(scene.scans, 1)

    def test_single_user_data_is_never_scanned(self):
        obj = Obj("Solo", data=Data("Solo mesh"))
        scene = Scene(obj)
        admit(obj, scene)
        self.assertEqual(scene.scans, 0)
        self.assertEqual(admit(Obj("Empty", data=None, kind="EMPTY")), 0)

    def test_what_no_mode_change_can_fix_comes_first(self):
        with self.assertRaises(ProgramError) as caught:
            admit(Obj("Linked", "POSE", Data("Linked rig"), linked=True, kind="ARMATURE"))
        self.assertEqual(str(caught.exception), "Program write targets must be local editable objects")

    def test_an_input_s_own_mode_is_named_and_a_dependency_as_one(self):
        with self.assertRaises(ProgramError) as caught:
            admit(Obj("Rig", "POSE", Data("Rig"), kind="ARMATURE"), dependency=True)
        self.assertEqual(caught.exception.details, {"handles": ["o3"], "held_by": "Rig"})
        self.assertEqual(str(caught.exception), "'Rig', which this program's objects depend on, is in Pose Mode; "
                         "typed programs bind objects only in Object Mode, so nothing was applied. " + RETURN % "Rig")

    def test_edit_data_with_no_editing_object_found_names_no_holder(self):
        obj = Obj("A", data=Data("Orphan edit", editing=True))
        with self.assertRaises(ProgramError) as caught:
            admit(obj)
        self.assertEqual(caught.exception.details, {"handles": ["o3"], "held_by": None})
        self.assertEqual(str(caught.exception), "'A' uses data 'Orphan edit' that is in Edit Mode, and no object "
                         "editing it was found, so nothing was applied. Ask the person to leave Edit Mode when they "
                         "are ready, then observe again")


class HeldAfterBindTests(unittest.TestCase):
    def tearDown(self):
        exact_fence.end()

    def test_unchanged_modes_pass(self):
        self.assertIsNone(still(Obj("Cube", data=Data("Cube mesh"))))

    def test_the_input_entering_a_mode_after_bind_is_the_same_refusal(self):
        cube = Obj("Cube", "EDIT", Data("Cube mesh", editing=True))
        with self.assertRaises(ProgramError) as caught:
            still(cube)
        with self.assertRaises(ProgramError) as at_bind:
            admit(cube)
        self.assertEqual((caught.exception.code, caught.exception.details, str(caught.exception)),
                         (at_bind.exception.code, at_bind.exception.details, str(at_bind.exception)))
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertEqual(str(caught.exception), "'Cube' is in Edit Mode; typed programs bind objects only in Object "
                         "Mode, so nothing was applied. " + RETURN % "Cube")

    def test_another_object_holding_the_data_after_bind_is_the_same_refusal(self):
        for mode, why in (("EDIT", "has open in Edit Mode"), ("SCULPT", "has open in Sculpt Mode")):
            with self.subTest(mode=mode):
                exact_fence.end()
                mesh = Data("Shared mesh", users=2, editing=mode == "EDIT")
                a, b = Obj("A", data=mesh), Obj("B", mode, mesh)
                with self.assertRaises(ProgramError) as caught:
                    still(a, Scene(a, b), dependency=True)
                self.assertEqual(caught.exception.code, "UNSUPPORTED")
                self.assertEqual(caught.exception.details, {"handles": ["o3"], "held_by": "B"})
                self.assertEqual(str(caught.exception), "'A', which this program's objects depend on, uses data "
                                 "'Shared mesh' that 'B' %s; typed programs never bind data another object is working "
                                 "on, so nothing was applied. %s" % (why, RETURN % "B"))


class WiringTests(unittest.TestCase):
    """Source level: bind and verify need a live document to run."""

    def function(self, name):
        tree = ast.parse((ADDON / "angelo_agent/native_programs.py").read_text())
        return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)

    def test_bind_names_undeclared_dependencies_as_such(self):
        calls = [node for node in ast.walk(self.function("bind")) if isinstance(node, ast.Call)
                 and getattr(node.func, "id", None) == "admit_input"]
        self.assertEqual([[(k.arg, ast.unparse(k.value)) for k in call.keywords] for call in calls],
                         [[("handle", "handle")], [("dependency", "True"), ("handle", "handle")]])

    def test_verify_checks_modes_before_any_revision(self):
        source = ast.unparse(self.function("verify"))
        self.assertIn("assert_still_admitted(obj, handle, handle not in declared_handles(binding))", source)
        self.assertLess(source.index("assert_still_admitted"), source.index("bound_revision"))
        self.assertEqual(program_admission.declared_handles(
            {"program": {"read_set": [{"handle": "o1"}], "write_set": [{"handle": "o2"}]}}), {"o1", "o2"})


if __name__ == "__main__":
    unittest.main()
