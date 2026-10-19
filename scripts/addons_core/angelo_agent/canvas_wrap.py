# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measured native font rows, resumable between rows for large review layouts."""

import blf

from . import canvas_rtl, text_layout

def iter_wrap(value, width, size, font, ordered=True):
    """Yield exact rows so large review layouts can pause between font calls."""
    if ordered and canvas_rtl.has_rtl(value):
        from .rtl_text import iter_rows_to_visual
        measure = lambda part: (blf.size(font, size), blf.dimensions(font, part)[0])[1]
        yield from ((row, 0) for row in iter_rows_to_visual(value, measure, width))
        return
    offset = 0
    for paragraph in value.split("\n"):
        remaining = paragraph
        if not remaining:
            yield "", offset
        while remaining:
            blf.size(font, size)
            lo, hi = 1, min(64, len(remaining))
            while hi < len(remaining) and blf.dimensions(font, remaining[:hi])[0] <= width:
                lo, hi = hi, min(len(remaining), hi * 2)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if blf.dimensions(font, remaining[:mid])[0] <= width:
                    lo = mid
                else:
                    hi = mid - 1
            cut = text_layout.line_break(remaining, lo)  # Spaces, or any boundary in Chinese and Japanese.
            yield remaining[:cut], offset
            offset += cut
            remaining = remaining[cut:]
        offset += 1
