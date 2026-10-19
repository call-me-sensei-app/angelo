# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The planted project and the before/after snapshots of test_blender_read_tools.py. Not a test module.

``plant`` builds a small project in a folder the suite owns: facts the reads
must return (socket identifiers, a modifier input, Cycles settings, an orphan
mesh, a missing texture, a missing library) and facts they must never return
(absolute paths with a user and a client name, script text, a driver
expression, string socket values, another scene). ``light`` and ``deep`` are
two independent views of the session for the read-only proof: neither uses
the reader's own code.
"""

import hashlib
import os
import struct
import zlib
from pathlib import Path

import bpy

# Text that must never appear in any answer, wherever it was planted.
SECRETS = ("alice", "Client X", "PLANTED_SCRIPT_BODY", "PLANTEDDRIVER", "notes-for-nobody", "label-for-nobody",
           "string-node-secret")
PRIVATE = "/Users/alice/Client X"
FILLER_NODES = 40


def png(path, width=2, height=2):
    """A valid PNG written with the standard library."""
    raw = b"".join(b"\x00" + b"\xff\x80\x00\xff" * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _node_group(cube):
    group = bpy.data.node_groups.new("Planted Rock", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    height = group.interface.new_socket("Height", in_out="INPUT", socket_type="NodeSocketFloat")
    label = group.interface.new_socket("Label", in_out="INPUT", socket_type="NodeSocketString")
    label.default_value = PRIVATE + "/notes-for-nobody.txt"
    noise = group.nodes.new("ShaderNodeTexNoise")
    noise.name, noise.noise_dimensions = "Planted Noise", "3D"
    scale = group.nodes.new("ShaderNodeVectorMath")
    scale.name, scale.operation = "Planted Scale", "SCALE"
    text = group.nodes.new("FunctionNodeInputString")
    text.name, text.string = "Planted String", PRIVATE + "/string-node-secret"
    empty = group.nodes.new("GeometryNodeGroup")
    empty.name = "Planted Empty Group"
    for index in range(FILLER_NODES):  # Enough rows that a depth-2 page of the nodes exceeds the character budget.
        group.nodes.new("ShaderNodeMath").name = "Planted Filler %02d" % index

    class PlantedLostNode(bpy.types.Node):
        bl_idname, bl_label = "PlantedLostNode", "Planted Lost"

        @classmethod
        def poll(cls, _tree):
            return True
    bpy.utils.register_class(PlantedLostNode)
    group.nodes.new("PlantedLostNode").name = "Planted Lost Node"
    bpy.utils.unregister_class(PlantedLostNode)  # Its node is now of a type this build does not know.
    modifier = cube.modifiers.new("Planted GN", "NODES")
    modifier.node_group = group
    inputs = modifier.properties.inputs
    getattr(inputs, height.identifier).value = 2.5
    getattr(inputs, label.identifier).value = PRIVATE + "/label-for-nobody.txt"
    return {"height": height.identifier, "label": label.identifier}


def _images(project, outside):
    png(project / "textures" / "planted_present.png")
    png(outside / "outside_secret.png")
    present = bpy.data.images.load(str(project / "textures" / "planted_present.png"), check_existing=False)
    missing = bpy.data.images.new("planted_missing.png", 4, 4)
    missing.source, missing.filepath = "FILE", "//textures/planted_missing.png"
    beyond = bpy.data.images.load(str(outside / "outside_secret.png"), check_existing=False)
    png(project / "textures" / "planted_packed.png")
    packed = bpy.data.images.load(str(project / "textures" / "planted_packed.png"), check_existing=False)
    packed.pack()
    material = bpy.data.materials.new("Planted Material")
    if material.node_tree is None:
        material.use_nodes = True
    tree = material.node_tree
    for image in (present, missing, beyond, packed):
        tree.nodes.new("ShaderNodeTexImage").image = image
    bpy.data.objects["Cube"].data.materials.append(material)


def _libraries(project):
    """Two libraries with one object each; the second is deleted after the project is saved."""
    (project / "libs").mkdir()
    bpy.ops.mesh.primitive_cube_add()
    prop = bpy.context.active_object
    prop.name = "Planted Prop"
    for name in ("props.blend", "gone.blend"):
        bpy.data.libraries.write(str(project / "libs" / name), {prop}, fake_user=True)
    other = bpy.data.objects.new("Planted In Other File", None)
    (project / "stages").mkdir()
    bpy.data.libraries.write(str(project / "stages" / "other.blend"), {other, prop}, fake_user=True)
    bpy.data.objects.remove(other)
    bpy.data.objects.remove(prop)
    for name in ("props.blend", "gone.blend"):
        with bpy.data.libraries.load(str(project / "libs" / name), link=True, relative=True) as (_source, target):
            target.objects = ["Planted Prop"]
        for linked in target.objects:
            bpy.context.scene.collection.objects.link(linked)


def _files(project, outside):
    """Workspace files that are not readable .blend files, and one .blend outside the workspace."""
    source = (project / "stages" / "other.blend").read_bytes()
    (project / "stages" / "corrupt.blend").write_bytes(b"BLENDER-v405" + os.urandom(4096))
    (project / "stages" / "cut.blend").write_bytes(source[:max(64, len(source) // 3)])
    (project / "stages" / "text.blend").write_bytes(b"not a blend file at all\n" * 8)
    (outside / "secret.blend").write_bytes(source)
    (project / "link.blend").symlink_to(outside / "secret.blend")


def plant(folder):
    """Build, save and reopen the planted project in ``folder``; returns its paths and identifiers."""
    project, outside = Path(folder).resolve() / "project", Path(folder).resolve() / "Client X" / "alice"
    project.mkdir(parents=True)
    outside.mkdir(parents=True)
    scene, cube = bpy.context.scene, bpy.data.objects["Cube"]
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 137
    scene.render.filepath = PRIVATE + "/renders/shot_"
    identifiers = _node_group(cube)
    _images(project, outside)
    bpy.data.meshes.new("Planted Kept").use_fake_user = True
    cube.driver_add("location", 0).driver.expression = 'frame * 0.137 + len("PLANTEDDRIVER")'
    bpy.data.texts.new("planted_script.py").write("PLANTED_SCRIPT_BODY = 1\n")
    other = bpy.data.scenes.new("Planted Other Scene")
    other.frame_current = 77
    scene.background_set = other
    _libraries(project)
    main = project / "main.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(main))
    (project / "libs" / "gone.blend").unlink()
    _files(project, outside)
    bpy.ops.wm.open_mainfile(filepath=str(main))  # The missing library and its data are flagged on load.
    # Data left without a user during the session: a save drops it, so it is planted after the load.
    orphan = bpy.data.meshes.new("Planted Orphan")
    orphan.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    bpy.data.materials.new("Planted Orphan Material")
    # A mesh made in this session keeps flags that are the same everywhere as one value (storage SINGLE);
    # a loaded one has arrays.
    bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
    bpy.context.active_object.name = "Planted Fresh"
    bpy.context.active_object.data.name = "Planted Fresh Mesh"
    return {"project": project, "outside": outside, "main": main, **identifiers}


def light(project):
    """Identity of every datablock, the dirty flag, loaded images and the workspace's files."""
    blocks = []
    for definition in bpy.data.bl_rna.properties:
        if definition.type == "COLLECTION" and definition.identifier != "all_ids":
            for block in getattr(bpy.data, definition.identifier):
                blocks.append((definition.identifier, block.name, block.session_uid, block.users, block.use_fake_user,
                               block.library.name if block.library else None))
    files = sorted((str(path.relative_to(project)), path.lstat().st_size, path.lstat().st_mtime_ns)
                   for path in project.rglob("*"))
    return {"blocks": blocks, "dirty": bpy.data.is_dirty, "filepath": bpy.data.filepath,
            "loaded": [(image.name, image.has_data) for image in bpy.data.images], "files": files,
            "scene": bpy.context.window.scene.name if bpy.context.window else bpy.context.scene.name,
            "frame": bpy.context.scene.frame_current, "mode": bpy.context.mode,
            "selected": sorted(obj.name for obj in bpy.context.scene.objects if obj.select_get())}


