# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles' scene device defaults to GPU, and Angelo's device setup lists devices off the app's own process.

Checks, in the real binary (background, local only; writes results.json under ANGELO_QA_DIR):

* A factory scene and a new scene read GPU, with no device stored.
* Saved and opened again: a scene saved with CPU chosen stays CPU, one saved with GPU stays GPU, and one that never
  chose (as stock Blender saves a scene whose device was never changed) reads GPU.
* ``render_device.setup`` returns at once and lists in a child process of this app; once the child reports, the
  compute backend is the first with a device (Metal on Apple Silicon) or, with none, stays None with a note, and
  Blender's preferences are not left dirty.
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
    from angelo_agent import render_device
except ImportError:   # Run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import render_device

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-cycles-device-default-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, notes = {}, {}


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            print(traceback.format_exc())
        return function
    return wrap


@case("a_factory_scene_and_a_new_scene_read_gpu_with_nothing_stored")
def _():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    first, new = bpy.context.scene, bpy.data.scenes.new("New")
    return all(scene.cycles.device == "GPU" and not scene.cycles.is_property_set("device") for scene in (first, new))


@case("a_saved_choice_is_kept_and_a_scene_that_never_chose_reads_gpu")
def _():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    chosen_cpu, chosen_gpu, never = bpy.context.scene, bpy.data.scenes.new("Chose GPU"), bpy.data.scenes.new("Never")
    chosen_cpu.name = "Chose CPU"
    chosen_cpu.cycles.device = "CPU"
    chosen_gpu.cycles.device = "GPU"
    path = str(OUT / "devices.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    read = {name: (bpy.data.scenes[name].cycles.device, bpy.data.scenes[name].cycles.is_property_set("device"))
            for name in ("Chose CPU", "Chose GPU", "Never")}
    notes["reopened"] = read
    return read == {"Chose CPU": ("CPU", True), "Chose GPU": ("GPU", True), "Never": ("GPU", False)}


@case("setup_lists_in_a_child_and_selects_the_first_backend_with_a_device")
def _():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    preferences = bpy.context.preferences
    prefs = preferences.addons["cycles"].preferences
    render_device.attach(tempfile.mkdtemp(prefix="angelo-render-device-"))
    render_device._seen = render_device._available = render_device._listing = None
    dirty = preferences.is_dirty
    started = time.perf_counter()
    first = render_device.setup()
    notes["setup_call_s"] = round(time.perf_counter() - started, 4)
    child = render_device._listing and render_device._listing["process"]
    notes["child_pid_differs"] = bool(child) and child.pid != os.getpid()
    while render_device._poll() is not None:   # Background mode runs no timers: poll as the app's timer would.
        time.sleep(0.05)
    result = render_device.status()
    notes.update(listing_s=round(time.perf_counter() - started, 2), status=result,
                 devices=[(entry.type, entry.name, entry.use) for entry in prefs.devices])
    expected = next((kind for kind in render_device.ORDER if any(entry.type == kind for entry in prefs.devices)), None)
    if expected is None:
        chosen = result["state"] == "no_gpu" and prefs.compute_device_type == "NONE"
    else:
        chosen = result["state"] == "set_up" and prefs.compute_device_type == expected
    return (first["state"] == "listing" and notes["setup_call_s"] < 0.25 and notes["child_pid_differs"] and chosen
            and preferences.is_dirty == dirty)


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "notes": notes}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_CYCLES_DEVICE_DEFAULT", json.dumps(report))
