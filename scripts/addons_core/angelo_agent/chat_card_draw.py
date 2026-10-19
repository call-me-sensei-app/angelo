# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's decision cards: approvals, a native preview, a native job on a copy, and the changes pill.

``chat_view`` builds a transcript block for each card with ``approval_block``,
``preview_block`` and ``job_block`` and hands drawing back to ``draw``. The
hits keep the actions ``chat_view.dispatch_action`` already routes (``approve``,
``decline``, ``review``, ``script_always_allow``, ``accept_native_preview``,
``discard_native_preview``, ``expand``, ``stop_native_job``): this module is
presentation only and never decides what is approved.

Every card has the same anatomy: a rounded panel, a status chip (amber for
"needs you", teal for work in progress or a preview), a title, plain-language
rows, technical detail in a framed mono block, and one action row at the card's
bottom: the primary action in teal, the secondary in a quiet panel, a ghost
text toggle. Heights are computed by the same cursor the draw uses, so text
never runs under a button. Details stay the complete, exact review (every row
the review prepared, handles included): they are technical by design and
closed until asked for. Right-to-left languages mirror the canvas
(``canvas_rtl``); detail text there uses the proportional font, because the
mono font has no Arabic forms.
"""

from .card_kit import AMBER, LINE, PANEL, PANEL_HOVER, TEAL, TEXT, MUTED, INK
from .ui_text import iface_, n_, plural_

CODE_FILL = (0.071, 0.078, 0.094, 1)    # The framed detail block: one step below PANEL (private token).
CORAL = (0.941, 0.541, 0.424, 1)         # ui_tokens.CORAL: a removal.
BUTTON_Y, BUTTON_H, ALWAYS_GAP = 26, 30, 40
CHIP_H = 20
PAID_TOOLS = frozenset({"angelo_studio_generate"})  # Approvals that spend API credits get the kit's paid chip.
REVIEW_INSET = 64  # approval_review prepares a program's rows at the region width less this, with chat_view._mono.
UNAVAILABLE = n_("This preview is no longer available. Open Details to see what it did.")


def _view():
    from . import chat_view
    return chat_view


def _measure(value, size, font=None):
    """Width as drawn: the canvas lays out unscaled and draws under one scale factor (``chat_view._scale``)."""
    import blf
    view = _view()
    font = view._font if font is None else font
    scale = getattr(view, "_scale", 1.0) or 1.0
    blf.size(font, size * scale)
    return blf.dimensions(font, view.canvas_rtl.visual(value))[0] / scale


def detail_font():
    """Mono for detail text, except in a right-to-left language: the mono font has no Arabic forms."""
    from .ui_text import is_rtl
    view = _view()
    return view._font if is_rtl() else view._mono



# Layout: one cursor for height and draw.

def _body_height(rows, details, notes, size, line_height):
    """From the block top to the bottom of the last text row (title row first)."""
    height = 12 + 14 + CHIP_H + 12 + round(size * 1.9) + max(0, len(rows) - 1) * line_height
    if details:
        height += 12 + 16 + len(details) * line_height
    if notes:
        height += 10 + len(notes) * line_height
    return height


def _footer(always):
    return 16 + BUTTON_Y + BUTTON_H + (ALWAYS_GAP if always else 0)


def approval_block(controller, pending, area, width, size, line_height, expanded, state):
    """The transcript block for a pending approval (or None while its rows are being prepared)."""
    from . import chat_cards
    from .approval_review import details_preparing, review_rows, review_token
    view = _view()
    opened = ("approval", pending.id) in expanded
    # Rows are prepared (and keyed) with the mono font's measure everywhere; only the drawing font changes for
    # right-to-left text, so the timer's prepared review and this lookup always agree.
    mono = detail_font()
    rows, detail_rows = review_rows(pending, area.as_pointer(), max(40, width - REVIEW_INSET), size, view._font, view._mono,
                                    view.wrap, opened)
    if not rows:
        return None
    state["approval_review"] = review_token(pending)
    preparing = opened and not detail_rows and details_preparing(pending, area.as_pointer())
    always = chat_cards.always_allow_rows(controller, pending, view.wrap, max(40, width - REVIEW_INSET), size - 1, view._font)
    script = pending.name == "angelo_execute_python"
    paid = pending.name in PAID_TOOLS
    height = _body_height(rows, detail_rows, always, size, line_height) + _footer(always)
    kind = (iface_("Script") if script else iface_("Background job") if pending.name.startswith("angelo_job_")
            else iface_("Scene changes"))
    card = {"detail_rows": detail_rows, "script": script, "preparing": preparing, "always_rows": always, "mono": mono,
            "paid": paid, "kind": kind}
    return ("approval", card, rows, height, pending.id, 0)


def _plain_summary(descriptor):
    """A preview's result in plain words: what changes, how the checks went, what Accept does."""
    changes = descriptor.get("changes") or {}
    counts = {group: len(changes.get(group) or ()) for group in ("added", "modified", "removed")}
    parts = [plural_(counts["added"], "{count} object added", "{count} objects added") if counts["added"] else "",
             plural_(counts["modified"], "{count} object changed", "{count} objects changed") if counts["modified"] else "",
             plural_(counts["removed"], "{count} object removed", "{count} objects removed") if counts["removed"] else ""]
    lines = [" · ".join(part for part in parts if part) or iface_("No objects change.")]
    names = [str(entry.get("name")) for entry in changes.get("added") or () if isinstance(entry, dict) and entry.get("name")]
    if names:
        shown = ", ".join("“" + name + "”" for name in names[:3])
        lines.append(iface_("New: {names}", names=shown) if len(names) <= 3
                     else iface_("New: {names} and {count} more", names=shown, count=len(names) - 3))
    checks = descriptor.get("checks") or ()
    if isinstance(checks, dict):
        checks = [value if isinstance(value, dict) else {"status": value} for value in checks.values()]
    statuses = [check.get("status", check.get("passed")) for check in checks if isinstance(check, dict)]
    passed = sum(1 for status in statuses if status is True or str(status).lower() in {"pass", "passed", "ok"})
    if statuses:
        lines.append(plural_(len(statuses), "The check passed", "All {count} checks passed") if passed == len(statuses)
                     else iface_("{passed} of {total} checks passed", passed=passed, total=len(statuses)))
    if changes.get("truncated") or changes.get("uncertain"):
        lines.append(iface_("Some changes could not be read back. Open Details before you accept."))
    lines.append(iface_("Accept applies it as one Undo step. Nothing in your scene changes until then."))
    return "\n".join(lines), counts


