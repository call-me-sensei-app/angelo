# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout: unwrap and pack meshes for a texture, in one call (doc/angelo/uv_layout.rst).

``run(objects, spec)`` is the step the game-ready job calls and what ``kit.uv_layout`` runs for the agent: optional
unwrap, texel density (one target in px/m, or even across the meshes), stacking of congruent islands so they share
texels, grouping by material or object into UDIM tiles, padding that survives the mip levels asked for, and Blender's
Pack Islands with its Search, per tile. It returns a receipt of measured facts. Everything goes through Blender's own
operators and the native packer; this module only decides what goes where.
"""

import array
import hashlib
import math
import time

from types import MappingProxyType

import bmesh
import bpy
import numpy as np
from bpy_extras import bmesh_utils

from . import uv_layout_math as uvm

UNWRAPS = ("KEEP", "SEAMS", "SMART")
GROUPS = ("NONE", "MATERIAL", "OBJECT")
# Read-only: every later layout reads it (an agent script could otherwise change in place what the person's own
# later layouts do).
DEFAULTS = MappingProxyType({"texture_size": 2048, "density": None, "mips": 4, "unwrap": "KEEP", "stack": False,
                             "mirrored": False, "group_by": "NONE", "search": 0, "rotate": True,
                             "packer": uvm.DEFAULT_PACKER})
STACK_TOLERANCE = 1e-4   # Metres: congruent islands' vertices may differ by this much.


def validate(spec):
    """The canonical layout; ValueError naming the first thing that is wrong."""
    if not isinstance(spec, dict):
        raise ValueError("a layout must be an object")
    extra = set(spec) - set(DEFAULTS)
    if extra:
        raise ValueError("a layout does not take: " + ", ".join(sorted(extra)))
    out = dict(DEFAULTS, **spec)
    out["mips_auto"] = "mips" not in spec   # Nobody set it: the layout may lower it to leave the islands room.
    uvm.mip_margin(out["texture_size"], out["mips"])   # Checks both.
    density = out["density"]
    if density is not None and (isinstance(density, bool) or not isinstance(density, (int, float)) or
                                not 0 < density <= 1e6):
        raise ValueError("density must be pixels per metre above 0, or None to fill the tiles")
    for key, allowed in (("unwrap", UNWRAPS), ("group_by", GROUPS)):
        if out[key] not in allowed:
            raise ValueError("%s must be one of %s" % (key, ", ".join(allowed)))
    for key in ("stack", "mirrored", "rotate"):
        if not isinstance(out[key], bool):
            raise ValueError("%s must be true or false" % key)
    if out["packer"] not in uvm.PACKERS:
        raise ValueError("packer must be one of %s" % ", ".join(uvm.PACKERS))
    search = out["search"]
    if isinstance(search, bool) or not isinstance(search, int) or not 0 <= search <= 256:
        raise ValueError("search must be a whole number of extra layouts from 0 to 256")
    return out


def _world_area(face, matrix):
    points = [matrix @ loop.vert.co for loop in face.loops]
    total = points[0] * 0.0
    for i in range(1, len(points) - 1):
        total += (points[i] - points[0]).cross(points[i + 1] - points[0])
    return total.length / 2.0


class _Island:
    __slots__ = ("obj", "faces", "uv_area", "world_area", "group", "tile", "stacked_on")

    def __init__(self, obj, faces, layer, matrix):
        self.obj, self.faces = obj, faces
        self.uv_area = sum(uvm.polygon_area([tuple(l[layer].uv) for l in f.loops]) for f in faces)
        self.world_area = sum(_world_area(f, matrix) for f in faces)
        self.group, self.tile, self.stacked_on = 0, 0, None


def _faces_of(islands):
    """Each island as its object's name and face indices, fixed for the rest of the run: moved into tiles, two islands
    of one mesh can meet at a shared vertex with the same UV, and a fresh read by UV connectivity would join them."""
    return [(island.obj.name, [face.index for face in island.faces]) for island in islands]


def _topology(obj):
    """What a layout's fixed islands and its snapshot assume of a mesh in Edit Mode: its vertex count and every face's
    corners by vertex, in order (as one hash). Packing never changes them; faces added, removed or rebuilt meanwhile
    (by the person between two packs, a timer or a script) do, and then neither the islands nor the saved corners fit.
    Read at C speed, since every re-read pays it: a copy into a scratch mesh, its corners' vertices as one attribute
    array and its faces' first corners. At 1M corners on the pool VM, under one load, a Python walk took 468 ms and
    reading the corners per loop (``MeshLoop.vertex_index``) 113 ms of 180; the attribute array takes 0.4 ms."""
    scratch = bpy.data.meshes.new("Angelo UV Layout topology")
    try:
        bmesh.from_edit_mesh(obj.data).to_mesh(scratch)   # Reads the edit mesh; writes only the scratch mesh.
        corners = np.empty(len(scratch.loops), dtype=np.int32)
        attribute = scratch.attributes.get(".corner_vert")
        if attribute is not None:
            attribute.data.foreach_get("value", corners)
        else:   # Not stored as that attribute: the same values per loop, slower.
            scratch.loops.foreach_get("vertex_index", corners)
        starts = np.empty(len(scratch.polygons), dtype=np.int32)
        scratch.polygons.foreach_get("loop_start", starts)
        return len(scratch.vertices), hashlib.blake2b(starts.tobytes() + corners.tobytes(), digest_size=16).digest()
    finally:
        bpy.data.meshes.remove(scratch)


def _shapes(objects):
    """Each mesh's topology and active UV map, as ``steps`` fixes its islands."""
    return [(_topology(obj), bmesh.from_edit_mesh(obj.data).loops.layers.uv.active.name) for obj in objects]


