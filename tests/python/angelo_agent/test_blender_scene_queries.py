# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable scene intelligence regressions; isolated factory-startup only.

Run the existing native binary --background --factory-startup --python FILE.
No user document, provider, credential/account, network or paid inference access.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import bmesh

assert "--factory-startup" in sys.argv, "Never run fixture mutations in a user project"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-scene-intelligence"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT/"accounts"),
                  ANGELO_STATE_DIR=str(OUT/"state"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]/"scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import scene_queries as queries

report = {"checks": {}, "paid_requests": 0, "user_projects_opened": 0,
          "binary_path": bpy.app.binary_path, "opensubdiv": bool(bpy.app.build_options.opensubdiv)}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def expect_error(name, function, contains):
    try:
        function()
    except ValueError as error:
        check(name, contains.casefold() in str(error).casefold())
    else:
        check(name, False)


def search(**args):
    return queries.execute(window, "angelo_scene_search", args)


def detail(obj, **args):
    return queries.execute(window, "angelo_scene_object", dict(object=queries.object_handle(obj), **args))


def delta(token, **args):
    return queries.execute(window, "angelo_scene_delta", dict(observation_token=token, **args))


def changed(result, handle, field):
    return any(row["change"] == "MODIFIED" and row["object"]["object"] == handle and field in row["fields"] for row in result["changes"])


def artist_state():
    return {"active": window.view_layer.objects.active.session_uid if window.view_layer.objects.active else None,
            "mode": bpy.context.mode, "frame": window.scene.frame_current,
            "objects": {o.session_uid: {"name": o.name, "mode": o.mode, "selected": o.select_get(),
                                        "hidden": o.hide_get(), "location": tuple(o.location), "keys": tuple(o.keys()),
                                        "data_keys": tuple(o.data.keys()) if o.data else (),
                                        "attribute_names": tuple(a.name for a in o.data.attributes) if o.type == "MESH" else ()}
                        for o in window.scene.objects},
            "id_counts": {kind: len(getattr(bpy.data, kind)) for kind in ("objects", "meshes", "materials", "actions", "armatures", "node_groups", "images")}}


def fixture():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    queries.invalidate()
    mesh = bpy.data.meshes.new("Bounded topology")
    mesh.from_pydata([(0,0,0), (1,0,0), (1,1,0), (0,1,0), (2,0,0), (3,0,0), (9,0,0)], [(4,5)], [(0,1,2,3)])
    mesh.update()
    obj = bpy.data.objects.new("Artist mesh", mesh)
    window.scene.collection.objects.link(obj)
    obj.select_set(True)
    window.view_layer.objects.active = obj
    shared = bpy.data.objects.new("Shared geometry", mesh)
    window.scene.collection.objects.link(shared)
    shared.location.y = 3
    for collection in (mesh.vertices, mesh.edges, mesh.polygons):
        for component in collection:
            component.select = False
    mesh.vertices[1].select = True
    mesh.edges[0].select = True
    mesh.polygons[0].select = True
    uv = mesh.uv_layers.new(name="Artist UV")
    for index, value in enumerate(uv.uv):
        value.vector = [(index & 1), (index >> 1)]
    uv.pin_ensure().data[0].value = True
    material = bpy.data.materials.new("Artist color")
    material.use_nodes = True
    material["do_not_observe"] = "fixture-secret-token-must-never-be-emitted"
    mesh.materials.append(material)
    obj["do_not_observe"] = "fixture-secret-token-must-never-be-emitted"
    mask = mesh.attributes.new(".sculpt_mask", "FLOAT", "POINT")
    mask.data[1].value = .5
    window.view_layer.update()
    return obj, shared, material


