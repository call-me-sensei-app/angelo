# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Extensions list in a real window when an add-on replaced its row drawer with 5.2's arguments.

BlenderKit replaces ``bl_extension_ui.extension_draw_item`` to add its own controls to each row; on 5.3, which
added ``show_type`` (a12f839dd9c), its 5.2-shaped replacement raised ``TypeError`` on every row and the list drew
empty (#166392). Here a local extension is installed, a stand-in for that replacement takes over the drawer, and
Preferences > Extensions opens: the replacement must draw the row (``bl_pkg.extension_draw_compat``), with a
screenshot. Run in a window: ``--factory-startup`` without ``--background``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Run in a window"
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-extension-rows-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "phase": "setup", "screenshots": []}
bpy.context.preferences.view.show_splash = False
PACKAGE = "qa_row_drawer"
MANIFEST = '''schema_version = "1.0.0"
id = "%s"
version = "1.0.0"
name = "QA Row Drawer"
tagline = "A local extension for the row drawer suite"
maintainer = "Angelo QA"
type = "add-on"
blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]
''' % PACKAGE
drawn = []


def older(layout, *, pkg_id, item_local, item_remote, is_enabled, is_outdated, show, mark, repo_index, repo_item,
          operation_in_progress, extensions_warnings, show_developer_ui):
    """BlenderKit's shape on 5.2: no ``show_type``."""
    drawn.append(pkg_id)
    layout.label(text="Drawn by the replacement: " + pkg_id, translate=False)


def check(name, passed):
    state["checks"][name] = bool(passed)


def write():
    (OUT / "extension-row-drawers.json").write_text(json.dumps(state, indent=2))


def preferences_window():
    return next((w for w in bpy.context.window_manager.windows
                 if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)


def install():
    repo = next(r for r in bpy.context.preferences.extensions.repos if r.module == "user_default")
    folder = Path(repo.directory) / PACKAGE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "blender_manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (folder / "__init__.py").write_text("def register():\n    pass\n\n\ndef unregister():\n    pass\n",
                                        encoding="utf-8")
    bpy.ops.extensions.repo_refresh_all()
    return repo


def steps():
    from bl_pkg import bl_extension_ui as ui
    install()
    builtin = ui.extension_draw_item
    ui.extension_draw_item = older
    try:
        bpy.context.preferences.active_section = 'EXTENSIONS'
        bpy.ops.screen.userpref_show(section='EXTENSIONS')
        yield 1.5
        window = preferences_window()
        check("preferences_open", window is not None)
        for _ in range(10):
            if drawn:
                break
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            yield 0.5
        check("the_replacement_drew_the_row", PACKAGE in drawn)
        state["drawn"] = sorted(set(drawn))
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(OUT / "extensions-list.png"))
        state["screenshots"].append("extensions-list.png")
    finally:
        ui.extension_draw_item = builtin


def run():
    generator = steps()

    def tick():
        try:
            return next(generator)
        except StopIteration:
            state["phase"] = "complete"
            state["success"] = bool(state["checks"]) and all(state["checks"].values())
        except Exception:
            state["error"] = traceback.format_exc()
        write()
        print("ANGELO_EXTENSION_ROW_DRAWERS", json.dumps({"success": state["success"]}), flush=True)
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(qa_boot.gated(tick), first_interval=2)


run()
