# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Media Studio window keeps its tab, draft and references when Blender gives it a copied area, and only then.

Blender moves a child window with its parent window's workspace, so the Studio window (opened by Duplicate Area into
New Window) is shown a copied screen with a new area whenever Director and Manual mode switch workspaces, and while
Manual's tab build passes the main window through its tabs. Studio kept its state by area pointer, so the copy
started over on the Image tab with no draft and no references (found by the 1.0.6 Workflows suite on Windows,
2026-10-23). ``studio_views`` hands the copy the state of the area it replaced.

Studio's pump was a timer that is not persistent, so a file load removed it: Studio stopped finishing generations,
answering AI Enhance and taking typed input until a restart (shipped in 1.0.4 and the 1.0.5 release build).

The real app, in order, each step checked and captured (Studio window and main window), never stopping at the first
miss so a run on an unfixed build shows every case:
* before any other load: a fixture generation (no network, held until another file is open) is running when another
  file opens; released after the load, it finishes on Studio's own timer (never pumped by hand), and an AI Enhance
  asked for after the load is answered;
* Director: the Studio window opens; its 3D tab is chosen, a draft typed and a reference attached;
* Manual, then Director again: the window shows the same tab, draft and reference (and the area it shows is new);
* the Studio window is closed and opened again: it starts fresh;
* a file as 1.0.4 saved it (no Media Studio or Generation Flows tab) is opened keeping the current windows (Load UI
  off): the kept Studio window starts fresh (nothing carries across a file load); Manual's tab build then runs
  mid-session through the tabs, and the draft and reference given to the Studio window as it starts are kept;
* the same file is opened with its own windows: no shown Studio carries a draft from before the load;
* Studio's pump still runs after both loads (a timer that is not persistent is removed by a file load).
No account, network or inference.

Run through ``run_native_suites.py --gui --include test_blender_studio_mode_switch``; check
``studio-mode-switch.json`` for ``success`` and ``phase == "complete"``.
"""

import base64
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-mode-switch-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_STATE_DIR=str(OUT / "state"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"), ANGELO_DISABLE_DEFAULT_CHAT="1")
REPORT = OUT / "studio-mode-switch.json"
DRAFT, LATER, BUILD = "QA draft kept across the switch", "QA draft before the file load", "QA draft kept across the tab build"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "steps": {},
         "fixture": "Fixture Gemini account and a local fixture transport: no network, no key, nothing paid"}
ENHANCED = "Enhanced fixture prompt: a teal lantern"
gate, calls = threading.Event(), []   # The fixture generation waits on the gate until the other file is open.
picture = []   # The fixture JPEG, painted on the main thread (the transport runs on a worker, which never touches bpy).
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def studio_window():
    from angelo_agent import studio_toggle
    return studio_toggle.opened_window(bpy.context.window_manager.windows)


def studio_area(target=None):
    from angelo_agent import studio
    target = target or studio_window()
    return next((a for a in target.screen.areas if studio.is_studio(a)), None) if target and target.screen else None


def view():
    from angelo_agent import studio
    area = studio_area()
    return studio.view(area) if area else None


def building():
    from angelo_agent import studio_workspaces
    return studio_workspaces._building or studio_workspaces._holding is not None


def settled():
    from angelo_agent import studio_workspaces
    return (qa_boot.started() and not building() and studio_workspaces.tab("studio") is not None
            and studio_workspaces.tab("flows") is not None)


def held(step, kind, prompt, references):
    """Record what the Studio window shows at ``step``; True when it is ``kind``, ``prompt`` and ``references``."""
    v, area = view(), studio_area()
    seen = {"window": bool(studio_window()), "area": area.as_pointer() if area else None,
            "kind": v and v["kind"], "prompt": v and v["prompt"], "references": len(v["references"]) if v else None}
    state["steps"][step] = seen
    return (seen["kind"], seen["prompt"], seen["references"]) == (kind, prompt, references)


def capture(name):
    for label, target in (("studio", studio_window()), ("main", window())):
        if target is None:
            continue
        path = OUT / "{}-{}.png".format(name, label)
        with bpy.context.temp_override(window=target):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(path))
        state["screenshots"].append(str(path))


def reference_png():
    path = OUT / "reference.png"
    image = bpy.data.images.new("Angelo Mode Switch QA Reference", width=8, height=8, alpha=True)
    try:
        image.pixels.foreach_set([0.2, 0.6, 0.5, 1.0] * 64)
        image.filepath_raw, image.file_format = str(path), 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    return str(path)


def enter_director():
    """Director through the product's own request, once the start-up is over (no-op when already in Director)."""
    from angelo_agent import director
    assert qa_boot.started(), "enter Director only once the start-up and any layout change are over"
    if not director.is_director(window()):
        director.request_mode(window(), "DIRECTOR", persist=False)