def run_queries():
    obj, shared, material = fixture()
    handle = queries.object_handle(obj)
    before = artist_state()
    observed = detail(obj)
    topology = observed["sections"]["mesh"]["topology"]
    check("original_topology_stats", topology["boundary_edges"] == 4 and topology["loose_edges"] == 1 and
          topology["isolated_vertices"] == 1 and topology["connected_components"] == 3 and topology["quads"] == 1)
    check("mesh_selected_components", observed["sections"]["mesh"]["selection"]["vertices"] == {"count": 1, "indices": [1]})
    check("uv_layers_and_pins", observed["sections"]["uv"][0]["name"] == "Artist UV" and observed["sections"]["uv"][0]["pinned"] == 1)
    check("readers_do_not_mutate_artist_state_or_custom_properties", before == artist_state())
    check("no_custom_properties_or_secrets_in_payload", "fixture-secret-token" not in json.dumps(observed))
    check("exact_supported_original_revision", observed["revision_exact"] and len(queries.object_revision(window, obj)) == 64)
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.tool_specs import COLLABORATOR_TOOLS, validate_call
    from angelo_agent.scene_spec import SCENE_NAMES
    dispatched = BlenderExecutor(window).execute("angelo_scene_search", {"selected": True})
    check("public_executor_dispatch", dispatched["objects"][0]["object"] == handle and dispatched["read_only"])
    check("public_readonly_collaborator_registry", SCENE_NAMES <= COLLABORATOR_TOOLS)
    # Claude plans defer these schemas; the real executor returns them exactly.
    executor = BlenderExecutor(window)
    deferred = executor.execute("angelo_agent_capabilities", {"family": "TOOLS",
                                                             "names": ["angelo_sculpt_view", "angelo_memory_update"]})
    check("deferred_tool_schema_lookup", [tool["name"] for tool in deferred["tools"]] ==
          ["angelo_sculpt_view", "angelo_memory_update"] and set(deferred["guidance"]) == {"SCULPT"}
          and deferred["scene_changed"] is False)
    actions = executor.execute("angelo_agent_capabilities", {"family": "ACTIONS", "names": ["light_create"]})
    check("deferred_action_schema_lookup", [entry["kind"] for entry in actions["actions"]] == ["light_create"]
          and "runtime" in actions and "unknown_names" not in actions)
    summary = executor.execute("angelo_agent_capabilities", {"family": "SUMMARY"})
    facts = {"runtime_features", "unavailable_in_this_build"}
    check("capability_results_do_not_repeat_the_catalog", set(summary["runtime"]) == facts == set(actions["runtime"])
          and summary["runtime"]["runtime_features"]["opensubdiv"] == bool(bpy.app.build_options.opensubdiv)
          and len(json.dumps(summary)) < 16000 and len(json.dumps(actions)) < 8000)
    report["capability_result_bytes"] = {label: len(json.dumps(value, separators=(",", ":")).encode())
                                         for label, value in (("SUMMARY", summary), ("ACTIONS light_create", actions),
                                                              ("TOOLS two", deferred))}
    index = executor.execute("angelo_agent_capabilities", {"family": "TOOLS"})
    check("deferred_tool_index", any(entry["name"] == "angelo_sculpt_batch" for entry in index["deferred_tools"]))
    named = executor.execute("angelo_agent_capabilities", {"family": "SUMMARY", "names": ["angelo_view", "bevel-and-shade"]})
    check("capability_names_in_every_family", [tool["name"] for tool in named.get("tools", [])] == ["angelo_view"]
          and [recipe["name"] for recipe in named.get("recipes", [])] == ["bevel-and-shade"]
          and "unknown_names" not in named)
    expect_error("public_validator_payload_abuse", lambda: validate_call("angelo_scene_object", {"object": ["not a handle"]}), "handle")
    repeated = detail(obj)
    check("immutable_cache_hits", repeated["cache"]["cache_hits"] > 0)
    observed["sections"]["transform"]["location"][0] = 987
    check("cache_return_values_not_aliased", detail(obj)["sections"]["transform"]["location"][0] == 0)

    baseline = search()
    old = queries.object_revision(window, obj)
    obj.data.vertices[0].co.x = .25  # Deliberately NO update or invalidation handler.
    obj.data.attributes[".sculpt_mask"].data[1].value = .7
    check("unreported_manual_geometry_and_mask_edits_are_not_stale", queries.object_revision(window, obj) != old)
    change = delta(baseline["observation_token"])
    check("shared_mesh_delta_correct", changed(change, handle, "geometry") and changed(change, queries.object_handle(shared), "geometry"))
    check("exact_delta_for_supported_original_state", change["delta_exact"])
    old = queries.object_revision(window, obj)
    obj.data.uv_layers[0].uv[0].vector = [.3, .4]
    check("uv_coordinates_change_revision", queries.object_revision(window, obj) != old)

    baseline = search()
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = [.1, .3, .8, 1]
    check("material_node_input_delta", changed(delta(baseline["observation_token"]), handle, "materials"))
    rgb = material.node_tree.nodes.new("ShaderNodeRGB")
    material.node_tree.links.new(rgb.outputs[0], shader.inputs["Base Color"])
    old = queries.object_revision(window, obj)
    rgb.outputs[0].default_value = [.9, .2, .1, 1]
    check("node_output_default_is_in_exact_revision", queries.object_revision(window, obj) != old)
    nodes = detail(obj, sections=["MATERIALS"])["sections"]["materials"][0]["node_tree"]
    check("material_node_links_described", any(link["from_node"] == rgb.name for link in nodes["links"]))

    obj.keyframe_insert(data_path="location", frame=1)
    obj.location.z = 2
    obj.keyframe_insert(data_path="location", frame=12)
    old = queries.object_revision(window, obj)
    curve = next(queries._curves(obj.animation_data.action))
    curve.keyframe_points[0].co.y += .1
    check("mutable_action_dependency_in_revision", queries.object_revision(window, obj) != old)
    action = detail(obj, sections=["ANIMATION"])["sections"]["animation"]["object"]["action"]
    check("layered_action_summaries", action["curve_count"] == 3 and action["keyframe_count"] == 6)
    bevel = obj.modifiers.new("Artist bevel", "BEVEL")
    old = queries.object_revision(window, obj)
    bevel.width = .07
    check("modifier_settings_in_revision", queries.object_revision(window, obj) != old)
    check("modifier_settings_in_detail", detail(obj, sections=["MODIFIERS"])["sections"]["modifiers"][0]["settings"]["width"] > .06)
    obj.modifiers.remove(bevel)

    check("selection_filter", [row["object"] for row in search(selected=True)["objects"]] == [handle])
    shared.hide_set(True)
    check("bound_view_layer_visibility_filter", [row["object"] for row in search(visible=False)["objects"]] == [queries.object_handle(shared)])
    shared.hide_set(False)
    check("type_and_material_filters", len(search(types=["MESH"], material="color")["objects"]) == 2)
    for i in range(11):
        item = bpy.data.objects.new(f"Page fixture {i:02}", None)
        window.scene.collection.objects.link(item)
    first = search(query="Page fixture", limit=3)
    page = first
    rows = []
    while True:
        rows.extend(page["objects"])
        if not page["next_cursor"]:
            break
        page = search(query="Page fixture", limit=3, cursor=page["next_cursor"])
        check("paged_observation_is_pinned", page["observation_token"] == first["observation_token"])
    check("stable_complete_pagination", len(rows) == 11 and len({r["object"] for r in rows}) == 11)
    expect_error("cursor_query_binding", lambda: search(query="Other", limit=3, cursor=first["next_cursor"]), "query")
    baseline = search()
    obj.name = "Artist renamed object"
    check("object_handle_survives_rename", queries.object_handle(obj) == handle and queries.resolve_object(window, handle) == obj)
    check("rename_delta", changed(delta(baseline["observation_token"]), handle, "name"))
    expect_error("pagination_rejects_changed_scene", lambda: search(query="Page fixture", limit=3, cursor=first["next_cursor"]), "changed during pagination")
    baseline = search()
    removed = window.scene.objects["Page fixture 00"]
    removed_handle = queries.object_handle(removed)
    bpy.data.objects.remove(removed, do_unlink=True)
    check("delete_delta", any(c["change"] == "REMOVED" and c["object"]["object"] == removed_handle for c in delta(baseline["observation_token"])["changes"]))
    expect_error("deleted_handles_fail_closed", lambda: queries.resolve_object(window, removed_handle), "deleted")

    baseline = search()
    other_scene = bpy.data.scenes.new("Different admitted scope")
    other_window = SimpleNamespace(scene=other_scene, view_layer=window.view_layer)
    expect_error("cross_scene_token_rejected", lambda: queries.execute(other_window, "angelo_scene_delta", {"observation_token": baseline["observation_token"]}), "another document")
    expect_error("invented_token_rejected", lambda: delta("obs:"+queries._epoch+":"+"f"*32), "unadmitted")
    obj.data.vertices[0].co.z = .2
    queries.invalidate(window=window)
    check("unknown_edit_invalidation_preserves_baseline_and_exact_fallback", changed(delta(baseline["observation_token"]), handle, "geometry"))
    detail(obj)
    entries_before = len(queries._cache)
    queries.invalidate(depsgraph=SimpleNamespace(updates=[SimpleNamespace(id=material.node_tree)]))
    check("node_dependency_depsgraph_invalidates_cache", len(queries._cache) < entries_before)

    # Rigs, constraints and incoming/outgoing/shared dependencies are descriptive
    # even when nested modifier dependencies cannot authorize a native program.
    control = bpy.data.objects.new("Rig control", None)
    window.scene.collection.objects.link(control)
    obj.parent = control
    armature = bpy.data.armatures.new("Artist armature")
    rig = bpy.data.objects.new("Artist rig", armature)
    window.scene.collection.objects.link(rig)
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    window.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    hip = armature.edit_bones.new("Hip")
    hip.head, hip.tail = (0,0,0), (0,0,1)
    spine = armature.edit_bones.new("Spine")
    spine.head, spine.tail, spine.parent = (0,0,1), (0,0,2), hip
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Hip"].constraints.new("COPY_LOCATION").target = control
    obj.modifiers.new("Artist skin", "ARMATURE").object = rig
    obj.vertex_groups.new(name="Hip").add([0,1], .8, "REPLACE")
    obj.constraints.new("COPY_LOCATION").target = control
    user = bpy.data.objects.new("Native ID user", None)
    window.scene.collection.objects.link(user)
    user["native_id_reference"] = obj
    user["secret"] = "fixture-secret-token-must-never-be-emitted"
    private_stage = bpy.data.objects.new("PRIVATE PENDING STAGE MUST NOT BE EMITTED", None)
    private_stage["input_reference"] = obj
    observed = detail(rig, sections=["RIG"])
    check("rig_bone_and_pose_constraint_metadata", len(observed["sections"]["rig"]["bones"]) == 2 and
          observed["sections"]["rig"]["pose_bones"][0]["constraints"][0]["type"] == "COPY_LOCATION")
    check("rig_vertex_group_metadata", detail(obj, sections=["RIG"])["sections"]["rig"]["vertex_groups"][0]["name"] == "Hip")
    edges = queries.execute(window, "angelo_scene_relationships", {"object": handle})
    kinds = {e["kind"] for e in edges["relationships"]}
    check("typed_and_native_dependencies_and_users", {"PARENT", "RIG", "CONSTRAINT", "DATA", "MATERIAL", "ANIMATION", "SHARED_DATA", "SHARED_MATERIAL", "ID_USER"} <= kinds)
    check("relationships_no_custom_property_content", "fixture-secret-token" not in json.dumps(edges))
    check("private_main_stage_users_never_emitted", private_stage.name not in json.dumps(edges) and
          str(queries.object_handle(private_stage)) not in json.dumps(edges))
    bpy.data.objects.remove(private_stage)
    expect_error("nested_mutable_rig_revision_is_not_guessed", lambda: queries.object_revision(window, obj), "nested_dependency")

    # Image descriptors explicitly omit paths/pixels and cannot grant authority.
    image = bpy.data.images.new("Unread image dependency", width=1, height=1)
    image.filepath = str(OUT/"must-not-read-secret-image")
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    payload = detail(shared, sections=["MATERIALS"])
    check("image_metadata_never_exposes_or_reads_paths", "must-not-read-secret-image" not in json.dumps(payload) and
          any(node.get("image", {}).get("name") == image.name for node in payload["sections"]["materials"][0]["node_tree"]["nodes"] if node.get("image")))
    expect_error("image_dependency_revision_fails_closed", lambda: queries.object_revision(window, shared), "image_pixel")
    material.node_tree.nodes.remove(texture)

    # Live Edit Mesh coordinates/selections, no mode switch from observation.
    bpy.ops.object.select_all(action="DESELECT")
    shared.select_set(True)
    window.view_layer.objects.active = shared
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(shared.data)
    for face in bm.faces: face.select_set(False)
    for edge in bm.edges: edge.select_set(False)
    for vertex in bm.verts: vertex.select_set(False)
    vertex = next(iter(bm.verts))
    vertex.select_set(True)
    vertex.co.x += .2
    before = artist_state()
    live = detail(shared, sections=["MESH", "UV"])
    check("live_edit_mesh_not_stale_original_buffers", live["sections"]["mesh"]["source"] == "LIVE_EDIT_MESH" and live["sections"]["mesh"]["selection"]["vertices"]["count"] == 1)
    check("live_edit_observation_preserves_mode_and_artist_state", before == artist_state() and shared.mode == "EDIT")
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.mode_set(mode="SCULPT")
    expect_error("live_sculpt_program_authority_rejected", lambda: queries.object_revision(window, shared), "sculpt")
    bpy.ops.object.mode_set(mode="OBJECT")
    baseline = search()
    with patch.object(queries, "MAX_ELEMENTS", 1):
        limited = delta(baseline["observation_token"])
        check("overbudget_delta_never_claims_unchanged_geometry", not limited["delta_exact"] and limited["uncertain_count"] > 0)
        expect_error("overbudget_program_revision_rejected", lambda: queries.object_revision(window, shared), "budget")
        metadata = detail(shared, sections=["MATERIALS", "UV"])
        check("overbudget_geometry_still_allows_material_and_uv_metadata", bool(metadata["sections"]["materials"]) and
              metadata["sections"]["uv"][0]["name"] == "Artist UV" and not metadata["revision_exact"])
    with patch.object(queries, "MAX_OBJECTS", 2):
        limited = search()
        check("membership_scan_limit_is_explicit", limited["truncated"] and not limited["matching_count_exact"] and limited["scan_limit"] == 2)
    check("payload_hard_limit", len(json.dumps(detail(shared), separators=(",", ":")).encode()) <= queries.MAX_PAYLOAD_BYTES)


