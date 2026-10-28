# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live view: a worker's part full screen in the 3D view, looked at with a view of its own.

When the active session is a worker's (``parallel_sessions``), the window's largest 3D view shows that worker's live
part (``live_view``: a private Main of its own) over itself, in every 3D-view region of the area (quad view's four
too), always in Solid with the artist's Solid settings and a grid floor. The view is the session's own
(``live_nav``): the artist orbits, pans and zooms it with their own gestures, and the artist's 3D view, its matrices
and its saved state are never touched (break-it P1-2 M3, M8). The 3D view beneath never takes input while the live
view shows: its input is held natively (``live_view.hold``, ``wm_angelo_input_hold.cc``), so whatever the input
handler (``session_rail_input``) hands on never reaches it (P1-2 H1, P1-3 H1, H2, M3). The hold is a lease the watch
renews (``renew``), and the live view is drawn only while it holds: what the artist sees is where input goes. A region's picture is drawn again only when its view,
size, Solid settings or the part's newest read changed, and otherwise copied; the draw only reads (a part is
evaluated when it is read).

**Why the window's scene does not switch:** every tool of Angelo's own chat reads the window's scene (111 places
read ``window.scene``), so a window showing a worker's scene would point the lead's reads and edits at the wrong
document, and a scene switch resets the view's render engines and runs frame changes each time.

The hidden 3D view keeps its own view and shading; since nothing moves it, Blender redraws it from what it already
finished (a still view). A window capture of the lead's (``view_editor``) is taken with the live view and the rail
hidden (``hidden``), drawn again into an offscreen and read from there (``live_view.window_pixels``: Blender's
window screenshot reads the last presented frame on macOS and Windows, P1-3 M1), so the lead sees its own document. A click on the part names what is under the pointer.
"""

import contextlib
import time

import bpy

from . import live_nav, live_windows

_offscreens = {}    # region pointer -> (GPUOffScreen, key of the picture it holds)
navigation = live_nav.Sessions()   # Each worker session's own view, and the drag in progress.
shown = None        # The session the 3D view showed at the last ``follow`` (None: the main one).
inspected = None    # (session, object name, type label, region x, region y) of the last click on a live view.
draws = []          # Fixtures: (seconds, redrawn) of each live-view draw; bounded.
notice_until = 0.0  # Until when the read-only notice shows (a held edit asked for it).
NOTICE_SECONDS = 2.5
_notice_timer = []  # The one pending redraw that ends the notice (never one per held key).
_hidden = [0]       # Nesting depth of ``hidden``: nothing of the live view or rail is drawn meanwhile.
HOLD_SECONDS = 2.0  # The input hold's lease: the watch renews it twice a second.


def active_worker():
    """The worker session the active chat is (its live view shows), or None."""
    from . import parallel_sessions
    active = parallel_sessions._active()
    return active if parallel_sessions.is_worker(active) else None


def is_hidden():
    return _hidden[0] > 0


@contextlib.contextmanager
def hidden(window):
    """Nothing of the live view or the rail draws meanwhile: a capture drawn inside (``live_view.window_pixels``)
    shows the lead's own document; the window shows them again afterwards."""
    _hidden[0] += 1
    try:
        yield
    finally:
        _hidden[0] -= 1
        for area in live_windows.areas(window):
            area.tag_redraw()


def capture(window):
    """``(width, height, RGBA bytes bottom row first)`` of ``window`` as the lead's own document shows it."""
    from . import live_view
    with hidden(window), bpy.context.temp_override(window=window):
        return live_view.window_pixels(window)


def holding(window, area):
    """Whether ``area`` holds its input now (a build without the hold never does: no live view there)."""
    from . import live_view
    try:
        return live_view.held(window, area)
    except (ImportError, AttributeError):
        return False


def renew(window):
    """Hold the input of the 3D view the live view covers, or release the window's hold when none shows."""
    from . import live_view
    area = target(window)
    showing = area is not None and shown is not None and shown == active_worker()
    try:
        live_view.hold(window, area if showing else None, HOLD_SECONDS)
    except (ImportError, AttributeError, RuntimeError, TypeError) as error:
        _note_hold_error(error)   # No hold: ``holding`` is False, so no live view is drawn or takes input.


_hold_error = [None]


def _note_hold_error(error):
    if _hold_error[0] != repr(error):
        _hold_error[0] = repr(error)
        print("Angelo: live view input hold:", error)


def target(window):
    """The 3D view a live view and the rail stand in: the window's largest."""
    views_3d = [area for area in live_windows.areas(window) if area.type == 'VIEW_3D']
    return max(views_3d, key=lambda area: area.width * area.height, default=None)


def view_of(session_id):
    """The session's own view; framed on the part while the artist has not moved it."""
    view = navigation.views.get(session_id)
    if view is None:
        view = navigation.views[session_id] = live_nav.View(rotation=tuple(live_nav.default_rotation()))
    return view


