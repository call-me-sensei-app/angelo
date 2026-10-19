# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The heavy scene in Blender: make it from the plan, save it, append the saved file, and know what it holds.

* ``build``: every ``heavy_plan`` entry as a Blender object, meshes filled
  with ``foreach_set`` from the plan's arrays (data API only, no operators);
  the walls, towers and the statue also carry vertex groups, as production
  meshes do.
* ``save_start``: the start file (``start.blend``) the reference job keeps
  with the references, uncompressed, as a person's working file would be.
* ``append_start``: what both sides of a trial do before the first message:
  remove the factory scene's objects, then append the start file's
  collections, world and camera into the trial's own scene, after checking
  the file's SHA-256. Both sides therefore start from the same bytes.
* ``expected``: per object, what the plan says it holds (transform, parent,
  materials, face and vertex counts, CRC-32 of its positions and corners), the
  ground truth ``heavy_checks`` compares untouched objects with.
"""

import hashlib
import os
import time
import zlib

import heavy_plan as plan

COLLECTIONS = ("Terrain", "Castle", "Forest", "Rocks", "Village", "Props", "Lighting")
WORLD = "Castle Sky"
MATERIALS = {"Castle Stone": ((0.66, 0.62, 0.56), 0.85), "Castle Slate": ((0.09, 0.13, 0.22), 0.55),
             "Window Stone": ((0.52, 0.48, 0.42), 0.8), "Meadow Grass": ((0.12, 0.26, 0.05), 0.9),
             "Bark": ((0.16, 0.1, 0.06), 0.9), "Leaves": ((0.07, 0.2, 0.04), 0.7), "Rock": ((0.3, 0.29, 0.27), 0.9),
             "Plaster": ((0.7, 0.66, 0.58), 0.85), "Roof Tiles": ((0.45, 0.16, 0.08), 0.7),
             "Timber": ((0.3, 0.18, 0.09), 0.8), "Flag Cloth": ((0.6, 0.04, 0.03), 0.6),
             "Statue Bronze": ((0.35, 0.22, 0.1), 0.35)}


def _materials(bpy):
    import scenario_build_kit as kit
    made = {name: kit.principled(bpy, name, colour, roughness=roughness) for name, (colour, roughness) in
            MATERIALS.items()}
    made["Window Glass"] = kit.principled(bpy, "Window Glass", (0.05, 0.05, 0.06), roughness=0.1,
                                          emission=(1.0, 0.62, 0.3), emission_strength=2.5)
    return made


def fill_mesh(bpy, name, arrays):
    """A new mesh from ``(positions, sizes, corners, material indices)`` (``foreach_set``, no Python loops)."""
    import numpy
    positions, sizes, corners, indices = arrays
    data = bpy.data.meshes.new(name)
    data.vertices.add(len(positions))
    data.vertices.foreach_set("co", positions.ravel())
    data.loops.add(len(corners))
    data.loops.foreach_set("vertex_index", corners)
    data.polygons.add(len(sizes))
    starts = numpy.zeros(len(sizes), dtype=numpy.int32)
    numpy.cumsum(sizes[:-1], out=starts[1:])
    data.polygons.foreach_set("loop_start", starts)
    if indices is not None:
        data.polygons.foreach_set("material_index", numpy.asarray(indices, dtype=numpy.int32))
    data.update(calc_edges=True)
    return data


def weighted_groups(name):
    """The vertex groups a planned object carries (production meshes have them): weights in eight steps by height."""
    if name.startswith("Castle Wall "):
        return ("Weathering",)
    if name.startswith("Castle Tower ") and not name.endswith(" Roof"):
        return ("Moss",)
    return ("Upper", "Lower") if name == plan.STATUE else ()


def add_weights(obj, positions, names):
    """Each vertex in every group, weighted 1/8 to 1 by its height (the second group the other way up)."""
    import numpy
    z = positions[:, 2].astype(numpy.float64)
    level = numpy.clip(((z - z.min()) / ((z.max() - z.min()) or 1.0) * 8).astype(numpy.int64), 0, 7)
    for index, name in enumerate(names):
        group = obj.vertex_groups.new(name=name)
        steps = level if index == 0 else 7 - level
        for step in range(8):
            members = numpy.flatnonzero(steps == step)
            if len(members):
                group.add(members.tolist(), (step + 1) / 8.0, "REPLACE")


def build(bpy, target_scene):
    """Every planned object in ``target_scene`` (its collections linked under the scene's own)."""
    import scenario_build_kit as kit
    started = time.perf_counter()
    made = _materials(bpy)
    collections = {}
    for name in COLLECTIONS:
        collections[name] = bpy.data.collections.new(name)
        target_scene.collection.children.link(collections[name])
    meshes, objects = {}, {}
    for item in plan.plan():
        if item.kind == "camera":
            obj = kit.camera_object(bpy, item.name, item.location, plan.CAMERA_TARGET, 35.0,
                                    collection=collections[item.collection], clip_end=2000.0)
        elif item.kind == "sun":
            obj = kit.sun_object(bpy, item.name, plan.SUN_ELEVATION, plan.SUN_AZIMUTH, (1.0, 0.82, 0.62), 4.0,
                                 collection=collections[item.collection])
        else:
            if item.mesh not in meshes:
                meshes[item.mesh] = fill_mesh(bpy, item.mesh, plan.geometry(item))
                for material in item.materials:
                    meshes[item.mesh].materials.append(made[material])
            obj = bpy.data.objects.new(item.name, meshes[item.mesh])
            collections[item.collection].objects.link(obj)
            add_weights(obj, plan.geometry(item)[0], weighted_groups(item.name))
            obj.location, obj.rotation_euler = item.location, (0.0, 0.0, item.rotation)
        if item.parent:
            obj.parent = objects[item.parent]
        objects[item.name] = obj
    target_scene.world = kit.sky_world(bpy, WORLD, plan.SUN_ELEVATION, plan.SUN_AZIMUTH, strength=0.6)
    target_scene.camera = objects[plan.CAMERA]
    target_scene.render.engine = "BLENDER_EEVEE"
    bpy.context.view_layer.update()
    return {"build_s": round(time.perf_counter() - started, 2), **plan.counts()}


def save_start(bpy, path):
    """The start file, uncompressed and without a thumbnail; returns ``{"file", "bytes", "sha256", "save_s"}``."""
    started = time.perf_counter()
    preferences = bpy.context.preferences.filepaths
    kept = preferences.file_preview_type
    preferences.file_preview_type = "NONE"
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=False, check_existing=False)
    finally:
        preferences.file_preview_type = kept
    return {"file": os.path.basename(str(path)), "bytes": os.path.getsize(path), "sha256": sha256(path),
            "save_s": round(time.perf_counter() - started, 2)}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def remove_factory(bpy):
    """The factory scene's objects and the data only they used."""
    for name in ("Cube", "Camera", "Light"):
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            for kind, blocks in ((bpy.types.Mesh, bpy.data.meshes), (bpy.types.Camera, bpy.data.cameras),
                                 (bpy.types.Light, bpy.data.lights)):
                if isinstance(data, kind):
                    blocks.remove(data)