@bpy.app.handlers.persistent
def reset_epoch(*_args):
    queries.invalidate()


def undo_and_load():
    global window
    bpy.app.handlers.undo_post.append(reset_epoch)
    bpy.app.handlers.redo_post.append(reset_epoch)
    bpy.app.handlers.load_post.append(reset_epoch)
    try:
        bpy.context.preferences.edit.use_global_undo = True
        obj = window.scene.objects["Shared geometry"]
        before_name = obj.name
        handle = queries.object_handle(obj)
        baseline = search()["observation_token"]
        bpy.ops.ed.undo_push(message="Scene query baseline")
        obj.name = "Undo replacement fixture"
        bpy.ops.ed.undo_push(message="Scene query rename")
        bpy.ops.ed.undo()
        expect_error("undo_invalidates_old_document_handles", lambda: queries.resolve_object(window, handle), "expired")
        expect_error("undo_invalidates_old_observation_tokens", lambda: delta(baseline), "unadmitted")
        check("undo_artist_change_restored", window.scene.objects.get(before_name) is not None)
        fresh = queries.object_handle(window.scene.objects[before_name])
        bpy.ops.ed.redo()
        expect_error("redo_invalidates_handles", lambda: queries.resolve_object(window, fresh), "expired")
        window.view_layer.update()
        target = window.scene.objects["Undo replacement fixture"]
        handle = queries.object_handle(target)
        baseline = search()["observation_token"]
        path = OUT/"only-disposable-fixture.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        window = bpy.context.window_manager.windows[0]
        expect_error("load_invalidates_old_handles", lambda: queries.resolve_object(window, handle), "expired")
        expect_error("load_invalidates_old_tokens", lambda: delta(baseline), "unadmitted")
        check("fresh_loaded_handles_usable", bool(search()["objects"]))
    finally:
        for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post, bpy.app.handlers.load_post):
            if reset_epoch in handlers:
                handlers.remove(reset_epoch)


