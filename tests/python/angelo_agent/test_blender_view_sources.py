# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view``'s pictures beyond the viewport, in the pixels each runtime hands a model (factory-startup GUI).

doc/angelo/view_sources.rst is the contract. Every check plants something in
Blender's own data and reads it back from the payload a model would receive
(the PNG of a Codex ``inputImage`` and of a Claude ``image`` block); a picture
being returned proves nothing by itself.

* **Image data-blocks**: an 8-bit image painted in memory (a red block) is
  delivered byte for byte as Blender holds it, unsaved; a float bake target
  at linear 0.5 arrives as sRGB 188; a non-colour map arrives as its stored
  values. An image loaded from a file but never displayed is refused and is
  still not loaded afterwards (``has_data`` stays False).
* **UV layouts**: a cube's default UV map over its painted texture: orange
  edges, and the texture at each face's UV centre; from Edit Mode the same
  layout; a texture not in memory is not loaded.
* **Weights and colour attributes**: WEIGHT shows a cube whose top vertices
  weigh 1 red at the top and blue at the bottom; ATTRIBUTE shows a sphere's
  green colour attribute green.
* **What the person sees**: the window with a text editor holding a planted
  secret arrives with that editor greyed out (and the status bar); an Image
  Editor showing a red image arrives red; asking for the text editor is
  refused; no editor, space or data-block changed. Each capture is announced
  in the chat (a notice naming the editors, none for the refusal), and with
  the real controller the notice row appears in the chat's messages.
* **Render passes**: after an EEVEE F12 with Depth, Normal and Cryptomatte,
  RENDER_RESULT:Depth shows the cube nearer than the floor behind it, the
  Normal pass shows the floor facing up, Cryptomatte tells the objects apart,
  Combined is Blender's display copy, a missing pass names what exists, and
  the scene's image format is put back exactly.
* **Motion**: a cube keyed from left to right, drawn at frames 1, 5 and 9 as
  one strip with a held camera, sits further right in each tile, and the scene
  is back at its frame afterwards. With a frame-change handler that does not
  ship with the application registered, the strip is refused, names it and
  the approved job route, and changes no frame.
* **Depsgraph handlers** (review P0, mode-aware): with an add-on handler on
  ``depsgraph_update_post`` that turns the world red, a strip and a RENDERED
  look are refused in Read-only and Ask (naming it; Read-only says the job
  route is refused too) and run nothing of it; in Auto both run and name the
  handler, the look reports the world it changed (``scene_changed`` true), and
  the strip, whose frame changes need not run it, never claims the scene
  untouched.
* **Message-bus subscribers** (review P1, same rule): with an add-on's
  ``bpy.msgbus`` subscriber to ``RenderSettings.resolution_x`` and to the 3D
  view's shading, which moves the cube, a strip and a RENDERED look are
  refused in Read-only and Ask and notify nothing; in Auto both run and name
  it, it really runs, and neither result says the scene is unchanged (the
  look reports the cube it moved).
* **Every view that writes** (same rule): with a
  subscriber to the 3D view's shading, SOLID and MATERIAL captures are refused
  in Read-only and Ask and notify nobody; in Auto a capture runs, names it,
  never says unchanged, and the subscriber really runs after it; a repeat in
  Read-only runs only from the view cache. With an add-on
  depsgraph handler, a SOLID capture runs in Ask and never runs it, while with
  the cube in Sculpt Mode a MATERIAL capture is refused in Ask and in Auto
  runs it after the capture.
* **Pictures saved without writing the scene** (``view_save``): with an
  add-on depsgraph handler and message-bus subscribers to every struct a save
  could have written registered, the Render Result, its Depth, Cryptomatte and
  Combined passes and a scene-linear file are shown in Read-only, Ask and
  Auto, say the scene is unchanged and call neither (0 calls over the events
  after them). Their files equal the earlier route's (the scene's format set
  for the save), pixel for pixel and channel for channel, in the scene's AgX
  view with a look and exposure, in Standard, and with the output overriding
  the colour management.
* Nothing changed: no data-block was added or removed, the viewport's
  shading, the mode, the selection, the frame and the meshes are as before.

