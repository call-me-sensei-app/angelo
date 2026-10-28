# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mirror origins through real stages, commits, previews, Undo and request Stop.

Run only as an isolated factory-startup GUI native suite. No provider is
constructed or called. Pointer identity and evaluated vertices are separate
oracles; the receipt records every refusal and partial-verification outcome.
"""

import copy
import os
import sys
import tempfile
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Matrix

assert not bpy.app.background and "--factory-startup" in sys.argv
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/")
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-mirror-object-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.identifiers import IdentifierTable  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import args_for, commit_operator, no_stages, spent_budget  # noqa: E402
import rendered_view_fixtures as rv  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402

state = {"phase": "boot", "success": False, "checks": {}, "cases": {}, "paid_requests": 0,
         "provider_started": False, "binary": bpy.app.binary_path}
window = bpy.context.window_manager.windows[0]
MODIFIER = "Fixture Mirror"
SCALARS = {"axes": [True, False, False], "use_clip": False,
           "use_merge_vertices": False, "merge_threshold": .002}
CommitOperator = commit_operator(state)
bpy.utils.register_class(CommitOperator)
native.register()


def check(name, condition):
    state["checks"][state["phase"] + "/" + name] = bool(condition)
    assert condition, state["phase"] + "/" + name


def clean(before_ids=None):
    check("private_owners_released", no_stages() and not native.runtime_is_staging()
          and native.WRITER.owner is None and not native.PREVIEWS.records and not native.has_pending_cleanup())
    if before_ids is not None:
        check("no_temporary_datablock_leaks", rv.id_names() == before_ids)


def handle(obj):
    return queries.object_handle(obj)


def objects(label, kind="EMPTY"):
    """Each case owns new, uniquely named live inputs; no unrelated scene is opened."""
    state["phase"] = label
    mesh = bpy.data.meshes.new(label + " Mesh")
    mesh.from_pydata([(.4, -.2, .1), (1.7, .2, .3), (.8, 1.2, -.2)], [], [(0, 1, 2)])
    owner = bpy.data.objects.new(label + " Owner", mesh)
    data = None
    if kind == "MESH":
        data = mesh.copy()
    elif kind == "CAMERA":
        data = bpy.data.cameras.new(label + " Camera")
    origin = bpy.data.objects.new(label + " Origin", data)
    for obj in (owner, origin):
        window.scene.collection.objects.link(obj)
    owner.location, owner.rotation_euler, owner.scale = (.2, -.3, .4), (.1, -.2, .3), (1.2, .8, 1.1)
    origin.location, origin.rotation_euler = (1.1, .6, -.4), (.2, -.1, .6)
    window.view_layer.update()
    queries.invalidate()
    state["cases"][label] = {}
    return owner, origin


def inherited(owner, origin):
    modifier = owner.modifiers.new(MODIFIER, "MIRROR")
    modifier.mirror_object = origin
    modifier.use_axis, modifier.use_clip, modifier.use_mirror_merge = SCALARS["axes"], False, False
    modifier.merge_threshold = SCALARS["merge_threshold"]
    window.view_layer.update()
    queries.invalidate()


def operation(owner, settings=None, *, add=False):
    return {"kind": "modifier_add" if add else "modifier_configure", "target": handle(owner),
            "parameters": {"type": "MIRROR", "name" if add else "modifier": MODIFIER,
                           "settings": dict(SCALARS, **(settings or {}))}}


def program(owner, origin, operations, *, mode="APPLY", writes=None, reads=None):
    values = args_for(window, operations, writes=[owner] if writes is None else writes,
                      reads=[origin] if reads is None else reads, mode=mode)
    return validate_call("angelo_scene_program", values)


def expected_vertices(owner, origin):
    """Reflect world positions in the chosen origin's local X plane, independently of the modifier."""
    basis = origin.matrix_world if origin is not None else owner.matrix_world
    reflect = basis @ Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)) @ basis.inverted()
    source = [owner.matrix_world @ vertex.co for vertex in owner.data.vertices]
    return sorted(tuple(float(v) for v in point) for point in source + [reflect @ point for point in source])


