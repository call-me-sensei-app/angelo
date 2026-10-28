# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""glTF files with morph targets, in a real background Blender (``gltf_morph``, ``io_scene_gltf2``).

Blender's glTF importer keeps split seam vertices apart unless Merge Vertices is on, and it cannot keep morph normals,
so once a shape key moves the surface the seams shade apart, while hard edges a model has at rest stay its own (a person importing kit GLBs on 1.0.2 found it; stock
Blender 5.2 does the same). Fixtures this test writes, each exported with a UV layout that splits its seams and one
shape key: a UV sphere (the case the report described), and held out from the fix's development a torus with a twist,
a capped cylinder with a bulge, and a file holding a morph mesh beside a plain one; plus a plain sphere with no shape
key. Checked per morph fixture: the importer's default still splits the seams (Blender's default is unchanged) and
they shade apart once the shape key moves, the import warns on the console (which an agent's script output captures),
an import with ``gltf_morph.import_options`` welds it so no seam shades apart, and Angelo's model path
(``studio_assets_mesh.import_glb``) welds it too. The plain file imports with no option and no warning, and an
explicit Merge Vertices import never warns. Receipt: ``gltf-morph-weld.json``.
"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import bpy
from mathutils import Vector

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-gltf-morph-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, details = {}, {}

from angelo_agent import gltf_morph, studio_assets_mesh  # noqa: E402
import io_scene_gltf2  # noqa: E402

# Corner normals at one point that differ by more than this are a visible seam.
SEAM_DEGREES = 1.0


def check(name, value, detail=None):
    """Receipt contract (native_suite_checks): each check is exactly True or False; details are kept apart."""
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail


def seamed(add, deform):
    """The active object from ``add``, smooth, seams split by a UV layout, with a 'Move' shape key from ``deform``."""
    add()
    ob = bpy.context.object
    bpy.ops.object.shade_smooth()
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(40))
    bpy.ops.object.mode_set(mode='OBJECT')
    ob.shape_key_add(name="Basis")
    key = ob.shape_key_add(name="Move")
    for index, vertex in enumerate(ob.data.vertices):
        key.data[index].co = deform(Vector(vertex.co))
    return ob


def export(path, objects):
    bpy.ops.object.select_all(action='DESELECT')
    for ob in objects:
        ob.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB' if path.suffix == ".glb" else 'GLTF_SEPARATE',
                              use_selection=True, export_morph_normal=True)


def fixtures(folder):
    files = {}

    def scene():
        bpy.ops.wm.read_factory_settings(use_empty=True)

    scene()
    ob = seamed(lambda: bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0),
                lambda p: p + p.normalized() * 0.35 * math.sin(3 * p.x) * math.cos(2 * p.z))
    export(folder / "sphere.glb", [ob])
    files["sphere"] = folder / "sphere.glb"
    scene()
    ob = seamed(lambda: bpy.ops.mesh.primitive_torus_add(major_segments=48, minor_segments=16),
                lambda p: Vector((p.x * math.cos(p.z * 2) - p.y * math.sin(p.z * 2),
                                  p.x * math.sin(p.z * 2) + p.y * math.cos(p.z * 2), p.z * 1.6)))
    export(folder / "torus.gltf", [ob])
    files["torus"] = folder / "torus.gltf"
    scene()
    ob = seamed(lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=40, depth=2.0),
                lambda p: p + Vector((0.5 * p.z * p.z, 0.3 * math.sin(2 * p.z), 0.2 * p.x)))
    export(folder / "cylinder.glb", [ob])
    files["cylinder"] = folder / "cylinder.glb"
    scene()
    morph = seamed(lambda: bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, location=(-2, 0, 0)),
                   lambda p: p + Vector((0, 0, 0.3 * p.x)))
    bpy.ops.mesh.primitive_monkey_add(location=(2, 0, 0))
    export(folder / "mixed.glb", [morph, bpy.context.object])
    files["mixed"] = folder / "mixed.glb"
    scene()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    bpy.ops.object.shade_smooth()
    export(folder / "plain.glb", [bpy.context.object])
    files["plain"] = folder / "plain.glb"
    return files


