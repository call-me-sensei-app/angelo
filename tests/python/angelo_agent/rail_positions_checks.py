# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The geometry rules test_blender_rail_positions holds the Sessions panel to, in each of its places (1.0.6).

The maintainer (2026-10-25): the panel can stand down the left of the 3D view or centred across its top or bottom, it
scrolls when its workers do not fit, and it minimizes to a round button at the top right of the view. Every rule here
is geometry (inside, clear of, centred, in order, counted), read from the drawn ``session_rail.Layout`` and the
regions the view has, never a coordinate of one screen, one scale or one count, so the same rules hold in any window,
at any interface scale, on any platform.

Pure: rectangles are ``(x, y, w, h)`` in the 3D view's main region's pixels, y up; regions are anything with
``type``, ``alignment``, ``x``, ``y``, ``width`` and ``height`` (window pixels); a plan is anything shaped like
``session_rail.Layout``. Host-tested by test_native_rail_positions_checks; no Blender needed.
"""

EPS = 0.5   # Pixels: drawn rectangles are floats; a touch is not an overlap.
# The regions Blender draws over a 3D view's main region that the panel and its button stay clear of (SPEC.md,
# acceptance 2 and 4): the headers, the toolbar, the sidebar, the asset shelf and its tab strip.
OVER_VIEW = frozenset({'HEADER', 'TOOL_HEADER', 'TOOLS', 'UI', 'ASSET_SHELF', 'ASSET_SHELF_HEADER'})
EXPECTED = {"LEFT": "VERTICAL", "TOP": "HORIZONTAL", "BOTTOM": "HORIZONTAL"}


SMALLEST = 0.5   # rail_minimized.SMALLEST: the least the button shrinks to before it covers anything.
MARGIN = 16      # rail_minimized.MARGIN (ui_tokens.SPACE_4) at a scale of 1: its gap from edges and what it stands by.

def overlaps(a, b, eps=EPS):
    return a[0] < b[0] + b[2] - eps and b[0] < a[0] + a[2] - eps and a[1] < b[1] + b[3] - eps and b[1] < a[1] + a[3] - eps


def contains(outer, inner, eps=EPS):
    return (inner[0] >= outer[0] - eps and inner[1] >= outer[1] - eps
            and inner[0] + inner[2] <= outer[0] + outer[2] + eps and inner[1] + inner[3] <= outer[1] + outer[3] + eps)


def circle_meets(circle, rect, eps=EPS):
    """Whether the disc ``(cx, cy, radius)`` reaches into ``rect`` (closer than its radius to the rectangle)."""
    cx, cy, radius = circle
    nx, ny = min(max(cx, rect[0]), rect[0] + rect[2]), min(max(cy, rect[1]), rect[1] + rect[3])
    return (cx - nx) ** 2 + (cy - ny) ** 2 < (radius - eps) ** 2


def centre(rect):
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def _same(a, b):
    pointer = getattr(a, "as_pointer", None)
    return pointer() == b.as_pointer() if pointer is not None and hasattr(b, "as_pointer") else a is b


def shown(region):
    return region.width > 1 and region.height > 1


def local(region, other):
    """``other``'s rectangle in ``region``'s pixels."""
    return (other.x - region.x, other.y - region.y, other.width, other.height)


def view_obstacles(regions, region):
    """``[(kind, rect)]`` of every region drawn over ``region`` (region overlap): headers, toolbar, sidebar, the
    asset shelf and its tab strip. ``kind`` is the region type in lower case."""
    found, bounds = [], (0, 0, region.width, region.height)
    for other in regions:
        if _same(other, region) or other.type not in OVER_VIEW or not shown(other):
            continue
        rect = local(region, other)
        if overlaps(rect, bounds):
            found.append((other.type.lower(), rect))
    return found


def visible_top_right(regions, region):
    """``(xmax, ymax)`` of the region's visible rectangle (``ED_region_visible_rect``, area.cc
    ``region_visible_rect_calc``) in its pixels: an rcti, so the region's last pixel column and row ("winrct.xmax =
    xmin + winx - 1"), less the overlapping regions aligned to its right (the sidebar: "rect->xmax =
    region_iter->winrct.xmin") and top (the headers: "rect->ymax = region_iter->winrct.ymin")."""
    xmax, ymax, bounds = region.width - 1, region.height - 1, (0, 0, region.width, region.height)
    for other in regions:
        if _same(other, region) or other.type == 'WINDOW' or not shown(other):
            continue
        rect = local(region, other)
        if not overlaps(rect, bounds):
            continue
        if other.alignment == 'RIGHT':
            xmax = min(xmax, rect[0])
        elif other.alignment == 'TOP':
            ymax = min(ymax, rect[1])
    return xmax, ymax


