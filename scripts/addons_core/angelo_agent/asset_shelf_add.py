# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio and Generation Flows on every Asset Shelf (1.0.5).

Evidence: the maintainer (2026-10-23): "And asset shelf as well. There should be a button to add asset, which then
opens up media library with 3D selected (or image if it's to do with texture)." The binding design is
doc/angelo/studio_entry_points.rst (revision 3): two permanent pills, never "Add asset" (in Blender that means Mark
as Asset).

* **Where**: every shown Asset Shelf of a 3D view or Image editor (sculpt, paint, texture paint, Grease Pencil,
  Object mode's ``media_shelf``, and any shelf a later mode or add-on adds: the pills follow the shelf regions, never
  a list of shelf classes). They sit on the shelf's top edge at its left end, clear of the toolbar, the Adjust Last
  Operation panel and Angelo in the corner (``place``). They are drawn over the view, not in the shelf's header:
  Blender draws only the first header type of a region (``ED_region_header_layout``), the shelf's own C++ one, so a
  Python header there never shows, and the header's catalog tabs have no Python-readable extent to fit beside.
* **What**: *Media Studio* (its spark-frame icon, ``studio_toggle.frame_icon``) then *Generation Flows* (the
  three-node glyph, ``flow_open.flow_icon``), 6 px apart. Each is a card-kit ``PANEL`` pill with a 1 px ``LINE``
  border: ``PANEL_HOVER`` under the pointer, ``TEAL_SOFT`` while pressed, never teal-filled. Where the labels do not
  fit, both pills show their icons only (never an ellipsis); the tooltip still names them.
* **Media Studio** opens through ``studio_toggle.open_media_studio`` with the shelf's tab, always selected: Image
  for texture paint and the Image editor, and on Object mode's shelf for its Images catalog tab
  (``media_shelf.shelf_kind``); 3D everywhere else. **Generation Flows** opens through ``angelo.show_flows``, which
  routes by mode (Manual's Generation Flows tab, Director's Flows popup).
* **Collapse**: on Object mode's Angelo shelf (``media_shelf``, collapsed by default) a round chevron comes first:
  up while only the tab strip shows (expand), down while the rows show (collapse to the tabs). The pills stay in
  both states, on the tab strip's top edge.
* **Input**: a passive modal operator per window (``input_policy.CANVAS_INPUTS``) that takes only presses on the
  pills and passes every other event, so a brush stroke or a shelf click is never delayed. It starts from a timer
  the first time a pill draws in a window and ends when no pill is shown there.
* Shown in every interactive run, automation included, so every suite's captures show the product as people see it;
  never in a background run. ``shelf_empty`` adds an empty state inside a shelf that lists nothing; its button's
  clicks come through this input handler too.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import card_kit as kit, input_policy, live_windows, shelf_empty
from .ui_text import iface_, n_

IDENTITY = "angelo.asset_shelf_add_input"
PILLS = ("studio", "flows")
FOLD = "fold"      # The collapse chevron, icon only, first on Object mode's Angelo shelf.
LABELS = {"studio": n_("Media Studio"), "flows": n_("Generation Flows")}
TIPS = {"studio": n_("Media Studio: generate images and 3D models in one step"),
        "flows": n_("Generation Flows: go further, chain generation steps into a flow you can rerun")}
FOLD_TIPS = {True: n_("Show the shelf's assets"), False: n_("Collapse the shelf to its tabs")}   # By collapsed.
HEIGHT = 28.0      # ui_tokens.BUTTON_HEIGHT_SMALL at a UI scale of 1 (pixels); the radius is half of it.
PAD = 12.0         # Inside a pill, left and right.
ICON = 16.0        # ui_tokens.ICON_SIZE
GAP = 8.0          # Between the icon and the label.
BETWEEN = 6.0      # Between the two pills.
TEXT_SIZE = 12.0   # ui_tokens.TYPE_SMALL
MARGIN = 8.0       # From the shelf's left edge and from regions drawn over the view.
LIFT = 6.0         # Above the shelf's top edge.
SHADOW = (0.0, 0.0, 0.0, 0.28)
OBSTACLES = frozenset({'TOOLS', 'HUD', 'UI'})

places = {}        # area pointer -> (window pointer, ((action, (x, y, w, h)), ...) window pixels, labelled, kind).
_hover = set()     # (area pointer, action) under the pointer.
_pressed = set()
_starting = set()  # Window pointers whose input handler a timer is about to start.
_handlers = []
_view = None
_icons = {}        # action -> GPU texture of its icon, built once.
draws = 0          # Fixtures: pills drawn, and the openings asked for in order ("3d", "image", "flows").
opened = []
last_error = None


def enabled():
    return not bpy.app.background


def overlaps(a, b):
    return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]


