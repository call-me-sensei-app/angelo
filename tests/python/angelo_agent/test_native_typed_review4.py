# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for typed-refusal edge cases: defaults, dependency walks and rotation modes, host side.

* A ``material_principled`` is name-only when the call states no input, never
  when its stated values equal the defaults; canonical programs keep that
  (``inputs`` empty) and re-validate to themselves.
* ``object_dependencies.walk`` follows what drivers read (objects, through
  data and scenes), the Objects and Collections in Geometry Nodes groups,
  nested ones included, and scans a group shared by modifiers once per walk;
  an orbit's camera needs only its position, so its rotation-only constraints
  and rotation channels are left out.
* A ``rotation_mode`` write lists the channel Blender's setter rewrites.
* A failure resuming a stage while polling is verified, not assumed partial.

Their staged and committed forms are in test_blender_typed_review4.py.
"""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import material_rules  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.object_dependencies import walk  # noqa: E402
from angelo_agent.program_frames import frame_independent, placement_moves_with_time, unkeyed_writes  # noqa: E402
from angelo_agent.program_orbits import followed_objects  # noqa: E402

OBJ = "scn:" + "c" * 32 + ":13"


def principled(**parameters):
    return validate_operation({"kind": "material_principled", "target": OBJ, "parameters": dict(name="Paint",
                                                                                                **parameters)})


class NameOnlyTests(unittest.TestCase):
    def test_stating_a_default_value_is_stating_it(self):
        default = material_rules.NEW_INPUTS
        for inputs in ({"Base Color": default["Base Color"]}, {"Roughness": default["Roughness"]}, dict(default)):
            with self.subTest(inputs=inputs):
                stated = principled(inputs=inputs)
                self.assertFalse(material_rules.names_only(stated))
                # Only what it states is written (action_schemas.SETTINGS_NOTE).
                self.assertEqual(material_rules.principled_inputs(stated["parameters"]), inputs)
        for raw in ({}, {"inputs": {}}):
            with self.subTest(raw=raw):
                alone = principled(**raw)
                self.assertTrue(material_rules.names_only(alone))
                self.assertEqual(alone["parameters"]["inputs"], {})
                self.assertEqual(validate_operation(alone), alone)  # Canonical programs re-validate to themselves.
                self.assertEqual(material_rules.principled_inputs(alone["parameters"]), {})
        # Either way a new material is Blender's default one, so a later statement of its defaults is the same material.
        self.assertEqual(material_rules.definition(principled()), material_rules.definition(principled(inputs=default)))
        self.assertEqual(material_rules.definition(principled(inputs={"Roughness": .5})),
                         material_rules.definition(principled(inputs={"Roughness": .5, "Metallic": 0.0})))

    def test_a_name_alone_that_makes_a_material_states_no_value(self):
        """A new material keeps Blender's defaults; the check states only what the call wrote (none here)."""
        from angelo_agent.derived_checks import derive_checks
        self.assertEqual([check for check in derive_checks([principled()]) if check["kind"] == "material_values"], [])
        values, = [check for check in derive_checks([principled(inputs={"Roughness": .3})])
                   if check["kind"] == "material_values"]
        self.assertEqual({key: value for key, value in values.items() if key not in ("kind", "target", "material")},
                         {"roughness": .3})


def rna(**fields):
    def prop(name, value):
        if isinstance(value, list):
            return NS(identifier=name, type="COLLECTION")
        if isinstance(value, str):
            return NS(identifier=name, type="STRING")
        kind = "Collection" if hasattr(value, "all_objects") else "Object" if value is None or hasattr(
            value, "parent") else "Nested"
        return NS(identifier=name, type="POINTER", fixed_type=NS(identifier=kind))
    return NS(bl_rna=NS(properties=[prop(name, value) for name, value in fields.items()]), **fields)


def obj(name, kind="EMPTY", animation=None, **fields):
    defaults = dict(type=kind, parent=None, parent_type="OBJECT", constraints=[], animation_data=animation,
                    rigid_body=None, data=None, modifiers=[], particle_systems=[], pose=None, matrix_world=None)
    defaults.update(fields)
    return NS(name=name, bl_rna=NS(identifier="Object", properties=[]), **defaults)


def drivers(*curves):
    return NS(action=None, nla_tracks=[], drivers=list(curves))


