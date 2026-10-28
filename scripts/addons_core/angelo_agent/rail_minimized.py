# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel minimized: a round button at the top right of the 3D view (doc/angelo/agent_chat.rst).

Evidence: the maintainer (2026-10-25): "user also should be able to minimize it when they don't want it in the way.
I think what we can do it make it become a circle like how Angelo button is when chat window is hidden. And it can be
position probably top right of the 3D space." and "It can show a robot icon with count maybe to represent number of
workers."

* **What**: the Angelo button's round card (``round_button``, shared with corner Angelo) holding Angelo's robot
  (``rail_glyph.robot``, its eyes cut out in the card's fill), the number of workers on a teal badge on the rim at
  the lower right (every tile but the main one, queued parts too: ``counts``), and an amber dot on the rim at the
  upper right while a worker waits for approval (a tile of kind ``session_rail.APPROVAL``); the marks' places are
  ``rail_glyph``'s (the designer's visual spec). The pointer over it rings it in teal and shows what a click does
  and the counts (``label``).
* **Where** (``place``): the top right of the view's main region (the top-right quarter in quad view,
  ``region_of``), below the headers drawn over it, left of the sidebar, right of the toolbar, above a bottom shelf,
  and never on Blender's navigation gizmo (``gizmo_rect``: the axes and the zoom, pan, camera and perspective
  column, derived from Blender 5.2's own source): left of it in the top row, below it when that row has no room.
