# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assistant replies in the chat transcript: markdown drawn natively, laid out once per reply.

API (``chat_view.draw`` calls these for every ``assistant`` row; error rows go to ``chat_notices``):
    block(message, key, text, width, size, line_height) -> the transcript block ("reply", message, (), height, key, 0)
    draw(state, message, key, top, bottom, width, size, line_height, clip)

A reply is parsed by ``markdown_blocks`` and laid out by ``_layout``, which is cached by its text, width, size and
fonts, so a long chat does not re-wrap every frame and a streaming reply re-lays out only when a fragment
arrives. Paragraphs, headings, bullet and numbered lists, block quotes, inline code (a mono chip), links (teal,
clickable through the chat's ``url`` action, which opens only http(s)) and fenced code blocks (a dark panel with
its language and a Copy action) are drawn. A fence still streaming is an open code block. In a right-to-left
language the canvas mirrors the layout (``canvas_rtl``); code blocks stay left to right.
"""

from functools import lru_cache


from . import canvas_controls, canvas_rtl, ui_tokens
from .markdown_blocks import inline, parse
from .ui_text import iface_

CODE_FILL = (0.071, 0.078, 0.094, 1)      # A step below the transcript, the code panel.
CODE_EDGE = (1, 1, 1, 0.07)
CHIP_FILL = (1, 1, 1, 0.075)              # Inline code chip.
CODE_TEXT = (0.86, 0.90, 0.92, 1)
QUOTE_BAR = (0.169, 0.769, 0.749, 0.55)   # ui_tokens.TEAL at reduced alpha.
LINK = ui_tokens.TEAL
LINK_HOVER = ui_tokens.TEAL_HOVER
_CODE_WRAP = 0.86                         # A paragraph with inline code wraps a little narrower: mono is wider.


def _view():
    from . import chat_view
    return chat_view


def _measure(font, size, value):
    """Width as drawn (``chat_view.measure``), so runs on one row join without gaps."""
    return _view().measure(value, size, font)


def _row_runs(value, offset, spans):
    """A row's text split into (text, kind, url) runs by the paragraph's inline spans."""
    runs, cursor, end = [], 0, len(value)
    for start, finish, kind, url in spans:
        begin, stop = max(start - offset, 0), min(finish - offset, end)
        if stop <= begin or begin < cursor:
            continue
        if begin > cursor:
            runs.append((value[cursor:begin], "", ""))
        runs.append((value[begin:stop], kind, url))
        cursor = stop
    if cursor < end:
        runs.append((value[cursor:], "", ""))
    return tuple(runs)


@lru_cache(maxsize=256)
def _layout(text, width, size, line_height, font, mono, scale=1.0):  # ``scale``: rows are measured at it (wrap).
    """Draw ops for a reply, top down: ``(kind, x, dy, ...)`` with ``dy`` below the block's top; and its height."""
    view, ops, dy = _view(), [], 0
    body = size
    for index, block in enumerate(parse(text)):
        if block.kind == "heading":
            heading = {1: body + 7, 2: body + 5}.get(block.level, body + 2)
            dy += 14 if index else 0
            plain, _ = inline(block.text)
            for value, _offset in view.wrap(plain, max(40, width), heading, font):
                dy += heading + 9
                ops.append(("runs", 0, dy, ((value.rstrip(), "", ""),), heading, "heading"))
            dy += 8
            continue
        if block.kind == "rule":
            dy += 12
            ops.append(("rule", 0, dy, width))
            dy += 8
            continue
        if block.kind == "code":
            mono_size = body - 1
            rows = view.wrap(block.text or " ", max(40, width - 28), mono_size, mono, ordered=False)
            header = 26
            height = header + len(rows) * (mono_size + 7) + 14
            dy += 6 if index else 0
            ops.append(("code", 0, dy, width, height, block.lang, block.text, tuple(v for v, _ in rows),
                        block.closed, mono_size))
            dy += height + 8
            continue
        indent = 0
        prefix = ""
        if block.kind in {"bullet", "number"}:
            indent = 18 + block.level * 18
            prefix = block.marker
        elif block.kind == "quote":
            indent = 14
        plain, spans = inline(block.text)
        rtl = canvas_rtl.has_rtl(plain)
        wrap_width = max(40, (width - indent) * (_CODE_WRAP if any(s[2] == "code" for s in spans) else 1))
        rows = view.wrap(plain, wrap_width, body, font)
        first_dy = dy
        for row, (value, offset) in enumerate(rows):
            dy += line_height
            runs = ((value.rstrip(), "", ""),) if rtl else _row_runs(value.rstrip(), offset, spans)
            ops.append(("runs", indent, dy - 6, runs, body, "quote" if block.kind == "quote" else "body"))
            if row == 0 and prefix:
                ops.append(("marker", indent - 14 if block.kind == "bullet" else indent - 18, dy - 6, prefix, body))
        if block.kind == "quote" and rows:
            ops.append(("bar", 0, first_dy + 2, dy - first_dy + 2))
        dy += 1 if block.kind in {"bullet", "number"} else 10
    return tuple(ops), max(line_height, dy)


