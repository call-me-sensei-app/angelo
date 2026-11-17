# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A mesh's topology seal breaks on every way a script can write its topology, in a real background Blender.

After a reviewed script, Angelo validates the meshes the script may have left unsound (``script_mesh_check``).
Validating every mesh cost 36 to 175 ms a call on large scenes, so it validates only a mesh whose TOPOLOGY SEAL is
broken: ``Mesh.topology_seal()`` records the identity and write version of the mesh's edge, face-corner and
face-offset arrays and its element counts; ``Mesh.is_topology_sealed`` is True while none of them was handed out
for writing, replaced or resized since. Skipping a sealed mesh is only sound if NO write can leave the seal
standing, so this suite writes topology every way RNA allows and reads the seal after each:

* an item's property, set through a fresh lookup and through an item, an array or a collection the script kept
  from BEFORE the seal (a kept item writes straight into the array, with no accessor in between);
* ``foreach_set`` on edges, face corners and faces;
* the attribute API on the arrays' own attributes (``.edge_verts``, ``.corner_vert``, ``.corner_edge``);
* ``from_pydata``, ``clear_geometry``, ``vertices/edges/loops/polygons.add``, ``update(calc_edges=True)``,
  ``flip_normals``, ``bmesh.to_mesh`` and leaving Edit Mode.

Each must break the seal. The controls must keep it, or untouched meshes would be validated after all: reading
the counts, renaming, moving the object, and evaluating it with a modifier (what the executor does after every
script). A mesh nobody sealed is not sealed: a new one, a copy, one read from a file.

Positions have a seal of their own, read together with the topology's as ``Mesh.is_geometry_sealed``: a script
that writes only coordinates must leave the topology seal standing (the mesh is not validated) and break the
geometry seal (its coordinates are scanned for a value that is not a finite number, the one thing ``validate``
does for positions). So every way to write a coordinate is here too: ``foreach_set``, a fresh and a kept vertex,
a kept ``co``, the ``position`` attribute as a whole and by item, and ``transform``.
What else was observed (reads through RNA, ``validate``, ``update``, a save) is in the report as ``observed``:
RNA hands out writable arrays even to a reader, so a read may break the seal; that costs a check, never a miss.

Writes ``mesh-topology-seal.json`` in ``ANGELO_QA_DIR``; exits 1 on any failed check.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-mesh-topology-seal-"))
OUT.mkdir(parents=True, exist_ok=True)
VERTS = [(x, y, 0) for y in range(3) for x in range(3)]
FACES = [(0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 7, 6), (4, 5, 8, 7)]
BROKEN, POSITIONS, KEPT, OBSERVED = "broken", "positions", "kept", "observed"
# What (is_topology_sealed, is_geometry_sealed) must be after each kind of action.
EXPECTED = {BROKEN: (False, False), POSITIONS: (True, False), KEPT: (True, True)}
checks = {}
report = {"cases": {}, "observed": {}, "unavailable": {}, "checks": checks}


class Unavailable(Exception):
    """This Blender offers no such way to write: said in the report, and nothing to prove."""


