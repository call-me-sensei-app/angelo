# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layout of Angelo's notice cards: the update notice, What's New and the crash dialog.

Same visual language as the welcome screen (``welcome_screen.py``: dark card,
teal accent, rounded shapes) but a different job: small, text-led cards with
real buttons whose hover, pressed, focus and disabled states are designed, a
checkbox, a disclosure row and a scrolling text box. This module only **lays
out**: it turns a description into a ``Plan`` (drawing operations and clickable
areas in region pixels); ``card_draw.py`` draws it and ``card_host.py`` owns
input. It never touches Blender data, so layout is host-tested with a fake view.

Layout is written top-down (``y`` grows downward from the card's top edge) and
placed into the region at the end (``Builder.place``). Text is measured with the
chat's font and wrapped, never truncated; the card is as tall as its content and
shrinks as a whole when the region is small. Every string reaching ``Builder``
is already translated by the caller (``ui_text.iface_``), whole sentences with
named placeholders.
"""

from dataclasses import dataclass, field

# The welcome screen's palette (a host test pins these to welcome_screen.py).
CARD = (0.090, 0.098, 0.118, 1)
PANEL = (0.122, 0.133, 0.157, 1)
PANEL_HOVER = (0.150, 0.163, 0.192, 1)
LINE = (0.200, 0.215, 0.245, 1)
TEXT = (0.950, 0.960, 0.970, 1)
MUTED = (0.620, 0.650, 0.690, 1)
TEAL = (0.169, 0.769, 0.749, 1)
TEAL_SOFT = (0.169, 0.769, 0.749, 0.22)
INK = (0.035, 0.055, 0.065, 1)
SCRIM = (0.020, 0.024, 0.030, 0.66)
# Added here: states and the warning accent.
TEAL_HOVER = (0.24, 0.85, 0.82, 1)
TEAL_PRESSED = (0.12, 0.60, 0.58, 1)
LINE_HOVER = (0.27, 0.29, 0.33, 1)
LINE_PRESSED = (0.15, 0.16, 0.19, 1)
FOCUS = (0.62, 0.92, 0.90, 1)
AMBER = (0.98, 0.72, 0.28, 1)
AMBER_SOFT = (0.98, 0.72, 0.28, 0.20)
EDGE = (1, 1, 1, 0.09)

STYLES = ("primary", "secondary", "ghost", "destructive")   # destructive: coral, for delete/remove/sign out

from .ui_kit_builder import KitBuilder  # noqa: E402  (after the palette: ui_tokens reads it from here)


@dataclass
class Plan:
    """What to draw and where to click. Coordinates are region pixels, y up."""

    ops: list = field(default_factory=list)
    hits: list = field(default_factory=list)          # [(rect, action, value)]
    focus_order: list = field(default_factory=list)   # [(action, value)] in Tab order (enabled controls only)
    scroll: dict = field(default_factory=dict)        # key -> {"rect", "content", "visible"}
    card: tuple = (0, 0, 0, 0)
    scale: float = 1.0


@dataclass
class UiState:
    """Pointer and keyboard state a card is drawn with."""

    hover: tuple = None     # (action, value) under the pointer
    pressed: tuple = None   # (action, value) the pointer went down on
    focus: tuple = None     # (action, value) with keyboard focus (shown only after the keyboard was used)
    scroll: dict = field(default_factory=dict)   # scroll box key -> offset in pixels
    time: float = 0.0       # for the spinner


class BaseCard:
    """What ``card_host`` needs from a card. Subclasses set ``kind`` and ``priority`` and implement ``layout`` and ``act``."""

    kind = "card"
    modal = True               # Takes every event over the 3D view (False: only its own controls).
    priority = 0               # Higher shows first.
    animated = False           # True while it needs continuous redraws (a spinner).
    cancel_action = "close"    # Escape, and a click outside when ``outside_dismisses``.
    default_action = None      # Return with nothing focused.
    outside_dismisses = False
    retry_when_lost = False    # Shown again when its 3D view disappears (a layout change).

    def version(self):
        """Changes whenever the card's content does, so the host redraws its cached layout."""
        return 0

    def layout(self, view, region_w, region_h, scale, ui):
        raise NotImplementedError

    def act(self, action, value, host):
        raise NotImplementedError

    def opened(self, host):
        pass

    def closed(self, how):
        pass


def hit_test(plan, x, y):
    """The ``(action, value)`` under a point (later controls win), or None."""
    for (rx, ry, rw, rh), action, value in reversed(plan.hits):
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return action, value
    return None


def inside(rect, x, y):
    return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]


def scroll_box_at(plan, x, y):
    return next((key for key, box in plan.scroll.items() if inside(box["rect"], x, y)), None)


