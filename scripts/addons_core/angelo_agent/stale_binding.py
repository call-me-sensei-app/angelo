# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The refusal of an approved call whose binding changed before it ran (``Controller._execute_tool``).

A call binds what its approval request saw (``Controller._precondition``): a script the scene's revision, frame, mode
and every object's transform, data and modifier count (``BlenderExecutor.precondition``), a merge its parts' saved
files, a typed edit its target, a paid generation its connection and model. When that binding differs at the run, the
call is refused ``STALE_REVISION`` before anything of it runs, and the refusal says ``scene_changed: false`` as the
dispatch site and native staging say for theirs (``request_recovery.unchanged``): nothing of the request is held, its
other edits, merges and jobs run as usual, and the call made again binds what is current then. The binding
guards the approval (what was reviewed is what runs), never what the model last read: a change before the approval
request is never refused at all, so a refusal after it calls for a look, not a hold.

Recorded (C3 early town-centre diagnostic, Sol on Codex): the lead's script was refused here after a merge changed the
scene; the refusal did not say nothing had run, so the recovery barrier read it as an edit that may have run, found no
target in a whole-scene binding and held every later scene edit of the request, merges included, until the next
request (11 built parts were never merged). Pinned for every bound tool in ``test_native_recovery_hold_start.py``.

The text names what changed as far as the binding's parts tell, binding-neutral (a merge binds files, a generation a
connection) and never as a complete account of the change.
"""

from .refusal_codes import RefusalError

SCRIPT = "angelo_execute_python"
# What a refusal before ``started_call`` says (``Controller._execute_tool``), as the dispatch site and native staging say:
# nothing of the call ran. ``error_result`` still drops it for an unclassified code, which keeps holding wherever it
# arises (agent_execution.rst: unknown and internal failures do not claim scene_changed false).
NOT_STARTED = {"scene_changed": False, "partial_changes_possible": False}
MOMENT = "after it was bound, before it ran; nothing of it ran"
# "Call", never "request": the request is the person's message, which the recovery barrier holds or frees (audit-1010).
LOOK = "Look at what changed, then call it again; the new call binds what is current then."
# The script binding's parts (BlenderExecutor.precondition), in order. The refusal lists the changed ones after a colon,
# separated by semicolons, so a part's own words may hold one parenthetical or commas without nesting or blurring the
# list (``refusal``; every combination is pinned in test_native_recovery_hold_start).
# The revision moves with every main-thread depsgraph update (blender_tools.track_scene_changes): an edit, a selection
# or an evaluation; it is named only when no other part tells more.
SCRIPT_PARTS = (("scene", "the active scene switched"),
                ("revision", "the scene updated (an edit, a selection or an evaluation)"),
                ("frame", "the frame"), ("mode", "the mode"),
                ("objects", "objects were added, removed or moved, or their data or modifiers changed"))
PLAYBACK = "animation playback was running"


def same(before, after):
    """Whether two bindings are equal, a NaN equal to a NaN in the same place (every scene binding check).

    ``==`` holds no NaN equal to itself, so a binding read from an object holding one (``angelo_set_transform``'s
    location, a batch of them, a program binding's tuples) never matched its own approval and every call of it was
    refused stale at its run, Auto's included, however often it was asked again (break-it C7b N-1). Anything else
    compares exactly as ``==`` does. Used at the run, in native staging, at both commit fences, by a memory edit's review
    check and by a worker's own approved check (``worker_host``); such a value crosses the worker connection as itself (``worker_values``).
    """
    if before is after:
        return True
    if type(before) is float and type(after) is float:
        return before == after or before != before and after != after
    if type(before) is not type(after):
        return before == after
    if isinstance(before, (tuple, list)):
        return len(before) == len(after) and all(map(same, before, after))
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(same(value, after[key]) for key, value in before.items())
    return before == after


def script_parts(before, after):
    """The script binding's parts that differ, as (key, label) pairs, or None for another binding's shape."""
    if not (isinstance(before, tuple) and isinstance(after, tuple) and len(before) == len(after) == len(SCRIPT_PARTS)):
        return None
    parts = [part for part, old, new in zip(SCRIPT_PARTS, before, after) if not same(old, new)]
    return [part for part in parts if part[0] != "revision"] or parts


def _playing(executor):
    try:
        return getattr(executor, "playback_running", lambda: False)() is True
    except Exception:   # noqa: BLE001 - optional detail of the refusal's text.
        return False


def refusal(call, executor, current, change=None):
    """The ``STALE_REVISION`` error for ``call``, whose approved ``call.precondition`` differs from ``current``.

    ``change`` is the program binding's classification (``program_context.precondition_change``), or None.
    """
    from .program_context import changed_message
    message = changed_message(change, MOMENT)
    parts = script_parts(call.precondition, current) if call.name == SCRIPT else None
    changed = [key for key, _label in parts] if parts else ([change["kind"]] if message else [])
    if message is None:
        labels = [label for _key, label in parts or ()]
        if "frame" in changed and _playing(executor):
            labels[changed.index("frame")] += " (" + PLAYBACK + ")"
        what = "; ".join(labels) if labels else "its target, files, review or connection"
        message = "What this call is bound to changed before it ran: %s. Nothing of it ran. %s" % (what, LOOK)
    return RefusalError(message, code="STALE_REVISION", details={"binding_changed": changed} if changed else None)