def _c_roundf(value):
    """C's ``roundf``: halves away from zero."""
    import math
    return math.floor(value + 0.5) if value >= 0 else -math.floor(0.5 - value)


def _pixel_size(scale, view):
    """``U.pixelsize`` from the scale factor (wm_window.cc ``WM_window_dpi_set_userdef``: "U.scale_factor = U.dpi /
    72.0f", "pixelsize = max_ii(1, (U.dpi / 64))", then "max_ii(1, pixelsize + U.ui_line_width)", the line width
    preference THIN -1, AUTO 0, THICK 1)."""
    dpi = int(round(scale * 72.0))
    line = {'THIN': -1, 'THICK': 1}.get(getattr(view, "ui_line_width", 'AUTO'), 0)
    return max(1, max(1, dpi // 64) + line)


def _quad_top_right(regions, region):
    """Whether ``region`` is a 3D view's top-right quarter, or no quarter (quad view draws the axes there only)."""
    if getattr(region, "alignment", "") != 'QUAD_SPLIT':
        return True
    quarters = [other for other in regions
                if other.type == 'WINDOW' and getattr(other, "alignment", "") == 'QUAD_SPLIT']
    return _same(max(quarters, key=lambda other: (other.x, other.y)), region) if quarters else True


def _column_slots(region):
    """The navigation column's buttons (``WIDGETGROUP_navigate_draw_prepare``'s ``icon_mini_slot``): zoom and pan
    (their locks are not in Python: counted), the camera and, outside a camera view, the perspective button while the
    rotation is free, and the camera lock in a camera view."""
    data = getattr(region, "data", None)
    free = not getattr(data, "lock_rotation", False)
    camera = getattr(data, "view_perspective", "") == 'CAMERA'
    return 2 + (1 if free else 0) + (1 if free and not camera else 0) + (1 if camera else 0)


def navigation_gizmo(regions, region, view, scale, space, pixel_size=None):
    """Where Blender 5.2 draws the navigation gizmo, its button column and the minimal axes, worked from its own
    source, or None when it draws none. ``view`` is ``Preferences.view``, ``scale`` ``UI_SCALE_FAC``
    (``Preferences.system.ui_scale``, already holding a Retina display's native pixel size), ``space`` the 3D view,
    ``pixel_size`` ``U.pixelsize`` (derived from the scale when not given). What is drawn:

    * the axes (view3d_gizmo_navigate.cc ``WIDGETGROUP_navigate_draw_prepare``): centred at "roundf(rect_visible->xmax
      - icon_offset)", "icon_offset = UI_SCALE_FAC * (GIZMO_OFFSET + (show_rotate_gizmo ? GIZMO_SIZE / 2.0f :
      0.0f))", GIZMO_OFFSET 10; radius ``WIDGET_RADIUS`` (view3d_gizmo_navigate_type.cc: "(U.gizmo_size_navigate_v3d
      / 2.0f) * UI_SCALE_FAC", also its ``gizmo_axis_screen_bounds_get``);
    * the column: its buttons are transparent ("gz->color[3] = 0.0f"), so what shows is the backdrop
      (button2d_gizmo.cc ``ED_gizmo_button2d_group_background``): the int rcti union of each button's bounds,
      "co + area->totrct.xmin -/+ rad" with rad "mat4_to_scale(matrix_final)" = scale_basis "GIZMO_MINI_SIZE / 2.0f"
      (28) times UI_SCALE_FAC (WM_GIZMOGROUPTYPE_SCALE), at "co = {roundf(xmax - icon_offset_mini * 0.75f),
      roundf(ymax - icon_offset_from_axis)}" stepping down "icon_offset_mini" = "(GIZMO_MINI_SIZE + GIZMO_MINI_OFFSET)
      * UI_SCALE_FAC" per slot, padded "rad * 0.2f" (half its width) above and below, outline inside;
    * the minimal axes (view3d_draw.cc ``draw_view_axis``, drawn whatever the navigate preference): lines from
      "startx = rect->xmax - (k + UI_UNIT_X * 1.5)", "starty = rect->ymax - (k + UI_UNIT_Y)", "k = U.rvisize *
      UI_SCALE_FAC" to each tip, letters from "axis_pos + 2": the box certainly drawn (lines and letter origins)."""
    import math
    if not getattr(space, "show_gizmo", True) or not getattr(space, "show_gizmo_navigate", True):
        return None
    navigate, axis = bool(getattr(view, "show_navigate_ui", True)), getattr(view, "mini_axis_type", 'GIZMO')
    xmax, ymax = visible_top_right(regions, region)
    s, corner = scale, _quad_top_right(regions, region)
    pixel = _pixel_size(s, view) if pixel_size is None else pixel_size
    unit = _c_roundf(18.0 * s) + 2 * pixel   # UI_UNIT_X: "int(roundf(18.0f * U.scale_factor)) + (2 * pixelsize)"
    k = float(getattr(view, "mini_axis_size", 25)) * s     # U.rvisize * UI_SCALE_FAC
    boxes = []
    if axis == 'MINIMAL' and corner:
        sx, sy = xmax - (k + unit * 1.5), ymax - (k + unit)
        boxes.append((sx - k, sy - k, sx + k + 2, sy + k + 2))
    if navigate or axis == 'GIZMO':                         # WIDGETGROUP_navigate_poll
        rotate = axis == 'GIZMO' and corner
        size = float(getattr(view, "gizmo_size_navigate_v3d", 80))
        icon_offset = s * (10.0 + (size / 2.0 if rotate else 0.0))
        step = (28.0 + 2.0) * s
        if rotate:
            radius = size / 2.0 * s
            cx, cy = _c_roundf(xmax - icon_offset), _c_roundf(ymax - icon_offset)
            boxes.append((cx - radius, cy - radius, cx + radius, cy + radius))
        if navigate:
            if axis == 'GIZMO':
                from_axis = icon_offset * 2.2
            elif axis == 'MINIMAL' and corner:
                from_axis = unit * 2.0 + k * 1.6
            else:
                from_axis = step * 0.75
            co = (_c_roundf(xmax - step * 0.75), _c_roundf(ymax - from_axis))
            rad = 28.0 / 2.0 * s
            column = [(math.floor(co[0] - rad), math.floor(y - rad), math.floor(co[0] + rad), math.floor(y + rad))
                      for y in (_c_roundf(co[1] - step * slot) for slot in range(_column_slots(region)))]
            x0, y0 = min(b[0] for b in column), min(b[1] for b in column)
            x1, y1 = max(b[2] for b in column), max(b[3] for b in column)
            pad = (x1 - x0) / 2.0 * 0.2
            boxes.append((x0, y0 - pad, x1, y1 + pad))
    if not boxes:
        return None
    left, bottom = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, top = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return (left, bottom, right - left, top - bottom)


def gizmo_rules(product, own, scale, error=None):
    """``[(rule, passed, detail)]``: the product's navigation gizmo rect (what the panel and the button clear) is
    there exactly when Blender draws the gizmo, and covers Blender's own placement (``navigation_gizmo``) within two
    interface pixels."""
    slack = 2.0 * scale + 1.0
    covers = product is not None and own is not None and contains(product, own, slack)
    return [("gizmo_rect_is_known", error is None, error),
            ("gizmo_rect_covers_blenders_own", covers or (product is None and own is None),
             {"product": product, "blender": own})]


def headers_bottom(obstacles, height):
    """The lowest edge of the headers over the view (its top when none): what the live-view label stands under."""
    return min([rect[1] for kind, rect in obstacles if kind in ('header', 'tool_header')] + [height])


def label_rect(width, height, obstacles, label_width, scale):
    """The live view's label pill (``live_view_draw._draw_chip``): ``label_width`` wide (its text and 28 px of
    padding at a scale of 1), 26 px tall, 12 px under the headers, centred."""
    top, pill = headers_bottom(obstacles, height), 26.0 * scale
    return ((width - label_width) / 2, top - pill - 12.0 * scale, label_width, pill)


def more_rects(plan):
    """``(before, after)`` rectangles of the "+N more" lines at the panel's edges (None where nothing is hidden),
    or None when the layout has no such field."""
    more = getattr(plan, "more", None)
    if more is None:
        return None
    if isinstance(more, dict):
        return tuple(more.get(key) for key in ("before", "after"))
    return tuple(more)


def hit_of(plan, rect):
    """``(action, value)`` of the hit drawn on exactly ``rect``, or None."""
    return next(((action, value) for hit_rect, action, value in plan.hits if tuple(hit_rect) == tuple(rect)), None)


def hidden_sides(order, shown_ids):
    """``(before, after)`` session ids hidden before and after the shown window of a rail in ``order`` (rail order,
    the main session first). The main tile pinned first is not part of the window."""
    window = list(shown_ids)
    if len(window) > 1 and window[0] == order[0]:
        window = window[1:]
    places = [order.index(s) for s in window if s in order]
    if not places:
        return [], []
    low, high = min(places), max(places)
    hidden = [s for s in order if s not in shown_ids]
    return [s for s in hidden if order.index(s) < low], [s for s in hidden if order.index(s) > high]


CORNERS = ('navigation_gizmo', 'angelo_in_the_corner', 'live_view_label')   # Coverable for a readable panel.


def plan_rules(plan, size, obstacles, position, order, waiting=(), label_room=0.0, least=None):
    """``[(rule, passed, detail)]`` for a drawn plan of the panel at ``position`` in a region of ``size``, with
    ``obstacles`` ``[(kind, rect)]`` it must stay clear of, the rail's sessions in ``order`` and the ids of the
    sessions waiting for approval; ``label_room`` the pixels a top strip keeps under the headers for the live view's
    label (``live_view_label.room``), whether or not it shows; ``least`` the (height, width) pixels of the panel at
    the least scale it is drawn at (``session_rail_strip.READABLE`` of the person's), its margins in: the toolbar,
    the sidebar and the corner things may be covered only where the view's clear room cannot hold that
    (``coverable``)."""
    width, height = size
    bounds, out = (0, 0, width, height), []
    panel, tiles = tuple(plan.panel), list(plan.tiles)
    orientation = getattr(plan, "orientation", None)
    out.append(("orientation_is_" + EXPECTED[position].lower(), orientation == EXPECTED[position],
                orientation if orientation is not None else "Layout has no orientation"))
    out.append(("panel_inside_the_view", contains(bounds, panel) and panel[2] > 0, [panel, size]))
    allowed = coverable(obstacles, size, least, position, label_room)
    for kind in sorted({kind for kind, _rect in obstacles}):
        hits = [rect for k, rect in obstacles if k == kind and overlaps(panel, rect)]
        out.append(("panel_clears_" + kind, not hits or kind in allowed, {"panel": panel, "covers": hits,
                                                                          "coverable": sorted(allowed)}))
    rects = [tuple(t.rect) for t in tiles]
    out.append(("tiles_inside_the_panel", all(contains(panel, r) for r in rects), [panel, rects]))
    clash = [(a, b) for i, a in enumerate(rects) for b in rects[i + 1:] if overlaps(a, b)]
    out.append(("tiles_never_overlap", not clash, clash[:4]))
    keys = [r[0] for r in rects] if orientation == "HORIZONTAL" else [-r[1] for r in rects]
    out.append(("tiles_stand_in_rail_order", all(a < b for a, b in zip(keys, keys[1:])), keys))
    cx, cy = centre(panel)
    if position == "LEFT":
        out.append(("panel_stands_at_the_left", cx < width / 2, [cx, width]))
    else:   # Nearer its edge of the clear room than the other: a shelf or headers take the view's own edges.
        top = position == "TOP"
        low, high = clear_box(obstacles, size)[1::2]
        high -= label_room
        above = [rect for _kind, rect in obstacles if rect[0] < panel[0] + panel[2] - EPS and rect[0] + rect[2] >
                 panel[0] + EPS and rect[2] < width - EPS]   # A corner thing over the panel's span (the gizmo):
        high = min([high] + [r[1] for r in above if r[1] >= panel[1] + panel[3] - EPS])   # it stands right under it,
        low = max([low] + [r[1] + r[3] for r in above if r[1] + r[3] <= panel[1] + EPS])   # or right above it.
        gaps = (panel[1] - low, high - panel[1] - panel[3])
        slack = 2.0 * plan.scale + 1.0   # A panel filling its room stands at both edges.
        out.append(("panel_stands_at_the_" + position.lower(), gaps[1] <= gaps[0] + slack if top
                    else gaps[0] <= gaps[1] + slack,
                    {"panel": panel, "room": [low, high]}))
        room = clear_room(panel, obstacles, width)
        tolerance = max(2.0 * plan.scale + 1.0, 0.02 * width)   # Centred to the eye: within 2% of the view.
        centred = min(abs(cx - width / 2), abs(cx - (room[0] + room[1]) / 2))
        out.append(("panel_is_centred", centred <= tolerance, {"centre": cx, "view": width, "room": room}))
    shown_ids = [t.session for t in tiles]
    hidden = tuple(getattr(plan, "hidden", (0, 0)))
    out.append(("every_session_shown_or_counted_hidden", len(shown_ids) + sum(hidden) == len(order)
                and set(shown_ids) <= set(order), [len(shown_ids), hidden, len(order)]))
    before, after = hidden_sides(order, shown_ids)
    out.append(("hidden_counts_match_each_side", plan.paged and (len(before), len(after)) == hidden
                or not plan.paged and not before and not after, [len(before), len(after), hidden]))
    attention = tuple(getattr(plan, "attention", (False, False)))
    expected = (any(s in waiting for s in before), any(s in waiting for s in after))
    out.append(("a_hidden_approval_is_marked_on_its_side", attention == expected, [attention, expected]))
    out += _controls(plan, panel, bounds, rects, orientation, hidden)
    return out


def _controls(plan, panel, bounds, rects, orientation, hidden):
    out = []
    arrows = getattr(plan, "arrows", None)
    if plan.paged:
        ok = arrows is not None and len(arrows) == 2
        out.append(("paged_has_arrows", ok, arrows))
        if ok:
            out.append(("arrows_inside_the_panel_and_view", all(contains(panel, a) and contains(bounds, a)
                                                               for a in arrows), [arrows, panel]))
            out.append(("arrows_clear_of_tiles", not any(overlaps(a, r) for a in arrows for r in rects), arrows))
            if orientation == "HORIZONTAL":
                out.append(("arrows_point_left_then_right", arrows[0][0] + arrows[0][2] <= arrows[1][0] + EPS,
                            arrows))
    more = more_rects(plan)
    out.append(("more_lines_exist", more is not None, "Layout has no 'more' field" if more is None else more))
    # Where two tiles fit but not with their lines, the lines go and the header's arrows page (session_rail.page).
    dropped = plan.paged and arrows is not None and more is not None and not any(more)
    for side, name in ((0, "before"), (1, "after")):
        rect = more[side] if more is not None and len(more) > side else None
        out.append(("more_%s_shown_only_when_hidden" % name, (rect is not None) == (hidden[side] > 0) or dropped,
                    [rect, hidden]))
        if rect is not None:
            out.append(("more_%s_inside_the_panel_and_view" % name, contains(panel, rect) and contains(bounds, rect),
                        [rect, panel]))
            out.append(("more_%s_clear_of_tiles" % name, not any(overlaps(rect, r) for r in rects), rect))
            out.append(("more_%s_is_clickable" % name, hit_of(plan, rect) is not None, rect))
    minimize = getattr(plan, "minimize", None)
    out.append(("header_has_a_minimize_control", minimize is not None, minimize))
    if minimize is not None:
        out.append(("minimize_inside_the_panel", contains(panel, minimize), [minimize, panel]))
        out.append(("minimize_clear_of_tiles", not any(overlaps(minimize, r) for r in rects), minimize))
        order = [action for _rect, action, _value in plan.hits]
        first_switch = order.index("switch") if "switch" in order else len(order)
        out.append(("minimize_hit_before_the_tiles", "minimize" in order[:first_switch], order[:6]))
    return out


def room_holds(obstacles, size, least, position, label_room=0.0):
    """Whether the view's clear room (``clear_box``: beside the toolbar and the sidebar, between the headers and the
    shelf with its pills; less a top strip's label room) holds a panel of ``least`` (height, width) pixels, the corner
    things covered if need be: where it does not, the round button stands for the panel (pure)."""
    left, low, right, high = clear_box(obstacles, size)
    high -= label_room if position == "TOP" else 0.0
    return right - left >= least[1] - EPS and high - low >= least[0] - EPS


def coverable(obstacles, size, least, position, label_room=0.0):
    """The obstacle kinds a panel of ``least`` (height, width) pixels may cover in a view of ``size``: the corner
    things (drawn in the view, under the panel) only where the clear room holds it but no way around one of them
    does; never the toolbar or the sidebar (regions over the view: a panel under them could be neither seen nor
    clicked). ``least`` None: none (pure)."""
    if least is None or not room_holds(obstacles, size, least, position, label_room):
        return set()
    left, low, right, high = clear_box(obstacles, size)
    high -= label_room if position == "TOP" else 0.0
    need_h, need_w = least

    def holds(box):
        return box[2] - box[0] >= need_w - EPS and box[3] - box[1] >= need_h - EPS

    for kind, (x, y, w, h) in obstacles:
        if kind not in CORNERS or x >= right or x + w <= left or y >= high or y + h <= low:
            continue   # Not in the clear room: nothing to go around.
        ways = ((max(left, x + w), low, right, high), (left, low, min(right, x), high),
                (left, low, right, min(high, y)), (left, max(low, y + h), right, high))
        if not any(holds(way) for way in ways):
            return set(CORNERS)
    return set()


def clear_box(obstacles, size):
    """``(left, bottom, right, top)`` of the view no region or bottom overlay covers: right of the toolbar, left of the
    sidebar, below the headers, above an asset shelf and its pills (what "top right" and "at the bottom" are of)."""
    width, height = size
    left = max([0.0] + [r[0] + r[2] for k, r in obstacles if k == 'tools'])
    right = min([float(width)] + [r[0] for k, r in obstacles if k == 'ui'])
    low = max([0.0] + [r[1] + r[3] for k, r in obstacles if k.startswith(('asset_shelf', 'shelf_pill'))])
    return left, low, right, float(headers_bottom(obstacles, height))


def clear_room(panel, obstacles, width):
    """``(left, right)``: the span the panel's rows have between what stands over the view at its left and right
    (toolbar, sidebar, gizmo column) across the panel's height. Which side a thing bounds is read from the panel's own
    centre, never the view's middle: a sidebar can cover half the view, and the gizmo then stands left of it."""
    left, right = 0.0, float(width)
    band = (0, panel[1], width, panel[3])
    middle = centre(panel)[0]
    for _kind, rect in obstacles:
        if not overlaps(rect, band) or rect[2] >= width - EPS:
            continue   # Full-width regions (headers, a shelf) are above or below it, never beside it.
        if centre(rect)[0] < middle:
            left = max(left, rect[0] + rect[2])
        else:
            right = min(right, rect[0])
    return left, right


def circle_rules(circle, size, obstacles, scale, disc, exact=True):
    """``[(rule, passed, detail)]`` for the minimized panel's round button ``(cx, cy, radius)`` in a region of
    ``size``, judged against what the view's clear room (``clear_box``) allows (``free_spot``, an oracle over the
    obstacles): inside the view; at the top right (nearer the clear room's top and right than its bottom and left)
    wherever a clear spot of its size is there; clear of every obstacle wherever any clear spot of the least size it
    shrinks to (``SMALLEST``) exists; native overlay regions are never coverable. The Angelo button's size (``disc``
    pixels across at a scale of 1, within 15%)
    wherever a clear full-size spot is in the room's right half (``exact``; the preference path is never stricter
    than never larger), else no smaller than ``SMALLEST`` of it or the visible strip, whichever is smaller."""
    width, height = size
    cx, cy, radius = circle
    box = clear_box(obstacles, size)
    left, low, right, high = box
    blocks = [rect for _kind, rect in obstacles]
    mid_x, mid_y = (left + right) / 2, (low + high) / 2
    top_right = right - (cx + radius) <= cx - radius - left and high - (cy + radius) <= cy - radius - low
    room_there = free_spot(radius, (mid_x, mid_y, right, high), blocks, scale) is not None
    out = [("circle_inside_the_view", radius > 0 and cx - radius >= -EPS and cy - radius >= -EPS
            and cx + radius <= width + EPS and cy + radius <= height + EPS, [circle, size]),
           ("circle_at_the_top_right", top_right or not room_there, [circle, size, box])]
    expected = disc * scale
    least_free = free_spot(SMALLEST * expected / 2, box, blocks, scale) is not None
    for kind in sorted({kind for kind, _rect in obstacles}):
        hits = [rect for k, rect in obstacles if k == kind and circle_meets(circle, rect)]
        hard = kind.upper() in OVER_VIEW
        out.append(("circle_clears_" + kind, not hits or (not hard and not least_free),
                    {"circle": circle, "covers": hits}))
    gap = MARGIN * scale   # The button keeps this from what it stands beside wherever it can (rail_minimized.MARGIN).
    inner = (mid_x + gap, low + gap, right - gap, high - gap)
    grown = [(x - gap, y - gap, w + 2 * gap, h + 2 * gap) for x, y, w, h in blocks]
    if exact and free_spot(expected / 2, inner, grown, scale) is not None:
        out.append(("circle_is_the_angelo_buttons_size", abs(2 * radius - expected) <= 0.15 * expected + 2,
                    [2 * radius, expected]))
    else:
        minimum = max(0.0, min(SMALLEST * expected, right - left, high - low))
        out.append(("circle_is_never_larger_than_the_angelo_button", minimum - 2 <= 2 * radius
                    <= 1.15 * expected + 2, [2 * radius, expected]))
    return out


def free_spot(radius, box, blocks, scale):
    """A centre in ``box`` (left, bottom, right, top) where a disc of ``radius`` lies inside it and meets none of
    ``blocks``, searched on a grid of every 4 interface pixels; None when there is none."""
    left, low, right, high = box
    step = max(1.0, 4.0 * scale)
    x = left + radius
    while x <= right - radius + EPS:
        y = low + radius
        while y <= high - radius + EPS:
            if not any(circle_meets((x, y, radius), rect) for rect in blocks):
                return x, y
            y += step
        x += step
    return None


def circle_from_hits(points):
    """``(cx, cy, radius)`` of the points a hit test said are on the button (its bounding square), or None."""
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(max(xs) - min(xs), max(ys) - min(ys)) / 2)
