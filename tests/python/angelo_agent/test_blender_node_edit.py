# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""node_edit through real staged programs: material, Geometry Nodes and World trees, with Undo and Redo.

Programs are normalized by ``tool_specs.validate_call`` as a model's plan is,
so each carries its derived ``node_graph`` check. Covers staging isolation
across suspend/resume, commit and name adoption, preview and accept, stale
revisions before binding and before commit, shared trees (refused, isolated,
a fake user, shared mesh data, users gained before commit), every structural
step (reconnect, detach, swap, insert, exact duplicates of ramps and curves,
make group, ungroup, interface sockets of a nested group shared or not),
Geometry Nodes catalog graphs bounded again before evaluation, a World with an
environment image, the refusals and a failed commit's rollback. Run only in a
disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-node-edit-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_commit, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.node_copy import digest  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def obj(name):
    return window.scene.objects[name]


def handle(target):
    return queries.object_handle(target)


def binding(target):
    return {"handle": handle(target), "revision": queries.object_revision(window, target)}


def scene_binding():
    settings = scene_settings.execute(window, {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def edit(target, edits, **tree):
    return {"kind": "node_edit", "target": target if isinstance(target, str) else handle(target),
            "parameters": dict(tree or {"material": "QA Wood"}, edits=edits)}


def program(operations, writes=(), reads=(), mode="APPLY", scene=False):
    write_set = [binding(item) for item in writes] + ([scene_binding()] if scene else [])
    return validate_call("angelo_scene_program", {"mode": mode, "write_set": write_set,
                                                  "read_set": [binding(item) for item in reads], "operations": operations})


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def refused(build, code, policy=None):
    """``build()`` makes the program after observing; it must fail with ``code`` and leave no stage.

    ``policy``: the closed refusal code the staged program's failure must carry, so a refusal before
    staging (plan validation) whose text merely contains ``code`` cannot pass for it.
    """
    queries.invalidate(window)
    try:
        job, result = run(build())
        report.setdefault("refusals", {})[code] = result.get("error")
        if result.get("ready_to_commit"):
            job.cancel()  # Not refused: release the writer, then fail the check.
        return (result.get("status") == "failed" and code in (result.get("error") or "") and no_stages()
                and policy in (None, result.get("error_code")))
    except ValueError as error:
        report.setdefault("refusals", {})[code] = str(error)[:300]
        return policy is None and code in str(error) and no_stages()


def apply(build):
    queries.invalidate(window)
    job, result = run(build())
    report.setdefault("applied", []).append({key: result.get(key) for key in ("status", "error")})
    return job if result.get("ready_to_commit") and commit(job) else None


def bindable(target):
    queries.invalidate(window)
    return isinstance(queries.object_revision(window, target), str)


def tree_of(target, name="QA Wood"):
    return next(slot.material for slot in target.material_slots if slot.material and slot.material.name == name).node_tree


def material(name):
    created = bpy.data.materials.new(name)
    return created


def setup():
    cube = obj("Cube")
    cube.data.materials.clear()
    wood = material("QA Wood")
    cube.data.materials.append(wood)
    tree = wood.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.name = "Noise"
    tree.links.new(noise.outputs["Fac"], tree.nodes["Principled BSDF"].inputs["Roughness"])
    ball = bpy.data.objects.new("QA Ball", bpy.data.meshes.new("QA Ball"))
    ball.data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    window.scene.collection.objects.link(ball)
    queries.invalidate()


def material_edits():
    """Steps on a private copy across suspend/resume; commit keeps the material's name; one Undo and Redo."""
    cube = obj("Cube")
    live = cube.material_slots[0].material
    before, revision = native._fingerprint(cube), binding(cube)["revision"]
    bpy.ops.ed.undo_push(message="node_edit QA material baseline")
    queries.invalidate(window)
    arguments = program([edit(cube, [
        {"op": "add", "node": "Ramp", "type": "ShaderNodeValToRGB", "location": [-400, 200]},
        {"op": "insert", "node": "Ramp", "from_node": "Noise", "from_socket": "Fac", "to_node": "Principled BSDF",
         "to_socket": "Roughness", "input": "Fac", "output": "Alpha"},
        {"op": "add", "node": "Frame", "type": "NodeFrame", "settings": {"label": "Pattern"}},
        {"op": "parent", "nodes": ["Noise", "Ramp"], "frame": "Frame"}]),
        edit(cube, [{"op": "set", "node": "Principled BSDF", "inputs": {"Metallic": .25},
                     "settings": {"label": "Main", "use_custom_color": True, "color": [1, .5, 0]}},
                    {"op": "set", "node": "Noise", "settings": {"mute": True, "hide": True}}])], writes=[cube])
    with spent_budget():
        job, result = run(arguments)
    check("material_program_derives_node_graph_checks",
          [row["kind"] for row in job.obligations.checks()].count("node_graph") >= 1
          and arguments["expected_checks"] == [])
    check("material_ready_without_live_effects", result.get("ready_to_commit") and native._fingerprint(cube) == before
          and no_stages() and len(live.node_tree.nodes) == 3)
    check("material_commits_with_checks", commit(job) and passed(job))
    committed = cube.material_slots[0].material
    tree = committed.node_tree
    check("material_live_effects", committed.name == "QA Wood" and tree.nodes["Ramp"].parent == tree.nodes["Frame"]
          and tree.nodes["Noise"].mute and tree.nodes["Noise"].hide
          and abs(tree.nodes["Principled BSDF"].inputs["Metallic"].default_value - .25) < 1e-6
          and tree.nodes["Principled BSDF"].label == "Main" and not tree.nodes["Ramp"].select
          and any(link.from_node.name == "Ramp" and link.to_socket.name == "Roughness" for link in tree.links))
    check("material_original_is_an_orphan", live.users == 0 and live.name != "QA Wood")
    check("material_stays_bindable_with_frames", bindable(cube))
    check("material_revision_changed", queries.object_revision(window, cube) != revision)
    check("material_no_stage_residue", no_stages() and not native.runtime_is_staging())
    bpy.ops.ed.undo()
    queries.invalidate()
    check("material_one_undo", "Ramp" not in tree_of(obj("Cube")).nodes and len(tree_of(obj("Cube")).nodes) == 3)
    bpy.ops.ed.redo()
    queries.invalidate()
    check("material_one_redo", "Ramp" in tree_of(obj("Cube")).nodes and tree_of(obj("Cube")).nodes["Noise"].mute)


def preview_and_stale():
    """A preview leaves the live tree alone until accepted; a manual edit after observing or staging is stale."""
    queries.invalidate()
    cube = obj("Cube")
    arguments = program([edit(cube, [{"op": "set", "node": "Ramp", "settings": {"label": "Preview"}}])], writes=[cube],
                        mode="PREVIEW")
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    rows = [row.get("changes", {}).get("node_trees") for row in result.get("delta", {}).get("details", [])]
    check("preview_keeps_live_tree", result.get("status") == "preview" and tree_of(cube).nodes["Ramp"].label == ""
          and no_stages() and any(rows) and rows[0][0]["policy"] == "REMAP")
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("preview_accept_commits", settle(accepted).get("ready_to_commit") and commit(accepted)
          and tree_of(obj("Cube")).nodes["Ramp"].label == "Preview")
    queries.invalidate()
    cube = obj("Cube")
    observed = binding(cube)
    tree_of(cube).nodes["Principled BSDF"].use_custom_color = False  # A manual layout edit of a known node.
    queries.invalidate(window)
    check("layout_edit_changes_revision", queries.object_revision(window, cube) != observed["revision"])
    try:
        stale = validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": [observed], "read_set": [],
                                                       "operations": [edit(cube, [{"op": "remove", "node": "Frame"}])]})
        _job, outcome = run(stale)
        check("stale_revision_refused", outcome.get("status") == "failed" and "Frame" in tree_of(cube).nodes)
    except ValueError as error:
        check("stale_revision_refused", "revision" in str(error) and "Frame" in tree_of(cube).nodes)
    arguments = program([edit(cube, [{"op": "remove", "node": "Frame"}])], writes=[cube], mode="PREVIEW")
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    tree_of(cube).nodes["Ramp"].color = (0, 1, 0)  # A manual edit while the preview waits.
    try:
        accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
        stale = settle(accepted).get("status") == "failed" or not commit(accepted)
    except Exception:
        stale = True
    check("edit_during_preview_refused", stale and "Frame" in tree_of(cube).nodes and no_stages())


