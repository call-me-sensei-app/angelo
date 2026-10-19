# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete host checks through real staging, preview Accept and UNDO commit.

Run an existing full binary serially with --background --factory-startup
--disable-autoexec --python-exit-code 1 --python this_file. Each case owns a
disposable scene. Fault hooks change real staged RNA; the production validator
always runs. No model, provider, network, build or artist project is involved.
The five-trial diagnostics include admission, cooperative staging and commit;
setup and disposal are outside those timings. They are measurements, not a UI
latency guarantee. Set ANGELO_QA_DIR to retain the JSON evidence. Separate
correctness and idle-machine timings with ANGELO_COMPLETE_PHASE (default all).
"""

import copy
import json
import os
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv, "Use isolated headless factory startup"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-complete-checks-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_checks, scene_queries as queries  # noqa: E402
from angelo_agent.action_recipes import execute as expand_recipe  # noqa: E402
from angelo_agent.node_edit_spec import MAX_TREE_NODES  # noqa: E402
from angelo_agent.scene_validation import execute as verify_public  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

window = bpy.context.window_manager.windows[0]
phase = os.environ.get("ANGELO_COMPLETE_PHASE", "all")
assert phase in {"all", "correctness", "performance"}, phase
trials = int(os.environ.get("ANGELO_COMPLETE_TRIALS", "5"))
assert trials in {1, 5}, trials
journal_sampling = os.environ.get("ANGELO_COMPLETE_JOURNAL") == "1"
factory_scene, factory_cube = window.scene, window.scene.objects["Cube"]
report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "paid_requests": 0, "phase": phase,
          "checks": {}, "cases": {}, "failures": {}, "trials_per_workload": trials,
          "journal_sampling": journal_sampling, "timing_eligible": not journal_sampling,
          "timing_scope": "serial; setup/disposal excluded; real validators and UNDO operator commits",
          "concurrent_native_jobs": os.environ.get("ANGELO_QA_CONCURRENT_NATIVE_JOBS", "unverified")}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def encoded_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def main_ids():
    collections = (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
                   bpy.data.scenes, bpy.data.collections, bpy.data.actions, bpy.data.armatures,
                   bpy.data.cameras, bpy.data.lights, bpy.data.worlds)
    return [block for collection in collections for block in collection]


@contextmanager
def isolated_scene(name):
    """Remove only this case's allocations, including any failed fixture setup."""
    before = {block.as_pointer() for block in main_ids()}
    scene = bpy.data.scenes.new("Complete checks " + name)
    window.scene = scene
    queries.invalidate()
    try:
        yield scene
    finally:
        owner = native.WRITER.owner
        if owner is not None and not owner.closed:
            owner.cancel()
        native.PREVIEWS.clear()
        window.scene = factory_scene
        allocated = [block for block in main_ids() if block.as_pointer() not in before]
        bpy.data.batch_remove(ids=allocated)
        queries.invalidate()


