# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Godot live link in a windowed Angelo, in Director Mode, against a real Godot editor without a window
(doc/angelo/godot_link.rst, Validation).

Steps: the chat bar's + menu offers the Godot group and turns the link on; the status bar says so; a request shows
the card with Discard and Open for a never-saved file and Save and Open for an unsaved one (Not now refuses it);
then the Godot editor (``godot_link_e2e/driver_addon``, headless) asks for crate.glb, the card's Open opens it, an
edit made here reaches Godot's preview, Edit Mode holds live exports (Godot is told once) until it is left, saving
in Edit Mode writes the .glb back and stays in Edit Mode, Godot re-imports it, Godot stops the link,
and turning the link off removes its record. Screenshots of each card and of the live state are saved for a look.

Needs Godot 4.4 or newer: ``ANGELO_QA_GODOT`` (default ``/Applications/Godot.app/Contents/MacOS/Godot``).
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-link-gui"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"), ANGELO_LINK_RECORD=str(OUT / "link-record.json"))
GODOT = os.environ.get("ANGELO_QA_GODOT") or "/Applications/Godot.app/Contents/MacOS/Godot"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
product_env.clear("DISABLE_DEFAULT_CHAT")
from angelo_agent import card_host, chat_destinations, director, engine_link, engine_link_ui, ui  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
import test_blender_godot_link_e2e as e2e  # noqa: E402

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.use_save_prompt = False
REPORT = OUT / "godot-link-gui.json"
started = time.monotonic()
state = {"phase": "startup", "checks": {}, "success": False, "screenshots": []}
editor = {"process": None, "project": None, "since": 0.0}


