# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression: staged ID copies keep their live names, and a refusal stays correctable (production path).

A one-shot castle scene failed on three host defects:

* its first program deleted the default objects and configured the World: the deletion was retained ("approved
  objects retained in data", an Outliner tree-store reference), the result named the World "World.001" and the
  deleted objects "__angelo_copy_<id>_Cube";
* a later program reused the keep's material twice: staging names its copy "Castle pale limestone.001", the first
  reuse registered the copy under that private name, and the second refused MATERIAL_NOT_FOUND;
* the refusal was a plain ValueError: error_code UNKNOWN and recovery_required, so every later edit was blocked.

C1 replays those programs; C2 checks that a refusal (a missing material, a 33rd slot) leaves the request free to
correct it; C3 checks the same rename hazard for collections, node groups and Actions (staging never copies an
image: test_blender_material_images.py). Every program goes through the actual Controller, its approval,
BlenderExecutor and ``angelo.commit_native_job`` (program_production_helpers).
GUI factory startup with the exact Angelo binary (an Outliner must have drawn the objects); no provider, account or
paid request. Writes stage-id-names.json in ANGELO_QA_DIR.
"""

import contextlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-stage-id-names-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
from angelo_agent import agent_workspace_ui, native_programs as native, program_undo  # noqa: E402
from angelo_agent import scene_queries as queries, scene_settings  # noqa: E402
from program_production_helpers import production_call, production_controller  # noqa: E402

REPORT = {"checks": {}, "results": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
STONE, SLATE = "Castle pale limestone", "Castle blue slate"


def check(name, value):
    REPORT["checks"][name] = bool(value)
    assert value, name


def window():
    return bpy.context.window_manager.windows[0]


def objects():
    return window().scene.objects


def handle(name):
    return queries.object_handle(objects()[name])


def binding(name):
    return {"handle": handle(name), "revision": queries.object_revision(window(), objects()[name])}


def scene_entry():
    settings = scene_settings.execute(window(), {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def program(operations, writes=(), reads=(), checks=()):
    return {"mode": "APPLY", "read_set": list(reads), "write_set": list(writes), "operations": operations,
            "expected_checks": list(checks)}


def run(controller, label, arguments):
    """One program through the production path; the delivered result, recorded without its bulky delta.

    No queries.invalidate() here: it starts a new document epoch and expires the handles the arguments were just
    built with (baseline() and undo() invalidate before they are built, as a host does)."""
    result = production_call(controller, window(), label, "angelo_scene_program", arguments)
    REPORT["results"][label] = {key: value for key, value in result.items() if key not in ("delta", "live_revisions")}
    return result


def private_free(result):
    """No staged private name reaches the model: object copies, stage containers, or a suffixed World.

    ``orphan_data`` names the replaced live World ("World.001") on purpose: data left without users."""
    text = json.dumps({key: value for key, value in result.items() if key not in ("recovery", "orphan_data")},
                      default=str)
    return "__angelo_" not in text and "World.001" not in text


def correctable(result):
    return (result.get("error_code") == "VALIDATION" and not result.get("partial_changes_possible")
            and "recovery_required" not in result and result.get("scene_changed") is False)


def rows(result, kind):
    return [row for entry in result.get("checks") or [] for row in entry.get("checks", []) if row.get("kind") == kind]


def baseline(message):
    bpy.ops.ed.undo_push(message=message)
    queries.invalidate()


def undo():
    bpy.ops.ed.undo()
    queries.invalidate()


def redo():
    bpy.ops.ed.redo()
    queries.invalidate()


def castle(controller):
    """C1: the castle scene's programs, in order (the gatehouse and roof come from a reviewed script, as there)."""
    baseline("castle")
    world_uid = window().scene.world.session_uid
    scene = scene_entry()
    first = run(controller, "c1_first_program", program([
        *[op("object_delete", handle(name)) for name in ("Camera", "Cube", "Light")],
        op("create_primitive", name="Ground", alias="ground", primitive="PLANE", size=160.0),
        op("material_principled", "$ground", name="Meadow grass", inputs={"Base Color": [0.12, 0.27, 0.055, 1.0]}),
        op("camera_create", name="Hero Camera", alias="camera", location=[42, -74, 16], look_at=[0, 0, 12]),
        op("light_create", name="Sun", alias="sun", location=[-50, -50, 38], look_at=[0, 0, 0],
           settings={"light_type": "SUN", "energy": 2.7}),
        op("world_configure", scene["handle"], strength=0.35),
        op("property_set", scene["handle"], values={"camera": "$camera"})],
        writes=[binding("Camera"), binding("Cube"), binding("Light"), scene]))
    check("c1_first_program_commits", first["ok"])
    check("c1_deletion_is_not_retained", not first.get("cleanup_error") and not first.get("cleanup_warning")
          and not first.get("partial_changes_possible"))
    check("c1_deleted_objects_leave_main", not {"Camera", "Cube", "Light"} & set(bpy.data.objects.keys()))
    configured = next(row for row in first["operation_results"] if row["kind"] == "world_configure")
    check("c1_world_result_names_the_world_it_will_be", configured["world"]["name"] == "World"
          and window().scene.world.name == "World")
    check("c1_result_names_no_private_copy", private_free(first))
    deleted = [row["object"] for row in first["operation_results"] if row["kind"] == "object_delete"]
    check("c1_deleted_rows_name_the_objects", deleted == ["Camera", "Cube", "Light"])
    check("c1_the_result_names_the_data_left_without_users",
          (first.get("orphan_data") or {}).get("names") == ["Camera", "Cube", "Light", "World.001"])
    # By design the replaced World stays as orphan data until the file is saved (program_identities._adopt_names).
    orphans = [item for item in bpy.data.worlds if item.users == 0]
    REPORT["orphan_worlds"] = [item.name for item in orphans]
    check("c1_the_only_orphan_world_is_the_replaced_original", [item.session_uid for item in orphans] == [world_uid])
    second = run(controller, "c1_keep_and_cone", program([
        op("create_primitive", name="Castle Keep", alias="keep", primitive="CUBE", location=[0, 6, 12.5]),
        op("create_primitive", name="Castle Wall West", alias="west", primitive="CUBE", location=[-20, 0, 4.5]),
        op("material_principled", "$keep", name=STONE, inputs={"Base Color": [0.72, 0.68, 0.57, 1.0]},
           also=["$west"]),
        op("create_primitive", name="Castle Tower Cone", alias="cone", primitive="CONE", location=[-20, -15, 21]),
        op("material_principled", "$cone", name=SLATE, inputs={"Base Color": [0.045, 0.105, 0.19, 1.0]})]))
    check("c1_keep_commits", second["ok"])
    for name in ("Castle Gatehouse", "Castle Keep Slate Roof"):  # The scene's reviewed script made these two.
        mesh = bpy.data.meshes.new(name + " Mesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        window().scene.collection.objects.link(bpy.data.objects.new(name, mesh))
    baseline("castle script")
    reuse = run(controller, "c1_reuse_twice", program([
        op("material_assign", handle("Castle Gatehouse"), material=STONE, source=handle("Castle Keep")),
        op("material_assign", handle("Castle Keep Slate Roof"), material=SLATE, source=handle("Castle Tower Cone")),
        op("create_primitive", name="Castle Wall Merlon", alias="merlon", primitive="CUBE", location=[-19.5, 15.5, 9.6],
           scale=[0.5, 0.5, 0.6]),
        op("material_assign", "$merlon", material=STONE, source=handle("Castle Keep")),
        op("batch", sources=["$merlon"], linked=True, at=[[-19.5, 15.5, 9.6], [-17.7, 15.5, 9.6], [-15.9, 15.5, 9.6]]),
        op("create_primitive", name="Castle Gatehouse Merlon", alias="gate", primitive="CUBE",
           location=[-4.5, -18, 13.6]),
        op("material_assign", "$gate", material=STONE, source=handle("Castle Keep"))],
        writes=[binding("Castle Gatehouse"), binding("Castle Keep Slate Roof")],
        reads=[binding("Castle Keep"), binding("Castle Tower Cone")]))
    check("c1_a_material_reused_twice_commits", reuse["ok"] and not reuse.get("error"))
    stone = objects()["Castle Keep"].material_slots[0].material
    merlons = sorted(obj.name for obj in objects() if obj.name.startswith("Castle Wall Merlon"))
    REPORT["merlons"] = merlons
    users = ["Castle Gatehouse", "Castle Gatehouse Merlon"] + merlons
    check("c1_every_reuse_shares_the_live_material", stone.name == STONE and len(merlons) == 3
          and all(objects()[name].material_slots[0].material == stone for name in users)
          and objects()["Castle Keep Slate Roof"].material_slots[0].material.name == SLATE)
    check("c1_no_suffixed_material_copy",
          not [item.name for item in bpy.data.materials if item.users and "." in item.name])
    assigned = [row for row in reuse["operation_results"] if row["kind"] == "material_assign"]
    check("c1_results_name_the_live_materials", [row["material"] for row in assigned] == [STONE, SLATE, STONE, STONE]
          and assigned[0]["object"] == "Castle Gatehouse")
    check("c1_reuse_result_names_no_private_copy", private_free(reuse))
    check("c1_gatehouse_keeps_its_mesh_name", objects()["Castle Gatehouse"].data.name == "Castle Gatehouse Mesh")
    undo()
    check("c1_one_undo_restores", len(objects()["Castle Gatehouse"].material_slots) == 0
          and "Castle Wall Merlon" not in objects())
    redo()
    check("c1_one_redo_reapplies", objects()["Castle Gatehouse"].material_slots[0].material.name == STONE)


def refusals(controller):
    """C2: a refused program is a correctable refusal, and the same request's next program commits."""
    baseline("refusals")
    missing = run(controller, "c2_missing_material", program([
        op("create_primitive", name="Castle Window Frame", alias="frame", primitive="CUBE", location=[0, 1.5, 15]),
        op("material_assign", "$frame", material="Castle granite", source=handle("Castle Keep"))],
        reads=[binding("Castle Keep")]))
    REPORT["missing_error"] = missing.get("error")
    check("c2_missing_material_is_correctable", not missing["ok"] and "MATERIAL_NOT_FOUND" in missing.get("error", "")
          and correctable(missing) and "Castle Window Frame" not in objects())
    corrected = run(controller, "c2_corrected", program([
        op("create_primitive", name="Castle Window Frame", alias="frame", primitive="CUBE", location=[0, 1.5, 15]),
        op("material_assign", "$frame", material=STONE, source=handle("Castle Keep"))], reads=[binding("Castle Keep")]))
    check("c2_the_correction_commits_in_the_same_request", corrected["ok"])
    crowded = bpy.data.objects.new("QA Crowded", bpy.data.meshes.new("QA Crowded Mesh"))
    window().scene.collection.objects.link(crowded)
    for index in range(32):
        crowded.data.materials.append(bpy.data.materials.new("QA Slot %02d" % index))
    baseline("thirty-two slots")
    slot = run(controller, "c2_thirty_third_slot", program([
        op("material_assign", handle("QA Crowded"), material=STONE, source=handle("Castle Keep"))],
        writes=[binding("QA Crowded")], reads=[binding("Castle Keep")]))
    check("c2_a_33rd_slot_is_correctable", not slot["ok"] and "at most 32 slots" in slot.get("error", "")
          and correctable(slot) and len(objects()["QA Crowded"].material_slots) == 32)
    after = run(controller, "c2_after_slot", program([op("transform", handle("QA Crowded"), location=[30, 0, 0])],
                                                     writes=[binding("QA Crowded")]))
    check("c2_the_next_program_commits", after["ok"] and abs(objects()["QA Crowded"].location.x - 30) < 1e-6)


def other_kinds(controller):
    """C3: collections, node groups, Actions and object data keep the names commit gives them."""
    baseline("other kinds")
    membership = run(controller, "c3_memberships", program(
        [op("create_primitive", name="QA Post", alias="post", primitive="CUBE", location=[40, 0, 0]),
         op("transform", handle("Castle Keep"), location=[0, 6.5, 12.5])], writes=[binding("Castle Keep")],
        checks=[{"kind": "collection_membership", "target": "$post", "collections": ["Scene Collection"]},
                {"kind": "collection_membership", "target": handle("Castle Keep"), "collections": ["Scene Collection"],
                 "absent": ["Collection"]}]))
    observed = [row.get("observed", {}).get("collections") for row in rows(membership, "collection_membership")]
    REPORT["memberships_observed"] = observed
    check("c3_membership_checks_read_committed_collections", membership["ok"]
          and observed == [["Scene Collection"], ["Scene Collection"]] and private_free(membership))
    graph = {"name": "QA Nodes", "nodes": [{"id": "input", "type": "GROUP_INPUT"},
             {"id": "move", "type": "TRANSFORM_GEOMETRY", "inputs": {"Translation": [0, 0, .1]}},
             {"id": "output", "type": "GROUP_OUTPUT"}],
             "links": [{"from_node": "input", "from_socket": "Geometry", "to_node": "move", "to_socket": "Geometry"},
                       {"from_node": "move", "from_socket": "Geometry", "to_node": "output", "to_socket": "Geometry"}]}
    nodes = run(controller, "c3_node_group", program([op("geometry_nodes", handle("QA Post"), **graph)],
                                                     writes=[binding("QA Post")]))
    check("c3_node_modifier_commits", nodes["ok"])
    moved = run(controller, "c3_node_group_check", program(
        [op("transform", handle("QA Post"), location=[41, 0, 0])], writes=[binding("QA Post")],
        checks=[{"kind": "node_graph", "target": handle("QA Post"), "modifier": "QA Nodes",
                 "nodes": [{"type": "GeometryNodeTransform"}]}]))
    trees = [row.get("observed", {}).get("tree") for row in rows(moved, "node_graph")]
    REPORT["node_graph_observed"] = trees
    check("c3_node_graph_check_names_the_live_group", moved["ok"] and trees == ["QA Nodes"]
          and objects()["QA Post"].modifiers["QA Nodes"].node_group.name == "QA Nodes")
    post = objects()["QA Post"]
    post.keyframe_insert("location", frame=1)
    post.location.x = 45
    post.keyframe_insert("location", frame=20)
    action = post.animation_data.action
    twin = bpy.data.objects.new("QA Twin", bpy.data.meshes.new("QA Twin Mesh"))
    window().scene.collection.objects.link(twin)
    twin.animation_data_create().action = action  # One live Action, two users.
    twin.animation_data.action_slot = post.animation_data.action_slot
    baseline("animated")
    kept = run(controller, "c3_shared_action_check", program(
        [op("transform", handle("QA Twin"), scale=[2, 2, 2])], writes=[binding("QA Twin")],
        checks=[{"kind": "nla", "target": handle("QA Twin"), "action": action.name}]))
    REPORT["shared_action_rows"] = rows(kept, "nla")
    check("c3_a_shared_unedited_action_reads_its_live_name", kept["ok"]
          and [row.get("passed") for row in rows(kept, "nla")] == [True])
    alone = bpy.data.objects.new("QA Solo", bpy.data.meshes.new("QA Solo Mesh"))
    window().scene.collection.objects.link(alone)
    alone.keyframe_insert("location", frame=1)
    solo = alone.animation_data.action.name
    baseline("solo action")
    pushed = run(controller, "c3_push_down", program(
        [op("nla_push_down", handle("QA Solo"), track=None)], writes=[binding("QA Solo")],
        checks=[{"kind": "nla", "target": handle("QA Solo"), "track_names": [solo],
                 "strip": {"track": solo, "name": solo}}]))
    track = objects()["QA Solo"].animation_data.nla_tracks[0] if pushed["ok"] else None
    check("c3_push_down_names_track_and_strip_after_the_action", pushed["ok"] and track.name == solo
          and track.strips[0].name == solo and track.strips[0].action.name == solo and private_free(pushed))


def run_all():
    classes = []
    try:
        win = window()
        area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.context.preferences.edit.use_global_undo = True
            native.register()
            classes = [c for c in agent_workspace_ui.workspace_operators()
                       if getattr(c, "bl_idname", "") == "angelo.commit_native_job"]
            for cls in classes:
                bpy.utils.register_class(cls)
            check("native_undo_bridge_available", program_undo.native_bridge() is not None)
            check("an_outliner_is_open", any(area.type == "OUTLINER" for area in win.screen.areas))
            with production_controller(OUT) as controller:
                castle(controller)
                refusals(controller)
                other_kinds(controller)
            check("no_stage_residue", not any(scene.get("_angelo_program_stage") for scene in bpy.data.scenes)
                  and not native.runtime_is_staging())
        REPORT["success"] = True
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    finally:
        native.cleanup_all()
        for cls in reversed(classes):
            with contextlib.suppress(Exception):
                bpy.utils.unregister_class(cls)
        (OUT / "stage-id-names.json").write_text(json.dumps(REPORT, indent=2, default=str))
        print("STAGE_ID_NAMES_DONE", REPORT.get("success", False), flush=True)
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(run_all, first_interval=1.)
