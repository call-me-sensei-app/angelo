# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hide undoes Show: a docked chat's room goes back where Show took it from (doc/angelo/agent_chat.rst, "Show and
hide").

In Manual mode Show usually makes the chat a new column right of Properties, taking the strip from the editors beside
that column, the 3D view first (``docking.relocate_chat``; ``ui._dock_metadata`` records it per chat area). Hide was
Blender's own Close Area, which hands a closed area's room to the editor sharing its edge, the Properties column, so
every Show and Hide moved the chat's width from the 3D view to Properties. Evidence: on shipped 1.0.4 the Layout tab's
3D view went 1881, 1286, 686, 356 px over three cycles while Properties went 406 to 1931 px (Mac 1 diagnostic and
release-105's screenshot review, 2026-10-23).

Hide now runs the dock's own inverse (``docking.collapse_chat`` with the strip given back), one step per timer tick as
a dock does (``ui._layout_timer``, so a tool, a mode switch or another dock waits for it, and corner Angelo with it).
A chat Show did not widen that way (Director's split, a chat that took its width from the column, one moved by hand)
closes with Close Area, as does any chat whose layout changed so that the inverse no longer fits.
"""

import bpy

from . import docking, layout_hold, live_windows, timer_jobs


def close(window, area):
    """Blender's own Close Area on ``area``: its neighbour along the shared edge takes the room."""
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.area_close()


def request_hide(window, area):
    """Hide the chat docked in ``area`` of ``window``: at once with Close Area, or as the dock's inverse over the next
    timer ticks. The move's end moves corner Angelo (``chat_launcher.settled``)."""
    from . import ui
    pointer, window_pointer = area.as_pointer(), window.as_pointer()
    widened = ui._dock_metadata.pop(pointer, False)
    if not widened or ui._layout_timer is not None:
        close(window, area)
        return

    def finish(target):
        ui._layout_timer = None
        from . import chat_launcher
        chat_launcher.settled(window_pointer)

    def fall_back(target, error):
        """The layout changed since Show: the chat still goes, as Close Area takes it."""
        print("Angelo: hiding the chat with Close Area:", error)
        chat = next((a for a in live_windows.areas(target) if a.as_pointer() == pointer), None)
        if chat is not None:
            try:
                close(target, chat)
            except RuntimeError as failure:
                print("Angelo: hiding the chat:", failure)

    def advance(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        from .input_policy import layout_blocking
        if timer_jobs.aborted(job):   # A load, a quit or unregister ended it and released the layout (timer_jobs).
            return None
        if target is None:
            job.close()
            finish(None)
            return None
        if layout_blocking(target):
            return 0.1
        try:
            return next(job)
        except StopIteration:
            finish(target)
        except Exception as error:   # noqa: BLE001  Any step that fails: the chat still hides.
            fall_back(target, error)
            finish(target)
        return None

    ui._layout_timer = live_windows.deferred(lambda: bpy.context.window_manager, window_pointer, advance)
    job = timer_jobs.track(docking.collapse_chat(window, pointer, True), layout_hold.layout(ui._layout_timer))
    bpy.app.timers.register(ui._layout_timer, first_interval=0.01)


_presses = {}   # window pointer -> toggle presses made while the chat was moving.
_settle = live_windows.Wait()   # Presses wait (bounded) while a window is between screens: it could hold the chat.


def after_move(window):
    """A toggle pressed while the chat moves (a dock, this inverse, a mode switch) is applied once the move ends, never
    refused: Hide became a timed move, so a quick second press met "The chat dock is already moving" (Mac 1 slot 105i,
    2026-10-23). Presses count by parity: two quick presses leave the chat as it was."""
    pointer = window.as_pointer()
    _presses[pointer] = _presses.get(pointer, 0) + 1
    if not bpy.app.timers.is_registered(_apply_presses):
        bpy.app.timers.register(_apply_presses, first_interval=0.05)


def _apply_presses():
    from . import director, ui
    if ui._layout_timer is not None or director._timer is not None:
        return 0.05
    if _settle(not live_windows.settled(bpy.context.window_manager)):
        return 0.05
    pending = [pointer for pointer, count in _presses.items() if count % 2]
    _presses.clear()
    # A toggle can open or close a window (the chat's own), freeing the window the collection iterator stands on: the
    # 6ceaef365ff full line crashed in WindowManager_windows_next here. Each window is looked up afresh by pointer,
    # never iterated across a toggle.
    for pointer in pending:
        window = live_windows.find(bpy.context.window_manager, pointer, live=False)
        if window is None or window.screen is None:   # Gone, or still between screens past the bounded wait above.
            continue
        try:
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.toggle_chat()
        except RuntimeError as error:   # The toggle reported why.
            print("Angelo: chat toggle:", error)
    return None