* **Input** (``handle``): a left press on the circle holds it, its release on it shows the panel again
  (``restore``: the person's preference, kept like Workers at once), as Return with the pointer on it does; no
  press on it reaches the view behind. The session rail's draw and input handler call ``draw`` and ``handle``.

Geometry is pure and host-tested (``test_native_rail_minimized``); sizes are pixels at a UI scale of 1, multiplied by
the scale; coordinates are region pixels, y up, unless a name says window pixels.
"""

import math

from . import card_kit as kit, live_windows, round_button, ui_tokens as tk

MARGIN = tk.SPACE_4    # From the view's edges, the headers and anything it stands beside (as corner Angelo's).
CAP = 0.72             # The interface font's cap height, as a share of its size: a numeral's vertical centre.
SMALLEST = 0.5         # The least the circle shrinks to before it covers anything: its robot still reads.
SHRINK_STEPS = 4       # Sizes tried between the full disc and SMALLEST.
MOVES = frozenset({'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'})
BUTTONS = frozenset({'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE'})

# Blender 5.2, source/blender/editors/space_view3d/view3d_gizmo_navigate.cc (unscaled pixels):
GIZMO_OFFSET = 10.0       # "#define GIZMO_OFFSET 10.0f": the axes from the visible rect's top right.
GIZMO_MINI_SIZE = 28.0    # "#define GIZMO_MINI_SIZE 28.0f": a button's diameter (scale_basis = GIZMO_MINI_SIZE / 2).
GIZMO_MINI_OFFSET = 2.0   # "#define GIZMO_MINI_OFFSET 2.0f": between buttons.
BACKDROP_PAD = 0.2        # button2d_gizmo.cc ED_gizmo_button2d_group_background: "BLI_rctf_pad(&draw_rect, 0.0f,
                          # rad * 0.2f)" above and below the column, rad = half its width; its U.pixelsize outline is
                          # drawn inside that rect (interface_draw.cc draw_roundbox_4fv_ex insets recti by it).
BUTTONS_MAX = 4           # Zoom, pan, camera and perspective (camera lock instead in a camera view).

_places = {}     # window pointer -> (area pointer, centre x, centre y, radius) in window pixels, as last drawn.
_hover = set()   # Window pointers whose circle the pointer is over.
_pressed = set()
_cramped = set()  # Window pointers whose 3D view holds no readable panel: the circle stands for it (``draw``).
_view = None     # card_view.View for the tooltip, made on first use.


# -- where Blender draws its navigation gizmo -----------------------------------------------------------------
def visible_rect(regions, region):
    """``(xmin, ymin, xmax, ymax)`` of ``region`` that no region drawn over it covers, in region pixels, edges
    included as Blender's rcti are: ``ED_region_visible_rect`` (area.cc ``region_visible_rect_calc``), the rect the
    navigation gizmo is placed in. Blender's loop, quoted: for each other region with ``region_iter->overlap`` that
    intersects the rect, LEFT/RIGHT-aligned: "if (abs(rect->xmin - region_iter->winrct.xmin) < 2) rect->xmin =
    region_iter->winrct.xmax;" and "if (abs(rect->xmax - region_iter->winrct.xmax) < 2) rect->xmax =
    region_iter->winrct.xmin;", TOP/BOTTOM the same on y, FLOAT skipped. Only an overlapping region can intersect the
    view's region (the others are laid out beside it), so intersecting is the overlap test; the view's own regions
    (quad view) tile it, and a hidden region covers nothing. Regions are anything with
    type/alignment/x/y/width/height in window pixels (pure)."""
    xmin, ymin = region.x, region.y
    xmax, ymax = region.x + region.width - 1, region.y + region.height - 1
    for other in regions:
        if other.type == 'WINDOW' or other.width <= 1 or other.height <= 1:
            continue
        oxmin, oymin = other.x, other.y
        oxmax, oymax = other.x + other.width - 1, other.y + other.height - 1
        if not (xmin <= oxmax and xmax >= oxmin and ymin <= oymax and ymax >= oymin):
            continue
        alignment = getattr(other, "alignment", "")
        if alignment in {'LEFT', 'RIGHT'}:
            if abs(xmin - oxmin) < 2:
                xmin = oxmax
            if abs(xmax - oxmax) < 2:
                xmax = oxmin
        elif alignment in {'TOP', 'BOTTOM'}:
            if abs(ymin - oymin) < 2:
                ymin = oymax
            if abs(ymax - oymax) < 2:
                ymax = oymin
    return xmin - region.x, ymin - region.y, xmax - region.x, ymax - region.y


def column_buttons(lock_rotation=False, camera=False):
    """How many buttons the navigation column holds (``WIDGETGROUP_navigate_draw_prepare``'s slots): zoom and pan,
    then while rotation is free the camera button and, outside a camera view, the perspective toggle, and in a
    camera view the camera lock. Python cannot read the zoom and pan locks, so both are counted: the column is never
    taken for shorter than Blender draws it (pure)."""
    return 2 + (0 if lock_rotation else 1 + (0 if camera else 1)) + (1 if camera else 0)


def _roundf(value):
    """C's ``roundf``: halves away from zero (Python's ``round`` rounds them to even)."""
    return math.copysign(math.floor(abs(value) + 0.5), value)


def gizmo_rect(visible, scale, view, shown=True, top_right=True, buttons=BUTTONS_MAX, pixel_size=1.0):
    """``(x, y, width, height)`` in region pixels that Blender 5.2's navigation gizmo covers in a region whose
    ``visible_rect`` is ``visible``: the axes (or the minimal axes) and the button column with its backdrop; None when
    it draws none. ``scale`` is ``UI_SCALE_FAC`` (Preferences ``system.ui_scale``, rna_userdef.cc's read-only
    "scale_factor": "U.dpi / 72.0f" with the display's native pixel size already in ``U.dpi``, wm_window.cc
    ``WM_window_dpi_set_userdef``, so a Retina display needs no other factor), ``view`` the preferences'
    ``view`` (``show_navigate_ui``, ``mini_axis_type``, ``mini_axis_size``, ``gizmo_size_navigate_v3d``), ``shown``
    the view's Gizmos and Navigate toggles both on, ``top_right`` False for a quad view's other quarters,
    ``buttons`` ``column_buttons``, ``pixel_size`` ``U.pixelsize`` (Preferences ``system.pixel_size``). From
    view3d_gizmo_navigate.cc (pure):

    * poll: hidden when "((U.uiflag & USER_SHOW_GIZMO_NAVIGATE) == 0) && (U.mini_axis_type !=
      USER_MINI_AXIS_TYPE_GIZMO)" or "v3d->gizmo_flag & (V3D_GIZMO_HIDE | V3D_GIZMO_HIDE_NAVIGATE)";
    * the axes when "U.mini_axis_type == USER_MINI_AXIS_TYPE_GIZMO" in a top-right region, at "co_rotate =
      {rect_visible->xmax - icon_offset, rect_visible->ymax - icon_offset}" with "icon_offset = UI_SCALE_FAC *
      (GIZMO_OFFSET + (show_rotate_gizmo ? GIZMO_SIZE / 2.0f : 0.0f))", of radius "WIDGET_RADIUS
      ((U.gizmo_size_navigate_v3d / 2.0f) * UI_SCALE_FAC)" (view3d_gizmo_navigate_type.cc; the group's
      WM_GIZMOGROUPTYPE_SCALE makes ``scale_final = scale_basis * UI_SCALE_FAC``);
    * the buttons at "co = {roundf(rect_visible->xmax - icon_offset_mini * 0.75f), roundf(rect_visible->ymax -
      icon_offset_from_axis)}", each next "co[1] - (icon_offset_mini * icon_mini_slot++)", "icon_offset_mini =
      (GIZMO_MINI_SIZE + GIZMO_MINI_OFFSET) * UI_SCALE_FAC", "icon_offset_from_axis" "icon_offset * 2.2f" under the
      axes, "(UI_UNIT_X * 2.0) + (U.rvisize * UI_SCALE_FAC * 1.6f)" under the minimal axes (top right), else
      "icon_offset_mini * 0.75f"; the buttons themselves are drawn transparent ("gz->color[3] = 0.0f"), so the
      column is its backdrop (``ED_gizmo_button2d_group_background``): the union of each button's
      ``gizmo_button2d_bounds``, "co[0] + area->totrct.xmin - rad" .. "+ rad" with "rad = mat4_to_scale(matrix_final)"
      (``GIZMO_MINI_SIZE / 2 * UI_SCALE_FAC``) stored in an int rcti (truncated: floored, window pixels are
      positive), padded by ``BACKDROP_PAD`` of half its width above and below; and ``_minimal_axes``."""
    if not shown:
        return None
    navigate = bool(getattr(view, "show_navigate_ui", True))
    axes = getattr(view, "mini_axis_type", 'GIZMO')
    xmax, ymax, s = visible[2], visible[3], scale
    unit = _roundf(18.0 * s) + 2 * pixel_size   # wm_window.cc: "U.widget_unit = int(roundf(18.0f * U.scale_factor))
    parts = []                                    # + (2 * pixelsize)" (UI_UNIT_X, UI_UNIT_Y).
    if axes == 'MINIMAL' and top_right:
        parts.append(_minimal_axes(xmax, ymax, s, float(getattr(view, "mini_axis_size", 25)), unit))
    if navigate or axes == 'GIZMO':
        rotate = axes == 'GIZMO' and top_right
        size = float(getattr(view, "gizmo_size_navigate_v3d", 80))
        offset = s * (GIZMO_OFFSET + (size / 2 if rotate else 0.0))
        mini = (GIZMO_MINI_SIZE + GIZMO_MINI_OFFSET) * s
        if rotate:
            cx, cy, radius = _roundf(xmax - offset), _roundf(ymax - offset), size / 2 * s
            parts.append((cx - radius, cy - radius, cx + radius, cy + radius))
        if navigate and buttons > 0:
            if axes == 'GIZMO':
                below = offset * 2.2
            elif axes == 'MINIMAL' and top_right:
                below = unit * 2.0 + float(getattr(view, "mini_axis_size", 25)) * s * 1.6
            else:
                below = mini * 0.75
            x, top = _roundf(xmax - mini * 0.75), _roundf(ymax - below)
            low = _roundf(top - mini * (buttons - 1))
            radius = GIZMO_MINI_SIZE / 2 * s
            left, right = math.floor(x - radius), math.floor(x + radius)
            pad = (right - left) / 2 * BACKDROP_PAD
            parts.append((left, math.floor(low - radius) - pad, right, math.floor(top + radius) + pad))
    if not parts:
        return None
    x0, y0 = min(p[0] for p in parts), min(p[1] for p in parts)
    x1, y1 = max(p[2] for p in parts), max(p[3] for p in parts)
    return (x0, y0, x1 - x0, y1 - y0)


def _minimal_axes(xmax, ymax, s, size, unit):
    """The minimal axes' box (view3d_draw.cc ``draw_view_axis``): centred at "startx = rect->xmax - (k + UI_UNIT_X *
    1.5)", "starty = rect->ymax - (k + UI_UNIT_Y)" with "k = U.rvisize * UI_SCALE_FAC", each tip within ``k`` of the
    centre and its letter drawn at "axis_pos[i][0] + 2, axis_pos[i][1] + 2": a widget unit holds a letter at the
    default font size, a quarter of one its descender."""
    k = size * s
    x, y = xmax - (k + unit * 1.5), ymax - (k + unit)
    return (x - k, y - k - unit / 4, x + k + 2 + unit, y + k + 2 + unit)


# -- where the circle stands ----------------------------------------------------------------------------------
def place(size, insets, scale, gizmo, avoid=()):
    """``(cx, cy, radius)`` of the circle in a region of ``size`` (width, height). ``insets``: the pixels other
    regions cover at its edges, ``{"left", "right", "top", "bottom"}`` (the toolbar, the sidebar, the headers drawn
    over it, a bottom shelf); ``gizmo``: ``gizmo_rect`` or None; ``avoid``: other ``(x, y, w, h)`` to keep clear (a
    live view's label; pure).
    Try the top/right clear spots, shrink to ``SMALLEST``, then the left half and no margin. Last, cover a corner
    if needed, never a native region. A subpixel disc must have an uncovered integer hit; no usable pixel gives
    radius zero (``draw`` removes its hit target)."""
    width, height = size
    left, right, top, bottom = (float(insets.get(key) or 0) for key in ("left", "right", "top", "bottom"))
    blocks = [tuple(rect) for rect in (gizmo, *avoid) if rect and rect[2] > 0 and rect[3] > 0]
    full, margin = round_button.DISC * scale / 2, MARGIN * scale
    for step in range(SHRINK_STEPS + 1):
        radius = full * (1 - (1 - SMALLEST) * step / SHRINK_STEPS)
        lo_x, hi_x = left + margin + radius, width - right - margin - radius
        lo_y, hi_y = bottom + margin + radius, height - top - margin - radius
        row = _slide(hi_x, hi_y, radius, margin, blocks, 0)
        middle = (left + width - right) / 2
        if hi_y >= lo_y and row is not None and row >= max(lo_x, middle):
            return _inside_region(row, hi_y, radius, width, height)
        column = _slide(hi_x, hi_y, radius, margin, blocks, 1)
        if hi_x >= lo_x and column is not None and column >= lo_y:
            return _inside_region(hi_x, column, radius, width, height)
        found = _spots((max(lo_x, middle), lo_y, hi_x, hi_y), radius, margin, blocks)
        if found is not None:
            return _inside_region(found[0], found[1], radius, width, height)
    for gap in (margin, 0.0):   # The margin goes before anything is covered.
        found = _spots((left + gap + radius, bottom + gap + radius, width - right - gap - radius,
                        height - top - gap - radius), radius, gap, blocks)
        if found is not None:
            return _inside_region(found[0], found[1], radius, width, height)
    radius = min(radius, max(0.0, width - right - left) / 2, max(0.0, height - top - bottom) / 2)
    lo_x, hi_x, lo_y, hi_y = left + radius, width - right - radius, bottom + radius, height - top - radius
    pixels = (left, bottom, width - right, height - top) if 0 < radius < 1 else None
    found = _spots((lo_x, lo_y, hi_x, hi_y), radius, 0.0, blocks, pixels)
    if found is not None:
        return found[0], found[1], radius
    spots = [(min(max(lo_x, row if row is not None else lo_x), hi_x), hi_y),
             (hi_x, min(max(lo_y, column if column is not None else lo_y), hi_y))]
    if pixels is not None:
        spots = [point for spot in spots for point in round_button.pixel_centres(spot, radius, pixels)]
    if not spots:
        return left, bottom, 0.0
    cx, cy = min(spots, key=lambda spot: _covered(spot[0], spot[1], radius, blocks))
    return _inside_region(cx, cy, radius, width, height)


def _spots(box, radius, margin, blocks, pixels=None):
    """Highest/rightmost clear centre touching a box/block edge; optional native ``pixels`` require an integer hit."""
    lo_x, lo_y, hi_x, hi_y = box
    if hi_x < lo_x or hi_y < lo_y:
        return None
    reach = margin + radius
    xs = {lo_x, hi_x} | {v for x, _y, w, _h in blocks for v in (x - reach, x + w + reach)}
    ys = {lo_y, hi_y} | {v for _x, y, _w, h in blocks for v in (y - reach, y + h + reach)}
    clear = [(x, y) for x in xs if lo_x <= x <= hi_x for y in ys if lo_y <= y <= hi_y
             and _first_block(x, y, radius, margin, blocks) is None]
    if pixels is not None:
        clear = [point for spot in clear for point in round_button.pixel_centres(spot, radius, pixels)
                 if _first_block(*point, radius, margin, blocks) is None]
    return max(clear, key=lambda spot: (spot[1], spot[0]), default=None)


def _first_block(cx, cy, radius, margin, blocks):
    """The first block the circle's square, grown by ``margin``, overlaps; None when it is clear of them all."""
    reach = radius + margin - 1e-6   # A spot touching a block exactly is clear (floats round the edge both ways).
    for block in blocks:
        x, y, w, h = block
        if cx + reach > x and cx - reach < x + w and cy + reach > y and cy - reach < y + h:
            return block
    return None


def _slide(cx, cy, radius, margin, blocks, axis):
    """The centre moved left (``axis`` 0: x) or down (1: y) past each block in its way, ``margin`` clear of it; None
    when it never comes clear. Each move passes one block for good, so there are at most as many as blocks."""
    for _ in range(len(blocks) + 1):
        block = _first_block(cx, cy, radius, margin, blocks)
        if block is None:
            return cx if axis == 0 else cy
        if axis == 0:
            cx = block[0] - margin - radius
        else:
            cy = block[1] - margin - radius
    return None


def _covered(cx, cy, radius, blocks):
    """How many pixels of ``blocks`` the circle's square covers at (``cx``, ``cy``)."""
    total = 0.0
    for x, y, w, h in blocks:
        total += max(0.0, min(cx + radius, x + w) - max(cx - radius, x)) * max(0.0, min(cy + radius, y + h)
                                                                                - max(cy - radius, y))
    return total


def _inside_region(cx, cy, radius, width, height):
    return (min(max(cx, radius), max(radius, width - radius)), min(max(cy, radius), max(radius, height - radius)),
            radius)


def badge_text(count):
    """The workers' number as the badge shows it: two digits at most."""
    return str(count) if count < 100 else "99+"


def counts(tiles, lead):
    """``(workers, waiting)`` of the rail's tiles (``session_rail_draw.states``): how many are not the lead's
    (``lead``: its session id), and whether one of those waits for approval (pure)."""
    from .session_rail import APPROVAL
    workers = [tile for tile in tiles if tile.session != lead]
    return len(workers), any(tile.kind == APPROVAL for tile in workers)


def label(count, waiting, cramped=False):
    """The circle's tooltip, translated now: what a click does (``cramped``: that the view holds no panel now), the
    badge's ``count`` of workers, and a waiting approval."""
    from .ui_text import iface_, plural_
    first = (iface_("No room for the Sessions panel here: close the sidebar or widen the view") if cramped
             else iface_("Show the Sessions panel"))
    lines = [first, plural_(count, "{count} worker", "{count} workers")]
    if waiting:
        lines.append(iface_("A worker waits for your approval"))
    return "\n".join(lines)


# -- drawing --------------------------------------------------------------------------------------------------
def paint(view, card, glyph, cx, cy, radius, scale, count, waiting, hover=False, pressed=False):
    """The circle with ``view``'s primitives (``chat_view``: shape, text, measure), ``card`` (``round_button.draw``)
    and ``glyph`` (``rail_glyph.robot``): the card, the robot, the workers' badge and the approval dot, in card_kit
    colours only. The badge and the dot sit where ``rail_glyph`` puts them, each ringed in the card's fill so it
    parts from the rim."""
    from . import rail_glyph
    s = scale
    card(cx, cy, radius, s, hover, pressed)
    fill = round_button.colors(hover, pressed)[0]
    glyph(cx, cy, rail_glyph.robot_size(radius), kit.TEXT, eyes=fill)
    ring = rail_glyph.BADGE_RING * s
    if count > 0:
        words, size = badge_text(count), tk.TYPE_SMALL * s
        text_w = view.measure(words, size, scale=1.0)
        x, y, w, h = rail_glyph.badge_rect(cx, cy, radius, text_w, s)
        view.shape(x - ring, y - ring, w + 2 * ring, h + 2 * ring, fill, h / 2 + ring)
        view.shape(x, y, w, h, kit.TEAL, h / 2)
        view.text(words, x + (w - text_w) / 2, y + (h - CAP * size) / 2, size, kit.CARD)
    if waiting:
        x, y, d = rail_glyph.approval_dot(cx, cy, radius, s)
        view.shape(x - ring, y - ring, d + 2 * ring, d + 2 * ring, fill, d / 2 + ring)
        view.shape(x, y, d, d, kit.AMBER, d / 2)


def region_of(area):
    """The region the circle stands in: the 3D view's main region at its top right (quad view's top-right quarter,
    where Blender draws the axes)."""
    regions = [region for region in area.regions if region.type == 'WINDOW']
    return max(regions, key=lambda region: (region.y + region.height, region.x + region.width), default=None)


def insets(regions, region):
    """``place``'s insets of ``region``: the toolbar and the sidebar (``chat_launcher.insets``), the headers drawn over
    it (``session_rail.top_inset``) and a bottom shelf or header (``session_rail.bottom_inset``)."""
    from . import chat_launcher, session_rail
    left, right, _bottom = chat_launcher.insets(regions, region)
    return {"left": left, "right": right, "top": session_rail.top_inset(regions, region),
            "bottom": session_rail.bottom_inset(regions, region)}


def navigation_rect(area, region):
    """``gizmo_rect`` of this 3D view's ``region`` as Blender draws it now: the person's preferences, the view's
    Gizmos and Navigate toggles, quad view, and the region's camera view and rotation lock."""
    import bpy
    preferences = bpy.context.preferences
    space = area.spaces.active
    shown = bool(getattr(space, "show_gizmo", True) and getattr(space, "show_gizmo_navigate", True))
    data = getattr(region, "data", None)
    corner = region_of(area)
    top_right = getattr(region, "alignment", "") != 'QUAD_SPLIT' or (
        corner is not None and corner.as_pointer() == region.as_pointer())
    return gizmo_rect(visible_rect(area.regions, region), preferences.system.ui_scale, preferences.view, shown,
                      top_right, column_buttons(bool(getattr(data, "lock_rotation", False)),
                                                getattr(data, "view_perspective", "") == 'CAMERA'),
                      float(getattr(preferences.system, "pixel_size", 1.0) or 1.0))


def spot(area, region, scale, avoid=()):
    """``(cx, cy, radius)`` of the circle in ``region`` of ``area`` now (``place`` with the regions and gizmo)."""
    return place((region.width, region.height), insets(area.regions, region), scale, navigation_rect(area, region),
                 avoid)


def draw(window, area, region, count, waiting, scale, avoid=(), cramped=False):
    """Draw/remember the count/approval circle for ``hit``/``handle``; ``cramped`` means no panel fits this view."""
    global _view
    import gpu
    from . import canvas_controls, chat_view, rail_glyph
    cx, cy, radius = spot(area, region, scale, avoid)
    pointer = window.as_pointer()
    cramp(pointer, cramped)
    if radius <= 0:
        _places.pop(pointer, None)
        leave(window)
        return
    _places[pointer] = (area.as_pointer(), region.x + cx, region.y + cy, radius)
    hover = pointer in _hover
    gpu.state.blend_set('ALPHA')
    marks = scale * min(1.0, radius / (round_button.DISC * scale / 2))   # A shrunk circle's badge and dot shrink too.
    paint(chat_view, round_button.draw, rail_glyph.robot, cx, cy, radius, marks, count, waiting, hover,
          pointer in _pressed)
    if hover:
        if _view is None:
            from .card_view import View
            _view = View()
        canvas_controls.tip(_view, (cx - radius, cy - radius, 2 * radius, 2 * radius), label(count, waiting, cramped),
                            scale, region.width, region.height)
    gpu.state.blend_set('NONE')


# -- input ----------------------------------------------------------------------------------------------------
def minimized():
    """The person minimized the Sessions panel (``parallel_setting``: False whenever the preferences are unreadable)."""
    from . import parallel_setting
    return parallel_setting.rail_minimized()


def shown(window_pointer):
    """Whether the circle stands for the panel in that window: minimized, or no room for a readable panel there."""
    return window_pointer in _cramped or minimized()


def hit(window_pointer, x, y):
    """Whether window pixel (``x``, ``y``) is on the circle as last drawn in that window while the panel is
    minimized: on the disc, never its square's corners."""
    place = _places.get(window_pointer)
    return place is not None and round_button.inside(place[1], place[2], place[3], x, y) and shown(window_pointer)


def handle(window, event):
    """Circle hover/press/release and Return restore the panel; no taken press reaches the view. Return
    ``RUNNING_MODAL`` for what it takes, None otherwise (moves always pass on)."""
    pointer = window.as_pointer()
    place = _places.get(pointer)
    if place is None or not shown(pointer):
        leave(window)
        return None
    over = round_button.inside(place[1], place[2], place[3], event.mouse_x, event.mouse_y)
    if event.type in MOVES:
        if over and pointer not in _hover:
            _hover.add(pointer)
            _cursor(window, True)
            _redraw(window, place[0])
        elif not over and pointer in _hover:
            leave(window)   # Redraws it too.
        return None
    if event.type == 'LEFTMOUSE':
        if event.value in {'PRESS', 'DOUBLE_CLICK'} and over:
            _pressed.add(pointer)
            _redraw(window, place[0])
            return {'RUNNING_MODAL'}
        if event.value == 'RELEASE' and pointer in _pressed:
            _pressed.discard(pointer)
            _redraw(window, place[0])
            if over:
                restore(window)
            return {'RUNNING_MODAL'}
        return None
    if over and event.type in BUTTONS:
        return {'RUNNING_MODAL'}   # No selection or menu behind the circle.
    if over and event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
        restore(window)
        return {'RUNNING_MODAL'}
    return None


def restore(window):
    """Restore the saved panel choice; a cramped view keeps the circle whose tooltip explains why."""
    from . import parallel_setting
    leave(window)
    if not minimized():
        return
    place = _places.pop(window.as_pointer(), None)
    parallel_setting.set_rail_minimized(False)
    _redraw(window, place[0] if place else None)


def leave(window):
    """The pointer left the circle (or it went): no hover or press stays, and the cursor is the view's again."""
    pointer = window.as_pointer()
    if pointer in _hover:
        _hover.discard(pointer)
        _cursor(window, False)
        place = _places.get(pointer)
        _redraw(window, place[0] if place else None)
    _pressed.discard(pointer)


def cramp(window_pointer, cramped):
    """Whether the circle stands in that window for a panel with no room (the panel's draw says so every frame)."""
    (_cramped.add if cramped else _cramped.discard)(window_pointer)


def release():
    """Forget every window's circle (a file load replaced the windows; the add-on unregistering)."""
    global _view
    _places.clear()
    _hover.clear()
    _pressed.clear()
    _cramped.clear()
    _view = None


def _cursor(window, hand):
    try:
        if hand:
            window.cursor_modal_set('HAND')
        else:
            window.cursor_modal_restore()
    except (RuntimeError, TypeError):
        pass


def _redraw(window, area_pointer):
    for area in live_windows.areas(window):
        if area_pointer is None or area.as_pointer() == area_pointer:
            area.tag_redraw()