def block(message, key, text, width, size, line_height):
    """The transcript block of a reply: its cached layout and height (with room for its hover Copy)."""
    view = _view()
    ops, height = _layout(text, max(40, width - 32), size, line_height, view._font, view._mono or view._font, getattr(view, "_scale", 1.0))
    return "reply", dict(message, _reply_ops=ops), (), height + 26, key, 0


def _draw_runs(state, runs, x, y, size, role, line_height):
    view = _view()
    font, mono = view._font, view._mono or view._font
    color = ui_tokens.TEXT if role in {"body", "heading"} else ui_tokens.MUTED
    if len(runs) == 1 and not runs[0][1]:
        view.text(runs[0][0], x, y, size, color)
        return
    widths = [_measure(mono, size - 1, value) + 10 if kind == "code" else _measure(font, size, value)
              for value, kind, _ in runs]
    total, advance, mirrored = sum(widths), 0, canvas_rtl.mirrored()
    for (value, kind, url), width in zip(runs, widths):
        # Runs of a left-to-right row keep their order in a mirrored (right-to-left) canvas too.
        rx = x + total - advance - width if mirrored else x + advance
        advance += width
        if kind == "code":
            view.shape(rx, y - 4, width - 2, size + 6, CHIP_FILL, 4)
            view.text(value, rx + 4, y, size - 1, CODE_TEXT, mono)
        elif kind == "link":
            lit = canvas_controls.state_of(state, "url", url) in {"hover", "pressed"}
            view.text(value, rx, y, size, LINK_HOVER if lit else LINK)
            view.stroke(rx, y - 3, rx + width, y - 3, LINK_HOVER if lit else (LINK[0], LINK[1], LINK[2], 0.45), 1.0)
            state["hits"].append(((rx, y - 5, width, line_height), "url", url))
            canvas_controls.style(state, "url", url, "link")
        else:
            view.text(value, rx, y, size, color)


def _draw_code(state, x, top, op, clip):
    view = _view()
    _, _, dy, width, height, lang, code, rows, closed, mono_size = op
    y0 = top - dy - height
    view.shape(x, y0, width, height, CODE_EDGE, 9)
    view.shape(x + 1, y0 + 1, width - 2, height - 2, CODE_FILL, 8)
    label = lang or iface_("Code")
    view.text(label, x + 14, top - dy - 18, 11, ui_tokens.MUTED)
    if closed:
        copy = iface_("Copy")
        copy_width = _measure(view._font, 11, copy)
        rect = (x + width - copy_width - 30, top - dy - 24, copy_width + 20, 20)
        lit = canvas_controls.state_of(state, "copy", code) in {"hover", "pressed"}
        if lit:
            view.shape(*rect, (1, 1, 1, 0.08), 6)
        view.text(copy, rect[0] + 10, rect[1] + 6, 11, ui_tokens.TEXT if lit else ui_tokens.MUTED)
        state["hits"].append((rect, "copy", code))
    else:
        view.text("…", x + width - 26, top - dy - 18, 11, ui_tokens.MUTED)
    view.stroke(x + 1, top - dy - 26, x + width - 1, top - dy - 26, CODE_EDGE, 1)
    y = top - dy - 26 - mono_size - 6
    low, high = clip
    with canvas_rtl.unmirrored():  # Code reads left to right in every language; the panel is centred, so x holds.
        for value in rows:
            if low - mono_size <= y <= high:
                view.text(value, x + 14, y, mono_size, CODE_TEXT, view._mono or view._font, shaped=False)
            y -= mono_size + 7


def draw(state, message, key, top, bottom, width, size, line_height, clip):
    """Draw a laid-out reply between ``top`` and ``bottom``; ``clip`` is the transcript's (low, high) y range."""
    view, x0 = _view(), 16
    low, high = clip
    for op in message["_reply_ops"]:
        kind, dx, dy = op[0], op[1], op[2]
        y = top - dy
        if kind == "code":
            if top - dy - op[4] <= high and top - dy >= low:
                _draw_code(state, x0 + dx, top, op, clip)
            continue
        if not low - line_height <= y <= high + line_height:
            continue
        if kind == "runs":
            _draw_runs(state, op[3], x0 + dx, y, op[4], op[5], line_height)
        elif kind == "marker":
            view.text(op[3], x0 + dx, y, op[4], ui_tokens.MUTED)
        elif kind == "bar":
            view.shape(x0, top - dy - op[3], 3, op[3], QUOTE_BAR, 1.5)
        elif kind == "rule":
            view.stroke(x0, y, x0 + op[3], y, ui_tokens.LINE, 1)
    if state.get("hover_message") == key:  # Copy the whole reply: shown on hover, as in Codex.
        view.text(iface_("Copy"), 16, bottom + 8, 11, ui_tokens.MUTED)
        state["hits"].append(((16, bottom + 3, 42, 20), "copy", message["text"]))
    state.setdefault("message_rects", {})[key] = (16, bottom, width - 32, top - bottom)
    if message.get("restore_checkpoint"):
        view.text(iface_("Undo restore"), 40, bottom + 4, 11, ui_tokens.MUTED)
        state["hits"].append(((32, bottom, 116, 24), "restore", message["restore_checkpoint"]))