Run through ``run_native_suites.py --gui --include test_blender_view_sources``;
check ``view-sources.json`` for ``success`` and ``phase == "complete"``. No
provider, account or paid request.
"""

import os
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
assert not bpy.app.background, "Run in a window: the views draw on the GPU and capture editors"
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/"), \
    "Set a private %s so quit.blend cannot replace the user's session recovery file" % TEMP_VARIABLE
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-sources-qa"))
OUT.mkdir(parents=True, exist_ok=True)
OWN = OUT / "own"  # The person's own image files: never written by the views.
OWN.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

import numpy  # noqa: E402
import rendered_view_fixtures as rv  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402
from angelo_agent import scene_queries, view_cache, view_save  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

REPORT = OUT / "view-sources.json"
state = {"phase": "boot", "success": False, "checks": {}, "pixels": {}, "refusals": {}, "seconds": {},
         "paid_requests": 0, "binary": bpy.app.binary_path}
controller = rv.conversation()
emitted = []  # What the stand-in conversation was asked to show in the chat (an editor capture's notice).
controller.emit = lambda kind, data=None: emitted.append((kind, data))
references = ReferenceStore(OUT, "5b8f0e32-61c4-4b5e-9a7d-2f4c8d1e6a93")
ORANGE = (255, 140, 0)
SHADING_KEYS = ("type", "light", "color_type", "show_shadows", "show_cavity", "background_type", "show_xray",
                "use_scene_lights", "use_scene_world", "studio_light")


def check(name, condition):
    state["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


def view_area():
    return next(area for area in window().screen.areas if area.type == "VIEW_3D")


def executor():
    value = BlenderExecutor(window())
    value.studio_context, value.observation_store = controller, references
    return value


def view(**arguments):
    """One ``angelo_view`` call through the executor: ``(arguments, result)``."""
    controller._request_generation += 1
    view_cache.images.clear()
    started = time.perf_counter()
    result = executor().execute("angelo_view", arguments)
    state["last_seconds"] = round(time.perf_counter() - started, 3)
    assert isinstance(result, dict) and not result.get("error"), result
    state["views"] = state.get("views", 0) + 1
    for item in result.get("views", ()):  # Kept beside the receipt for a person to look at.
        label = "".join(c if c.isalnum() else "_" for c in str(arguments.get("image") or arguments.get("shading")))
        (OUT / ("%02d_%s.png" % (state["views"], label[:40]))).write_bytes(Path(item["path"]).read_bytes())
    return arguments, result


def picture(**arguments):
    """``view`` of an existing picture (RENDERED, SCENE_CAMERA): ``(pixels per runtime, result)``."""
    arguments = dict({"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}, **arguments)
    arguments, result = view(**arguments)
    return rv.payloads(references, arguments, result), result


def refusal(**arguments):
    try:
        picture(**arguments)
    except (RuntimeError, ValueError) as error:
        return str(error)
    return ""


def both(name, pixels, predicate):
    for runtime, rows in pixels.items():
        check("%s_%s" % (name, runtime), predicate(rows))


def artist_state():
    space = view_area().spaces.active
    layer = window().view_layer
    return {"shading": {key: rv.rna_values(space.shading).get(key) for key in SHADING_KEYS},
            "mode": bpy.context.mode, "frame": (window().scene.frame_current, window().scene.frame_subframe),
            "selection": sorted(o.name for o in layer.objects if o.select_get(view_layer=layer)),
            "active": layer.objects.active.name if layer.objects.active else None, "ids": rv.id_names()}


def png_file(path, rows):
    """An 8-bit RGBA PNG of top-first rows, written without Blender (the person's file on disk)."""
    rows = numpy.ascontiguousarray(rows, dtype=numpy.uint8)
    height, width = rows.shape[:2]
    lines = b"".join(b"\0" + rows[y].tobytes() for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(lines)) + chunk(b"IEND", b""))


def painted(name, width, height, top_rows, *, float_buffer=False, color_space=None):
    """An image whose pixels are written in memory, as paint or a bake writes them; ``top_rows`` are 0 to 1, top first.

    The colour space is set before the pixels: changing it afterwards makes Blender free the image's buffers
    (they are rebuilt from the source, and a generated image loses what was written), which a view then
    rightly refuses as not in memory.
    """
    image = bpy.data.images.new(name, width, height, alpha=True, float_buffer=float_buffer)
    if color_space is not None:
        image.colorspace_settings.name = color_space
    image.pixels.foreach_set(numpy.ascontiguousarray(top_rows[::-1], dtype=numpy.float32).reshape(-1))
    return image


def image_data():
    state["phase"] = "image_data"
    top = numpy.zeros((32, 64, 4), dtype=numpy.float32)
    top[...] = (0.2, 0.4, 0.6, 1.0)
    top[4:8, 10:14] = (1.0, 0.0, 0.0, 1.0)
    painted("Paint", 64, 32, top)
    painted("Bake", 32, 32, numpy.full((32, 32, 4), (0.5, 0.5, 0.5, 1.0), numpy.float32), float_buffer=True)
    painted("Normal Map", 16, 16, numpy.full((16, 16, 4), (0.5, 0.5, 1.0, 1.0), numpy.float32), float_buffer=True,
            color_space="Non-Color")
    file_rows = numpy.zeros((16, 16, 4), numpy.uint8)
    file_rows[...] = (10, 200, 30, 255)
    png_file(OWN / "wood.png", file_rows)
    wood = bpy.data.images.load(str(OWN / "wood.png"), check_existing=False)
    yield
    check("the_file_image_starts_unloaded", not wood.has_data)
    before = artist_state()
    pixels, result = picture(image="IMAGE:Paint")
    expected = (numpy.round(top[..., :3] * 255)).astype(numpy.int32)
    both("painted_pixels_are_delivered_exactly", pixels, lambda rows: rows.shape == (32, 64, 3) and bool((rows == expected).all()))
    check("painted_result_facts", result["rendered"]["image"] == "Paint" and result["rendered"]["unsaved_changes"]
          and result["views"][0]["framing"] == "IMAGE_DATA" and "path" not in result["rendered"])
    pixels, result = picture(image="IMAGE:Bake")
    both("a_linear_half_bake_arrives_as_srgb_188", pixels, lambda rows: bool((abs(rows - 188) <= 1).all()))
    pixels, result = picture(image="IMAGE:Normal Map")
    both("a_data_map_arrives_as_stored_values", pixels, lambda rows: rows[0, 0].tolist() == [128, 128, 255])
    check("data_map_says_stored", result["rendered"]["values"] == "STORED")
    message = refusal(image="IMAGE:" + wood.name)
    state["refusals"]["not_in_memory"] = message
    check("an_unloaded_file_image_is_refused_with_what_works", "not in memory" in message and "angelo_execute_python" in message)
    check("the_file_image_is_still_not_loaded", not wood.has_data)
    check("image_views_changed_nothing", artist_state() == before)
    state["seconds"]["image_view"] = state["last_seconds"]


def cube_with_texture():
    with rv.in_view3d():
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
    cube = bpy.context.active_object
    cube.name = "Crate"
    quarters = numpy.zeros((128, 128, 4), numpy.float32)
    quarters[...] = (0.0, 0.0, 1.0, 1.0)
    quarters[:64, :64] = (1.0, 0.0, 0.0, 1.0)   # Top-left quarter of the texture red,
    quarters[64:, 64:] = (0.0, 1.0, 0.0, 1.0)   # bottom-right green, the rest blue.
    texture = painted("Crate Colour", 128, 128, quarters)
    material = bpy.data.materials.new("Crate")
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = texture
    material.node_tree.nodes.active = node
    cube.data.materials.append(material)
    return cube, texture


