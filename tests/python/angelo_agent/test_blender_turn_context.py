# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable native turn context v2 and change-feed contract.

Run with the exact Angelo binary and fresh isolated directories::

  QA=$(mktemp -d) BLENDER_USER_CONFIG=$QA/config ANGELO_QA_DIR=$QA \\
    ANGELO_CONNECTIONS_DIR=$QA/accounts ANGELO_STATE_DIR=$QA/state \\
    "$ANGELO" --background --factory-startup --disable-autoexec \\
    --python-exit-code 1 --python tests/python/angelo_agent/test_blender_turn_context.py

Proves in the compiled binary: active and selected objects beyond the legacy
32-entry cap come first; a turn-context revision binds a production
angelo_scene_program APPLY with no discovery query; a manual edit after the
turn context makes that program fail closed at commit; a committed program's
post-commit revisions bind the next program and fail closed after a manual
edit; a PREVIEW keeps its declared revisions until Accept; an object the
message names and an object this chat's own commit changed carry production
revisions that bind a program, also when the send waited for a CPU after the
active object's revision; angelo_apply_recipe expands on the host, shows
every expanded control for approval, runs nothing before approval, commits the
reviewed program through the controller's Undo operator and fails closed for a
stale revision (bound again after the refusal too) or a manual edit after
review, and a recovery hold bars a rebinding to the live revision; the change feed reports
a manual transform once and resets at the planning boundary; Undo and file load
report an epoch change. It alters only its factory-startup fixture and a
temporary blend in ANGELO_QA_DIR; no user project, provider or account is used.
Inspect results.json: a zero exit status alone is not proof.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
# test_blender_runtime_recovery.py accepts the same names: Angelo, or angelo.exe on Windows.
assert Path(bpy.app.binary_path).name.lower() in {"angelo", "angelo.exe"}, "Do not use the adjacent stale Blender.app"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-turn-context"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
# Source add-on first: a background process has not imported the bundled copy,
# so every module below (product_env included) is the checkout's code.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import product_env  # noqa: E402
product_env.clear("TURN_CONTEXT")  # Every spelling the app honours, deprecated ones included.
from angelo_agent import change_feed, native_programs as native, scene_queries as queries, turn_context, ui
from angelo_agent.blender_tools import track_scene_changes

CHAT = "native-turn-context-chat"
report = {"checks": {}, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "paid_requests": 0, "user_projects_opened": 0}
HANDLERS = (("depsgraph_update_post", track_scene_changes), ("load_pre", ui.document_load_pre),
            ("undo_post", ui.workspace_undo_post), ("redo_post", ui.workspace_redo_post))


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


def boundary():
    """Exactly what console.execute does around a successful send."""
    turn = turn_context.prepare(window(), CHAT)
    turn.commit()
    return turn.observation


def settle(job):
    for _ in range(200):
        outcome = job.poll()
        if outcome.get("final") or outcome.get("ready_to_commit"):
            return outcome
    raise AssertionError("Native job did not settle within bounded polling")


class TurnContextCommit(bpy.types.Operator):
    bl_idname = "angelo.turn_context_commit"
    bl_label = "Disposable turn context commit"
    bl_options = {"REGISTER", "UNDO"}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        result = native.commit_job(self.program_id, context.window)
        report["last_commit"] = {key: result.get(key) for key in ("success", "error", "status")}
        return {"FINISHED"} if result.get("success") else {"CANCELLED"}


def build_scene():
    scene = window().scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for index in range(37):
        scene.collection.objects.link(bpy.data.objects.new("Filler %02d" % index, None))
    mesh = bpy.data.meshes.new("Turn cube mesh")
    mesh.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    objects = {}
    for name in ("Selected A", "Selected B", "Active Cube"):
        objects[name] = bpy.data.objects.new(name, mesh.copy())
        scene.collection.objects.link(objects[name])
    for obj in scene.objects:
        obj.select_set(False)
    objects["Selected A"].select_set(True)
    objects["Selected B"].select_set(True)
    objects["Active Cube"].select_set(True)
    window().view_layer.objects.active = objects["Active Cube"]
    window().view_layer.update()
    return objects


def program(handle, revision, location):
    return {"mode": "APPLY", "read_set": [], "write_set": [{"handle": handle, "revision": revision}],
            "operations": [{"kind": "transform", "target": handle, "parameters": {"location": location}}],
            "expected_checks": [{"kind": "finite_coordinates", "target": handle}]}


