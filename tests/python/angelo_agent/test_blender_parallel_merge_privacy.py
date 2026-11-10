# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing of a worker's own reaches the person's file through a merge (``worker_merge_privacy``, break-it E2E-20
F-L-2, privacy).

A part is saved as a worker saves it (its part file opened, then saved): a driver and an ID property targeting the
worker's Scene, which has its own world; images, a font and a sound read from Angelo's private folders (the part's
folder, a registered worker folder, a worker-family folder an earlier process left), one already packed, one by a
path relative to the part; an Ocean cache folder there, which cannot be packed; and a texture of the person's own,
outside every private folder. The lead merges it as an Auto edit into an unsaved file, saves it, and after Undo
merges it again into that file, now saved (where a path could be made relative to it), and saves. The oracle is the
saved file's own bytes: every private folder's name carries one random marker, and no byte sequence of it may be in
the file (absolute or relative, in any datablock, a library, a weak reference or a packed file's path); after Undo
and Redo of the merge (the Undo step autosave writes) too. Also
checked: one scene, the driver and the property read the person's scene, no library, the private images, font and
sound packed and still drawing, the person's own texture where it was, the cache reported, and the answer's
``also_added``. A second part holds what Blender's own path walk misses and what its review found (BREAKIT-C7A): a chain
linked from a .blend in its folder, a bake image never saved, a node's string, a node modifier's input, a custom
property, a point cache and a Dynamic Paint folder, a case spelling of its folder, and a texture in the chat's default
workspace, which stays where it is. A fifth part brings what a worker's files carry besides what Blender reads, and
two more spell a private path as text transports do; one has a long text (BREAKIT-C7A-5,
``parallel_merge_privacy_media``). No provider or paid call.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid
import wave
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
MARKER = "pv" + uuid.uuid4().hex[:12]   # In every private folder's name: found in a saved file, it is a leak.
BASE = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.gettempdir())
# A space, as macOS's Application Support (BREAKIT-C7A-2 H1), a capital letter JSON escapes and only text folds
# (BREAKIT-C7A-3 H2, BREAKIT-C7A-4 H1), and one that folds to two letters ("İ", BREAKIT-C7A-5 M3).
OUT = BASE / ("angelo privacy \u00c4\u0130 " + MARKER)
OUT.mkdir(parents=True, exist_ok=True)
PERSON = Path(tempfile.mkdtemp(prefix="angelo-person-"))   # The person's own folder: never private, no marker.
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (private_roots, ui, worker_merge_media, worker_merge_verify, worker_paths,  # noqa: E402
                          worker_sessions)
import parallel_merge_privacy_media as media  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform}
HITS = report["details"]["verifier_hits"] = []   # What the merge's own check found, so a refusal says what it saw.
_hit = worker_merge_verify._hit


def _recorded_hit(private, chunk):
    found = _hit(private, chunk)
    if found is not None and len(HITS) < 12:
        context = str(found)[-200:]
        for text in worker_merge_verify.views(chunk):   # A folder's bare name: the text around it.
            at = text.casefold().find(str(found).casefold())
            if at >= 0 and found in private.names:
                context = text[max(0, at - 160):at + 60]
                break
        HITS.append(context)
    return found


worker_merge_verify._hit = _recorded_hit
WORKER_SCENE = "WorkerScene-" + MARKER


def check(name, passed, details=None):
    report["checks"][name] = bool(passed)
    report["details"][name] = details


def lead_merge(window, lead, parts, timeout=120):
    """The lead merges ``parts`` ({number: placements}) as an Auto edit (as the materials suite); its answer."""
    answers = []
    arguments = {"parts": [{"worker": number, "placements": placements} for number, placements in parts.items()]}
    lead.request_tool("call-%d" % time.monotonic_ns(), "angelo_workers_merge", arguments, answers.append)

    def apply_mutation(target, call):
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    started = time.perf_counter()
    while not answers and time.perf_counter() - started < timeout:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply_mutation,
                              budget=ToolDispatchBudget(seconds=60))
    assert answers, "the merge was not answered"
    lead.close_external_read(lead.fence)
    lead.fence = lead.open_external_read()
    return answers[0]