def face_centres(mesh):
    """Each face's mean UV, from Blender's own arrays."""
    layer = mesh.uv_layers.active
    return [tuple(numpy.mean([tuple(layer.uv[index].vector) for index in face.loop_indices], axis=0))
            for face in mesh.polygons]


def texel(image, uv):
    """The texture's 8-bit colour at a UV position, read from Blender's own buffer (rows bottom first)."""
    width, height = image.size
    values = numpy.empty(width * height * 4, numpy.float32)
    image.pixels.foreach_get(values)
    rows = values.reshape(height, width, 4)
    row, column = min(height - 1, int(uv[1] * height)), min(width - 1, int(uv[0] * width))
    return tuple(int(round(value * 255)) for value in rows[row, column, :3])


def uv_layout():
    state["phase"] = "uv_layout"
    cube, texture = cube_with_texture()
    yield
    handle = scene_queries.object_handle(cube)
    centres = face_centres(cube.data)
    expected = [texel(texture, centre) for centre in centres]
    state["pixels"]["uv_face_centres"] = expected
    uv_before = [tuple(loop.vector) for loop in cube.data.uv_layers.active.uv]
    before = artist_state()
    pixels, result = picture(image="UV", targets=[handle], resolution=512)

    def shows_layout(rows):
        size = rows.shape[0]
        orange = int(((rows[..., 0] > 230) & (abs(rows[..., 1] - 140) < 20) & (rows[..., 2] < 30)).sum())
        inside = all(tuple(rows[min(size - 1, int((1 - v) * size)), min(size - 1, int(u * size))]) == expected[index]
                     for index, (u, v) in enumerate(centres))
        return orange > 600 and inside
    both("the_layout_is_drawn_over_its_texture", pixels, shows_layout)
    check("uv_facts", result["rendered"]["texture"] == "Crate Colour" and result["rendered"]["faces"] == 6
          and result["rendered"]["edges"] == 24 and not result["rendered"]["edit_mode"])
    with rv.in_view3d():
        bpy.ops.object.mode_set(mode="EDIT")
    yield
    edit_pixels, edit_result = picture(image="UV", targets=[handle], resolution=512)
    check("edit_mode_reads_the_live_layout", edit_result["rendered"]["edit_mode"]
          and bool((edit_pixels["codex"] == pixels["codex"]).all()))
    with rv.in_view3d():
        bpy.ops.object.mode_set(mode="OBJECT")
    yield
    unloaded = bpy.data.images.load(str(OWN / "wood.png"), check_existing=False)
    _, result = picture(image="UV:" + unloaded.name, targets=[handle])
    check("a_texture_not_in_memory_is_not_loaded", not unloaded.has_data and not result["rendered"]["texture_shown"]
          and [entry["code"] for entry in result["uncertainty"]] == ["uv_texture_not_loaded"])
    bpy.data.images.remove(unloaded)
    check("uv_views_left_the_mesh", [tuple(loop.vector) for loop in cube.data.uv_layers.active.uv] == uv_before)
    after = artist_state()
    after["ids"]["images"] = before["ids"]["images"]  # The fixture's own load and removal above.
    check("uv_views_changed_nothing", after == before)
    state["seconds"]["uv_view"] = state["last_seconds"]


def weights_and_attributes():
    state["phase"] = "weights_and_attributes"
    cube = bpy.data.objects["Crate"]
    group = cube.vertex_groups.new(name="Top")
    for vertex in cube.data.vertices:
        group.add([vertex.index], 1.0 if vertex.co.z > 0 else 0.0, "REPLACE")
    with rv.in_view3d():
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(4, 0, 0))
    sphere = bpy.context.active_object
    colours = sphere.data.color_attributes.new("Paint", "FLOAT_COLOR", "POINT")
    colours.data.foreach_set("color", [0.0, 1.0, 0.0, 1.0] * len(sphere.data.vertices))
    sphere.data.color_attributes.active_color = colours
    yield
    before = artist_state()
    arguments, result = view(targets=[scene_queries.object_handle(cube)], views=["FRONT"], shading="WEIGHT",
                             group="Top", resolution=256)
    pixels = rv.payloads(references, arguments, result)

    def top_red_bottom_blue(rows):
        column = rows.shape[1] // 2
        top, bottom = rows[int(rows.shape[0] * 0.2), column], rows[int(rows.shape[0] * 0.8), column]
        return top[0] > top[2] + 40 and bottom[2] > bottom[0] + 40
    both("weights_are_red_at_the_top_and_blue_at_the_bottom", pixels, top_red_bottom_blue)
    check("weight_facts", result["weights"][0]["group"] == "Top" and result["weights"][0]["assigned"] == 8)
    check("weight_view_changed_nothing", artist_state() == before)
    arguments, result = view(targets=[scene_queries.object_handle(sphere)], views=["FRONT"], shading="ATTRIBUTE",
                             resolution=256)
    pixels = rv.payloads(references, arguments, result)
    both("the_colour_attribute_is_shown", pixels,
         lambda rows: int(((rows[..., 1] > rows[..., 0] + 40) & (rows[..., 1] > rows[..., 2] + 40)).sum()) > 4000)
    check("attribute_view_changed_nothing", artist_state() == before)


def layout():
    """The window's editors and what they show: what an editor capture must leave as it was."""
    return [(area.ui_type, area.x, area.y, area.width, area.height,
             getattr(getattr(area.spaces.active, "image", None), "name", None),
             getattr(getattr(area.spaces.active, "text", None), "name", None)) for area in window().screen.areas]