def ordering(objects):
    legacy = turn_context.legacy_observation(window())
    check("legacy_cap_cuts_off_the_active_object", "Active Cube" not in [o["name"] for o in legacy["objects"]])
    observation = turn_context.prepare(window(), None).observation
    names = [entry["name"] for entry in observation["objects"]]
    check("active_first_then_selected_beyond_the_cap", names[:3] == ["Active Cube", "Selected A", "Selected B"])
    # 40 objects: the active and two selected ones, then at most MAX_FILL_OBJECTS scene-order rows (payload trims).
    check("cap_and_counts", len(names) == 3 + turn_context.MAX_FILL_OBJECTS and observation["object_count"] == 40
          and observation["truncated"]
          and observation["selected_count"] == 3 and observation["mode"] == "OBJECT"
          and observation["frame_current"] == window().scene.frame_current)
    check("active_handle_reported", observation["active_object"] == queries.object_handle(objects["Active Cube"]))
    for entry in observation["objects"][:3]:
        check("production_revision_" + entry["name"], entry["object_revision"] == queries.object_revision(window(), objects[entry["name"]]))
    check("unselected_entries_have_no_revision", all("object_revision" not in e for e in observation["objects"][3:]))


def apply_without_discovery(objects):
    cube = objects["Active Cube"]
    observation = boundary()
    entry = observation["objects"][0]
    original_execute = queries.execute
    queries.execute = lambda *_args: (_ for _ in ()).throw(AssertionError("No discovery query is allowed"))
    try:
        arguments = program(entry["handle"], entry["object_revision"], [2.0, 0.0, 0.0])
        native.precondition(window(), "angelo_scene_program", arguments)  # The controller's approval binding.
        job = native.begin(window(), "angelo_scene_program", arguments)
        check("turn_context_revision_stages_without_discovery", settle(job).get("ready_to_commit")
              and len(queries._observations) == 0)
        result = bpy.ops.angelo.turn_context_commit("EXEC_DEFAULT", True, program_id=job.id)
    finally:
        queries.execute = original_execute
    check("turn_context_revision_commits", "FINISHED" in result and job.result["success"]
          and tuple(cube.location) == (2.0, 0.0, 0.0))
    agent_commit = boundary()["changes_since_last_turn"]
    check("own_commit_reported_as_source_unknown_hint", agent_commit["status"] == "TRACKED"
          and any(o["name"] == "Active Cube" and "transform" in o["changes"] for o in agent_commit["objects"])
          and "Source unknown" in agent_commit["hint"])


def manual_edit_fails_closed(objects):
    cube = objects["Active Cube"]
    entry = boundary()["objects"][0]
    job = native.begin(window(), "angelo_scene_program", program(entry["handle"], entry["object_revision"], [5.0, 5.0, 5.0]))
    check("second_program_ready", settle(job).get("ready_to_commit"))
    cube.location = (3.0, 1.0, 0.0)  # Manual edit after the turn context and staging.
    window().view_layer.update()
    result = bpy.ops.angelo.turn_context_commit("EXEC_DEFAULT", True, program_id=job.id)
    check("manual_edit_fails_closed_at_commit", "CANCELLED" in result and not job.result["success"]
          and "changed" in job.result.get("error", "") and tuple(cube.location) == (3.0, 1.0, 0.0)
          and not native.runtime_is_staging() and not native.has_active_programs())


def commit(job):
    return "FINISHED" in bpy.ops.angelo.turn_context_commit("EXEC_DEFAULT", True, program_id=job.id)


def live(result):
    return {entry["role"] + ":" + entry["name"]: entry for entry in result["live_revisions"]["objects"]}


def no_discovery():
    """Replace scene queries so any lookup fails the check that uses it."""
    original = queries.execute
    queries.execute = lambda *_args: (_ for _ in ()).throw(AssertionError("No discovery query is allowed"))
    return original


