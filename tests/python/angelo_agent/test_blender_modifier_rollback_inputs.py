# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A removed modifier comes back exactly when its commit is undone, every store it owns included (modifier_state).

Found by v210-features (2026-11-15): Blender 5.3 keeps Geometry Nodes inputs in ``modifier.properties.inputs``, not in
ID properties, and the rollback read ID properties, got a TypeError, took it for "no values" and re-created the
modifier with DEFAULT inputs. A staged program cannot target such a modifier today (only Angelo's own catalog graphs,
which take no value inputs, give an exact revision), so the Geometry Nodes case drives the commit's own stack sync and
rollback (``program_commit._sync_stack`` / ``_restore_stacks``) on real modifiers; the same rollback also dropped
stores a program does reach, which a real staged program shows:

* a Geometry Nodes modifier whose float, int, object, material, vector, boolean, string and field inputs (one set to
  an attribute) and an output attribute name are not defaults is removed by the stack sync, rolled back, and every
  value is back;
* through a staged program and a forced failure after the stack edit: UV Project's projectors and a Vertex Weight
  Edit falloff curve come back as they were;
* every mesh modifier type, freshly added and with its owned structs edited (curve mapping points, a custom profile,
  projectors), is captured and restored exactly, or refused with a reason (never silently partial);
* a modifier holding data no rollback can write back (a Corrective Smooth bind) is refused at staging, before
  anything is removed;
* the object's revision reads the same owned stores the rollback restores (curve points, projectors), so a change
  to one makes a bound program stale (the coordinator, 2026-11-15: revision and rollback can never disagree).

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec --python-exit-code 1``. No
provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-modifier-rollback-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import native_programs as native, program_commit, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report["details"][name] = detail


def handle(obj):
    return queries.object_handle(obj)


def program(operations, writes=(), reads=()):
    def bindings(objects):
        return [{"handle": handle(obj), "revision": queries.object_revision(window, obj)} for obj in objects]
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": bindings(reads),
                                                  "write_set": bindings(writes), "operations": operations})


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return job, result
    raise AssertionError("cooperative native program did not settle")


def node_group():
    """Inputs of every kind the bug lost: float, int, object, material, a field (as a value and as an attribute),
    a vector, a boolean, a string; and a float output stored as an attribute."""
    tree = bpy.data.node_groups.new("QA Rollback Inputs", "GeometryNodeTree")
    sockets = tree.interface
    sockets.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    for name, kind in (("Amount", "NodeSocketFloat"), ("Count", "NodeSocketInt"), ("Target", "NodeSocketObject"),
                       ("Finish", "NodeSocketMaterial"), ("Offset", "NodeSocketVector"), ("Flip", "NodeSocketBool"),
                       ("Label", "NodeSocketString"), ("Weight", "NodeSocketFloat")):
        sockets.new_socket(name, in_out="INPUT", socket_type=kind)
    sockets.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sockets.new_socket("Measure", in_out="OUTPUT", socket_type="NodeSocketFloat")
    inputs, outputs = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    tree.links.new(inputs.outputs["Geometry"], outputs.inputs["Geometry"])
    tree.links.new(inputs.outputs["Weight"], outputs.inputs["Measure"])   # A field input passed to a field output.
    return tree


def socket(modifier, name, outputs=False):
    tree = modifier.node_group
    item = next(item for item in tree.interface.items_tree if item.item_type == "SOCKET" and item.name == name
                and item.in_out == ("OUTPUT" if outputs else "INPUT"))
    return getattr(modifier.properties.outputs if outputs else modifier.properties.inputs, item.identifier)


def inputs_state(modifier):
    state = {}
    for name in ("Amount", "Count", "Target", "Finish", "Offset", "Flip", "Label", "Weight"):
        store = socket(modifier, name)
        state[name] = {key: (tuple(getattr(store, key)) if key == "value" and name == "Offset" else getattr(store, key))
                       for key in ("value", "type", "attribute_name") if hasattr(store, key)}
    state["Measure"] = socket(modifier, "Measure", outputs=True).attribute_name
    return state


def geometry_nodes_rollback():
    cube = window.scene.objects["Cube"]
    target = bpy.data.objects.new("QA Target", None)
    window.scene.collection.objects.link(target)
    material = bpy.data.materials.new("QA Finish")
    modifier = cube.modifiers.new("QA Nodes", "NODES")
    modifier.node_group = node_group()
    socket(modifier, "Amount").value = 2.75
    socket(modifier, "Count").value = 7
    socket(modifier, "Target").value = target
    socket(modifier, "Finish").value = material
    socket(modifier, "Offset").value = (0.5, -1.25, 3.0)
    socket(modifier, "Flip").value = True
    socket(modifier, "Label").value = "kept"
    socket(modifier, "Weight").type = "ATTRIBUTE"
    socket(modifier, "Weight").attribute_name = "qa_weight"
    socket(modifier, "Measure", outputs=True).attribute_name = "qa_measure"
    cube.modifiers.new("QA After", "TRIANGULATE")
    queries.invalidate()
    before = inputs_state(modifier)
    stack = [(item.name, item.type) for item in cube.modifiers]
    staged = cube.copy()   # The stage: the same stack without the node modifier.
    staged.modifiers.remove(staged.modifiers["QA Nodes"])
    journal = []
    program_commit._sync_stack(cube, staged, journal)
    check("node_modifier_removed_by_the_sync", cube.modifiers.get("QA Nodes") is None)
    program_commit._restore_stacks(journal)
    restored = cube.modifiers.get("QA Nodes")
    check("node_modifier_back_in_place", [(item.name, item.type) for item in cube.modifiers] == stack)
    after = inputs_state(restored) if restored is not None else None
    check("every_input_value_back", after == before, {"before": repr(before), "after": repr(after)})
    bpy.data.objects.remove(staged)
    for name in ("QA Nodes", "QA After"):
        if cube.modifiers.get(name) is not None:
            cube.modifiers.remove(cube.modifiers[name])
    queries.invalidate()


