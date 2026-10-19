# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable native freshness regressions and production two-turn Noise edit.

Use the exact Angelo binary, --background --factory-startup --python-exit-code 1,
and fresh config/account/state/recovery directories. No user projects/providers.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

import bpy
import bmesh

assert "--factory-startup" in sys.argv and bpy.app.background
# test_blender_runtime_recovery.py accepts the same names: Angelo, or angelo.exe on Windows.
assert Path(bpy.app.binary_path).name.lower() in {"angelo", "angelo.exe"}, "Do not use the adjacent stale Blender.app"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-scene-query-freshness"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent.blender_tools import track_scene_changes
from angelo_agent.scene_read_fields import (CONSTRAINT_COMMON_FIELDS, CONSTRAINT_TYPE_FIELDS,
                                           NODE_FIELDS, TEXTURE_MAPPING_FIELDS, COLOR_MAPPING_FIELDS)

window = bpy.context.window_manager.windows[0]
report = {"checks": {}, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "opensubdiv": bool(bpy.app.build_options.opensubdiv), "paid_requests": 0, "user_projects_opened": 0}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def expect_error(name, function, text):
    try:
        function()
    except ValueError as error:
        check(name, text in str(error))
    else:
        check(name, False)


def observe():
    return queries.execute(window, "angelo_scene_search", {})


def detail(obj, sections):
    return queries.execute(window, "angelo_scene_object", {"object": queries.object_handle(obj), "sections": sections})


def changed(token, obj, field):
    result = queries.execute(window, "angelo_scene_delta", {"observation_token": token})
    return result["delta_exact"] and any(field in row.get("fields", ()) and row["object"]["object"] == queries.object_handle(obj)
                                         for row in result["changes"])


def empty(name):
    obj = bpy.data.objects.new(name, None)
    window.scene.collection.objects.link(obj)
    return obj


def semantic_properties(owner):
    # Test-only schema inspection challenges the closed production allowlists.
    # Production readers never walk RNA/custom properties this way.
    return {p.identifier for p in owner.bl_rna.properties if not p.is_readonly}


def mutate_field(owner, field, target=None):
    prop = owner.bl_rna.properties[field]
    old = getattr(owner, field)
    restore = old.copy() if type(old).__name__ == "Matrix" else list(old) if getattr(prop, "is_array", False) else old
    if prop.type == "BOOLEAN":
        value = not old
    elif prop.type == "STRING":
        value = "Freshness field probe"
    elif prop.type == "POINTER":
        value = target
    elif prop.type == "ENUM":
        value = next(item.identifier for item in prop.enum_items if item.identifier != old)
        if field in {"owner_space", "target_space"}:
            value = "LOCAL" if old != "LOCAL" else "WORLD"
    elif prop.type in {"FLOAT", "INT"}:
        step = .125 if prop.type == "FLOAT" else 1
        if type(old).__name__ == "Matrix":
            value = old.copy()
            value[0][0] += step
        elif prop.is_array:
            value = list(old)
            value[0] = max(prop.hard_min, min(prop.hard_max, value[0] + step if value[0] < prop.hard_max else value[0] - step))
        else:
            value = max(prop.hard_min, min(prop.hard_max, old + step if old < prop.hard_max else old - step))
    else:
        raise AssertionError("Uncovered semantic property: " + field)
    setattr(owner, field, value)
    return restore


def constraint_fields(obj):
    target = empty("Constraint dependency")
    counts = {}
    for kind, fields in CONSTRAINT_TYPE_FIELDS.items():
        constraint = obj.constraints.new(kind)
        admitted = set(CONSTRAINT_COMMON_FIELDS + fields) | {"name"}
        mutable = semantic_properties(constraint) - {"show_expanded", "active"}
        check("constraint_coverage_" + kind, not (mutable - admitted))
        count = 0
        for field in sorted(mutable):
            before = queries.object_revision(window, obj)
            old = mutate_field(constraint, field, target)
            check("constraint_revision_" + kind + "." + field, before != queries.object_revision(window, obj))
            setattr(constraint, field, old)
            count += 1
        counts[kind] = count
        obj.constraints.remove(constraint)
    report["constraint_fields_tested"] = counts

    # Exact original repro: flag changes no evaluated transform at this origin.
    con = obj.constraints.new("LIMIT_LOCATION")
    con.min_x, con.use_min_x = -1, False
    window.view_layer.update()
    baseline = observe()
    before_matrix = tuple(tuple(row) for row in obj.matrix_world)
    con.use_min_x = True
    window.view_layer.update()
    check("limit_flag_exact_delta_at_unchanged_matrix", before_matrix == tuple(tuple(row) for row in obj.matrix_world)
          and changed(baseline["observation_token"], obj, "rig"))
    obj.constraints.remove(con)
    # Generic constraint types are fingerprinted by their settings; one using a non-object datablock is not.
    unsupported = obj.constraints.new("ACTION")
    unsupported.action = bpy.data.actions.new("QA Constraint Action")
    expect_error("unadmitted_constraint_stays_fail_closed", lambda: queries.object_revision(window, obj), "constraint:ACTION")
    obj.constraints.remove(unsupported)
    bpy.data.objects.remove(target, do_unlink=True)


