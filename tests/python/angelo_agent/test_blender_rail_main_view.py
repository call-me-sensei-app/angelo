# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Main tile's picture as the maintainer ruled (R3-M2, 2026-10-25 16:55; for the camera, "Yes, camera too"), in a
real Angelo (background): through the scene camera only if the geometry fills a reasonable part of its frame, through
the artist's own view only if it fills a reasonable part of that (``rail_pictures.FILL`` of the picture), otherwise on
the geometry it shows.

The Main picture's earlier scenes (``test_blender_rail_crowd.py``: a camera and a light only, a desk and a chair, a 2 mm
part a kilometre away, a 500 m slab, instances only, a wire guide only, a model hidden from renders only) seen from one
artist's view far off (the bounds decide), then the ruling's own cases: a scene camera aimed away from the geometry (it
falls through: to the bounds from a far view, to the artist's view from a near one), a scene camera aimed at the
geometry from afar (it falls through) and up close (it decides), a camera inside a room, the artist's view at a working
distance (it decides), the same view ratio at a millimetre and a kilometre scale (the same answer), the artist's view
looking at empty space (the bounds decide), a large ground plane under a small model (seen up close, then with the view
at the sky), and views that meet a box but see little: a view between two far models, a view that sees only a helper, a
view that sees only an instance; and the shapes a box test misreads, where the picture decides
(``rail_pictures.shown_share``, REVIEW-MAIN-THUMB-2): one joined mesh either side of a camera and of a view, a camera
inside a rotated wall's world box aimed away, an eye inside the box of two cubes far apart; and the grid measure
(``rail_pictures.fill``, "Count where it is"): two small cubes at opposite corners of a working-distance view (the
bounds), a thin rod across a view (the view). Each redraw decides afresh (``redecided``): inside walls that fix the
box, a model moved out of the camera's frame or hidden gives up the camera (REVIEW-TILE-COST H1), and one shown in
front of it again takes it back (REVIEW-TILE-COST-2 M); Suzanne panned a few pixels in a small orthographic view keeps the view
while it holds ``KEEP`` cells (``panned``, REVIEW-TILE-COST-2 H). Each picture is
drawn as the
rail draws it (``native_draw``, Solid) and saved as a PNG; a picture framed on the bounds reaches across at least
``FILL`` of it, one through the artist's view shows geometry. Also the choice's cost (``costs``), counted in the
decision pictures it draws: fields of 2,000 and 8,000 objects, and ~200k instances under a scene camera that sees them
and one aimed away.

The artist's view is a stand-in with the matrices a 1280 x 720 3D view has (no region draws in the background); the
GUI shots of ``test_blender_rail_crowd.py`` use the real one. Run: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``.
"""

import json
import math
import os
import struct
import sys
import tempfile
import traceback
import zlib
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector

assert bpy.app.background and '--factory-startup' in sys.argv   # It deletes every object: a disposable instance only.

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-rail-main-view"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import live_view, rail_pictures  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rail_main_costs  # noqa: E402

FILL = 0.4   # As the GUI suite: a picture framed on the bounds reaches across at least this much of it.
NEAR = ((2.4, -2.6, 1.8), (0.0, -0.3, 0.5))   # The desk and chair at a working distance.
SIZE = rail_pictures.SIZE
report = {"checks": {}, "readings": {}, "pictures": {}, "timings": {}, "binary": bpy.app.binary_path,
          "native_background": True}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report["readings"][name] = detail


SHADING = {}   # The 3D view's Solid settings as the run found them, put back before each scene.


def shading():
    return next(a for a in bpy.context.window_manager.windows[0].screen.areas
                if a.type == 'VIEW_3D').spaces.active.shading


def see_through(xray=None, alpha=None):
    """The artist's Solid view shows geometry see-through, outline off: X-ray at ``xray``, or materials of viewport
    alpha ``alpha`` shown in material colour."""
    view = shading()
    view.show_object_outline = False
    if xray is not None:
        view.show_xray, view.xray_alpha = True, xray
    if alpha is not None:
        view.color_type = 'MATERIAL'
        material = bpy.data.materials.new("Main Glass")
        material.diffuse_color = (0.6, 0.8, 1.0, alpha)
        for obj in bpy.context.scene.objects:
            if obj.type == 'MESH':
                obj.data.materials.append(material)


def clear():
    scene = bpy.context.scene
    view = shading()
    if not SHADING:
        SHADING.update(show_xray=view.show_xray, xray_alpha=view.xray_alpha, color_type=view.color_type,
                       show_object_outline=view.show_object_outline)
    for key, value in SHADING.items():
        setattr(view, key, value)
    scene.camera = None
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for collection in list(scene.collection.children):
        bpy.data.collections.remove(collection)


def box(name, size, location=(0.0, 0.0, 0.0), collection=None, display='SOLID', mesh=None):
    if mesh is None:
        sx, sy, sz = (v / 2 for v in size)
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x * sx, y * sy, z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                         [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.display_type = display
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def camera_and_light():   # As the GUI suite's scenes: neither is the scene's camera.
    scene = bpy.context.scene
    camera = bpy.data.objects.new("Main Camera Stand", bpy.data.cameras.new("Main Camera Stand"))
    camera.location = (30.0, -30.0, 20.0)
    light = bpy.data.objects.new("Main Light", bpy.data.lights.new("Main Light", 'SUN'))
    light.location = (-20.0, 10.0, 40.0)
    scene.collection.objects.link(camera)
    scene.collection.objects.link(light)


def scene_camera(location, target, lens=50.0):
    camera = bpy.data.objects.new("Main Scene Camera", bpy.data.cameras.new("Main Scene Camera"))
    camera.data.lens = lens
    camera.location = location
    camera.rotation_euler = (Vector(target) - Vector(location)).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera


def empty_as_camera():   # Any object can be the scene's camera: Blender renders from it with a default lens.
    desk_and_chair()
    empty = bpy.data.objects.new("Main Empty Camera", None)
    empty.location = (0.0, -4.0, 1.0)   # Near enough to fill FILL of its frame ("Yes, camera too").
    empty.rotation_euler = (Vector((0.0, 0.0, 0.5)) - empty.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.collection.objects.link(empty)
    bpy.context.scene.camera = empty


def camera_excluded():   # The scene's camera in a collection the view layer excludes: renders still use it.
    desk_and_chair()
    hidden = bpy.data.collections.new("Main Camera Rig")
    bpy.context.scene.collection.children.link(hidden)
    camera = bpy.data.objects.new("Main Rig Camera", bpy.data.cameras.new("Main Rig Camera"))
    camera.location = (0.0, -6.0, 1.0)
    camera.rotation_euler = (Vector((0.0, 0.0, 0.5)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    hidden.objects.link(camera)
    bpy.context.view_layer.layer_collection.children[hidden.name].exclude = True
    bpy.context.scene.camera = camera


def desk_and_chair():
    box("Main Desk", (1.6, 0.8, 0.75), (0.0, 0.0, 0.375))
    box("Main Chair", (0.5, 0.5, 1.0), (0.0, -0.9, 0.5))


def instances(at, count=5, far=None):
    source = bpy.data.collections.new("Main Source")
    bpy.context.scene.collection.children.link(source)
    box("Main Tile", (1.0, 1.0, 0.2), collection=source)
    bpy.context.view_layer.layer_collection.children[source.name].exclude = True
    for index in range(count):
        empty = bpy.data.objects.new("Main Tile Copy %d" % index, None)
        empty.instance_type = 'COLLECTION'
        empty.instance_collection = source
        empty.location = (at[0] + index * 1.5, at[1], at[2])
        bpy.context.scene.collection.objects.link(empty)
    if far is not None:
        box("Main Far Box", (2.0, 2.0, 2.0), far)


def room():
    for name, size, location in (("Main Floor", (6, 6, 0.1), (0, 0, -0.05)), ("Main Ceiling", (6, 6, 0.1), (0, 0, 3.05)),
                                 ("Main Wall N", (6, 0.1, 3), (0, 3.05, 1.5)), ("Main Wall S", (6, 0.1, 3), (0, -3.05, 1.5)),
                                 ("Main Wall E", (0.1, 6, 3), (3.05, 0, 1.5)), ("Main Wall W", (0.1, 6, 3), (-3.05, 0, 1.5))):
        box(name, size, location)
    box("Main Room Desk", (1.6, 0.8, 0.75), (0.0, 1.5, 0.375))


def joined_either_side(gap=50.0):   # One object: two cubes either side of what a view between them looks at.
    mesh = bpy.data.meshes.new("Main Joined")
    verts, faces = [], []
    for side in (-gap, gap):
        base = len(verts)
        verts += [(side + x, y, 1.0 + z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        faces += [tuple(base + i for i in face) for face in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6),
                                                             (0, 2, 6, 4), (1, 5, 7, 3))]
    mesh.from_pydata(verts, [], faces)
    return box("Main Joined", None, mesh=mesh)


def rotated_wall():   # A 100 m wall at 45 degrees: its world box is a 71 m square around open ground.
    wall = box("Main Wall", (100.0, 0.2, 3.0), (0.0, 0.0, 1.5))
    wall.rotation_euler = (0.0, 0.0, math.radians(45.0))


def at_the_picture(spec, points, size):
    """Cubes of ``size`` metres where ``points`` (picture NDC, -1 to 1) lie in the Main picture the artist's view
    ``spec`` gives, at its target's depth."""
    area, region = artist(*spec)
    rv3d = area.spaces.active.region_3d
    fitted = rail_pictures.fitted(rv3d.window_matrix, region.width / region.height, SIZE[0] / SIZE[1])
    matrix = fitted @ rv3d.view_matrix
    target = matrix @ Vector(tuple(spec[1]) + (1.0,))
    for index, (x, y) in enumerate(points):
        point = matrix.inverted() @ Vector((x, y, target.z / target.w, 1.0))
        box("Main Corner %d" % index, (size,) * 3, point.xyz / point.w)


def ground_and_model():
    box("Main Ground", (200.0, 200.0, 0.02), (0.0, 0.0, -0.01))
    box("Main Small Model", (0.3, 0.3, 0.3), (0.0, 0.0, 0.15))


ARTIST = ((12.0, -12.0, 8.0), (0.0, 0.0, 0.0))   # One artist's view for the earlier scenes: front right, above.
CASES = (   # (name, builder, artist's (eye, target, vertical fov degrees), expected source or None)
    ("camera_and_light_only", lambda: None, ARTIST, "empty"),
    ("desk_and_chair", desk_and_chair, ARTIST, "bounds"),
    ("desk_and_chair_at_a_working_distance", desk_and_chair, NEAR, "view"),
    ("a_two_centimetre_part_seen_as_close_for_its_size", lambda: box("Main Pin", (0.02, 0.02, 0.02)),
     ((0.03, -0.03, 0.02), (0.0, 0.0, 0.0)), "view"),
    ("a_two_centimetre_part_seen_as_far_for_its_size", lambda: box("Main Pin", (0.02, 0.02, 0.02)),
     ((0.25, -0.25, 0.17), (0.0, 0.0, 0.0)), "bounds"),
    ("a_two_kilometre_block_seen_as_close_for_its_size", lambda: box("Main Block", (2000.0,) * 3, (0, 0, 0)),
     ((3000.0, -3000.0, 2000.0), (0.0, 0.0, 0.0), 40.0, 1.0, 1e6), "view"),
    ("a_two_kilometre_block_seen_as_far_for_its_size", lambda: box("Main Block", (2000.0,) * 3, (0, 0, 0)),
     ((25000.0, -25000.0, 17000.0), (0.0, 0.0, 0.0), 40.0, 1.0, 1e6), "bounds"),
    ("two_millimetres_a_kilometre_away", lambda: box("Main Bead", (0.002,) * 3, (1000.0, 1000.0, 0.0)), ARTIST, None),
    ("five_hundred_metre_slab", lambda: box("Main Slab", (500.0, 500.0, 2.0), (0.0, 0.0, -1.0)), ARTIST, None),
    ("instances_only", lambda: instances((40.0, 7.0, 0.0)), ARTIST, None),
    ("only_a_wire_guide", lambda: box("Main Guide", (3.0, 3.0, 3.0), display='WIRE'), ARTIST, "empty"),
    ("only_a_model_hidden_from_renders", lambda: setattr(box("Main Mockup", (2.0, 1.0, 1.0)), "hide_render", True),
     ARTIST, None),
    ("camera_aimed_away_far_view", lambda: (desk_and_chair(), scene_camera((0.0, -6.0, 1.2), (0.0, -20.0, 1.2))),
     ARTIST, "bounds"),
    ("camera_aimed_away_near_view", lambda: (desk_and_chair(), scene_camera((0.0, -6.0, 1.2), (0.0, -20.0, 1.2))),
     NEAR, "view"),
    ("camera_aimed_at_the_desk_from_afar", lambda: (desk_and_chair(), scene_camera((0.0, -40.0, 1.2),
                                                                                  (0.0, 0.0, 0.5))),
     ARTIST, "bounds"),   # "Yes, camera too" (the maintainer): it sees the desk, too small to fill FILL.
    ("camera_aimed_at_the_desk_up_close", lambda: (desk_and_chair(), scene_camera((0.0, -4.0, 1.2), (0.0, 0.0, 0.5))),
     ARTIST, "camera"),
    ("camera_inside_a_room", lambda: (room(), scene_camera((0.0, -2.2, 1.5), (0.0, 3.0, 1.2), lens=24.0)), ARTIST,
     "camera"),
    ("an_empty_as_the_scene_camera", empty_as_camera, ARTIST, "camera"),
    ("scene_camera_in_an_excluded_collection", camera_excluded, ARTIST, "camera"),
    ("view_looking_at_empty_space", desk_and_chair, ((0.0, -6.0, 1.5), (0.0, -20.0, 1.5)), "bounds"),
    ("ground_plane_small_model_up_close", ground_and_model, ((0.8, -0.8, 0.6), (0.0, 0.0, 0.15)), "view"),
    ("ground_plane_small_model_view_at_the_sky", ground_and_model, ((0.0, 0.0, 2.0), (0.0, 0.0, 10.0)), "bounds"),
    ("view_between_two_far_models", lambda: (box("Main Left", (2, 2, 2), (-50, 0, 1)), box("Main Right", (2, 2, 2),
                                                                                         (50, 0, 1))),
     ((0.0, -10.0, 1.0), (0.0, 0.0, 1.0), 20.0), "bounds"),
    ("view_sees_only_a_helper", lambda: (box("Main Far Model", (2, 2, 2), (50, 0, 1)),
                                         box("Main Near Guide", (2, 2, 2), (0, 0, 1), display='WIRE')),
     ((0.0, -10.0, 1.0), (0.0, 0.0, 1.0), 20.0), "bounds"),
    ("one_joined_mesh_either_side_camera_between", lambda: (joined_either_side(), scene_camera((0.0, -10.0, 1.0),
                                                                                          (0.0, 0.0, 1.0))),
     ((0.0, -10.0, 1.0), (0.0, 0.0, 1.0), 20.0), "bounds"),
    ("one_joined_mesh_either_side_view_between", joined_either_side, ((0.0, -10.0, 1.0), (0.0, 0.0, 1.0), 20.0),
     "bounds"),
    ("rotated_wall_camera_inside_its_box_aimed_away", lambda: (rotated_wall(), scene_camera((20.0, -20.0, 1.5),
                                                                                          (40.0, -40.0, 1.5))),
     ARTIST, "view"),
    ("two_cubes_far_apart_eye_inside_their_box", lambda: (box("Main West", (1, 1, 1), (-30, 0, 0)),
                                                          box("Main East", (1, 1, 1), (30, 0, 0))),
     ((0.0, 0.0, 0.0), (30.0, 0.0, 0.0), 40.0), "bounds"),
    ("a_plane_up_close_through_x_ray", lambda: (box("Main Plane", (2.0, 2.0, 0.0)), see_through(xray=0.5)),
     ((1.6, -1.6, 1.2), (0.0, 0.0, 0.0)), "view"),   # See-through shading never hides geometry from the choice.
    ("a_pane_of_viewport_alpha_0_3_up_close", lambda: (box("Main Pane", (2.0, 0.02, 1.5)), see_through(alpha=0.3)),
     ((1.6, -2.4, 1.0), (0.0, 0.0, 0.0)), "view"),
    ("a_camera_through_x_ray", lambda: (box("Main Plane", (2.0, 2.0, 0.0)), see_through(xray=0.2),
                                        scene_camera((0.0, -3.0, 2.0), (0.0, 0.0, 0.0))), ARTIST, "camera"),
    ("view_sees_only_an_instance", lambda: instances((0.0, 0.0, 0.0), count=1, far=(200.0, 0.0, 1.0)),
     ((0.0, -2.5, 1.5), (0.0, 0.0, 0.0), 40.0), "view"),   # Near enough to fill FILL of the picture.
    # The grid measure ("Count where it is", the maintainer): where the geometry is, not the rectangle around it.
    ("corner_cubes_at_a_working_distance", lambda: at_the_picture(NEAR, ((-0.8, -0.7), (0.8, 0.7)), 0.1), NEAR,
     "bounds"),   # Two specks span the picture's rectangle, yet hold a few cells of 160.
    ("a_thin_rod_across_the_view", lambda: box("Main Rod", (8.0, 0.04, 0.04), (0.0, 0.0, 0.5)),
     ((0.0, -4.0, 0.5), (0.0, 0.0, 0.5), 40.0), "view"),   # About 2 px tall, across every column of cells.
)


def artist(eye, target, fov=40.0, near=0.01, far=1000.0, size=(1280, 720)):
    """A stand-in 3D view whose artist's view looks from ``eye`` at ``target`` (Blender's default clip range unless
    given: a kilometre-scale scene needs its own, as an artist sets it)."""
    eye, target = Vector(eye), Vector(target)
    rotation = (target - eye).to_track_quat('-Z', 'Y').to_matrix().to_4x4()
    view = (Matrix.Translation(eye) @ rotation).inverted()
    projection = rail_pictures.perspective(math.radians(fov), size[0] / size[1], near, far)
    rv3d = SimpleNamespace(view_matrix=view, window_matrix=projection, perspective_matrix=projection @ view,
                           as_pointer=lambda: 1)
    area = SimpleNamespace(spaces=SimpleNamespace(active=SimpleNamespace(region_3d=rv3d)), regions=[])
    return area, SimpleNamespace(width=size[0], height=size[1])


def host():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def draw(target, view, projection, offscreen, view_layer):
    window, area, region = host()
    with bpy.context.temp_override(window=window, area=area, region=region):
        live_view.native_draw(target, offscreen, area.spaces.active, view, projection, view_layer=view_layer)
    with offscreen.bind():
        import gpu
        pixels = gpu.state.active_framebuffer_get().read_color(0, 0, SIZE[0], SIZE[1], 4, 0, 'UBYTE')
    return [list(row) for row in pixels.to_list()]   # Bottom row first.


def write_png(path, rows):
    raw = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel[:3]) for row in reversed(rows))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE[0], SIZE[1], 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def measure(rows, empty):
    """(the share of pixels that are not the empty picture's, how far they reach across it: larger of width/height)."""
    shown = [(x, y) for y, row in enumerate(rows) for x, pixel in enumerate(row)
             if max(abs(a - b) for a, b in zip(pixel[:3], empty[y][x][:3])) > 12]
    if not shown:
        return 0.0, 0.0
    xs, ys = [x for x, _ in shown], [y for _, y in shown]
    return (round(len(shown) / (SIZE[0] * SIZE[1]), 4),
            round(max((max(xs) - min(xs) + 1) / SIZE[0], (max(ys) - min(ys) + 1) / SIZE[1]), 4))