def post_commit_revisions_bind_the_next_program(objects):
    cube = objects["Active Cube"]
    entry = boundary()["objects"][0]
    created = {"kind": "create_primitive", "target": None, "parameters": {
        "primitive": "CUBE", "name": "Live Created", "alias": "made", "size": 1.0,
        "location": [0.0, 3.0, 0.0], "rotation": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}}
    arguments = program(entry["handle"], entry["object_revision"], [1.0, 0.0, 0.0])
    arguments["operations"].append(created)
    arguments["expected_checks"].append({"kind": "object_name", "target": "$made", "expected": "Live Created"})
    job = native.begin(window(), "angelo_scene_program", arguments)
    check("creating_program_ready", settle(job).get("ready_to_commit") and commit(job))
    made = window().scene.objects["Live Created"]
    window().view_layer.update()  # A depsgraph evaluation after the Undo operator returned.
    rows = live(job.result)
    written, new = rows.get("WRITTEN:Active Cube", {}), rows.get("CREATED:Live Created", {})
    check("post_commit_result_status", job.result["live_revisions"]["status"] == "COMMITTED"
          and job.result["live_revisions"]["document_epoch"] == queries.document_epoch())
    check("post_commit_written_identity_is_production", written.get("handle") == queries.object_handle(cube)
          and written.get("object_revision") == queries.object_revision(window(), cube))
    check("post_commit_created_identity_is_production", new.get("alias") == "$made"
          and new.get("handle") == queries.object_handle(made)
          and new.get("object_revision") == queries.object_revision(window(), made))
    second = {"mode": "APPLY", "read_set": [],
              "write_set": [{"handle": row["handle"], "revision": row["object_revision"]} for row in (written, new)],
              "operations": [{"kind": "transform", "target": written["handle"], "parameters": {"location": [1.5, 0.0, 0.0]}},
                             {"kind": "transform", "target": new["handle"], "parameters": {"location": [0.0, 3.5, 0.0]}}],
              "expected_checks": [{"kind": "finite_coordinates", "target": new["handle"]}]}
    original = no_discovery()
    try:
        native.precondition(window(), "angelo_scene_program", second)
        job = native.begin(window(), "angelo_scene_program", second)
        check("post_commit_revisions_stage_without_lookup", settle(job).get("ready_to_commit"))
        check("post_commit_revisions_commit", commit(job) and tuple(made.location) == (0.0, 3.5, 0.0)
              and tuple(cube.location) == (1.5, 0.0, 0.0) and len(queries._observations) == 0)
    finally:
        queries.execute = original
    # The returned revision is evidence, not authority: a manual edit after it
    # makes the next program fail closed at commit, never overwrite the edit.
    again = live(job.result)["WRITTEN:Live Created"]
    job = native.begin(window(), "angelo_scene_program", program(again["handle"], again["object_revision"], [9.0, 9.0, 9.0]))
    check("stale_candidate_ready", settle(job).get("ready_to_commit"))
    made.location = (0.0, 4.0, 0.0)
    window().view_layer.update()
    check("stale_post_commit_revision_fails_closed", not commit(job) and not job.result["success"]
          and "changed" in job.result.get("error", "") and tuple(made.location) == (0.0, 4.0, 0.0))


def preview_revisions_survive_until_accept(objects):
    made = window().scene.objects["Live Created"]
    handle, revision = queries.object_handle(made), queries.object_revision(window(), made)
    job = native.begin(window(), "angelo_scene_program", dict(program(handle, revision, [0.0, 6.0, 0.0]), mode="PREVIEW"))
    preview = settle(job)
    declared = preview.get("live_revisions", {})
    check("preview_reports_unchanged_live_revisions", preview.get("status") == "preview"
          and declared.get("status") == "UNCHANGED_UNTIL_ACCEPT"
          and declared.get("objects") == [{"handle": handle, "object_revision": revision, "role": "DECLARED"}]
          and queries.object_revision(window(), made) == revision and tuple(made.location) == (0.0, 4.0, 0.0))
    accept = native.begin(window(), "angelo_accept_preview", {"preview_id": preview["preview_id"]})
    check("accept_ready", settle(accept).get("ready_to_commit") and commit(accept))
    window().view_layer.update()
    row = live(accept.result).get("WRITTEN:Live Created", {})
    check("accept_returns_post_commit_revision", accept.result["live_revisions"]["status"] == "COMMITTED"
          and tuple(made.location) == (0.0, 6.0, 0.0) and row.get("object_revision") != revision
          and row.get("object_revision") == queries.object_revision(window(), made))


