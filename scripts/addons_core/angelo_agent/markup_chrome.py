# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Draws the picture viewer's chrome as ``markup_layout`` placed it: the bar and its glyphs, the palette, the
instructions field and send, Close, the comment hint and the hovered control's tooltip (2.1.0).

Shapes and strokes only, in card_kit's language (``ui_tokens``): no icon font, nothing stock. A glyph never mirrors;
the bar's order does (``canvas_rtl``, begun by ``markup_draw``). Labels are translated here, at display time.
"""

import math

from . import canvas_controls, chat_view, markup_doc as md, markup_layout as ml
from . import ui_tokens as tk
from .ui_kit_field import draw_field
from .ui_text import iface_, n_, language

HINT_SIZE = n_("{hint} · {size}")   # Resize's hint with the size it will make.


def _label(value):
    if isinstance(value, tuple):
        template, fields = value
        return iface_(template, **{k: iface_(v) if k == "name" else v for k, v in fields.items()})
    return iface_(value)


def _ring(cx, cy, r, colour, width):
    steps = 40
    for i in range(steps):
        a0, a1 = 2 * math.pi * i / steps, 2 * math.pi * (i + 1) / steps
        chat_view.stroke(cx + r * math.cos(a0), cy + r * math.sin(a0), cx + r * math.cos(a1), cy + r * math.sin(a1),
                         colour, width)


def glyph(name, x, y, w, h, colour, s, session=None):
    """A control's 20 px glyph centred in (x, y, w, h)."""
    cx, cy, u = x + w / 2.0, y + h / 2.0, 8 * s
    line = 1.6 * s
    stroke = chat_view.stroke
    if name == "select":   # An arrow pointer.
        pts = [(cx - u * 0.6, cy + u), (cx - u * 0.6, cy - u * 0.7), (cx - u * 0.1, cy - u * 0.2),
               (cx + u * 0.6, cy - u * 0.3), (cx - u * 0.6, cy + u)]
        for a, b in zip(pts, pts[1:]):
            stroke(*a, *b, colour, line)
    elif name == "pen":   # A squiggle.
        previous = None
        for i in range(13):
            t = i / 12.0
            point = (cx - u + 2 * u * t, cy + 0.5 * u * math.sin(t * 2.4 * math.pi))
            if previous:
                stroke(*previous, *point, colour, line)
            previous = point
    elif name == "text":
        stroke(cx - u * 0.8, cy + u * 0.8, cx + u * 0.8, cy + u * 0.8, colour, line)
        stroke(cx, cy + u * 0.8, cx, cy - u * 0.8, colour, line)
    elif name == "arrow":
        stroke(cx - u, cy - u, cx + u, cy + u, colour, line)
        stroke(cx + u, cy + u, cx + u * 0.1, cy + u, colour, line)
        stroke(cx + u, cy + u, cx + u, cy + u * 0.1, colour, line)
    elif name == "ellipse":
        _ring(cx, cy, u, colour, line)
    elif name == "rect":
        for a, b in (((-1, -0.75), (1, -0.75)), ((1, -0.75), (1, 0.75)), ((1, 0.75), (-1, 0.75)), ((-1, 0.75), (-1, -0.75))):
            stroke(cx + a[0] * u, cy + a[1] * u, cx + b[0] * u, cy + b[1] * u, colour, line)
    elif name == "remove_bg":   # A figure in front of a dashed frame.
        for i in range(8):
            if i % 2 == 0:
                stroke(cx - u + i * u / 4, cy - u * 0.9, cx - u + (i + 1) * u / 4, cy - u * 0.9, colour, line)
                stroke(cx - u + i * u / 4, cy + u * 0.9, cx - u + (i + 1) * u / 4, cy + u * 0.9, colour, line)
        chat_view.shape(cx - u * 0.3, cy + u * 0.05, u * 0.6, u * 0.6, colour, u * 0.3)
        chat_view.shape(cx - u * 0.55, cy - u * 0.85, u * 1.1, u * 0.8, colour, u * 0.35)
    elif name == "resize":   # Crop marks.
        stroke(cx - u * 0.6, cy + u, cx - u * 0.6, cy - u * 0.6, colour, line)
        stroke(cx - u * 0.6, cy - u * 0.6, cx + u, cy - u * 0.6, colour, line)
        stroke(cx - u, cy + u * 0.6, cx + u * 0.6, cy + u * 0.6, colour, line)
        stroke(cx + u * 0.6, cy + u * 0.6, cx + u * 0.6, cy - u, colour, line)
    elif name == "clear":   # A bin.
        stroke(cx - u * 0.8, cy + u * 0.6, cx + u * 0.8, cy + u * 0.6, colour, line)
        for a, b in (((-0.6, 0.6), (-0.45, -0.9)), ((-0.45, -0.9), (0.45, -0.9)), ((0.45, -0.9), (0.6, 0.6))):
            stroke(cx + a[0] * u, cy + a[1] * u, cx + b[0] * u, cy + b[1] * u, colour, line)
        stroke(cx - u * 0.25, cy + u * 0.6, cx - u * 0.25, cy + u * 0.9, colour, line)
        stroke(cx - u * 0.25, cy + u * 0.9, cx + u * 0.25, cy + u * 0.9, colour, line)
        stroke(cx + u * 0.25, cy + u * 0.9, cx + u * 0.25, cy + u * 0.6, colour, line)
    elif name in ("apply_resize", "run"):   # A check mark.
        stroke(cx - u * 0.7, cy, cx - u * 0.15, cy - u * 0.6, colour, line * 1.2)
        stroke(cx - u * 0.15, cy - u * 0.6, cx + u * 0.8, cy + u * 0.6, colour, line * 1.2)
    elif name in ("eraser", "erase"):   # A tilted block with its rubber end.
        corners = [(-0.9, -0.2), (0.1, 0.8), (0.9, 0.0), (-0.1, -1.0)]
        pts = [(cx + a * u, cy + b * u) for a, b in corners] + [(cx - 0.9 * u, cy - 0.2 * u)]
        for a, b in zip(pts, pts[1:]):
            stroke(*a, *b, colour, line)
        stroke(cx - 0.4 * u, cy + 0.3 * u, cx + 0.4 * u, cy - 0.5 * u, colour, line)
    elif name in ("undo", "redo"):
        sign = -1 if name == "undo" else 1
        previous = None
        for i in range(10):
            a = math.pi * (0.15 + 0.7 * i / 9.0)
            point = (cx - sign * u * 0.7 * math.cos(a), cy - u * 0.2 + u * 0.7 * math.sin(a))
            if previous:
                stroke(*previous, *point, colour, line)
            previous = point
        tip = (cx + sign * u * 0.6, cy - u * 0.2 + u * 0.7 * math.sin(math.pi * 0.85))
        stroke(*tip, tip[0] - sign * u * 0.45, tip[1] + u * 0.05, colour, line)
        stroke(*tip, tip[0] - sign * u * 0.05, tip[1] - u * 0.45, colour, line)
    elif name in ("view", "close"):   # Done and Close: a cross.
        stroke(cx - u * 0.7, cy - u * 0.7, cx + u * 0.7, cy + u * 0.7, colour, line)
        stroke(cx - u * 0.7, cy + u * 0.7, cx + u * 0.7, cy - u * 0.7, colour, line)
    elif name == "markup":   # A pen over a line.
        stroke(cx - u, cy - u * 0.9, cx + u, cy - u * 0.9, colour, line)
        stroke(cx - u * 0.6, cy - u * 0.4, cx + u * 0.6, cy + u * 0.8, colour, line * 1.6)
    elif name == "comment":   # A speech bubble.
        chat_view.shape(cx - u, cy - u * 0.4, 2 * u, 1.4 * u, colour, 4 * s)
        stroke(cx - u * 0.5, cy - u * 0.4, cx - u * 0.8, cy - u, colour, line * 1.4)
    elif name == "palette" and session is not None:
        r = u * 0.75
        chat_view.shape(cx - r, cy - r, 2 * r, 2 * r, md.COLOURS[session.colour], r)
        _ring(cx, cy, r + 1.5 * s, tk.SWATCH_EDGE, s)
    elif name == "send":   # An arrow up.
        stroke(cx, cy - u * 0.8, cx, cy + u * 0.8, colour, line * 1.2)
        stroke(cx, cy + u * 0.8, cx - u * 0.6, cy + u * 0.2, colour, line * 1.2)
        stroke(cx, cy + u * 0.8, cx + u * 0.6, cy + u * 0.2, colour, line * 1.2)


