# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tool calls that land while Manual's tab build runs wait for it, then each runs exactly once, in order.

1.0.5 RC: the build closes the extra areas of the tab it makes; a script that changed one of them left notifiers
visiting a freed area, and the app crashed (asset_browser_states, wm_event_do_notifiers after ED_area_do_listen).
Agent turns, connected apps' calls and approved script files all reach Blender through the GUI pump's one
``pump_controller_tools`` call, which holds them while the build runs, at most ``TOOLS_WAIT`` seconds; held calls are
never dropped or reordered (1.0.5 final round: dispatch_fixes queues 32 programs on the tick the add-on registers).

The real app and the real UI pump, twice:
* start-up: ``CALLS`` calls are requested on the chat's controller on the very tick the add-on is enabled, as
  dispatch_fixes does, while the session's first build starts;
* mid-session: a file saved as 1.0.4 saved it (no Media Studio or Generation Flows tab, no record) is opened, and
  the moment its build starts its sphere is prepared for sculpting (Sculpt Mode), ``CALLS`` more calls are requested,
  and ``CALLS`` zero-pressure sculpt programs on the sphere, as dispatch_fixes queues them.
Each time: every call ran exactly once, natively, in the order requested, each with its one result and no error;
none ran while the build held the window; the wait stayed under the cap; both tabs were built; and the cube, in
Sculpt Mode throughout, stayed in it (the build passed through other workspaces, whose own object mode Blender
applies on every switch: dispatch_fixes' programs then failed with "Prepare this object for sculpting first"). No
crash (the runner fails the suite on a crash or a Python traceback). No account, network or inference.

Run through ``run_native_suites.py --gui --include test_blender_tools_wait_for_tab_build``; check
``tools-wait-for-tab-build.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-tools-wait-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"),
                  ANGELO_DISABLE_DEFAULT_CHAT="1")
REPORT = OUT / "tools-wait-for-tab-build.json"
CALLS = 12
state = {"phase": "boot", "success": False, "checks": {}, "rounds": {}}
checks = state["checks"]
started = time.monotonic()
rounds = {}   # "startup" / "mid_session" -> {"ids", "requested_at", "results", "executions", "held", ...}


def window():
    return bpy.context.window_manager.windows[0]


def building():
    from angelo_agent import studio_workspaces
    return studio_workspaces._building or studio_workspaces._holding is not None


def tabs_filled():
    from angelo_agent import studio_workspaces
    return studio_workspaces.tab("studio") is not None and studio_workspaces.tab("flows") is not None


SPHERE = "Tools wait sphere"   # The mid-session sculpt programs' object (prepared as dispatch_fixes prepares).


def open_rounds():
    return [record for record in rounds.values() if not record.get("judged")]


def new_turn():
    """After the 1.0.4 file opens, the chat's earlier turn is bound to the document that went away and its calls are
    refused ("No active turn bound to this Blender document": the product's ownership rule). As a person does, the
    chat starts anew on the open document (``Controller.new_chat``), then a turn begins."""
    from angelo_agent import ui
    controller = ui.get_controller()
    controller.busy = False
    controller.new_chat(ui.document_key())


def request(name, tool="angelo_inspect_scene", arguments=None):
    """``CALLS`` calls on the chat's controller, as an agent turn or a connected app requests them."""
    from angelo_agent import ui
    controller = ui.get_controller()
    controller.busy, controller.cancelled = True, False
    controller.state["options"]["approval"] = "AUTO_BOUNDED"
    record = rounds[name] = {"tool": tool, "ids": [], "requested_at": time.monotonic(), "results": [],
                             "executions": [], "held": 0, "inside_the_build": state["phase"] == "opened"}
    for index in range(CALLS):
        identity = "%s-%02d" % (name, index)
        record["ids"].append(identity)
        controller.request_tool(identity, tool, dict(arguments or {}),
                                lambda result, identity=identity: record["results"].append((identity, result)))