def place(header, view, obstacles, size, scale):
    """The pills' joint ``(x, y, w, h)`` in window pixels, or None where it does not fit. ``header`` is the shown
    shelf header's rectangle, ``view`` the editor's main region, ``obstacles`` rectangles drawn over the view
    (toolbar, Adjust Last Operation, Angelo in the corner), ``size`` the pills' joint ``(w, h)``. Pure."""
    width, height = size
    x, y = header[0] + MARGIN * scale, header[1] + header[3] + LIFT * scale
    for other in sorted(obstacles, key=lambda r: r[0]):
        if overlaps((x, y, width, height), other):
            x = other[0] + other[2] + MARGIN * scale
    fits_x = x + width <= min(header[0] + header[2], view[0] + view[2]) - MARGIN * scale
    fits_y = y + height <= view[1] + view[3] - MARGIN * scale
    return (x, y, width, height) if fits_x and fits_y else None


def pill_width(text_width, scale, labelled):
    return (PAD * 2 + ICON) * scale + ((GAP * scale + text_width) if labelled else 0)


def layout(header, view, obstacles, text_widths, scale, actions=PILLS):
    """``(((action, rect), ...), labelled)`` for ``actions`` (the pills, after the chevron where it shows), labelled
    where they fit, else icon only, else None. The chevron is always icon only."""
    height = HEIGHT * scale
    for labelled in (True, False):
        widths = [height if action == FOLD else pill_width(text_widths[action], scale, labelled)
                  for action in actions]
        joint = place(header, view, obstacles, (sum(widths) + BETWEEN * scale * (len(widths) - 1), height), scale)
        if joint is not None:
            rects, x = [], joint[0]
            for action, width in zip(actions, widths):
                rects.append((action, (x, joint[1], width, height)))
                x += width + BETWEEN * scale
            return tuple(rects), labelled
    return None


def holds(region, header):
    """Whether ``region`` is the one of its editor directly above the shelf header's left end."""
    x, y = header.x + MARGIN, header.y + header.height + LIFT
    return region.x <= x < region.x + region.width and region.y <= y < region.y + region.height


def shown(region):
    return region is not None and region.width > 1 and region.height > 1


def shelf_header(area):
    """The area's shelf header while its Asset Shelf is shown, expanded or collapsed to its tab strip (a mode with a
    shelf, not hidden: the header hides with the rows), else None."""
    header = next((r for r in area.regions if r.type == 'ASSET_SHELF_HEADER'), None)
    return header if shown(header) else None


def folds(area, mode):
    """Whether this view's shelf has the collapse chevron: Object mode's Angelo shelf on a binary that collapses.
    An RNA function is no class attribute (``hasattr(bpy.types.AssetShelf, ...)`` is always False): read ``bl_rna``."""
    rna = getattr(getattr(bpy.types, "AssetShelf", None), "bl_rna", None)
    return area.type == 'VIEW_3D' and mode == 'OBJECT' and rna is not None and "angelo_set_collapsed" in rna.functions


def collapsed(area):
    return not shown(next((r for r in area.regions if r.type == 'ASSET_SHELF'), None))


def obstacle_rects(area, window_pointer):
    rects = [(r.x, r.y, r.width, r.height) for r in area.regions if r.type in OBSTACLES and shown(r)]
    from . import chat_launcher
    corner = chat_launcher._places.get(window_pointer)
    if corner and corner[0] == area.as_pointer() and window_pointer in chat_launcher._shown:
        _area, cx, cy, radius = corner
        rects.append((cx - radius, cy - radius, 2 * radius, 2 * radius))
    return rects