def _active(item, session):
    action, value = item["action"], item["value"]
    if action == "tool":
        return session.tool == value
    if action == "colour":
        return session.colour == value
    if action == "width":
        return session.width == value
    if action == "scale":
        return session.scale == value
    if action == "mode":
        return session.mode == value
    return False


def _enabled(item, session):
    action, erase = item["action"], session.mode == "erase"
    if action in ("clear",) or (action == "undo" and erase) or (action == "ask" and item["value"] == "erase"):
        return bool(session.erase_strokes)
    if action == "apply_resize":
        try:
            return session.resize_plan()["changed"]
        except ValueError:
            return False
    if item["action"] == "undo":
        return session.doc.can_undo
    if item["action"] == "redo":
        return session.doc.can_redo
    return True


def _button(item, session, s, ui):
    x, y, w, h = item["rect"]
    key = (item["action"], item["value"])
    enabled = _enabled(item, session)
    if _active(item, session):
        chat_view.shape(x, y, w, h, tk.TEAL_SOFT, tk.RADIUS_CONTROL * s)
    elif enabled and ui.get("pressed") == key:
        chat_view.shape(x, y, w, h, tk.PRESSED_LAYER, tk.RADIUS_CONTROL * s)
    elif enabled and ui.get("hover") == key:
        chat_view.shape(x, y, w, h, tk.CONTROL_HOVER, tk.RADIUS_CONTROL * s)
    colour = tk.TEXT if enabled else tk.DISABLED_TEXT
    if item["action"] == "colour":
        r = 9 * s
        cx, cy = x + w / 2.0, y + h / 2.0
        chat_view.shape(cx - r, cy - r, 2 * r, 2 * r, md.COLOURS[item["value"]], tk.RADIUS_CHIP * s)
    elif item["action"] == "width":
        cx, cy = x + w / 2.0, y + h / 2.0
        chat_view.stroke(cx - 9 * s, cy, cx + 9 * s, cy, colour, {1: 1.5, 2: 3.0, 3: 5.0}[item["value"]] * s)
    elif item["action"] == "scale":
        label = _label(item["label"])
        size = ml.LABEL_SIZE * s
        chat_view.text(label, x + (w - chat_view.measure(label, size)) / 2.0, y + h / 2.0 - 4.5 * s, size, colour)
    elif item["action"] == "ask" and session.mode == "erase":   # Erase's run: teal, it starts a paid edit.
        chat_view.shape(x + 2 * s, y + 2 * s, w - 4 * s, h - 4 * s, tk.TEAL if enabled else tk.DISABLED_FILL,
                        tk.RADIUS_CONTROL * s)
        glyph("run", x, y, w, h, tk.INK if enabled else tk.DISABLED_TEXT, s)
    elif item["action"] in ("mode", "ask") and item["value"] in ml.MODE_LABELS and session.mode == "view":
        glyph(item["value"], x + ml.PAD * s, y, ml.GLYPH_W * s, h, colour, s)
        chat_view.text(_label(item["label"]), x + (ml.GLYPH_W + 2 * ml.PAD) * s, y + h / 2.0 - 4.5 * s,
                       ml.LABEL_SIZE * s, colour)
    else:
        glyph(item["glyph"], x, y, w, h, colour, s, session)
    if ui.get("focus") == key:
        canvas_controls.ring(x - 2 * s, y - 2 * s, w + 4 * s, h + 4 * s, tk.RADIUS_CONTROL * s + 2 * s,
                             tk.FOCUS_RING * s, tk.FOCUS)