def curve_points(mapping):
    return [tuple(round(value, 5) for value in point.location) for point in mapping.curves[0].points]


def program_rollback():
    """Stores a staged program reaches: projectors and a falloff curve, removed, the commit failing after the edit."""
    cube = window.scene.objects["Cube"]
    projectors = [bpy.data.objects.new("QA Projector %d" % index, None) for index in range(2)]
    for item in projectors:
        window.scene.collection.objects.link(item)
    project = cube.modifiers.new("QA Project", "UV_PROJECT")
    project.projector_count = 2
    for index, item in enumerate(projectors):
        project.projectors[index].object = item
    cube.vertex_groups.new(name="QA Group")
    weights = cube.modifiers.new("QA Weights", "VERTEX_WEIGHT_EDIT")
    weights.vertex_group = "QA Group"
    weights.falloff_type = "CURVE"
    weights.map_curve.curves[0].points.new(0.35, 0.9)
    weights.map_curve.update()
    queries.invalidate()
    before = {"projectors": [item.object for item in project.projectors], "curve": curve_points(weights.map_curve)}
    stack = [(item.name, item.type) for item in cube.modifiers]
    real = program_commit._sync_stack

    def fail_after_sync(original, staged, journal):
        real(original, staged, journal)
        raise RuntimeError("injected failure after the stack edit")

    try:
        arguments = program([{"kind": "modifier_remove", "target": handle(cube), "parameters": {"modifier": name}}
                             for name in ("QA Project", "QA Weights")], writes=[cube], reads=projectors)
    except Exception as error:   # noqa: BLE001 - reported: the program path must reach these stores.
        check("program_reaches_projectors_and_curve", False, str(error))
        return
    job, staged = run(arguments)
    check("program_reaches_projectors_and_curve", staged.get("ready_to_commit") is True, staged.get("error"))
    with patch.object(program_commit, "_sync_stack", fail_after_sync):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["details"]["program_rollback_result"] = {key: job.result.get(key) for key in (
        "status", "error_code", "rollback_verified", "rollback_error")}
    check("program_rollback_verified", job.result.get("status") == "failed"
          and job.result.get("rollback_verified") is True, job.result.get("rollback_error"))
    project, weights = cube.modifiers.get("QA Project"), cube.modifiers.get("QA Weights")
    after = {"projectors": [item.object for item in project.projectors] if project else None,
             "curve": curve_points(weights.map_curve) if weights else None}
    check("projectors_and_curve_back", [(item.name, item.type) for item in cube.modifiers] == stack and after == before,
          {"before": repr(before), "after": repr(after)})
    check("no_stage_residue", no_stages())
    for name in ("QA Project", "QA Weights"):
        if cube.modifiers.get(name) is not None:
            cube.modifiers.remove(cube.modifiers[name])
    queries.invalidate()


def every_type_round_trip():
    """Each mesh modifier type, its owned structs edited, captured from one modifier and restored into a fresh one."""
    from angelo_agent import modifier_state
    mesh = bpy.data.meshes.new("QA Round Trip")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)], [], [(0, 1, 3, 2)])
    obj = bpy.data.objects.new("QA Round Trip", mesh)
    window.scene.collection.objects.link(obj)
    projector = bpy.data.objects.new("QA Projector", None)
    window.scene.collection.objects.link(projector)
    outcomes = {}
    for kind in [item.identifier for item in bpy.types.Modifier.bl_rna.properties["type"].enum_items]:
        try:
            source = obj.modifiers.new("QA " + kind, kind)
        except (RuntimeError, TypeError):
            source = None
        if source is None:
            continue   # Not a mesh modifier.
        if kind == "VERTEX_WEIGHT_EDIT":
            source.map_curve.curves[0].points.new(0.4, 0.8)
            source.map_curve.update()
        elif kind == "BEVEL":
            source.profile_type = "CUSTOM"
            source.custom_profile.points.add(0.3, 0.6)
            source.custom_profile.update()
        elif kind == "UV_PROJECT":
            source.projector_count = 3
            source.projectors[2].object = projector
        try:
            state = modifier_state.capture(source)
        except modifier_state.Unrestorable as error:
            outcomes[kind] = "refused: " + str(error)
            obj.modifiers.remove(source)
            continue
        obj.modifiers.remove(source)
        fresh = obj.modifiers.new("QA " + kind, kind)
        try:
            modifier_state.restore(fresh, state, {})
            outcomes[kind] = "restored"
        except Exception as error:   # noqa: BLE001 - every type's outcome is reported.
            outcomes[kind] = "FAILED: %s" % error
        obj.modifiers.remove(fresh)
    report["details"]["round_trip"] = outcomes
    check("every_type_restores_or_refuses_with_a_reason",
          bool(outcomes) and not any(value.startswith("FAILED") for value in outcomes.values()))
    check("edited_structs_restore", all(outcomes.get(kind) == "restored"
                                        for kind in ("VERTEX_WEIGHT_EDIT", "BEVEL", "UV_PROJECT", "NODES", "HOOK")))
    check("particle_system_refused", outcomes.get("PARTICLE_SYSTEM", "").startswith("refused"))
    bpy.data.objects.remove(obj)


