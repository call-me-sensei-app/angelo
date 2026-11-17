# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first four parts of ``test_blender_parallel_merge_privacy``: a part as a worker leaves it (``write_part``),
what Blender's own path walk misses or a worker can do besides (``write_kit``, BREAKIT-C7A), text that names files in
a part's folder (``write_notes``, BREAKIT-C7A-2), and what no cleaning can reach (``write_stale``). The suite's own
helpers and names (``png``, ``wav``, ``quad``, ``textured``, ``_secret``, ``OUT``, ``MARKER``, ``SIBLING``,
``WORKER_SCENE``) are given once by ``bind``.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import bpy
from angelo_agent import private_roots

png = wav = quad = textured = _secret = OUT = MARKER = SIBLING = WORKER_SCENE = None


def bind(**names):
    """The suite's helpers and names these parts use."""
    globals().update(names)


FONT = []   # The worker's font file, once copied.


def font_file(folder):
    fonts = Path(bpy.utils.system_resource("DATAFILES", path="fonts"))
    source = next((p for p in sorted(fonts.iterdir()) if p.suffix.lower() in (".ttf", ".otf", ".woff2")), None)
    if source is None:
        return None
    FONT.append(shutil.copy(source, folder / ("worker-font" + source.suffix)))
    return FONT[-1]


