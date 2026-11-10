# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight's fix in a real window: its approval card, the Approve click and one Ctrl+Z.

Real default (Director) startup and a real turn: the stand-in signed-in ``claude`` CLI (``fake_provider_cli.py``)
plays the model, whose first round calls ``angelo_engine_fix`` and whose second replies; no account, network or
inference. A rig rotated 90 degrees with a skinned child and two identical meshes in separate data blocks give
Unreal two fixable findings (``transform_unapplied``, ``instancing_lost``). Under Ask the card for both is drawn in
the chat (title and one line per fix, ``engine_fix_approvals``), the person's Approve click applies them, and one
Ctrl+Z over the 3D view restores every one, as the person's own last step returns; Ctrl+Shift+Z applies them again.
doc/angelo/engine_preflight.rst ("Fixes"). Run through ``run_native_suites.py --gui --include
test_blender_engine_fix_gui``; check ``engine-fix-gui.json`` for ``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-fix-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')   # The default Director startup, as people get it.
ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
SESSION = "fixture-engine-fix"
fake_provider_cli.sign_in(os.environ["HOME"], "claude")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
REPORT = OUT / "engine-fix-gui.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Stand-in claude CLI, synthetic clicks and keys; no account, network or inference"}
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


def script(index, plan):
    """The plan the stand-in model returns for the session's ``index``-th round."""
    (ROUNDS / ("round-%d.json" % index)).write_text(json.dumps([{
        "type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "duration_ms": 1000,
        "structured_output": plan}]), encoding="utf-8")


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)


def pending_fix():
    from angelo_agent import ui
    return next((call for call in ui.get_controller().pending.values()
                 if call.name == "angelo_engine_fix" and call.approval), None)


