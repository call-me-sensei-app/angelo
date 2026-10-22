# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cycles renders on the GPU by default, and the agent is told which device a render uses.

Contract: doc/angelo/long_jobs.rst, "Render device".

Stock Blender never lists the Cycles compute devices at startup (``CyclesPreferences.refresh_devices``: some
drivers crash while listing). Until something lists them (opening Preferences > System, or drawing Cycles'
Render panel) the device list is empty, and a scene whose Cycles device is GPU renders on the CPU without a
word (``intern/cycles/blender/device.cpp``, ``blender_device_info``: no enabled device matches).

``setup`` never lists devices in the app. Listing initializes GPU drivers, holds the interpreter lock and can
end the process, so it runs in a short child process (this app with ``--background --factory-startup``); a
driver that crashes ends only that child. When the child reports, the main thread adds Cycles' device entries
as ``CyclesPreferences.update_device_entries`` would (every GPU enabled, the CPU not) and selects the first
backend that has a device: OptiX, then CUDA (NVIDIA), HIP (AMD), Metal (Apple), oneAPI (Intel). The listing is
saved for this build (``render-device.json``), so every later start, background runs included, applies it at
once, before anything renders; a windowed start lists again once to refresh it. ``session`` makes every
Cycles session wait for this decision.

This package is outside the agent add-on on purpose: Cycles imports it, and Cycles must never import the
agent package (its start-up cost and import order belong to the agent). It imports nothing of the agent;
``angelo_agent.render_device`` (and ``render_device_facts``, ``render_device_session``) are this package's
modules under the agent's names.

The person's choice wins: a compute backend someone set (``is_property_set``: in Preferences > System, or
brought in with Import Blender Settings), None included, is left alone unless it is the one Angelo applied;
so is Cycles' ``--cycles-device`` command-line option. Nothing is saved to Blender's preferences on Angelo's
behalf (their dirty state is restored); what Angelo applied is ``render-device.json`` in the state folder,
which tells its own setup from the person's later.

