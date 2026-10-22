# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the live row's status line and its note (what ``live_progress`` decided), and the well under it that shows a
script being written (``script_overlay``), with the chat's canvas.

``chat_rows`` lays the live row out and calls these from its ``draw``; the canvas primitives, fonts and right-to-left
mirroring are ``chat_view``'s, as for every other row.
"""

import math

from .card_kit import AMBER, MUTED, TEAL, TEXT
from .ui_text import plural_

NOTE_LINE = 17    # A line of the note: a quiet or stalled turn.
CODE_LINE = 16    # A line of a script being written (script_overlay), in the well under the live row.
CODE_CAPTION = 20 # The well's caption line: how many lines the script has so far.
CODE_PAD = 8


def status_line(view, message, x, right, centre, size, state):
    """The live row's status line from ``x``: "• 1m 9s · 562 tokens · Thinking", the phase elided to ``right``.

    Only the dot is a marble mark (``marble_draw.paint``), drawn over the chat's kept picture on every frame so it
    breathes; the text is part of the picture, which ``marble_frames.key`` renews when the drawn time or token count
    changes (``live_progress.lead``): one layout a second instead of the text on every frame (the frame A/B of
    PLAN-FRAMES measured 0.07 ms more per frame with the text painted each time). The dot is teal, amber while the
    turn is stalled, and holds still with Reduce Motion. Without a clock (a voice turn) the line is the phase alone.
    """
    import time
    from . import live_progress, marble_draw, marble_host, ui, ui_kit
    started, phase = message.get("started"), message["text"]
    accent = AMBER if message.get("stalled") else TEAL
    baseline, left = centre - size * 0.36, x
    if started is not None:
        def dot(now):
            import gpu
            controller = ui._controller
            wait = marble_host.current(controller) if controller is not None else None
            breath = 0.5 if wait is None or wait.reduced else 0.5 + 0.5 * math.sin(now * 4.0)
            radius = 3.2 + 0.8 * breath
            gpu.state.blend_set('ALPHA')
            view.shape(x + 4 - radius - 2, centre - radius - 2, 2 * radius + 4, 2 * radius + 4,
                       (*accent[:3], 0.12 + 0.18 * (1 - breath)), radius + 2)
            view.shape(x + 4 - 3.2, centre - 3.2, 6.4, 6.4, accent, 3.2)

        marble_draw.paint(state, dot)
        left += 16
        controller = ui._controller
        lead = live_progress.lead(controller, time.monotonic()) if controller is not None else ""
        if lead:
            view.text(lead, left, baseline, size, MUTED)
            left += ui_kit.measure(view, lead, size)
            state.setdefault("progress_drawn", {})["live_clock"] = ("live_clock", lead.split(" · ")[0],
                                                                     accent == AMBER)  # For the GUI fixtures.
    if right - left > 20:
        view.text(view.elide(phase, right - left, size), left, baseline, size, TEXT)


def draw_note(view, message, top, width, size):
    """The note's lines from ``top`` down: muted while the model works quietly, amber when nothing arrives."""
    color = AMBER if message.get("stalled") else MUTED
    y = top - (size - 1) * 0.36
    for line in message.get("note") or ():
        view.text(view.elide(line, max(20, width - 56), size - 1), 40, y, size - 1, color)
        y -= NOTE_LINE


def code_height():
    """The height of the well that shows a script being written."""
    from .script_stream import SHOWN_LINES
    return 4 + 2 * CODE_PAD + CODE_CAPTION + SHOWN_LINES * CODE_LINE


def code_caption(lines):
    """The well's caption: the script is being written and how long it is so far."""
    return plural_(lines, "Writing the script: {count} line", "Writing the script: {count} lines")


def script_well(view, message, top, bottom, width, size, state):
    """A script being written, in a well under the live row: a caption with its line count, then its newest lines
    in the code font, the line being written brightest and followed by a caret. Display only (``script_overlay``)."""
    from .canvas_rtl import elide
    from .chat_rows import DETAIL_FILL, GUIDE, INDENT, LIVE, PLANNED, _fill, _note, _title
    lines, code = message["script"]
    caption = code_caption(lines)
    _note(state, "live_script", "live_script", caption, tuple(code))
    planned = len(message.get("planned") or ()) + bool(message.get("more"))
    if not planned:
        _fill(25, bottom + 6, 1, top - message.get("header", LIVE) - bottom - 2, GUIDE)
    well_top = top - message.get("header", LIVE) + 4 - PLANNED * planned - (6 if planned else 0) - 4
    x, right = 16 + INDENT + 8, width - 16
    _fill(x, bottom + 6, right - x, well_top - bottom - 6, DETAIL_FILL, 8)
    text_x, text_right, small = x + CODE_PAD + 4, right - CODE_PAD - 4, size - 2
    y = well_top - CODE_PAD - CODE_CAPTION / 2 - small * 0.36
    _title(view, caption, text_x, text_right, y, small, MUTED)
    mono = view._mono
    measure = lambda part: view.measure(part, small, mono)  # noqa: E731  (as drawn: chat_view.measure)
    y = well_top - CODE_PAD - CODE_CAPTION - CODE_LINE / 2 - small * 0.36
    for index, line in enumerate(code):
        last = index == len(code) - 1
        shown = elide(line.replace("\t", "    ").rstrip(), measure, max(20, text_right - text_x - (8 if last else 0)))
        view.text(shown, text_x, y, small, TEXT if last else MUTED, mono)
        if last:
            caret = text_x + measure(shown) + 2
            _fill(caret, y - small * 0.2, 2, small * 1.1, TEAL, 1)
        y -= CODE_LINE

