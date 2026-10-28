# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rail at its real maximum, and the Main tile's picture, in the real window (GUI; no draw is forced).

The maintainer (2026-10-25): the rail is a scrollable container when many workers do not fit, and the Main tile's
picture frames what the lead's scene shows (it drew the artist's own view, so a merged desk and chair were a dot and a
scene of a camera and a light an empty frame: Director capture, Sol desk_chair on 9060a812b84).

* The Main picture, on scenes of every kind it must frame (regression cases written with the fix, not blind ones): only
  a camera and a light (no picture: the tile says so), a desk and a chair, a 2 mm part a kilometre from the origin, a
  500 m slab, a collection instance whose source collection is excluded (instances only), only a wire guide (no picture:
  a Solid picture draws none), and only a model hidden from renders (a Solid picture draws it), all seen from one
  artist's view far off (``ARTIST``: the bounds decide); then the maintainer's ruling (R3-M2, 2026-10-25 16:55, and for
  the camera "Yes, camera too": the scene camera only if the geometry fills a reasonable part of its frame, the artist's
  view only if it fills a reasonable part of that, ``rail_pictures.FILL``, otherwise the geometry bounds; the picture
  decides) on its own cases: the artist's view at a working distance, a scene camera aimed away from the geometry (it
  falls through: to the bounds from a far view, to the artist's view once that view moves near, drawn again by itself),
  a scene camera aimed at the geometry from afar (it falls through) and up close, a camera inside a room, the artist's
  view looking at empty space, and a large ground plane under a small model seen up close and with the view at the sky.
  A picture framed on the bounds passes when what it shows reaches across at least ``FILL`` of the picture's width or
  height (a dot does not); one through the artist's view when it shows geometry; the source each used is recorded
  (``rail_pictures.sources``).
* 16 workers, Workers at once's maximum, with the lead first, at 1x and 2x interface scale in the window the run gives
  (the pa-p1 slot launches 1280 x 720): the rail pages inside the view with Main pinned first (or, where only one
  tile fits, paged over with the rest) and says how many are hidden (arrows), its panel is one tile column wide
  whatever the count (the big view keeps the rest), the wheel over it scrolls, and a switch to a hidden worker
  brings it into view.

With ``ANGELO_CROWD_DIRECTOR=1`` the same checks run in Director Mode, the view people get by default (entered as the
default startup enters it, ``angelo_default_layout.open_default``, without the first-launch intro), shots named apart.

Stand-in workers: chats bound as worker sessions with no worker process and no provider (the rail draws the same tiles
from the same state). Screenshots of every state. GUI ``--factory-startup --enable-event-simulate``, disposable profile.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

assert '--factory-startup' in sys.argv and '--enable-event-simulate' in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-rail-crowd'))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1')
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
import addon_utils  # noqa: E402
addon_utils.enable('angelo_agent', default_set=False, persistent=False)
from angelo_agent import (connect_hero, live_view, live_view_draw, parallel_access, parallel_sessions,  # noqa: E402
                          rail_pictures, session_rail, session_rail_draw, session_rail_input)

WORKERS = 16
DIRECTOR = os.environ.get('ANGELO_CROWD_DIRECTOR') == '1'
TAG = '-director' if DIRECTOR else ''
FILL = 0.4      # A framed picture's content reaches across at least this much of its width or height.
SETTLE = 1.5    # Seconds for the product to draw a change by itself.
bpy.context.preferences.view.show_splash = False
state = {'phase': 'boot', 'started': time.monotonic(), 'at': None, 'done': False, 'scenes': None, 'scales': None}
report = {'checks': {}, 'readings': {}, 'shots': [], 'binary': bpy.app.binary_path}
window = lead = None
workers = []


def check(name, condition, detail=None):
    report['checks'][name] = bool(condition)
    if detail is not None:
        report['readings'][name] = detail


def screenshot(name):
    from angelo_agent import view_editor
    width, height, pixels = live_view.window_pixels(window)
    name = name.replace('.png', TAG + '.png')
    (OUT / name).write_bytes(view_editor.encode_png(pixels, width, height, bottom_up=True))
    report['shots'].append(name)


