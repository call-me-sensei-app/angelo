# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rebinds Angelo's own interface fonts after Blender reloads its fonts.

The chat, the welcome screen and the notice cards draw with fonts Angelo loads itself
(``chat_view.reload_fonts``, through ``blf.load``). Blender frees every loaded font whenever it
initializes its interface fonts again (``ui::style_init`` calls ``BLF_unload_all``): applying a theme
preset, Reset to Default Theme, or changing a Text Rendering preference. The saved IDs then name no font
and every custom canvas draws without text. Opening a file does the same and is handled at load
(``ui.document_load_post``).

Angelo's own build keeps a font a script loaded through both reloads (``BLF_SCRIPT_OWNED``, ``blf.cc``), so this
is the fallback for anything that still drops one. Blender sends no notification Python can receive for this, so a
timer checks twice a second whether the chat's fonts still measure text, and loads them again when they do not. Main thread only; two
``blf.dimensions`` calls per check, no file access unless a reload is needed.
"""

import blf
import bpy

INTERVAL = 0.5
checks = {"reloads": 0}   # Window fixtures read it.


def lost(font):
    """True when a loaded font ID no longer measures text (0 means "never loaded", not lost)."""
    if not font:
        return False
    blf.size(font, 12)   # Every drawing call sets its own size first, so this changes nothing visible.
    return blf.dimensions(font, "M")[0] <= 0


def check():
    from . import chat_view, live_windows
    if lost(chat_view._font) or lost(chat_view._mono):
        chat_view.reload_fonts()
        checks["reloads"] += 1
        for _window, area in live_windows.window_areas(bpy.context.window_manager):
            area.tag_redraw()
    return INTERVAL


def register():
    if not bpy.app.background:
        bpy.app.timers.register(check, first_interval=INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(check):
        bpy.app.timers.unregister(check)
