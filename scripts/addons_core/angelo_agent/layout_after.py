# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Studio and Flows entries never act on a window whose layout is mid-change (1.0.5).

A layout change (Director's start-up, a chat dock, Manual's tab build) holds ``ui._layout_timer`` while it switches
the window's workspace for a few event-loop passes. An entry acting in those passes reads the transient workspace: in
Director, a Studio click during the tab build took the window for Manual and switched it to the half-made Media
Studio tab, left there with another workspace's layout (35f5b544698 diagnosis, studio_discoverability). As the Chat
toggle already does (``chat_hide.after_move``), such an entry runs once the layout has settled; it is never refused.
"""

import time

from . import live_windows

LIMIT = 10.0   # Seconds an entry waits for a layout change at most; then it runs anyway, never lost.
deferred = []  # Labels of the entries that waited (GUI suites read it).
_running = [False]   # A deferred entry is running: it never waits again (after LIMIT the layout may still be busy).


def busy():
    """A layout change holds the windows' layout: Director's start-up or ``ui._layout_timer`` (docks, the tab build)."""
    if _running[0]:
        return False
    try:
        from . import director, ui
    except ImportError:   # A host test's stand-ins without Blender: nothing moves.
        return False
    return (director._timer is not None or ui._layout_timer is not None)


def run_after(window, action, label=""):
    """``action(window)`` now, or once no layout change is running. Returns True when it was deferred."""
    if not busy():
        action(window)
        return False
    import bpy
    pointer, started = window.as_pointer(), time.monotonic()
    deferred.append(label)

    def wait(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        if target is None:
            return None   # The window closed meanwhile: nothing to act on.
        if busy() and time.monotonic() - started < LIMIT:
            return 0.05
        _running[0] = True
        try:
            action(target)
        except Exception as error:   # noqa: BLE001 - a timer never breaks; the entry says why once.
            print("Angelo: %s did not open:" % (label or "an entry"), error)
        finally:
            _running[0] = False
        return None
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, wait), first_interval=0.05)
    return True


def again(idname, **properties):
    """An action that runs operator ``idname`` (``"angelo.toggle_studio"``) again in the window, with ``properties``:
    once the layout has settled it takes its normal route for where the window then is."""
    # A running operator reports its bl_idname in Blender's C form ("ANGELO_OT_toggle_studio"), the class its Python
    # form ("angelo.toggle_studio"); both name the same bpy.ops entry (35f5b544698 interim line: the C form raised).
    module, name = idname.split("_OT_", 1) if "_OT_" in idname else idname.split(".", 1)
    module = module.lower()

    def action(window):
        import bpy
        with bpy.context.temp_override(window=window, screen=window.screen):
            getattr(getattr(bpy.ops, module), name)(**properties)
    return action