def named_and_changed_objects_are_revised(objects):
    made, filler = window().scene.objects["Live Created"], window().scene.objects["Filler 20"]
    # Not committed: the previous steps' own commits are still in the feed.
    observation = turn_context.prepare(window(), CHAT, message="Move 'Filler 20' beside the new cube").observation
    rows = {entry["name"]: entry for entry in observation["objects"]}
    check("own_commit_object_is_recently_changed_with_production_revision",
          rows.get("Live Created", {}).get("recently_changed")
          and rows["Live Created"].get("object_revision") == queries.object_revision(window(), made))
    check("named_object_has_production_revision", rows.get("Filler 20", {}).get("mentioned")
          and rows["Filler 20"].get("object_revision") == queries.object_revision(window(), filler)
          and observation["mentions"] == {"matched": 1, "included": 1})
    check("named_then_selected_order", [e["name"] for e in observation["objects"][:4]]
          == ["Active Cube", "Filler 20", "Selected A", "Selected B"])
    original = no_discovery()
    try:
        entry = rows["Filler 20"]
        job = native.begin(window(), "angelo_scene_program", program(entry["handle"], entry["object_revision"], [0.0, 0.0, 2.0]))
        check("named_revision_binds_without_lookup", settle(job).get("ready_to_commit") and commit(job)
              and tuple(filler.location) == (0.0, 0.0, 2.0))
    finally:
        queries.execute = original
    boundary()


def preempted_send_binds_the_named_object(objects):
    """A send that waits for a CPU after the active object's revision (a busy machine) still
    offers the named object's revision, and a recipe leaving it to inference binds it."""
    from angelo_agent import context_ledger
    from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call
    active, target, revision = objects["Active Cube"], objects["Selected A"], queries.object_revision

    def waits_after_active(win, obj):
        value = revision(win, obj)
        if obj == active:
            time.sleep(2 * turn_context.REVISION_BUDGET_SECONDS)  # No work: wall time only.
        return value
    queries.object_revision = waits_after_active
    try:
        observation = turn_context.prepare(window(), CHAT, message="Give every face of 'Selected A' a material").observation
    finally:
        queries.object_revision = revision
    entry = next(e for e in observation["objects"] if e["name"] == "Selected A")
    check("preempted_send_keeps_named_revision", entry.get("mentioned")
          and entry.get("object_revision") == queries.object_revision(window(), target))
    table = IdentifierTable()
    context_ledger.compact_observation(observation, table)
    try:
        bound = expand_call("angelo_apply_recipe", {"recipe": "material-setup", "target": entry["handle"], "mode": "APPLY",
                                                    "parameters": {"name": "Preempted"}}, table).get("revision")
    except IdentifierError as error:
        bound = str(error)
    check("preempted_send_binds_inferred_recipe_revision", bound == queries.object_revision(window(), target))


DOCUMENT = ("turn-context-fixture", 1)
CONTROLLER = {}


class ControllerCommit(bpy.types.Operator):
    """The production continuation: the controller commits inside an Undo operator."""
    bl_idname = "angelo.turn_context_controller_commit"
    bl_label = "Disposable controller commit"
    bl_options = {"REGISTER", "UNDO"}
    call_id: bpy.props.StringProperty()

    def execute(self, context):
        from angelo_agent.blender_tools import BlenderExecutor
        controller = CONTROLLER["value"]
        call = controller.pending.get(self.call_id)
        result = controller.commit_native_job(call, BlenderExecutor(context.window), DOCUMENT) if call else None
        return {"FINISHED"} if isinstance(result, dict) and result.get("success") else {"CANCELLED"}


def recipe_request(controller, identity, arguments):
    from angelo_agent.blender_tools import BlenderExecutor
    responses, executor = [], BlenderExecutor(window())
    controller.request_tool(identity, "angelo_apply_recipe", arguments, responses.append)
    call = controller.pending[controller.state["id"] + "/" + identity]
    controller.pump_tools(executor, DOCUMENT)  # Admission: host expansion and approval request.
    return call, responses, executor


def recipe_request_unqueued(controller, identity, arguments):
    """recipe_request for a call admission may refuse: the pending call, or None, and its answers."""
    from angelo_agent.blender_tools import BlenderExecutor
    responses, executor = [], BlenderExecutor(window())
    controller.request_tool(identity, "angelo_apply_recipe", arguments, responses.append)
    controller.pump_tools(executor, DOCUMENT)
    return controller.pending.get(controller.state["id"] + "/" + identity), responses, executor


def drive(controller, executor, responses):
    commit_mutation = lambda call: bpy.ops.angelo.turn_context_controller_commit("EXEC_DEFAULT", True, call_id=call.id)
    for _ in range(200):
        if responses:
            return responses[0]
        controller.pump_tools(executor, DOCUMENT, commit_mutation=commit_mutation)
    raise AssertionError("The recipe program did not finish within bounded pumping")


