# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long the picture viewer takes on a 4K picture (doc/angelo/attachment_markup.rst, "Latency and cost").

Manual and timed (a quiet Mac 1 slot): run inside the app, with its window, after Angelo has started:

    Angelo --factory-startup --python tests/python/angelo_agent/probe_markup_latency.py

A 3840 x 2160 PNG is made in the chat's own pictures (noise, so its PNG is as large as a photo's). Each of ``RUNS``
rounds, alternating which comes first (ABAB, so a warming cache or a busy moment hits both alike):

* **open**: ``markup_overlay.open_reference`` and the window's next drawn frame (the chip's small picture scaled up);
* **full**: the viewer's own ``_load`` (the picture decoded at ``DISPLAY_SIDE`` and uploaded), the swap to full size;
* **flatten**: what Send does before its worker: decode at the flattened size, upload, ``markup_flatten.render`` with
  a pen stroke, an arrow, an ellipse, a text and two pins, then the PNG encode (the worker's step, timed here inline).

Targets from the design note: open under 100 ms, full under 300 ms, flatten under 250 ms (median). The report gives
every sample, the median, p75 and the spread; nothing is dropped. Output: ``ANGELO_MARKUP_LATENCY {json}`` and
``$ANGELO_QA_DIR/markup-latency.json``.
"""

import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import statistics
import tempfile
import time
from pathlib import Path

import bpy

RUNS = 10
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-markup-latency"))
OUT.mkdir(parents=True, exist_ok=True)
samples = {"open": [], "full": [], "flatten": []}
state = {"phase": "wait", "round": 0, "picture": None}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def make_picture(controller):
    import numpy as np
    from angelo_agent import owned_paths
    from angelo_agent.view_png import encode_png
    rng = np.random.default_rng(7)
    # A 4K picture within the chat's picture limit, as a person's would be: gradients with 16 px blocks of texture.
    # (Per-pixel noise does not compress: its PNG was 33 MB and the reference store refused it before any timing.)
    y, x = np.mgrid[0:2160, 0:3840]
    blocks = np.kron(rng.integers(0, 48, size=(135, 240, 3)), np.ones((16, 16, 1)))
    pixels = np.empty((2160, 3840, 4), dtype=np.uint8)
    pixels[..., 0] = (x * 200 // 3840 + blocks[..., 0]).clip(0, 255)
    pixels[..., 1] = (y * 200 // 2160 + blocks[..., 1]).clip(0, 255)
    pixels[..., 2] = ((x + y) * 160 // 6000 + blocks[..., 2]).clip(0, 255)
    pixels[..., 3] = 255
    reference = controller.references.new_observation("Latency probe")
    data = encode_png(pixels.tobytes(), 3840, 2160, bottom_up=True)
    assert len(data) <= controller.references.image_limit, "the probe's picture must fit the chat's limit: %d" % len(data)
    with open(owned_paths.require_writable(reference["path"]), "wb") as handle:
        handle.write(data)
    reference["bytes"] = len(data)
    controller.attachments.append(reference)
    return reference


def time_open(controller, picture):
    from angelo_agent import markup_overlay
    begun = time.perf_counter()
    markup_overlay.open_reference(controller, picture)
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    samples["open"].append((time.perf_counter() - begun) * 1000)


def time_full():
    from angelo_agent import markup_overlay
    shown = markup_overlay.current()
    shown.texture = None
    begun = time.perf_counter()
    markup_overlay._load()
    samples["full"].append((time.perf_counter() - begun) * 1000)


def time_flatten(controller, picture):
    from angelo_agent import markup_doc as md, markup_flatten, markup_message as mm, studio_tile_previews as tiles
    from angelo_agent.view_png import encode_png
    doc = md.MarkupDoc(picture["id"], (3840, 2160))
    doc.add_mark("pen", points=[(400, 400), (600, 520), (900, 610)], colour="red")
    doc.add_mark("arrow", start=(1200, 300), end=(1600, 700), colour="amber")
    doc.add_mark("ellipse", box=(2000, 900, 500, 300), colour="teal")
    doc.add_mark("text", at=(2600, 400), text="this strand")
    doc.add_comment(at=(1300, 1300), text="Too dark")
    doc.add_comment(box=(3000, 1500, 400, 300), text="Tighter")
    begun = time.perf_counter()
    path = controller.references.checked_path(picture)
    size = mm.flat_size(doc.size)
    pixels, width, height = tiles.decode(path, max(size))
    texture = tiles.upload(pixels, width, height)
    rgba, width, height = markup_flatten.render(doc, texture)
    encode_png(rgba, width, height)
    samples["flatten"].append((time.perf_counter() - begun) * 1000)


def summary(values):
    ordered = sorted(values)
    return {"n": len(values), "median_ms": round(statistics.median(values), 1),
            "p75_ms": round(ordered[max(0, int(len(ordered) * 0.75 + 0.5) - 1)], 1),
            "min_ms": round(ordered[0], 1), "max_ms": round(ordered[-1], 1),
            "samples_ms": [round(v, 1) for v in values]}


def tick():
    from angelo_agent import markup_overlay, ui
    try:
        if state["phase"] == "wait":
            if time.monotonic() - started < 8 or ui.get_controller() is None:
                return .5
            state["picture"] = make_picture(ui.get_controller())
            state["phase"] = "round"
            return .5
        controller, picture = ui.get_controller(), state["picture"]
        if state["round"] < RUNS:
            if state["round"] % 2 == 0:   # ABAB: the flatten first on even rounds, the open first on odd ones.
                time_flatten(controller, picture)
                time_open(controller, picture)
                time_full()
            else:
                time_open(controller, picture)
                time_full()
                time_flatten(controller, picture)
            markup_overlay.close("done")
            state["round"] += 1
            return .3
        report = {key: summary(values) for key, values in samples.items()}
        report["picture"] = {"size": [3840, 2160], "png_bytes": picture.get("bytes")}
        report["targets_ms"] = {"open": 100, "full": 300, "flatten": 250}
        report["note"] = "ABAB rounds; open = open_reference + one drawn frame; full = _load; flatten incl. PNG encode"
        (OUT / "markup-latency.json").write_text(json.dumps(report, indent=2))
        print("ANGELO_MARKUP_LATENCY", json.dumps(report), flush=True)
    except Exception as error:   # Said, then the app quits: a probe never hangs a slot.
        print("ANGELO_MARKUP_LATENCY_ERROR", repr(error), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.0)