def cube(name):
    obj = factory_cube.copy()
    obj.data = factory_cube.data.copy()
    obj.name = name
    obj.data.materials.clear()
    obj.animation_data_clear()
    obj.modifiers.clear()
    window.scene.collection.objects.link(obj)
    window.view_layer.objects.active = obj
    obj.select_set(True)
    window.view_layer.update()
    return obj


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def arguments(operations, *, writes=(), reads=(), mode="APPLY", checks=()):
    window.view_layer.update()
    rows = lambda objects: [{"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}
                            for obj in objects]
    return {"mode": mode, "read_set": rows(reads), "write_set": rows(writes),
            "operations": operations, "expected_checks": list(checks)}


def live_snapshot():
    window.view_layer.update()
    return {"objects": [(obj.name, obj.as_pointer(), native._fingerprint(obj)) for obj in window.scene.objects],
            "ids": sorted(block.as_pointer() for block in main_ids()),
            "active": window.view_layer.objects.active.as_pointer() if window.view_layer.objects.active else None,
            "selected": sorted(obj.as_pointer() for obj in window.scene.objects if obj.select_get()),
            "frame": (window.scene.frame_current, window.scene.frame_subframe)}


def clean_boundary(name):
    check(name + "_no_hidden_stage", no_stages() and not native.runtime_is_staging())
    check(name + "_writer_released", native.WRITER.owner is None)
    check(name + "_no_retained_preview", not native.PREVIEWS.records)


def settle(job):
    for _ in range(4096):
        result = job.poll()
        assert no_stages() and not native.runtime_is_staging(), "A cooperative yield retained Main stage IDs"
        if result.get("final") or result.get("ready_to_commit"):
            return result
    job.cancel()
    raise AssertionError("Native program did not settle within bounded polling")


def commit(job):
    status = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    result = job.result
    assert ("FINISHED" in status) == bool(result.get("success")), result
    return result


class ValidatorTrace:
    """Observe real validators, optionally corrupting staged data at one boundary."""

    def __init__(self, *, fault=None, boundary=None):
        self.phase, self.fault, self.boundary = "stage", fault, boundary
        self.injected = False
        self.rows, self.milliseconds = defaultdict(list), defaultdict(float)
        self.journal = {"serialized_payload_highwater_bytes": 0, "entries_highwater": 0, "owner_edges_highwater": 0,
                        "runtime_identity_entries_highwater": 0, "runtime_clone_entries_highwater": 0,
                        "runtime_copy_source_entries_highwater": 0, "samples": 0,
                        "scope": "Serialized provenance payload and runtime entry counts; not Python or native memory"}

    def sample_journal(self, stage, phase):
        from angelo_agent.program_material_facts import REGISTRY
        values = {key: stage.extras.get(key, {}) for key in (REGISTRY, "obligation_clones")}
        records = list(values[REGISTRY].values()) + list(values["obligation_clones"])
        observed = {"serialized_payload_highwater_bytes": encoded_bytes(values),
                    "entries_highwater": len(records),
                    "owner_edges_highwater": sum(len(row.get("owners", ())) for row in records)}
        context = getattr(stage, "context", None) or getattr(getattr(stage, "resident", None), "context", None)
        observed.update({name + "_highwater": len(getattr(context, field, {})) for name, field in
                         (("runtime_identity_entries", "_obligation_ids"), ("runtime_clone_entries", "_obligation_clone_ids"),
                          ("runtime_copy_source_entries", "_obligation_copy_sources"))})
        for key, value in observed.items():
            if value > self.journal[key]:
                self.journal[key] = value
                self.journal[key + "_phase"] = phase
        self.journal["samples"] += 1

    def corrupt(self, stage):
        if self.fault == "last_location":
            stage.context.resolve("$b31").location.x += 9
        elif self.fault == "last_also":
            stage.context.resolve("$b30").data.materials.clear()
        else:
            raise AssertionError("Unknown RNA fault")
        self.injected = True

    @contextmanager
    def installed(self):
        original_apply, original_validate = native.BlenderProgramBackend.apply, native.BlenderProgramBackend.validate

        def applied(backend, stage, operation):
            outcome = original_apply(backend, stage, operation)
            final_operation = self.fault == "last_location" and operation["parameters"].get("alias") == "b31"
            if self.boundary == "stage" and final_operation and not self.injected:
                self.corrupt(stage)
            return outcome

        def validated(backend, stage, predicate):
            # Late-target faults must reach that exact mandatory check. Earlier
            # material corruption is independently caught by provenance at a yield.
            at_late_material = (self.fault == "last_also" and predicate["target"] == "$b30"
                                and predicate["kind"] == "material_assignment")
            at_boundary = self.fault == "last_location" and self.phase != "stage"
            if (self.boundary == self.phase and (at_late_material or at_boundary)
                    and not self.injected):
                self.corrupt(stage)
            started = time.perf_counter()
            outcome = original_validate(backend, stage, predicate)
            self.milliseconds[self.phase] += (time.perf_counter() - started) * 1000
            self.rows[self.phase].append({"predicate": copy.deepcopy(predicate), "passed": outcome.get("passed"),
                                          "outcome": outcome})
            return outcome

        with ExitStack() as stack:
            stack.enter_context(patch.object(native.BlenderProgramBackend, "apply", applied))
            stack.enter_context(patch.object(native.BlenderProgramBackend, "validate", validated))
            if journal_sampling:
                from angelo_agent import program_material_facts
                for name in ("initialize_clones", "finish_operation", "snapshot_provenance", "restore_provenance", "strip_provenance"):
                    original = getattr(program_material_facts, name)
                    def sampled(stage, *args, original=original, name=name, **kwargs):
                        self.sample_journal(stage, name + ":before")
                        outcome = original(stage, *args, **kwargs)
                        self.sample_journal(stage, name + ":after")
                        return outcome
                    stack.enter_context(patch.object(program_material_facts, name, sampled))
            yield self

    def assert_complete(self, name, phase, expected):
        rows = self.rows[phase]
        check(name + "_" + phase + "_all_predicates", [row["predicate"] for row in rows] == expected)
        check(name + "_" + phase + "_all_passed", all(row["passed"] for row in rows))

    def summary(self):
        return {phase: {"count": len(rows), "passed": sum(bool(row["passed"]) for row in rows),
                        "validator_ms": round(self.milliseconds[phase], 3),
                        "failed": [row for row in rows if not row["passed"]]}
                for phase, rows in self.rows.items()}


def receipt(job, result, trace, request, started, staged_at, committed_at, commit_ms=0):
    result_file = job.id + "-result.json"
    (OUT / result_file).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return {"status": result.get("status"), "error": result.get("error"),
            "result_file": result_file,
            "authored_checks": len(job.program["expected_checks"]), "required_checks": len(job.checks),
            "result_check_count": len(result.get("checks", [])), "checks_digest": job.checks_digest,
            "request_json_bytes": encoded_bytes(request), "required_checks_json_bytes": encoded_bytes(job.checks),
            "binding_facts_json_bytes": len(job.obligations.facts_encoded) if job.obligations else 0,
            "effect_rules_json_bytes": len(job.obligations.effects_encoded) if job.obligations else 0,
            "unknowns_json_bytes": len(job.obligations.unknown_encoded) if job.obligations else 0,
            **({"provenance_journal": trace.journal} if journal_sampling else {}),
            "result_json_bytes": encoded_bytes(result), "result_checks_json_bytes": encoded_bytes(result.get("checks", [])),
            "stage_ms": round((staged_at - started) * 1000, 3),
            "stage_verification_ms": round(job.verification_ms, 3),
            "commit_ms": round(commit_ms, 3),
            "wall_ms": round((committed_at - started) * 1000, 3), "validators": trace.summary()}


def successful(name, request, count=None):
    before = live_snapshot()
    started = time.perf_counter()
    with ValidatorTrace().installed() as trace:
        job = native.begin(window, "angelo_scene_program", request)
        result = settle(job)
        staged_at = time.perf_counter()
        expected = job.obligations.checks() if job.obligations else []
        check(name + "_authored_preserved", job.program["expected_checks"] == request["expected_checks"])
        check(name + "_frozen_complete_checks", job.checks == expected)
        if count is not None:
            check(name + "_required_count", len(expected) == count)
        diagnostic = {"stage_status": result.get("status"), "stage_error": result.get("error"),
                      "stage_result_json_bytes": encoded_bytes(result)}
        report.setdefault("native_outcomes", {})[name] = diagnostic
        check(name + "_stage_ready", result.get("ready_to_commit"))
        check(name + "_stage_keeps_live_scene", live_snapshot() == before)
        trace.assert_complete(name, "stage", expected)
        trace.phase = "precommit"
        commit_started = time.perf_counter()
        result = commit(job)
        committed_at = time.perf_counter()
        diagnostic.update(commit_status=result.get("status"), commit_error=result.get("error"))
        check(name + "_committed", result.get("success") and result.get("undoable"))
        trace.assert_complete(name, "precommit", expected)
        check(name + "_complete_result", len(result["checks"]) == len(expected)
              and all(row.get("passed") for row in result["checks"]))
    clean_boundary(name)
    return receipt(job, result, trace, request, started, staged_at, committed_at,
                   (committed_at - commit_started) * 1000)


def creation_request(material=False, mode="APPLY"):
    operations = [op("create_primitive", primitive="CUBE", name="Complete Box %02d" % index, alias="b%d" % index)
                  for index in range(31 if material else 32)]
    if material:
        operations.append(op("material_principled", "$b0", name="Complete Copper",
                             also=["$b%d" % index for index in range(1, 31)],
                             inputs={"Base Color": [.8, .3, .1, 1], "Metallic": .9, "Roughness": .3}))
    return arguments(operations, mode=mode)


def creation_success(material):
    result = successful("creation_material" if material else "creation_cubes", creation_request(material), 125 if material else 96)
    count = 31 if material else 32
    check("created_objects_" + str(count), len(window.scene.objects) == count)
    if material:
        check("last_also_live_assignment", window.scene.objects["Complete Box 30"].data.materials[0].name == "Complete Copper")
    else:
        check("last_cube_live_location", list(window.scene.objects["Complete Box 31"].location) == [0, 0, 0])
    return result


def injected_failure(fault, boundary):
    name = fault + "_" + boundary
    cube("Unchanged sentinel")
    request = creation_request(fault == "last_also", "PREVIEW" if boundary == "accept" else "APPLY")
    before = live_snapshot()
    started = time.perf_counter()
    commit_ms = 0
    with ValidatorTrace(fault=fault, boundary=boundary).installed() as trace:
        job = native.begin(window, "angelo_scene_program", request)
        expected = copy.deepcopy(job.checks)
        result = settle(job)
        staged_at = time.perf_counter()
        if boundary == "accept":
            check(name + "_retained", result.get("status") == "preview")
            retained = native.PREVIEWS.records[result["preview_id"]]
            check(name + "_retained_complete_digest", retained["checks"] == expected
                  and retained["checks_digest"] == program_checks.checks_digest(expected))
            trace.assert_complete(name, "stage", expected)
            check(name + "_preview_keeps_live", live_snapshot() == before and no_stages())
            trace.phase = "accept"
            job = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
            check(name + "_accepted_complete_checks", job.checks == expected)
            result = settle(job)
        elif boundary == "precommit":
            check(name + "_ready_before_corruption", result.get("ready_to_commit"))
            trace.assert_complete(name, "stage", expected)
            trace.phase = "precommit"
            commit_started = time.perf_counter()
            result = commit(job)
            commit_ms = (time.perf_counter() - commit_started) * 1000
        finished = time.perf_counter()
        rows = trace.rows[boundary]
        report.setdefault("fault_traces", {})[name] = {"result": result, "rows": rows,
                                                       "required_count": len(expected)}
        check(name + "_real_fault_injected", trace.injected)
        check(name + "_failed", result.get("status") == "failed" and not result.get("scene_changed")
              and not result.get("partial_changes_possible"))
        target, kind = ("$b31", "transform") if fault == "last_location" else ("$b30", "material_assignment")
        check(name + "_late_predicate_reached", len(rows) > 32 and not rows[-1]["passed"]
              and rows[-1]["predicate"]["target"] == target and rows[-1]["predicate"]["kind"] == kind)
        if fault == "last_also":
            check(name + "_exact_material_identity_refused", any(
                row.get("kind") == "material_identity" and row.get("passed") is False
                for row in rows[-1]["outcome"].get("checks", [])))
        check(name + "_live_unchanged", live_snapshot() == before)
    clean_boundary(name)
    return receipt(job, result, trace, request, started, staged_at, finished, commit_ms)


RECIPE_CASES = {
    "bevel-and-shade": {"width": .1, "segments": 2},
    "fit-dimensions": {"dimensions": [4, 1, 3], "minimum_scale": .1, "maximum_scale": 4, "maximum_scale_ratio": 2},
    "clean-mesh": {"vertex_indices": [0, 1], "distance": .001, "manifold": "CLOSED"},
    "material-setup": {"name": "Recipe Copper", "base_color": [.25, .5, .75, 1], "roughness": .3, "metallic": .4},
    "simple-animation": {"property": "location", "start_frame": 1, "end_frame": 12,
                         "start_value": [0, 0, 0], "end_value": [2, 3, 4], "interpolation": "LINEAR"},
    "uv-prep": {"method": "CUBE", "name": "Complete Recipe UV"},
}


def recipe_case(name):
    obj = cube("Recipe " + name)
    if name == "clean-mesh":
        obj.data.vertices[1].co = obj.data.vertices[0].co
        obj.data.update()
    target, revision = queries.object_handle(obj), queries.object_revision(window, obj)
    before = live_snapshot()
    expanded = expand_recipe(window, "angelo_expand_recipe", {
        "recipe": name, "target": target, "revision": revision, "parameters": RECIPE_CASES[name]})
    check(name + "_expansion_read_only", expanded["read_only"] and live_snapshot() == before)
    result = successful(name, expanded["program"])
    window.view_layer.update()
    verified = verify_public(window, "angelo_scene_verify", {
        "read_set": [{"handle": target, "revision": queries.object_revision(window, obj)}],
        "checks": expanded["expected_checks"]})
    check(name + "_public_live_verification", verified["passed"])
    if name == "bevel-and-shade":
        feature = len(obj.modifiers) == 2 and {mod.type for mod in obj.modifiers} == {"BEVEL", "NODES"}
    elif name == "fit-dimensions":
        feature = all(abs(value - expected) < 1e-5 for value, expected in zip(obj.dimensions, [4, 1, 3]))
    elif name == "clean-mesh":
        feature = len(obj.data.vertices) == 7
    elif name == "material-setup":
        feature = obj.data.materials[0].name == "Recipe Copper" and obj.data.materials[0].use_nodes
    elif name == "simple-animation":
        feature = obj.animation_data is not None and obj.animation_data.action is not None
    else:
        feature = obj.data.uv_layers.get("Complete Recipe UV") is not None
    check(name + "_native_feature_preserved", feature)
    result["live_verification"] = verified
    return result


def bevel_apply_scale_case():
    """bevel-and-shade apply_scale bakes the scale in the recipe's own program, keeps the shape and refuses children.

    A bevel on a scaled cube is even only when the scale is applied first; without this, models left the bevel
    stretched or spent extra rounds on transform_apply.
    """
    obj = cube("Recipe bevel apply_scale")
    obj.scale = (2, .5, 1.5)

    def world_corners():
        window.view_layer.update()
        return sorted(tuple(round(value, 5) for value in obj.matrix_world @ vertex.co) for vertex in obj.data.vertices)

    before = world_corners()
    queries.invalidate()
    target = queries.object_handle(obj)
    arguments = {"recipe": "bevel-and-shade", "target": target, "revision": queries.object_revision(window, obj),
                 "parameters": {"width": .1, "segments": 2, "apply_scale": True}}
    expanded = expand_recipe(window, "angelo_expand_recipe", arguments)
    check("bevel_apply_scale_bakes_first", [item["kind"] for item in expanded["operations"]]
          == ["transform_apply", "modifier_add", "geometry_nodes"])
    result = successful("recipe_bevel_apply_scale", expanded["program"])
    check("bevel_apply_scale_unit_scale_same_shape", tuple(obj.scale) == (1, 1, 1) and world_corners() == before
          and sorted(modifier.type for modifier in obj.modifiers) == ["BEVEL", "NODES"])
    verified = verify_public(window, "angelo_scene_verify", {
        "read_set": [{"handle": target, "revision": queries.object_revision(window, obj)}],
        "checks": expanded["expected_checks"]})
    check("bevel_apply_scale_public_live_verification", verified["passed"])
    parent = cube("Recipe bevel apply_scale parent")
    parent.scale = (2, 1, 1)
    child = cube("Recipe bevel apply_scale child")
    child.parent = parent
    window.view_layer.update()
    queries.invalidate()
    try:
        expand_recipe(window, "angelo_expand_recipe", dict(arguments, target=queries.object_handle(parent),
                                                           revision=queries.object_revision(window, parent)))
        refusal = ""
    except Exception as error:  # RefusalError, before any approval or stage.
        refusal = str(error)
    check("bevel_apply_scale_children_refused_before_approval", "CHILDREN" in refusal
          and tuple(parent.scale) == (2, 1, 1) and no_stages())
    return result


def bevel_apply_scale_review_case():
    """apply_scale edge cases, in the compiled app.

    A mirrored scale with a keyed rotation is baked as Blender's Apply Scale bakes it: the rotation channels and
    their keys stay, faces still point outward, and the next frame shows the same object. apply_scale refuses,
    before approval, a modifier that measures in object space (Solidify, Array, Hook, Multires), a parent's
    scale and a delta scale at unit scale, instead of certifying a stretched or changed result.
    """
    def world_corners(obj):
        window.view_layer.update()
        return sorted(tuple(round(value, 4) for value in obj.matrix_world @ vertex.co) for vertex in obj.data.vertices)

    def outward(obj):
        window.view_layer.update()
        matrix = obj.matrix_world
        normals = matrix.to_3x3().inverted().transposed()
        center = matrix @ (sum((vertex.co for vertex in obj.data.vertices), obj.data.vertices[0].co * 0)
                           / len(obj.data.vertices))
        return all((normals @ polygon.normal).dot(matrix @ polygon.center - center) > 0 for polygon in obj.data.polygons)

    def expand(obj):
        window.view_layer.update()
        queries.invalidate()
        return expand_recipe(window, "angelo_expand_recipe", {
            "recipe": "bevel-and-shade", "target": queries.object_handle(obj),
            "revision": queries.object_revision(window, obj),
            "parameters": {"width": .1, "segments": 2, "apply_scale": True}})

    def refusal(obj):
        try:
            expand(obj)
        except Exception as error:  # RefusalError, before any approval or stage.
            return str(error)
        return ""

    mirrored = cube("Recipe mirrored apply_scale")
    mirrored.scale, mirrored.rotation_euler = (-2, 1, 1), (0, 0, .5)
    mirrored.keyframe_insert("rotation_euler", frame=1)
    mirrored.keyframe_insert("rotation_euler", frame=20)
    window.scene.frame_set(1)
    before, faces_out = world_corners(mirrored), outward(mirrored)
    expanded = expand(mirrored)
    check("mirrored_apply_scale_bakes_scale_only", expanded["operations"][0]["parameters"]
          == {"location": False, "rotation": False, "scale": True})
    result = successful("recipe_bevel_apply_mirrored_scale", expanded["program"])
    check("mirrored_apply_scale_keeps_rotation_channels", tuple(round(v, 6) for v in mirrored.rotation_euler)
          == (0, 0, .5) and tuple(mirrored.scale) == (1, 1, 1))
    check("mirrored_apply_scale_keeps_world_shape_and_outward_faces", faces_out and world_corners(mirrored) == before
          and outward(mirrored))
    window.scene.frame_set(10)
    check("mirrored_apply_scale_next_frame_same_object", world_corners(mirrored) == before)
    window.scene.frame_set(1)
    for kind in ("SOLIDIFY", "ARRAY", "HOOK", "MULTIRES"):
        obj = cube("Recipe apply_scale " + kind.title())
        obj.scale = (2, 1, 1)
        obj.modifiers.new(kind.title(), kind)
        check("apply_scale_refuses_" + kind.lower(), "MODIFIERS" in refusal(obj) and tuple(obj.scale) == (2, 1, 1))
    parent = cube("Recipe apply_scale scaled parent")
    parent.scale = (3, 1, 1)
    child = cube("Recipe apply_scale child of a scaled parent")
    child.parent = parent
    for own in ((1, 1, 1), (2, .5, 1.5)):
        child.scale = own
        check("apply_scale_refuses_parent_scale_%s" % "_".join("%g" % v for v in own), "PARENT_SCALE" in refusal(child))
    delta = cube("Recipe apply_scale delta scale")
    delta.delta_scale = (2, 1, 1)
    check("apply_scale_refuses_delta_scale_at_unit_scale", "DELTA_TRANSFORMS" in refusal(delta) and no_stages())
    return result


def ordinary_workload():
    return arguments([op("create_primitive", primitive="CUBE", name="Ordinary Cube", alias="ordinary"),
                      op("transform", "$ordinary", location=[2, 3, 4]),
                      op("material_principled", "$ordinary", name="Ordinary Material")]), None


def material_workload():
    targets = [cube("Material Target %02d" % index) for index in range(32)]
    handles = [queries.object_handle(obj) for obj in targets]
    operations = [op("material_principled", handles[0], name="Complete Material %02d" % index,
                     also=handles[1:]) for index in range(32)]
    return arguments(operations, writes=targets), 1056


def material_cap_workload():
    targets = [cube("Material Cap Target %02d" % index) for index in range(63)]
    handles = [queries.object_handle(obj) for obj in targets]
    operations = [op("material_principled", handles[index], name="Cap Material %02d" % index,
                     also=handles[32:]) for index in range(32)]
    authored = [{"kind": "object_count", "minimum": 63, "maximum": 63, "types": []} for _ in range(32)]
    return arguments(operations, writes=targets, checks=authored), 1088


def batch_workload():
    sources = [cube("Batch Source %02d" % index) for index in range(8)]
    for index, obj in enumerate(sources):
        obj.location.y = index * 3
    operation = op("batch", sources=[queries.object_handle(obj) for obj in sources], linked=True,
                   array=[{"step": [3, 0, 0], "count": 129}])
    return arguments([operation], writes=sources), 8


def parent_workload():
    obj = cube("Parent Graph Target")
    material = bpy.data.materials.new("Complete Parent Material")
    material.use_nodes = True
    tree = material.node_tree
    frame = tree.nodes.new("NodeFrame")
    frame.name = "Frame"
    for index in range(MAX_TREE_NODES - len(tree.nodes)):
        tree.nodes.new("ShaderNodeValue").name = "Member %03d" % index
    names = [node.name for node in tree.nodes if node != frame]
    obj.data.materials.append(material)
    edits = [{"op": "parent", "nodes": names[start:start + 32], "frame": "Frame"}
             for start in range(0, len(names), 32)]
    operation = op("node_edit", queries.object_handle(obj), material=material.name, edits=edits)
    return arguments([operation], writes=[obj]), 8


def workload_trials(name, factory):
    samples = []
    for trial in range(trials):
        label = name + "_%d" % trial
        with isolated_scene(label):
            request, expected_count = factory()
            samples.append(successful(label, request, expected_count))
            if name == "batch_1024":
                check(label + "_all_copies_committed", len(window.scene.objects) == 1032)
                check(label + "_linked_meshes_preserved", len({obj.data.as_pointer() for obj in window.scene.objects}) == 8)
            elif name == "parent_256":
                tree = window.scene.objects["Parent Graph Target"].data.materials[0].node_tree
                check(label + "_every_parent_committed", len(tree.nodes) == MAX_TREE_NODES
                      and all(node.parent == tree.nodes["Frame"] for node in tree.nodes if node.name != "Frame"))
            elif name == "material_dense":
                check(label + "_all_32_materials_retained", all(len(obj.material_slots) == 32 for obj in window.scene.objects))
            elif name == "material_1088":
                check(label + "_all_primary_and_also_assignments", all(
                    len(window.scene.objects["Material Cap Target %02d" % index].material_slots) == (1 if index < 32 else 32)
                    for index in range(63)))
        print("COMPLETE_CHECKS_TRIAL " + json.dumps({"workload": name, "trial": trial, **samples[-1]}), flush=True)
    return samples


def over_budget_refusal(kind):
    obj = cube("Budget sentinel")
    target = queries.object_handle(obj)
    # Generic closed RNA values are syntactically legal. Their complete host
    # predicates must be refused before any installed RNA path lookup.
    value, operation_count, limit = ("x" * 1024, 1, "1048576") if kind == "bytes" else ([0] * 32, 4, "131072")
    settings = {"setting_%02d" % index: value for index in range(32)}
    edits = [{"op": "set", "node": "Node %02d" % index, "settings": settings} for index in range(32)]
    request = arguments([op("node_edit", target, material="Budget Material %02d" % index, edits=edits)
                         for index in range(operation_count)], writes=[obj])
    before, allocations = live_snapshot(), []
    original = native.BlenderProgramBackend.create_stage

    def create_stage(backend, binding):
        allocations.append(True)
        return original(backend, binding)

    started = time.perf_counter()
    with patch.object(native.BlenderProgramBackend, "create_stage", create_stage):
        try:
            native.begin(window, "angelo_scene_program", request)
        except ValueError as error:
            message = str(error)
        else:
            raise AssertionError("Over-budget complete predicates were admitted")
    check("actual_" + kind + "_budget_refusal", "PROGRAM_CHECK_BUDGET" in message and limit in message
          and "split" in message and not allocations)
    check(kind + "_budget_refusal_live_unchanged", live_snapshot() == before)
    clean_boundary(kind + "_budget_refusal")
    return {"error": message, "request_json_bytes": encoded_bytes(request),
            "wall_ms": round((time.perf_counter() - started) * 1000, 3), "stage_allocations": len(allocations)}


def run_case(name, callback):
    try:
        with isolated_scene(name):
            report["cases"][name] = callback()
    except Exception:
        report["failures"][name] = traceback.format_exc()
    (OUT / "complete-checks.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("COMPLETE_CHECKS_CASE " + json.dumps({"case": name, "passed": name not in report["failures"]}), flush=True)


bpy.context.preferences.filepaths.file_preview_type = "NONE"
CommitOperator = commit_operator(report)
bpy.utils.register_class(CommitOperator)
try:
    if phase != "performance":
        run_case("creation_96", lambda: creation_success(False))
        run_case("creation_125", lambda: creation_success(True))
        for fault in ("last_location", "last_also"):
            for boundary in ("stage", "precommit", "accept"):
                run_case(fault + "_" + boundary, lambda fault=fault, boundary=boundary: injected_failure(fault, boundary))
        for name in RECIPE_CASES:
            run_case("recipe_" + name, lambda name=name: recipe_case(name))
        run_case("recipe_bevel_apply_scale", bevel_apply_scale_case)
        run_case("recipe_bevel_apply_scale_review", bevel_apply_scale_review_case)
        for kind in ("bytes", "scalars"):
            run_case("over_budget_" + kind, lambda kind=kind: over_budget_refusal(kind))
    if phase != "correctness":
        for name, factory in (("ordinary_3", ordinary_workload), ("material_dense", material_workload),
                              ("material_1088", material_cap_workload),
                              ("batch_1024", batch_workload), ("parent_256", parent_workload)):
            run_case("timing_" + name, lambda name=name, factory=factory: workload_trials(name, factory))
    elif os.environ.get("ANGELO_CAPTURE_LARGE_RESULT") == "1":
        run_case("capture_material_1088", lambda: successful("capture_material_1088", material_cap_workload()[0], 1088))
finally:
    bpy.utils.unregister_class(CommitOperator)
    report["all_passed"] = not report["failures"] and all(report["checks"].values())
    (OUT / "complete-checks.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("COMPLETE_CHECKS " + json.dumps({"all_passed": report["all_passed"], "checks": report["checks"],
                                          "failures": report["failures"]}), flush=True)
assert report["all_passed"], report["failures"]
