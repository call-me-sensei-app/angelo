# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The usage meter on the chat canvas: the sticky bar along the chat's top and its detail card (popover ``"usage"``).

Contract: doc/angelo/agent_chat.rst, "Usage meter". ``usage_meter`` keeps the numbers, ``usage_windows`` a
plan's windows, ``usage_view`` the words; this module lays them out and draws them in the chat's palette
with the kit's segmented control. ``chat_view`` owns opening, closing, Esc and outside clicks.

The bar shows the chat's total tokens and, beside them, the estimate (API keys, teams) or the plan's busiest
window as a ring and "Included in your … plan" (subscriptions, never a dollar figure). It stays put while the
transcript scrolls under it (``chat_view`` reserves ``BAR_HEIGHT`` from the chat's first counted or running request
on); a click opens the card.

Cost on the chat path: the bar's parts are computed when their inputs change (the ledger's revision, the chat's
connection, the plan windows' report, the request in progress), not per frame; the card is computed only while
it is open. No I/O and no network here.
"""

import math

from . import ui_kit_controls, ui_text, ui_tokens, usage_meter, usage_view, usage_windows
from .ui_text import iface_

WIDTH, PAD = 340, 14
BAR_HEIGHT = 30
BAR_HOVER = (0.135, 0.135, 0.135, 1)
TRACK = (0.125, 0.125, 0.125, 1)
COLORS = {"cache_read": ui_tokens.TEAL, "cache_write": (0.435, 0.659, 0.651, 1), "input": (0.55, 0.55, 0.55, 1),
          "output": (0.85, 0.85, 0.85, 1)}
_cache = {}  # Chat id -> (signature, bar parts): recomputed only when the bar's inputs change.


def _inputs(controller):
    ledger = usage_meter.UsageLedger.shared(controller.telemetry.directory)
    key = usage_windows.connection_key(controller)
    windows = usage_windows.current(key) if usage_view.billing(controller.state["provider"]) == usage_view.PLAN else None
    live = usage_meter.in_progress(getattr(controller, "_request_metrics", None), controller.state["provider"]) \
        if controller.busy else None
    return ledger, windows, live


def bar_parts(controller):
    """The bar's ``usage_view.bar`` for this chat, cached by its inputs (the ledger's revision, the chat's
    connection, the plan windows' report, the request in progress)."""
    ledger, windows, live = _inputs(controller)
    chat_id, mode = controller.state["id"], controller.state["provider"]
    signature = (ui_text.language(), ledger.revision, mode, windows and windows["at"],
                 windows and tuple(w["used"] for w in windows["windows"]), windows and windows["limited"],
                 live and tuple(sorted((key, bucket["input"] + bucket["cache_read"] + bucket["cache_write"],
                                        bucket["output"], round(bucket["usd"], 4)) for key, bucket in live.items())))
    cached = _cache.get(chat_id)
    if cached and cached[0] == signature:
        return cached[1]
    record = ledger.chat(chat_id) or {}
    value = usage_view.bar(usage_meter.combined([record.get("modes"), live]), mode, windows, live=bool(live))
    _cache.clear()  # Only the drawn chat's bar is worth keeping.
    _cache[chat_id] = (signature, value)
    return value


def bar_items(parts, width, measure):
    """``[(kind, text)]`` that fit in ``width``: tokens first, then the estimate or the plan's window and name.

    The least important part goes first when the chat is narrow: the plan's name, then the Estimate label, then
    the window text, then the cached share. The tokens and a dollar figure (which keeps its "≈") always stay.
    """
    items = [("tokens", parts["tokens"])]
    if parts.get("cached"):
        items.append(("cached", parts["cached"]))
    if parts["money"]:
        items += [("money", parts["money"]), ("estimate", iface_("Estimate"))]
    if parts["window"]:
        items.append(("window", parts["window"]))
    if parts["plan"]:
        items.append(("plan", parts["plan"]))
    room = width - 2 * PAD - 24  # The chevron at the end.

    def needed(chosen):
        return sum(measure(text) + (22 if kind in ("window", "estimate", "plan") else 14) for kind, text in chosen)
    for kind in ("plan", "estimate", "window", "cached"):
        if needed(items) <= room:
            break
        items = [item for item in items if item[0] != kind]
    return items


def bar_height(controller, blocks):
    """The usage bar's height from the chat's first counted or running request, else 0 (0 too when it fails)."""
    try:
        return BAR_HEIGHT if blocks and bar_parts(controller) else 0
    except Exception:  # noqa: BLE001 - a broken meter is left out.
        return 0


def draw_bar_safely(view, state, controller, width, height):
    """``draw_bar``; a failure leaves its strip empty, and the transcript and composer are unaffected."""
    try:
        draw_bar(view, state, controller, width, height)
    except Exception:  # noqa: BLE001
        pass


