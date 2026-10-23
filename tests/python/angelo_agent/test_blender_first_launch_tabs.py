# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A fresh profile's first launch with Manual's Media Studio and Generation Flows tabs (1.0.5, ``studio_workspaces``).

1.0.5 first launch: building the tabs switched the window's workspace under the setup card, which closed unanswered;
the welcome never opened and the window was left in Manual on the Animation tab, without the chat. Now the session's
first build is part of the start-up: it runs under the start-up cover before Director's layout, and the cards wait.
Real first launch (Blender's splash, the setup card, the welcome) and real clicks; no CLI, account, network or
inference. Covered, each with a screenshot: the start-up build is made to stop part-way (the Generation Flows tab's
fill fails): by the time the setup card shows, it has finished, kept the Media Studio tab and removed the half-made
Generation Flows tab, and no build runs under the card; the build ran before Director's chat showed, and once the
chat shows the window never leaves it (watched every event-loop pass: 1.0.5 RC switched it ~2.5 s in); Continue opens
the welcome over Director with its chat, and no build runs under it however long it stays; once Start creating closes
it, the next quiet moment builds Generation Flows after all, both tabs filled (Media Studio, the flow canvas), the
window still in Director with its chat. Manual then shows an ordinary workspace with its 3D view, never a half-made
tab. Last, a file as 1.0.4 saved it (no tab, no record) opens with both tabs built once, at a quiet moment, and
recorded. The runner fails the suite on any Python traceback in its log.

Run through ``run_native_suites.py --gui --include test_blender_first_launch_tabs``; check
``first-launch-tabs.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
from angelo_agent import studio_workspaces  # noqa: E402
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

# The start-up build stops part-way: Generation Flows' fill fails until the welcome has closed ("built" phase). Set
# before the start-up build can run: it starts at the first timer pass, and --python runs before the event loop.
_FILL, _FAIL = studio_workspaces.fill, {"flows": True}


def _failing_fill(window_, kind):
    if kind == "flows" and _FAIL["flows"]:
        raise RuntimeError("fixture: this tab's editor could not be set")
    return _FILL(window_, kind)


studio_workspaces.fill = _failing_fill
_BUILD, BUILDS = studio_workspaces.build, []   # Every build started: (time, kinds).


def _counted_build(window_, kinds):
    BUILDS.append((time.monotonic(), list(kinds)))
    return _BUILD(window_, kinds)


studio_workspaces.build = _counted_build
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-first-launch-tabs-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "first-launch-tabs.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": []}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y):
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=int(x), y=int(y))


def click_card(action):
    from angelo_agent import canvas_rtl, card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in reversed(active.plan.hits) if a == action)
    press(region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width), region.y + rect[1] + rect[3] / 2)


def click_welcome(action):
    from angelo_agent import canvas_rtl, intro
    area = next(a for a in window().screen.areas if a.as_pointer() == intro._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action)
    press(region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width), region.y + rect[1] + rect[3] / 2)


def settled():
    from angelo_agent import director, ui
    return qa_boot.started()


def has_chat():
    from angelo_agent import console
    return any(console.is_chat(a) for a in window().screen.areas)


def tabs():
    from angelo_agent import studio_workspaces
    return studio_workspaces.tab("studio"), studio_workspaces.tab("flows")


def filled():
    """Both tabs hold their one editor: Media Studio, and the flow canvas."""
    from angelo_agent import flow_nodes, studio
    studio_tab, flows_tab = tabs()
    if studio_tab is None or flows_tab is None:
        return False
    studio_areas = [a for s in studio_tab.screens for a in s.areas]
    flows_areas = [a for s in flows_tab.screens for a in s.areas]
    return (len(studio_areas) == 1 and studio.is_studio(studio_areas[0])
            and len(flows_areas) == 1 and flows_areas[0].ui_type == flow_nodes.TREE)