def memberships_match_users_collection():
    """The per-pass membership index must equal Blender's own Object.users_collection."""
    root = bpy.data.collections.new("Membership root")
    nested = bpy.data.collections.new("Membership nested")
    outside = bpy.data.collections.new("Membership outside scene")
    window.scene.collection.children.link(root)
    root.children.link(nested)
    other_scene = bpy.data.scenes.new("Membership other scene")
    mesh = bpy.data.meshes.new("Membership mesh")
    owners = ((root,), (nested, root), (nested, outside), (window.scene.collection, nested))
    objects = []
    for index, links in enumerate(owners):
        obj = bpy.data.objects.new("Membership %d" % index, mesh)
        for collection in links:
            collection.objects.link(obj)
        objects.append(obj)
    other_scene.collection.objects.link(objects[0])
    window.view_layer.update()
    try:
        scanned = {obj.session_uid for obj in window.scene.objects}
        check("membership_index_complete_for_fixture",
              queries.collection_memberships(scanned, time.monotonic() + 60, queries.MAX_MEMBERSHIP_LINKS) is not None)
        baseline = search(query="Membership")
        rows = {row["object"]: row for row in baseline["objects"]}
        for index, obj in enumerate(objects):
            expected = [c.name for c in obj.users_collection]
            row = rows[queries.object_handle(obj)]
            check("membership_matches_users_collection_%d" % index,
                  row["collections"] == expected[:32] and row["collection_count"] == len(expected))
        filtered = {row["object"] for row in search(query="Membership", collection="Membership nested")["objects"]}
        check("collection_filter_matches_users_collection",
              filtered == {queries.object_handle(o) for o in objects if any(c.name == "Membership nested" for c in o.users_collection)})
        root.objects.unlink(objects[0])  # Move object 0 from the root into the nested collection.
        nested.objects.link(objects[0])
        window.view_layer.update()
        moved = [row for row in delta(baseline["observation_token"])["changes"]
                 if row["object"]["object"] == queries.object_handle(objects[0])]
        check("collection_move_reported_by_delta",
              len(moved) == 1 and moved[0]["change"] == "MODIFIED" and "collections" in moved[0]["fields"])
    finally:
        for obj in objects:
            bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        for collection in (nested, root, outside):
            bpy.data.collections.remove(collection)
        bpy.data.scenes.remove(other_scene)
        window.view_layer.update()


