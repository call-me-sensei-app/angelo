# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the session rail (``session_rail``) over the left of the full-screen 3D view, in the card_kit language.

A rail panel (the card colour, an edge line, the cards' radius) holds one tile per session: the session's picture
with rounded corners (``rail_pictures``), its role chip (Main, or the part a worker builds), its name, and its
status line with a coloured dot (teal working, amber waiting for the artist, coral failed, grey ended or idle). The
active session's tile is ringed in teal; the pointer over a tile lifts it. A pending approval also shows as an amber
pill on the picture. GPU only, inside the 3D view's POST_PIXEL callback; tile states are read from each chat
(``states``), never written.
"""

import time

import bpy

from . import card_kit as kit, session_rail as rail, ui_tokens as tk

DOT = {rail.WORKING: kit.TEAL, rail.MERGING: kit.TEAL, rail.MERGED: kit.TEAL, rail.DONE: kit.TEAL,
       rail.APPROVAL: kit.AMBER, rail.FAILED: tk.CORAL, rail.ENDED: kit.MUTED, rail.REPLIED: kit.MUTED,
       rail.IDLE: kit.LINE_HOVER, rail.QUEUED: kit.LINE_HOVER, rail.STARTING: kit.TEAL}
PANEL_FILL = (*kit.CARD[:3], 0.94)
_image_shader = None
PAGING = ("scroll", "page")   # Hit actions that move a paged rail: an arrow's step, a "+N more" line's page.
_view = None       # The tooltip's view (card_host._View), made at its first tip.
last = {}          # window pointer -> (area pointer, Layout) as last drawn (input and fixtures read it).
hover = {}         # window pointer -> session id under the pointer
pressed = {}       # window pointer -> (action, value) pressed: released on the same, it acts (P1-18 L8)
text_kept = {}     # area pointer -> (its text settings, the deepest text inset since they changed): rail.steady_inset
scroll = {}        # window pointer -> (first a paged rail shows, the active worker last brought into view, paged)
pan = {}           # window pointer -> trackpad travel not yet turned into a step (``scroll_pan``)
last_error = None


def states(now=None):
    """Every tile's ``session_rail.Tile`` from the chats as they are now, the lead first; then each part not yet bound
    to a chat, queued or starting (``worker_queue``)."""
    from . import live_progress, parallel_sessions, ui, worker_queue
    from .chat_titles import session_title
    from .ui_text import iface_, join
    now = time.monotonic() if now is None else now
    active = parallel_sessions._active()
    found = []
    sessions = parallel_sessions.sessions()
    for index, session_id in enumerate(rail.ordered(sessions, parallel_sessions.is_worker,
                                                    lambda s: _worker_number(s, len(sessions)))):
        controller = ui._controllers.get(session_id)
        record = (ui._sessions.records.get(session_id) if ui._sessions is not None else None) or {
            "title": "", "id": session_id}
        worker = parallel_sessions.is_worker(session_id)
        approval = bool(controller and any(getattr(call, "approval", None) for call in controller.pending.values()))
        busy = bool(controller and working(controller))
        end = turn_end(controller, reply=False)[0] if controller is not None else ""
        failed = bool(controller and not busy and (_last_failed(controller) or end in {"failed", "not_sent"}))
        replied = end == "completed"   # A stopped turn reads idle, never "Finished its turn" (P1-20 L3).
        planned = parallel_sessions.progress_of(session_id) if worker else None
        finished = bool(planned) and planned[0] >= planned[1]   # Every step of its own plan reported done.
        gone = worker and parallel_sessions.ended(session_id)   # Closable even when its turn failed (audit 3).
        kind = rail.tile_kind(busy, approval, gone, failed, merging=parallel_sessions.is_merging(session_id),
                              merged=parallel_sessions.is_merged(session_id),
                              done=worker and end == "completed" and finished,
                              replied=worker and replied)
        back = worker_queue.resuming_kind(session_id) if worker else None   # A parked chat's worker coming back.
        kind = back[0] if back else kind
        label = clock = ""
        if kind == rail.WORKING:
            live = live_progress.live(live_progress.facts(controller), now)
            label, clock = live.label, live.clock
        # The chip says which session it is; the title what it is about: the part a worker builds (its chat's own
        # title is its brief's first words), the lead's chat title. The number is the one status and merge use.
        number = _worker_number(session_id, index) if worker else None
        role = iface_("Worker {number}", number=number) if worker else iface_("Main", "Angelo")
        name = parallel_sessions.names.get(session_id) if worker else None
        title = name or (session_title(record) if record.get("title") else role)
        step = parallel_sessions.current_step(session_id) if worker else ""
        head, keep = rail.status_parts(kind, label, clock, step, progress=planned)
        keep = rail.queue_text(back[1]) if back else keep
        status = rail.status(kind, label, clock, step, progress=planned)
        if kind == rail.DONE and _checking(session_id):   # Not mergeable until its part's count is known (A').
            keep = iface_("Checking the saved part")
            status = join([head, keep])
        found.append(rail.Tile(session_id, title, role, status, kind,
                               active=session_id == active,
                               progress=planned if kind in {rail.WORKING, rail.APPROVAL, rail.DONE} else None,
                               status_head=head, status_keep=keep, step=step,
                               closable=bool(gone or parallel_sessions.is_merged(session_id))))
    for part in worker_queue.parts_waiting():   # No chat yet: nothing to switch to, nothing to close.
        head, keep = rail._state_words(part["kind"]), rail.queue_text(part["position"])
        found.append(rail.Tile(part["key"], part["name"], iface_("Worker {number}", number=part["number"]),
                               join([text for text in (head, keep) if text]), part["kind"], status_head=head,
                               status_keep=keep))
    return found


def _checking(session_id):
    """A worker's saved part whose object count is still being asked of its worker (``worker_part_counts``)."""
    from . import worker_part_counts, worker_sessions
    part = getattr(worker_sessions, "_parts", {}).get(session_id)
    return part is not None and worker_part_counts.checking(part[2])


def _worker_number(session_id, index):
    """The worker's number from its launch (``worker_sessions``: kept whatever starts or ends later), else its place."""
    from . import worker_sessions
    found = getattr(worker_sessions, "_parts", {}).get(session_id)
    return found[0] if found else index


def working(controller):
    """A chat still at it: busy, a call pending, or its lane still running a commit, as ``worker_sessions.finished``
    reads a worker (break-it P1-19 L4: a queued follow-up or a commit after Stop read as finished)."""
    from . import worker_dispatch
    lane = worker_dispatch.lane(controller, create=False)
    return bool(controller.busy or controller.pending or (lane is not None and (lane.actions or lane.busy)))


def turn_end(controller, reply=True):
    """``rail.turn_end`` of a chat: its rows, whether it works, and how its last turn ended as recorded then."""
    return rail.turn_end(controller.state.get("messages"), working(controller),
                         getattr(controller, "turn_outcome", None), reply)


def _last_failed(controller):
    messages = controller.state.get("messages") or ()
    return bool(messages) and messages[-1].get("role") == "error"


def draw():
    """POST_PIXEL of every 3D view: the rail in the window's target view while concurrent sessions exist."""
    global last_error
    from . import live_view_draw, parallel_sessions
    area, region, window = bpy.context.area, bpy.context.region, bpy.context.window
    if area is None or region is None or window is None or not parallel_sessions.sessions():
        return
    if live_view_draw.is_hidden():
        return   # A capture of the lead's own window (live_view_draw.hidden).
    from . import session_rail_controls
    from .session_rail_input import rail_region
    circle = session_rail_controls.minimized()
    if live_view_draw.target(window) != area or (circle.region_of(area) if circle else rail_region(area)) != region:
        return   # One rail per window, in its view's main region (the top-left one in quad view; minimized, the
        # top-right one, rail_minimized.region_of).
    try:
        from . import rail_redraw
        version, tiles = parallel_sessions.version, states()
        try:
            _draw(window, area, region, tiles)
        finally:   # Layout decides whether this window shows tiles or only the cramped button.
            rail_redraw.note_drawn(window.as_pointer(), version, tiles, area.as_pointer())
    except Exception as error:   # noqa: BLE001 - a failing tile never takes the 3D view down; said once.
        if last_error != str(error):
            last_error = str(error)
            print("Angelo: session rail:", error)


def _draw(window, area, region, tiles):
    """The rail where the person keeps it (``parallel_setting.rail_position``: the column on the left, or a strip
    across the top or bottom, ``session_rail_strip``), or, minimized, only its round button (``rail_minimized``)."""
    from . import chat_view, live_view_draw, parallel_setting, session_rail_controls
    from . import session_rail_place, session_rail_strip
    scale = chat_view.ui_scale()
    key, drawn = window.as_pointer(), {tile.session for tile in tiles}   # A tile gone holds nothing (P1-16 L3).
    label = live_view_draw.chip_rect(window, area, region)   # The live view's label while it shows, if any.
    if session_rail_controls.minimized() is not None:   # Nothing of the panel shows or takes a click.
        _circle(window, area, region, tiles, scale, label, False)
        return
    arrows = rail.control_keys((last.get(key) or (None, None))[1])   # Its buttons' hover holds it too (P1-19 L2),
    press = pressed.get(key) or (None, None)                         # as their press does.
    held = hover.get(key) in drawn or hover.get(key) in arrows or press[1] in drawn or press[0] in PAGING
    held = held or press[0] == "minimize"
    text = _steady_text(window, area, region, held)
    first, shown, paged = scroll.get(key, (0, None, False))
    active = next((index for index, tile in enumerate(tiles[1:]) if tile.active), None)
    active_id = tiles[1 + active].session if active is not None else None
    keep = rail.keep_in_view(active, active_id, shown, paged)
    position = parallel_setting.rail_position()
    if position in session_rail_strip.SIDES:   # Every position clears one obstacle set (session_rail_place).
        plan = session_rail_place.strip(window, area, region, position, tiles, scale, text, first, keep,
                                        _more_width(len(tiles)))
    else:
        plan = session_rail_place.column(window, area, region, tiles, scale, text, first, keep, label)
    if plan is None:   # No readable panel fits this view (session_rail_strip.readable): its round button stands in.
        _circle(window, area, region, tiles, scale, label, True)
        return
    from . import rail_minimized
    rail_minimized.cramp(key, False)
    rail_minimized.leave(window)   # The cramped button disappeared: release its cursor and any unfinished press.
    scroll[key] = (plan.first, active_id, plan.paged)
    last[key] = (area.as_pointer(), plan)
    if plan.tiles:
        _paint(plan, tiles, key, region)


def _circle(window, area, region, tiles, scale, label, cramped):
    """The panel's round button (``rail_minimized``) in its stead: minimized, or ``cramped`` (no room for a readable
    panel); clear of the live view's ``label``, Angelo in the corner and the shelf's pills. No tile takes a click."""
    from . import parallel_sessions, rail_minimized, session_rail_place
    last[window.as_pointer()] = (area.as_pointer(), rail.Layout(scale=scale))
    workers, waiting = rail_minimized.counts(tiles, parallel_sessions.lead)   # The workers, Main excluded.
    avoid = tuple(rect for rect in (label, session_rail_place.angelo(window, area, region),
                                    *session_rail_place.pill_rects(window, area, region)) if rect)
    rail_minimized.draw(window, area, region, workers, waiting, scale, avoid=avoid, cramped=cramped)


def _paint(plan, tiles, pointer, region):
    """A laid-out rail at its own scale (``session_rail.fit``): the panel, its header (label, session count, arrows,
    minimize), its "+N more" lines and its tiles."""
    import gpu
    from . import chat_view
    from .ui_text import iface_
    scale = plan.scale
    gpu.state.blend_set('ALPHA')
    panel = plan.panel
    chat_view.shape(panel[0], panel[1] - 2 * scale, panel[2], panel[3], (0, 0, 0, 0.22), tk.RADIUS_CARD * scale)
    chat_view.shape(*panel, PANEL_FILL, tk.RADIUS_CARD * scale)
    _ring(panel, tk.RADIUS_CARD * scale, max(1.0, scale), kit.EDGE)
    size = tk.TYPE_SMALL * scale
    chat_view.text(iface_("Sessions", "Angelo"), plan.header_at[0], plan.header_at[1], size, kit.MUTED)
    count = str(len(tiles))   # Every session, a paged rail's hidden ones too (P1-18 M3).
    width = chat_view.measure(count, size, scale=1.0)
    hx = plan.header_end - width - 12 * scale
    chat_view.shape(hx, plan.header_at[1] - 4 * scale, width + 12 * scale, 18 * scale, kit.LINE, 9 * scale)
    chat_view.text(count, hx + 6 * scale, plan.header_at[1], size, kit.TEXT)
    horizontal = plan.orientation == "HORIZONTAL"
    for rect, step, more, waiting in zip(plan.arrows or (), (-1, 1), plan.hidden, plan.attention):
        _arrow(rect, step, scale, more > 0, waiting, hover.get(pointer) == step, horizontal)
    ways = ("LEFT", "RIGHT") if horizontal else ("UP", "DOWN")
    for side, rect in enumerate(plan.more):
        if rect is not None:
            step = (-1, 1)[side] * max(1, plan.page)
            _more(rect, plan.hidden[side], plan.attention[side], scale, hover.get(pointer) == ("page", step),
                  pressed.get(pointer) == ("page", step), ways[side])
    for tile in plan.tiles:
        _tile(tile, scale, hover.get(pointer) == tile.session, pressed.get(pointer) == ("switch", tile.session))
    _minimize(plan, pointer, region)
    gpu.state.blend_set('NONE')


def _more_width(count):
    """A strip's "+N more" cell, interface pixels at a scale of 1: its words in this language for the most that can
    be hidden (every session), its padding and amber dot, so the cell never cuts them and keeps its width."""
    from . import chat_view
    from .ui_text import iface_
    return chat_view.measure(iface_("+{count} more", "Angelo", count=count), tk.TYPE_SMALL, scale=1.0) + 26


def _more(rect, count, waiting, scale, hovered, down, way):
    """A "+N more" row (VISUAL-SPEC): how many workers are hidden that way after a chevron pointing there, a 6 px amber
    dot after the words when one of them waits for approval; lifted under the pointer, where a click moves a page. A
    column's row runs across it; a strip's cell stands at its end, the words under the chevron."""
    from . import chat_view, rail_glyph
    from .ui_text import iface_
    x, y, w, h = rect
    if hovered or down:
        chat_view.shape(x, y, w, h, kit.LINE if down else kit.PANEL_HOVER, tk.RADIUS_CARD * scale / 2)
    color, size = kit.TEXT if hovered else kit.MUTED, tk.TYPE_SMALL * scale
    words = iface_("+{count} more", "Angelo", count=count)
    width, glyph, dot = chat_view.measure(words, size, scale=1.0), 8 * scale, 6 * scale
    trail = dot + 4 * scale if waiting else 0
    if way in {"UP", "DOWN"}:
        start = x + (w - glyph - 6 * scale - width - trail) / 2
        rail_glyph.chevron(start + glyph / 2, y + h / 2, glyph, color, way)
        tx, ty = start + glyph + 6 * scale, y + h / 2 - 4 * scale
    else:
        rail_glyph.chevron(x + w / 2, y + h / 2 + 8 * scale, glyph, color, way)
        tx, ty = x + (w - width - trail) / 2, y + h / 2 - 14 * scale
    chat_view.text(words, tx, ty, size, color)
    if waiting:
        chat_view.shape(tx + width + 4 * scale, ty + scale, dot, dot, kit.AMBER, dot / 2)


def _minimize(plan, pointer, region):
    """The header's minimize button (VISUAL-SPEC): ``rail_glyph.minimize``, an arrow into the top-right corner where
    the panel goes, lifted under the pointer as the arrows are, with its tooltip."""
    global _view
    from . import chat_view, rail_glyph
    from .ui_text import iface_
    if plan.minimize is None:
        return
    scale, (x, y, w, h) = plan.scale, plan.minimize
    hovered, down = hover.get(pointer) == rail.MINIMIZE_KEY, pressed.get(pointer) == ("minimize", None)
    if hovered or down:
        chat_view.shape(x, y, w, h, kit.LINE_PRESSED if down else kit.PANEL_HOVER, w / 2)
    rail_glyph.minimize(x + w / 2, y + h / 2, 10 * scale, kit.TEXT)
    if hovered:
        from . import canvas_controls, card_host
        _view = _view or card_host._View()
        canvas_controls.tip(_view, plan.minimize, iface_("Minimize the Sessions panel"), scale, region.width,
                            region.height)


def _arrow(rect, step, scale, enabled, waiting, hovered, horizontal=False):
    """A paged rail's arrow: up (``step`` -1) or down, left or right on a strip; dim when nothing is hidden that way,
    an amber dot when a hidden worker there waits for approval."""
    from . import chat_view
    x, y, w, h = rect
    if hovered and enabled:
        chat_view.shape(x, y, w, h, kit.PANEL_HOVER, w / 2)
    color = kit.TEXT if enabled else kit.LINE_HOVER
    from . import rail_glyph
    rail_glyph.chevron(x + w / 2, y + h / 2, 8 * scale, color, (("LEFT", "RIGHT") if horizontal else ("UP", "DOWN"))[
        step > 0])
    if waiting:
        chat_view.shape(x + w - 6 * scale, y + h - 6 * scale, 6 * scale, 6 * scale, kit.AMBER, 3 * scale)


def scroll_by(window_pointer, step):
    """A paged rail shows the sessions ``step`` further down (up when negative); the layout keeps it in range. The
    input handler then ends the hover (the tile under the pointer changed) and its cursor."""
    first, shown, paged = scroll.get(window_pointer, (0, None, False))
    scroll[window_pointer] = (max(0, first + step), shown, paged)


def scroll_pan(window_pointer, travel, unit):
    """Trackpad travel over a paged rail, in pixels (positive: towards later workers), a step per ``unit``
    (``rail.pan_steps``)."""
    steps, pan[window_pointer] = rail.pan_steps(pan.get(window_pointer, 0.0), travel, unit)
    if steps:
        scroll_by(window_pointer, steps)
    return steps


def _tile(tile, scale, hovered, down):
    import gpu
    from . import chat_view, rail_glyph, rail_pictures
    x, y, w, h = tile.rect
    radius = tk.RADIUS_PANEL * scale
    fill = kit.PANEL_HOVER if hovered and not down else kit.LINE_PRESSED if down else kit.PANEL
    chat_view.shape(x, y, w, h, fill, radius)
    if tile.active:
        _ring(tile.rect, radius, 2 * scale, kit.TEAL)
    elif hovered:
        _ring(tile.rect, radius, max(1.0, scale), kit.LINE_HOVER)
    if tile.picture is not None:
        px, py, pw, ph = tile.picture
        texture = rail_pictures.texture(tile.session)
        if texture is not None:
            _image(texture, tile.picture, tk.RADIUS_CONTROL * scale)
        else:
            chat_view.shape(px, py, pw, ph, kit.CARD, tk.RADIUS_CONTROL * scale)
            from .ui_text import iface_
            words = (iface_("Building…") if tile.kind == rail.WORKING else iface_("Not started yet")
                     if tile.kind in {rail.QUEUED, rail.STARTING} else iface_("Nothing built yet"))
            size, line_y = tk.TYPE_MICRO * scale, rail_glyph.placeholder_y(tile.picture, tile.badge, scale)
            if line_y is not None:   # Only where it clears the tile's chips: a short picture shows them alone.
                chat_view.text(words, px + (pw - chat_view.measure(words, size, scale=1.0)) / 2, line_y, size,
                               kit.MUTED)
        gpu.state.blend_set('ALPHA')
        _chip(tile.role, px + 6 * scale, py + 6 * scale, scale, kit.TEAL if tile.active else kit.TEXT)
        if tile.badge is not None:
            from .ui_text import iface_
            bx, by, bw, bh = tile.badge
            words = iface_("Approval", "Angelo")
            bw = min(bw, chat_view.measure(words, tk.TYPE_MICRO * scale, scale=1.0) + 16 * scale)
            chat_view.shape(bx, by, bw, bh, kit.AMBER, bh / 2)
            chat_view.text(words, bx + 8 * scale, by + 6 * scale, tk.TYPE_MICRO * scale, kit.INK)
    if tile.close is not None:
        cx, cy, cw, ch = tile.close
        chat_view.shape(cx, cy, cw, ch, (*kit.INK[:3], 0.78), cw / 2)
        mid_x, mid_y, arm = cx + cw / 2, cy + ch / 2, cw * 0.2
        chat_view.stroke(mid_x - arm, mid_y - arm, mid_x + arm, mid_y + arm, kit.TEXT, max(1.0, 1.5 * scale))
        chat_view.stroke(mid_x - arm, mid_y + arm, mid_x + arm, mid_y - arm, kit.TEXT, max(1.0, 1.5 * scale))
    size = tk.TYPE_BODY * scale
    title_w = w - 20 * scale
    chat_view.text(_elide(tile.title, title_w, size), tile.title_at[0], tile.title_at[1], size, kit.TEXT)
    sx, sy = tile.status_at
    chat_view.shape(sx - 12 * scale, sy + 2 * scale, 6 * scale, 6 * scale, DOT.get(tile.kind, kit.MUTED), 3 * scale)
    small = tk.TYPE_SMALL * scale
    lines = status_lines(tile, x + w - sx - 8 * scale, small)
    for (lx, ly), line in zip((tile.status_at, tile.detail_at), lines):
        chat_view.text(line, lx, ly, small, kit.AMBER if tile.kind == rail.APPROVAL else kit.MUTED)
    share = rail.fraction(tile.progress)
    if tile.bar is not None and share is not None:   # The worker's own plan, counted as it reports each step.
        bx, by, bw, bh = tile.bar
        chat_view.shape(bx, by, bw, bh, kit.LINE, bh / 2)
        if share > 0:
            chat_view.shape(bx, by, max(bh, bw * share), bh, kit.TEAL, bh / 2)


def _text_end(region):
    """Where the view's own text info ended in this draw (``rail.text_inset``): Blender draws it before the region's
    POST_PIXEL callbacks, so this is the frame being drawn (a region at a freed one's address reads the freed one's
    value only before its own first draw, which this never is). A build without it (an add-on on an older app) places
    the rail below the headers alone rather than draw no rail (break-it P1-14 V3, V4)."""
    import _bpy
    text_info_end = getattr(getattr(_bpy, "_angelo_live", None), "text_info_end", None)   # P1-15 L8.
    return text_info_end(region) if text_info_end is not None else None


def _text_settings(window, area):
    """What sets how much text the view draws at its top-left, never what the text says: the overlays (the floor and
    axes bring the grid unit), the preferences that add lines, playback (its FPS line), the mode (its statistics),
    quad view, shading and engine (an engine's own lines), where the text starts (headers, region overlap) and the
    interface's scale. A setting missed here only leaves a gap below the text until another one changes."""
    space, preferences = area.spaces.active, bpy.context.preferences
    overlay, view, system = space.overlay, preferences.view, preferences.system
    styles = preferences.ui_styles
    playing = window.screen is not None and window.screen.is_animation_playing
    return (overlay.show_overlays, overlay.show_text, overlay.show_stats, overlay.show_performance, overlay.show_floor,
            overlay.show_axis_x, overlay.show_axis_y, overlay.show_axis_z, view.show_view_name, view.show_object_info,
            view.show_playback_fps, playing, bpy.context.mode, len(space.region_quadviews),
            space.shading.type, window.scene.render.engine, space.show_region_header, space.show_region_tool_header,
            system.use_region_overlap, system.ui_scale, styles[0].widget.points if len(styles) else 0)


def _steady_text(window, area, region, held):
    """How far down the view's text info reaches, held steady (``rail.steady_inset``). While the live view shows, the
    document's text is covered but its room is kept, so the rail stands still across a switch."""
    if len(text_kept) > 64:   # Areas closed since: each draw writes its own again.
        text_kept.clear()
    kept = rail.steady_inset(text_kept.get(area.as_pointer()), _text_settings(window, area),
                             rail.text_inset(region.height, _text_end(region)), held)
    text_kept[area.as_pointer()] = kept
    return kept[1]


def status_lines(tile, width, size):
    """The tile's status in ``width``: what the session does, then its counts and clock on a line of their own when
    it has them; each cut to the width on its own, so a long step title never hides the counts."""
    if not tile.status_keep:
        return [_elide(tile.status, width, size)]
    return [_elide(tile.status_head, width, size), _elide(tile.status_keep, width, size)]


def _elide(value, width, size):
    from . import chat_view
    if chat_view.measure(value, size, scale=1.0) <= width:
        return value
    while value and chat_view.measure(value + "…", size, scale=1.0) > width:
        value = value[:-1]
    return value + "…"


def _chip(words, x, y, scale, color):
    from . import chat_view
    size = tk.TYPE_MICRO * scale
    width = chat_view.measure(words, size, scale=1.0) + 14 * scale
    chat_view.shape(x, y, width, 18 * scale, (*kit.INK[:3], 0.78), 9 * scale)
    chat_view.text(words, x + 7 * scale, y + 5 * scale, size, color)


def _ring(rect, radius, thickness, color):
    from . import ui_kit
    ui_kit.outline(rect, radius, thickness, color)


def _image(texture, rect, radius):
    """``texture`` in ``rect`` with rounded corners: the rounded rectangle's own outline, textured."""
    global _image_shader
    import gpu
    from gpu_extras.batch import batch_for_shader
    from . import canvas_controls
    x, y, w, h = rect
    outline = canvas_controls._contour(x, y, w, h, radius)
    points = [(x + w / 2, y + h / 2)] + [(px, py) for px, py, *_rest in outline]
    points.append(points[1])
    coords = [((px - x) / w, (py - y) / h) for px, py in points]
    if _image_shader is None:
        _image_shader = gpu.shader.from_builtin('IMAGE')
    batch = batch_for_shader(_image_shader, 'TRI_FAN', {"pos": points, "texCoord": coords})
    gpu.state.blend_set('ALPHA_PREMULT')
    _image_shader.bind()
    _image_shader.uniform_sampler("image", texture)
    batch.draw(_image_shader)
    gpu.state.blend_set('ALPHA')
    _ring(rect, radius, 1.0, kit.EDGE)