def run():
    import gpu
    gpu.init() if hasattr(gpu, "init") else None
    scene, view_layer = bpy.context.scene, bpy.context.view_layer
    window = SimpleNamespace(scene=scene, view_layer=view_layer)
    offscreen = gpu.types.GPUOffScreen(*SIZE)
    nothing = Matrix.Translation((0.0, 0.0, 1e7))   # A view with the whole scene behind it: what nothing looks like.
    for name, build, spec, expected in CASES:
        clear()
        camera_and_light()
        build()
        view_layer.update()
        rail_pictures._scene_boxes.clear()
        found = rail_pictures._scene_bounds(window)
        if found is rail_pictures._UNREAD:
            check(name + ": evaluated", False, "the depsgraph was not evaluated")
            continue
        if found is None:
            report["pictures"][name] = {"source": "empty"}
            check(name + ": " + str(expected or "framed"), expected == "empty", "no geometry: no picture")
            continue
        area, region = artist(*spec)
        window_, host_area, host_region = host()
        with bpy.context.temp_override(window=window_, area=host_area, region=host_region):   # As the rail decides.
            view, projection, source = rail_pictures.lead_view(
                window, area, region, found, shows=lambda matrix, lens: rail_pictures.shown_share(
                    scene, offscreen, host_area.spaces.active, matrix, lens, view_layer))
        rows = draw(scene, view, projection, offscreen, view_layer)
        empty = draw(scene, nothing, projection, offscreen, view_layer)
        share, reach = measure(rows, empty)
        write_png(OUT / ("main-view-%s.png" % name), rows)
        rv3d = area.spaces.active.region_3d
        fill = rail_pictures.coverage(rail_pictures.fitted(rv3d.window_matrix, region.width / region.height,
                                                           SIZE[0] / SIZE[1]) @ rv3d.view_matrix, found)
        report["pictures"][name] = {"source": source, "share": share, "reach": reach, "box": found,
                                    "artist_view_coverage": round(fill, 4)}
        if expected is not None:
            check("%s: %s" % (name, expected), source == expected, source)
        if source == "bounds":
            check("%s: framed on its bounds reaches across" % name, reach >= FILL, reach)
        elif source == "view":
            check("%s: the artist's view shows geometry" % name, share > 0.0, share)
        elif name in ("an_empty_as_the_scene_camera", "scene_camera_in_an_excluded_collection",
                      "camera_aimed_at_the_desk_up_close"):
            check("%s: the camera sees the desk" % name, share > 0.0, share)
        elif name == "camera_inside_a_room":   # Walls all round: most of it (an unlit ceiling reads as background).
            check("%s: the room fills what the camera sees" % name, share >= 0.5 and reach >= FILL, (share, reach))
    offscreen.free()
    redecided(window)
    panned(window)
    rail_main_costs.costs(sys.modules[__name__], window)


