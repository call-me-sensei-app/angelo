# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The two preferences of ``job_desktop``, as a mix-in of the add-on's preferences (``ui.ANGELO_Preferences``).

Blender registers the properties of a mix-in base with the class that uses it
(``bpy_rna.cc``, ``pyrna_deferred_register_class_recursive``), so they are saved
with the add-on's preferences like any other. They are shown and switched on the
AI Providers page, in its Background jobs section (``providers_page_model``),
whose switches toggle a preference by name.
"""

from bpy.props import BoolProperty


class JobDesktopPreferences:
    """Background jobs outside the window: both on by default, for renders and bakes left running."""
    job_keep_awake: BoolProperty(
        name="Keep the computer awake during background jobs", default=True,
        description="While a render, bake or export runs, stop the computer from sleeping when idle. The display "
                    "may still turn off; closing a laptop's lid still puts it to sleep")
    job_notifications: BoolProperty(
        name="Notify when a long background job ends", default=True,
        description="When a render, bake or export that ran for a minute or more finishes or fails, show a system "
                    "notification. Jobs you stop yourself are not announced")

