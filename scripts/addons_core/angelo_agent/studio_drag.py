# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's grid: select items and drag them, as the Asset Browser drags assets.

A press on a ready item waits: moving past Blender's drag threshold starts Blender's own asset drag of the item and
every selected item (``ASSET_OT_angelo_drag``), so every drop behaves as from the Asset Browser or a shelf; a release
without moving opens the item, as a click always did. Shift or Ctrl/Cmd+click adds the item to the selection or takes
it out. An item not yet in the Asset Browser library is written at once first (it is local and small), never shown
as a box; with the library turned off, the drag carries Media Studio's own files, so a drop does what dropping those
files does.
"""

import threading
import time
from pathlib import Path

import bpy

from . import studio_assets

WRITE_WAIT = 1.0   # Seconds a drag waits for this Angelo's writer to step aside (it stops within a quarter second);
                  # another Angelo writing the folder is never waited for: the drag carries the files instead.


def available():
    """Whether this Angelo can start Blender's asset drag (its native operator); else a press stays a click."""
    return "angelo_drag" in dir(bpy.ops.asset)


def press(state, identity, rect, area, event, x, y):
    """A press on a ready item: nothing happens until it moves (a drag) or is released (a click)."""
    forget_source()
    state["asset_press"] = {"id": identity, "rect": rect, "area": area.as_pointer(), "x": x, "y": y,
                            "extend": bool(event.shift or event.ctrl or event.oskey)}


def moved(context, window, area, region, state, x, y):
    """Start the drag once the pointer moved past Blender's drag threshold. True when it started."""
    pressed = state.get("asset_press")
    if not pressed:
        return False
    threshold = context.preferences.inputs.drag_threshold_mouse * context.preferences.system.ui_scale
    if max(abs(x - pressed["x"]), abs(y - pressed["y"])) < threshold:
        return False
    state.pop("asset_press", None)
    shown = set(state.get("visible_assets") or ())   # Never items of another tab or folder the person can't see.
    selected = [i for i in state.get("selected_ids") or [] if i in shown]
    if pressed["id"] not in selected:
        selected = [pressed["id"]]
    identities = [pressed["id"]] + [i for i in selected if i != pressed["id"]]   # The pressed item first.
    state["selected_ids"] = selected
    with context.temp_override(window=window, area=area, region=region):
        start(bpy.context, identities)
    return True


def forget(views):
    """Drop every waiting press (the window lost focus, or an overlay took the release): never a drag later."""
    for state in views:
        state.pop("asset_press", None)


def released(state):
    """A release that never moved: ``("toggle" | "open", id, rect, area pointer)``, or None."""
    pressed = state.pop("asset_press", None)
    if not pressed:
        return None
    selected = list(state.get("selected_ids") or [])
    if pressed["extend"]:
        if pressed["id"] in selected:
            selected.remove(pressed["id"])
        else:
            selected.append(pressed["id"])
        state["selected_ids"] = selected
        return "toggle", pressed["id"], pressed["rect"], pressed["area"]
    state["selected_ids"] = [pressed["id"]]
    return "open", pressed["id"], pressed["rect"], pressed["area"]


def _library():
    from . import studio_assets_host as host
    library_dir, directory = host.directories()
    return library_dir, directory, studio_assets.find_library(host._libraries(), directory)


def ensure_written(identities):
    """Whether every item is in the Asset Browser library now, writing the missing or stale ones at once (the
    background writer steps aside within a quarter second). False with the library turned off."""
    from . import studio_assets_host as host
    from .studio_state import StudioLibrary
    library_dir, directory, entry = _library()
    if entry is None:
        return False
    studio = StudioLibrary(library_dir)
    manifest = studio_assets.read_manifest(directory)
    wanted = []
    for identity in identities:
        record = studio.assets.get(identity)
        found = studio_assets.item(library_dir, record, studio.folders) if record else None
        if found is None:
            return False
        current = manifest.get(identity) or {}
        if current.get("fingerprint") == found["fingerprint"] and current.get("failed"):
            return False   # It failed to write as it is: drag its files, never retry on every drag.
        if current.get("fingerprint") != found["fingerprint"] or not Path(found["blend"]).is_file():
            wanted.append(found)
    if not wanted:
        return True
    own_writer = host._worker_running()
    host._state["busy"] = True   # This Angelo's writer yields; the next tick reads the person's work again.
    deadline = time.monotonic() + (WRITE_WAIT if own_writer else 0.0)   # Another Angelo's: never waited for.
    while True:
        with studio_assets.sync_lock(directory) as held:
            if held:
                manifest = studio_assets.read_manifest(directory)
                results = host.convert_batch(bpy.app.binary_path, wanted, threading.Event())
                studio_assets.apply_results(manifest, wanted, results, host._state["session"])
                studio_assets.write_manifest(directory, manifest)
                host._state["changed"] = True
                return all((results.get(found["id"]) or {}).get("ok") for found in wanted)
        if time.monotonic() > deadline:
            return False   # Another Angelo is writing this folder: drag the files instead.
        time.sleep(0.1)


_source = {"area": 0, "items": frozenset()}   # The drag Media Studio's grid started last.


def came_back(area, identities):
    """Whether a drop on ``area`` is the drag its own grid started (a click that moved a little): it adds nothing, as
    an asset dropped back on the Asset Browser. Asked once per drop."""
    back = area is not None and _source["area"] == area.as_pointer() and _source["items"] == frozenset(identities)
    forget_source()
    return back


def forget_source():
    """The grid's last drag landed somewhere (or a new press began): a later drop is never its coming back."""
    _source.update(area=0, items=frozenset())


def start(context, identities):
    """Blender's asset drag of the items (written first if needed), or of their files when the library is off."""
    from .studio_state import StudioLibrary
    library_dir, _directory, entry = _library()
    studio = StudioLibrary(library_dir)
    prefixes, paths = [], []
    for identity in identities:
        record = studio.assets.get(identity)
        found = studio_assets.item(library_dir, record, studio.folders) if record else None
        if found is None:
            continue
        prefixes.append(f"{'models' if found['kind'] == '3D' else 'images'}/{identity}.blend/")
        paths.extend(found["files"])
    if not prefixes:
        return False
    _source.update(area=context.area.as_pointer() if context.area else 0, items=frozenset(identities))
    window = context.window
    if window is not None:
        window.cursor_set('WAIT')
    try:
        written = entry is not None and ensure_written(identities)
    finally:
        if window is not None:
            window.cursor_set('DEFAULT')
    result = bpy.ops.asset.angelo_drag('INVOKE_DEFAULT', library=entry.name if written else "",
                                       items="\n".join(prefixes) if written else "", paths="\n".join(paths))
    return result == {'FINISHED'}


def prefetch():
    """Load the library's asset list in the background, so the first drag finds it ready (Media Studio's open)."""
    _library_dir, _directory, entry = _library()
    if entry is not None and available():   # bpy.ops answers hasattr for any name: ask the registered operators.
        bpy.ops.asset.angelo_drag('INVOKE_DEFAULT', library=entry.name, prefetch=True)
