# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure geometry policy for a chat column outside Blender's existing editors."""

from dataclasses import dataclass


GAP = 4  # Neighbouring editors at Blender's default border width (2) and scale (1): ``border_gap``.
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


def border_gap(border_width, scale):
    """The tolerance between neighbouring editors' rectangles. Native editor borders are not part of Area.width/height:
    Blender pads each editor by ``int(max(border_width * scale, scale))`` pixels on every shared side
    (``area_calc_totrct``), so neighbours lie ``2 * pad - 1`` apart. ``border_width`` is Preferences > Interface >
    Editors > Border Width, ``scale`` the interface's scale factor (``PreferencesSystem.ui_scale``). Evidence: a fixed
    4 px refused every dock at UI scale 2 (neighbours 7 px apart; "The outer editor column must span the window
    height", shipped 1.0.4 too, Mac 1 diagnostic 2026-10-23), and so it would at border width 3 and scale 1."""
    return max(GAP, 2 * int(max(border_width * scale, scale)))


def covers_height(areas, bottom, top, gap=GAP):
    rows = sorted(areas, key=lambda a: a.y)
    if not rows or abs(rows[0].y - bottom) > gap or abs(rows[-1].top - top) > gap:
        return False
    return all(abs(a.top - b.y) <= gap for a, b in zip(rows, rows[1:]))


def is_edge_dock(areas, key, side, gap=GAP):
    chat = next((a for a in areas if a.key == key), None)
    if not chat:
        return False
    edge = min(a.x for a in areas) if side == "LEFT" else max(a.right for a in areas)
    position = chat.x if side == "LEFT" else chat.right
    return abs(position - edge) <= gap and covers_height(
        [chat], min(a.y for a in areas), max(a.top for a in areas), gap)


def neighbour_room(areas, column, gap=GAP):
    """The widest strip the editors bordering ``column`` on its left can give while each keeps its minimum: what
    ``docking.transfer_strip`` takes from each of them (selected as ``docking.neighbors_for`` does). 0 when they don't
    line up along the column's whole height."""
    edge, bottom, top = min(a.x for a in column), min(a.y for a in column), max(a.top for a in column)
    keys = {a.key for a in column}
    neighbours = [a for a in areas if a.key not in keys and abs(a.right - edge) <= gap
                  and a.y >= bottom - gap and a.top <= top + gap]
    if not covers_height(neighbours, bottom, top, gap):
        return 0
    return min(a.width - (MIN_VIEWPORT_WIDTH if a.kind == "VIEW_3D" else MIN_EDITOR_WIDTH) for a in neighbours)


def plan_dock(areas, side, requested_width=None, gap=GAP):
    if side not in {"LEFT", "RIGHT"} or not areas:
        raise ValueError("Choose a left or right native chat dock")
    left, right = min(a.x for a in areas), max(a.right for a in areas)
    edge = left if side == "LEFT" else right
    column = tuple(sorted((a for a in areas if abs((a.x if side == "LEFT" else a.right) - edge) <= gap), key=lambda a: a.y))
    if not covers_height(column, min(a.y for a in areas), max(a.top for a in areas), gap):
        raise ValueError("The outer editor column must span the window height")
    width = requested_width or max(MIN_CHAT_WIDTH, round((right - left) * 0.26))
    # Preserve the actual Properties column width, rather than taking chat space
    # out of it or placing chat between Properties and the viewport.
    expand = side == "RIGHT" and any(a.kind == "PROPERTIES" for a in column)
    if expand:
        if max(a.x for a in column) - min(a.x for a in column) > gap:
            raise ValueError("Align the Outliner/Properties column before automatic docking")
        room = neighbour_room(areas, column, gap)
        spare = min(a.width for a in column) - MIN_EDITOR_WIDTH
        if room < width and spare > room:
            # The editors beside the column can't give the chat its width, but the column can give more: the chat
            # takes its room from the column. Evidence: Blender's own Close Area on the docked chat can hand its room
            # to the Properties/Outliner column (3D view 356 px, Properties 1931 px), and showing the chat again
            # failed; at UI scale 2 in six of nine Manual tabs (Mac 1 dock sweep, 2026-10-23) the room was counted
            # from the window's edge while the strip comes from the one editor beside Properties.
            expand = False
            width = min(width, spare)
        else:
            width = min(width, room)
    else:
        width = min(width, min(a.width - (MIN_VIEWPORT_WIDTH if a.kind == "VIEW_3D" else MIN_EDITOR_WIDTH) for a in column))
    if width < MIN_CHAT_WIDTH:
        raise ValueError("Widen the Blender window before adding an outer chat column")
    return DockPlan(side, int(width), column, expand)
