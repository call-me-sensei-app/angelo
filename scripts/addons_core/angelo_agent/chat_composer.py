# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's composer: the draft, attachments, chosen tools and the toolbar (+, approval mode, model, voice, send).

Drawn by ``chat_view.draw`` only once the chat can be used: while an empty chat
has no connection the connect options are the only call to action and there is
no composer; when a connection becomes ready it appears
with keyboard focus. Moved out of ``chat_view`` unchanged otherwise.

In a right-to-left interface the composer is mirrored like the rest of the chat,
except the typed text, its selection and the caret: they are a left-to-right text
field, like every Blender text field (``canvas_rtl.unmirrored``).
"""

import math
import time

from . import canvas_controls, canvas_rtl, chosen_tools, ui_tokens
from .ui_text import iface_, n_

CHOSEN_FILL = (0.141, 0.188, 0.184, 1)   # A chosen tool's chip: teal-tinted, so it reads as an action.
DRAFT_LEFT = 28   # The typed text's left edge, in the chat's layout units.


def cursor_position(line):
    """The caret as an index into ``line.body`` (a Python string). ``ConsoleLine.current_character`` is a character
    index already (RNA converts it from the native byte offset); only ``console.history_append`` takes bytes."""
    return max(0, min(len(line.body), line.current_character))


def set_draft(line, value, cursor):
    line.body = value[:32000]
    line.current_character = max(0, min(len(line.body), cursor))


def column_x(value, column, size, scale=None):
    """Where ``column`` of a draft row is drawn: the pen after ``value[:column]``, measured as ``chat_view.text``
    draws the row (``chat_view.measure``; ``scale``: the scale the row was drawn at, the draw's own by default).
    The caret, the selection and a click (``column_at``) all place columns here, so they agree with the glyphs."""
    from . import chat_view
    return DRAFT_LEFT + chat_view.measure(value[:max(0, column)], size, scale=scale, shaped=False)  # Drawn unshaped.


def column_at(value, x, size, scale=None):
    """The column of a draft row nearest ``x``: the inverse of ``column_x``."""
    return min(range(len(value) + 1), key=lambda column: abs(column_x(value, column, size, scale) - x))


def vertical_caret(rows, caret, up, size, scale=None):
    """The caret one wrapped row up (``up``) or down in ``rows`` ((text, offset), ...): the column nearest the same x,
    as drawn at ``size`` and ``scale``. A soft-wrapped row's last column is the next row's first: stop before it."""
    current = max((i for i, (_, offset) in enumerate(rows) if offset <= caret), default=0)
    target = max(0, min(len(rows) - 1, current + (-1 if up else 1)))
    here = column_x(rows[current][0], caret - rows[current][1], size, scale)
    row, start = rows[target]
    soft = target + 1 < len(rows) and rows[target + 1][1] == start + len(row)
    return start + min(column_at(row, here, size, scale), max(0, len(row) - 1) if soft else len(row))

def draw(state, ui, controller, *, geometry, composer_bottom, composer_height, line, draft, draft_lines, size,
         line_height, width, attachment_rows, connected):
    from . import chat_view as view
    from .chat_settings import clean_options, selected_model
    from .chat_popovers import effective_effort, effort_name, model_name, supports_fast
    cx, _, cw, _ = geometry["rect"]
    view.shape(cx, composer_bottom, cw, composer_height, view.CARD, geometry["radius"])
    caret = cursor_position(line) if line else 0
    caret_row = max((i for i, (_, offset) in enumerate(draft_lines) if offset <= caret), default=0)
    first = max(0, caret_row - 7)
    start_y = composer_bottom + composer_height - attachment_rows * 50 - line_height - 13
    state["composer"] = (24, composer_bottom + 48, width - 48, composer_height - 50 - attachment_rows * 50)
    state["draft_rows"] = [(value, offset, start_y - i * line_height) for i, (value, offset) in enumerate(draft_lines[first:first + 8])]
    state["draft_lines"], state["font_size"] = draft_lines, size
    if not draft:
        placeholder = iface_("External client: approvals and Stop only") if getattr(controller, "external_client", "") else iface_("Do anything")
        view.text(placeholder, DRAFT_LEFT, start_y, size, (0.46, 0.46, 0.46, 1))
    else:
        with canvas_rtl.unmirrored():
            _draw_draft(view, state, size, line_height)
    state["text_scale"] = view._scale  # The draft is measured, clicked and drawn at this one scale (view.measure).
    if line and state.get("focused", True) and time.monotonic() % 1 < 0.5:
        value, offset = draft_lines[caret_row]
        x = column_x(value, caret - offset, size)
        y = start_y - (caret_row - first) * line_height
        with canvas_rtl.unmirrored():
            view.shape(x, y - 3, 1, line_height - 3, view.TEXT)
    for index, reference in enumerate(controller.attachments):
        chip_width = (width - 66) / 2
        x = 28 + (index % 2) * (chip_width + 10)
        y = composer_bottom + composer_height - 48 - (index // 2) * 50
        view.shape(x, y, chip_width, 40, view.USER, 9)
        view.text(view.elide(reference["name"], chip_width - 30, 12), x + 10, y + 23, 12)
        view.text(iface_("{kind} · {size} KB", kind=reference.get("kind", "image"), size=max(1, math.ceil(reference["bytes"] / 1024))).upper(), x + 10, y + 8, 10, view.MUTED)
        view.icon("close", x + chip_width - 12, y + 25)
        state["hits"].append(((x + chip_width - 26, y + 13, 26, 27), "remove_reference", index))
    chosen = list(getattr(controller, "chosen_tools", ()) or ())
    for offset, choice in enumerate(chosen):   # Tools the person chose: teal chips after the attachments.
        index = len(controller.attachments) + offset
        chip_width = (width - 66) / 2
        x = 28 + (index % 2) * (chip_width + 10)
        y = composer_bottom + composer_height - 48 - (index // 2) * 50
        view.shape(x, y, chip_width, 40, ui_tokens.TEAL, 9)
        view.shape(x + 1, y + 1, chip_width - 2, 38, CHOSEN_FILL, 8)
        label, kind = chosen_tools.chip(choice)
        view.text(view.elide(label, chip_width - 30, 12), x + 10, y + 23, 12)
        view.text(iface_(kind).upper(), x + 10, y + 8, 10, ui_tokens.TEAL)
        view.icon("close", x + chip_width - 12, y + 25)
        state["hits"].append(((x + chip_width - 26, y + 13, 26, 27), "remove_chosen", offset))
    state["add_bottom"] = composer_bottom + composer_height + 8   # The + menu opens above the composer.
    options = clean_options(controller.state.get("options"))
    model = selected_model(controller)
    fast_supported = supports_fast(model)
    team = ui.connection_record(controller) if not (model or options["model"]) else None  # A team connection names itself.
    from .chat_start_states import default_label
    label = model_name(model) if model else options["model"] or (team["name"] if team and team.get("method") == "TEAM"
                                                                  else default_label(controller.selection_models))
    label += " " + effort_name(effective_effort(model, options["effort"])) if model and model["efforts"] else ""
    label_inset = 21 if fast_supported else 9
    desired = min(205, view.measure(label, 13) + label_inset + 23)
    available = max(60, width - 230)
    model_width = min(desired, available)
    from .voice_ui import mic_visible
    show_mic = connected and mic_visible(controller)  # Off by default at 1.0; never shown where voice can't work.
    model_x = (geometry["mic_x"] if show_mic else geometry["send_x"] - 10) - 24 - model_width
    state["model_label"] = label  # For window fixtures.
    label = view.elide(label, model_width - label_inset - 14, 13)
    cy = geometry["toolbar_y"]
    view.icon("plus", geometry["plus_x"], cy, view.TEXT)
    state["hits"].append(((23, cy - 14, 28, 28), "attach", None))
    view.icon("approval", geometry["approval_x"] + 5, cy, view.MUTED)
    label_approval = {"ASK": n_("Ask for approval"), "READ_ONLY": n_("Read only"), "AUTO_BOUNDED": n_("Approve for me")}[options["approval"]]
    if controller.trusted_scripts:
        label_approval = n_("Trusted scripts")
    elif controller.always_allow_scripts:   # Auto with the person's Always allow scripts: say scripts run unasked.
        label_approval = n_("Approve for me · scripts allowed")
    view.text(view.elide(iface_(label_approval), model_x - 94, 13), 83, cy - 5, 13, view.MUTED)
    state["hits"].append(((59, cy - 14, max(30, model_x - 68), 28), "approval", None))
    state["picker_anchor"] = (model_x - 4, cy - 14, model_width + 10, 28)
    if connected or ui.ready_connections():  # With nothing connected, the Connect card replaces "Choose model".
        view.shape(*state["picker_anchor"], (0.255, 0.255, 0.255, 1), 14)
        if fast_supported:
            view.icon("fast", model_x + 7, cy, view.BLUE if options["fast"] else view.TEXT)
            state["hits"].append(((model_x - 4, cy - 14, 25, 28), "fast", None))
        view.text(label, model_x + label_inset, cy - 5, 13, view.MUTED)
        view.icon("chevron", model_x + model_width - 4, cy, view.MUTED)
        state["hits"].append((state["picker_anchor"], "model", None))
    voice_active = controller.voice is not None or controller.status == "Starting voice"
    if show_mic or voice_active:
        view.icon("mic", geometry["mic_x"], cy, (0.95, 0.3, 0.35, 1) if voice_active else view.TEXT)
        state["hits"].append(((geometry["mic_x"] - 14, cy - 14, 28, 28), "voice", None))
    enabled = bool(draft.strip() or controller.attachments or controller.busy)
    sx = geometry["send_x"]
    view.shape(sx - 14, cy - 14, 28, 28, view.TEXT if enabled else (0.28, 0.28, 0.28, 1), 14)
    if controller.busy:
        view.shape(sx - 4, cy - 4, 8, 8, view.BG, 2)
    else:
        view.icon("arrow", sx, cy, view.BG if enabled else view.MUTED)
    state["hits"].append(((sx - 14, cy - 14, 28, 28), *(("stop" if controller.busy else "send", None) if enabled else (canvas_controls.DISABLED, canvas_controls.SEND_EMPTY))))


def _draw_draft(view, state, size, line_height):
    """The typed text and its selection, as laid out (never shaped or mirrored: the caret follows the characters)."""
    for value, offset, y in state["draft_rows"]:
        selected = state.get("selection")
        if selected:
            begin, end = max(offset, selected[0]), min(offset + len(value), selected[1])
            if end > begin:
                left = column_x(value, begin - offset, size)
                view.shape(left, y - 3, column_x(value, end - offset, size) - left, line_height, (0.27, 0.38, 0.57, 0.65))
        view.text(value.rstrip(), DRAFT_LEFT, y, size, shaped=False)