def region():
    return next(r for r in live_view_draw.target(window).regions if r.type == 'WINDOW')


def plan():
    placed = session_rail_draw.last.get(window.as_pointer())
    return placed[1] if placed else None


def redraw():
    for area in window.screen.areas:
        area.tag_redraw()


def bind(count):
    """``count`` more stand-in workers bound to the rail, each with a plan of its own on its tile."""
    for _ in range(count):
        chat = ui.create_session()
        workers.append(chat.state['id'])
    ui.show_session(lead.state['id'])
    for number, session in enumerate(workers, start=1):
        if session in parallel_sessions.sessions():
            continue
        parallel_sessions.bound(ui._controllers[session], SimpleNamespace(worker=None, part_layout=None))
        parallel_sessions.names[session] = 'Part %d' % number
        parallel_sessions.note_progress(session, number % 4, ['Block out', 'Shape', 'Detail', 'Finish'])
    rail_pictures.ensure_timer()


def clear_scene():
    scene = window.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj)
    for collection in list(scene.collection.children):
        bpy.data.collections.remove(collection)


def box(name, size, location=(0.0, 0.0, 0.0), collection=None, display='SOLID'):
    """A box of ``size`` metres at ``location``, built from data (no operator context in a timer)."""
    sx, sy, sz = (v / 2 for v in size)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x * sx, y * sy, z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.display_type = display
    (collection or window.scene.collection).objects.link(obj)
    return obj


def camera_and_light():
    scene = window.scene
    camera = bpy.data.objects.new('Crowd Camera', bpy.data.cameras.new('Crowd Camera'))
    camera.location = (30.0, -30.0, 20.0)
    light = bpy.data.objects.new('Crowd Light', bpy.data.lights.new('Crowd Light', 'SUN'))
    light.location = (-20.0, 10.0, 40.0)
    scene.collection.objects.link(camera)
    scene.collection.objects.link(light)


def instances_only():
    """A collection instance of a collection excluded from the view layer: only its instances show."""
    source = bpy.data.collections.new('Crowd Source')
    window.scene.collection.children.link(source)
    box('Crowd Tile', (1.0, 1.0, 0.2), collection=source)
    window.view_layer.layer_collection.children[source.name].exclude = True
    for index in range(5):
        empty = bpy.data.objects.new('Crowd Tile Copy %d' % index, None)
        empty.instance_type = 'COLLECTION'
        empty.instance_collection = source
        empty.location = (40.0 + index * 1.5, 7.0, 0.0)
        window.scene.collection.objects.link(empty)


def scene_camera(location, target, lens=50.0):
    from mathutils import Vector
    camera = bpy.data.objects.new('Crowd Scene Camera', bpy.data.cameras.new('Crowd Scene Camera'))
    camera.data.lens = lens
    camera.location = location
    camera.rotation_euler = (Vector(target) - Vector(location)).to_track_quat('-Z', 'Y').to_euler()
    window.scene.collection.objects.link(camera)
    window.scene.camera = camera


def desk_and_chair():
    box('Crowd Desk', (1.6, 0.8, 0.75), (0.0, 0.0, 0.375))
    box('Crowd Chair', (0.5, 0.5, 1.0), (0.0, -0.9, 0.5))


def room():
    for name, size, at in (('Floor', (6, 6, .1), (0, 0, -.05)), ('Ceiling', (6, 6, .1), (0, 0, 3.05)),
                           ('Wall N', (6, .1, 3), (0, 3.05, 1.5)), ('Wall S', (6, .1, 3), (0, -3.05, 1.5)),
                           ('Wall E', (.1, 6, 3), (3.05, 0, 1.5)), ('Wall W', (.1, 6, 3), (-3.05, 0, 1.5))):
        box('Crowd ' + name, size, at)
    box('Crowd Room Desk', (1.6, 0.8, 0.75), (0.0, 1.5, 0.375))


def ground_and_model():
    box('Crowd Ground', (200.0, 200.0, 0.02), (0.0, 0.0, -0.01))
    box('Crowd Small Model', (0.3, 0.3, 0.3), (0.0, 0.0, 0.15))


