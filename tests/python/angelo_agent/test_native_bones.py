# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""bone_set: schema, every rejection, families, derived checks, the bone check, the pose commit journal.

Dependency-free, with RNA stand-ins. Staged Edit mode, the pose commit on real
data, staleness and Undo are covered by test_blender_bones.py in the compiled
app (with the kernel's refusals: unknown bone, animated channel, parent cycle,
degenerate bone), and commit/Undo/Redo equality by test_blender_program_commit_effects.py.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import bone_state  # noqa: E402
from angelo_agent.action_catalog import affected_references, resource_dependencies, validate_operation  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS, DEFERRED_CHECKS  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.rna_rules import DENIED_STRUCTS  # noqa: E402
from angelo_agent.validation_spec import check_references, validate_check  # noqa: E402

RIG = "scn:" + "a" * 32 + ":7"
SHAPE = "scn:" + "a" * 32 + ":8"


def op(target=RIG, **parameters):
    return {"kind": "bone_set", "target": target, "parameters": parameters}


def canonical(**parameters):
    return validate_operation(op(**parameters))


class SchemaTests(unittest.TestCase):
    def test_deferred_and_float32_canonical(self):
        self.assertIn("bone_set", DEFERRED_ACTIONS)
        self.assertIn("bone", DEFERRED_CHECKS)
        params = canonical(bone="Arm", head=[0, 0, 0.1], tail=[0, 0, 1])["parameters"]
        self.assertEqual(params["head"][2], 0.10000000149011612)
        self.assertEqual((params["settings"], params["pose_settings"], params["unparent"]), ({}, {}, False))

    def test_settings_keep_json_types(self):
        params = canonical(bone="Arm", settings={"bbone_segments": 4, "envelope_distance": 1})["parameters"]
        self.assertIs(type(params["settings"]["bbone_segments"]), int)
        self.assertIs(type(params["settings"]["envelope_distance"]), int)  # RNA, not the schema, types it.

    def test_every_rejection(self):
        cases = {
            "nothing": dict(bone="Arm"),
            "parent and unparent": dict(bone="Arm", parent="Root", unparent=True),
            "own parent": dict(bone="Arm", parent="Arm"),
            "connected without parent": dict(bone="Arm", connected=True, unparent=True),
            "connected head": dict(bone="Arm", connected=True, head=[0, 0, 0]),
            "zero length": dict(bone="Arm", head=[0, 0, 0], tail=[0, 0, 0]),
            "zero scale": dict(bone="Arm", scale=[1, 0, 1]),
            "pose channel path": dict(bone="Arm", pose_settings={"rotation_quaternion": [1, 0, 0, 0]}),
            "matrix path": dict(bone="Arm", pose_settings={"matrix_basis": [1]}),
            "rest geometry path": dict(bone="Arm", settings={"head_local": [0, 0, 0]}),
            "selection": dict(bone="Arm", pose_settings={"select": True}),
            "other bone": dict(bone="Arm", settings={"bbone_custom_handle_start.use_deform": True}),
            "custom shape transform": dict(bone="Arm", pose_settings={"custom_shape_transform": None}),
            "file path": dict(bone="Arm", settings={"filepath": "/tmp/x"}),
            "driver": dict(bone="Arm", pose_settings={"driver_expression": "1"}),
            "own armature as shape": dict(bone="Arm", pose_settings={"custom_shape": {"object": RIG}}),
            "unknown field": dict(bone="Arm", roll=0.1, length=2),
            "roll bound": dict(bone="Arm", roll=7.0),
            "rotation mode": dict(bone="Arm", rotation_mode="XYZW"),
            "blank bone": dict(bone=" ", roll=0.1),
        }
        for name, parameters in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                canonical(**parameters)

    def test_families_are_narrowed(self):
        self.assertEqual(resource_dependencies(op(bone="Arm", roll=0.2))["mutates"], ["object", "armature"])
        self.assertEqual(resource_dependencies(op(bone="Arm", settings={"use_deform": False}))["mutates"],
                         ["object", "armature"])
        self.assertEqual(resource_dependencies(op(bone="Arm", rotation=[0.1, 0, 0]))["mutates"], ["object", "pose"])
        self.assertEqual(resource_dependencies(op(bone="Arm", unparent=True, pose_settings={"ik_stretch": 0.1}))["mutates"],
                         ["object", "armature", "pose"])

    def test_short_identifiers_expand_in_the_target_and_settings(self):
        import copy
        from angelo_agent import identifiers
        from angelo_agent.action_schemas import OPERATION_SCHEMAS
        table = identifiers.IdentifierTable()
        for handle, revision in ((RIG, "1" * 64), (SHAPE, "2" * 64)):
            table.compact({"handle": handle, "object_revision": revision})
        schema = {"oneOf": [copy.deepcopy(variant) for variant in OPERATION_SCHEMAS.values()]}
        handles, check_types = identifiers._reference_schemas()
        value = op("o1", bone="Arm", pose_settings={"custom_shape": {"object": "o2"}})
        resolved = identifiers._expand(value, schema, table, handles, check_types)
        self.assertEqual(resolved, op(RIG, bone="Arm", pose_settings={"custom_shape": {"object": SHAPE}}))

    def test_custom_shape_is_a_declared_dependency(self):
        operation = op(bone="Arm", pose_settings={"custom_shape": {"object": SHAPE}})
        self.assertEqual(affected_references(operation), (RIG, SHAPE))
        check = derive_checks([validate_operation(operation)])[0]
        self.assertEqual(check_references(check), (RIG, SHAPE))


