# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable production catalog GN composability and fail-closed tampering.

Exact Angelo --background --factory-startup --python-exit-code 1, isolated state.
No permissive admission store, user projects, provider/auth/inference or builds.
"""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
assert Path(bpy.app.binary_path).stem == "Angelo"   # Angelo.exe on Windows
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-scene-catalog-graphs"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import native_programs as native, scene_queries as queries
from angelo_agent.action_recipes import execute as expand_recipe
from angelo_agent.action_schemas import GEOMETRY_NODE_TYPES
from angelo_agent.scene_catalog_graphs import PROVENANCE_KEY
from angelo_agent.scene_validation import execute as verify

window = bpy.context.window_manager.windows[0]
report = {"checks": {}, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "opensubdiv": bool(bpy.app.build_options.opensubdiv), "paid_requests": 0, "user_projects_opened": 0}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def cube(name):
    obj = window.scene.objects["Cube"].copy()
    obj.data = obj.data.copy()
    obj.modifiers.clear()
    obj.animation_data_clear()
    obj.name = name
    window.scene.collection.objects.link(obj)
    return obj


def args(obj, operations, checks=()):
    handle = queries.object_handle(obj)
    return {"mode": "APPLY", "read_set": [], "write_set": [{"handle": handle, "revision": queries.object_revision(window, obj)}],
            "operations": operations, "expected_checks": list(checks)}


class CatalogGraphCommit(bpy.types.Operator):
    bl_idname = "angelo.catalog_graph_query_commit"
    bl_label = "Disposable catalog graph commit"
    bl_options = {"REGISTER", "UNDO"}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        report["last_commit"] = native.commit_job(self.program_id, context.window)
        return {"FINISHED"} if report["last_commit"].get("success") else {"CANCELLED"}


def finish(arguments, label):
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(100):
        result = job.poll()
        if result.get("final"):
            raise AssertionError(json.dumps(result))
        if result.get("ready_to_commit"):
            check(label + "_not_resident_before_commit", not native.runtime_is_staging())
            operator = bpy.ops.angelo.catalog_graph_query_commit("EXEC_DEFAULT", True, program_id=job.id)
            check(label + "_production_commit", "FINISHED" in operator and report["last_commit"]["success"])
            check(label + "_no_staging_or_writer", not native.runtime_is_staging() and not native.has_active_programs())
            return report["last_commit"]
    job.cancel()
    raise AssertionError("Native graph program did not settle")


def transform(obj, label):
    handle = queries.object_handle(obj)
    return finish(args(obj, [{"kind": "transform", "target": handle, "parameters": {"location": [3, 2, 1]}}],
                       [{"kind": "finite_coordinates", "target": handle}]), label)


def parameters(kind):
    if kind in {"GROUP_INPUT", "GROUP_OUTPUT"}:
        return {"name": "Catalog " + kind, "nodes": [{"id": "in", "type": "GROUP_INPUT"}, {"id": "out", "type": "GROUP_OUTPUT"}],
                "links": [{"from_node": "in", "from_socket": "Geometry", "to_node": "out", "to_socket": "Geometry"}]}
    input_name = "Mesh" if kind in {"SUBDIVIDE_MESH", "EXTRUDE_MESH"} else "Geometry"
    output_name = "Mesh" if kind in {"SUBDIVIDE_MESH", "EXTRUDE_MESH", "MESH_CUBE", "MESH_GRID"} else "Geometry"
    links = [{"from_node": "work", "from_socket": output_name, "to_node": "out", "to_socket": "Geometry"}]
    if kind not in {"MESH_CUBE", "MESH_GRID"}:
        links.insert(0, {"from_node": "in", "from_socket": "Geometry", "to_node": "work", "to_socket": input_name})
    return {"name": "Catalog " + kind, "nodes": [{"id": "in", "type": "GROUP_INPUT"}, {"id": "work", "type": kind},
                                                    {"id": "out", "type": "GROUP_OUTPUT"}], "links": links}


def detail(obj):
    return queries.execute(window, "angelo_scene_object", {"object": queries.object_handle(obj), "sections": ["MODIFIERS"]})


def new_graph(obj, params, label):
    handle = queries.object_handle(obj)
    finish(args(obj, [{"kind": "geometry_nodes", "target": handle, "parameters": params}],
                [{"kind": "finite_coordinates", "target": handle}]), label)
    return obj.modifiers[-1].node_group


def catalog_types():
    target = None
    for kind in GEOMETRY_NODE_TYPES:
        obj = cube("Query " + kind)
        tree = new_graph(obj, parameters(kind), kind)
        before = json.dumps({"metadata": tree[PROVENANCE_KEY], "tree_keys": list(tree.keys()),
                             "inputs": list(obj.modifiers[-1].properties.inputs.keys())})
        revision = queries.object_revision(window, obj)
        graph = detail(obj)["sections"]["modifiers"][-1]["node_tree"]
        after = json.dumps({"metadata": tree[PROVENANCE_KEY], "tree_keys": list(tree.keys()),
                            "inputs": list(obj.modifiers[-1].properties.inputs.keys())})
        check(kind + "_exact_catalog_revision_and_readonly", len(revision) == 64 and before == after and graph["scope"] == "CLOSED_CATALOG_GEOMETRY")
        transform(obj, kind + "_second_turn")
        check(kind + "_second_turn_preserves_graph", list(obj.location) == [3, 2, 1] and obj.modifiers[-1].node_group == tree)
        if kind == "SET_SHADE_SMOOTH":
            target = obj
        else:
            bpy.data.objects.remove(obj, do_unlink=True)
    return target


def shade_recipe():
    obj = cube("Standard bevel-and-shade public workflow")
    expanded = expand_recipe(window, "angelo_expand_recipe", {"recipe": "bevel-and-shade", "target": queries.object_handle(obj),
                              "revision": queries.object_revision(window, obj), "parameters": {"width": .1, "segments": 1}})
    finish(expanded["program"], "recipe_turn_one")
    revision = queries.object_revision(window, obj)
    result = verify(window, "angelo_scene_verify", {"read_set": [{"handle": queries.object_handle(obj), "revision": revision}],
                    "checks": expanded["expected_checks"]})
    check("standard_bevel_and_shade_fresh_public_verification", result["passed"])
    transform(obj, "recipe_turn_two")
    check("standard_bevel_and_shade_next_turn_works", list(obj.location) == [3, 2, 1] and len(obj.modifiers) == 2)
    bpy.data.objects.remove(obj, do_unlink=True)


def rejected(obj, name, effect, undo):
    baseline = queries.execute(window, "angelo_scene_search", {})
    old_revision = queries.object_revision(window, obj)
    handle = queries.object_handle(obj)
    effect()
    try:
        try:
            queries.object_revision(window, obj)
        except ValueError as error:
            check(name + "_unsupported_revision", "catalog_geometry" in str(error))
        else:
            check(name + "_unsupported_revision", False)
        result = queries.execute(window, "angelo_scene_delta", {"observation_token": baseline["observation_token"]})
        check(name + "_never_exact_unchanged", not result["delta_exact"] and result["uncertain_count"] > 0)
        check(name + "_no_secret_values", "untracked-secret-value" not in json.dumps(detail(obj)))
        bad = native.begin(window, "angelo_scene_program", {"mode": "APPLY", "read_set": [],
            "write_set": [{"handle": handle, "revision": old_revision}], "operations": [{"kind": "transform", "target": handle,
            "parameters": {"location": [9, 9, 9]}}]})
        outcome = bad.poll()
        check(name + "_production_admission_refused", outcome.get("final") and not outcome["success"]
              and not native.runtime_is_staging() and not native.has_active_programs())
    finally:
        undo()
    check(name + "_valid_after_repair", len(queries.object_revision(window, obj)) == 64)


def tampering(obj):
    modifier = obj.modifiers[-1]
    tree, shade = modifier.node_group, modifier.node_group.nodes["work"]
    original = tree[PROVENANCE_KEY]
    extra_outputs = []
    def replace_output():
        # Blender keeps the sole output active. A real second active output
        # is needed to change that state, not a no-op assignment to False.
        node = tree.nodes.new("NodeGroupOutput")
        extra_outputs.append(node)
        node.is_active_output = True
    def restore_output():
        tree.nodes.remove(extra_outputs.pop())
        tree.nodes["out"].is_active_output = True
    cases = (("missing_metadata", lambda: tree.pop(PROVENANCE_KEY), lambda: tree.__setitem__(PROVENANCE_KEY, original)),
             ("forged_schema", lambda: tree.__setitem__(PROVENANCE_KEY, '{"approved":true}'), lambda: tree.__setitem__(PROVENANCE_KEY, original)),
             ("duplicate_metadata", lambda: tree.__setitem__(PROVENANCE_KEY, '{"nodes":[],"nodes":[]}'), lambda: tree.__setitem__(PROVENANCE_KEY, original)),
             ("nonfinite_metadata", lambda: tree.__setitem__(PROVENANCE_KEY, '{"nodes":NaN}'), lambda: tree.__setitem__(PROVENANCE_KEY, original)),
             ("oversized_metadata", lambda: tree.__setitem__(PROVENANCE_KEY, "x" * 65537), lambda: tree.__setitem__(PROVENANCE_KEY, original)),
             ("actual_input_tamper", lambda: setattr(shade.inputs["Shade Smooth"], "default_value", False), lambda: setattr(shade.inputs["Shade Smooth"], "default_value", True)),
             ("actual_domain_tamper", lambda: setattr(shade, "domain", "EDGE"), lambda: setattr(shade, "domain", "FACE")),
             ("muted_node", lambda: setattr(shade, "mute", True), lambda: setattr(shade, "mute", False)),
             ("replaced_active_output", replace_output, restore_output),
             ("interface_setting", lambda: setattr(tree.interface.items_tree[0], "hide_in_modifier", True), lambda: setattr(tree.interface.items_tree[0], "hide_in_modifier", False)))
    for name, effect, undo in cases:
        rejected(obj, name, effect, undo)
    outsider = bpy.data.objects.new("Untracked dependency outside bound scene", None)
    input_id = next(s.identifier for s in tree.interface.items_tree if s.in_out == "INPUT")
    fallback = getattr(modifier.properties.inputs, input_id)
    rejected(obj, "wrong_known_fallback_storage", lambda: fallback.__setitem__("type", 1), lambda: fallback.__setitem__("type", 0))
    for owner, label in ((tree, "tree"), (shade, "node"), (shade.inputs["Selection"], "socket"),
                         (modifier.properties.inputs, "modifier_inputs"), (modifier.properties.outputs, "modifier_outputs"),
                         (getattr(modifier.properties.inputs, input_id), "modifier_fallback")):
        try:
            owner["untracked-secret-value"] = outsider
        except (AttributeError, TypeError):
            check(label + "_native_custom_dependencies_not_supported", True)
            continue
        else:
            del owner["untracked-secret-value"]
        rejected(obj, label + "_untracked_dependency", lambda owner=owner: owner.__setitem__("untracked-secret-value", outsider),
                 lambda owner=owner: owner.__delitem__("untracked-secret-value"))
    bpy.data.objects.remove(outsider)
    extra = []
    rejected(obj, "extra_object_interface", lambda: extra.append(tree.interface.new_socket(name="Untracked", in_out="INPUT", socket_type="NodeSocketObject")),
             lambda: tree.interface.remove(extra.pop()))
    rejected(obj, "unknown_resource_node", lambda: extra.append(tree.nodes.new("GeometryNodeObjectInfo")), lambda: tree.nodes.remove(extra.pop()))
    rejected(obj, "tree_animation", tree.animation_data_create, tree.animation_data_clear)
    links = [(l.from_socket, l.to_socket) for l in tree.links]
    rejected(obj, "actual_links_removed", lambda: tree.links.remove(next(iter(tree.links))), lambda: [tree.links.new(a, b) for a, b in links if not any(l.from_socket == a and l.to_socket == b for l in tree.links)])
    with patch.object(queries, "MAX_METADATA_ITEMS", 1):
        try:
            queries.object_revision(window, obj)
        except ValueError as error:
            check("catalog_work_budget_fails_closed", "budget" in str(error))
        else:
            check("catalog_work_budget_fails_closed", False)
    parameters = json.loads(original)
    parameters["nodes"][1]["inputs"]["Shade Smooth"] = False
    baseline = queries.execute(window, "angelo_scene_search", {})
    old = queries.object_revision(window, obj)
    tree[PROVENANCE_KEY], shade.inputs["Shade Smooth"].default_value = json.dumps(parameters), False
    result = queries.execute(window, "angelo_scene_delta", {"observation_token": baseline["observation_token"]})
    check("matching_typed_provenance_and_actual_edit_is_exact_changed", queries.object_revision(window, obj) != old and result["delta_exact"]
          and any("modifiers" in row.get("fields", ()) for row in result["changes"]))


def implicit_controls():
    obj = cube("Closed implicit controls")
    tree = new_graph(obj, parameters("TRANSFORM_GEOMETRY"), "implicit_transform")
    mode = tree.nodes["work"].inputs["Mode"]
    rejected(obj, "unadmitted_transform_matrix_mode", lambda: setattr(mode, "default_value", "Matrix"), lambda: setattr(mode, "default_value", "Components"))
    obj.modifiers.clear()
    tree = new_graph(obj, parameters("SET_POSITION"), "implicit_position")
    position = tree.nodes["work"].inputs["Position"]
    rejected(obj, "implicit_position_replaced", lambda: setattr(position, "default_value", [1, 2, 3]), lambda: setattr(position, "default_value", [0, 0, 0]))
    rejected(obj, "implicit_position_field_disabled", lambda: setattr(position, "hide_value", False), lambda: setattr(position, "hide_value", True))
    bpy.data.objects.remove(obj, do_unlink=True)


def multi_input_order():
    obj = cube("Exact Join ordering")
    params = parameters("JOIN_GEOMETRY")
    params["nodes"].insert(1, {"id": "cube", "type": "MESH_CUBE"})
    params["links"].insert(1, {"from_node": "cube", "from_socket": "Mesh", "to_node": "work", "to_socket": "Geometry"})
    tree = new_graph(obj, params, "join_order")
    a, b = [l for l in tree.links if l.to_node == tree.nodes["work"]]
    old = queries.object_revision(window, obj)
    a.swap_multi_input_sort_id(b)
    check("native_multi_input_link_order_changes_exact_revision", old != queries.object_revision(window, obj))
    bpy.data.objects.remove(obj, do_unlink=True)


def main():
    started = time.perf_counter()
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    bpy.context.preferences.edit.use_global_undo = True
    for obj in list(bpy.data.objects):
        if obj.name != "Cube":
            bpy.data.objects.remove(obj, do_unlink=True)
    queries.invalidate()
    native.register()
    bpy.utils.register_class(CatalogGraphCommit)
    try:
        target = catalog_types()
        shade_recipe()
        tampering(target)
        implicit_controls()
        multi_input_order()
        report["completed"] = True
        report["instrumentation"] = dict(queries._counters)
    except Exception:
        report.update(completed=False, error=traceback.format_exc())
    finally:
        native.unregister()
        bpy.utils.unregister_class(CatalogGraphCommit)
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_CATALOG_GRAPH_QUERIES", json.dumps(report), flush=True)
    assert report["completed"], report.get("error")


if __name__ == "__main__":
    main()