def half_made():
    """Workspaces marked as a tab that hold anything but their one editor."""
    from angelo_agent import studio_workspaces
    return [w.name for w in bpy.data.workspaces if studio_workspaces.tab_kind(w)
            and sum(len(s.areas) for s in w.screens) != 1]


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_FIRST_LAUNCH_TABS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import card_host, director, intro, studio_workspaces
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: settled() and director.is_director(window()) and card_host.is_active("setup")
                        and card_host._active.plan is not None, 60):
                return .3
            state["phase"] = "setup"
            return 1.5   # Long past the build's own quiet time: it must still be waiting.
        if phase == "setup":
            capture("first-launch-tabs-0-setup-card.png")
            checks["the_setup_card_shows_over_director"] = card_host.is_active("setup") and director.is_director(window())
            state["startup_build"] = dict(studio_workspaces.last_build)
            studio_tab, flows_tab = tabs()
            checks["the_start_up_build_ran_before_the_setup_card"] = (
                bool(state["startup_build"]) and not studio_workspaces._building)
            checks["a_stopped_start_up_build_kept_media_studio_and_rolled_back_flows"] = (
                studio_tab is not None and flows_tab is None and not half_made()
                and "Generation Flows" in state["startup_build"].get("rolled_back", []))
            state["chat_seen_at"] = round(_seen["chat_at"] - started, 2) if _seen["chat_at"] else None
            state["left_the_chat"] = list(_seen["left"])
            checks["the_window_never_leaves_its_chat_once_it_shows"] = (
                _seen["chat_at"] is not None and not _seen["left"])
            checks["the_tabs_were_built_before_the_chat_showed"] = (
                bool(BUILDS) and _seen["chat_at"] is not None and BUILDS[0][0] < _seen["chat_at"])
            click_card("continue")
            state["phase"] = "welcome"
            return .5
        if phase == "welcome":
            if not wait(phase, lambda: intro.is_open() and "plan" in intro._open):
                return .2
            state["phase"] = "welcome_hold"
            return 2.5
        if phase == "welcome_hold":
            capture("first-launch-tabs-1-welcome.png")
            checks["the_welcome_opens_after_the_setup_card"] = intro.is_open()
            checks["no_tab_is_built_under_the_welcome"] = (tabs()[1] is None and not studio_workspaces._building
                                                           and studio_workspaces.last_build == state["startup_build"])
            checks["director_and_its_chat_under_the_welcome"] = director.is_director(window()) and has_chat()
            click_welcome("start")
            state["phase"] = "built"
            return .5
        if phase == "built":
            if not wait(phase, lambda: not intro.is_open() and not studio_workspaces._building, 10):
                return .3
            _FAIL["flows"] = False   # From now on the fill works: the retry must build it.
            state["phase"] = "retried"
            return .5
        if phase == "retried":
            if not wait(phase, lambda: filled() and studio_workspaces.last_build.get("done") is True
                        and not studio_workspaces._building, 20):
                return .3
            state["phase"] = "built_settled"
            return 1.0
        if phase == "built_settled":
            capture("first-launch-tabs-3-built.png")
            state["last_build"] = dict(studio_workspaces.last_build)
            checks["the_next_quiet_moment_builds_both_tabs_filled"] = filled() and not half_made()
            checks["the_window_stays_in_director_with_its_chat"] = director.is_director(window()) and has_chat()
            if studio_workspaces.strip() is not None:   # A build with the native tab order (1.0.5 C++).
                checks["the_strip_is_in_order"] = studio_workspaces.in_order()
            director.request_mode(window(), "MANUAL", persist=False)
            state["phase"] = "manual"
            return 1.0
        if phase == "manual":
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .3
            capture("first-launch-tabs-4-manual.png")
            workspace = window().workspace
            state["manual_workspace"] = workspace.name
            checks["manual_shows_an_ordinary_workspace_with_its_3d_view"] = (
                studio_workspaces.tab_kind(workspace) is None
                and any(a.type == 'VIEW_3D' for a in window().screen.areas) and not half_made())
            studio_workspaces.fill = _FILL
            state["phase"] = "strip_tabs"
            return .3
        if phase == "strip_tabs":   # A file as 1.0.4 saved it: no Media Studio or Generation Flows tab, no record.
            present = [w for w in tabs() if w is not None]
            if present:
                if window().workspace != present[0]:
                    window().workspace = present[0]
                    return .4
                with bpy.context.temp_override(window=window()):
                    bpy.ops.workspace.delete()
                return .5
            for workspace in bpy.data.workspaces:
                if studio_workspaces.FLAG in workspace:
                    del workspace[studio_workspaces.FLAG]
            state["old_file"] = str(OUT / "saved-by-1.0.4.blend")
            bpy.ops.wm.save_as_mainfile(filepath=state["old_file"])
            state["builds_before_open"] = len(BUILDS)
            state["opened_at"] = time.monotonic()
            bpy.ops.wm.open_mainfile(filepath=state["old_file"])
            state["phase"] = "old_file"
            return 1.0
        if phase == "old_file":
            if not wait(phase, lambda: filled() and settled() and not studio_workspaces._building, 30):
                return .3
            state["phase"] = "old_file_settled"
            return 2.0   # Long enough for a second build, were one to start.
        if phase == "old_file_settled":
            capture("first-launch-tabs-5-old-file.png")
            builds = BUILDS[state["builds_before_open"]:]
            state["old_file_builds"] = [(round(at - state["opened_at"], 2), kinds) for at, kinds in builds]
            checks["a_file_saved_by_1_0_4_gets_both_tabs_built_once"] = (
                filled() and not half_made() and len(builds) == 1 and sorted(builds[0][1]) == ["flows", "studio"])
            checks["that_build_waits_for_a_quiet_moment"] = bool(builds) and (
                builds[0][0] - state["opened_at"] >= studio_workspaces.QUIET * 0.9)
            checks["the_file_records_its_tabs_once"] = studio_workspaces.added_once() == {"studio", "flows"}
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


_seen = {"chat_at": None, "home": None, "left": []}   # From the chat's first frame: each pass on another workspace.


def watch():
    """Every event-loop pass until the setup card's click: once the chat shows, the window never leaves it."""
    if state["phase"] not in ("boot", "setup"):
        return None
    try:
        workspace = window().workspace.name
        if _seen["chat_at"] is None and has_chat():
            _seen["chat_at"], _seen["home"] = time.monotonic(), workspace
        elif _seen["chat_at"] is not None and workspace != _seen["home"] and len(_seen["left"]) < 20:
            _seen["left"].append((round(time.monotonic() - started, 2), workspace))
    except (ImportError, AttributeError, IndexError):   # The add-on or the window is not there yet.
        pass
    return 0.0


bpy.app.timers.register(tick, first_interval=.5, persistent=True)   # Outlives the 1.0.4 file's load.
bpy.app.timers.register(watch, first_interval=0.0)
