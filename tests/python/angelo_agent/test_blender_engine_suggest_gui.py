# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Feature discovery in a windowed Angelo, in Director Mode (doc/angelo/features.rst, doc/angelo/engine_link.rst,
"Engine detection and the suggestion").

Steps: the chat bar's + menu offers the game engine group with a row per engine, Select Through and the render
engine choice (drawn and captured); a real Godot editor started without a window is detected by Angelo's own watcher
(no stand-in detection) and the suggestion card shows once, names Godot and offers Not now and Link a project…; the
agent's note and its FEATURES lookup name Godot and nothing else of the process table; Not now dismisses it for good;
the agent's angelo_app_feature turns the link on and off; Add Angelo Link copies Unity's and Unreal's plugins into
their projects and refuses a folder that isn't one; Link a project… opens the folder picker. Screenshots of the card,
the menu and the picker are saved for a look.

Needs Godot 4.4 or newer: ``ANGELO_QA_GODOT`` (default ``/Applications/Godot.app/Contents/MacOS/Godot``).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-suggest-gui"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"), ANGELO_LINK_RECORD=str(OUT / "link-record.json"))
GODOT = os.environ.get("ANGELO_QA_GODOT") or "/Applications/Godot.app/Contents/MacOS/Godot"
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
product_env.clear("DISABLE_DEFAULT_CHAT")
from angelo_agent import (app_feature_host, capabilities_host, card_host, chat_destinations, director,  # noqa: E402
                          engine_link, engine_link_ui, engine_suggest, engine_suggest_ui, ui)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
import test_blender_godot_link_e2e as e2e  # noqa: E402

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.view.use_save_prompt = False
REPORT = OUT / "engine-suggest-gui.json"
started = time.monotonic()
state = {"phase": "startup", "checks": {}, "success": False, "screenshots": []}
godot = {"process": None, "since": 0.0}


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def card_texts():
    plan = card_host._active.plan
    return [action for _, action, _ in plan.hits], [op[1] for op in plan.ops if op[0] in {"text", "mono"}]


def press(action):
    card_host._active.card.act(action, None, card_host.Host(card_host._active))


def check(name, condition):
    state["checks"][name] = bool(condition)


def menu_checks():
    entries = chat_destinations.entries()   # The top level's tiles; a group's rows are on its page (1.0.8).
    rows = [row for group in ("godot_link", "render_engine") for row in chat_destinations.group_rows(group)]
    values = [row.get("value") for row in rows]
    top = [entry["value"] for entry in entries]
    check("menu_names_engine_group", any(entry["value"] == "godot_link" and entry["kind"] == "group"
                                         and entry.get("label") == "Game engine live link" for entry in entries))
    check("menu_offers_every_engine", all(engine + "_link_install" in values for engine in ("godot", "unity", "unreal")))
    check("menu_offers_select_through", "select_through" in top)
    check("menu_offers_render_engines", "render_engine" in top and any(str(value).startswith("render_engine:engine:")
                                                                         for value in values))
    state["menu_top"] = top


