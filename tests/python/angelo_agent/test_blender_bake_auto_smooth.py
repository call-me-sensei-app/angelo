# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake Auto Smooth (Blender #153195, #126391) in the compiled app, without a window.

object.shade_auto_smooth_bake writes the Smooth by Angle result into each selected object's mesh and
removes the modifier from every object that uses it. Checked here:

* exact: every user's evaluated corner normals and sharpness are bit-identical before and after, on
  meshes the operator was not developed on (a capped cylinder with a sphere, a noisy grid with flat
  faces, a mesh with custom normals), at several angles and with Ignore Sharpness;
* one evaluated mesh afterwards for all users, unselected and hidden users included;
* each refusal leaves the mesh and every modifier as they were and names its reason, animation inside the
  node group or a nested group (an action or a driver) included;
* one Undo step restores the modifiers; Apply's multi-user refusal names the operator;
* 10,000 objects sharing a mesh: re-evaluation time and evaluated-mesh count before and after.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
from pathlib import Path
import random
import statistics
import time
import traceback

import bmesh
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cylinder_and_sphere(name):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=1.0, radius2=1.0, depth=2.0)
    sphere = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.7)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 1.6), verts=sphere["verts"])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def noisy_grid(name):
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=20, y_segments=20, size=2.0)
    rng = random.Random(7)
    for vert in bm.verts:
        vert.co.z = rng.uniform(-0.3, 0.3)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def custom_normals_cube(name):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.normals_split_custom_set_from_vertices([v.co.normalized() for v in mesh.vertices])
    return mesh


