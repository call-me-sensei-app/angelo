# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``card_kit.Builder`` methods for the ``ui_kit`` components (a mixin, so card_kit stays small).

Each method lays a component out at the Builder's cursor (top-down, card-relative), registers its clickable
parts as hits and focus stops, and advances the cursor. Drawing happens later from the plan (``card_draw``
dispatches ``("kit", ...)`` ops to ``ui_kit.draw_op``) with the pointer and focus state of that frame.

API (all sizes at UI scale 1; strings already translated):

    b.chip(kind, label=None, x=None) -> width                     status chip at the cursor (no hit)
    b.chips([(kind, label_or_None)], gap=8)                         a wrapping row of status chips
    b.notice(kind, title, body="", actions=(), details=None, details_action=None, expanded=False, details_value=None)
    b.code_block(code, line_numbers=False, label="", copy_action=None, copy_value=None, copied=False)
    b.toggle(action, value, label, on, caption="", enabled=True)  a settings row: label start, switch end
    b.segmented(action, segments, selected, width=None, enabled=True, x=None)
                                                                    segments [(value, label[, swatch])]
    b.text_field(action, value, fs, placeholder="", label=None, enabled=True, reveal_action="reveal",
                 paste_action="paste")             fs: ui_kit_field.FieldState (the card keeps it; secret via fs.secret)
    b.select(action, value, options, selected, open=False, highlighted=None, label=None, placeholder="",
             option_action="option", enabled=True)      the popover (when open) is drawn last and opens below
    b.list_row(action, value, title, subtitle="", lead=None, trailing=None, selected=False, enabled=True)
    b.provider_tiles(action, tiles, selected, columns=None, enabled=True)   tiles [(value, letter, name, caption)]
    b.kit(name, height, params, hits=(), x=None, width=None)       any registered component, full width