def _rebind(objects, fixed, shapes=None):
    """The fixed islands (``_faces_of``) on the meshes' current BMesh faces. With ``shapes`` (``_shapes`` as they were
    fixed), refused when a mesh changed meanwhile: the islands' face numbers would name other faces."""
    if shapes is not None:
        for obj, (topology, active) in zip(objects, shapes):
            layer = bmesh.from_edit_mesh(obj.data).loops.layers.uv.active
            if _topology(obj) != topology or layer is None or layer.name != active:
                raise ValueError("%s changed while it was laid out (its faces or active UV map): the layout stopped"
                                 % obj.name)
    meshes = {}
    for obj in objects:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        meshes[obj] = (bm, bm.loops.layers.uv.active)
    by_name = {obj.name: obj for obj in objects}
    out = []
    for name, indices in fixed:
        obj = by_name[name]
        bm, uv_layer = meshes[obj]
        out.append(_Island(obj, [bm.faces[k] for k in indices], uv_layer, obj.matrix_world))
    return out, meshes


def _islands(objects):
    out, meshes = [], {}
    for obj in objects:
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active or bm.loops.layers.uv.new("UVMap")
        meshes[obj] = (bm, uv_layer)
        for faces in bmesh_utils.bmesh_linked_uv_islands(bm, uv_layer):
            out.append(_Island(obj, faces, uv_layer, obj.matrix_world))
    return out, meshes


def _scale(island, layer, factor, offset=(0.0, 0.0)):
    loops = [l for f in island.faces for l in f.loops]
    cu = sum(l[layer].uv.x for l in loops) / len(loops)
    cv = sum(l[layer].uv.y for l in loops) / len(loops)
    for loop in loops:
        uv = loop[layer].uv
        uv.x = (uv.x - cu) * factor + cu + offset[0]
        uv.y = (uv.y - cv) * factor + cv + offset[1]
    island.uv_area *= factor * factor


def _move_to(island, layer, tile):
    loops = [l for f in island.faces for l in f.loops]
    cu = sum(l[layer].uv.x for l in loops) / len(loops)
    cv = sum(l[layer].uv.y for l in loops) / len(loops)
    u, v = uvm.udim_offset(tile)
    _scale(island, layer, 1.0, (u + 0.5 - cu, v + 0.5 - cv))