def driver(path, *targets, expression="var", kind="SCRIPTED", variable="TRANSFORMS", simple=True, bone="",
           transform="LOC_X"):
    variables = [NS(type=variable, targets=[NS(id=target, bone_target=bone, transform_type=transform)
                                            for target in targets])]
    return NS(data_path=path, driver=NS(type=kind, expression=expression, is_simple_expression=simple,
                                        variables=variables))


def dict_set(struct, **fields):
    for name, value in fields.items():
        setattr(struct, name, value)
    return struct


class Tree:
    """A node group as the walker reads it (hashable, as Blender's IDs are)."""

    def __init__(self, nodes):
        self.nodes, self.animation_data = nodes, None


def constraint(kind, **pointers):
    struct = rna(subtarget="", **pointers)
    struct.type, struct.mute, struct.influence = kind, False, 1.0
    return struct


def names(camera, **options):
    found = followed_objects(camera, lambda item: item.name, **options)
    return sorted(found) if found is not None else None


class WalkerTests(unittest.TestCase):
    def test_what_drivers_read_is_followed(self):
        ctrl = obj("Ctrl")
        camera = obj("Cam", animation=drivers(driver("location", ctrl)))
        self.assertEqual(names(camera), ["Ctrl"])
        self.assertFalse(placement_moves_with_time(camera))  # A driver of still objects is still.
        ticking = obj("Cam", animation=drivers(driver("location", ctrl, expression="var + frame")))
        self.assertTrue(placement_moves_with_time(ticking))
        python = obj("Cam", animation=drivers(driver("location", ctrl, simple=False)))
        self.assertTrue(placement_moves_with_time(python))
        chained = obj("Cam", animation=drivers(driver("location", obj("Mid", animation=drivers(driver(
            "location", obj("Ctrl")))))))
        self.assertEqual(names(chained), ["Ctrl", "Mid"])
        parent = obj("Dolly", animation=drivers(driver("rotation_euler", ctrl)))
        self.assertEqual(names(obj("Cam", parent=parent)), ["Ctrl", "Dolly"])  # A parent's rotation carries it.
        own = obj("Cam", animation=drivers(driver("rotation_euler", ctrl)))
        self.assertEqual(names(own), [])  # Its own rotation never moves its position.

    def test_scenes_drivers_read_are_followed_by_handle_or_nothing_is_proven(self):
        scene = NS(name="Scene", bl_rna=NS(identifier="Scene", properties=[]))
        camera = obj("Cam", animation=drivers(driver("location", scene, variable="SINGLE_PROP")))
        self.assertIsNone(names(camera))
        self.assertEqual(names(camera, scene_handle_of=lambda item: "scene:" + item.name), ["scene:Scene#*"])
        scoped = obj("Cam", animation=drivers(driver("location", scene, variable="SINGLE_PROP")))
        scoped.animation_data.drivers[0].driver.variables[0].targets[0].data_path = '["dolly"]'
        self.assertEqual(names(scoped, scene_handle_of=lambda item: "scene:" + item.name), ['scene:Scene#["dolly"]'])
        self.assertTrue(placement_moves_with_time(camera))

    def test_geometry_nodes_groups_point_at_objects_and_collections(self):
        ctrl, member = obj("Ctrl"), obj("Member")
        node = lambda **fields: dict_set(rna(**fields), bl_idname="GeometryNodeSetPosition")
        inner = Tree([node(inputs=[rna(default_value=ctrl)], node_tree=None)])
        outer = Tree([node(node_tree=inner), node(collection=NS(all_objects=[member]))])
        host = obj("Host", kind="MESH", modifiers=[NS(type="NODES", node_group=outer, bl_rna=NS(properties=[]))])
        self.assertEqual(names(obj("Cam", parent=host, parent_type="VERTEX")), ["Ctrl", "Host", "Member"])

    def test_a_group_shared_by_modifiers_is_scanned_once(self):
        """Regression: a 300-node group on two modifiers spent the 512-node budget twice."""
        group = Tree([NS(bl_idname="GeometryNodeSetPosition") for _ in range(300)])
        mesh = obj("Box", kind="MESH", data=NS(animation_data=None, shape_keys=None), modifiers=[
            NS(type="NODES", node_group=group, bl_rna=NS(properties=[])) for _ in range(2)])
        self.assertTrue(frame_independent(mesh))
        self.assertFalse(walk(mesh, 64, roles=("shape",)).time)

    def test_rotation_only_constraints_are_left_out_only_when_all_are(self):
        aim, ctrl = obj("Aim", animation=drivers(driver("location", obj("Far")))), obj("Ctrl")
        track, copy_location = constraint("TRACK_TO", target=aim), constraint("COPY_LOCATION", target=ctrl)
        self.assertEqual(names(obj("Cam", constraints=[track])), [])
        self.assertEqual(names(obj("Cam", constraints=[track, copy_location])), ["Aim", "Ctrl", "Far"])


