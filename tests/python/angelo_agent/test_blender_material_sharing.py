# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One material for several objects through real staged programs.

``material_assign`` (assign and copy), one material stated once in a program
and used by several objects, and ``shared: ALL_USERS`` on
``material_principled``, ``shader_nodes`` and ``node_edit``. Programs are
normalized by ``tool_specs.validate_call`` as a model's plan is, so each
carries its derived checks. Every path is checked on the live scene after
commit, then for one Undo step and Redo, a forced commit failure, a stale
binding and a changed user set. Run only in a disposable instance with the
exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-material-sharing-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion, compact_receipt, terse_receipt  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
IRON, RED, BLUE = [0.02, 0.02, 0.025, 1], [0.8, 0.05, 0.05, 1], [0.05, 0.1, 0.8, 1]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def objects():
    return window.scene.objects


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def arguments(operations, writes=(), reads=()):
    return {"mode": "APPLY", "read_set": [binding(obj) for obj in reads],
            "write_set": [binding(obj) for obj in writes], "operations": operations}


def program(operations, writes=(), reads=()):
    return validate_call("angelo_scene_program", arguments(operations, writes, reads))


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def run(canonical):
    job = native.begin(window, "angelo_scene_program", canonical)
    return job, settle(job)


def committed(name, operations, writes=(), reads=()):
    job, staged = run(program(operations, writes, reads))
    report["last_error"] = staged.get("error")
    check(name + "_stages", staged.get("ready_to_commit"))
    done = commit(job)
    report["last_error"] = job.result.get("error")
    check(name + "_commits", done and passed(job))
    return job


def refused(name, operations, code, writes=(), reads=()):
    before = {(block.bl_rna.identifier, block.as_pointer()) for collection in
              (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
               bpy.data.scenes, bpy.data.collections) for block in collection}
    try:
        canonical = program(operations, writes, reads)
    except ValueError as error:
        after = {(block.bl_rna.identifier, block.as_pointer()) for collection in
                 (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
                  bpy.data.scenes, bpy.data.collections) for block in collection}
        report["last_error"] = str(error)
        check(name, code in str(error) and before == after and no_stages())
        return str(error)
    job, result = run(canonical)
    failed = result.get("status") == "failed" or not commit(job)
    error = (job.result or result).get("error", "")
    report.setdefault("refusal_results", {})[name] = dict(job.result or result)
    report["last_error"] = error
    check(name, failed and code in error and no_stages())
    return error


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def material_of(name, slot=0):
    return objects()[name].material_slots[slot].material


def color(material):
    node = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    return [round(value, 4) for value in node.inputs["Base Color"].default_value]


def roughness(material):
    node = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    return round(node.inputs["Roughness"].default_value, 4)


def named(prefix):
    return sorted(material.name for material in bpy.data.materials if material.name.startswith(prefix) and material.users)


def primitive(name, alias, x):
    return op("create_primitive", primitive="CUBE", name=name, alias=alias, size=1, location=[x, 0, 0])


def one_material_in_one_program():
    """The railings case: several objects of one program share one stated material."""
    baseline("materials QA one program")
    job = committed("one_program", [
        primitive("QA Post", "post", 3), primitive("QA Rail", "rail", 5), primitive("QA Cap", "cap", 7),
        op("material_principled", "$post", name="QA Iron", inputs={"Base Color": IRON, "Metallic": 0.9, "Roughness": 0.4}),
        op("material_assign", "$rail", material="QA Iron", source="$post"),
        # The same statement again names the same material; it never makes 'QA Iron.001'.
        op("material_principled", "$cap", name="QA Iron", inputs={"Base Color": IRON, "Metallic": 0.9, "Roughness": 0.4})])
    iron = material_of("QA Post")
    check("one_program_one_datablock", material_of("QA Rail") == iron and material_of("QA Cap") == iron
          and named("QA Iron") == ["QA Iron"] and color(iron) == [round(v, 4) for v in IRON])
    kinds = [row["checks"][0]["kind"] for row in job.result["checks"]]
    check("one_program_every_user_is_checked", kinds.count("material_assignment") == 3)
    check("one_program_reuse_is_reported", job.result["operation_results"][5].get("reused") is True)
    undo()
    check("one_program_one_undo", "QA Post" not in objects() and not named("QA Iron") and no_stages())
    redo()
    check("one_program_redo", material_of("QA Rail") == material_of("QA Post") and named("QA Iron") == ["QA Iron"])
    refused("a_material_stated_twice_with_other_values_is_refused", [
        primitive("QA A", "a", 9), primitive("QA B", "b", 11),
        op("material_principled", "$a", name="QA Paint", inputs={"Base Color": RED}),
        op("material_principled", "$b", name="QA Paint", inputs={"Base Color": BLUE})], "MATERIAL_REDEFINED")