def _world(obj, verts):
    """World positions and unit normals of ``verts`` as (x, y, z) tuples; normals through the inverse transpose, so
    a scaled or mirrored object's surface still faces the way it shows."""
    turn = obj.matrix_world.to_3x3().inverted_safe().transposed()
    return ([tuple(obj.matrix_world @ v.co) for v in verts],
            [tuple((turn @ v.normal).normalized()) for v in verts])


def _parts(bm):
    """The mesh's connected pieces, as face lists."""
    seen, parts = set(), []
    for face in bm.faces:
        if face in seen:
            continue
        seen.add(face)
        part, todo = [], [face]
        while todo:
            current = todo.pop()
            part.append(current)
            for edge in current.edges:
                for other in edge.link_faces:
                    if other not in seen:
                        seen.add(other)
                        todo.append(other)
        parts.append(part)
    return parts


def _stack(objects, meshes, mirrored):
    """Give every part congruent to an earlier part that part's UVs, face corner by face corner (seams included),
    whatever its own unwrap made. Returns {(object name, face index): (object name, face index)} from each copied
    face to the face it copied."""
    kinds = {}
    for obj in objects:
        bm = meshes[obj][0]
        bm.verts.index_update()
        bm.faces.index_update()
        for faces in _parts(bm):
            verts = sorted({v for f in faces for v in f.verts}, key=lambda v: v.index)
            if 3 <= len(verts) <= uvm.MAX_MATCH:
                points, normals = _world(obj, verts)
                key = uvm.signature(points, len(faces), STACK_TOLERANCE * 10)
                kinds.setdefault(key, []).append((obj, faces, verts, points, normals))
    copied = {}
    for members in kinds.values():
        masters = []   # Different parts can share a signature: each part copies the first master it matches.
        for obj, faces, verts, points, normals in members:
            for m_obj, m_faces, m_verts, m_points, m_normals in masters:
                mapping = uvm.match(m_points, m_normals, points, normals, STACK_TOLERANCE, mirrored)
                sources = None if mapping is None else uvm.corner_sources(
                    [tuple(v.index for v in f.verts) for f in m_faces], [tuple(v.index for v in f.verts) for f in faces],
                    {verts[mapping[i]].index: m_verts[i].index for i in range(len(m_verts))})
                if sources is None:
                    continue
                layer_a, layer_b = meshes[m_obj][1], meshes[obj][1]
                for face, (index, corners) in zip(faces, sources):
                    source = m_faces[index]
                    for loop, corner in zip(face.loops, corners):
                        loop[layer_b].uv = source.loops[corner][layer_a].uv
                    copied[(obj.name, face.index)] = (m_obj.name, source.index)
                break
            else:
                masters.append((obj, faces, verts, points, normals))
    return copied


def _copies(islands, copied):
    """{copy island's key: master island's key} for the islands made entirely of copied faces."""
    island_of = {(i.obj.name, f.index): _faces_key(i) for i in islands for f in i.faces}
    out = {}
    for island in islands:
        sources = [copied.get((island.obj.name, f.index)) for f in island.faces]
        if all(sources):
            out[_faces_key(island)] = island_of[sources[0]]
            island.stacked_on, island.uv_area = True, 0.0
    return out


def _select(objects, meshes, islands):
    chosen = {id(f) for island in islands for f in island.faces}
    for obj in objects:
        bm = meshes[obj][0]
        for face in bm.faces:
            face.select_set(id(face) in chosen)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def pack(spec, step, job=False):
    """One of ``steps``' packs. With ``job``, Pack Islands runs as its own window-manager job (progress, Stop; the
    interface keeps drawing) and without an Undo step of its own: the layout's operator pushes one when it ends."""
    options = dict(udim_source="CLOSEST_UDIM", rotate=spec["rotate"], rotate_method="ANY", scale=step["scale"],
                   merge_overlap=step["merge"], margin_method="FRACTION",
                   margin=uvm.mip_margin(spec["texture_size"], spec["mips"]),
                   shape_method=uvm.PACKERS[spec["packer"]], search=spec["search"])
    if job:   # Invoked without its confirm dialog (confirm=False): the pack starts as a job at once.
        bpy.ops.uv.pack_islands("INVOKE_DEFAULT", False, confirm=False, **options)
    else:
        bpy.ops.uv.pack_islands(**options)