def preview_block(descriptor, key, width, size, line_height, expanded, available):
    from .agent_workspace_ui import control_lines
    view = _view()
    summary, counts = _plain_summary(descriptor)
    rows = view.wrap(summary, max(40, width - REVIEW_INSET), size, view._font)
    detail_rows = ()
    if key in expanded:
        detail = "\n".join(control_lines(descriptor.get("arguments", {}), iface_("Exact submitted controls"),
                                         target_labels=descriptor.get("target_labels")))
        detail_rows = view.wrap(detail, max(40, width - REVIEW_INSET), size - 1, view._mono)
    image = 0
    if (descriptor.get("changes") or {}).get("views"):
        image = round(min(260, (width - 64) * 9 / 16)) + 34
    card = dict(descriptor, detail_rows=detail_rows, available=available, image_height=image, counts=counts,
                mono=detail_font())
    height = _body_height(((None, 0),) + tuple(rows), detail_rows, (), size, line_height) + image + _footer(False)
    return ("native_preview", card, rows, height, key, 0)


def _step(descriptor):
    progress = descriptor.get("progress")
    if isinstance(progress, dict):
        done = progress.get("completed", progress.get("completed_units"))
        total = progress.get("total", progress.get("total_units"))
        if type(done) in (int, float) and type(total) in (int, float) and total > 0:
            return max(0.0, min(1.0, done / total)), iface_("Step {current} of {total}", current=int(done), total=int(total))
    elif type(progress) in (int, float) and 0 <= progress <= 100:
        return progress / 100, iface_("{percent}% done", percent=int(progress))
    return None, iface_("Getting ready")


