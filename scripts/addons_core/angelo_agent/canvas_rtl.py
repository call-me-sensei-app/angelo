# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Right-to-left layout for Angelo's own canvases (the chat, its cards, the welcome screen, the showcase).

A canvas lays everything out in ordinary left-to-right pixels. In a right-to-left
interface language (Arabic) its draw runs between ``begin(width)`` and ``end()``,
which put a mirror on the GPU matrix: every shape, stroke, ring and icon lands
mirrored across the region with no change to the layout code. What must not be
mirrored is drawn *upright*: text (glyphs read left to right; ``upright`` places
the string so it ends where it began in the layout, which right-aligns it) and
pictures (thumbnails, previews, the banner). Scissor rectangles are window
pixels, so ``scissor`` mirrors them by hand, and input mirrors the pointer's x
back (``pointer_x``) before hit-testing, so a button is where it is drawn.
``unmirrored`` draws a block as laid out: the composer's text field (typed text
stays left to right, like every Blender text field) and the welcome screen's
diagrams of Blender's real, unmirrored screen.

Text needs more than a mirror: Blender draws one glyph per character left to
right, so Arabic must be shaped and in visual order (``rtl_text``). ``visual``
does that for raw Arabic (typed by a person or written by a model) in any
interface language and leaves pre-shaped catalogue text alone. No ``bpy`` at
import; ``gpu`` only while a mirrored draw is active.
"""

import re
from contextlib import contextmanager

from . import rtl_text, ui_text

# Hebrew, Arabic, Syriac, Thaana, NKo ... and the Arabic presentation forms: one C-level scan per string.
_RTL = re.compile("[֐-ࣿיִ-﷿ﹰ-ﻼ]")
_width = None  # Region width while a mirrored draw is active, else None.


def begin(width):
    """Start a region's draw: mirrored only when the interface language is right to left."""
    global _width
    _width = None
    if ui_text.is_rtl():
        import gpu
        gpu.matrix.push()
        _mirror(width)
        _width = width


def end():
    """End the draw ``begin`` started (always pair them with ``try``/``finally``)."""
    global _width
    if _width is not None:
        import gpu
        gpu.matrix.pop()
    _width = None


def _mirror(width):
    import gpu
    gpu.matrix.translate((width, 0))
    gpu.matrix.scale((-1, 1))


def mirrored():
    return _width is not None


@contextmanager
def unmirrored():
    """Draw the block in plain left-to-right region pixels, even inside a mirrored draw."""
    global _width
    width = _width
    if width is None:
        yield
        return
    import gpu
    gpu.matrix.push()
    _mirror(width)  # Mirroring twice is the identity.
    _width = None
    try:
        yield
    finally:
        _width = width
        gpu.matrix.pop()


@contextmanager
def upright(x, extent):
    """Draw something that must read left to right (text, a picture) whose layout box is ``x`` .. ``x + extent``.

    Yields the x to draw it at: ``x`` itself, or in a mirrored draw the mirrored box's left edge, unmirrored.
    """
    width = _width
    if width is None:
        yield x
        return
    with unmirrored():
        yield width - x - extent


def scissor(x, y, width, height):
    """A scissor rectangle for ``gpu.state.scissor_set``, mirrored like the drawing around it."""
    if _width is None:
        return int(x), int(y), max(1, int(width)), max(1, int(height))
    return int(_width - x - width), int(y), max(1, int(width)), max(1, int(height))


def pointer_x(x, width):
    """A pointer x in a region ``width`` wide, as the left-to-right layout sees it."""
    return width - x if ui_text.is_rtl() else x


def has_rtl(value):
    return bool(_RTL.search(value))


def visual(value):
    """``value`` ready to hand to ``blf``: raw Arabic is shaped and put in visual order; anything else is unchanged."""
    if not _RTL.search(value) or rtl_text.is_visual(value):
        return value
    return rtl_text.to_visual(value)


def elide(value, measure, width):
    """``value`` cut to fit ``width`` with "…" where it was cut: at the end of the *reading* order, so a
    right-to-left string loses its last words (on the left) rather than its first. ``measure(text)`` is a width."""
    if measure(value) <= width:
        return value
    rtl = bool(_RTL.search(value))
    logical = rtl_text.to_logical(value) if rtl and rtl_text.is_visual(value) else value
    drawn = rtl_text.to_visual if rtl else str
    low, high = 1, max(1, len(logical) - 1)  # The longest kept prefix whose cut form fits; at least one character.
    while low < high:
        middle = (low + high + 1) // 2
        if measure(drawn(logical[:middle] + "…")) <= width:
            low = middle
        else:
            high = middle - 1
    return drawn(logical[:low] + "…")


def rows(value, measure, width):
    """Rows of ``value`` wrapped in reading order for ``width``; each row is visual (``blf`` ready)."""
    return rtl_text.rows_to_visual(value, measure, width)