def _bounds(island, layer):
    us = [l[layer].uv.x for f in island.faces for l in f.loops]
    vs = [l[layer].uv.y for f in island.faces for l in f.loops]
    return min(us), min(vs), max(us), max(vs)


def begin(objects, spec):
    """The checked spec and meshes, plus what ``finish`` needs: UV selection follows mesh selection while a layout
    runs (sync) and the person's setting comes back, and a snapshot puts a refused or stopped layout back."""
    spec = validate(spec)
    objects = [o for o in objects if o.type == "MESH"]
    if not objects:
        raise ValueError("UV Layout needs at least one mesh")
    settings = bpy.context.scene.tool_settings
    sync = settings.use_uv_select_sync
    settings.use_uv_select_sync = True
    return spec, objects, (sync, _snapshot(objects))


def finish(state, restore):
    """The person's sync setting back; with ``restore``, the meshes as they were before ``begin``. Returns the names
    of the meshes it could not put back (``_restore``)."""
    sync, saved = state
    try:
        return _restore(saved) if restore else []
    finally:
        bpy.context.scene.tool_settings.use_uv_select_sync = sync


def run(objects, spec):
    """Unwrap and pack ``objects`` (meshes, already in Edit Mode together) per ``spec``, every pack at once; returns
    the receipt. A refused layout leaves the meshes as they were. ``uv_layout_job`` runs the same steps as jobs."""
    spec, objects, state = begin(objects, spec)
    layout = steps(objects, spec)
    try:
        step = next(layout)
        while True:
            pack(spec, step)
            step = next(layout)
    except StopIteration as done:
        finish(state, restore=False)
        return done.value
    except BaseException:
        finish(state, restore=True)
        raise


def _snapshot(objects):
    """What a layout can change on each mesh: its UV layers' names, the active layer's corners (a flat float array)
    and the face selection, to put back when the layout is refused."""
    saved = []
    for obj in objects:
        bm = bmesh.from_edit_mesh(obj.data)
        active = bm.loops.layers.uv.active
        corners = array.array("d")
        if active is not None:
            for face in bm.faces:
                for loop in face.loops:
                    corners.extend(loop[active].uv)
        saved.append((obj.name, set(bm.loops.layers.uv.keys()), active.name if active else None, corners,
                      [face.select for face in bm.faces], _topology(obj)))
    return saved


def _restore(saved):
    """Each mesh as ``_snapshot`` saw it. Returns the names it could not put back, and changes nothing on those: a
    mesh removed, renamed or out of Edit Mode meanwhile, or changed so its saved corners no longer fit (faces added or
    removed, the saved UV map removed). Never raises for them, so a stop or failure always finishes its cleanup."""
    missed = []
    for name, uv_names, active_name, corners, selected, topology in saved:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH" or not obj.data.is_editmode or _topology(obj) != topology:
            missed.append(name)
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        if active_name is not None and bm.loops.layers.uv.get(active_name) is None:
            missed.append(name)
            continue
        for uv_layer in [layer for key, layer in bm.loops.layers.uv.items() if key not in uv_names]:
            bm.loops.layers.uv.remove(uv_layer)
        if active_name is not None:
            uv_layer = bm.loops.layers.uv[active_name]
            index = 0
            for face in bm.faces:
                for loop in face.loops:
                    loop[uv_layer].uv = (corners[index], corners[index + 1])
                    index += 2
        for face, was in zip(bm.faces, selected):
            face.select_set(was)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return missed


def _faces_key(island):
    """An island's identity across re-reads: its object and lowest face index (packing never changes topology)."""
    return (island.obj.name, min(f.index for f in island.faces))


