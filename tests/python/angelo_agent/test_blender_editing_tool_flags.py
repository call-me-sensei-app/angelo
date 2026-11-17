# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's operation in progress, read from the real build's operator flags, in background.

``input_policy.editing_tools`` decides when Angelo's agent, external apps and snapshots wait for the person
(``edit_tool_hold``). Its rule is Blender's own for "not in a valid state to save": a running modal operator without
MODAL_PASSIVE. The first version held only for operators with UNDO or UNDO_GROUPED, which a sculpt or paint stroke
and a mask gesture never carry (they manage their own undo), and its host tests used invented flags (break-it review
2026-11-13, M1). This suite reads the real ``bl_options`` of common tools from different families and checks the
predicate on a window running each one (a stand-in with the operator's real identity, flags and label: a modal cannot
be started without a window). It also records which carry an Undo flag, the evidence for the rule.

Writes ``editing-tool-flags.json`` in ``ANGELO_QA_DIR``; exits 1 on any failed check.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-editing-tool-flags-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks = {}
report = {"checks": checks, "operators": {}}

# Families of the person's operations: transform, mesh edit, a macro, sculpt, paint, gestures, grease pencil, filters.
EDITING = ("transform.translate", "transform.edge_crease", "transform.shrink_fatten", "mesh.bevel", "mesh.knife_tool",
           "mesh.loopcut_slide", "mesh.inset", "sculpt.brush_stroke", "sculpt.mesh_filter",
           "paint.image_paint", "paint.vertex_paint", "paint.weight_paint", "paint.mask_lasso_gesture",
           "sculpt.trim_box_gesture", "grease_pencil.brush_stroke", "object.transform_axis_target")
NAVIGATION = ("view3d.rotate", "view3d.move", "view3d.zoom", "view3d.walk")   # A short pause while the person moves.
# The ones a build always registers in background: the check is never vacuous.
REQUIRED = ("transform.translate", "transform.edge_crease", "mesh.bevel", "mesh.knife_tool", "sculpt.brush_stroke",
            "paint.image_paint", "view3d.rotate")


def operator(identity):
    group, name = identity.split(".", 1)
    return getattr(getattr(bpy.ops, group), name)


def running(identity, flags, label):
    """A window manager whose one window runs ``identity`` with its real flags and label."""
    group, name = identity.split(".", 1)
    op = SimpleNamespace(bl_idname=group.upper() + "_OT_" + name, bl_options=set(flags), bl_label=label)
    return SimpleNamespace(windows=[SimpleNamespace(modal_operators=[op], screen=None)])


def run():
    addon_utils.enable("angelo_agent", default_set=True)
    from angelo_agent import input_policy
    for identity in EDITING + NAVIGATION:
        try:
            ref = operator(identity)
            flags, label = set(ref.bl_options), ref.get_rna_type().name
        except (AttributeError, KeyError):
            report["operators"][identity] = "not registered"
            checks[identity + "_registered"] = identity not in REQUIRED
            continue
        held = input_policy.editing_tool(running(identity, flags, label))
        report["operators"][identity] = {"bl_options": sorted(flags), "label": label, "held_as": held,
                                         "undo_flag": bool({"UNDO", "UNDO_GROUPED"} & flags)}
        checks[identity + "_holds"] = held == label
        checks[identity + "_is_not_passive"] = "MODAL_PASSIVE" not in flags
    # The evidence for the rule: a sculpt stroke carries no Undo flag, so an Undo-flag rule never held for it.
    stroke = report["operators"].get("sculpt.brush_stroke")
    checks["a_sculpt_stroke_has_no_undo_flag"] = isinstance(stroke, dict) and not stroke["undo_flag"]
    # Angelo's own canvases, overlays and holding tools never hold, with their real flags.
    for identity in sorted(input_policy.CANVAS_INPUTS | input_policy.PASSIVE_OVERLAYS | input_policy.HOLDING_TOOLS):
        try:
            flags = set(operator(identity).bl_options)
        except (AttributeError, KeyError):
            continue
        checks[identity + "_never_holds"] = input_policy.editing_tool(running(identity, flags, identity)) is None
    checks["the_chat_input_was_checked"] = "angelo.chat_input_never_holds" in checks
    # A finished render (no RENDER job runs in background) never holds.
    render = set(operator("render.render").bl_options)
    checks["an_ended_render_never_holds"] = input_policy.editing_tool(running("render.render", render, "Render")) is None
    checks["no_window_holds_nothing"] = input_policy.editing_tool(SimpleNamespace(windows=[])) is None
    # The runtime mark the add-on-modal rule reads (an53, 2.0.0): on Operator, read-only, when this build has it.
    for name in ("has_consumed_input", "has_answered_input"):   # What the add-on-modal rule reads (2.0.0).
        mark = bpy.types.Operator.bl_rna.properties.get(name)
        report[name] = None if mark is None else {"type": mark.type, "read_only": mark.is_readonly}
        if mark is not None:
            checks[name + "_is_a_read_only_boolean"] = mark.type == "BOOLEAN" and mark.is_readonly


try:
    run()
except Exception as error:   # Reported as a failed check, never a silent pass.
    import traceback
    checks["no_error"] = False
    report["error"] = traceback.format_exc()
report["success"] = bool(checks) and all(checks.values())
(OUT / "editing-tool-flags.json").write_text(json.dumps(report, indent=2))
print("ANGELO_EDITING_TOOL_FLAGS", json.dumps({"success": report["success"]}), flush=True)
sys.exit(0 if report["success"] else 1)
