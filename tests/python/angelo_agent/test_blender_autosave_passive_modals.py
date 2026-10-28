# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Auto-save writes while the chat is open, and still waits for a real modal tool (``OPTYPE_MODAL_PASSIVE``).

Evidence: Blender's auto-save timer (``wm_autosave_timer``) skips the save and retries every 10 ms while any window
has a modal operator, and the chat's input handler (``angelo.chat_input``) runs in every window that shows the chat.
Up to 1.0.5, Angelo with the chat open never auto-saved: manual work between agent requests had no recovery file.
Angelo's canvases and overlays now carry the native MODAL_PASSIVE option (``input_policy.modal_options``), which
auto-save ignores.

Real default startup with the chat docked, Auto Save at one minute, undoable edits (Add Cube), simulated events only.
1. The chat's handler runs and every passive canvas or overlay operator carries MODAL_PASSIVE; a holding tool does not.
2. With the chat's handler and a second passive modal running, ``<pid>_autosave.blend`` is written within a minute.
3. With a real (non-passive) modal running beside them, no auto-save is written for well over a minute.
4. Once that modal ends, the deferred auto-save is written at once (the 10 ms retry), as in stock Blender.
No CLI, account, network or inference.

Run through ``run_native_suites.py --gui --include test_blender_autosave_passive_modals``; check
``autosave-passive-modals.json`` for ``success`` and ``phase == "complete"``.
"""

import glob
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import input_policy, product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-autosave-passive-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "autosave-passive-modals.json"
bpy.context.preferences.use_preferences_save = False
INTERVAL = 60.0   # Auto Save's shortest interval (one minute).
state = {"phase": "boot", "success": False, "checks": {}, "events": []}
started = time.monotonic()
held = {"release": False}


class ANGELOQA_OT_passive_watch(bpy.types.Operator):
    """A modal that only passes events on, as Angelo's canvases do."""
    bl_idname = "angeloqa.passive_watch"
    bl_label = "QA passive watch"
    bl_options = {'INTERNAL', 'MODAL_PASSIVE'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return {'PASS_THROUGH'}


class ANGELOQA_OT_real_tool(bpy.types.Operator):
    """A modal tool in progress (no MODAL_PASSIVE): auto-save must wait until it ends."""
    bl_idname = "angeloqa.real_tool"
    bl_label = "QA real tool"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        return {'CANCELLED', 'PASS_THROUGH'} if held["release"] else {'PASS_THROUGH'}


def window():
    return bpy.context.window_manager.windows[0]


def modals():
    return [input_policy.operator_identity(op) for w in bpy.context.window_manager.windows for op in w.modal_operators]


def autosaves():
    base = os.path.dirname(bpy.app.tempdir.rstrip("/\\"))   # bpy.app.tempdir is a session folder in the base.
    return sorted(glob.glob(os.path.join(base, "*%d_autosave.blend" % os.getpid())))


def remove_autosaves():
    for path in autosaves():
        os.remove(path)


def note(text):
    state["events"].append([round(time.monotonic() - started, 1), text, modals()])


def edit():
    """An undoable edit, as a person makes one: the document becomes unsaved. A script's operator call pushes no
    undo step (``WM_operator_call_py``), so the document would still read as saved and auto-save would rightly skip
    it (#146003); a person's edit pushes one, so this does too."""
    area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=area, region=region):
        result = bpy.ops.mesh.primitive_cube_add(location=(len(state["events"]) * 3.0, 0.0, 0.0))
        bpy.ops.ed.undo_push(message="Autosave QA edit")
    return result == {'FINISHED'} and bpy.data.is_dirty


def invoke(operator):
    with bpy.context.temp_override(window=window()):
        operator('INVOKE_DEFAULT')


def options(identity):
    group, name = identity.split(".", 1)
    return set(getattr(getattr(bpy.ops, group), name).bl_options)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_AUTOSAVE_PASSIVE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.context.preferences.view.use_save_prompt = False
    bpy.ops.wm.quit_blender()


def since(key):
    return time.monotonic() - state[key]


