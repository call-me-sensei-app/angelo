# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collision reaches Unreal, Unity and Godot in the form each reads, written on the exported copy, in a real
background Blender.

Game Physics colliders (``doc/angelo/game_physics.rst``) export as OMI or Godot name suffixes, which only Godot
reads. ``collision_conventions.apply`` renames and rearranges them for one engine on the copy an export writes (the
engine link's write-back child loads the saved file and exports that copy; the person's scene is never touched).
Plan ``release-2.1.0-plan.md``, section 7; game-engine requests, theme 1.

The scene: a rendered mesh ``Crate`` with convex hulls from ``collision_fit``; a collision-only concave mesh
``WallCollision`` (Mesh shape) with its own hulls; a plain mesh ``Lamp`` with no collision.

* **Unreal** (``UCX_`` convex collision by mesh name): every hull is named ``UCX_<owner>_NN`` after the nearest
  rendered mesh it belongs to, made a sibling of that mesh at the same world placement; a concave collision-only
  mesh is left out of the copy (Unreal reads no concave collision by name; its hulls carry it) and the report says
  so; hulls with no rendered owner keep their collision-only owner's name.
* **Unity** (Angelo Link's importer rule): hulls are named ``<owner>_ConvexCollider_NN`` and a concave collision
  mesh ``<owner>_MeshCollider``, which the plugin turns into MeshColliders (convex or not) and hides.
* **Godot**: names stay as they are; Game Physics already writes OMI or Godot suffixes by the scene's setting.
* Rendered meshes keep their names in every engine; the report counts what was renamed or left out.

Run through ``run_native_suites.py --include test_blender_collision_conventions``; check
``collision-conventions.json`` for ``success``. Expected to fail on 2476124521f: the module does not exist there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import re
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-collision-conventions-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False

REPORT = OUT / "collision-conventions.json"
BLEND = OUT / "scene.blend"
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def solid(name, outline, height, matrix):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y, 0)) for x, y in outline])
    top = [e for e in bmesh.ops.extrude_face_region(bm, geom=[face])["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top, vec=(0, 0, height))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = matrix
    return obj


def build_scene():
    from angelo_agent import collision_fit
    crate = solid("Crate", [(0, 0), (2, 0), (2, 0.6), (0.6, 0.6), (0.6, 2), (0, 2)], 0.8, Matrix.Translation((3, 0, 0)))
    collision_fit.fit(crate, 'DECOMPOSE', 6, 32)
    wall = solid("WallCollision", [(0, 0), (4, 0), (4, 0.3), (0, 0.3)], 2.0, Matrix.Translation((-5, 0, 0)))
    wall.angelo_physics.shape = 'TRIMESH'
    wall.angelo_physics.collision_only = True
    collision_fit.fit(wall, 'CONVEX', 1, 32)
    solid("Lamp", [(0, 0), (0.3, 0), (0.3, 0.3), (0, 0.3)], 1.5, Matrix.Translation((0, 4, 0)))
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))


def world_points():
    return {o.name: [tuple(round(v, 4) for v in (o.matrix_world @ p.co)) for p in o.data.vertices[:3]]
            for o in bpy.data.objects if o.type == 'MESH'}


def reload():
    bpy.ops.wm.open_mainfile(filepath=str(BLEND), load_ui=False)


def main():
    try:
        try:
            from angelo_agent import collision_conventions
        except ImportError:
            check("module_exists", False)
            raise SystemExit
        check("module_exists", True)
        build_scene()
        hull_points = sorted(sum(world_points().values(), []))

        reload()
        report = collision_conventions.apply(bpy.context.scene, "unreal")
        names = sorted(o.name for o in bpy.data.objects if o.type == 'MESH')
        ucx = [n for n in names if n.startswith("UCX_")]
        check("unreal_hulls_named_ucx_after_owner", ucx and all(re.fullmatch(r"UCX_(Crate|WallCollision)_\d\d", n)
                                                                 for n in ucx), names)
        check("unreal_hulls_are_siblings", all(bpy.data.objects[n].parent is None for n in ucx))
        check("unreal_concave_left_out", "WallCollision" not in names and report.get("left_out") == ["WallCollision"]
              and report.get("notes"), report)
        check("unreal_rendered_names_kept", {"Crate", "Lamp"} <= set(names))
        moved = sorted(p for n in ucx for p in world_points()[n])
        check("unreal_hulls_keep_world_placement", set(moved) <= set(hull_points))

        reload()
        report = collision_conventions.apply(bpy.context.scene, "unity")
        names = sorted(o.name for o in bpy.data.objects if o.type == 'MESH')
        check("unity_convex_names", any(re.fullmatch(r"Crate_ConvexCollider_\d\d", n) for n in names), names)
        check("unity_concave_name", "WallCollision_MeshCollider" in names, names)
        check("unity_rendered_names_kept", {"Crate", "Lamp"} <= set(names))

        reload()
        before = sorted(o.name for o in bpy.data.objects)
        report = collision_conventions.apply(bpy.context.scene, "godot")
        check("godot_unchanged", sorted(o.name for o in bpy.data.objects) == before and report.get("renamed") == 0,
              report)
        state["success"] = len(state["checks"]) == 10 and all(state["checks"].values())
    except SystemExit:
        pass
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_COLLISION_CONVENTIONS", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