def _panel(rect, s):
    chat_view.shape(*rect, tk.MENU, tk.RADIUS_PANEL * s)


def draw(plan, session, s, ui, now, notice=None, busy=False, card=None):
    """``notice``: (text already translated, is_error) shown above the bar: a failure in coral (ui_kit's error
    language), what an edit is doing in the tooltip's colours. ``card``: an open question, (title, lines, run label),
    each a label as ``markup_layout`` gives them."""
    _panel(plan["bar_rect"], s)
    if notice and notice[0]:
        text, error = notice
        size = tk.TYPE_SMALL * s
        width = min(chat_view.measure(text, size) + 24 * s, plan["bar_rect"][2] + 240 * s)
        x = plan["bar_rect"][0] + plan["bar_rect"][2] / 2.0 - width / 2.0
        y = plan["bar_rect"][1] + plan["bar_rect"][3] + 44 * s
        chat_view.shape(x, y, width, 28 * s, tk.CORAL_SOFT if error else tk.TIP_FILL, tk.RADIUS_CHIP * s)
        chat_view.text(chat_view.elide(text, width - 24 * s, size), x + 12 * s, y + 9 * s, size,
                       tk.CORAL if error else tk.TIP_TEXT)
    for item in plan["bar"]:
        _button(item, session, s, ui)
    if plan["palette"]:
        _panel(plan["palette_rect"], s)
        for item in plan["palette"]:
            _button(item, session, s, ui)
    if plan["hint"]:
        hint = iface_(plan["hint"])
        if session.mode == "resize":
            try:
                hint = iface_(HINT_SIZE, hint=hint, size=_label(session.resize_plan()["label"]))
            except ValueError:
                pass
        size = tk.TYPE_SMALL * s
        width = chat_view.measure(hint, size) + 24 * s
        x = (plan["bar_rect"][0] + plan["bar_rect"][2] / 2.0) - width / 2.0
        y = plan["bar_rect"][1] + plan["bar_rect"][3] + 10 * s
        chat_view.shape(x, y, width, 28 * s, tk.TIP_FILL, tk.RADIUS_CHIP * s)
        chat_view.text(hint, x + 12 * s, y + 9 * s, size, tk.TIP_TEXT)
    if plan["field"]:
        draw_field(_view(), tuple(plan["field"]), s, fs=session.instructions,
                   placeholder=iface_(ml.LABELS["placeholder"]), action="instructions", time=now,
                   trailing=plan["send"][2])
        x, y, w, h = plan["send"]
        ready = session.can_send and not busy
        chat_view.shape(x + 2 * s, y + 2 * s, w - 4 * s, h - 4 * s, tk.TEAL if ready else tk.DISABLED_FILL, (h - 4 * s) / 2)
        glyph("send", x, y, w, h, tk.INK if ready else tk.DISABLED_TEXT, s)
    x, y, w, h = plan["close"]
    if ui.get("hover") == ("close", None):
        chat_view.shape(x, y, w, h, tk.CONTROL_HOVER, h / 2)
    glyph("close", x, y, w, h, tk.TEXT, s)
    if card and plan.get("card"):
        _card(plan, card, s, ui)
    else:
        _tooltip(plan, s, ui)


