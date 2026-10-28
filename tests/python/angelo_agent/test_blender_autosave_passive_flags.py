# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The passive-modal option on the real build, in background: what lets auto-save run with the chat open.

Up to 1.0.5 Angelo never auto-saved while the chat was open, because auto-save waits for every modal operator and the
chat's input handler is one (``test_blender_autosave_passive_modals`` proves the fix with the timer, in a window).
The timer only runs in a window's event loop. This suite checks, on any platform's build without a window, what that
proof rests on:
- Blender has the ``MODAL_PASSIVE`` operator option;
- every Angelo canvas input and passive overlay that is registered carries it, and no holding tool does;
- an operator can declare it or not;
- auto-save writes ``<pid>_autosave.blend`` in the temp folder, where the GUI suite looks for it.

Writes ``autosave-passive-flags.json`` in ``ANGELO_QA_DIR``; exits 1 on any failed check.
"""

import glob
import json
import os
import sys
import tempfile
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-autosave-passive-flags-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks = {}
report = {"checks": checks, "operators": {}}


class ANGELOQA_OT_passive_flag(bpy.types.Operator):
    bl_idname = "angeloqa.passive_flag"
    bl_label = "QA passive flag"
    bl_options = {'INTERNAL', 'MODAL_PASSIVE'}


class ANGELOQA_OT_plain_flag(bpy.types.Operator):
    bl_idname = "angeloqa.plain_flag"
    bl_label = "QA plain flag"
    bl_options = {'INTERNAL'}


def options(identity):
    group, name = identity.split(".", 1)
    return set(getattr(getattr(bpy.ops, group), name).bl_options)


def autosaves():
    base = os.path.dirname(bpy.app.tempdir.rstrip("/\\"))   # bpy.app.tempdir is a session folder in the base.
    return sorted(glob.glob(os.path.join(base, "*%d_autosave.blend" % os.getpid())))


def run():
    items = bpy.types.Operator.bl_rna.properties["bl_options"].enum_items.keys()
    checks["blender_has_the_option"] = "MODAL_PASSIVE" in items
    addon_utils.enable("angelo_agent", default_set=True)
    from angelo_agent import input_policy
    checks["the_add_on_sees_it"] = input_policy._native_passive_option() == frozenset({"MODAL_PASSIVE"})
    for identity in sorted(input_policy.CANVAS_INPUTS | input_policy.PASSIVE_OVERLAYS | input_policy.HOLDING_TOOLS):
        try:
            flags = options(identity)
        except (AttributeError, KeyError):
            report["operators"][identity] = "not registered"
            continue
        passive = identity not in input_policy.HOLDING_TOOLS
        report["operators"][identity] = sorted(flags)
        checks["%s_passive_is_%s" % (identity, passive)] = ("MODAL_PASSIVE" in flags) == passive
    registered = {i for i, v in report["operators"].items() if v != "not registered"}
    # Not vacuous: the chat's own handler is always registered. A passive operator a machine never registers (parallel
    # edit below the bar) cannot block Auto Save; test_native_autosave_passive_modals proves every passive class
    # carries the option.
    checks["the_chat_input_is_registered"] = "angelo.chat_input" in registered
    checks["the_chat_input_is_passive"] = "MODAL_PASSIVE" in options("angelo.chat_input")
    bpy.utils.register_class(ANGELOQA_OT_passive_flag)
    bpy.utils.register_class(ANGELOQA_OT_plain_flag)
    checks["an_operator_can_declare_it"] = options("angeloqa.passive_flag") == {"INTERNAL", "MODAL_PASSIVE"}
    checks["an_operator_need_not"] = options("angeloqa.plain_flag") == {"INTERNAL"}
    for path in autosaves():
        os.remove(path)
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.ed.undo_push(message="Autosave QA edit")
    checks["save_auto_save_writes"] = bpy.ops.wm.save_auto_save() == {"FINISHED"}
    found = autosaves()
    report["autosave_files"] = found
    checks["autosave_is_in_the_temp_folder_named_by_pid"] = len(found) == 1
    for path in found:
        os.remove(path)


try:
    run()
except Exception as error:   # Reported as a failed check, never a silent pass.
    import traceback
    checks["no_error"] = False
    report["error"] = traceback.format_exc()
report["success"] = bool(checks) and all(checks.values())
(OUT / "autosave-passive-flags.json").write_text(json.dumps(report, indent=2))
print("ANGELO_AUTOSAVE_PASSIVE_FLAGS", json.dumps({"success": report["success"]}), flush=True)
sys.exit(0 if report["success"] else 1)