def label_width(label, scale):
    """The label's width in pixels as ``_paint`` draws it (``chat_view.measure``: shaped, at the drawn size)."""
    from . import chat_view
    return chat_view.measure(label, TEXT_SIZE, scale=scale) * scale


def _covered(area):
    from . import chat_launcher
    return chat_launcher._covered(area) or chat_launcher.moving()


# -- drawing --------------------------------------------------------------------------------------------------
def _draw():
    global draws, _view, last_error
    area, region, window = bpy.context.area, bpy.context.region, bpy.context.window
    if area is None or region is None or window is None or region.type != 'WINDOW':
        return
    pointer = area.as_pointer()
    header = shelf_header(area) if enabled() else None
    if header is None or _covered(area):
        _forget(pointer)
        return
    if not holds(region, header):
        return   # Another view of a split (Quad View) whose region is not above the shelf's left end.
    import gpu
    try:
        from . import chat_view, studio_toggle
        scale = chat_view.ui_scale()
        labels = {action: iface_(LABELS[action]) for action in PILLS}
        actions = ((FOLD,) if folds(area, bpy.context.mode) else ()) + PILLS
        placed = layout((header.x, header.y, header.width, header.height),
                        (region.x, region.y, region.width, region.height), obstacle_rects(area, window.as_pointer()),
                        {action: label_width(labels[action], scale) for action in PILLS}, scale, actions)
        if placed is None:
            _forget(pointer)
            return
        rects, labelled = placed
        kind = studio_toggle.kind_for(area.type, bpy.context.mode)
        places[pointer] = (window.as_pointer(), rects, labelled, kind)
        folded = collapsed(area)
        for action, rect in rects:
            _paint(rect, region, labels[action] if labelled and action != FOLD else None, action, scale,
                   (pointer, action) in _hover, (pointer, action) in _pressed, folded)
        hovered = next(((a, r) for a, r in rects if (pointer, a) in _hover), None)
        if hovered is not None:
            if _view is None:
                from . import card_host
                _view = card_host._View()
            from . import canvas_controls
            action, rect = hovered
            canvas_controls.tip(_view, (rect[0] - region.x, rect[1] - region.y, rect[2], rect[3]),
                                iface_(FOLD_TIPS[folded] if action == FOLD else TIPS[action]), scale, region.width,
                                region.height)
        draws += 1
        _ensure_input(window)
    except Exception as error:   # A failed pill never breaks the editor's own draw.
        last_error = str(error)
        print("Angelo: asset shelf pills draw:", error)
    finally:
        gpu.state.blend_set('NONE')


def _texture(action):
    """The pill's icon (premultiplied RGBA floats from ``studio_toggle.frame_icon`` / ``flow_open.flow_icon``) as a
    GPU texture."""
    texture = _icons.get(action)
    if texture is None:
        import gpu
        from . import flow_open, studio_toggle
        if action == "studio":
            size, pixels = studio_toggle.ICON_SIZE, studio_toggle.frame_icon(False, studio_toggle.ICON_SIZE)
        else:
            size, pixels = flow_open.ICON_SIZE, flow_open.flow_icon(flow_open.ICON_SIZE)
        texture = _icons[action] = gpu.types.GPUTexture(
            (size, size), format='RGBA16F', data=gpu.types.Buffer('FLOAT', size * size * 4, pixels))
    return texture