def tick():
    try:
        assert time.monotonic() - started < 420, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        if phase == "boot":
            if not (qa_boot.started() and "angelo.chat_input" in modals()):
                if time.monotonic() - started > 90:
                    checks["the_chat_input_handler_runs"] = False
                    finish()
                    return None
                return .5
            checks["the_chat_input_handler_runs"] = True
            for identity in sorted(input_policy.CANVAS_INPUTS | input_policy.PASSIVE_OVERLAYS
                                   | input_policy.HOLDING_TOOLS):
                try:
                    flags = options(identity)
                except (AttributeError, KeyError):
                    continue   # Registered elsewhere (an approval's operator belongs to its card's module).
                passive = identity not in input_policy.HOLDING_TOOLS
                checks["%s_passive_is_%s" % (identity, passive)] = ("MODAL_PASSIVE" in flags) == passive
                state.setdefault("operators_checked", []).append(identity)
            # Not vacuous: the chat's own handler is always registered. A passive operator a machine never registers
            # (parallel edit below the bar) cannot block Auto Save; test_native_autosave_passive_modals proves every
            # passive class carries the option.
            checks["the_chat_input_is_registered"] = "angelo.chat_input" in state.get("operators_checked", [])
            checks["the_chat_input_is_passive"] = "MODAL_PASSIVE" in options("angelo.chat_input")
            prefs = bpy.context.preferences.filepaths
            remove_autosaves()
            prefs.use_auto_save_temporary_files = True
            prefs.auto_save_time = 1   # Its RNA update restarts the timer: the next auto-save is a minute away.
            invoke(bpy.ops.angeloqa.passive_watch)
            checks["an_edit_leaves_the_document_unsaved"] = edit()
            note("passive: chat handler and a passive watch running")
            state["phase"], state["since_passive"] = "passive", time.monotonic()
            return 1.0
        if phase == "passive":
            if autosaves():
                checks["auto_save_writes_with_the_chat_and_a_passive_modal_running"] = (
                    since("since_passive") < INTERVAL + 15 and "angelo.chat_input" in modals())
                note("passive: auto-save written after %.1f s" % since("since_passive"))
                remove_autosaves()
                invoke(bpy.ops.angeloqa.real_tool)
                checks["a_second_edit_leaves_the_document_unsaved"] = edit()
                note("real tool running")
                state["phase"], state["since_real"] = "real", time.monotonic()
                return 1.0
            if since("since_passive") > INTERVAL + 15:
                checks["auto_save_writes_with_the_chat_and_a_passive_modal_running"] = False
                note("passive: no auto-save")
                finish()
                return None
            return 1.0
        if phase == "real":
            if autosaves():
                checks["auto_save_waits_while_a_real_modal_runs"] = False
                note("real: auto-save written after %.1f s while the tool ran" % since("since_real"))
                finish()
                return None
            if since("since_real") < INTERVAL + 20:   # The timer came due at about 60 s and kept deferring.
                return 1.0
            checks["auto_save_waits_while_a_real_modal_runs"] = "angeloqa.real_tool" in modals()
            held["release"] = True
            view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=view.x + 40, y=view.y + 40)
            note("real tool released")
            state["phase"], state["since_end"] = "after_real", time.monotonic()
            return .1
        if phase == "after_real":
            if autosaves() and "angeloqa.real_tool" not in modals():
                checks["the_deferred_auto_save_writes_once_the_tool_ends"] = since("since_end") < 5.0
                note("after: auto-save written %.2f s after the tool ended" % since("since_end"))
                finish()
                return None
            if since("since_end") > 10.0:
                checks["the_deferred_auto_save_writes_once_the_tool_ends"] = False
                note("after: no auto-save")
                finish()
                return None
            return .1
    except Exception as error:
        finish(error)
        return None
    return .5


bpy.utils.register_class(ANGELOQA_OT_passive_watch)
bpy.utils.register_class(ANGELOQA_OT_real_tool)
bpy.app.timers.register(tick, first_interval=.5)
