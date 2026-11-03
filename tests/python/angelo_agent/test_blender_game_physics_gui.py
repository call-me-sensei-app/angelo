# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Game Physics in a real window: the Properties panel, the + menu's row and its popup, with screenshots.

A rigid crate with a fitted box, and a barrel with a Mesh shape in a rigid body (a ``trimesh_moving`` finding).
Checks: the panel and operators are registered by the add-on; the + menu's Game engine live link group has the row
and it runs ``angelo.game_physics``; Edit Fitted Size switches to Manual with the fitted size; the popup opens on
the active object. Screenshots: the Physics tab with the panel open for the crate and for the barrel (its finding),
and the popup. doc/angelo/game_physics.rst. Run in a window: ``--factory-startup`` without ``--background``;
it enables Angelo itself, like the other suites that run without the default chat.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Run in a window"
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-game-physics-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "phase": "setup", "screenshots": []}
bpy.context.preferences.view.show_splash = False


def check(name, passed):
    state["checks"][name] = bool(passed)


def write():
    (OUT / "game-physics-gui.json").write_text(json.dumps(state, indent=2))


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def properties_area():
    """A Properties editor on the Physics tab: the window's own, else its largest non-3D area turned into one."""
    areas = window().screen.areas
    area = next((item for item in areas if item.type == 'PROPERTIES'), None)
    if area is None:
        area = max((item for item in areas if item.type != 'VIEW_3D'), key=lambda item: item.width * item.height)
        area.type = 'PROPERTIES'
    area.spaces.active.context = 'PHYSICS'
    return area


def build():
    bpy.data.batch_remove(tuple(bpy.data.objects))
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
    crate = bpy.context.object
    crate.name = "Crate"
    crate.scale = (1, 2, 1)
    with bpy.context.temp_override(active_object=crate, selected_editable_objects=[crate]):
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    crate.angelo_physics.body, crate.angelo_physics.mass, crate.angelo_physics.shape = "RIGID", 5.0, "BOX"
    bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=1.2, location=(3, 0, 0.6))
    barrel = bpy.context.object
    barrel.name = "Barrel"
    barrel.angelo_physics.body, barrel.angelo_physics.shape = "RIGID", "TRIMESH"
    return crate, barrel


def select(obj):
    for item in bpy.context.view_layer.objects:
        item.select_set(item is obj)
    bpy.context.view_layer.objects.active = obj


def open_panel():
    """Draw the panel open: Blender has no API to open one panel (a click on its header does), so the test
    re-registers it without DEFAULT_CLOSED. The person's first click opens it the same way."""
    from angelo_agent import game_physics_ui
    cls = game_physics_ui.ANGELO_PT_game_physics
    bpy.utils.unregister_class(cls)
    cls.bl_options = set()
    bpy.utils.register_class(cls)


def steps():
    from angelo_agent import engine_link_ui, game_physics, game_physics_ui
    check("panel_registered", hasattr(bpy.types, "ANGELO_PT_game_physics"))
    check("operators_registered", hasattr(bpy.ops.angelo, "game_physics")
          and hasattr(bpy.ops.angelo, "game_physics_edit_fit"))
    rows = engine_link_ui._group_rows(None, None)
    row = next((item for item in rows if item["value"] == "game_physics"), None)
    check("plus_menu_row_runs_the_popup", row is not None and row["operator"] == "angelo.game_physics")
    crate, barrel = build()
    area = properties_area()
    open_panel()
    select(crate)
    yield 0.5
    capture("panel-crate.png")
    found = {(item["code"], item["subject"]) for item in game_physics.scene_findings(bpy.context.scene)}
    check("barrel_finding", ("trimesh_moving", "Barrel") in found and not any(code[1] == "Crate" for code in found))
    fit = game_physics_ui.fitted(crate)
    with bpy.context.temp_override(window=window(), area=area, active_object=crate, object=crate):
        bpy.ops.angelo.game_physics_edit_fit()
    settings = crate.angelo_physics
    check("edit_fitted_size_takes_the_fit", settings.fit == "MANUAL"
          and all(abs(a - b) < 1e-5 for a, b in zip(settings.size, fit.size)))
    select(barrel)
    yield 0.5
    capture("panel-barrel-finding.png")
    view = max((item for item in window().screen.areas if item.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(item for item in view.regions if item.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=view, region=region):
        result = bpy.ops.angelo.game_physics('INVOKE_DEFAULT')
    check("popup_opens", "RUNNING_MODAL" in result or "FINISHED" in result or "INTERFACE" in result)
    state["popup_result"] = sorted(result)
    yield 0.5
    capture("popup-barrel.png")


def run():
    generator = steps()

    def tick():
        try:
            delay = next(generator)
            return delay
        except StopIteration:
            state["phase"] = "complete"
            state["success"] = bool(state["checks"]) and all(state["checks"].values())
        except Exception:
            state["error"] = traceback.format_exc()
        write()
        print("ANGELO_GAME_PHYSICS_GUI", json.dumps({"success": state["success"]}), flush=True)
        bpy.ops.wm.quit_blender()
        return None

    # Held until Angelo's start-up (the first tab build its enable schedules) is over: qa_boot.gated.
    bpy.app.timers.register(qa_boot.gated(tick), first_interval=2)


run()
