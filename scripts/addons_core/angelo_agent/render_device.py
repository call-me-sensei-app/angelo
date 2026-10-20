# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles renders on the GPU by default, and the agent is told which device a render uses.

Contract: doc/angelo/long_jobs.rst, "Render device".

Stock Blender never lists the Cycles compute devices at startup (``CyclesPreferences.refresh_devices``: some
drivers crash while listing). Until something lists them (opening Preferences > System, or drawing Cycles'
Render panel) the device list is empty, and a scene whose Cycles device is GPU renders on the CPU without a
word (``intern/cycles/blender/device.cpp``, ``blender_device_info``: no enabled device matches). An agent
render, run from a script with no Cycles panel drawn, always met that empty list.

``setup`` (a second after startup, after Load Factory Preferences, and when a Cycles render's facts are
measured) never lists devices itself. Listing initializes GPU drivers, holds the interpreter lock and can end
the process, so it runs in a short child process (this app with ``--background --factory-startup``); a driver
that crashes ends only that child. When the child reports, the main thread adds Cycles' device entries as
``CyclesPreferences.update_device_entries`` would (every GPU enabled, the CPU not) and selects the first
backend that has a device: OptiX, then CUDA (NVIDIA), HIP (AMD), Metal (Apple), oneAPI (Intel). Cycles
itself initializes the backend when it first renders, on its render thread for a render job.

The person's choice wins: a compute backend someone set (``is_property_set``: in Preferences > System, or
brought in with Import Blender Settings), None included, is left alone unless it is the one Angelo applied.
Nothing is saved to Blender's preferences on Angelo's behalf (their dirty state is restored); what Angelo
applied is ``render-device.json`` in the state folder, which tells its own setup from the person's later.

``facts`` states, for a scene, the device a Cycles render will use, and why when that is the CPU; render jobs
record it when they start, and ``estimate`` turns the last finished render on the same device into a duration
estimate. Main thread only.
"""

import json
import os
import subprocess
import sys
import time

FILE = "render-device.json"
ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")   # The first backend with a device is used.
NAMES = {"OPTIX": "OptiX", "CUDA": "CUDA", "HIP": "HIP", "METAL": "Metal", "ONEAPI": "oneAPI"}
MARK = "ANGELO_CYCLES_DEVICES "
LIST_TIMEOUT = 120.0   # Seconds a listing child may take before it is ended and Cycles stays on the CPU.
# The child: the device backends this build has (cheap), then each one's devices (driver initialization).
LISTING = ("import json, _cycles\n"
           "built = dict(zip(('CUDA', 'OPTIX', 'HIP', 'METAL', 'ONEAPI'), _cycles.get_device_types()))\n"
           "found = {kind: [list(device[:3]) for device in _cycles.available_devices(kind)]\n"
           "         for kind in %r if built.get(kind)}\n"
           "print(%r + json.dumps(found), flush=True)\n" % (ORDER, MARK))
_path = None
_status = {"state": "not_set_up"}   # The last decision (``status``).
_seen = None         # The device setup this session last decided on.
_available = None    # {backend: [(name, type, id), ...]} from the last listing; None before one finished.
_listing = None      # The running child: {"process", "started"}.
_rates = {}          # Device text -> (seconds, work) of the last finished render on it (``observe``).
_clock = time.monotonic


def attach(directory):
    global _path
    _path = None if directory is None else os.path.join(os.fspath(directory), FILE)


def _read():
    try:
        with open(_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        return saved if isinstance(saved, dict) and saved.get("version") == 1 else {}
    except (OSError, TypeError, ValueError):
        return {}


def _write(saved):
    if _path is None:
        return
    temporary = _path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(dict(saved, version=1), handle)
        os.replace(temporary, _path)
    except OSError:
        pass


def _setup_of(prefs):
    """``{"type", "ids"}``: the compute backend and the devices enabled for it, as the preferences hold them."""
    kind = prefs.compute_device_type or "NONE"
    return {"type": kind, "ids": sorted(device.id for device in prefs.devices if device.use and device.type == kind)}


def _cycles_prefs(preferences):
    """Cycles' add-on preferences, or None when Cycles is not enabled (it then renders nothing)."""
    addons = getattr(preferences, "addons", None)
    addon = addons.get("cycles") if hasattr(addons, "get") else None
    return getattr(addon, "preferences", None)


def _preferences(preferences):
    if preferences is None:
        import bpy
        preferences = bpy.context.preferences
    return preferences


def _decided(prefs):
    """The person's or Angelo's kept setup (a status), or None when Angelo should set the device up."""
    current, applied = _setup_of(prefs), _read().get("applied")
    if not prefs.is_property_set("compute_device_type"):
        return None   # Blender's own default: nobody chose a backend.
    if current != applied:
        return {"state": "person", "backend": current["type"]}   # Left as the person set it, None included.
    if current["type"] != "NONE" and current["ids"]:
        return {"state": "set_up", "backend": current["type"]}   # Angelo's, kept in saved preferences.
    return None