"""


class KitBuilder:
    """Mixed into ``card_kit.Builder``; uses its ``view``, ``s``, ``pad``, ``inner``, ``y``, ``items``, ``hits``, ``focus``."""

    def kit(self, name, height, params, hits=(), x=None, width=None):
        """Place component ``name`` (``height`` in pixels, already scaled) and its ``[(rect, action, value)]`` hits.

        Hit rects are relative to the component's top-left corner, y down: ``(dx, dtop, w, h)``.
        """
        x = self.pad if x is None else x
        width = self.inner if width is None else width
        self.items.append(("kit", name, x, self.y, width, height, dict(params)))
        for (dx, dtop, w, h), action, value in hits:
            self.hits.append((x + dx, self.y + dtop, w, h, action, value))
            self.focus.append((action, value))
        self.y += height

    @staticmethod
    def _relative(hits, x0, top0):
        """Region-style hits laid out at ``(x0, top0)`` (y up) as component-relative ``(dx, dtop, w, h)``."""
        return [((rx - x0, top0 - (ry + rh), rw, rh), action, value) for (rx, ry, rw, rh), action, value in hits]

    def _kit_live(self, name, ui):
        """Per-frame parameters a placed component reads from the card's state (the running chip's pulse)."""
        return {"time": ui.time} if name in {"chip", "text_field", "list_row"} else {}

    def chip(self, kind, label=None, x=None):
        from . import ui_kit_status as status
        w, h = status.chip_size(self.view, kind, self.s, label)
        self.kit("chip", h, {"kind": kind, "label": label}, x=x, width=w)
        return w

    def chips(self, specs, gap=8):
        from . import ui_kit_status as status
        x, top, row_h, gap = self.pad, self.y, 0, gap * self.s
        for kind, label in specs:
            w, h = status.chip_size(self.view, kind, self.s, label)
            if x > self.pad and x + w > self.pad + self.inner:
                x, top = self.pad, top + h + gap
            self.items.append(("kit", "chip", x, top, w, h, {"kind": kind, "label": label}))
            x, row_h = x + w + gap, h
        self.y = top + row_h

    def notice(self, kind, title, body="", actions=(), details=None, details_action=None, expanded=False,
               details_value=None):
        from . import ui_kit_status as status
        params = {"kind": kind, "title": title, "body": body, "actions": tuple(actions), "details": details,
                  "expanded": expanded, "details_action": details_action, "details_value": details_value}
        height, hits = status.notice(self.view, 0, 0, self.inner, self.s, None, False, **params)
        self.kit("notice", height, params, self._relative(hits, 0, 0))

    def code_block(self, code, line_numbers=False, label="", copy_action=None, copy_value=None, copied=False):
        from . import ui_kit_status as status
        header = bool(copy_action or label)
        height = status.code_block_height(self.view, code, self.s, header)
        hits = status.code_block_hits((0, -height, self.inner, height), self.s, copy_action, copy_value, self.view)
        self.kit("code_block", height, {"code": code, "line_numbers": line_numbers, "label": label,
                                        "copy_action": copy_action, "copy_value": copy_value, "copied": copied},
                 self._relative(hits, 0, 0))

    def toggle(self, action, value, label, on, caption="", enabled=True):
        from . import ui_kit_controls as controls
        height = controls.toggle_row_height(self.view, self.inner, self.s, label, caption)
        hits = [((0, 0, self.inner, height), action, value)] if enabled else []
        self.kit("toggle", height, {"label": label, "caption": caption, "on": on, "action": action, "value": value,
                                    "enabled": enabled}, hits)

    def segmented(self, action, segments, selected, width=None, enabled=True, x=None):
        """``x``: card-relative left edge (default the padding), e.g. a control column beside its label."""
        from . import ui_kit_controls as controls, ui_tokens as tokens
        height = tokens.SEGMENTED_HEIGHT * self.s
        width = min(self.inner, width or controls.segmented_width(self.view, self.s, segments))
        hits = controls.segmented_hits((0, -height, width, height), segments, action) if enabled else []
        self.kit("segmented", height, {"segments": tuple(segments), "selected": selected, "action": action,
                                       "enabled": enabled}, self._relative(hits, 0, 0), x=x, width=width)

    def text_field(self, action, value, fs, placeholder="", label=None, enabled=True, reveal_action="reveal",
                   paste_action="paste"):
        from . import ui_kit_field as field
        if label:
            self.paragraph(label, 12.5, x=self.pad + 2 * self.s)
            self.y += 4 * self.s
        height = field.field_height(self.s, fs.error)
        hits = field.field_hits((0, -height, self.inner, height), self.s, action, value, fs.secret, reveal_action,
                                paste_action, fs.error) if enabled else []
        self.kit("text_field", height, {"fs": fs, "placeholder": placeholder, "action": action, "value": value,
                                        "enabled": enabled, "reveal_action": reveal_action,
                                        "paste_action": paste_action},
                 self._relative(hits[::-1], 0, 0))   # card_kit: the last hit wins; canvases: the first.

    def select(self, action, value, options, selected, open=False, highlighted=None, label=None, placeholder="",
               option_action="option", enabled=True):
        from . import ui_kit_select as select
        if label:
            self.paragraph(label, 12.5, x=self.pad + 2 * self.s)
            self.y += 4 * self.s
        height = 36 * self.s
        text = next((option[1] for option in options if option[0] == selected), "")
        x, top = self.pad, self.y
        self.kit("select", height, {"text": text, "placeholder": placeholder, "open": open, "action": action,
                                    "value": value, "enabled": enabled},
                 [((0, 0, self.inner, height), action, value)] if enabled else [])
        if open:
            anchor = (0, -height, self.inner, height)
            hits = select.popover_hits(anchor, options, self.s, option_action, flip=False)
            self.kit_overlays().append((("kit", "select_popover", x, top, self.inner, height,
                                         {"options": tuple(options), "selected": selected, "highlighted": highlighted,
                                          "action": option_action, "flip": False}),
                                        [(x + dx, top + dtop, w, h, a, v)
                                         for (dx, dtop, w, h), a, v in self._relative(hits, 0, 0)]))

    def kit_overlays(self):
        """Items drawn after everything else (open popovers), with their hits, which win over the content's."""
        if not hasattr(self, "_overlays"):
            self._overlays = []
        return self._overlays

    def list_row(self, action, value, title, subtitle="", lead=None, trailing=None, selected=False, enabled=True):
        from . import ui_kit_rows as rows
        height = rows.row_height(self.s, bool(subtitle))
        self.kit("list_row", height, {"title": title, "subtitle": subtitle, "lead": lead, "trailing": trailing,
                                      "selected": selected, "action": action, "value": value, "enabled": enabled},
                 [((0, 0, self.inner, height), action, value)] if enabled else [])

    def provider_tiles(self, action, tiles, selected, columns=None, enabled=True):
        from . import ui_kit_rows as rows
        tile_w, tile_h = rows.tile_size(self.s)
        gap = 10 * self.s
        columns = columns or max(1, min(3, int((self.inner + gap) // (tile_w + gap))))
        width = (self.inner - gap * (columns - 1)) / columns
        for index, (value, letter, name, caption) in enumerate(tiles):
            x = self.pad + (index % columns) * (width + gap)
            top = self.y + (index // columns) * (tile_h + gap)
            self.items.append(("kit", "provider_tile", x, top, width, tile_h,
                               {"letter": letter, "name": name, "caption": caption, "selected": value == selected,
                                "action": action, "value": value, "enabled": enabled}))
            if enabled:
                self.hits.append((x, top, width, tile_h, action, value))
                self.focus.append((action, value))
        count = -(-len(tiles) // columns)
        self.y += count * tile_h + max(0, count - 1) * gap
