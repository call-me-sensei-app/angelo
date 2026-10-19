# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual editor menu + Python interpreter isolation, no auth/model calls."""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-editor-types-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = str(OUT / "accounts")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, console, editor_types, preview_branding
from angelo_agent.editor_registry import EDITOR_GROUPS, CODING_AGENT

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
preview_branding.install()
bpy.context.preferences.view.show_splash = False
state = {"phase": "boot", "checks": {}, "success": False, "fixture": "Native UI/Python only; no connection changes or inference"}
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
pointer = None
all_types = [i for _, entries in EDITOR_GROUPS for i, _, _ in entries]
index = 0


def save():
    (OUT / "editor-types.json").write_text(json.dumps(state, indent=2))


def area(window):
    return next(a for a in window.screen.areas if a.as_pointer() == pointer)


def select(window, identity):
    target = area(window)
    with bpy.context.temp_override(window=window, area=target):
        result = bpy.ops.angelo.editor_type(ui_type=identity)
    assert 'FINISHED' in result, identity


def python(window, command):
    target = area(window)
    region = next(r for r in target.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=target, region=region):
        target.spaces.active.history[-1].body = command
        bpy.ops.console.execute()


def capture(window, name):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def tick():
    global pointer, index
    try:
        window = bpy.context.window_manager.windows[0]
        if state["phase"] == "boot":
            pointer = next(a for a in window.screen.areas if a.type == 'VIEW_3D').as_pointer()
            if editor_types.native_picker():
                # The compiled fork draws the AI column in its native picker, so
                # register() deliberately leaves every header unwrapped.
                state["checks"]["native_picker_leaves_headers_unwrapped"] = not editor_types._headers
            else:
                state["checks"]["all_standard_headers_have_picker_compatibility"] = bool(editor_types._headers) and any(h.bl_space_type == 'VIEW_3D' for h in editor_types._headers)
            select(window, "CONSOLE")
            state["phase"] = "python"
            return .3
        if state["phase"] == "python":
            target = area(window)
            state["checks"]["python_console_choice_is_native_python"] = target.spaces.active.language == 'python' and not console.is_chat(target)
            python(window, "angelo_python_console_probe = 17; print('NATIVE_PYTHON_OK', angelo_python_console_probe)")
            state["checks"]["native_python_executes_not_agent"] = any('NATIVE_PYTHON_OK 17' in l.body for l in target.spaces.active.scrollback)
            target.spaces.active.history[-1].body = "print('saved Python draft')"
            select(window, CODING_AGENT)
            state["phase"] = "agent"
            return .3
        if state["phase"] == "agent":
            target = area(window)
            state["checks"]["coding_agent_is_a_distinct_editor_choice"] = console.is_chat(target) and target.spaces.active.history[-1].body != "print('saved Python draft')"
            target.spaces.active.history[-1].body = "Unsent agent draft — never Python"
            select(window, "CONSOLE")
            state["phase"] = "return_python"
            return .3
        if state["phase"] == "return_python":
            target = area(window)
            state["checks"]["python_history_and_input_are_restored"] = target.spaces.active.history[-1].body == "print('saved Python draft')" and any('NATIVE_PYTHON_OK 17' in l.body for l in target.spaces.active.scrollback)
            python(window, "print('PYTHON_NAMESPACE_RETAINED', angelo_python_console_probe)")
            state["checks"]["python_namespace_survives_editor_switch"] = any('PYTHON_NAMESPACE_RETAINED 17' in l.body for l in target.spaces.active.scrollback)
            capture(window, "restored-python-console.png")
            select(window, CODING_AGENT)
            state["checks"]["agent_draft_restored_separately"] = target.spaces.active.history[-1].body == "Unsent agent draft — never Python"
            with bpy.context.temp_override(window=window, area=target):
                bpy.ops.wm.call_menu(name='ANGELO_MT_editor_type')
            state["phase"] = "menu"
            return .3
        if state["phase"] == "menu":
            capture(window, "ai-column-editor-menu.png")
            state["checks"]["menu_has_separate_scripting_and_ai_columns"] = dict(EDITOR_GROUPS)["AI"][0][0] == CODING_AGENT and dict(EDITOR_GROUPS)["Scripting"][1][0] == "CONSOLE"
            window.event_simulate(type='ESC', value='PRESS')
            state["phase"] = "all_editors"
            return .3
        if state["phase"] == "all_editors":
            if index < len(all_types):
                identity = all_types[index]
                select(window, identity)
                state.setdefault("tested_editor_types", []).append(identity)
                index += 1
                # File Browser params are initialized asynchronously. Don't
                # force drawing its header in the same event as a type change.
                state["phase"] = "editor_draw"
                return .3
            state["checks"]["every_original_editor_still_selectable"] = len(state["tested_editor_types"]) == len(all_types)
            state["checks"]["no_header_proxy_errors"] = editor_types.last_error is None
            state["checks"]["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
            state["drawn_header_types"] = sorted(editor_types._drawn_headers)
            select(window, CODING_AGENT)
            addon_utils.disable("angelo_agent", default_set=False)
            state["checks"]["disable_restores_python_and_native_headers"] = area(window).spaces.active.language == 'python' and not editor_types._headers
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            save()
            print("ANGELO_EDITOR_TYPES_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        if state["phase"] == "editor_draw":
            capture(window, "current-editor.png")
            assert editor_types.last_error is None, (state["tested_editor_types"][-1], editor_types.last_error)
            state["phase"] = "all_editors"
            return .08
        save()
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