def _paint(rect, region, label, action, scale, hover, pressed, folded=False):
    import blf
    import gpu
    from gpu_extras.presets import draw_texture_2d
    from . import canvas_rtl, chat_view as canvas
    x, y, w, h = rect[0] - region.x, rect[1] - region.y, rect[2], rect[3]
    radius, edge = h / 2, max(1.0, scale)
    gpu.state.blend_set('ALPHA')
    canvas.shape(x, y - 2 * scale, w, h, SHADOW, radius)
    canvas.shape(x, y, w, h, kit.LINE, radius)
    canvas.shape(x + edge, y + edge, w - 2 * edge, h - 2 * edge, kit.PANEL_HOVER if hover else kit.PANEL,
                 radius - edge)
    if pressed:   # TEAL_SOFT over the fill: never teal-filled (teal fill is one primary action per surface).
        canvas.shape(x + edge, y + edge, w - 2 * edge, h - 2 * edge, kit.TEAL_SOFT, radius - edge)
    if action == FOLD:   # A chevron: up to expand a collapsed shelf, down to collapse it.
        cx, cy, arm, rise = x + w / 2, y + h / 2, 4.5 * scale, (2.5 if folded else -2.5) * scale
        canvas.stroke(cx - arm, cy - rise, cx, cy + rise, kit.TEXT, 1.6 * scale)
        canvas.stroke(cx, cy + rise, cx + arm, cy - rise, kit.TEXT, 1.6 * scale)
        return
    mirrored = canvas_rtl.mirrored()
    icon = ICON * scale
    if label is None:
        icon_x = x + (w - icon) / 2
    else:
        icon_x = x + w - PAD * scale - icon if mirrored else x + PAD * scale
    cy = y + h / 2
    gpu.state.blend_set('ALPHA_PREMULT')
    draw_texture_2d(_texture(action), (icon_x, cy - icon / 2), icon, icon)
    gpu.state.blend_set('ALPHA')
    if label is None:
        return
    font = canvas._font
    blf.size(font, TEXT_SIZE * scale)
    shaped = canvas_rtl.visual(label)
    text_w = label_width(label, scale)
    text_x = icon_x - GAP * scale - text_w if mirrored else icon_x + icon + GAP * scale
    baseline = cy - TEXT_SIZE * scale * 0.35   # About half the cap height below the centre line.
    blf.position(font, text_x, baseline, 0)
    blf.color(font, *kit.TEXT)
    blf.draw(font, shaped)


# -- state ----------------------------------------------------------------------------------------------------
def _area(pointer):
    for window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.as_pointer() == pointer:
            return window, area
    return None, None


def _redraw(pointer):
    area = _area(pointer)[1]
    if area is not None:
        area.tag_redraw()


def _forget(pointer):
    places.pop(pointer, None)
    for key in [k for k in _hover if k[0] == pointer]:
        _leave(key)
    for key in [k for k in _pressed if k[0] == pointer]:
        _pressed.discard(key)


def _leave(key):
    _hover.discard(key)
    window = _area(key[0])[0]
    if window is not None and not any(k[0] == key[0] for k in _hover):
        try:
            window.cursor_modal_restore()
        except (RuntimeError, TypeError):
            pass
    _redraw(key[0])


def _ensure_input(window):
    """Start this window's input handler after the draw (an operator never starts inside one)."""
    pointer = window.as_pointer()
    if pointer in _starting or input_policy.handler_running(window, IDENTITY):
        return
    _starting.add(pointer)

    def start(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        _starting.discard(pointer)
        if target is None or input_policy.handler_running(target, IDENTITY):
            return None
        if any(p[0] == pointer for p in places.values()):
            try:
                with bpy.context.temp_override(window=target):
                    bpy.ops.angelo.asset_shelf_add_input('INVOKE_DEFAULT')
            except RuntimeError as error:
                print("Angelo: asset shelf pills input:", error)
        return None
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, start), first_interval=0.0)


def pill_at(window_pointer, x, y):
    """``(area pointer, action)`` of the pill in this window holding window pixel (``x``, ``y``), else None; the
    shelf's empty-state button (``shelf_empty``) is the action "empty"."""
    for pointer, (window, rects, _labelled, _kind) in places.items():
        if window != window_pointer:
            continue
        for action, rect in rects:
            if rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]:
                return pointer, action
    pointer = shelf_empty.hit(window_pointer, x, y)
    return (pointer, "empty") if pointer is not None and pointer in places else None