def sculpt_programs():
    """Zero-pressure sculpt programs on the sphere, prepared for sculpting first, as dispatch_fixes queues them: the
    geometry never changes, and a program fails if the object is no longer prepared ("Prepare this object for
    sculpting first")."""
    from angelo_agent.blender_tools import BlenderExecutor
    executor, sphere = BlenderExecutor(window()), bpy.data.objects[SPHERE]
    before = executor.execute("angelo_inspect_sculpt", {"object": sphere.name, "probes": []})
    prepared = executor.execute("angelo_prepare_sculpt", {"object": sphere.name, "add_levels": 0,
                                                          "mesh_revision": before["mesh_revision"]})
    if not prepared.get("success") or sphere.mode != 'SCULPT':
        raise AssertionError("The sphere could not be prepared for sculpting: %s" % str(prepared)[:300])
    observed = executor.execute("angelo_inspect_sculpt", {"object": sphere.name, "probes": []})
    return {"object": sphere.name, "mesh_revision": observed["mesh_revision"], "steps": [{
        "brush": "DRAW", "radius": .3, "strength": .5, "direction": "ADD", "symmetry": "NONE",
        "normal": [0, -1, 0], "front_faces_only": True, "spacing": .2,
        "paths": [[{"position": [0, -1, 0], "pressure": 0}]]}]}


def instrument():
    """Counts the real executor's runs and the pump's held ticks; requests the mid-session calls as the 1.0.4 file's
    build starts (``build`` is called by ``studio_tab_build.run`` the moment it starts)."""
    from angelo_agent import studio_tab_build, studio_workspaces
    from angelo_agent.blender_tools import BlenderExecutor
    execute, build, wait = BlenderExecutor.execute, studio_workspaces.build, studio_tab_build.tools_wait

    def counted_execute(self, name, args):
        for record in open_rounds():
            if record["tool"] == name:
                record["executions"].append({"at": time.monotonic(), "while_building": building()})
                break
        return execute(self, name, args)

    def counted_wait():
        held = wait()
        for record in open_rounds():
            record["held"] += int(held)
        return held

    def build_and_request(window_, kinds):
        job = build(window_, kinds)   # A generator: nothing has switched yet.
        if state["phase"] == "opened" and "mid_session" not in rounds:
            state["mid_session_build_started"] = round(time.monotonic() - started, 2)
            new_turn()
            request("mid_session")
            request("mid_session_sculpt", "angelo_sculpt_program", sculpt_programs())
        return job
    BlenderExecutor.execute = counted_execute
    studio_workspaces.build = build_and_request
    studio_tab_build.tools_wait = counted_wait


def sculpting():
    """The object being sculpted: the start-up file's cube, then the prepared sphere (one object sculpts at a time)."""
    return bpy.context.view_layer.objects.active


def judge(name):
    from angelo_agent import studio_tab_build
    record = rounds[name]
    checks[name + "_the_sculpting_object_stays_in_sculpt_mode"] = (sculpting() is not None
                                                                    and sculpting().mode == 'SCULPT')
    record["object_mode"] = sculpting().mode if sculpting() else None
    done = [identity for identity, _ in record["results"]]
    runs = record["executions"]
    record["judged"] = True
    record["last_ran_after"] = round(runs[-1]["at"] - record["requested_at"], 3) if runs else None
    checks[name + "_every_call_ran_exactly_once"] = (len(runs) == CALLS and sorted(done) == sorted(record["ids"])
                                                     and len(set(done)) == len(done))
    checks[name + "_in_the_order_requested"] = done == record["ids"]
    checks[name + "_each_with_its_result"] = all(isinstance(result, dict) and "error" not in result
                                                 for _, result in record["results"])
    checks[name + "_none_ran_while_the_build_held_the_window"] = not any(run["while_building"] for run in runs)
    # Held at most TOOLS_WAIT, then one call per tick: the last ran within the cap plus the queue's own time.
    checks[name + "_under_the_cap"] = (record["last_ran_after"] is not None
                                       and record["last_ran_after"] < studio_tab_build.TOOLS_WAIT + 5.0)
    state["rounds"][name] = {k: v for k, v in record.items() if k != "results"}
    state["rounds"][name]["errors"] = [str(result)[:300] for _, result in record["results"]
                                       if not isinstance(result, dict) or "error" in result][:5]


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    for name in rounds:
        if name not in state["rounds"]:
            state["rounds"][name] = {k: v for k, v in rounds[name].items() if k != "results"}
            state["rounds"][name]["results"] = [(i, str(r)[:300]) for i, r in rounds[name]["results"]][:CALLS]
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_TOOLS_WAIT_FOR_TAB_BUILD", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def finished(name):
    return name in rounds and len(rounds[name]["results"]) >= CALLS and not building()