def live_edit_shading(obj):
    # These native attributes are translated to BMesh flags, not live arrays.
    for name, domain in (("sharp_face", "FACE"), ("sharp_edge", "EDGE"), ("uv_seam", "EDGE")):
        if obj.data.attributes.get(name) is None:
            obj.data.attributes.new(name, "BOOLEAN", domain)
    window.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        report["live_edit_attribute_names"] = [a.name for a in obj.data.attributes]
        face, edge = next(iter(bm.faces)), next(iter(bm.edges))
        for owner, field, name in ((face, "smooth", "face_smooth"), (edge, "smooth", "edge_sharp"), (edge, "seam", "edge_seam")):
            baseline = observe()
            before = queries.object_revision(window, obj)
            setattr(owner, field, not getattr(owner, field))  # No update_edit_mesh.
            check("unsynchronized_edit_revision_" + name, queries.object_revision(window, obj) != before)
            check("unsynchronized_edit_exact_delta_" + name, changed(baseline["observation_token"], obj, "geometry"))
        baseline = observe()
        face.smooth = not face.smooth
        bmesh.update_edit_mesh(obj.data)
        check("synchronized_edit_shading_delta", changed(baseline["observation_token"], obj, "geometry"))
        obj.data.attributes.new("Unadmitted live layer", "FLOAT", "POINT")
        expect_error("unknown_live_layer_stays_fail_closed", lambda: queries.object_revision(window, obj), "live_edit_custom_mesh_layers")
        obj.data.attributes.remove(obj.data.attributes["Unadmitted live layer"])
        check("read_queries_preserve_edit_mode", obj.mode == "EDIT")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def incoming_freshness():
    parent, child, user = empty("Parent"), empty("Child"), empty("Native incoming user")
    child.parent = parent
    user["unread_fixture_secret"] = "freshness-secret-must-not-emit"
    window.view_layer.update()
    first = detail(parent, ["RELATIONSHIPS"])
    revision = queries.object_revision(window, parent)
    child.name = "Renamed incoming child"
    window.view_layer.update()
    fresh = detail(parent, ["RELATIONSHIPS"])
    check("incoming_rename_fresh_with_real_depsgraph_handler", any(e["from"]["name"] == child.name for e in fresh["sections"]["relationships"])
          and first["object_revision"] == fresh["object_revision"] == revision)
    user["native_pointer"] = parent  # Unknown edit, deliberately no update callback.
    fresh = detail(parent, ["TRANSFORM", "RELATIONSHIPS"])
    check("incoming_native_pointer_addition_without_target_edit", any(e["kind"] == "ID_USER" and e["from"]["object"] == queries.object_handle(user)
          for e in fresh["sections"]["relationships"]) and queries.object_revision(window, parent) == revision)
    check("incoming_reads_never_emit_custom_property_values", "freshness-secret" not in json.dumps(fresh))
    del user["native_pointer"]
    child.parent = None
    fresh = detail(parent, ["RELATIONSHIPS"])
    check("incoming_native_pointer_and_parent_removal_are_fresh", not any(e["direction"] == "USE" for e in fresh["sections"]["relationships"]))
    bpy.data.objects.remove(child, do_unlink=True)
    check("incoming_deleted_user_is_absent", not any(e["direction"] == "USE" for e in detail(parent, ["RELATIONSHIPS"])["sections"]["relationships"]))
    for obj in (parent, user):
        bpy.data.objects.remove(obj, do_unlink=True)


