# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hold live and retired Python views across CPU render/bake workers, drivers and file load.

Background-only; no provider, account or network. This covers completed jobs and file reload;
GUI cancellation, loading during a job and quitting during a job remain separate gates.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy


def removed(read):
    try:
        read()
    except ReferenceError:
        return True
    return False


def run(checks, out):
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = bpy.context.scene
    obj = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = obj
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 2
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 2
    scene.render.resolution_x = scene.render.resolution_y = 32
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    material = bpy.data.materials.new("Lifetime bake")
    material.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(material)
    image = bpy.data.images.new("Lifetime bake", width=16, height=16)
    texture = material.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    material.node_tree.nodes.active = texture
    held = []
    obj["live"] = [1.0, 2.0]
    live_view = memoryview(obj["live"])
    modifier = obj.modifiers.new("Live", 'BEVEL')
    modifier.width = 0.02
    retired = obj.modifiers.new("Retired", 'SOLIDIFY')
    obj.modifiers.remove(retired)
    calls = []

    def driver_probe():
        calls.append(1)
        assert removed(lambda: retired.thickness)
        return modifier.width

    bpy.app.driver_namespace["lifetime_probe"] = driver_probe
    curve = obj.driver_add("location", 0)
    curve.driver.expression = "lifetime_probe() + frame * 0.001"
    for index in range(10):
        obj["retire"] = [float(index)] * 64
        view = memoryview(obj["retire"])
        array = obj["retire"]
        del obj["retire"]
        held.append((view, array, float(index)))
        scene.frame_set(index + 1)
        frame = out / ("render-%02d.png" % index)
        scene.render.filepath = str(frame)
        result = bpy.ops.render.render(write_still=True)
        assert result == {'FINISHED'} and frame.is_file() and frame.stat().st_size > 0
        assert removed(lambda: array[0])
        assert view[0] == index
        assert modifier.width > 0
        assert live_view[0] == 1.0
    checks["ten_renders_with_held_views"] = True
    checks["python_driver_executed"] = bool(calls) and curve.driver.is_valid
    assert bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}) == {'FINISHED'}
    checks["bake_completed"] = any(image.pixels[i] > 0 for i in range(0, len(image.pixels), 4))
    checks["held_views_survive_workers"] = all(view[0] == value for view, _, value in held)
    checks["retired_arrays_reject_access"] = all(removed(lambda: array[0]) for _, array, _ in held)
    # This function exists only in the fixture's process, not in the saved document.
    obj.driver_remove("location", 0)
    bpy.app.driver_namespace.pop("lifetime_probe", None)
    blend = out / "lifetime-soak.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    checks["file_load_invalidates_old_modifier"] = removed(lambda: modifier.width)
    checks["file_load_keeps_exported_buffer"] = live_view[0] == 1.0
    live_view.release()
    for view, _, _ in held:
        view.release()


def main():
    assert bpy.app.background, "Run this fixture in background mode"
    out = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-sub-data-soak-"))
    out.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "checks": {}, "paid_requests": 0}
    try:
        run(report["checks"], out)
        report["success"] = bool(report["checks"]) and all(report["checks"].values())
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "sub-data-soak.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_SUB_DATA_SOAK", json.dumps(report), flush=True)
    if not report["success"]:
        raise RuntimeError("Sub-data lifetime soak failed; see sub-data-soak.json")


main()
