# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A parent-inverse statement reads its parent's current matrix (script_prepare), in Blender.

The recorded car: a body's location set, its parts built at world positions and parented with
``p.matrix_parent_inverse = body.matrix_world.inverted()`` before Blender evaluated the move, so every part landed
offset by the body's location. Run as written the parts move; run as the executor runs it they stay where they were
built. A fresh parent is left alone (the same result, no evaluation), a keyed parent is evaluated at the current frame,
and a loop over many parts evaluates once. Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

try:
    from angelo_agent import script_prepare
    from angelo_agent.script_errors import SCRIPT_FILE
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import script_prepare
    from angelo_agent.script_errors import SCRIPT_FILE

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-parent-inverse-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, notes = {}, {}
CAR = '''
def box(name, lo, hi):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj
body = box("Body", (-2, -0.9, -0.3), (2, 0.9, 0.3))
body.location = (0, -1, 0.6)
{keys}
parts = [box("Part %d" % i, (x - 0.3, -2.0, 0.0), (x + 0.3, -1.8, 0.7)) for i, x in enumerate({xs})]
{update}
for p_ in parts: p_.parent=body; p_.matrix_parent_inverse=body.matrix_world.inverted()
'''


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            print(traceback.format_exc())
        return function
    return wrap


def fresh():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    bpy.context.scene.frame_set(1)


def centres(prefix="Part"):
    bpy.context.view_layer.update()
    out = []
    for obj in sorted((o for o in bpy.data.objects if o.name.startswith(prefix)), key=lambda o: o.name):
        corners = [obj.matrix_world @ v.co for v in obj.data.vertices]
        out.append(tuple(round(sum(c[a] for c in corners) / len(corners), 4) for a in range(3)))
    return out


def run(code, prepared):
    fresh()
    namespace = {"bpy": bpy}
    if prepared:
        prepared = script_prepare.Prepared(bpy, bpy.context.view_layer)
        exec(prepared.compiled(code, namespace), namespace)
        return centres(), prepared.after(code)
    exec(compile(code, SCRIPT_FILE, "exec"), namespace)
    return centres(), {}


@case("the_recorded_car_keeps_its_parts_where_they_were_built")
def _():
    code = CAR.format(keys="", xs="(-1.3, 1.3)", update="")
    written, _ = run(code, False)
    kept, note = run(code, True)
    notes["car"] = {"as_written": written, "as_run": kept, "note": note}
    built = [(-1.3, -1.9, 0.35), (1.3, -1.9, 0.35)]
    moved = [(x, y - 1, z + 0.6) for x, y, z in built]   # Offset by the body's location, as recorded.
    return written == moved and kept == built and note["evaluated_before_parent_inverse"]["lines"] == [14]


@case("a_fresh_parent_is_left_alone")
def _():
    code = CAR.format(keys="", xs="(-1.3, 1.3)", update="bpy.context.view_layer.update()")
    written, _ = run(code, False)
    kept, note = run(code, True)
    return written == kept == [(-1.3, -1.9, 0.35), (1.3, -1.9, 0.35)] and note == {}


@case("a_keyed_parent_is_evaluated_at_the_current_frame")
def _():
    keys = ("body.location.x = -10.5; body.keyframe_insert('location', frame=1)\n"
            "body.location.x = 10.5; body.keyframe_insert('location', frame=120)\nbody.location.x = -10.5")
    code = CAR.format(keys=keys, xs="(-11.8, -9.2)", update="")
    kept, note = run(code, True)
    bpy.context.scene.frame_set(120)
    later = centres()
    notes["keyed"] = {"frame_1": kept, "frame_120": later}
    return (kept == [(-11.8, -1.9, 0.35), (-9.2, -1.9, 0.35)] and later == [(9.2, -1.9, 0.35), (11.8, -1.9, 0.35)]
            and note["evaluated_before_parent_inverse"]["lines"] == [16])


@case("a_loop_over_many_parts_evaluates_once")
def _():
    code = CAR.format(keys="", xs="[i * 0.1 for i in range(200)]", update="")
    started = time.perf_counter()
    kept, note = run(code, True)
    seconds = time.perf_counter() - started
    notes["many"] = round(seconds, 3)
    return kept[0] == (0.0, -1.9, 0.35) and len(kept) == 200 and seconds < 5.0 and \
        note["evaluated_before_parent_inverse"]["lines"] == [14]


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "notes": notes}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_PARENT_INVERSE", json.dumps(report))
