# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a capture shows drawn content or one flat colour (1.0.5).

35f5b544698's final line captured Media Studio's own window as a single flat colour in every studio_palette and
welcome_slides shot, and welcome_slides passed anyway because it compared only the main window. A capture of an
editor that drew its content has well over 2% of its pixels away from the frame's median colour; a blank frame has
none. numpy only: it runs in the app (GUI suites) and on the host.
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
