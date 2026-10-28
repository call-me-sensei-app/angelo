# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merged part stays merged through a Restore, and an Undo of its merge still takes it out (break-it E2E-20 H-1).

This (background) Angelo is the lead. Its worker's part file holds one object; the real merge brings it in; a request
snapshot is taken after the merge, the scene changes, and the person Restores that snapshot (Angelo's own operator), so
every collection is read anew. Checked: the part reads merged (status's ``merged_into_this_scene``), it is mergeable
nowhere, the lead's observation lists nothing, and a second merge is refused. Then the merged collection goes (as an
Undo of the merge takes it, another collection added so the count is unchanged): the part reads not in this scene
and the merge takes it again. Before the fix the merge's record was a runtime id every file read renews, and a
background Angelo's cache answered stale either way; then records on the collection and its objects were lost when
the person deleted the collection or joined an object into another (R2-M-1, R3-M-2). The lead's document scene
records it now, inside the merge's Undo step. The file holds no path of the person's (R2-L-5). While Angelo's window
shows another scene of the file, status still reads the lead's own scene and a merge is refused, adding nothing to the
shown scene; shown again, it merges (R5-M-1).

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-restore-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


def run_checks():
    part = OUT / "parallel" / "lead" / "1-seat" / "part.blend"   # The worker's part file: one object.
    part.parent.mkdir(parents=True, exist_ok=True)
    mesh = bpy.data.meshes.new("SeatMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    seat = bpy.data.objects.new("Seat", mesh)
    bpy.data.libraries.write(str(part), {seat}, fake_user=False)
    bpy.data.objects.remove(seat)
    bpy.data.meshes.remove(mesh)
    import addon_utils
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)   # Its Restore operator and handlers.
    from angelo_agent import parallel_sessions, ui, worker_facts, worker_merge, worker_sessions
    from angelo_agent.refusal_codes import RefusalError
    lead = ui.get_controller()
    lead_id = lead.state["id"]
    worker_sessions._started[lead_id] = ["w1"]   # Worker 1's chat has left Angelo: its turn is over.
    worker_sessions._parts["w1"] = (1, "Seat", part)
    parts = {1: (None, part)}

    def window():   # Read again after a load.
        return SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer)

    def refusal():
        try:
            worker_merge.precondition(lead, {"parts": [{"worker": 1}]}, parts, lambda chat: None)
        except RefusalError as error:
            return str(error)
        return None

    def merge():
        result = worker_merge.merge(lead, window(), worker_merge.validate({"parts": [{"worker": 1}]}), parts,
                                    lambda number: "Seat", {})
        return [entry.get("collection") for entry in result.get("merged") or ()]

    def seats():
        return sorted(obj.name for obj in bpy.data.objects if obj.name.startswith("Seat"))

    check("the_first_merge_brings_it_in", merge() == ["Seat"] and seats() == ["Seat"], seats())
    check("merged_it_is_recorded", parallel_sessions.part_in_file(part, lead)
          and "merged into this scene before" in (refusal() or ""))
    point = ui.capture_checkpoint(lead)   # The lead's next request's snapshot, taken after the merge.
    lead.state["messages"].append({"role": "user", "text": "Add a lamp", "checkpoint": point["id"],
                                   "checkpoint_scope": "before_request"})
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "Lamp"   # That request's work, which the person restores away.
    outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=point["id"])
    check("restored", "FINISHED" in outcome and "Lamp" not in bpy.data.objects and seats() == ["Seat"],
          {"outcome": sorted(outcome), "objects": sorted(o.name for o in bpy.data.objects)})
    parallel_sessions.data_changed()   # As a window's load handler does: what it says is read from the file anew.
    status = worker_facts.annotate(lead_id, {"workers": [{"worker": 1, "name": "Seat", "state": "done"}]})
    check("after_a_restore_it_is_still_in_this_scene", status["workers"][0]["merged_into_this_scene"] is True
          and status["workers"][0]["mergeable"] is False and "mergeable_not_in_this_scene" not in status, status)
    check("and_the_leads_observation_lists_nothing", worker_facts.observed(lead_id) == {},
          worker_facts.observed(lead_id))
    check("a_second_merge_is_refused", "merged into this scene before" in (refusal() or ""), refusal())
    check("the_file_holds_no_path_of_the_persons", not any(str(OUT) in str(value) for block in
          list(bpy.data.collections) + list(bpy.data.objects) for value in block.values()))
    for collection in [c for c in bpy.data.collections if c.name.startswith("Seat")]:   # Deleted, its objects kept
        for obj in list(collection.objects):                                            # (break-it E2E-20 R2-M-1).
            bpy.context.scene.collection.objects.link(obj)
        bpy.data.collections.remove(collection)
    parallel_sessions.data_changed()
    check("its_collection_deleted_and_its_objects_kept_it_is_still_merged", parallel_sessions.part_in_file(part, lead)
          and "merged into this scene before" in (refusal() or ""), seats())
    seat = bpy.data.objects["Seat"]   # Joined into another object, which keeps only its own properties (R3-M-2).
    mesh = bpy.data.meshes.new("PropMesh")
    mesh.from_pydata([(3, 0, 0), (4, 0, 0), (4, 1, 0)], [], [(0, 1, 2)])
    prop = bpy.data.objects.new("Prop", mesh)
    bpy.context.scene.collection.objects.link(prop)
    with bpy.context.temp_override(active_object=prop, selected_editable_objects=[prop, seat]):
        bpy.ops.object.join()
    check("joined_into_another_object_it_is_still_merged", "Seat" not in bpy.data.objects
          and parallel_sessions.part_in_file(part, lead) and "merged into this scene before" in (refusal() or ""),
          sorted(o.name for o in bpy.data.objects))
    # Its copy gone (joined above), the record outlives what Undo can reach; the merge's own "again" places it anew
    # (break-it E2E-20 R4-M-2), and status says it was merged, never that it is in the scene.
    status = worker_facts.annotate(lead_id, {"workers": [{"worker": 1, "name": "Seat", "state": "done"}]})
    check("status_says_merged_never_present", status["workers"][0]["merged_into_this_scene"] is True
          and "in_this_scene" not in status["workers"][0], status)
    worker_merge.precondition(lead, {"parts": [{"worker": 1, "again": True}]}, parts, lambda chat: None)
    again = worker_merge.merge(lead, window(), worker_merge.validate({"parts": [{"worker": 1, "again": True}]}),
                               parts, lambda number: "Seat", {})
    check("again_merges_a_new_copy", [entry.get("collection") for entry in again.get("merged") or ()] == ["Seat"]
          and seats() == ["Seat"] and "merged into this scene before" in (refusal() or ""), seats())
    leg = OUT / "parallel" / "lead" / "2-leg" / "part.blend"   # A second part, merged and undone for real.
    leg.parent.mkdir(parents=True, exist_ok=True)
    mesh = bpy.data.meshes.new("LegMesh")
    mesh.from_pydata([(0, 0, 0), (0.2, 0, 0), (0.2, 0.2, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Leg", mesh)
    bpy.data.libraries.write(str(leg), {obj}, fake_user=False)
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    worker_sessions._started[lead_id].append("w2")
    worker_sessions._parts["w2"] = (2, "Leg", leg)
    parts[2] = (None, leg)
    bpy.ops.ed.undo_push(message="Before the leg's merge")
    worker_merge.merge(lead, window(), worker_merge.validate({"parts": [{"worker": 2}]}), parts,
                       lambda number: "Leg", {})
    bpy.ops.ed.undo_push(message="The leg's merge")
    check("the_leg_is_merged", parallel_sessions.part_in_file(leg, lead))
    bpy.ops.ed.undo()   # An Undo of the merge takes its record out with it.
    parallel_sessions.data_changed()
    check("undone_it_is_not_in_this_scene_and_merges_again", not parallel_sessions.part_in_file(leg, lead)
          and "2 Leg" in (worker_facts.observed(lead_id).get(worker_facts.OBSERVED) or ()),
          worker_facts.observed(lead_id))
    # A merge is whole or not at all (break-it E2E-20 R4-L-1, R5-L-1): whatever fails after its first append removes
    # every datablock it added and puts the scene's record back, so the part reads mergeable and a retry adds one copy.
    from unittest import mock
    from angelo_agent import worker_material_check

    def faulty(arguments, **patches):
        before, record = worker_material_check.ids(), dict(bpy.context.scene.get(parallel_sessions.PART_KEY) or {})
        with mock.patch.multiple(worker_merge, **patches) if patches else mock.patch.object(
                worker_merge.placement_check, "built", side_effect=RuntimeError("a fault")):
            try:
                worker_merge.merge(lead, window(), worker_merge.validate(arguments), parts, lambda number: "Part", {})
            except RuntimeError as error:
                raised = error
            else:
                raised = None
        parallel_sessions.data_changed()
        return (raised is not None and getattr(raised, "partial_changes_possible", True) is False,
                worker_material_check.ids() == before,
                dict(bpy.context.scene.get(parallel_sessions.PART_KEY) or {}) == record)
    check("a_fault_after_the_append_leaves_no_copy_and_no_record", faulty({"parts": [{"worker": 2}]}) == (
        True, True, True) and not parallel_sessions.part_in_file(leg, lead)
          and not any(obj.name.startswith("Leg") for obj in bpy.data.objects)
          and "2 Leg" in (worker_facts.observed(lead_id).get(worker_facts.OBSERVED) or ()),
          {"objects": sorted(o.name for o in bpy.data.objects), "observed": worker_facts.observed(lead_id)})
    appended, calls = worker_merge._append, []

    def second_faults(*args, **kwargs):   # The second part's append adds its datablocks, then faults.
        calls.append(1)
        item = appended(*args, **kwargs)
        if len(calls) == 2:
            raise RuntimeError("a fault in the second part's append")
        return item
    seats_before = seats()
    check("a_fault_in_a_later_parts_append_leaves_neither_part", faulty(
        {"parts": [{"worker": 1, "again": True}, {"worker": 2}]}, _append=second_faults) == (True, True, True)
          and len(calls) == 2 and seats() == seats_before and not parallel_sessions.part_in_file(leg, lead)
          and parallel_sessions.part_in_file(part, lead), {"seats": seats(), "calls": len(calls)})
    # Angelo's window shows another scene of the file (break-it E2E-20 R5-M-1; the maintainer: selecting a session only
    # changes what is displayed): "this scene" stays the lead's own, and a merge, by its admission or by the merge
    # itself, lands nowhere until the lead's scene is shown again.
    own, main = parallel_sessions._document_scene(lead), ui.main_window()
    kitchen = bpy.data.scenes.new("Kitchen")
    check("the_window_shows_the_leads_scene", main is not None and main.scene == own and own.name != "Kitchen")
    main.scene = kitchen
    try:
        status = worker_facts.annotate(lead_id, {"workers": [{"worker": 1, "name": "Seat", "state": "done"},
                                                             {"worker": 2, "name": "Leg", "state": "done"}]})
        rows = {row["worker"]: row for row in status["workers"]}
        facts = (rows[1]["merged_into_this_scene"], rows[2]["mergeable"], parallel_sessions.part_in_file(part, lead))
        check("away_this_scene_is_still_the_leads_own", facts == (True, True, True), status)
        try:
            worker_merge.precondition(lead, {"parts": [{"worker": 2}]}, parts, lambda chat: None)
            admitted = None
        except RefusalError as error:
            admitted = error
        check("away_the_merge_is_refused_by_its_admission", admitted is not None and admitted.code == "STALE_CONTEXT"
              and "'Kitchen'" in str(admitted), str(admitted))
        try:
            worker_merge.merge(lead, SimpleNamespace(scene=kitchen, view_layer=kitchen.view_layers[0]),
                               worker_merge.validate({"parts": [{"worker": 2}]}), parts, lambda number: "Leg", {})
            merged = None
        except RefusalError as error:
            merged = error
        check("away_the_merge_itself_lands_nowhere", merged is not None and not kitchen.collection.children
              and not any(obj.name.startswith("Leg") for obj in bpy.data.objects)
              and not parallel_sessions.part_in_file(leg, lead), str(merged))
    finally:
        main.scene = own
    check("shown_again_it_merges", worker_merge.precondition(lead, {"parts": [{"worker": 2}]}, parts,
                                                             lambda chat: None)[0] == "merge")
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "parallel-restore.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-RESTORE-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()
    os._exit(1)
