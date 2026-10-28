# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a capture shows drawn content or one flat colour (1.0.5).

35f5b544698's final line captured Media Studio's own window as a single flat colour in every studio_palette and
welcome_slides shot, and welcome_slides passed anyway because it compared only the main window. A capture of an
editor that drew its content has well over 2% of its pixels away from the frame's median colour; a blank frame has
none. numpy only: it runs in the app (GUI suites) and on the host. ``redraw`` and ``drawn_in`` give a crop the
rectangle its row has in the frame being read.
"""

DRAWN_SHARE = 0.02   # More than this share of pixels differs from the median colour: something was drawn.
TOLERANCE = 8        # Per channel, 0-255: anti-aliasing noise in a flat fill stays under it.


def content_share(px):
    """Share of pixels whose colour differs from the frame's median colour by more than ``TOLERANCE`` in any channel.
    ``px`` is an (height, width, 3) array of 0-255 integers."""
    import numpy
    flat = numpy.asarray(px).reshape(-1, 3).astype(numpy.int32)
    if not len(flat):
        return 0.0
    median = numpy.median(flat, axis=0)
    return float((numpy.abs(flat - median) > TOLERANCE).any(axis=1).mean())


def drawn(px):
    return content_share(px) > DRAWN_SHARE


def redraw(window):
    """Every region of ``window`` drawn again now, so a suite's draw probe records the frame ``live_view.window_pixels``
    reads next (it blits each region's last drawn buffer, drawing none); returns when the redraw began."""
    import time
    import bpy   # Here, so the rest of this module needs no Blender (host tests).
    since = time.monotonic()
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN', iterations=1)
    return since


def drawn_in(records, key, since, field="key"):
    """The newest of ``records`` (a draw probe's dicts with ``at``, oldest first) whose ``field`` is ``key``, drawn at
    or after ``since``, else None (``field="title"``: a row matched by what it says, which survives a message rebuilt
    under a new identity). A crop takes its rectangle from the frame its pixels come from: a row's first drawn
    place moves once the rows above it settle (an answered card goes, a turn's group closes), so a rectangle kept from
    an earlier frame crops whatever moved in (1.0.6 on 7ea90a2ee3c: the stale refusal row's crop held the turn's
    "Done." below it, while the window showed the row)."""
    return next((record for record in reversed(records) if record.get(field) == key and record["at"] >= since), None)
