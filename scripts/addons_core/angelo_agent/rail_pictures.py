# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session rail's pictures: each session's current state, drawn by Angelo itself, never by a worker.

Policy (the parallel-agents design): workers stay GPU-free; Angelo draws every picture with its window's GPU, a
256 x 160 Solid draw of a worker's live part (``live_view``) framed on its contents, and of the lead's own scene as the
maintainer ruled (R3-M2, "Yes, camera too", ``lead_view``): through the scene camera only if the geometry fills
``FILL`` of a grid of ``CELLS`` over its frame, else through the artist's own view on the same terms (``shown_share``),
else framed on the geometry the scene shows (``_scene_bounds``). Only while the window shows that scene, else the tile
keeps its last picture (``lead_shown``, E2E-20 R5-M-1). A picture is redrawn when out of date
(``parallel_sessions.stale``): after its worker's part was read again, for the lead at most once a second after a
change its picture shows (``rail_changes``), or once the artist's view settles elsewhere (``_view_moved``). Always
Solid; drawn from a timer, never inside a region's draw, never while a tool or a job holds the window.

Each picture is a ``GPUOffScreen`` kept per session; ``texture`` hands the rail its colour texture.
"""

import math
import time

import bpy

from . import live_windows, rail_changes
from .rail_fill import CELLS, FILL, KEEP, KEEP_PX, _rectangle, coverage, fill, fitted, perspective  # noqa: F401

SIZE = (256, 160)
LEAD_INTERVAL = 1.0    # The lead's picture: at most once a second (its own scene changes with every edit), and no
LEAD_SHARE = 20.0      # sooner than this many times its recent draws (lower median of 3, none before two): a heavy
_lead_costs = []       # document's picture costs it <= 5%; one slow first draw (a shader compile) never paces it.
POLL = 0.25
RETRY = 0.5        # A picture that could not be drawn is tried again this much later, doubling while it keeps
RETRY_MAX = 8.0    # failing, up to this: never every tick, never given up (the maintainer's audit, e2e 34f111dfc68).
DIRECTION = (0.62, -0.72, 0.48)   # Where a framed part is seen from: front right, above (Blender's own default view).
FOV = math.radians(38.0)
STAGE_TICK = 0.01  # A lead redraw in progress runs its next stage this soon: each stage its own main-loop turn.

_offscreens = {}   # session id -> GPUOffScreen
_drawn = {}        # session id -> time.monotonic() of its last picture
_retry = {}        # session id -> (draws failed in a row, time.monotonic() before which it is not tried again)
_lead_keys = {}    # session id -> what decided its last picture's view (``_view_key``): drawn again when it changes.
_staged = {}       # session id -> (out of date since, its redraw's stages) while one runs (``refresh``).
_cameras = {}      # session id -> the scene camera its last picture was decided with (``KEEP`` keeps only that one).
_scratch = [None]  # The decision pictures' own offscreen: never a tile's, so no rejected view ever shows (VW11-M-1).
sources = {}       # Fixtures: session id -> how its last picture was framed ("camera", "view", "bounds" or "part").
draw_seconds = []  # Fixtures: each picture's draw, seconds; the newest 256.
draw_total = [0, 0.0]   # Every picture drawn this launch and their seconds, never capped (fixtures' totals).
last_error = None


def texture(session_id):
    offscreen = _offscreens.get(session_id)
    return offscreen.texture_color if offscreen is not None else None


def framing(bounds, aspect):
    """``(view_matrix, projection_matrix, eye, centre, distance)`` that frame world-space ``bounds``
    (``((min x, y, z), (max x, y, z))``, as ``live_view.bounds`` gives them)."""
    from mathutils import Matrix, Vector
    low, high = Vector(bounds[0]), Vector(bounds[1])
    centre = (low + high) / 2
    radius = max(1e-5, (high - low).length / 2)   # Any size: a 2 mm part fills its picture as a 2 km one does.
    direction = Vector(DIRECTION).normalized()
    distance = radius / math.sin(FOV / 2) * 1.08
    eye = centre + direction * distance
    rotation = (centre - eye).to_track_quat('-Z', 'Y').to_matrix().to_4x4()
    view = (Matrix.Translation(eye) @ rotation).inverted()
    return view, perspective(FOV, aspect, distance * 0.01, distance * 10.0), eye, centre, distance


def lead_view(window, area, region, box, shows=None, last=None):
    """``(view_matrix, projection_matrix, source)`` of the Main tile's picture of ``window``'s scene, showing ``box``,
    as the maintainer ruled (R3-M2, "Yes, camera too"): ``"camera"`` when the geometry fills ``FILL`` of the scene
    camera's frame, else ``"view"`` when it fills ``FILL`` of the artist's view (``area``), else ``"bounds"``. The
    picture decides (``shows``: ``fill`` of its geometry pixels; REVIEW-MAIN-THUMB-2 H1), after ``fills_at_most``
    rules a candidate out without one; decided afresh each redraw, at most two decision pictures. The source shown
    before (``last``) stays while its geometry grown by ``KEEP_PX`` fills ``KEEP``. All at once; the rail runs
    ``lead_choice`` a decision picture per main-loop turn."""
    choice = lead_choice(window, area, region, box, shows, last)
    while True:
        try:
            next(choice)
        except StopIteration as done:
            return done.value


def lead_choice(window, area, region, box, shows=None, last=None):
    """``lead_view`` as a generator: it yields before each decision picture, so a caller can draw each on a turn of
    its own, and returns ``(view_matrix, projection_matrix, source)``. The candidates are read now, from the window
    as it is; the generator holds only their matrices, never the window (C2's Windows crash, live_target)."""
    aspect = SIZE[0] / SIZE[1]
    scene = window.scene
    shows = (lambda view, projection: None) if shows is None else shows
    candidates = []
    camera = getattr(scene, "camera", None)
    if camera is not None:   # Any object the scene renders from: a camera's lens, a spot's cone, else Blender's own.
        render, depsgraph = scene.render, window.view_layer.depsgraph
        projection = camera.calc_matrix_camera(depsgraph, x=render.resolution_x, y=render.resolution_y,
                                               scale_x=render.pixel_aspect_x, scale_y=render.pixel_aspect_y)
        view = camera.evaluated_get(depsgraph).matrix_world.normalized().inverted()   # A mirrored one too.
        frame = render.resolution_x * render.pixel_aspect_x / max(1e-6, render.resolution_y * render.pixel_aspect_y)
        # Decided on its render frame (its decision picture stretches it over the picture's); shown fitted whole.
        candidates.append(("camera", view, projection, fitted(projection, frame, aspect)))
    found = _artist_region(area, region)
    if found is not None:
        rv3d, width, height = found
        projection = fitted(rv3d.window_matrix, width / height, aspect)
        candidates.append(("view", rv3d.view_matrix.copy(), projection, projection))
    return _choose(candidates, box, shows, last, aspect)


def _choose(candidates, box, shows, last, aspect):
    source = "bounds"
    for name, view, decide, shown in candidates:
        need = KEEP if name == last else FILL
        if fills_at_most(decide @ view, box) >= need:
            yield name
            share = shows(view, decide, KEEP_PX) if name == last else shows(view, decide)
            if share is None or share >= need:
                source = name
                break
    chosen = next((item for item in candidates if item[0] == source), None)
    return (chosen[1], chosen[3], source) if chosen is not None else framing(box, aspect)[:2] + ("bounds",)


def fills_at_most(matrix, box, size=SIZE):
    """``rail_fill.fills_at_most`` for a picture of ``size`` (the tile's by default)."""
    from . import rail_fill
    return rail_fill.fills_at_most(matrix, box, size)


def shown_share(target, offscreen, space, view, projection, view_layer, grow=0):
    """``fill`` of the geometry pixels when ``target`` is drawn into ``offscreen`` through ``view`` and
    ``projection`` with no background, its geometry opaque whatever the artist's X-ray or material alpha (any alpha
    marks geometry); None when this build cannot leave the background out."""
    from . import live_view
    try:
        live_view.native_draw(target, offscreen, space, view, projection, view_layer=view_layer, background=False)
    except TypeError as error:
        if "background" in str(error):
            return None   # A build without ``background``.
        raise
    pixels = offscreen.texture_color.read()
    try:
        import numpy
        return fill(numpy.asarray(pixels)[..., 3] > 0, grow)   # Geometry draws opaque, anti-aliased edges partly.
    except (ImportError, TypeError, ValueError, IndexError):
        return fill([[pixel[3] > 0 for pixel in row] for row in pixels.to_list()], grow)


def _artist_region(area, region):
    """``(region_3d, width, height)`` of the artist's own view in ``area`` (the camera region of a quad view), or
    None."""
    rv3d = getattr(area.spaces.active, "region_3d", None)
    if rv3d is None:
        return None
    pointer = rv3d.as_pointer()
    shown = next((r for r in area.regions if r.type == 'WINDOW' and getattr(r.data, "as_pointer", lambda: 0)() ==
                  pointer), region)
    return (rv3d, shown.width, shown.height) if shown.width > 0 and shown.height > 0 else None


def _view_key(window, area, region, source=None):
    """What decides the lead's view besides its scene's own data (``lead_view``): the scene camera's render frame, and
    unless the picture was drawn through that camera (``source``), the artist's view and its shape. Cheap: read on the
    rail's timer."""
    scene = window.scene
    camera = getattr(scene, "camera", None)
    key = ()
    if camera is not None:   # Its own transform and lens are scene data: a change marks the lead (``lead_changed``).
        render = scene.render
        key = ("camera", camera.as_pointer(), render.resolution_x, render.resolution_y, render.pixel_aspect_x,
               render.pixel_aspect_y)
        if source == "camera":
            return key
    shown = _artist_region(area, region)
    if shown is None:
        return key or None
    rv3d, width, height = shown
    return key + ("view", tuple(map(tuple, rv3d.perspective_matrix)), width, height)


def _host():
    """``(window, area, region)`` of the largest 3D view: what ``draw_view3d`` takes its settings from."""
    for window in live_windows.windows(bpy.context.window_manager):
        views = [area for area in live_windows.areas(window) if area.type == 'VIEW_3D']
        area = max(views, key=lambda item: item.width * item.height, default=None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        if region is not None:
            return window, area, region
    return None


def draw(session_id, view=None):
    """Draw one session's picture now (``view``: matrices; framed on its contents when None, a worker's part or the
    lead's scene, ``_scene_bounds``).
    True when one was drawn; False when there is nothing to draw (its picture is removed); None when it could not be
    drawn this time (no 3D view to draw with, or the draw raised): ``refresh`` keeps it out of date and tries again.
    All at once; ``refresh`` runs the lead's a stage a main-loop turn (``_stages``)."""
    stages = _stages(session_id, view)
    while True:
        try:
            next(stages)
        except StopIteration as done:
            return done.value


def _stages(session_id, view=None):
    """``draw`` as a generator yielding between the lead's stages, each on a main-loop turn of its own (one redraw
    was one 73-98 ms block at ~200k instances): its geometry box, each decision picture, the picture. A worker's
    picture, or one with ``view`` given, is one stage. After a yield the window, its 3D view, its scene and view layer
    are found live again by identity (``live_target``), else it was not drawn (None)."""
    global last_error
    import gpu
    from . import live_view, parallel_sessions
    host = _host()
    if host is None:
        return None
    window, area, region = host
    space = area.spaces.active
    worker = parallel_sessions.is_worker(session_id)
    target = live_view.part(session_id) if worker else window.scene
    if target is None:
        _free(session_id)
        _drawn[session_id] = time.monotonic()
        return False
    spent, started = 0.0, time.perf_counter()   # Its stages' own time is its picture's cost (the lead's pacing).
    # What the picture is drawn from is recorded now, with the box: a change during the stages is a change after it
    # (REVIEW-TILE-STAGES M1). The full view key; through the camera only its camera part counts (``_view_key``).
    full_key = None if worker else _view_key(window, area, region)
    if not worker:   # A change during the stages is then a change after this picture (REVIEW-TILE-STAGES-2 M).
        rail_changes.record(session_id, rail_changes.shown_state(getattr(window.view_layer, "depsgraph", None)))
    camera = None if worker else getattr(getattr(window.scene, "camera", None), "as_pointer", lambda: None)()
    found = None
    if view is None:
        found = live_view.bounds(session_id) if worker else _scene_bounds(window)
        if found is _UNREAD:
            return None
        if found is None:
            _free(session_id)
            _drawn[session_id] = time.monotonic()
            return False
    layer = None if worker else window.view_layer
    from . import live_target   # Across turns only identities are held; each resumed stage finds them live again.
    address = lambda value: getattr(value, "as_pointer", lambda: id(value))()   # noqa: E731
    held = (live_target.window_ref(window), address(area), address(region),
            live_target.scene_ref(target), live_target.view_layer_ref(layer) if layer is not None else (lambda: None))
    current = [(window, area, region, target, layer)]   # This turn's, refreshed after every yield before any read.

    def resolve():   # The same window, 3D view, scene and view layer as it began with, live now; else None.
        try:
            now, shown, scene, part = _host(), held[0](), held[3](), held[4]()
            if now is None or shown is None or now[0] != shown or address(now[1]) != held[1] or (
                    address(now[2]) != held[2]) or scene is None:
                return None
            if not worker and (shown.scene != scene or part is None or shown.view_layer != part):
                return None
            return now[0], now[1], now[2], scene, part
        except ReferenceError:
            return None

    def shows(matrix, projection, grow=0):   # Read from a private picture: the tile keeps its own (VW11-M-1).
        if _scratch[0] is None:
            _scratch[0] = gpu.types.GPUOffScreen(*SIZE)
        shown, at, inside, scene, part = current[0]
        with bpy.context.temp_override(window=shown, area=at, region=inside):
            return shown_share(scene, _scratch[0], at.spaces.active, matrix, projection, part, grow)
    try:
        if view is None and worker:
            view = framing(found, SIZE[0] / SIZE[1])[:2] + ("part",)
        elif view is None:
            last = sources.get(session_id)   # Kept only for the very camera it was earned with (-STAGES-2 L-b).
            last = None if last == "camera" and _cameras.get(session_id) != camera else last
            choice = lead_choice(window, area, region, found, shows=shows, last=last)
            while view is None:
                try:
                    next(choice)   # Draws the picture it stopped before (none at first), up to the next or done.
                except StopIteration as done:
                    view = done.value
                spent += time.perf_counter() - started
                yield   # The next decision picture, or once chosen the picture itself, on a turn of its own.
                started = time.perf_counter()
                current[0] = resolve()
                if current[0] is None:
                    return None   # Closed, replaced, or showing another scene: not drawn, tried again later.
                window, area, region, target, layer = current[0]
                space = area.spaces.active
        offscreen = _offscreens.get(session_id)   # The tile's picture: drawn only now, its last one shown till then.
        if offscreen is None:
            offscreen = _offscreens[session_id] = gpu.types.GPUOffScreen(*SIZE)
        with bpy.context.temp_override(window=window, area=area, region=region):
            live_view.native_draw(target, offscreen, space, view[0], view[1], view_layer=layer)
    except (RuntimeError, ReferenceError, ValueError, TypeError) as error:   # Nested or not evaluated: next time.
        last_error = str(error)
        return None
    key = None if worker else (full_key[:6] if (len(view) > 2 and view[2] == "camera" and full_key
                                                and full_key[0] == "camera") else full_key)
    spent += time.perf_counter() - started
    draw_seconds.append(spent)
    del draw_seconds[:-256]
    draw_total[0], draw_total[1] = draw_total[0] + 1, draw_total[1] + spent
    if worker:
        parallel_sessions.note_draw(session_id, spent)
    else:
        _lead_costs.append(spent)
        del _lead_costs[:-3]
        _lead_keys[session_id] = key
        _cameras[session_id] = camera
    sources[session_id] = view[2] if len(view) > 2 else None
    _drawn[session_id] = time.monotonic()
    return True


def refresh(now=None):
    """Redraw the pictures that are out of date (``parallel_sessions.stale``); returns the sessions drawn.

    The lead's redraw runs a stage a call (``_stages``, kept in ``_staged`` meanwhile; ``_tick`` calls again after
    ``STAGE_TICK`` while one runs), a worker's at once. A picture that could not be drawn (``draw`` None: no 3D view
    this moment, a draw that raised, or the lead's view not ready), the lead's or a worker's alike, stays out of date
    and is tried again after ``RETRY``, doubling to ``RETRY_MAX`` while it keeps failing; a draw that raises past
    ``draw`` stays out of date too. The maintainer's audit (e2e 34f111dfc68): only the lead's picture was tried
    again, so a worker's failed draw was lost until another save marked it stale."""
    from . import parallel_sessions, rail_redraw
    now = time.monotonic() if now is None else now
    drawn = []

    def failed(session_id, since):   # Not drawn: still out of date, tried again later.
        count = _retry.get(session_id, (0, 0.0))[0] + 1
        _retry[session_id] = (count, now + min(RETRY_MAX, RETRY * 2 ** (count - 1)))
        parallel_sessions.stale.setdefault(session_id, since)

    def run(session_id, since, stages):
        _staged.pop(session_id, None)
        try:
            next(stages)
        except StopIteration as done:
            outcome = done.value
        except BaseException:
            failed(session_id, since)
            raise
        else:
            _staged[session_id] = (since, stages)   # More stages: the next call goes on.
            return
        if outcome is None:
            failed(session_id, since)
        else:
            _retry.pop(session_id, None)
        if outcome:
            drawn.append(session_id)
    for session_id, (since, stages) in list(_staged.items()):
        run(session_id, since, stages)
    for session_id in list(parallel_sessions.stale):
        if session_id in _staged:
            continue   # Its redraw is running; out of date again meanwhile: drawn again after, at its pace.
        recent = rail_redraw.recent_cost(_lead_costs) if len(_lead_costs) >= 2 else 0.0
        if session_id == parallel_sessions.lead and now - _drawn.get(session_id, -math.inf) < max(
                LEAD_INTERVAL, LEAD_SHARE * recent):
            continue
        if now < _retry.get(session_id, (0, -math.inf))[1]:
            continue   # It failed a moment ago: tried again at its time, never every tick.
        if session_id == parallel_sessions.lead and not lead_shown(session_id):
            continue   # Still out of date: drawn once its own scene is shown again, its last picture kept meanwhile.
        since = parallel_sessions.stale.pop(session_id, now)
        if session_id == parallel_sessions.lead:
            run(session_id, since, _stages(session_id))
        else:
            run(session_id, since, iter(_whole(session_id)))
    if drawn:
        parallel_sessions._bump()
    return drawn


def _whole(session_id):
    """A worker's picture as one stage (``_stages`` without a yield)."""
    yield from ()
    return draw(session_id)


def lead_shown(session_id, host=None):
    """Whether the window the pictures draw with shows the lead's own scene (``parallel_sessions._document_scene``):
    the lead's picture draws that window's scene, so while it shows another the lead keeps its last picture."""
    from . import parallel_sessions
    host = _host() if host is None else host
    if host is None:
        return True   # No 3D view to draw with: ``draw`` says so, and the picture is retried (``refresh``).
    scene = parallel_sessions._document_scene(parallel_sessions.lead_of(session_id))
    return scene is not None and scene == host[0].scene


_UNREAD = object()
_scene_boxes = {}   # depsgraph pointer -> (its update count, the box its scene shows): read again after a change only.


def _scene_bounds(window):
    """The world box of what the lead's scene shows, as a worker's part is framed: geometry as evaluated, instances
    included, helpers left out (a scene of only helpers has no picture), never a camera, light or empty
    (``_bpy._angelo_live.depsgraph_bounds``, the rule the parts' sizes use); None when it shows none; ``_UNREAD`` while
    its depsgraph is not
    evaluated since its last change (the picture is tried again)."""
    import _bpy
    live = getattr(_bpy, "_angelo_live", None)
    depsgraph = window.view_layer.depsgraph
    count = live.update_count(depsgraph) if hasattr(live, "update_count") else None
    if count is None or not hasattr(live, "depsgraph_bounds"):
        return _UNREAD
    key = depsgraph.as_pointer()
    kept = _scene_boxes.get(key)
    if kept is not None and kept[0] == count:
        return kept[1]
    box = live.depsgraph_bounds(depsgraph, False)   # Helpers alone only as a Solid draw shows them (no wire guide).
    if len(_scene_boxes) > 8:
        _scene_boxes.clear()
    _scene_boxes[key] = (count, box)
    return box


def _tick():
    from . import parallel_sessions, rail_redraw
    if not parallel_sessions.sessions():
        _unstage()   # The rail is gone: a redraw left running would resume on old inputs (-STAGES L2), still owed.
        return None
    try:
        held = parallel_sessions._held_now()   # A tool, a navigation or a job: pictures and redraws wait too.
        if not held:
            _view_moved()
        if not held:
            if rail_redraw.panels_visible():
                refresh()   # Shared pictures remain needed if any other window still shows its panel.
            else:
                _unstage()   # No tile shows: keep the work stale, without polling half-finished draws.
    except Exception as error:   # noqa: BLE001 - a timer that raises is dropped; the rail keeps its pictures.
        held = False
        _unstage()   # Still out of date (-STAGES M2).
        print("Angelo: rail pictures:", error)
    if not held:   # Even after a picture failed: the tiles' words and states still reach the screen (P1-9 Q15).
        rail_redraw.tick()   # What the rail shows changed (a picture, a state, the clock): drawn again.
    return STAGE_TICK if _staged and not held else POLL   # A hold pauses a redraw at the timer's pace (L3).


def _unstage():
    """Drop the redraws left running, each still out of date (REVIEW-TILE-STAGES M2, -2 L-a)."""
    from . import parallel_sessions
    for session_id, (since, _stages_left) in list(_staged.items()):
        parallel_sessions.stale.setdefault(session_id, since)
    _staged.clear()


def _view_moved():
    """The lead's picture is out of date once what decides its view changed since it was drawn (``_view_key``): the
    artist's view settled somewhere else while the scene camera does not decide, or the scene camera or its frame
    changed.
    Read only after navigation ends (``_tick`` waits while the window is held)."""
    from . import parallel_sessions
    lead = parallel_sessions.lead
    if lead is None or lead in parallel_sessions.stale or lead not in _lead_keys or lead in _staged:
        return   # A redraw running compares its own key when it ends (REVIEW-TILE-STAGES H1).
    host = _host()
    if host is not None and lead_shown(lead, host) and _view_key(*host, sources.get(lead)) != _lead_keys[lead]:
        parallel_sessions.stale.setdefault(lead, time.monotonic())


def ensure_timer():
    if not bpy.app.background and not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0)


def _free(session_id):
    offscreen = _offscreens.pop(session_id, None)
    if offscreen is not None:
        offscreen.free()


def forget(session_id):
    _free(session_id)
    _drawn.pop(session_id, None)
    _retry.pop(session_id, None)
    _lead_keys.pop(session_id, None)
    sources.pop(session_id, None)


def release():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for session_id in list(_offscreens):
        _free(session_id)
    if _scratch[0] is not None:
        _scratch[0].free()
        _scratch[0] = None
    _drawn.clear()
    _retry.clear()
    _staged.clear()
    _cameras.clear()
    rail_changes.forget()
    _lead_costs.clear()
    _scene_boxes.clear()
    _lead_keys.clear()
    sources.clear()
