# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the live view's label stands: the pill ``live_view_draw`` draws centred under the headers over the 3D view
while a worker's live view shows ("Live view · Worker 2 · …"). One place for its geometry, so what clears it (a
Sessions strip across the top, ``session_rail_strip``) reads the same numbers it is drawn with. Pixels at a UI scale
of 1, times ``scale``; region pixels, y up (pure).
"""

HEIGHT = 26   # The pill's height.
BELOW = 12    # From the lowest header over the view down to the pill's top.


def rect(region_width, top, width, scale):
    """``(x, y, w, h)`` of a pill ``width`` pixels wide, centred in a region ``region_width`` wide, under ``top`` (the
    region pixel row where the headers over the view end)."""
    height = HEIGHT * scale
    return (region_width - width) / 2, top - height - BELOW * scale, width, height


def room(scale):
    """Pixels from the headers' foot down to the pill's foot: what a strip at the top keeps clear below the headers."""
    return (BELOW + HEIGHT) * scale
