# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A region left alone shows what a redraw would show: the watch that finds a picture gone stale.

Evidence (2026-11-16, 2.0.0): Generation Flows' pump asked every chat, Media Studio and flow editor to redraw every
2 s whether anything had changed (``test_blender_idle_redraw``). When that stopped, a chat whose model list arrived
for a connection it was not bound to stayed as it was: the state had changed and nothing had asked for a redraw; the
blanket redraw had been showing it within two seconds. Any state a surface draws from can have such a write path,
and reading the code for each one finds the cases somebody thought of. This watch finds them by what is on screen.

How: with ``ANGELO_QA_STALE_FRAMES`` set, ``run_native_suites.py`` starts the watch in every window suite's process
before the suite's own script (``start_line``, placed by ``build_command``); a windowless suite never gets it, and
no suite imports it, so a machine without a GPU never reaches its drawing code (``test_native_suite_opengl``). The
watch counts the draws of the regions in scope and keeps each one's last picture (the framebuffer the region drew
into, read at the end of its draw). A region nobody has drawn for ``REST_SECONDS`` is asked to draw again, and the
two pictures are compared: the same picture is the rule. A different one is a stale frame: something the region
draws from changed and no redraw was requested. It is written to ``stale-frames.json`` in the suite's QA folder with
the tab, editor, region, how long the region had rested, where the pictures differ and the two pictures (the
differing part, as PNG).

Scope (the variable's value): ``CONSOLE,NODE_EDITOR`` style editor types (the chat and Media Studio are CONSOLE
editors, Generation Flows a NODE_EDITOR), or ``all``. ``1`` is the three surfaces the blanket redraw covered.

