# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live UI transfers, file load during a CPU render, and quit during a CPU render.

Run through run_native_suites.py --gui in an isolated factory-startup profile.
The runner must also observe a clean exit after the final quit receipt.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot
import qa_waits

assert not bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-ui-lifetime-"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"phase": "boot", "success": False, "checks": {}, "paid_requests": 0}
held = {}
started = time.monotonic()


def save():
    (OUT / "sub-data-ui-lifetime.json").write_text(json.dumps(state, indent=2))


def removed(read):
    try:
        read()
    except ReferenceError:
        return True
    return False


def active_area(window):
    return max(window.screen.areas, key=lambda area: area.width * area.height)


def toggle_area(window):
    area = active_area(window)
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.screen.screen_full_area(use_hide_panels=False) == {'FINISHED'}


class TransferFloat:
    def __init__(self, window, value):
        self.window, self.value, self.calls = window, value, 0

    def __float__(self):
        self.calls += 1
        toggle_area(self.window)
        return self.value


def begin_render(window):
    scene = window.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 65536
    scene.cycles.use_adaptive_sampling = False
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 2
    scene.render.resolution_x = scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    bpy.context.preferences.view.render_display_type = 'NONE'
    with bpy.context.temp_override(window=window):
        assert bpy.ops.render.render('INVOKE_DEFAULT') == {'RUNNING_MODAL'}


def tick():
    try:
        if time.monotonic() - started > 240 * qa_waits.timeout_scale():
            raise TimeoutError("UI lifetime fixture exceeded its phase budget")
        window = bpy.context.window_manager.windows[0]
        checks = state["checks"]
        phase = state["phase"]
        if phase == "boot":
            bpy.context.preferences.use_preferences_save = False
            bpy.context.preferences.view.use_save_prompt = False
            area = active_area(window)
            area.type = 'VIEW_3D'
            held["space"] = area.spaces.active
            held["shading"] = area.spaces.active.shading
            held["region3d"] = area.spaces.active.region_3d
            held["view2d"] = next(r for r in area.regions if r.type == 'WINDOW').view2d
            toggle_area(window)
            held["temporary_screen"] = window.screen
            held["temporary_areas"] = window.screen.areas
            held["maximized_space"] = active_area(window).spaces.active
            state["phase"] = "maximized"
        elif phase == "maximized":
            value = TransferFloat(window, 42.0)
            held["space"].lens = value
            checks["setter_conversion_transfers_once"] = value.calls == 1
            checks["live_space_after_restore"] = held["space"].lens == 42.0
            checks["space_acquired_while_maximized_survives"] = held["maximized_space"].lens == 42.0
            checks["substructs_after_restore"] = (held["shading"].type == 'SOLID'
                                                   and len(held["region3d"].view_rotation) == 4)
            checks["owner_refreshed_after_restore"] = held["space"].id_data == window.screen
            checks["freed_temporary_screen_collection_rejected"] = removed(lambda: len(held["temporary_areas"]))
            # Function argument conversion must preserve the bound UI ancestry too.
            for name in ("maximize", "restore"):
                value = TransferFloat(window, 0.0)
                result = held["view2d"].view_to_region(value, 0.0)
                checks["function_conversion_" + name] = value.calls == 1 and len(result) == 2
            area = active_area(window)
            area.type = 'CONSOLE'
            area.spaces.active.language = 'python'
            state["phase"] = "console"
        elif phase == "console":
            area = active_area(window)
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                for text in ("held first line", "held second line"):
                    assert bpy.ops.console.history_append(text=text, current_character=0,
                                                          remove_duplicates=False) == {'FINISHED'}
            held["history"] = area.spaces.active.history
            held["line"] = held["history"][0]
            held["body"] = held["line"].body
            held["iterator"] = iter(held["history"])
            next(held["iterator"])
            toggle_area(window)
            toggle_area(window)
            checks["separately_allocated_console_line_survives"] = held["line"].body == held["body"]
            checks["collection_survives_transfer"] = len(held["history"]) >= 2
            checks["iterator_parent_and_cursor_survive"] = isinstance(next(held["iterator"]).body, str)
            # Save before installing a process-only Python driver namespace callback.
            obj = bpy.data.objects["Cube"]
            obj["pinned"] = [7.0, 8.0]
            held["buffer"] = memoryview(obj["pinned"])
            held["modifier"] = obj.modifiers.new("Live lifetime probe", 'BEVEL')
            retired = obj.modifiers.new("Retired lifetime probe", 'SOLIDIFY')
            obj.modifiers.remove(retired)
            held["retired"] = retired
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "during-render.blend"))
            held["driver_calls"] = []

            def probe():
                held["driver_calls"].append(bpy.app.is_job_running('RENDER'))
                assert removed(lambda: retired.thickness)
                assert held["buffer"][0] == 7.0
                return held["modifier"].width

            bpy.app.driver_namespace["ui_lifetime_probe"] = probe
            curve = obj.driver_add("location", 0)
            curve.driver.expression = "ui_lifetime_probe() + frame * 0.001"
            window.scene.frame_set(2)
            begin_render(window)
            state["phase"] = "load_during_render"
        elif phase == "load_during_render":
            if not bpy.app.is_job_running('RENDER'):
                return 0.05
            # Force real driver evaluation while the asynchronous worker owns its
            # render data, rather than counting only the pre-render setup call.
            before = len(held["driver_calls"])
            window.scene.frame_set(3)
            assert bpy.app.is_job_running('RENDER')
            checks["load_requested_while_render_running"] = True
            checks["python_driver_read_live_and_retired_views"] = any(held["driver_calls"][before:])
            save()
            # A load must join the render worker without retaining a Python/allocator lock.
            # Do not keep a context override alive across replacement of Main.
            bpy.ops.wm.open_mainfile(filepath=str(OUT / "during-render.blend"), load_ui=False)
            checks["load_joined_render"] = not bpy.app.is_job_running('RENDER')
            checks["load_invalidates_old_subdata"] = removed(lambda: held["modifier"].width)
            checks["load_preserves_exported_storage"] = held["buffer"][0] == 7.0
            held["buffer"].release()
            state["phase"] = "start_quit_render"
        elif phase == "start_quit_render":
            begin_render(window)
            state["phase"] = "quit_during_render"
        elif phase == "quit_during_render":
            if not bpy.app.is_job_running('RENDER'):
                return 0.05
            checks["quit_requested_while_render_running"] = True
            state["success"] = bool(checks) and all(checks.values())
            state["phase"] = "complete"
            save()
            print("ANGELO_SUB_DATA_UI_LIFETIME", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        save()
        return 0.1
    except Exception:
        state["error"] = traceback.format_exc()
        save()
        print("ANGELO_SUB_DATA_UI_LIFETIME", json.dumps(state), flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(qa_boot.gated(tick), first_interval=0.25, persistent=True)
