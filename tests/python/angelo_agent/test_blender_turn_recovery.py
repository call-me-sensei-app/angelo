# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Failed plan steps in the compiled app: a long round, failed lookups, a rolled-back commit, a script that raises.

Real default startup and real turns through the stand-in ``claude`` CLI, which
plays the rounds this suite scripts (``fake_provider_cli.play_round``): the
typed-plan loop, approvals, staging, commit, reviewed Python and Blender's Undo
are the app's own. No account, network or inference.

Covered, in one chat under Ask before changes (chosen after checking that a new
chat starts in Auto):

* A round that streams its thinking shows a thinking row and then
  "Preparing the next steps" while its plan is written; the chip shows the
  catalog name of a model named by its full ID.
* A lookup of check schemas is not read as verification, a lookup the host
  cannot answer goes back to the model with the other results, and an add-on's
  settings type (Cycles) is found.
* A program that deletes an object still referenced by an undeclared artist
  object fails at commit and rolls back; the script after it never runs; the
  model gets a corrected plan, whose program is approved again and commits.
* A reviewed script that adds one object, deletes another, changes a camera
  setting, enters Edit Mode and then raises is undone by the host from its
  restore point: the deleted object, the camera setting and the mode are back,
  the result names the failing line, the person and the model are told, and
  the corrected script is reviewed and approved again. One Undo takes the
  corrected script back; the failed one left one step that changes nothing
  before the person's own.
* A script that finishes is one Undo step, with no empty step before it.
* While the person is in Edit Mode a script gets no restore point: one that
  changes a camera setting and raises is not reported rolled back, and the
  setting really is still changed.
* A script that pushes its own Undo step before raising is left alone: what it
  changed stays, and the person and the model are told exactly that.
* The receipt of a request says how it went: a script that raised, and which
  round of a later request received the view the model asked for.
* A script that uses the application preferences says so on its approval card.

Run through ``run_native_suites.py --gui --include test_blender_turn_recovery``;
check ``turn-recovery.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-turn-recovery-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from turn_recovery_fixtures import (CORRECTED, IN_EDIT_MODE, OWN_STEP, PLAIN, PREFERENCES, RAISING,  # noqa: E402
                                    lookups_rounds, operation, result, reviewed)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
REPORT = OUT / "turn-recovery.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {},
         "fixture": "Stand-in claude CLI playing scripted rounds; no browser, account, network or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def actions():
    return [(action, value) for _, action, value in view_state().get("hits", [])]


def click(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in view_state()["hits"] if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_TURN_RECOVERY", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def prompt(index):
    return (ROUNDS / ("prompt-%d.txt" % index)).read_text(encoding="utf-8")


def script(index, *events):
    """The round the stand-in model plays for the next ``index``-th user message of the session."""
    (ROUNDS / ("round-%d.json" % index)).write_text(json.dumps(list(events)), encoding="utf-8")


def pending(name):
    from angelo_agent import ui
    return next((call for call in ui.get_controller().pending.values() if call.name == name and call.approval), None)


def rows(role=None, tool=None):
    from angelo_agent import ui
    return [m for m in ui.get_controller().state["messages"]
            if (role is None or m["role"] == role) and (tool is None or m.get("tool") == tool)]


def view3d():
    area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def undo():
    with bpy.context.temp_override(window=window()):
        return bpy.ops.ed.undo()


def lens():
    return round(window().scene.camera.data.lens, 3)


def edit_mode(mode):
    """The person enters or leaves Edit Mode on the committed cube, as one Undo step."""
    area, region = view3d()
    window().view_layer.objects.active = bpy.data.objects["Second Try"]
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode=mode)


def own_edit():
    """The person's own edit, one Undo step: a cone, whose name is returned."""
    area, region = view3d()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.mesh.primitive_cone_add('EXEC_DEFAULT', True, location=(-4, 0, 0))
        return bpy.context.view_layer.objects.active.name