def shared_trees():
    """Shared trees are refused, isolated only on request, and re-checked at commit."""
    queries.invalidate()
    cube, ball = obj("Cube"), obj("QA Ball")
    shared = material("QA Shared")
    cube.data.materials.append(shared)
    ball.data.materials.append(shared)
    roughness = lambda block: block.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value
    step = [{"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": .9}}]
    check("shared_material_refused", refused(lambda: program([edit(obj("Cube"), step, material="QA Shared")],
                                                             writes=[obj("Cube")]), "NODE_TREE_SHARED"))
    bpy.ops.ed.undo_push(message="node_edit QA isolate baseline")
    job = apply(lambda: program([edit(obj("Cube"), step, material="QA Shared", shared="ISOLATE")], writes=[obj("Cube")]))
    cube_material, ball_material = obj("Cube").material_slots[1].material, obj("QA Ball").material_slots[0].material
    check("shared_material_isolated", job is not None and job.result.get("isolated_shared_data")
          and cube_material != ball_material and ball_material.name == "QA Shared"
          and abs(roughness(cube_material) - .9) < 1e-6 and abs(roughness(ball_material) - .5) < 1e-6)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("isolation_one_undo", obj("Cube").material_slots[1].material == obj("QA Ball").material_slots[0].material)
    obj("QA Ball").data.materials.clear()
    kept = bpy.data.materials["QA Shared"]
    kept.use_fake_user = True
    check("fake_user_counts_as_shared", refused(lambda: program([edit(obj("Cube"), step, material="QA Shared")],
                                                                writes=[obj("Cube")]), "fake user"))
    kept.use_fake_user = False
    twin = bpy.data.objects.new("QA Twin", obj("Cube").data)  # A linked duplicate: the mesh data is shared.
    window.scene.collection.objects.link(twin)
    check("shared_mesh_slot_not_isolated", refused(lambda: program(
        [edit(obj("Cube"), step, material="QA Shared", shared="ISOLATE")], writes=[obj("Cube")]), "NODE_TREE_SHARED")
        or refused(lambda: program([edit(obj("Cube"), step, material="QA Shared", shared="ISOLATE")],
                                   writes=[obj("Cube")]), "DATA_SHARED"))
    bpy.data.objects.remove(twin)
    queries.invalidate(window)
    arguments = program([edit(obj("Cube"), step, material="QA Shared")], writes=[obj("Cube")], mode="PREVIEW")
    result = settle(native.begin(window, "angelo_scene_program", arguments))
    obj("QA Ball").data.materials.append(kept)  # Another user arrives while the preview waits.
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    outcome = settle(accepted)
    gained = outcome.get("status") == "failed" or not commit(accepted)
    check("users_gained_before_commit_refused", gained and abs(roughness(kept) - .5) < 1e-6
          and obj("Cube").material_slots[1].material == kept and no_stages())
    obj("QA Ball").data.materials.clear()
    queries.invalidate()


