# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Clickable geometry must match the pixels visible in its owning viewport."""


def clip_rect(rect, viewport):
    x, y, width, height = rect
    left, bottom, clip_width, clip_height = viewport
    x2, y2 = min(x + width, left + clip_width), min(y + height, bottom + clip_height)
    x, y = max(x, left), max(y, bottom)
    return (x, y, x2 - x, y2 - y) if x2 > x and y2 > y else None


def clipped_hits(hits, viewport):
    return [(visible, action, value) for rect, action, value in hits
            if (visible := clip_rect(rect, viewport)) is not None]


def contains(rect, x, y):
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def release_active(pressed, hits, x, y):
    """Revalidate the exact press target against current, clipped draw geometry.

    A redraw may remove, clip or move a control while the mouse is held. Never
    dispatch an obsolete invisible target using only its old rectangle.
    """
    rect, action, value = pressed
    return contains(rect, x, y) and any(found == action and argument == value and contains(current, x, y)
        for current, found, argument in hits)