def tick():
    from angelo_agent import studio_workspaces
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 200, "Suite timed out"
        if phase == "boot":   # The start-up build and the start-up calls are done; both tabs are made.
            if not wait(phase, lambda: tabs_filled() and qa_boot.started() and finished("startup")):
                return .2
            state["phase"] = "startup_settled"
            return 1.0   # Long enough for a second run, were a call run twice.
        if phase == "startup_settled":
            judge("startup")
            checks["startup_both_tabs_were_built"] = tabs_filled()
            state["phase"] = "strip_tabs"
            return .2
        if phase == "strip_tabs":   # A file as 1.0.4 saved it: no Media Studio or Generation Flows tab, no record.
            present = [w for w in (studio_workspaces.tab("studio"), studio_workspaces.tab("flows")) if w is not None]
            if present:
                if window().workspace != present[0]:
                    window().workspace = present[0]
                    return .3
                with bpy.context.temp_override(window=window()):
                    bpy.ops.workspace.delete()
                return .4
            for workspace in bpy.data.workspaces:
                if studio_workspaces.FLAG in workspace:
                    del workspace[studio_workspaces.FLAG]
            area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
            with bpy.context.temp_override(window=window(), area=area):
                bpy.ops.object.mode_set(mode='OBJECT')
                # The program's control point [0, -1, 0] is on a unit sphere at the origin, as dispatch_fixes' sphere:
                # the cube moves out of its way (a point off the observed surface is refused, as it should be).
                bpy.data.objects["Cube"].location = (-4, 0, 0)
                bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(0, 0, 0))
                bpy.context.object.name = SPHERE   # Active, in Object Mode: prepared for sculpting as the build starts
                # (the product refuses to prepare one object while another is in Sculpt Mode).
            path = str(OUT / "saved-by-1.0.4.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            state["phase"] = "opened"
            bpy.ops.wm.open_mainfile(filepath=path)
            return .5
        if phase == "opened":   # Its build starts at a quiet moment; the calls are requested as it starts.
            if not wait(phase, lambda: finished("mid_session") and finished("mid_session_sculpt"), 60):
                return .05
            state["phase"] = "mid_session_settled"
            return 1.0
        if phase == "mid_session_settled":
            judge("mid_session")
            judge("mid_session_sculpt")
            # Never vacuous: the calls were queued from inside the build's start, and the pump held them for it.
            for name in ("mid_session", "mid_session_sculpt"):
                checks[name + "_queued_as_the_build_started"] = rounds[name]["inside_the_build"]
                checks[name + "_the_pump_held_them_while_the_build_ran"] = rounds[name]["held"] > 0
            checks["mid_session_both_tabs_were_built"] = tabs_filled() and not building()
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


from angelo_agent import ui  # noqa: E402  (importing registers nothing)

ui._authentication.start = lambda *args, **kwargs: None   # No account, network or inference.
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None


def enable():
    """As dispatch_fixes: the add-on is enabled and calls are queued on the same tick; the session's first build
    starts at the next pass."""
    bpy.context.preferences.view.show_splash = False
    target = window()
    area = next(a for a in target.screen.areas if a.type == 'VIEW_3D')
    bpy.context.view_layer.objects.active = bpy.data.objects["Cube"]
    with bpy.context.temp_override(window=target, area=area):
        bpy.ops.object.mode_set(mode='SCULPT')   # A build switching workspaces must never change it.
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    instrument()
    request("startup")
    bpy.app.timers.register(tick, first_interval=.5, persistent=True)   # Outlives the 1.0.4 file's load.


bpy.app.timers.register(enable, first_interval=1.0)