def recipe_applies_in_one_call(objects):
    from angelo_agent import action_recipes, approvals
    from angelo_agent.controller import Controller
    from angelo_agent.program_checks import required_checks
    from angelo_agent.program_spec import validate_program
    target = objects["Selected B"]
    boundary()
    handle, revision = queries.object_handle(target), queries.object_revision(window(), target)
    controller = CONTROLLER["value"] = Controller(str(OUT / "recipe-controller"))
    controller.set_document(DOCUMENT)
    controller.busy, controller.cancelled = True, False
    controller.state["options"]["approval"] = "ASK"
    controller._begin_metrics()
    try:
        arguments = {"recipe": "bevel-and-shade", "target": handle, "revision": revision,
                     "parameters": {"width": 0.125, "segments": 3}, "mode": "APPLY"}
        call, responses, executor = recipe_request(controller, "recipe", arguments)
        expansion = action_recipes.execute(window(), "angelo_expand_recipe",
                                           {key: arguments[key] for key in ("recipe", "target", "revision", "parameters")})
        check("recipe_expanded_on_the_host_for_review", call.approval and not responses
              and call.program_arguments == validate_program("angelo_scene_program", dict(expansion["program"], mode="APPLY")))
        details = approvals.approval_details(call.name, approvals.review_arguments(call))
        controls = [str(value) for operation in call.program_arguments["operations"] for value in (
            operation["kind"], operation["parameters"].get("name"))] + ["Width: 0.125", "Segments: 3", "smooth_shading_graph",
            "finite_coordinates", "Expected checks", "Recipe inputs, defaults included", "Angelo Recipe Shade", revision]
        check("approval_shows_every_expanded_control", all(text in details for text in controls))
        check("nothing_runs_before_approval", len(target.modifiers) == 0 and not native.runtime_is_staging()
              and queries.object_revision(window(), target) == revision)
        controller.approve_tool(call, executor, DOCUMENT)  # What Approve does, outside any Undo operator.
        result = drive(controller, executor, responses)
        # The recipe's own checks plus the host obligations its operations imply (every typed
        # program, recipe expansions included, gets the complete verification).
        authored = call.program_arguments["expected_checks"]
        required = required_checks(call.program_arguments)
        check("reviewed_recipe_program_commits_with_passing_checks", result.get("status") == "committed"
              and [modifier.name for modifier in target.modifiers] == ["Angelo Recipe Bevel", "Angelo Recipe Shade"]
              and authored and required[:len(authored)] == authored and len(required) > len(authored)
              and sorted(row["kind"] for row in result["checks"]) == sorted(row["kind"] for row in required)
              and all(check_result["passed"] for check_result in result["checks"]))
        row = live(result).get("WRITTEN:Selected B", {})
        check("recipe_result_carries_post_commit_revision",
              row.get("object_revision") == queries.object_revision(window(), target))
        controller.pump_events()
        activity = next(m for m in controller.state["messages"] if m.get("call_id", "").endswith("/recipe"))
        check("transcript_records_the_reviewed_program", activity["arguments"].get("program") == call.program_arguments)
        # Stale before expansion: refused before any approval is requested.
        stale = dict(arguments, revision=revision, parameters={"width": 0.2, "segments": 2})
        call, responses, executor = recipe_request(controller, "stale-recipe", stale)
        check("stale_revision_is_refused_before_approval", responses and "changed" in responses[0].get("error", "")
              and call.program_arguments is None and len(target.modifiers) == 2)
        # That refusal changed nothing, so it holds nothing (request_recovery), but a rebinding
        # nobody observed is refused again: the recipe bound to the stale revision never expands.
        current = queries.object_revision(window(), target)
        edited = dict(stale, revision=current, recipe="material-setup",
                      parameters={"name": "Late Material", "base_color": [1, 0, 0, 1]})
        call, refused, executor = recipe_request_unqueued(controller, "rebound-to-stale", dict(edited, revision=revision))
        report["rebound_to_stale"] = refused[0] if refused else None
        check("stale_refusal_still_refuses_a_stale_rebinding", call is None and refused
              and refused[0].get("error_code") == "STALE_REVISION" and "recovery_required" not in refused[0]
              and len(target.material_slots) == 0)
        # An edit of the target that may have run holds every rebinding, even to the live revision, until the
        # target is read again: refused before expansion or approval.
        controller.recovery_barrier.refused("angelo_apply_recipe", edited, {
            "error": "The commit stopped after it began", "error_code": "STALE_REVISION",
            "partial_changes_possible": True, "mutation_started": True, "refusal_details": {"handles": [handle]}})
        call, barred, executor = recipe_request_unqueued(controller, "barred-recipe", edited)
        report["barred_recipe"] = barred[0] if barred else None
        check("a_hold_bars_rebinding_until_the_target_is_read", call is None and barred
              and barred[0].get("error_code") == "RECOVERY_REQUIRED" and len(target.material_slots) == 0)
        controller._begin_metrics()  # A new human request renews recovery authority.
        # Stale after review: the reviewed program's binding fails closed at staging.
        call, responses, executor = recipe_request(controller, "edited-recipe", edited)
        check("second_recipe_awaits_approval", call.approval and call.program_arguments is not None)
        target.location = (0.0, 0.0, 9.0)  # Manual edit after the review.
        window().view_layer.update()
        controller.approve_tool(call, executor, DOCUMENT)
        result = drive(controller, executor, responses)
        check("manual_edit_after_review_fails_closed", "error" in result and len(target.material_slots) == 0
              and not native.runtime_is_staging() and not native.has_active_programs())
    finally:
        controller.busy = False
        controller.close()


