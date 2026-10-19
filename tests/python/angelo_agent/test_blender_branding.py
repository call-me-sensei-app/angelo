# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scratch native menu/logo QA. No auth, inference, scene edits or system-app edits."""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-branding-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import preview_branding as branding

state = {"success": False, "checks": {}, "phase": "boot", "started": time.monotonic()}
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
bpy.context.preferences.view.show_splash = False


class Layout:
    def __init__(self):
        self.calls = []

    def menu(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def operator(self, name, **kwargs):
        self.calls.append((name, kwargs))

    def separator(self):
        pass


def write():
    (OUT / "branding.json").write_text(json.dumps(state, indent=2))


def capture(name):
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))


def tick():
    try:
        if state["phase"] == "boot":
            original = bpy.types.TOPBAR_MT_editor_menus.draw
            branding.install()
            state["checks"]["native_menu_is_patched_only_in_preview"] = bpy.types.TOPBAR_MT_editor_menus.draw == branding.header_draw
            state["checks"]["original_callback_retained"] = branding._original_menus == original
            state["checks"]["native_preview_icons_loaded"] = all(branding._icons[n].icon_id > 0 for n in ("white", "dark", "app"))
            for intensity, expected in ((229, "white"), (35, "dark"), (.9, "white"), (.1, "dark")):
                layout = Layout()
                context = SimpleNamespace(area=SimpleNamespace(show_menus=True), preferences=SimpleNamespace(
                    themes=[SimpleNamespace(user_interface=SimpleNamespace(wcol_menu=SimpleNamespace(text=(intensity,) * 3)))]))
                branding.header_draw(SimpleNamespace(layout=layout), context)
                self_icon = layout.calls[0][1]
                assert self_icon["icon_value"] == branding._icons[expected].icon_id and "icon" not in self_icon
            state["checks"]["white_on_dark_dark_on_light"] = True
            layout = Layout()
            branding.app_menu_draw(SimpleNamespace(layout=layout), None)
            # The operator idnames keep their pre-rename spelling; the labels show Angelo.
            state["checks"]["about_and_welcome_use_angelo_preview_surfaces"] = (
                [c[0] for c in layout.calls[:2]] == ["angelo.welcome", "angelo.about"]
                and [c[1].get("text") for c in layout.calls[:2]] == ["Welcome to Angelo", "About Angelo"])
            branding._last_icon = None
            state["phase"] = "header"
            write()
            return .5
        if state["phase"] == "header":
            capture("angelo-white-toolbar.png")
            state["actual_icon"] = branding._last_icon
            state["native_theme_text"] = list(bpy.context.preferences.themes[0].user_interface.wcol_menu.text)
            state["checks"]["actual_header_draw_used_white_mark"] = branding._last_icon == "white"
            bpy.ops.angelo.about('INVOKE_DEFAULT')
            state["phase"] = "about"
            write()
            return .5
        if state["phase"] == "about":
            capture("angelo-about-preview.png")
            state["checks"]["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
            state["checks"]["original_renderer_not_overwritten"] = branding._original_menus != branding.header_draw
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            write()
            print("ANGELO_BRANDING_GUI_OK", json.dumps(state), flush=True)
            if os.environ.get("ANGELO_QA_KEEP_OPEN") != "1":
                bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        write()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