def key_over_view(shift=False):
    """Ctrl+Z (Ctrl+Shift+Z with ``shift``) with the pointer over the largest 3D view, as a person presses it."""
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    x, y = area.x + area.width // 2, area.y + area.height // 2
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='Z', value=value, x=x, y=y, ctrl=True, shift=shift)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_ENGINE_FIX_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def build():
    """The person's scene: a rig rotated 90 degrees with its skinned body, and two identical crates in separate
    meshes. Built as a person would, then one Undo step for it."""
    scene = window().scene
    for name in ("FixRig", "FixBody", "FixCrate", "FixCrate.001"):
        if name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name])
    armature = bpy.data.armatures.new("FixRig")
    rig = bpy.data.objects.new("FixRig", armature)
    scene.collection.objects.link(rig)
    with bpy.context.temp_override(window=window(), active_object=rig, object=rig, selected_objects=[rig],
                                   selected_editable_objects=[rig]):
        window().view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        bone = armature.edit_bones.new("Root")
        bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
    body_mesh = bpy.data.meshes.new("FixBody")
    body_mesh.from_pydata([(-.2, -.2, 0), (.2, -.2, 0), (.2, .2, 0), (-.2, .2, 0)], [], [(0, 1, 2, 3)])
    body = bpy.data.objects.new("FixBody", body_mesh)
    scene.collection.objects.link(body)
    body.parent = rig
    body.modifiers.new("Armature", 'ARMATURE').object = rig
    body.vertex_groups.new(name="Root").add(range(4), 1.0, 'REPLACE')
    rig.rotation_euler = (math.radians(90), 0, 0)
    material = bpy.data.materials.new("FixWood")
    for index, name in enumerate(("FixCrate", "FixCrate.001")):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], [],
                         [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
        mesh.materials.append(material)
        crate = bpy.data.objects.new(name, mesh)
        crate.location = (3 + 2 * index, 0, 0)
        scene.collection.objects.link(crate)
    window().view_layer.update()
    with bpy.context.temp_override(window=window()):
        bpy.ops.ed.undo_push(message="Build the fixture")


def fixed():
    rig, crates = bpy.data.objects["FixRig"], [bpy.data.objects["FixCrate"], bpy.data.objects["FixCrate.001"]]
    return {"rig_rotation_x": round(math.degrees(rig.rotation_euler.x), 3), "crates_share": crates[0].data == crates[1].data,
            "body_world_z": round(bpy.data.objects["FixBody"].matrix_world.translation.z, 4)}


def tick():
    try:
        assert time.monotonic() - started < 240, "Engine-fix fixture timed out in " + state["phase"]
        from angelo_agent import director, quick_connect, ui
        checks, phase = state["checks"], state["phase"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            click("welcome_connect", "claude_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models
                        and ui.connection_available()):
                return .2
            build()
            state["before"] = fixed()
            from angelo_agent import engine_preflight
            report = engine_preflight.check(window(), "UNREAL", "GLB")
            wanted = [item for item in report["findings"] if item["fixable"]
                      and item["code"] in {"transform_unapplied", "instancing_lost"}]
            checks["both_findings_are_offered_a_fix"] = {item["code"] for item in wanted} == {
                "transform_unapplied", "instancing_lost"}
            script(0, {"reply": "", "operations": [{"tool": "angelo_engine_fix", "arguments": {
                "target": "UNREAL", "format": "GLB",
                "fixes": [{"code": item["code"], "subject": item["subject"]} for item in wanted]}}]})
            script(1, {"reply": "Fixed both for Unreal.", "operations": []})
            controller.set_options(approval="ASK")   # The card, whichever lane a fix would take.
            send("Get this ready for Unreal")
            state["phase"] = "card"
            return .4
        if phase == "card":
            call = pending_fix()
            if not wait(lambda: call is not None and ("approve", call.id) in actions()):
                return .2
            state["call"] = call.id
            capture("engine-fix-1-card.png")
            from angelo_agent.approvals import approval_message, review_arguments
            call = controller.pending[state["call"]]
            message = approval_message(call.name, review_arguments(call), target_labels=getattr(call, "target_labels", None))
            state["card"] = message
            shown = message["title"] + message["body"] + message.get("warning", "")
            checks["card_names_the_engine"] = "Unreal Engine" in message["title"]
            checks["card_has_a_line_per_fix_with_its_subject"] = "FixRig" in shown and "FixCrate" in shown
            checks["card_shows_no_codes"] = not any(word in shown for word in ("transform_unapplied", "instancing_lost",
                                                                              "link_data", "apply_transform"))
            checks["nothing_changed_before_approve"] = fixed() == state["before"]
            click("approve", state["call"])
            state["phase"] = "applied"
            return .4
        if phase == "applied":
            if not wait(lambda: state["call"] not in controller.pending and not controller.busy):
                return .2
            capture("engine-fix-2-applied.png")
            state["after"] = fixed()
            state["status"] = controller.status
            state["last_messages"] = [{key: str(message.get(key))[:300] for key in ("role", "text", "status")}
                                      for message in controller.state["messages"][-4:]]
            checks["the_turn_completed"] = controller.status == "Completed"
            checks["approve_applied_both_fixes"] = (state["after"]["rig_rotation_x"] == 0.0
                                                    and state["after"]["crates_share"])
            checks["the_body_kept_its_place"] = abs(state["after"]["body_world_z"] - state["before"]["body_world_z"]) < 1e-3
            key_over_view()
            state["phase"] = "undone"
            return .6
        if phase == "undone":
            state["undone"] = fixed()
            capture("engine-fix-3-one-undo.png")
            checks["one_ctrl_z_restores_every_fix"] = (state["after"] != state["before"]
                                                       and state["undone"] == state["before"])
            key_over_view(shift=True)
            state["phase"] = "redone"
            return .6
        if phase == "redone":
            state["redone"] = fixed()
            checks["ctrl_shift_z_applies_them_again"] = (state["after"] != state["before"]
                                                         and state["redone"] == state["after"])
            state["phase"] = "done"
            return .2
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
