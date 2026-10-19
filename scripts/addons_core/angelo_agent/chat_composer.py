# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's composer: the draft, attachments and the toolbar (attach, approval mode, model, voice, send).

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

import blf

from . import canvas_controls, canvas_rtl
from .ui_text import iface_, n_


def cursor_position(line):
    return len(line.body.encode("utf-8")[:line.current_character].decode("utf-8", errors="ignore"))


def set_draft(line, value, cursor):
    line.body = value[:32000]
    line.current_character = len(line.body[:cursor].encode("utf-8"))


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
        view.text(placeholder, 28, start_y, size, (0.46, 0.46, 0.46, 1))
    else:
        with canvas_rtl.unmirrored():
            _draw_draft(view, state, size, line_height)
    if line and state.get("focused", True) and time.monotonic() % 1 < 0.5:
        value, offset = draft_lines[caret_row]
        blf.size(view._font, size)
        x = 28 + blf.dimensions(view._font, value[:max(0, caret - offset)])[0]
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
    options = clean_options(controller.state.get("options"))
    model = selected_model(controller)
    fast_supported = supports_fast(model)
    team = ui.connection_record(controller) if not (model or options["model"]) else None  # A team connection names itself.
    label = model_name(model) if model else options["model"] or (team["name"] if team and team.get("method") == "TEAM" else iface_("Choose model"))
    label += " " + effort_name(effective_effort(model, options["effort"])) if model and model["efforts"] else ""
    blf.size(view._font, 13)
    label_inset = 21 if fast_supported else 9
    desired = min(205, blf.dimensions(view._font, label)[0] + label_inset + 23)
    available = max(60, width - 230)
    model_width = min(desired, available)
    from .voice_ui import mic_visible
    show_mic = connected and mic_visible(controller)  # Off by default at 1.0; never shown where voice can't work.
    model_x = (geometry["mic_x"] if show_mic else geometry["send_x"] - 10) - 24 - model_width
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
                blf.size(view._font, size)
                prefix = blf.dimensions(view._font, value[:begin - offset])[0]
                selected_width = blf.dimensions(view._font, value[begin - offset:end - offset])[0]
                view.shape(28 + prefix, y - 3, selected_width, line_height, (0.27, 0.38, 0.57, 0.65))
        view.text(value.rstrip(), 28, y, size, shaped=False)