def setup(preferences=None, *, launch=None):
    """Make sure Cycles gets a GPU when one exists, unless someone chose their own setup. Never blocks: it starts
    or reads the listing child and returns ``status()``."""
    global _status, _seen
    preferences = _preferences(preferences)
    prefs = _cycles_prefs(preferences)
    if prefs is None:
        _status, _seen = {"state": "unavailable", "note": "Cycles is not enabled"}, None
        return status()
    current = _setup_of(prefs)
    if _listing is not None or (_seen is not None and current == _seen):
        return status()   # Listing, or nothing changed since this session's last decision.
    decided = _decided(prefs)
    if decided is not None:
        _status, _seen = decided, current
        if decided["state"] == "set_up" and _available is None:
            _start(launch, keep=decided)   # Angelo's kept setup: the listing only checks the devices still exist.
        return status()
    if _available is not None:
        _apply(preferences, prefs, _available)   # Listed earlier in this session (after a preferences reset).
        return status()
    _start(launch)
    return status()


def _start(launch=None, keep=None):
    global _listing, _status
    import bpy
    command = [bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
               "--python-expr", LISTING]
    try:
        if launch is None:
            from .child_process import popen
            launch = popen
        process = launch(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True)
    except Exception as error:
        _status = {"state": "failed", "note": "Angelo could not start listing GPU devices (%s: %s); Cycles "
                   "renders on the CPU" % (type(error).__name__, str(error)[:160])}
        return
    _listing, _status = {"process": process, "started": _clock()}, dict(keep or {"state": "listing"})
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.1, persistent=True)


def _poll(preferences=None):
    """Main-thread timer while the child lists: apply its result once it exits."""
    global _listing, _status, _seen, _available
    if _listing is None:
        return None
    process, seconds = _listing["process"], _clock() - _listing["started"]
    if process.poll() is None:
        if seconds < LIST_TIMEOUT:
            return 0.1
        process.kill()
        process.wait()
        _listing = None
        _status = {"state": "failed", "note": "Listing GPU devices took over %d s and was stopped; Cycles renders "
                   "on the CPU" % LIST_TIMEOUT}
        _report()
        return None
    output = process.stdout.read() if process.stdout is not None else ""
    _listing = None
    listed = None
    for line in (output or "").splitlines():
        if line.startswith(MARK):
            try:
                listed = {kind: [tuple(device[:3]) for device in devices]
                          for kind, devices in json.loads(line[len(MARK):]).items() if kind in ORDER}
            except (ValueError, TypeError, IndexError):
                listed = None
    if listed is None:
        _status = {"state": "failed", "note": "Listing GPU devices failed (the listing process ended with code %s); "
                   "Cycles renders on the CPU" % process.returncode}
        _report()
        return None
    _available = listed
    preferences = _preferences(preferences)
    prefs = _cycles_prefs(preferences)
    if prefs is not None:
        decided = _decided(prefs)   # The person may have chosen while it listed.
        if decided is None:
            _apply(preferences, prefs, listed, seconds=round(seconds, 2))
        else:
            _status, _seen = decided, _setup_of(prefs)
    _report()
    return None


def _apply(preferences, prefs, listed, seconds=None):
    """Add the listed devices as Cycles would and select the first backend that has one."""
    global _status, _seen
    dirty_editable = not preferences.bl_rna.properties["is_dirty"].is_readonly
    dirty, chosen = preferences.is_dirty, None
    try:
        for kind in ORDER:
            for name, device_type, identity in listed.get(kind, ()):
                if not any(entry.id == identity and entry.type == device_type for entry in prefs.devices):
                    entry = prefs.devices.add()
                    entry.id, entry.name, entry.type, entry.use = identity, name, device_type, device_type != "CPU"
        chosen = next((kind for kind in ORDER if any(device[1] == kind for device in listed.get(kind, ()))), None)
        if chosen is not None:
            prefs.compute_device_type = chosen
    finally:
        if dirty_editable:
            preferences.is_dirty = dirty   # Never saves Blender's preferences on Angelo's behalf.
    timing = {} if seconds is None else {"seconds": seconds}
    if chosen is None:
        _status = dict(timing, state="no_gpu", note="No GPU that Cycles supports was found (checked %s); Cycles "
                       "renders on the CPU" % ", ".join(NAMES[kind] for kind in ORDER))
    else:
        _status = dict(timing, state="set_up", backend=chosen)
    _seen = _setup_of(prefs)
    _write(dict(_read(), applied=_seen if chosen is not None else None))


def _report():
    if _status.get("state") in {"no_gpu", "failed"}:
        print("Angelo:", _status["note"])


def status():
    return dict(_status)


def _override():
    """The ``--cycles-device`` command-line choice (Blender's own arguments only), or None."""
    argv = sys.argv[:sys.argv.index("--")] if "--" in sys.argv else sys.argv
    for index, argument in enumerate(argv[:-1]):
        if argument == "--cycles-device":
            return argv[index + 1].split("+")[0].upper()
    return None