def fresh(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(VERTS, [], FACES)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, mesh


def attribute(mesh, name):
    try:
        return mesh.attributes[name]
    except KeyError as error:
        raise Unavailable("mesh.attributes has no %r" % name) from error


def keep(mesh):
    """What a script could have kept from before the seal: items, an item's array, collections."""
    kept = {"edge": mesh.edges[0], "edge_vertices": mesh.edges[0].vertices, "loop": mesh.loops[0],
            "polygon": mesh.polygons[0], "edges": mesh.edges, "loops": mesh.loops, "polygons": mesh.polygons,
            "vertex": mesh.vertices[0], "vertex_co": mesh.vertices[0].co, "vertices": mesh.vertices}
    for name, key in ((".corner_vert", "corner"), ("position", "position")):
        try:
            found = mesh.attributes[name]
            kept.update({key + "_item": found.data[0], key + "_data": found.data})
        except KeyError:
            pass
    return kept


def had(kept, name):
    if name not in kept:
        raise Unavailable("mesh.attributes has no such attribute for %r" % name)
    return kept[name]


def remove(mesh, name):
    found = attribute(mesh, name)
    try:
        mesh.attributes.remove(found)
    except RuntimeError as error:   # Blender refuses to remove an attribute it requires.
        raise Unavailable("mesh.attributes.remove(%r) is refused" % name) from error


def flat(collection, name, size):
    values = [0] * (len(collection) * size)
    collection.foreach_get(name, values)
    return values


def set_item(item, name, value):
    setattr(item, name, value)


def leave_edit_mode(obj):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.object.mode_set(mode="OBJECT")


def through_bmesh(mesh):
    built = bmesh.new()
    built.from_mesh(mesh)
    built.to_mesh(mesh)
    built.free()


def evaluate(obj):
    obj.modifiers.new("Subdivision", "SUBSURF")
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return len(evaluated.data.vertices), [edge.vertices[0] for edge in evaluated.data.edges][:2]


def again(mesh):
    mesh.clear_geometry()
    mesh.from_pydata(VERTS, [], FACES)


# name -> (what the seal must be afterwards, the action on (obj, mesh, kept)). Every index written here is one
# the mesh has: the seal is this suite's subject, and what an unsound write costs is
# test_blender_script_invalid_mesh's.
CASES = {
    # A fresh lookup of an item, then its property.
    "edge_vertices_set": (BROKEN, lambda o, m, k: set_item(m.edges[0], "vertices", tuple(m.edges[0].vertices))),
    "edge_vertices_index_set": (BROKEN, lambda o, m, k: m.edges[0].vertices.__setitem__(0, m.edges[0].vertices[0])),
    "loop_vertex_index_set": (BROKEN, lambda o, m, k: set_item(m.loops[0], "vertex_index", m.loops[0].vertex_index)),
    "loop_edge_index_set": (BROKEN, lambda o, m, k: set_item(m.loops[0], "edge_index", m.loops[0].edge_index)),
    "polygon_loop_start_set": (BROKEN, lambda o, m, k: set_item(m.polygons[0], "loop_start", 0)),
    "polygon_vertices_set": (BROKEN, lambda o, m, k: set_item(m.polygons[0], "vertices", FACES[0])),
    # What the script kept from before the seal.
    "kept_edge_vertices_set": (BROKEN, lambda o, m, k: set_item(k["edge"], "vertices", (0, 1))),
    "kept_edge_array_index_set": (BROKEN, lambda o, m, k: k["edge_vertices"].__setitem__(0, 0)),
    "kept_loop_vertex_index_set": (BROKEN, lambda o, m, k: set_item(k["loop"], "vertex_index", 0)),
    "kept_loop_edge_index_set": (BROKEN, lambda o, m, k: set_item(k["loop"], "edge_index", k["loop"].edge_index)),
    "kept_polygon_loop_start_set": (BROKEN, lambda o, m, k: set_item(k["polygon"], "loop_start", 0)),
    "kept_polygon_vertices_set": (BROKEN, lambda o, m, k: set_item(k["polygon"], "vertices", FACES[0])),
    "kept_edges_foreach_set": (BROKEN, lambda o, m, k: k["edges"].foreach_set(
        "vertices", flat(m.edges, "vertices", 2))),
    "kept_loops_foreach_set": (BROKEN, lambda o, m, k: k["loops"].foreach_set(
        "vertex_index", flat(m.loops, "vertex_index", 1))),
    "kept_polygons_foreach_set": (BROKEN, lambda o, m, k: k["polygons"].foreach_set(
        "loop_start", flat(m.polygons, "loop_start", 1))),
    "kept_attribute_item_set": (BROKEN, lambda o, m, k: set_item(had(k, "corner_item"), "value", 0)),
    "kept_attribute_data_foreach_set": (BROKEN, lambda o, m, k: had(k, "corner_data").foreach_set(
        "value", flat(m.loops, "vertex_index", 1))),
    # Whole arrays.
    "edges_foreach_set": (BROKEN, lambda o, m, k: m.edges.foreach_set("vertices", flat(m.edges, "vertices", 2))),
    "loops_foreach_set_vertex_index": (BROKEN, lambda o, m, k: m.loops.foreach_set(
        "vertex_index", flat(m.loops, "vertex_index", 1))),
    "loops_foreach_set_edge_index": (BROKEN, lambda o, m, k: m.loops.foreach_set(
        "edge_index", flat(m.loops, "edge_index", 1))),
    "polygons_foreach_set_loop_start": (BROKEN, lambda o, m, k: m.polygons.foreach_set(
        "loop_start", flat(m.polygons, "loop_start", 1))),
    # The attribute API on the arrays' own attributes.
    "attribute_edge_verts_foreach_set": (BROKEN, lambda o, m, k: attribute(m, ".edge_verts").data.foreach_set(
        "value", flat(m.edges, "vertices", 2))),
    "attribute_corner_vert_foreach_set": (BROKEN, lambda o, m, k: attribute(m, ".corner_vert").data.foreach_set(
        "value", flat(m.loops, "vertex_index", 1))),
    "attribute_corner_edge_foreach_set": (BROKEN, lambda o, m, k: attribute(m, ".corner_edge").data.foreach_set(
        "value", flat(m.loops, "edge_index", 1))),
    "attribute_corner_vert_item_set": (BROKEN, lambda o, m, k: set_item(
        attribute(m, ".corner_vert").data[0], "value", 0)),
    "attribute_edge_verts_item_set": (BROKEN, lambda o, m, k: set_item(
        attribute(m, ".edge_verts").data[0], "value", tuple(m.edges[0].vertices))),
    "attribute_removed": (BROKEN, lambda o, m, k: remove(m, ".corner_edge")),
    # Building, resizing and rebuilding.
    "from_pydata_again": (BROKEN, lambda o, m, k: again(m)),
    "clear_geometry": (BROKEN, lambda o, m, k: m.clear_geometry()),
    "vertices_add": (BROKEN, lambda o, m, k: m.vertices.add(1)),
    "edges_add": (BROKEN, lambda o, m, k: m.edges.add(1)),
    "loops_add": (BROKEN, lambda o, m, k: m.loops.add(1)),
    "polygons_add": (BROKEN, lambda o, m, k: m.polygons.add(1)),
    "update_calc_edges": (BROKEN, lambda o, m, k: m.update(calc_edges=True)),
    "flip_normals": (BROKEN, lambda o, m, k: m.flip_normals()),
    "bmesh_to_mesh": (BROKEN, lambda o, m, k: through_bmesh(m)),
    "leave_edit_mode": (BROKEN, lambda o, m, k: leave_edit_mode(o)),
    # Coordinates alone: the topology seal stands, the geometry seal is broken.
    "positions_foreach_set": (POSITIONS, lambda o, m, k: m.vertices.foreach_set("co", flat(m.vertices, "co", 3))),
    "position_item_set": (POSITIONS, lambda o, m, k: set_item(m.vertices[0], "co", (0, 0, 0))),
    "position_item_index_set": (POSITIONS, lambda o, m, k: m.vertices[0].co.__setitem__(0, 0.0)),
    "kept_vertex_co_set": (POSITIONS, lambda o, m, k: set_item(k["vertex"], "co", (0, 0, 0))),
    "kept_co_index_set": (POSITIONS, lambda o, m, k: k["vertex_co"].__setitem__(0, 0.0)),
    "kept_vertices_foreach_set": (POSITIONS, lambda o, m, k: k["vertices"].foreach_set(
        "co", flat(m.vertices, "co", 3))),
    "attribute_position_foreach_set": (POSITIONS, lambda o, m, k: attribute(m, "position").data.foreach_set(
        "vector", flat(m.vertices, "co", 3))),
    "attribute_position_item_set": (POSITIONS, lambda o, m, k: set_item(
        attribute(m, "position").data[0], "vector", (0, 0, 0))),
    "kept_attribute_position_item_set": (POSITIONS, lambda o, m, k: set_item(
        had(k, "position_item"), "vector", (0, 0, 0))),
    "kept_attribute_position_data_foreach_set": (POSITIONS, lambda o, m, k: had(k, "position_data").foreach_set(
        "vector", flat(m.vertices, "co", 3))),
    "transform": (POSITIONS, lambda o, m, k: m.transform(Matrix.Translation((1, 0, 0)))),
    # Controls: none of these can write the mesh's arrays, and each must leave both seals standing.
    "read_the_counts": (KEPT, lambda o, m, k: (len(m.vertices), len(m.edges), len(m.loops), len(m.polygons))),
    "rename": (KEPT, lambda o, m, k: set_item(m, "name", "Renamed")),
    "object_moved": (KEPT, lambda o, m, k: set_item(o, "location", (1, 2, 3))),
    "evaluated_with_a_modifier": (KEPT, lambda o, m, k: evaluate(o)),
    # Observed only: said in the report. A reader is handed writable arrays, so a read may break the seal.
    "read_edges_one_by_one": (OBSERVED, lambda o, m, k: [tuple(edge.vertices) for edge in m.edges]),
    "read_edges_foreach_get": (OBSERVED, lambda o, m, k: flat(m.edges, "vertices", 2)),
    "read_positions": (OBSERVED, lambda o, m, k: flat(m.vertices, "co", 3)),
    "validate_a_sound_mesh": (OBSERVED, lambda o, m, k: m.validate(verbose=False, clean_customdata=False)),
    "update": (OBSERVED, lambda o, m, k: m.update()),
    "calc_loop_triangles": (OBSERVED, lambda o, m, k: m.calc_loop_triangles()),
    "bmesh_from_mesh": (OBSERVED, lambda o, m, k: bmesh.new().from_mesh(m)),
    "uv_layer_added": (OBSERVED, lambda o, m, k: m.uv_layers.new(name="UV")),
    "saved": (OBSERVED, lambda o, m, k: bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "seal-save.blend"), copy=True)),
}