def _card(plan, card, s, ui):
    """A paid edit's question: its title, what it costs and does, and Cancel / Run."""
    title, lines, run = card
    x, y, w, h = plan["card"]
    chat_view.shape(0, 0, x * 2 + w, y * 2 + h, tk.VEIL)
    chat_view.shape(x, y, w, h, tk.MENU, tk.RADIUS_CARD * s)
    top = y + h - ml.EDGE * s
    chat_view.text(chat_view.elide(_label(title), w - 2 * ml.EDGE * s, tk.TYPE_HEADING * s), x + ml.EDGE * s,
                   top - tk.TYPE_HEADING * s, tk.TYPE_HEADING * s, tk.TEXT)
    line_y = top - ml.CARD_TITLE * s
    for line in lines:
        chat_view.text(chat_view.elide(_label(line), w - 2 * ml.EDGE * s, tk.TYPE_SMALL * s), x + ml.EDGE * s,
                       line_y - tk.TYPE_SMALL * s, tk.TYPE_SMALL * s, tk.SUBTITLE)
        line_y -= ml.CARD_LINE * s
    for answer in plan["answers"]:
        bx, by, bw, bh = answer["rect"]
        hovered = ui.get("hover") == ("answer", answer["value"])
        if answer["value"]:
            chat_view.shape(bx, by, bw, bh, tk.TEAL_HOVER if hovered else tk.TEAL, tk.RADIUS_CONTROL * s)
            colour, label = tk.INK, _label(run)
        else:
            chat_view.shape(bx, by, bw, bh, tk.CONTROL_HOVER if hovered else tk.CONTROL_OFF, tk.RADIUS_CONTROL * s)
            colour, label = tk.TEXT, _label(answer["label"])
        size = tk.TYPE_BODY * s
        chat_view.text(label, bx + (bw - chat_view.measure(label, size)) / 2.0, by + bh / 2.0 - size * 0.36, size,
                       colour)