class FifthReviewWalkerTests(unittest.TestCase):
    """What a driver reads beyond its target, and walks that stopped short."""

    def test_keys_in_an_embedded_node_tree_or_behind_a_path_hop_mark_time(self):
        tree = Tree([])
        tree.animation_data = NS(action=NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(
            data_path='nodes["Principled BSDF"].inputs["Roughness"].default_value')])])])]), drivers=[], nla_tracks=[])
        material = NS(name="Mat", bl_rna=NS(identifier="Material", properties=[]), animation_data=None, node_tree=tree)
        camera = obj("Cam", animation=drivers(driver("location", material, variable="SINGLE_PROP")))
        self.assertTrue(placement_moves_with_time(camera))
        holder = obj("Holder")
        holder.path_resolve = lambda path, coerce=True: NS(id_data=material)  # active_material.roughness
        hop = obj("Cam", animation=drivers(driver("location", holder, variable="SINGLE_PROP")))
        hop.animation_data.drivers[0].driver.variables[0].targets[0].data_path = "active_material.roughness"
        self.assertTrue(placement_moves_with_time(hop))
        self.assertEqual(names(hop), ["Holder"])

    def test_an_instanced_collection_is_followed(self):
        member = obj("Member")
        empty = obj("Instancer", instance_collection=NS(all_objects=[member]))
        empty.bl_rna = NS(identifier="Object", properties=[NS(identifier="instance_collection", type="POINTER",
                                                               fixed_type=NS(identifier="Collection"))])
        info = dict_set(rna(object=empty), bl_idname="GeometryNodeObjectInfo")
        host = obj("Host", kind="MESH", modifiers=[NS(type="NODES", node_group=Tree([info]), bl_rna=NS(properties=[]))])
        self.assertEqual(names(obj("Cam", parent=host, parent_type="VERTEX")), ["Host", "Instancer", "Member"])

    def test_a_walk_cut_short_never_reads_as_complete(self):
        from angelo_agent.object_dependencies import MAX_POINTER_NODES
        big = Tree([dict_set(rna(), bl_idname="GeometryNodeSetPosition") for _ in range(MAX_POINTER_NODES + 1)])
        host = obj("Host", kind="MESH", modifiers=[NS(type="NODES", node_group=big, bl_rna=NS(properties=[]))])
        self.assertIsNone(names(obj("Cam", parent=host, parent_type="VERTEX")))

    def test_a_rotation_read_back_into_the_camera_counts(self):
        camera = obj("Cam", constraints=[constraint("TRACK_TO", target=obj("Subject"))])
        keyed = NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(data_path="rotation_euler")])])])])
        camera.animation_data = NS(action=keyed, nla_tracks=[], drivers=[driver("location", camera, transform="ROT_Z")])
        self.assertTrue(placement_moves_with_time(camera))  # Its location reads its own keyed rotation.
        # A dolly zoom reads its own location or lens, never its rotation: still exempt.
        dolly = obj("Cam", constraints=[constraint("TRACK_TO", target=obj("Subject"))])
        dolly.animation_data = NS(action=keyed, nla_tracks=[], drivers=[driver("location", dolly, transform="LOC_X")])
        self.assertFalse(placement_moves_with_time(dolly))
        still = obj("Cam", constraints=[constraint("TRACK_TO", target=obj("Subject"))])
        still.animation_data = NS(action=keyed, nla_tracks=[], drivers=[])
        self.assertFalse(placement_moves_with_time(still))  # Keyed rotation alone never moves its position.