def steps(objects, spec):
    """The layout as a generator: it yields each pack it needs, ``{"scale", "merge", "done", "tiles"}`` (tiles packed
    so far and planned), for the caller to run (``pack``), and returns the receipt. ``spec`` comes from ``begin``."""
    started = time.perf_counter()
    if spec["unwrap"] != "KEEP":
        for obj in objects:
            for face in bmesh.from_edit_mesh(obj.data).faces:
                face.select_set(True)
        if spec["unwrap"] == "SMART":
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.0, scale_to_bounds=False)
        else:
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.0)
    islands, meshes = _islands(objects)
    if not islands:
        raise ValueError("the meshes have no faces to lay out")
    if spec["density"] is not None:
        for island in islands:
            current = uvm.texel_density(island.uv_area, island.world_area, spec["texture_size"])
            if current > 0:
                _scale(island, meshes[island.obj][1], uvm.density_scale(current, spec["density"]))
        margin = uvm.mip_margin(spec["texture_size"], spec["mips"])
        spans = [uvm.tile_span([tuple(l[meshes[i.obj][1]].uv) for f in i.faces for l in f.loops]) for i in islands]
        wide = [s for s in spans if s > 1.0 - 2.0 * margin + 1e-9]
        if wide:   # Pack Islands keeps a fixed density only inside one tile: say so rather than overlap tiles.
            raise ValueError("%d island(s) are wider than a tile at %g px/m (the widest spans %.2f tiles): lower "
                             "the density or the texture size" % (len(wide), spec["density"], max(wide)))
    else:
        _select(objects, meshes, islands)
        bpy.ops.uv.average_islands_scale()   # Even density across every mesh before filling the tiles.
        islands, meshes = _islands(objects)
    copied = _stack(objects, meshes, spec["mirrored"]) if spec["stack"] else {}
    if copied:
        islands, meshes = _islands(objects)   # Copied UVs make the copies' islands the masters' islands.
    copies = _copies(islands, copied)
    fixed = _faces_of(islands)   # From here on an island is these faces, whatever its UVs come to touch.
    shapes = _shapes(objects)    # ... on these meshes: each later re-read refuses a mesh changed meanwhile.
    stacked = len(copies)
    key = {"NONE": lambda i: 0, "MATERIAL": lambda i: i.faces[0].material_index,
           "OBJECT": lambda i: objects.index(i.obj)}[spec["group_by"]]
    room, lowered = (None, None) if spec["density"] is not None else uvm.fit_margin(   # Room for the padding?
        [([tuple(l[meshes[i.obj][1]].uv) for f in i.faces for l in f.loops], i.uv_area, key(i)) for i in islands
         if _faces_key(i) not in copies], spec["texture_size"], spec["mips"], spec.get("mips_auto", False))
    if lowered:
        spec["mips"] = lowered["to"]   # Nobody set it: the packs use the most mip levels that leave the islands room.
    tile_of = {}   # Island (by its first face) -> tile index, 0 = UDIM 1001.
    groups = {}    # Tile index -> its group: a spilled island stays in its group's tiles.
    next_tile = 0
    for group in sorted({key(i) for i in islands}):
        members = [i for i in islands if key(i) == group and _faces_key(i) not in copies]
        tiles = uvm.tiles_needed([i.uv_area for i in members]) if spec["density"] is not None else 1
        if next_tile + tiles > uvm.MAX_TILES:
            raise ValueError("the layout needs more than %d tiles: lower the density" % uvm.MAX_TILES)
        for island, tile in zip(members, uvm.assign_tiles([i.uv_area for i in members], tiles)):
            tile_of[_faces_key(island)] = next_tile + tile
        groups.update(dict.fromkeys(range(next_tile, next_tile + tiles), group))
        next_tile += tiles
    for copy, master in copies.items():
        tile_of[copy] = tile_of[master]
    for island in islands:   # Every island into its tile first, so a tile's neighbours never wander in.
        _move_to(island, meshes[island.obj][1], tile_of[_faces_key(island)])
    used = sorted(set(tile_of.values()))
    index = 0
    def send_on(tile, moving, meshes):
        """Islands leaving ``tile`` go together to its group's next unpacked tile (spill_tile)."""
        if not moving:
            return
        target = uvm.spill_tile(used, used.index(tile), groups)
        if target >= uvm.MAX_TILES:
            raise ValueError("the layout needs more than %d tiles: lower the density" % uvm.MAX_TILES)
        for island in moving:
            tile_of[_faces_key(island)] = target
            _move_to(island, meshes[island.obj][1], target)
        if target not in groups:
            groups[target] = groups[tile]
            used.append(target)

    while index < len(used):   # Each tile is packed once; at a fixed density, what overflows it moves on together.
        tile = used[index]
        index += 1
        islands, meshes = _rebind(objects, fixed, shapes)
        here = [i for i in islands if tile_of[_faces_key(i)] == tile]
        if spec["density"] is not None:   # At most the tile's planned fill goes into its pack; the rest moves on.
            early = uvm.over_fill([i.uv_area for i in here],
                                  [_faces_key(i) not in copies and _faces_key(i) not in copies.values() for i in here])
            send_on(tile, [here[k] for k in early], meshes)
            here = [i for k, i in enumerate(here) if k not in early]
        # Packed at UDIM 1001, then moved to its tile: Pack Islands' closest-UDIM placement kept islands centred on
        # 1001 inside it, but packed those centred on any other tile outside that tile (pool VM, 2026-11-15). Only
        # the selection moves, so the islands 1001 already holds stay where their own pack put them.
        for island in here:
            _move_to(island, meshes[island.obj][1], 0)
        _select(objects, meshes, here)
        yield {"scale": spec["density"] is None, "merge": bool(copies), "done": index - 1, "tiles": len(used)}
        islands, meshes = _rebind(objects, fixed, shapes)
        here = [i for i in islands if tile_of[_faces_key(i)] == tile]
        leaving = []
        if spec["density"] is not None:
            leaving = uvm.overflowing([_inside(i, meshes[i.obj][1], 0, 0) for i in here],
                                      [_faces_key(i) not in copies and _faces_key(i) not in copies.values()
                                       for i in here],
                                      [i.uv_area for i in here])
        send_on(tile, [here[k] for k in leaving], meshes)   # Those left inside keep the places this pack gave them.
        u, v = uvm.udim_offset(tile)
        if u or v:
            for k, island in enumerate(here):
                if k not in leaving:
                    _scale(island, meshes[island.obj][1], 1.0, (u, v))
    islands, meshes = _rebind(objects, fixed, shapes)
    for island in islands:
        if _faces_key(island) in copies:
            island.stacked_on = True
    for obj in objects:
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return _receipt(spec, objects, islands, meshes, sorted(set(tile_of.values())), stacked, started, room, lowered)