def rich_relation_cache(source):
    child, user = empty("Rich cache child"), empty("Rich cache native user")
    child.parent = source
    window.view_layer.update()
    sections = ["MESH", "RELATIONSHIPS"]
    revision = queries.object_revision(window, source)
    order = []
    real_read, real_cached = queries._relations.read, queries._cached
    def read(*args):
        order.append("read")
        return real_read(*args)
    def cached(key):
        if key[1] == "detail":
            order.append("cache")
        return real_cached(key)
    with patch.object(queries._relations, "read", side_effect=read) as relations, \
         patch.object(queries, "_cached", side_effect=cached), \
         patch.object(queries._geometry, "mesh", wraps=queries._geometry.mesh) as mesh:
        detail(source, sections)
        warm = detail(source, sections)
    check("rich_warm_cache_reads_relations_before_every_lookup", order == ["read", "cache", "read", "cache"] and relations.call_count == 2)
    check("rich_warm_cache_skips_only_proven_unchanged_mesh_detail", sum(c.kwargs.get("details", False) for c in mesh.call_args_list) == 1
          and warm["sections"]["mesh"]["vertices"] == len(source.data.vertices))
    child.name = "Missed callback child rename"  # No update/invalidation callback.
    with patch.object(queries._geometry, "mesh", wraps=queries._geometry.mesh) as mesh:
        fresh = detail(source, sections)
    check("rich_cached_incoming_rename_is_fresh", any(e["from"]["name"] == child.name for e in fresh["sections"]["relationships"])
          and sum(c.kwargs.get("details", False) for c in mesh.call_args_list) == 1 and queries.object_revision(window, source) == revision)
    child.parent = None
    constraint = child.constraints.new("COPY_LOCATION")
    constraint.target = source
    fresh = detail(source, sections)
    check("rich_cached_incoming_target_addition_is_fresh", any(e["kind"] == "CONSTRAINT" and e["direction"] == "USE" for e in fresh["sections"]["relationships"]))
    constraint.target = user
    check("rich_cached_incoming_target_switch_is_fresh", not any(e["direction"] == "USE" for e in detail(source, sections)["sections"]["relationships"]))
    user["native_pointer"] = source
    check("rich_cached_native_user_addition_is_fresh", any(e["kind"] == "ID_USER" for e in detail(source, sections)["sections"]["relationships"]))
    del user["native_pointer"]
    check("rich_cached_native_user_removal_is_fresh", not any(e["direction"] == "USE" for e in detail(source, sections)["sections"]["relationships"]))
    def exhausted(*args):
        args[4].items = 0  # Exhaust only the relationship reader, not the target.
        return real_read(*args)
    with patch.object(queries._relations, "read", side_effect=exhausted) as relations, \
         patch.object(queries._geometry, "mesh", wraps=queries._geometry.mesh) as mesh:
        results = [detail(source, sections), detail(source, sections)]
    check("incomplete_relation_scan_cannot_hit_rich_detail_cache", relations.call_count == 2
          and sum(c.kwargs.get("details", False) for c in mesh.call_args_list) == 2)
    check("incomplete_relation_scan_reports_truthful_omission", all(r["truncated"] and
          {"field": "relationships", "reason": "work_budget"} in r["truncations"] for r in results))
    queries.invalidate(window=window)
    with patch.object(queries, "MAX_OBJECTS", 1):
        limited = detail(source, sections)
    check("bounded_relation_scan_is_not_cached_as_complete", limited["truncated"] and not any(k[1] == "detail" for k in queries._cache))
    for obj in (child, user):
        bpy.data.objects.remove(obj, do_unlink=True)


def pose_constraint_freshness(source):
    data = bpy.data.armatures.new("Pose field coverage")
    rig = bpy.data.objects.new("Pose field coverage", data)
    window.scene.collection.objects.link(rig)
    source.select_set(False)
    rig.select_set(True)
    window.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = data.edit_bones.new("Bone")
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    for kind, field in (("COPY_SCALE", "power"), ("LIMIT_LOCATION", "use_max_z")):
        constraint = rig.pose.bones["Bone"].constraints.new(kind)
        before = queries.object_revision(window, rig)
        baseline = observe()
        mutate_field(constraint, field)
        check("pose_constraint_" + kind + "_revision_and_delta", before != queries.object_revision(window, rig)
              and changed(baseline["observation_token"], rig, "rig"))
        rig.pose.bones["Bone"].constraints.remove(constraint)
    bpy.data.objects.remove(rig, do_unlink=True)
    bpy.data.armatures.remove(data)
    source.select_set(True)
    window.view_layer.objects.active = source


def settle(job):
    for _ in range(200):
        outcome = job.poll()
        if outcome.get("final") or outcome.get("ready_to_commit"):
            return outcome
    raise AssertionError("Native job did not settle within bounded polling")


