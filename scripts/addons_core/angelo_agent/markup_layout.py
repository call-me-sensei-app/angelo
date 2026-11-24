# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's chrome, laid out: the bar of modes or tools, the colour and width palette, the instructions
field with its send button, and Close (2.1.0, doc/angelo/attachment_markup.rst).

``layout`` returns rectangles in region pixels (x, y, w, h; y up) as written left to right (``canvas_rtl`` mirrors the
chrome for a right-to-left language at draw and hit time; the picture never mirrors), every control's action, value,
glyph and tooltip label, the hit list (the open palette first) and the margins the picture keeps clear of. Labels are
``n_`` constants or (template, fields) pairs, translated where drawn. Text is measured by the caller's ``measure``.
No Blender here.
"""

from . import markup_doc as md, markup_tools as mt
from .ui_text import n_

MODES = mt.MODES
EDGE, BUTTON, PAD, GAP, SEPARATOR = 16, 36, 4, 4, 12
FIELD_H, FIELD_W, ROW_GAP = 40, 560, 12
LABEL_SIZE, GLYPH_W = 13, 20

MODE_LABELS = {"markup": n_("Markup"), "comment": n_("Comment"), "remove_bg": n_("Remove BG"), "erase": n_("Erase"),
               "resize": n_("Resize")}
SCALE_LABEL = n_("{percent}%")
CARD_W, CARD_TITLE, CARD_LINE, CARD_BUTTONS = 420, 44, 22, 60
TOOL_NAMES = {"select": n_("Select and move"), "pen": n_("Pen"), "arrow": n_("Arrow"), "ellipse": n_("Ellipse"),
              "rect": n_("Rectangle"), "text": n_("Text"), "eraser": n_("Eraser"), "comment": n_("Comment")}
COLOUR_NAMES = {"red": n_("Red"), "amber": n_("Amber"), "teal": n_("Teal"), "blue": n_("Blue"), "white": n_("White"),
                "black": n_("Black")}
WIDTH_NAMES = {1: n_("Thin"), 2: n_("Medium"), 3: n_("Thick")}
KEYED = n_("{name} ({key})")
LABELS = {"palette": n_("Colour and width"), "undo": n_("Undo"), "redo": n_("Redo"), "done": n_("Done marking up"),
          "close": n_("Close"), "send": n_("Send to Angelo"), "placeholder": n_("Add instructions…"),
          "comment_hint": n_("Click to pin a comment, or drag over a region"),
          "erase_hint": n_("Brush over what to erase"), "resize_hint": n_("Drag on the picture to crop"),
          "clear": n_("Clear the brushed areas"), "erase_run": n_("Erase the brushed areas…"),
          "apply_resize": n_("Make the resized picture"), "cancel": n_("Cancel"), "remove_bg": n_("Remove BG"),
          # The viewer's fields take no input method yet (re-review of 4e712847d3b, N4): said where the person types.
          "paste_hint": n_("To write Chinese, Japanese or Korean here, paste it: input methods don't type here yet")}
KEYS = {tool: key for key, tool in mt.TOOL_KEYS.items()}
# The markup bar, left to right; None is a separator.
MARKUP_BAR = (("tool", "select"), ("tool", "pen"), ("tool", "text"), ("tool", "arrow"), ("tool", "ellipse"),
              ("tool", "rect"), None, ("palette", None), ("tool", "eraser"), None, ("undo", None), ("redo", None),
              None, ("mode", "view"))
COMMENT_BAR = (("undo", None), ("redo", None), None, ("mode", "view"))
VIEW_BAR = (("mode", "markup"), ("mode", "comment"), ("ask", "remove_bg"), ("mode", "erase"), ("mode", "resize"))
ERASE_BAR = (("width", 1), ("width", 2), ("width", 3), None, ("undo", None), ("clear", None), None, ("ask", "erase"),
             None, ("mode", "view"))
RESIZE_BAR = (("scale", 100), ("scale", 75), ("scale", 50), ("scale", 25), None, ("apply_resize", None), None,
              ("mode", "view"))
HINTS = {"comment": "comment_hint", "erase": "erase_hint", "resize": "resize_hint"}


def _label(action, value):
    if action == "tool":
        return (KEYED, {"name": TOOL_NAMES[value], "key": KEYS[value]}) if value in KEYS else TOOL_NAMES[value]
    if action == "mode":
        return LABELS["done"] if value == "view" else MODE_LABELS[value]
    if action == "colour":
        return COLOUR_NAMES[value]
    if action == "width":
        return WIDTH_NAMES[value]
    if action == "scale":
        return (SCALE_LABEL, {"percent": value})
    if action == "ask":
        return LABELS["remove_bg"] if value == "remove_bg" else LABELS["erase_run"]
    return LABELS[action]


def _row(entries, scale, measure, labelled=False):
    """Items of a bar laid out from x = 0: ``[{"rect", "action", "value", "glyph", "label"}]`` and its width."""
    items, x = [], PAD * scale
    for entry in entries:
        if entry is None:
            x += SEPARATOR * scale - GAP * scale
            continue
        action, value = entry
        width = BUTTON * scale
        from .ui_text import iface_
        if labelled:   # A mode button shows its name beside its glyph.
            width = max(width, (GLYPH_W + 3 * PAD) * scale + measure(iface_(MODE_LABELS[value]), LABEL_SIZE * scale))
        elif action == "scale":   # A scale is its number.
            width = max(width, 2 * PAD * scale + measure(iface_(SCALE_LABEL, percent=value), LABEL_SIZE * scale))
        items.append({"rect": [x, PAD * scale, width, BUTTON * scale], "action": action, "value": value,
                      "glyph": value if action in ("tool", "colour", "width", "mode", "ask") else action,
                      "label": _label(action, value)})
        x += width + GAP * scale
    return items, x - GAP * scale + PAD * scale


def _place(items, x, y):
    for item in items:
        item["rect"] = [item["rect"][0] + x, item["rect"][1] + y, item["rect"][2], item["rect"][3]]
    return items


def layout(width, height, scale, mode, palette_open, measure, card=None, insets=(0, 0, 0)):
    """``card``: the number of lines of an open question (a paid edit's card), whose two answers then take every
    hit. ``insets``: the (left, right, bottom) pixels other regions draw over the view (``chat_launcher.insets``: the
    toolbar, the sidebar, the asset shelf and its header); every control stays in the frame they leave free."""
    if mode not in MODES:
        raise ValueError(mode)
    s = scale
    cover_left, cover_right, cover_bottom = insets
    x0, width, height = cover_left, width - cover_left - cover_right, height - cover_bottom
    field = send = None
    bottom = cover_bottom + EDGE * s
    if mode in ("markup", "comment"):
        field_w = min(FIELD_W * s, width - 2 * EDGE * s)
        field = [x0 + (width - field_w) / 2.0, bottom, field_w, FIELD_H * s]
        send = [field[0] + field_w - BUTTON * s - 2 * s, bottom + (FIELD_H - BUTTON) / 2.0 * s, BUTTON * s, BUTTON * s]
        bottom += (FIELD_H + ROW_GAP) * s
    view_bar = VIEW_BAR if mt.AI_EDITS else tuple(entry for entry in VIEW_BAR
                                                   if entry not in (("ask", "remove_bg"), ("mode", "erase")))
    entries = {"view": view_bar, "markup": MARKUP_BAR, "comment": COMMENT_BAR, "erase": ERASE_BAR,
               "resize": RESIZE_BAR}[mode]
    bar, bar_w = _row(entries, s, measure, labelled=(mode == "view"))
    bar_h = BUTTON * s + 2 * PAD * s
    bar_rect = [x0 + (width - bar_w) / 2.0, bottom, bar_w, bar_h]
    _place(bar, bar_rect[0], bar_rect[1])
    palette = palette_rect = None
    if mode == "markup" and palette_open:
        palette, palette_w = _row([("colour", c) for c in md.COLOURS] + [None] + [("width", w) for w in md.WIDTHS], s,
                                  measure)
        anchor = next(i["rect"] for i in bar if i["action"] == "palette")
        left = min(max(x0 + EDGE * s, anchor[0] + anchor[2] / 2.0 - palette_w / 2.0), x0 + width - EDGE * s - palette_w)
        palette_rect = [left, bar_rect[1] + bar_h + ROW_GAP / 2.0 * s, palette_w, bar_h]
        _place(palette, palette_rect[0], palette_rect[1])
    close = [x0 + width - (EDGE + BUTTON) * s, cover_bottom + height - (EDGE + BUTTON) * s, BUTTON * s, BUTTON * s]
    card_rect = answers = None
    if card is not None:   # A question over everything: only its answers take a press.
        card_w = min(CARD_W * s, width - 2 * EDGE * s)
        card_h = (CARD_TITLE + CARD_LINE * card + CARD_BUTTONS) * s
        card_rect = [x0 + (width - card_w) / 2.0, cover_bottom + max(EDGE * s, (height - card_h) / 2.0), card_w, card_h]
        button_w = (card_w - 3 * EDGE * s) / 2.0
        y = card_rect[1] + EDGE * s
        answers = [{"rect": [card_rect[0] + EDGE * s, y, button_w, BUTTON * s], "action": "answer", "value": False,
                    "label": LABELS["cancel"]},
                   {"rect": [card_rect[0] + 2 * EDGE * s + button_w, y, button_w, BUTTON * s], "action": "answer",
                    "value": True, "label": None}]
    hits = [(tuple(i["rect"]), i["action"], i["value"]) for i in (palette or []) + bar]
    if field:
        hits += [(tuple(send), "send", None), (tuple(field), "instructions", None)]
    hits.append((tuple(close), "close", None))
    if answers:
        hits = [(tuple(a["rect"]), a["action"], a["value"]) for a in answers]
    top = cover_bottom + height - close[1] + EDGE * s
    return {"bar": bar, "bar_rect": bar_rect, "palette": palette, "palette_rect": palette_rect, "field": field,
            "send": send, "close": close, "close_label": LABELS["close"], "hits": hits,
            "margins": (cover_left + 2 * EDGE * s, cover_right + 2 * EDGE * s, top, bar_rect[1] + bar_h + EDGE * s),
            "hint": LABELS[HINTS[mode]] if mode in HINTS else None, "card": card_rect, "answers": answers}


def hit_at(plan, x, y):
    """The first control under (x, y), as ``(action, value)``, or None (the picture or the scrim)."""
    for (rx, ry, rw, rh), action, value in plan["hits"]:
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return action, value
    return None