def spreads(objects, value):
    """``{(object, rest point): worst degrees between its corner normals}`` with every shape key at ``value``. Points
    are keyed by the rest shape, so the same point is compared at rest and moved."""
    for ob in objects:
        if ob.type == 'MESH' and ob.data.shape_keys:
            for block in ob.data.shape_keys.key_blocks[1:]:
                block.value = value
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    found = {}
    for ob in objects:
        if ob.type != 'MESH':
            continue
        mesh = ob.evaluated_get(depsgraph).to_mesh()
        at = defaultdict(list)
        for index, loop in enumerate(mesh.loops):
            co = ob.data.vertices[loop.vertex_index].co
            at[(ob.name, round(co.x, 4), round(co.y, 4), round(co.z, 4))].append(
                Vector(mesh.corner_normals[index].vector))
        for point, normals in at.items():
            found[point] = max((math.degrees(normals[0].angle(n, 0.0)) for n in normals[1:]), default=0.0)
        ob.evaluated_get(depsgraph).to_mesh_clear()
    return found


def seam_break(objects, value):
    """``(vertices, points that shade apart only once the shape keys are at value, worst degrees among them)``.
    A hard edge the model has at rest (a flat-shaded mesh) is the model's own and is not counted."""
    rest, moved = spreads(objects, 0.0), spreads(objects, value)
    new = [moved[point] for point in moved if moved[point] > SEAM_DEGREES and rest.get(point, 0.0) <= SEAM_DEGREES]
    vertices = sum(len(ob.data.vertices) for ob in objects if ob.type == 'MESH')
    return vertices, len(new), round(max(new, default=0.0), 2)


def imported(path, **options):
    """``(objects, console text)`` of one glTF import into an empty scene."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    console = io.StringIO()
    with contextlib.redirect_stdout(console):
        result = bpy.ops.import_scene.gltf(filepath=str(path), **options)
    assert result == {'FINISHED'}, result
    return list(bpy.context.scene.objects), console.getvalue()


def main():
    with tempfile.TemporaryDirectory(prefix="angelo-gltf-morph-") as folder:
        files = fixtures(Path(folder))
        warning = io_scene_gltf2.UNWELDED_MORPH_TARGETS
        for name in ("sphere", "torus", "cylinder", "mixed"):
            path = files[name]
            check(name + "_has_morph_targets", gltf_morph.has_morph_targets(path))
            objects, console = imported(path)
            hard = sorted(point for point, spread in spreads(objects, 0.0).items() if spread > SEAM_DEGREES)
            default = seam_break(objects, 1.0)
            check(name + "_default_still_splits_and_shades_apart", default[1] > 0, default)
            check(name + "_default_warns", warning in console)
            options = gltf_morph.import_options(path)
            objects, console = imported(path, **options)
            welded = seam_break(objects, 1.0)
            check(name + "_welded_by_its_options", options == {"merge_vertices": True} and welded[1] == 0
                  and welded[0] < default[0], [options, welded])
            check(name + "_welded_import_does_not_warn", warning not in console)
            welded_hard = sorted(point for point, spread in spreads(objects, 0.0).items() if spread > SEAM_DEGREES)
            check(name + "_welding_keeps_the_model_s_hard_edges", welded_hard == hard, [len(hard), len(welded_hard)])
            bpy.ops.wm.read_factory_settings(use_empty=True)
            made = studio_assets_mesh.import_glb(path)
            model = seam_break(made.objects, 1.0)
            check(name + "_angelo_model_path_welds", model[1] == 0 and model[0] == welded[0], model)
        plain = files["plain"]
        check("plain_has_no_morph_targets", not gltf_morph.has_morph_targets(plain))
        check("plain_gets_no_option", gltf_morph.import_options(plain) == {})
        objects, console = imported(plain)
        check("plain_imports_without_a_warning", warning not in console and len(objects) == 1)
        check("not_a_gltf_is_no_morph_file", not gltf_morph.has_morph_targets(Path(folder) / "missing.glb"))

    report = {"success": all(checks.values()), "checks": checks, "details": details}
    (OUT / "gltf-morph-weld.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_GLTF_MORPH_WELD", json.dumps(report), flush=True)


main()