def notice_in_a_real_chat():
    """The real controller: an editor capture asked for by a model adds a notice row the person sees in the chat."""
    from angelo_agent import ui_text
    from angelo_agent.controller import Controller
    folder = tempfile.TemporaryDirectory()
    chat = Controller(folder.name)
    try:
        chat.set_document(("view-sources-document", 1))
        chat.busy = True
        chat.state["options"]["approval"] = "READ_ONLY"  # Admitted and answered with no approval, as any view.
        replies = []
        chat.request_tool("capture", "angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED",
                                                     "image": "EDITOR:IMAGE_EDITOR"}, replies.append)
        chat.pump_tools(BlenderExecutor(window()), chat.document)
        deadline = time.monotonic() + 10
        while not (replies and any(row.get("notice") for row in chat.state["messages"])) and time.monotonic() < deadline:
            chat.pump_events()
            yield
        rows = [row for row in chat.state["messages"] if row.get("role") == "system" and row.get("notice")]
        state["chat_notice"] = [ui_text.display_notice(row) for row in rows]
        check("the_capture_notice_appears_in_the_real_chat", len(replies) == 1 and replies[0].get("views")
              and len(rows) == 1 and ui_text.display_notice(rows[0]).startswith("The agent was sent a picture of your "
                                                                                "Image Editor"))
    finally:
        chat.close()
        folder.cleanup()


def editors():
    state["phase"] = "editors"
    areas = window().screen.areas
    timeline = next(area for area in areas if area.type == "DOPESHEET_EDITOR")
    outliner = next(area for area in areas if area.type == "OUTLINER")
    original = (timeline.ui_type, outliner.ui_type)
    timeline.ui_type, outliner.ui_type = "TEXT_EDITOR", "IMAGE_EDITOR"
    yield
    secret = bpy.data.texts.new("Planted")
    secret.write("api_key = 'planted-secret'\n" * 40)
    timeline.spaces.active.text = secret
    red = painted("Red", 256, 256, numpy.full((256, 256, 4), (1.0, 0.0, 0.0, 1.0), numpy.float32))
    outliner.spaces.active.image = red
    # The capture shows what Blender has drawn; let it draw both changed editors before looking.
    for _tick in range(40):
        timeline.tag_redraw()
        outliner.tag_redraw()
        yield
        if (outliner.type, getattr(outliner.spaces.active, "image", None)) == ("IMAGE_EDITOR", red) and _tick >= 5:
            break
    state["pixels"]["image_editor_shows"] = getattr(getattr(outliner.spaces.active, "image", None), "name", None)
    check("fixture_editors_show_their_data", outliner.spaces.active.image == red
          and timeline.spaces.active.text == secret)
    before, ids = layout(), rv.id_names()
    pixels, result = picture(image="EDITOR", resolution=1024)
    facts = result["rendered"]
    check("the_window_masks_the_text_editor_and_status_bar", "TEXT_EDITOR" in facts["masked"]
          and "STATUSBAR" in facts["masked"] and "IMAGE_EDITOR" in facts["editors"])

    def text_editor_is_grey(rows):
        height, width = rows.shape[:2]
        scale = width / float(facts["window_size"][0])
        native = facts["window_size"][0] / float(max(area.x + area.width for area in areas))
        centre_x = int((timeline.x + timeline.width / 2) * native * scale)
        centre_y = height - 1 - int((timeline.y + timeline.height / 2) * native * scale)
        patch = rows[centre_y - 5:centre_y + 5, centre_x - 5:centre_x + 5]
        return bool((abs(patch - 72) <= 1).all())
    both("no_planted_secret_pixels_leave_the_window", pixels, text_editor_is_grey)
    pixels, result = picture(image="EDITOR:IMAGE_EDITOR", resolution=512)
    both("the_image_editor_shows_its_red_image", pixels,
         lambda rows: int(((rows[..., 0] > 200) & (rows[..., 1] < 60) & (rows[..., 2] < 60)).sum()) > 1000)
    check("one_editor_is_cropped", result["rendered"]["editor"] == "IMAGE_EDITOR"
          and result["rendered"]["area_size"][0] <= facts["window_size"][0])
    notices = [data for kind, data in emitted if kind == "message" and data.get("notice")]
    state["editor_notices"] = [row["text"] for row in notices]
    check("each_editor_capture_is_announced_in_the_chat", len(notices) == 2
          and "Image Editor" in notices[0]["text"] and "Text Editor" not in notices[0]["text"]
          and "your Image Editor as you see it" in notices[1]["text"]
          and all(row["role"] == "system" and row.get("i18n") for row in notices))
    message = refusal(image="EDITOR:TEXT_EDITOR")
    state["refusals"]["text_editor"] = message
    check("a_refused_capture_is_not_announced", len(emitted) == 2)
    yield from notice_in_a_real_chat()
    check("a_text_editor_is_refused", "can show code, credentials or file paths" in message)
    check("editor_captures_changed_no_editor", layout() == before)
    check("editor_captures_added_no_data", rv.id_names() == ids)
    state["seconds"]["window_capture"] = state["last_seconds"]
    timeline.ui_type, outliner.ui_type = original
    bpy.data.texts.remove(secret)
    yield