class SceneFreshnessCommit(bpy.types.Operator):
    bl_idname = "angelo.scene_freshness_commit"
    bl_label = "Disposable scene freshness commit"
    bl_options = {"REGISTER", "UNDO"}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        result = native.commit_job(self.program_id, context.window)
        report["last_commit"] = {key: value for key, value in result.items() if key != "delta"}
        return {"FINISHED"} if result.get("success") else {"CANCELLED"}


@bpy.app.handlers.persistent
def reset_queries(*_args):
    queries.invalidate()


def program(obj, operation, revision=None):
    handle = queries.object_handle(obj)
    return {"mode": "APPLY", "read_set": [], "write_set": [{"handle": handle, "revision": revision or queries.object_revision(window, obj)}],
            "operations": [dict(operation, target=handle)], "expected_checks": [{"kind": "finite_coordinates", "target": handle}]}


def commit_program(obj, operation, label):
    job = native.begin(window, "angelo_scene_program", program(obj, operation))  # Production scene query store, no FullStore adapter.
    intent = settle(job)
    check(label + "_ready_without_live_commit", intent.get("ready_to_commit") and not native.runtime_is_staging())
    result = bpy.ops.angelo.scene_freshness_commit("EXEC_DEFAULT", True, program_id=job.id)
    check(label + "_production_commit_verified", "FINISHED" in result and job.result["success"] and all(c["passed"] for c in job.result["checks"]))
    check(label + "_no_resident_stage_or_writer", not native.runtime_is_staging() and not native.has_active_programs())
    return job.result