def rollback_rounds():
    """Rounds 2 and 3: a commit-only delete refusal, then an approved correction."""
    from angelo_agent import scene_queries
    # Active-camera deletion now fails during bind, before any approval. An
    # undeclared object's reverse dependency remains a genuine live-Main
    # commit refusal, so it exercises the original rollback/approval contract.
    target = bpy.data.objects.new("Recovery Delete Target", None)
    dependent = bpy.data.objects.new("Recovery Delete Dependent", None)
    window().scene.collection.objects.link(target)
    window().scene.collection.objects.link(dependent)
    dependent.parent = target
    window().view_layer.update()
    state["observed"].update(delete_target=target.name, delete_dependent=dependent.name)
    handle = scene_queries.object_handle(target)
    delete = operation("angelo_scene_program", mode="APPLY", read_set=[], expected_checks=[],
                       write_set=[{"handle": handle, "revision": scene_queries.object_revision(window(), target)}],
                       operations=[{"kind": "object_delete", "target": handle, "parameters": {}}])
    never = operation("angelo_execute_python", description="Add a marker that must never exist",
                      code="import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Never Built', None))")
    cube = operation("angelo_scene_program", mode="APPLY", read_set=[], write_set=[], operations=[
        {"kind": "create_primitive", "target": None, "parameters": {
            "primitive": "CUBE", "name": "Second Try", "alias": "second", "size": 2, "location": [4, 0, 0]}}],
        expected_checks=[{"kind": "dimensions", "target": "$second", "expected": [2, 2, 2], "tolerance": 0.001, "space": "WORLD"}])
    script(2, result({"reply": "", "operations": [delete, never], "verify": True}))
    script(3, result({"reply": "Another object still uses the target, so I left it and added the cube.", "operations": [cube]}))


def progress_phase(phase, controller):
    """Agent progress rows: work groups fold and open, titles are friendly, notice Details start closed."""
    from angelo_agent import activity_text
    checks, observed = state["checks"], state["observed"]
    drawn = view_state().get("progress_drawn", {})
    groups = [v for a, v in actions() if a == "expand" and isinstance(v, tuple) and v[:1] == ("group",)]
    notices = [v for a, v in actions() if a == "expand" and isinstance(v, tuple) and v[:1] == ("notice",)]
    if phase == "progress_rows":
        if not wait(lambda: groups and notices):
            return .2
        steps = rows("activity")
        titles = [activity_text.step_title(m) for m in steps]
        observed["progress_titles"] = sorted(set(titles))
        checks["progress_titles_never_show_tool_ids"] = bool(titles) and not any("angelo_" in t for t in titles)
        checks["progress_drawn_rows_are_friendly"] = bool(drawn) and not any(
            "angelo_" in str(title) or "Error" in str(title) or "OBJECT_IN_USE" in str(title)
            for _, title, _ in drawn.values())
        checks["progress_groups_start_folded"] = not any(g in view_state().get("expanded", set()) for g in groups)
        checks["progress_notice_details_start_closed"] = not any(n in view_state().get("expanded", set()) for n in notices)
        observed["progress_group_titles"] = [drawn[g][1] for g in groups if g in drawn]
        observed["progress_notice_headlines"] = [drawn[n[1]][1] for n in notices if n[1] in drawn]
        observed["progress_folded_hits"] = len(actions())
        capture("progress-1-groups-folded.png")
        observed["progress_group"] = groups[0]
        click("expand", groups[0])
        state["phase"] = "progress_open"
        return .4
    if phase == "progress_open":
        group = observed["progress_group"]
        if not wait(lambda: group in view_state().get("expanded", set())):
            return .2
        failures = [n for n in notices if drawn.get(n[1], ("", "", ""))[2] == "coral"]  # An error notice's Details.
        if failures and not observed.get("progress_notice"):
            observed["progress_notice"] = failures[0]
            click("expand", failures[0])
            return .4
        opened_steps = [key for key, (role, _, _) in drawn.items() if role == "activity"]
        checks["progress_group_opens_to_its_steps"] = bool(opened_steps) and len(actions()) > observed["progress_folded_hits"]
        checks["progress_notice_details_open_on_click"] = observed.get("progress_notice") in view_state().get("expanded", set())
        capture("progress-2-group-and-details-open.png")
        if not bpy.app.build_options.international:
            state["progress_arabic"] = "WITH_INTERNATIONAL is off: Arabic not checked"
            finish()
            return None
        bpy.context.preferences.view.use_translate_interface = True
        bpy.context.preferences.view.language = 'ar_EG'
        state["phase"] = "progress_arabic"
        return .8
    if phase == "progress_arabic":
        from angelo_agent.ui_text import is_rtl
        checks["progress_arabic_is_right_to_left"] = is_rtl()
        capture("progress-3-arabic.png")
        bpy.context.preferences.view.language = 'en_US'
        area, _ = chat()
        edge, middle = area.x - 1, area.y + area.height // 2
        target = area.x + area.width - 320
        window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=edge, y=middle)
        window().event_simulate(type='LEFTMOUSE', value='PRESS', x=edge, y=middle)
        for x in (edge + 4, (edge + target) // 2, target):
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=middle)
        window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=target, y=middle)
        state["phase"] = "progress_narrow"
        return .6
    area, region = chat()
    if not wait(lambda: view_state().get("hit_region") == (region.width, region.height)):
        return .2
    observed["progress_narrow_width"] = area.width
    checks["progress_rows_fit_a_narrow_chat"] = area.width <= 360 and all(
        rect[0] >= 0 and rect[0] + rect[2] <= region.width + 1 for rect, _, _ in view_state().get("hits", []))
    capture("progress-4-narrow.png")
    finish()
    return None