What it is not: a pass/fail gate by itself. Asking a rested region to draw is the blanket redraw again, so a suite
run under the watch may pass where the app alone would wait forever: read the report. A picture that depends on the
clock (a caret that blinks without asking for redraws, "2 minutes ago") is reported too and is for a person to
judge; an animation that asks for its own redraws never rests, so it is never compared. A change that arrives in the
very main-loop turn of the watch's own request is reported although it did ask: rare (one turn in a rest period),
and the report says how long the region had rested.
"""

import atexit
import json
import os
import struct
import time
import zlib
from pathlib import Path

ENV = "ANGELO_QA_STALE_FRAMES"
DEFAULT_SCOPE = ("CONSOLE", "NODE_EDITOR")
REST_SECONDS = 1.5      # No draw for this long: the region is at rest and may be compared.
ARM_SECONDS = 0.25      # Between finding a region at rest and asking it to draw: a draw on its way arrives first.
PICTURES = 12           # Stale frames whose two pictures are saved, per process.
REPORT = "stale-frames.json"

_regions = {}           # region pointer -> its last picture and times
_events = []
_state = {"started": None, "scope": DEFAULT_SCOPE, "out": None, "draws": 0, "probes": 0, "saved": 0, "errors": []}


def scope_of(value):
    """The editor types a value of the variable names; None for ``all``; ``DEFAULT_SCOPE`` for ``1``."""
    value = (value or "").strip()
    if value == "all":
        return None
    if value in ("", "1"):
        return DEFAULT_SCOPE
    return tuple(part for part in value.split(",") if part)


def valid(value):
    """Whether a value may be passed to a suite: ``1``, ``all`` or editor type names."""
    parts = (value or "").split(",")
    return value in ("1", "all") or (bool(value) and all(part and part.replace("_", "").isalnum() and part.isupper()
                                                         for part in parts))


def difference(before, after, width, height):
    """Where two RGBA pictures of one size differ: ``(share of pixels, (x0, y0, x1, y1))`` or ``(0.0, None)``."""
    import numpy
    a = numpy.frombuffer(before, dtype=numpy.uint8).reshape(height, width, 4)
    b = numpy.frombuffer(after, dtype=numpy.uint8).reshape(height, width, 4)
    changed = (a != b).any(axis=2)
    if not changed.any():
        return 0.0, None
    rows, columns = numpy.flatnonzero(changed.any(axis=1)), numpy.flatnonzero(changed.any(axis=0))
    return float(changed.mean()), (int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1)


def png(pixels, width, height, box):
    """A PNG of the part ``box`` of an RGBA picture whose first row is its bottom one (as a framebuffer reads)."""
    x0, y0, x1, y1 = box
    rows = b"".join(b"\x00" + pixels[(row * width + x0) * 4:(row * width + x1) * 4] for row in range(y1 - 1, y0 - 1, -1))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", x1 - x0, y1 - y0, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def _write():
    """The report, as it stands. Plain file writing: also run at exit, after Blender's data is gone."""
    out = _state["out"]
    if out is None:
        return
    try:
        Path(out, REPORT).write_text(json.dumps({
            "scope": "all" if _state["scope"] is None else list(_state["scope"]), "rest_seconds": REST_SECONDS,
            "draws_seen": _state["draws"], "regions_asked_to_draw_again": _state["probes"],
            "stale_frames": [{key: value for key, value in event.items() if key != "_pictures"} for event in _events],
            "errors": _state["errors"][:20]}, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass


def _in_scope(area):
    scope = _state["scope"]
    return area is not None and (scope is None or area.type in scope)


def _drawn():
    """A draw handler: this region was drawn; keep its picture, and compare it when the watch asked for the draw."""
    import bpy
    import gpu
    area, region = bpy.context.area, bpy.context.region
    if region is None or not _in_scope(area) or region.width < 2 or region.height < 2:
        return
    now = time.monotonic()
    _state["draws"] += 1
    key = region.as_pointer()
    width, height = region.width, region.height
    try:
        read = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE')
        try:
            pixels = memoryview(read).tobytes()
        except TypeError:
            read.dimensions = width * height * 4
            pixels = bytes(read.to_list())
    except Exception as error:   # noqa: BLE001  A picture that cannot be read is said, never guessed.
        if len(_state["errors"]) < 20:
            _state["errors"].append("%s/%s: %s" % (area.ui_type, region.type, error))
        return
    entry = _regions.get(key)
    if entry and entry.get("asked") and entry["size"] == (width, height) and len(pixels) == len(entry["pixels"]):
        share, box = difference(entry["pixels"], pixels, width, height)
        if box is not None:
            window = bpy.context.window
            event = {"s": round(now - _state["started"], 2), "tab": window.workspace.name if window else None,
                     "editor": area.ui_type, "region": region.type, "size": [width, height],
                     "rested_seconds": round(entry["asked"] - entry["at"], 2),
                     "share_of_pixels": round(share, 5), "box": list(box)}
            if _state["saved"] < PICTURES:
                _state["saved"] += 1
                margin = 24
                crop = (max(0, box[0] - margin), max(0, box[1] - margin), min(width, box[2] + margin),
                        min(height, box[3] + margin))
                event["_pictures"] = (entry["pixels"], pixels, width, height, crop, len(_events))
            _events.append(event)
    _regions[key] = {"pixels": pixels, "size": (width, height), "at": now, "armed": None, "asked": None}


def _tick():
    """A timer: ask each region at rest to draw again, and write what was found."""
    import bpy
    try:
        now = time.monotonic()
        live = set()
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if not _in_scope(area):
                    continue
                for region in area.regions:
                    key = region.as_pointer()
                    live.add(key)
                    entry = _regions.get(key)
                    if entry is None or entry["asked"]:
                        continue
                    if entry["armed"] is None:
                        if now - entry["at"] >= REST_SECONDS:
                            entry["armed"] = now
                    elif now - entry["armed"] >= ARM_SECONDS:
                        entry["asked"] = now
                        _state["probes"] += 1
                        region.tag_redraw()
        for key in [key for key in _regions if key not in live]:
            del _regions[key]
        wrote = False
        for event in _events:
            pictures = event.pop("_pictures", None)
            if pictures:
                before, after, width, height, crop, index = pictures
                names = ["stale-%02d-%s.png" % (index, which) for which in ("shown", "redrawn")]
                for name, pixels in zip(names, (before, after)):
                    Path(_state["out"], name).write_bytes(png(pixels, width, height, crop))
                event["pictures"], event["pictures_box"] = names, list(crop)
                wrote = True
        if wrote or now - _state.get("written", 0) > 5:
            _state["written"] = now
            _write()
    except Exception as error:   # noqa: BLE001  The watch never stops a suite.
        if len(_state["errors"]) < 20:
            _state["errors"].append("tick: %s" % error)
    return ARM_SECONDS


def watch(value=None, out=None):
    """Start the watch in this Blender process (once). ``value`` is the variable's; ``out`` the report's folder."""
    import bpy
    if _state["started"] is not None or bpy.app.background:
        return False
    _state.update(started=time.monotonic(), scope=scope_of(os.environ.get(ENV) if value is None else value),
                  out=out or os.environ.get("ANGELO_QA_DIR") or os.getcwd())
    regions = [item.identifier for item in bpy.types.Region.bl_rna.properties["type"].enum_items]
    for name in dir(bpy.types):
        space = getattr(bpy.types, name)
        if not (name.startswith("Space") and isinstance(space, type) and issubclass(space, bpy.types.Space)
                and hasattr(space, "draw_handler_add")):
            continue
        for region in regions:
            try:
                space.draw_handler_add(_drawn, (), region, 'POST_PIXEL')
            except (TypeError, ValueError, RuntimeError, AttributeError):   # This editor has no such region.
                continue
    bpy.app.timers.register(_tick, first_interval=1.0, persistent=True)
    atexit.register(_write)
    return True


def start_line(folder):
    """One line of Python that starts the watch in a suite's process, isolated like the suite itself. The runner's
    ``build_command`` puts it before the suite's script (it owns the command line and its window flags).
    ``folder`` is this file's, for the process's import path."""
    return ("import sys; sys.path.insert(0, %r); import suite_isolation, qa_stale_frames; qa_stale_frames.watch()"
            % str(folder))
