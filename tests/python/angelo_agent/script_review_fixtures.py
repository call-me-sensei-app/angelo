# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native probes for what can make a reviewed script's rollback claim false (script_watch, script_errors).

Shared by ``test_blender_script_rollback.py``. Each case reads the scene
itself before and after the script, so a ``main_data_restored`` claim is
checked against Blender, never against the host's own account:

* exits: ``sys.exit``, ``exit``, ``quit``, ``raise SystemExit`` and
  ``KeyboardInterrupt`` are failures of the script with the normal rollback,
  and this process keeps running;
* the script replays the restore point with hidden Undo and Redo and pushes
  its own step: never claimed;
* linked library data (a linked object's modifier visibility) and an editable
  brush asset in a paint mode, which Undo does not reload: never claimed, while
  the same modes with no such change still are; a brush the script links by
  entering a paint mode and then edits is never claimed either;
* window-manager and workspace properties are outside the claim, and the
  result says so.
"""

import os

import bpy

from angelo_agent import script_errors
from angelo_agent.script_watch import INTERFACE_NOTE

HIDDEN = ("import bpy\ned = bpy.ops.ed\nU = getattr(ed, 'un' + 'do')\nR = getattr(ed, 're' + 'do')\n"
          "P = getattr(ed, 'un' + 'do' + '_push')\n")
LIGHT = "import bpy\nbpy.data.lights['Light'].energy = 777.0\n"
RAISE = "raise KeyError('Scale')\n"
EXITS = {
    "sys_exit": (LIGHT + "import sys\nsys.exit(1)\n", "SystemExit: 1"),
    "builtin_exit": (LIGHT + "exit()\n", None),
    "builtin_quit": (LIGHT + "quit()\n", None),
    "raise_system_exit": (LIGHT + "raise SystemExit\n", "SystemExit"),
    "keyboard_interrupt": (LIGHT + "raise KeyboardInterrupt()\n", "KeyboardInterrupt"),
}
# name: (the person's mode, the script, whether the claim must be made)
CLAIMS = {
    "marker_replay": ("OBJECT", HIDDEN + "U()\nR()\nbpy.data.lights['Light'].energy = 12345.0\nP(message='mine')\n" + RAISE,
                      False),
    "linked_modifier_toggle": ("OBJECT", LIGHT + "bpy.data.objects['QA Linked'].modifiers[0].show_viewport = False\n"
                               + RAISE, False),
    "linked_present_local_change_only": ("OBJECT", LIGHT + RAISE, True),
    "vertex_paint_brush": ("VERTEX_PAINT", "import bpy\nbpy.context.tool_settings.vertex_paint.brush.strength = 0.123\n"
                           + RAISE, False),
    "weight_paint_brush": ("WEIGHT_PAINT", "import bpy\nbpy.context.tool_settings.weight_paint.brush.strength = 0.321\n"
                           + RAISE, False),
    "vertex_paint_no_brush_change": ("VERTEX_PAINT", LIGHT + RAISE, True),
    # Entering the mode links its brush (test_blender_script_rollback's Sculpt case: still claimed); an edit is not.
    "script_enters_vertex_paint_and_edits_the_brush": (
        "OBJECT", "import bpy\nbpy.ops.object.mode_set(mode='VERTEX_PAINT')\n"
        "bpy.context.tool_settings.vertex_paint.brush.strength = 0.123\n" + RAISE, False),
    "weight_paint_no_brush_change": ("WEIGHT_PAINT", LIGHT + RAISE, True),
}


def _library(out):
    path = str(out / "qa-linked-library.blend")
    if not os.path.exists(path):
        mesh = bpy.data.meshes.new("QA Linked Mesh")
        obj = bpy.data.objects.new("QA Linked", mesh)
        obj.modifiers.new("QA Linked Subsurf", "SUBSURF")
        bpy.data.libraries.write(path, {obj})
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
    return path


def _setup(build, out, mode):
    """``build``'s scene with a linked object; the restore baseline is pushed before the person's mode."""
    build("OBJECT", history=False)
    with bpy.data.libraries.load(_library(out), link=True) as (_source, target):
        target.objects = ["QA Linked"]
    bpy.context.window_manager.windows[0].scene.collection.objects.link(target.objects[0])
    bpy.context.window_manager.windows[0].view_layer.update()
    bpy.ops.ed.undo_push(message="Review probe baseline")
    if mode != "OBJECT":
        assert "FINISHED" in bpy.ops.object.mode_set("EXEC_DEFAULT", True, mode=mode), mode


def _brush(mode):
    paint = {"VERTEX_PAINT": "vertex_paint", "WEIGHT_PAINT": "weight_paint"}.get(mode)
    brush = getattr(bpy.context.tool_settings, paint).brush if paint else None
    return None if brush is None else [brush.name, brush.library is not None, round(brush.strength, 4)]


def _probe(mode):
    linked = bpy.data.objects.get("QA Linked")
    return {"mode": bpy.context.mode, "energy": bpy.data.lights["Light"].energy,
            "linked_viewport": None if linked is None else [item.show_viewport for item in linked.modifiers],
            "brush": _brush(mode),
            "linked_brushes": sorted([item.name, round(item.strength, 4)] for item in bpy.data.brushes if item.library)}


def _case(report, name, mode, code, run_script):
    before = _probe(mode)
    result = run_script(code)
    after = _probe(mode)
    changed = sorted(key for key in before if before[key] != after[key])
    report["cases"]["review:" + name] = {"before": before, "after": after, "changed": changed,
                                         **{key: result.get(key) for key in (
                                             "main_data_restored", "rolled_back", "error", "rollback",
                                             "rollback_scope_excludes", "partial_changes_possible")}}
    claimed = result.get("main_data_restored") is True
    report["checks"]["review:%s:never_claimed_while_changed" % name] = not (claimed and changed)
    report["checks"]["review:%s:partial_recovery_explicit" % name] = (
        result.get("partial_changes_possible") is True and result.get("rolled_back") is False and "scene_changed" not in result)
    return result, claimed, changed


def review_cases(report, build, run_script, out):
    checks = report["checks"]
    for name, (code, error) in EXITS.items():
        _setup(build, out, "OBJECT")
        result, claimed, changed = _case(report, "exit:" + name, "OBJECT", code, run_script)
        checks["review:exit:%s:a_failure_rolled_back" % name] = claimed and not changed and result.get("script_error") is True
        if error is not None:
            checks["review:exit:%s:error_names_the_exit" % name] = str(result.get("error", "")).startswith(error)
    for name, (mode, code, claim) in CLAIMS.items():
        _setup(build, out, mode)
        result, claimed, changed = _case(report, name, mode, code, run_script)
        if claim:
            checks["review:%s:still_claimed_and_true" % name] = claimed and not changed
        else:
            checks["review:%s:not_claimed" % name] = not claimed and "still in the scene" in str(result.get("rollback"))
            checks["review:%s:the_change_was_real" % name] = bool(changed)
    checks["review:marker_replay:says_the_script_used_the_history"] = (
        script_errors.OWN_HISTORY in str(report["cases"]["review:marker_replay"]["rollback"]))
    for name in ("linked_modifier_toggle", "vertex_paint_brush", "weight_paint_brush",
                 "script_enters_vertex_paint_and_edits_the_brush"):
        checks["review:%s:says_linked_data" % name] = "linked library data" in str(report["cases"]["review:" + name]["rollback"])
    # Interface data is outside the claim: the result says so wherever it claims Main data.
    _setup(build, out, "OBJECT")
    code = "import bpy\nbpy.context.workspace['qa_review'] = 1\nbpy.context.window_manager['qa_review'] = 2\n" + LIGHT[11:] + RAISE
    result, claimed, changed = _case(report, "interface_properties", "OBJECT", code, run_script)
    checks["review:interface_properties:scope_explicit"] = (
        claimed and not changed and result.get("rollback_scope") == "MAIN_DATA"
        and result.get("rollback_scope_excludes") == ["window_manager", "workspaces", "screens"]
        and INTERFACE_NOTE in str(result.get("rollback")))
    for owner in (bpy.context.workspace, bpy.context.window_manager):
        owner.pop("qa_review", None)