def clamp_scroll(plan, key, offset):
    box = plan.scroll[key]
    return max(0.0, min(offset, max(0.0, box["content"] - box["visible"])))


class Builder(KitBuilder):
    """Collects a card's content top-down, then places it in a region (``KitBuilder`` adds ``ui_kit`` components)."""

    def __init__(self, view, scale, width, ui=None, padding=26):
        self.view, self.s, self.ui = view, scale, ui or UiState()
        self.width = width
        self.pad = padding * scale
        self.inner = width - 2 * self.pad
        self.y = self.pad
        self.items = []          # top-down items: (kind, ...) with card-relative x and top y
        self.hits = []           # (x, y_top, w, h, action, value) card-relative
        self.focus = []
        self.boxes = {}

    # -- measuring -----------------------------------------------------------------------------
    def measure(self, text, size, mono=False):
        return self.view.measure(text, size, self.view._mono if mono else self.view._font)

    def lines(self, text, width, size, mono=False):
        font = self.view._mono if mono else self.view._font
        # Measured at the size it is drawn at (13.5 px, not 13): a translation filling the width must not overflow.
        return [line.rstrip("\n") for line, _ in self.view.wrap(text, max(40, int(width)), round(size, 2), font)]

    # -- content -------------------------------------------------------------------------------
    def gap(self, pixels):
        self.y += pixels * self.s

    def paragraph(self, text, size, color=MUTED, x=None, width=None, leading=1.5, mono=False):
        """Wrapped text at the cursor; returns the number of lines."""
        x = self.pad if x is None else x
        lines = self.lines(text, width or self.inner, size * self.s, mono)
        height = size * self.s * leading
        for line in lines:
            if line:
                self.items.append(("text", line, x, self.y, size * self.s, height, color, mono))
            self.y += height
        return len(lines)

    def heading(self, text, size=21, color=TEXT, x=None, width=None):
        return self.paragraph(text, size, color, x, width, leading=1.3)

    def bullets(self, texts, size=13.5, color=MUTED, dot=TEAL):
        """Bulleted lines with a teal dot; the dot is drawn, not a glyph."""
        indent = 20 * self.s
        for text in texts:
            top = self.y
            count = self.paragraph(text, size, color, x=self.pad + indent, width=self.inner - indent)
            self.items.append(("dot", self.pad + 5 * self.s, top + size * self.s * 0.75, 3.2 * self.s, dot))
            self.y += 4 * self.s if count else 0

    def icon(self, kind, x, y, size):
        self.items.append(("icon", kind, x, y, size * self.s))

    def rule(self):
        self.items.append(("rule", self.pad, self.y, self.inner))
        self.y += 1 * self.s

    def buttons(self, specs, align="right", size=13.5, height=36, left=()):
        """A row of buttons ``[(action, value, label, style, enabled)]``; rows wrap if they do not fit.

        ``left`` buttons start at the left edge of the same row when everything fits on one line.
        """
        s, gap, padx, h = self.s, 10 * self.s, 18 * self.s, height * self.s

        def widths(group):
            return [(spec, self.measure(spec[2], size * s) + 2 * padx) for spec in group]

        def row_width(row):
            return sum(w for _, w in row) + gap * max(0, len(row) - 1)

        left_row, rows = widths(left), []
        sized = widths(specs)
        if left_row and row_width(left_row) + gap + row_width(sized) <= self.inner:
            rows.append((left_row, sized))
        else:
            rows += [(left_row, [])] if left_row else []
            row, used = [], 0
            for spec, width in sized:
                if row and used + gap + width > self.inner:
                    rows.append(([], row))
                    row, used = [], 0
                row.append((spec, width))
                used += width + (gap if len(row) > 1 else 0)
            rows.append(([], row))
        for left_part, right_part in rows:
            x = self.pad
            for item in left_part:
                x = self._button(item, x, size, h)
            x = self.pad + (self.inner - row_width(right_part) if align == "right" else 0)
            for item in right_part:
                x = self._button(item, x, size, h)
            self.y += h + gap
        self.y -= gap

    def _button(self, item, x, size, h):
        (action, value, label, style, enabled), width = item
        self.items.append(("button", x, self.y, width, h, label, style, action, value, enabled, size * self.s))
        if enabled:
            self.hits.append((x, self.y, width, h, action, value))
            self.focus.append((action, value))
        return x + width + 10 * self.s

    def checkbox(self, action, value, label, checked, size=13.5, enabled=True):
        s, box = self.s, 20 * self.s
        lines = self.lines(label, self.inner - box - 12 * s, size * s)
        height = max(box, len(lines) * size * s * 1.45)
        self.items.append(("checkbox", self.pad, self.y, box, checked, action, value, enabled))
        for index, line in enumerate(lines):
            self.items.append(("text", line, self.pad + box + 12 * s, self.y + index * size * s * 1.45 + 1 * s,
                               size * s, size * s * 1.45, TEXT if enabled else MUTED, False))
        if enabled:
            self.hits.append((self.pad, self.y, self.inner, height, action, value))
            self.focus.append((action, value))
        self.y += height

    def disclosure(self, action, label, expanded, size=13.5):
        s = self.s
        height = size * s * 1.7
        width = self.measure(label, size * s) + 22 * s
        self.items.append(("disclosure", self.pad, self.y, width, height, label, expanded, action, size * s))
        self.hits.append((self.pad, self.y, width, height, action, None))
        self.focus.append((action, None))
        self.y += height

    def scrollbox(self, key, content, height, size=11.5, mono=True):
        """A clipped, wheel-scrollable text area: the crash report as it will be sent, or the full release notes.

        ``content`` is text, shown line for line (wrapped), or ``[(style, text)]`` with style ``heading`` (bright,
        a blank line before it) or ``item`` (a teal dot and a hanging indent).
        """
        s, pad = self.s, 12 * self.s
        box_h, width, line_h = height * s, self.inner - 2 * pad - 10 * s, size * s * 1.5
        if isinstance(content, str):
            rows = [(line, 0.0, MUTED, False) for line in self.lines(content, width, size * s, mono)]
        else:
            rows, indent = [], 16 * s
            for style, value in content:
                if style == "heading":
                    rows += [("", 0.0, MUTED, False)] if rows else []
                    rows += [(line, 0.0, TEXT, False) for line in self.lines(value, width, size * s, mono)]
                else:
                    rows += [(line, indent, MUTED, index == 0)
                             for index, line in enumerate(self.lines(value, width - indent, size * s, mono))]
        total = len(rows) * line_h + 2 * pad
        offset = max(0.0, min(self.ui.scroll.get(key, 0.0), max(0.0, total - box_h)))
        self.boxes[key] = {"y": self.y, "x": self.pad, "w": self.inner, "h": box_h, "content": total, "offset": offset}
        self.items.append(("scrollbox", key, self.pad, self.y, self.inner, box_h, rows, line_h, offset, size * s, mono, pad))
        self.y += box_h

    def close_button(self, action, size=28):
        """The round x at the card's top-right corner."""
        side = size * self.s
        x, top = self.width - side - 12 * self.s, 12 * self.s
        self.items.append(("closebtn", x, top, side, action))
        self.hits.append((x, top, side, side, action, None))
        self.focus.append((action, None))

    def spinner(self, x, y, radius):
        self.items.append(("spinner", x, y, radius * self.s))

    def progress(self, fraction, height=6):
        """A full-width bar: a dark track with a teal fill for ``fraction`` (0 to 1) of it."""
        h = height * self.s
        self.items.append(("bar", self.pad, self.y, self.inner, h, max(0.0, min(1.0, fraction))))
        self.y += h

    # -- placing -------------------------------------------------------------------------------
    def height(self):
        return self.y + self.pad

    def place(self, x0, y_top, modal, region):
        """The ``Plan`` with the card's top-left corner at ``(x0, y_top)`` (region pixels, y up)."""
        s, height = self.s, self.height()
        plan = Plan(scale=s, card=(x0, y_top - height, self.width, height))
        ops, ui = plan.ops, self.ui
        if modal:
            ops.append(("scrim", (0, 0, region[0], region[1])))
        for depth, alpha in ((9, 0.05), (5, 0.08), (2, 0.12)):
            d = depth * s
            ops.append(("shape", (x0 - d, y_top - height - d - 2 * s, self.width + 2 * d, height + 2 * d), (0, 0, 0, alpha), 16 * s + d))
        ops.append(("shape", (x0 - 1, y_top - height - 1, self.width + 2, height + 2), EDGE, 17 * s))
        ops.append(("shape", plan.card, CARD, 16 * s))

        def rect(x, top, w, h):
            return (x0 + x, y_top - top - h, w, h)

        for item in self.items:
            kind = item[0]
            if kind == "text":
                _, line, x, top, size, leading, color, mono = item
                baseline = y_top - top - leading / 2 - 0.36 * size
                ops.append(("mono" if mono else "text", line, x0 + x, baseline, size, color))
            elif kind == "dot":
                _, x, top, r, color = item
                ops.append(("shape", (x0 + x - r, y_top - top - r, 2 * r, 2 * r), color, r))
            elif kind == "icon":
                _, name, x, top, size = item
                ops.append(("icon", name, rect(x, top, size, size)))
            elif kind == "rule":
                _, x, top, w = item
                ops.append(("shape", rect(x, top, w, 1 * s), LINE, 0))
            elif kind == "button":
                _, x, top, w, h, label, style, action, value, enabled, size = item
                state = _state(ui, action, value, enabled)
                ops.append(("button", rect(x, top, w, h), label, style, state, size))
            elif kind == "closebtn":
                _, x, top, side, action = item
                ops.append(("closebtn", rect(x, top, side, side), _state(ui, action, None, True)))
            elif kind == "checkbox":
                _, x, top, box, checked, action, value, enabled = item
                ops.append(("checkbox", rect(x, top, box, box), checked, _state(ui, action, value, enabled)))
            elif kind == "disclosure":
                _, x, top, w, h, label, expanded, action, size = item
                ops.append(("disclosure", rect(x, top, w, h), label, expanded, _state(ui, action, None, True), size))
            elif kind == "scrollbox":
                self._place_scrollbox(plan, item, rect)
            elif kind == "spinner":
                _, x, top, r = item
                ops.append(("spinner", x0 + x, y_top - top, r, ui.time))
            elif kind == "bar":
                _, x, top, w, h, fraction = item
                ops.append(("shape", rect(x, top, w, h), PANEL, h / 2))
                if fraction > 0:
                    ops.append(("shape", rect(x, top, max(h, w * fraction), h), TEAL, h / 2))
            elif kind == "kit":
                _, name, x, top, w, h, params = item
                ops.append(("kit", name, rect(x, top, w, h), dict(params, **self._kit_live(name, ui)),
                            _kit_snapshot(ui), s))
        overlays = getattr(self, "_overlays", ())
        for (_, name, x, top, w, h, params), _hits in overlays:   # Open popovers: drawn last, hit first.
            ops.append(("kit", name, rect(x, top, w, h), dict(params), _kit_snapshot(ui), s))
        for x, top, w, h, action, value in self.hits + [hit for _, hits in overlays for hit in hits]:
            plan.hits.append((rect(x, top, w, h), action, value))
        plan.focus_order = list(self.focus)
        return plan

    def _place_scrollbox(self, plan, item, rect):
        _, key, x, top, w, h, rows, line_h, offset, size, mono, pad = item
        box = rect(x, top, w, h)
        plan.ops.append(("shape", box, PANEL, 10 * self.s))
        plan.ops.append(("clip", (box[0] + 1, box[1] + 1, box[2] - 2, box[3] - 2)))
        first = max(0, int((offset - pad) // line_h))
        last = min(len(rows), int((offset + h - pad) // line_h) + 2)
        for index in range(first, last):
            line, indent, color, dot = rows[index]
            middle = box[1] + h - pad - index * line_h + offset - line_h / 2
            if dot:
                r = 2.6 * self.s
                plan.ops.append(("shape", (box[0] + pad + 4 * self.s - r, middle - r, 2 * r, 2 * r), TEAL, r))
            if line:
                plan.ops.append(("mono" if mono else "text", line, box[0] + pad + indent, middle - 0.36 * size, size, color))
        plan.ops.append(("unclip",))
        content = len(rows) * line_h + 2 * pad
        plan.scroll[key] = {"rect": box, "content": content, "visible": h}
        if content > h:
            track = (box[0] + w - 9 * self.s, box[1] + 6 * self.s, 4 * self.s, h - 12 * self.s)
            thumb_h = max(24 * self.s, track[3] * h / content)
            thumb_y = track[1] + (track[3] - thumb_h) * (1 - offset / max(1.0, content - h))
            plan.ops.append(("shape", track, (1, 1, 1, 0.06), 2 * self.s))
            plan.ops.append(("shape", (track[0], thumb_y, track[2], thumb_h), (1, 1, 1, 0.28), 2 * self.s))


def _kit_snapshot(ui):
    from .ui_kit import snapshot
    return snapshot(ui)


def _state(ui, action, value, enabled):
    """``(hover, pressed, focus, enabled)`` flags for the control ``(action, value)``."""
    key = (action, value)
    return (enabled and ui.hover == key, enabled and ui.pressed == key and ui.hover == key, enabled and ui.focus == key,
            enabled)


def fit(plan_builder, region_w, region_h, scale, minimum=0.6):
    """Layout callbacks use this: the scale that makes ``plan_builder(scale)`` fit the region (never below ``minimum``)."""
    plan = plan_builder(scale)
    for _ in range(4):
        if plan.card[3] <= region_h - 24 * plan.scale or plan.scale <= minimum:
            break
        scale = max(minimum, plan.scale * (region_h - 24 * plan.scale) / plan.card[3])
        plan = plan_builder(scale)
    return plan