def _inside(island, layer, u, v):
    lo_u, lo_v, hi_u, hi_v = _bounds(island, layer)
    return lo_u >= u - 1e-5 and lo_v >= v - 1e-5 and hi_u <= u + 1 + 1e-5 and hi_v <= v + 1 + 1e-5


def _receipt(spec, objects, islands, meshes, used, stacked, started, room, lowered):
    size = spec["texture_size"]
    densities = [uvm.texel_density(i.uv_area, i.world_area, size) for i in islands if i.stacked_on is None]
    weights = [i.world_area for i in islands if i.stacked_on is None]
    low, high, mean = uvm.spread(densities, weights)
    area = sum(i.uv_area for i in islands if i.stacked_on is None)
    return {"objects": sorted(o.name for o in objects), "islands": len(islands), "stacked": stacked,
            "tiles": [1001 + t for t in used], "efficiency": round(uvm.efficiency([area], len(used)), 4),
            "density_px_per_m": {"low": round(low, 2), "high": round(high, 2), "mean": round(mean, 2)},
            "margin_px": 2 ** spec["mips"], "gap_px": 2 ** (spec["mips"] + 1), "mips_kept": spec["mips"],
            "texture_size": size, "search": spec["search"], "packer": spec["packer"], "room": room,
            "margin_lowered": lowered, "milliseconds": round((time.perf_counter() - started) * 1000.0, 1)}
