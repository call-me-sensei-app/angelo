# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the host kept or refused wrongly before, measured through Blender.

* A material edited in place is edited again by a later program: the mesh an earlier commit left without users
  is no user of it (``material_sharing.kept``), for ``material_principled``, ``node_edit`` and ``property_set``.
* An artist's unkeyed edit of a keyed channel the program does not write stays (``kept_values``): through a
  transform of the object, a key insert on another channel, and a program's own unkeyed write before a key.
* A material that came to an object in the program, or that the program created and shared, is refused before
  approval with what works instead (``program_material_obligations.shared_in_program``).
* A constraint's own ``track_axis`` is a setting, not the object's transform (``rna_rules.screen_path``).
* ``keyframe_insert`` without a frame keys the current one; a UV kind without a map uses the active one.
* An object an Average driver drives binds and commits; a scripted driver's still refuses.
* Adversarial cases: an indexed Python-getter driver refuses, a failed commit's rollback keeps unkeyed edits,
  a muted link drives nothing, a joined or animated material is refused before approval, a node's place is no
  writable setting.

Run in a disposable instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-kept-values-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle():  # No frame change: that would replace the artist's unkeyed edits before the program runs.
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="kept values")
    queries.invalidate()


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def op(kind, owner, /, **parameters):
    return {"kind": kind, "target": owner if isinstance(owner, str) or owner is None else queries.object_handle(owner),
            "parameters": parameters}


def commit(name, build, writes, reads=()):
    """Approve, stage and commit ``build()`` as the controller does; ``(status, error, result)``, recorded."""
    settle()
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [entry(obj) for obj in reads],
                                         "write_set": [entry(obj) for obj in writes], "operations": build()})
        approved = native.precondition(window, NAME, arguments)
    except ValueError as error:
        report["cases"][name] = ["refused before approval", str(error)[:300]]
        return "refused before approval", str(error), {}
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        staged = job.result or staged
    bpy.context.view_layer.update()
    report["cases"][name] = [staged.get("status"), str(staged.get("error") or "")[:300]]
    return staged.get("status"), str(staged.get("error") or ""), staged


def mesh(name, location=(0, 0, 0)):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    window.scene.collection.objects.link(obj)
    return obj


def keyed_empty(name, paths=("location", "rotation_euler")):
    obj = bpy.data.objects.new(name, None)
    window.scene.collection.objects.link(obj)
    for value, key in ((0.0, 1), (1.0, 21)):
        obj.location.x = obj.rotation_euler.z = value
        for path in paths:
            obj.keyframe_insert(path, frame=key)
    window.scene.frame_set(1)
    return obj