class DerivedCheckTests(unittest.TestCase):
    def derived(self, *operations):
        return derive_checks([validate_operation(operation) for operation in operations])

    def test_every_stated_value_is_checked(self):
        check = self.derived(op(bone="Arm", tail=[0, 1, 2], roll=0.3, rotation=[0.5, 0, 0], unparent=True,
                                settings={"use_deform": False}))[0]
        self.assertEqual(check, {"kind": "bone", "target": RIG, "bone": "Arm", "tail": [0.0, 1.0, 2.0], "roll": 0.3,
                                 "parent": None, "connected": False, "rotation": [0.5, 0, 0],
                                 "settings": {"use_deform": False}})
        self.assertEqual(validate_check(check), check)

    def test_same_bone_merges_and_a_snapped_head_is_dropped(self):
        merged = self.derived(op(bone="Arm", head=[0, 0, 1], settings={"use_deform": False}),
                              op(bone="Arm", parent="Root", settings={"bbone_segments": 2}, rotation=[0, 0.1, 0]))
        self.assertEqual(len(merged), 1)
        self.assertNotIn("head", merged[0])
        self.assertEqual(merged[0]["settings"], {"use_deform": False, "bbone_segments": 2})
        self.assertEqual((merged[0]["parent"], merged[0]["rotation"]), ("Root", [0, 0.1, 0]))

    def test_moving_another_bone_drops_stated_endpoints_only(self):
        kept = self.derived(op(bone="Arm", rotation=[0.1, 0, 0]), op(bone="Hand", tail=[0, 0, 3]))
        self.assertEqual([check["bone"] for check in kept], ["Arm", "Hand"])
        dropped = self.derived(op(bone="Arm", tail=[0, 0, 2]), op(bone="Hand", head=[0, 0, 2.5]))
        self.assertEqual([check["bone"] for check in dropped], ["Hand"])
        posed = self.derived(op(bone="Arm", tail=[0, 0, 2]), op(bone="Hand", rotation=[0, 0, 0.2]))
        self.assertEqual([check["bone"] for check in posed], ["Arm", "Hand"])

    def test_check_shapes(self):
        for check in ({"kind": "bone", "target": RIG, "bone": "Arm"},
                      {"kind": "bone", "target": RIG, "bone": "Arm", "parent": None},
                      {"kind": "bone", "target": RIG, "bone": "Arm", "rotation_mode": "ZYX", "scale": [1, 2, 1]}):
            self.assertEqual(validate_check(check), check)
        for check in ({"kind": "bone", "target": RIG}, {"kind": "bone", "target": RIG, "bone": "Arm", "roll": 9},
                      {"kind": "bone", "target": RIG, "bone": "Arm", "pose_settings": {"location": [0, 0, 0]}},
                      {"kind": "bone", "target": RIG, "bone": "Arm", "settings": {}}):
            with self.subTest(check=check), self.assertRaises(ValueError):
                validate_check(check)


class Prop:
    def __init__(self, kind="FLOAT", length=0):
        self.type, self.array_length, self.is_runtime = kind, length, False


class Rna:
    def __init__(self, identifier, props):
        self.identifier, self.properties = identifier, props