def structure():
    """Reconnect, detach, swap, insert, exact duplicates, make group, ungroup and nested interfaces."""
    queries.invalidate()
    cube = obj("Cube")
    steps = material("QA Steps")
    cube.data.materials.append(steps)
    tree = steps.node_tree
    for kind, name in (("ShaderNodeBrightContrast", "Bright"), ("ShaderNodeValToRGB", "Ramp"),
                       ("ShaderNodeRGBCurve", "Curve"), ("ShaderNodeTexCoord", "Coords")):
        tree.nodes.new(kind).name = name
    ramp = tree.nodes["Ramp"].color_ramp
    ramp.elements.new(.4).color = (1, 0, 0, 1)
    ramp.interpolation = "EASE"
    curve = tree.nodes["Curve"].mapping
    curve.curves[3].points.new(.3, .7)
    curve.update()
    tree.links.new(tree.nodes["Coords"].outputs["Generated"], tree.nodes["Bright"].inputs["Color"])
    tree.links.new(tree.nodes["Bright"].outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    bpy.ops.ed.undo_push(message="node_edit QA structure baseline")
    job = apply(lambda: program([edit(obj("Cube"), [
        {"op": "duplicate", "node": "Ramp", "as": "Ramp Copy"}, {"op": "duplicate", "node": "Curve", "as": "Curve Copy"},
        {"op": "add", "node": "Route", "type": "NodeReroute"},
        {"op": "insert", "node": "Route", "from_node": "Bright", "from_socket": "Color", "to_node": "Principled BSDF",
         "to_socket": "Base Color"},
        {"op": "remove", "node": "Route", "reconnect": True},
        {"op": "swap", "node": "Bright", "type": "ShaderNodeHueSaturation"}], material="QA Steps")],
        writes=[obj("Cube")]))
    tree = tree_of(obj("Cube"), "QA Steps")
    links = {(link.from_node.name, link.to_node.name, link.to_socket.name) for link in tree.links}
    check("structure_steps_commit", job is not None and passed(job) and tree.nodes["Bright"].bl_idname
          == "ShaderNodeHueSaturation" and ("Coords", "Bright", "Color") in links
          and ("Bright", "Principled BSDF", "Base Color") in links and "Route" not in tree.nodes)
    check("duplicates_are_exact", digest(tree.nodes["Ramp Copy"])[1:] == digest(tree.nodes["Ramp"])[1:]
          and digest(tree.nodes["Curve Copy"])[1:] == digest(tree.nodes["Curve"])[1:]
          and len(tree.nodes["Ramp Copy"].color_ramp.elements) == 3)
    job = apply(lambda: program([edit(obj("Cube"), [
        {"op": "detach", "node": "Bright", "reconnect": True},
        {"op": "group", "nodes": ["Ramp", "Ramp Copy"], "node": "QA Grouped", "name": "QA Ramp Group"}],
        material="QA Steps")], writes=[obj("Cube")]))
    tree = tree_of(obj("Cube"), "QA Steps")
    links = {(link.from_node.name, link.to_node.name) for link in tree.links}
    check("detach_and_group_commit", job is not None and ("Coords", "Principled BSDF") in links
          and tree.nodes["QA Grouped"].node_tree.name == "QA Ramp Group" and "Ramp" not in tree.nodes
          and len(tree.nodes["QA Grouped"].node_tree.nodes) == 4)
    job = apply(lambda: program([edit(obj("Cube"), [
        {"op": "interface", "action": "ADD", "name": "Gain", "in_out": "INPUT", "socket_type": "FLOAT", "default": .5,
         "min": 0, "max": 2},
        {"op": "interface", "action": "ADD", "name": "Tint", "in_out": "OUTPUT", "socket_type": "COLOR"},
        {"op": "interface", "action": "SET", "name": "Gain", "in_out": "INPUT", "new_name": "Strength"}],
        material="QA Steps", group="QA Grouped")], writes=[obj("Cube")]))
    group = tree_of(obj("Cube"), "QA Steps").nodes["QA Grouped"]
    check("nested_interface_commits", job is not None and [s.name for s in group.inputs] == ["Strength"]
          and [s.name for s in group.outputs] == ["Tint"] and group.node_tree.name == "QA Ramp Group"
          and abs(group.inputs["Strength"].default_value - .5) < 1e-6)
    other = material("QA Other")
    other.node_tree.nodes.new("ShaderNodeGroup").node_tree = group.node_tree
    obj("QA Ball").data.materials.append(other)
    check("shared_nested_group_refused", refused(lambda: program([edit(obj("Cube"), [
        {"op": "interface", "action": "REMOVE", "name": "Tint", "in_out": "OUTPUT"}], material="QA Steps",
        group="QA Grouped")], writes=[obj("Cube")]), "NODE_TREE_SHARED"))
    job = apply(lambda: program([edit(obj("Cube"), [
        {"op": "interface", "action": "REMOVE", "name": "Tint", "in_out": "OUTPUT"}], material="QA Steps",
        group="QA Grouped", shared="ISOLATE")], writes=[obj("Cube")]))
    mine = tree_of(obj("Cube"), "QA Steps").nodes["QA Grouped"].node_tree
    theirs = other.node_tree.nodes["Group"].node_tree
    check("shared_nested_group_isolated", job is not None and mine != theirs and theirs.name == "QA Ramp Group"
          and [item.name for item in theirs.interface.items_tree if item.in_out == "OUTPUT"] == ["Tint"]
          and [item.name for item in mine.interface.items_tree if item.in_out == "OUTPUT"] == [])
    job = apply(lambda: program([edit(obj("Cube"), [{"op": "ungroup", "node": "QA Grouped"}], material="QA Steps")],
                                writes=[obj("Cube")]))
    tree = tree_of(obj("Cube"), "QA Steps")
    check("ungroup_commits", job is not None and "QA Grouped" not in tree.nodes and "Ramp" in tree.nodes
          and "Ramp Copy" in tree.nodes and theirs.users >= 1)
    obj("QA Ball").data.materials.clear()
    shell = material("QA Shell")  # One material on two objects; its group is used by that material only.
    inner = bpy.data.node_groups.new("QA Shell Group", "ShaderNodeTree")
    shell.node_tree.nodes.new("ShaderNodeGroup").node_tree = inner
    shell.node_tree.nodes["Group"].name = "Shell"
    obj("Cube").data.materials.append(shell)
    obj("QA Ball").data.materials.append(shell)
    add_input = [{"op": "interface", "action": "ADD", "name": "Wear", "in_out": "INPUT", "socket_type": "FLOAT"}]
    check("group_in_shared_material_refused", refused(lambda: program([edit(obj("Cube"), add_input, material="QA Shell",
                                                                            group="Shell")], writes=[obj("Cube")]),
                                                      "NODE_TREE_SHARED"))
    job = apply(lambda: program([edit(obj("Cube"), add_input, material="QA Shell", group="Shell", shared="ISOLATE")],
                                writes=[obj("Cube")]))
    mine = next(slot.material for slot in obj("Cube").material_slots if slot.material.name.startswith("QA Shell"))
    theirs = obj("QA Ball").material_slots[0].material
    check("group_in_shared_material_isolated", job is not None and mine != theirs and theirs.name == "QA Shell"
          and [s.name for s in mine.node_tree.nodes["Shell"].inputs] == ["Wear"]
          and not len(theirs.node_tree.nodes["Shell"].inputs) and theirs.node_tree.nodes["Shell"].node_tree == inner
          and not len(inner.interface.items_tree))
    obj("QA Ball").data.materials.clear()
    bpy.ops.ed.undo_push(message="node_edit QA after structure")
    queries.invalidate()


def geometry_nodes():
    """Catalog graphs stay closed and bounded; shared groups follow the same policy."""
    queries.invalidate()
    ball = obj("QA Ball")
    bpy.ops.ed.undo_push(message="node_edit QA geometry baseline")
    job = apply(lambda: program([{"kind": "geometry_nodes", "target": handle(obj("QA Ball")), "parameters": {
        "name": "QA Scatter", "nodes": [
            {"id": "inp", "type": "NodeGroupInput"},
            {"id": "scatter", "type": "GeometryNodeDistributePointsOnFaces", "inputs": {"Density": 50}},
            {"id": "out", "type": "NodeGroupOutput"}], "links": [
            {"from_node": "inp", "from_socket": "Geometry", "to_node": "scatter", "to_socket": "Mesh"},
            {"from_node": "scatter", "from_socket": "Points", "to_node": "out", "to_socket": "Geometry"}]}}],
        writes=[obj("QA Ball")]))
    check("geometry_graph_created", job is not None)
    job = apply(lambda: program([edit(obj("QA Ball"), [
        {"op": "set", "node": "scatter", "inputs": {"Density": 20}},
        {"op": "add", "node": "lift", "type": "GeometryNodeSetPosition", "inputs": {"Offset": [0, 0, .5]}},
        {"op": "insert", "node": "lift", "from_node": "scatter", "from_socket": "Points", "to_node": "out",
         "to_socket": "Geometry"}], modifier="QA Scatter")], writes=[obj("QA Ball")]))
    group = obj("QA Ball").modifiers["QA Scatter"].node_group
    window.view_layer.update()
    points = obj("QA Ball").evaluated_get(bpy.context.evaluated_depsgraph_get()).evaluated_geometry()
    count = len(points.pointcloud.points) if points.pointcloud is not None else 0
    check("geometry_edit_commits", job is not None and passed(job) and group.name == "QA Scatter"
          and abs(group.nodes["scatter"].inputs["Density"].default_value - 20) < 1e-6 and "lift" in group.nodes
          and 0 < count <= 20 * .5 + 2 and list(group.keys()) == ["_angelo_native_graph_v1"])
    check("geometry_stays_bindable", bindable(obj("QA Ball")))
    # Each is node_edit's own refusal ("node_edit CODE:"), never another layer's text that contains the code.
    # The density passes plan validation (its derived check holds each value at its own precision) and the
    # stack's evaluated bound refuses it before evaluation: domain BUDGET, policy LIMIT_EXCEEDED.
    for code, policy, steps in (("BUDGET", "LIMIT_EXCEEDED", [{"op": "set", "node": "scatter",
                                                               "inputs": {"Density": 1e9}}]),
                                ("GN_CONTRACT", None, [{"op": "set", "node": "lift", "settings": {"mute": True}}]),
                                ("GN_CONTRACT", None, [{"op": "add", "node": "Frame", "type": "NodeFrame"}]),
                                ("GN_CONTRACT", None, [{"op": "add", "node": "Bad Name",
                                                        "type": "GeometryNodeTransform"}]),
                                ("NODE_TYPE_DENIED", None, [{"op": "add", "node": "info",
                                                             "type": "GeometryNodeObjectInfo"}])):
        check("geometry_refused_" + code + "_" + steps[0]["op"] + str(len(report["checks"])),
              refused(lambda steps=steps: program([edit(obj("QA Ball"), steps, modifier="QA Scatter")],
                                                  writes=[obj("QA Ball")]), "node_edit %s:" % code, policy))
    bpy.ops.ed.undo()
    queries.invalidate()
    check("geometry_one_undo", "lift" not in obj("QA Ball").modifiers["QA Scatter"].node_group.nodes)
    bpy.ops.ed.redo()
    queries.invalidate()
    check("geometry_one_redo", "lift" in obj("QA Ball").modifiers["QA Scatter"].node_group.nodes)
    shared = obj("Cube").modifiers.new("QA Shared Scatter", "NODES")
    shared.node_group = obj("QA Ball").modifiers["QA Scatter"].node_group
    check("shared_geometry_refused", refused(lambda: program([edit(obj("QA Ball"), [
        {"op": "set", "node": "scatter", "inputs": {"Density": 10}}], modifier="QA Scatter")],
        writes=[obj("QA Ball")]), "NODE_TREE_SHARED"))
    job = apply(lambda: program([edit(obj("QA Ball"), [{"op": "set", "node": "scatter", "inputs": {"Density": 10}}],
                                      modifier="QA Scatter", shared="ISOLATE")], writes=[obj("QA Ball")]))
    mine, theirs = obj("QA Ball").modifiers["QA Scatter"].node_group, obj("Cube").modifiers["QA Shared Scatter"].node_group
    check("shared_geometry_isolated", job is not None and mine != theirs
          and abs(mine.nodes["scatter"].inputs["Density"].default_value - 10) < 1e-6
          and abs(theirs.nodes["scatter"].inputs["Density"].default_value - 20) < 1e-6 and bindable(obj("QA Ball")))
    obj("Cube").modifiers.remove(obj("Cube").modifiers["QA Shared Scatter"])
    queries.invalidate()


def world():
    """Any World graph on a private copy, its environment image kept by reference; conflicts refused."""
    queries.invalidate()
    sky = bpy.data.images.new("QA Sky", 8, 8)
    tree = window.scene.world.node_tree
    environment = tree.nodes.new("ShaderNodeTexEnvironment")
    environment.name, environment.image = "Sky", sky
    tree.links.new(environment.outputs["Color"], tree.nodes["Background"].inputs["Color"])
    images, world_name = len(bpy.data.images), window.scene.world.name
    bpy.ops.ed.undo_push(message="node_edit QA world baseline")
    scene = scene_binding()["handle"]
    job = apply(lambda: program([edit(scene_binding()["handle"], [
        {"op": "add", "node": "Gain", "type": "ShaderNodeValue"}, {"op": "set", "node": "Gain", "outputs": {"Value": 2.5}},
        {"op": "link", "from_node": "Gain", "from_socket": "Value", "to_node": "Background", "to_socket": "Strength"}],
        world=True)], scene=True))
    live = window.scene.world
    check("world_commits", job is not None and passed(job) and live.name == world_name and scene
          and live.node_tree.nodes["Sky"].image == sky and len(bpy.data.images) == images
          and live.node_tree.nodes["Background"].inputs["Strength"].is_linked and "_angelo_image_source_uid"
          not in live.node_tree.nodes["Sky"].keys() and no_stages())
    observed = scene_settings.execute(window, {})["world_graph"]
    check("world_graph_is_observable", ["Gain", "ShaderNodeValue"] in observed["nodes"]
          and ["Gain", "Value", "Background", "Strength"] in observed["links"] and not observed["truncated"])
    bpy.ops.ed.undo()
    queries.invalidate()
    check("world_one_undo", "Gain" not in window.scene.world.node_tree.nodes
          and window.scene.world.node_tree.nodes["Sky"].image == bpy.data.images["QA Sky"])
    bpy.ops.ed.redo()
    queries.invalidate()
    check("world_one_redo", "Gain" in window.scene.world.node_tree.nodes)
    check("world_settings_conflict_refused", refused(lambda: program([
        edit(scene_binding()["handle"], [{"op": "remove", "node": "Gain"}], world=True),
        {"kind": "property_set", "target": scene_binding()["handle"], "parameters": {"values": {"world.mist_settings.start": 2.0}}}],
        scene=True), "WORLD_CONFLICT"))
    check("world_configure_conflict_refused", refused(lambda: program([  # world_configure refuses the custom graph first.
        edit(scene_binding()["handle"], [{"op": "remove", "node": "Gain"}], world=True),
        {"kind": "world_configure", "target": scene_binding()["handle"], "parameters": {"strength": 1.0}}], scene=True),
        "CUSTOM_WORLD_GRAPH"))
    check("world_target_must_be_the_scene", refused(lambda: program([edit(obj("Cube"), [
        {"op": "remove", "node": "Gain"}], world=True)], writes=[obj("Cube")]), "OWNER_TYPE_MISMATCH"))


def refusals():
    """Graphs stay valid; requests outside the rules are refused before anything commits."""
    queries.invalidate()
    cube = obj("Cube")
    before = native._fingerprint(cube)
    cases = [
        ("OUTPUT_NODES", [{"op": "remove", "node": "Material Output"}]),
        ("OUTPUT_NODES", [{"op": "add", "node": "Out 2", "type": "ShaderNodeOutputMaterial"}]),
        ("OUTPUT_DISCONNECTED", [{"op": "unlink", "to_node": "Material Output", "to_socket": "Surface"}]),
        ("INCOMPATIBLE_SOCKETS", [{"op": "link", "from_node": "Principled BSDF", "from_socket": "BSDF",
                                   "to_node": "Noise", "to_socket": "Scale"}]),
        ("INVALID_LINK", [{"op": "link", "from_node": "Ramp", "from_socket": "Alpha", "to_node": "Noise",
                           "to_socket": "Scale"}]),  # Noise feeds Ramp: a cycle.
        ("NODE_NOT_FOUND", [{"op": "remove", "node": "Nothing"}]),
        ("NAME_TAKEN", [{"op": "add", "node": "Noise", "type": "ShaderNodeMath"}]),
        ("SOCKET_NOT_FOUND", [{"op": "set", "node": "Noise", "inputs": {"Nothing": 1}}]),
        ("RNA_RANGE", [{"op": "set", "node": "Noise", "inputs": {"Roughness": 5.0}}]),
        ("NODE_TYPE_DENIED", [{"op": "add", "node": "Script", "type": "ShaderNodeScript"}]),
        ("NODE_TYPE_UNAVAILABLE", [{"op": "add", "node": "Nope", "type": "ShaderNodeDoesNotExist"}]),
        ("SETTING_DENIED", [{"op": "set", "node": "Noise", "settings": {"select": True}}]),
        ("INTERFACE_NEEDS_GROUP", [{"op": "interface", "action": "ADD", "name": "X", "in_out": "INPUT",
                                    "socket_type": "FLOAT"}]),
    ]
    for index, (code, steps) in enumerate(cases):
        check("refused_%02d_%s" % (index, code), refused(lambda steps=steps: program([edit(obj("Cube"), steps)],
                                                                                    writes=[obj("Cube")]), code))
    check("missing_material_refused", refused(lambda: program([edit(obj("Cube"), [{"op": "remove", "node": "Noise"}],
                                                                    material="QA Nothing")], writes=[obj("Cube")]),
                                              "MATERIAL_NOT_FOUND"))
    check("new_object_refused", refused(lambda: program([
        {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "alias": "fresh"}},
        edit("$fresh", [{"op": "remove", "node": "Noise"}])]), "NEW_OBJECT"))
    animated = tree_of(obj("Cube"))
    animated.animation_data_create().action = bpy.data.actions.new("QA Tree Action")
    check("animated_tree_refused", refused(lambda: program([edit(obj("Cube"), [{"op": "remove", "node": "Frame"}])],
                                                           writes=[obj("Cube")]), "ANIMATED_TREE"))
    animated.animation_data_clear()
    bpy.data.actions.remove(bpy.data.actions["QA Tree Action"])
    queries.invalidate(window)
    check("refusals_leave_the_object", native._fingerprint(obj("Cube")) == before)


