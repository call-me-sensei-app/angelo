# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for typed-refusal edge cases, host side.

Each test reproduces a failure class, written for the class rather than one case:

* a ``kind`` that is not a string raised TypeError from validation, not the
  ValueError every caller catches;
* linked copies: a statement without a name makes a new material each time,
  so two of them on a linked group are refused before approval, with that
  reason; a ``copy_as`` copy reused by name, and a restatement on a member that
  already holds the material (edited in place), stay admitted; a restatement on
  a program object the program never gave that material is refused before
  approval exactly where staging refuses it (``action_materials.defined``);
* a frame move refuses only writes an F-curve or driver holds
  (``FRAME_ANIMATED``): a rename, a material and a key deletion of an animated
  object are admitted;
* an orbit camera moves with time only when something animates what places it
  (``program_frames.placement_moves_with_time``), not for any parent or
  constraint.

Also: what places a camera is read from RNA
(``object_dependencies``: custom spaces, modifiers' objects, nested inputs),
the configure kinds' keyed F-curve paths, parenting that writes no channel,
``world.`` paths, and a failure resuming the stage that is verified, not
assumed partial.

Their staged and committed forms are in test_blender_typed_review.py and
test_blender_typed_review3.py.
"""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_refusals  # noqa: E402
from angelo_agent.action_catalog import OPERATION_SCHEMAS  # noqa: E402
from angelo_agent.program_frames import MAX_PLACEMENT_OBJECTS, placement_moves_with_time  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.program_spec import validate_program  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

EPOCH = "c" * 32
SCENE, OBJ, OTHER = ("scn:%s:%d" % (EPOCH, number) for number in (3, 13, 14))
REVISION, SETTINGS = "8" * 64, "7" * 64
UNNAMED = "without a name makes a new material each time"


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations, *handles):
    return validate_program({"mode": "APPLY", "operations": copy.deepcopy(operations), "write_set": [
        {"handle": handle, "revision": SETTINGS if handle == SCENE else REVISION} for handle in handles]})


POST = op("create_primitive", None, primitive="CUBE", name="Post", alias="p", size=1)
COPY = op("object_duplicate", None, source="$p", alias="c", linked=True)
RED, BLUE = {"Base Color": [.8, .1, .1, 1]}, {"Base Color": [.1, .1, .9, 1]}


class NonStringKindTests(unittest.TestCase):
    def test_a_kind_that_is_not_a_string_is_a_value_error_everywhere(self):
        values = ([], {}, [1], {"a": 1}, None, 3, 2.5, True)
        for kind in list(OPERATION_SCHEMAS) + [None]:
            for field in ("kind", "target", "parameters"):
                for value in values:
                    operation = dict(op(kind if kind is not None else "transform"), **{field: value})
                    with self.subTest(kind=kind, field=field, value=value):
                        try:  # Admitted (a default parameters object) or refused: never another exception.
                            validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [
                                {"handle": OBJ, "revision": REVISION}], "operations": [operation]})
                        except ValueError:
                            pass
        for kind in ([], {}):  # Unhashable kinds raised TypeError before.
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "documented native action kind"):
                validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [
                    {"kind": kind, "target": SCENE, "parameters": {"values": {"frame_current": 5}}}]})


class LinkedMaterialIdentityTests(unittest.TestCase):
    def test_unnamed_statements_on_a_linked_group_are_refused_with_the_reason(self):
        for second in (op("material_principled", "$c", inputs=RED),  # The same values: still two materials.
                       op("material_principled", "$c", inputs=BLUE),  # Other values: never "choose another name".
                       op("material_principled", "$c", inputs=RED, also=["$p"])):
            with self.subTest(second=second), self.assertRaisesRegex(ValueError, "LINKED_EDIT") as caught:
                program([POST, COPY, op("material_principled", "$p", inputs=RED), second])
            self.assertIn(UNNAMED, str(caught.exception))
            self.assertNotIn("MATERIAL_REDEFINED", str(caught.exception))
        # Once, with also: one material for the group.
        program([POST, COPY, op("material_principled", "$p", inputs=RED, also=["$c"])])

    def test_a_copy_as_copy_reused_by_name_is_the_same_material(self):
        program([POST, COPY, op("material_assign", "$p", material="Oak", source=OTHER, copy_as="Oak Copy"),
                 op("material_assign", "$c", material="Oak Copy")], OTHER)
        with self.assertRaisesRegex(ValueError, "LINKED_EDIT"):  # The original is another material than the copy.
            program([POST, COPY, op("material_assign", "$p", material="Oak", source=OTHER, copy_as="Oak Copy"),
                     op("material_assign", "$c", material="Oak", source=OTHER)], OTHER)

    def test_a_restatement_is_refused_only_where_staging_refuses_it(self):
        given = op("material_principled", "$p", name="Paint", inputs=RED, also=["$c"])
        # Every member holds it: edited in place, for all its users.
        program([POST, COPY, given, op("material_principled", "$p", name="Paint", inputs=BLUE, shared="ALL_USERS")])
        # A member the program never gave it would restate it: refused before approval, as staging would.
        with self.assertRaisesRegex(ValueError, "MATERIAL_REDEFINED"):
            program([POST, COPY, op("material_principled", "$p", name="Paint", inputs=RED),
                     op("material_principled", "$c", name="Paint", inputs=BLUE)])
        # Outside any group too, on an object the program made; staging compares with the last statement.
        made = op("create_primitive", None, primitive="CUBE", name="Box", alias="b", size=1)
        with self.assertRaisesRegex(ValueError, "MATERIAL_REDEFINED"):
            program([POST, made, op("material_principled", "$p", name="Stone", inputs=RED),
                     op("material_principled", "$b", name="Stone", inputs=BLUE)])
        program([POST, made, op("material_principled", "$p", name="Stone", inputs=RED),
                 op("material_principled", "$p", name="Stone", inputs=BLUE),
                 op("material_principled", "$b", name="Stone", inputs=BLUE)])
        # An object the program did not make may hold it: staging decides.
        program([POST, op("material_principled", "$p", name="Stone", inputs=RED),
                 op("material_principled", OBJ, name="Stone", inputs=BLUE)], OBJ)


def channels(*paths, nla=False):
    action = NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(data_path=path) for path in paths])])])])
    return NS(action=action if paths else None, drivers=[], nla_tracks=[object()] if nla else [])


class FrameAnimatedTests(unittest.TestCase):
    FRAME = op("property_set", SCENE, values={"frame_current": 40})

    def refused(self, operations, block, scene=None, handles=(SCENE, OBJ)):
        arguments = program(operations + [self.FRAME], *handles)
        scene = scene or NS(timeline_markers=[], animation_data=None, name="Scene")
        try:
            program_refusals.check_frame_move(scene, {OBJ: block}, arguments)
        except ProgramError as error:
            self.assertEqual(error.code, "VALIDATION")
            return str(error)
        return None

    def test_only_writes_a_key_or_driver_holds_are_refused(self):
        car = NS(name="Car", animation_data=channels("location", "hide_render"), data=None)
        for admitted in (op("object_rename", name="Car 2"), op("material_principled", name="Paint", inputs=RED),
                         op("keyframe_delete", property="location", frame=1), op("transform", scale=[2, 2, 2]),
                         op("transform", rotation=[0, 0, 1]), op("visibility_set", hidden=True),
                         op("property_set", values={"hide_viewport": True})):
            with self.subTest(admitted=admitted["kind"]):
                self.assertIsNone(self.refused([admitted], car))
        for refused, path in ((op("transform", location=[0, 0, 1]), "location"), (op("origin_set"), "location"),
                              (op("parent_set", parent=None), "location"),
                              (op("property_set", values={"hide_render": True}), "hide_render")):
            with self.subTest(refused=refused["kind"]):
                text = self.refused([refused], car)
                self.assertIn("FRAME_ANIMATED: operations[0] (%s) sets %s of 'Car' without a key" % (refused["kind"], path),
                              text)

    def test_data_material_nla_and_scene_channels(self):
        lens = NS(name="Cam", animation_data=None, data=NS(animation_data=channels("lens")))
        self.assertIn("sets data.lens", self.refused([op("property_set", values={"data.lens": 35})], lens))
        self.assertIsNone(self.refused([op("property_set", values={"data.sensor_width": 30})], lens))
        stack = NS(name="Crate", animation_data=channels(nla=True), data=None)
        self.assertIn("sets scale", self.refused([op("transform", scale=[2, 2, 2])], stack))
        scene = NS(timeline_markers=[], animation_data=channels("gravity"), name="Scene")
        still = NS(name="Shelf", animation_data=None, data=None)
        self.assertIn("sets gravity of 'Scene'", self.refused([op("property_set", SCENE, values={"gravity": [0, 0, -5]})],
                                                              still, scene))
        nla_scene = NS(timeline_markers=[], animation_data=channels(nla=True), name="Scene")
        self.assertIsNone(self.refused([], still, nla_scene))  # The frame itself is never a keyed value.
        world = NS(timeline_markers=[], animation_data=None, name="Scene", world=NS(animation_data=channels("color")))
        self.assertIn("sets world.color", self.refused([op("property_set", SCENE, values={"world.color": [0, 0, 1]})],
                                                       still, world))  # As staging checks it.

    def test_configure_kinds_name_the_f_curve_paths_they_write(self):
        """These fell through and were refused only at staging, after approval."""
        lamp = NS(name="Key", animation_data=None, data=NS(animation_data=channels("energy")))
        self.assertIn("sets data.energy", self.refused([op("light_configure", settings={"energy": 1000.0})], lamp))
        self.assertIsNone(self.refused([op("light_configure", settings={"color": [1, 0, 0]})], lamp))
        lens = NS(name="Cam", animation_data=None, data=NS(animation_data=channels("lens")))
        self.assertIn("sets data.lens", self.refused([op("camera_configure", settings={"lens": 24.0})], lens))
        rig = NS(name="Rig", data=NS(animation_data=None), animation_data=channels(
            'constraints["Follow"].influence', 'modifiers["Bevel"].width', 'pose.bones["Bone"].location'))
        cases = ((op("constraint_configure", constraint="Follow", influence=.25), 'constraints["Follow"].influence'),
                 (op("modifier_configure", modifier="Bevel", type="BEVEL", settings={"width": .5}),
                  'modifiers["Bevel"].width'),
                 (op("bone_set", bone="Bone", location=[1, 1, 1]), 'pose.bones["Bone"].location'))
        for operation, path in cases:
            with self.subTest(path):
                self.assertIn("sets %s of 'Rig'" % path, self.refused([operation], rig))
        for operation in (op("constraint_configure", constraint="Other", influence=.25),
                          op("modifier_configure", modifier="Bevel", type="SOLIDIFY", settings={"thickness": .1}),
                          op("bone_set", bone="Bone", scale=[2, 2, 2])):
            with self.subTest(admitted=operation["kind"]):
                self.assertIsNone(self.refused([operation], rig))

    def test_parenting_writes_channels_only_when_unparenting_in_place(self):
        """action_object.parent_set: a parent keeps the world transform through the parent inverse."""
        car = NS(name="Car", animation_data=channels("location"), data=None)
        for keep in (True, False):
            with self.subTest(keep=keep):
                self.assertIsNone(self.refused([op("parent_set", parent=OTHER, keep_transform=keep)], car, handles=(
                    SCENE, OBJ, OTHER)))
        self.assertIsNone(self.refused([op("parent_set", parent=None, keep_transform=False)], car))
        self.assertIn("sets location", self.refused([op("parent_set", parent=None, keep_transform=True)], car))

    def test_typed_modifier_rna_names_follow_the_kernel(self):
        """The kernel writes each stated setting by MODIFIER_RNA_NAMES (its own name otherwise) and names only the
        implied properties directly; program_frames names the same paths. test_blender_kept_settings runs the kernel."""
        import ast
        from angelo_agent.action_schemas import MODIFIER_ALSO_WRITES, MODIFIER_RNA_NAMES, MODIFIER_SETTINGS, also_writes
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/action_modifiers.py").read_text()
        kernel = next(node for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.FunctionDef) and node.name == "_configure_modifier")
        direct = {target.attr for assign in ast.walk(kernel) if isinstance(assign, ast.Assign) for target in assign.targets
                  if isinstance(target, ast.Attribute) and getattr(target.value, "id", None) == "modifier"}
        implied = {name for _trigger, names in MODIFIER_ALSO_WRITES.values() for name in names}
        self.assertEqual(direct, implied | {"mirror_object", "object"})  # The rest: setattr(modifier, renamed name).
        from angelo_agent.program_frames import _setting_paths
        from angelo_agent.action_catalog import validate_operation
        full = {"BEVEL": {"width": .2, "segments": 3, "limit_method": "NONE", "angle_limit": .4, "profile": .3,
                          "clamp_overlap": False},
                "MIRROR": {"axes": [True, True, False], "use_clip": True, "use_merge_vertices": False,
                           "merge_threshold": .01, "mirror_object": OTHER},
                "BOOLEAN": {"operand": OTHER, "operation": "UNION", "solver": "FLOAT", "use_self": True,
                            "use_hole_tolerant": True},
                "SUBSURF": {"levels": 2, "render_levels": 3, "subdivision_type": "SIMPLE"},
                "ARRAY": {"count": 3}}
        for kind, settings in full.items():
            canonical = validate_operation(op("modifier_configure", modifier="M", type=kind, settings=settings))
            paths = {path.split(".", 1)[1] for path in _setting_paths("modifier_configure", canonical["parameters"])}
            renamed = MODIFIER_RNA_NAMES.get(kind, {})
            expected = {renamed.get(key, key) for key in settings} - {None} | set(also_writes(kind, settings))
            with self.subTest(kind):
                self.assertEqual(paths, expected)
                self.assertTrue(set(settings) <= set(MODIFIER_SETTINGS[kind]["properties"]))
        # A Boolean's operand type goes with a stated operand only; an Array is a fixed count every time.
        self.assertEqual((also_writes("BOOLEAN", {}), also_writes("ARRAY", {})), ((), ("fit_type",)))

def obj(name, parent=None, constraints=(), animated=False, kind="EMPTY", parent_type="OBJECT", data=None, pose=None,
        modifiers=()):
    return NS(name=name, type=kind, parent=parent, parent_type=parent_type, constraints=list(constraints),
              animation_data=channels("location") if animated else None, rigid_body=None, data=data,
              modifiers=list(modifiers), particle_systems=[], pose=pose, matrix_world=None)


def rna(**fields):
    """A struct as Blender's RNA lists it: Object pointers, strings and collections, by the field's value."""
    def prop(name, value):
        if isinstance(value, list):
            return NS(identifier=name, type="COLLECTION")
        if isinstance(value, str):
            return NS(identifier=name, type="STRING")
        if value is None or hasattr(value, "parent"):  # An Object (stand-in) or an empty Object pointer.
            return NS(identifier=name, type="POINTER", fixed_type=NS(identifier="Object"))
        return NS(identifier=name, type="POINTER", fixed_type=NS(identifier="Nested"))
    return NS(bl_rna=NS(properties=[prop(name, value) for name, value in fields.items()]), **fields)


def constraint(kind, target=None, subtarget="", mute=False, influence=1.0, targets=(), **pointers):
    fields = dict(target=target, subtarget=subtarget, **pointers)
    if targets:
        fields["targets"] = list(targets)
    return dict_ns(rna(**fields), type=kind, mute=mute, influence=influence)


def modifier(kind, **pointers):
    return dict_ns(rna(**pointers), type=kind)


def dict_ns(struct, **extra):
    for name, value in extra.items():
        setattr(struct, name, value)
    return struct


class PlacementTimeTests(unittest.TestCase):
    def test_parents_and_constraints_move_a_camera_only_when_something_animates_them(self):
        still, moving = obj("Aim"), obj("Dolly", animated=True)
        cases = {
            "plain": (obj("Cam"), False),
            "track_to_still": (obj("Cam", constraints=[constraint("TRACK_TO", still)]), False),
            "parent_still": (obj("Cam", parent=still), False),
            "child_of_still_chain": (obj("Cam", constraints=[constraint("CHILD_OF", obj("Rig", parent=still))]), False),
            "muted_follow_path": (obj("Cam", constraints=[constraint("FOLLOW_PATH", moving, mute=True)]), False),
            "own_keys": (obj("Cam", animated=True), True),
            # An orbit reads the camera's position only: aiming it moves nothing, unless a
            # positional constraint stands beside the aim, or the aimed object is a parent that carries the camera.
            "track_to_moving": (obj("Cam", constraints=[constraint("TRACK_TO", moving)]), False),
            "track_to_beside_copy_location": (obj("Cam", constraints=[constraint("TRACK_TO", moving),
                                                                      constraint("COPY_LOCATION", still)]), True),
            "pivot_after_track_to": (obj("Cam", constraints=[constraint("TRACK_TO", moving),
                                                             constraint("PIVOT", still)]), True),
            "parent_aimed_at_moving": (obj("Cam", parent=obj("Dolly", constraints=[constraint("TRACK_TO", moving)])),
                                       True),
            "parent_moving": (obj("Cam", parent=moving), True),
            "grandparent_moving": (obj("Cam", parent=obj("Arm", parent=moving)), True),
            "follow_path": (obj("Cam", constraints=[constraint("FOLLOW_PATH", still)]), True),
            "camera_solver": (obj("Cam", constraints=[constraint("CAMERA_SOLVER")]), True),
            "keyed_curve_parent": (obj("Cam", parent=obj("Path", kind="CURVE", data=NS(animation_data=channels(
                "eval_time")))), True),
            "bone_following_a_moving_object": (obj("Cam", parent=obj("Rig", kind="ARMATURE", data=NS(animation_data=None),
                                                                     pose=NS(bones=[NS(constraints=[constraint(
                                                                         "COPY_LOCATION", moving)])])),
                                                   parent_type="BONE"), True),
            "bone_following_a_still_object": (obj("Cam", parent=obj("Rig", kind="ARMATURE", data=NS(animation_data=None),
                                                                    pose=NS(bones=[NS(constraints=[constraint(
                                                                        "IK", still)])])),
                                                  parent_type="BONE"), False),
            "bone_on_a_path": (obj("Cam", parent=obj("Rig", kind="ARMATURE", data=NS(animation_data=None),
                                                     pose=NS(bones=[NS(constraints=[constraint("SPLINE_IK", still)])])),
                                   parent_type="BONE"), True),
            "armature_targets": (obj("Cam", constraints=[constraint("ARMATURE", targets=[rna(target=moving, subtarget="b")])]),
                                 True),
            # What an attribute list missed, read from the RNA instead.
            "custom_space_moving": (obj("Cam", constraints=[constraint("LIMIT_LOCATION", space_object=moving)]), True),
            "custom_space_still": (obj("Cam", constraints=[constraint("LIMIT_LOCATION", space_object=still)]), False),
            "mirror_on_vertex_parent_moving": (obj("Cam", parent=obj("Host", kind="MESH", modifiers=[
                modifier("MIRROR", mirror_object=moving)]), parent_type="VERTEX"), True),
            "mirror_on_vertex_parent_still": (obj("Cam", parent=obj("Host", kind="MESH", modifiers=[
                modifier("MIRROR", mirror_object=still)]), parent_type="VERTEX"), False),
            "mirror_on_object_parent": (obj("Cam", parent=obj("Host", kind="MESH", modifiers=[
                modifier("MIRROR", mirror_object=moving)])), False),  # Only the parent's origin places it.
            "subtarget_on_hooked_mesh": (obj("Cam", constraints=[constraint("COPY_LOCATION", obj("Host", kind="MESH", modifiers=[
                modifier("HOOK", object=moving)]), subtarget="Tip")]), True),
            "nested_input_moving": (obj("Cam", parent=obj("Host", kind="MESH", modifiers=[modifier(
                "SOLIDIFY", properties=rna(inputs=rna(Socket_1=rna(value=moving))))]), parent_type="VERTEX"), True),
            "texture_modifier_still": (obj("Cam", parent=obj("Host", kind="MESH", modifiers=[
                modifier("DISPLACE", texture_coords_object=still)]), parent_type="VERTEX"), True),
        }
        for label, (camera, moves) in cases.items():
            with self.subTest(label):
                self.assertIs(placement_moves_with_time(camera), moves)

    def test_cycles_end_and_long_chains_count_as_moving(self):
        first = obj("A")
        second = obj("B", constraints=[constraint("COPY_LOCATION", first)])
        first.constraints.append(constraint("COPY_ROTATION", second))
        self.assertFalse(placement_moves_with_time(first))
        chain = obj("Root")
        for index in range(MAX_PLACEMENT_OBJECTS + 1):
            chain = obj("Link %d" % index, parent=chain)
        self.assertTrue(placement_moves_with_time(chain))


class ResumeBeforeWritesTests(unittest.TestCase):
    def test_a_failure_resuming_the_stage_is_verified_not_assumed_partial(self):
        from angelo_agent.program_job_state import ProgramError as JobError, resumed_before_writes
        from angelo_agent.refusal_codes import RefusalError

        def backend(error):
            def resume(stage):
                raise error
            return NS(resume_stage=resume)
        with self.assertRaises(JobError) as caught:
            resumed_before_writes(backend(RefusalError("lost metadata", code="INTERNAL_ERROR")), object())
        self.assertTrue(caught.exception.rollback_restored)  # The job verifies the live binding (program_jobs._error).
        self.assertEqual(caught.exception.code, "INTERNAL_ERROR")
        cleanup = JobError("hydration and its cleanup failed", code="CLEANUP_FAILED")
        cleanup.partial_changes_possible = True
        with self.assertRaises(JobError) as caught:
            resumed_before_writes(backend(cleanup), object())
        self.assertFalse(getattr(caught.exception, "rollback_restored", False))  # Its own partial changes stand.
        self.assertEqual(resumed_before_writes(NS(resume_stage=lambda stage: "resumed"), object()), "resumed")


if __name__ == "__main__":
    unittest.main()
