# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight tools on the host (``engine_check_spec``): the agent's and MCP apps' route. Main thread.

CHECK reads the scene. FIX, after its card, checks the scene again, refuses any requested finding that is no longer
there or has no typed fix (the scene changed since the check: check again), applies the rest (``engine_fixes``) in
Object Mode as one Undo step for all of them (in a window, the approval's apply operator's), then reports what
each changed and what remains. When the
engine link has an asset of the target's engine open, its import settings (Godot's ``.import``) shape the profile,
as they shape the link's own export.
"""

import bpy

from . import engine_preflight
from .refusal_codes import RefusalError

UNDO_FIX = "Engine fixes"   # i18n: undo history entry, as Blender's own are named in English


def _link_params(target):
    """The open engine-link asset's import settings when it belongs to ``target``'s engine, else None."""
    from . import engine_link
    found = engine_link._state.get("session")
    if not found or engine_preflight.TARGET_FOR_ENGINE.get(found["asset"].engine) != target:
        return None
    return found.get("params")


def _run(window, request):
    return engine_preflight.run(window, request["target"], request["format"], request.get("engine_version"),
                                request.get("selection_only", False), _link_params(request["target"]))


def _fix(window, request):
    if bpy.context.mode != "OBJECT":
        raise RefusalError("Switch Blender to Object Mode before approving these fixes", code="UNSUPPORTED")
    report, facts, options = _run(window, request)
    found = {(finding["code"], finding["subject"]): finding for finding in report["findings"]}
    chosen, gone, unfixable = [], [], []
    for item in request["fixes"]:
        finding = found.get((item["code"], item["subject"]))
        where = "%s on %s" % (item["code"], item["subject"] or "the scene")
        if finding is None:
            gone.append(where + ": not found now")
        elif not finding["fixable"]:
            unfixable.append(where + ": no safe typed fix here")
        elif (finding["fixable"], item["subject"]) not in chosen:
            chosen.append((finding["fixable"], item["subject"]))
    if gone:
        raise RefusalError("Check the scene again before fixing: " + "; ".join(gone + unfixable),
                           code="STALE_REVISION")
    if unfixable:
        raise RefusalError("Only findings with fixable can be fixed: " + "; ".join(unfixable), code="VALIDATION")
    from . import engine_fixes
    with bpy.context.temp_override(**({} if window is None else {"window": window})):
        applied, failed, touched = engine_fixes.apply(chosen, facts, report["target"], report["format"], options)
        changed = bool(applied) or touched
        # One step for everything that ran, a fix that failed part way included. In a window the approval's apply
        # operator owns it (ui.ANGELO_OT_apply_approved_tool, an Undo operator the Approve click calls with undo on):
        # a push here would add a second step with the same state, so the first Ctrl+Z would seem to do nothing.
        # A background Blender has no operator around the call, so the route pushes it there.
        if changed and bpy.app.background:
            bpy.ops.ed.undo_push(message=UNDO_FIX)
    after = engine_preflight.check(window, report["target"], report["format"], request.get("engine_version"),
                                   params=_link_params(request["target"]))
    from .engine_findings import summarize
    return {"success": not failed, "applied": applied,
            "failed": [{"fix": fix, "subject": subject, "reason": reason} for fix, subject, reason in failed],
            "undoable": changed, "undo_steps": 1 if changed else 0,
            "remaining": summarize(after["findings"]), "counts": after["counts"]}


def execute(controller, window, name, request):
    from .engine_check_spec import CHECK
    try:
        if name == CHECK:
            return engine_preflight.for_agent(_run(window, request)[0], request.get("code"))
        return _fix(window, request)
    except RefusalError:
        raise
    except ValueError as error:
        raise RefusalError(str(error), code="VALIDATION") from None
