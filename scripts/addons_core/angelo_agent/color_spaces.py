# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Colour spaces and views as the active OCIO configuration names them.

Blender 5.3 lets a Project switch the configuration at runtime, to ACES 1.3 or 2.0 among others (``ocio://``
URIs), and reopens a project on start-up. Those configurations have no colour space called "Non-Color" or "sRGB"
and no view called "Standard", so writing such a name raises and comparing with one misjudges. Every write and
comparison in the add-on goes through here instead (test_native_color_spaces.py refuses names elsewhere):

* ``mark_stored``: the stored values, drawn or read as they are, through the configuration's data role.
* ``mark_srgb``: sRGB-encoded colour, found as Blender finds it (``IMB_colormanagement_srgb_colorspace_name_get``):
  the interop IDs, then the common names of configurations without them, then the default byte role.
* ``view_untonemapped``: a view that shows display values as they are ("Standard", ACES's "Un-tone-mapped",
  "Raw").
* ``kind`` and ``fact_kind``: ``srgb``, ``data`` or ``other``, for rules that judge a colour space. A display is
  judged by ``web_tone.srgb_display``.

No ``bpy`` at import: the functions take Blender's settings objects.
"""

SRGB_INTEROP_IDS = ("srgb_rec709_scene", "srgb_rec709_display")
# Blender's own list for configurations without interop IDs (colormanagement.cc), in its order.
SRGB_NAMES = ("sRGB Encoded Rec.709 (sRGB)", "Utility - sRGB - Texture", "sRGB - Texture", "sRGB")
_OLDER_SRGB_NAMES = ("sRGB IEC61966-2.1",)   # Recognized as sRGB, never chosen.
UNTONEMAPPED_VIEWS = ("Standard", "Un-tone-mapped", "Raw")
# Facts recorded before kinds existed carry only the name, in Blender's own configuration.
_LEGACY_KINDS = {"sRGB": "srgb", "Non-Color": "data"}


def _first_accepted(settings, prop, candidates):
    """Set ``prop`` to the first of ``candidates`` the configuration has; whether one was. These enums are built by
    the configuration at runtime, so their item list is not readable outside a context: the setting itself answers."""
    for value in candidates:
        try:
            setattr(settings, prop, value)
            return True
        except (TypeError, ValueError):
            continue
    return False


def mark_stored(settings):
    """The configuration's data colour space: values are stored and drawn as they are."""
    settings.is_data = True


def _default_byte():
    """The default byte role's colour space: what a new 8-bit image gets."""
    import bpy
    image = bpy.data.images.new("Angelo colour space probe", 1, 1)
    try:
        return image.colorspace_settings.name
    finally:
        bpy.data.images.remove(image)


def mark_srgb(settings, default_byte=_default_byte):
    """The configuration's sRGB-encoded colour space, as Blender finds it."""
    if hasattr(settings, "interop_id") and _first_accepted(settings, "interop_id", SRGB_INTEROP_IDS):
        return
    if not _first_accepted(settings, "name", SRGB_NAMES):
        settings.name = default_byte()


def kind(settings):
    """``data``, ``srgb`` or ``other``."""
    if getattr(settings, "is_data", False):
        return "data"
    name = getattr(settings, "name", "")
    if getattr(settings, "interop_id", "NONE") in SRGB_INTEROP_IDS or name in SRGB_NAMES + _OLDER_SRGB_NAMES:
        return "srgb"
    return "other"


def fact_kind(fact):
    """The kind a recorded image fact names: its ``colorspace_kind``, else its colour space's name in Blender's own
    configuration (facts recorded before kinds existed)."""
    return fact.get("colorspace_kind") or _LEGACY_KINDS.get(fact.get("colorspace"), "other")


def view_untonemapped(view_settings):
    """Set a view that shows display values as they are; False (view unchanged) when the configuration has none."""
    return _first_accepted(view_settings, "view_transform", UNTONEMAPPED_VIEWS)