def panned(window):
    """REVIEW-TILE-COST-2 H, pinned with real pictures: Suzanne small in an orthographic artist's view (about 40 to
    56 px tall on the tile), panned 0 to 8 px each way. Where it is adopted unpanned (10 cells or more), every pan keeps
    the view while it still holds ``KEEP`` (7 of 160) cells, as the coordinator ruled; below that it decides afresh."""
    import gpu
    scene, layer = window.scene, window.view_layer
    clear()
    bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 0), rotation=(math.pi / 2, 0, 0))
    layer.update()
    rail_pictures._scene_boxes.clear()
    found = rail_pictures._scene_bounds(window)
    offscreen = gpu.types.GPUOffScreen(*SIZE)
    window_, host_area, host_region = host()
    shows = lambda matrix, lens, grow=0: rail_pictures.shown_share(   # noqa: E731
        scene, offscreen, host_area.spaces.active, matrix, lens, layer, grow)
    view = Matrix.Rotation(-math.pi / 2, 4, 'X')
    readings, adopted = {}, 0
    for tall in (40, 48, 56):
        half = SIZE[1] / tall
        lens = Matrix(((1 / (half * SIZE[0] / SIZE[1]), 0, 0, 0), (0, 1 / half, 0, 0), (0, 0, -0.1, 0), (0, 0, 0, 1)))
        for dx, dy in ((0, 0), (2, 0), (0, 2), (2, 2), (4, 4), (8, 0)):
            pan = Matrix.Translation((2 * dx / SIZE[0], 2 * dy / SIZE[1], 0)) @ lens
            rv3d = SimpleNamespace(view_matrix=view, window_matrix=pan, perspective_matrix=pan @ view,
                                   as_pointer=lambda: 1)
            area = SimpleNamespace(spaces=SimpleNamespace(active=SimpleNamespace(region_3d=rv3d)), regions=[])
            region = SimpleNamespace(width=SIZE[0] * 5, height=SIZE[1] * 5)
            with bpy.context.temp_override(window=window_, area=host_area, region=host_region):
                cells = round(shows(view, pan) * 160)
                grown = round(shows(view, pan, rail_pictures.KEEP_PX) * 160)
                kept = rail_pictures.lead_view(window, area, region, found, shows=shows, last="view")[2]
                fresh = rail_pictures.lead_view(window, area, region, found, shows=shows)[2]
            readings["%d px, pan %d,%d" % (tall, dx, dy)] = {"cells": cells, "kept": kept, "fresh": fresh}
            if (dx, dy) == (0, 0):
                adopted += fresh == "view"
            readings["%d px, pan %d,%d" % (tall, dx, dy)]["grown_cells"] = grown
            check("panned %d px %d,%d: a shown view stays while grown it holds 7 cells" % (tall, dx, dy),
                  kept == ("view" if grown >= 7 else "bounds"), readings["%d px, pan %d,%d" % (tall, dx, dy)])
            check("panned %d px %d,%d: a new view needs 10" % (tall, dx, dy),
                  fresh == ("view" if cells >= 10 else "bounds"), readings["%d px, pan %d,%d" % (tall, dx, dy)])
    check("panned: Suzanne is adopted unpanned at some size", adopted >= 1, readings)
    for tall in (40, 48, 56):
        if readings["%d px, pan 0,0" % tall]["fresh"] == "view":   # The coordinator's pin: 2 px of pan never flips.
            check("panned %d px: a 2 px pan stays on the view" % tall,
                  all(readings["%d px, pan %s" % (tall, pan)]["kept"] == "view" for pan in ("2,0", "0,2", "2,2")),
                  {pan: readings["%d px, pan %s" % (tall, pan)] for pan in ("2,0", "0,2", "2,2")})
    report["pictures"]["panned"] = readings
    offscreen.free()


