# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual typed edits retain mandatory known checks and honest partial results."""

import copy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-partial-checks-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from angelo_agent.program_completion import capture_program  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

window = bpy.context.window_manager.windows[0]
factory = window.scene
report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
CommitOperator = commit_operator(report)
bpy.utils.register_class(CommitOperator)


def check(label, condition):
    report["checks"][label] = bool(condition)
    assert condition, label


def poll(job):
    for _ in range(512):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Partial-verification fixture exceeded bounded polls")


def clean_now(label):
    check(label, native.WRITER.owner is None and not native.PREVIEWS.records and no_stages()
          and not native.has_pending_cleanup())


def run_case(name):
    scene = bpy.data.scenes.new("Partial verification " + name)
    window.scene = scene
    mesh = bpy.data.meshes.new("Partial mesh")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new("Partial object", mesh)
    scene.collection.objects.link(obj)
    material = bpy.data.materials.new("Partial Paint")
    if material.node_tree is None:
        material.use_nodes = True
    mesh.materials.append(material)
    queries.invalidate()
    handle = queries.object_handle(obj)
    parameters = {"material": material.name, "edits": [
        {"op": "add", "node": "Math", "type": "ShaderNodeMath", "inputs": {"Value": 1}},
        {"op": "add", "node": "Unaffected", "type": "ShaderNodeMath", "inputs": {"Value": 2}},
        {"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}}]}
    authored = ([{"kind": "node_graph", "target": handle, "material": material.name,
                  "nodes": [{"name": "Unaffected", "inputs": {"Value": 9}}]}] if name == "authored_failure" else [])
    arguments = validate_call("angelo_scene_program", {
        "mode": "PREVIEW" if name == "preview_accept" else "APPLY",
        "write_set": [{"handle": handle, "revision": queries.object_revision(window, obj)}],
        "operations": [{"kind": "node_edit", "target": handle, "parameters": parameters}],
        "expected_checks": authored})
    before = native._fingerprint(obj)
    job = None
    data = report["cases"][name] = {}
    try:
        approved = native.precondition(window, "angelo_scene_program", arguments)
        check(name + "_unknowns_frozen_before_stage", bool(approved.obligations.unknowns()))
        if name == "stale":
            material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = .17
            before = native._fingerprint(obj)
        job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
        original = native.BlenderProgramBackend.apply

        def apply(backend, stage, operation):
            result = original(backend, stage, operation)
            if name == "known_failure":
                stage.objects[handle].material_slots[0].material.node_tree.nodes["Unaffected"].inputs["Value"].default_value = 9
            return result

        with patch.object(native.BlenderProgramBackend, "apply", apply):
            result = poll(job)
        data["staged"] = copy.deepcopy(result)
        if name in {"authored_failure", "known_failure", "stale"}:
            check(name + "_mandatory_refusal", result.get("success") is False)
            check(name + "_original_unchanged", native._fingerprint(obj) == before)
            check(name + "_no_success_partial_metadata", "verification" not in result)
            check(name + "_no_completion", job.completion_obligations() is None)
            clean_now(name + "_immediate_cleanup")
            return
        if name == "cancel":
            check(name + "_ready", result.get("ready_to_commit"))
            data["cancelled"] = job.cancel()
            check(name + "_original_unchanged", native._fingerprint(obj) == before)
            check(name + "_no_completion", job.completion_obligations() is None)
            clean_now(name + "_immediate_cleanup")
            return
        if name == "preview_accept":
            check(name + "_partial_preview", result.get("status") == "preview" and
                  result.get("verification", {}).get("status") == "partial")
            check(name + "_preview_original_unchanged", native._fingerprint(obj) == before)
            preview_bundle = job.obligations
            identity = result["preview_id"]
            native.precondition(window, "angelo_accept_preview", {"preview_id": identity})
            job = native.begin(window, "angelo_accept_preview", {"preview_id": identity})
            check(name + "_same_unknown_authority", job.obligations is preview_bundle)
            result = poll(job)
        check(name + "_ready", result.get("ready_to_commit"))
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        result = data["committed"] = copy.deepcopy(job.result)
        check(name + "_typed_edit_committed", result.get("status") == "committed")
        check(name + "_honest_partial", result.get("verification", {}).get("status") == "partial"
              and result["verification"]["unknowns"] == job.obligations.unknowns())
        check(name + "_known_checks_complete", len(job.check_results) == len(job.checks)
              and all(row["passed"] for row in job.check_results))
        check(name + "_no_success_compaction", "check_summary" not in result)
        check(name + "_private_partial_capture", capture_program(job).obligations is job.obligations)
        outcome = obj.material_slots[0].material.node_tree.nodes
        check(name + "_actual_unaffected_fact", abs(outcome["Unaffected"].inputs["Value"].default_value - 2) < 1e-6)
        check(name + "_actual_setting", outcome["Math"].operation == "MULTIPLY")
        operation = {"tool": "angelo_scene_program", "arguments": arguments}
        check(name + "_no_auto_certificate", certified_completion([operation], [result], read_only=lambda tool: False) is None)
        clean_now(name + "_immediate_cleanup")
    finally:
        if job is not None and not job.closed:
            job.cancel()
        native.cleanup_all()
        window.scene = factory
        materials = {slot.material for slot in obj.material_slots if slot.material}
        bpy.data.scenes.remove(scene)
        bpy.data.batch_remove(ids={obj, mesh, *materials})
        queries.invalidate()


def unlink_case(name, *, geometry=False):
    """Actual duplicate socket names, qualified source aliases and multi-inputs."""
    scene = bpy.data.scenes.new("Qualified unlink " + name)
    window.scene = scene
    mesh = bpy.data.meshes.new("Unlink mesh")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new("Unlink object", mesh)
    scene.collection.objects.link(obj)
    groups, job = set(), None
    try:
        if geometry:
            from angelo_agent.node_edit_geometry import provenance
            from angelo_agent.action_catalog import validate_operation
            from angelo_agent.legacy_names import NATIVE_GRAPH
            tree = bpy.data.node_groups.new("Unlink geometry", "GeometryNodeTree")
            groups.add(tree)
            tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
            tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
            first = tree.nodes.new("NodeGroupInput")
            first.name = "First"
            second = tree.nodes.new("GeometryNodeMeshCube")
            second.name = "Second"
            target = tree.nodes.new("GeometryNodeJoinGeometry")
            target.name = "Join"
            output = tree.nodes.new("NodeGroupOutput")
            output.name = "Output"
            tree.links.new(first.outputs["Geometry"], target.inputs["Geometry"])
            tree.links.new(second.outputs["Mesh"], target.inputs["Geometry"])
            tree.links.new(target.outputs["Geometry"], output.inputs["Geometry"])
            tree[NATIVE_GRAPH[0]] = json.dumps(validate_operation({"kind": "geometry_nodes", "target": "metadata",
                "parameters": provenance(tree, "Geometry")})["parameters"], sort_keys=True, separators=(",", ":"))
            modifier = obj.modifiers.new("Geometry", "NODES")
            modifier.node_group = tree
            root = {"modifier": "Geometry"}
            edit = {"op": "unlink", "to_node": "Join", "to_socket": "Geometry"}
            if name == "multi_selective":
                edit.update(from_node="Second", from_socket="Mesh")
            preserved = [{"from_node": "First", "from_socket": "Geometry", "to_node": "Join", "to_socket": "Geometry"}]
        else:
            material = bpy.data.materials.new("Unlink Paint")
            if material.node_tree is None:
                material.use_nodes = True
            mesh.materials.append(material)
            tree = material.node_tree
            first = tree.nodes.new("ShaderNodeValue")
            first.name = "First"
            target = tree.nodes.new("ShaderNodeMath")
            target.name = "Math"
            tree.links.new(first.outputs[0], target.inputs[1])
            group = bpy.data.node_groups.new("Unlink source", "ShaderNodeTree")
            groups.add(group)
            group.interface.new_socket(name="Tint", in_out="OUTPUT", socket_type="NodeSocketFloat")
            group.nodes.new("NodeGroupOutput")
            second = tree.nodes.new("ShaderNodeGroup")
            second.name, second.node_tree = "Second", group
            tree.links.new(second.outputs[0], target.inputs[0])
            root = {"material": material.name}
            edit = {"op": "unlink", "to_node": "Math", "to_socket": "Value"}
            if name not in {"all_first_named_socket", "all_unlink_then_opaque"}:
                edit.update(from_node="Second", from_socket="Tint" if name == "source_name" else second.outputs[0].identifier)
            preserved = [{"from_node": "First", "from_socket": "Value", "to_node": "Math",
                          "to_socket": target.inputs[1].identifier}]
        edits = [edit]
        if name == "removed_endpoint":
            edits.append({"op": "remove", "node": "Second"})
        if name == "swapped_endpoint":
            edits.append({"op": "swap", "node": "Math", "type": "ShaderNodeVectorMath"})
        if name == "all_unlink_then_opaque":
            edits.append({"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}})
        queries.invalidate()
        handle = queries.object_handle(obj)
        expected = [] if name in {"multi_all", "swapped_endpoint"} else [
            {"kind": "node_graph", "target": handle, **root, "links": preserved}]
        if name == "authored_unlink_conflict":
            expected.append({"kind": "node_graph", "target": handle, **root, "links": [
                {"from_node": "Second", "from_socket": second.outputs[0].identifier,
                 "to_node": "Math", "to_socket": target.inputs[0].identifier}]})
        arguments = validate_call("angelo_scene_program", {"mode": "APPLY",
            "write_set": [{"handle": handle, "revision": queries.object_revision(window, obj)}],
            "operations": [{"kind": "node_edit", "target": handle, "parameters": dict(root, edits=edits)}],
            "expected_checks": expected})
        before = native._fingerprint(obj)
        approved = native.precondition(window, "angelo_scene_program", arguments)
        job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
        result = poll(job)
        data = report["cases"][name] = {"staged": copy.deepcopy(result), "unknowns": job.obligations.unknowns()}
        if name == "authored_unlink_conflict":
            check(name + "_failed", result.get("success") is False and native._fingerprint(obj) == before)
            clean_now(name + "_immediate_cleanup")
            return
        check(name + "_ready", result.get("ready_to_commit"))
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        data["committed"] = copy.deepcopy(job.result)
        check(name + "_committed", job.result.get("status") == "committed")
        if name == "all_unlink_then_opaque":
            check(name + "_prior_absence_explicitly_unknown", bool(job.obligations.unknowns())
                  and job.result.get("verification", {}).get("status") == "partial")
        result_tree = obj.modifiers["Geometry"].node_group if geometry else obj.material_slots[0].material.node_tree
        if geometry:
            links = result_tree.nodes["Join"].inputs["Geometry"].links
            check(name + "_actual_remaining_edges", len(links) == (1 if name == "multi_selective" else 0)
                  and all(link.from_node.name == "First" for link in links))
        elif name == "swapped_endpoint":
            check(name + "_new_generation", result_tree.nodes["Math"].bl_idname == "ShaderNodeVectorMath")
        else:
            inputs = result_tree.nodes["Math"].inputs
            check(name + "_exact_duplicate_name_target", len(inputs[0].links) == 0 and len(inputs[1].links) == 1)
        if name == "removed_endpoint":
            check(name + "_retired_source", result_tree.nodes.get("Second") is None)
        clean_now(name + "_immediate_cleanup")
    finally:
        if job is not None and not job.closed:
            job.cancel()
        native.cleanup_all()
        window.scene = factory
        materials = {slot.material for slot in obj.material_slots if slot.material}
        groups.update(mod.node_group for mod in obj.modifiers if mod.type == "NODES" and mod.node_group)
        bpy.data.scenes.remove(scene)
        bpy.data.batch_remove(ids={obj, mesh, *materials, *groups})
        queries.invalidate()


def certify(job, arguments):
    """The host summary through the production completion proof of the finished job."""
    from types import SimpleNamespace
    from angelo_agent.program_completion import mint_completion
    host = SimpleNamespace(_epoch=1, _request_generation=1, _closing=False, cancelled=False)
    delivery = SimpleNamespace(id="native-topology", epoch=1, generation=1, name="angelo_scene_program",
                               arguments=arguments, program_arguments=None)
    completion = mint_completion(host, delivery, capture_program(job), job.result)
    return certified_completion([{"tool": "angelo_scene_program", "arguments": arguments}], [job.result],
                                read_only=lambda tool: False, completions=[completion])


def topology_case():
    """A topology edit no check measures leaves the program partial.

    Beside a named box whose derived checks all pass, an unchecked extrude was
    certified "Host-verified". It is now an explicit unverified effect; a
    declared geometry check of the extruded object makes the program complete.
    """
    import bmesh
    scene = bpy.data.scenes.new("Partial verification topology")
    window.scene = scene
    mesh = bpy.data.meshes.new("Topology mesh")
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=2)
    shape.to_mesh(mesh)
    shape.free()
    obj = bpy.data.objects.new("Topology cube", mesh)
    scene.collection.objects.link(obj)
    queries.invalidate()
    handle = queries.object_handle(obj)
    data = report["cases"]["topology"] = {}
    try:
        for label, measured in (("unchecked", False), ("measured", True)):
            # An extrude adds faces and moves them: counts and dimensions together see both.
            checks = [{"kind": "dimensions", "target": handle, "space": "WORLD", "tolerance": 0.0001,
                       "expected": [obj.dimensions.x, obj.dimensions.y, obj.dimensions.z + 1]},
                      {"kind": "mesh_counts", "target": handle, **{key: {"minimum": 1, "maximum": 100}
                                                                    for key in ("vertices", "edges", "faces")}}] if measured else []
            arguments = validate_call("angelo_scene_program", {
                "mode": "APPLY", "write_set": [{"handle": handle, "revision": queries.object_revision(window, obj)}],
                "operations": [
                    {"kind": "create_primitive", "target": None, "parameters": {
                        "primitive": "CUBE", "name": "Topology Marker " + label, "alias": "marker", "size": 1,
                        "location": [4, 0, 0]}},
                    {"kind": "mesh_extrude", "target": handle, "parameters": {
                        "face_selector": {"normal": [0, 0, 1]}, "translation": [0, 0, 1.0]}}],
                "expected_checks": checks})
            approved = native.precondition(window, "angelo_scene_program", arguments)
            unknowns = approved.obligations.unknowns()
            job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
            check("topology_" + label + "_ready", poll(job).get("ready_to_commit"))
            bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
            summary = certify(job, arguments)
            data[label] = {"unknowns": unknowns, "verification": job.result.get("verification"), "summary": summary}
            check("topology_" + label + "_committed", job.result.get("status") == "committed"
                  and all(row.get("passed") for row in job.result.get("checks", ())))
            if measured:
                check("topology_measured_is_complete", not unknowns and "verification" not in job.result
                      and summary is not None and summary.startswith("Host-verified result"))
            else:
                check("topology_unchecked_is_partial", [(row["operation"], row["reason"]) for row in unknowns]
                      == [(1, "unchecked_geometry_edit")]
                      and job.result.get("verification", {}).get("status") == "partial")
                check("topology_unchecked_is_not_certified", summary is None)
            clean_now("topology_" + label + "_cleanup")
    finally:
        native.cleanup_all()
        window.scene = factory
        created = [item for item in bpy.data.objects if item.name.startswith("Topology Marker")]
        meshes = {item.data for item in created} | {obj.data}
        bpy.data.scenes.remove(scene)
        bpy.data.batch_remove(ids={obj, *created, *meshes})
        queries.invalidate()


def geometry_cases():
    """Every geometry change beside a named box: partial unless a check measures it.

    A modifier apply, a join, a conversion, a data operator and a curve rebuild
    were certified with only the box's checks. Each now leaves the program
    partial, except curve_points, which derives its own point and spline
    counts, and an extrude a declared dimensions check measures, even with
    batch copies after it.
    """
    import bmesh
    box = {"kind": "create_primitive", "target": None,
           "parameters": {"primitive": "CUBE", "name": "Geometry Marker", "alias": "m", "size": 1, "location": [6, 0, 0]}}

    def op(kind, target=None, **parameters):
        return {"kind": kind, "target": target, "parameters": parameters}

    cases = {
        "modifier_apply": lambda c, o, cu: ([box, op("modifier_add", c, type="SUBSURF", name="Geometry Sub",
                                                     settings={"levels": 2}),
                                             op("modifier_apply", c, modifier="Geometry Sub")], [c], [], "partial"),
        "object_join": lambda c, o, cu: ([box, op("object_join", c, sources=[o])], [c, o], [], "partial"),
        "object_convert": lambda c, o, cu: ([box, op("object_convert", source=cu, alias="cm", keep_original=False)],
                                            [cu], [], "partial"),
        "operator_run": lambda c, o, cu: ([box, op("operator_run", c, operator="mesh.subdivide",
                                                   properties={"number_cuts": 2})], [c], [], "partial"),
        "curve_points": lambda c, o, cu: ([box, op("curve_points", cu, points=[[0, 0, 0], [1, 1, 0], [2, 0, 0], [3, 1, 0]])],
                                          [cu], [], "certified"),
        "extrude_on_a_new_object": lambda c, o, cu: ([box, op("mesh_extrude", "$m", face_selector={"normal": [0, 0, 1]},
                                                              translation=[0, 0, 2.0])], [], [], "partial"),
        "measured_extrude_then_batch": lambda c, o, cu: (
            [op("mesh_extrude", c, face_selector={"normal": [0, 0, 1]}, translation=[0, 0, 2.0]),
             op("batch", sources=[c], array=[{"step": [0, 3, 0], "count": 3}])], [c],
            [{"kind": "dimensions", "target": c, "expected": [2, 2, 4], "tolerance": 0.01},
             {"kind": "mesh_counts", "target": c, **{key: {"minimum": 1, "maximum": 100}
                                                     for key in ("vertices", "edges", "faces")}}], "certified"),
    }
    for name, make in cases.items():
        scene = bpy.data.scenes.new("Partial verification geometry " + name)
        window.scene = scene
        made = []
        for label, location in (("Geometry Cube", 0), ("Geometry Other", 3)):
            mesh = bpy.data.meshes.new(label + " mesh")
            shape = bmesh.new()
            bmesh.ops.create_cube(shape, size=2)
            shape.to_mesh(mesh)
            shape.free()
            obj = bpy.data.objects.new(label, mesh)
            obj.location.x = location
            scene.collection.objects.link(obj)
            made.append(obj)
        curve = bpy.data.curves.new("Geometry curve", "CURVE")
        curve.dimensions = "3D"
        spline = curve.splines.new("POLY")
        spline.points.add(2)
        for index, point in enumerate(spline.points):
            point.co = (index, 0, 0, 1)
        curve_object = bpy.data.objects.new("Geometry Curve", curve)
        scene.collection.objects.link(curve_object)
        made.append(curve_object)
        bpy.context.view_layer.update()
        queries.invalidate()
        handles = [queries.object_handle(obj) for obj in made]
        operations, writes, checks, expected = make(*handles)
        arguments = validate_call("angelo_scene_program", {
            "mode": "APPLY", "expected_checks": checks, "operations": operations,
            "write_set": [{"handle": handle, "revision": queries.object_revision(window, made[handles.index(handle)])}
                          for handle in writes]})
        data = report["cases"]["geometry_" + name] = {}
        try:
            approved = native.precondition(window, "angelo_scene_program", arguments)
            job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
            check("geometry_" + name + "_ready", poll(job).get("ready_to_commit"))
            bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
            summary = certify(job, arguments)
            reasons = [(row["reason"], row["affected"][0]["kind"]) for row in job.obligations.unknowns()]
            data.update(unknowns=reasons, verification=job.result.get("verification"), summary=summary)
            check("geometry_" + name + "_committed", job.result.get("status") == "committed"
                  and all(row.get("passed") for row in job.result.get("checks", ())))
            if expected == "certified":
                check("geometry_" + name + "_certified", not reasons and summary is not None
                      and summary.startswith("Host-verified result"))
            else:
                check("geometry_" + name + "_partial", [reason for reason, _kind in reasons] == ["unchecked_geometry_edit"]
                      and job.result.get("verification", {}).get("status") == "partial" and summary is None)
            clean_now("geometry_" + name + "_cleanup")
        finally:
            native.cleanup_all()
            window.scene = factory
            # Whatever the program left in its scene (a joined or converted source is already gone).
            leftovers = set(scene.objects) | {obj.data for obj in scene.objects if obj.data is not None}
            bpy.data.scenes.remove(scene)
            bpy.data.batch_remove(ids=leftovers)
            queries.invalidate()


try:
    topology_case()
    geometry_cases()
    for name in ("apply", "preview_accept", "authored_failure", "known_failure", "stale", "cancel"):
        run_case(name)
    for name in ("source_name", "source_identifier", "all_first_named_socket", "all_unlink_then_opaque", "removed_endpoint",
                 "swapped_endpoint", "authored_unlink_conflict", "multi_selective", "multi_all"):
        unlink_case(name, geometry=name.startswith("multi_"))
    report["passed"] = True
except Exception:
    report["passed"] = False
    report["traceback"] = traceback.format_exc()
    raise
finally:
    bpy.utils.unregister_class(CommitOperator)
    (OUT / "partial-checks.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