def seals(mesh):
    return [bool(mesh.is_topology_sealed), bool(mesh.is_geometry_sealed)]


def run_case(name):
    expected, action = CASES[name]
    obj, mesh = fresh(name)
    entry = report["cases"][name] = {"expected": expected}
    kept = keep(mesh)
    mesh.topology_seal()
    entry["sealed_before"] = seals(mesh)
    try:
        action(obj, mesh, kept)
    except Unavailable as error:
        entry["unavailable"] = report["unavailable"][name] = str(error)
        return
    entry["sealed_after"] = seals(mesh)
    if expected == OBSERVED:
        report["observed"][name] = entry["sealed_after"]
        checks[name + "_starts_sealed"] = entry["sealed_before"] == [True, True]
        return
    label = {BROKEN: "_breaks_the_seal", POSITIONS: "_breaks_the_geometry_seal_only", KEPT: "_keeps_both_seals"}
    checks[name + label[expected]] = (entry["sealed_before"] == [True, True]
                                      and entry["sealed_after"] == list(EXPECTED[expected]))
    if expected != KEPT:   # Found sound again, it is sealed again: the check's own next step.
        mesh.validate(verbose=False, clean_customdata=False)
        mesh.topology_seal()
        entry["sealed_again"] = seals(mesh)
        checks[name + "_is_sealed_again_after_a_check"] = entry["sealed_again"] == [True, True]


