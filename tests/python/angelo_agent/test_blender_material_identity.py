# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh material identity through real staging and the production validator.

Run in a disposable compiled app with --background --factory-startup
--disable-autoexec --python-exit-code 1. No provider calls and no commits.
Existing material-sharing/node-edit fixtures cover live UID transfer and Undo.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-material-identity-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from angelo_agent.refusal_codes import RefusalError, refusal_code  # noqa: E402
from program_qa_helpers import no_stages  # noqa: E402

RED, GREEN, BLUE = [.8, .05, .05, 1], [.05, .8, .1, 1], [.05, .1, .8, 1]
report = {"checks": {}, "cases": {}, "paid_requests": 0, "commits_attempted": 0,
          "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
factory_scene = window.scene


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    assert condition, detail or name


def main_ids():
    groups = (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups, bpy.data.scenes,
              bpy.data.collections, bpy.data.actions, bpy.data.armatures, bpy.data.cameras, bpy.data.lights, bpy.data.worlds)
    return [block for group in groups for block in group]


def snapshot():
    window.view_layer.update()
    return {"ids": sorted((block.bl_rna.identifier, block.name, block.as_pointer()) for block in main_ids()),
            "live_objects": sorted((obj.name, obj.as_pointer(), native._fingerprint(obj)) for obj in bpy.data.objects),
            "scene_objects": sorted(obj.name for obj in window.scene.objects),
            "active": window.view_layer.objects.active.as_pointer() if window.view_layer.objects.active else None,
            "selected": sorted(obj.as_pointer() for obj in window.scene.objects if obj.select_get()),
            "frame": (window.scene.frame_current, window.scene.frame_subframe)}


def material_state(stage, isolated_target=None):
    rows = {}
    for alias in ("$a", "$b", "$c"):
        obj = stage.context.aliases.get(alias)
        if alias == "$b" and isolated_target is not None:
            obj = stage.objects.get(isolated_target)
        rows[alias] = []
        for slot in (obj.material_slots if obj else ()):
            material = slot.material
            if material is None:
                continue
            shader = next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
            rows[alias].append({"name": material.name, "pointer": material.as_pointer(),
                                "rgba": [round(value, 5) for value in shader.inputs["Base Color"].default_value]
                                if shader else None,
                                "roughness": round(shader.inputs["Roughness"].default_value, 5) if shader else None,
                                "metallic": round(shader.inputs["Metallic"].default_value, 5) if shader else None})
    owned = set(stage.owned_ids) | stage.context.owned_ids
    return {"objects": rows, "material_count": sum(isinstance(block, bpy.types.Material) for block in owned)}


def operation(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def operations(policy, copied=False, isolated_target=None):
    b = isolated_target or "$b"
    result = [operation("create_primitive", primitive="CUBE", name="Identity A", alias="a", location=[-2, 0, 0]),
              operation("create_primitive", primitive="CUBE", name="Identity B", alias="b", location=[2, 0, 0]),
              operation("create_primitive", primitive="CUBE", name="Identity C", alias="c", location=[0, 3, 0]),
              operation("material_principled", "$c", name="Green", inputs={"Base Color": GREEN}),
              operation("material_principled", "$a", name="Paint", inputs={"Base Color": RED}, also=[b]),
              # Same values make this identity regression independent of final-state value derivation.
              operation("material_principled", "$b", name="Paint", inputs={"Base Color": RED}, shared=policy)]
    if isolated_target is not None:
        del result[1]  # B is a bound existing object: node_edit deliberately refuses new aliases.
        result[-1:] = [operation("node_edit", b, material="Paint", shared="ISOLATE", edits=[
                           {"op": "set", "node": "Principled BSDF", "inputs": {"Roughness": .2}}]),
                       operation("node_edit", b, material="Paint", shared="ISOLATE", edits=[
                           {"op": "set", "node": "Principled BSDF", "inputs": {"Metallic": .3}}]),
                       operation("material_assign", "$c", material="Paint", source=b),
                       operation("material_assign", b, material="Paint", also=["$a"])]
    if copied:
        result.extend([operation("material_assign", "$b", material="Paint", copy_as="Private Paint"),
                       operation("material_principled", "$b", name="Private Paint", inputs={"Base Color": BLUE})])
    return result


def _run_case(name, policy, copied, isolated):
    scene = bpy.data.scenes.new("Material identity " + name)
    window.scene = scene
    isolated_target, writes = None, []
    if isolated:
        target = factory_scene.objects["Cube"].copy()
        target.data = target.data.copy()
        target.data.materials.clear()
        target.name = "Identity B"
        scene.collection.objects.link(target)
    queries.invalidate()
    if isolated:
        isolated_target = queries.object_handle(target)
        writes = [{"handle": isolated_target, "revision": queries.object_revision(window, target)}]
    arguments = {"mode": "APPLY", "write_set": writes, "operations": operations(policy, copied, isolated_target)}
    if policy == "REFUSE":
        # An in-place edit of a material the program created and shared is refused when the program is validated,
        # before approval, with what works instead (material_rules.shared_in_program); only after staging before.
        before = snapshot()
        try:
            validate_call("angelo_scene_program", arguments)
            message, code = "", None
        except RefusalError as error:
            message, code = str(error), refusal_code(error)
        report["cases"][name] = {"refusal": {"message": message, "error_code": code}}
        check(name + "_refused_before_approval", code == "VALIDATION" and "NODE_TREE_SHARED" in message
              and "ALL_USERS" in message and "copy_as" in message, message)
        check(name + "_live_main_unchanged", before == snapshot())
        check(name + "_cleanup", no_stages() and native.WRITER.owner is None and not native.PREVIEWS.records)
        return
    request = validate_call("angelo_scene_program", arguments)
    data = report["cases"][name] = {"request": request, "applied": [], "validators": []}
    before, job = snapshot(), None
    original_apply, original_validate = native.BlenderProgramBackend.apply, native.BlenderProgramBackend.validate

    def applied(backend, stage, item):
        entry = {"kind": item["kind"], "target": item["target"]}
        try:
            outcome = original_apply(backend, stage, item)
            entry["outcome"] = outcome
            return outcome
        except Exception as error:
            entry["exception"] = {"type": type(error).__name__, "message": str(error),
                                  "error_code": refusal_code(error),
                                  "validation_refusal": isinstance(error, RefusalError)}
            raise
        finally:
            entry["state"] = material_state(stage, isolated_target)
            data["applied"].append(entry)

    def validated(backend, stage, predicate):
        outcome = original_validate(backend, stage, predicate)
        data["validators"].append({"predicate": copy.deepcopy(predicate), "outcome": outcome})
        return outcome

    try:
        with patch.object(native.BlenderProgramBackend, "apply", applied), \
                patch.object(native.BlenderProgramBackend, "validate", validated):
            job = native.begin(window, "angelo_scene_program", request)
            for _ in range(512):
                result = job.poll()
                if result.get("final") or result.get("ready_to_commit"):
                    data["result"] = copy.deepcopy(result)
                    break
            else:
                raise AssertionError("Material identity staging exceeded bounded polls")
        if not job.closed:
            data["disposal"] = job.cancel()
        check(name + "_live_main_unchanged", before == snapshot())
        check(name + "_cleanup", no_stages() and native.WRITER.owner is None and not native.PREVIEWS.records)
        check(name + "_operations_dispatched", len(data["applied"]) == len(request["operations"]),
              name + ": " + data["result"].get("error", "program stopped before all expected operations"))
        last = data["applied"][-1]
        rows = last["state"]["objects"]
        a, b, c = rows["$a"][0], rows["$b"][0], rows["$c"][0]
        if isolated:
            check(name + "_only_target_gets_private_material", a["pointer"] != b["pointer"]
                  and a["name"] == "Paint" and b["name"] != "Paint" and last["state"]["material_count"] == 3)
            check(name + "_subsequent_logical_lookup_edits_same_copy", b["roughness"] == .2 and b["metallic"] == .3
                  and a["roughness"] == .5 and a["metallic"] == 0
                  and all(entry["outcome"]["tree"]["name"] == "Paint" for entry in data["applied"]
                          if entry["kind"] == "node_edit"))
            check(name + "_explicit_source_assignment_uses_private_copy", rows["$c"][1]["pointer"] == b["pointer"])
            check(name + "_implicit_source_assignment_uses_target_copy", rows["$a"][1]["pointer"] == b["pointer"])
        else:
            check(name + "_real_aliases_remain_shared", a["pointer"] == b["pointer"] and a["name"] == b["name"] == "Paint")
        check(name + "_unrelated_fresh_material_preserved", c["name"] == "Green" and c["rgba"] == GREEN
              and c["pointer"] != a["pointer"])
        check(name + "_original_values_preserved", a["rgba"] == b["rgba"] == RED)
        check(name + "_stages", data["result"].get("ready_to_commit") is True)
        check(name + "_production_checks_pass", bool(data["validators"])
              and all(row["outcome"].get("passed") is True for row in data["validators"]))
        check(name + "_green_assignment_verified", any(row["predicate"]["kind"] == "material_assignment"
              and row["predicate"].get("target") == "$c" and row["predicate"].get("material") == "Green"
              and row["outcome"].get("passed") is True
              for row in data["validators"]))
        if copied:
            private = rows["$b"][1]
            check(name + "_copy_has_independent_identity", private["name"] == "Private Paint"
                  and private["rgba"] == BLUE and private["pointer"] not in (a["pointer"], c["pointer"]))
    finally:
        if job is not None and not job.closed:
            job.cancel()


def run_case(name, policy, copied=False, isolated=False):
    ids_before = {block.as_pointer() for block in main_ids()}
    try:
        _run_case(name, policy, copied, isolated)
    finally:
        native.PREVIEWS.clear()
        window.scene = factory_scene
        bpy.data.batch_remove(ids=[block for block in main_ids() if block.as_pointer() not in ids_before])
        queries.invalidate()


try:
    run_case("all_users", "ALL_USERS")
    run_case("refuse", "REFUSE")
    run_case("copy_as", "ALL_USERS", copied=True)
    run_case("isolate", "ISOLATE", isolated=True)
    report["success"] = all(report["checks"].values())
except Exception:
    report["success"] = False
    report["traceback"] = traceback.format_exc()
    raise
finally:
    (OUT / "material-identity.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("ANGELO_MATERIAL_IDENTITY_TEST " + json.dumps({"success": report.get("success"),
          "checks": len(report["checks"])}), flush=True)