def feed(objects):
    filler = window().scene.objects["Filler 05"]
    boundary()
    filler.location = (0.0, 4.0, 0.0)
    window().view_layer.update()
    filler.select_set(True)
    window().view_layer.update()
    changes = boundary()["changes_since_last_turn"]
    rows = [o for o in changes["objects"] if o["handle"] == queries.object_handle(filler)]
    check("feed_reports_manual_transform", changes["status"] == "TRACKED" and len(rows) == 1 and rows[0]["changes"] == ["transform"])
    check("feed_reports_selection_change", changes["selection_changed"])
    check("feed_resets_after_the_turn", boundary()["changes_since_last_turn"]["objects"] == [])


def undo_resets_epoch(objects):
    bpy.ops.ed.undo_push(message="Turn context baseline")
    boundary()
    window().scene.objects["Filler 06"].location = (0.0, 0.0, 6.0)
    window().view_layer.update()
    bpy.ops.ed.undo_push(message="Turn context manual edit")
    bpy.ops.ed.undo()
    changes = boundary()["changes_since_last_turn"]
    check("undo_reports_epoch_change", changes["status"] == "DOCUMENT_EPOCH_CHANGED" and changes["reason"] == "undo"
          and "objects" not in changes)
    check("tracking_resumes_after_undo_boundary", boundary()["changes_since_last_turn"]["status"] == "TRACKED")


def load_resets_epoch():
    path = OUT / "turn_context_fixture.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    boundary()
    bpy.ops.wm.open_mainfile(filepath=str(path))
    window().view_layer.update()  # The loaded file's first evaluation precedes the next boundary.
    check("depsgraph_handler_survives_load", track_scene_changes in bpy.app.handlers.depsgraph_update_post)
    changes = boundary()["changes_since_last_turn"]
    check("load_reports_epoch_change", changes["status"] == "DOCUMENT_EPOCH_CHANGED" and changes["reason"] == "load")
    moved = window().scene.objects["Filler 07"]
    moved.location = (7.0, 0.0, 0.0)
    window().view_layer.update()
    rows = boundary()["changes_since_last_turn"]["objects"]
    check("feed_records_after_load", [o["name"] for o in rows] == ["Filler 07"])


def main():
    started = time.perf_counter()
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    bpy.context.preferences.edit.use_global_undo = True
    queries.invalidate()
    change_feed.clear()
    native.register()
    bpy.utils.register_class(TurnContextCommit)
    bpy.utils.register_class(ControllerCommit)
    for name, handler in HANDLERS:
        getattr(bpy.app.handlers, name).append(handler)
    try:
        objects = build_scene()
        ordering(objects)
        apply_without_discovery(objects)
        manual_edit_fails_closed(objects)
        post_commit_revisions_bind_the_next_program(objects)
        preview_revisions_survive_until_accept(objects)
        named_and_changed_objects_are_revised(objects)
        preempted_send_binds_the_named_object(objects)
        recipe_applies_in_one_call(objects)
        feed(objects)
        undo_resets_epoch(objects)
        load_resets_epoch()
        report["completed"] = True
    except Exception:
        report.update(completed=False, error=traceback.format_exc())
    finally:
        for name, handler in HANDLERS:
            handlers = getattr(bpy.app.handlers, name)
            if handler in handlers:
                handlers.remove(handler)
        native.unregister()
        bpy.utils.unregister_class(TurnContextCommit)
        bpy.utils.unregister_class(ControllerCommit)
        change_feed.clear()
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_TURN_CONTEXT", json.dumps(report), flush=True)
    assert report["completed"], report.get("error")


if __name__ == "__main__":
    main()