def draw_bar(view, state, controller, width, height):
    """The sticky bar along the top of the chat; registers its hit and the card's anchor. Returns its height."""
    parts = bar_parts(controller)
    y = height - BAR_HEIGHT
    open_ = (state.get("popover") or {}).get("kind") == "usage"
    hovered = state.get("hover") == ("usage", None)
    view.shape(0, y, width, BAR_HEIGHT, BAR_HOVER if open_ or hovered else view.BG)
    view.shape(0, y, width, 1, view.LINE)

    def measure(text, size=12):
        return view.measure(text, size)  # As the chat draws it (chat_view.measure).
    x, cy = PAD, y + BAR_HEIGHT / 2
    for index, (kind, text) in enumerate(bar_items(parts, width, measure)):
        if index:
            view.text("·", x, cy - 4, 12, view.MUTED)
            x += 10
        if kind == "window":
            ring(view, x + 6, cy, parts["ring"], parts["warn"])
            x += 16
        if kind in ("estimate", "plan"):
            label_w = _badge(view, x, cy - 8, text, *((ui_tokens.AMBER, ui_tokens.AMBER_SOFT) if kind == "estimate"
                                                    else (ui_tokens.TEAL, ui_tokens.TEAL_SOFT)))
            x += label_w + 8
            continue
        color = (ui_tokens.AMBER if kind == "window" and parts["warn"] else view.MUTED if kind in ("window", "cached")
                 else view.TEXT)
        view.text(text, x, cy - 4, 12, color)
        x += measure(text) + 6
    view.icon("chevron", width - PAD - 4, cy, view.MUTED)
    state["usage_anchor"] = (0, y, width, BAR_HEIGHT)
    state["usage_bar"] = parts["tokens"]  # For window fixtures.
    state["hits"].append((state["usage_anchor"], "usage", None))
    return BAR_HEIGHT


def ring_triangles(cx, cy, used, radius=5.5, width=1.8, steps=32):
    """Triangles of a clockwise annulus arc from twelve o'clock covering ``used`` (0-1) of the circle."""
    count = max(1, round(steps * min(1.0, used)))
    inner, outer, end = radius - width / 2, radius + width / 2, 2 * math.pi * min(1.0, used)
    points = []
    for i in range(count + 1):
        angle = end * i / count
        points += [(cx + inner * math.sin(angle), cy + inner * math.cos(angle), 0),
                   (cx + outer * math.sin(angle), cy + outer * math.cos(angle), 0)]
    return points, [(2 * i, 2 * i + 1, 2 * i + 2) for i in range(count)] + \
        [(2 * i + 1, 2 * i + 3, 2 * i + 2) for i in range(count)]


def ring(view, cx, cy, used, warn):
    """A plan window's share as a ring (teal, amber from ``usage_view.WARN_AT``): one batch per arc."""
    from gpu_extras.batch import batch_for_shader
    for share, color in ((1.0, (0.33, 0.33, 0.33, 1)), (used or 0, ui_tokens.AMBER if warn else ui_tokens.TEAL)):
        if share <= 0:
            continue
        points, triangles = ring_triangles(cx, cy, share)
        batch = batch_for_shader(view._shader, 'TRIS', {"pos": points}, indices=triangles)
        view._shader.bind()
        view._shader.uniform_float("color", color)
        batch.draw(view._shader)


def card_content(controller, scope):
    """The open card's ``usage_view.card`` for ``"chat"`` or ``"since"``, with the plan windows of the chat's connection."""
    ledger, windows, live = _inputs(controller)
    if scope == "since":
        since = ledger.since_started()
        live_chats = 1 if live and controller.state["id"] not in ledger.chats_since_start else 0
        return usage_view.card(usage_meter.combined([since["modes"], live]), windows=windows,
                               chats=since["chats"] + live_chats)
    record = ledger.chat(controller.state["id"]) or {}
    return usage_view.card(usage_meter.combined([record.get("modes"), live]), last=record.get("last"), windows=windows)


def blocks(content, width, wrap):
    """``[(kind, height, data)]`` top to bottom for the card's content; ``wrap(text, width, size)`` -> lines."""
    inner = width - 2 * PAD
    output = [("scope", 26, None), ("gap", 12, None), ("headline", 30, None)]
    if content["badge"] == "plan":
        output.append(("badge", 22, None))
    output += [("summary", 18, None), ("bar", 20, None)]
    output += [("segment", 17, segment) for segment in content["segments"]]
    if content["rows"] or content["last"]:
        output.append(("rule", 13, None))
    output += [("row", 18, row) for row in content["rows"][:4]]
    if content["last"]:
        output.append(("row", 18, (iface_("Last request"), content["last"])))
    if content["windows"]:
        output.append(("rule", 13, None))
        output += [("window", 40, window) for window in content["windows"]]
    if content["notes"]:
        output.append(("gap", 6, None))
        for note in content["notes"]:
            lines = wrap(note, inner, 11)
            output.append(("note", 15 * len(lines) + 4, lines))
    return output


def layout(width, height, anchor, total):
    """The card's rect: hanging from the bar (``anchor``), at its right end, inside the chat."""
    w = min(WIDTH, max(220, width - 24))
    h = min(total + 2 * PAD, max(120, height - BAR_HEIGHT - 24))
    ax, ay, aw, ah = anchor
    x = max(12, min(ax + aw - 12 - w, width - 12 - w))
    y = max(12, ay - 6 - h)
    return x, y, w, h


