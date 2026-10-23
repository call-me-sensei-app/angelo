# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remembered Manual startup and editor-only Studio launch. No provider usage."""

import json
import os
import plistlib
import runpy
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-startup-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
product_env.clear("DISABLE_DEFAULT_CHAT")  # Every spelling the app honours, deprecated ones included.
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, console, studio
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

(OUT / "state").mkdir(parents=True, exist_ok=True)
(OUT / "state/workspace-mode.json").write_text('{"mode":"MANUAL"}')

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
original_scene = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
started = time.monotonic()
state = {"phase":"startup", "checks":{}, "fixture":"Native default GUI startup; auth/model discovery disabled; no generation"}


def tick():
    try:
        assert time.monotonic() - started < 30, "Default startup did not finish"
        window = bpy.context.window_manager.windows[0]
        state["areas"] = [{"type": a.type, "language": a.spaces.active.language if a.type == 'CONSOLE' else "", "x":a.x, "width":a.width} for a in window.screen.areas]
        state["layout_pending"] = ui._layout_timer is not None
        state["messages"] = ui._controller.state["messages"][-3:] if ui._controller else []
        (OUT / "startup-progress.json").write_text(json.dumps(state, indent=2))
        if state["phase"] == "startup":
            chats = [a for a in window.screen.areas if console.is_chat(a)]
            if not chats or not qa_boot.started():   # Includes Manual's tab build, part of the start-up (1.0.5).
                return .2
            chat = chats[0]
            properties = next(a for a in window.screen.areas if a.type == 'PROPERTIES')
            state["checks"]["normal_startup_opens_coding_agent"] = "angelo_agent" in bpy.context.preferences.addons
            state["checks"]["chat_is_outside_properties_on_right"] = chat.x >= properties.x + properties.width - 3
            state["checks"]["native_viewport_remains_visible"] = any(a.type == 'VIEW_3D' for a in window.screen.areas)
            binary = Path(bpy.app.binary_path)
            info = plistlib.loads((binary.parents[1] / "Info.plist").read_bytes())
            state["checks"]["angelo_bundle_and_executable"] = binary.name == "Angelo" and binary.parents[2].name == "Angelo.app"
            state["checks"]["angelo_macos_identity"] = info["CFBundleName"] == info["CFBundleDisplayName"] == info["CFBundleExecutable"] == "Angelo" and info["CFBundleIdentifier"] == json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text())["bundle"]["macos_id"]
            state["checks"]["angelo_app_menu_label"] = bpy.types.TOPBAR_MT_blender.bl_label == "Angelo"
            # Taken now, after the start-up: it builds Manual's two tabs by design, and the check is that launching
            # Studio adds no top-level workspace of its own (a baseline from before the start-up counted the tabs).
            state["workspaces_before_studio"] = sorted(w.name for w in bpy.data.workspaces)
            runpy.run_path(str(ROOT / "scripts/angelo/launch_studio.py"), run_name="__main__")
            state["phase"] = "studio"
            return .8
        if state["phase"] == "studio":
            areas = [a for a in window.screen.areas if studio.is_studio(a)]
            if not areas:
                return .2
            state["checks"]["actual_studio_editor_visible"] = studio.draw_count > 0 and studio.last_error is None
            state["checks"]["studio_is_not_a_top_level_workspace"] = (
                sorted(w.name for w in bpy.data.workspaces) == state["workspaces_before_studio"])
            state["checks"]["coding_agent_remains_on_right_in_studio"] = any(console.is_chat(a) for a in window.screen.areas)
            state["checks"]["scene_unchanged"] = original_scene == [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
            state["phase"] = "complete"
            state["success"] = all(state["checks"].values())
            (OUT / "startup-qa.json").write_text(json.dumps(state, indent=2))
            print("ANGELO_STARTUP_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        (OUT / "startup-qa.json").write_text(json.dumps(state, indent=2))
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.3)
