# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio in Blender's Asset Browser: the library's registration and its background writer.

Startup does one thing: registering the add-on compares the asset libraries in Preferences with Angelo's folder
(``studio_assets.asset_directory``) and adds the entry *Angelo Media Studio* when it is missing and the person has
not removed it, before any asset shelf draws (adding a library while a shelf loads previews crashed Blender). Nothing
is scanned or written on the startup path.

Then, every ``TICK`` seconds on the main thread, two directory stats decide whether the Studio library changed (a
record is replaced atomically, so its folder's time changes) or the interface language did. Only then a worker
thread reads the records, plans the files (``studio_assets.plan``) and runs ``studio_assets_convert`` in a
background Blender at lowered priority, ``BATCH`` items at a time. When files changed, open Asset Browsers refresh.

The entry is the person's once added: renamed, it is still found by its folder; removed in Preferences, the removal
is recorded and it is not added again, and nothing more is written. Adding the folder again resumes. A preference
read (factory preferences, Revert, a settings import, a template) is not a removal: the entry comes back.
"""

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from . import private_roots
from . import live_windows, pump_guard, studio_assets
from .ui_text import iface_, language

TICK = 5.0
FIRST = 2.0          # The registration check: after the window is up, never during add-on registration.
SYNC_DELAY = 20.0    # The first scan: well after startup work, so it never competes with it.
RESCAN = 600.0       # A full scan now and then, for changes that did not touch a folder's time.
REFRESH_TRIES = 24   # Ticks a refresh waits for a drag to end (two minutes).

_state = {"registered": None, "since": 0.0, "session": uuid.uuid4().hex, "worker": None, "changed": False,
          "reread": False, "active": False, "names": {}, "binary": "", "busy": False, "waits": 0, "previews": True}
_stop = threading.Event()   # Replaced at each registration: a worker of an earlier one never runs on.
_process = None


def directories():
    from .studio_state import library_directory
    library = library_directory()
    return library, studio_assets.asset_directory(library)


def _libraries():
    return bpy.context.preferences.filepaths.asset_libraries


def ensure_registered():
    """Add the entry if it is missing and was not removed; return whether the library is active."""
    _library, directory = directories()
    libraries = _libraries()
    record = studio_assets.read_registration(directory)
    decision = studio_assets.registration_decision(libraries, directory, record)
    if decision == "removed":
        return False
    if decision == "present" and record.get("removed"):
        # The entry is back (a crash or a quit after Turn off before the preferences were saved, or another Angelo
        # saved them): it is on, so the writer writes again, never "On" but idle.
        studio_assets.write_registration(directory, removed=False)
    if decision != "present":
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = libraries.new(name=studio_assets.LIBRARY_NAME, directory=str(directory))
        try:
            # Append, not 5.2's default Pack (a link to a packed copy of the asset file): an appended material is
            # local, so its picture is packed into the person's file once (pack_landed_images) and the file never
            # depends on Studio's folder. Set only here, when Angelo adds the entry; a later choice is the person's.
            entry.import_method = 'APPEND'
        except (TypeError, ValueError, AttributeError):
            pass
        studio_assets.write_registration(directory, removed=False)
    return True


def observe():
    """Follow the person's edits in Preferences: a removal is recorded, a re-added folder resumes the library.

    Path text only (``studio_assets.same_directory``): this runs on the main thread, and a library on an unreachable
    network share must never stall it.
    """
    _library, directory = directories()
    present = studio_assets.find_library(_libraries(), directory) is not None
    reread, _state["reread"] = _state["reread"], False
    if _state["registered"] and not present:
        if reread:
            return ensure_registered()
        studio_assets.write_registration(directory, removed=True)
        # The person turned it off in Preferences: ask there whether to free its space too (Turn off in Media
        # Studio asks in its own dialog, and sets "registered" first, so it never comes here).
        from . import studio_library_ops
        bpy.app.timers.register(studio_library_ops.ask_free, first_interval=0.2)
    elif present and not _state["registered"]:
        studio_assets.write_registration(directory, removed=False)
    return present


def turn_off():
    """Media Studio's Turn off: as removing the entry in Preferences; a running batch stops within a quarter second."""
    from . import studio_assets_space
    _library, directory = directories()
    _state["registered"] = _state["active"] = False
    studio_assets_space.turn_off(_libraries(), directory)


def turn_on():
    """Media Studio's Turn on: as adding the folder again in Preferences; the writer resumes at the next tick."""
    _library, directory = directories()
    studio_assets.write_registration(directory, removed=False)
    _state["registered"] = ensure_registered()
    _state["since"] = time.monotonic() - SYNC_DELAY


def redraw_studio():
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        area.tag_redraw()


def _signature(library):
    def stamp(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None
    return stamp(library / "assets"), stamp(library / "folders")


BUSY_JOBS = ("RENDER", "OBJECT_BAKE", "COMPOSITE")
STOPPED = "the writer stopped on this item"   # The start of an item's error when the writer ended on it.
# The start of a written item's "preview" when the writer ended rendering it: Blender's OpenGL backend always counts
# as supported, so a background render on a machine with no usable OpenGL driver ends the process instead of failing.
PREVIEW_STOPPED = "the writer stopped rendering this item's preview"
RENDERING = "rendering"   # studio_assets_convert.RENDERING: the result's "preview" while its render runs.
# Where an image asset lands in the person's file: its picture packed (studio_assets_landing).
from .studio_assets_landing import (STUDIO_IMAGE, STUDIO_PACKED, _before_save, _imported,  # noqa: E402
                                    add_landing_handlers, pack_landed_images, remove_landing_handlers)


def host_busy():
    """Whether the person's own work is running: a render or bake, an Angelo background job, or an agent turn.

    The writer never competes with them: it starts no batch while this holds and stops a running one (main thread).
    """
    for kind in BUSY_JOBS:
        try:
            if bpy.app.is_job_running(kind):
                return True
        except (TypeError, ValueError):
            continue
    from . import job_ui, ui
    jobs = job_ui.manager(create=False)
    if jobs is not None and jobs.running:
        return True
    return any(getattr(controller, "busy", False) for controller in list(ui._controllers.values()))


def _catalog_names():
    return {kind: iface_(name) for kind, (_identity, name, _folder) in studio_assets.CATALOGS.items()}


@pump_guard.keep_alive(TICK)
def _tick():
    """Main thread: Preferences, the interface language and the refresh; every file access is the worker's."""
    if _state["registered"] is None:
        _state["registered"] = ensure_registered()
        _state["since"] = time.monotonic()
        return TICK
    _state["registered"] = observe()
    if _state["changed"]:
        # Not now (a drag holds the lists): the next tick. A shelf whose list never loaded refuses too, so the
        # wait is bounded (REFRESH_TRIES ticks, far longer than a drag); its list is read fresh when it first shows.
        waiting = not refresh_asset_browsers() and _state["waits"] < REFRESH_TRIES
        _state["changed"], _state["waits"] = waiting, _state["waits"] + 1 if waiting else 0
    _state["names"], _state["binary"], _state["busy"] = _catalog_names(), bpy.app.binary_path, host_busy()
    _state["active"] = bool(_state["registered"]) and time.monotonic() - _state["since"] >= SYNC_DELAY
    if _state["active"] and not _state.get("prefetched"):
        _state["prefetched"] = True
        _prefetch()
    if _state["active"] and not _worker_running():
        worker = threading.Thread(target=_watch, name="Angelo asset library", args=(_stop,), daemon=True)
        _state["worker"] = worker
        worker.start()
    return TICK


def _prefetch():
    """Load the library's asset list in the background once, so Media Studio's first drag finds it ready."""
    window = live_windows.first(bpy.context.window_manager)
    if window is None:
        return
    try:
        from . import studio_drag
        with bpy.context.temp_override(window=window):
            studio_drag.prefetch()
    except Exception as error:  # noqa: BLE001 - only a head start; the first drag loads the list itself.
        pump_guard.report("studio_assets_host.prefetch", error)


def _worker_running():
    return _state["worker"] is not None and _state["worker"].is_alive()


def _watch(stop):
    """Worker: every ``TICK`` two folder stats; a sync when the library or the language changed, or after ``RESCAN``."""
    seen, scanned = None, 0.0
    while not stop.is_set():
        if _state["active"] and _state["busy"]:
            seen = None   # Scan again once the person's render or turn is over.
        elif _state["active"]:
            try:
                library, directory = directories()
                names = dict(_state["names"])
                signature = (_signature(library), tuple(sorted(names.items())))
                if signature != seen or time.monotonic() - scanned >= RESCAN:
                    seen, scanned = signature, time.monotonic()
                    if sync(library, directory, names, _state["binary"], _state["session"], stop,
                            yield_to=lambda: _state["busy"] or not _state["active"]):   # Off: stop at once.
                        _state["changed"] = True
                    if _state["busy"]:
                        seen = None   # It stepped aside part-way: finish after the person's work.
            except Exception as error:  # noqa: BLE001 - reported once; the next change tries again.
                pump_guard.report("studio_assets_host.sync", error)
        stop.wait(TICK)


def sync(library, directory, names, binary, session, stop, run=None, yield_to=lambda: False):
    """Bring the asset folder up to date with the Studio library; True when any file was written or removed."""
    if studio_assets.read_registration(directory).get("removed"):
        return False   # Removed in Preferences (here or in another Angelo): nothing more is written.
    with studio_assets.sync_lock(directory) as held:
        if not held:
            return False   # Another Angelo is writing this folder; the next change or rescan tries again.
        if halted(directory):
            return False   # Turned off or freed between the check above and holding the folder.
        return _sync_locked(library, directory, names, binary, session, stop, run, yield_to)


def halted(directory):
    """Whether the library was turned off (here or in another Angelo): nothing more is written. Read under the lock and
    between batches, so a Turn off from another Angelo stops this writer before its next batch."""
    return bool(studio_assets.read_registration(directory).get("removed"))


def _sync_locked(library, directory, names, binary, session, stop, run, yield_to):
    from .studio_state import StudioLibrary
    changed = studio_assets.write_catalogs(directory, names)
    studio_assets.remove_stale_temporaries(library)
    # The manifest before the records: an item in the manifest had its record written first, so it is never taken
    # for a removed one (another Angelo may add both between two reads).
    manifest = studio_assets.read_manifest(directory)
    studio = StudioLibrary(library)
    convert, remove = studio_assets.plan(library, studio.assets.values(), studio.folders, manifest, session)
    if remove:
        changed = studio_assets.remove_orphans(library, manifest, remove) or changed
        studio_assets.write_manifest(directory, manifest)
    pending, empty = list(convert), 0
    while pending and not stop.is_set() and not yield_to() and empty < 2 and not halted(directory):
        if not _state["previews"]:   # A model already written gets no second try while renders are off.
            pending = [item for item in pending if not item.get("preview_only")]
            if not pending:
                break
        batch, pending = pending[:studio_assets.BATCH], pending[studio_assets.BATCH:]
        results = (run or convert_batch)(binary, batch, stop, yield_to)
        if stop.is_set() or yield_to():
            # Stopped, or stepping aside for the person's render or turn: what finished counts, the rest waits.
            batch = [item for item in batch if item["id"] in results]
            studio_assets.apply_results(manifest, batch, results, session)
            studio_assets.write_manifest(directory, manifest)
            return changed or any(r.get("ok") for r in results.values())
        # Twice a writer that wrote nothing and stopped: it cannot run here; wait for a change.
        stalled = not any(r.get("ok") for r in results.values()) and any(
            str(r.get("error", "")).startswith(STOPPED) for r in results.values()) and len(results) <= 1
        empty = 0 if results and not stalled else empty + 1
        missing = [item for item in batch if item["id"] not in results]
        if missing:
            # The writer ended early (a crash, the time limit) on an item: convert_batch gives it the reason (else it
            # is the first without a result); the items after it never ran and go to a fresh process.
            named = any(str(r.get("error", "")).startswith(STOPPED) or str(r.get("preview", "")).startswith(
                PREVIEW_STOPPED) for r in results.values())
            if not named:
                results[missing[0]["id"]] = {"ok": False, "error": STOPPED}
                missing = missing[1:]
            pending = missing + pending
            batch = [item for item in batch if item["id"] in results]
        studio_assets.apply_results(manifest, batch, results, session)
        studio_assets.write_manifest(directory, manifest)
        changed = changed or any(r.get("ok") for r in results.values())
    return changed


def convert_batch(binary, batch, stop, yield_to=lambda: False):
    """Run the converter in a background Blender with lowered priority; its per-item results (partial if stopped).

    Every writer goes through here (the background sync, a drag's write at once), so each honours the session's
    preview switch: once a preview render ended a writer (no usable GPU context), models are written without one.
    """
    global _process
    import json
    if not _state["previews"]:
        batch = [dict(item, render=False) for item in batch]
    script = Path(__file__).with_name("studio_assets_convert.py")
    with private_roots.TemporaryDirectory("angelo-assets-") as scratch:
        batch_path, results_path = Path(scratch) / "batch.json", Path(scratch) / "results.json"
        batch_path.write_text(json.dumps(batch), encoding="utf-8")
        command = [binary, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
                   "--", str(batch_path), str(results_path)]
        log_path = Path(scratch) / "writer.log"
        log = open(log_path, "wb")   # The child's own output: why an item it stopped on failed.
        options = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "cwd": scratch}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        process = _process = subprocess.Popen(command, **options)
        if sys.platform != "win32":
            try:
                os.setpriority(os.PRIO_PROCESS, process.pid, 10)
            except OSError:
                pass
        deadline = time.monotonic() + 120 + 30 * len(batch)
        interrupted = False
        while process.poll() is None:
            if stop.is_set() or yield_to() or time.monotonic() > deadline:
                interrupted = stop.is_set() or yield_to()   # Ours to stop: no item failed.
                process.kill()
                process.wait()
                break
            stop.wait(.25)
        _process = None
        log.close()
        from .studio_state import read_json
        results = read_json(results_path, {})
        results = results if isinstance(results, dict) else {}
        rendering = [key for key, result in results.items()
                     if isinstance(result, dict) and result.get("preview") == RENDERING]
        for key in rendering:
            # Written and reported before its preview render, so the render is what ended (or was stopped in) the
            # process: the item stands without a preview, and no item after it is blamed.
            if interrupted:   # Stopped for the person's work: a later session tries the preview again.
                results[key]["preview"] = "stopped before its preview was rendered"
            else:
                results[key]["preview"] = _stopped_reason(process.returncode, log_path, PREVIEW_STOPPED)
                _state["previews"] = False   # The rest of this session writes models without a render.
        ended = not interrupted and (process.returncode not in (0, None) or len(results) < len(batch))
        if ended and not rendering:
            stopped = next((item["id"] for item in batch if item["id"] not in results), None)
            if stopped is not None:   # The item the writer ended on gets the reason: its exit code and last lines.
                results[stopped] = {"ok": False, "error": _stopped_reason(process.returncode, log_path)}
        if ended:
            _print_log(process.returncode, log_path)
        return results


def _log_tail(log_path, length):
    try:
        return log_path.read_bytes()[-length:].decode("utf-8", "replace")
    except OSError:
        return ""


def _stopped_reason(code, log_path, start=STOPPED):
    lines = [line.strip() for line in _log_tail(log_path, 1500).splitlines() if line.strip() and "INFO" not in line]
    return f"{start} (exit code {code}): " + " | ".join(lines[-4:])


def _print_log(code, log_path):
    """The console gets the end of a writer that ended early (a crash's backtrace and file name are in it): its
    folder is temporary, and this is the one place the cause is kept."""
    print(f"Angelo asset library: the writer ended early (exit code {code}); its last output:\n"
          + _log_tail(log_path, 6000).rstrip(), file=sys.stderr, flush=True)


def refresh_asset_browsers():
    """Re-read the library where it is shown, so new files appear without a Refresh: every Asset Browser on it (or
    All), and every Asset Shelf on it (or All), once per library reference (shelves share their asset lists).

    Blender re-reads an on-disk library only when asked. A browser or shelf on another library is left alone:
    re-reading a large library of the person's costs them seconds. Returns False when a refresh has to wait: the
    operator refuses while a drag is in progress (a dragged asset points into the lists a refresh frees).
    """
    _library, directory = directories()
    entry = studio_assets.find_library(_libraries(), directory)
    if entry is None:
        return True
    shown, shelves, done = {entry.name, 'ALL'}, set(), True
    for window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'FILE_BROWSER' and area.ui_type == 'ASSETS':
            params = getattr(area.spaces.active, "params", None)
            if getattr(params, "asset_library_reference", None) not in shown:
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        else:
            region = next((r for r in area.regions if r.type == 'ASSET_SHELF'), None)
            if region is None:
                continue
        with bpy.context.temp_override(window=window, area=area, region=region):
            if region.type == 'ASSET_SHELF':   # The shelf region gives the context its shelf and library.
                library = getattr(getattr(bpy.context, "asset_shelf", None), "asset_library_reference", None)
                if library not in shown or library in shelves:
                    continue
            if not bpy.ops.asset.library_refresh.poll():
                done = False
                continue
            bpy.ops.asset.library_refresh()
            if region.type == 'ASSET_SHELF':
                shelves.add(library)
    return done


@persistent
def preferences_read(*_args):
    _state["reread"] = True   # A read replaced every library: bring Angelo's back, it was not "removed".


def register():
    if bpy.app.background:
        return   # A background Blender (a test, a render, this library's own writer) lists nothing.
    global _stop
    _stop = threading.Event()
    _state.update(registered=None, worker=None, changed=False, reread=False, active=False)
    try:
        # Now, before any Asset Shelf or Browser draws: adding a library rebuilds Blender's asset lists, and doing that
        # while a shelf's preview job runs crashed Blender (PreviewLoadJob, part D's isolation runs). A path check.
        _state["registered"], _state["since"] = ensure_registered(), time.monotonic()
    except Exception as error:  # noqa: BLE001 - the timer tries again; registering the add-on never fails on it.
        pump_guard.report("studio_assets_host.register", error)
    add_landing_handlers()
    from . import prefs_reload
    if preferences_read not in prefs_reload.READ_LISTENERS:
        prefs_reload.READ_LISTENERS.append(preferences_read)
    if preferences_read not in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.append(preferences_read)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=FIRST, persistent=True)


def unregister():
    _stop.set()
    remove_landing_handlers()
    process = _process
    if process is not None and process.poll() is None:
        process.kill()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    from . import prefs_reload
    if preferences_read in prefs_reload.READ_LISTENERS:
        prefs_reload.READ_LISTENERS.remove(preferences_read)
    if preferences_read in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.remove(preferences_read)
