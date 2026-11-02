# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds and pixel checks of ``test_blender_chat_waiting.py``.

The rounds stream through the stand-in ``claude`` CLI (``fake_provider_cli``) as Claude Code 2.1.293 does: a
thinking block whose text arrives as summary sentences (``LINES``: the live row's thinking line,
``plan_progress.summary_line``), then a castle plan at Opus 5.5's recorded pace (``plan_progress_fixtures``),
accepted, and the round's result. The castle's plan asks to verify, so a second round (thinking, then an answer)
keeps the turn building.

Every moment the suite observes is held (``Holds``), never timed: the stand-in waits there until the suite has
seen it and lets it go. A timed pause before the moment raced the app's main thread: a stall longer than the
pause applied both sides of it in one pump pass (1.0.8, on the release Mac at load 5-8: the measured wait's
22 s plan arrived before the measurements ended; the revealed raven's 2.6 s pause passed unseen).

``crop`` cuts a drawn mark out of a window screenshot (its rect as the chat drew it), saves it enlarged for review
and measures its pixels: the share of pale unsaturated stone and of the mark's teal.
"""

import shutil
from pathlib import Path

import fake_provider_cli
import plan_progress_fixtures as pace
import reply_stream_fixtures as base

LINES = ("Reading the brief: a small castle on a hill.", "Laying out the curtain wall and four round towers.",
         "Working out the gatehouse and its arch.", "Checking the clearance between the towers.")
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


class Holds:
    """One phase's holds (``fake_provider_cli.hold``), a fresh folder each under ``folder``, by name.

    ``hold(name)`` is the round item. ``let_go(name)`` asks for it to be released; ``pump`` (every suite tick)
    releases each asked-for hold once the stand-in waits there, so asking before it arrives still lets it go on.
    """

    def __init__(self, folder):
        self.folder, self.asked, self.thinking_seen = Path(folder), set(), None
        shutil.rmtree(self.folder, ignore_errors=True)

    def hold(self, name):
        return fake_provider_cli.hold(self.folder / name)

    def held(self, name):
        return fake_provider_cli.held(self.folder / name)

    def let_go(self, name):
        self.asked.add(name)

    def pump(self):
        for name in self.asked:
            fake_provider_cli.release(self.folder / name)

    def thinking_settled(self, controller):
        """Whether a thinking line sent now shows the moment the app reads it (call every tick).

        ``claude_stream`` shows a thinking block's text at most every ``THINKING_SECONDS``, timed from the block's
        start; a line that comes sooner waits for the next one, so a hold right after it waits for a line that never
        shows. The app stamps the block before its running row reaches the transcript, so that row seen here
        ``THINKING_SECONDS`` ago bounds it, on any machine load: a hold before the first line goes once this holds.
        """
        import time
        from angelo_agent import claude_stream
        now = time.monotonic()
        if self.thinking_seen is None and any(message.get("role") == "thinking" and message.get("status") == "running"
                                              for message in controller.state["messages"][-40:]):
            self.thinking_seen = now
        return self.thinking_seen is not None and now - self.thinking_seen >= claude_stream.THINKING_SECONDS

    def waiting(self):
        """The holds the stand-in waits at now, for a failure's message."""
        return sorted(path.name for path in self.folder.glob("*") if fake_provider_cli.held(path))


def _thinking(items, index=0):
    """A thinking block of ``items`` in order: summary lines (strings) and holds (round items)."""
    events = [base.stream({"type": "content_block_start", "index": index,
                           "content_block": {"type": "thinking", "thinking": ""}})]
    for item in items:
        events.append(item if isinstance(item, dict) else base.stream({"type": "content_block_delta", "index": index,
                                                                       "delta": {"type": "thinking_delta",
                                                                                 "thinking": item + " "}}))
    return events + [base.stream({"type": "content_block_stop", "index": index})]


def _round(message, plan, thinking, before_accepting=None, before_result=None):
    """One model message: ``thinking`` (``_thinking``'s items), then ``plan`` streamed at Opus 5.5's pace and accepted.

    ``before_accepting`` (a hold item, or None) waits before the runtime accepts the plan: the reveal comes after it;
    ``before_result`` waits after it, while the turn runs revealed.
    """
    verdict = base.accepted(message, [plan])
    outcome = [item for item in [before_accepting, *verdict[:-1], before_result, verdict[-1]] if item]
    events = base.round_events(message, [plan], pace.CHARS, pace.SECONDS, outcome)
    events[1:1] = _thinking(thinking)  # After message_start, before the plan call.
    for item in events:
        if "stream_plan" in item:
            item["stream_plan"]["index"] = 1
    return events


def castle_rounds(holds):
    """The castle turn and the ``holds`` (``Holds``) it waits at, in order: ``chips`` before the first thinking
    line (stone falls before any event; the line then shows on arrival, ``Holds.thinking_settled``), ``line``
    after it, ``rows`` with the plan written but not accepted, ``revealed`` once accepted, then the verify round's
    ``building``, thinking before its answer."""
    hold = holds.hold
    return [_round("msg_castle", CASTLE, [hold("chips"), LINES[0], hold("line"), *LINES[1:]],
                   hold("rows"), hold("revealed")),
            _round("msg_castle_answer", ANSWER, [hold("building")])]


def measure_rounds(holds):
    """A turn that thinks with no event until the suite lets ``silent`` go, then adds one cube."""
    return [_round("msg_measure", MEASURE, [holds.hold("silent")])]


def quiet_rounds(holds):
    """A shorter turn for Reduce Motion: one thinking line (``thinking`` before it, ``Holds.thinking_settled``;
    ``line`` after it), the plan, accepted (held at ``revealed``), done."""
    return [_round("msg_quiet", QUIET, [holds.hold("thinking"), LINES[0], holds.hold("line")],
                   before_result=holds.hold("revealed"))]


def texts(window):
    """Redraw ``window`` once and return the chat's text runs."""
    import bpy
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    return " ".join(runs)


def chat_draw_ms(window, area, region, iterations=20):
    """The chat region's whole draw, in milliseconds per draw."""
    import time
    import bpy
    with bpy.context.temp_override(window=window, area=area, region=region):
        begin = time.perf_counter()
        bpy.ops.wm.redraw_timer(type='DRAW', iterations=iterations)
        return round((time.perf_counter() - begin) * 1000 / iterations, 3)


def most_in_a_second(samples, start=0.0, seconds=1e9):
    """The most frames counted within any one second of ``samples`` ((seconds since sent, frames so far) pairs)
    taken from ``start`` for ``seconds``."""
    best, samples = 0, [(t, n) for t, n in samples if start <= t <= start + seconds]
    for t0, n0 in samples:
        best = max(best, max(n for t, n in samples if t0 <= t <= t0 + 1.0) - n0)
    return best


def draw_at(window, when):
    """Redraw ``window`` with the marks' clock at ``when`` (``marble_draw`` and ``marble_frames`` read it); the chat's
    view state then records what each mark drew. A pose is a function of time alone (``marble_wait.Wait.pose``),
    so a state lasting less than a frame (a blink) is drawn on purpose instead of caught by a frame's chance."""
    import time
    from types import SimpleNamespace
    import bpy
    from angelo_agent import marble_draw, marble_frames
    clocks = marble_draw.time, marble_frames.time
    marble_draw.time = marble_frames.time = SimpleNamespace(monotonic=lambda: when, perf_counter=time.perf_counter)
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        marble_draw.time, marble_frames.time = clocks


def still_frame(wait):
    """Whether the hero's chips in flight sit where they stood, with no dust and no shake (Reduce Motion), at a time
    inside the latest chip's fade: a frame is a function of time alone, so no tick has to land in a fade."""
    from angelo_agent import marble_carving as carving, marble_wait as mw
    now = max(flight.start for flight in wait.hero.flights) + carving.FADE_SECONDS / 2
    drawn = mw.frame(wait, wait.hero, now, False)
    rest = [p for flight in wait.hero.flights if 0 <= now - flight.start < carving.FADE_SECONDS
            for p in wait.hero.chips[flight.chip].mesh.positions]
    return bool(rest) and list(drawn.flying.positions) == rest and not drawn.dust.indices and drawn.offset == (0, 0)


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
