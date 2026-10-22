# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every side of a benchmark trial renders Cycles on the machine's GPU, set the same way (test harness only).

The maintainer's rule for the GPU test VMs (2026-10-21): before each trial, the trial's own Blender, Angelo's or
stock Blender with the MCP add-on, gets the Cycles preference a person with that GPU would set
(``prepare``):

- Cycles lists its devices. Stock Blender never does this by itself at startup, and with an empty list a GPU
  scene renders on the CPU without a word.
- The first backend that lists a GPU is selected: OptiX, CUDA when OptiX lists none, then HIP, Metal and oneAPI.
  Its GPUs are enabled and the CPU is not.
- The trial's own preferences are saved.
- Every scene renders with device GPU, files opened later included.

The trial's receipt records the resulting ``device_line``. A trial whose Blender lists no GPU fails at setup:
nothing falls back to the CPU silently. Angelo's own device setup leaves a backend someone set alone
(angelo_agent/render_device.py), so both sides keep this choice.

``ANGELO_BENCH_GPU`` is ``require`` (the step runs; no GPU fails the trial) or ``off``. Unset, it is ``require``
on Windows, where every bench machine is a GPU test VM, and ``off`` elsewhere: macOS runs keep the factory
preference. The process that starts a run decides once and hands the value to every child (``environment``),
so the two sides of a trial always run the same mode.

``probe`` is the dry run's check. A short background Blender (the side's own binary, in empty user folders) runs
``prepare``, and its device line must name the expected GPU:

- ``ANGELO_BENCH_GPU_EXPECT``, when set;
- else the first GPU ``nvidia-smi`` lists (on the T4 VMs, "Tesla T4").

