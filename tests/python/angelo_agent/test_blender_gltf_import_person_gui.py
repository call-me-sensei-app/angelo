# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A person's glTF import welds a file with morph targets, in the real window (doc/angelo/generative_studio.rst,
"Models with morph targets").

From Angelo's default start (Director): a GLB with UV seams and one shape key is written in this run's own temporary
folder, then imported the way File > Import and a dropped file import it (``INVOKE_DEFAULT`` with the file: the
importer's options popup, where Weld Shape Key Seams shows ticked), confirmed with Return as a person confirms it. With
the shape key moved, no seam shades apart, and the face corners keep the file's UVs. A script's import of the same
file (no option) still splits the seams, Blender's default. Screenshots: the popup, and the shape key at full.

Run through ``run_native_suites.py --gui --include test_blender_gltf_import_person_gui``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import tempfile
import time
import traceback
from collections import defaultdict
from pathlib import Path

import bpy
from mathutils import Vector

from angelo_agent import director, product_env  # noqa: E402
import qa_boot  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-gltf-person"))
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="angelo-gltf-person-"))
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def make_fixture(path):
    """A smooth UV sphere whose UV layout splits its seams, with a 'Move' shape key, exported alone and removed."""
    area, region = view3d()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, location=(0, 0, 1))
        ob = bpy.context.object
        bpy.ops.object.shade_smooth()
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(angle_limit=math.radians(40))
        bpy.ops.object.mode_set(mode='OBJECT')
        ob.shape_key_add(name="Basis")
        key = ob.shape_key_add(name="Move")
        for index, vertex in enumerate(ob.data.vertices):
            p = Vector(vertex.co) - Vector((0, 0, 0))
            key.data[index].co = p + p.normalized() * 0.35 * math.sin(3 * p.x) * math.cos(2 * p.z)
        bpy.ops.object.select_all(action='DESELECT')
        ob.select_set(True)
        bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=True)
    bpy.data.objects.remove(ob, do_unlink=True)


def seam_points(objects, value):
    """Points whose corner normals differ by more than a degree with every shape key at ``value``."""
    for ob in objects:
        if ob.data.shape_keys:
            for block in ob.data.shape_keys.key_blocks[1:]:
                block.value = value
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    count = 0
    for ob in objects:
        mesh = ob.evaluated_get(depsgraph).to_mesh()
        at = defaultdict(list)
        for index, loop in enumerate(mesh.loops):
            co = ob.data.vertices[loop.vertex_index].co
            at[(round(co.x, 4), round(co.y, 4), round(co.z, 4))].append(Vector(mesh.corner_normals[index].vector))
        count += sum(1 for normals in at.values()
                     if max((math.degrees(normals[0].angle(n, 0.0)) for n in normals[1:]), default=0.0) > 1.0)
        ob.evaluated_get(depsgraph).to_mesh_clear()
    return count


def new_meshes(before):
    return [ob for ob in bpy.context.scene.objects if ob.type == 'MESH' and ob.name not in before]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
        state["failed_checks"] = [k for k, v in state["checks"].items() if not v]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    state.pop("scenario", None)
    (OUT / "gltf-import-person.json").write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_GLTF_PERSON", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def scenario():
    checks, observed = state["checks"], state["observed"]
    began = time.monotonic()
    while not (director.is_director(window()) and qa_boot.started()):
        assert time.monotonic() - began < 90, "Director never settled: %s" % qa_boot.waiting_on()
        yield
    checks["starts_in_director_mode"] = True
    path = WORK / "morph-sphere.glb"
    make_fixture(path)
    yield

    # A script's import: Blender's default, seams split.
    before = {ob.name for ob in bpy.context.scene.objects}
    bpy.ops.import_scene.gltf(filepath=str(path))
    script = new_meshes(before)
    observed["script_vertices"] = sum(len(ob.data.vertices) for ob in script)
    observed["script_seams_moved"] = seam_points(script, 1.0)
    checks["a_script_keeps_blender_s_default"] = observed["script_seams_moved"] > 0
    for ob in script:
        bpy.data.objects.remove(ob, do_unlink=True)
    yield

    # The person's import: the importer invoked with the file, as File > Import and a drop invoke it.
    before = {ob.name for ob in bpy.context.scene.objects}
    area, region = view3d()
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.import_scene.gltf('INVOKE_DEFAULT', filepath=str(path))
    for _ in range(4):
        yield
    capture("gltf-person-1-options.png")
    x, y = region.x + region.width // 2, region.y + region.height // 2
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS')
    window().event_simulate(type='RET', value='RELEASE')
    began = time.monotonic()
    while not new_meshes(before):
        assert time.monotonic() - began < 30, "The person's import never finished"
        yield
    person = new_meshes(before)
    observed["person_vertices"] = sum(len(ob.data.vertices) for ob in person)
    observed["person_seams_moved"] = seam_points(person, 1.0)
    checks["the_person_s_import_welds"] = (observed["person_seams_moved"] == 0
                                           and observed["person_vertices"] < observed["script_vertices"])
    checks["the_uvs_came_with_it"] = all(ob.data.uv_layers for ob in person)
    with bpy.context.temp_override(window=window(), area=area, region=region):
        bpy.ops.view3d.view_all()
    for _ in range(3):
        yield
    capture("gltf-person-2-shape-key-at-full.png")


def tick():
    try:
        assert time.monotonic() - started < 300, "The fixture timed out in " + state["phase"]
        if state["phase"] == "boot":
            state["phase"], state["scenario"] = "run", scenario()
            return 1.0
        try:
            next(state["scenario"])
        except StopIteration:
            finish()
            return None
        return 0.3
    except Exception as error:
        finish(error)
        return None


if __name__ == "__main__":
    bpy.app.timers.register(tick, first_interval=2.0)