def data_no_rollback_restores_is_refused():
    """A Corrective Smooth bind is data RNA cannot write back: staging refuses the removal, and nothing changes."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(4, 0, 0))
    block = bpy.context.object
    block.name = "QA Bound"
    smooth = block.modifiers.new("QA Smooth", "CORRECTIVE_SMOOTH")
    smooth.rest_source = "BIND"
    with bpy.context.temp_override(object=block, active_object=block):
        bpy.ops.object.correctivesmooth_bind(modifier=smooth.name)
    check("corrective_smooth_bound", block.modifiers["QA Smooth"].is_bind)
    queries.invalidate()
    try:
        from angelo_agent import modifier_state
    except ImportError as error:
        check("bind_named_in_the_refusal", False, str(error))
    else:
        why = modifier_state.refusal(block.modifiers["QA Smooth"])
        check("bind_named_in_the_refusal", why is not None and "is_bind" in why, why)
    try:
        arguments = program([{"kind": "modifier_remove", "target": handle(block),
                              "parameters": {"modifier": "QA Smooth"}}], writes=[block])
    except Exception as error:   # noqa: BLE001 - a refusal at validation is a refusal too.
        refused, detail = True, str(error)
    else:
        job, result = run(arguments)
        refused, detail = result.get("status") == "failed", result.get("error")
    check("bound_removal_refused_before_anything_changes",
          refused and "is_bind" in str(detail) and block.modifiers.get("QA Smooth") is not None and no_stages(), detail)
    bpy.data.objects.remove(block)
    queries.invalidate()


def revision_reads_owned_stores():
    """What the rollback restores, the revision reads: an edit to a store a modifier owns (a falloff curve's points,
    a projector) between an observation and its commit changes the object's revision, so a program bound to the old
    one is stale, never committed over the change and then rolled back to a state nobody observed."""
    mesh = bpy.data.meshes.new("QA Revision")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("QA Revision", mesh)
    window.scene.collection.objects.link(obj)
    obj.vertex_groups.new(name="QA Group")
    projector = bpy.data.objects.new("QA Revision Projector", None)
    window.scene.collection.objects.link(projector)
    weights = obj.modifiers.new("QA Weights", "VERTEX_WEIGHT_EDIT")
    weights.vertex_group, weights.falloff_type = "QA Group", "CURVE"
    hook = obj.modifiers.new("QA Hook", "HOOK")
    hook.falloff_type = "CURVE"
    project = obj.modifiers.new("QA Project", "UV_PROJECT")
    project.projector_count = 2
    edits = {
        "falloff_curve_point": lambda: (weights.map_curve.curves[0].points.new(0.4, 0.7), weights.map_curve.update()),
        "hook_curve_point": lambda: (hook.falloff_curve.curves[0].points.new(0.6, 0.2), hook.falloff_curve.update()),
        "projector_object": lambda: setattr(project.projectors[1], "object", projector),
    }
    changed = {}
    for name, edit in edits.items():
        queries.invalidate()
        before = queries.object_revision(window, obj)
        edit()
        queries.invalidate()
        changed[name] = queries.object_revision(window, obj) != before
    check("revision_changes_with_every_owned_store", all(changed.values()), changed)
    bpy.data.objects.remove(obj)
    bpy.data.objects.remove(projector)
    queries.invalidate()


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    for step in (geometry_nodes_rollback, program_rollback, every_type_round_trip,
                 data_no_rollback_restores_is_refused, revision_reads_owned_stores):
        try:
            step()
        except Exception:   # noqa: BLE001 - one step's failure is reported; the others still run.
            report["checks"][step.__name__] = False
            report["details"][step.__name__ + "_error"] = traceback.format_exc()[-3000:]
            native.cleanup_all()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "modifier-rollback-inputs.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_MODIFIER_ROLLBACK_INPUTS", json.dumps({key: report.get(key) for key in ("success", "checks")}), flush=True)
assert report.get("success"), report.get("error") or [name for name, ok in report["checks"].items() if not ok]
