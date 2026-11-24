# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Without a usable GPU, every GPU-drawn feature refuses with one message and nothing crashes; a Cycles render with
compositing still writes its image (doc/angelo/render_engines.rst, "Machines without a GPU").

``ANGELO_QA_REFUSE_GPU_CONTEXT`` (honoured in background mode only, read once as Angelo starts: the manifest's
variants set it) makes the system's GPU context refuse, as a machine without a usable GPU does (Microsoft's GDI Generic
1.1, a remote desktop, an outdated driver), so this runs on GPU machines too. Background Blender starts its GPU on
first use: ``main_first`` makes that start on the main thread (``gpu.init()``), ``thread_first`` on a thread before
anything on the main thread touches the GPU (``ANGELO_QA_GPU_UNAVAILABLE_ORDER=thread``; macOS skips it with its
reason: the background GHOST start there builds the application menus, which AppKit wants on the main thread, as
upstream's own first start from a job's thread does too). Each case would have ended Angelo with an access violation
before the capability check (test_blender_look on the 2.1.0 Windows builder, 4235812eb92; the review of 1dbbaf55627
for the threads and the render job's own context). test_native_gpu_capability_gate keeps every entry point on it.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
from pathlib import Path
import tempfile
import time
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
REFUSAL = "This needs a graphics card (GPU), and none is usable on this computer"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]


def outcome(fn):
    try:
        return {"ok": True, "value": fn()}
    except Exception as error:   # noqa: BLE001 - the refusal is the expected outcome.
        return {"ok": False, "error": type(error).__name__, "text": str(error)[:300]}


def refused(name, fn):
    result = outcome(fn)
    observed[name] = result
    checks[name + "_refused"] = not result["ok"] and REFUSAL in result.get("text", "")
    return result


def scene(engine, frames=1):
    bpy.ops.wm.read_factory_settings(use_empty=False)
    current = bpy.context.scene
    current.render.engine = engine
    current.render.resolution_x, current.render.resolution_y, current.render.resolution_percentage = 64, 36, 100
    current.frame_end = frames
    if engine == "CYCLES":
        current.cycles.samples, current.cycles.device = 1, "CPU"
    return current


def render(engine):
    scene(engine)
    bpy.ops.render.render(write_still=False)


def grease_pencil_over_cycles():
    scene("CYCLES")
    bpy.ops.object.grease_pencil_add(type="STROKE")
    bpy.ops.render.render(write_still=False)


def lightprobe_bake():
    scene("BLENDER_EEVEE")
    bpy.ops.object.lightprobe_add(type="VOLUME")
    bpy.ops.object.lightprobe_cache_bake(subset="ALL")


def gl_load():
    image = bpy.data.images.new("gpu-unavailable", 8, 8)
    image.gl_load()


def cycles_composite():
    current = scene("CYCLES")
    current.render.use_compositing = True
    tree = bpy.data.node_groups.new("composite", "CompositorNodeTree")
    current.compositing_node_group = tree
    layers = tree.nodes.new("CompositorNodeRLayers")
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    output = tree.nodes.new("NodeGroupOutput")
    tree.links.new(layers.outputs["Image"], output.inputs[0])
    path = Path(tempfile.mkdtemp(prefix="angelo-gpu-unavailable-")) / "composite.png"
    current.render.filepath = str(path)
    current.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    return path.stat().st_size if path.exists() else 0


try:
    import gpu
    import threading
    checks["knob_set_by_the_runner"] = os.environ.get("ANGELO_QA_REFUSE_GPU_CONTEXT") == "1"
    if os.environ.get("ANGELO_QA_GPU_UNAVAILABLE_ORDER") == "thread":
        if sys.platform == "darwin":
            observed["thread_first_skipped"] = "macOS: the background GHOST start must run on the main thread"
        else:
            # The first start, on a thread, with nothing GPU on the main thread before: refused, and alive.
            on_thread = {}
            thread = threading.Thread(target=lambda: on_thread.update(outcome(gpu.init)))
            thread.start()
            thread.join(60.0)
            observed["thread_first_gpu_init"] = on_thread
            checks["first_start_on_a_thread_refused"] = (not thread.is_alive() and not on_thread.get("ok", True)
                                                         and REFUSAL in on_thread.get("text", ""))
    first = refused("gpu_init", gpu.init)
    checks["knob_took_effect"] = not first["ok"]   # The GPU had not started before this script.
    refused("render_eevee", lambda: render("BLENDER_EEVEE"))
    refused("render_workbench", lambda: render("BLENDER_WORKBENCH"))
    refused("grease_pencil_over_cycles", grease_pencil_over_cycles)
    refused("lightprobe_bake", lightprobe_bake)
    refused("image_gl_load", gl_load)
    # Preview jobs render on a job's thread: the check answers truthfully there too (a material's is EEVEE, an
    # object's is the off-screen view).
    scene("BLENDER_EEVEE")
    material = bpy.data.materials.new("preview")
    observed["material_preview"] = outcome(material.asset_generate_preview)
    observed["object_preview"] = outcome(bpy.context.scene.objects["Cube"].asset_generate_preview)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and bpy.app.is_job_running("RENDER_PREVIEW"):
        time.sleep(0.2)
    observed["preview_job_still_running"] = bpy.app.is_job_running("RENDER_PREVIEW")
    checks["preview_jobs_did_not_crash"] = True   # This line is reached only if the process is alive.
    # A save with a camera thumbnail (the off-screen view): the file is written, without the thumbnail.
    scene("CYCLES")
    bpy.context.preferences.filepaths.file_preview_type = "CAMERA"
    blend = Path(tempfile.mkdtemp(prefix="angelo-gpu-unavailable-")) / "thumb.blend"
    observed["thumbnail_save"] = outcome(lambda: bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True))
    checks["save_with_camera_thumbnail_writes_the_file"] = blend.exists() and blend.stat().st_size > 0
    # What needs no GPU keeps working: the compositor composites on the CPU.
    composite = outcome(cycles_composite)
    observed["cycles_composite"] = composite
    checks["cycles_with_compositing_writes_its_image"] = composite["ok"] and (composite.get("value") or 0) > 0
    # Last (a job it starts keeps running until Blender exits): the render job's own context, which
    # render.render INVOKE_DEFAULT creates before the job starts.
    scene("CYCLES")
    observed["render_invoke"] = outcome(lambda: sorted(bpy.ops.render.render("INVOKE_DEFAULT")))
    time.sleep(2.0)
    checks["render_invoke_did_not_crash"] = True   # Reached only if the process is alive.
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    # default=repr: an operator's result is a set ({'FINISHED'}).
    (OUT / "gpu-unavailable.json").write_text(json.dumps(report, indent=2, default=repr))
failed = sorted(name for name, passed in checks.items() if not passed)
print("GPU_UNAVAILABLE failed checks:", json.dumps(failed))
assert report["success"], failed