Runs as a Blender script (``--python bench_gpu.py``) for ``probe``. Standard library only outside Blender.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = "ANGELO_BENCH_GPU"
EXPECT_ENV = "ANGELO_BENCH_GPU_EXPECT"
MODES = ("require", "off")
ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")   # The first backend that lists a GPU is used.
MARK = "ANGELO_BENCH_GPU "
PROBE_TIMEOUT = 300.   # Seconds: listing initializes the GPU driver.
WINDOWS_SMI = (r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",)


def mode(environ=None, platform=None):
    """``require`` or ``off``: the variable when set, else ``require`` on Windows only."""
    environ = os.environ if environ is None else environ
    value = environ.get(ENV, "").strip().lower()
    if value and value not in MODES:
        raise ValueError("%s must be %s, not %r" % (ENV, " or ".join(MODES), value))
    return value or ("require" if (platform or sys.platform) == "win32" else "off")


def environment(environ=None, platform=None):
    """The variables every child of a run gets, so each side decides nothing on its own."""
    environ = os.environ if environ is None else environ
    values = {ENV: mode(environ, platform)}
    if environ.get(EXPECT_ENV):
        values[EXPECT_ENV] = environ[EXPECT_ENV]
    return values


# In Blender ---------------------------------------------------------------------------------------------------

def _cycles(bpy):
    addon = bpy.context.preferences.addons.get("cycles")
    if addon is None:
        raise RuntimeError("Cycles is not enabled in this Blender: no GPU device can be set")
    return addon.preferences


def select(bpy):
    """Select the first backend whose device list has a GPU, enable its GPUs and not the CPU.
    ``{"backend", "devices"}``; raises when no backend lists a GPU."""
    prefs = _cycles(bpy)
    built = [item[0] for item in prefs.get_device_types(bpy.context) if item[0] in ORDER]
    for backend in sorted(built, key=ORDER.index):
        # Listing refreshes ``prefs.devices``; only the last listing's entries are read below.
        if any(entry.type == backend for entry in prefs.get_devices_for_type(backend)):
            break
    else:
        raise RuntimeError("Cycles lists no GPU device (backends in this build: %s); the trial would render on "
                           "the CPU" % (", ".join(built) or "none"))
    prefs.compute_device_type = backend
    for entry in prefs.devices:
        entry.use = entry.type == backend
    return {"backend": backend, "devices": [entry.name for entry in prefs.devices if entry.use]}


def _scenes_on_gpu(bpy):
    for scene in bpy.data.scenes:
        scene.cycles.device = "GPU"


def _on_load(*_args):
    import bpy
    _scenes_on_gpu(bpy)


def device_line(bpy):
    """What Cycles will render on: the backend, its enabled devices and the scenes' device."""
    prefs = _cycles(bpy)
    backend = prefs.compute_device_type or "NONE"
    names = [entry.name for entry in prefs.devices if entry.use and entry.type == backend]
    scenes = sorted({scene.cycles.device for scene in bpy.data.scenes})
    return "Cycles %s: %s; scene device %s" % (backend, ", ".join(names) or "no device", "/".join(scenes) or "-")


def render_on_gpu(bpy):
    """Every scene renders with device GPU, files opened later included."""
    _scenes_on_gpu(bpy)
    handler = bpy.app.handlers.persistent(_on_load)
    if handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(handler)


def receipt(bpy, chosen):
    """The receipt entry for the selection ``chosen`` (``select``'s result) as it now stands."""
    return {"mode": "require", "device_line": device_line(bpy), **chosen}


def prepare(bpy):
    """The trial's GPU preference (module docstring). Returns the receipt entry; raises when there is no GPU or
    the preferences were not saved."""
    chosen = select(bpy)
    render_on_gpu(bpy)
    config = bpy.utils.user_resource("CONFIG")
    if bpy.ops.wm.save_userpref() != {"FINISHED"} or not os.path.isfile(os.path.join(config, "userpref.blend")):
        raise RuntimeError("The trial's preferences were not saved in %s" % config)
    return dict(receipt(bpy, chosen), preferences_saved=True)


def run_if_required(bpy, environ=None):
    """The one call both sides make before a trial: ``prepare`` when its mode is ``require``, else the receipt
    entry ``{"mode": "off"}`` (the factory preference stays)."""
    return prepare(bpy) if mode(environ) == "require" else {"mode": "off"}


# Outside Blender ----------------------------------------------------------------------------------------------

def expected_gpu(environ=None, run=subprocess.run):
    """The GPU a device line must name: ``ANGELO_BENCH_GPU_EXPECT``, else nvidia-smi's first GPU, else None."""
    environ = os.environ if environ is None else environ
    if environ.get(EXPECT_ENV):
        return environ[EXPECT_ENV]
    # Older Windows drivers install it outside PATH.
    smi = shutil.which("nvidia-smi") or next((path for path in WINDOWS_SMI if os.path.isfile(path)), None)
    if not smi:
        return None
    try:
        listed = run([smi, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                     timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((line.strip() for line in listed.splitlines() if line.strip()), None)


def receipt_problem(entry, expected):
    """None when a receipt's GPU entry names the expected GPU; else what is wrong."""
    if not expected:
        return "no GPU to expect: set %s or make nvidia-smi available" % EXPECT_ENV
    line = (entry or {}).get("device_line") if isinstance(entry, dict) else None
    if not line:
        return "the receipt has no Cycles device line"
    if expected.lower() not in line.lower():
        return "the device line %r does not name %r" % (line, expected)
    if "scene device GPU" not in line:
        return "the device line %r has a scene not rendering on the GPU" % line
    return None


def dry_run_problem(entry, environ=None):
    """A dry run's check of a trial receipt's GPU entry: None when the mode is ``off`` or the entry names the
    expected GPU; else what is wrong."""
    if mode(environ) != "require":
        return None
    return receipt_problem(entry, expected_gpu(environ))


def probes(sides, environ=None):
    """``probe`` for each ``(side, binary)`` when the mode is ``require``: ``(results, problems)``."""
    if mode(environ) != "require":
        return [], []
    results = [dict(probe(binary, environ), side=side) for side, binary in sides if binary]
    return results, ["GPU check, %s side: %s" % (r["side"], r["problem"]) for r in results if r["problem"]]


def probe(binary, environ=None, timeout=PROBE_TIMEOUT):
    """The dry run's check for one side's Blender: ``{"binary", "gpu", "problem"}``; ``problem`` None is a pass."""
    environ = dict(os.environ if environ is None else environ)
    result = {"binary": str(binary), "gpu": None, "problem": None}
    with tempfile.TemporaryDirectory(prefix="angelo-bench-gpu-") as user:
        for name in ("RESOURCES", "CONFIG", "SCRIPTS", "DATAFILES", "EXTENSIONS"):
            folder = os.path.join(user, name.lower())
            os.makedirs(folder)
            environ["BLENDER_USER_" + name] = folder
        environ[ENV] = "require"
        command = [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code",
                   "1", "--python", os.path.abspath(__file__)]
        try:
            done = subprocess.run(command, env=environ, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as error:
            result["problem"] = "the probe did not run: %s" % error
            return result
    found = [line[len(MARK):] for line in done.stdout.splitlines() if line.startswith(MARK)]
    if not found:
        tail = (done.stdout + done.stderr).strip().splitlines()[-5:]
        result["problem"] = "the probe reported no device line (exit %s): %s" % (done.returncode, " | ".join(tail))
        return result
    result["gpu"] = json.loads(found[-1])
    result["problem"] = result["gpu"].get("error") or receipt_problem(result["gpu"], expected_gpu(environ))
    return result


if __name__ == "__main__":   # Inside the probe's Blender only.
    import bpy
    try:
        print(MARK + json.dumps(prepare(bpy)), flush=True)
    except Exception as error:  # Reported to ``probe``, which fails the dry run with it.
        print(MARK + json.dumps({"error": "%s: %s" % (type(error).__name__, error)}), flush=True)