def object_scoped_coverage():
    """Real-ID detail, discovery and covered-delta coverage, measured by captures."""
    created = []
    for index in range(12):
        mesh = bpy.data.meshes.new("Coverage mesh %02d" % index)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new("Coverage prop %02d" % index, mesh)
        obj.location.x = index * 3
        window.scene.collection.objects.link(obj)
        created.append(obj)
    window.view_layer.update()
    target, outside = created[0], created[1]
    try:
        captures = queries._counters["exact_checks"]
        observed = detail(target, sections=["TRANSFORM"])
        check("detail_fingerprints_only_its_target", queries._counters["exact_checks"] - captures == 1)
        check("detail_token_is_object_scoped", observed["observation_coverage"] == "OBJECTS"
              and observed["snapshot_exact"] and observed["revision_exact"])
        outside.location.x += 1
        window.view_layer.update()
        unchanged = delta(observed["observation_token"])
        check("object_delta_ignores_edits_outside_its_coverage", unchanged["changes"] == [] and unchanged["unchanged_count"] == 1
              and unchanged["observation_coverage"] == "OBJECTS" and "COVERED_OBJECTS_ONLY" in unchanged["scope"])
        target.location.z += 2
        window.view_layer.update()
        moved = delta(unchanged["observation_token"])
        check("object_delta_reports_the_covered_edit", [c["change"] for c in moved["changes"]] == ["MODIFIED"]
              and "transform" in moved["changes"][0]["fields"] and moved["delta_exact"])
        captures = queries._counters["exact_checks"]
        page = search(query="Coverage prop", coverage="PAGE", limit=5)
        check("discovery_fingerprints_only_the_returned_page", queries._counters["exact_checks"] - captures == 5
              and page["observation_coverage"] == "OBJECTS" and page["matching_count_exact"] and page["page"]["total"] == 12)
        scene = search(query="Coverage prop", limit=5)
        check("discovery_and_scene_search_agree", [o["object"] for o in page["objects"]] == [o["object"] for o in scene["objects"]]
              and [o["object_revision"] for o in page["objects"]] == [o["object_revision"] for o in scene["objects"]])
        page_token = page["observation_token"]
        bpy.data.objects.remove(target)
        window.view_layer.update()
        removed = delta(page_token)
        check("object_delta_proves_a_covered_removal", [c["change"] for c in removed["changes"]] == ["REMOVED"]
              and removed["unchanged_count"] == 4 and not removed["truncated"])
    finally:
        for name in ["Coverage prop %02d" % index for index in range(12)]:
            if bpy.data.objects.get(name) is not None:
                bpy.data.objects.remove(bpy.data.objects[name])
        for mesh in [m for m in bpy.data.meshes if m.name.startswith("Coverage mesh") and not m.users]:
            bpy.data.meshes.remove(mesh)
        window.view_layer.update()


