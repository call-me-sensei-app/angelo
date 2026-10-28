# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a merge, the person's main 3D view is framed once on what it brought.

The Director capture (2026-10-25, Sol desk_chair on 9060a812b84): the merged desk and chair sat a few pixels wide while
the view stayed framed on the empty start scene; nothing framed the person's view on a merge (the manual proofs looked
right only because their harness did). The coordinator approved (2026-10-25): when a merge lands and the lead's scene
is shown, frame the merged parts once in the main 3D view, smoothed; only if the person has not moved that view since
the lead's turn began (the view as each turn found it, ``note_turn``); never during their navigation (it waits for that
to end, ``WAIT`` at most, and gives up if the view moved); with no undo step and the selection as it was.
"""

import time

WAIT = 3.0             # Seconds a framing waits for the person's navigation or tool to end.
_turn_views = {}       # chat session id -> the main 3D view's matrix when that chat's latest turn began


def _view(window):
    """``(area, region, region_3d)`` of the main 3D view (the one the rail draws on), or None."""
    from . import live_view_draw
    area = live_view_draw.target(window) if window is not None else None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area is not None else None
    data = getattr(region, "data", None)
    return (area, region, data) if data is not None else None


def _matrix(data):
    return [round(value, 5) for row in data.view_matrix for value in row]


def note_turn(chat):
    """The main view as ``chat``'s turn begins (any chat: a lead's merge compares against its own turn's)."""
    try:
        from . import ui
        found = _view(ui.main_window())
        if found is not None:
            _turn_views[chat.state["id"]] = _matrix(found[2])
    except Exception:   # noqa: BLE001 - a turn never fails for a view it could not read; that merge just won't frame.
        _turn_views.pop(getattr(chat, "state", {}).get("id"), None)


def after_merge(lead, window, collections):
    """Frame the main view once on the objects of the merged ``collections`` (names), when the rules allow."""
    import bpy   # Here, not at the top: the controller imports this module in host tests, where no bpy exists.
    from . import view_warm
    view_warm.soon(window)   # The parts' materials compile in the background before the lead looks at them.
    lead_id = lead.state["id"]
    names = [name for name in collections if name]
    if not names or lead_id not in _turn_views:
        return
    until = time.monotonic() + WAIT
    from . import live_target
    held = live_target.window_ref(window)   # A timer may run after its window closed or a load (break-it SW-H-1).

    def attempt():
        from . import input_policy, parallel_sessions
        window = held()
        if window is None:
            return None   # Its window is gone: nothing to frame.
        try:
            found = _view(window)
            if found is None or window.scene != parallel_sessions._document_scene(lead):
                return None   # The lead's scene is not shown: the person is elsewhere.
            if _matrix(found[2]) != _turn_views.get(lead_id):
                return None   # The person moved the view since the turn began: theirs, never reframed.
            if input_policy.interaction_active(window):
                return .2 if time.monotonic() < until else None   # Never during their navigation or a tool.
            objects = [obj for name in names if (collection := bpy.data.collections.get(name)) is not None
                       for obj in collection.all_objects]
            if objects and _frame(window, found, objects):   # Its own move: a later merge of this turn may frame too.
                bpy.app.timers.register(lambda: _settled(lead_id, held()), first_interval=.6)   # After the smoothing.
        except Exception as error:   # noqa: BLE001 - a framing never fails the merge it follows.
            print("Angelo: framing the merged parts failed:", error)
        return None

    bpy.app.timers.register(attempt, first_interval=0.0)


def _settled(lead_id, window):
    found = _view(window) if window is not None else None   # None: its window is gone (live_target).
    if found is not None:
        _turn_views[lead_id] = _matrix(found[2])
    return None


def _frame(window, found, objects):
    """Blender's own View Selected on ``objects`` (smoothed as the person's Smooth View sets), with the selection and
    the active object as they were; a view operator pushes no undo step."""
    import bpy
    area, region, _data = found
    layer = window.view_layer
    shown = [obj for obj in objects if obj.name in layer.objects and obj.visible_get(view_layer=layer)]
    if not shown:
        return False
    selected = [obj for obj in layer.objects if obj.select_get(view_layer=layer)]
    active = layer.objects.active
    try:
        for obj in selected:
            obj.select_set(False, view_layer=layer)
        for obj in shown:
            obj.select_set(True, view_layer=layer)
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.view3d.view_selected('EXEC_DEFAULT', use_all_regions=False)
    finally:
        for obj in shown:
            obj.select_set(False, view_layer=layer)
        for obj in selected:
            obj.select_set(True, view_layer=layer)
        layer.objects.active = active
    return True
