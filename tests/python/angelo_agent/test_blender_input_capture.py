# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real native Scale/escape, safe editor switching, and canvas pointer isolation."""

import json
import os
import runpy
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-input-capture-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_STATE_DIR=str(OUT / "state"),
                  ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_LIBRARY_DIR=str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, studio, chat_view, console
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator, beside this suite.
import qa_operator  # noqa: E402
from angelo_agent.input_policy import interaction_active, active_interactions, operator_identity
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

ui._authentication.start = lambda *a, **k: None
ui.refresh_connected_models = lambda *a, **k: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
ui.get_controller().setup_required = False
state = {"phase":"boot", "checks":{}, "fixture":"Native modal Scale with synthetic events, no providers or inference"}
started = time.monotonic()
main_pointer, chat_pointer = None, None
before = [(o.name, tuple(o.location), tuple(o.scale)) for o in bpy.context.scene.objects]


def window():
    return bpy.context.window_manager.windows[0]


def area(pointer):
    return next(a for a in window().screen.areas if a.as_pointer() == pointer)


def region(target):
    return next(r for r in target.regions if r.type == 'WINDOW')


def event_at(target, kind, value='PRESS'):
    r = region(target)
    return SimpleNamespace(type=kind, value=value, mouse_x=r.x+r.width//2, mouse_y=r.y+r.height//2,
        mouse_prev_y=r.y+r.height//2, ctrl=False, oskey=False, shift=False, alt=False, unicode="")


def tick():
    global main_pointer, chat_pointer
    try:
        assert time.monotonic() - started < 20, "Input isolation test timed out"
        state["operators"] = [operator_identity(op) for op in window().modal_operators]
        if state["phase"] == "boot":
            if not qa_boot.started():   # Never dock or switch while Angelo's start-up still moves the layout.
                return .1
            target = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
            main_pointer = target.as_pointer()
            chat = next(a for a in window().screen.areas if a.type == 'PROPERTIES')
            chat_pointer = chat.as_pointer()
            ui.setup_chat_area(window(), chat)
            state["phase"] = "resize"
            return .25
        if state["phase"] == "resize":
            target = area(main_pointer)
            with bpy.context.temp_override(window=window(), area=target, region=region(target)):
                result = bpy.ops.transform.resize('INVOKE_DEFAULT')
            assert result == {'RUNNING_MODAL'}, result
            state["phase"] = "blocked"
            return .2
        if state["phase"] == "blocked":
            state["checks"]["real_native_scale_is_detected"] = "transform.resize" in active_interactions(window())
            target, chat = area(main_pointer), area(chat_pointer)
            original_type = target.type
            try:
                studio.setup_area(window(), target)
            except RuntimeError:
                pass
            state["checks"]["editor_switch_is_blocked_during_transform"] = target.type == original_type == 'VIEW_3D'
            try:
                ui.request_chat_dock(window(), "RIGHT")
            except RuntimeError:
                pass
            state["checks"]["dock_does_not_change_layout_during_transform"] = ui._layout_timer is None
            handler = qa_operator.stand_in(chat_view.ANGELO_OT_chat_input, window_pointer=window().as_pointer(),
                                           focused_area=chat_pointer, dragging=True)
            chat_view._views.setdefault(chat_pointer, {})["focused"] = True
            with bpy.context.temp_override(window=window(), area=chat, region=region(chat)):
                result = chat_view.ANGELO_OT_chat_input.modal(handler, bpy.context, event_at(chat, 'ESC'))
            state["checks"]["focused_chat_yields_escape_to_native_tool"] = result == {'PASS_THROUGH'} and not handler.dragging
            # The old bug was an asynchronous launcher switching the viewport
            # while Scale was active. The launcher must now wait instead.
            runpy.run_path(str(ROOT / "scripts/angelo/launch_studio.py"), run_name="__main__")
            state["phase"] = "wait_launcher"
            return .65
        if state["phase"] == "wait_launcher":
            state["checks"]["launcher_does_not_preempt_scale"] = area(main_pointer).type == 'VIEW_3D' and interaction_active(window())
            # ESC is routed through the actual native handler stack, with the
            # chat composer still focused. No direct transform cancellation.
            window().event_simulate(type='ESC', value='PRESS')
            state["phase"] = "cancelled"
            return .4
        if state["phase"] == "cancelled":
            state["checks"]["native_escape_releases_transform"] = "transform.resize" not in active_interactions(window())
            if not studio.is_studio(area(main_pointer)):
                return .15
            target = area(main_pointer)
            state["checks"]["launcher_opens_studio_after_native_cancel"] = studio.is_studio(target)
            handler = qa_operator.stand_in(studio.ANGELO_OT_studio_input, window_pointer=window().as_pointer(),
                                           focus_area=main_pointer, dragging=True,
                                           input_generation=studio._input_generation)
            other = next(a for a in window().screen.areas if a.type == 'OUTLINER')
            with bpy.context.temp_override(window=window(), area=target, region=region(target)):
                result = studio.ANGELO_OT_studio_input.modal(handler, bpy.context, event_at(other, 'LEFTMOUSE', 'RELEASE'))
                state["checks"]["drag_release_outside_studio_is_not_captured"] = result == {'PASS_THROUGH'} and not handler.dragging
                result = studio.ANGELO_OT_studio_input.modal(handler, bpy.context, event_at(other, 'MOUSEMOVE', 'NOTHING'))
                state["checks"]["mouse_can_leave_studio_for_another_editor"] = result == {'PASS_THROUGH'}
                result = studio.ANGELO_OT_studio_input.modal(handler, bpy.context, event_at(other, 'LEFTMOUSE'))
                state["checks"]["outside_click_reaches_other_editor"] = result == {'PASS_THROUGH'}
                stale = qa_operator.stand_in(studio.ANGELO_OT_studio_input, window_pointer=window().as_pointer(),
                                             input_generation=studio._input_generation-1)
                result = studio.ANGELO_OT_studio_input.modal(stale, bpy.context, event_at(target, 'MOUSEMOVE'))
                state["checks"]["stale_handlers_cancel_without_clearing_current_handler"] = (
                    result == {'CANCELLED', 'PASS_THROUGH'} and window().as_pointer() in studio._modals)
            state["checks"]["scene_changes_are_cancelled_not_committed"] = before == [(o.name, tuple(o.location), tuple(o.scale)) for o in bpy.context.scene.objects]
            state["checks"]["no_canvas_errors"] = studio.last_error is None and chat_view.last_error is None
            state["success"] = all(state["checks"].values())
            (OUT / "input-capture.json").write_text(json.dumps(state, indent=2))
            print("ANGELO_INPUT_CAPTURE_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        (OUT / "input-capture.json").write_text(json.dumps(state, indent=2))
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