def attribute_reads_match_element_rna():
    """Contiguous attribute reads must equal per-element RNA for every mapped property."""
    from array import array
    from angelo_agent.scene_geometry import MESH_ATTRIBUTE_SOURCES, mesh_array
    meshes, materials = [], []
    try:
        for label, flagged in (("fresh", False), ("flagged", True)):
            mesh = bpy.data.meshes.new("Attribute parity " + label)
            bm = bmesh.new()
            bmesh.ops.create_grid(bm, x_segments=7, y_segments=5, size=1)
            bm.to_mesh(mesh)
            bm.free()
            meshes.append(mesh)
            if flagged:
                for index, vertex in enumerate(mesh.vertices):
                    vertex.select, vertex.hide = index % 3 == 0, index % 5 == 0
                for index, edge in enumerate(mesh.edges):
                    edge.select, edge.hide = index % 2 == 0, index % 7 == 0
                materials += [bpy.data.materials.new("Parity A"), bpy.data.materials.new("Parity B")]
                for material in materials:
                    mesh.materials.append(material)
                for index, polygon in enumerate(mesh.polygons):
                    polygon.select, polygon.hide = index % 4 == 0, index % 6 == 0
                    polygon.material_index, polygon.use_smooth = index % 2, index % 3 == 0
            if label == "fresh":
                report["attribute_parity_absent_on_fresh_grid"] = sorted(
                    source[0] for source in MESH_ATTRIBUTE_SOURCES.values() if mesh.attributes.get(source[0]) is None)
            else:
                check("attribute_parity_flagged_grid_has_every_source",
                      all(mesh.attributes.get(source[0]) is not None for source in MESH_ATTRIBUTE_SOURCES.values()))
            collections = {"POINT": mesh.vertices, "EDGE": mesh.edges, "CORNER": mesh.loops, "FACE": mesh.polygons}
            for domain, prop in MESH_ATTRIBUTE_SOURCES:
                collection = collections[domain]
                code = "f" if prop == "co" else "b" if prop in {"select", "hide", "use_smooth"} else "i"
                width = 3 if prop == "co" else 2 if prop == "vertices" else 1
                expected = array(code, [0]) * (len(collection) * width)
                collection.foreach_get(prop, expected)
                check("attribute_parity_%s_%s_%s" % (label, domain.lower(), prop),
                      mesh_array(mesh, domain, collection, prop, code, width)[0] == expected)
    finally:
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        for material in materials:
            bpy.data.materials.remove(material)


