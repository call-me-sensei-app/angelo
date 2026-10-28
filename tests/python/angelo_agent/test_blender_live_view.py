# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live view of worker parts (``live_view``, ``_bpy._angelo_live``): each part read into a private Main of its own,
read again after each worker save, never in Angelo's own data.

This (background) Angelo is the main one, with a document of its own. Two real workers (``Angelo --background``
children) build unrelated parts with typed programs, through chats bound to them as the window binds them: with
``ANGELO_WORKER_CHAT_HOST`` unset or ``1`` each chat runs in its worker, played by the stand-in model
(``HostedWorkerChat``, the "hosted" variant), with ``0`` on the lanes (``WorkerChat``). Checked from the real files and
Angelo's own data:

* binding a chat gives it a live part; each worker save is read again (objects added, deleted) and the live part's
  summary, bounds and ray cast answer from it;
* Angelo's ``bpy.data`` never holds anything of a part: no library, no part object, material or collection, before
  or after reads, saves, a file save and a reopen; a saved main file has no library;
* drawing a part offscreen (always Solid) draws pixels and leaves Angelo's own scene revision, change feed and view
  cache alone; the window's scene is the document's throughout;
* a dismissed tile frees its part.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-live-view-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_ENABLE_PARALLEL_AGENTS="1",   # Background: the machine check never passes (parallel_capability).
                  ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import blender_tools, live_view, parallel_sessions, ui, worker_routing  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from parallel_workers_qa import op, paint, primitive, program, worker_chat  # noqa: E402
from hosted_worker_chat import hosted  # noqa: E402

HOSTED = hosted()   # Design A′: each chat's turns run in its worker (the "hosted" variant).

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "platform": sys.platform, "timings": {}, "variant": "hosted" if HOSTED else "lanes"}
STATE = OUT / "state"
PARTS = OUT / "parts"
CHECK_FILE = """
import bpy, json, sys
path = sys.argv[sys.argv.index('--') + 1]
print('FILE', json.dumps({'libraries': [l.filepath for l in bpy.data.libraries],
    'scenes': [s.name for s in bpy.data.scenes], 'objects': sorted(o.name for o in bpy.data.objects),
    'linked': sorted(i.name for c in (bpy.data.objects, bpy.data.materials, bpy.data.collections, bpy.data.meshes)
                     for i in c if i.library)}))
"""


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


def committed(result):
    return isinstance(result, dict) and result.get("status") == "committed" and not result.get("error")


def live_names(session):
    parallel_sessions.flush()   # No timer runs in a background Blender: read what the schedule would.
    summary = live_view.summary(session) or {}
    return {"objects": sorted(summary.get("objects") or [])}


def live_names_until(session, done, seconds=10.0):
    """``live_names`` once ``done(names)`` holds, polled within ``seconds``: a worker's save reaches the lead on its
    own connection, never within the call that edited the part (the last names read, either way)."""
    deadline = time.monotonic() + seconds
    names = live_names(session)
    while not done(names) and time.monotonic() < deadline:
        time.sleep(0.05)
        names = live_names(session)
    return names


def angelo_has_part_data(names):
    """Whether Angelo's own data holds any library, or any ID named like a part's."""
    found = [lib.filepath for lib in bpy.data.libraries]
    for collection in (bpy.data.objects, bpy.data.materials, bpy.data.collections, bpy.data.meshes):
        found += [item.name for item in collection if item.name in names or item.library is not None]
    return found


def reopen(path):
    script = OUT / "check_file.py"
    script.write_text(CHECK_FILE)
    out = subprocess.run([bpy.app.binary_path, "-b", str(path), "--factory-startup", "--disable-autoexec",
                          "--python", str(script), "--", str(path)], capture_output=True, text=True, timeout=120)
    rows = [json.loads(line[5:]) for line in out.stdout.splitlines() if line.startswith("FILE ")]
    return rows[0] if rows else {"error": out.stdout[-2000:] + out.stderr[-2000:]}