class Recorder:
    def __init__(self):
        self.labels = []

    def label(self, text="", **kwargs):
        self.labels.append(text)

    def menu(self, menu, text="", **kwargs):
        self.labels.append(text)

    def row(self, **kwargs):
        return self

    def separator(self, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def status_text():
    recorder = Recorder()
    engine_link_ui.draw_status(type("Header", (), {"layout": recorder})(), bpy.context)
    return recorder.labels


def director_bar_text():
    """What Director's top bar shows for the link (Director hides the status bar)."""
    recorder = Recorder()
    context = type("Context", (), {"region": type("Region", (), {"alignment": "TOP"})(), "window": window(),
                                   "preferences": bpy.context.preferences})()
    engine_link_ui.draw_director_bar(type("Header", (), {"layout": recorder})(), context)
    return recorder.labels


def card_actions():
    plan = card_host._active.plan
    return [action for _, action, _ in plan.hits], [op[1] for op in plan.ops if op[0] in {"text", "mono"}]


def press(action):
    card_host._active.card.act(action, None, card_host.Host(card_host._active))


def edit(location=(0, 0, 0)):
    """A change as a person makes one: an operator and its undo step, which marks the file changed."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.mesh.primitive_cube_add(location=location)
        bpy.ops.ed.undo_push(message="Add Cube")
    assert bpy.data.is_dirty, "the edit didn't mark the file changed"


def inject(project):
    """A request as an editor would send it (client 0: no socket), for the card's variants."""
    engine_link._handle(0, {"type": "open", "id": "q", "project": str(project), "asset": "res://crate.glb",
                            "project_name": "Angelo Link E2E", "node": "Crate"})


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        print(state["traceback"], flush=True)
    else:
        state["phase"] = "complete"
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
    process = editor["process"]
    if process is not None and process.poll() is None:
        process.kill()
    engine_link.stop()
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_GODOT_LINK_GUI", json.dumps(state), flush=True)
    bpy.ops.wm.quit_blender()


def go(phase, wait=0.3):
    state["phase"] = phase
    editor["since"] = time.monotonic()
    return wait


def tick():
    try:
        assert time.monotonic() - started < 240, "Godot link GUI timed out in phase " + state["phase"]
        phase = state["phase"]
        if phase == "startup":
            if not director.is_director(window()) or not qa_boot.started():
                return 0.2
            rows = chat_destinations.menu_rows(open_groups={"godot_link"})
            on = [row for row in rows if row.get("value") == "godot_link_on"]
            state["checks"]["menu_offers_turn_on"] = len(on) == 1 and on[0]["operator"] == "angelo.godot_link"
            with bpy.context.temp_override(window=window()):
                getattr(bpy.ops.angelo, "godot_link")("EXEC_DEFAULT", **dict(on[0]["properties"]))
            state["checks"]["link_on"] = engine_link.running() and os.path.isfile(os.environ["ANGELO_LINK_RECORD"])
            state["checks"]["status_bar_says_on"] = status_text() == ["Godot link on"]
            state["checks"]["director_top_bar_quiet_until_live"] = director_bar_text() == []
            rows = chat_destinations.menu_rows(open_groups={"godot_link"})
            state["checks"]["menu_offers_turn_off"] = any(row.get("value") == "godot_link_off" for row in rows)
            project, _version = e2e.build_project(OUT / "godot", bpy.app.binary_path, GODOT, OUT / "home",
                                                  Path(os.environ["ANGELO_LINK_RECORD"]))
            editor["project"] = project
            edit()   # An unsaved, never-saved file.
            inject(project)
            return go("card_untitled")
        if phase in {"card_untitled", "card_unsaved"} and not card_host.is_active("godot_link"):
            assert time.monotonic() - editor["since"] < 20, "the request card never showed"
            return 0.2
        if phase == "card_untitled":
            actions, texts = card_actions()
            state["checks"]["untitled_offers_discard_and_open"] = "discard_open" in actions and "later" in actions
            state["checks"]["card_names_asset"] = "Godot wants to edit crate.glb" in texts
            capture("card-untitled.png")
            press("later")
            card_host.check_area()
            state["checks"]["not_now_withdraws"] = engine_link.pending() is None
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "work.blend"))
            edit((3, 0, 0))   # Saved once, changed since.
            inject(editor["project"])
            return go("card_unsaved")
        if phase == "card_unsaved":
            actions, _texts = card_actions()
            state["checks"]["unsaved_offers_save_and_open"] = "save_open" in actions
            capture("card-unsaved.png")
            press("later")
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.save_mainfile()
            env = e2e._environment(OUT / "home", Path(os.environ["ANGELO_LINK_RECORD"]),
                                   {"ANGELO_QA_DIR": str(OUT), "ANGELO_E2E_ASSET": "res://crate.glb"})
            editor["process"] = subprocess.Popen([GODOT, "--headless", "--editor", "--path", str(editor["project"])],
                                                 env=env, stdout=(OUT / "godot.log").open("w"), stderr=subprocess.STDOUT)
            return go("wait_request")
        if phase == "wait_request":
            if engine_link.pending() is None or not card_host.is_active("godot_link"):
                return 0.2
            actions, _texts = card_actions()
            state["checks"]["clean_file_offers_open"] = "open" in actions
            capture("card-open.png")
            press("open")
            return go("wait_linked", 0.5)
        if phase == "wait_linked":
            linked = engine_link.session()
            if linked is None or engine_link._state["version"] < 1:
                return 0.2
            state["checks"]["opened_working_file"] = bpy.data.filepath == linked.editable
            state["checks"]["status_bar_says_live"] = status_text() == ["Live in Godot: crate.glb"]
            state["checks"]["director_top_bar_says_live"] = director_bar_text() == ["Live in Godot: crate.glb"]
            rows = chat_destinations.menu_rows(open_groups={"godot_link"})
            state["checks"]["menu_offers_stop_editing"] = any(row.get("value") == "godot_link_unlink" for row in rows)
            with bpy.context.temp_override(window=window()):
                bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 2))
            bpy.context.object.name = "AngeloAdded"
            state["edit_version"] = engine_link._state["version"]
            return go("wait_edit")
        if phase == "wait_edit":
            if engine_link._state["version"] <= state["edit_version"]:
                return 0.2
            capture("live.png")   # Director's top bar names the live asset.
            from angelo_agent import top_bar_fit
            fitted = top_bar_fit.widths.get(window().as_pointer(), {}).get("godot_link")
            state["checks"]["top_bar_label_fits"] = bool(fitted) and fitted[0] == "Live in Godot: crate.glb"
            editor["editors"] = engine_link.editors
            engine_link.editors = lambda live=False: 0   # How the bars look when Godot closes (the driver ends the link itself).
            engine_link.hooks["changed"]()   # What a disconnection runs; the bars redraw on the next loop pass.
            return go("not_connected", 0.5)
        if phase == "not_connected":
            try:
                state["checks"]["bars_say_godot_isnt_connected"] = (
                    status_text() == ["Godot isn't connected: crate.glb"]
                    and director_bar_text() == ["Godot isn't connected: crate.glb"])
                capture("godot-not-connected.png")
            finally:
                engine_link.editors = editor.pop("editors")
                engine_link.hooks["changed"]()
            with bpy.context.temp_override(window=window()):
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.transform.translate(value=(0, 0, 0.5))
            state["edit_version"] = engine_link._state["version"]
            return go("in_edit_mode", 2.0)
        if phase == "in_edit_mode":
            state["checks"]["edit_mode_holds_live_exports"] = engine_link._state["version"] == state["edit_version"]
            state["checks"]["edit_mode_tells_godot_once"] = engine_link._state["edit_notice"] is True
            with bpy.context.temp_override(window=window()):
                bpy.ops.object.mode_set(mode="OBJECT")
            return go("left_edit_mode")
        if phase == "left_edit_mode":
            if engine_link._state["version"] <= state["edit_version"]:
                assert time.monotonic() - editor["since"] < 10, "no export after leaving Edit Mode"
                return 0.2
            state["checks"]["leaving_edit_mode_exports"] = bpy.data.objects["AngeloAdded"].mode == "OBJECT"
            with bpy.context.temp_override(window=window()):
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.wm.save_mainfile()
            state["checks"]["saving_in_edit_mode_stays_there"] = bpy.data.objects["AngeloAdded"].mode == "EDIT"
            return go("wait_godot", 0.5)
        if phase == "wait_godot":
            process = editor["process"]
            if process.poll() is None:
                return 0.3
            report = json.loads((OUT / "godot.json").read_text(encoding="utf-8"))
            state["godot"] = report
            state["checks"].update({"godot." + key: value for key, value in report["checks"].items()})
            state["checks"]["godot_exit_ok"] = process.returncode == 0 and not report.get("problem")
            state["checks"]["godot_reported_reimport"] = any(item.get("code") == "reimported"
                                                             for item in engine_link._state["reports"])
            state["checks"]["godot_stop_ends_link"] = engine_link.session() is None
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.godot_link(action="OFF")
            state["checks"]["off_removes_record"] = not os.path.exists(os.environ["ANGELO_LINK_RECORD"])
            state["checks"]["status_bar_clear_when_off"] = status_text() == [] and director_bar_text() == []
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return 0.2


bpy.app.timers.register(tick, first_interval=0.5, persistent=True)   # It outlives the asset's load.