def users_of(mesh, count, hidden=False):
    objects = []
    for index in range(count):
        ob = bpy.data.objects.new(f"{mesh.name}.user{index:05d}", mesh)
        ob.location = (index % 100 * 3.0, index // 100 * 3.0, 0.0)
        bpy.context.scene.collection.objects.link(ob)
        objects.append(ob)
    for ob in objects:
        ob.hide_viewport = hidden
    return objects


def select(objects):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def auto_smooth(objects, angle):
    select(objects)
    bpy.ops.object.shade_auto_smooth(angle=angle)
    return [ob.modifiers[0] for ob in objects]


def angle_input(modifier):
    return next(item.identifier for item in modifier.node_group.interface.items_tree
                if getattr(item, "in_out", "") == 'INPUT' and item.name == "Angle")


def modifier_input(modifier, name):
    """A Geometry Nodes modifier input (5.2: modifier.properties.inputs.<identifier>)."""
    ident = next(item.identifier for item in modifier.node_group.interface.items_tree
                 if getattr(item, "in_out", "") == 'INPUT' and item.name == name)
    return getattr(modifier.properties.inputs, ident)


def set_input(modifier, name, value):
    modifier_input(modifier, name).value = value


def shading(objects):
    """Per object: its evaluated corner normals, sharp edges, sharp faces and evaluated mesh pointer."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result = []
    for ob in objects:
        ob_eval = ob.evaluated_get(depsgraph)
        mesh = ob_eval.data
        normals = [0.0] * (len(mesh.corner_normals) * 3)
        mesh.corner_normals.foreach_get("vector", normals)
        attributes = mesh.attributes

        def bools(name, domain_len):
            if name not in attributes:
                return None
            values = [False] * domain_len
            attributes[name].data.foreach_get("value", values)
            return values

        result.append({"normals": normals, "sharp_edge": bools("sharp_edge", len(mesh.edges)),
                       "sharp_face": bools("sharp_face", len(mesh.polygons)),
                       "pointer": ob_eval.data.as_pointer()})
    return result


def same_shading(before, after):
    return all(b["normals"] == a["normals"] and b["sharp_edge"] == a["sharp_edge"]
               and b["sharp_face"] == a["sharp_face"] for b, a in zip(before, after, strict=True))


def bake(objects, override=False):
    """(result set, error text) of the operator on `objects` selected (or passed as the selection)."""
    try:
        if override:
            with bpy.context.temp_override(selected_editable_objects=objects):
                return bpy.ops.object.shade_auto_smooth_bake(), ""
        select(objects)
        return bpy.ops.object.shade_auto_smooth_bake(), ""
    except RuntimeError as error:
        return {'CANCELLED'}, str(error)


def exact_case(name, make_mesh, angle, count=40, ignore_sharpness=False, hidden=False, selected=None,
               flat_every=0):
    reset()
    mesh = make_mesh(name)
    objects = users_of(mesh, count)
    modifiers = auto_smooth(objects, angle)
    if ignore_sharpness:
        for modifier in modifiers:
            set_input(modifier, "Ignore Sharpness", True)
    if flat_every:
        # Shade Auto Smooth shades every face smooth; flat faces made afterwards must stay flat.
        for index in range(0, len(mesh.polygons), flat_every):  # A bpy collection takes no slice step.
            mesh.polygons[index].use_smooth = False
        mesh.update()
    before = shading(objects)
    # Hidden users are not in the viewport's graph (the bake evaluates them in its own), so they are
    # compared while visible, and passed as the selection the way a script would.
    for ob in objects:
        ob.hide_viewport = hidden
    result, error = bake(objects[:selected] if selected else objects, override=hidden)
    for ob in objects:
        ob.hide_viewport = False
    after = shading(objects)
    observed[name] = {"result": sorted(result), "error": error, "users": count,
                      "evaluated_meshes_after": len({entry["pointer"] for entry in after})}
    return (result == {'FINISHED'} and same_shading(before, after)
            and all(len(ob.modifiers) == 0 for ob in objects)
            and len({entry["pointer"] for entry in after}) == 1)


def refusal_case(name, prepare, expect_in_error):
    """Builds 20 users with Smooth by Angle, lets `prepare` break one condition, and expects a refusal."""
    reset()
    mesh = cylinder_and_sphere(name)
    objects = users_of(mesh, 20)
    auto_smooth(objects, math.radians(30.0))
    prepare(mesh, objects)
    modifiers_before = [[modifier.name for modifier in ob.modifiers] for ob in objects]
    sharp_before = "sharp_edge" in mesh.attributes
    result, error = bake(objects)
    observed[name] = {"result": sorted(result), "error": error}
    return (result == {'CANCELLED'} and expect_in_error in error
            and [[modifier.name for modifier in ob.modifiers] for ob in objects] == modifiers_before
            and ("sharp_edge" in mesh.attributes) == sharp_before)


def reevaluation_seconds(objects, repeats=5):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    samples = []
    for _ in range(repeats):
        for ob in objects:
            ob.update_tag(refresh={'DATA'})
        start = time.perf_counter()
        depsgraph.update()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


try:
    # Exact on held-out meshes, angles and inputs.
    checks["exact_shared_cylinder_30"] = exact_case("cylinder_30", cylinder_and_sphere, math.radians(30.0))
    checks["exact_shared_cylinder_60"] = exact_case("cylinder_60", cylinder_and_sphere, math.radians(60.0))
    checks["exact_noisy_grid_flat_faces"] = exact_case("noisy_grid", noisy_grid,
                                                       math.radians(20.0), flat_every=7)
    checks["exact_custom_normals"] = exact_case("custom_normals", custom_normals_cube, math.radians(30.0))
    checks["exact_ignore_sharpness"] = exact_case("ignore_sharpness", cylinder_and_sphere, math.radians(45.0),
                                                  ignore_sharpness=True)
    checks["exact_single_user"] = exact_case("single_user", cylinder_and_sphere, math.radians(30.0), count=1)
    # Unselected users lose the modifier too; hidden users are evaluated without the viewport.
    checks["unselected_users_baked"] = exact_case("unselected", cylinder_and_sphere, math.radians(30.0),
                                                  selected=1)
    checks["hidden_users_baked"] = exact_case("hidden", cylinder_and_sphere, math.radians(30.0), hidden=True)

    # Refusals: nothing changes, and the reason is named.
    def different_angle(mesh, objects):
        set_input(objects[3].modifiers[0], "Angle", math.radians(50.0))

    def other_modifier_first(mesh, objects):
        objects[5].modifiers.new("Bevel", 'BEVEL')
        bpy.context.view_layer.objects.active = objects[5]
        bpy.ops.object.modifier_move_to_index(modifier="Bevel", index=0)

    def animated_angle(mesh, objects):
        modifier = objects[2].modifiers[0]
        action = bpy.data.actions.new("Angle")
        objects[2].animation_data_create().action = action
        action.fcurve_ensure_for_datablock(
            objects[2], data_path=f'modifiers["{modifier.name}"]["{angle_input(modifier)}"]')

    def shape_keys(mesh, objects):
        objects[0].shape_key_add(name="Basis")

    def armature_parent(mesh, objects):
        armature = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
        bpy.context.scene.collection.objects.link(armature)
        objects[1].parent = armature
        objects[1].parent_type = 'ARMATURE'

    def disabled_for_render(mesh, objects):
        objects[4].modifiers[0].show_render = False

    def hidden_in_edit_mode(mesh, objects):
        objects[6].modifiers[0].show_in_editmode = False

    def another_user_in_sculpt_mode(mesh, objects):
        bpy.context.view_layer.objects.active = objects[7]
        bpy.ops.object.mode_set(mode='SCULPT')
        # Another object becomes active while this one stays in Sculpt Mode.
        bpy.context.view_layer.objects.active = objects[0]

    checks["refuses_different_settings"] = refusal_case("different_angle", different_angle,
                                                        "different Smooth by Angle settings")
    checks["refuses_other_modifier_first"] = refusal_case("modifier_first", other_modifier_first,
                                                          "first modifier")
    checks["refuses_animated_angle"] = refusal_case("animated", animated_angle, "is animated")
    checks["refuses_shape_keys"] = refusal_case("shape_keys", shape_keys, "shape keys")
    checks["refuses_parent_deformation"] = refusal_case("armature_parent", armature_parent, "deformed by its parent")
    checks["refuses_disabled_for_render"] = refusal_case("render_off", disabled_for_render,
                                                         "viewport, render and Edit Mode")
    checks["refuses_hidden_in_edit_mode"] = refusal_case("edit_mode_off", hidden_in_edit_mode,
                                                         "viewport, render and Edit Mode")
    checks["refuses_a_user_in_sculpt_mode"] = refusal_case("sculpt_user", another_user_in_sculpt_mode,
                                                           "is not in Object Mode")

    # Held out: an editable copy of Smooth by Angle, as files made before 5.0 hold (Shade Auto Smooth appended
    # the asset; an append records a library weak reference, which is how the copy is recognized). Unedited it
    # bakes exactly; edited, it is refused for the edit's own reason, the mesh and modifiers unchanged.
    def appended_copy(objects):
        # system_resource resolves folders only; the library is a file inside one.
        library = os.path.join(bpy.utils.system_resource('DATAFILES', path="assets/nodes"),
                               "geometry_nodes_essentials.blend")
        known = set(bpy.data.node_groups)
        bpy.ops.wm.append(directory=library + "/NodeTree/", filename="Smooth by Angle", do_reuse_local_id=True)
        group = next(group for group in bpy.data.node_groups if group not in known and group.name.startswith(
            "Smooth by Angle"))
        for ob in objects:
            ob.modifiers.new("Smooth by Angle", 'NODES').node_group = group
        return group

    def appended_copy_case(edit):
        reset()
        mesh = cylinder_and_sphere("appended_" + edit)
        objects = users_of(mesh, 4)
        group = appended_copy(objects)
        if edit == "store_attribute":
            node = group.nodes.new('GeometryNodeStoreNamedAttribute')
            node.inputs["Name"].default_value = "edited"
        elif edit == "set_material":
            node = group.nodes.new('GeometryNodeSetMaterial')
            node.inputs["Material"].default_value = bpy.data.materials.new("Edited")
        if edit != "unedited":
            output = next(node for node in group.nodes if node.bl_idname == 'NodeGroupOutput')
            link = next(link for link in group.links if link.to_node == output)
            source = link.from_socket
            group.links.new(source, node.inputs[0])
            group.links.new(node.outputs[0], output.inputs[0])
        before = shading(objects)
        attributes_before = sorted(mesh.attributes.keys())
        result, error = bake(objects)
        observed["appended_copy_" + edit] = {"result": sorted(result), "error": error, "local": group.library is None}
        if edit == "unedited":
            return (group.library is None and result == {'FINISHED'} and same_shading(before, shading(objects))
                    and all(len(ob.modifiers) == 0 for ob in objects))
        expected = {"store_attribute": "changes more than the shading", "set_material": "depends on more than its mesh"}
        return (group.library is None and result == {'CANCELLED'} and expected[edit] in error
                and all(len(ob.modifiers) == 1 for ob in objects) and sorted(mesh.attributes.keys()) == attributes_before)

    checks["bakes_an_unedited_appended_copy"] = appended_copy_case("unedited")
    checks["refuses_an_appended_copy_that_stores_an_attribute"] = appended_copy_case("store_attribute")
    checks["refuses_an_appended_copy_that_sets_a_material"] = appended_copy_case("set_material")

    # Held out: animation inside the node group, not on the modifier. An editable copy scales its Angle by a
    # Math node, directly or in a nested group; keyed by the group's action or driven by the frame, the shading
    # changes between frames, so the bake is refused. Unanimated, the same edit bakes exactly.
    def angle_scaled_case(animation):
        reset()
        mesh = cylinder_and_sphere("scaled_" + animation)
        objects = users_of(mesh, 4)
        group = appended_copy(objects)
        owner = group
        if animation.startswith("nested"):
            owner = bpy.data.node_groups.new("Angle scale", 'GeometryNodeTree')
            owner.interface.new_socket("Value", in_out='INPUT', socket_type='NodeSocketFloat')
            owner.interface.new_socket("Value", in_out='OUTPUT', socket_type='NodeSocketFloat')
        math_node = owner.nodes.new('ShaderNodeMath')
        math_node.operation = 'MULTIPLY'
        math_node.inputs[1].default_value = 1.0
        if owner is group:
            scale_in, scale_out = math_node.inputs[0], math_node.outputs[0]
        else:
            owner.links.new(owner.nodes.new('NodeGroupInput').outputs[0], math_node.inputs[0])
            owner.links.new(math_node.outputs[0], owner.nodes.new('NodeGroupOutput').inputs[0])
            nested = group.nodes.new('GeometryNodeGroup')
            nested.node_tree = owner
            scale_in, scale_out = nested.inputs[0], nested.outputs[0]
        group_input = next(node for node in group.nodes if node.bl_idname == 'NodeGroupInput')
        angle_links = [(link.from_socket, link.to_socket) for link in group.links
                       if link.from_node == group_input and link.from_socket.name == "Angle"]
        for angle, consumer in angle_links:
            group.links.new(angle, scale_in)
            group.links.new(scale_out, consumer)
        factor = math_node.inputs[1]
        if animation.endswith("action"):
            factor.keyframe_insert("default_value", frame=1)
            factor.default_value = 2.0
            factor.keyframe_insert("default_value", frame=20)
            factor.default_value = 1.0
        elif animation.endswith("driver"):
            factor.driver_add("default_value").driver.expression = "frame / 10"
        before = shading(objects)
        attributes_before = sorted(mesh.attributes.keys())
        result, error = bake(objects)
        observed["angle_scaled_" + animation] = {"result": sorted(result), "error": error,
                                                 "animated_tree": owner.name if owner.animation_data else None}
        if animation == "still":
            return result == {'FINISHED'} and same_shading(before, shading(objects))
        return (result == {'CANCELLED'} and "is animated" in error
                and all(len(ob.modifiers) == 1 for ob in objects) and sorted(mesh.attributes.keys()) == attributes_before)

    checks["bakes_an_unanimated_edit_inside_the_group"] = angle_scaled_case("still")
    checks["refuses_a_group_keyed_by_its_action"] = angle_scaled_case("action")
    checks["refuses_a_group_driven_by_the_frame"] = angle_scaled_case("driver")
    checks["refuses_a_nested_group_keyed_by_its_action"] = angle_scaled_case("nested_action")
    checks["refuses_a_nested_group_driven_by_the_frame"] = angle_scaled_case("nested_driver")

    # A hand-made group that only looks like Smooth by Angle is not recognized.
    reset()
    mesh = cylinder_and_sphere("custom_group")
    objects = users_of(mesh, 3)
    group = bpy.data.node_groups.new("Smooth by Angle", 'GeometryNodeTree')
    group.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    group.links.new(group.nodes.new("NodeGroupInput").outputs[0], group.nodes.new("NodeGroupOutput").inputs[0])
    for ob in objects:
        ob.modifiers.new("Smooth by Angle", 'NODES').node_group = group
    result, error = bake(objects)
    checks["refuses_unrecognized_group"] = result == {'CANCELLED'} and all(len(ob.modifiers) == 1 for ob in objects)

    # Mixed selection: the bakeable mesh is baked, the other one is reported and left alone.
    reset()
    good, bad = cylinder_and_sphere("good"), cylinder_and_sphere("bad")
    good_users, bad_users = users_of(good, 5), users_of(bad, 5)
    auto_smooth(good_users + bad_users, math.radians(30.0))
    bad_users[0].modifiers.new("Bevel", 'BEVEL')
    bpy.context.view_layer.objects.active = bad_users[0]
    bpy.ops.object.modifier_move_to_index(modifier="Bevel", index=0)
    result, error = bake(good_users + bad_users)
    observed["mixed"] = {"result": sorted(result), "error": error}
    checks["mixed_selection_bakes_what_it_can"] = (
        result == {'FINISHED'} and all(len(ob.modifiers) == 0 for ob in good_users)
        and all(len(ob.modifiers) >= 1 for ob in bad_users))

    # One Undo step restores every modifier.
    reset()
    mesh = cylinder_and_sphere("undo")
    objects = users_of(mesh, 10)
    auto_smooth(objects, math.radians(30.0))
    names = [ob.name for ob in objects]
    # Background Blender builds no Undo history (a load frees it; only a window starts it again). Keep the
    # document as a window does, as Angelo's headless host does, so Undo works as in the app.
    from _bpy import _angelo_undo
    bpy.context.preferences.edit.use_global_undo = True
    checks["undo_history_kept"] = bool(_angelo_undo.keep_background_document())
    bpy.ops.ed.undo_push(message="Before bake")
    bake(objects)
    baked = all(len(bpy.data.objects[name].modifiers) == 0 for name in names)
    bpy.ops.ed.undo()
    restored = all(len(bpy.data.objects[name].modifiers) == 1 for name in names)
    observed["undo"] = {"baked": baked, "restored": restored}
    checks["one_undo_step_restores_modifiers"] = baked and restored

    # Apply on a shared mesh names the operator.
    reset()
    mesh = cylinder_and_sphere("apply")
    objects = users_of(mesh, 2)
    modifiers = auto_smooth(objects, math.radians(30.0))
    bpy.context.view_layer.objects.active = objects[0]
    try:
        bpy.ops.object.modifier_apply(modifier=modifiers[0].name)
        apply_error = ""
    except RuntimeError as error:
        apply_error = str(error)
    observed["apply_error"] = apply_error
    checks["apply_refusal_names_the_operator"] = "Bake Auto Smooth" in apply_error
    # Not suggested where Bake would refuse: another modifier comes first.
    objects[0].modifiers.new("Bevel", 'BEVEL')
    bpy.ops.object.modifier_move_to_index(modifier="Bevel", index=0)
    try:
        bpy.ops.object.modifier_apply(modifier=modifiers[0].name)
        later_error = ""
    except RuntimeError as error:
        later_error = str(error)
    observed["apply_error_not_first"] = later_error
    checks["apply_refusal_suggests_it_only_when_first"] = ("multi-user" in later_error
                                                           and "Bake Auto Smooth" not in later_error)

    # The operator is in the Object menu and the object context menu, after Shade Auto Smooth.
    import inspect
    from bl_ui import space_view3d
    menus = {name: inspect.getsource(getattr(space_view3d, name).draw)
             for name in ("VIEW3D_MT_object", "VIEW3D_MT_object_context_menu")}
    checks["offered_after_shade_auto_smooth"] = all(
        source.index("object.shade_auto_smooth_bake") > source.index('"object.shade_auto_smooth"')
        for source in menus.values())

    # 10,000 objects sharing one mesh: before (one modifier each) and after the bake.
    reset()
    mesh = cylinder_and_sphere("many")
    objects = users_of(mesh, 10000)
    auto_smooth(objects, math.radians(30.0))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    before_meshes = len({ob.evaluated_get(depsgraph).data.as_pointer() for ob in objects})
    before_seconds = reevaluation_seconds(objects)
    select(objects)
    start = time.perf_counter()
    result = bpy.ops.object.shade_auto_smooth_bake()
    bake_seconds = time.perf_counter() - start
    depsgraph = bpy.context.evaluated_depsgraph_get()
    after_meshes = len({ob.evaluated_get(depsgraph).data.as_pointer() for ob in objects})
    after_seconds = reevaluation_seconds(objects)
    observed["ten_thousand"] = {"evaluated_meshes_before": before_meshes, "evaluated_meshes_after": after_meshes,
                                "reevaluate_s_before": before_seconds, "reevaluate_s_after": after_seconds,
                                "bake_s": bake_seconds, "faces": len(mesh.polygons),
                                "note": "timings are informational; compare runs only on a quiet machine"}
    checks["ten_thousand_users_evaluate_one_mesh"] = (result == {'FINISHED'} and before_meshes == 10000
                                                      and after_meshes == 1)
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("BAKE_AUTO_SMOOTH_BACKGROUND failed checks:", json.dumps(failed))
assert report["success"], failed
