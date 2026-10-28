# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lazy Generative AI editor bootstrap; a prompt is never executable Python."""

import bpy

_scheduled = False


def banner(context):
    global _scheduled
    if not _scheduled:
        _scheduled = True

        def enable():
            global _scheduled
            import addon_utils
            try:
                if not addon_utils.check("angelo_agent")[1]:
                    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
                from angelo_agent import live_windows, studio
                for window, area in live_windows.window_areas(bpy.context.window_manager):
                    if studio.is_studio(area):
                        studio.setup_area(window, area)
            finally:
                _scheduled = False
            return None
        bpy.app.timers.register(enable, first_interval=0.01)
    return {'FINISHED'}


def execute(context, is_interactive=False):
    banner(context)
    return {'CANCELLED'}


def autocomplete(context):
    return {'CANCELLED'}


def copy_as_script(context):
    return {'CANCELLED'}
