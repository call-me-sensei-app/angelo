# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A how-to card as drawn under its step in the chat (learn mode): layout without Blender, drawing with the canvas.

``layout(steps, width, size, line_height, measure, wrap)`` places a card's parts (``learn_cards.Step``) and returns
``(height, items)``; ``draw(view, items, x, top, width, height)`` draws them with the chat canvas's own primitives
(``chat_view``), so right-to-left mirroring, scaling and both themes are the canvas's. Colours are roles of
``ui_tokens``. Nothing here looks anything up: every text arrives ready (``learn_resolver``).

A card reads top to bottom, one block per hand command:

* its name, then its keys as key caps (or that it has none in the person's keymap);
* the key that opens one of its menus, when there is one;
* where it is, as breadcrumbs from the editor down to the item;
* the settings Angelo used, each with its own label;
* what must be true first (the mode, what is selected), as quiet pills;
* how doing it by hand differs, or why there is no single hand command.

No text is cut: a line that does not fit wraps, and breadcrumbs, key caps, settings and pills flow onto further
lines. Items are ``(kind, x, y, ...)`` with ``x`` from the card's left and ``y`` down from its top:
``("text", x, baseline, value, size, role)``, ``("cap" | "pill", x, y, width, height, value, size)``,
``("next", x, centre)`` (a breadcrumb's chevron) and ``("rule", x, y, width)``.

``plan(steps, ...)`` keeps the last ``MAX_PLANS`` layouts by the card they were made from, so a transcript that
redraws an open card every frame measures nothing again; ``forget()`` drops them (unregister).
"""

from collections import OrderedDict

from .ui_text import iface_, n_

HEADING = n_("How to do this yourself")
NO_KEY = n_("No shortcut in your keymap")
OPENS = n_("{key} opens {menu}")
PAD, GAP = 12, 6
CAP, CAP_PAD = 20, 7          # A key cap's height and its padding either side of the key's text.
PILL, PILL_PAD = 20, 8
NEXT = 14                     # The room a breadcrumb's chevron takes.
BETWEEN = 16                  # Between two settings.
MAX_PLANS = 32
_plans = OrderedDict()        # (id of the steps, width, size, line height) -> (the steps, (height, items)).


class _Flow:
    """Pieces placed left to right on lines of one height, wrapping at the right edge."""

    def __init__(self, left, right, top, line):
        self.left, self.right, self.line = left, right, line
        self.x, self.y = left, top

    def place(self, width, gap=0):
        """The x of a piece ``width`` wide (after ``gap`` when it follows another on its line), on a new line when
        it does not fit."""
        if self.x > self.left and self.x + gap + width > self.right:
            self.x, self.y = self.left, self.y + self.line
        elif self.x > self.left:
            self.x += gap
        x, self.x = self.x, self.x + width
        return x

    @property
    def bottom(self):
        return self.y + self.line


def _base(top, line, size):
    """The baseline of text centred on a line that starts at ``top`` (y grows downwards)."""
    return top + line / 2 + size * 0.36


def layout(steps, width, size, line_height, measure, wrap):
    """``(height, items)`` of a card ``width`` wide. ``measure(text, size)`` gives a text's width and
    ``wrap(text, width, size)`` its lines as the canvas would draw them."""
    items, small = [], size - 1
    left, right = PAD, width - PAD
    inner = max(40, right - left)
    row = max(line_height, CAP + 6)

    def lines(value, role, text_size, top):
        for line in wrap(value, inner, text_size):
            items.append(("text", left, _base(top, line_height, text_size), line, text_size, role))
            top += line_height
        return top
    y = lines(iface_(HEADING), "muted", small, PAD - 2) + 2

    for number, step in enumerate(steps):
        if number:
            items.append(("rule", left, y + GAP, inner))
            y += 2 * GAP + 1
        titles = list(wrap(step.title, inner, size)) or [step.title]
        for line in titles[:-1]:
            items.append(("text", left, _base(y, line_height, size), line, size, "text"))
            y += line_height
        flow = _Flow(left, right, y, row)
        x = flow.place(measure(titles[-1], size))
        items.append(("text", x, _base(flow.y, row, size), titles[-1], size, "text"))
        for key in step.keys:
            cap = measure(key, small) + 2 * CAP_PAD
            x = flow.place(cap, 10 if key is step.keys[0] else 6)
            items.append(("cap", x, flow.y + (row - CAP) / 2, cap, CAP, key, small))
        if step.command and not step.keys and not step.opens:
            text = iface_(NO_KEY)
            x = flow.place(measure(text, small), 10)
            items.append(("text", x, _base(flow.y, row, small), text, small, "muted"))
        y = flow.bottom
        if step.opens:
            y = lines(iface_(OPENS, key=step.opens[1], menu=step.opens[0]), "muted", small, y)
        if step.path:
            flow = _Flow(left, right, y, line_height)
            for index, part in enumerate(step.path):
                x = flow.place(measure(part, small) + (NEXT if index else 0))
                if index:
                    items.append(("next", x + NEXT / 2 - 1, flow.y + line_height / 2))
                    x += NEXT
                last = index == len(step.path) - 1
                items.append(("text", x, _base(flow.y, line_height, small), part, small, "text" if last else "muted"))
            y = flow.bottom
        if step.settings:
            flow = _Flow(left, right, y, line_height)
            for label, value in step.settings:
                wide = measure(label, small) + 6 + measure(value, small)
                x = flow.place(wide, BETWEEN)
                items.append(("text", x, _base(flow.y, line_height, small), label, small, "muted"))
                items.append(("text", x + measure(label, small) + 6, _base(flow.y, line_height, small), value, small, "text"))
            y = flow.bottom
        if step.needs:
            flow = _Flow(left, right, y + 2, PILL + 4)
            for need in step.needs:
                wide = measure(need, small - 1) + 2 * PILL_PAD
                x = flow.place(wide, 6)
                items.append(("pill", x, flow.y + 2, wide, PILL, need, small - 1))
            y = flow.bottom
        if step.note:
            y = lines(step.note, "muted", small, y + 2)
    return y + PAD - 2, tuple(items)


def plan(steps, width, size, line_height, measure, wrap):
    """``layout`` of ``steps``, kept while the same card is drawn at the same size."""
    key = (id(steps), width, size, line_height)
    kept = _plans.get(key)
    if kept is not None and kept[0] is steps:
        _plans.move_to_end(key)
        return kept[1]
    made = layout(steps, width, size, line_height, measure, wrap)
    _plans[key] = (steps, made)
    while len(_plans) > MAX_PLANS:
        _plans.popitem(last=False)
    return made


def forget():
    _plans.clear()


def draw(view, items, x, top, width, height):
    """Draw a laid-out card with its top left corner at (``x``, ``top``) on the canvas (y grows upwards there)."""
    from . import ui_tokens as tk
    roles = {"text": tk.TEXT, "muted": tk.MUTED}
    view.shape(x, top - height, width, height, tk.LINE, 9)             # A 1 px rim,
    view.shape(x + 1, top - height + 1, width - 2, height - 2, tk.PANEL, 8)   # around the card's own ground.
    for item in items:
        kind = item[0]
        if kind == "text":
            _, left, base, value, size, role = item
            view.text(value, x + left, top - base, size, roles[role])
        elif kind == "cap":   # A key cap: a raised key with its rim, the key's own text on it.
            _, left, y, wide, high, value, size = item
            view.shape(x + left, top - y - high, wide, high, tk.LINE_HOVER, 6)
            view.shape(x + left + 1, top - y - high + 2, wide - 2, high - 3, tk.INSET_RAISED, 5)
            view.text(value, x + left + CAP_PAD, top - (y + high / 2 + size * 0.36) + 0.5, size, tk.TEXT)
        elif kind == "pill":
            _, left, y, wide, high, value, size = item
            view.shape(x + left, top - y - high, wide, high, tk.NEUTRAL_SOFT, high / 2)
            view.text(value, x + left + PILL_PAD, top - (y + high / 2 + size * 0.36), size, tk.MUTED)
        elif kind == "next":
            view.icon("next", x + item[1], top - item[2], tk.MUTED)
        elif kind == "rule":
            view.shape(x + item[1], top - item[2] - 1, item[3], 1, tk.LINE)
