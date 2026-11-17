# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Undo and Redo across Edit, Sculpt and Vertex Paint mode boundaries, with changes no undo step recorded, keep the
scene consistent (readfile.cc reads again the IDs changed in place since the last memfile write or read and the IDs
pointing at one the step does not store; doc/angelo/agent_execution.rst, "Undo after an unrecorded change").

Mode steps are not memfile steps: Undo inside a mode is the mode's own system, and crossing the mode's entry decodes
the memfile step before it, with Angelo's Sculpt hooks around that read. For each mode: enter it (recorded), make a
change of the mode (recorded), add an object from Python (unrecorded), Undo twice across the entry; then enter again,
change, leave (all recorded), add again (unrecorded), Undo three times and Redo three times. After every key the whole
scene is read (undo_unrecorded_qa.consistent) and must hold; the unrecorded objects must be gone at the end.

A window is required: ``run_native_suites.py --gui --include test_blender_undo_unrecorded_modes.py``.
"""

import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

import qa_boot  # noqa: E402
import qa_input  # noqa: E402
import qa_waits  # noqa: E402
import undo_unrecorded_qa as qa  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-undo-unrecorded-modes"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "undo-unrecorded-modes.json"
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "steps": []}
# (mode, the recorded change made in it): one Edit Mode operator, one Sculpt operator, one Vertex Paint operator.
MODES = (("EDIT", "mesh.subdivide", {}), ("SCULPT", "paint.mask_flood_fill", {"mode": 'VALUE', "value": 1.0}),
         ("VERTEX_PAINT", "paint.vertex_color_set", {}))
SCALE = qa_waits.timeout_scale()
started = None


def settle():
    for _ in range(8):
        yield 0.1


def check(label, extra_ok=True):
    problems = qa.consistent()
    state["steps"].append({"step": label, "problems": problems, "mode": bpy.context.mode,
                           "objects": sorted(o.name for o in bpy.data.objects)})
    ok = not problems and bool(extra_ok)
    state["checks"][label] = ok
    REPORT.write_text(json.dumps(state, indent=2))


def select_cube():
    cube = bpy.data.objects["Cube"]
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == cube)
    bpy.context.view_layer.objects.active = cube


def unrecorded(name):
    bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, bpy.data.meshes.new(name)))


def scenario():
    qa.ensure_outliner()
    yield 0.5
    for mode, operator, properties in MODES:
        state["phase"] = mode
        select_cube()
        qa.recorded_operator("object.mode_set", mode=mode)
        yield 0.3
        qa.recorded_operator(operator, **properties)
        yield 0.3
        inside = "Inside %s" % mode
        unrecorded(inside)
        undos = 0
        while bpy.context.mode != 'OBJECT' and undos < 4:   # Over the mode's steps, then its entry (a memfile read).
            qa.press("ed.undo")
            yield from settle()
            undos += 1
            check("%s: undo inside, step %d" % (mode, undos))
        check("%s: inside add gone after crossing the entry" % mode,
              bpy.context.mode == 'OBJECT' and inside not in bpy.data.objects)

        select_cube()
        qa.recorded_operator("object.mode_set", mode=mode)
        yield 0.3
        qa.recorded_operator(operator, **properties)
        yield 0.3
        qa.recorded_operator("object.mode_set", mode='OBJECT')
        yield 0.3
        after = "After %s" % mode
        unrecorded(after)
        for n in range(3):
            qa.press("ed.undo")
            yield from settle()
            check("%s: undo %d across" % (mode, n + 1))
        check("%s: after add gone" % mode, after not in bpy.data.objects)
        for n in range(3):
            qa.press("ed.redo")
            yield from settle()
            check("%s: redo %d across" % (mode, n + 1))
        check("%s: back in Object Mode after the redos" % mode, bpy.context.mode == 'OBJECT')
        if bpy.context.mode != 'OBJECT':
            qa.recorded_operator("object.mode_set", mode='OBJECT')
            yield 0.3


def finish(error=None):
    if error is not None:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        print(state["traceback"], flush=True)
    else:
        state["phase"] = "complete"
        state["success"] = (all(state["checks"].values()) and
                            all(any(k.startswith(m + ": back in Object Mode") for k in state["checks"])
                                for m, _op, _props in MODES))
    REPORT.write_text(json.dumps(state, indent=2))
    print("ANGELO_UNDO_UNRECORDED_MODES", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


_steps = scenario()


def tick():
    global started
    try:
        started = time.monotonic() if started is None else started
        assert time.monotonic() - started < 240 * SCALE, "timed out in phase " + state["phase"]
        return next(_steps)
    except StopIteration:
        finish()
    except Exception as error:   # noqa: BLE001
        finish(error)
    return None


REPORT.write_text(json.dumps(state, indent=2))   # A boot that never ends leaves phase "boot" (the gate says why).
bpy.app.timers.register(qa_boot.gated(qa_input.after_clicks(tick)), first_interval=0.5)
