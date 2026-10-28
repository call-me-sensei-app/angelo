# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lazy native Coding Agent bootstrap. Never evaluate a chat draft as Python.

The compiled editor is selectable before the host addon is enabled. Enable it
on the next UI tick, not while the native text surface is inside a draw call.
The host replaces this module alias with its real console adapter on register.
"""

import bpy

_scheduled = False


def _start():
    global _scheduled
    if _scheduled:
        return
    _scheduled = True

    def enable():
        global _scheduled
        import addon_utils
        try:
            if not addon_utils.check("angelo_agent")[1]:
                addon_utils.enable("angelo_agent", default_set=False, persistent=False)
            from angelo_agent import ui, console, live_windows
            for window, area in live_windows.window_areas(bpy.context.window_manager):
                if console.is_chat(area):
                    if ui._main_window_pointer is None:
                        ui._main_window_pointer = window.as_pointer()
                    ui.setup_chat_area(window, area)
        finally:
            _scheduled = False
        return None
    bpy.app.timers.register(enable, first_interval=0.01)


def banner(context):
    _start()
    return {'FINISHED'}


def execute(context, is_interactive=False):
    _start()
    return {'CANCELLED'}  # Wait for the host; NEVER fall back to Python execution.


def autocomplete(context):
    return {'CANCELLED'}


def copy_as_script(context):
    return {'CANCELLED'}