def materials():
    window.scene = bpy.data.scenes.new("Materials")
    obj = mesh("Painted")
    obj.data.materials.append(bpy.data.materials.new("Paint"))
    for index, (kind, build) in enumerate((
            ("material_principled", lambda: [op("material_principled", obj, name="Paint", inputs={"Roughness": .3})]),
            ("material_principled", lambda: [op("material_principled", obj, name="Paint", inputs={"Metallic": .6})]),
            ("node_edit", lambda: [op("node_edit", obj, material="Paint", edits=[
                {"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": .7}}])]),
            ("property_set", lambda: [op("property_set", obj, values={"active_material.roughness": .4})]))):
        status, error, _ = commit("edit %d %s" % (index, kind), build, [obj])
        check("a_later_%s_of_a_material_edited_before_commits_%d" % (kind, index), status == "committed")
    shader = obj.material_slots[0].material.node_tree.nodes["Principled BSDF"]
    check("every_edit_stands", abs(shader.inputs["Roughness"].default_value - .7) < 1e-6
          and abs(shader.inputs["Metallic"].default_value - .6) < 1e-6)

    window.scene = bpy.data.scenes.new("Arrived")
    held = mesh("Holder")
    held.data.materials.append(bpy.data.materials.new("Held"))
    status, error, _ = commit("arrived", lambda: [
        op("create_primitive", None, primitive="PLANE", name="New", alias="new"),
        op("material_assign", "$new", material="Held", source=queries.object_handle(held)),
        op("material_principled", "$new", name="Held", inputs={"Roughness": .9})], [held])
    check("an_arrived_material_edit_is_refused_before_approval_with_a_remedy",
          status == "refused before approval" and "ARRIVED_IN_PROGRAM" in error and "the object it came from" in error)
    status, error, _ = commit("created_shared", lambda: [
        op("material_principled", held, name="Fresh", inputs={"Roughness": .2}),
        op("create_primitive", None, primitive="PLANE", name="Other", alias="other"),
        op("material_principled", "$other", name="Fresh", inputs={"Roughness": .2}),
        op("material_principled", "$other", name="Fresh", inputs={"Metallic": 1.0})], [held])
    check("a_created_shared_material_edit_is_refused_before_approval_with_a_remedy",
          status == "refused before approval" and "NODE_TREE_SHARED" in error and "ALL_USERS" in error)

    # Data nothing keeps that points at an object (a replaced material, say) does not keep it from being deleted;
    # data a fake user keeps still does.
    for fake in (False, True):
        window.scene = bpy.data.scenes.new("Delete %s" % fake)
        doomed = bpy.data.objects.new("Doomed %s" % fake, None)
        window.scene.collection.objects.link(doomed)
        holder = bpy.data.materials.new("Pointer %s" % fake)
        holder.node_tree.nodes.new("ShaderNodeTexCoord").object = doomed
        holder.use_fake_user = fake
        status, error, _ = commit("delete beside %s" % fake, lambda: [op("object_delete", doomed)], [doomed])
        gone = ("Doomed %s" % fake) not in bpy.data.objects
        # A fake user's datablock is refused when the program is bound, before approval (commit refused it after).
        check("data_a_fake_user_keeps_holds_an_object_refused_before_approval" if fake
              else "data_nothing_keeps_holds_no_object",
              (status == "refused before approval" and "OBJECT_IN_USE" in error and "fake user" in error and not gone)
              if fake else (status == "committed" and gone))


def unkeyed():
    window.scene = bpy.data.scenes.new("Unkeyed")
    obj = keyed_empty("Transformed")
    obj.rotation_euler.z = 0.7  # The artist's unkeyed edit of a keyed channel.
    status, _, _ = commit("other_channel", lambda: [op("transform", obj, location=[3.0, 0.0, 0.0])], [obj])
    check("a_transform_keeps_an_unkeyed_edit_of_another_keyed_channel",
          status == "committed" and abs(obj.rotation_euler.z - 0.7) < 1e-6)
    keyed = keyed_empty("Keyed")
    keyed.rotation_euler.z = 0.6
    status, _, result = commit("key_after_artist", lambda: [op("keyframe_insert", keyed, property="location",
                                                              value=[2.0, 0.0, 0.0], frame=9)], [keyed])
    check("a_key_insert_keeps_an_unkeyed_edit_of_another_channel",
          status == "committed" and abs(keyed.rotation_euler.z - 0.6) < 1e-6)
    own = keyed_empty("Own")
    status, _, result = commit("key_after_own_write", lambda: [
        op("transform", own, rotation=[0.0, 0.0, 0.5]),
        op("keyframe_insert", own, property="location", value=[2.0, 0.0, 0.0], frame=9)], [own])
    stated = [(row["path"], row["index"]) for row in (result.get("unkeyed_writes") or {}).get("channels", [])]
    check("a_key_insert_keeps_the_programs_earlier_unkeyed_write_and_states_it",
          status == "committed" and abs(own.rotation_euler.z - 0.5) < 1e-6 and ("rotation_euler", 2) in stated)