def _names(prefs, kind):
    """Enabled devices of ``kind`` that exist now (the last listing, else the saved entries), and the CPU's flag."""
    available = None if _available is None else {device[2] for device in _available.get(kind, ())}
    names, cpu = [], False
    for device in prefs.devices:
        if not device.use:
            continue
        if device.type == "CPU":
            cpu = True
        elif device.type == kind and (available is None or device.id in available):
            names.append(device.name)
    return names, cpu


def gpu_text(kind, names):
    return "GPU (%s: %s)" % (NAMES.get(kind, kind), ", ".join(names[:4]))


def facts(scene, preferences=None):
    """``{"device", "device_why", "gpu_available"}``: the device a Cycles render of ``scene`` uses now; None for
    other engines. Reads the preferences only."""
    render = getattr(scene, "render", None)
    if getattr(render, "engine", None) != "CYCLES":
        return None
    prefs = _cycles_prefs(_preferences(preferences))
    if prefs is None:
        return None   # Without Cycles' preferences Cycles is not enabled: no Cycles render to describe.
    override = _override()
    if override:
        return {"device": override, "device_why": "set by Angelo's --cycles-device command-line option"}
    wanted = getattr(getattr(scene, "cycles", None), "device", "CPU")
    kind = getattr(prefs, "compute_device_type", "NONE") or "NONE"
    names, cpu = _names(prefs, kind) if kind != "NONE" else ([], False)
    if wanted == "GPU" and names:
        return {"device": gpu_text(kind, names) + (" + CPU" if cpu else "")}
    entry = {"device": "CPU"}
    state = _status.get("state")
    if wanted != "GPU":
        entry["device_why"] = "the scene's Cycles device (cycles.device) is CPU"
        if names:
            entry["gpu_available"] = gpu_text(kind, names)
    elif state == "listing":
        entry["device_why"] = "the scene asks for the GPU; Angelo is still listing GPU devices (it started with the app)"
    elif state in {"no_gpu", "failed", "unavailable"}:
        entry["device_why"] = "the scene asks for the GPU; " + _status["note"]
    elif kind == "NONE":
        entry["device_why"] = "the scene asks for the GPU, but the Cycles compute device in Preferences > System is None"
    else:
        entry["device_why"] = ("the scene asks for the GPU, but no %s device is enabled in Preferences > System"
                               % NAMES.get(kind, kind))
    return entry


def work(entry):
    """A render's size for estimates: megapixels x samples x frames (x views)."""
    try:
        width, height = entry["resolution"]
        start, end = entry.get("frames") or (1, 1)
        frames = 1 if start is None or end is None else max(1, end - start + 1)
        return width * height / 1e6 * max(1, int(entry.get("samples") or 1)) * frames * max(1, entry.get("views", 1))
    except (KeyError, TypeError, ValueError):
        return None


def observe(record):
    """Remember a finished Cycles render's pace on its device (job_ui: every ended job)."""
    detail = record.get("detail") or {}
    if (record.get("status") != "done" or detail.get("engine") != "CYCLES" or not detail.get("device")
            or not record.get("started") or not record.get("ended")):
        return
    size, seconds = work(detail), record["ended"] - record["started"]
    if size and seconds > 0:
        _rates[detail["device"]] = (seconds, size)


def estimate(entry):
    """``{"estimate_s", "estimate_from"}`` for a render about to start, from the last one on its device; or {}."""
    seconds, size = _rates.get(entry.get("device"), (None, None))
    wanted = work(entry)
    if not seconds or not wanted:
        return {}
    return {"estimate_s": max(1, int(round(seconds * wanted / size))),
            "estimate_from": "the last finished render on this device in this session: %.1f s for %.3g "
                             "megapixel-samples; this one is %.3g" % (seconds, size, wanted)}


def describe(scene, entry, preferences=None):
    """Add the device facts (and an estimate when one is known) to a Cycles render's facts."""
    try:
        setup(preferences)
        found = facts(scene, preferences)
    except Exception as error:   # Facts never stop a render.
        found = {"device": "unknown", "device_why": "reading the device failed (%s)" % type(error).__name__}
    if found:
        entry.update(found)
        entry.update(estimate(entry))
    return entry


# Startup -------------------------------------------------------------------------------------------------

def _startup():
    try:
        setup()
    except Exception as error:
        print("Angelo: Cycles GPU setup failed (%s: %s); Cycles renders on the CPU" % (type(error).__name__, error))
    return None


def _prefs_read(*_args):
    import bpy
    if not bpy.app.timers.is_registered(_startup):
        bpy.app.timers.register(_startup, first_interval=0.5)


def register(directory):
    """Attach the state folder; set up a second after startup and after Load Factory Preferences."""
    import bpy
    attach(directory)
    bpy.app.timers.register(_startup, first_interval=1.0)
    if _prefs_read not in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.append(_prefs_read)


def unregister():
    global _listing
    import bpy
    for timer in (_startup, _poll):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    if _prefs_read in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.remove(_prefs_read)
    if _listing is not None:
        try:
            _listing["process"].kill()
            _listing["process"].wait(5)
        except Exception:
            pass
        _listing = None
    attach(None)