def draw(view, state, controller, width, height):
    """The open card (``chat_view.draw_popover``, kind ``"usage"``)."""
    import gpu
    popup = state["popover"]
    scope = popup.setdefault("scope", "chat")
    content = card_content(controller, scope)
    wrap = lambda value, w, size: [line[0] if isinstance(line, tuple) else line
                                   for line in view._flow_wrap(value, w, size)]
    items = blocks(content, WIDTH, wrap)
    x, y, w, h = layout(width, height, state.get("usage_anchor", (0, height - BAR_HEIGHT, width, BAR_HEIGHT)),
                        sum(item[1] for item in items))
    state["popup_rect"] = (x, y, w, h)
    view.shape(x, y, w, h, view.LINE, 15)
    view.shape(x + 1, y + 1, w - 2, h - 2, view.POPOVER, 14)
    old_scissor = gpu.state.scissor_get()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(*view._scissor(x + 1, y + 1, w - 2, h - 2))
    left, inner, top = x + PAD, w - 2 * PAD, y + h - PAD
    for kind, block_height, data in items:
        bottom = top - block_height
        _draw_block(view, state, kind, data, content, scope, left, inner, top, bottom, width, height)
        top = bottom
    gpu.state.scissor_set(*old_scissor)
    gpu.state.scissor_test_set(False)


def _draw_block(view, state, kind, data, content, scope, left, inner, top, bottom, width, height):
    if kind == "scope":
        segments = [("chat", iface_("This chat")), ("since", iface_("Since Angelo started"))]
        seg_w = min(inner, ui_kit_controls.segmented_width(view, 1.0, segments))
        rect = (left, bottom, seg_w, top - bottom)
        ui_kit_controls.draw_segmented(view, rect, 1.0, None, segments, scope, "popup_usage_scope")
        for hit in ui_kit_controls.segmented_hits(rect, segments, "popup_usage_scope"):
            state["popup_hits"].append(hit)
    elif kind == "headline":
        view.text(content["headline"], left, bottom + 6, 22)
        if content["badge"] == "estimate":
            _badge(view, left + view.measure(content["headline"], 22) + 8, bottom + 8,
                   content["badge_text"], ui_tokens.AMBER, ui_tokens.AMBER_SOFT, inner)
    elif kind == "badge":
        _badge(view, left, bottom + 4, content["badge_text"], ui_tokens.TEAL, ui_tokens.TEAL_SOFT, inner)
    elif kind == "summary":
        view.text(view.elide(content["summary"], inner, 12), left, bottom + 4, 12, view.MUTED)
    elif kind == "bar":
        view.shape(left, bottom + 8, inner, 8, TRACK, 4)
        x = left
        for key, _, _, share in content["segments"]:
            if share > 0:
                part = max(2, inner * share)
                view.shape(x, bottom + 8, min(part, left + inner - x), 8, COLORS[key], 2)
                x += part
    elif kind == "segment":
        key, label, count, _ = data
        view.shape(left, bottom + 5, 8, 8, COLORS[key], 2)
        view.text(label, left + 15, bottom + 3, 12, view.MUTED)
        _right(view, usage_view.tokens(count), left + inner, bottom + 3, 12, view.TEXT)
    elif kind == "rule":
        view.shape(left, bottom + 6, inner, 1, view.LINE)
    elif kind == "row":
        label, value = data
        value_w = view.measure(value, 12)
        view.text(view.elide(label, max(40, inner - value_w - 12), 12), left, bottom + 4, 12)
        _right(view, value, left + inner, bottom + 4, 12, view.TEXT)
    elif kind == "window":
        label, used, detail, warn = data
        view.text(view.elide(label, inner, 12), left, bottom + 24, 12)
        view.shape(left, bottom + 14, inner, 6, TRACK, 3)
        if used > 0:
            view.shape(left, bottom + 14, max(6, inner * used), 6, ui_tokens.AMBER if warn else ui_tokens.TEAL, 3)
        view.text(view.elide(detail, inner, 11), left, bottom + 1, 11, view.MUTED)
    elif kind == "note":
        for index, line in enumerate(data):
            view.text(line.rstrip(), left, top - 13 - index * 15, 11, view.MUTED)


def _badge(view, x, y, label, color, fill, inner=None):
    """A small pill (Estimate, Included in your plan); returns its width."""
    w = view.measure(label, 10.5) + 14
    view.shape(x, y, w, 17, fill, 8.5)
    view.text(label, x + 7, y + 5, 10.5, color)
    return w


def _right(view, value, right, y, size, color):
    view.text(value, right - view.measure(value, size), y, size, color)


def dispatch(context, controller, state, action, value):
    """A click on the card's scope control."""
    popup = state.get("popover") or {}
    if action == "popup_usage_scope" and value in ("chat", "since"):
        popup["scope"] = value
    if context.area:
        context.area.tag_redraw()
