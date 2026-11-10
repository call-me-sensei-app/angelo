# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's one set of design tokens: colour, type, spacing, radii and control sizes.

Every Angelo-drawn surface (cards, chat, settings, setup, nodes) takes its look from here, so the app has one
visual language instead of one palette per surface. Colours are named by role, never by where they are used: the
chat canvas's neutral greys and the cards' cool blue-greys are both roles here (``card_kit`` forwards the card names,
``chat_view`` its old names for modules not yet moved). A drawing module writes no colour of its own and keeps none
from import time (``test_native_ui_lints``): a new colour is a new role here. Sizes are in pixels at a UI scale of 1:
multiply by the region's scale when drawing.

**Light and dark.** Every colour role has a dark and a light value (``ROLES``). A canvas that follows the theme draws
inside ``themed()``: for that draw the module's colour names hold the variant the active Blender interface theme asks
for, and the dark values again afterwards, so a surface not yet made theme-aware keeps its look. The variant is
decided from the theme itself, never from a preset's name, so a custom or stock light theme works too
(``theme_variant``: the variant whose text reads better on the theme's Properties editor background). ``themed()``
reads that one colour once when the outermost draw starts and decides only when it changed; a token read is a plain
module attribute, never a theme lookup (``test_native_theme_tokens``). Read colours at draw time (``tk.TEXT``), never
at import, in a default argument or with ``from ui_tokens import``. Every text role meets WCAG contrast on each
backdrop it is drawn on, in both variants (``TEXT_ON``). Pictures and user content are never recoloured
(``untinted``).
"""

import contextlib

# Colour: role -> (dark, light), sRGB as gpu draws them (the design mockups' CSS values). The dark values are the
# 1.0.9 look (the chat's neutral greys merged in, ui_kit.rst); the light ones keep each role's job on a light ground.
ROLES = {
    # The chat canvas: neutral greys.
    "CANVAS": ((0.094, 0.094, 0.094, 1), (0.925, 0.925, 0.925, 1)),       # The chat's ground, under rows and cards.
    "RAISED": ((0.205, 0.205, 0.205, 1), (1, 1, 1, 1)),                   # A control on it: the composer, a button.
    "BUBBLE": ((0.185, 0.185, 0.185, 1), (0.860, 0.860, 0.860, 1)),       # The person's message, an attachment chip.
    "MENU": ((0.165, 0.165, 0.165, 1), (1, 1, 1, 1)),                     # A menu opened over it (models, +, chats).
    "HIGHLIGHT": ((0.230, 0.230, 0.230, 1), (0.860, 0.860, 0.860, 1)),    # A highlighted row or control (hover, open,
    #                                                                      the current one), a secondary button, and
    #                                                                      the canvas's rims and dividers.
    "WELL": ((0.125, 0.125, 0.125, 1), (0.945, 0.945, 0.945, 1)),         # A field or meter track sunk into a menu.
    "CANVAS_HOVER": ((0.135, 0.135, 0.135, 1), (0.885, 0.885, 0.885, 1)),  # A strip of the canvas under the pointer.
    "DISABLED_FILL": ((0.150, 0.150, 0.150, 1), (0.890, 0.890, 0.890, 1)),  # A button that cannot be pressed now.
    "CONTROL_OFF": ((0.290, 0.290, 0.290, 1), (0.720, 0.720, 0.720, 1)),  # An off toggle, a meter's empty part, Send
    #                                                                      with nothing to send.
    "TEAL_TINT": ((0.141, 0.188, 0.184, 1), (0.835, 0.925, 0.918, 1)),    # A chosen tool's chip: reads as an action.
    "SELECTION": ((0.27, 0.38, 0.57, 0.65), (0.55, 0.70, 0.95, 0.45)),    # Selected text in a field.
    # The cards (welcome, notices, Preferences pages, the chat's cards): cool blue-greys.
    "CARD": ((0.090, 0.098, 0.118, 1), (0.988, 0.988, 0.992, 1)),         # A card's ground (welcome_banner fades into
    #                                                                      the dark one).
    "PANEL": ((0.122, 0.133, 0.157, 1), (0.949, 0.953, 0.961, 1)),        # A raised block on a card, a card in the
    #                                                                      transcript (jobs, flows, approvals).
    "PANEL_HOVER": ((0.150, 0.163, 0.192, 1), (0.902, 0.910, 0.922, 1)),
    "POPOVER": ((0.110, 0.120, 0.142, 1), (1, 1, 1, 1)),                  # A dropdown's open list (ui_kit_select).
    "INSET": ((0.063, 0.071, 0.086, 1), (0.918, 0.925, 0.937, 1)),        # One step below: fields, tracks, code.
    "INSET_RAISED": ((0.082, 0.090, 0.110, 1), (0.878, 0.886, 0.902, 1)),  # An inset under the pointer, a code header.
    "DETAIL_FILL": ((0.071, 0.078, 0.094, 1), (0.937, 0.941, 0.949, 1)),  # Framed details and the Details well.
    "CONTROL_HOVER": ((0.250, 0.268, 0.302, 1), (0.722, 0.737, 0.761, 1)),  # An off toggle's track under the pointer.
    "LINE": ((0.200, 0.215, 0.245, 1), (0.808, 0.824, 0.847, 1)),         # Dividers and a control's border on a card.
    "LINE_HOVER": ((0.27, 0.29, 0.33, 1), (0.698, 0.718, 0.749, 1)),
    "LINE_PRESSED": ((0.15, 0.16, 0.19, 1), (0.871, 0.878, 0.894, 1)),
    # Text.
    "TEXT": ((0.950, 0.960, 0.970, 1), (0.110, 0.122, 0.141, 1)),
    "MUTED": ((0.620, 0.650, 0.690, 1), (0.318, 0.345, 0.384, 1)),        # Secondary text, a field's hint.
    "SUBTITLE": ((0.859, 0.890, 0.910, 1), (0.204, 0.224, 0.255, 1)),     # Secondary text one step stronger.
    "DISABLED_TEXT": ((0.50, 0.52, 0.55, 1), (0.459, 0.478, 0.506, 1)),   # A disabled label or hint (3:1).
    "INK": ((0.035, 0.055, 0.065, 1), (1, 1, 1, 1)),                      # Text on a teal, amber or coral fill.
    # Accents. Light keeps each hue and saturation and lowers only the lightness, until its text reads (TEXT_ON).
    "TEAL": ((0.169, 0.769, 0.749, 1), (0.089, 0.403, 0.393, 1)),         # The brand accent: the one primary action
    #                                                                      per surface, selection, progress, success.
    "TEAL_SOFT": ((0.169, 0.769, 0.749, 0.22), (0.089, 0.403, 0.393, 0.14)),
    "TEAL_HOVER": ((0.24, 0.85, 0.82, 1), (0.071, 0.359, 0.345, 1)),
    "TEAL_PRESSED": ((0.12, 0.60, 0.58, 1), (0.062, 0.308, 0.298, 1)),
    "AMBER": ((0.98, 0.72, 0.28, 1), (0.508, 0.324, 0.014, 1)),           # Needs you, and paid work; a warning.
    "AMBER_SOFT": ((0.98, 0.72, 0.28, 0.20), (0.508, 0.324, 0.014, 0.14)),
    "CORAL": ((0.941, 0.541, 0.424, 1), (0.669, 0.204, 0.068, 1)),        # Destructive actions and failures.
    "CORAL_SOFT": ((0.941, 0.541, 0.424, 0.20), (0.669, 0.204, 0.068, 0.12)),
    "CORAL_HOVER": ((0.976, 0.627, 0.522, 1), (0.628, 0.170, 0.032, 1)),
    "CORAL_PRESSED": ((0.784, 0.420, 0.322, 1), (0.455, 0.211, 0.145, 1)),
    "RUNNING": ((0.252, 0.52, 1.0, 1), (0.0, 0.352, 0.984, 1)),            # A job that is starting or running.
    "FOCUS": ((0.631, 0.957, 0.839, 1), (0.064, 0.546, 0.372, 1)),        # The keyboard focus ring (the console's
    #                                                                      mint), a primary control's hover ring.
    "LINK": ((0.631, 0.957, 0.839, 1), (0.055, 0.473, 0.322, 1)),         # A text link.
    "LINK_HOVER": ((0.902, 1.0, 0.937, 1), (0.0, 0.48, 0.171, 1)),
    "RECORDING": ((0.95, 0.3, 0.35, 1), (0.948, 0.268, 0.321, 1)),        # The microphone while it listens.
    "ACCENT_MARK": ((0.80, 0.89, 1, 1), (0.80, 0.89, 1, 1)),              # Marks on a teal fill (a slider's stops).
    # A chart's series in order (the usage meter: cache read is TEAL, then cache written, input, output).
    "CHART_B": ((0.435, 0.659, 0.651, 1), (0.339, 0.561, 0.553, 1)),
    "CHART_C": ((0.55, 0.55, 0.55, 1), (0.53, 0.53, 0.53, 1)),
    "CHART_D": ((0.85, 0.85, 0.85, 1), (0.25, 0.25, 0.25, 1)),
    # Layers over a surface: light on the dark variant, dark on the light one.
    "VEIL": ((1, 1, 1, 1), (0, 0, 0, 1)),                                 # veil(alpha)'s colour: hover layers.
    "PRESSED_LAYER": ((0, 0, 0, 0.24), (0, 0, 0, 0.14)),                  # A control while it is pressed.
    "INVERSE_HOVER": ((0, 0, 0, 0.10), (1, 1, 1, 0.16)),                  # Hover over a fill that stands out from
    #                                                                      the canvas (light on dark, dark on light).
    "EDGE": ((1, 1, 1, 0.09), (0, 0, 0, 0.10)),                           # A card's 1 px rim.
    "HAIRLINE": ((1, 1, 1, 0.12), (0, 0, 0, 0.14)),                       # A control's outline.
    "HAIRLINE_HOVER": ((1, 1, 1, 0.22), (0, 0, 0, 0.26)),
    "HAIRLINE_FAINT": ((1, 1, 1, 0.10), (0, 0, 0, 0.12)),                 # A toggle's rim.
    "NEUTRAL_SOFT": ((1, 1, 1, 0.08), (0, 0, 0, 0.06)),                   # A neutral chip's fill; a group's thread.
    "SCROLL_TRACK": ((1, 1, 1, 0.06), (0, 0, 0, 0.06)),                   # A scroll section's track and thumb.
    "SCROLL_THUMB": ((1, 1, 1, 0.28), (0, 0, 0, 0.30)),
    # The same in both: each carries its own backdrop.
    "SCRIM": ((0.020, 0.024, 0.030, 0.66), (0.020, 0.024, 0.030, 0.66)),  # Behind a modal card.
    "TIP_FILL": ((0.055, 0.047, 0.043, 0.97), (0.055, 0.047, 0.043, 0.97)),  # Tooltips.
    "TIP_LINE": ((0.953, 0.925, 0.886, 0.14), (0.953, 0.925, 0.886, 0.14)),
    "TIP_TEXT": ((0.953, 0.925, 0.886, 1), (0.953, 0.925, 0.886, 1)),     # Also text on a picture's dark band.
    "PICTURE_ACCENT": ((0.631, 0.957, 0.839, 1), (0.631, 0.957, 0.839, 1)),  # The mint on a dark band the same in
    #                                                                      both (a picture's caption, a TIP_FILL chip).
    "SWATCH_EDGE": ((0, 0, 0, 0.45), (0, 0, 0, 0.45)),                    # Every colour swatch's rim.
    # Text drawn straight into Blender's own editors (ui_kit_overlay) sits on this backdrop whatever the editor's
    # theme: dark enough to stand out (3:1) from a light editor and to carry both tones at 4.5:1 over any editor
    # (test_native_overlay_label).
    "OVERLAY_FILL": ((0.055, 0.060, 0.072, 0.96), (0.055, 0.060, 0.072, 0.96)),
    "OVERLAY_TEXT": ((0.950, 0.960, 0.970, 1), (0.950, 0.960, 0.970, 1)),
    "OVERLAY_MUTED": ((0.620, 0.650, 0.690, 1), (0.620, 0.650, 0.690, 1)),
}
# Exact duplicates: one value, two names a reader understands.
ALIASES = {"SUCCESS": "TEAL",           # A done or verified state is the brand teal, not a generic green.
           "WARNING": "AMBER", "DANGER": "CORAL",
           "GUIDE": "NEUTRAL_SOFT",     # The thread that ties an open group's steps to its header.
           "CHART_A": "TEAL"}

# Every text and glyph role and the backdrops the canvases draw it on: ``"BASE"`` or ``"LAYER/BASE"`` (a translucent
# layer over a base). Text reads at 4.5:1 (WCAG AA, ``TEXT_ON``); a glyph (an icon, a ring, a chart's series) and a
# disabled label at 3:1 (``GLYPH_ON``); in both variants (test_native_theme_tokens). The slider's stops on its teal
# fill (ACCENT_MARK) are decoration beside the thumb and its named level, and text on a picture's shaded band
# (TIP_TEXT, PICTURE_ACCENT on the connect showcase) depends on the picture, so they are in neither.
BODY, GLYPH = 4.5, 3.0
_CANVAS = ("CANVAS", "RAISED", "BUBBLE", "MENU", "HIGHLIGHT", "WELL", "CANVAS_HOVER")
_CARDS = ("CARD", "PANEL", "PANEL_HOVER", "POPOVER", "INSET", "INSET_RAISED", "DETAIL_FILL")
TEXT_ON = {
    "TEXT": _CANVAS + _CARDS + ("LINE", "TEAL_TINT", "SELECTION/RAISED", "NEUTRAL_SOFT/CANVAS", "TEAL_SOFT/CANVAS",
                                "TEAL_SOFT/MENU", "TEAL_SOFT/PANEL", "TEAL_SOFT/CARD", "AMBER_SOFT/MENU",
                                "CORAL_SOFT/MENU", "AMBER_SOFT/PANEL"),
    "MUTED": _CANVAS + _CARDS + ("DISABLED_FILL", "NEUTRAL_SOFT/CANVAS", "NEUTRAL_SOFT/PANEL"),
    "SUBTITLE": ("CANVAS", "CARD", "PANEL", "DETAIL_FILL", "INSET", "MENU", "CORAL_SOFT/MENU"),
    "TEAL": ("CANVAS", "RAISED", "MENU", "HIGHLIGHT", "CARD", "PANEL", "PANEL_HOVER", "POPOVER", "TEAL_TINT",
             "TEAL_SOFT/CANVAS", "TEAL_SOFT/CARD", "TEAL_SOFT/PANEL"),
    "AMBER": ("CANVAS", "RAISED", "BUBBLE", "MENU", "CANVAS_HOVER", "CARD", "PANEL", "POPOVER", "AMBER_SOFT/CANVAS",
              "AMBER_SOFT/PANEL"),
    "CORAL": ("CANVAS", "RAISED", "MENU", "CARD", "PANEL", "POPOVER", "CORAL_SOFT/CANVAS", "CORAL_SOFT/PANEL"),
    "RUNNING": ("PANEL", "CANVAS", "CARD"),
    "LINK": ("CANVAS", "RAISED", "MENU", "CARD", "PANEL"),
    "LINK_HOVER": ("CANVAS", "RAISED", "MENU", "CARD", "PANEL"),
    "INK": ("TEAL", "TEAL_HOVER", "TEAL_PRESSED", "AMBER", "CORAL", "CORAL_HOVER"),
    "CANVAS": ("TEXT",),                             # A label or glyph on a TEXT fill (Send, a primary button).
    "TIP_TEXT": ("TIP_FILL/CANVAS", "TIP_FILL/CARD", "SCRIM/CANVAS", "SCRIM/CARD"),   # SCRIM: a progress pill.
    "PICTURE_ACCENT": ("TIP_FILL/CANVAS", "TIP_FILL/CARD", "SCRIM/CANVAS", "SCRIM/CARD"),
    "OVERLAY_TEXT": ("OVERLAY_FILL/CANVAS",),
    "OVERLAY_MUTED": ("OVERLAY_FILL/CANVAS",),
}
GLYPH_ON = {
    "TEXT": ("CONTROL_OFF",),                        # An off toggle's knob.
    "MUTED": ("CONTROL_OFF",),                       # Send's arrow with nothing to send.
    "TEAL": ("TEAL_SOFT/RAISED",),                   # A teal chip's mark.
    "CORAL": ("CORAL_SOFT/MENU", "HIGHLIGHT"),       # A lit delete button's bin.
    "AMBER": ("AMBER_SOFT/MENU",),
    "DISABLED_TEXT": ("DISABLED_FILL", "RAISED", "PANEL", "PANEL_HOVER"),
    "FOCUS": ("CANVAS", "RAISED", "MENU", "HIGHLIGHT", "CARD", "PANEL"),
    "RECORDING": ("RAISED",),
    "CHART_B": ("MENU", "WELL"),
    "CHART_C": ("MENU", "WELL"),
    "CHART_D": ("MENU", "WELL"),
}

DARK = {name: dark for name, (dark, _light) in ROLES.items()}
LIGHT = {name: light for name, (_dark, light) in ROLES.items()}
for _alias, _role in ALIASES.items():
    DARK[_alias], LIGHT[_alias] = DARK[_role], LIGHT[_role]
VARIANTS = {"dark": DARK, "light": LIGHT}
COLOURS = frozenset(DARK)
globals().update(DARK)   # Outside themed(): the dark values.

_theme = {"variant": "dark", "back": None, "reads": 0}   # The theme last read and the variant it asks for.
_applied = {"variant": "dark", "depth": 0}               # What the module's colour names hold now.


def luminance(color):
    """WCAG 2 relative luminance of an sRGB colour (channels 0-1)."""
    def linear(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    return 0.2126 * linear(color[0]) + 0.7152 * linear(color[1]) + 0.0722 * linear(color[2])


def contrast(first, second):
    """WCAG 2 contrast ratio of two opaque sRGB colours: 1 (none) to 21 (black on white)."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def over(color, base):
    """``color`` (with its alpha) drawn over the opaque ``base``: the opaque colour the eye sees."""
    alpha_ = color[3] if len(color) > 3 else 1
    return tuple(top * alpha_ + below * (1 - alpha_) for top, below in zip(color[:3], base[:3])) + (1,)


def theme_variant(back):
    """``"light"`` or ``"dark"`` for a theme whose main background is ``back`` (sRGB): the variant whose text has the
    higher contrast on it, so a mid grey goes the way its own text reads better."""
    return "light" if contrast(LIGHT["TEXT"], back) > contrast(DARK["TEXT"], back) else "dark"


def _theme_back():
    """The active theme's main background: the Properties editor's, where Blender's own panels sit. None when no
    theme can be read (no Blender, or while it starts)."""
    _theme["reads"] += 1
    try:
        import bpy
        back = tuple(round(float(v), 4) for v in bpy.context.preferences.themes[0].properties.space.back[:3])
    except (AttributeError, ImportError, IndexError, TypeError, ValueError):
        return None
    return back if len(back) == 3 else None   # A stand-in theme with no colour decides nothing.


def follow_theme():
    """Read the active theme (one colour) and decide the variant when that colour changed; True when the variant
    changed. ``themed()`` calls it once per outermost draw."""
    back = _theme_back()
    if back is None or back == _theme["back"]:
        return False
    _theme["back"] = back
    chosen = theme_variant(back)
    changed = chosen != _theme["variant"]
    _theme["variant"] = chosen
    return changed


def variant():
    """The variant the active theme asks for (``"dark"`` or ``"light"``), as last read."""
    return _theme["variant"]


def _apply(name):
    globals().update(VARIANTS[name])
    _applied["variant"] = name


@contextlib.contextmanager
def themed(name=None):
    """Draw inside this to follow the theme. The outermost ``themed`` reads the theme once (``follow_theme``), then
    the colour names hold that variant's values (or ``name``'s) until it ends, and the dark ones again after. Nested
    ones change nothing and read nothing."""
    outermost = _applied["depth"] == 0
    if outermost:
        if name is None:
            follow_theme()
        wanted = name or _theme["variant"]
        if wanted != _applied["variant"]:
            _apply(wanted)
    _applied["depth"] += 1
    try:
        yield _applied["variant"]
    finally:
        _applied["depth"] -= 1
        if outermost and _applied["variant"] != "dark":
            _apply("dark")


def forward(names, module):
    """A module ``__getattr__`` that reads each old colour name in ``names`` (old name -> role) from here as it is
    read, so a module that still names it draws the variant ``themed()`` holds."""
    def __getattr__(name):
        if name in names:
            return globals()[names[name]]
        raise AttributeError("module %r has no attribute %r" % (module, name))
    return __getattr__


def veil(alpha):
    """The layer over a surface at ``alpha``: light over the dark variant, dark over the light one (hover and press
    layers, scrollbars)."""
    return globals()["VEIL"][:3] + (alpha,)


def shade(alpha):
    """A shadow at ``alpha``: the soft layers under a card, a popover or a pill, and a darkening hover over a light
    fill."""
    return (0, 0, 0, alpha)


def alpha(color, value):
    """``color`` at alpha ``value`` (a fade, a tinted fill)."""
    return tuple(color[:3]) + (value,)


def untinted(value=1.0):
    """The tint that draws a picture as it is, at alpha ``value``: pictures are never recoloured."""
    return (1, 1, 1, value)


# Type scale (pixel sizes; one weight, because blf draws a single weight).
TYPE_DISPLAY = 28    # Page heroes.
TYPE_TITLE = 21      # Card and dialog titles (card_kit.Builder.heading).
TYPE_HEADING = 16    # Section headings.
TYPE_BODY = 13.5     # Body text, buttons, rows.
TYPE_SMALL = 12      # Secondary lines, chips, captions.
TYPE_MICRO = 11      # Badges and dense metadata.
TYPE_MONO = 11.5     # Code, ids and keys.
LEADING_BODY = 1.5
LEADING_TITLE = 1.3

# Spacing (a 4 px grid).
SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_5, SPACE_6, SPACE_8 = 4, 8, 12, 16, 20, 24, 32
CARD_PADDING = 26    # card_kit.Builder's default.

# Radii.
RADIUS_CARD = 16
RADIUS_PANEL = 12
RADIUS_CONTROL = 8
RADIUS_CHIP = 999    # Pills: draw with radius = height / 2.

# Control sizes.
BUTTON_HEIGHT = 36
BUTTON_HEIGHT_SMALL = 28
FIELD_HEIGHT = 36
SEGMENTED_HEIGHT = 34
ROW_HEIGHT = 44
CHIP_HEIGHT = 22
TOGGLE_SIZE = (36, 20)
ICON_SIZE = 16
FOCUS_RING = 2

# Motion (seconds).
MOTION_FAST = 0.12
MOTION_BASE = 0.2

__all__ = [name for name in dir() if name.isupper()]