def native_weights_match_vertex_group_walk():
    """Angelo's native weight records must equal the per-vertex MeshVertex.groups walk."""
    from array import array
    import random
    rng = random.Random(7)
    mesh = bpy.data.meshes.new("Weight parity")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=9, y_segments=9, size=1)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Weight parity", mesh)
    window.scene.collection.objects.link(obj)
    try:
        check("native_weights_empty_without_deform_layer", mesh.angelo_vertex_group_weights == b"")
        groups = [obj.vertex_groups.new(name="Parity %d" % index) for index in range(5)]
        for vertex in mesh.vertices:
            if vertex.index % 7:
                for group in rng.sample(groups, rng.randint(1, 4)):
                    group.add([vertex.index], rng.random(), "REPLACE")
        walked = bytearray()
        for vertex in mesh.vertices:
            for group in vertex.groups:
                walked += array("i", [vertex.index, group.group]).tobytes() + array("f", [group.weight]).tobytes()
        native = mesh.angelo_vertex_group_weights
        check("native_weights_match_vertex_group_walk", len(native) > 0 and native == bytes(walked))
        window.view_layer.update()
        before = queries.object_revision(window, obj)
        groups[2].add([1], .123, "REPLACE")
        check("native_weights_change_the_revision", queries.object_revision(window, obj) != before)
    finally:
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)


def mirror_origin_stale_modifier(owner, origin, subdivision):
    """An inferred origin keeps its full observed binding through precommit."""
    from angelo_agent import native_programs as native
    from angelo_agent.identifiers import IdentifierTable
    from program_qa_helpers import no_stages

    handle = queries.object_handle(origin)
    observed = detail(origin, sections=["MODIFIERS"])
    check("mirror_origin_observation_exact", observed["revision_exact"])
    table = IdentifierTable()
    table.short_handle(handle)
    table.observe({"objects": [{"handle": handle, "object_revision": observed["object_revision"]}]})
    controller = SimpleNamespace(_identifiers=table)
    owner_handle, owner_revision = queries.object_handle(owner), queries.object_revision(window, owner)
    args = {"mode": "APPLY", "read_set": [],
            "write_set": [{"handle": owner_handle, "revision": owner_revision}],
            "operations": [{"kind": "transform", "target": owner_handle, "parameters": {"location": [2, 3, 4]}}],
            "expected_checks": []}
    approved = native.precondition(window, "angelo_scene_program", args, controller=controller)
    check("mirror_origin_inferred_from_observation",
          any(entry[0] == handle and entry[1] == observed["object_revision"] for entry in approved.inputs))
    before_ids = set(native._main_ids())
    job = native.begin(window, "angelo_scene_program", args, controller=controller,
                       approved_obligations=approved.obligations)
    try:
        for _ in range(256):
            result = job.poll()
            if result.get("ready_to_commit") or result.get("final"):
                break
        check("mirror_origin_stale_control_reached_ready", result.get("ready_to_commit") is True)
        check("mirror_origin_full_binding_retained", handle in job.binding["handles"]
              and job.binding["revisions"][handle] == observed["object_revision"])
        geometry = [tuple(vertex.co) for vertex in origin.data.vertices]
        matrix, location = origin.matrix_world.copy(), owner.location.copy()
        subdivision.levels = 2
        window.view_layer.update()
        check("mirror_origin_only_modifier_configuration_changed",
              origin.matrix_world == matrix and [tuple(vertex.co) for vertex in origin.data.vertices] == geometry
              and owner.location == location and queries.object_revision(window, owner) == owner_revision
              and queries.object_revision(window, origin) != observed["object_revision"]
              and table.observed(handle) == observed["object_revision"])
        artist_after_edit = native._fingerprint(owner)
        result = native.commit_job(job.id, window, controller)
        report["mirror_origin_stale_modifier_result"] = result
        check("mirror_origin_stale_modifier_refused_before_mutation", result.get("success") is False
              and "exact target or a mutable dependency changed" in result.get("error", "")
              and result.get("scene_changed") is False and result.get("partial_changes_possible") is False
              and job.backend.mutation_started is False)
        check("mirror_origin_stale_refusal_preserves_artist_edit", native._fingerprint(owner) == artist_after_edit
              and owner.location == location and owner.modifiers["Origin Mirror"].mirror_object == origin
              and subdivision.levels == 2)
        check("mirror_origin_stale_cleanup_complete", no_stages() and not native.runtime_is_staging()
              and native.WRITER.owner is None and not native.PREVIEWS.records and not native.has_pending_cleanup()
              and not result.get("cleanup_error") and not result.get("cleanup_warning")
              and set(native._main_ids()) == before_ids)
    finally:
        if not job.closed:
            job.cancel()