def append_start(bpy, target_scene, path, expected_sha256=None):
    """Remove the factory objects and append the start file into ``target_scene``; returns what it took."""
    started = time.perf_counter()
    digest = sha256(path)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("The heavy scene's start file changed on disk (%s != %s)" % (digest, expected_sha256))
    hashed = time.perf_counter()
    remove_factory(bpy)
    with bpy.data.libraries.load(str(path), link=False) as (source, into):
        into.collections = [name for name in source.collections if name in COLLECTIONS]
        into.worlds = [name for name in source.worlds if name == WORLD]
    for collection in into.collections:
        target_scene.collection.children.link(collection)
    target_scene.world = bpy.data.worlds.get(WORLD) or target_scene.world
    target_scene.camera = bpy.data.objects.get(plan.CAMERA)
    target_scene.render.engine = "BLENDER_EEVEE"
    bpy.context.view_layer.update()
    loaded = time.perf_counter()
    return {"file": os.path.basename(str(path)), "bytes": os.path.getsize(path), "sha256": digest,
            "hash_s": round(hashed - started, 3), "append_s": round(loaded - hashed, 3),
            "objects": len(target_scene.objects),
            "vertices": sum(len(mesh.vertices) for mesh in bpy.data.meshes if mesh.users)}


# ---------------------------------------------------------------- what an untouched object holds