def two_slots():
    """A material in two slots stays one material: one edited copy, its edited group transferred once."""
    queries.invalidate()
    twice = material("QA Twice")
    inner = bpy.data.node_groups.new("QA Twice Group", "ShaderNodeTree")
    holder = twice.node_tree.nodes.new("ShaderNodeGroup")
    holder.node_tree, holder.name = inner, "Twice"
    for _ in range(2):
        obj("Cube").data.materials.append(twice)
    queries.invalidate(window)
    arguments = program([edit(obj("Cube"), [{"op": "interface", "action": "ADD", "name": "Grain", "in_out": "INPUT",
                                             "socket_type": "FLOAT"}], material="QA Twice", group="Twice")],
                        writes=[obj("Cube")])
    job, _result = run(arguments)
    with native.job_context(job.id) as stage:  # Both staged slots show the one edited group, as commit will.
        staged = [slot.material for slot in stage.objects[handle(obj("Cube"))].material_slots
                  if slot.material and "Twice" in slot.material.node_tree.nodes]
        preview_equal = len(staged) == 2 and staged[0] == staged[1] and len(staged[0].node_tree.nodes["Twice"].inputs) == 1
    check("two_slots_staged_consistently", preview_equal)
    check("two_slots_commit", commit(job))
    job2 = apply(lambda: program([edit(obj("Cube"), [{"op": "set", "node": "Twice", "settings": {"label": "Twice edited"}}],
                                       material="QA Twice")], writes=[obj("Cube")]))
    slots = [slot.material for slot in obj("Cube").material_slots if slot.material.name.startswith("QA Twice")]
    check("two_slots_one_edited_material", job2 is not None and len(slots) == 2 and slots[0] == slots[1]
          and slots[0].name == "QA Twice" and slots[0].node_tree.nodes["Twice"].label == "Twice edited"
          and [s.name for s in slots[0].node_tree.nodes["Twice"].inputs] == ["Grain"]
          and slots[0].node_tree.nodes["Twice"].node_tree.name == "QA Twice Group"
          and not any(key.startswith("_angelo_") for key in slots[0].node_tree.nodes["Twice"].node_tree.keys()))
    for _ in range(2):
        obj("Cube").data.materials.pop()
    queries.invalidate()