def defaults_and_paths():
    window.scene = bpy.data.scenes.new("Defaults")
    window.scene.frame_set(7)
    obj = bpy.data.objects.new("Frame", None)
    window.scene.collection.objects.link(obj)
    status, _, _ = commit("current_frame", lambda: [op("keyframe_insert", obj, property="location",
                                                       value=[1.0, 0.0, 0.0])], [obj])
    frames = [key.co.x for bag in obj.animation_data.action.layers[0].strips[0].channelbags
              for curve in bag.fcurves for key in curve.keyframe_points] if obj.animation_data else []
    check("a_key_without_a_frame_is_at_the_current_frame", status == "committed" and frames and set(frames) == {7.0})
    surface = mesh("Unwrapped")
    surface.data.uv_layers.new(name="Painted UV")
    status, _, _ = commit("active_uv", lambda: [op("uv_smart_project", surface)], [surface])
    check("a_uv_kind_without_a_map_uses_the_active_one", status == "committed"
          and [layer.name for layer in surface.data.uv_layers] == ["Painted UV"])
    owner, target = mesh("Tracker"), mesh("Tracked", (0, 4, 0))
    constraint = owner.constraints.new("DAMPED_TRACK")
    constraint.name, constraint.target = "Aim", target
    status, error, _ = commit("track_axis", lambda: [op("constraint_configure", owner, constraint="Aim",
                                                        settings={"track_axis": "TRACK_X"})], [owner], [target])
    check("a_constraints_track_axis_is_its_own_setting", status == "committed"
          and owner.constraints["Aim"].track_axis == "TRACK_X")
    control, driven = bpy.data.objects.new("Control", None), bpy.data.objects.new("Driven", None)
    for item in (control, driven):
        window.scene.collection.objects.link(item)
    control.location.x = 0.5
    curve = driven.driver_add("empty_display_size")
    variable = curve.driver.variables.new()
    variable.type, curve.driver.type = "TRANSFORMS", "AVERAGE"
    variable.targets[0].id, variable.targets[0].transform_type = control, "LOC_X"
    status, _, _ = commit("average_driver", lambda: [op("transform", driven, location=[1.0, 0.0, 0.0])], [driven],
                          [control])
    check("an_object_an_average_driver_drives_binds_and_commits", status == "committed"
          and abs(driven.empty_display_size - 0.5) < 1e-6)
    scripted = bpy.data.objects.new("Scripted", None)
    window.scene.collection.objects.link(scripted)
    other = scripted.driver_add("empty_display_size")
    other.driver.type, other.driver.expression = "SCRIPTED", "1.5"
    settle()
    try:
        entry(scripted)
        bound = True
    except Exception:  # noqa: BLE001 - the exact revision refuses it
        bound = False
    check("a_scripted_driver_still_refuses", not bound)


def finding_nine():
    """Edit, assign, edit again: refused before approval exactly where it failed after approval, with what works
    instead; the shapes that committed still commit, with no silent per-object copy."""
    def material_case(label):
        first, second = mesh(label + " A"), mesh(label + " B")
        first.data.materials.append(bpy.data.materials.new(label))
        return first, second

    def principled(obj, name, shared="REFUSE", **inputs):
        return op("material_principled", obj, name=name, inputs=inputs, shared=shared)

    def assign(obj, name, source):
        return op("material_assign", obj, material=name, source=queries.object_handle(source))
    a, b = material_case("Again")
    status, error, _ = commit("edit_assign_edit", lambda: [principled(a, "Again", Roughness=.3), assign(b, "Again", a),
                                                           principled(a, "Again", Metallic=.9)], [a, b])
    check("an_edit_of_an_edited_material_another_object_got_is_refused_before_approval",
          status == "refused before approval" and "NODE_TREE_SHARED" in error and "ALL_USERS" in error
          and "before assigning" in error)
    a, b = material_case("Together")
    status, _, _ = commit("edit_assign_edit_all", lambda: [principled(a, "Together", Roughness=.3),
                                                           assign(b, "Together", a),
                                                           principled(a, "Together", "ALL_USERS", Metallic=.9)], [a, b])
    shown = [obj.material_slots[0].material for obj in (a, b)]
    check("the_same_edit_for_all_users_still_commits_on_one_material", status == "committed"
          and shown[0] == shown[1] and abs(shown[0].node_tree.nodes["Principled BSDF"].inputs["Metallic"].default_value
                                           - .9) < 1e-6)
    a, b = material_case("Copied")
    status, error, _ = commit("duplicate_isolate", lambda: [
        op("object_duplicate", None, source=queries.object_handle(a), alias="copy"),
        op("node_edit", a, material="Copied", shared="ISOLATE",
           edits=[{"op": "set", "node": "Principled BSDF", "inputs": {"Metallic": .9}}])], [a])
    check("isolating_a_material_a_copy_holds_is_refused_before_approval",
          status == "refused before approval" and "NODE_TREE_SHARED" in error and "isolate it before" in error)
    a, b = material_case("Duplicated")
    status, _, _ = commit("duplicate_edit_edit", lambda: [
        op("object_duplicate", None, source=queries.object_handle(a), alias="twin"),
        principled(a, "Duplicated", Roughness=.3), principled(a, "Duplicated", Metallic=.9)], [a])
    check("editing_twice_beside_a_duplicate_still_commits", status == "committed")