def look(eye, target, measure=False):
    """The artist's own view (the 3D view the pictures draw with) from ``eye`` at ``target`` (its region draws the new
    matrices; ``RegionView3D.update`` needs a GPU context); ``measure``: only how far the drawn view's eye is from
    ``eye`` (a reading: the view was set as meant)."""
    from mathutils import Vector
    rv3d = rail_pictures._host()[1].spaces.active.region_3d
    eye, target = Vector(eye), Vector(target)
    if measure:
        return round((rv3d.view_matrix.inverted().translation - eye).length, 4)
    rv3d.view_perspective = 'PERSP'
    rv3d.view_location = target
    rv3d.view_rotation = (eye - target).to_track_quat('Z', 'Y')   # View to world, as the property reads it.
    rv3d.view_distance = (eye - target).length
    return None


ARTIST = ((12.0, -12.0, 8.0), (0.0, 0.0, 0.0))   # One artist's view for the earlier scenes: front right, above.
NEAR = ((2.4, -2.6, 1.8), (0.0, -0.3, 0.5))      # The desk and chair at a working distance.
AWAY = ((0.0, -6.0, 1.2), (0.0, -20.0, 1.2))     # A scene camera aimed away from the desk and chair.
SCENES = (   # (name, builder, a picture expected, the artist's (eye, target), the source the ruling picks or None[,
    #          then: the artist's view moved to (eye, target) with nothing marking the picture, and the source then])
    ('camera_and_light_only', lambda: None, False, ARTIST, None),
    ('desk_and_chair', desk_and_chair, True, ARTIST, 'bounds'),
    ('desk_and_chair_at_a_working_distance', desk_and_chair, True, NEAR, 'view'),
    ('two_millimetres_a_kilometre_away', lambda: box('Crowd Bead', (0.002, 0.002, 0.002), (1000.0, 1000.0, 0.0)), True,
     ARTIST, None),
    ('five_hundred_metre_slab', lambda: box('Crowd Slab', (500.0, 500.0, 2.0), (0.0, 0.0, -1.0)), True, ARTIST, None),
    ('instances_only', instances_only, True, ARTIST, None),
    ('only_a_wire_guide', lambda: box('Crowd Guide', (3.0, 3.0, 3.0), display='WIRE'), False, ARTIST, None),
    ('only_a_model_hidden_from_renders', lambda: setattr(box('Crowd Mockup', (2.0, 1.0, 1.0)), 'hide_render', True),
     True, ARTIST, None),   # A helper too, but a Solid picture draws it.
    ('camera_aimed_away', lambda: (desk_and_chair(), scene_camera(*AWAY)), True, ARTIST, 'bounds', (NEAR, 'view')),
    ('camera_aimed_at_the_desk_from_afar', lambda: (desk_and_chair(), scene_camera((0.0, -40.0, 1.2), (0.0, 0.0, 0.5))),
     True, ARTIST, 'bounds'),   # "Yes, camera too" (the maintainer): it sees the desk, too small to fill FILL.
    ('camera_aimed_at_the_desk_up_close', lambda: (desk_and_chair(), scene_camera((0.0, -4.0, 1.2), (0.0, 0.0, 0.5))),
     True, ARTIST, 'camera'),
    ('camera_inside_a_room', lambda: (room(), scene_camera((0.0, -2.2, 1.5), (0.0, 3.0, 1.2), 24.0)), True, ARTIST,
     'camera'),
    ('view_looking_at_empty_space', desk_and_chair, True, ((0.0, -6.0, 1.5), (0.0, -20.0, 1.5)), 'bounds'),
    ('ground_plane_small_model_up_close', ground_and_model, True, ((0.8, -0.8, 0.6), (0.0, 0.0, 0.15)), 'view'),
    ('ground_plane_small_model_view_at_the_sky', ground_and_model, True, ((0.0, -0.01, 2.0), (0.0, 0.0, 10.0)),
     'bounds'),
)


