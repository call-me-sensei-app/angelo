# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Benchmark-like views after new materials, the old path against ``view_warm`` (GUI, factory startup, unbilled).

Run by hand in a copy of the app: ``Angelo --factory-startup --python bench_view_shader_wait.py`` with
``ANGELO_QA_DIR`` set (and optionally ``PROBE_PLAN``, ``PROBE_BATCH``, ``PROBE_BATCH_M``, ``PROBE_SLOTS``). The
add-on read is this checkout's (``PROBE_TREE``, default the checkout itself), so the app must hold it too.

Objects of several materials each (SLOTS, a face set a slot, as a chess piece, an outfit or a car body
carries them) and MATERIAL views as well as LIT: ``*_M`` steps add BATCH_M objects of SLOTS new materials each;
``SAME_MATERIAL`` asks the MATERIAL look of the scene its LIT view just showed (no new material).

Every material has a node graph of its own (an image texture through the UV map, a colour attribute or a procedural
chain, then mix nodes of its own number and blend modes), so no two share a shader and Blender's shader cache cannot
hide a compile, and the vertex layouts are those real assets use. A 5 ms timer records the main loop's
longest gap over each whole step (from the change to the picture), which is what the benchmark's main-loop record
measures. Steps, each on a new batch (BATCH objects, BATCH new shaders), after one untimed warm-up per path and look
(``WARM``), the new path first in each pair (whatever a step leaves warm helps the old path after it):
  OLD_n  view_capture.execute(..., wait_shaders=False): the image render compiles on the main thread (as before);
  NEW_n  the tool path (BlenderExecutor "angelo_view"): queue, pending while Blender compiles, then the view;
  MERGE_n  view_warm.soon right after the change (as a merge does), the view asked 2 s later (the lead's next request).
Writes view-shader-wait.json in ANGELO_QA_DIR.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

TREE = Path(os.environ.get("PROBE_TREE") or Path(__file__).resolve().parents[3])
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-wait"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(TREE / "scripts/addons_core"))
from angelo_agent import view_capture, view_warm  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402

BATCH = int(os.environ.get("PROBE_BATCH", "8"))
BATCH_M, SLOTS = int(os.environ.get("PROBE_BATCH_M", "4")), int(os.environ.get("PROBE_SLOTS", "5"))
ARGS = {"views": ["SCENE_CAMERA"], "shading": "LIT", "resolution": 768}
# The *_W steps first, untimed warm-ups, one per path and look: the first LIT and MATERIAL views of a session set
# their look up (studio light, world), which only the path timed first would otherwise pay.
WARM = "OLD_W NEW_W OLDMAT_W NEWMAT_W "
PLAN = os.environ.get("PROBE_PLAN", WARM + "NEW_1 OLD_1 NEW_M OLD_M SAME_MATERIAL NEWMAT_M OLDMAT_M MERGE_M NEW_M2 "
                      "OLD_M2").split()
report = {"steps": {}, "binary": bpy.app.binary_path, "batch": BATCH, "batch_m": BATCH_M, "slots": SLOTS, "plan": PLAN}
gaps = {"last": None, "max": 0.0}
state = {"i": 0, "step": None, "t": time.monotonic(), "made": 0}
OPS = ("ADD", "MULTIPLY", "SUBTRACT", "SINE", "POWER", "MINIMUM", "MAXIMUM", "ABSOLUTE")


def ticker():
    now = time.perf_counter()
    if gaps["last"] is not None:
        gaps["max"] = max(gaps["max"], now - gaps["last"])
    gaps["last"] = now
    return 0.005


SALT = int(time.time()) % 9973   # Shaders of their own each run: the system's shader cache never warms a step.


def make_material(n, owner):
    """A material of its own node graph (a shader of its own), in three kinds real assets use: an image texture
    through the UV map, a colour attribute, and a procedural chain on object coordinates."""
    material = bpy.data.materials.new("Probe material %d" % n)
    tree = material.node_tree
    principled = tree.nodes.get("Principled BSDF")
    n = n + SALT * 100   # Its chain length and operations differ from every earlier run's.
    kind = n % 3
    if kind == 0:   # An image texture through the UV map.
        image = bpy.data.images.new("Probe image %d" % n, 64, 64)
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.uv_map = "UVMap"
        tree.links.new(uv.outputs["UV"], texture.inputs["Vector"])
        source = texture.outputs["Color"]
    elif kind == 1:   # A colour attribute.
        attribute = tree.nodes.new("ShaderNodeVertexColor")
        attribute.layer_name = "Col"
        source = attribute.outputs["Color"]
    else:   # A procedural chain on object coordinates.
        coords = tree.nodes.new("ShaderNodeTexCoord")
        noise = tree.nodes.new("ShaderNodeTexNoise")
        tree.links.new(coords.outputs["Object"], noise.inputs["Vector"])
        source = noise.outputs["Color"]
    last = source
    for k in range(1 + n % 7):   # A chain of its own length and operations: a shader of its own.
        mix = tree.nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.blend_type = ("MULTIPLY", "ADD", "SCREEN", "OVERLAY", "DARKEN")[(n + k) % 5]
        tree.links.new(last, mix.inputs[6])
        last = mix.outputs[2]
    math = tree.nodes.new("ShaderNodeMath")
    math.operation = OPS[n % len(OPS)]
    tree.links.new(source, math.inputs[0])
    rough = math.outputs[0]
    for k in range(1 + SALT % 17):   # This run's own chain: code no earlier run compiled (cold caches).
        extra = tree.nodes.new("ShaderNodeMath")
        extra.operation = OPS[(SALT // 17 + k) % len(OPS)]
        tree.links.new(rough, extra.inputs[0])
        rough = extra.outputs[0]
    tree.links.new(rough, principled.inputs["Roughness"])
    tree.links.new(last, principled.inputs["Base Color"])
    return material


def new_batch(count=None, slots=1):
    """BATCH objects, each with a material of its own node graph (a shader of its own), in three kinds real assets
    use: an image texture through the UV map, a colour attribute, and a procedural chain on object coordinates."""
    for _ in range(count or BATCH):
        state["made"] += 1
        n = state["made"]
        mesh = bpy.data.meshes.new("Probe %d" % n)
        mesh.from_pydata([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], [],
                         [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
        mesh.uv_layers.new(name="UVMap")
        colours = mesh.color_attributes.new("Col", 'BYTE_COLOR', 'CORNER')
        for index, item in enumerate(colours.data):
            item.color = ((index * 0.13) % 1, (n * 0.07) % 1, 0.5, 1.0)
        for index, polygon in enumerate(mesh.polygons):
            polygon.material_index = index % slots
        obj = bpy.data.objects.new(mesh.name, mesh)
        obj.location = ((n % 8) * 1.5 - 6.0, (n // 8) * 1.5 - 6.0, 0.0)
        bpy.context.scene.collection.objects.link(obj)
        for slot in range(slots):
            mesh.materials.append(make_material(n * 10 + slot, n))
    bpy.context.view_layer.update()


def start(step):
    if step == "SAME_MATERIAL":
        pass
    elif step[-2:] == "_M" or step[-3:-1] == "_M":
        new_batch(BATCH_M, SLOTS)
    else:
        new_batch()
    ARGS["shading"] = "MATERIAL" if "MAT" in step else "LIT"
    # MATERIAL views may be cached: each MATERIAL step a resolution of its own, never a view-cache hit.
    ARGS["resolution"] = {"SAME_MATERIAL": 256, "NEWMAT_M": 384, "OLDMAT_M": 512}.get(step, 768)
    gaps["max"], gaps["last"] = 0.0, time.perf_counter()
    state.update(step=step, began=time.perf_counter(), pending=None, asked=None)
    record = report["steps"][step] = {"made_objects": state["made"]}
    if step.startswith("OLD"):
        result = view_capture.execute(state["window"], dict(ARGS), state["refs"], None, False)
        record["call_ms"] = round((time.perf_counter() - state["began"]) * 1000, 1)
        record["draw_wall_ms"] = result["timing"]["draw_wall_ms"]
        state["done_at"] = time.perf_counter()
    elif step.startswith("NEW") or step == "SAME_MATERIAL":
        ask(record)
    else:
        view_warm.soon(state["window"])
        state["asked"] = time.perf_counter() + 2.0


def ask(record):
    started = time.perf_counter()
    result = state["executor"].execute("angelo_view", dict(ARGS))
    record["call_ms"] = round((time.perf_counter() - started) * 1000, 1)
    if isinstance(result, StudioPending):
        record["pending"] = True
        state["pending"] = result
    else:
        record["pending"] = False
        record["draw_wall_ms"] = result["timing"]["draw_wall_ms"]
        state["done_at"] = time.perf_counter()


def finish(error=None):
    if error:
        report["error"] = error
    (OUT / "view-shader-wait.json").write_text(json.dumps(report, indent=2, default=str))
    print("PROBE_DONE", json.dumps({"error": bool(error)}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


def tick():
    try:
        if state["step"] is None:
            if time.monotonic() - state["t"] < 0.5:
                return 0.05
            if state["i"] == 0:
                window = bpy.context.window_manager.windows[0]
                state["window"] = window
                state["refs"] = ReferenceStore(OUT, "0f6c3c87-6b4e-4a35-9b7f-6c4c8f6f0c11")
                state["executor"] = BlenderExecutor(window)
                state["executor"].observation_store = state["refs"]
                bpy.app.timers.register(ticker, first_interval=0.0)
            if state["i"] >= len(PLAN):
                return finish()
            start(PLAN[state["i"]])
            return 0.01
        step, record = state["step"], report["steps"][state["step"]]
        if state.get("asked") and time.perf_counter() >= state["asked"]:
            state["asked"] = None
            record["compiling_when_asked"] = view_warm.compiling()
            ask(record)
            return 0.01
        if state["pending"] is not None:
            result = state["pending"].poll()
            if result is None:
                return 0.005
            record["waited_ms"] = result.get("timing", {}).get("shaders_waited_ms")
            record["draw_wall_ms"] = result.get("timing", {}).get("draw_wall_ms")
            record["error"] = result.get("error")
            state["pending"], state["done_at"] = None, time.perf_counter()
            return 0.01
        if state.get("done_at") is not None and time.perf_counter() - state["done_at"] > 0.3:
            record["step_ms"] = round((state["done_at"] - state["began"]) * 1000, 1)
            record["main_loop_max_gap_ms"] = round(gaps["max"] * 1000, 1)
            state.update(step=None, done_at=None, t=time.monotonic(), i=state["i"] + 1)
        return 0.01
    except Exception:   # noqa: BLE001
        return finish(traceback.format_exc())


bpy.app.timers.register(tick, first_interval=1.0)