def render_passes():
    """The person's own F12 with Depth, Normal and Cryptomatte passes, each read back from the Render Result."""
    state["phase"] = "render_passes"
    scene = window().scene
    rv.reset(scene, "BLENDER_EEVEE")
    rv._plane(scene)
    layer = window().view_layer
    layer.use_pass_z = layer.use_pass_normal = layer.use_pass_cryptomatte_object = True
    yield
    with rv.in_view3d():
        bpy.ops.render.render()  # Blocking, into the person's slot of the Render Result.
    yield
    before = (rv.rna_values(scene.render), rv.rna_values(scene.view_settings), rv.id_names())
    unset = scene.render.image_settings.linear_colorspace_settings.name == ""
    pixels, result = picture(image="RENDER_RESULT:Depth", resolution=384)
    facts = result["rendered"]
    state["pixels"]["depth_range"] = facts.get("range")
    state["pass_side_effects"] = result["side_effects"]
    # A factory scene's output colour space is unset; any format change names its default and Python cannot
    # unset it again. The first save says so precisely and leaves nothing else changed.
    named = unset and view_save.native() is None  # The settings route names it; save_copy sets nothing.
    check("an_unset_output_colour_space_is_reported_not_hidden", [entry["code"] for entry in result["side_effects"]]
          == (["output_color_space_named"] if named else []))
    check("depth_is_a_stated_range", facts["mapping"] == "RANGE" and facts["range"][0] < facts["range"][1]
          and facts["layer"] == layer.name)
    both("the_cube_is_nearer_than_the_floor_behind_it", pixels,
         lambda rows: rows[rows.shape[0] // 2, rows.shape[1] // 2, 0] > rows[3, rows.shape[1] // 2, 0] + 20)
    pixels, result = picture(image="RENDER_RESULT:Normal", resolution=384)
    both("the_floor_faces_up_in_the_normal_pass", pixels,
         lambda rows: bool((abs(rows[-3, rows.shape[1] // 2] - [128, 128, 255]) <= 8).all()))
    _, result = picture(image="RENDER_RESULT:CryptoObject00", resolution=384)
    state["pixels"]["cryptomatte"] = {key: value for key, value in result["rendered"].items() if key != "color"}
    check("cryptomatte_tells_the_objects_apart", result["rendered"].get("ids", 0) >= 2)
    _, result = picture(image="RENDER_RESULT:Combined", resolution=384)
    check("a_colour_pass_is_a_display_copy", result["rendered"]["values"] == "DISPLAY")
    message = refusal(image="RENDER_RESULT:Mist")
    state["refusals"]["missing_pass"] = message
    check("a_missing_pass_names_what_the_render_holds", "no pass Mist" in message and "Depth" in message)
    after = (rv.rna_values(scene.render), rv.rna_values(scene.view_settings), rv.id_names())
    named = "/image_settings/linear_colorspace_settings/name: '' -> "
    changed = [entry for entry in rv.differences(before[0], after[0]) if not (unset and entry.startswith(named))]
    state["pass_differences"] = (changed + rv.differences(before[1], after[1])
                                 + rv.differences(before[2], after[2]))[:20]
    check("pass_views_put_the_image_format_back", changed == []
          and rv.differences(before[1], after[1]) == [] and before[2] == after[2])
    state["seconds"]["pass_view"] = state["last_seconds"]


def motion():
    """A cube animated along X, high above the rest: a strip of frames shows it move, the frame put back."""
    state["phase"] = "motion"
    scene = window().scene
    with rv.in_view3d():
        bpy.ops.mesh.primitive_cube_add(size=1, location=(-3, -8, 20))
    mover = bpy.context.active_object
    mover.name = "Mover"
    mover.keyframe_insert("location", frame=1)
    mover.location.x = 3
    mover.keyframe_insert("location", frame=9)
    scene.frame_set(5)
    yield
    calls = []

    def planted_frame_handler(changed_scene, *args):  # An add-on's handler: not shipped with the application.
        calls.append(changed_scene.frame_current)
    strip = {"targets": [scene_queries.object_handle(mover)], "views": ["FRONT"], "shading": "SOLID",
             "frames": [1, 5, 9], "resolution": 768}
    bpy.app.handlers.frame_change_post.append(planted_frame_handler)
    try:
        view(**strip)
        message = ""
    except RuntimeError as error:
        message = str(error)
    finally:
        bpy.app.handlers.frame_change_post.remove(planted_frame_handler)
    state["refusals"]["strip_with_add_on_handler"] = message
    check("a_strip_that_would_run_add_on_python_is_refused", "runs Python from add-ons or scripts" in message
          and "planted_frame_handler" in message and "angelo_job_start RENDER_ANIMATION" in message)
    check("the_refused_strip_changed_no_frame", calls == [] and scene.frame_current == 5)
    before = artist_state()
    arguments, result = view(**strip)  # The factory scene with Angelo's own handlers only: automatic.
    pixels = rv.payloads(references, arguments, result)
    strip = result["views"][0]
    check("strip_facts", strip["framing"] == "STRIP" and strip["frames"] == [1, 5, 9] and strip["grid"] == [3, 1]
          and result["missing_frames"] == [] and result["frame"] == 5)
    width, height = strip["tile"]

    def moves_right(rows):
        centres = []
        for index in range(3):
            tile = rows[:height, index * (width + 2):index * (width + 2) + width]
            ink = (abs(tile - tile[-2, -2]).max(axis=2) > 30)
            ink[:20, :40] = False  # The frame label.
            columns = numpy.nonzero(ink)[1]
            centres.append(columns.mean() if len(columns) else -1)
        state["pixels"]["strip_centres"] = centres
        return centres[0] >= 0 and centres[0] + 20 < centres[1] and centres[1] + 20 < centres[2]
    both("the_cube_moves_right_across_the_strip", pixels, moves_right)
    check("the_frame_and_viewport_are_put_back", artist_state() == before and scene.frame_current == 5)
    state["seconds"]["strip_three_frames"] = state["last_seconds"]


def planted_depsgraph_handler(scene, depsgraph=None):  # An add-on's: its code is outside the shipped scripts.
    DEPSGRAPH_CALLS.append(scene.name)
    if scene.world is not None:
        scene.world.color = (1.0, 0.0, 0.0)  # The reviewer's proof of an unapproved scene change.


DEPSGRAPH_CALLS = []


def attempt(arguments):
    """Generator: one ``angelo_view`` call, ``(result, refusal text)``. A pending one (a RENDERED look, or a MATERIAL
    whose shaders are still compiling: ``view_warm``) is polled through the event loop, as ``Controller._pump_events``
    does. A refusal is raised at once, never the end of a pending call (a refused view neither waits nor draws first),
    so a pending answer has no refusal text."""
    controller._request_generation += 1
    try:
        result = executor().execute("angelo_view", arguments)
    except (RuntimeError, ValueError) as error:
        return None, str(error)
    if not isinstance(result, dict):
        result = yield from rv.pending_result(result)
    return result, ""


def depsgraph_handlers():
    """Review P0, mode-aware: Read-only and Ask refuse, Auto runs, names the handler and reports its change."""
    state["phase"] = "depsgraph_handlers"
    scene = window().scene
    world = scene.world
    original = tuple(world.color)
    strip = {"targets": [], "views": ["FRONT"], "shading": "SOLID", "frames": [1, 3], "resolution": 256}
    look = {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}
    window().view_layer.update()  # Nothing pending before the add-on's handler is registered.
    bpy.app.handlers.depsgraph_update_post.append(planted_depsgraph_handler)
    try:
        for mode in ("READ_ONLY", "ASK"):
            controller.state["options"] = {"approval": mode}
            for name, arguments in (("strip", strip), ("material_strip", dict(strip, shading="MATERIAL")),
                                    ("lit_strip", dict(strip, shading="LIT")), ("look", look)):
                _result, message = yield from attempt(arguments)
                state["refusals"]["%s_%s_with_depsgraph_handler" % (name, mode.lower())] = message
                check("%s_refused_in_%s" % (name, mode.lower()), "planted_depsgraph_handler" in message
                      and ("Read-only" in message) == (mode == "READ_ONLY"))
        check("refused_views_ran_no_add_on_python", DEPSGRAPH_CALLS == [] and tuple(world.color) == original)
        controller.state["options"] = {"approval": "AUTO_BOUNDED"}
        result, message = yield from attempt(strip)
        check("strip_runs_in_auto", result is not None and not message)
        state["auto_strip"] = {key: result.get(key) for key in ("scene_changed", "changed_during_view", "side_effects")}
        check("auto_strip_names_the_handler", any(entry["code"] == "extension_handlers_ran"
                                                  and "planted_depsgraph_handler" in entry["detail"]
                                                  for entry in result["side_effects"]))
        # Frame changes need not run depsgraph handlers (the reviewer saw none on frame_set): never "unchanged".
        ran, red = list(DEPSGRAPH_CALLS), tuple(world.color) != original
        state["auto_strip"].update(handler_calls=len(ran), world_changed=red)
        check("auto_strip_never_claims_the_scene_untouched", result["scene_changed"] is not False
              and (not red or "data-block World" in (result.get("changed_during_view") or [])))
        bpy.app.handlers.depsgraph_update_post.remove(planted_depsgraph_handler)  # Reset without running it.
        world.color = original
        window().view_layer.update()
        DEPSGRAPH_CALLS.clear()
        bpy.app.handlers.depsgraph_update_post.append(planted_depsgraph_handler)
        result, message = yield from attempt(look)
        check("look_runs_in_auto", result is not None and not message and not result.get("error"))
        state["auto_look"] = {key: result.get(key) for key in ("scene_changed", "changed_during_view", "side_effects")}
        state["auto_look"]["handler_calls"] = len(DEPSGRAPH_CALLS)
        check("auto_look_names_the_handler_and_reports_its_change", result.get("scene_changed") is True
              and "data-block World" in result.get("changed_during_view", [])
              and any(entry["code"] == "extension_handlers_ran" for entry in result.get("side_effects", [])))
    finally:
        if planted_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(planted_depsgraph_handler)
        controller.state.pop("options", None)
        world.color = original


MSGBUS_CALLS = []


def planted_msgbus_subscriber(*_args):  # An add-on's: its code is outside the shipped scripts.
    MSGBUS_CALLS.append(len(MSGBUS_CALLS))
    cube = bpy.data.objects.get("Cube")
    if cube is not None:
        cube.location.x += 0.5  # The reviewer's proof of an unapproved scene change.


def message_bus():
    """Review P1, mode-aware: msgbus subscribers to a view's own settings count like depsgraph handlers."""
    state["phase"] = "message_bus"
    cube = bpy.data.objects["Cube"]
    original, owner = tuple(cube.location), object()
    strip = {"targets": [], "views": ["FRONT"], "shading": "SOLID", "frames": [1, 3], "resolution": 256}
    look = {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}
    for key in ((bpy.types.RenderSettings, "resolution_x"), bpy.types.View3DShading):
        bpy.msgbus.subscribe_rna(key=key, owner=owner, args=(), notify=planted_msgbus_subscriber)
    try:
        for mode in ("READ_ONLY", "ASK"):
            controller.state["options"] = {"approval": mode}
            for name, arguments in (("strip", strip), ("look", look)):
                _result, message = yield from attempt(arguments)
                state["refusals"]["%s_%s_with_msgbus_subscriber" % (name, mode.lower())] = message
                check("%s_refused_for_msgbus_in_%s" % (name, mode.lower()), "planted_msgbus_subscriber" in message
                      and "message bus" in message)
        for _tick in range(5):
            yield  # Blender handles the message bus on its events: a refused view notified nobody.
        check("refused_views_notified_no_subscriber", MSGBUS_CALLS == [] and tuple(cube.location) == original)
        controller.state["options"] = {"approval": "AUTO_BOUNDED"}
        result, message = yield from attempt(strip)
        check("msgbus_strip_runs_in_auto", result is not None and not message)
        state["msgbus_strip"] = {key: result.get(key) for key in ("scene_changed", "changed_during_view")}
        check("msgbus_strip_names_it_and_never_claims_the_scene_untouched", result["scene_changed"] is not False
              and any(entry["code"] == "extension_handlers_ran" and "planted_msgbus_subscriber" in entry["detail"]
                      for entry in result["side_effects"]))
        yield from rv.wait_for(lambda: MSGBUS_CALLS, 30, "the strip's subscriber")  # After the strip ended.
        state["msgbus_strip"]["calls_after_it"] = len(MSGBUS_CALLS)
        for _tick in range(3):
            yield
        cube.location = original
        MSGBUS_CALLS.clear()
        result, message = yield from attempt(look)
        check("msgbus_look_runs_in_auto", result is not None and not message and not result.get("error"))
        state["msgbus_look"] = {key: result.get(key) for key in ("scene_changed", "changed_during_view")}
        state["msgbus_look"]["calls"] = len(MSGBUS_CALLS)
        check("msgbus_look_subscriber_ran_during_it", MSGBUS_CALLS != [])
        check("msgbus_look_names_it_and_reports_the_cube", result.get("scene_changed") is True
              and "object Cube" in (result.get("changed_during_view") or [])
              and any("planted_msgbus_subscriber" in entry["detail"] for entry in result.get("side_effects", [])))
    finally:
        bpy.msgbus.clear_by_owner(owner)
        controller.state.pop("options", None)
        cube.location = original


VIEW_CALLS = []


def planted_view_subscriber(*_args):  # An add-on's: its code is outside the shipped scripts.
    VIEW_CALLS.append(len(VIEW_CALLS))


def every_view():
    """Captures and saved pictures follow the mode-aware rule of looks and strips."""
    state["phase"] = "every_view"
    scene, owner = window().scene, object()
    world, original = scene.world, tuple(scene.world.color)
    solid = {"targets": [], "views": ["FRONT"], "shading": "SOLID", "resolution": 256}
    material = dict(solid, shading="MATERIAL")
    for key in (bpy.types.View3DShading,):
        bpy.msgbus.subscribe_rna(key=key, owner=owner, args=(), notify=planted_view_subscriber)
    cube, active = bpy.data.objects["Cube"], window().view_layer.objects.active
    try:
        for mode in ("READ_ONLY", "ASK"):
            controller.state["options"] = {"approval": mode}
            for name, arguments in (("solid", solid), ("material", material)):
                _result, message = yield from attempt(arguments)
                state["refusals"]["%s_%s_with_view_subscriber" % (name, mode.lower())] = message
                check("%s_refused_for_a_view_subscriber_in_%s" % (name, mode.lower()),
                      "planted_view_subscriber" in message)
        for _tick in range(5):
            yield
        check("refused_views_notified_no_view_subscriber", VIEW_CALLS == [])
        controller.state["options"] = {"approval": "AUTO_BOUNDED"}
        for name, arguments in (("solid", solid),):
            result, message = yield from attempt(arguments)
            state.setdefault("every_view", {})[name] = message or {key: result.get(key) for key in (
                "scene_changed", "uncertainty")}
            check("%s_runs_in_auto_and_never_says_unchanged" % name, result is not None and not message
                  and result["scene_changed"] is None and any("planted_view_subscriber" in entry["detail"]
                                                               for entry in result["side_effects"]))
            yield from rv.wait_for(lambda: VIEW_CALLS, 30, "the %s view's subscriber" % name)
            VIEW_CALLS.clear()
        controller.state["options"] = {"approval": "READ_ONLY"}
        # Served from the cache it writes nothing; drawn again it is refused.
        result, message = yield from attempt(solid)
        cached = result is not None and result["views"][0]["cache_hit"]
        state["every_view"]["read_only_repeat"] = {"cache_hit": cached, "refusal": message[:120]}
        check("a_read_only_repeat_runs_only_from_the_cache", (cached and result["scene_changed"] is False)
              or (result is None and "planted_view_subscriber" in message))
        bpy.msgbus.clear_by_owner(owner)
        window().view_layer.update()
        bpy.app.handlers.depsgraph_update_post.append(planted_depsgraph_handler)
        DEPSGRAPH_CALLS.clear()
        controller.state["options"] = {"approval": "ASK"}
        for _tick in range(5):
            yield  # A baseline: nothing else updates the depsgraph meanwhile.
        baseline = list(DEPSGRAPH_CALLS)
        result, message = yield from attempt(dict(solid, resolution=384))  # Not cached: drawn.
        for _tick in range(5):
            yield
        state["every_view"]["solid_beside_a_handler"] = {
            "baseline_calls": len(baseline), "calls": len(DEPSGRAPH_CALLS), "refusal": message[:160],
            "scene_changed": result.get("scene_changed") if result else "refused"}
        check("solid_capture_runs_in_ask_beside_a_depsgraph_handler_and_never_runs_it", result is not None
              and result["scene_changed"] is False and DEPSGRAPH_CALLS == [])
        bpy.app.handlers.depsgraph_update_post.remove(planted_depsgraph_handler)
        window().view_layer.objects.active = cube
        with rv.in_view3d():
            bpy.ops.object.mode_set(mode="SCULPT")
        for _tick in range(5):
            yield
        world.color = original
        window().view_layer.update()
        DEPSGRAPH_CALLS.clear()
        bpy.app.handlers.depsgraph_update_post.append(planted_depsgraph_handler)
        _result, message = yield from attempt(material)
        state["refusals"]["material_ask_with_a_sculpt_session"] = message
        check("material_capture_with_a_sculpt_session_refused_in_ask", "planted_depsgraph_handler" in message)
        controller.state["options"] = {"approval": "AUTO_BOUNDED"}
        result, message = yield from attempt(material)
        check("material_capture_runs_in_auto_and_never_says_unchanged", result is not None and not message
              and result["scene_changed"] is None)
        yield from rv.wait_for(lambda: DEPSGRAPH_CALLS, 30, "the depsgraph handler after the capture")
    finally:
        bpy.msgbus.clear_by_owner(owner)
        if planted_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(planted_depsgraph_handler)
        if cube.mode != "OBJECT":
            with rv.in_view3d():
                bpy.ops.object.mode_set(mode="OBJECT")
        window().view_layer.objects.active = active
        controller.state.pop("options", None)
        world.color = original


PICTURE_CALLS = []


def planted_picture_listener(*_args, **_kwargs):  # An add-on's handler and subscriber, outside the shipped scripts.
    PICTURE_CALLS.append(len(PICTURE_CALLS))


def rgba(path):
    """A PNG's pixels as Blender decodes them (``imbuf``: no Image data-block)."""
    import imbuf
    buffer = imbuf.load(str(path))
    try:
        with buffer.with_buffer() as view:
            return numpy.array(view, dtype=numpy.uint8)
    finally:
        buffer.free()


def exr(path):
    """``{(part, channel): float32 rows}`` of every channel of an OpenEXR (Angelo's own reader)."""
    from angelo_agent.view_exr import read_channels, read_parts
    with open(path, "rb") as handle:
        parts, _multipart = read_parts(handle)
    return {(part.name, name): rows for part in parts
            for name, rows in read_channels(path, part.index, [entry[0] for entry in part.channels]).items()}


def same_files(native, earlier):
    """Whether two saves of one picture match: PNG pixels, or every OpenEXR channel bit for bit."""
    if str(native).endswith(".exr"):
        first, second = exr(native), exr(earlier)
        return first.keys() == second.keys() and all(numpy.array_equal(first[key].view(numpy.uint32),
                                                                       second[key].view(numpy.uint32)) for key in first)
    return numpy.array_equal(rgba(native), rgba(earlier))


def pictures_without_writes():
    """Saved pictures never write the scene, so they run no add-on Python in any mode."""
    state["phase"] = "pictures_without_writes"
    from angelo_agent.view_exr import write_rgba
    from angelo_agent.view_render_restore import picture_values
    scene, owner = window().scene, object()
    check("picture_helper_available", view_save.native() is not None)
    result_image = next(image for image in bpy.data.images if image.type == "RENDER_RESULT")
    linear = OUT / "linear.exr"
    write_rgba(linear, numpy.tile(numpy.array([0.18, 0.5, 2.0, 1.0], numpy.float32), (24, 32, 1)))
    window().view_layer.update()
    for key in (bpy.types.ImageFormatSettings, bpy.types.RenderSettings, bpy.types.ColorManagedViewSettings,
                bpy.types.ColorManagedDisplaySettings, bpy.types.FFmpegSettings, bpy.types.FCurve, bpy.types.Image,
                bpy.types.Scene):
        bpy.msgbus.subscribe_rna(key=key, owner=owner, args=(), notify=planted_picture_listener)
    bpy.app.handlers.depsgraph_update_post.append(planted_picture_listener)
    ids, receipts = rv.id_names(), {}
    try:
        for mode in ("READ_ONLY", "ASK", "AUTO_BOUNDED"):
            controller.state["options"] = {"approval": mode}
            for image in ("RENDER_RESULT", "RENDER_RESULT:Depth", "RENDER_RESULT:CryptoObject00",
                          "RENDER_RESULT:Combined"):
                _pixels, result = picture(image=image)
                receipts["%s %s" % (mode, image)] = (result["scene_changed"], [entry["code"] for entry in
                                                                               result["side_effects"]])
            for _tick in range(5):
                yield  # Blender handles depsgraph updates and the message bus on its events.
        state["pictures"] = {"receipts": receipts, "calls": len(PICTURE_CALLS)}
        check("pictures_shown_in_every_mode_unchanged", all(changed is False and "extension_handlers_ran" not in codes
                                                            for changed, codes in receipts.values()))
        check("pictures_ran_no_add_on_python", PICTURE_CALLS == [])
        check("pictures_added_and_removed_no_data_block", rv.id_names() == ids)
    finally:
        bpy.msgbus.clear_by_owner(owner)
        bpy.app.handlers.depsgraph_update_post.remove(planted_picture_listener)
        controller.state.pop("options", None)
    # Parity with the earlier route, which set the scene's own format for the save and put it back.
    view, settings = scene.view_settings, scene.render.image_settings
    before = (view.view_transform, view.look, view.exposure, settings.color_management)
    parity, folder = {}, Path(tempfile.mkdtemp(dir=OUT))
    try:
        for case, (transform, look, exposure, management) in {
                "agx_punchy": ("AgX", "AgX - Punchy", -0.5, "FOLLOW_SCENE"),
                "standard": ("Standard", "None", 0.0, "FOLLOW_SCENE"),
                "output_override": ("AgX", "None", 0.5, "OVERRIDE")}.items():
            view.view_transform, view.look, view.exposure = transform, look, exposure
            settings.color_management = management
            if management == "OVERRIDE":
                settings.view_settings.view_transform = "Standard"  # The output's own, which a view never uses.
            for name, image, source in (("render_result", result_image, None), ("linear_file", None, linear)):
                native, earlier = folder / (case + name + "_native.png"), folder / (case + name + "_earlier.png")
                view_save.display_copy(scene, native, image=image, source=source)
                values = picture_values(scene)[0]
                with view_save._loaded(image, source) as loaded:
                    view_save._by_settings(scene, values, loaded, earlier)
                parity[case + " " + name] = same_files(native, earlier)
        native, earlier = folder / "passes_native.exr", folder / "passes_earlier.exr"
        view_save.multilayer_copy(scene, result_image, native)
        values = {"render.image_settings." + name: value for name, value in view_save.MULTILAYER.items()}
        view_save._by_settings(scene, values, result_image, earlier)
        parity["multilayer passes"] = same_files(native, earlier)
        parity["multilayer channels"] = sorted({channel for _part, channel in exr(native)})[:12]
    finally:
        view.view_transform, view.look, view.exposure, settings.color_management = before
    state["picture_parity"] = parity
    check("pictures_equal_the_earlier_route", all(value for key, value in parity.items() if key != "multilayer channels"))


def scenario():
    state["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    yield
    yield from image_data()
    yield from uv_layout()
    yield from weights_and_attributes()
    yield from editors()
    yield from render_passes()
    yield from motion()
    yield from depsgraph_handlers()
    yield from message_bus()
    yield from every_view()
    yield from pictures_without_writes()
    check("own_files_untouched", sorted(path.name for path in OWN.iterdir()) == ["wood.png"])


fixture_boot.drive(scenario, state, REPORT, "ANGELO_VIEW_SOURCES")
