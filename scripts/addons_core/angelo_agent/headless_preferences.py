# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's preferences in a worker Blender stay read-only (``worker_main``): nothing there saves them."""


def protect(bpy):
    """Turn preference saving off in memory, first thing a worker's own code does. Belt and braces: Blender never
    saves preferences when a background process exits, and any explicit save lands in the worker's private copy."""
    bpy.context.preferences.use_preferences_save = False
    bpy.app.use_userpref_skip_save_on_exit = True