def noise_workflow(obj):
    bpy.ops.ed.undo_push(message="Scene freshness production baseline")
    first = commit_program(obj, {"kind": "shader_nodes", "parameters": {"name": "Production Noise", "slot": 0,
                           "nodes": [{"id": "noise", "type": "TEX_NOISE"}, {"id": "shader", "type": "BSDF_PRINCIPLED"},
                                     {"id": "out", "type": "OUTPUT_MATERIAL"}],
                           "links": [{"from_node": "noise", "from_socket": "Fac", "to_node": "shader", "to_socket": "Roughness"},
                                     {"from_node": "shader", "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}]}}, "turn_one_noise")
    fresh = queries.object_revision(window, obj)
    check("catalog_noise_produces_next_turn_exact_revision", len(fresh) == 64)
    second = commit_program(obj, {"kind": "transform", "parameters": {"location": [3, 2, 1]}}, "turn_two_transform")
    graph = detail(obj, ["MATERIALS"])["sections"]["materials"][0]["node_tree"]
    check("second_turn_preserves_noise_graph_and_changes_transform", list(obj.location) == [3, 2, 1] and len(graph["links"]) == 2
          and any(n["type"] == "ShaderNodeTexNoise" for n in graph["nodes"]))
    report["production_program_ids"] = [first["program_id"], second["program_id"]]
    handle, name = queries.object_handle(obj), obj.name
    bpy.ops.ed.undo()
    obj = window.scene.objects[name]
    expect_error("production_undo_replaces_query_handles", lambda: queries.resolve_object(window, handle), "expired")
    check("turn_two_undo_preserves_turn_one_noise", list(obj.location) == [0, 0, 0]
          and obj.data.materials[0].node_tree.nodes.get("noise") is not None)
    bpy.ops.ed.redo()
    obj = window.scene.objects[name]
    check("turn_two_redo_and_fresh_query_revision", list(obj.location) == [3, 2, 1] and len(queries.object_revision(window, obj)) == 64)
    return obj, obj.data.materials[0].node_tree.nodes["noise"]


def noise_fields(obj, node):
    base = {p.identifier for p in bpy.types.Node.bl_rna.properties if not p.is_readonly}
    own = semantic_properties(node) - base
    # Base schema differences are tested by explicit controls below; the native
    # subclass adds exactly the Noise settings plus embedded readonly mappings.
    check("noise_semantic_controls_covered", {"noise_dimensions", "noise_type", "normalize"} <= set(NODE_FIELDS) and own <= set(NODE_FIELDS))
    for owner, fields, prefix in ((node, ("noise_dimensions", "noise_type", "normalize"), "noise"),
                                   (node.texture_mapping, TEXTURE_MAPPING_FIELDS, "texture_mapping"),
                                   (node.color_mapping, COLOR_MAPPING_FIELDS, "color_mapping")):
        if owner != node:
            check(prefix + "_native_field_coverage", semantic_properties(owner) <= set(fields) | {"color_ramp"})
        for field in fields:
            baseline = observe()
            before = queries.object_revision(window, obj)
            old = mutate_field(owner, field)
            check(prefix + "." + field + "_revision", before != queries.object_revision(window, obj))
            check(prefix + "." + field + "_delta", changed(baseline["observation_token"], obj, "materials"))
            setattr(owner, field, old)
    ramp = node.color_mapping.color_ramp
    check("noise_color_ramp_native_field_coverage", semantic_properties(ramp) <= {"interpolation", "color_mode", "hue_interpolation", "elements"})
    for field in ("interpolation", "color_mode", "hue_interpolation"):
        before = queries.object_revision(window, obj)
        old = mutate_field(ramp, field)
        check("noise_color_ramp_" + field, before != queries.object_revision(window, obj))
        setattr(ramp, field, old)
    before = queries.object_revision(window, obj)
    ramp.elements[0].color = [.1, .2, .3, 1]
    check("noise_color_ramp_elements_are_fingerprinted", before != queries.object_revision(window, obj))
    before = queries.object_revision(window, obj)
    element = ramp.elements.new(.25)
    check("noise_color_ramp_element_insertion", before != queries.object_revision(window, obj))
    before = queries.object_revision(window, obj)
    element.position = .4
    check("noise_color_ramp_element_position", before != queries.object_revision(window, obj))
    before = queries.object_revision(window, obj)
    ramp.elements.remove(element)
    check("noise_color_ramp_element_removal", before != queries.object_revision(window, obj))
    for socket in list(node.inputs) + list(node.outputs):
        before = queries.object_revision(window, obj)
        old = mutate_field(socket, "default_value")
        check("noise_socket_default_" + socket.name + ("_out" if socket.is_output else "_in"), before != queries.object_revision(window, obj))
        socket.default_value = old
    baseline = observe()
    tree = node.id_data
    link = next(link for link in tree.links if link.from_node == node)
    tree.links.remove(link)
    check("noise_link_removal_exact_delta", changed(baseline["observation_token"], obj, "materials"))
    old = queries.object_revision(window, obj)
    node.normalize = not node.normalize
    bad = native.begin(window, "angelo_scene_program", program(obj, {"kind": "transform", "parameters": {"location": [9, 9, 9]}}, old))
    result = settle(bad)
    check("stale_noise_revision_refuses_production_staging", not result["success"] and "revision changed" in result["error"]
          and list(obj.location) == [3, 2, 1] and not native.runtime_is_staging())
    with patch.object(queries, "MAX_METADATA_ITEMS", 1):
        expect_error("noise_overbudget_fails_closed", lambda: queries.object_revision(window, obj), "budget")
    unknown = tree.nodes.new("ShaderNodeScript")  # Generic node types are fingerprinted; denied ones never are.
    expect_error("unadmitted_shader_stays_fail_closed", lambda: queries.object_revision(window, obj), "unfingerprinted_node")
    tree.nodes.remove(unknown)


def main():
    started = time.perf_counter()
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    bpy.context.preferences.edit.use_global_undo = True
    source = window.scene.objects["Cube"]
    for obj in list(bpy.data.objects):
        if obj != source:
            bpy.data.objects.remove(obj, do_unlink=True)
    queries.invalidate()
    native.register()
    bpy.utils.register_class(SceneFreshnessCommit)
    bpy.app.handlers.depsgraph_update_post.append(track_scene_changes)
    bpy.app.handlers.undo_post.append(reset_queries)
    bpy.app.handlers.redo_post.append(reset_queries)
    try:
        constraint_fields(source)
        live_edit_shading(source)
        incoming_freshness()
        rich_relation_cache(source)
        pose_constraint_freshness(source)
        source, node = noise_workflow(source)
        noise_fields(source, node)
        report["completed"] = True
        report["instrumentation"] = dict(queries._counters)
    except Exception:
        report.update(completed=False, error=traceback.format_exc())
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(track_scene_changes)
        bpy.app.handlers.undo_post.remove(reset_queries)
        bpy.app.handlers.redo_post.remove(reset_queries)
        native.unregister()
        bpy.utils.unregister_class(SceneFreshnessCommit)
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_SCENE_QUERY_FRESHNESS", json.dumps(report), flush=True)
    assert report["completed"], report.get("error")


if __name__ == "__main__":
    main()