``facts`` states, for a scene, the device a Cycles render will use, and why when that is the CPU; ``shown``
is the same for the person's job card. Render jobs record it when they start, and ``estimate`` turns the last
finished render on the same device into a duration estimate. Main thread only, except ``facts``/``shown``
(they only read).
"""

import json
import os
import subprocess
import sys
import tempfile
import time

FILE = "render-device.json"
ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")   # The first backend with a device is used.
NAMES = {"OPTIX": "OptiX", "CUDA": "CUDA", "HIP": "HIP", "METAL": "Metal", "ONEAPI": "oneAPI"}
MARK = "ANGELO_CYCLES_DEVICES "
LIST_TIMEOUT = 120.0   # Seconds a listing child may take before it is ended and Cycles stays on the CPU.
CHILD = "ANGELO_GPU_LISTING_CHILD"   # In the listing child's environment: it never sets devices up itself.
SAVED_DAYS = 7         # A saved listing older than this is listed again (a background run has no other refresh);
NO_GPU_DAYS = 1        # one that found no GPU, after a day (a GPU added or plugged back in shows up in background runs).
FAILED_HOURS = 24      # After a listing that crashed or hung, later starts skip it this long (CPU, with the reason).
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
_tried = False       # A listing child was started in this session: at most one per launch.
_listeners = []      # Called (no arguments) after Angelo changed the compute device (session).
_clock = time.monotonic


def attach(directory):
    global _path
    _path = None if directory is None else os.path.join(os.fspath(directory), FILE)


_cache = None        # (path, mtime_ns, size, content) of the last read: one decision reads the file several times.


def _read():
    global _cache
    try:
        found = os.stat(_path)
        key = (_path, found.st_mtime_ns, found.st_size)
        if _cache is None or _cache[:3] != key:
            with open(_path, encoding="utf-8") as handle:
                _cache = key + (handle.read(),)
        saved = json.loads(_cache[3])   # A fresh object each time: callers change what they read.
        return saved if isinstance(saved, dict) and saved.get("version") == 1 else {}
    except (OSError, TypeError, ValueError):
        return {}


def _write(saved):
    if _path is None or dict(saved, version=1) == _read():
        return   # Unchanged: background runs that only read it never write it.
    temporary = None
    try:   # Its own temporary name: several processes (parallel background renders) may write at once.
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=FILE + ".", suffix=".tmp", dir=os.path.dirname(_path))
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(dict(saved, version=1), stream)
        os.replace(temporary, _path)
    except OSError:
        if temporary is not None:
            try:
                os.remove(temporary)
            except OSError:
                pass


def _build():
    """What a saved listing is valid for: this app and build (another build may offer other backends)."""
    import bpy
    app = bpy.app
    build = getattr(app, "build_hash", b"")
    build = build.decode("ascii", "replace") if isinstance(build, bytes) else str(build)
    # The resolved path: binary_path is as the app was started (relative from a shell, a symlink to the app).
    return "|".join((os.path.realpath(getattr(app, "binary_path", "")), getattr(app, "version_string", ""), build))


def _parsed(devices):
    """``{backend: [(name, type, id), ...]}`` from JSON (the child's line or the saved file); ValueError if malformed."""
    if not isinstance(devices, dict):
        raise ValueError("not a mapping")
    try:
        return {kind: [tuple(str(part) for part in device[:3]) for device in found]
                for kind, found in devices.items() if kind in ORDER and isinstance(found, list)}
    except (TypeError, IndexError) as error:
        raise ValueError(str(error)) from None


def _saved(key, seconds):
    """This build's saved ``key`` entry when younger than ``seconds``, else None."""
    saved = _read().get(key)
    if not isinstance(saved, dict) or saved.get("build") != _build():
        return None
    at = saved.get("at")
    return saved if isinstance(at, (int, float)) and 0 <= time.time() - at < seconds else None


def _saved_listing():
    """The last listing this build made on this machine (within ``SAVED_DAYS``), or None."""
    saved = _saved("listed", SAVED_DAYS * 86400)
    try:
        listed = None if saved is None else _parsed(saved.get("devices"))
    except ValueError:
        return None
    gpu = listed is not None and any(device[1] != "CPU" for devices in listed.values() for device in devices)
    if listed is not None and not gpu and time.time() - saved["at"] >= NO_GPU_DAYS * 86400:
        return None
    return listed


def relist(launch=None):
    """The saved listing no longer matches this machine (``stale``): forget it and list as on a first start (a
    render waits for it). Once per launch, like every listing."""
    global _seen
    saved = _read()
    if saved.get("listed") is not None:
        _write(dict(saved, listed=None))
    _seen = None
    if not _tried:
        _start(launch)


def stale(prefs, scene):
    """Background runs only (never on the interface's thread: it initializes the GPU driver, as Cycles is about
    to): True when the scene asks for the GPU and none of the devices Angelo set up (from the saved listing, or
    kept in saved preferences) exists. Not after this process listed (that listing is current)."""
    if _status.get("state") != "set_up" or _available is not None:
        return False
    if getattr(getattr(scene, "cycles", None), "device", "CPU") != "GPU":
        return False
    import _cycles
    kind, ids = _setup_of(prefs)["type"], set(_setup_of(prefs)["ids"])
    return not ids & {device[2] for device in _cycles.available_devices(kind)}


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


def setup(preferences=None, *, launch=None, listing=True):
    """Make sure Cycles gets a GPU when one exists, unless someone chose their own setup. Never blocks: it decides
    from what is known (this session's listing, else this build's saved one), starts the listing child when
    ``listing`` and none ran in this session, and returns ``status()``."""
    global _status, _seen
    preferences = _preferences(preferences)
    prefs = _cycles_prefs(preferences)
    if prefs is None:
        _status, _seen = {"state": "unavailable", "note": "Cycles is not enabled"}, None
        return status()
    if _override():
        _status, _seen = {"state": "override"}, None   # Cycles' --cycles-device: the person's, nothing to list.
        return status()
    current = _setup_of(prefs)
    if _seen is None or current != _seen:
        decided = _decided(prefs)
        if decided is not None:
            _status, _seen = decided, current
        else:
            known = _available if _available is not None else _saved_listing()
            failed = None if known is not None or _tried else _saved("failed", FAILED_HOURS * 3600)
            failed = failed if failed is not None and failed.get("slow", 2) >= 2 else None   # One slow run: list again.
            if known is not None:
                _apply(preferences, prefs, known, saved=_available is None)
            elif failed is not None:   # A crashed or hung listing recently: no render waits for another.
                _status, _seen = {"state": "failed", "from": "saved", "note": str(failed.get("note"))}, current
    if listing and not _tried and _status.get("state") not in {"person", "override"}:
        _start(launch, keep=None if _status.get("state") in {"not_set_up", "listing"} else _status)
    return status()


def _popen(command, **kwargs):
    """The listing child: UTF-8 text, and on Windows no console window (it is this app in background mode)."""
    kwargs.update(encoding="utf-8", errors="replace")
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000   # CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)


def _start(launch=None, keep=None):
    global _listing, _status, _tried
    import bpy
    _tried = True
    command = [os.path.realpath(bpy.app.binary_path), "--background", "--factory-startup", "--disable-autoexec",
               "--python-expr", LISTING]
    try:
        if launch is None:
            launch = _popen
        process = launch(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, env=dict(os.environ, **{CHILD: "1"}))
    except Exception as error:
        _failed("Angelo could not start listing GPU devices (%s: %s)" % (type(error).__name__, str(error)[:160]))
        return
    _listing, _status = {"process": process, "started": _clock()}, dict(keep or {"state": "listing"})
    if not bpy.app.timers.is_registered(_poll):   # Background runs have no timers: they call _poll themselves.
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
        stop("Listing GPU devices took over %d s and was stopped" % LIST_TIMEOUT, remember=True)
        return None
    if "listed" not in _listing:
        output = process.stdout.read() if process.stdout is not None else ""
        listed = None
        for line in (output or "").splitlines():
            if line.startswith(MARK):
                try:
                    listed = _parsed(json.loads(line[len(MARK):]))
                except ValueError:
                    listed = None
        if listed is None:
            _listing = None
            _failed("Listing GPU devices failed (the listing process ended with code %s)" % process.returncode)
            return None
        _listing["listed"] = _available = listed   # Facts read it at once, also while the writes below wait.
        _write(dict(_read(), failed=None, listed={"build": _build(), "at": time.time(),
                                                  "devices": {kind: [list(device) for device in devices]
                                                              for kind, devices in listed.items()}}))
    listed = _listing["listed"]
    if _status.get("state") != "listing" and _busy():
        return 0.5   # A refresh: a running render or bake reads these preferences on its own thread; wait for it.
    preferences = _preferences(preferences)
    prefs = _cycles_prefs(preferences)
    if prefs is not None and not _override():
        decided = _decided(prefs)   # The person may have chosen while it listed.
        if decided is None or decided["state"] == "set_up":   # Angelo's own setup: refreshed to what exists now.
            _apply(preferences, prefs, listed, seconds=round(seconds, 2))
        else:
            _status, _seen = decided, _setup_of(prefs)
    _listing = None   # Only now: a render job's thread waits for this (session), never a half-apply.
    _report()
    return None


def _busy():
    import bpy
    running = getattr(bpy.app, "is_job_running", None)
    return running is not None and any(running(kind) for kind in ("RENDER", "RENDER_PREVIEW", "OBJECT_BAKE"))


def stop(note, remember=False):
    """End a running listing child: Cycles keeps what is set up, else the CPU. ``remember``: True for a hang (over
    ``LIST_TIMEOUT``), saved like a crash; "slow" for a background render's own shorter wait (saved as a failure on
    the second in a row, so a hanging driver costs two renders, a slow one listing once more none); False for a
    cancel."""
    global _listing
    if _listing is None:
        return
    try:
        _listing["process"].kill()
        _listing["process"].wait(5)
    except Exception:
        pass
    _listing = None
    _failed(note, remember)


def _failed(note, remember=True):
    """A listing that did not report. A setup already in place (saved, or the person's) stays; else the CPU. Saved,
    so later starts skip listing for ``FAILED_HOURS`` (a driver that crashes or hangs would cost every render)."""
    global _status
    if _status.get("state") in {"set_up", "person"}:
        print("Angelo: %s; Cycles keeps the device set up earlier" % note)
        return
    _status = {"state": "failed", "note": note + "; Cycles renders on the CPU"}
    if remember:
        failed = {"build": _build(), "at": time.time(), "note": _status["note"]}
        if remember == "slow":
            earlier = _saved("failed", FAILED_HOURS * 3600)
            failed["slow"] = (earlier or {}).get("slow", 0) + 1 if earlier is None or "slow" in earlier else 2
        _write(dict(_read(), failed=failed))
    _report()


def _apply(preferences, prefs, listed, seconds=None, saved=False):
    """Add the listed devices as Cycles would and select the first backend that has one. ``saved``: the listing is
    this build's saved one (a later start), not this session's."""
    global _status, _seen
    dirty_editable = not preferences.bl_rna.properties["is_dirty"].is_readonly
    dirty, chosen = preferences.is_dirty, None
    before, applied = _setup_of(prefs), _read().get("applied")
    owned = prefs.is_property_set("compute_device_type") and before == applied
    try:
        for kind in ORDER:
            for name, device_type, identity in listed.get(kind, ()):
                if not any(entry.id == identity and entry.type == device_type for entry in prefs.devices):
                    entry = prefs.devices.add()
                    entry.id, entry.name, entry.type, entry.use = identity, name, device_type, device_type != "CPU"
        chosen = next((kind for kind in ORDER if any(device[1] == kind for device in listed.get(kind, ()))), None)
        # A GPU that is gone keeps its entry and backend: Cycles then renders on the CPU (no_gpu says so), and a
        # GPU that comes back is used again. Never set to None: a None someone sets stays theirs.
        if chosen is not None and prefs.compute_device_type != chosen:
            prefs.compute_device_type = chosen
    finally:
        if dirty_editable:
            preferences.is_dirty = dirty   # Never saves Blender's preferences on Angelo's behalf.
    timing = {} if seconds is None else {"seconds": seconds}
    if saved:
        timing["from"] = "saved"
    if chosen is None:
        _status = dict(timing, state="no_gpu", note="No GPU that Cycles supports was found (checked %s); Cycles "
                       "renders on the CPU" % ", ".join(NAMES[kind] for kind in ORDER))
    else:
        _status = dict(timing, state="set_up", backend=chosen)
    _seen = _setup_of(prefs)
    _write(dict(_read(), applied=_seen if chosen is not None else (applied if owned else None)))
    for listener in list(_listeners) if _seen != before else ():
        try:
            listener()
        except Exception as error:
            print("Angelo: after the Cycles device changed (%s: %s)" % (type(error).__name__, error))


def _report():
    if _status.get("state") in {"no_gpu", "failed"}:
        print("Angelo:", _status["note"])


def status():
    return dict(_status)


# Lifecycle: ``session`` (Cycles' add-on registers it).


def reset():
    """Preferences were read again (Load Factory Preferences): decide again from what is known."""
    global _seen
    _seen = None


def close():
    """End a running listing child and forget the state folder (Cycles' add-on unregistered, or registered again
    when preferences are read again: an ended listing may then run again)."""
    global _listing, _tried
    if _listing is not None:
        try:
            _listing["process"].kill()
            _listing["process"].wait(5)
        except Exception:
            pass
        _listing, _tried = None, False
    attach(None)


# What a render uses and why (facts), here for every caller: facts, shown, describe, observe, estimate.
from .facts import (_override, _rates, corrected, describe, estimate, facts, gpu_text,  # noqa: E402
                                  observe, shown, work)