def step_refusals():
    """Every remaining refusal of the steps, the tree selectors and staging, on real trees."""
    queries.invalidate()
    convert = material("QA Convert")  # A group input of one type feeding another, unlinked on the group node.
    group = bpy.data.node_groups.new("QA Convert Group", "ShaderNodeTree")
    group_in, group_out, invert = (group.nodes.new(kind) for kind in ("NodeGroupInput", "NodeGroupOutput",
                                                                      "ShaderNodeInvert"))
    group.interface.new_socket(name="Fac", in_out="INPUT", socket_type="NodeSocketFloat")
    group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    group.links.new(group_in.outputs[0], invert.inputs["Color"])
    group.links.new(invert.outputs["Color"], group_out.inputs[0])
    holder = convert.node_tree.nodes.new("ShaderNodeGroup")
    holder.node_tree, holder.name = group, "Convert"
    convert.node_tree.nodes.new("ShaderNodeTexCoord").name = "Coords"
    obj("Cube").data.materials.append(convert)
    image = "dat:" + "0" * 32 + ":IMAGE:1"
    shell = next(slot.material.name for slot in obj("Cube").material_slots if slot.material.name.startswith("QA Shell"))
    material_cases = [
        ("UNGROUP_CONVERSION", {"material": "QA Convert"}, [{"op": "ungroup", "node": "Convert"}]),
        ("NOT_A_GROUP", {"material": "QA Convert"}, [{"op": "ungroup", "node": "Coords"}]),
        ("NO_POINTERS", {"material": "QA Convert"}, [{"op": "set", "node": "Coords", "settings": {"object": None}}]),
        ("SOCKET_TYPE", {"material": "QA Wood"}, [{"op": "set", "node": "Noise", "outputs": {"Fac": 1}}]),
        ("SOCKET_TYPE", {"material": "QA Wood"}, [{"op": "set", "node": "Material Output", "inputs": {"Surface": 1}}]),
        ("NOT_AN_IMAGE_NODE", {"material": "QA Wood"}, [{"op": "set", "node": "Noise", "image": image}]),
        ("LINK_NOT_FOUND", {"material": "QA Wood"}, [{"op": "unlink", "to_node": "Noise", "to_socket": "Scale"}]),
        ("LINK_NOT_FOUND", {"material": "QA Wood"}, [{"op": "insert", "node": "Noise", "from_node": "Ramp",
                                                      "from_socket": "Color", "to_node": "Noise", "to_socket": "Scale"}]),
        ("NOT_A_FRAME", {"material": "QA Wood"}, [{"op": "parent", "nodes": ["Ramp"], "frame": "Noise"}]),
        ("PARENT", {"material": "QA Wood"}, [{"op": "add", "node": "Inner", "type": "NodeFrame", "parent": "Frame"},
                                             {"op": "parent", "nodes": ["Frame"], "frame": "Inner"}]),
        ("NOT_INSERTABLE", {"material": "QA Wood"}, [{"op": "insert", "node": "Frame", "from_node": "Noise",
                                                      "from_socket": "Fac", "to_node": "Ramp", "to_socket": "Fac"}]),
        ("NOT_SWAPPABLE", {"material": "QA Wood"}, [{"op": "swap", "node": "Frame", "type": "ShaderNodeMath"}]),
        ("NODE_TYPE_DENIED", {"material": "QA Wood"}, [{"op": "add", "node": "G", "type": "ShaderNodeGroup"}]),
        ("NOT_COPYABLE", {"material": "QA Wood"}, [{"op": "duplicate", "node": "Material Output", "as": "Out 2"}]),
        ("NOT_GROUPABLE", {"material": "QA Wood"}, [{"op": "group", "nodes": ["Frame"], "node": "G", "name": "QA G"}]),
        ("NAME_TAKEN", {"material": "QA Wood"}, [{"op": "group", "nodes": ["Ramp"], "node": "G",
                                                  "name": "QA Convert Group"}]),
        ("GROUP_NOT_FOUND", {"material": "QA Wood", "group": "Noise"}, [{"op": "remove", "node": "Anything"}]),
        ("NAME_TAKEN", {"material": shell, "group": "Shell"}, [{"op": "interface", "action": "ADD", "name": "Wear",
                                                                "in_out": "INPUT", "socket_type": "FLOAT"}]),
        ("SOCKET_NOT_FOUND", {"material": shell, "group": "Shell"}, [{"op": "interface", "action": "REMOVE",
                                                                      "name": "Nope", "in_out": "INPUT"}]),
        ("INTERFACE", {"material": shell, "group": "Shell"}, [{"op": "interface", "action": "ADD", "name": "Coat",
                                                               "in_out": "INPUT", "socket_type": "SHADER", "default": 1}]),
        ("MODIFIER_NOT_FOUND", {"modifier": "QA Nothing"}, [{"op": "remove", "node": "x"}]),
    ]
    for index, (code, tree, steps) in enumerate(material_cases):
        check("step_refused_%02d_%s" % (index, code), refused(
            lambda tree=tree, steps=steps: program([edit(obj("Cube"), steps, **tree)], writes=[obj("Cube")]), code))
    many = [{"op": "add", "node": "n%d" % index, "type": "ShaderNodeMath"} for index in range(15)]
    check("geometry_contract_size_refused", refused(lambda: program([edit(obj("QA Ball"), many, modifier="QA Scatter")],
                                                                    writes=[obj("QA Ball")]), "GN_CONTRACT"))
    check("geometry_nested_refused", refused(lambda: program([edit(obj("QA Ball"), [{"op": "remove", "node": "lift"}],
                                                                   modifier="QA Scatter", group="scatter")],
                                                             writes=[obj("QA Ball")]), "GN_CONTRACT"))
    check("geometry_group_step_refused", refused(lambda: program([edit(obj("QA Ball"), [
        {"op": "group", "nodes": ["lift"], "node": "grouped", "name": "QA GN Group"}], modifier="QA Scatter")],
        writes=[obj("QA Ball")]), "GN_CONTRACT"))
    owner_ids = set(native._main_ids())
    owner_objects = {item.name: native._fingerprint(item) for item in window.scene.objects}
    original_run, original_bind = run, native.BlenderProgramBackend.bind
    owner_result, owner_calls = {}, {"bind": 0}

    def capture_owner_run(arguments):
        job, result = original_run(arguments)
        owner_result.update(job.result or result)
        return job, result

    def capture_owner_bind(backend, arguments):
        owner_calls["bind"] += 1
        return original_bind(backend, arguments)

    with patch.object(sys.modules[__name__], "run", capture_owner_run), \
            patch.object(native.BlenderProgramBackend, "bind", capture_owner_bind), \
            patch.object(native.BlenderProgramBackend, "create_stage", side_effect=AssertionError("scene staged as object")) as staged, \
            patch.object(native.BlenderProgramBackend, "commit", side_effect=AssertionError("scene committed as object")) as committed:
        check("material_needs_object_target", refused(lambda: program([edit(scene_binding()["handle"], [
            {"op": "remove", "node": "Noise"}])], scene=True), "OWNER_TYPE_MISMATCH"))
    report["owner_type_result"] = owner_result
    report["owner_type_dispatch"] = dict(owner_calls, create_stage=staged.call_count, commit=committed.call_count)
    check("owner_type_refusal_precedes_stage_and_preserves_artist_state", owner_result.get("status") == "failed"
          and not owner_result.get("scene_changed") and not owner_result.get("partial_changes_possible")
          and owner_calls["bind"] == 1 and not staged.called and not committed.called
          and set(native._main_ids()) == owner_ids
          and {item.name: native._fingerprint(item) for item in window.scene.objects} == owner_objects
          and native.WRITER.owner is None and not native.PREVIEWS.records and no_stages())
    check("world_nested_refused", refused(lambda: program([edit(scene_binding()["handle"], [
        {"op": "remove", "node": "x"}], world=True, group="Gain")], scene=True), "WORLD_NESTED_GROUP"))
    world = window.scene.world
    window.scene.world = None
    check("missing_world_refused", refused(lambda: program([edit(scene_binding()["handle"], [
        {"op": "remove", "node": "x"}], world=True)], scene=True), "NO_WORLD"))
    window.scene.world = world
    obj("Cube").data.materials.pop()
    queries.invalidate()