def mirror_origin_dependencies():
    """Real public revisions: exclusive Mirror transform roles and retained guards."""
    original_scene = window.scene
    scene = bpy.data.scenes.new("Mirror dependency query fixture")
    objects, meshes = [], []
    original_collections = {collection.as_pointer() for collection in bpy.data.collections}
    namespace_key = "angelo_mirror_query_driver_probe"
    missing = object()
    old_probe = bpy.app.driver_namespace.get(namespace_key, missing)
    try:
        window.scene = scene
        for name in ("Owner", "Origin", "Replacement"):
            mesh = bpy.data.meshes.new("Mirror query " + name)
            mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
            obj = bpy.data.objects.new("Mirror query " + name, mesh)
            scene.collection.objects.link(obj)
            objects.append(obj)
            meshes.append(mesh)
        owner, origin, replacement = objects
        mirror = owner.modifiers.new("Origin Mirror", "MIRROR")
        mirror.mirror_object = origin
        subdivision = origin.modifiers.new("Origin subdivision", "SUBSURF")
        subdivision.levels = subdivision.render_levels = 1
        window.view_layer.update()
        before = artist_state()
        revision = queries.object_revision(window, owner)
        check("mirror_modified_origin_has_exact_revision", bool(revision) and artist_state() == before)
        origin.location.x += .5
        window.view_layer.update()
        moved = queries.object_revision(window, owner)
        check("mirror_origin_transform_invalidates_revision", moved != revision)
        mirror.mirror_object = replacement
        check("mirror_origin_identity_invalidates_revision", queries.object_revision(window, owner) != moved)
        mirror.mirror_object = origin
        for first in (False, True):
            boolean = owner.modifiers.new("Shared ordinary dependency", "BOOLEAN")
            boolean.object = origin
            if first:
                owner.modifiers.move(1, 0)
            expect_error("mirror_mixed_role_refuses_%s" % first,
                         lambda: queries.object_revision(window, owner), "nested_dependency_configuration")
            owner.modifiers.remove(boolean)
        origin.parent = replacement
        expect_error("mirror_parent_guard_retained", lambda: queries.object_revision(window, owner),
                     "nested_dependency_configuration")
        origin.parent = None
        constraint = origin.constraints.new("COPY_LOCATION")
        constraint.target = replacement
        expect_error("mirror_constraint_guard_retained", lambda: queries.object_revision(window, owner),
                     "nested_dependency_configuration")
        origin.constraints.remove(constraint)
        calls = []
        bpy.app.driver_namespace[namespace_key] = lambda: calls.append(True) or 0.0
        driver = origin.driver_add("location", 1)
        driver.driver.expression = namespace_key + "()"
        calls_before = len(calls)
        expect_error("mirror_driver_guard_retained", lambda: queries.object_revision(window, owner),
                     "animation_driver_expressions")
        check("mirror_query_does_not_execute_driver", len(calls) == calls_before)
        origin.driver_remove("location", 1)
        # RNA owns these settings; use its normal operators in this private scene.
        for obj in objects:
            obj.select_set(obj == origin)
        window.view_layer.objects.active = origin
        bpy.ops.rigidbody.object_add(type="ACTIVE")
        check("mirror_physics_control_is_real", origin.rigid_body is not None)
        expect_error("mirror_rigid_body_excludes_modifier_exemption", lambda: queries.object_revision(window, owner),
                     "nested_dependency_configuration")
        bpy.ops.rigidbody.object_remove()
        bpy.ops.rigidbody.constraint_add()
        check("mirror_physics_constraint_control_is_real", origin.rigid_body_constraint is not None)
        expect_error("mirror_rigid_body_constraint_excludes_modifier_exemption",
                     lambda: queries.object_revision(window, owner), "nested_dependency_configuration")
        bpy.ops.rigidbody.constraint_remove()
        bpy.ops.rigidbody.world_remove()
        window.view_layer.update()
        check("mirror_query_guards_restore_to_exact", bool(queries.object_revision(window, owner)))
        mirror_origin_stale_modifier(owner, origin, subdivision)
    finally:
        if old_probe is missing:
            bpy.app.driver_namespace.pop(namespace_key, None)
        else:
            bpy.app.driver_namespace[namespace_key] = old_probe
        window.scene = original_scene
        for obj in objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(scene)
        for collection in list(bpy.data.collections):
            if collection.as_pointer() not in original_collections:
                bpy.data.collections.remove(collection)


def main():
    (OUT/"recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT/"recovery")
    try:
        run_queries()
        memberships_match_users_collection()
        object_scoped_coverage()
        attribute_reads_match_element_rna()
        native_weights_match_vertex_group_walk()
        mirror_origin_dependencies()
        undo_and_load()
        report["completed"] = True
        report["instrumentation"] = dict(queries._counters)
    except Exception:
        report.update(completed=False, error=traceback.format_exc())
    (OUT/"results.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_SCENE_INTELLIGENCE_TESTS", json.dumps(report), flush=True)
    assert report["completed"], report.get("error")


if __name__ == "__main__":
    main()
