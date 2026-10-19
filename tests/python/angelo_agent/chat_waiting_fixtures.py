# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds and pixel checks of ``test_blender_chat_waiting.py``.

The rounds stream through the stand-in ``claude`` CLI (``fake_provider_cli``) as Claude Code 2.1.293 does: a
thinking block whose text arrives as summary sentences (``LINES``, ``LINE_SECONDS`` apart: the live row's thinking
line, ``plan_progress.summary_line``), then a castle plan at Opus 5.5's recorded pace (``plan_progress_fixtures``),
accepted, and a pause before the round's result so the revealed raven can be seen while the turn still runs. The
castle's plan asks to verify, so a second round (thinking, then an answer) keeps the turn building.

``crop`` cuts a drawn mark out of a window screenshot (its rect as the chat drew it), saves it enlarged for review
and measures its pixels: the share of pale unsaturated stone and of the mark's teal.
"""

import plan_progress_fixtures as pace
import reply_stream_fixtures as base

LINES = ("Reading the brief: a small castle on a hill.", "Laying out the curtain wall and four round towers.",
         "Working out the gatehouse and its arch.", "Checking the clearance between the towers.")
LINE_SECONDS = 1.4
CASTLE = {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
    pace.cube("Keep", "k", [0, 0, 1], [2, 2, 2]),
    pace.cube("Tower_NE", "t1", [3, 3, 1.5], [0.8, 0.8, 3]), pace.cube("Tower_NW", "t2", [-3, 3, 1.5], [0.8, 0.8, 3]),
    pace.cube("Tower_SE", "t3", [3, -3, 1.5], [0.8, 0.8, 3]), pace.cube("Tower_SW", "t4", [-3, -3, 1.5], [0.8, 0.8, 3]),
    pace.cube("Gatehouse", "g", [0, -3.4, 1], [1.6, 0.8, 2])])],
    "reply": "Built the keep, four round towers and the gatehouse.", "verify": True}
CASTLE_OBJECTS = ("Keep", "Tower_NE", "Tower_SW", "Gatehouse")
ANSWER = {"operations": [], "reply": "The castle stands: a keep, four towers and a gatehouse."}
QUIET = {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
    pace.cube("Quiet Keep", "q", [8, 0, 1], [2, 2, 2])])], "reply": "Added a quiet keep."}
MEASURE = {"operations": [base.operation("angelo_scene_program", mode="APPLY", operations=[
    pace.cube("Measured Keep", "m", [-8, 0, 1], [2, 2, 2])])], "reply": "Added a measured keep."}
MEASURE_THINKING = 22.0  # A long, silent plan: the measurements run inside it (test_blender_chat_waiting.measure).
REVEAL_PAUSE = 2.6  # Between the runtime accepting the plan (the reveal) and the round's result.


def _thinking(message, lines, seconds, index=0):
    events = [base.stream({"type": "content_block_start", "index": index,
                           "content_block": {"type": "thinking", "thinking": ""}})]
    for line in lines:
        events += [{"sleep": seconds}, base.stream({"type": "content_block_delta", "index": index, "delta": {
            "type": "thinking_delta", "thinking": line + " "}})]
    return events + [{"sleep": seconds}, base.stream({"type": "content_block_stop", "index": index})]


def _round(message, plan, lines, seconds, pause):
    events = base.round_events(message, [plan], pace.CHARS, pace.SECONDS, base.accepted(message, [plan]))
    events[1:1] = _thinking(message, lines, seconds)  # After message_start, before the plan call.
    for item in events:
        if "stream_plan" in item:
            item["stream_plan"]["index"] = 1
    result = next(index for index, item in enumerate(events) if item.get("type") == "result")
    events.insert(result, {"sleep": pause})
    return events


def castle_rounds():
    """The castle turn: thinking lines, the plan (accepted, then a pause), then a verify round that answers."""
    return [_round("msg_castle", CASTLE, LINES, LINE_SECONDS, REVEAL_PAUSE),
            _round("msg_castle_answer", ANSWER, (), 2.4, 0.0)]


def measure_rounds():
    """A turn that thinks for ``MEASURE_THINKING`` seconds with no event, then adds one cube."""
    return [_round("msg_measure", MEASURE, (), MEASURE_THINKING, 0.3)]


def quiet_rounds():
    """A shorter turn for Reduce Motion: one thinking line, the plan, accepted, a pause, done."""
    return [_round("msg_quiet", QUIET, LINES[:1], 1.0, 1.6)]


def stone(pixel):
    low, high = min(pixel[:3]), max(pixel[:3])
    return low > 0.72 and high - low < 0.09


def teal(pixel):
    r, g, b = pixel[:3]
    return g > 0.42 and g - r > 0.2 and b - r > 0.15


def crop(source, rect, out, zoom):
    """Shares of stone and teal pixels in ``rect`` (window pixels, origin bottom left) of the screenshot
    ``source``; the crop is saved ``zoom`` times larger at ``out`` for review."""
    import bpy
    import numpy
    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        width, height = image.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    pixels = pixels.reshape(height, width, 4)
    x, y, w, h = (int(round(value)) for value in rect)
    x, y = max(0, x), max(0, y)
    part = pixels[y:y + max(1, min(h, height - y)), x:x + max(1, min(w, width - x))]
    flat = part.reshape(-1, 4)
    shares = {"pixels": int(len(flat)), "stone": round(sum(1 for p in flat if stone(p)) / max(1, len(flat)), 4),
              "teal": round(sum(1 for p in flat if teal(p)) / max(1, len(flat)), 4)}
    large = numpy.repeat(numpy.repeat(part, zoom, axis=0), zoom, axis=1)
    result = bpy.data.images.new("marble crop", large.shape[1], large.shape[0], alpha=True)
    try:
        result.pixels.foreach_set(numpy.ascontiguousarray(large).ravel())
        result.filepath_raw, result.file_format = str(out), 'PNG'
        result.save()
    finally:
        bpy.data.images.remove(result)
    return shares


def differing(first, second, rect, holes, pad=4):
    """The share of pixels in ``rect`` (window pixels) that differ between two screenshots by more than 2/255
    in any channel, leaving out ``holes`` (the marks, which move between the two)."""
    import bpy
    import numpy
    pictures = []
    for path in (first, second):
        image = bpy.data.images.load(str(path), check_existing=False)
        try:
            width, height = image.size
            pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
            image.pixels.foreach_get(pixels)
        finally:
            bpy.data.images.remove(image)
        pictures.append(pixels.reshape(height, width, 4))
    x, y, w, h = (int(round(value)) for value in rect)
    a, b = (picture[y:y + h, x:x + w, :3] for picture in pictures)
    mask = numpy.ones(a.shape[:2], dtype=bool)
    for hx, hy, hw, hh in holes:
        left, bottom = int(hx - x) - pad, int(hy - y) - pad
        mask[max(0, bottom):max(0, bottom + int(hh) + 2 * pad), max(0, left):max(0, left + int(hw) + 2 * pad)] = False
    changed = (numpy.abs(a - b).max(axis=2) > 2.5 / 255) & mask
    return round(float(changed.sum()) / max(1, int(mask.sum())), 6)