def picture_reach():
    """How far what the Main picture shows reaches across it: the larger of its width and height fraction, against
    the picture's own corner (its background)."""
    offscreen = rail_pictures._offscreens.get(lead.state['id'])
    if offscreen is None:
        return None
    pixels = offscreen.texture_color.read().to_list()
    height, width = len(pixels), len(pixels[0])
    corner = pixels[0][0]
    step = 0.06 * (255 if max(corner[:3] + pixels[height // 2][width // 2][:3]) > 1.5 else 1)   # 8-bit or float.
    rows = [y for y in range(height) if any(max(abs(a - b) for a, b in zip(pixels[y][x][:3], corner[:3])) > step
                                            for x in range(0, width, 2))]
    columns = [x for x in range(width) if any(max(abs(a - b) for a, b in zip(pixels[y][x][:3], corner[:3])) > step
                                              for y in range(0, height, 2))]
    if not rows or not columns:
        return 0.0
    return max((columns[-1] - columns[0] + 1) / width, (rows[-1] - rows[0] + 1) / height)


def enter_director():
    """Director Mode as the default startup enters it; True once it is in place (``report['director']``)."""
    from angelo_agent import director, director_layout
    if not qa_boot.started(window):   # The start-up's layout, and Director's own once asked for, settle first.
        return False
    if not state.get('director_asked'):
        director.request_mode(window, 'DIRECTOR', persist=False)
        state['director_asked'] = True
        return False
    director_layout.configure(window)
    report['director'] = director_layout.is_director(window)
    return True


def tick():
    global window, lead
    try:
        now = time.monotonic()
        if now - state['started'] > 200:
            return finish('cap reached in %s' % state['phase'])
        if state['phase'] == 'boot':
            if not qa_boot.started() or ui.main_window() is None:
                return .2
            window = ui.main_window()
            report['window'] = [window.width, window.height]
            connect_hero._dismissed = True   # No account here: the empty scene explored, so the view shows the rail.
            redraw()
            if DIRECTOR and not enter_director():
                return .2
            check('parallel_edit_is_on_here', parallel_access.enabled(), 'needs this machine above the bar')
            if not parallel_access.enabled():
                return finish('parallel edit is off on this machine')
            lead = ui.create_session()
            bind(2)
            state['phase'], state['scenes'] = 'pictures', list(SCENES)
            return .5
        if state['phase'] == 'pictures':
            return pictures(now)
        if state['phase'] == 'crowd':
            return crowd(now)
        return .2
    except Exception:   # noqa: BLE001 - recorded, the run fails.
        return finish(traceback.format_exc())


def pictures(now):
    session = lead.state['id']
    if state['at'] is None:
        name, build, _expected, view, _source = state['scenes'][0][:5]
        if build is None:   # The artist's view moves; nothing else marks the picture (``rail_pictures._view_moved``).
            look(*view)
            redraw()
            state['at'] = now
            return .2
        clear_scene()
        camera_and_light()
        build()
        look(*view)
        window.view_layer.update()
        rail_pictures._drawn.pop(session, None)   # Its once-a-second pacing: this change is drawn now.
        parallel_sessions.stale[session] = now
        redraw()
        state['at'] = now
        return .2
    moved = state['scenes'][0][1] is None   # Its pacing: at most once a second, after the timer sees the move.
    if now - state['at'] < SETTLE * (2 if moved else 1) or (session in parallel_sessions.stale and
                                                             now - state['at'] < 6):
        return .2
    entry = state['scenes'].pop(0)
    name, _build, expected, view, source = entry[:5]
    if len(entry) > 5:
        state['scenes'].insert(0, (name + '_then_the_view_moves_near', None, expected) + entry[5])
    reach, drawn = picture_reach(), rail_pictures.sources.get(session)
    report['readings']['main_source_' + name] = drawn
    report['readings']['artist_eye_error_' + name] = look(*view, measure=True)
    if not expected:
        check('main_picture_is_empty_for_' + name, rail_pictures.texture(session) is None, reach)
    elif source is not None:
        check('main_picture_source_' + name, drawn == source, drawn)
    if expected and drawn == 'bounds':
        check('main_picture_frames_' + name, reach is not None and reach >= FILL, reach)
    elif expected and drawn == 'view':   # What the artist sees: geometry, at whatever size it is seen.
        check('main_picture_shows_the_view_' + name, bool(reach), reach)
    elif expected and name == 'camera_inside_a_room':
        check('main_picture_frames_' + name, reach is not None and reach >= FILL, reach)
    elif expected and drawn == 'camera':   # What the camera sees: geometry, at whatever size it sees it.
        check('main_picture_shows_the_camera_' + name, bool(reach), reach)
    screenshot('main-%s.png' % name)
    state['at'] = None
    if not state['scenes']:
        bind(WORKERS - len(workers))
        state['phase'], state['scales'] = 'crowd', [(1.0, 'layout'), (2.0, 'layout')]
    return .2


def crowd(now):
    preferences = bpy.context.preferences.view
    scale, stage = state['scales'][0]
    tag = '%gx' % scale
    if state['at'] is None:
        if stage == 'layout':
            state.setdefault('base_scale', preferences.ui_scale)
            preferences.ui_scale = scale
        elif stage == 'wheel':
            x, y, w, h = plan().tiles[-1].rect
            r = region()
            window.event_simulate(type='WHEELDOWNMOUSE', value='PRESS', x=int(r.x + x + w / 2), y=int(r.y + y + h / 2))
        elif stage == 'switch':
            session_rail_input.switch(workers[-1], window)
        redraw()
        state['at'] = now
        return .2
    if now - state['at'] < SETTLE:
        return .2
    state['at'] = None
    placed, r = plan(), region()
    sessions = len(parallel_sessions.sessions())
    shown = [t.session for t in placed.tiles] if placed else []
    if stage == 'layout':
        width = (session_rail.WIDTH + 2 * session_rail.PAD) * placed.scale
        check('%s_rail_pages_with_%d_workers' % (tag, WORKERS), placed.paged, [len(shown), sessions])
        # Main stays pinned first while two tiles fit, with or without the "+N more" rows (session_rail.page, pinned
        # by test_native_session_rail_strip); a rail with room for one pages over every session, Main too (break-it
        # P1-18 L2): then Main is the tile shown or among those hidden before it. Which holds is the window's: at 2x
        # the Mac 1 slot's 564 px view held two, the T4's 581 px one, under the asset shelf's pills (68 px), one.
        one = len(shown) == 1
        check('%s_main_is_pinned_first' % tag, shown[:1] == [lead.state['id']] or (one and placed.hidden[0] > 0),
              [shown[:1], "one tile fits" if one else "tiles fit", placed.hidden, r.height])
        check('%s_the_hidden_are_counted_and_have_arrows' % tag, placed.arrows is not None and
              sum(placed.hidden) == sessions - len(shown), [placed.hidden, len(shown), sessions])
        check('%s_panel_is_inside_the_view' % tag, placed.panel[1] >= 0 and placed.panel[1] + placed.panel[3] <=
              r.height, [placed.panel, r.height])
        check('%s_panel_is_one_column_wide_whatever_the_count' % tag, abs(placed.panel[2] - width) < 1.0,
              [placed.panel[2], width, r.width])
        state['first'] = placed.first
        screenshot('crowd-%s.png' % tag)
        state['scales'][0] = (scale, 'wheel')
    elif stage == 'wheel':
        check('%s_the_wheel_scrolls_the_rail' % tag, placed.first > state['first'], [state['first'], placed.first])
        screenshot('crowd-%s-wheel.png' % tag)
        state['scales'][0] = (scale, 'switch')
    else:
        check('%s_a_switch_brings_a_hidden_worker_into_view' % tag, workers[-1] in shown, shown)
        screenshot('crowd-%s-switch.png' % tag)
        session_rail_input.switch(lead.state['id'], window)
        state['scales'].pop(0)
        if not state['scales']:
            preferences.ui_scale = state['base_scale']
            return finish()
    return .2


def finish(error=None):
    if state['done']:
        return None
    state['done'] = True
    report.update(error=error, phase=state['phase'],
                  success=error is None and bool(report['checks']) and all(report['checks'].values()))
    (OUT / 'rail-crowd.json'.replace('.json', TAG + '.json')).write_text(json.dumps(report, indent=2, default=str))
    print('ANGELO_RAIL_CROWD', json.dumps({'success': report['success'], 'phase': state['phase']}), flush=True)
    if not report['success']:
        os._exit(1)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=.5)