def activate(key, window_pointer):
    """Open what a pill names, after this event (opening a window or switching a tab changes the screen)."""
    pointer, action = key
    _window, rects, _labelled, kind = places[pointer]

    def run(target):   # The live window (live_windows.deferred waits while it is between screens), or None: gone.
        window, area = _area(pointer)
        window = window or target
        if window is None:
            return None
        if action == FOLD:
            from . import media_shelf
            if area is not None:
                media_shelf.set_collapsed(window, area, not collapsed(area))
            return None
        if action == "flows":   # Routes by mode itself: Manual's Generation Flows tab, Director's Flows popup.
            opened.append("flows")
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.show_flows()
            return None
        from . import media_shelf
        tab = (media_shelf.shelf_kind(window, area) if area is not None else None) or kind
        opened.append(tab)
        with bpy.context.temp_override(window=window):   # The operator reports a failure in the status bar.
            bpy.ops.angelo.open_media_studio('EXEC_DEFAULT', kind=tab.upper())
        return None
    # settle: a pill opens Media Studio or the Flows; a window between screens could already hold it.
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, window_pointer, run, settle=True),
                            first_interval=0.01)


# -- input ----------------------------------------------------------------------------------------------------
class ANGELO_OT_asset_shelf_add_input(Operator):
    bl_idname = "angelo.asset_shelf_add_input"   # IDENTITY
    bl_label = "Media Studio"
    bl_description = "Media Studio and Generation Flows from this shelf"
    bl_options = {'INTERNAL'} | input_policy.modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    def invoke(self, context, event):
        self.window = context.window.as_pointer()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}
        window = context.window
        if window is None or window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        if not any(p[0] == self.window for p in places.values()):
            for key in [k for k in _hover if _area(k[0])[0] is window]:
                _leave(key)
            return {'FINISHED', 'PASS_THROUGH'}   # No pill in this window: whatever this event is, it is not ours.
        if input_policy.canvas_blocking(window):
            for key in list(_hover):
                _leave(key)
            return {'PASS_THROUGH'}   # A real tool holds the window.
        over = pill_at(self.window, event.mouse_x, event.mouse_y)
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            for key in [k for k in _hover if k != over and places.get(k[0], (None,))[0] == self.window]:
                _leave(key)
            hovered = {over[0]} if over is not None and over[1] == "empty" else set()
            if hovered != shelf_empty.hovered:
                for pointer in hovered | shelf_empty.hovered:
                    _redraw(pointer)
                shelf_empty.hovered.clear()
                shelf_empty.hovered.update(hovered)
            if over is not None and over not in _hover:
                _hover.add(over)
                try:
                    window.cursor_modal_set('HAND')
                except (RuntimeError, TypeError):
                    pass
                _redraw(over[0])
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE':
            if event.value in {'PRESS', 'DOUBLE_CLICK'} and over is not None:
                _pressed.add(over)
                _redraw(over[0])
                return {'RUNNING_MODAL'}   # Never a brush stroke or a selection behind a pill.
            if event.value == 'RELEASE' and _pressed:
                was = set(_pressed)
                _pressed.clear()
                for key in was:
                    _redraw(key[0])
                if over in was:
                    self.click(over)
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        if event.type == 'RIGHTMOUSE' and over is not None:
            return {'RUNNING_MODAL'}   # No context menu for what is behind a pill.
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS' and over is not None:
            self.click(over)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def click(self, key):
        _leave(key)
        activate(key, self.window)


@persistent
def _load_post(_):
    """A file load drops modal handlers and replaces windows and areas."""
    places.clear()
    _hover.clear()
    _pressed.clear()
    _starting.clear()


def register():
    bpy.utils.register_class(ANGELO_OT_asset_shelf_add_input)
    if not bpy.app.background:
        for name in ("SpaceView3D", "SpaceImageEditor"):
            space = getattr(bpy.types, name)
            _handlers.append((space, space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')))
    bpy.app.handlers.load_post.append(_load_post)


def unregister():
    global _view
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for key in list(_hover):
        _leave(key)
    for space, handler in _handlers:
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    _load_post(None)
    _view = None
    _icons.clear()
    bpy.utils.unregister_class(ANGELO_OT_asset_shelf_add_input)
