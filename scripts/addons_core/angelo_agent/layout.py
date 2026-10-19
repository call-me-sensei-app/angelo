# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure geometry policy for a chat column outside Blender's existing editors."""

from dataclasses import dataclass


GAP = 4  # Native editor borders are not part of Area.width/height.
MIN_CHAT_WIDTH = 240
MIN_EDITOR_WIDTH = 160
MIN_VIEWPORT_WIDTH = 360


@dataclass(frozen=True)
class AreaRect:
    key: int
    kind: str
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self):
        return self.x + self.width

    @property
    def top(self):
        return self.y + self.height


@dataclass(frozen=True)
class DockPlan:
    side: str
    width: int
    column: tuple
    expand: bool = False


def covers_height(areas, bottom, top):
    rows = sorted(areas, key=lambda a: a.y)
    if not rows or abs(rows[0].y - bottom) > GAP or abs(rows[-1].top - top) > GAP:
        return False
    return all(abs(a.top - b.y) <= GAP for a, b in zip(rows, rows[1:]))


def is_edge_dock(areas, key, side):
    chat = next((a for a in areas if a.key == key), None)
    if not chat:
        return False
    edge = min(a.x for a in areas) if side == "LEFT" else max(a.right for a in areas)
    position = chat.x if side == "LEFT" else chat.right
    return abs(position - edge) <= GAP and covers_height(
        [chat], min(a.y for a in areas), max(a.top for a in areas))


def plan_dock(areas, side, requested_width=None):
    if side not in {"LEFT", "RIGHT"} or not areas:
        raise ValueError("Choose a left or right native chat dock")
    left, right = min(a.x for a in areas), max(a.right for a in areas)
    edge = left if side == "LEFT" else right
    column = tuple(sorted((a for a in areas if abs((a.x if side == "LEFT" else a.right) - edge) <= GAP), key=lambda a: a.y))
    if not covers_height(column, min(a.y for a in areas), max(a.top for a in areas)):
        raise ValueError("The outer editor column must span the window height")
    width = requested_width or max(MIN_CHAT_WIDTH, round((right - left) * 0.26))
    # Preserve the actual Properties column width, rather than taking chat space
    # out of it or placing chat between Properties and the viewport.
    expand = side == "RIGHT" and any(a.kind == "PROPERTIES" for a in column)
    if expand:
        if max(a.x for a in column) - min(a.x for a in column) > GAP:
            raise ValueError("Align the Outliner/Properties column before automatic docking")
        width = min(width, min(a.x for a in column) - left - MIN_VIEWPORT_WIDTH)
    else:
        width = min(width, min(a.width - (MIN_VIEWPORT_WIDTH if a.kind == "VIEW_3D" else MIN_EDITOR_WIDTH) for a in column))
    if width < MIN_CHAT_WIDTH:
        raise ValueError("Widen the Blender window before adding an outer chat column")
    return DockPlan(side, int(width), column, expand)