def in_director():
    """In Director; a failed Director build fails the suite with Director's own error."""
    from angelo_agent import director
    if director.last_error:
        raise AssertionError("Director: " + str(director.last_error))
    return director.is_director(window())


def switch(mode):
    assert qa_boot.started(), "switch modes only once the start-up and any layout change are over"
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.workspace_mode(mode=mode)


def strip_tabs():
    """One step towards a file as 1.0.4 saved it (no Media Studio or Generation Flows tab, no record of them), as
    test_blender_tools_wait_for_tab_build makes it. True when done."""
    from angelo_agent import studio_workspaces
    present = [w for w in (studio_workspaces.tab("studio"), studio_workspaces.tab("flows")) if w is not None]
    if present:
        if window().workspace != present[0]:
            window().workspace = present[0]
        else:
            with bpy.context.temp_override(window=window()):
                bpy.ops.workspace.delete()
        return False
    for workspace in bpy.data.workspaces:
        if studio_workspaces.FLAG in workspace:
            del workspace[studio_workspaces.FLAG]
    return True


def instrument_build():
    """As the build starts (a generator: nothing has switched yet), record what the kept Studio window shows, then
    give it a draft and a reference that the build's passes through the tabs must leave in place."""
    from angelo_agent import studio, studio_workspaces
    build = studio_workspaces.build

    def build_and_draft(window_, kinds):
        job = build(window_, kinds)
        if not state.get("build_started"):
            state["build_started"] = True
            v = view()
            state["at_build_start"] = {"prompt": v and v["prompt"], "references": len(v["references"]) if v else None}
            if v is not None:
                v["prompt"] = BUILD
                studio.attach(v, [reference_png()])
        return job
    studio_workspaces.build = build_and_draft


def fixture_transport(url, **kwargs):
    body = json.loads(kwargs.get("body") or b"{}")
    calls.append(body.get("model"))
    if body.get("model") == "gemini-3.8-flash":   # AI Enhance's default model.
        return json.dumps({"steps": [{"type": "model_output", "content": [{"type": "text", "text": ENHANCED}]}]}).encode()
    gate.wait(60)
    image = {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(picture[0]).decode()}
    return json.dumps({"steps": [{"type": "model_output", "content": [image]}]}).encode()


def fixture_account():
    """A Gemini account that is never called: Studio's transport is the fixture above."""
    import qa_fixtures
    from angelo_agent import studio, ui
    from angelo_agent.api_connections import CheckResult
    from angelo_agent.provider_registry import SPECS
    picture.append(qa_fixtures.painted_picture(OUT / "fixture.jpg", 'JPEG'))
    record = ui.connections().add("GOOGLE", "API_KEY", "QA Fixture Gemini", group="GENAI")
    ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
    ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
    studio._jobs.transport = fixture_transport
    return record


