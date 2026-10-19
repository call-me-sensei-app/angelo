# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derived checks on real staged data: model-style programs, real kernels, real commits.

Each program is normalized by ``tool_specs.validate_call`` (as a model's plan is),
then its complete host obligations are derived separately, staged, verified on the
stage, verified again before commit and committed through an Undo operator.
Every derived check must pass on the real read-back (float32 storage, real
mesh bounds, real modifier and material properties). Run only in a disposable
``--factory-startup --background`` instance. No providers or paid calls.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-derived-checks-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.program_checks import required_checks
from angelo_agent.derived_checks import derived_flags  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "programs": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
ProgramQACommit = commit_operator(report)


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def binding(window, objects):
    return [{"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)} for obj in objects]


def run_program(window, name, operations, writes=(), reads=()):
    """Normalize like a model's plan, stage, verify, commit; return the committed result."""
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": binding(window, reads), "write_set": binding(window, writes),
        "operations": operations})
    job = native.begin(window, "angelo_scene_program", arguments)
    staged = settle(job)
    required = job.obligations.checks()
    check(name + "_has_derived_checks", required and all(job.obligations.derived_flags))
    check(name + "_derived_checks_pass_on_the_stage", staged.get("ready_to_commit") is True)
    outcome = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = job.result
    report["programs"][name] = {"derived_checks": required,
                                "status": result.get("status"), "error": result.get("error"),
                                "checks": [check_result.get("status") for check_result in result.get("checks", [])]}
    check(name + "_commits_after_rechecking", "FINISHED" in outcome and result.get("status") == "committed")
    check(name + "_every_derived_check_passed",
          len(result["checks"]) == len(required)
          and all(item.get("passed") is True and item.get("status") == "pass" for item in result["checks"]))
    return result


CAMERA = {"kind": "camera_create", "target": None, "parameters": {
    "name": "Derived Camera", "alias": "shot", "location": [-11, 1.4, 2.6], "look_at": [11, -0.6, 1.6],
    "settings": {"lens": 24}}}
SUN = {"kind": "light_create", "target": None, "parameters": {
    "name": "Derived Sun", "alias": "sun", "location": [81.6034923452, -57.1393804843, 8.7155742748],
    "look_at": [0, 0, 0], "settings": {"light_type": "SUN", "energy": 7, "color": [1, 0.57, 0.28], "angle": 0.0174532925}}}


def renamed(operation, name):
    return dict(operation, parameters=dict(operation["parameters"], name=name))


def camera_and_light(window):
    """A new camera or light is certified by what its operation states: no check declared, none looked up."""
    result = run_program(window, "camera_and_sun", [CAMERA, SUN, {"kind": "light_create", "target": None, "parameters": {
        "name": "Derived Lamp", "alias": "lamp", "location": [0, 0, 4], "rotation": [0.3, 0, 1.1],
        "settings": {"light_type": "SPOT", "spot_size": 0.9}}}])
    ran = [row.get("kind") for row in result["checks"]]
    check("camera_and_light_checks_ran", ran.count("aim") == 2 and ran.count("camera_settings") == 1
          and ran.count("light_settings") == 2 and ran.count("object_name") == 3)
    camera = window.scene.objects["Derived Camera"]
    check("camera_committed_as_stated", abs(camera.data.lens - 24) < 1e-6 and tuple(round(v, 4) for v in camera.location)
          == (-11.0, 1.4, 2.6))


def restated_checks_change_nothing(window):
    """Restating public facts preserves every host predicate and private identity guard."""
    def operations(tag):
        return [{"kind": "create_primitive", "target": None, "parameters": {
                    "primitive": "CUBE", "name": "Restated %s" % tag, "alias": "box", "location": [3, 4, 5],
                    "scale": [.05, .05, .5]}},
                {"kind": "modifier_add", "target": "$box", "parameters": {
                    "name": "Row", "type": "ARRAY", "settings": {"count": 17, "relative_offset_displace": [15, 0, 0]}}},
                {"kind": "material_principled", "target": "$box", "parameters": {
                    "name": "Restated Iron %s" % tag, "inputs": {"Base Color": [.018, .021, .024, 1], "Metallic": .8,
                                                                 "Roughness": .36}}}]
    omitted = validate_call("angelo_scene_program", {"mode": "APPLY", "operations": operations("A")})
    public = [{key: value for key, value in row.items() if not key.startswith("__host_")}
              for row in required_checks(omitted)]
    restated = validate_call("angelo_scene_program", {
        "mode": "APPLY", "operations": operations("B"),
        "expected_checks": json.loads(json.dumps(public).replace("Restated A", "Restated B")
                                      .replace("Iron A", "Iron B"))[::-1]})
    key = lambda item: json.dumps(item, sort_keys=True).replace("Restated B", "Restated A").replace("Iron B", "Iron A")
    baseline, repeated = required_checks(omitted), required_checks(restated)
    public_key = lambda row: key({field: value for field, value in row.items() if not field.startswith("__host_")})
    check("restated_and_omitted_public_facts_are_equal",
          set(map(public_key, repeated)) == set(map(public_key, baseline)) and len(baseline) == 6)
    check("restated_checks_retain_all_host_identity_guards",
          sorted(key(row) for row in repeated if "__host_material" in row)
          == sorted(key(row) for row in baseline if "__host_material" in row)
          and len(restated["expected_checks"]) == 6 and len(repeated) == 8)
    counts = []
    for program in (omitted, restated):
        job = native.begin(window, "angelo_scene_program", program)
        check("restated_pair_stages", settle(job).get("ready_to_commit") is True)
        outcome = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        rows = job.result.get("checks", [])
        check("restated_pair_commits", "FINISHED" in outcome and all(row.get("passed") is True for row in rows))
        counts.append(sorted(row.get("kind") for row in rows))
    check("restated_and_omitted_run_every_required_check",
          counts[0] == sorted(row["kind"] for row in baseline)
          and counts[1] == sorted(row["kind"] for row in repeated))


def planted_wrong_results_fail(window):
    """A staged result that is not what the operation states fails its derived check; nothing is committed."""
    original = native.BlenderProgramBackend.apply
    cases = {
        "transform": ([{"kind": "create_primitive", "target": None, "parameters": {
            "primitive": "CUBE", "name": "Planted Cube", "alias": "box", "location": [1, 2, 3]}}],
            lambda stage: setattr(stage.context.aliases["$box"], "location", (1, 2, 3.5))),
        "material_values": ([{"kind": "create_primitive", "target": None, "parameters": {
            "primitive": "CUBE", "name": "Planted Painted", "alias": "box"}},
            {"kind": "material_principled", "target": "$box", "parameters": {
                "name": "Planted Paint", "inputs": {"Base Color": [.6, .1, .1, 1], "Roughness": .3}}}],
            lambda stage: setattr(stage.context.aliases["$box"].material_slots[0].material.node_tree.nodes[
                "Principled BSDF"].inputs["Roughness"], "default_value", .9)),
        "camera_settings": ([renamed(CAMERA, "Planted Camera")],
                            lambda stage: setattr(stage.context.aliases["$shot"].data, "lens", 50)),
        "aim": ([renamed(SUN, "Planted Sun")],
                lambda stage: setattr(stage.context.aliases["$sun"], "rotation_euler", (0, 0, 0))),
    }
    before = len(bpy.data.objects), len(bpy.data.materials)
    for kind, (operations, tamper) in cases.items():
        def tampered(self, stage, operation, tamper=tamper, last=operations[-1]):
            outcome = original(self, stage, operation)
            if operation["kind"] == last["kind"]:
                tamper(stage)
                stage.view_layer.update()
            return outcome
        program = validate_call("angelo_scene_program", {"mode": "APPLY", "operations": operations})
        native.BlenderProgramBackend.apply = tampered
        try:
            job = native.begin(window, "angelo_scene_program", program)
            result = settle(job)
        finally:
            native.BlenderProgramBackend.apply = original
        report["programs"]["planted_" + kind] = {"status": result.get("status"), "error": result.get("error")}
        check("planted_wrong_%s_fails_its_derived_check" % kind, result.get("status") == "failed"
              and ("Expected check failed: " + kind) in result.get("error", "") and not result.get("scene_changed"))
    check("planted_results_committed_nothing", (len(bpy.data.objects), len(bpy.data.materials)) == before and no_stages())


def run_checks():
    window = bpy.context.window_manager.windows[0]
    run_program(window, "named_cube", [{"kind": "create_primitive", "target": None, "parameters": {
        "primitive": "CUBE", "name": "Derived Cube", "alias": "box", "size": 0.3,
        "location": [12345.678, -0.1, 3.3], "scale": [2, 0.5, 7.25]}}])
    box = window.scene.objects["Derived Cube"]
    run_program(window, "rotated_plane", [{"kind": "create_primitive", "target": None, "parameters": {
        "primitive": "PLANE", "name": "Derived Plane", "alias": "plane", "size": 4.2,
        "rotation": [0.1, -1.2, 3.0]}}])
    run_program(window, "set_transform", [{"kind": "transform", "target": queries.object_handle(box),
                                           "parameters": {"mode": "SET", "location": [0.1, 0.2, 0.3],
                                                          "rotation": [6.2, -0.7, 1e-6], "scale": [-3, 0.01, 999.5]}}],
                writes=[box])
    run_program(window, "modifiers_and_material", [
        {"kind": "modifier_add", "target": queries.object_handle(box), "parameters": {
            "type": "BEVEL", "name": "Derived Bevel", "settings": {"width": 0.125, "segments": 3, "angle_limit": 0.7}}},
        {"kind": "modifier_add", "target": queries.object_handle(box), "parameters": {
            "type": "MIRROR", "name": "Derived Mirror", "settings": {"axes": [False, True, True], "merge_threshold": 0.002}}},
        {"kind": "modifier_add", "target": queries.object_handle(box), "parameters": {
            "type": "SOLIDIFY", "name": "Derived Solidify", "settings": {"thickness": -0.33, "offset": 0.4}}},
        {"kind": "material_principled", "target": queries.object_handle(box), "parameters": {
            "name": "Derived Material", "inputs": {"Base Color": [0.1, 0.7, 0.33, 1], "Roughness": 0.3, "Metallic": 0.9}}}],
        writes=[box])
    cube = window.scene.objects["Cube"]
    run_program(window, "parent", [{"kind": "parent_set", "target": queries.object_handle(box),
                                    "parameters": {"parent": queries.object_handle(cube)}}],
                writes=[box], reads=[cube])
    camera_and_light(window)
    restated_checks_change_nothing(window)
    planted_wrong_results_fail(window)
    check("no_stage_ids_survive", no_stages())
    report["success"] = True


def run():
    try:
        bpy.utils.register_class(ProgramQACommit)
        run_checks()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_DERIVED_CHECKS", json.dumps({"success": report.get("success"), "checks": len(report["checks"])}),
          flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()
    return None


if bpy.app.background:
    run()
else:
    bpy.app.timers.register(run, first_interval=1)