def write_part(part, registered, family, own):
    """The part as a worker leaves it: its file saved first (``worker_main.open_part``), built, saved again. Each part
    is saved uncompressed so its own bytes can be read: a default save compresses, and whether a path survives as
    raw bytes then is chance."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    scene = bpy.context.scene
    scene.name = WORKER_SCENE
    scene.world = bpy.data.worlds.new("WorkerWorld")
    scene.world.use_nodes = True
    environment = scene.world.node_tree.nodes.new("ShaderNodeTexEnvironment")
    environment.image = bpy.data.images.load(str(png(part.parent / "sky.png", (0.2, 0.4, 0.9, 1))))
    scene.render.filepath = str(part.parent / "renders") + os.sep
    mesh = bpy.data.meshes.new("LampMesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    base = bpy.data.objects.new("Lamp Base", mesh)
    scene.collection.objects.link(base)
    material = bpy.data.materials.new("Lamp Brass")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    for file, pack in ((png(registered / "reference.png", (1, 0, 0, 1)), False),   # A worker's view, say.
                       (png(family / "kept.png", (0, 1, 0, 1)), True),           # Packed by the worker already.
                       (png(part.parent / "baked.png", (0, 0, 1, 1)), False),     # By a path relative to the part.
                       (own, False)):                                             # The person's own texture.
        node = nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(str(file))
        if pack:
            node.image.pack()
        if file.parent == part.parent:
            node.image.filepath = "//" + file.name
    mesh.materials.append(material)
    base.modifiers.new("Waves", "OCEAN").filepath = "//ocean-cache"
    driver = base.driver_add("rotation_euler", 2).driver
    variable = driver.variables.new()
    variable.targets[0].id_type = "SCENE"
    variable.targets[0].id = scene
    variable.targets[0].data_path = "frame_current"
    driver.expression = "var * 0.01"
    base["home"] = scene
    shade = bpy.data.objects.new("Lamp Shade", bpy.data.meshes.new("ShadeMesh"))
    scene.collection.objects.link(shade)
    shade.parent = base
    label = bpy.data.objects.new("Lamp Label", bpy.data.curves.new("LabelText", "FONT"))
    scene.collection.objects.link(label)
    font = font_file(registered)
    if font is not None:
        label.data.font = bpy.data.fonts.load(str(font))
    speaker = bpy.data.objects.new("Lamp Hum", bpy.data.speakers.new("Hum"))
    scene.collection.objects.link(speaker)
    speaker.data.sound = bpy.data.sounds.load(str(wav(part.parent / "hum.wav")))
    bpy.ops.wm.save_mainfile(compress=False)
    return font is not None



def write_stale(part, state):
    """A fourth part with what no cleaning can reach, which the merge must refuse with nothing added: a multi-view
    image (each view's own path is saved where no RNA reads it) read from the state folder through the default
    workspace (``workspace/..``: the workspace is not private, the folder it leads to is, BREAKIT-C7A-3 M1), plus a
    WorkSpace an ID property names."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    scene = bpy.context.scene
    stale = bpy.data.objects.new("Stale", quad("StaleMesh"))
    scene.collection.objects.link(stale)
    scene.render.use_multiview = True
    (state / "stale-eyes").mkdir(exist_ok=True)
    png(state / "stale-eyes" / "eyes.png", (1, 0, 1, 1))
    image = bpy.data.images.load(str(state / "workspace") + os.sep + os.path.join("..", "stale-eyes", "eyes.png"))
    image.use_multiview, image.views_format = True, "STEREO_3D"
    stale.data.materials.append(textured("Eyes", image))
    stale["workspace"] = bpy.data.workspaces[0] if bpy.data.workspaces else None
    bpy.ops.wm.save_mainfile(compress=False)




def write_kit(part, workspace):
    """A second part with what Blender's own path walk misses or a worker can do besides (break-it BREAKIT-C7A):
    objects linked from a .blend in its folder (a chain: object, mesh, material, image), one local object using a
    linked mesh; a bake image never saved whose path is in its folder; a node's string, a node modifier's string
    input and a custom property naming files there; a cloth point cache and a Dynamic Paint output folder there; an
    image read through another case's spelling of its folder; and a texture in the chat's default workspace."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    assets = bpy.data.collections.new("Linked assets")
    bpy.context.scene.collection.children.link(assets)
    for index in range(3):
        image = bpy.data.images.load(str(png(part.parent / ("lib%d.png" % index), (index / 3, 0.2, 0.2, 1))))
        assets.objects.link(bpy.data.objects.new("Linked %d" % index, quad("LinkedMesh%d" % index,
                                                                           [textured("Linked%d" % index, image)])))
    library = part.parent / "assets.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(library))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    scene = bpy.context.scene
    with bpy.data.libraries.load(str(library), link=True) as (source, target):
        target.objects = list(source.objects)
    linked = bpy.data.collections.new("Kit Linked")   # The part's own collection, holding what it linked.
    scene.collection.children.link(linked)
    for obj in target.objects:
        linked.objects.link(obj)
    scene.collection.objects.link(bpy.data.objects.new("Kit Local", target.objects[0].data))
    bake = bpy.data.images.new("bake.png", 8, 8)
    bake.filepath_raw = str(part.parent / "bake.png")   # Never saved: still GENERATED.
    tree = bpy.data.node_groups.new("Importer", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    socket = tree.interface.new_socket("Source", in_out="INPUT", socket_type="NodeSocketString")
    tree.nodes.new("FunctionNodeInputString").string = str(part.parent / "node.obj")
    tree.nodes.new("NodeGroupOutput")
    socket.default_value = str(part.parent / "input.obj")
    variant = png(part.parent / "variant.png", (0, 0, 1, 1))
    spelled = Path(str(variant).replace("angelo privacy ", "ANGELO PRIVACY "))   # Another spelling of the same file,
    spelled = spelled if spelled.exists() else Path(variant)   # where the filesystem folds case (macOS, Windows).
    brick = bpy.data.images.load(str(png(workspace / "brick.png", (0.6, 0.3, 0.2, 1))))
    kit = bpy.data.objects.new("Kit", quad("KitMesh", [textured("Bake", bake), textured("Variant", bpy.data.images.load(
        str(spelled))), textured("Brick", brick)]))
    scene.collection.objects.link(kit)
    kit.modifiers.new("Importer", "NODES").node_group = tree   # Its input takes the interface's default.
    kit["source_file"] = "Built from %s, by hand" % (part.parent / "kit.fbx")
    cloth = kit.modifiers.new("Cloth", "CLOTH").point_cache
    cloth.use_disk_cache, cloth.use_external = True, True
    cloth.filepath = str(part.parent / "cloth-cache")
    cloth.use_disk_cache = False   # Blender's own path walk visits it only while it caches to disk.
    paint = kit.modifiers.new("Paint", "DYNAMIC_PAINT")
    paint.ui_type = "CANVAS"
    bpy.context.view_layer.objects.active = kit
    bpy.ops.dpaint.type_toggle(type="CANVAS")
    surface = paint.canvas_settings.canvas_surfaces[0]
    surface.surface_format = "IMAGE"
    surface.image_output_path = str(part.parent / "paint-output")
    bpy.ops.wm.save_mainfile(compress=False)
    return brick.filepath


def write_notes(part, moved):
    """A third part whose text names files in its folder, as the second review found it missed or kept
    (BREAKIT-C7A-2): an ID property's note and two-line note, a path after a person's one, a long text, bytes, a list
    and a nested list; a Text's line, a driver's expression, asset metadata and a socket's description; a worker
    folder in the temporary folder and a URL spelling; a WorkSpace an ID property names; and an image in an allocated
    folder that was moved (its owner file keeps it private)."""
    from urllib.parse import quote
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    scene, folder = bpy.context.scene, str(part.parent)
    notes = bpy.data.objects.new("Notes", quad("NotesMesh"))
    scene.collection.objects.link(notes)
    family = Path(tempfile.gettempdir()) / (private_roots.WORKER_PREFIX + MARKER)
    notes["note"] = "reference image: %s/ref.png, from the worker" % folder
    notes["two_lines"] = "first line\nsecond: %s/two.png" % folder
    notes["after_own"] = "/tmp/a.png;%s/b.png" % folder
    notes["long"] = "x" * 70000 + " %s/long.png" % folder
    notes["raw"] = ("%s/raw.png" % folder).encode()
    notes["listed"] = ["kept", "%s/listed.png" % folder]
    notes["nested"] = {"inner": ["%s/nested.png" % folder]}
    notes["family"] = "see %s/h.png" % family
    notes["url"] = "file://" + quote("%s/url.png" % folder)
    notes["json"] = json.dumps({"src": "%s%sj.png" % (folder, os.sep)})   # Its letters escaped as \\u00e9.
    notes["json_backslashed"] = json.dumps({"src": ("%s/w.png" % folder).replace("/", "\\")})
    notes["doubled"] = "cache at %s%s%sd.json" % (folder.replace(os.sep + "parallel", os.sep * 2 + "parallel"),
                                                   os.sep + "." + os.sep, "")   # Separators doubled, a "." folder.
    # Through another folder and back, and a URL of a backslashed path (Windows' "%5C", BREAKIT-C7A-6).
    notes["detour"] = "see %s" % os.path.join(str(OUT), "5.2", "..", "state", "sessions", "dd.json")
    notes["url_backslashed"] = "file://" + quote(("%s/ub.png" % folder).replace("/", "\\"))
    # Through the default workspace and back out, past a name a fixed stop would end at (BREAKIT-C7A-7 M1); many
    # detours, which a backtracking pattern took exponential time on (L1); a person's folder beside a private one.
    workspace = os.path.join(str(OUT), "state", "workspace")
    notes["workspace_space"] = "saved to %s today" % os.path.join(workspace, "My Renders", "..", "..", "sessions",
                                                                  "ws.json")
    notes["workspace_quote"] = 'open("%s")' % os.path.join(workspace, "Bob's", "..", "..", "sessions", "wq.json")
    notes["workspace_bracket"] = "at %s" % os.path.join(workspace, "take (2)", "..", "..", "sessions", "wb.json")
    notes["hops"] = "see %s%s" % (str(OUT) + os.sep, "a/" * 24 + "../" * 24 + "state/sessions/h.json")
    notes["hops_elsewhere"] = "a/" * 30 + "../" * 30 + "state"
    notes["sibling"] = "see %s" % SIBLING
    unread = bpy.data.images.load(str(_secret(part.parent / "token.png")))   # Not an image: never packed.
    notes.data.materials.append(textured("Unread", unread))
    renamed = bpy.data.objects.new("%s/scan source.obj" % folder, quad("RenamedMesh"))   # Named after a path, then
    scene.collection.objects.link(renamed)                                                # renamed: the old bytes
    renamed.name = "Renamed"                                                              # stay after its name.
    text = bpy.data.texts.new("loader.py")
    text.write("import bpy\nimg = bpy.data.images.load('%s/loaded.png')\n" % folder)
    notes["script"] = text
    driver = notes.driver_add("location", 0).driver
    driver.type, driver.expression = "SCRIPTED", "len('%s/expr.png') * 0" % folder
    notes.asset_mark()
    notes.asset_data.description = "Made from %s/asset.png" % folder
    tree = bpy.data.node_groups.new("Described", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry").description = (
        "Reads %s/socket.vdb" % folder)
    notes.modifiers.new("Described", "NODES").node_group = tree
    notes["workspace"] = bpy.data.workspaces[0]   # A worker WorkSpace (with its Screens) the part names.
    moved.mkdir(exist_ok=True)
    image = bpy.data.images.load(str(png(moved / "moved.png", (1, 1, 0, 1))))
    notes.data.materials.append(textured("Moved", image))
    bpy.ops.wm.save_mainfile(compress=False)