def _shape():
    """``(aspect, lens)`` of the main region of the 3D view a live view stands in (the main window's first)."""
    from . import session_rail_input
    for window in live_windows.windows(bpy.context.window_manager):
        area = target(window)
        region = session_rail_input.rail_region(area) if area is not None else None
        if region is not None:
            return max(1, region.width) / max(1, region.height), getattr(area.spaces.active, "lens", 50.0)
    return 1.6, 50.0


def refit(session_id, aspect=None, lens=None):
    """Frame a session's view on its part again unless the artist moved it (after each read; Home): for the
    region it shows in, unless told."""
    from . import live_view
    found = live_view.bounds(session_id)
    view = view_of(session_id)
    if found is not None:
        shape = _shape() if aspect is None or lens is None else (aspect, lens)
        aspect, lens = aspect or shape[0], lens or shape[1]
        auto = view.auto
        live_nav.frame(view, found, aspect, lens)
        view.auto = auto


def follow(window):
    """Called when the active session may have changed (the rail's watch): the view to show switches."""
    global shown
    area = target(window)
    if area is None:
        return
    now = active_worker()
    if now == shown:
        return
    if now is not None and view_of(now).auto:
        refit(now)
    shown = now
    navigation.drag = live_nav.Drag()   # A drag never outlives the view it moved (break-it P1-3 M2).
    renew(window)
    _free_offscreens()
    area.tag_redraw()


def matrices(session_id, space, region):
    settings = live_nav.settings_from(bpy.context.preferences, space)
    view = view_of(session_id)
    return live_nav.view_matrix(view), live_nav.projection_matrix(view, settings, region.width, region.height)


def _solid_key(space):
    shading = space.shading
    return (shading.light, shading.color_type, shading.single_color[:], shading.studio_light, shading.show_xray,
            shading.show_shadows, shading.show_cavity, shading.show_object_outline, space.lens, space.clip_start,
            space.clip_end)


def draw():
    """POST_PIXEL of every 3D view: the live view, in each region of the target view, while a worker is active. A draw
    that fails covers the view with an opaque card: never the document whose input is held (break-it P1-5 L3)."""
    try:
        _draw()
    except Exception as error:   # noqa: BLE001 - printed once per kind; the region is covered instead.
        from . import card_kit as kit, chat_view
        region = bpy.context.region
        if region is not None and shown is not None:
            chat_view.shape(0, 0, region.width, region.height, kit.CARD)
        _note_hold_error(error)


def _draw():
    session_id = active_worker()
    area, region, window = bpy.context.area, bpy.context.region, bpy.context.window
    if session_id is None or area is None or region is None or window is None or is_hidden():
        return
    if not _showing(window, area, session_id):
        return
    import gpu
    from gpu_extras.presets import draw_texture_2d
    from . import card_kit as kit, chat_view, live_view
    found = live_view.part(session_id)
    started = time.perf_counter()
    redrawn = False
    gpu.state.blend_set('NONE')
    gpu.state.depth_mask_set(False)
    if found is None:
        chat_view.shape(0, 0, region.width, region.height, kit.CARD)   # Not read yet: never the document behind.
    else:
        space = area.spaces.active
        view, projection = matrices(session_id, space, region)
        key = (session_id, region.width, region.height, tuple(map(tuple, view)), tuple(map(tuple, projection)),
               _solid_key(space), live_view.revision(session_id))
        pointer = region.as_pointer()
        kept = _offscreens.get(pointer)
        if kept is None or kept[1] != key:
            offscreen = kept[0] if kept is not None and (kept[0].width, kept[0].height) == (
                region.width, region.height) else None
            if offscreen is None:
                if kept is not None:
                    kept[0].free()
                offscreen = gpu.types.GPUOffScreen(region.width, region.height)
            began = time.perf_counter()
            live_view.native_draw(found, offscreen, space, view, projection, grid=True)
            if kept is None or kept[1][-1] != key[-1]:   # The first picture of a new read: part of its cost.
                from . import parallel_sessions
                parallel_sessions.note_draw(session_id, time.perf_counter() - began)
            _offscreens[pointer] = (offscreen, key)
            redrawn = True
        draw_texture_2d(_offscreens[pointer][0].texture_color, (0, 0), region.width, region.height)
    draws.append((time.perf_counter() - started, redrawn))
    del draws[:-512]
    _draw_chip(area, region, session_id)


def _free_offscreens():
    for offscreen, _key in _offscreens.values():
        offscreen.free()
    _offscreens.clear()


def _showing(window, area, session_id):
    """Whether ``area`` of ``window`` shows ``session_id``'s live view now (what ``_draw`` draws)."""
    if target(window) != area or shown != session_id:
        return False   # The watch has not switched the views yet: one frame of the document, never another's view.
    return holding(window, area)   # Its input not held (the lease lapsed): the document the input reaches shows.


