# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rig check's card in Director, the view people start in (doc/angelo/engine_skeleton.rst).

Real default startup (Director layout and chat), real window events through ``Window.event_simulate`` on the card.
The scene: a skinned humanoid in an A-pose with a control bone, as a hand-made rig often is. The scenario:

* What the chat bar's + menu row runs (``angelo.skeleton_check``) opens the card for the last engine (Unreal first).
* Clicks on the card: Unity (its usual FBX export: leaf bones and the A-pose show as problems), Godot, the format
  switch to FBX, More problems and Notes open, a problem's Details, Copy Report (the clipboard holds the report as
  shown), Check Again after the rig's control bone is deleted (the finding is gone), the close button.
* A long report with every section open: the rig made worse (scaled, a live constraint, B-Bones), Unity again, every
  problem's and note's Details opened by clicks. The card keeps its size and the findings scroll: the wheel over the
  card reaches the end, and the buttons stay where they were and still work.
* The card itself never changes the scene or the selection (the one edit is the scenario's own fix).

Stand-in provider CLIs (``fake_provider_cli.py``) answer the start-up sign-in checks; no account or inference. Run
through ``run_native_suites.py --gui --include test_blender_engine_skeleton_gui``; check
``engine-skeleton-gui.json`` for ``success`` and ``phase == "complete"``. Screenshots after each visible step.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-skeleton-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "engine-skeleton-gui.json"
state = {"phase": "boot", "success": False, "checks": {}, "steps": {}, "screenshots": {},
         "fixture": "Default Director startup, stand-in Claude connected, a skinned A-pose humanoid with a control "
                    "bone, synthetic window events; no provider, account or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def view():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW'), area.spaces.active


def send(kind, value='NOTHING', x=None, y=None, **extra):
    _, region, _ = view()
    window().event_simulate(type=kind, value=value, x=region.x + region.width // 2 if x is None else int(x),
                            y=region.y + region.height // 2 if y is None else int(y), **extra)


def card():
    from angelo_agent import card_host
    shown = card_host.active_card()
    return shown if shown is not None and shown.kind == "skeleton_check" else None


def click_card(action, value=None):
    """A click on the shown card's control for (action, value), at its centre in window pixels."""
    def run():
        from angelo_agent import card_host
        plan = card_host._active.plan if card_host._active else None
        rect = next((r for r, a, v in plan.hits if a == action and (value is None or v == value)), None)
        assert rect is not None, f"no {action} {value} on the card"
        _, region, _ = view()
        x, y = region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2
        send('MOUSEMOVE', x=x, y=y)
        send('LEFTMOUSE', 'PRESS', x=x, y=y)
        send('LEFTMOUSE', 'RELEASE', x=x, y=y)
    return run


def open_card():
    """What the chat bar's + menu row runs (``chat_destinations`` Feature ``engine_skeleton``): its operator."""
    area, region, _ = view()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.angelo.skeleton_check('INVOKE_DEFAULT', target="UNREAL")


def remove_control_bone():
    """The person's fix between checks: the control bone is deleted; then Check Again on the card."""
    arm = bpy.data.objects["Body"]
    with bpy.context.temp_override(window=window(), active_object=arm, object=arm):
        bpy.ops.object.mode_set(mode="EDIT")
        arm.data.edit_bones.remove(arm.data.edit_bones["CTRL_ik"])
        bpy.ops.object.mode_set(mode="OBJECT")
    click_card("again")()


def make_rig_worse():
    """A long report: the armature scaled, a live constraint and B-Bones on the spine; then Unity on the card."""
    arm = bpy.data.objects["Body"]
    arm.scale = (1.5, 1.5, 1.5)
    arm.data.bones["spine"].bbone_segments = 4
    arm.pose.bones["spine"].constraints.new("COPY_ROTATION").target = arm
    bpy.context.view_layer.update()
    click_card("target", "UNITY")()


def plan_now():
    from angelo_agent import card_host
    return card_host._active.plan if card_host._active else None


def open_next_details():
    """One click on the next problem's or note's Details link not open yet (run again until all are)."""
    shown = card()
    closed = [f["check"] for f in shown.report["findings"] if f["status"] != "pass" and f["check"] not in shown.expanded]
    visible = {v for _r, a, v in plan_now().hits if a == "details"}
    target = next((c for c in closed if c in visible), None)
    if target:
        click_card("details", target)()
    else:   # the next one is below the window: one wheel turn down over the findings
        wheel_down()


def wheel_down():
    rect = plan_now().scroll["findings"]["rect"]
    _, region, _ = view()
    x, y = region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2
    send('MOUSEMOVE', x=x, y=y)
    send('WHEELDOWNMOUSE', 'PRESS', x=x, y=y)


def all_open(a):
    return a["card"] and not a["closed_details"] and a["more_open"] and a["notes_open"]


def scrolled_to_end(a):
    box = a["findings_scroll"]
    return bool(box) and box["offset"] >= box["content"] - box["visible"] - 0.5


def scene_fixture():
    data = bpy.data.armatures.new("Body")
    arm = bpy.data.objects.new("Body", data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    with bpy.context.temp_override(window=window(), active_object=arm, object=arm):
        bpy.ops.object.mode_set(mode="EDIT")
        layout = [("hips", None, (0, 0, 1.0), (0, 0, 1.1)), ("spine", "hips", (0, 0, 1.1), (0, 0, 1.45)),
                  ("head", "spine", (0, 0, 1.55), (0, 0, 1.8)), ("CTRL_ik", None, (0.3, 0.3, 0), (0.3, 0.4, 0))]
        for s, x in (("L", 1), ("R", -1)):
            layout += [("arm." + s, "spine", (x * .15, 0, 1.42), (x * .35, 0, 1.22)),
                       ("fore." + s, "arm." + s, (x * .35, 0, 1.22), (x * .53, 0, 1.04)),
                       ("hand." + s, "fore." + s, (x * .53, 0, 1.04), (x * .58, 0, .99)),
                       ("thigh." + s, "hips", (x * .1, 0, 1.0), (x * .1, -.01, .53)),
                       ("shin." + s, "thigh." + s, (x * .1, -.01, .53), (x * .1, 0, .08)),
                       ("foot." + s, "shin." + s, (x * .1, 0, .08), (x * .1, -.15, 0))]
        for name, parent, head, tail in layout:
            bone = data.edit_bones.new(name)
            bone.head, bone.tail = head, tail
            bone.use_deform = not name.startswith("CTRL")   # a control bone, as a hand-made rig often has
            if parent:
                bone.parent = data.edit_bones[parent]
        bpy.ops.object.mode_set(mode="OBJECT")
    mesh = bpy.data.meshes.new("Skin")
    mesh.from_pydata([(0, 0, 0.1 * i) for i in range(18)], [], [])
    skin = bpy.data.objects.new("Skin", mesh)
    bpy.context.scene.collection.objects.link(skin)
    skin.modifiers.new("Armature", "ARMATURE").object = arm
    for name, *_ in layout:
        if not name.startswith("CTRL"):
            skin.vertex_groups.new(name=name).add([0, 1], 0.5, "REPLACE")
    arm.select_set(True)
    bpy.context.view_layer.update()


def snapshot():
    shown = card()
    report = shown.report if shown else None
    plan = plan_now() if shown else None
    box = plan.scroll.get("findings") if plan else None
    from angelo_agent import card_host
    offset = card_host._active.ui.scroll.get("findings", 0.0) if shown and card_host._active else 0.0
    _, region, _ = view()
    return {"card": shown is not None, "plan_scale": plan.scale if plan else None,
            "card_rect": list(plan.card) if plan else None, "region_height": region.height,
            "findings_scroll": dict(content=box["content"], visible=box["visible"], offset=offset) if box else None,
            "buttons": sorted([a, list(r)] for r, a, v in plan.hits if a in ("copy", "again")) if plan else None,
            "closed_details": [f["check"] for f in report["findings"] if f["status"] != "pass"
                               and f["check"] not in shown.expanded] if report else None, "target": shown.target if shown else None, "format": shown.fmt if shown else None,
            "statuses": {f["check"]: f["status"] for f in report["findings"]} if report else None,
            "notes_open": shown.notes_open if shown else None, "more_open": shown.more_open if shown else None,
            "expanded": sorted(shown.expanded) if shown else None, "dirty": bpy.data.is_dirty,
            "selected": sorted(o.name for o in bpy.context.selected_objects),
            "clipboard": bpy.context.window_manager.clipboard[:200]}


def statuses(a):
    return a["statuses"] or {}


# (name, run, test(before, after), screenshot or None, seconds to wait). Order matters: each starts from the last.
STEPS = (
    ("plus_menu_opens_the_card", open_card,
     lambda b, a: a["card"] and a["target"] == "UNREAL" and "auto_retarget" in statuses(a), "skeleton-unreal.png", 4.0),
    ("card_unity", click_card("target", "UNITY"),
     lambda b, a: a["target"] == "UNITY" and a["format"] == "FBX" and statuses(a).get("rest_pose") == "warn",
     "skeleton-unity.png", 3.0),
    ("card_more_problems", click_card("more"), lambda b, a: a["more_open"], "skeleton-unity-more.png", 3.0),
    ("card_details", click_card("details", "rest_pose"), lambda b, a: a["expanded"] == ["rest_pose"],
     "skeleton-unity-details.png", 3.0),
    ("card_godot", click_card("target", "GODOT"), lambda b, a: a["target"] == "GODOT" and a["format"] == "GLB",
     "skeleton-godot.png", 3.0),
    ("card_notes", click_card("notes"), lambda b, a: a["notes_open"], "skeleton-godot-notes.png", 3.0),
    ("card_fbx", click_card("format", "FBX"), lambda b, a: a["format"] == "FBX" and
     statuses(a).get("leaf_bones") == "warn", None, 3.0),
    ("card_copy", click_card("copy"), lambda b, a: a["clipboard"].startswith("Rig check for Godot"), None, 3.0),
    ("card_check_again", remove_control_bone,
     lambda b, a: statuses(b).get("deform_only") == "warn" and statuses(a).get("deform_only") == "pass",
     "skeleton-godot-fixed.png", 3.0),
    ("card_long_report", make_rig_worse,
     lambda b, a: a["target"] == "UNITY" and {"transform", "constraints", "bendy_bones"} <= {
         c for c, v in statuses(a).items() if v != "pass"}, "skeleton-long.png", 3.0),
    ("card_every_section_open", open_next_details,
     lambda b, a: all_open(a) and a["findings_scroll"] is not None and a["plan_scale"] == b["plan_scale"]
     and a["findings_scroll"]["content"] > a["findings_scroll"]["visible"]
     and a["card_rect"][1] >= 0 and a["card_rect"][1] + a["card_rect"][3] <= a["region_height"],
     "skeleton-all-open.png", 20.0),
    ("card_scrolls_to_the_end", wheel_down,
     lambda b, a: scrolled_to_end(a) and a["buttons"] == b["buttons"] and a["plan_scale"] == b["plan_scale"],
     "skeleton-all-open-end.png", 10.0),
    ("card_copy_while_scrolled", click_card("copy"),
     lambda b, a: a["clipboard"].startswith("Rig check for Unity") and scrolled_to_end(a), None, 3.0),
    ("card_closes", click_card("close"), lambda b, a: not a["card"], "skeleton-closed.png", 3.0),
)
REPEAT = {"card_every_section_open", "card_scrolls_to_the_end"}   # run again each poll until the test holds


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"][name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        shots, checks = state["screenshots"], state["checks"]
        checks["engines_look_different"] = len({shots.get(n) for n in (
            "skeleton-unreal.png", "skeleton-unity.png", "skeleton-godot.png")}) == 3
        first = next(iter(state["steps"].values()))["before"]["selected"]
        checks["selection_unchanged"] = all(step["after"]["selected"] == first for step in state["steps"].values())
        state["phase"] = "complete"
        state["success"] = all(checks.values()) and all(s["passed"] is not False for s in state["steps"].values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_ENGINE_SKELETON_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 300, "rig check Director fixture timed out in " + state["phase"]
        from angelo_agent import director, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            bpy.ops.angelo.quick_connect(mode="claude_subscription")
            state["phase"] = "connect"
            return .2
        if state["phase"] == "connect":
            if not ui.connection_available():
                return .2
            send('MOUSEMOVE')
            state["phase"], state["settled"] = "settle", time.monotonic()
            return .2
        if state["phase"] == "settle":
            if time.monotonic() - state["settled"] < 1.5:
                return .2
            state["checks"]["director_is_default"] = director.preferred_mode() == "DIRECTOR"
            scene_fixture()
            state["checks"]["menu_row_registered"] = bool(
                __import__("angelo_agent.chat_destinations", fromlist=["x"]).feature("engine_skeleton"))
            capture("skeleton-start.png")
            state["phase"], state["index"], state["pending"] = "step", 0, None
            save()
            return .3
        if state["phase"] == "step":
            if state["pending"]:
                name, test, shot, before, sent, wait = state["pending"]
                after = snapshot()
                passed = None if test is None else bool(test(before, after))
                if passed is False and time.monotonic() - sent < wait:
                    if name in REPEAT and time.monotonic() - state.get("repeated", 0) > .4:
                        state["repeated"] = time.monotonic()
                        STEPS[state["index"]][1]()
                    return .1  # A card click takes a few event-loop passes.
                state["steps"][name] = {"passed": passed, "before": before, "after": after}
                state["pending"] = None
                state["index"] += 1
                if shot:
                    capture(shot)
                save()
            if state["index"] >= len(STEPS):
                finish()
                return None
            name, run, test, shot, wait = STEPS[state["index"]]
            before = snapshot()
            run()
            state["pending"] = (name, test, shot, before, time.monotonic(), wait)
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
