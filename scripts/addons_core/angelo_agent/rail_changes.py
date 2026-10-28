# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which of Angelo's own scene changes put the session rail's Main picture out of date: those its Solid picture can
show (the coordinator, 2026-10-25: a selection redrew it every second or two at ~200k instances, 73-98 ms each)."""

_drawn = {}   # session id -> ``shown_state`` of its scene when its picture was drawn.


def shown_state(depsgraph):
    """``_angelo_live.shown_state``: a number that changes when which objects ``depsgraph`` shows changes, never with
    selection; None when it cannot be read (an older build, not evaluated)."""
    try:
        import _bpy
        live = getattr(_bpy, "_angelo_live", None)
        return live.shown_state(depsgraph) if hasattr(live, "shown_state") else None
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return None


def drawn(session_id, depsgraph):
    """The picture of ``session_id`` was drawn from ``depsgraph`` as it shows now."""
    _drawn[session_id] = shown_state(depsgraph)


def record(session_id, shown):
    """The picture of ``session_id`` was drawn from what ``shown`` (``shown_state``, read when its redraw began)."""
    _drawn[session_id] = shown


def visual_update(depsgraph, session_id):
    """Whether a depsgraph update can change the picture of ``session_id``: any geometry, transform or shading
    update; else (a bare Scene update, as a selection, the active object, a rename and an eye toggle all reach Python,
    Mac 2 probe 2026-10-25) whether which objects it shows changed since its picture was drawn (``shown_state``); True
    whenever that cannot be told."""
    try:
        if any(update.is_updated_geometry or update.is_updated_transform or update.is_updated_shading
               for update in depsgraph.updates):
            return True
    except (AttributeError, ReferenceError, RuntimeError):
        return True
    shown = shown_state(depsgraph)
    return shown is None or _drawn.get(session_id) is None or shown != _drawn[session_id]


def forget():
    _drawn.clear()