def assign_and_copy_between_objects():
    """A later program gives a new object a material another object has, then an independent copy."""
    baseline("materials QA assign")
    post = objects()["QA Post"]
    iron = material_of("QA Post")
    job = committed("assign_existing", [primitive("QA Lamp", "lamp", 9),
                                        op("material_assign", "$lamp", material="QA Iron", source=handle(post))], reads=[post])
    check("assign_shares_the_live_datablock", material_of("QA Lamp") == iron and named("QA Iron") == ["QA Iron"])
    check("assign_names_the_material", job.result["operation_results"][1]["material"] == "QA Iron")
    error = refused("a_taken_name_is_refused_with_the_remedy", [
        primitive("QA Bolt", "bolt", 11), op("material_principled", "$bolt", name="QA Iron", inputs={"Base Color": RED})],
        "NAME_TAKEN")
    check("the_refusal_names_material_assign", "material_assign" in error)
    refused("an_unknown_material_is_refused", [primitive("QA Bolt", "bolt", 11),
                                                op("material_assign", "$bolt", material="QA Brass", source=handle(post))],
            "MATERIAL_NOT_FOUND", reads=[post])
    lamp = objects()["QA Lamp"]
    committed("copy_as", [op("material_assign", lamp, material="QA Iron", copy_as="QA Iron Dark"),
                          op("material_principled", lamp, name="QA Iron Dark", inputs={"Base Color": [0, 0, 0, 1]})],
              writes=[lamp])
    dark = material_of("QA Lamp", 1)
    check("copy_is_independent", dark.name == "QA Iron Dark" and dark != iron and color(dark) == [0, 0, 0, 1]
          and color(iron) == [round(v, 4) for v in IRON] and material_of("QA Post") == iron)
    undo()
    check("copy_one_undo", bpy.data.materials.get("QA Iron Dark") is None or not bpy.data.materials["QA Iron Dark"].users)
    undo()
    check("assign_one_undo", "QA Lamp" not in objects() and material_of("QA Post").name == "QA Iron" and no_stages())
    redo()
    check("assign_redo", material_of("QA Lamp") == material_of("QA Post"))


