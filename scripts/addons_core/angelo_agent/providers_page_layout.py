# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layout of Preferences > AI Providers: one centred column of sections, drawn on Angelo's canvas.

Pure: takes the page *model* (``providers_page_model.build``: every string already translated) and a
measuring view, and returns a ``card_kit.Plan`` in region pixels (y up) whose ops ``providers_page_draw``
draws and whose hits the input modal tests. No ``bpy``, so it is host-tested with a fake view.

API: ``layout(view, model, region_w, top, scale, ui) -> Plan`` (``top`` is the page's top edge in region
pixels: the View2D scroll moves it); ``plan.card[3]`` is the page height the panel's spacer reserves.
"""

from . import card_kit as kit
from .card_kit import AMBER, AMBER_SOFT, CARD, LINE, MUTED, PANEL, PANEL_HOVER, TEAL, TEAL_SOFT, TEXT, Plan, _state
from .ui_tokens import CORAL, CORAL_SOFT, SUBTITLE

MAX_WIDTH = 720
NARROW = 540            # Below this column width the readiness chip moves under the name.
PAGE_BG = (0.074, 0.080, 0.096, 1)   # The content region behind the column (one step below CARD).
BANNER_ASPECT = 480 / 1520   # welcome_screen.BANNER_ASPECT (the banner art's height over width).
TONES = {"teal": (TEAL, TEAL_SOFT), "amber": (AMBER, AMBER_SOFT), "coral": (CORAL, CORAL_SOFT),
         "muted": (MUTED, (1, 1, 1, 0.07))}


class Page:
    """Top-down cursor over the column. Items are card-relative (x from the column's left, y down)."""

    def __init__(self, view, scale, width, ui):
        self.view, self.s, self.ui, self.width = view, scale, ui, width
        self.y, self.items, self.hits, self.focus = 0.0, [], [], []

    def measure(self, text, size, mono=False):
        return self.view.measure(text, size * self.s, self.view._mono if mono else self.view._font)

    def lines(self, text, width, size):
        return [line.rstrip("\n") for line, _ in self.view.wrap(text, max(40, int(width)), round(size * self.s, 2), self.view._font)]

    def text_block(self, text, x, top, width, size, color, leading=1.45):
        """Wrapped text from ``top``; returns its height."""
        height = size * self.s * leading
        rows = self.lines(text, width, size) if text else []
        for index, line in enumerate(rows):
            if line:
                self.items.append(("text", line, x, top + index * height, size * self.s, height, color))
        return len(rows) * height

    def rect(self, x, top, w, h, color, radius=0):
        item = ["rect", x, top, w, h, color, radius * self.s]
        self.items.append(item)
        return item

    def hit(self, x, top, w, h, action, value, enabled=True):
        if enabled:
            self.hits.append((x, top, w, h, action, value))
            self.focus.append((action, value))

    def button(self, x, top, label, style, action, value, enabled=True, size=13, height=30, icon=None):
        s = self.s
        w = self.measure(label, size) + 2 * 15 * s + (18 * s if icon else 0)
        self.items.append(("button", x, top, w, height * s, label, style, action, value, enabled, size * s, icon))
        self.hit(x, top, w, height * s, action, value, enabled)
        return w

    def button_width(self, label, size=13, icon=None):
        return self.measure(label, size) + 2 * 15 * self.s + (18 * self.s if icon else 0)

    def icon_button(self, x, top, glyph, action, value, tip=""):
        side = 30 * self.s
        self.items.append(("iconbtn", x, top, side, glyph, action, value))
        self.hit(x, top, side, side, action, value)
        return side

    def chip(self, x, top, label, tone, size=11.5, spinner=False):
        """A pill; returns its width."""
        s = self.s
        w = self.measure(label, size) + 2 * 9 * s + (14 * s if tone == "teal" or spinner else 0)
        self.items.append(("chip", x, top, w, 21 * s, label, tone, size * s, spinner))
        return w

    def chip_width(self, label, tone, size=11.5, spinner=False):
        return self.measure(label, size) + 2 * 9 * self.s + (14 * self.s if tone == "teal" or spinner else 0)


def layout(view, model, region_w, top, scale, ui=None):
    ui = ui or kit.UiState()
    s = scale
    width = min(MAX_WIDTH * s, region_w - 2 * 20 * s)
    page = Page(view, s, width, ui)
    page.y = 24 * s
    _hero(page, model["hero"])
    if model.get("attention"):
        _attention(page, model["attention"])
    if model.get("team"):
        _team(page, model["team"])
    for section in model.get("sections", ()):
        _section(page, section)
    if model.get("trust"):
        page.y += 6 * s
        page.y += page.text_block(model["trust"], 20 * s, page.y, width - 40 * s, 12, MUTED)
    page.y += 32 * s
    return _place(page, (region_w - width) / 2, top, region_w)


# -- sections ------------------------------------------------------------------------------------------------

def _hero(page, hero):
    """The title over Angelo's raven banner (the welcome screen's art, like the About card); a plain card and
    the drawn mark without the art (an undownloaded asset)."""
    s, w = page.s, page.width
    top, pad = page.y, 28 * s
    panel = page.rect(0, top, w, 0, CARD, 16)
    art_h, text_w, y = 0, w - 2 * pad - (90 * s if w > 460 * s else 0), top + pad
    if hero.get("banner"):
        inset = 3 * s
        art_h = (w - 2 * inset) * BANNER_ASPECT
        page.items.append(("paint", "banner", inset, top + inset, w - 2 * inset, art_h))
        if w >= 560 * s:   # The banner's left half is dark and text-free: the title sits there.
            text_w, y = w * 0.5 - pad, top + max(pad, art_h * 0.14)
        else:              # Too narrow to share: the art on top, the text under it.
            y, art_h = top + art_h + 2 * inset + 16 * s, 0
    y += page.text_block(hero["title"], pad, y, text_w, 28, TEXT, 1.3) + 6 * s
    y += page.text_block(hero["purpose"], pad, y, text_w, 14, SUBTITLE) + 16 * s
    if hero.get("add"):
        page.button(pad, y, hero["add"], hero.get("add_style", "primary"), hero.get("add_action", "add"), "", height=36, size=13.5,
                    icon=None if hero.get("add_action") else "plus")
        y += 36 * s
    y = max(y + pad, top + art_h + 6 * s)
    if not hero.get("banner") and w > 460 * s:
        page.items.append(("mark", w - pad - 72 * s, top + pad, 72 * s))
    panel[4] = y - top
    page.items.insert(page.items.index(panel), ("shadow", 0, top, w, y - top))
    page.y = y + 18 * s


def _attention(page, notices):
    s, w = page.s, page.width
    for notice in notices:
        tone = notice.get("tone", "amber")
        color, soft = TONES[tone]
        top, pad = page.y, 16 * s
        box = page.rect(0, top, w, 0, PANEL, 12)
        edge = page.rect(0, top, 3 * s, 0, color, 1.5)
        x = pad + 26 * s
        page.items.append(("glyph", notice.get("glyph", "alert"), pad, top + pad, 16 * s, color))
        y = top + pad
        y += page.text_block(notice["title"], x, y, w - x - pad, 14, TEXT) + 2 * s
        for line in notice.get("lines", ()):
            y += page.text_block(line, x, y, w - x - pad, 12.5, MUTED) + 2 * s
        if notice.get("locks"):
            cx = x
            y += 4 * s
            for label in notice["locks"]:
                cw = page.chip_width(label, "amber")
                if cx + cw > w - pad and cx > x:
                    cx, y = x, y + 26 * s
                page.chip(cx, y, label, "amber")
                cx += cw + 6 * s
            y += 25 * s
        if notice.get("action"):
            label, action, value = notice["action"]
            y += 4 * s
            page.button(x, y, label, "secondary", action, value, size=12.5, height=28)
            y += 28 * s
        y += pad
        box[4] = edge[4] = y - top
        page.y = y + 10 * s
    page.y += 8 * s


def _team(page, team):
    s, w = page.s, page.width
    if team.get("slim"):
        top, pad = page.y, 16 * s
        box = page.rect(0, top, w, 0, PANEL, 14)
        page.items.append(("tile", pad, top + pad, 36 * s, "", "people", None))
        right = page.button_width(team["button"][0], icon="external") if team.get("button") else 0
        if team.get("chip"):
            right = page.chip_width(team["chip"], "amber")
        x, text_w = pad + 50 * s, w - pad * 2 - 50 * s - right - 12 * s
        y = top + pad - 2 * s
        y += page.text_block(team["title"], x, y, text_w, 14, TEXT) + 1 * s
        for line in team.get("lines", ()):
            y += page.text_block(line, x, y, text_w, 12.5, MUTED)
        for line in team.get("problems", ()):
            y += page.text_block(line, x, y, text_w, 12.5, AMBER)
        y = max(y, top + pad + 36 * s) + pad
        if team.get("button"):
            label, action, value = team["button"]
            page.button(w - pad - right, top + (y - top - 30 * s) / 2, label, "secondary", action, value, icon="external")
        elif team.get("chip"):
            page.chip(w - pad - right, top + pad + 6 * s, team["chip"], "amber")
        box[4] = y - top
        page.y = y + 26 * s
        return
    _section(page, team)


def _section(page, section):
    s, w = page.s, page.width
    page.y += 4 * s
    top = page.y
    add = section.get("add")
    add_w = page.button_width(add[0], icon="plus") if add else 0
    page.y += page.text_block(section["title"], 0, page.y, w - add_w - 12 * s, 17, TEXT, 1.3) + 3 * s
    if section.get("description"):
        page.y += page.text_block(section["description"], 0, page.y, w - add_w - 12 * s, 12.5, MUTED)
    if add:
        page.button(w - add_w, top + 6 * s, add[0], "ghost", add[1], add[2], icon="plus")
    page.y += 12 * s
    rows = section.get("rows", ())
    if section.get("chips"):
        x = 0
        for label, action, value in section["chips"]:
            bw = page.button_width(label, 12.5, icon="plus")
            if x + bw > w and x:
                x, page.y = 0, page.y + 36 * s
            x += page.button(x, page.y, label, "secondary", action, value, size=12.5, icon="plus") + 8 * s
        page.y += 30 * s + 10 * s
    if not rows:
        if section.get("empty"):
            page.y += page.text_block(section["empty"], 0, page.y, w, 12.5, MUTED) + 8 * s
        page.y += 22 * s
        return
    box_top = page.y
    box = page.rect(0, box_top, w, 0, PANEL, 16)
    y = box_top
    for index, row in enumerate(rows):
        if index:
            page.rect(18 * s, y, w - 36 * s, 1 * s, LINE)
        y = _row(page, row, y)
    box[4] = y - box_top
    page.y = y + 30 * s


def _row(page, row, top):
    kind = row.get("kind", "connection")
    if kind == "switch":
        return _switch_row(page, row, top)
    s, w, pad = page.s, page.width, 18 * page.s
    narrow = w < NARROW * s
    has_tile = row.get("tile") is not None or row.get("glyph")
    x = pad + (54 * s if has_tile else 0)
    if has_tile:
        page.items.append(("tile", pad, top + pad, 40 * s, row.get("tile") or "", row.get("glyph"), row.get("dot")))
    # Right side: chip (wide pages), then the row's buttons.
    buttons = row.get("buttons", ())
    right = sum(30 * s + 4 * s for _ in row.get("icons", ())) + sum(page.button_width(b[0]) + 8 * s for b in buttons)
    chip = row.get("chip")
    chip_w = page.chip_width(chip[0], chip[1], spinner=chip[2]) if chip else 0
    if chip and not narrow:
        right += chip_w + 10 * s
    text_w = w - x - pad - right
    y = top + pad - 1 * s
    name_w = min(page.measure(row["name"], 15), text_w)
    name_rows = page.lines(row["name"], text_w, 15)
    y += page.text_block(row["name"], x, y, text_w, 15, TEXT, 1.35)
    if row.get("tag"):
        tag_w = page.chip_width(row["tag"], "muted", 11)
        if len(name_rows) == 1 and name_w + 8 * s + tag_w <= text_w:
            page.chip(x + name_w + 8 * s, y - 15 * s * 1.35 + 0.5 * s, row["tag"], "muted", 11)
        else:
            page.chip(x, y + 2 * s, row["tag"], "muted", 11)
            y += 25 * s
    if chip and narrow:
        page.chip(x, y + 3 * s, chip[0], chip[1], spinner=chip[2])
        y += 28 * s
    for line in row.get("lines", ()):
        y += page.text_block(line, x, y, text_w if not narrow else w - x - pad, 12.5, MUTED) + 1 * s
    facts = row.get("facts", ())
    if facts:
        y += 6 * s
        fx, line = x, 20 * s   # A line of facts is as tall as its tallest, wrapped, fact (break-it P1-21 M1).
        for glyph, label, tone in facts:
            fw = 20 * s + page.measure(label, 12)
            if fx + fw > w - pad and fx > x:
                fx, y, line = x, y + line + 2 * s, 20 * s
            color = TONES[tone][0] if tone != "muted" else MUTED
            page.items.append(("glyph", glyph, fx, y + 2.5 * s, 12 * s, color))
            line = max(line, page.text_block(label, fx + 18 * s, y, w - fx - pad, 12, color) + 2 * s)
            fx += fw + 16 * s
        y += line
    if row.get("choices"):   # One value of a few (Workers at once): a button each below the text, the chosen one primary.
        y += 10 * s
        cx = x
        for label, action, value, chosen in row["choices"]:
            bw = page.button_width(label, 12.5)
            if cx + bw > w - pad and cx > x:   # Wraps on a narrow page.
                cx, y = x, y + 36 * s
            page.button(cx, y, label, "primary" if chosen else "secondary", action, value, size=12.5, height=28)
            cx += bw + 8 * s
        y += 28 * s
    if row.get("fix"):
        label, action, value = row["fix"]
        y += 8 * s
        page.button(x, y, label, "secondary", action, value, size=12.5, height=28)
        y += 28 * s
    bottom = max(y, top + pad + 40 * s) + pad
    # Right side, top-aligned with the name.
    rx = w - pad
    for glyph, action, value in reversed(row.get("icons", ())):
        rx -= 30 * s
        page.icon_button(rx, top + pad - 5 * s, glyph, action, value)
        rx -= 4 * s
    for label, action, value, style in reversed(buttons):
        bw = page.button_width(label)
        rx -= bw
        page.button(rx, top + pad - 5 * s, label, style, action, value)
        rx -= 8 * s
    if chip and not narrow:
        page.chip(rx - chip_w - 2 * s, top + pad + 0.5 * s, chip[0], chip[1], spinner=chip[2])
    return bottom


def _switch_row(page, row, top):
    s, w, pad = page.s, page.width, 18 * page.s
    sw, sh = 40 * s, 22 * s
    text_w = w - 2 * pad - sw - 20 * s
    y = top + pad - 1 * s
    title_rows = page.lines(row["name"], text_w, 14)
    title_w = page.measure(row["name"], 14) if len(title_rows) == 1 else text_w
    y += page.text_block(row["name"], pad, y, text_w, 14, TEXT if row.get("enabled", True) else MUTED, 1.4)
    if row.get("chip"):
        label, tone, spin = row["chip"]
        page.chip(pad + title_w + 8 * s, y - 14 * s * 1.4 + 1 * s, label, tone, spinner=spin)
    for line in row.get("lines", ()):
        y += page.text_block(line, pad, y, text_w, 12.5, MUTED) + 1 * s
    for glyph, label, tone in row.get("facts", ()):
        y += 6 * s
        color = MUTED if tone == "muted" else TONES[tone][0]
        page.items.append(("glyph", glyph, pad, y + 2.5 * s, 12 * s, color))
        y += page.text_block(label, pad + 18 * s, y, text_w - 18 * s, 12, color)
    if row.get("lock"):
        y += 6 * s
        page.items.append(("glyph", "lock", pad, y + 2.5 * s, 12 * s, AMBER))
        y += page.text_block(row["lock"], pad + 18 * s, y, text_w - 18 * s, 12, AMBER)
    if row.get("warning"):
        y += 6 * s
        page.items.append(("glyph", "alert", pad, y + 2.5 * s, 12 * s, AMBER))
        y += page.text_block(row["warning"], pad + 18 * s, y, text_w - 18 * s, 12, AMBER)
    enabled = row.get("enabled", True)
    page.items.append(("switch", w - pad - sw, top + pad, sw, sh, bool(row.get("on")), row["action"], row.get("value"), enabled))
    page.hit(w - pad - sw - 6 * s, top + pad - 6 * s, sw + 12 * s, sh + 12 * s, row["action"], row.get("value"), enabled)
    return max(y, top + pad + sh) + pad


# -- placing -------------------------------------------------------------------------------------------------

def _place(page, x0, y_top, region_w):
    s, height = page.s, page.y
    plan = Plan(scale=s, card=(x0, y_top - height, page.width, height))
    ops, ui = plan.ops, page.ui

    def rect(x, top, w, h):
        return (x0 + x, y_top - top - h, w, h)

    ops.append(("page_bg",))
    for item in page.items:
        kind = item[0]
        if kind == "rect":
            _, x, top, w, h, color, radius = item
            ops.append(("shape", rect(x, top, w, h), color, radius))
            if color is PANEL or color is CARD:
                ops.insert(len(ops) - 1, ("shape", (x0 + x - 1, y_top - top - h - 1, w + 2, h + 2), kit.EDGE, radius + 1))
        elif kind == "shadow":
            _, x, top, w, h = item
            for depth, alpha in ((8, 0.05), (4, 0.08)):
                d = depth * s
                ops.append(("shape", (x0 + x - d, y_top - top - h - d - 2 * s, w + 2 * d, h + 2 * d), (0, 0, 0, alpha), 16 * s + d))
        elif kind == "text":
            _, line, x, top, size, leading, color = item
            ops.append(("text", line, x0 + x, y_top - top - leading / 2 - 0.36 * size, size, color))
        elif kind == "button":
            _, x, top, w, h, label, style, action, value, enabled, size, icon = item
            ops.append(("pbutton", rect(x, top, w, h), label, style, _state(ui, action, value, enabled), size, icon))
        elif kind == "iconbtn":
            _, x, top, side, glyph, action, value = item
            ops.append(("iconbtn", rect(x, top, side, side), glyph, _state(ui, action, value, True)))
        elif kind == "chip":
            _, x, top, w, h, label, tone, size, spinner = item
            ops.append(("chip", rect(x, top, w, h), label, tone, size, spinner))
        elif kind == "tile":
            _, x, top, side, letter, glyph, dot = item
            ops.append(("tile", rect(x, top, side, side), letter, glyph, dot))
        elif kind == "glyph":
            _, name, x, top, size, color = item
            ops.append(("glyph", name, rect(x, top, size, size), color))
        elif kind == "switch":
            _, x, top, w, h, on, action, value, enabled = item
            ops.append(("switch", rect(x, top, w, h), on, _state(ui, action, value, enabled)))
        elif kind == "paint":
            _, name, x, top, w, h = item
            ops.append(("paint", name, rect(x, top, w, h)))
        elif kind == "mark":
            _, x, top, side = item
            ops.append(("mark", rect(x, top, side, side)))
    for x, top, w, h, action, value in page.hits:
        plan.hits.append((rect(x, top, w, h), action, value))
    plan.focus_order = list(page.focus)
    plan.animated = any(op[0] == "chip" and op[5] for op in ops)
    return plan


__all__ = ["layout", "MAX_WIDTH", "PAGE_BG", "TONES"]