class SixthReviewTests(unittest.TestCase):
    """Every ID a path crosses, context scenes, nested settings, index paths, modes."""

    def test_every_id_a_driver_path_crosses_is_walked(self):
        from angelo_agent.dependency_paths import path_prefixes as _path_prefixes
        self.assertEqual(_path_prefixes('active_material.node_tree.nodes["P"].inputs[0].default_value'),
                         ["active_material", "active_material.node_tree", "active_material.node_tree.nodes",
                          'active_material.node_tree.nodes["P"]', 'active_material.node_tree.nodes["P"].inputs',
                          'active_material.node_tree.nodes["P"].inputs[0].default_value'])
        self.assertEqual(_path_prefixes("matrix_world[3][0]"), ["matrix_world"])
        material = NS(name="Shared", bl_rna=NS(identifier="Material", properties=[]), animation_data=None,
                      node_tree=None)
        tree = NS(name="Embedded", bl_rna=NS(identifier="ShaderNodeTree", properties=[]), animation_data=None)
        holder = obj("Holder")
        steps = {"active_material": material, "active_material.node_tree": tree}
        holder.path_resolve = lambda path, coerce=True: NS(id_data=steps.get(path, tree))
        camera = obj("Cam", animation=drivers(driver("location", holder, variable="SINGLE_PROP")))
        camera.animation_data.drivers[0].driver.variables[0].targets[0].data_path = (
            'active_material.node_tree.nodes["P"].inputs["Roughness"].default_value')
        from angelo_agent import object_dependencies
        walked = []
        with patch.object(object_dependencies, "_curve_paths", side_effect=lambda animation: set()), \
                patch.object(object_dependencies, "walk", wraps=object_dependencies.walk):
            found = object_dependencies.walk(camera, 64, position_only=True)
        self.assertEqual([item.name for item in found.objects], ["Holder"])  # Owners need Blender's user map.
        flat = obj("Still")
        values = {"matrix_world": NS(id_data=flat), "matrix_world[3][0]": 0.0}  # As Blender resolves them.
        flat.path_resolve = lambda path, coerce=True: values[path] if path in values else (_ for _ in ()).throw(
            ValueError(path))
        indexed = obj("Cam", animation=drivers(driver("location", flat, variable="SINGLE_PROP")))
        indexed.animation_data.drivers[0].driver.variables[0].targets[0].data_path = "matrix_world[3][0]"
        self.assertFalse(placement_moves_with_time(indexed))  # Two indices resolve; a still empty is still.

    def test_a_context_variable_follows_the_scene_the_camera_is_in(self):
        scene = NS(name="Shot", bl_rna=NS(identifier="Scene", properties=[]))
        camera = obj("Cam", users_scene=[scene], animation=drivers(driver("location", None, variable="CONTEXT_PROP")))
        target = camera.animation_data.drivers[0].driver.variables[0].targets[0]
        target.context_property, target.data_path = "ACTIVE_SCENE", "render.resolution_x"
        self.assertEqual(names(camera, scene_handle_of=lambda item: item.name), ["Shot#render"])
        # A view layer's path is read from each of the scene's view layers.
        scene.view_layers = [NS(id_data=scene, path_from_id=lambda: 'view_layers["Layer"]')]
        target.context_property = "ACTIVE_VIEW_LAYER"
        self.assertEqual(names(camera, scene_handle_of=lambda item: item.name), ["Shot#view_layers"])

    def test_nested_settings_of_an_object_are_not_its_instances(self):
        lit = obj("Mover", animation=drivers(driver("location", obj("Far"), expression="frame")))
        linking = rna(receiver_collection=NS(all_objects=[lit]))
        lamp = obj("Lamp", kind="LIGHT", data=NS(animation_data=None, shape_keys=None), light_linking=linking)
        lamp.bl_rna = NS(identifier="Object", properties=[NS(identifier="light_linking", type="POINTER",
                                                              fixed_type=NS(identifier="ObjectLightLinking"))])
        self.assertTrue(frame_independent(lamp))  # Its light linking never changes its own evaluated state.

    def test_a_mode_an_earlier_bone_set_left_decides_what_a_later_one_touches(self):
        from angelo_agent import program_refusals
        from angelo_agent.program_jobs import ProgramError
        from angelo_agent.program_spec import validate_program
        scene_handle = "scn:" + "c" * 32 + ":3"
        bone = NS(rotation_mode="XYZ")
        rig = NS(name="Rig", animation_data=NS(action=NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=[NS(
            data_path='pose.bones["Bone"].rotation_euler')])])])]), drivers=[], nla_tracks=[]),
                 data=NS(animation_data=None), pose=NS(bones={"Bone": bone}))
        program = validate_program({"mode": "APPLY", "write_set": [
            {"handle": OBJ, "revision": "8" * 64}, {"handle": scene_handle, "revision": "7" * 64}], "operations": [
            {"kind": "bone_set", "target": OBJ, "parameters": {"bone": "Bone", "rotation_mode": "QUATERNION"}},
            {"kind": "bone_set", "target": OBJ, "parameters": {"bone": "Bone", "rotation": [0, 0, 1]}},
            {"kind": "property_set", "target": scene_handle, "parameters": {"values": {"frame_current": 11}}}]})
        with self.assertRaises(ProgramError) as caught:  # The switch itself stops the keyed Euler: operations[0].
            program_refusals.check_frame_move(NS(timeline_markers=[]), {OBJ: rig}, program)
        self.assertIn("operations[0]", str(caught.exception))