def crc(array):
    return zlib.crc32(memoryview(array).cast("B"))


def mesh_facts(data):
    """``(vertices, faces, CRC-32 of positions, CRC-32 of corner vertices)`` read through attribute arrays."""
    import numpy
    positions = numpy.empty(len(data.vertices) * 3, numpy.float32)
    data.attributes["position"].data.foreach_get("vector", positions)
    corners = numpy.empty(len(data.loops), numpy.int32)
    data.attributes[".corner_vert"].data.foreach_get("value", corners)
    return len(data.vertices), len(data.polygons), crc(positions), crc(corners)


def expected(item):
    """What the plan says an object holds."""
    facts = {"location": list(item.location), "rotation": [0.0, 0.0, item.rotation], "scale": [1.0, 1.0, 1.0],
             "parent": item.parent, "materials": list(item.materials)}
    if item.kind == "mesh":
        positions, sizes, corners, _ = plan.geometry(item)
        facts["mesh"] = (len(positions), len(sizes), crc(positions), crc(corners))
    return facts


def differences(obj, item, tolerance=1.0e-4):
    """What differs between a live object and its plan entry (empty when it is untouched)."""
    want, found = expected(item), []
    for key, value in (("location", obj.location), ("rotation", obj.rotation_euler), ("scale", obj.scale)):
        if any(abs(a - b) > tolerance for a, b in zip(want[key], value)):
            found.append(key)
    if (obj.parent.name if obj.parent else None) != want["parent"]:
        found.append("parent")
    if item.kind == "mesh":
        if [slot.material.name if slot.material else None for slot in obj.material_slots] != want["materials"]:
            found.append("materials")
        if len(obj.modifiers):
            found.append("modifiers")
        if mesh_facts(obj.data) != want["mesh"]:
            found.append("geometry")
    return found


def surface_ray(bpy, terrain, x, y):
    """The terrain surface's height under (x, y) by a ray straight down onto the terrain's own mesh."""
    from mathutils import Vector
    inverse = terrain.matrix_world.inverted()
    origin = inverse @ Vector((x, y, 1000.0))
    direction = (inverse.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    hit, location, _normal, _index = terrain.ray_cast(origin, direction, distance=5000.0)
    return (terrain.matrix_world @ location).z if hit else None


def summit():
    """``(x, y, z)`` of the planned terrain's highest vertex."""
    positions = plan.geometry(plan.entry(plan.TERRAIN))[0]
    index = int(positions[:, 2].argmax())
    return tuple(float(v) for v in positions[index])


def wall_volume_ok(positions, sizes, corners):
    """``(consistent, signed volume)``: every edge is walked once each way, and the volume is positive."""
    import numpy
    starts = numpy.zeros(len(sizes), dtype=numpy.int64)
    numpy.cumsum(sizes[:-1], out=starts[1:])
    face_of_corner = numpy.repeat(numpy.arange(len(sizes)), sizes)
    following = numpy.arange(len(corners)) + 1
    ends = starts + sizes
    following[ends - 1] = starts
    a, b = corners.astype(numpy.int64), corners[following].astype(numpy.int64)
    directed = a * (len(positions) + 1) + b
    consistent = len(numpy.unique(directed)) == len(directed)
    first = corners[starts[face_of_corner]].astype(numpy.int64)
    keep = (numpy.arange(len(corners)) != starts[face_of_corner]) & (following != starts[face_of_corner])
    p = positions.astype(numpy.float64)
    volume = float(numpy.einsum("ij,ij->i", p[first[keep]], numpy.cross(p[a[keep]], p[b[keep]])).sum() / 6.0)
    return consistent, volume