def nobody_sealed():
    """A mesh no check has found sound is not sealed: a new one, a copy of a sealed one, one read from a file."""
    obj, mesh = fresh("Never")
    checks["a_new_mesh_is_not_sealed"] = seals(mesh) == [False, False]
    mesh.topology_seal()
    mesh.topology_seal()   # Sealing a sealed mesh changes nothing.
    checks["a_sealed_mesh_stays_sealed"] = seals(mesh) == [True, True]
    mesh.topology_seal(positions=False)   # For a reader that found only the positions written: topology alone.
    checks["sealing_the_topology_alone_leaves_the_positions_written"] = seals(mesh) == [True, False]
    mesh.topology_seal()
    copy = mesh.copy()
    checks["a_copy_is_not_sealed"] = seals(copy) == [False, False]
    checks["its_original_stays_sealed"] = seals(mesh) == [True, True]
    copy.edges.foreach_set("vertices", flat(copy.edges, "vertices", 2))   # The copy shared the arrays until now.
    copy.vertices.foreach_set("co", flat(copy.vertices, "co", 3))
    checks["writing_the_copy_leaves_the_original_sealed"] = seals(mesh) == [True, True]
    path = OUT / "seal.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    loaded = bpy.data.meshes.get("Never")
    checks["a_mesh_read_from_a_file_is_not_sealed"] = loaded is not None and seals(loaded) == [False, False]
    for name in ("is_topology_sealed", "is_geometry_sealed"):   # The whole file's seals in one call.
        flags = [True] * len(bpy.data.meshes)
        bpy.data.meshes.foreach_get(name, flags)
        checks["a_files_%s_is_read_in_one_call" % name] = len(flags) > 40 and not any(flags)


def main():
    has = {"is_topology_sealed", "is_geometry_sealed"} <= set(bpy.types.Mesh.bl_rna.properties.keys())
    checks["this_blender_has_the_seal"] = has and "topology_seal" in bpy.types.Mesh.bl_rna.functions.keys()
    if not checks["this_blender_has_the_seal"]:
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for name in [c for c in os.environ.get("ANGELO_QA_CASES", "").split(",") if c] or list(CASES):
        print("ANGELO_MESH_TOPOLOGY_SEAL case", name, flush=True)   # The last line before a crash names its case.
        run_case(name)
    # Only the attribute API may be missing for an array (then it is no way to write one): every other way must
    # have been there to prove.
    checks["every_way_but_the_attribute_api_was_there"] = not [name for name in report["unavailable"]
                                                               if "attribute" not in name]
    nobody_sealed()


try:
    main()
except Exception:   # Reported as a failed check, never a silent pass.
    checks["no_error"] = False
    report["error"] = traceback.format_exc()
report["success"] = bool(checks) and all(checks.values())
(OUT / "mesh-topology-seal.json").write_text(json.dumps(report, indent=2))
print("ANGELO_MESH_TOPOLOGY_SEAL", json.dumps({"success": report["success"], "observed": report["observed"],
                                               "unavailable": report["unavailable"],
                                               "failed": sorted(k for k, v in checks.items() if not v)}), flush=True)
sys.exit(0 if report["success"] else 1)