def _tooltip(plan, s, ui):
    hovered = ui.get("hover")
    if not hovered or not ui.get("tip"):
        return
    label = None
    for item in plan["bar"] + (plan["palette"] or []):
        if (item["action"], item["value"]) == hovered:
            label, anchor = item["label"], item["rect"]
    if hovered == ("close", None):
        label, anchor = plan["close_label"], plan["close"]
    if hovered == ("send", None):
        label, anchor = ml.LABELS["send"], plan["send"]
    if hovered == ("instructions", None) and plan["field"] and language() in PASTE_LANGUAGES:
        label, anchor = ml.LABELS["paste_hint"], plan["field"]
    if label is None:
        return
    size = tk.TYPE_SMALL * s
    rows = tip_rows(_label(label), lambda text: chat_view.measure(text, size), TIP_WIDTH * s)
    line = 18 * s
    width = max(chat_view.measure(row, size) for row in rows) + 20 * s
    height = 26 * s + line * (len(rows) - 1)
    x, y = canvas_controls.tip_rect(anchor, width, height, s)[:2]
    chat_view.shape(x, y, width, height, tk.TIP_FILL, tk.RADIUS_CHIP * s)
    for index, row in enumerate(rows):
        chat_view.text(row, x + 10 * s, y + 8 * s + line * (len(rows) - 1 - index), size, tk.TIP_TEXT)


# The paste hint is for those who type with an input method the viewer's fields don't take yet (re-review of
# b5808fd5a2e, N5: it showed to everyone on every hover, one unwrapped line).
PASTE_LANGUAGES = frozenset({"zh_HANS", "zh_HANT", "ja", "ko"})
TIP_WIDTH = 320   # A tooltip's widest row, in pixels at scale 1; a longer one wraps.


def tip_rows(text, measure, limit):
    """``text`` in rows no wider than ``limit``: broken at spaces, or between characters in scripts without them."""
    parts = text.split(" ") if " " in text else list(text)
    joiner = " " if " " in text else ""
    rows = [""]
    for part in parts:
        candidate = rows[-1] + joiner + part if rows[-1] else part
        if rows[-1] and measure(candidate) > limit:
            rows.append(part)
        else:
            rows[-1] = candidate
    return rows


_card_view = None


def _view():
    global _card_view
    if _card_view is None:
        from .card_view import View
        _card_view = View()
    return _card_view
