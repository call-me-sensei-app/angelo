# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Workers at once: the person's own choice of how many worker agents run at the same time, as a mix-in of the add-on's
preferences (``ui.ANGELO_Preferences``, as ``job_desktop_prefs``).

The maintainer (2026-10-25): people run other things on their computers, so they decide. 0 is Suggested: the count
follows what this computer fits (``parallel_capability``), so more memory or another computer just works. 1-16 is the
person's number, above the suggestion too (Settings then says what that may cost). ``parallel_access.worker_limit``
reads it; a forced launch (a benchmark arm, a test fixture) keeps its own count. It applies to the next start:
running workers are never stopped. The lead's start tool states the count this launch began with (``tool_specs``,
set once the preferences are readable; the maintainer: launch-time is fine). Shown and chosen on the AI Providers
page, in its Parallel edit section (``providers_page_model``).

The Sessions panel's place is the person's too (maintainer, 2026-10-25: "We can let users decide"): a column down the
left of the 3D view, or a strip centred across its top or bottom (``session_rail``), and minimized, a round button at
the top right of the view (``rail_minimized``). Both are kept with the preferences, as Workers at once is.
"""

from bpy.props import BoolProperty, EnumProperty, IntProperty

from .parallel_capability import MAX_WORKERS


class ParallelPreferences:
    parallel_workers: IntProperty(
        name="Workers at once", default=0, min=0, max=MAX_WORKERS,
        description="How many worker agents parallel edit may run at the same time on this computer. 0 follows what "
                    "this computer fits")
    rail_position: EnumProperty(
        name="Sessions panel", default="LEFT", translation_context="Angelo",   # Places, never Blender's shape words.
        items=(("LEFT", "Left", "A column down the left of the 3D view"),
               ("TOP", "Top", "A strip centred across the top of the 3D view"),
               ("BOTTOM", "Bottom", "A strip centred across the bottom of the 3D view")),
        description="Where the Sessions panel stands in the 3D view")
    rail_minimized: BoolProperty(
        name="Sessions panel minimized", default=False,
        description="The Sessions panel shows as a round button at the top right of the 3D view")


RAIL_POSITIONS = ("LEFT", "TOP", "BOTTOM")


def _preferences():
    """The add-on's preferences, or None (no Blender: host tests; a stand-in ``bpy`` without preferences; before the
    add-on registers). Never raises: the Sessions panel's draw reads it every frame."""
    try:
        import bpy
        addon = bpy.context.preferences.addons.get(__package__)
    except Exception:   # noqa: BLE001 - any failing read (a closing window, a freed context) is "unreadable".
        return None
    return getattr(addon, "preferences", None)


def rail_position():
    """Where the Sessions panel stands: "LEFT", "TOP" or "BOTTOM"; "LEFT" whenever the preferences are unreadable."""
    value = getattr(_preferences(), "rail_position", "LEFT")
    return value if value in RAIL_POSITIONS else "LEFT"


def rail_minimized():
    """Whether the person minimized the Sessions panel to its round button; False whenever unreadable."""
    return getattr(_preferences(), "rail_minimized", False) is True


def set_rail_position(value):
    """Keep ``value`` (one of ``RAIL_POSITIONS``) as the person's place for the panel; anything else changes nothing."""
    preferences = _preferences()
    if value in RAIL_POSITIONS and hasattr(preferences, "rail_position") and preferences.rail_position != value:
        preferences.rail_position = value
        _changed()


def set_rail_minimized(value):
    """Minimize the Sessions panel to its round button (True) or show it again (False), kept for the next launch."""
    preferences = _preferences()
    if hasattr(preferences, "rail_minimized") and preferences.rail_minimized != bool(value):
        preferences.rail_minimized = bool(value)
        _changed()


def _changed():
    """A value set from code is saved with the preferences as one set in Preferences is (Blender auto-saves changed
    preferences when it quits)."""
    try:
        import bpy
        bpy.context.preferences.is_dirty = True
    except (ImportError, AttributeError, RuntimeError, TypeError):
        pass