def room():
    return [program([
        primitive("PLANE", "Floor", "floor", size=6),
        primitive("CUBE", "Back Wall", "wall", size=1, location=[0, 3, 1.4], scale=[6, 0.1, 2.8]),
        paint("floor", "Oak Floor", [0.42, 0.28, 0.16, 1], Roughness=0.6),
    ]), program([
        primitive("CUBE", "Table Top", "top", size=1, location=[0, 0, 0.74], scale=[1.4, 0.8, 0.06]),
        primitive("CUBE", "Chair Seat", "seat", size=0.45, location=[0, -0.75, 0.45], scale=[1, 1, 0.12]),
    ])]


def lamp():
    return [program([
        primitive("CYLINDER", "Lamp Base", "base", radius=0.3, depth=0.05, location=[0, 0, 0.025]),
        primitive("CYLINDER", "Lamp Pole", "pole", radius=0.03, depth=1.4, location=[0, 0, 0.75]),
        primitive("CONE", "Lamp Shade", "shade", radius=0.35, depth=0.4, location=[0, 0, 1.5]),
        paint("shade", "Linen", [0.9, 0.85, 0.7, 1]),
    ])]


def run_checks():
    PARTS.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    parallel_sessions.register()
    window = bpy.context.window_manager.windows[0]
    document = window.scene
    main_file = OUT / "main.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(main_file), check_existing=False)
    # The person's chat, before any worker starts (the product order): the rail's lead, holding the request, on the
    # document scene, where its merges land and are recorded (parallel_sessions.is_merged reads the lead's own scene
    # since break-it E2E-20 R5-M-1; without a lead chat this suite's merge record was read from no scene).
    ui.get_controller().state["messages"].append({"role": "user", "text": "Build a room and a lamp."})
    revision_before = blender_tools._scene_revisions.get(document.session_uid, 0)

    seen = []

    def watch(scene, depsgraph):
        if scene == document:
            seen.append((phase[0], [(u.id.name, getattr(u.id, "library", None) is not None, u.is_updated_geometry,
                                     u.is_updated_transform) for u in depsgraph.updates][:12]))
    phase = ["start"]
    bpy.app.handlers.depsgraph_update_post.append(watch)
    report["document_updates"] = seen
    chats = {name: worker_chat(STATE, PARTS / (name + ".blend"), ROOT) for name in ("room", "lamp")}
    for name, chat in chats.items():
        check(name + "_has_a_live_part_at_bind", live_view.part(chat.session) is not None)
        check(name + "_has_a_tile", chat.session in parallel_sessions.sessions())

    phase[0] = "build"
    for index, (name, plans) in enumerate((("room", room()), ("lamp", lamp()))):
        for plan in plans:
            result = chats[name].call("angelo_scene_program", plan)
            check("%s_program_committed_%d" % (name, index), committed(result), result)
    room_names = {"Floor", "Back Wall", "Table Top", "Chair Seat", "Oak Floor"}
    lamp_names = {"Lamp Base", "Lamp Pole", "Lamp Shade", "Linen"}
    check("room_live_shows_its_objects", {"Floor", "Back Wall", "Table Top", "Chair Seat"}
          <= set(live_names(chats["room"].session)["objects"]), live_names(chats["room"].session))
    check("lamp_live_shows_its_objects", {"Lamp Base", "Lamp Pole", "Lamp Shade"}
          <= set(live_names(chats["lamp"].session)["objects"]), live_names(chats["lamp"].session))
    check("parts_never_mix", not ({"Lamp Base"} & set(live_names(chats["room"].session)["objects"])))
    check("angelo_data_holds_nothing_of_a_part", not angelo_has_part_data(room_names | lamp_names),
          angelo_has_part_data(room_names | lamp_names))
    check("document_revision_unchanged_by_worker_saves",
          blender_tools._scene_revisions.get(document.session_uid, 0) == revision_before,
          [revision_before, blender_tools._scene_revisions.get(document.session_uid, 0), report["document_updates"]])
    document.objects["Cube"].location.x += 0.5   # A real edit of the document still counts.
    bpy.context.view_layer.update()
    check("a_real_document_edit_still_counts", blender_tools._scene_revisions.get(document.session_uid, 0)
          == revision_before + 1, blender_tools._scene_revisions.get(document.session_uid, 0))
    check("window_scene_is_the_document", window.scene == document)

    # Deleting in the worker: the next read drops it.
    rows = chats["room"].objects()
    seat = rows["Chair Seat"]
    detail = chats["room"].call("angelo_scene_object", {"object": seat.get("handle")})
    binding = {"handle": seat.get("handle"), "revision": detail.get("object_revision")}
    deleted = chats["room"].call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [binding],
                                                          "operations": [op("object_delete", seat.get("handle"))]})
    check("delete_committed_in_worker", committed(deleted), {"result": deleted, "row": seat})
    after = live_names_until(chats["room"].session, lambda names: "Chair Seat" not in names["objects"])
    check("live_drops_deleted_object", "Chair Seat" not in after["objects"], after)
    report["timings"]["read_ms"] = [round(s * 1000, 3) for s in parallel_sessions.refresh_seconds]

    # Bounds, a ray cast and a summary answer from the part.
    bounds = live_view.bounds(chats["lamp"].session)
    check("lamp_bounds_are_the_lamps", bounds is not None and bounds[1][2] > 1.5 and bounds[0][2] < 0.1, bounds)
    hit = live_view.ray_cast(chats["lamp"].session, (0.0, -10.0, 0.75), (0.0, 1.0, 0.0))
    check("a_ray_hits_the_lamp_pole", hit is not None and hit[0] == "Lamp Pole", hit)
    summary = live_view.summary(chats["lamp"].session)
    check("summary_counts_meshes_and_materials", summary["meshes"] >= 3 and summary["materials"] >= 1, summary)

    # Drawing a part offscreen (always Solid) draws pixels and is no change to the document.
    import gpu
    gpu.init()
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.shading.type = 'MATERIAL'   # The artist's own shading: a part is drawn Solid whatever it is.
    off = gpu.types.GPUOffScreen(256, 160)
    phase[0] = "draw"
    report["revision_before_draw"] = blender_tools._scene_revisions.get(document.session_uid, 0)
    from angelo_agent import rail_pictures
    view, projection = rail_pictures.framing(bounds, 1.6)[:2]
    draws = []
    for _ in range(6):
        started = time.perf_counter()
        with bpy.context.temp_override(window=window, area=area, region=region):
            live_view.native_draw(live_view.part(chats["lamp"].session), off, space, view, projection)
        draws.append(round((time.perf_counter() - started) * 1000, 3))
    with off.bind():
        pixels = gpu.state.active_framebuffer_get().read_color(0, 0, 256, 160, 4, 0, 'UBYTE')
    values = pixels.to_list()
    flat = [channel for row in values for pixel in row for channel in pixel[:3]]
    check("a_part_draws_pixels", len(set(flat)) > 8, len(set(flat)))
    off.free()
    report["timings"]["draw_256_ms"] = draws
    check("document_revision_unchanged_by_part_draws",
          blender_tools._scene_revisions.get(document.session_uid, 0) == report["revision_before_draw"])

    # The live view's own navigation (live_nav): its own view, never the artist's; a click through its matrices.
    from types import SimpleNamespace
    from angelo_agent import live_nav, live_view_draw
    from mathutils import Vector
    lamp_id = chats["lamp"].session
    rv3d = space.region_3d   # ``region.data`` is None in a background Blender; the space's own region view is not.
    artist = (tuple(rv3d.view_location), tuple(rv3d.view_rotation), rv3d.view_distance)
    view = live_view_draw.view_of(lamp_id)
    live_view_draw.refit(lamp_id)
    low, high = bounds
    check("framing_centres_the_part", (Vector(view.location) - (Vector(low) + Vector(high)) / 2).length < 1e-4)
    settings = live_nav.settings_from(bpy.context.preferences, space)
    before = (view.rotation, view.distance)
    live_nav.orbit(view, 40, 10, settings)
    check("orbit_turns_and_keeps_distance", view.rotation != before[0] and abs(view.distance - before[1]) < 1e-9)
    live_nav.zoom(view, 0.5)
    check("zoom_changes_distance", abs(view.distance - before[1] * 0.5) < 1e-6)
    live_nav.look_along(view, "FRONT", False)
    forward = live_nav.rotation_of(view) @ Vector((0.0, 0.0, -1.0))
    check("numpad_front_looks_along_plus_y", (forward - Vector((0.0, 1.0, 0.0))).length < 1e-5, tuple(forward))
    live_view_draw.refit(lamp_id)
    event = SimpleNamespace(type='WHEELUPMOUSE', value='PRESS', shift=False, ctrl=False, alt=False, mouse_x=0,
                            mouse_y=0, mouse_prev_x=0, mouse_prev_y=0)
    taken = live_nav.handle(event, view, live_nav.Drag(), settings, (320, 200), lambda: None)
    check("the_wheel_zooms_the_live_view", taken)
    # Framing fits the whole box, wide or tall, perspective or ortho (break-it P1-3 M4): a standing figure.
    figure = ((-0.25, -0.15, 0.0), (0.25, 0.15, 1.8))
    worst = []
    for width, height in ((1600, 1000), (1000, 1600), (1000, 1000)):
        for perspective in (True, False):
            probe = live_nav.View(rotation=tuple(live_nav.default_rotation()), perspective=perspective)
            live_nav.frame(probe, figure, width / height, settings.lens)
            matrix = live_nav.projection_matrix(probe, settings, width, height) @ live_nav.view_matrix(probe)
            for corner in [(x, y, z) for x in (figure[0][0], figure[1][0]) for y in (figure[0][1], figure[1][1])
                           for z in (figure[0][2], figure[1][2])]:
                point = matrix @ Vector((*corner, 1.0))
                worst.append(max(abs(point.x / point.w), abs(point.y / point.w), abs(point.z / point.w)))
    check("framing_shows_every_corner_wide_tall_persp_ortho", max(worst) <= 1.0, max(worst))
    # A drag ends on any button's release, and a press of another button ends it (its release went elsewhere, M2).
    drag = live_nav.Drag()
    def move(kind, value, x=0, y=0, **mods):
        return SimpleNamespace(type=kind, value=value, shift=mods.get("shift", False), ctrl=False, alt=False,
                               mouse_x=x, mouse_y=y, mouse_prev_x=x, mouse_prev_y=y)
    live_nav.handle(move('MIDDLEMOUSE', 'PRESS'), view, drag, settings, (320, 200), lambda: None)
    started = drag.mode == "orbit"
    live_nav.handle(move('LEFTMOUSE', 'PRESS'), view, drag, settings, (320, 200), lambda: None)
    check("a_drag_ends_when_another_button_is_pressed", started and not drag.mode)
    live_nav.handle(move('MIDDLEMOUSE', 'PRESS'), view, drag, settings, (320, 200), lambda: None)
    live_nav.handle(move('RIGHTMOUSE', 'RELEASE'), view, drag, settings, (320, 200), lambda: None)
    check("a_drag_ends_on_any_release", not drag.mode)
    # The input hold: a lease on the covered view, released by the window, lapsing on its own.
    live_view.hold(window, area, 0.3)
    held_now = live_view.held(window, area)
    time.sleep(0.4)
    lapsed = not live_view.held(window, area)
    live_view.hold(window, area, 5.0)
    live_view.hold(window, None, 0)
    check("the_hold_holds_lapses_and_releases", held_now and lapsed and not live_view.held(window, area),
          [held_now, lapsed])
    live_view_draw.refit(lamp_id)
    hit = live_view_draw.inspect(lamp_id, area, region, region.width / 2, region.height * 0.45)
    check("a_click_through_the_live_views_own_matrices_hits_the_lamp", hit is not None and hit[1].startswith("Lamp"),
          hit)
    check("the_artists_own_view_never_moved",
          artist == (tuple(rv3d.view_location), tuple(rv3d.view_rotation), rv3d.view_distance))

    # The worker's own size of its part: subdivision evaluated, never the cage (break-it P1-3 M5).
    from angelo_agent import live_layout
    probe = bpy.data.scenes.new("Size Probe")
    mesh = bpy.data.meshes.new("Size Probe Cube")
    import bmesh
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=2.0)
    shape.to_mesh(mesh)
    shape.free()
    cube = bpy.data.objects.new("Size Probe Cube", mesh)
    probe.collection.objects.link(cube)
    cube.modifiers.new("Sculpt", 'MULTIRES')
    with bpy.context.temp_override(scene=probe, view_layer=probe.view_layers[0], object=cube, active_object=cube):
        for _ in range(2):
            bpy.ops.object.multires_subdivide(modifier="Sculpt", mode='CATMULL_CLARK')
        graph = bpy.context.evaluated_depsgraph_get()   # As the worker's own scene is, evaluated.
    measured = live_layout.layout(probe, 1, graph)["size"]
    # Multires at level 2 on a 2 m cage is 1.68 m across; the live view's copy (Simplify 0) shows the 2 m cage.
    check("worker_size_is_the_evaluated_surface", measured is not None and 1.5 < max(measured) < 1.9, measured)
    cleaned = live_layout.clean(live_layout.layout(probe, 2, graph))
    check("a_reported_size_survives_the_check", cleaned is not None and cleaned["size"] == measured, cleaned)
    check("a_bad_size_is_unknown_never_guessed", live_layout.clean(
        {"revision": 3, "collections": [], "objects": [], "size": [float("nan"), 1, 1]})["size"] is None)
    bpy.data.objects.remove(cube)
    bpy.data.meshes.remove(mesh)
    bpy.data.scenes.remove(probe)

    # A thin poly curve (a leaf rib: points of the default radius 1, a 1 mm bevel) is as wide as its geometry, never
    # padded by its points' radius (Sol live trial on e2e 03020c0ecca: 6 cm ribs read 2 m, a 0.6 m planter 2.4 m).
    probe = bpy.data.scenes.new("Curve Probe")
    curve = bpy.data.curves.new("Size Probe Rib", 'CURVE')
    curve.dimensions, curve.bevel_depth = '3D', 0.001
    spline = curve.splines.new('POLY')
    spline.points.add(2)
    for point, x in zip(spline.points, (0.0, 0.05, 0.1)):
        point.co = (x, 0.0, 0.0, 1.0)
    rib = bpy.data.objects.new("Size Probe Rib", curve)
    probe.collection.objects.link(rib)
    with bpy.context.temp_override(scene=probe, view_layer=probe.view_layers[0]):
        graph = bpy.context.evaluated_depsgraph_get()
    measured = live_layout.layout(probe, 1, graph)["size"]
    check("a_curve_part_is_as_wide_as_its_geometry", measured is not None and 0.09 < max(measured) < 0.2, measured)
    # The root (BKE): an evaluated curve's own box is its geometry's, as Python and every depsgraph reader see it.
    from mathutils import Vector
    corners = [rib.evaluated_get(graph).matrix_world @ Vector(c) for c in rib.evaluated_get(graph).bound_box]
    width = max(c.x for c in corners) - min(c.x for c in corners)
    check("an_evaluated_curves_box_is_its_geometry", 0.09 < width < 0.2, round(width, 4))
    bpy.data.objects.remove(rib)
    bpy.data.curves.remove(curve)
    bpy.data.scenes.remove(probe)

    # Overlap scope (a): where a part stands, helper geometry left out by one rule (the maintainer, 2026-10-25): a
    # Boolean cutter (object or collection operand), an object displayed as wire or bounds, one hidden from renders,
    # and an instance of a hidden-from-render object, each placed far from the part's only shown object.
    probe = bpy.data.scenes.new("Helper Probe")
    shown = bpy.data.objects.new("Helper Probe Shown", bpy.data.meshes.new("Helper Probe Shown"))
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=1.0)
    shape.to_mesh(shown.data)
    shape.free()
    probe.collection.objects.link(shown)

    def helper(name, location):
        obj = bpy.data.objects.new(name, shown.data)
        obj.location = location
        probe.collection.objects.link(obj)
        return obj
    cutter = helper("Helper Probe Cutter", (6, 0, 0))   # Cuts nothing it reaches, but is an operand.
    cutters = bpy.data.collections.new("Helper Probe Cutters")
    probe.collection.children.link(cutters)
    far_cutter = bpy.data.objects.new("Helper Probe Far Cutter", shown.data)
    far_cutter.location = (-6, 0, 0)
    cutters.objects.link(far_cutter)
    boolean = shown.modifiers.new("Cut", 'BOOLEAN')
    boolean.object = cutter
    collection_cut = shown.modifiers.new("Cut set", 'BOOLEAN')
    collection_cut.operand_type, collection_cut.collection = 'COLLECTION', cutters
    helper("Helper Probe Wire", (0, 6, 0)).display_type = 'WIRE'
    helper("Helper Probe Bounds", (0, -6, 0)).display_type = 'BOUNDS'
    hidden = helper("Helper Probe Not Rendered", (0, 0, 6))
    hidden.hide_render = True
    kept = bpy.data.collections.new("Helper Probe Instanced")
    kept_member = bpy.data.objects.new("Helper Probe Instanced Hidden", shown.data)
    kept_member.hide_render = True
    kept.objects.link(kept_member)
    placed = bpy.data.objects.new("Helper Probe Instancer", None)
    placed.instance_type, placed.instance_collection, placed.location = 'COLLECTION', kept, (0, 0, -6)
    probe.collection.objects.link(placed)
    with bpy.context.temp_override(scene=probe, view_layer=probe.view_layers[0]):
        graph = bpy.context.evaluated_depsgraph_get()
    measured = live_layout.layout(probe, 1, graph)
    check("a_part_measures_without_its_helpers", measured["size"] is not None and max(measured["size"]) < 1.05,
          measured["size"])
    check("a_part_states_where_it_stands", measured["bounds"] is not None and all(
        abs(measured["bounds"][0][i] + 0.5) < 0.01 and abs(measured["bounds"][1][i] - 0.5) < 0.01 for i in range(3)),
        measured["bounds"])
    check("a_reported_box_survives_the_check", live_layout.clean(measured)["bounds"] == measured["bounds"])
    for name in {obj.name for obj in list(probe.collection.all_objects) + [kept_member, far_cutter]}:
        bpy.data.objects.remove(bpy.data.objects[name])
    bpy.data.collections.remove(cutters)
    bpy.data.collections.remove(kept)
    bpy.data.scenes.remove(probe)

    # Saving: no library, nothing of a part, in the main file, a copy or the reopened file.
    started = time.perf_counter()
    bpy.ops.wm.save_as_mainfile(filepath=str(main_file), check_existing=False)
    report["timings"]["save_ms_with_two_live_parts"] = round((time.perf_counter() - started) * 1000, 3)
    saved = reopen(main_file)
    check("saved_main_has_no_library", saved.get("libraries") == [] and not saved.get("linked"), saved)
    check("saved_main_has_no_part_object", not (set(saved.get("objects", [])) & (room_names | lamp_names)), saved)
    check("angelo_data_still_holds_nothing_of_a_part", not angelo_has_part_data(room_names | lamp_names))

    # A worker that ends keeps its tile on its last saved part; a dismissed tile frees it.
    # A merge (the lead's angelo_workers_merge): the tile says Merging, then ends with its live part freed.
    from angelo_agent import session_rail, session_rail_draw
    room_id = chats["room"].session
    parallel_sessions.merging(room_id)
    kinds = {tile.session: tile.kind for tile in session_rail_draw.states()}
    check("a_merging_tile_says_so", kinds.get(room_id) == session_rail.MERGING, kinds)
    parallel_sessions.merged(room_id)
    kinds = {tile.session: tile.kind for tile in session_rail_draw.states()}
    check("a_merged_tile_says_so", kinds.get(room_id) == session_rail.MERGED and not parallel_sessions.is_merging(
        room_id), kinds)
    # Merged only while the document scene records the merge of its part file: an Undo of the merge takes the record
    # out and the word with it (P1-6 M3; the scene's record since break-it E2E-20 round 3, never a collection's).
    probe_part = str(PARTS / "merged-probe.blend")
    unrecord = parallel_sessions.parts_merged([probe_part], document)
    parallel_sessions.merged(room_id, probe_part)
    held_merged = parallel_sessions.is_merged(room_id)
    unrecord()
    check("merged_follows_its_part_record", held_merged and not parallel_sessions.is_merged(room_id))
    parallel_sessions.merged(room_id)
    # A worker's own plan on its tile, reported through its own chat's executor (no window there, P1-6 H1).
    plan = {"steps": ["Base", "Pole", "Shade", "Bulb", "Cord", "Switch", "Finish"], "done": 3}
    if HOSTED:   # Reported mid-turn, as a model reports its plan: the turn still works while its tile is read.
        _key, answers = chats["lamp"].request("angelo_workers_progress", plan, hold="plan")
        answer = chats["lamp"].wait(answers)
        deadline = time.monotonic() + 10   # The report reaches the tile with the worker's next forwarded frames.
        while parallel_sessions.progress_of(chats["lamp"].session) is None and time.monotonic() < deadline:
            chats["lamp"].pump()
    else:
        answer = chats["lamp"].call("angelo_workers_progress", plan)
    check("a_worker_reports_progress_through_its_chat", "'success': True" in str(answer), str(answer)[:600])
    lamp_tile = next(t for t in session_rail_draw.states() if t.session == chats["lamp"].session)
    check("a_worker_tile_counts_its_own_plan", lamp_tile.progress == (3, 7) and parallel_sessions.current_step(
        chats["lamp"].session) == "Bulb", [lamp_tile.progress, lamp_tile.status])
    if HOSTED:
        chats["lamp"].release("plan")
        chats["lamp"].wait_idle()
    # A long step title is cut, never the counts and the clock after it (break-it P1-8 P8).
    head, keep = session_rail.status_parts(session_rail.WORKING, "", "1m 9s", "Carving every strand of the mane and "
                                           "the tail into place", (3, 7))
    narrow = session_rail.Tile("w", "W", "W", session_rail.status(session_rail.WORKING, "", "1m 9s", head, (3, 7)),
                               session_rail.WORKING, status_head=head, status_keep=keep)
    from angelo_agent import ui_tokens
    lines = session_rail_draw.status_lines(narrow, 158, ui_tokens.TYPE_SMALL)   # A tile's status width at scale 1.
    check("a_long_step_keeps_its_counts_and_clock", len(lines) == 2 and lines[0].startswith("Carving")
          and lines[0].endswith("…") and lines[1] == keep, lines)
    from angelo_agent import session_rail_input
    session_rail_input.dismiss(room_id)
    check("a_closed_merged_tile_frees_its_part", room_id not in parallel_sessions.sessions()
          and live_view.part(room_id) is None)
    chats["lamp"].stop()
    check("ended_worker_keeps_tile", parallel_sessions.ended(chats["lamp"].session)
          and chats["lamp"].session in parallel_sessions.sessions())
    check("ended_worker_part_still_shown", "Lamp Shade" in live_names(chats["lamp"].session)["objects"])
    parallel_sessions.dismiss(chats["lamp"].session)
    check("dismissed_tile_and_part_gone", chats["lamp"].session not in parallel_sessions.sessions()
          and live_view.part(chats["lamp"].session) is None)
    chats["room"].stop()
    # The exit cleanup that frees live parts survives a file load (else every part leaks at exit, build4 8.7 MB).
    bpy.ops.wm.read_homefile(use_empty=True)
    check("live_parts_exit_cleanup_survives_a_file_load", live_view._exit_pre in bpy.app.handlers.exit_pre)
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "live-view.json").write_text(json.dumps(report, indent=2, default=str))
print("LIVE-VIEW-DONE", report.get("success"), flush=True)
if not report.get("success"):
    os._exit(1)   # A failed run exits 1 (it printed False and exited 0); its report is written, its workers stopped.
