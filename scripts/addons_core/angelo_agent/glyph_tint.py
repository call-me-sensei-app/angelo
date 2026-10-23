# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The colour Angelo's own glyphs take on Blender's widgets: the theme's text colour for that widget (1.0.5).

Chat's bubble, Studio's frame-and-spark and Flows' three nodes are drawn as icon pixels. They were baked near-white,
which vanished on Angelo Light's light buttons (1.0.5 review, F's run 5). Each glyph is now baked in the text colour
of the widget it sits on (a top-bar tool button, pressed or not; a menu item), read from the theme when drawn, so it
reads like the label beside it in every theme. Pure besides the theme read; the bakers cache one icon per colour.
"""

DEFAULT = (0.93, 0.95, 0.94)   # A theme that cannot be read: the dark top bar's near-white.


def colour(widget="tool", selected=False):
    """``widget``'s text colour in the active theme (``wcol_<widget>``; ``text_sel`` while pressed), rounded so a
    theme's colour bakes one icon."""
    try:
        import bpy
        theme = bpy.context.preferences.themes[0].user_interface
        state = getattr(theme, "wcol_" + widget)
        value = state.text_sel if selected else state.text
        return tuple(round(float(v), 2) for v in value[:3])
    except (AttributeError, IndexError, ImportError, TypeError):
        return DEFAULT


def key(base, rgb):
    """The preview name of ``base`` baked in ``rgb``."""
    return "%s-%.2f-%.2f-%.2f" % ((base,) + tuple(rgb))