def fake_project(kind):
    folder = OUT / ("unity-project" if kind == "UNITY" else "unreal-project")
    if kind == "UNITY":
        (folder / "Assets").mkdir(parents=True, exist_ok=True)
        (folder / "ProjectSettings").mkdir(parents=True, exist_ok=True)
        (folder / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.1f1\n")
    else:
        (folder / "Content").mkdir(parents=True, exist_ok=True)
        (folder / "Game.uproject").write_text('{"FileVersion": 3}\n')
    return folder


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        print(state["traceback"], flush=True)
    else:
        state["phase"] = "complete"
        state["success"] = bool(state["checks"]) and all(state["checks"].values())
    process = godot["process"]
    if process is not None and process.poll() is None:
        process.kill()
    engine_link.stop()
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_ENGINE_SUGGEST_GUI", json.dumps(state), flush=True)
    bpy.ops.wm.quit_blender()


def go(phase, wait=0.3):
    state["phase"] = phase
    godot["since"] = time.monotonic()
    return wait


def tick():
    try:
        assert time.monotonic() - started < 300, "engine suggestion GUI timed out in phase " + state["phase"]
        phase = state["phase"]
        if phase == "startup":
            if not director.is_director(window()) or not qa_boot.started():
                return 0.2
            # The runner opts every suite out of app notices (automation_off); this suite is about one, so its watcher
            # runs as for a person. Detection itself is the real one.
            engine_suggest_ui._off = lambda: bpy.app.background
            memory_file = OUT / "state" / engine_suggest.FILE_NAME
            if memory_file.exists():
                memory_file.unlink()
            engine_suggest_ui._memory = None
            if not bpy.app.timers.is_registered(engine_suggest_ui.tick):
                bpy.app.timers.register(engine_suggest_ui.tick, first_interval=0.5, persistent=True)
            check("no_engine_no_card", not card_host.is_active("engine_suggest"))
            menu_checks()
            project = OUT / "godot-project"
            project.mkdir(exist_ok=True)
            (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="Detect QA"\n')
            env = e2e._environment(OUT / "home", Path(os.environ["ANGELO_LINK_RECORD"]))
            godot["process"] = subprocess.Popen([GODOT, "--headless", "--editor", "--path", str(project)], env=env,
                                                stdout=(OUT / "godot.log").open("w"), stderr=subprocess.STDOUT)
            return go("wait_card")
        if phase == "wait_card":
            if not card_host.is_active("engine_suggest"):
                assert godot["process"].poll() is None, "Godot exited before Angelo saw it"
                assert time.monotonic() - godot["since"] < 60, "the suggestion card never showed"
                return 0.25
            state["seconds_to_card"] = round(time.monotonic() - godot["since"], 1)
            actions, texts = card_texts()
            check("card_names_godot", "Godot is open" in texts)
            check("card_offers_not_now_and_link", "later" in actions and "link" in actions)
            check("card_is_not_modal", not card_host._active.card.modal)
            check("nothing_connected_yet", not engine_link.running())
            capture("suggestion-card.png")
            note = engine_suggest_ui.for_request(SimpleNamespace())
            check("agent_note_names_godot", note is not None and note["running"] == ["Godot"]
                  and note["live_link"] is False)
            check("agent_note_holds_no_path", "/" not in json.dumps(note).replace("angelo_agent_capabilities", ""))
            answer = capabilities_host.agent_capabilities("FEATURES", ["engine_link"])
            entry = answer["features"][0]
            check("lookup_says_how_to_link_unity", "Unity" in entry["where"] and "Add Angelo Link" in entry["where"])
            check("lookup_carries_tool", entry["tool"]["name"] == "angelo_app_feature")
            check("lookup_sees_godot_running", entry["this_computer"]["running"] == ["Godot"])
            index = capabilities_host.agent_capabilities("FEATURES")
            state["features_offered"] = [item["name"] for item in index["features"]]
            check("index_lists_engine_link", "engine_link" in state["features_offered"])
            from angelo_agent import feature_guide
            absent = [guide.id for guide in feature_guide.GUIDES if guide.requires
                      and not all(getattr(bpy.types, op.split(".")[0].upper() + "_OT_" + op.split(".")[1], None)
                                  for op in guide.requires)]
            check("index_omits_features_this_build_lacks", not set(absent) & set(state["features_offered"]))
            press("later")
            card_host.check_area()
            check("not_now_closes", not card_host.is_active("engine_suggest"))
            return go("no_second_card", 12.0)   # More than one watcher interval: the same engine never asks again.
        if phase == "no_second_card":
            check("suggested_once", not card_host.is_active("engine_suggest")
                  and not card_host.queued("engine_suggest"))
            stored = json.loads((OUT / "state" / engine_suggest.FILE_NAME).read_text())
            check("memory_holds_engine_ids_only", stored == {"suggested": ["godot"]})
            on = app_feature_host.execute(window(), "angelo_app_feature", {"feature": "ENGINE_LINK", "action": "ON"})
            check("agent_turns_link_on", on["on"] and engine_link.running())
            check("note_follows_the_link", engine_suggest_ui.for_request(SimpleNamespace())["live_link"] is True)
            capture("link-on.png")
            app_feature_host.execute(window(), "angelo_app_feature", {"feature": "ENGINE_LINK", "action": "OFF"})
            check("agent_turns_link_off", not engine_link.running())
            with bpy.context.temp_override(window=window()):
                for kind, folder in (("UNITY", "Packages/com.hyperbond.angelo-link/package.json"),
                                     ("UNREAL", "Plugins/AngeloLink/AngeloLink.uplugin")):
                    project = fake_project(kind)
                    result = bpy.ops.angelo.godot_link_install(engine=kind, directory=str(project))
                    check("installs_into_" + kind.lower(), result == {'FINISHED'} and (project / folder).is_file())
                try:   # A cancelled operator that reported an error raises here.
                    refused = bpy.ops.angelo.godot_link_install(engine="UNITY", directory=str(OUT / "godot-project"))
                except RuntimeError as error:
                    refused = str(error)
            check("refuses_a_folder_of_another_engine", "Choose the Unity project's folder" in str(refused)
                  and not (OUT / "godot-project/Packages").exists())
            check("install_turns_link_on", engine_link.running())
            engine_link.stop()
            engine_link_ui.remember(False)
            # The Godot card once more, as a person who hasn't answered it yet would see it: Link a project….
            engine_suggest_ui.memory().suggested.discard("godot")
            card_host.request(engine_suggest_ui.SuggestionCard("godot"))
            card_host.pump(lambda: True)
            return go("link_card", 0.5)
        if phase == "link_card":
            if not card_host.is_active("engine_suggest"):
                assert time.monotonic() - godot["since"] < 10, "the card never showed again"
                return 0.2
            capture("suggestion-card-link.png")
            press("link")
            return go("picker", 1.0)
        if phase == "picker":
            browsers = [(w, a) for w in bpy.context.window_manager.windows for a in w.screen.areas
                        if a.type == 'FILE_BROWSER']
            if not browsers:
                assert time.monotonic() - godot["since"] < 10, "Link a project… opened no folder picker"
                return 0.2
            check("link_opens_folder_picker", True)
            capture("folder-picker.png")
            picker_window, area = browsers[0]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=picker_window, area=area, region=region):
                bpy.ops.file.cancel()
            check("picker_connects_nothing", not engine_link.running())
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return 0.2


bpy.app.timers.register(tick, first_interval=0.5, persistent=True)