def rollback():
    """A commit failing after the transfer restores the live tree and its users."""
    queries.invalidate()
    cube = obj("Cube")
    live = cube.material_slots[0].material
    before = native._fingerprint(cube)
    queries.invalidate(window)
    job, result = run(program([edit(cube, [{"op": "remove", "node": "Frame"}])], writes=[cube]))
    with patch.object(program_commit, "_adopt_names", side_effect=RuntimeError("injected after the transfer")):
        outcome = job.commit()
    report["rollback_result"] = outcome
    report["rollback_state"] = {"same_material": obj("Cube").material_slots[0].material == live,
                                "original_node_present": "Frame" in live.node_tree.nodes,
                                "same_fingerprint": native._fingerprint(obj("Cube")) == before,
                                "no_stages": no_stages()}
    check("failed_commit_rolls_back_the_tree", result.get("ready_to_commit") and not outcome["success"]
          and not outcome["partial_changes_possible"] and obj("Cube").material_slots[0].material == live
          and "Frame" in live.node_tree.nodes and native._fingerprint(obj("Cube")) == before and no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    setup()
    material_edits()
    preview_and_stale()
    shared_trees()
    structure()
    geometry_nodes()
    world()
    refusals()
    two_slots()
    step_refusals()
    rollback()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "node-edit.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_NODE_EDIT", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