_SKIP_STRUCTS = {"Depsgraph", "ImagePreview", "PackedFile", "Struct", "Property", "Function"}
_SKIP_COLLECTIONS = {"all_ids", "window_managers", "screens", "workspaces"}
_IMAGE_LOADING = {"size", "channels", "depth", "is_float", "resolution", "file_format", "frame_duration", "pixels",
                  "bindcode"}


def _value(holder, definition, depth, budget):
    name, kind = definition.identifier, definition.type
    if name == "rna_type" or budget[0] <= 0:
        return None
    budget[0] -= 1
    if isinstance(holder, bpy.types.Image) and name in _IMAGE_LOADING:
        return None
    try:
        value = getattr(holder, name)
    except Exception as error:
        return "error:" + type(error).__name__
    if kind == "POINTER":
        if value is None:
            return None
        if isinstance(value, bpy.types.ID):
            return ("id", value.name, value.session_uid)
        if value.bl_rna.identifier in _SKIP_STRUCTS or depth <= 0:
            return value.bl_rna.identifier
        return _struct(value, depth - 1, budget)
    if kind == "COLLECTION":
        if depth <= 0 or (name == "data" and isinstance(holder, bpy.types.Attribute)):
            return len(value)  # An attribute's elements are never fetched: the fetch rewrites single-value storage.
        return [None if item is None else ("id", item.name, item.session_uid) if isinstance(item, bpy.types.ID)
                else _struct(item, depth - 1, budget) for item in value[:64]] + [len(value)]
    if kind in ("BOOLEAN", "INT", "FLOAT") and (definition.array_length or any(definition.array_dimensions)):
        return repr(tuple(value[:256]) if hasattr(value, "__getitem__") else value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value if isinstance(value, (bool, int, str)) else repr(value)


def _struct(value, depth, budget):
    return {definition.identifier: _value(value, definition, depth, budget) for definition in value.bl_rna.properties}


def deep(depth=3, budget=400000):
    """A digest of every datablock's RNA values, three structs deep (an independent walk, bounded)."""
    remaining = [budget]
    document = {}
    for definition in bpy.data.bl_rna.properties:
        if definition.type != "COLLECTION" or definition.identifier in _SKIP_COLLECTIONS:
            continue
        document[definition.identifier] = [_struct(block, depth, remaining)
                                           for block in getattr(bpy.data, definition.identifier)]
    encoded = repr(document).encode("utf-8", "backslashreplace")
    return {"digest": hashlib.blake2b(encoded, digest_size=16).hexdigest(), "values": budget - remaining[0],
            "complete": remaining[0] > 0}


def tag_stage_scene():
    """A scene carrying the tag of a resident private stage (native_programs.runtime_is_staging)."""
    from angelo_agent.legacy_names import PROGRAM_STAGE
    scene = bpy.data.scenes.new("__angelo_stage_planted")
    scene[PROGRAM_STAGE[0]] = "planted"
    return scene
