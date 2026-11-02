# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The photo drop card: what a photo dropped on the 3D view becomes (1.0.8), an Angelo card (``card_host``).

It stands beside the point the photo landed on, marked with a teal ring, and lists ``photo_drop_choices.rows``: Make
a 3D model (the model, its account and the estimated price on the row, the confirmation the Media Studio Generate
dialog asks for), Use as a reference, Use as a material, Add as a picture. With several 3D models ready a picker
chooses which one bills. A choice that fails keeps the card open and says why; Cancel, Escape and the close button
leave the scene untouched. The card only presents and collects: ``photo_drop`` does the work (``run``).
"""

from . import card_kit as kit, photo_drop_choices as choices
from .ui_text import iface_, language, n_

TITLE = n_("What should this photo become?")
SEVERAL = n_("{count} photos dropped: this card uses the first one")
MODEL = n_("3D model")
COULD_NOT = n_("That didn't work")
CANCEL = n_("Cancel")
WIDTH, THUMB, GAP_FROM_POINT = 460, 56, 22
ERROR_KIND = "error"   # ui_kit_status.NOTICE_KINDS


class PhotoDropCard(kit.BaseCard):
    kind, modal, priority = "photo_drop", True, 85
    cancel_action, default_action, outside_dismisses = "cancel", None, True   # Return never bills by itself.

    def __init__(self, facts, point, run, count=1, area=None, preview=None, release=None, busy=None):
        """``point``: where the photo landed, region pixels of ``area`` (its pointer), or None to stand centred and
        unmarked (a drop on another quadrant of Quad View: ``card_host`` draws in the first); ``run(action, card)``
        does a choice and returns an error text or None; ``preview()`` gives the thumbnail painter (or None) when
        the card opens and ``release()`` frees it; ``busy()`` reads whether the chat is mid-turn, so Use as a
        reference comes back when the turn ends."""
        self.facts, self.point, self.run, self.count, self._busy = facts, point, run, count, busy
        self.area_pointer, self._preview, self._release, self.painter = area, preview, release, None
        self.picking = False   # The model picker is open.
        self.error = ""
        self.done = None       # The action that closed the card, None for a cancel.

    def target_area(self, window):
        from . import live_windows
        return next((a for a in live_windows.areas(window) if a.as_pointer() == self.area_pointer), None)

    def version(self):
        if self._busy is not None:
            self.facts.chat_busy = bool(self._busy())
        model = self.facts.model
        return (choices.key(model) if model else "", self.picking, self.error, self.facts.chat_busy, language(),
                self.painter is not None)

    def opened(self, host):
        self.painter = self._preview() if self._preview else None

    def closed(self, how):
        self.free_preview()

    def free_preview(self):
        """Before a choice changes the file, so the thumbnail's image never lands in an Undo step."""
        self.painter = None
        if self._release:
            self._release()
            self._release = None

    # -- layout ------------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        from .ui_text import is_rtl
        px, py = self.point if self.point is not None else (region_w / 2, region_h / 2)
        if is_rtl():   # Laid out left to right and mirrored as drawn (canvas_rtl), so the point is mirrored here.
            px = region_w - px

        def plan(size):
            b = self._build(view, size, region_w, ui)
            s, height, gap = b.s, b.height(), GAP_FROM_POINT * b.s
            if self.point is None:
                x, top = (region_w - b.width) / 2, min((region_h + height) / 2, region_h - 12 * s)
            else:
                x = px + gap if px + gap + b.width <= region_w - 12 * s else px - gap - b.width
                x = max(12 * s, min(x, region_w - b.width - 12 * s))
                top = max(height + 12 * s, min(py + height / 2, region_h - 12 * s))
            placed = b.place(x, top, True, (region_w, region_h))
            if self.painter is not None:
                placed.ops.append(("paint", self.painter, (x + b.pad, top - b.pad - THUMB * s, THUMB * s, THUMB * s)))
            if self.point is not None:
                placed.ops.extend(marker(px, py, s))
            return placed
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=22)
        s, facts = b.s, self.facts
        top, text_x = b.y, b.pad + THUMB * s + 14 * s
        text_w = b.width - text_x - b.pad - 30 * s
        b.paragraph(iface_(TITLE), 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(facts.name, 12.5, kit.MUTED, x=text_x, width=text_w, leading=1.4)
        b.y = max(b.y, top + THUMB * s)
        if self.count > 1:
            b.gap(8)
            b.paragraph(iface_(SEVERAL, count=self.count), 12.5, kit.MUTED)
        rows = choices.rows(facts)
        if facts.model_photo and len([m for m in facts.models if m.get("ready")]) > 1:
            b.gap(14)
            options = [(choices.key(m), iface_("{model} · {account}", model=m["name"], account=m["account"]))
                       for m in facts.models if m.get("ready")]
            b.select("model_open", None, options, choices.key(facts.model), open=self.picking, label=iface_(MODEL),
                     option_action="model_pick")
        b.gap(12)
        for row in rows:
            b.list_row(row.action, None, iface_(row.title), iface_(row.subtitle, **dict(row.fields)),
                       trailing=_trailing(row), enabled=row.enabled)
            b.gap(2)
        if self.error:
            b.gap(10)
            b.notice(ERROR_KIND, iface_(COULD_NOT), self.error)
        b.gap(14)
        b.buttons([("cancel", None, iface_(CANCEL), "secondary", True)])
        b.close_button("cancel")
        return b

    # -- input -------------------------------------------------------------------------------------------------
    def act(self, action, value, host):
        if action == "model_open":
            self.picking = not self.picking
        elif action == "model_pick":
            self.picking = False
            self.facts.model = next((m for m in self.facts.models if choices.key(m) == value), self.facts.model)
        elif action in {"cancel", "close", "dismiss"}:
            if self.picking:   # Escape or a click beside the picker closes the picker first.
                self.picking = False
                return
            host.close("done")
        elif action in {choices.MAKE_3D, choices.CONNECT, choices.REFERENCE, choices.MATERIAL, choices.PICTURE}:
            self.picking = False
            self.free_preview()
            self.error = self.run(action, self) or ""
            if not self.error:
                self.done = action
                host.close("done")


def _trailing(row):
    if row.action == choices.CONNECT:
        return ("chevron",)
    label = choices.price_label(row)
    if label is None:
        return None
    template, fields = label
    return ("meta", iface_(template, **dict(fields)))


def marker(x, y, scale):
    """Where the photo landed: a soft teal disc, a teal ring and a dot, drawn over the scrim (card ops)."""
    r, dot = 14 * scale, 2.6 * scale
    disc = (x - r, y - r, 2 * r, 2 * r)
    return [("shape", disc, kit.TEAL_SOFT, r), ("paint", _ring, disc),
            ("shape", (x - dot, y - dot, 2 * dot, 2 * dot), kit.TEAL, dot)]


def _ring(rect):
    from . import canvas_controls
    x, y, w, h = rect
    canvas_controls.ring(x, y, w, h, w / 2, max(1.5, w / 14), kit.TEAL)