def tick():
    try:
        assert time.monotonic() - started < 420, "Turn-recovery fixture timed out in " + state["phase"]
        from angelo_agent import director, ui, quick_connect
        from angelo_agent.approvals import approval_message, review_arguments
        from angelo_agent.chat_settings import selected_model
        from angelo_agent.chat_popovers import model_name
        checks, phase, observed = state["checks"], state["phase"], state["observed"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            click("welcome_connect", "claude_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models and ui.connection_available()):
                return .2
            checks["a_new_chat_starts_in_auto"] = controller.state["options"]["approval"] == "AUTO_BOUNDED"
            # A full model ID (the catalog lists the alias "opus"); Ask, because this suite clicks every Approve.
            controller.set_options(model="claude-opus-5-5", approval="ASK")
            chat()[0].tag_redraw()
            state["phase"] = "chip"
            return .4
        if phase == "chip":
            capture("recovery-1-model-chip.png")
            model = selected_model(controller)
            checks["chip_shows_the_catalog_name_of_a_full_model_id"] = model is not None and model_name(model) == "Opus"
            checks["ask_before_changes_is_the_mode"] = controller.state["options"]["approval"] == "ASK"
            for index, events in lookups_rounds().items():
                script(index, *events)
            send("Which type holds the Cycles render settings here, and what is its samples property?")
            state["phase"] = "long_round"
            return .2
        if phase == "long_round":
            if controller.status == "Preparing the next steps" and "plan_status_seen" not in observed:
                observed["plan_status_seen"] = True
                capture("recovery-2-preparing-the-next-steps.png")
            if not wait(lambda: not controller.busy and played() == 2):
                return .1
            thinking = rows("thinking")
            checks["thinking_streams_into_a_row"] = (len(thinking) == 1 and thinking[0]["status"] == "completed"
                                                     and thinking[0]["text"].count("Cycles settings") == 3)
            checks["plan_writing_is_named_while_it_happens"] = observed.get("plan_status_seen") is True
            lookups = rows("activity", "angelo_agent_capabilities") + rows("activity", "angelo_blender_api")
            checks["every_lookup_ran_and_only_the_unknown_one_failed"] = [m["status"] for m in lookups] == [
                "completed", "failed", "completed"]
            checks["check_schemas_are_not_verification_evidence"] = not any(
                "inconclusive" in m["text"] for m in rows("system"))
            checks["cycles_settings_type_is_found"] = lookups[2]["result"]["property"]["type"] == "INT"
            head = prompt(1).split("\n", 1)[0]
            checks["the_failed_lookup_went_back_to_the_model"] = (
                "operations[1] (angelo_blender_api) failed: Unknown installed RNA type" in head)
            checks["the_turn_completed"] = controller.status == "Completed" and rows("assistant")[-1]["text"].startswith(
                "The settings type is CyclesRenderSettings")
            counts = controller._request_metrics.snapshot()["counts"]
            observed["lookup_counts"] = counts
            checks["two_model_rounds_one_failed_tool"] = (counts.get("model_rounds"), counts.get("tool_failures")) == (2, 1)
            observed["camera"] = window().scene.camera.name
            rollback_rounds()
            send("Delete Recovery Delete Target, then build the marker.")
            state["phase"] = "delete_approval"
            return .2
        if phase == "delete_approval":
            call = pending("angelo_scene_program")
            if not wait(lambda: call is not None and ("approve", call.id) in actions()):
                return .2
            capture("recovery-3-delete-approval.png")
            observed["first_program"] = call.id
            click("approve", call.id)
            state["phase"] = "corrected_approval"
            return .3
        if phase == "corrected_approval":
            call = pending("angelo_scene_program")
            if not wait(lambda: call is not None and call.id != observed["first_program"] and ("approve", call.id) in actions()):
                return .2
            capture("recovery-4-corrected-plan-asks-again.png")
            failed = rows("activity", "angelo_scene_program")[0]["result"]
            observed["commit_failure"] = {key: failed.get(key) for key in ("error", "scene_changed", "partial_changes_possible", "status")}
            checks["the_commit_failed_and_rolled_back"] = ("OBJECT_IN_USE" in failed["error"] and failed["scene_changed"] is False
                                                           and failed["partial_changes_possible"] is False)
            checks["the_camera_is_untouched"] = (window().scene.camera is not None
                                                 and window().scene.camera.name == observed["camera"])
            target = window().scene.objects.get(observed["delete_target"])
            dependent = window().scene.objects.get(observed["delete_dependent"])
            checks["the_referenced_target_and_artist_dependency_are_untouched"] = (
                target is not None and dependent is not None and dependent.parent == target)
            checks["the_script_after_the_failed_program_never_ran"] = ("Never Built" not in bpy.data.objects
                                                                       and not rows("activity", "angelo_execute_python"))
            checks["the_person_is_told_why"] = any(m["text"].startswith(
                "The plan stopped before changing the scene; asking for a corrected plan") for m in rows("system"))
            checks["the_model_is_told_nothing_changed"] = prompt(3).startswith(
                "The host stopped your last plan before it changed the scene") and "OBJECT_IN_USE" in prompt(3)
            checks["the_corrected_plan_is_approved_again"] = controller.state["options"]["approval"] == "ASK" and call.approval
            click("approve", call.id)
            state["phase"] = "corrected_done"
            return .3
        if phase == "corrected_done":
            if not wait(lambda: not controller.busy):
                return .2
            checks["the_corrected_program_committed"] = ("Second Try" in bpy.data.objects and controller.status == "Completed"
                                                         and any(m.get("host_summary") for m in rows("system")))
            checks["no_round_after_the_certified_correction"] = played() == 4
            observed["cone"], observed["lens"] = own_edit(), lens()
            script(4, reviewed("Add 'Half Applied', remove 'Second Try', then fail", RAISING, verify=True))
            script(5, reviewed("Add 'Half Applied' at x = 5", CORRECTED, reply="Fixed the index and built it."))
            send("Run the half-applied script.")
            state["phase"] = "script_card"
            return .2
        if phase in ("script_card", "corrected_card", "plain_card", "edit_mode_card", "own_step_card"):
            call = pending("angelo_execute_python")
            if not wait(lambda: call is not None and call.id != observed.get("last_script") and ("approve", call.id) in actions()):
                return .2
            if phase == "corrected_card":
                capture("recovery-5-script-raised-and-undone.png")
                failed = rows("activity", "angelo_execute_python")[0]
                observed["script_failure"] = answer = failed["result"]
                checks["the_error_names_its_type_line_source_and_the_scripts_frames"] = (
                    failed["status"] == "failed" and answer["error"] == "IndexError: list index out of range (line 7)"
                    and (answer["line"], answer["source"]) == (7, "return [0, 1, 2, 3][count]")
                    and answer["traceback"] == ["line 8, in <module>: build(4)", "line 7, in build: return [0, 1, 2, 3][count]"])
                checks["the_result_distinguishes_main_restore_from_unverified_effects"] = (
                    answer["main_data_restored"] is True and answer["rolled_back"] is False and "scene_changed" not in answer
                    and answer["partial_changes_possible"] is True and answer["changes"]["removed"] == []
                    and answer["changes"]["added"] == [])
                checks["the_rollback_is_the_restore_point"] = "restore point the host made" in answer["rollback"]
                checks["the_object_the_script_deleted_is_back"] = "Second Try" in window().scene.objects
                checks["the_camera_setting_and_the_mode_the_script_changed_are_back"] = (
                    lens() == observed["lens"] and window().view_layer.objects.active.mode == 'OBJECT')
                checks["the_object_the_script_added_is_gone"] = "Half Applied" not in bpy.data.objects
                checks["the_persons_own_edit_is_untouched"] = observed["cone"] in window().scene.objects
                checks["the_person_is_told_the_recovery_is_partial"] = any(
                    m["text"] == "The script stopped with an error. Blender data was restored, but image pixels and "
                    "external effects were not verified:\n" + answer["error"]
                    for m in rows("system"))
                head = prompt(5).split("\n", 1)[0]
                checks["the_model_is_told_what_was_restored"] = (
                    "operations[0] (angelo_execute_python) raised: IndexError: list index out of range (line 7)." in head
                    and "The host restored Blender's Main data" in head and "corrected script is safe" in head)
                checks["the_next_script_is_reviewed_and_approved_again"] = (
                    call.approval and call.id not in controller.reviewed_calls and controller.state["options"]["approval"] == "ASK")
            click("approve", call.id)  # The first click shows the code and runs nothing.
            state["phase"] = phase.replace("_card", "_reviewed")
            return .4
        if phase in ("script_reviewed", "corrected_reviewed", "plain_reviewed", "edit_mode_reviewed", "own_step_reviewed"):
            call = pending("angelo_execute_python")
            if not wait(lambda: call is not None and call.id in controller.reviewed_calls):
                return .2
            observed["last_script"] = call.id
            click("approve", call.id)
            state["phase"] = {"script_reviewed": "corrected_card", "corrected_reviewed": "corrected_script_done",
                              "plain_reviewed": "plain_done", "edit_mode_reviewed": "edit_mode_done",
                              "own_step_reviewed": "own_step_done"}[phase]
            return .3
        if phase == "corrected_script_done":
            if not wait(lambda: not controller.busy):
                return .2
            capture("recovery-5b-script-corrected.png")
            halves = [o for o in bpy.data.objects if o.name.startswith("Half Applied")]
            checks["the_models_next_script_finished_the_work"] = (
                controller.status == "Completed" and len(halves) == 1 and tuple(halves[0].location) == (5.0, 0.0, 0.0)
                and played() == 6 and "Second Try" in bpy.data.objects)
            checks["the_camera_setting_was_changed_once_not_twice"] = lens() == round(observed["lens"] * 2, 3)
            undo()
            state["phase"] = "undone_once"
            return .5
        if phase == "undone_once":
            checks["one_undo_reverts_the_corrected_script"] = (
                "Half Applied" not in bpy.data.objects and observed["cone"] in bpy.data.objects
                and "Second Try" in bpy.data.objects and lens() == observed["lens"])
            undo()
            state["phase"] = "undone_twice"
            return .5
        if phase == "undone_twice":
            checks["the_rolled_back_script_left_one_step_that_changes_nothing"] = (
                observed["cone"] in bpy.data.objects and "Second Try" in bpy.data.objects
                and "Half Applied" not in bpy.data.objects and lens() == observed["lens"])
            undo()
            state["phase"] = "undone_thrice"
            return .5
        if phase == "undone_thrice":
            checks["the_persons_own_step_is_the_one_before_it"] = (
                observed["cone"] not in bpy.data.objects and "Second Try" in bpy.data.objects)
            observed["second_cone"] = own_edit()
            script(6, reviewed("Add 'Plain Step'", PLAIN, reply="Added it."))
            send("Add the plain step.")
            state["phase"] = "plain_card"
            return .2
        if phase == "plain_done":
            if not wait(lambda: not controller.busy):
                return .2
            observed["plain"] = [controller.status, played(), "Plain Step" in bpy.data.objects]
            undo()
            state["phase"] = "plain_undone"
            return .5
        if phase == "plain_undone":
            observed["plain"].append(["Plain Step" in bpy.data.objects, observed["second_cone"] in bpy.data.objects])
            undo()
            state["phase"] = "plain_undone_twice"
            return .5
        if phase == "plain_undone_twice":
            checks["a_finished_script_is_one_undo_step_with_no_empty_step_before_it"] = (
                observed["plain"] == ["Completed", 7, True, [False, True]] and observed["second_cone"] not in bpy.data.objects)
            edit_mode('EDIT')  # The person is editing a mesh when the next script runs.
            script(7, reviewed("Change the camera, then fail", IN_EDIT_MODE, verify=True))
            script(8, result({"reply": "The camera's focal length is still doubled; I stopped there.", "operations": []}))
            send("Run the camera script while I edit this mesh.")
            state["phase"] = "edit_mode_card"
            return .2
        if phase == "edit_mode_done":
            if not wait(lambda: not controller.busy):
                return .2
            observed["edit_mode_failure"] = answer = rows("activity", "angelo_execute_python")[-1]["result"]
            checks["in_edit_mode_nothing_is_reported_rolled_back"] = (
                answer["rolled_back"] is False and answer["partial_changes_possible"] is True
                and "EDIT_MESH" in answer["rollback"] and "no restore point" in answer["rollback"]
                and "still in the scene" in answer["rollback"])
            checks["in_edit_mode_the_change_really_remains"] = (
                lens() == round(observed["lens"] * 2, 3) and window().view_layer.objects.active.mode == 'EDIT')
            checks["in_edit_mode_the_turn_went_on_to_the_models_answer"] = controller.status == "Completed" and played() == 9
            edit_mode('OBJECT')
            script(9, reviewed("Add 'Kept Half', push an Undo step, then fail", OWN_STEP, verify=True))
            script(10, result({"reply": "The script left 'Kept Half' in the scene; I stopped there.", "operations": []}))
            send("Run the script that pushes its own undo step.")
            state["phase"] = "own_step_card"
            return .2
        if phase == "own_step_done":
            if not wait(lambda: not controller.busy):
                return .2
            capture("recovery-5c-script-kept.png")
            answer = rows("activity", "angelo_execute_python")[-1]["result"]
            observed["kept_failure"] = answer
            checks["a_script_that_used_the_undo_history_is_not_rolled_back"] = (
                answer["rolled_back"] is False and answer["partial_changes_possible"] is True
                and answer["error"] == "KeyError: 'Scale' (line 4)" and "used the Undo history" in answer["rollback"]
                and bool(answer.get("request_checkpoint")))
            checks["what_it_changed_stays_and_is_listed"] = (
                "Kept Half" in bpy.data.objects and [o["name"] for o in answer["changes"]["added"]] == ["Kept Half"])
            checks["the_person_is_told_the_changes_remain"] = any(m["text"].startswith(
                "The script stopped with an error. Some changes may remain; inspect the scene before continuing")
                for m in rows("system"))
            checks["the_model_is_told_nothing_was_rolled_back"] = (
                "is still in the scene; nothing was rolled back or undone" in prompt(10).split("\n", 1)[0])
            checks["the_turn_went_on_to_the_models_answer"] = controller.status == "Completed" and played() == 11
            observed["script_path"] = controller.telemetry.receipts[-1].get("path")
            checks["the_receipt_says_a_script_raised_and_its_changes_stayed"] = observed["script_path"] == {
                "edits": 1, "steps": [["script", "raised", "", [], 1]]}
            script(11, result({"reply": "", "operations": [operation(
                "angelo_view", targets=[], views=["FRONT"], shading="SOLID", resolution=256)], "verify": True}))
            script(12, result({"reply": "The marker is where the script left it.", "operations": []}))
            send("Look at the scene from the front.")
            state["phase"] = "looked"
            return .2
        if phase == "looked":
            if not wait(lambda: not controller.busy and played() == 13):
                return .2
            observed["view_path"] = controller.telemetry.receipts[-1].get("path")
            checks["the_receipt_says_which_round_saw_the_view"] = observed["view_path"] == {
                "edits": 0, "images_returned": 1, "image_delivery": "confirmed",
                "images_delivered": 1, "images": [[2, 0, {"view:SOLID:AXIS": 1}]]}
            script(13, reviewed("Read the undo depth from the preferences", PREFERENCES, verify=True))
            send("How many undo steps do my preferences keep?")
            state["phase"] = "preferences_card"
            return .2
        if phase == "preferences_card":
            call = pending("angelo_execute_python")
            if not wait(lambda: call is not None and call.id != observed["last_script"] and ("approve", call.id) in actions()):
                return .2
            capture("recovery-6-preferences-script.png")
            warning = approval_message(call.name, review_arguments(call))["warning"]
            checks["a_script_that_uses_preferences_says_so"] = "neither Undo nor a snapshot" in warning
            click("decline", call.id)
            state["phase"] = "declined"
            return .3
        if phase == "declined":
            if not wait(lambda: not controller.busy):
                return .2
            checks["a_decline_still_ends_the_turn"] = controller.status == "Interrupted" and played() == 14
            from angelo_agent import chat_view
            chat_view._scroll[controller.state["id"]] = 10 ** 6  # The top of the transcript: its first work group.
            chat()[0].tag_redraw()
            state["phase"] = "progress_rows"
            return .5
        if phase in ("progress_rows", "progress_open", "progress_arabic", "progress_narrow"):
            return progress_phase(phase, controller)
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