def evaluated_vertices(obj, layer):
    layer.update()
    evaluated = obj.evaluated_get(layer.depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return sorted(tuple(float(v) for v in evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def same_vertices(actual, expected):
    return len(actual) == len(expected) and all(abs(a - b) < 2e-5 for row, want in zip(actual, expected)
                                               for a, b in zip(row, want))


def stage_observer(records):
    original = native.BlenderProgramBackend.apply

    def applied(backend, stage, op):
        result = original(backend, stage, op)
        if op["kind"] in {"modifier_add", "modifier_configure"} and op["parameters"].get("type") == "MIRROR":
            obj = stage.context.resolve(op["target"])
            modifier = obj.modifiers.get(MODIFIER)
            reference = op["parameters"]["settings"].get("mirror_object")
            expected = stage.context.resolve(reference) if reference else None
            actual = modifier.mirror_object
            records.append({"owner_private": obj != stage.originals[op["target"]],
                            "set_exact": actual == expected if reference else None,
                            "clear_exact": actual is None if op["parameters"]["settings"].get("clear") else None,
                            "pointer": actual.as_pointer() if actual else None,
                            "origin_private": actual not in stage.originals.values() if actual else True,
                            "geometry": same_vertices(evaluated_vertices(obj, stage.view_layer), expected_vertices(obj, actual))})
        return result
    return patch.object(native.BlenderProgramBackend, "apply", applied)


def settle(job):
    for _ in range(256):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
        yield
    raise AssertionError("Mirror fixture exceeded 256 cooperative polls")


def prepare(values, *, controller=None, observe=True):
    approved = native.precondition(window, "angelo_scene_program", values, controller=controller)
    job = native.begin(window, "angelo_scene_program", values, controller=controller,
                       approved_obligations=approved.obligations)
    records = []
    with stage_observer(records) if observe else nullcontext():
        result = yield from settle(job)
    state["cases"][state["phase"]].setdefault("stages", []).append(
        {"result": copy.deepcopy(result), "staged_pointers": records})
    if records:
        check("private_stage_identity_and_geometry", all(r["owner_private"] and r["origin_private"] and r["geometry"]
              and r["set_exact"] is not False and r["clear_exact"] is not False for r in records))
    return job, result


def pointer_obligation(job, expected):
    checks = job.obligations.checks()
    return any(row.get("kind") == "modifier_values" and row.get("type") == "MIRROR"
               and row.get("name") == MODIFIER and row.get("values") == {"mirror_object": expected} for row in checks)


def commit(job):
    check("ready_before_commit", job.ready_to_commit)
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    state["cases"][state["phase"]].setdefault("commits", []).append(copy.deepcopy(job.result))
    check("committed", job.result.get("success") and job.result.get("status") == "committed")
    clean()
    return job.result


def pointer_and_geometry(owner_name, origin_name):
    owner = window.scene.objects[owner_name]
    origin = window.scene.objects[origin_name] if origin_name else None
    check("live_pointer_exact", owner.modifiers[MODIFIER].mirror_object == origin)
    check("live_reflection_geometry", same_vertices(evaluated_vertices(owner, window.view_layer), expected_vertices(owner, origin)))


def set_clear_undo():
    for kind in ("EMPTY", "MESH", "CAMERA"):
        owner, origin = objects("set_" + kind.lower(), kind)
        names = owner.name, origin.name
        before = native._fingerprint(owner)
        values = program(owner, origin, [operation(owner, {"mirror_object": handle(origin)}, add=True)])
        bpy.ops.ed.undo_push(message="Mirror set baseline")
        job, result = yield from prepare(values)
        check("set_obligation", pointer_obligation(job, {"object": handle(origin)}))
        check("staging_left_live_unchanged", native._fingerprint(owner) == before)
        commit(job)
        pointer_and_geometry(*names)
        bpy.ops.ed.undo()
        yield
        queries.invalidate()
        check("one_undo_removes_set", window.scene.objects[names[0]].modifiers.get(MODIFIER) is None)
        bpy.ops.ed.redo()
        yield
        queries.invalidate()
        pointer_and_geometry(*names)
        owner, origin = (window.scene.objects[name] for name in names)
        for preserve in ({}, {"mirror_object": None}, {"clear": False}):
            job, _ = yield from prepare(program(owner, origin, [operation(owner, preserve)]))
            commit(job)
            pointer_and_geometry(*names)
        owner, origin = (window.scene.objects[name] for name in names)
        bpy.ops.ed.undo_push(message="Mirror clear baseline")
        job, _ = yield from prepare(program(owner, origin, [operation(owner, {"clear": True})]))
        check("clear_obligation", pointer_obligation(job, None))
        commit(job)
        pointer_and_geometry(names[0], None)
        bpy.ops.ed.undo()
        yield
        queries.invalidate()
        pointer_and_geometry(*names)
        bpy.ops.ed.redo()
        yield
        queries.invalidate()
        pointer_and_geometry(names[0], None)
        clean()


def alias_and_second_edit():
    owner, origin = objects("earlier_alias")
    alias_name = "Mirror earlier alias"
    values = program(owner, origin, [
        {"kind": "create_primitive", "target": None,
         "parameters": {"primitive": "CUBE", "name": alias_name, "alias": "mirror_origin"}},
        operation(owner, {"mirror_object": "$mirror_origin"}, add=True),
        operation(owner, {"mirror_object": None})], reads=[])
    job, _ = yield from prepare(values)
    check("set_obligation_survives_scalar_configure", pointer_obligation(job, {"object": "$mirror_origin"}))
    commit(job)
    pointer_and_geometry(owner.name, alias_name)
    values = program(owner, origin, [operation(owner, {"mirror_object": handle(origin)})],
                     reads=[origin, window.scene.objects[alias_name]])
    job, _ = yield from prepare(values)
    commit(job)
    pointer_and_geometry(owner.name, origin.name)


def refusal_cases():
    owner, origin = objects("conflict_and_self")
    before = native._fingerprint(owner)
    for name, settings, reason in (
            ("conflict", {"mirror_object": handle(origin), "clear": True}, "clear and mirror_object cannot be set together"),
            ("typed_self", {"mirror_object": handle(owner)}, "mirror_object cannot be the modified object itself")):
        try:
            program(owner, origin, [operation(owner, settings, add=True)])
        except (ValueError, RuntimeError) as error:
            state["cases"][state["phase"]][name] = str(error)
            check(name + "_intended_refusal", reason in str(error))
        else:
            raise AssertionError(name + " was not refused before staging")
        check(name + "_no_live_write", native._fingerprint(owner) == before)
    modifier = owner.modifiers.new(MODIFIER, "MIRROR")
    try:
        modifier.mirror_object = owner
    except (TypeError, ValueError, RuntimeError) as error:
        state["cases"][state["phase"]]["native_self"] = str(error)
    check("native_self_never_retained", modifier.mirror_object != owner)
    owner.modifiers.remove(modifier)
    values = program(owner, origin, [operation(owner, {"mirror_object": handle(origin)}, add=True)])
    original = native.BlenderProgramBackend.apply

    def resolved_self(backend, stage, op):
        resolve = stage.context.resolve
        with patch.object(stage.context, "resolve", lambda ref: resolve(handle(owner)) if ref == handle(origin) else resolve(ref)):
            return original(backend, stage, op)
    with patch.object(native.BlenderProgramBackend, "apply", resolved_self):
        job, result = yield from prepare(values, observe=False)
    check("resolved_identity_self_refused", result.get("success") is False
          and "mirror_object resolves to the modified object itself" in result.get("error", "")
          and native._fingerprint(owner) == before)
    clean()


def stale_and_rollback():
    for changed in ("owner", "origin", "pointer"):
        owner, origin = objects("stale_" + changed)
        inherited(owner, origin)
        values = program(owner, origin, [operation(owner, {"mirror_object": handle(origin)})])
        job, _ = yield from prepare(values)
        if changed == "owner":
            owner.data.vertices[0].co.x += .3
        elif changed == "origin":
            origin.location.x += .3
        else:
            old_name = origin.name
            origin.name += " previous"
            replacement = bpy.data.objects.new(old_name, None)
            window.scene.collection.objects.link(replacement)
            owner.modifiers[MODIFIER].mirror_object = replacement
        window.view_layer.update()
        before = native._fingerprint(owner)
        result = job.commit()
        state["cases"][state["phase"]]["refusal"] = result
        check("stale_refused_without_overwriting_live", result.get("success") is False
              and "exact target or a mutable dependency changed" in result.get("error", "")
              and native._fingerprint(owner) == before)
        clean()
    owner, origin = objects("rollback_after_pointer")
    inherited(owner, origin)
    before = native._fingerprint(owner)
    before_ids = rv.id_names()
    job, _ = yield from prepare(program(owner, origin, [operation(owner, {"clear": True, "merge_threshold": .02})]))
    original, calls = native._set_settings, []

    def fail_once(target, settings, mapping):
        original(target, settings, mapping)
        calls.append({"target": target.name, "cleared": target.mirror_object is None,
                      "threshold": target.merge_threshold})
        if len(calls) == 1:
            raise RuntimeError("Fixture failure after Mirror pointer/scalar transfer")
    with patch.object(native, "_set_settings", fail_once):
        result = job.commit()
    state["cases"][state["phase"]]["failure"] = result
    check("fault_after_actual_pointer_and_scalar_transfer", calls and calls[0]["cleared"]
          and abs(calls[0]["threshold"] - .02) < 1e-6)
    check("pointer_scalars_rolled_back", result.get("success") is False
          and not result.get("partial_changes_possible") and native._fingerprint(owner) == before
          and owner.modifiers[MODIFIER].mirror_object == origin)
    clean(before_ids)


def previews_and_stop():
    owner, origin = objects("preview_cancel")
    before = native._fingerprint(owner)
    before_ids = rv.id_names()
    job, result = yield from prepare(program(owner, origin, [operation(owner, {"mirror_object": handle(origin)}, add=True)], mode="PREVIEW"))
    check("retained_preview_not_live", result.get("status") == "preview" and native._fingerprint(owner) == before)
    discarded = native.begin(window, "angelo_discard_preview", {"preview_id": result["preview_id"]})
    check("discard_keeps_live", discarded.get("success") and native._fingerprint(owner) == before)
    clean(before_ids)
    owner, origin = objects("preview_stale_origin")
    job, result = yield from prepare(program(owner, origin, [operation(owner, {"mirror_object": handle(origin)}, add=True)], mode="PREVIEW"))
    origin.location.y += .4
    window.view_layer.update()
    before = native._fingerprint(owner)
    try:
        native.precondition(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    except (ValueError, RuntimeError) as error:
        state["cases"][state["phase"]]["refused"] = str(error)
        check("stale_accept_intended_refusal", "exact target or a mutable dependency changed" in str(error))
    else:
        raise AssertionError("Accept used a stale Mirror origin")
    check("stale_accept_no_live_write", native._fingerprint(owner) == before)
    clean()
    owner, origin = objects("controller_stop")
    before = native._fingerprint(owner)
    before_ids = rv.id_names()
    controller = Controller(OUT / "stop-controller", session_id=str(uuid.uuid4()))
    controller.set_document((queries.document_epoch(), window.scene.session_uid))
    controller._begin_metrics()
    controller.busy = True
    executor, responses = BlenderExecutor(window), []
    values = program(owner, origin, [operation(owner, {"mirror_object": handle(origin)}, add=True)])
    try:
        controller.request_tool("mirror-stop", "angelo_scene_program", values, responses.append)
        controller.pump_tools(executor, controller.document)
        call = next(iter(controller.pending.values()))
        controller.approve_tool(call, executor, controller.document)
        with spent_budget():
            for _ in range(128):
                controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=1))
                active = controller.native_jobs.get(call.id)
                if active is not None and active.ready_to_commit:
                    break
                yield
            check("actual_prepared_stage_before_stop", active is not None and active.ready_to_commit and active.stage is not None)
            check("staged_mirror_obligation_before_stop", pointer_obligation(active, {"object": handle(origin)}))
            controller.stop()
        stopped = controller.state["results"][call.id]["result"]
        state["cases"][state["phase"]].update(stopped_result=copy.deepcopy(stopped), callbacks=copy.deepcopy(responses))
        check("stop_releases_pending_job", not controller.pending and not controller.native_jobs
              and stopped.get("cancelled") and stopped.get("scene_changed") is False
              and not stopped.get("partial_changes_possible"))
        check("stop_callbacks_if_delivered_are_cancelled", all(row.get("cancelled") for row in responses))
        check("stop_never_writes_live", native._fingerprint(owner) == before and controller.provider is None)
        clean(before_ids)
    finally:
        controller.close()


def pointer_fault(records, owner_ref, expected_ref, substitute_ref):
    """Change only a private pointer at its real predicate boundary; delegate every check once."""
    original = native.BlenderProgramBackend.validate

    def validate(backend, stage, predicate):
        detail = None
        if predicate["kind"] == "modifier_values" and predicate.get("type") == "MIRROR":
            owner = stage.context.resolve(owner_ref)
            expected, substitute = (stage.context.resolve(ref) for ref in (expected_ref, substitute_ref))
            assert all(obj != stage.originals[ref] for obj, ref in
                       ((owner, owner_ref), (expected, expected_ref), (substitute, substitute_ref)))
            modifier = owner.modifiers[MODIFIER]
            assert modifier.mirror_object == expected and expected != substitute
            scalars = lambda: (tuple(modifier.use_axis), modifier.use_clip,
                               modifier.use_mirror_merge, modifier.merge_threshold)
            before = scalars()
            modifier.mirror_object = substitute
            assert modifier.mirror_object == substitute and scalars() == before
            detail = {"expected_pointer": expected.as_pointer(), "substitute_pointer": substitute.as_pointer(),
                      "actual_pointer": modifier.mirror_object.as_pointer(), "private_only": True,
                      "scalars_before": before, "scalars_after": scalars()}
        outcome = original(backend, stage, predicate)
        records.append({"predicate": copy.deepcopy(predicate), "outcome": copy.deepcopy(outcome), "fault": detail})
        return outcome
    return patch.object(native.BlenderProgramBackend, "validate", validate)


def pointer_check_refusals():
    for phase in ("stage", "precommit", "accept"):
        owner, origin = objects("pointer_check_" + phase)
        substitute = bpy.data.objects.new(state["phase"] + " Substitute", None)
        window.scene.collection.objects.link(substitute)
        window.view_layer.update()
        refs = tuple(handle(obj) for obj in (owner, origin, substitute))
        before = [native._fingerprint(obj) for obj in (owner, origin, substitute)]
        before_ids, records = rv.id_names(), []
        values = program(owner, origin, [operation(owner, {"mirror_object": refs[1]}, add=True)],
                         mode="PREVIEW" if phase == "accept" else "APPLY", reads=[origin, substitute])
        if phase == "stage":
            with pointer_fault(records, *refs):
                job, result = yield from prepare(values)
        else:
            job, result = yield from prepare(values)
            if phase == "accept":
                check("matching_preview_first_passed", result.get("status") == "preview")
                args = {"preview_id": result["preview_id"]}
                native.precondition(window, "angelo_accept_preview", args)
                job = native.begin(window, "angelo_accept_preview", args)
            else:
                check("matching_stage_first_passed", job.ready_to_commit)
            with pointer_fault(records, *refs):
                result = (yield from settle(job)) if phase == "accept" else job.commit()
        state["cases"][state["phase"]].update(pointer_fault_checks=records, refusal=copy.deepcopy(result))
        faults = [row for row in records if row["fault"] is not None]
        check("actual_pointer_obligation_present", pointer_obligation(job, {"object": refs[1]}))
        check("scalar_check_passed_before_pointer_fault", any(row["predicate"]["kind"] == "modifier"
              and row["outcome"].get("passed") is True for row in records))
        check("exact_pointer_checker_failed", len(faults) == 1 and faults[0]["outcome"].get("status") == "fail"
              and faults[0]["outcome"].get("passed") is False and any(row.get("kind") == "modifier_values"
              and row.get("observed", {}).get("mismatched") == ["mirror_object"]
              for row in faults[0]["outcome"].get("checks", [])))
        prefix = "Expected check changed before commit:" if phase == "precommit" else "Expected check failed:"
        check("intended_verification_refusal", result.get("success") is False
              and result.get("error", "").startswith(prefix) and not result.get("mutation_started"))
        check("all_live_inputs_unchanged", before == [native._fingerprint(obj) for obj in (owner, origin, substitute)])
        clean(before_ids)


def partial_programs():
    for mode in ("APPLY", "PREVIEW"):
        owner, origin = objects("mixed_partial_" + mode.lower())
        material = bpy.data.materials.new("Mirror Partial " + mode)
        if material.node_tree is None:
            material.use_nodes = True
        owner.data.materials.append(material)
        node = {"kind": "node_edit", "target": handle(owner), "parameters": {"material": material.name, "edits": [
            {"op": "add", "node": "Math", "type": "ShaderNodeMath", "inputs": {"Value": 1}},
            {"op": "add", "node": "Unaffected", "type": "ShaderNodeMath", "inputs": {"Value": 2}},
            {"op": "set", "node": "Math", "settings": {"operation": "MULTIPLY"}}]}}
        values = program(owner, origin, [operation(owner, {"mirror_object": handle(origin)}, add=True), node], mode=mode)
        job, result = yield from prepare(values)
        bundle = job.obligations
        check("pointer_plus_unknowns", pointer_obligation(job, {"object": handle(origin)}) and bool(bundle.unknowns()))
        if mode == "PREVIEW":
            check("partial_preview", result.get("verification", {}).get("status") == "partial")
            args = {"preview_id": result["preview_id"]}
            native.precondition(window, "angelo_accept_preview", args)
            job = native.begin(window, "angelo_accept_preview", args)
            check("accept_retains_exact_unknown_authority", job.obligations is bundle)
            yield from settle(job)
        result = commit(job)
        check("honest_partial", result.get("verification", {}).get("status") == "partial"
              and result["verification"]["unknowns"] == bundle.unknowns() and "check_summary" not in result)
        check("no_auto_certificate", certified_completion([{"tool": "angelo_scene_program", "arguments": values}],
              [result], read_only=lambda tool: False) is None)
        check("known_node_fact_retained", abs(owner.material_slots[0].material.node_tree.nodes["Unaffected"].inputs["Value"].default_value - 2) < 1e-6)
        pointer_and_geometry(owner.name, origin.name)


def frame_cases():
    for inferred in (False, True):
        owner, origin = objects("frame_" + ("inherited" if inferred else "explicit"))
        window.scene.frame_set(1)
        origin.location.x = 1
        origin.keyframe_insert("location", frame=1)
        origin.location.x = 3
        origin.keyframe_insert("location", frame=2)
        window.scene.frame_set(1)
        if inferred:
            inherited(owner, origin)
        table = IdentifierTable()
        table.short_handle(handle(origin))
        table.observe({"objects": [{"handle": handle(origin), "object_revision": queries.object_revision(window, origin)}]})
        controller = SimpleNamespace(_identifiers=table)
        values = program(owner, origin, [operation(owner, {} if inferred else {"mirror_object": handle(origin)}, add=not inferred)],
                         reads=[] if inferred else [origin])
        approved = native.precondition(window, "angelo_scene_program", values, controller=controller)
        check("origin_bound_at_old_frame", handle(origin) in {row[0] for row in approved.inputs})
        window.scene.frame_set(2)
        before = native._fingerprint(owner)
        try:
            result = native.rebind(window, "angelo_scene_program", values, approved, controller=controller)
        except (ValueError, RuntimeError) as error:
            result = {"refused": str(error)}
        state["cases"][state["phase"]]["rebind"] = result
        check("time_changed_origin_refused", isinstance(result, dict) and bool(result.get("refused")))
        reason = result["refused"]
        check("intended_frame_dependency_refusal", ("The current frame changed from 1 to 2" in reason
              and ("a declared object changed too" in reason or "a declared object can change with time" in reason))
              or (inferred and "A dependency (" in reason and "changed since your latest observation" in reason))
        check("frame_refusal_preserves_live", native._fingerprint(owner) == before)
        clean()
        window.scene.frame_set(1)
        yield


def deletion_cases():
    owner, origin = objects("dependency_delete")
    inherited(owner, origin)
    delete = {"kind": "object_delete", "target": handle(origin), "parameters": {}}
    values = program(owner, origin, [delete], writes=[origin], reads=[owner])
    before = native._fingerprint(owner)
    try:
        job, result = yield from prepare(values)
        if result.get("ready_to_commit"):
            result = job.commit()
    except (ValueError, RuntimeError) as error:
        result = {"success": False, "error": str(error)}
    state["cases"][state["phase"]]["refusal"] = result
    check("in_use_origin_not_deleted", result.get("success") is False
          and "object_delete OBJECT_IN_USE" in result.get("error", "") and owner.modifiers[MODIFIER].mirror_object == origin)
    check("deletion_refusal_keeps_owner", native._fingerprint(owner) == before)
    clean()
    name = origin.name
    values = program(owner, origin, [operation(owner, {"clear": True}), delete], writes=[owner, origin], reads=[])
    job, _ = yield from prepare(values)
    commit(job)
    check("clear_before_delete", owner.modifiers[MODIFIER].mirror_object is None and bpy.data.objects.get(name) is None)


def scenario():
    check("checkout_modules_loaded", str(ROOT) in native.__file__)
    bpy.context.preferences.use_preferences_save = False
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    (OUT / "recovery").mkdir(exist_ok=True)
    yield
    for cases in (set_clear_undo, alias_and_second_edit, refusal_cases, stale_and_rollback,
                  previews_and_stop, pointer_check_refusals, partial_programs, frame_cases, deletion_cases):
        yield from cases()
    state["phase"] = "final_cleanup"
    clean()


fixture_boot.drive(scenario, state, OUT / "mirror-object.json", "ANGELO_MIRROR_OBJECT")