def pose_bone(name, mode="QUATERNION", **values):
    item = SimpleNamespace(name=name, rotation_mode=mode, location=(0., 0., 0.), rotation_quaternion=(1., 0., 0., 0.),
                           rotation_euler=(0., 0., 0.), rotation_axis_angle=(0., 0., 1., 0.), scale=(1., 1., 1.),
                           ik_stretch=0.)
    item.__dict__.update(values)
    return item


class PoseBones(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


class ModeConvertingBone(SimpleNamespace):
    """Setting rotation_mode rewrites the channels, as Blender's RNA does."""

    def __setattr__(self, name, value):
        if name == "rotation_mode" and "rotation_mode" in self.__dict__ and value != self.__dict__["rotation_mode"]:
            super().__setattr__("rotation_euler", (9., 9., 9.))
            super().__setattr__("rotation_quaternion", (0.5, 0.5, 0.5, 0.5))
        super().__setattr__(name, value)


class PoseCommitTests(unittest.TestCase):
    def setUp(self):
        bone_state._PATHS.clear()
        self.addCleanup(bone_state._PATHS.clear)

    def armature(self, bones):
        return SimpleNamespace(pose=SimpleNamespace(bones=PoseBones(bones)))

    def test_changed_channels_transfer_and_a_rollback_restores_mode_first(self):
        live = ModeConvertingBone(**vars(pose_bone("Arm", rotation_quaternion=(0.9, 0.1, 0., 0.))))
        staged = pose_bone("Arm", mode="XYZ", rotation_euler=(0.5, 0., 0.), location=(0., 1., 0.))
        journal = []
        bone_state.transfer_pose(self.armature([live]), self.armature([staged]), {}, journal)
        self.assertEqual((live.rotation_mode, live.rotation_euler, live.location), ("XYZ", (0.5, 0., 0.), (0., 1., 0.)))
        self.assertEqual([entry[1] for entry in journal][-1], "rotation_mode")
        for holder, name, value in reversed(journal):  # The commit's rollback order.
            setattr(holder, name, value)
        self.assertEqual((live.rotation_mode, live.rotation_quaternion, live.rotation_euler, live.location),
                         ("QUATERNION", (0.9, 0.1, 0., 0.), (0., 0., 0.), (0., 0., 0.)))

    def test_only_the_posed_bones_transfer(self):
        live = [pose_bone("Arm"), pose_bone("Hand")]
        staged = [pose_bone("Arm", location=(1., 0., 0.)), pose_bone("Hand", location=(2., 0., 0.))]
        journal = []
        bone_state.transfer_pose(self.armature(live), self.armature(staged), {}, journal, ["Hand"])
        self.assertEqual((live[0].location, live[1].location), ((0., 0., 0.), (2., 0., 0.)))
        with self.assertRaises(ProgramError):
            bone_state.transfer_pose(self.armature(live), self.armature(staged), {}, [], ["Missing"])

    def test_unchanged_bones_write_nothing_and_a_missing_bone_fails(self):
        journal = []
        bone_state.transfer_pose(self.armature([pose_bone("Arm")]), self.armature([pose_bone("Arm")]), {}, journal)
        self.assertEqual(journal, [])
        with self.assertRaises(ProgramError):
            bone_state.transfer_pose(self.armature([]), self.armature([pose_bone("Arm")]), {}, [])

    def test_state_reads_stand_ins_without_rna(self):
        armature = SimpleNamespace(data=SimpleNamespace(bones=[SimpleNamespace(name="Arm")]),
                                   pose=SimpleNamespace(bones=[pose_bone("Arm")]))
        work = SimpleNamespace(take=lambda **_: None)
        state = bone_state.armature_state(armature, work, lambda value: value.name)
        self.assertEqual(state["bones"], [["Arm", [], [None, None]]])
        self.assertEqual(state["pose"][0][:2], ["Arm", "QUATERNION"])
        self.assertEqual(state["pose_columns"]["channels"][0][1], [0.0, 0.0, 0.0])


def animated_owner(paths=(), nla=False):
    bag = SimpleNamespace(fcurves=[SimpleNamespace(data_path=path) for path in paths])
    action = SimpleNamespace(layers=[SimpleNamespace(strips=[SimpleNamespace(channelbags=[bag])])])
    return SimpleNamespace(animation_data=SimpleNamespace(drivers=[], action=action,
                                                          nla_tracks=[object()] if nla else []))


class PredicateTests(unittest.TestCase):
    def rig(self, animation=None):
        bone = SimpleNamespace(name="Arm", head_local=(0., 0., 1.), tail_local=(0., 0.5, 2.), use_connect=True,
                               parent=SimpleNamespace(name="Root"))
        data = SimpleNamespace(bones=PoseBones([bone]), animation_data=None)
        rig = SimpleNamespace(type="ARMATURE", data=data, pose=SimpleNamespace(bones=PoseBones([pose_bone("Arm")])),
                              animation_data=animation)
        return rig

    def evaluate(self, obj, **fields):
        reader = SimpleNamespace(spend=lambda amount: None, context={"targets": {}})
        return bone_state.evaluate(reader, obj, {"kind": "bone", "target": RIG, "bone": "Arm", "tolerance": 0.00001,
                                                 **fields})

    def test_rest_values_pass_and_fail_with_observed_values(self):
        passed, result = self.evaluate(self.rig(), tail=[0, 0.5, 2], parent="Root", connected=True)
        self.assertTrue(passed, result)
        passed, result = self.evaluate(self.rig(), head=[0, 0, 1.1], parent=None)
        self.assertFalse(passed)
        self.assertEqual(result["mismatched"], ["head", "parent"])
        self.assertEqual(result["values"]["parent"], "Root")

    def test_missing_bone_wrong_type_and_animated_pose(self):
        self.assertEqual(self.evaluate(SimpleNamespace(type="MESH"), tail=[0, 0, 1]), (False, {"type": "MESH"}))
        rig = self.rig()
        rig.data.bones.clear()
        self.assertEqual(self.evaluate(rig, tail=[0, 0, 1]), (False, {"present": False}))
        keyed = self.rig(animated_owner(['pose.bones["Arm"].location']).animation_data)
        from angelo_agent.validation_reader import Inconclusive
        with self.assertRaises(Inconclusive):
            self.evaluate(keyed, location=[0, 0, 0])
        self.assertTrue(self.evaluate(keyed, tail=[0, 0.5, 2])[0])  # Rest geometry is not animated.
        # Only the channels a check reads count: a keyed quaternion leaves the
        # stored location measurable, and decides the rotation of a bone in that mode.
        turned = self.rig(animated_owner(['pose.bones["Arm"].rotation_quaternion']).animation_data)
        self.evaluate(turned, location=[0, 0, 0])
        turned.pose.bones[0].rotation_mode = "QUATERNION"
        with self.assertRaises(Inconclusive):
            self.evaluate(turned, rotation=[0, 0, 0])


class KernelGuardTests(unittest.TestCase):
    def test_animated_paths_by_escaped_bone_name(self):
        obj = animated_owner(['pose.bones["Arm \\"L\\""].location'])
        obj.data = SimpleNamespace(animation_data=None)
        self.assertEqual(bone_state.animated(obj, 'Arm "L"', ["location"]), "location")
        self.assertIsNone(bone_state.animated(obj, "Arm", ["location"], ["use_deform"]))
        nla = animated_owner(nla=True)  # A track whose strips cannot be read: every path.
        nla.data = SimpleNamespace(animation_data=None)
        self.assertEqual(bone_state.animated(nla, "Arm", ["scale"]), "scale")
        self.assertIsNone(bone_state.animated(nla, "Arm", [], []))
        # A track's strips name what they animate (their Actions, meta strips too): only those paths count.
        bag = SimpleNamespace(fcurves=[SimpleNamespace(data_path='pose.bones["Arm"].rotation_euler')])
        clip = SimpleNamespace(action=SimpleNamespace(layers=[SimpleNamespace(strips=[SimpleNamespace(channelbags=[
            bag])])]), strips=[])
        meta = SimpleNamespace(action=None, strips=[clip])
        pushed = SimpleNamespace(animation_data=SimpleNamespace(drivers=[], action=None, nla_tracks=[
            SimpleNamespace(strips=[meta])]), data=SimpleNamespace(animation_data=None))
        self.assertEqual(bone_state.animated(pushed, "Arm", ["location", "rotation_euler"]), "rotation_euler")
        self.assertIsNone(bone_state.animated(pushed, "Arm", ["location", "scale"]))

    def test_other_bones_are_denied_structs(self):
        self.assertTrue({"Bone", "PoseBone", "EditBone"} <= DENIED_STRUCTS)


if __name__ == "__main__":
    unittest.main()
