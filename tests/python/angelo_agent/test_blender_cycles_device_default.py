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
* A later start (``render_device_session.early``, as Cycles' register runs it) applies the saved listing at once,
  with no child; a background Cycles render passes the device gate in the real Cycles add-on, and before this
  suite imports Angelo's add-on, such a render has imported none of it (only ``angelo_render_device``).
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

# Before this suite imports anything of Angelo's: a Cycles render in a background start goes through the device
# gate without ever importing the agent package (Cycles imports angelo_render_device only).
AGENT_BEFORE = sorted(name for name in sys.modules if name.split(".")[0] == "angelo_agent")
bpy.ops.wm.read_factory_settings(use_empty=False)
bpy.context.scene.render.engine = "CYCLES"
bpy.context.scene.cycles.device = "CPU"
bpy.context.scene.cycles.samples = 1
bpy.context.scene.render.resolution_x = bpy.context.scene.render.resolution_y = 16
bpy.ops.render.render()
AGENT_AFTER = sorted(name for name in sys.modules if name.split(".")[0] == "angelo_agent")
GATE_LOADED = "angelo_render_device.session" in sys.modules

try:
    from angelo_agent import render_device, render_device_session
except ImportError:   # Run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import render_device, render_device_session

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-cycles-device-default-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, notes = {}, {}
STATE = tempfile.mkdtemp(prefix="angelo-render-device-")   # Never the person's state folder.


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            print(traceback.format_exc())
        return function
    return wrap


@case("a_cycles_render_in_a_background_start_imports_no_agent_module")
def _():
    notes["agent_modules"] = {"before": AGENT_BEFORE, "after_render": AGENT_AFTER, "gate_loaded": GATE_LOADED}
    return AGENT_BEFORE == [] and AGENT_AFTER == [] and GATE_LOADED


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
    render_device.attach(STATE)   # Reading factory settings registers Cycles again, which attaches its own.
    render_device._seen = render_device._available = render_device._listing = None
    render_device._tried, render_device._status = False, {"state": "not_set_up"}
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



@case("a_later_start_applies_the_saved_listing_at_once_without_a_child")
def _():
    saved = {kind: [list(device) for device in devices] for kind, devices in (render_device._available or {}).items()}
    bpy.ops.wm.read_factory_settings(use_empty=True)   # This build's listing is saved; preferences are Blender's again.
    render_device.attach(STATE)
    render_device._seen = render_device._available = render_device._listing = None
    render_device._tried, render_device._status = False, {"state": "not_set_up"}
    started = time.perf_counter()
    render_device_session.early()   # As Cycles' register runs it in a background start.
    prefs = bpy.context.preferences.addons["cycles"].preferences   # Cycles registered again: read it afresh.
    notes["saved_apply_s"] = round(time.perf_counter() - started, 4)
    result = render_device.status()
    notes["saved_status"] = result
    expected = next((kind for kind in render_device.ORDER
                     if any(device[1] == kind for device in saved.get(kind, ()))), None)
    no_child = render_device._listing is None and not render_device._tried
    if expected is None:
        return no_child and result["state"] == "no_gpu"
    return (no_child and result.get("from") == "saved" and prefs.compute_device_type == expected
            and notes["saved_apply_s"] < 0.05)


@case("a_background_cycles_render_passes_the_device_gate")
def _():
    # A CPU render (a GPU's first render compiles kernels for minutes; never in a suite): the real Cycles add-on
    # calls the gate before its session reads the device, and the session's device is recorded.
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.render.resolution_x = scene.render.resolution_y = 32
    before = render_device_session.count()
    bpy.ops.render.render()
    used = render_device_session.used_since(before)
    notes["gate_used"] = used
    return used is not None and used[0]["device"] == "CPU" and used[1] == {"gpu": False, "reason": "scene"}


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "notes": notes}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_CYCLES_DEVICE_DEFAULT", json.dumps(report))
