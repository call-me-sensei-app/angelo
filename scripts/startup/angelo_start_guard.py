# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A start that never completed makes the next launch enable only the add-ons that ship with Angelo
(``angelo_safe_start``; doc/angelo/app_identity.rst, "A start that did not finish").

A startup module because Blender registers startup modules before it enables add-ons (``bpy.utils.load_scripts``,
then ``load_scripts_extensions``): the add-ons are left off, and this start's record is held, before the first one
loads. Blender unregisters startup modules on exit (``bpy.utils._on_exit``), which ends the record of a start that
quit before completing.
"""


def register():
    import bpy
    import angelo_safe_start
    if angelo_safe_start.applies(bpy.app.background, bpy.app.factory_startup):
        angelo_safe_start.start(bpy)


def unregister():
    import angelo_safe_start
    angelo_safe_start.stop(exiting=True)