def review_round():
    """Adversarial reproductions, each handled."""
    window.scene = bpy.data.scenes.new("Review")
    bpy.types.Object.angelo_qa_vector = bpy.props.FloatVectorProperty(get=lambda self: (1.0, 2.0, 3.0), size=3)
    try:
        driven = bpy.data.objects.new("Getter", None)
        window.scene.collection.objects.link(driven)
        curve = driven.driver_add("empty_display_size")
        variable = curve.driver.variables.new()
        variable.type, curve.driver.type = "SINGLE_PROP", "SUM"
        variable.targets[0].id, variable.targets[0].data_path = driven, "angelo_qa_vector[0]"
        settle()
        try:
            entry(driven)
            bound = True
        except Exception:  # noqa: BLE001 - the exact revision refuses it
            bound = False
        check("a_driver_reading_an_index_of_a_python_getter_still_refuses", not bound)
    finally:
        del bpy.types.Object.angelo_qa_vector

    keyed = keyed_empty("Rolled")
    keyed.rotation_euler.z = 0.7
    from unittest.mock import patch
    from angelo_agent import commit_fidelity
    from angelo_agent.program_jobs import ProgramError

    def refused(*args, **kwargs):  # A commit that fails after its transfers and their update, then rolls back.
        raise ProgramError("forced commit failure", code="INTERNAL_ERROR")
    with patch.object(commit_fidelity, "verify", refused):
        status, error, _ = commit("rolled_back", lambda: [op("transform", keyed, location=[2.0, 0.0, 0.0])], [keyed])
    check("a_failed_commits_rollback_keeps_an_unkeyed_edit", status != "committed" and "forced" in error
          and abs(keyed.rotation_euler.z - 0.7) < 1e-6)

    muted = mesh("Muted")
    material = bpy.data.materials.new("Muted Link")
    muted.data.materials.append(material)
    tree = material.node_tree
    link = tree.links.new(tree.nodes.new("ShaderNodeRGB").outputs[0], tree.nodes["Principled BSDF"].inputs["Base Color"])
    link.is_muted = True
    status, error, _ = commit("muted_link", lambda: [op("material_principled", muted, name="Muted Link",
                                                        inputs={"Base Color": [0.1, 0.2, 0.3, 1.0]})], [muted])
    check("a_muted_link_does_not_drive_an_input", status == "committed")

    joined, source = mesh("Joined"), mesh("Source", (3, 0, 0))
    joined.data.materials.append(bpy.data.materials.new("Kept"))
    source.data.materials.append(bpy.data.materials.new("Brought"))
    status, error, _ = commit("joined", lambda: [op("object_join", joined, sources=[queries.object_handle(source)]),
                                                 op("material_principled", joined, name="Brought",
                                                    inputs={"Roughness": .4})], [joined, source])
    check("a_material_a_join_brought_is_refused_before_approval",
          status == "refused before approval" and "ARRIVED_IN_PROGRAM" in error)

    animated = mesh("Animated")
    keyed_material = bpy.data.materials.new("Keyed Tree")
    animated.data.materials.append(keyed_material)
    socket = keyed_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"]
    for value, frame in ((0.1, 1), (0.9, 21)):
        socket.default_value = value
        socket.keyframe_insert("default_value", frame=frame)
    status, error, _ = commit("animated_tree", lambda: [op("material_principled", animated, name="Keyed Tree",
                                                           inputs={"Metallic": .5})], [animated])
    check("an_animated_tree_is_refused_before_approval", status == "refused before approval" and "ANIMATED_TREE" in error)

    finding_nine()
    from angelo_agent.node_graph_check import writable_paths
    check("a_nodes_place_is_no_writable_setting", "location" not in writable_paths(tree.nodes["Principled BSDF"]))


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    for part in (materials, unkeyed, defaults_and_paths, review_round):
        try:
            part()
        except AssertionError:
            report.setdefault("failed_parts", []).append(part.__name__ + ": " + traceback.format_exc()[-400:])
    check("every_part_passed", not report.get("failed_parts"))
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "kept-values.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_KEPT_VALUES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