def redecided(window):
    """REVIEW-TILE-COST H1 and REVIEW-TILE-COST-2 M: walls fix the box; a model inside it seen by the scene camera,
    moved out of the camera's frame (still inside the box), hidden, then shown in front of the camera again. Each
    redraw decides afresh: the tile never stays on an empty camera frame, and the camera takes the picture back once
    the model fills its frame again."""
    import gpu
    scene, layer = window.scene, window.view_layer
    clear()
    box("Main Wall West", (0.2, 20.0, 20.0), (-10.0, 0.0, 0.0))
    box("Main Wall East", (0.2, 20.0, 20.0), (10.0, 0.0, 0.0))
    model = box("Main Inner Model", (1.0, 1.0, 1.0))
    scene_camera((0.0, -4.0, 0.0), (0.0, 0.0, 0.0))
    area, region = artist(*ARTIST)
    offscreen = gpu.types.GPUOffScreen(*SIZE)
    window_, host_area, host_region = host()
    shows = lambda matrix, lens: rail_pictures.shown_share(   # noqa: E731
        scene, offscreen, host_area.spaces.active, matrix, lens, layer)
    steps, first = {}, None
    for step, change in (("seen", None), ("moved_out_of_its_frame", lambda: setattr(model, "location", (0, 8, 8))),
                         ("hidden", lambda: setattr(model, "hide_viewport", True)),
                         ("shown_in_front_again", lambda: (setattr(model, "hide_viewport", False),
                                                           setattr(model, "location", (0, 0, 0))))):
        if change is not None:
            change()
        layer.update()
        rail_pictures._scene_boxes.clear()
        found = rail_pictures._scene_bounds(window)
        first = found if first is None else first
        with bpy.context.temp_override(window=window_, area=host_area, region=host_region):
            source = rail_pictures.lead_view(window, area, region, found, shows=shows)[2]
        steps[step] = {"source": source, "box": found}
        check("redecided, %s: the same box" % step, found == first, found)
    for step in ("seen", "shown_in_front_again"):
        check("redecided, %s: the camera" % step, steps[step]["source"] == "camera", steps[step])
    for step in ("moved_out_of_its_frame", "hidden"):
        check("redecided, %s: no longer the camera" % step, steps[step]["source"] != "camera", steps[step])
    report["pictures"]["redecided"] = steps
    offscreen.free()


try:
    run()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
except Exception:   # noqa: BLE001 - recorded, the run fails.
    report["error"] = traceback.format_exc()
    report["success"] = False
(OUT / "rail-main-view.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_RAIL_MAIN_VIEW", json.dumps({"success": report["success"]}), flush=True)
if not report["success"]:
    sys.exit(1)
