# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native split/join operations; yields between window-manager geometry updates."""

import bpy

from .layout import AreaRect, GAP, covers_height, is_edge_dock, plan_dock


def rectangles(window):
    return [AreaRect(a.as_pointer(), a.type, a.x, a.y, a.width, a.height) for a in window.screen.areas]


def area_for(window, pointer):
    area = next((a for a in window.screen.areas if a.as_pointer() == pointer), None)
    if area is None:
        raise RuntimeError("The native editor layout changed while chat was moving")
    return area


def center(area):
    return area.x + area.width // 2, area.y + area.height // 2


def split(window, pointer, direction, factor):
    area = area_for(window, pointer)
    before = {a.as_pointer() for a in window.screen.areas}
    with bpy.context.temp_override(window=window, area=area):
        result = bpy.ops.screen.area_split(direction=direction, factor=factor)
    created = next((a.as_pointer() for a in window.screen.areas if a.as_pointer() not in before), None)
    if result != {'FINISHED'} or created is None:
        raise RuntimeError("Blender could not create the outer chat column")
    return created


def join(window, keep_pointer, discard_pointer):
    keep, discard = area_for(window, keep_pointer), area_for(window, discard_pointer)
    with bpy.context.temp_override(window=window, area=keep):
        result = bpy.ops.screen.area_join(source_xy=center(keep), target_xy=center(discard))
    if result != {'FINISHED'}:
        raise RuntimeError("Blender could not join the native chat column")


def neighbors_for(rects, column, side):
    edge = column[0].x if side == "LEFT" else column[0].right
    keys = {a.key for a in column}
    bottom, top = min(a.y for a in column), max(a.top for a in column)
    rows = sorted((a for a in rects if a.key not in keys
                   and abs((a.right if side == "LEFT" else a.x) - edge) <= GAP
                   and a.y >= bottom - GAP and a.top <= top + GAP), key=lambda a: a.y)
    if not covers_height(rows, bottom, top):
        raise RuntimeError("Align the adjacent editor column before moving chat")
    return rows


def join_column(window, pointers):
    pointers = sorted(pointers, key=lambda p: area_for(window, p).y)
    keep = pointers[0]
    for pointer in pointers[1:]:
        join(window, keep, pointer)
        yield 0.05
    return keep


def slice_column(window, pointer, rows):
    pieces, remainder = {pointer}, pointer
    for row in rows[:-1]:
        current = area_for(window, remainder)
        created = split(window, remainder, "HORIZONTAL", (row.top - current.y) / current.height)
        pieces.add(created)
        yield 0.05
        remainder = max((area_for(window, p) for p in (remainder, created)), key=lambda a: a.y).as_pointer()
    return pieces


def fill_rows(window, pieces, rows):
    for row in rows:
        area = area_for(window, row.key)
        middle = area.y + area.height // 2
        target = next((area_for(window, p) for p in pieces
                       if area_for(window, p).y <= middle < area_for(window, p).y + area_for(window, p).height), None)
        if target is None or abs(target.height - area.height) > GAP:
            raise RuntimeError("The adjacent editor boundaries no longer match chat")
        discard = target.as_pointer()
        join(window, row.key, discard)
        pieces.remove(discard)
        yield 0.05


def transfer_strip(window, sources, destinations, width, source_side):
    # area_move is mouse-edge gated in stock Blender. Use its real split/join
    # API instead of moving the user's cursor or reaching into native memory.
    if any(a.width - width < 160 for a in sources):
        raise RuntimeError("Not enough space beside Properties for a separate chat column")
    fragments = []
    for source in sources:
        area = area_for(window, source.key)
        factor = width / area.width if source_side == "LEFT" else (area.width - width) / area.width
        created = split(window, source.key, "VERTICAL", factor)
        yield 0.05
        pair = sorted((area_for(window, p) for p in (source.key, created)), key=lambda a: a.x)
        fragments.append(pair[0 if source_side == "LEFT" else -1].as_pointer())
    pointer = yield from join_column(window, fragments)
    pieces = yield from slice_column(window, pointer, destinations)
    yield from fill_rows(window, pieces, destinations)


def collapse_chat(window, pointer, restore_properties=False):
    """Remove only chat copies, keeping every neighboring editor's native state."""
    chat = next(a for a in rectangles(window) if a.key == pointer)
    rects = rectangles(window)
    neighbors = []
    for side in ("LEFT", "RIGHT"):
        candidates = [a for a in rects if a.key != pointer
                      and abs((a.right if side == "LEFT" else a.x) - (chat.x if side == "LEFT" else chat.right)) <= GAP
                      and a.y >= chat.y - GAP and a.top <= chat.top + GAP]
        if covers_height(candidates, chat.y, chat.top):
            neighbors = sorted(candidates, key=lambda a: a.y)
            break
    if not neighbors:
        raise RuntimeError("Use native area controls to align chat before moving its dock")
    if restore_properties and max(a.x for a in neighbors) - min(a.x for a in neighbors) > GAP:
        raise RuntimeError("The Properties column changed; use native area controls to move chat")
    restore_delta = chat.right - neighbors[0].right if restore_properties else 0

    # Recreate only the chat's horizontal boundaries to match the intact column
    # beside it. Joins then keep Outliner/Properties (or Viewport/Timeline) data.
    pieces = yield from slice_column(window, pointer, neighbors)
    yield from fill_rows(window, pieces, neighbors)
    if restore_delta:
        expanded = [a for a in rectangles(window) if a.key in {n.key for n in neighbors}]
        destinations = neighbors_for(rectangles(window), expanded, "LEFT")
        yield from transfer_strip(window, expanded, destinations, restore_delta, "LEFT")


def relocate_chat(window, side, existing_pointer=None, restore_properties=False):
    if existing_pointer and is_edge_dock(rectangles(window), existing_pointer, side):
        return existing_pointer, restore_properties
    if existing_pointer:
        yield from collapse_chat(window, existing_pointer, restore_properties)
    plan = plan_dock(rectangles(window), side)
    if plan.expand:
        sources = neighbors_for(rectangles(window), plan.column, "LEFT")
        yield from transfer_strip(window, sources, plan.column, plan.width, "RIGHT")
    fragments = []
    for row in plan.column:
        area = area_for(window, row.key)
        factor = (area.width - plan.width) / area.width if side == "RIGHT" else plan.width / area.width
        created = split(window, row.key, "VERTICAL", factor)
        yield 0.05
        pair = sorted((area_for(window, p) for p in (row.key, created)), key=lambda a: a.x)
        fragments.append(pair[-1 if side == "RIGHT" else 0].as_pointer())
    keep = yield from join_column(window, fragments)
    return keep, plan.expand
