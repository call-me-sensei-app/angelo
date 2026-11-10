# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The camera preview's own settings, kept with the add-on's preferences (``ui.ANGELO_Preferences``, as
``parallel_setting``): whether it shows, its corner, how far in from that corner it stands and how wide it is.
They are the person's for every file, never a .blend's: the preview is a way of looking, not part of the scene.

Shown by default only if its measured cost in normal use is below noise (doc/angelo/camera_preview.rst, "Default";
``DEFAULT_SHOWN``). Reads never raise: the 3D view's draw reads them every frame.
"""

from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty

from . import camera_preview_layout as layout

DEFAULT_SHOWN = False   # Decided by the measurements in doc/angelo/camera_preview.rst ("Default").


class CameraPreviewPreferences:
    camera_preview_shown: BoolProperty(
        name="Camera preview", default=DEFAULT_SHOWN,
        description="Show a small live picture of the scene camera's view in a corner of the 3D view")
    camera_preview_corner: EnumProperty(
        name="Camera preview corner", default=layout.DEFAULT_CORNER, translation_context="Angelo",
        items=(("BOTTOM_LEFT", "Bottom left", ""), ("BOTTOM_RIGHT", "Bottom right", ""),
               ("TOP_LEFT", "Top left", ""), ("TOP_RIGHT", "Top right", "")),
        description="Which corner of the 3D view the camera preview stands in")
    camera_preview_offset: FloatVectorProperty(
        name="Camera preview offset", size=2, default=(0.0, 0.0), min=0.0, max=10000.0,
        description="How far in from its corner the camera preview stands, in points")
    camera_preview_width: FloatProperty(
        name="Camera preview width", default=layout.DEFAULT_WIDTH, min=layout.MIN_WIDTH, max=layout.MAX_WIDTH,
        description="The camera preview's width, in points")


def _preferences():
    try:
        import bpy
        addon = bpy.context.preferences.addons.get(__package__)
    except Exception:   # noqa: BLE001 - any failing read (a closing window, a freed context) is "unreadable".
        return None
    return getattr(addon, "preferences", None)


def shown():
    """Whether the person wants the preview; ``DEFAULT_SHOWN`` whenever the preferences are unreadable."""
    value = getattr(_preferences(), "camera_preview_shown", DEFAULT_SHOWN)
    return value is True


def placement():
    """``(corner, (dx, dy) points, width points)`` the person left the card at."""
    preferences = _preferences()
    corner = getattr(preferences, "camera_preview_corner", layout.DEFAULT_CORNER)
    try:
        offset = tuple(float(v) for v in getattr(preferences, "camera_preview_offset", (0.0, 0.0)))[:2]
        width = float(getattr(preferences, "camera_preview_width", layout.DEFAULT_WIDTH))
    except (TypeError, ValueError):
        offset, width = (0.0, 0.0), float(layout.DEFAULT_WIDTH)
    if corner not in layout.CORNERS:
        corner = layout.DEFAULT_CORNER
    return corner, offset if len(offset) == 2 else (0.0, 0.0), width


def set_shown(value):
    preferences = _preferences()
    if hasattr(preferences, "camera_preview_shown") and preferences.camera_preview_shown != bool(value):
        preferences.camera_preview_shown = bool(value)
        _changed()


def set_placement(corner=None, offset=None, width=None):
    preferences = _preferences()
    if preferences is None or not hasattr(preferences, "camera_preview_corner"):
        return
    if corner in layout.CORNERS and preferences.camera_preview_corner != corner:
        preferences.camera_preview_corner = corner
    if offset is not None:
        preferences.camera_preview_offset = (max(0.0, offset[0]), max(0.0, offset[1]))
    if width is not None:
        preferences.camera_preview_width = max(layout.MIN_WIDTH, min(layout.MAX_WIDTH, width))
    _changed()


def _changed():
    """Saved with the preferences, as a value set in Preferences is (Blender auto-saves changed preferences)."""
    try:
        import bpy
        bpy.context.preferences.is_dirty = True
    except (ImportError, AttributeError, RuntimeError, TypeError):
        pass