def png(path, colour):
    image = bpy.data.images.new(path.stem, 4, 4)
    image.pixels = list(colour) * 16
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


def wav(path):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 800)
    return path


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


def leaks(path, kept=()):
    """Where the saved file holds a private folder's marker (in any case) or the worker's scene name, besides the
    ``kept`` paths, which are the person's: up to 4 places, each with the bytes around it, so a failure says what it
    found."""
    data, found = Path(path).read_bytes().lower(), []
    for allowed in kept:
        for spelled in {allowed, allowed.replace("\\", "/")}:
            data = data.replace(spelled.encode("utf-8").lower(), b"")   # Folded as the bytes are (ASCII).
    for word in (MARKER.lower().encode("utf-8"), WORKER_SCENE.lower().encode("utf-8")):
        at = data.find(word)
        while at >= 0 and len(found) < 4:
            found.append(data[max(0, at - 120):at + 40].decode("latin-1"))
            at = data.find(word, at + 1)
    return found


def quad(name, materials=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    for material in materials:
        mesh.materials.append(material)
    return mesh


def textured(name, image):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.new("ShaderNodeTexImage").image = image
    return material


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
    spelled = Path(str(png(part.parent / "variant.png", (0, 0, 1, 1))).replace("angelo privacy ", "ANGELO PRIVACY "))
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


SECRET = "angelo-secret-" + MARKER   # A private file's contents that must never be packed into the person's file.


def _secret(path):
    path.write_text(SECRET)
    return path


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


# A sound is kept only decoded and written again, by Blender's audio library; a build without one (Lite) detaches it.
AUDIO = importlib.util.find_spec("aud") is not None
NAMES = {1: "Lamp", 2: "Kit", 3: "Notes", 4: "Stale", 5: "Media", 6: "Plus", 7: "Wide", 8: "Long"}


def lead(paths):
    """The lead's chat in a new file, with ``paths`` ({number: part file}) as its workers' parts."""
    bpy.ops.wm.read_factory_settings()
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    window = bpy.context.window_manager.windows[0]
    controller = ui.get_controller()
    controller.state["options"] = dict(controller.state.get("options") or {}, approval="AUTO_BOUNDED")
    controller.set_document(ui.document_key())
    controller.fence = controller.open_external_read()
    worker_sessions._started[controller.state["id"]] = ["qa-%d" % number for number in paths]
    for number, path in paths.items():
        worker_sessions._parts["qa-%d" % number] = (number, NAMES[number], path)
    bpy.ops.ed.undo_push(message="Before the merge")
    return window, controller


def inspect(label, answer, saved):
    check(label + "_merged", answer.get("success") is True, answer)
    check(label + "_saved_file_holds_nothing_private", not leaks(saved), leaks(saved))
    check(label + "_one_scene", [s.name for s in bpy.data.scenes] == ["Scene"], [s.name for s in bpy.data.scenes])
    check(label + "_no_library", not list(bpy.data.libraries), [l.filepath for l in bpy.data.libraries])
    check(label + "_no_worker_world", "WorkerWorld" not in bpy.data.worlds, list(bpy.data.worlds.keys()))
    base = bpy.data.objects.get("Lamp Base")
    target = base.animation_data.drivers[0].driver.variables[0].targets[0].id if base else None
    check(label + "_driver_reads_the_persons_scene", target == bpy.data.scenes["Scene"], getattr(target, "name", None))
    check(label + "_property_names_the_persons_scene",
          base is not None and base.get("home") == bpy.data.scenes["Scene"])
    weak = [item.name for kind in ("meshes", "materials", "images", "worlds", "sounds", "fonts", "curves")
            for item in getattr(bpy.data, kind)
            if item.library_weak_reference is not None and MARKER in item.library_weak_reference.filepath]
    check(label + "_no_weak_reference_path", not weak, weak)
    private = sorted(i.name for i in bpy.data.images if i.name in ("reference.png", "kept.png", "baked.png"))
    check(label + "_private_images_packed", private == ["baked.png", "kept.png", "reference.png"]
          and all(bpy.data.images[n].packed_file for n in private), [(n, bool(bpy.data.images[n].packed_file))
                                                                     for n in private])
    own = bpy.data.images.get("own.png")
    check(label + "_persons_own_texture_where_it_was", own is not None and not own.packed_file and Path(
        bpy.path.abspath(own.filepath)).resolve() == (PERSON / "textures" / "own.png").resolve(),
          getattr(own, "filepath", None))
    also = answer.get("also_added") or {}
    report["details"][label + "_not_kept"] = list(worker_merge_media.failures)   # Why a file was not kept.
    check(label + "_answer_lists_what_else_came", also.get("datablocks", {}).get("meshes") == 2
          and also.get("datablocks", {}).get("images") == 4 and "scenes" not in also.get("datablocks", {})
          and also.get("scene_users") == ["Lamp Base"]
          and "Lamp Base" in [item["name"] for item in answer["changes"]["added"]]
          and ("hum.wav" in also.get("packed", [])) == AUDIO and "Lamp Base" in also.get("detached", []), also)


def kit_checks(answer, saved, brick):
    """The kit merged into the saved file, then saved again: what Blender's path walk misses is cut too."""
    check("kit_merged", answer.get("success") is True, answer)
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    check("kit_saved_file_holds_nothing_private", not leaks(saved, [brick]), leaks(saved, [brick]))
    linked = sorted(item.name for kind in ("objects", "meshes", "materials", "images")
                    for item in getattr(bpy.data, kind) if item.library is not None)
    check("kit_nothing_linked_from_its_folder", not linked and not list(bpy.data.libraries), linked)
    chain = {name: name in bpy.data.objects for name in ("Linked 0", "Linked 2", "Kit Local")}
    chain.update({name: bool(getattr(bpy.data.images.get(name), "packed_file", None))
                  for name in ("lib0.png", "lib1.png", "lib2.png")})
    check("kit_linked_chain_is_ordinary_data", all(chain.values()), chain)
    bake = bpy.data.images.get("bake.png")
    check("kit_bake_image_kept_its_pixels", bake is not None and bake.size[0] == 8 and MARKER not in bake.filepath_raw,
          getattr(bake, "filepath_raw", None))
    kit = bpy.data.objects["Kit"]
    texts = [bpy.data.node_groups["Importer"].nodes["String"].string, kit["source_file"],
             kit.modifiers["Cloth"].point_cache.filepath,
             kit.modifiers["Paint"].canvas_settings.canvas_surfaces[0].image_output_path]
    modifier = kit.modifiers["Importer"]
    stack = [modifier.properties.bl_system_properties_get()]   # Its inputs' values, Blender's own ID properties.
    while stack:
        group = stack.pop()
        for key in group.keys() if group is not None else ():
            value = group[key]
            texts.append(value) if isinstance(value, str) else stack.append(value) if hasattr(value, "keys") else None
    check("kit_strings_keep_only_file_names", all(MARKER not in text.lower() for text in texts)
          and "node.obj" in texts[0] and texts[1] == "Built from kit.fbx, by hand", texts)
    variant = bpy.data.images.get("variant.png")
    check("kit_case_spelling_packed", variant is not None and bool(variant.packed_file),
          getattr(variant, "filepath", None))
    own = bpy.data.images.get("brick.png")
    check("kit_default_workspace_texture_where_it_was", own is not None and not own.packed_file
          and own.filepath == brick, getattr(own, "filepath", None))
    also = answer.get("also_added") or {}
    cleared = also.get("cleared", [])
    check("kit_answer_names_what_was_cleared", "Kit" in cleared and "Importer" in cleared, also)


def layouts():
    return len(bpy.data.workspaces), len(bpy.data.screens)


def notes_checks(answer, saved, layouts_before):
    """The third part merged and saved: every path its text named cut, nothing of its folder in the file."""
    check("notes_merged", answer.get("success") is True, answer)
    check("notes_worker_workspace_left_behind", layouts() == layouts_before, (layouts(), layouts_before))
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    workspace = str(OUT / "state" / "workspace" / "brick.png")
    check("notes_saved_file_holds_nothing_private", not leaks(saved, [workspace]), leaks(saved, [workspace]))
    notes = bpy.data.objects["Notes"]
    unread = bpy.data.images.get("token.png")
    check("notes_unreadable_private_file_never_packed", unread is not None and not unread.packed_file
          and SECRET.encode() not in Path(saved).read_bytes() and MARKER not in unread.filepath.lower(),
          getattr(unread, "filepath", None))
    check("notes_doubled_separators_keep_file_names", notes["doubled"] == "cache at d.json", notes["doubled"])
    check("notes_detour_and_encoded_backslash_keep_file_names", notes["detour"] == "see dd.json"
          and notes["url_backslashed"] == "file://ub.png", (notes["detour"], notes["url_backslashed"]))
    check("notes_escaped_paths_keep_file_names", json.loads(notes["json"]) == {"src": "j.png"}
          and json.loads(notes["json_backslashed"]) == {"src": "w.png"} and "Renamed" in bpy.data.objects,
          (notes["json"], notes["json_backslashed"]))
    check("notes_text_keeps_file_names", notes["note"] == "reference image: ref.png, from the worker"
          and notes["after_own"] == "/tmp/a.png;b.png" and bytes(notes["raw"]) == b"raw.png"
          and list(notes["listed"]) == ["kept", "listed.png"] and notes["family"] == "see h.png",
          {key: str(notes[key])[-60:] for key in ("note", "after_own", "raw", "listed", "family", "url")})
    moved = bpy.data.images.get("moved.png")
    check("notes_moved_private_folder_packed", moved is not None and bool(moved.packed_file),
          getattr(moved, "filepath", None))


def refused_checks(label, window, controller, number, saved, named):
    """A part refused with nothing of it added, WorkSpaces and Screens included, the refusal naming ``named``; the
    file still clean."""
    def every():
        kinds = ("objects", "meshes", "materials", "images", "workspaces", "screens")
        return sorted((kind, item.name) for kind in kinds for item in getattr(bpy.data, kind))
    before = every()
    answer = lead_merge(window, controller, {number: [{}]})
    check(label + "_refused", answer.get("error_code") == "UNSUPPORTED" and "can't be cleared" in answer.get("error", "")
          and answer.get("scene_changed") is False and named in answer.get("error", ""), answer)
    check(label + "_refusal_leaves_every_datablock", every() == before, sorted(set(every()) ^ set(before)))
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    workspace = str(OUT / "state" / "workspace" / "brick.png")
    check(label + "_saved_file_holds_nothing_private", not leaks(saved, [workspace]), leaks(saved, [workspace]))


def run_checks():
    lead_id = "qa-lead"
    part = worker_paths.part_folder(OUT / "state", lead_id, 1, "lamp", MARKER[:8]) / "part.blend"
    (OUT / "scratch").mkdir(exist_ok=True)
    registered = private_roots.allocate(private_roots.WORKER_PREFIX, OUT / "scratch")
    family = Path(tempfile.gettempdir()) / (private_roots.WORKER_PREFIX + MARKER)   # An earlier process's.
    family.mkdir(exist_ok=True)
    (PERSON / "textures").mkdir()
    own = png(PERSON / "textures" / "own.png", (0.5, 0.5, 0.5, 1))
    has_font = write_part(part, Path(registered), family, own)
    report["details"]["font"] = has_font
    check("the_part_file_holds_the_private_paths_unmerged", MARKER.encode() in part.read_bytes())
    kit_part = worker_paths.part_folder(OUT / "state", lead_id, 2, "kit", MARKER[:8]) / "part.blend"
    workspace = OUT / "state" / "workspace"   # The chat's default workspace (``Controller.workspace``).
    workspace.mkdir(parents=True, exist_ok=True)
    brick = write_kit(kit_part, workspace)
    notes_part = worker_paths.part_folder(OUT / "state", lead_id, 3, "notes", MARKER[:8]) / "part.blend"
    moved = Path(private_roots.allocate(private_roots.WORKER_PREFIX, OUT / "scratch"))
    moved = moved.rename(moved.with_name("moved " + MARKER))   # Its owner file goes with it.
    write_notes(notes_part, moved)
    stale_part = worker_paths.part_folder(OUT / "state", lead_id, 4, "stale", MARKER[:8]) / "part.blend"
    write_stale(stale_part, OUT / "state")
    folder = lambda number, name: worker_paths.part_folder(OUT / "state", lead_id, number, name, MARKER[:8])
    media_part = folder(5, "media") / "part.blend"
    expected = media.write_media(media_part, media_part.parent, quad, textured, SECRET)
    sessions = OUT / "state" / "sessions" / "chat.json"
    encoded = {6: folder(6, "plus") / "part.blend", 7: folder(7, "wide") / "part.blend"}
    media.write_encoded(encoded[6], quad, sessions, "plus")
    media.write_encoded(encoded[7], quad, sessions, "wide")
    long_part = folder(8, "long") / "part.blend"
    media.write_long(long_part, quad, long_part.parent)

    # A file never saved: Blender has no folder to make a path relative to.
    window, controller = lead({1: part, 2: kit_part, 3: notes_part, 4: stale_part, 5: media_part, 6: encoded[6],
                               7: encoded[7], 8: long_part})
    started = time.perf_counter()
    answer = lead_merge(window, controller, {1: [{}]})
    report["details"]["merge_seconds"] = round(time.perf_counter() - started, 3)
    unsaved = PERSON / "unsaved-then-saved.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(unsaved), compress=False)
    inspect("unsaved_lead", answer, unsaved)
    # The merge's Undo step, written again by Redo (what memory Undo and autosave hold).
    bpy.ops.ed.undo()
    check("undo_takes_the_merge_back", "Lamp Base" not in bpy.data.objects, list(bpy.data.objects.keys()))
    bpy.ops.ed.redo()
    again = PERSON / "after-redo.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(again), compress=False, copy=True)
    check("redo_saved_file_holds_nothing_private", "Lamp Base" in bpy.data.objects and not leaks(again), leaks(again))

    # The same file, saved now, merged again after Undo: paths into the part's folder could be relative to it.
    bpy.ops.ed.undo()
    answer = lead_merge(window, controller, {1: [{}]})
    saved = PERSON / "saved-before-the-merge.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    inspect("saved_lead", answer, saved)
    if has_font:   # A font cannot be written again: a worker's is never kept, its path cut (BREAKIT-C7A-6).
        font = bpy.data.objects["Lamp Label"].data.font
        check("saved_lead_private_font_never_packed", not font.packed_file and MARKER not in font.filepath.lower(),
              (font.filepath, bool(font.packed_file)))
    kit_checks(lead_merge(window, controller, {2: [{}]}), saved, brick)
    before = layouts()
    notes_checks(lead_merge(window, controller, {3: [{}]}), saved, before)
    refused_checks("stale", window, controller, 4, saved, "eyes.png")
    persons = lambda path: leaks(path, [str(OUT / "state" / "workspace" / "brick.png")])   # Kept where it was.
    media.media_checks(check, lead_merge(window, controller, {5: [{}]}), saved, persons, SECRET, expected, MARKER)
    refused_checks("form_encoded", window, controller, 6, saved, "Encoded plus")
    refused_checks("utf16", window, controller, 7, saved, "Encoded wide")
    answer, seconds = media.timed(lambda: lead_merge(window, controller, {8: [{}]}))
    media.long_checks(check, answer, seconds, saved, persons)
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    check("reopened_one_scene_no_library", [s.name for s in bpy.data.scenes] == ["Scene"]
          and not list(bpy.data.libraries), ([s.name for s in bpy.data.scenes], list(bpy.data.libraries)))
    check("reopened_packed_images_read", all(bpy.data.images[n].size[0] == 4 for n in ("reference.png", "kept.png",
                                                                                       "baked.png")))
    media.reopened_media_checks(check, expected)
    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(BASE / "parallel-merge-privacy.json").write_text(json.dumps(report, indent=2, default=str))   # Where the runner reads.
print("ANGELO_PARALLEL_MERGE_PRIVACY", json.dumps({"success": report.get("success"), "error": report.get("error"),
                                                  "failed": [k for k, v in report["checks"].items() if not v]}))
sys.exit(0 if report.get("success") else 1)