def job_block(descriptor, width, size, line_height):
    view = _view()
    fraction, step = _step(descriptor)
    body = iface_("Nothing in your scene changes until you apply the result. Stop takes effect between steps.")
    rows = view.wrap(step + "\n" + body, max(40, width - REVIEW_INSET), size, view._font)
    card = dict(descriptor, fraction=fraction)
    height = _body_height(((None, 0),) + tuple(rows), (), (), size, line_height) + 18 + _footer(False)
    return ("native_job", card, rows, height, descriptor["call_id"], 0)


# Drawing.

def _over(color, base=PANEL):
    """A translucent token composited over the card: the canvas draws its shapes without blending."""
    alpha = color[3]
    return tuple(color[i] * alpha + base[i] * (1 - alpha) for i in range(3)) + (1,)


def _chip(x, y, label, fill, color):
    view = _view()
    w = _measure(label, 11) + 20
    view.shape(x, y, w, CHIP_H, _over(fill), CHIP_H / 2)
    view.text(label, x + 10, y + 6, 11, color)
    return x + w + 6


def _card(bottom, top, width, rail):
    view = _view()
    view.shape(16, bottom + 12, width - 32, top - bottom - 24, LINE, 13)
    view.shape(17, bottom + 13, width - 34, top - bottom - 26, PANEL, 12)
    if rail:  # "Needs you": the amber rail on the reading-start edge (mirrored with the canvas).
        view.shape(17, bottom + 13, 4, top - bottom - 26, rail, 2)


def _rows(rows, x, y, size, line_height, band, color=TEXT, font=None):
    for value, _ in rows:
        if band[0] - line_height <= y <= band[1]:
            _view().text(value.rstrip(), x, y, size, color, font)
        y -= line_height
    return y


