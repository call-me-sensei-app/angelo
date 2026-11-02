# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In the background Angelo processes Angelo starts, render engine add-ons never load (``angelo_lead_only_addons``).

A startup module because Blender registers startup modules before it enables add-ons (``bpy.utils.load_scripts``,
then ``load_scripts_extensions``), so the wrap is in place before the first add-on loads. Anywhere else (Angelo's
window, a person's own ``--background`` run) it does nothing.
"""


def register():
    import angelo_lead_only_addons
    if angelo_lead_only_addons.applies():
        angelo_lead_only_addons.install()


def unregister():
    pass
