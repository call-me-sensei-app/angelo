# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual bound checks, private material provenance, preview and commit gates.

Run only in a disposable --background --factory-startup native app. This
fixture uses the production binder, kernels, validator and preview store.
"""

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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-bound-obligations-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent import program_material_facts as material_facts  # noqa: E402
from angelo_agent.program_material_provenance import CLONE_MARKER  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

window = bpy.context.window_manager.windows[0]
factory = window.scene
report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "journal_samples": [], "serialized_provenance_highwater_bytes": 0,
          "journal_highwater_entries": 0, "journal_highwater_owner_edges": 0}
report["journal_metric_scope"] = "Serialized retained provenance only; runtime map entry counts separately, not Python/RNA memory usage"
RED, BLUE = [.8, .05, .05, 1], [.05, .1, .8, 1]
CommitOperator = commit_operator(report)
bpy.utils.register_class(CommitOperator)


def check(label, passed):
    report["checks"][label] = bool(passed)
    assert passed, label


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def poll(job):
    for _ in range(512):
        result = job.poll()
        sample_journal(job.stage, job.phase)
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Native obligation job exceeded bounded polls")


def sample_journal(stage, phase):
    if stage is None:
        return
    values = {key: stage.extras.get(key, {}) for key in (material_facts.REGISTRY, "obligation_clones")}
    size = len(json.dumps(values, separators=(",", ":")).encode())
    entries = sum(len(value) for value in values.values())
    records = list(values[material_facts.REGISTRY].values()) + list(values["obligation_clones"])
    edges = sum(len(entry.get("owners", ())) for entry in records)
    context = getattr(stage, "context", None) or getattr(getattr(stage, "resident", None), "context", None)
    runtime = {name: len(getattr(context, attribute, {})) for name, attribute in
               (("runtime_identity_entries", "_obligation_ids"), ("runtime_clone_entries", "_obligation_clone_ids"),
                ("runtime_copy_source_entries", "_obligation_copy_sources"))}
    report["journal_samples"].append({"phase": phase, "serialized_provenance_bytes": size,
                                     "entries": entries, "owner_edges": edges, **runtime})
    report["serialized_provenance_highwater_bytes"] = max(report["serialized_provenance_highwater_bytes"], size)
    report["journal_highwater_entries"] = max(report["journal_highwater_entries"], entries)
    report["journal_highwater_owner_edges"] = max(report["journal_highwater_owner_edges"], edges)


def commit(job):
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return job.result


def cleanup(scene):
    native.cleanup_all()
    window.scene = factory
    owned = set(scene.objects)
    for obj in list(owned):
        if obj.data:
            owned.add(obj.data)
        owned.update(slot.material for slot in obj.material_slots if slot.material)
        for slot in obj.material_slots:
            if slot.material and slot.material.node_tree:
                owned.update(node.node_tree for node in slot.material.node_tree.nodes
                             if node.bl_idname == "ShaderNodeGroup" and node.node_tree)
    bpy.data.scenes.remove(scene)
    if owned:
        bpy.data.batch_remove(ids=owned)
    queries.invalidate()
    check("cleanup_" + str(len(report["cases"])), native.WRITER.owner is None and not native.PREVIEWS.records
          and not native.has_pending_cleanup())


def setup(name, live_material=False):
    scene = bpy.data.scenes.new("Bound obligations " + name)
    window.scene = scene
    objects = []
    for label in ("A", "B"):
        mesh = bpy.data.meshes.new(name + label)
        mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new(name + label, mesh)
        scene.collection.objects.link(obj)
        objects.append(obj)
    if live_material:
        material = bpy.data.materials.new("Paint")
        if material.node_tree is None:
            material.use_nodes = True
        material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = RED
        for obj in objects:
            obj.data.materials.append(material)
    queries.invalidate()
    return scene, objects


def request(objects, *, mode="APPLY", isolated=False, live=False, authored=False):
    handles = [queries.object_handle(obj) for obj in objects]
    writes = [{"handle": handle, "revision": queries.object_revision(window, obj)}
              for handle, obj in zip(handles, objects)]
    operations = [] if live else [op("material_principled", handles[0], name="Paint", inputs={"Base Color": RED},
                                     also=[handles[1]])]
    if isolated:
        operations += [op("node_edit", handles[1], material="Paint", shared="ISOLATE", edits=[
                           {"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": .2}}]),
                       op("node_edit", handles[1], material="Paint", shared="ISOLATE", edits=[
                           {"op": "set", "node": "Principled BSDF", "inputs": {"Metallic": .3}}])]
    else:
        operations += [op("material_principled", handles[1], name="Paint", inputs={"Base Color": BLUE},
                          shared="ALL_USERS")]
    checks = [{"kind": "material_assignment", "target": handles[1], "material": "Paint", "all_faces": False}] if authored else []
    return validate_call("angelo_scene_program", {"mode": mode, "write_set": writes,
                         "operations": operations, "expected_checks": checks}), handles


def run_material(name, *, isolated=False, live=False, mode="APPLY", corruption=None, authored=False):
    scene, objects = setup(name, live)
    job = None
    try:
        arguments, handles = request(objects, mode=mode, isolated=isolated, live=live, authored=authored)
        before = [native._fingerprint(obj) for obj in objects]
        approved = native.precondition(window, "angelo_scene_program", arguments)
        job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
        result = poll(job)
        report["cases"][name] = {"result": copy.deepcopy(result), "check_count": len(job.checks),
                                 "identity": job.obligations.identity}
        if authored:
            check(name + "_strict_authored_name", result.get("success") is False)
            check(name + "_main_unchanged", all(len(obj.material_slots) == 0 for obj in objects))
            check(name + "_immediate_cleanup", native.WRITER.owner is None and not native.PREVIEWS.records and no_stages())
            return
        check(name + "_stage_pass", result.get("status") in {"preview", "ready_to_commit"})
        if mode == "PREVIEW":
            preview_id = result["preview_id"]
            record = native.PREVIEWS.get(preview_id, job.owner)
            sample_journal(record["stage"], "RETAINED_PREVIEW")
            check(name + "_retained_immutable_bundle", record["obligations"] is job.obligations)
            check(name + "_preview_completion", job.completion_obligations() is job.obligations)
            native.precondition(window, "angelo_accept_preview", {"preview_id": preview_id})
            job = native.begin(window, "angelo_accept_preview", {"preview_id": preview_id})
            result = poll(job)
            check(name + "_accept_rechecks", result.get("ready_to_commit") and len(job.check_results) == len(job.checks))
        job.stage = job.backend.resume_stage(job.stage)
        stage = job.stage
        first = stage.objects[handles[0]].material_slots[0].material
        second = stage.objects[handles[1]].material_slots[0].material
        check(name + "_actual_identity", (first != second) if isolated else first == second)
        if isolated:
            check(name + "_logical_name", material_facts.logical_material_name(stage.context, handles[1], second) == "Paint")
            check(name + "_actual_name_distinct", first.name != second.name)
        # Force the file-backed path: markers and native identities must survive
        # removal from Main and rehydration from the retained private bundle.
        suspended = job.backend.suspend_stage(stage, use_memory=False)
        job.stage = suspended
        stage = job.backend.resume_stage(suspended)
        job.stage = stage
        sample_journal(stage, "FILE_HYDRATED")
        provenance = {key: stage.extras.get(key) for key in (material_facts.REGISTRY, "obligation_clones")}
        report["cases"][name]["provenance_bytes"] = len(json.dumps(provenance, separators=(",", ":")).encode())
        report["cases"][name]["owned_id_count"] = len(stage.context.owned_ids)
        check(name + "_file_hydration", all(job.backend.validate(stage, item)["passed"] for item in job.checks))
        if corruption:
            target = stage.objects[handles[1]]
            material = target.material_slots[0].material
            if corruption == "missing":
                del material[material_facts.MARKER]
            elif corruption == "forged":
                other = stage.context.track_id(material.copy())
                stage.context.owned_ids.add(other.node_tree)
                target.material_slots[0].material = other
                del material[material_facts.MARKER]
            elif corruption == "duplicate":
                other = stage.context.track_id(material.copy())
                stage.context.owned_ids.add(other.node_tree)
            elif corruption == "forged_source":
                from angelo_agent.node_edit_spec import SOURCE
                other = stage.context.track_id(material.copy())
                stage.context.owned_ids.add(other.node_tree)
                for key in (material_facts.MARKER, CLONE_MARKER):
                    if key in other.keys():
                        del other[key]
                other[SOURCE] = int(objects[1].material_slots[0].material.session_uid)
                target.material_slots[0].material = other
            elif corruption == "captured_source":
                from angelo_agent.node_edit_spec import SOURCE
                stage.objects[handles[0]].material_slots[0].material[SOURCE] = 999999999
            elif corruption == "owner":
                target.data.materials.clear()
            result = commit(job)
            report["cases"][name]["commit"] = copy.deepcopy(result)
            check(name + "_precommit_refusal", result.get("success") is False and not result.get("scene_changed"))
            check(name + "_physical_originals_unchanged", [native._fingerprint(obj) for obj in objects] == before)
            check(name + "_immediate_cleanup", native.WRITER.owner is None and not native.PREVIEWS.records and no_stages())
            check(name + "_no_completion", job.completion_obligations() is None)
        else:
            result = commit(job)
            report["cases"][name]["commit"] = copy.deepcopy(result)
            check(name + "_committed", result.get("status") == "committed")
            check(name + "_completion", job.completion_obligations() is job.obligations)
            check(name + "_markers_stripped", all(not {material_facts.MARKER, CLONE_MARKER} & set(slot.material.keys())
                                                   for obj in objects for slot in obj.material_slots if slot.material))
            check(name + "_committed_identity", (objects[0].material_slots[0].material != objects[1].material_slots[0].material)
                  if isolated else objects[0].material_slots[0].material == objects[1].material_slots[0].material)
    finally:
        if job is not None and not job.closed:
            job.cancel()
        cleanup(scene)


def changed_users():
    scene, objects = setup("changed_users", True)
    job = None
    try:
        arguments, handles = request(objects, live=True)
        approved = native.precondition(window, "angelo_scene_program", arguments)
        outsider = bpy.data.objects.new("New material user", objects[0].data.copy())
        scene.collection.objects.link(outsider)
        job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
        result = poll(job)
        report["cases"]["changed_users"] = copy.deepcopy(result)
        check("changed_users_refused_before_stage", result.get("success") is False and job.stage is None
              and not result.get("scene_changed"))
    finally:
        if job is not None and not job.closed:
            job.cancel()
        cleanup(scene)


def stored_facts_only():
    scene, objects = setup("stored_facts", True)
    calls = []
    try:
        material = objects[0].material_slots[0].material
        material.node_tree.nodes.new("ShaderNodeMath").name = "Unrelated"
        group = material.node_tree.nodes.new("ShaderNodeGroup")
        group.name = "Group"
        group.node_tree = bpy.data.node_groups.new("Bound group", "ShaderNodeTree")
        arguments, handles = request(objects, live=True)
        arguments["operations"].append(op("node_edit", handles[0], material="Paint", group="Group", edits=[
            {"op": "set", "node": "Missing", "settings": {"label": "read-only probe"}}]))
        arguments["operations"].append(op("node_edit", handles[0], material="Paint", edits=[
            {"op": "link", "from_node": "Principled BSDF", "from_socket": "BSDF",
             "to_node": "Material Output", "to_socket": "Surface"}]))
        arguments = validate_call("angelo_scene_program", arguments)
        setattr(bpy.types.Material, "angelo_bound_getter_probe",
                bpy.props.BoolProperty(get=lambda material: calls.append(material.name) or False))
        setattr(bpy.types.ShaderNodeMath, "node_tree", property(lambda node: calls.append(node.name)))
        backend = native.BlenderProgramBackend(window)
        binding = {"program": arguments, "handles": handles, "originals": dict(zip(handles, objects))}
        before_ids = native._main_ids()
        before_groups = {item.as_pointer() for item in bpy.data.node_groups}
        observed = material_facts.collect_material_facts(backend, binding)
        report["cases"]["stored_facts"] = {"getter_calls": len(calls), "resources": len(observed["resources"])}
        check("collector_never_reads_addon_getter", not calls and bool(observed["resources"]))
        check("collector_builtin_socket_role", observed["resources"]["live:%d" % material.session_uid]
              ["inputs"]["Material Output"]["Surface"] is False)
        check("collector_no_datablock_allocation", set(native._main_ids()) == set(before_ids))
        check("collector_no_group_allocation", {item.as_pointer() for item in bpy.data.node_groups} == before_groups)
    finally:
        if hasattr(bpy.types.Material, "angelo_bound_getter_probe"):
            delattr(bpy.types.Material, "angelo_bound_getter_probe")
        if hasattr(bpy.types.ShaderNodeMath, "node_tree"):
            delattr(bpy.types.ShaderNodeMath, "node_tree")
        cleanup(scene)


def nested_group():
    scene, objects = setup("nested_group", True)
    job = None
    try:
        material = objects[0].material_slots[0].material
        group = bpy.data.node_groups.new("Bound nested group", "ShaderNodeTree")
        group.nodes.new("ShaderNodeValue").name = "Value"
        node = material.node_tree.nodes.new("ShaderNodeGroup")
        node.name, node.node_tree = "Group", group
        handles = [queries.object_handle(obj) for obj in objects]
        arguments = validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": [
            {"handle": handle, "revision": queries.object_revision(window, obj)} for handle, obj in zip(handles, objects)],
            "operations": [op("node_edit", handles[1], material="Paint", group="Group", shared="ISOLATE",
                              edits=[{"op": "set", "node": "Value", "settings": {"label": "Verified group"}}])]})
        approved = native.precondition(window, "angelo_scene_program", arguments)
        job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
        staged = poll(job)
        report["cases"]["nested_group"] = {"stage": copy.deepcopy(staged)}
        check("nested_group_staged", staged.get("ready_to_commit"))
        resident = job.backend.resume_stage(job.stage)
        job.stage = job.backend.suspend_stage(resident, use_memory=False)
        resident = job.backend.resume_stage(job.stage)
        job.stage = resident
        sample_journal(resident, "NESTED_FILE_HYDRATED")
        check("nested_group_file_hydration", all(job.backend.validate(resident, item)["passed"] for item in job.checks))
        result = commit(job)
        report["cases"]["nested_group"]["commit"] = copy.deepcopy(result)
        check("nested_group_commit", result.get("status") == "committed")
        first = objects[0].material_slots[0].material.node_tree.nodes["Group"].node_tree
        second = objects[1].material_slots[0].material.node_tree.nodes["Group"].node_tree
        check("nested_group_isolated_identity", first != second and first.nodes["Value"].label == ""
              and second.nodes["Value"].label == "Verified group")
        check("nested_group_markers_stripped", not {material_facts.MARKER, CLONE_MARKER} & set(second.keys()))
    finally:
        if job is not None and not job.closed:
            job.cancel()
        cleanup(scene)


patches = []
for name in ("initialize_clones", "finish_operation", "snapshot_provenance", "restore_provenance", "strip_provenance"):
    original = getattr(material_facts, name)

    def sampled(stage, *args, original=original, name=name, **kwargs):
        sample_journal(stage, name + ":before")
        result = original(stage, *args, **kwargs)
        sample_journal(stage, name + ":after")
        return result

    patcher = patch.object(material_facts, name, sampled)
    patcher.start()
    patches.append(patcher)

try:
    stored_facts_only()
    for case in ("shared_apply", "shared_preview", "live_shared", "fresh_isolate", "live_isolate", "authored_name",
                 "missing", "forged", "forged_source", "captured_source", "duplicate", "owner"):
        run_material(case, isolated=case in {"fresh_isolate", "live_isolate", "authored_name", "forged", "owner", "captured_source"},
                     live=case in {"live_shared", "live_isolate", "forged_source", "captured_source"},
                     mode="PREVIEW" if case == "shared_preview" else "APPLY", authored=case == "authored_name",
                     corruption=case if case in {"missing", "forged", "forged_source", "captured_source", "duplicate", "owner"} else None)
    changed_users()
    nested_group()
    report["passed"] = True
except Exception:
    report["passed"] = False
    report["traceback"] = traceback.format_exc()
    raise
finally:
    for patcher in reversed(patches):
        patcher.stop()
    (OUT / "bound-obligations.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    bpy.utils.unregister_class(CommitOperator)