def _code(rows, y, width, size, line_height, band, font):
    """Detail rows in a framed block; ``y`` is the block's top. Returns the cursor below it."""
    view = _view()
    height = 16 + len(rows) * line_height
    view.shape(24, y - height, width - 48, height, LINE, 9)
    view.shape(25, y - height + 1, width - 50, height - 2, CODE_FILL, 8)
    first = max(0, int((y - 8 - band[1]) // line_height))  # Rows above the viewport are skipped, not drawn.
    _rows(rows[first:first + int(band[1] / line_height) + 2], 32, y - 8 - line_height + 6 - first * line_height,
          size - 1, line_height, band, MUTED, font)
    return y - height


def _button(state, x, y, label, action, value, kind, size=13):
    """A primary (teal), secondary (panel) or ghost (text) button; returns its right edge."""
    view = _view()
    w = max(92 if kind != "ghost" else 0, _measure(label, size) + 28)
    rect = (x, y, w, BUTTON_H)
    if kind == "primary":
        view.shape(*rect, TEAL, 8)
        view.text(label, x + 14, y + 10, size, INK)
    elif kind == "secondary":
        view.shape(*rect, LINE, 8)
        view.shape(x + 1, y + 1, w - 2, BUTTON_H - 2, PANEL_HOVER, 7)
        view.text(label, x + 14, y + 10, size, TEXT)
    elif kind == "disabled":
        view.shape(*rect, PANEL_HOVER, 8)
        view.text(label, x + 14, y + 10, size, (0.45, 0.47, 0.5, 1))
    else:
        view.text(label, x + 14, y + 10, size - 1, MUTED)
    state["hits"].append((rect, action, value))
    return x + w + 10


def _status(x, y, kind, label):
    """The kit's status chip (``ui_kit_status``: needs_you, preview, running); returns the next chip's x."""
    from . import ui_kit_status
    view = _view()
    w, h = ui_kit_status.chip_size(view, kind, 1.0, label)
    import gpu
    blend = gpu.state.blend_get()
    gpu.state.blend_set('ALPHA')  # The chip's soft fill is translucent; the transcript draws without blending.
    try:
        ui_kit_status.draw_chip(view, (x, y + (CHIP_H - h) / 2, w, h), 1.0, kind=kind, label=label)
    finally:
        gpu.state.blend_set(blend)
    return x + w + 6


def _header(top, chips, title, width, size):
    """Chips are ``(kind, label)`` for the kit's status chip or ``(label, fill, color)`` for a count/kind tag."""
    view = _view()
    x = 32
    for chip in chips:
        x = _status(x, top - 26 - CHIP_H, *chip) if len(chip) == 2 else _chip(x, top - 26 - CHIP_H, *chip)
    title_size = size + 3
    baseline = top - 26 - CHIP_H - 12 - round(size * 1.9) + (round(size * 1.9) - title_size) / 2 + 3
    view.text(view.elide(title, width - 72, title_size), 32, baseline, title_size, TEXT)
    return top - 26 - CHIP_H - 12 - round(size * 1.9)


def draw(role, message, rows, key, top, bottom, ctx):
    {"approval": _draw_approval, "native_preview": _draw_preview, "native_job": _draw_job}[role](
        message, rows, key, top, bottom, ctx)


def _draw_approval(message, rows, key, top, bottom, ctx):
    from . import chat_cards
    state, controller, width, size, line_height = ctx["state"], ctx["controller"], ctx["width"], ctx["size"], ctx["line_height"]
    band = ctx["band"]
    _card(bottom, top, width, AMBER)
    kind = (("paid", iface_("Uses API credits")) if message.get("paid") else
            (message["kind"], PANEL_HOVER, MUTED))
    y = _header(top, (("needs_you", iface_("Needs your approval")), kind),
                rows[0][0].rstrip(), width, size)
    y = _rows(rows[1:], 32, y - line_height + 6, size, line_height, band, (0.859, 0.890, 0.910, 1)) + line_height - 6
    # What the card shows, for QA state asserts (the rows' text and the font they are drawn in).
    state["last_detail_rows"] = tuple(row[0] for row in message["detail_rows"])
    state["last_detail_font"] = message["mono"]
    if message["detail_rows"]:
        y = _code(message["detail_rows"], y - 12, width, size, line_height, band, message["mono"])
    if message["always_rows"]:
        y = _rows(message["always_rows"], 32, y - 10 - line_height + 6, size - 1, line_height, band, MUTED)
        chat_cards.draw_always_allow(key, state, 32, bottom + BUTTON_Y + ALWAYS_GAP)
    # One primary Approve that never looks disabled; an unreviewed script's first click reveals its code.
    review_first = message["script"] and key not in controller.reviewed_calls
    x = _button(state, 32, bottom + BUTTON_Y, iface_("Review & approve") if review_first else iface_("Approve"),
                "approve", key, "primary")
    x = _button(state, x, bottom + BUTTON_Y, iface_("Decline"), "decline", key, "secondary")
    shown = ("approval", key) in ctx["expanded"]
    if message["script"]:
        toggle = iface_("Hide code") if shown else iface_("Show code")
    else:  # Opened Details that are still being prepared say so; partial rows are never drawn.
        toggle = (iface_("Preparing details…") if message["preparing"] else iface_("Hide details")) if shown else iface_("Show details")
    _button(state, x, bottom + BUTTON_Y, toggle, "review", key, "ghost")


def _count_chips(counts):
    chips = []
    for group, label, color in (("added", n_("+{count} added"), TEAL), ("modified", n_("~{count} changed"), AMBER),
                                ("removed", n_("−{count} removed"), CORAL)):
        if counts.get(group):
            chips.append((iface_(label, count=counts[group]), (*color[:3], 0.16), color))
    return chips


def _draw_preview(message, rows, key, top, bottom, ctx):
    state, controller, width, size, line_height = ctx["state"], ctx["controller"], ctx["width"], ctx["size"], ctx["line_height"]
    band = ctx["band"]
    view = _view()
    _card(bottom, top, width, None)
    chips = (("preview", iface_("Preview · not applied")), *_count_chips(message["counts"]))
    y = _header(top, chips, iface_("Built on a copy of your scene"), width, size)
    if message["image_height"]:
        h = message["image_height"] - 34
        rect = (32, y - 10 - h, width - 64, h)
        view.shape(*rect, CODE_FILL, 10)
        if not ctx["draw_image"](message, rect, controller):
            view.text(iface_("The picture of this preview is not available. Open Details for the result."),
                      44, y - 10 - h / 2, 11, MUTED)
        view.text(iface_("Solid shading · final materials may look different"), 32, y - h - 26, 10, MUTED)
        y -= message["image_height"]
    y = _rows(rows, 32, y - line_height + 6, size, line_height, band, (0.859, 0.890, 0.910, 1)) + line_height - 6
    if message["detail_rows"]:
        _code(message["detail_rows"], y - 12, width, size, line_height, band, message["mono"])
    value = (controller.state["id"], message["preview_id"])
    available = message["available"]
    x = 32
    for label, action, kind in ((iface_("Accept"), "accept_native_preview", "primary"),
                                (iface_("Discard"), "discard_native_preview", "secondary")):
        if available:
            x = _button(state, x, bottom + BUTTON_Y, label, action, value, kind)
        else:  # Disabled with its reason, never a dead control.
            x = _button(state, x, bottom + BUTTON_Y, label, "disabled", UNAVAILABLE, "disabled")
    _button(state, x, bottom + BUTTON_Y, iface_("Hide details") if message["detail_rows"] else iface_("Details"),
            "expand", key, "ghost")


def _draw_job(message, rows, key, top, bottom, ctx):
    state, controller, width, size, line_height = ctx["state"], ctx["controller"], ctx["width"], ctx["size"], ctx["line_height"]
    view = _view()
    _card(bottom, top, width, None)
    y = _header(top, (("running", iface_("Working on a copy")),), iface_("Building your change"), width, size)
    track = (32, y - 8, width - 64, 4)
    view.shape(*track, LINE, 2)
    if message["fraction"] is not None and message["fraction"] > 0:
        view.shape(track[0], track[1], max(4, track[2] * message["fraction"]), 4, TEAL, 2)
    _rows(rows, 32, y - 18 - line_height + 6, size, line_height, ctx["band"], (0.859, 0.890, 0.910, 1))
    _button(state, 32, bottom + BUTTON_Y, iface_("Stop"), "stop_native_job", controller.state["id"], "secondary")


def draw_changes_pill(state, width, y, changed, changed_files, added, removed):
    """The "N objects changed  +A −R" pill above the composer; every part is measured, never offset by guesswork."""
    view = _view()
    label = (iface_("{objects} · {files} changed", objects=plural_(changed, "{count} object", "{count} objects"),
                    files=plural_(changed_files, "{count} file", "{count} files")) if changed and changed_files
             else plural_(changed, "{count} object changed", "{count} objects changed") if changed
             else plural_(changed_files, "{count} file changed", "{count} files changed"))
    plus, minus = "+" + str(added), "−" + str(removed)
    label_width, plus_width, minus_width = _measure(label, 14), _measure(plus, 13), _measure(minus, 13)
    pill_width = min(width - 32, 12 + label_width + 10 + plus_width + 8 + minus_width + 14)
    x = (width - pill_width) / 2
    view.shape(x, y, pill_width, 35, LINE, 14)
    view.shape(x + 1, y + 1, pill_width - 2, 33, PANEL, 13)
    view.text(view.elide(label, max(20, pill_width - plus_width - minus_width - 44), 14), x + 12, y + 12, 14, MUTED)
    right = x + pill_width - 14
    view.text(minus, right - minus_width, y + 12, 13, CORAL)
    view.text(plus, right - minus_width - 8 - plus_width, y + 12, 13, TEAL)
    state["hits"].append(((x, y, pill_width, 35), "changes", None))