def edit_for_all_users():
    """The second edit: recolour the shared material once and every user changes."""
    baseline("materials QA all users")
    post, rail, cap, lamp = (objects()[name] for name in ("QA Post", "QA Rail", "QA Cap", "QA Lamp"))
    error = refused("a_shared_material_is_refused_by_default", [
        op("material_principled", rail, name="QA Iron", inputs={"Base Color": RED})], "NODE_TREE_SHARED", writes=[rail])
    check("the_refusal_names_all_users", "ALL_USERS" in error)
    before = {obj.name: queries.object_revision(window, obj) for obj in (post, cap, lamp)}
    shown, message = review(program([op("material_principled", rail, name="QA Iron",
                                        inputs={"Base Color": RED, "Roughness": 0.3}, shared="ALL_USERS")], writes=[rail]))
    report["all_users_review"] = {"shared_edits": shown.get("shared_edits"), "warning": message["warning"]}
    check("the_all_users_approval_names_the_other_users", shown.get("shared_edits") == [
        {"step": 1, "material": "QA Iron", "other_users": ["QA Cap", "QA Lamp", "QA Post"], "other_count": 3}]
        and "3 other objects: “QA Cap”, “QA Lamp”, “QA Post”" in message["warning"])
    queries.invalidate()
    job = committed("recolour_for_all_users", [
        op("material_principled", rail, name="QA Iron", inputs={"Base Color": RED, "Roughness": 0.3}, shared="ALL_USERS")],
        writes=[rail])
    materials = {material_of(name) for name in ("QA Post", "QA Rail", "QA Cap", "QA Lamp")}
    check("every_user_shows_the_edit", len(materials) == 1 and color(next(iter(materials))) == [round(v, 4) for v in RED]
          and named("QA Iron") == ["QA Iron"])
    (row,) = job.result["shared_edits"]
    check("the_result_names_the_users", row == {"material": "QA Iron", "users": ["QA Cap", "QA Lamp", "QA Post", "QA Rail"],
                                                 "count": 4, "truncated": False})
    check("the_staged_result_named_them_too", job.result["operation_results"][0]["shared_users"] == row["users"]
          and job.result["operation_results"][0]["shared_policy"] == "ALL_USERS")
    live = {entry["name"]: entry for entry in job.result["live_revisions"]["objects"]}
    report["live_revisions"] = job.result["live_revisions"]
    check("the_other_users_get_new_revisions", all(
        live[name]["role"] == "SHARED" and live[name]["object_revision"] == queries.object_revision(window, objects()[name])
        and live[name]["object_revision"] != before[name] for name in before))
    check("the_mesh_was_not_swapped", "isolated_shared_data" not in job.result
          and [row["fields"] for row in job.result["changes"]["modified"]] == [["node_trees", "object"]])
    report["evidence"] = evidence(job)
    undo()
    check("all_users_one_undo", color(material_of("QA Post")) == [round(v, 4) for v in IRON]
          and len({material_of(name) for name in ("QA Post", "QA Rail", "QA Cap", "QA Lamp")}) == 1 and no_stages())
    redo()
    check("all_users_redo", color(material_of("QA Cap")) == [round(v, 4) for v in RED]
          and len({material_of(name) for name in ("QA Post", "QA Rail", "QA Cap", "QA Lamp")}) == 1)
    cap = objects()["QA Cap"]
    job = committed("node_edit_for_all_users", [op("node_edit", cap, material="QA Iron", shared="ALL_USERS", edits=[
        {"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": 0.75}}])], writes=[cap])
    check("node_edit_reaches_every_user", roughness(material_of("QA Lamp")) == 0.75
          and job.result["shared_edits"][0]["count"] == 4)
    post = objects()["QA Post"]
    job = committed("shader_nodes_for_all_users", [op("shader_nodes", post, name="QA Iron", shared="ALL_USERS", nodes=[
        {"id": "bsdf", "type": "BSDF_PRINCIPLED", "inputs": {"Base Color": BLUE}},
        {"id": "out", "type": "OUTPUT_MATERIAL"}], links=[
        {"from_node": "bsdf", "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}])], writes=[post])
    check("shader_nodes_reaches_every_user", color(material_of("QA Rail")) == [round(v, 4) for v in BLUE]
          and job.result["shared_edits"][0]["users"] == ["QA Cap", "QA Lamp", "QA Post", "QA Rail"])


def evidence(job):
    """What each runtime's model is handed for the committed program (claude_outcomes)."""
    call = {"tool": "angelo_scene_program", "arguments": job.program}
    # Exercise the production proof API with the actual native finished job.
    # This fixture does not claim controller durability/transport coverage.
    from types import SimpleNamespace
    from angelo_agent.program_completion import capture_program, mint_completion
    captured = capture_program(job)
    host = SimpleNamespace(_epoch=1, _request_generation=1, _closing=False, cancelled=False)
    delivery = SimpleNamespace(id="native-material-evidence", epoch=1, generation=1,
        name=call["tool"], arguments=job.program, program_arguments=None)
    completion = mint_completion(host, delivery, captured, job.result)
    check("native_finished_job_supplies_private_proof", captured is not None and completion is not None)
    check("raw_bound_result_does_not_certify", certified_completion([call], [job.result],
          read_only=lambda name: False, completions=[None]) is None)
    summary = certified_completion([call], [job.result], read_only=lambda name: False, completions=[completion])
    host._request_generation += 1
    check("native_proof_expires_with_request", certified_completion([call], [job.result],
          read_only=lambda name: False, completions=[completion]) is None)
    terse = terse_receipt("angelo_scene_program", job.result)
    receipt = compact_receipt("angelo_scene_program", job.result)
    wanted = "material 'QA Iron' edited for all 4 users ('QA Cap', 'QA Lamp', 'QA Post', 'QA Rail')"
    check("the_host_summary_names_the_users", wanted in summary)
    check("the_claude_handoff_names_the_users", wanted in terse["verified"]
          and {"QA Cap", "QA Lamp", "QA Post"} <= {row["name"] for row in terse["live"]})
    check("the_codex_receipt_names_the_users", receipt["shared_edits"][0]["users"] == ["QA Cap", "QA Lamp", "QA Post", "QA Rail"]
          and {"SHARED"} <= {row["role"] for row in receipt["live_revisions"]["objects"]})
    return {"summary": summary, "claude": terse, "codex": receipt}


def instances_keep_their_mesh():
    """Linked duplicates share mesh data; recolouring through one of them keeps the instancing."""
    baseline("materials QA instances")
    post = objects()["QA Post"]
    committed("instances", [op("object_duplicate", source=handle(post), alias="twin", name="QA Post Twin", linked=True,
                               location=[3, 3, 0])], reads=[post])
    twin = objects()["QA Post Twin"]
    check("instances_share_mesh", twin.data == post.data and post.data.users == 2)
    job = committed("recolour_through_an_instance", [
        op("material_principled", twin, name="QA Iron", inputs={"Base Color": IRON}, shared="ALL_USERS")], writes=[twin])
    post, twin = objects()["QA Post"], objects()["QA Post Twin"]
    check("the_instances_still_share_mesh", twin.data == post.data and post.data.users == 2
          and "isolated_shared_data" not in job.result)
    check("the_instance_edit_reached_every_user", color(material_of("QA Rail")) == [round(v, 4) for v in IRON]
          and job.result["shared_edits"][0]["count"] == 5)


def recovery():
    """A forced commit failure, a changed user set and a stale binding leave the scene as it was."""
    queries.invalidate()
    rail = objects()["QA Rail"]
    iron = material_of("QA Rail")
    users = {name: material_of(name) for name in ("QA Post", "QA Rail", "QA Cap", "QA Lamp", "QA Post Twin")}
    names = sorted(material.name for material in bpy.data.materials)
    fingerprints = {name: native._fingerprint(objects()[name]) for name in users}
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after the material transfer")

    job, _ = run(program([op("material_principled", rail, name="QA Iron", inputs={"Base Color": BLUE}, shared="ALL_USERS")],
                         writes=[rail]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("rollback_reports_failure", job.result.get("status") == "failed" and not job.result.get("partial_changes_possible"))
    check("rollback_restores_slots_names_and_scene",
          all(material_of(name) == material for name, material in users.items())
          and iron.name == "QA Iron" and color(iron) == [round(v, 4) for v in IRON]
          and sorted(material.name for material in bpy.data.materials if material.users) == [
              name for name in names if bpy.data.materials[name].users]
          and all(native._fingerprint(objects()[name]) == value for name, value in fingerprints.items()) and no_stages())
    queries.invalidate()
    # The user set changes between approval and commit: an object that never saw the plan gains the material.
    job, staged = run(program([op("material_principled", rail, name="QA Iron", inputs={"Base Color": BLUE},
                                  shared="ALL_USERS")], writes=[rail]))
    check("changed_users_stage", staged.get("ready_to_commit"))
    before_main = set(native._main_ids())
    cube = objects()["Cube"]
    cube.data.materials.append(iron)
    with patch.object(native.BlenderProgramBackend, "resume_stage", side_effect=AssertionError("stale stage hydrated")) as resumed, \
            patch.object(native.BlenderProgramBackend, "create_stage", side_effect=AssertionError("stale stage restarted")) as restarted, \
            patch.object(native.BlenderProgramBackend, "commit", side_effect=AssertionError("stale stage committed")) as commit_attempt:
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["changed_users_result"] = dict(job.result)
    report["changed_users_dispatch"] = {"resume": resumed.call_count, "create_stage": restarted.call_count,
                                        "commit": commit_attempt.call_count}
    check("a_changed_user_set_is_refused", job.result.get("status") == "failed"
          and "bound material verification changed" in job.result.get("error", "")
          and "users of material 'QA Iron' changed" in job.result["error"] and "review" in job.result["error"]
          and not job.result.get("scene_changed") and not job.result.get("partial_changes_possible")
          and color(iron) == [round(v, 4) for v in IRON] and all(material_of(name) == item for name, item in users.items())
          and cube.data.materials[-1] == iron and no_stages()
          and set(native._main_ids()) == before_main)
    check("changed_users_never_hydrate_restage_or_commit", not resumed.called and not restarted.called
          and not commit_attempt.called and job.closed
          and native.WRITER.owner is None and not native.PREVIEWS.records)
    cube.data.materials.pop()
    queries.invalidate()
    # The material itself changes after the plan was bound: the target's revision is stale.
    stale = program([op("material_principled", rail, name="QA Iron", inputs={"Base Color": BLUE}, shared="ALL_USERS")],
                    writes=[rail])
    shader = next(node for node in iron.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    shader.inputs["Roughness"].default_value = 0.11
    queries.invalidate(window)  # An edit the host did not make: the cached revision is read again.
    job, result = run(stale)
    report["last_error"] = result.get("error")
    check("a_stale_binding_is_refused", result.get("status") == "failed" and "revision changed" in result.get("error", "")
          and color(iron) == [round(v, 4) for v in IRON] and roughness(iron) == 0.11 and no_stages())
    shader.inputs["Roughness"].default_value = 0.4
    queries.invalidate()


def users_outside_the_program():
    """A user outside the bound scene, or another kind of datablock, refuses an edit for all users."""
    queries.invalidate()
    rail = objects()["QA Rail"]
    iron = material_of("QA Rail")
    other = bpy.data.scenes.new("QA Other Scene")
    mesh = bpy.data.meshes.new("QA Elsewhere Mesh")
    mesh.materials.append(iron)
    elsewhere = bpy.data.objects.new("QA Elsewhere", mesh)
    other.collection.objects.link(elsewhere)
    error = refused("a_user_in_another_scene_refuses_all_users", [
        op("material_principled", rail, name="QA Iron", inputs={"Base Color": BLUE}, shared="ALL_USERS")],
        "SHARED_OUTSIDE_SCENE", writes=[rail])
    check("the_refusal_names_the_outside_user", "QA Elsewhere" in error and "ISOLATE" in error)
    bpy.data.objects.remove(elsewhere)
    # Data no object uses still lists the material; it is not a user.
    job = committed("orphan_data_is_not_a_user", [
        op("material_principled", rail, name="QA Iron", inputs={"Base Color": IRON, "Roughness": 0.6}, shared="ALL_USERS")],
        writes=[rail])
    check("orphan_data_is_ignored", job.result["shared_edits"][0]["count"] == 5 and mesh.users == 0)
    bpy.data.meshes.remove(mesh)
    bpy.data.scenes.remove(other)
    queries.invalidate()
    rail = objects()["QA Rail"]
    try:  # Refused when the plan is validated, before anything is staged.
        program([op("node_edit", handle(rail), modifier="Nodes", shared="ALL_USERS", edits=[
            {"op": "remove", "node": "X"}])], writes=[rail])
        rejected = ""
    except ValueError as error:
        rejected = str(error)
    check("all_users_is_for_materials_only", "ALL_USERS_MATERIAL_ONLY" in rejected)
    post = objects()["QA Post"]
    before_ids = set(native._main_ids())
    before_objects = {obj.name: native._fingerprint(obj) for obj in objects()}
    calls = {"bind": 0, "create_stage": 0}
    originals = {name: getattr(native.BlenderProgramBackend, name) for name in calls}

    def counted(name):
        def invoke(backend, *args, **kwargs):
            calls[name] += 1
            return originals[name](backend, *args, **kwargs)
        return invoke

    with patch.object(native.BlenderProgramBackend, "bind", counted("bind")), \
            patch.object(native.BlenderProgramBackend, "create_stage", counted("create_stage")), \
            patch.object(native.BlenderProgramBackend, "commit", side_effect=AssertionError("refused policy committed")) as attempt:
        error = refused("assign_then_a_private_edit_is_refused", [
            primitive("QA Late", "late", 13), op("material_assign", "$late", material="QA Iron", source=handle(post)),
            op("node_edit", handle(post), material="QA Iron", shared="ISOLATE", edits=[
                {"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": 0.2}}])], "NODE_TREE_SHARED", writes=[post])
    report["assigned_isolate_dispatch"] = dict(calls, commit=attempt.call_count)
    # Refused when its obligations are bound, before any stage or approval (material_rules.shared_in_program).
    check("assigned_policy_keeps_native_precedence_and_artist_state", "assigned to another object earlier" in error
          and calls == {"bind": 1, "create_stage": 0} and not attempt.called and set(native._main_ids()) == before_ids
          and {obj.name: native._fingerprint(obj) for obj in objects()} == before_objects
          and native.WRITER.owner is None and not native.PREVIEWS.records and no_stages())


def certify(job):
    """The host summary of a finished job, through the production completion proof."""
    from types import SimpleNamespace
    from angelo_agent.program_completion import capture_program, mint_completion
    call = {"tool": "angelo_scene_program", "arguments": job.program}
    host = SimpleNamespace(_epoch=1, _request_generation=1, _closing=False, cancelled=False)
    delivery = SimpleNamespace(id="native-property-material", epoch=1, generation=1, name=call["tool"],
                               arguments=job.program, program_arguments=None)
    completion = mint_completion(host, delivery, capture_program(job), job.result)
    return certified_completion([call], [job.result], read_only=lambda name: False, completions=[completion])


def review(canonical):
    """What the approval shows for a program bound now (approvals; no controller)."""
    from types import SimpleNamespace
    from angelo_agent import approvals
    bound = native.precondition(window, "angelo_scene_program", canonical)
    call = SimpleNamespace(name="angelo_scene_program", arguments=canonical, precondition=bound, program_arguments=None)
    shown = approvals._review_arguments(call)
    return shown, approvals.approval_message("angelo_scene_program", shown)


def property_paths_of_a_shared_material():
    """property_set's active_material paths write the live material in place.

    They used to change every object that shows the material while reporting
    only the target. A material another user shows is now refused unless
    shared ALL_USERS; then the approval names the other users, every user
    changes, and the result, the host summary and the post-commit revisions
    name them. A user outside the scene, or a user set that changes before
    commit, refuses the program; an own copy made in the same program commits.
    """
    paint = bpy.data.materials.new("QA Paint")
    paint.roughness = .5
    for index, name in enumerate(("QA Fence A", "QA Fence B", "QA Gate")):
        mesh = bpy.data.meshes.new(name + " Mesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
        mesh.materials.append(paint)
        obj = bpy.data.objects.new(name, mesh)
        obj.location = (40 + 3 * index, 0, 0)
        window.scene.collection.objects.link(obj)
    baseline("materials QA property paths")
    fence = objects()["QA Fence A"]
    before = {name: queries.object_revision(window, objects()[name]) for name in ("QA Fence B", "QA Gate")}
    error = refused("a_shared_material_path_is_refused_by_default", [
        op("property_set", fence, values={"active_material.roughness": .9})], "MATERIAL_SHARED", writes=[fence])
    check("the_path_refusal_names_the_users_and_the_remedy", "QA Fence B" in error and "ALL_USERS" in error
          and "copy_as" in error and abs(paint.roughness - .5) < 1e-6 and all(
              queries.object_revision(window, objects()[name]) == revision for name, revision in before.items()))
    canonical = program([op("property_set", fence, values={"active_material.roughness": .9,
                                                           "active_material.metallic": .3}, shared="ALL_USERS")],
                        writes=[fence])
    shown, message = review(canonical)
    report["property_review"] = {"shared_edits": shown.get("shared_edits"), "warning": message["warning"]}
    check("the_approval_names_the_other_path_users", shown.get("shared_edits") == [
        {"step": 1, "material": "QA Paint", "other_users": ["QA Fence B", "QA Gate"], "other_count": 2}]
        and "2 other objects: “QA Fence B”, “QA Gate”" in message["warning"])
    job, staged = run(canonical)
    report["all_users_path_staged"] = {key: staged.get(key) for key in ("status", "error")}
    check("all_users_path_stages", staged.get("ready_to_commit"))
    check("all_users_path_commits", commit(job) and passed(job))
    check("every_user_shows_the_path_edit", all(abs(material_of(name).roughness - .9) < 1e-6 and material_of(name) == paint
                                                for name in ("QA Fence A", "QA Fence B", "QA Gate")))
    check("the_path_result_names_the_users", job.result.get("shared_edits") == [
        {"material": "QA Paint", "users": ["QA Fence A", "QA Fence B", "QA Gate"], "count": 3, "truncated": False}])
    live = {entry["name"]: entry for entry in job.result["live_revisions"]["objects"]}
    check("the_other_path_users_get_new_revisions", all(
        live[name]["role"] == "SHARED" and live[name]["object_revision"] == queries.object_revision(window, objects()[name])
        and live[name]["object_revision"] != before[name] for name in before))
    summary = certify(job)
    report["property_summary"] = summary
    check("the_path_summary_names_the_users",
          "material 'QA Paint' edited for all 3 users ('QA Fence A', 'QA Fence B', 'QA Gate')" in (summary or ""))
    undo()
    check("all_users_path_one_undo", abs(bpy.data.materials["QA Paint"].roughness - .5) < 1e-6 and no_stages())
    redo()
    check("all_users_path_redo", abs(bpy.data.materials["QA Paint"].roughness - .9) < 1e-6)
    # The user set changes after staging: an object that never saw the plan gains the material.
    paint, fence = bpy.data.materials["QA Paint"], objects()["QA Fence A"]
    job, staged = run(program([op("property_set", fence, values={"active_material.roughness": .2}, shared="ALL_USERS")],
                              writes=[fence]))
    check("changed_path_users_stage", staged.get("ready_to_commit"))
    cube = objects()["Cube"]
    cube.data.materials.append(paint)
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report["changed_path_users_result"] = {key: job.result.get(key) for key in ("status", "error", "scene_changed")}
    check("a_changed_path_user_set_is_refused", job.result.get("status") == "failed"
          and "users of material 'QA Paint' changed" in job.result.get("error", "")
          and not job.result.get("scene_changed") and abs(paint.roughness - .9) < 1e-6 and no_stages())
    cube.data.materials.pop()
    queries.invalidate()
    # A user in another scene: the program cannot name it.
    other = bpy.data.scenes.new("QA Paint Scene")
    mesh = bpy.data.meshes.new("QA Far Mesh")
    mesh.materials.append(paint)
    far = bpy.data.objects.new("QA Far", mesh)
    other.collection.objects.link(far)
    error = refused("a_path_user_in_another_scene_is_refused", [
        op("property_set", fence, values={"active_material.roughness": .2}, shared="ALL_USERS")],
        "SHARED_OUTSIDE_SCENE", writes=[fence])
    check("the_outside_path_refusal_names_the_user", "QA Far" in error and abs(paint.roughness - .9) < 1e-6)
    bpy.data.objects.remove(far)
    bpy.data.meshes.remove(mesh)
    bpy.data.scenes.remove(other)
    queries.invalidate()
    try:
        program([op("property_set", fence, values={"hide_render": True}, shared="ALL_USERS")], writes=[fence])
        rejected = ""
    except ValueError as error:
        rejected = str(error)
    check("all_users_needs_a_material_path", "ALL_USERS_MATERIAL_ONLY" in rejected)
    # The other remedy in one program: an own copy in the active slot, then its settings.
    job = committed("an_own_copy_then_its_settings", [
        op("material_assign", fence, material="QA Paint", copy_as="QA Fence Paint", slot=0),
        op("property_set", fence, values={"active_material.roughness": .25})], writes=[fence])
    own = material_of("QA Fence A")
    check("the_copy_takes_the_setting_and_the_others_keep_theirs", own.name == "QA Fence Paint" and own != paint
          and abs(own.roughness - .25) < 1e-6 and abs(paint.roughness - .9) < 1e-6 and material_of("QA Gate") == paint
          and "shared_edits" not in job.result)
    # A material only its object uses: an ordinary in-place edit, with nothing shared to report.
    job = committed("an_unshared_material_path", [op("property_set", objects()["QA Fence A"],
                                                     values={"active_material.roughness": .35})],
                    writes=[objects()["QA Fence A"]])
    check("an_unshared_path_is_written_in_place", abs(own.roughness - .35) < 1e-6 and material_of("QA Fence A") == own
          and "shared_edits" not in job.result)


def library_material(name):
    """A material linked from a library file this test writes."""
    path = str(OUT / ("library-%s.blend" % name.replace(" ", "-")))
    block = bpy.data.materials.new(name)
    block.roughness = .5
    bpy.data.libraries.write(path, {block}, fake_user=True)
    bpy.data.materials.remove(block)
    with bpy.data.libraries.load(path, link=True) as (_source, target):
        target.materials = [name]
    return next(material for material in bpy.data.materials if material.name == name and material.library)


def linked_orphan_and_slot_paths():
    """Linked and overridden materials, data no object uses and a slot change beside material paths.

    A linked material's edit was verified and lost on save and reopen; an
    override failed as an INTERNAL_ERROR with possible partial changes; an
    orphan mesh with a fake user changed unnamed; a slot change beside a
    material path failed as VALUE_NOT_RETAINED. Each is now a coded refusal
    that changed nothing, or a named edit.
    """
    linked = library_material("QA Library Paint")
    mesh = bpy.data.meshes.new("QA Linked Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    mesh.materials.append(linked)
    holder = bpy.data.objects.new("QA Linked Holder", mesh)
    window.scene.collection.objects.link(holder)
    baseline("materials QA linked")
    for shared in (None, "ALL_USERS"):
        parameters = {"values": {"active_material.roughness": .9}, **({"shared": shared} if shared else {})}
        error = refused("a_linked_material_is_refused_" + str(shared).lower(), [op("property_set", holder, **parameters)],
                        "LINKED_MATERIAL", writes=[holder])
        result = report["refusal_results"]["a_linked_material_is_refused_" + str(shared).lower()]
        check("the_linked_refusal_is_coded_and_changed_nothing_" + str(shared).lower(),
              result.get("error_code") == "UNSUPPORTED" and result.get("partial_changes_possible") is False
              and "copy_as" in error and abs(linked.roughness - .5) < 1e-6)
    override = linked.override_create(remap_local_usages=True)
    queries.invalidate()
    holder = objects()["QA Linked Holder"]
    check("the_holder_shows_the_override", holder.active_material == override)
    refused("an_override_material_is_refused", [op("property_set", holder, values={"active_material.roughness": .9})],
            "LINKED_MATERIAL", writes=[holder])
    result = report["refusal_results"]["an_override_material_is_refused"]
    check("the_override_refusal_is_coded_and_changed_nothing", result.get("error_code") == "UNSUPPORTED"
          and result.get("partial_changes_possible") is False and abs(override.roughness - .5) < 1e-6)
    # An orphan mesh kept by a fake user holds the material: the edit for all users names it.
    paint = bpy.data.materials.new("QA Orphan Paint")
    paint.roughness = .5
    gate = objects()["QA Gate"]
    gate.data.materials[0] = paint
    orphan = bpy.data.meshes.new("QA Orphan Mesh")
    orphan.materials.append(paint)
    orphan.use_fake_user = True
    queries.invalidate()
    canonical = program([op("property_set", gate, values={"active_material.roughness": .7}, shared="ALL_USERS")],
                        writes=[gate])
    shown, message = review(canonical)
    report["orphan_review"] = {"shared_edits": shown.get("shared_edits"), "warning": message["warning"]}
    check("the_approval_names_data_no_object_uses", "“QA Orphan Mesh”" in message["warning"])
    job, staged = run(canonical)
    check("the_orphan_edit_commits", staged.get("ready_to_commit") and commit(job))
    check("the_result_names_the_orphan_holder", job.result.get("shared_edits") == [
        {"material": "QA Orphan Paint", "users": ["QA Gate"], "count": 1, "truncated": False,
         "holders": ["QA Orphan Mesh"]}] and abs(paint.roughness - .7) < 1e-6)
    # A slot change beside the active material's paths: refused before anything is staged.
    error = refused("a_slot_change_beside_material_paths_is_refused", [
        op("property_set", objects()["QA Gate"], values={"active_material_index": 0, "active_material.roughness": .1})],
        "ACTIVE_SLOT_CONFLICT", writes=[objects()["QA Gate"]])
    check("the_slot_refusal_names_the_remedy", "separate program" in error and abs(paint.roughness - .7) < 1e-6)


def one_statement_for_several_objects():
    """also gives one operation's material to more objects; a name alone assigns a material stated earlier."""
    committed("also_setup", [primitive("QA Old", "old", 21),
                             op("material_principled", "$old", name="QA Old Paint", inputs={"Base Color": BLUE})])
    baseline("materials QA also")
    old = objects()["QA Old"]
    job = committed("also", [
        primitive("QA Deck", "deck", 23), primitive("QA Pillar", "pillar", 25), primitive("QA Beam", "beam", 27),
        op("material_principled", "$deck", name="QA Stone", inputs={"Base Color": IRON, "Roughness": 0.85},
           also=["$pillar", handle(old)]),
        # Only the name: the material stated above, never a second definition.
        op("material_principled", "$beam", name="QA Stone")], writes=[old])
    stone, old = material_of("QA Deck"), objects()["QA Old"]
    check("also_one_datablock", material_of("QA Pillar") == stone and material_of("QA Beam") == stone
          and material_of("QA Old", 1) == stone and named("QA Stone") == ["QA Stone"]
          and color(stone) == [round(v, 4) for v in IRON])
    # Its slots stay; every face goes to the material's slot, so the object renders it (a slot no face uses would
    # leave it looking unchanged, as 1.0.3 did while its checks passed).
    check("also_keeps_the_slots_an_object_had", material_of("QA Old").name == "QA Old Paint" and len(old.material_slots) == 2)
    check("also_objects_render_the_material", all(polygon.material_index == 1 for polygon in old.data.polygons)
          and all(polygon.material_index == 0 for name in ("QA Pillar", "QA Beam")
                  for polygon in objects()[name].data.polygons))
    results = job.result["operation_results"]
    check("the_result_names_the_also_objects", results[3]["also"] == ["QA Pillar", "QA Old"])
    check("a_name_alone_reuses_the_stated_material", results[4].get("reused") is True)
    kinds = [row["checks"][0]["kind"] for row in job.result["checks"]]
    check("every_object_is_checked_and_the_values_once", kinds.count("material_assignment") == 4
          and kinds.count("material_values") == 1)
    undo()
    check("also_one_undo", "QA Deck" not in objects() and len(objects()["QA Old"].material_slots) == 1
          and not named("QA Stone") and no_stages())
    redo()
    check("also_redo", material_of("QA Old", 1) == material_of("QA Deck") and named("QA Stone") == ["QA Stone"])
    deck = objects()["QA Deck"]
    job = committed("assign_also", [primitive("QA Step 1", "one", 29), primitive("QA Step 2", "two", 31),
                                    op("material_assign", "$one", material="QA Stone", source=handle(deck), also=["$two"])],
                    reads=[deck])
    check("material_assign_also_shares_the_live_datablock", material_of("QA Step 1") == material_of("QA Deck")
          and material_of("QA Step 2") == material_of("QA Deck") and named("QA Stone") == ["QA Stone"])
    old = objects()["QA Old"]
    refused("also_is_not_for_a_material_edited_in_place", [
        op("material_principled", deck, name="QA Stone", inputs={"Base Color": RED}, shared="ALL_USERS",
           also=[handle(old)])], "ALSO_NEW_MATERIAL", writes=[deck, old])
    # A forced commit failure: the declared also object gets its slots back.
    pillar, fingerprint = objects()["QA Pillar"], native._fingerprint(old)
    original = native.BlenderProgramBackend._apply_collection_intents

    def fail_after(self, stage, journal):
        original(self, stage, journal)
        raise RuntimeError("injected failure after the material transfer")

    job, _ = run(program([primitive("QA Slab", "slab", 33),
                          op("material_principled", "$slab", name="QA Slate", inputs={"Base Color": RED},
                             also=[handle(old), handle(pillar)])], writes=[old, pillar]))
    with patch.object(native.BlenderProgramBackend, "_apply_collection_intents", fail_after):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("also_rollback_restores_slots_names_and_scene", job.result.get("status") == "failed"
          and not job.result.get("partial_changes_possible") and native._fingerprint(objects()["QA Old"]) == fingerprint
          and len(objects()["QA Old"].material_slots) == 2 and len(objects()["QA Pillar"].material_slots) == 1
          and "QA Slab" not in objects() and not named("QA Slate") and no_stages())
    # A stale binding of an also object refuses the whole program.
    stale = program([primitive("QA Slab", "slab", 33),
                     op("material_principled", "$slab", name="QA Slate", inputs={"Base Color": RED},
                        also=[handle(old)])], writes=[old])
    old.pass_index = 5
    queries.invalidate(window)
    job, result = run(stale)
    check("a_stale_also_object_is_refused", result.get("status") == "failed" and "revision changed" in result.get("error", "")
          and "QA Slab" not in objects() and no_stages())
    old.pass_index = 0
    queries.invalidate(window)
    undo()
    undo()
    check("also_two_more_undos", "QA Step 1" not in objects() and "QA Deck" not in objects() and no_stages())


def also_objects_render_the_material():
    """An also object renders the material it is given (1.0.5 A/B: also gave a slot and moved no face)."""
    committed("render_src", [primitive("QA Src", "src", 43),
                             op("material_principled", "$src", name="QA Grain", inputs={"Base Color": IRON})])
    src = objects()["QA Src"]
    # Objects that already have another material (1.0.5 A/B kitchen: cabinets in primer given the first one's walnut).
    committed("primed", [primitive("QA Cab 1", "cab1", 33), primitive("QA Cab 2", "cab2", 35),
                         op("material_principled", "$cab1", name="QA Primer", inputs={"Base Color": BLUE},
                            also=["$cab2"])])
    cabs = [objects()["QA Cab 1"], objects()["QA Cab 2"]]
    job = committed("primed_also", [op("material_assign", cabs[0], material="QA Grain", source=handle(src),
                                       also=[handle(cabs[1])])], reads=[src], writes=cabs)
    cabs = [objects()["QA Cab 1"], objects()["QA Cab 2"]]
    check("primed_objects_render_the_assigned_material", all(
        cab.material_slots[polygon.material_index].material == material_of("QA Src")
        for cab in cabs for polygon in cab.data.polygons) and [len(cab.material_slots) for cab in cabs] == [2, 2])
    check("primed_also_checks_pass_on_rendered_faces", job.result.get("status") == "committed" and all(
        row["checks"][0].get("observed", {}).get("rendered_faces", 0) > 0 for row in job.result["checks"]
        if row["checks"][0]["kind"] == "material_assignment"))
    # An also object that already renders the live material (staging gives it its own private copy) keeps its
    # faces and its other materials: one Walnut slot, the brass handle kept (break-it review of this fix).
    committed("held_setup", [primitive("QA Held A", "ha", 37), primitive("QA Held B", "hb", 39),
                             op("material_principled", "$ha", name="QA Walnut", inputs={"Base Color": IRON},
                                also=["$hb"]),
                             op("material_principled", "$hb", name="QA Brass", inputs={"Base Color": RED},
                                face_indices=[0, 1])])
    held_a, held_b = objects()["QA Held A"], objects()["QA Held B"]
    before = [polygon.material_index for polygon in held_b.data.polygons]
    job = committed("held_also", [primitive("QA Held C", "hc", 41),
                                  op("material_assign", "$hc", material="QA Walnut", source=handle(held_a),
                                     also=[handle(held_b)])], reads=[held_a], writes=[held_b])
    held_b = objects()["QA Held B"]
    check("held_also_keeps_one_slot_and_the_other_materials",
          [slot.material.name for slot in held_b.material_slots] == ["QA Walnut", "QA Brass"]
          and [polygon.material_index for polygon in held_b.data.polygons] == before
          and material_of("QA Held C") == material_of("QA Held A") == material_of("QA Held B"))
    # The bug's residue: an object holding a material in a slot no face uses (fresh break-it review).
    committed("unused_setup", [primitive("QA Unused", "un", 45), primitive("QA Bool", "bo", 47),
                               op("material_principled", "$un", name="QA Glass", inputs={"Base Color": BLUE}),
                               op("material_principled", "$un", name="QA Body", inputs={"Base Color": RED}),
                               op("material_principled", "$bo", name="QA Glass"),
                               op("material_principled", "$bo", name="QA Body")])
    unused, boolean = objects()["QA Unused"], objects()["QA Bool"]
    boolean.modifiers.new("Cut", "BOOLEAN").object = objects()["QA Src"]
    queries.invalidate(window)
    check("unused_setup_leaves_glass_unused", [slot.material.name for slot in unused.material_slots] == ["QA Glass", "QA Body"]
          and all(polygon.material_index == 1 for polygon in unused.data.polygons))
    # An in-place value edit of the unused material moves no face and commits (it is no placement).
    committed("unused_edit", [op("material_principled", handle(unused), name="QA Glass", inputs={"Roughness": .05},
                                 shared="ALL_USERS")], writes=[unused])
    unused = objects()["QA Unused"]
    check("an_in_place_edit_keeps_the_faces", all(polygon.material_index == 1 for polygon in unused.data.polygons))
    # A name-only restatement gives its target every face on that slot, as also does: the object then shows it.
    committed("unused_keep", [op("material_principled", handle(unused), name="QA Glass")], writes=[unused])
    unused = objects()["QA Unused"]
    check("a_name_only_restatement_renders_the_unused_material",
          all(polygon.material_index == 0 for polygon in unused.data.polygons))
    # An also object whose Boolean decides what renders still gets its base faces on the held unused material.
    committed("unused_also", [op("material_assign", handle(unused), material="QA Glass", also=[handle(boolean)])],
              writes=[unused, boolean], reads=[objects()["QA Src"]])   # The Boolean's operand is a dependency.
    boolean = objects()["QA Bool"]
    glass_slots = [index for index, slot in enumerate(boolean.material_slots) if slot.material.name == "QA Glass"]
    check("an_also_object_with_a_boolean_gets_its_base_faces", len(glass_slots) == 1 and all(
        polygon.material_index == glass_slots[0] for polygon in boolean.data.polygons))
    # A stale face index past the last slot (an applied Set Material Index, a join, an import) never reads as the
    # new slot an also object gets: every face goes to it (fresh break-it review, round 2).
    committed("stale_setup", [primitive("QA Stale", "st", 49),
                              op("material_principled", "$st", name="QA Primer Two", inputs={"Base Color": BLUE})])
    stale = objects()["QA Stale"]
    stale.data.polygons[0].material_index = 3
    stale.data.update()
    queries.invalidate(window)
    committed("stale_also", [op("material_assign", handle(unused), material="QA Glass", also=[handle(stale)])],
              writes=[unused, stale])
    stale = objects()["QA Stale"]
    check("a_stale_index_never_reads_as_the_new_slot", [slot.material.name for slot in stale.material_slots]
          == ["QA Primer Two", "QA Glass"] and all(polygon.material_index == 1 for polygon in stale.data.polygons))
    check("also_render_no_stages", no_stages())


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    one_material_in_one_program()
    assign_and_copy_between_objects()
    edit_for_all_users()
    instances_keep_their_mesh()
    recovery()
    users_outside_the_program()
    property_paths_of_a_shared_material()
    linked_orphan_and_slot_paths()
    one_statement_for_several_objects()
    also_objects_render_the_material()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "material-sharing.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_MATERIAL_SHARING", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