class RotationModeTests(unittest.TestCase):
    def test_a_rotation_mode_write_names_the_channel_it_rewrites(self):
        bone = validate_operation({"kind": "bone_set", "target": OBJ, "parameters": {
            "bone": "Bone", "rotation_mode": "QUATERNION"}})
        self.assertIn((OBJ, 'pose.bones["Bone"].rotation_quaternion'), unkeyed_writes(bone))
        euler = validate_operation({"kind": "bone_set", "target": OBJ, "parameters": {
            "bone": "Bone", "rotation_mode": "XYZ"}})
        self.assertIn((OBJ, 'pose.bones["Bone"].rotation_euler'), unkeyed_writes(euler))  # The new mode's channel.
        # Blender's setter rewrites the channel only across rotation families (BKE_rotMode_change_values), and the
        # kernel, its check and this list name only the channels a write touches.
        # Across families both channels count: the new one is rewritten, the old one stops deciding the rotation
        # (a keyed Euler bone switched to quaternion stops playing).
        for current, touched in (("ZXY", []), ("XYZ", []),
                                 ("QUATERNION", ['pose.bones["Bone"].rotation_euler', 'pose.bones["Bone"].rotation_quaternion']),
                                 ("AXIS_ANGLE", ['pose.bones["Bone"].rotation_euler', 'pose.bones["Bone"].rotation_axis_angle'])):
            with self.subTest(current=current):
                paths = [path for _, path in unkeyed_writes(euler, lambda reference, bone: current)]
                self.assertEqual([path for path in paths if path.endswith(("_euler", "_quaternion", "_angle"))], touched)
        from angelo_agent.action_bones_spec import pose_paths
        location = validate_operation({"kind": "bone_set", "target": OBJ, "parameters": {"bone": "Bone",
                                                                                        "location": [1, 0, 0]}})
        self.assertEqual(pose_paths(location["parameters"], "XYZ"), ["location"])  # Never its keyed rotation.
        turned = validate_operation({"kind": "bone_set", "target": OBJ, "parameters": {
            "bone": "Bone", "rotation_mode": "XYZ", "rotation": [0, 0, 1]}})
        self.assertEqual(pose_paths(turned["parameters"], "QUATERNION"),
                         ["rotation_mode", "rotation_euler", "rotation_quaternion"])
        self.assertEqual(pose_paths(turned["parameters"], "ZXY"), ["rotation_mode", "rotation_euler"])


class PollResumeTests(unittest.TestCase):
    def test_a_failure_resuming_a_stage_while_polling_is_verified(self):
        from angelo_agent.program_jobs import PreviewStore, ProgramJob, WriterLease
        from angelo_agent.refusal_codes import RefusalError
        from test_native_program_jobs import Backend, program

        class Resuming(Backend):
            def suspend_stage(self, stage):
                return stage if "suspended" in stage else {"suspended": copy.deepcopy(stage), "binding": None}

            def resume_stage(self, stage):
                raise RefusalError("Node metadata refers outside the staged IDs", code="INTERNAL_ERROR")

        for verified in (True, False):
            with self.subTest(verified=verified):
                backend = Resuming()
                job = ProgramJob(backend, program(), owner="chat", writer=WriterLease(), previews=PreviewStore())
                outcome = None if verified else RuntimeError("a bound revision changed")
                with patch.object(backend, "verify_rollback", side_effect=outcome, create=True):
                    for _ in range(20):
                        result = job.poll()
                        if result.get("final"):
                            break
                self.assertEqual(result["status"], "failed")
                self.assertIs(result["partial_changes_possible"], not verified)
                self.assertEqual(result.get("rollback_verified"), True if verified else None)


if __name__ == "__main__":
    unittest.main()