def _chip(area, region, session_id, widest=False):
    """``(line, rect)`` of the live view's label: its words and where it stands (``live_view_label.rect``);
    ``widest``: the rect of the widest words it can show for this worker (what stays clear of it, so nothing moves
    as its words change: nothing saved yet, saved, the read-only notice)."""
    from . import chat_view, live_view, live_view_label, parallel_sessions, session_rail, ui_tokens as tk
    from .ui_text import iface_
    scale = chat_view.ui_scale()
    name = parallel_sessions.names.get(session_id) or iface_("Worker", "Angelo")
    lines = (iface_("Live view · {name} · nothing saved yet", name=name),
             iface_("Read-only: {name} edits its part. Ask it in its chat.", name=name),
             iface_("Live view · {name} · Solid, simplified · look only, steer it in its chat", name=name))
    if live_view.part(session_id) is None:
        line = lines[0]
    elif time.monotonic() < notice_until:
        line = lines[1]
    else:
        line = lines[2]
    width = max(chat_view.measure(words, tk.TYPE_SMALL * scale, scale=1.0) for words in (lines if widest else (line,)))
    top = region.height - session_rail.top_inset(area.regions, region)
    return line, live_view_label.rect(region.width, top, width + 28 * scale, scale)


def chip_rect(window, area, region):
    """Where the live view's label stands in ``region`` while it shows there, at its widest words, else None: the
    Sessions panel and its round button keep clear of it, read in the same frame as it is drawn (never last frame's:
    an idle view draws once)."""
    session_id = active_worker()
    if session_id is None or is_hidden() or not _showing(window, area, session_id):
        return None
    return _chip(area, region, session_id, widest=True)[1]


def _draw_chip(area, region, session_id):
    """The live view's own label at the top, below any header drawn over the view: whose part this is and that it is
    read-only; the inspected object."""
    import gpu
    from . import card_kit as kit, chat_view, ui_tokens as tk
    from .ui_text import iface_
    scale = chat_view.ui_scale()
    size = tk.TYPE_SMALL * scale
    line, (x, y, width, height) = _chip(area, region, session_id)
    gpu.state.blend_set('ALPHA')
    chat_view.shape(x, y, width, height, tk.TIP_FILL, height / 2)
    chat_view.shape(x + 12 * scale, y + height / 2 - 3 * scale, 6 * scale, 6 * scale, kit.TEAL, 3 * scale)
    chat_view.text(line, x + 22 * scale, y + 8 * scale, size, tk.TIP_TEXT)
    if inspected is not None and inspected[0] == session_id:
        _session, label, kind, px, py = inspected
        text = iface_("{name} · {kind}", name=label, kind=kind) if kind else label
        w = chat_view.measure(text, size, scale=1.0) + 20 * scale
        chat_view.shape(px + 12 * scale, py - 30 * scale, w, 24 * scale, tk.TIP_FILL, 8 * scale)
        chat_view.text(text, px + 22 * scale, py - 23 * scale, size, tk.TIP_TEXT)
    gpu.state.blend_set('NONE')


def inspect(session_id, area, region, x, y):
    """Name the object under region pixel (x, y) of a live view (a ray into the part); None when none."""
    global inspected
    from mathutils import Vector
    from . import live_view
    view, projection = matrices(session_id, area.spaces.active, region)
    inverse = (projection @ view).inverted()
    nx, ny = 2.0 * x / max(1, region.width) - 1.0, 2.0 * y / max(1, region.height) - 1.0
    near, far = inverse @ Vector((nx, ny, -1.0, 1.0)), inverse @ Vector((nx, ny, 1.0, 1.0))
    near, far = near.xyz / near.w, far.xyz / far.w
    hit = live_view.ray_cast(session_id, near, (far - near).normalized())
    inspected = (session_id, hit[0], _type_name(hit[1]), x, y) if hit is not None else None
    area.tag_redraw()
    return inspected


def _type_name(value):
    """An object type's name as Blender's interface shows it (translated), from its enum value."""
    from .ui_text import iface_
    for item in bpy.types.Object.bl_rna.properties["type"].enum_items:
        if item.value == value:
            return iface_(item.name)
    return ""


def held(area):
    """An edit was held back over the live view: say, for a moment, that it is read-only."""
    global notice_until
    notice_until = time.monotonic() + NOTICE_SECONDS
    area.tag_redraw()
    if _notice_timer:
        return   # Its redraw is already set; the notice just lasts longer.
    pointer = area.as_pointer()   # Never the area itself: it may be gone when the timer runs.

    def redraw():
        if time.monotonic() < notice_until:
            return notice_until - time.monotonic() + 0.05
        _notice_timer.clear()
        for _window, item in live_windows.window_areas(bpy.context.window_manager):
            if item.as_pointer() == pointer:
                item.tag_redraw()
        return None
    _notice_timer.append(pointer)
    bpy.app.timers.register(redraw, first_interval=NOTICE_SECONDS + 0.05)


def forget(session_id):
    navigation.views.pop(session_id, None)


def release():
    global shown, inspected
    from . import live_view
    try:
        live_view.release_holds()
    except (ImportError, AttributeError):
        pass
    _free_offscreens()
    _notice_timer.clear()
    shown = inspected = None
    navigation.views.clear()
    navigation.drag = live_nav.Drag()
