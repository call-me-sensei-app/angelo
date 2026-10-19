# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's one set of design tokens: colour, type, spacing, radii and control sizes.

Every Angelo-drawn surface (cards, chat, settings, setup, nodes) takes its look from here, so the app has one
visual language instead of one palette per surface. The colours are the welcome and notice cards' palette
(``card_kit``), which stays the source for the values it already defines; this module adds what the other
surfaces need. Sizes are in pixels at a UI scale of 1: multiply by the region's scale when drawing.
"""

from .card_kit import (AMBER, AMBER_SOFT, CARD, EDGE, FOCUS, INK, LINE, LINE_HOVER, LINE_PRESSED, MUTED, PANEL,
                       PANEL_HOVER, SCRIM, TEAL, TEAL_HOVER, TEAL_PRESSED, TEAL_SOFT, TEXT)

# Colour (linear-ish RGBA as drawn by gpu; the same values as the design mockups' CSS).
SUBTITLE = (0.859, 0.890, 0.910, 1)       # Secondary text one step brighter than MUTED.
CORAL = (0.941, 0.541, 0.424, 1)          # Destructive actions and failures (the raven's beak).
CORAL_SOFT = (0.941, 0.541, 0.424, 0.20)
CORAL_HOVER = (0.976, 0.627, 0.522, 1)    # Destructive button states (card_draw "destructive").
CORAL_PRESSED = (0.784, 0.420, 0.322, 1)
TIP_FILL = (0.055, 0.047, 0.043, 0.97)    # Tooltips.
TIP_LINE = (0.953, 0.925, 0.886, 0.14)
TIP_TEXT = (0.953, 0.925, 0.886, 1)
SUCCESS = TEAL                            # A done or verified state is the brand teal, not a generic green.
WARNING = AMBER                           # Needs attention: approvals, expiring sign-ins, policy locks.
DANGER = CORAL

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