def start_generation(record):
    """What Generate does once confirmed (``angelo.studio_generate``), with the fixture account."""
    from angelo_agent import studio, ui
    asset = studio.library().add("IMAGE", "[QA fixture] across a file load", "QA fixture", record["id"], "",
                                 mode=record["mode"], model_id="gemini-3.1-flash-image",
                                 options={"aspect": "1:1", "resolution": "2K", "quality": "auto", "background": "auto",
                                          "size": "", "polycount": "10000", "texture": True}, reference_names=[])
    studio._jobs.start(asset, ui.generation_binding(record["id"]), asset["options"], [])
    return asset["id"]


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_STUDIO_MODE_SWITCH", json.dumps({"success": state["success"], "phase": state["phase"],
                                                   "failed": state["failed_checks"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=40):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in {} (waiting on: {})".format(phase, ", ".join(qa_boot.waiting_on()) or "-"))
    return False


def tick():
    from angelo_agent import director, studio, studio_toggle, studio_workspaces, ui
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 220, "Suite timed out"
        if phase == "boot":   # The runner's start-up may leave the window in Manual: enter Director the product's way.
            if not wait(phase, settled, 60):
                return .2
            enter_director()
            state["phase"] = "director_ready"
            return .3
        if phase == "director_ready":
            if not wait(phase, lambda: in_director() and settled(), 60):
                return .2
            studio.library()
            state["pump_before_any_load"] = bpy.app.timers.is_registered(studio.pump)
            state["account"] = fixture_account()["id"]
            state["asset"] = start_generation(ui.find_connection(state["account"]))
            path = str(OUT / "another.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
            state["phase"] = "generation_loaded"
            bpy.ops.wm.open_mainfile(filepath=path)   # With the generation still running.
            return .5
        if phase == "generation_loaded":
            if not wait(phase, settled, 60):
                return .2
            gate.set()   # The provider answers now, after the load.
            state["released"] = time.monotonic()
            state["phase"] = "generation_done"
            return .2
        if phase == "generation_done":   # Never pumped by hand: only Studio's own timer finishes it.
            asset = studio.library().assets.get(state["asset"], {})
            if asset.get("status") != "ready" and time.monotonic() - state["released"] < 15:
                return .2
            state["steps"]["generation"] = {"status": asset.get("status"), "pump_before": state["pump_before_any_load"],
                                            "pump_after": bpy.app.timers.is_registered(studio.pump), "calls": list(calls)}
            checks["a_generation_running_when_another_file_opens_finishes"] = asset.get("status") == "ready"
            studio_toggle.open_media_studio("image", window())
            state["phase"] = "enhance_after_load"
            return .5
        if phase == "enhance_after_load":
            if not wait(phase, lambda: view() is not None):
                return .2
            v = view()
            v["prompt"] = "a lantern"
            with bpy.context.temp_override(window=studio_window(), area=studio_area()):
                bpy.ops.angelo.studio_enhance('EXEC_DEFAULT', area_pointer=str(studio_area().as_pointer()))
            state["asked"] = time.monotonic()
            state["phase"] = "enhance_answered"
            return .2
        if phase == "enhance_answered":
            v = view()
            if (v or {}).get("enhanced") != ENHANCED and time.monotonic() - state["asked"] < 15:
                return .2
            state["steps"]["enhance"] = {"enhanced": (v or {}).get("enhanced"), "enhancing": (v or {}).get("enhancing")}
            checks["ai_enhance_asked_after_a_file_load_is_answered"] = (v or {}).get("enhanced") == ENHANCED
            capture("0-after-another-file")
            studio_toggle.close_studio(studio_window())
            state["phase"] = "fresh_start"
            return .5
        if phase == "fresh_start":
            if not wait(phase, lambda: studio_window() is None and settled(), 60):
                return .2
            enter_director()   # The file just loaded may open in Manual.
            state["phase"] = "fresh_director"
            return .3
        if phase == "fresh_director":
            if not wait(phase, lambda: in_director() and settled(), 60):
                return .2
            studio_toggle.open_media_studio("3d", window())
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: view() is not None):
                return .2
            v = view()
            v["prompt"] = DRAFT
            studio.attach(v, [reference_png()])
            checks["director_opens_the_studio_window_on_3d_with_a_draft_and_a_reference"] = held("opened", "3D", DRAFT, 1)
            capture("1-director")
            switch("MANUAL")
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            if not wait(phase, lambda: qa_boot.started() and not director.is_director(window())):
                return .2
            checks["manual_keeps_the_studio_windows_tab_draft_and_reference"] = held("manual", "3D", DRAFT, 1)
            state["manual_showed_a_new_area"] = state["steps"]["manual"]["area"] != state["steps"]["opened"]["area"]
            capture("2-manual")
            switch("DIRECTOR")
            state["phase"] = "director"
            return .5
        if phase == "director":
            if not wait(phase, lambda: qa_boot.started() and director.is_director(window())):
                return .2
            checks["director_again_keeps_the_studio_windows_tab_draft_and_reference"] = held("director", "3D", DRAFT, 1)
            capture("3-director-again")
            studio_toggle.close_studio(studio_window())   # The top-bar button's close.
            state["phase"] = "closed"
            return .5
        if phase == "closed":
            if not wait(phase, lambda: studio_window() is None):
                return .2
            studio_toggle.open_media_studio(None, window())
            state["phase"] = "reopened"
            return .5
        if phase == "reopened":
            if not wait(phase, lambda: view() is not None):
                return .2
            checks["a_closed_and_reopened_studio_starts_fresh"] = held("reopened", "IMAGE", "", 0)
            capture("4-reopened")
            v = view()
            v["prompt"] = LATER
            studio.attach(v, [reference_png()])
            state["phase"] = "strip"
            return .2
        if phase == "strip":
            if not strip_tabs():
                return .4
            path = str(OUT / "saved-by-1.0.4.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
            state["file"], state["build_before"] = path, dict(studio_workspaces.last_build)
            instrument_build()
            state["phase"] = "tab_build"
            bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)   # Keeps these windows; the file lacks the tabs.
            return .5
        if phase == "tab_build":
            if not wait(phase, lambda: settled() and studio_window() is not None and state.get("build_started"), 60):
                return .2
            checks["load_ui_off_keeps_the_window_but_carries_no_draft"] = state["at_build_start"]["prompt"] == ""
            checks["the_mid_session_tab_build_ran"] = (studio_workspaces.last_build != state["build_before"]
                                                       and bool(studio_workspaces.last_build.get("done")))
            checks["the_studio_window_keeps_its_draft_across_the_tab_build"] = held("tab_build", "IMAGE", BUILD, 1)
            capture("5-after-tab-build")
            state["phase"] = "loaded"
            bpy.ops.wm.open_mainfile(filepath=state["file"])   # With its own windows.
            return .5
        if phase == "loaded":
            if not wait(phase, settled, 60):
                return .2
            shown = [studio.view(a)["prompt"] for w in bpy.context.window_manager.windows if w.screen
                     for a in w.screen.areas if studio.is_studio(a)]
            state["steps"]["loaded"] = {"shown_prompts": shown, "views_kept": len(studio._views),
                                        "views_with_an_old_draft": sum(v.get("prompt") in (DRAFT, LATER, BUILD)
                                                                       for v in studio._views.values())}
            checks["no_shown_studio_carries_a_draft_across_a_file_load"] = not set(shown) & {DRAFT, LATER, BUILD}
            checks["studios_pump_still_runs_after_file_loads"] = bpy.app.timers.is_registered(studio.pump)
            capture("6-file-loaded")
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
    bpy.context.preferences.view.show_splash = False
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    bpy.app.timers.register(tick, first_interval=.5, persistent=True)   # Outlives the file loads.


bpy.app.timers.register(enable, first_interval=1.0)
