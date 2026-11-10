# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's composer: the draft, a held goal's chip, attachments, chosen tools and the toolbar (+, approval mode,
model, voice, send).

Drawn by ``chat_view.draw`` only once the chat can be used: while an empty chat
has no connection the connect options are the only call to action and there is
no composer; when a connection becomes ready it appears
with keyboard focus. Moved out of ``chat_view`` unchanged otherwise.

In a right-to-left interface the composer is mirrored like the rest of the chat,
except the typed text, its selection and the caret: they are a left-to-right text
field, like every Blender text field (``canvas_rtl.unmirrored``).
"""

import math
import re
import time

from . import canvas_controls, canvas_rtl, chat_goal, chosen_tools, composer_edit, composer_scroll, ui_tokens
from .ui_text import iface_, n_

DRAFT_LEFT = 28   # The typed text's left edge, in the chat's layout units.
TEXT_RECT = 24   # The draft's viewport (``composer_scroll.place``) starts here and ends this far from the right.
APPROVAL_TEXT_X = 83   # The approval mode's label, after its icon.
APPROVAL_ICON_END = 82   # The approval icon's right edge: the control alone, when its label does not fit.
CHIP_MAX = 205   # The model chip's widest.
GAP = 10   # Between the approval control and the model chip.


# An attachment chip's second line, translated whole: its kind (a label per ``ReferenceStore.import_images`` kind, never
# the raw key) and its size, in the case the catalogue writes them (no upper-casing: it breaks Turkish and means
# nothing in caseless scripts).
CHIP_META = n_("{kind} · {size}")
KIND_LABELS = {"image": n_("Image"), "text": n_("Text"), "pdf": n_("PDF"), "asset": n_("3D asset"), None: n_("File")}
UNREADABLE = n_("Its text can't be read")   # The chip's line; its tooltip says why and what to do (``problem``).
# What each icon-only control is (``canvas_controls.label``): its tooltip, after a hover delay.
LABELS = {"attach": n_("Attach files and more"), "voice": n_("Talk with the agent by voice"),
          "voice_active": n_("End voice"), "send": n_("Send (Enter)"), "stop": n_("Stop"),
          "fast_on": n_("Fast mode is on: quicker replies that may cost more"),
          "fast_off": n_("Fast mode is off. Turn it on for quicker replies that may cost more."),
          "remove_reference": n_("Remove this attachment"), "remove_chosen": n_("Remove this tool")}


def kind_label(kind):
    """The message id naming an attachment's kind on its chip (``KIND_LABELS``; any other kind is a file)."""
    return KIND_LABELS.get(kind, KIND_LABELS[None])


def size_text(count_bytes):
    """An attachment's size, translated: whole kilobytes (at least 1) below a megabyte, else megabytes."""
    if count_bytes < 1024 * 1024:
        return iface_("{value} KB", value=str(max(1, math.ceil(count_bytes / 1024))))
    value = count_bytes / (1024 * 1024)
    return iface_("{value} MB", value=f"{value:.1f}" if value < 10 else f"{value:.0f}")


HINT_ROWS = 8   # The draft's cap (``chat_layout.composer_layout``): a hint grows the composer as a draft does.
_STOPS = ".!?\u061f\u0964"   # A sentence's end before a space, in the scripts Angelo ships (Latin, Arabic, Hindi).
_FULL_STOPS = "\u3002\uff01\uff1f"   # Full-width ends (Chinese, Japanese): the next sentence follows unspaced.


def sentence_ends(text):
    """Where each sentence of ``text`` (reading order) ends: after its terminator and any closing quotes or brackets,
    where a space or the end follows, or anywhere after a full-width terminator."""
    import unicodedata
    ends = []
    for index, char in enumerate(text):
        if char not in _STOPS and char not in _FULL_STOPS:
            continue
        end = index + 1
        while end < len(text) and (text[end] in "\"'" or unicodedata.category(text[end]) in ("Pe", "Pf")):
            end += 1
        if char in _FULL_STOPS or end == len(text) or text[end].isspace():
            ends.append(end)
    return ends


def hint(controller):
    """The composer's words while the draft is empty: why the chat waits and how back while its scene is not shown
    (``document_scene.notice``; R5-M-1, never a transcript row), else the field's own."""
    from .document_scene import notice
    return notice(controller) or (iface_("External client: approvals and Stop only")
                                  if getattr(controller, "external_client", "") else iface_("Do anything"))


def hint_width(width):
    """The width the draft and the hint wrap to in a chat ``width`` layout units wide (``chat_view.draw``): from
    ``DRAFT_LEFT`` to clear of the composer's scrollbar (``composer_scroll.BAR_CLEARANCE``)."""
    return max(40, width - TEXT_RECT - composer_scroll.BAR_CLEARANCE - DRAFT_LEFT)


def hint_rows(text, width, size, wrap, font):
    """``text`` as the composer shows it, every row, so no hint in any language is cut mid-sentence (the AWAY hint ran
    past a one-row clip: the session_rail GUI capture, 2026-10-25): wrapped as the chat wraps a message (``wrap``:
    ``chat_view.wrap`` in reading order, so Arabic rows come back visual, its first row first; break-it R5-L-1). In a
    chat too narrow for it within ``HINT_ROWS``: the whole sentences that fit (the first, the fact, at least), then an
    ellipsis; else as much as fits with the ellipsis measured on its row (R5-L-2)."""
    from . import rtl_text
    logical = rtl_text.to_logical(text) if rtl_text.is_visual(text) else text   # Sentences are read, not drawn.

    def rows(value):
        return [row.rstrip() for row, _ in wrap(value, hint_width(width), size, font, ordered=True)]

    def fits(cut):
        return len(rows(logical[:cut].rstrip() + "\u2026")) <= HINT_ROWS
    shown = rows(text)
    if len(shown) <= HINT_ROWS:
        return shown
    whole = [end for end in sentence_ends(logical) if fits(end)]
    if whole:
        return rows(logical[:whole[-1]] + "\u2026")
    low, high = 0, len(logical)   # The longest prefix that fits with its ellipsis (``fits(0)``: one row).
    while low < high:
        middle = (low + high + 1) // 2
        low, high = (middle, high) if fits(middle) else (low, middle - 1)
    # Where the script allows a break at that end (``text_layout.line_break``: a space between spaced words, any
    # character in Chinese or Japanese), but never before the last row's own text: an earlier space keeps no less
    # than the rows above (break-it R4M1-R2-L-1: "Blender " and a long Chinese run kept only "Blender…").
    from .text_layout import line_break
    last = rows(logical[:low].rstrip() + "\u2026")[-1]
    first_on_last = low - (len(rtl_text.to_logical(last) if rtl_text.is_visual(last) else last) - 1)
    cut = line_break(logical, low) if low else 0
    return rows(logical[:cut if first_on_last < cut <= low else low].rstrip() + "\u2026")


def attachment_rows(controller):
    """The composer's chip rows: its attachments and chosen tools, two to a row, and the goal chip's own full-width row
    (``chat_goal.chip_shown``; a defect there only leaves its row out, ``chat_guard``)."""
    from . import chat_guard
    chips = len(controller.attachments) + len(getattr(controller, "chosen_tools", ()) or ())
    goal_row = chat_guard.call("The goal chip", chat_goal.chip_shown, controller, default=False)
    return math.ceil(chips / 2) + (1 if goal_row else 0)


def composer_rows(draft, draft_lines, controller, width, size, wrap, font):
    """How many rows the composer holds: the draft's, or its hint's while the draft is empty (``chat_view.draw``)."""
    return len(draft_lines) if draft else len(hint_rows(hint(controller), width, size, wrap, font))


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


def vertical_caret(rows, caret, up, size, scale=None, current=None, x=None):
    """The caret one wrapped row up (``up``) or down in ``rows`` ((text, offset), ...) from row ``current`` (the row
    the caret is drawn on; by default the last row starting at or before it): the column nearest the same x (``x``,
    the goal a run of Up and Down keeps, else the caret's own), as drawn at ``size`` and ``scale``. A row wrapped at
    a space ends before it (``composer_edit.line_end``)."""
    if current is None:
        current = max((i for i, (_, offset) in enumerate(rows) if offset <= caret), default=0)
    target = max(0, min(len(rows) - 1, current + (-1 if up else 1)))
    here = column_x(rows[current][0], caret - rows[current][1], size, scale) if x is None else x
    row, start = rows[target]
    # A row wrapped at a space ends before it; one wrapped inside a word or CJK text keeps its last character.
    space = target + 1 < len(rows) and rows[target + 1][1] == start + len(row) and row[-1:].isspace()
    return start + min(column_at(row, here, size, scale), len(row) - 1 if space else len(row))

def draw(state, ui, controller, *, geometry, composer_bottom, composer_height, line, draft, draft_lines, size,
         line_height, width, attachment_rows, connected, waiting=False):
    """The composer. ``waiting``: no account yet, so the field is dimmed without a caret and only the toolbar (the +
    menu, Angelo's navigation) acts."""
    from . import chat_view as view
    from .chat_settings import clean_options, selected_model
    from .chat_popovers import effective_effort, effort_name, model_name, supports_fast
    cx, _, cw, _ = geometry["rect"]
    view.shape(cx, composer_bottom, cw, composer_height, ui_tokens.RAISED, geometry["radius"])
    state["composer_card"] = (cx, composer_bottom, cw, composer_height)   # Tooltips open clear of its text.
    caret = cursor_position(line) if line else 0
    from .composer_input import adopt, keep
    adopt(state, controller.state["id"])   # This chat's own scroll, from its first frame in this area.
    # The row the caret is drawn on: the earlier one at a wrap inside a word when the caret is upstream there.
    upstream = state.get("composer_upstream") == (draft, caret)
    caret_row = composer_edit.caret_row(draft_lines, caret, upstream)
    total = len(draft_lines) if draft else 0
    # Its own scroll: the caret's row only when the caret or the draft changed (composer_scroll; 1.0.9 report).
    first = composer_scroll.shown_first(state, draft_lines if draft else (), caret_row, (draft, caret, upstream),
                                        (width, size, view._scale))
    keep(state, controller.state["id"])   # As drawn here: another area showing this chat later starts from it.
    start_y = composer_bottom + composer_height - attachment_rows * 50 - line_height - 13
    state["composer"] = (24, composer_bottom + 48, width - 48, composer_height - 50 - attachment_rows * 50)
    rows = composer_scroll.ROWS
    composer_scroll.place(state, (TEXT_RECT, start_y - (rows - 1) * line_height - 3, width - 2 * TEXT_RECT,
                                  rows * line_height),
                          start_y, line_height, total)
    state["draft_rows"] = [(value, offset, start_y - i * line_height)
                           for i, (value, offset) in enumerate(draft_lines[first:first + rows])]
    state["draft_lines"], state["font_size"] = draft_lines, size
    if not draft:
        state["hint"] = hint(controller)   # What a GUI check reads: the hint and its rows as drawn.
        state["hint_rows"] = hint_rows(state["hint"], width, size, view.wrap, view._font)
        state["hint_width"] = hint_width(width)   # The rows' width, so a check knows what this chat could hold.
        for index, row in enumerate(state["hint_rows"]):
            view.text(row, DRAFT_LEFT, start_y - index * line_height, size,
                      ui_tokens.DISABLED_TEXT if waiting else ui_tokens.MUTED)
    else:
        with canvas_rtl.unmirrored():
            _draw_draft(view, state, size, line_height)
            composer_scroll.draw_bar(view, state)   # With the text: a left-to-right field in every language.
    state["text_scale"] = view._scale  # The draft is measured, clicked and drawn at this one scale (view.measure).
    caret_shown = first <= caret_row < first + rows   # Scrolled away from it: no caret drawn over another row.
    if line and not waiting and state.get("focused", True) and time.monotonic() % 1 < 0.5 and caret_shown:
        value, offset = draft_lines[caret_row]
        x = column_x(value, caret - offset, size)
        y = start_y - (caret_row - first) * line_height
        with canvas_rtl.unmirrored():
            view.shape(x, y - 3, 1, line_height - 3, ui_tokens.TEXT)
    goal_row = 0
    if chat_goal.chip_shown(controller):
        chat_goal.draw_chip(view, state, controller, 28, composer_bottom + composer_height - 48, width - 56)
        goal_row = 50
    store = getattr(controller, "references", None)
    for index, reference in enumerate(controller.attachments):
        chip_width = (width - 66) / 2
        x = 28 + (index % 2) * (chip_width + 10)
        y = composer_bottom + composer_height - 48 - (index // 2) * 50 - goal_row
        view.shape(x, y, chip_width, 40, ui_tokens.BUBBLE, 9)
        view.text(view.elide(reference["name"], chip_width - 30, 12), x + 10, y + 23, 12)
        problem = store.problem(reference) if store is not None and hasattr(store, "problem") else None
        if problem:   # Its text won't reach the agent: said on the chip, why and what to do in its tooltip.
            view.text(view.elide(iface_(UNREADABLE), chip_width - 20, ui_tokens.TYPE_MICRO), x + 10, y + 8,
                      ui_tokens.TYPE_MICRO, ui_tokens.WARNING)
        else:
            meta = iface_(CHIP_META, kind=iface_(kind_label(reference.get("kind"))), size=size_text(reference["bytes"]))
            view.text(view.elide(meta, chip_width - 20, ui_tokens.TYPE_MICRO), x + 10, y + 8, ui_tokens.TYPE_MICRO,
                      ui_tokens.MUTED)
        view.icon("close", x + chip_width - 12, y + 25)
        state["hits"].append(((x + chip_width - 26, y + 13, 26, 27), "remove_reference", index))
        canvas_controls.label(state, "remove_reference", index, LABELS["remove_reference"])
        if problem:   # After its x, which keeps its own hit.
            state["hits"].append(((x, y, chip_width - 26, 40), canvas_controls.INFO, problem))
    chosen = list(getattr(controller, "chosen_tools", ()) or ())
    for offset, choice in enumerate(chosen):   # Tools the person chose: teal chips after the attachments.
        index = len(controller.attachments) + offset
        chip_width = (width - 66) / 2
        x = 28 + (index % 2) * (chip_width + 10)
        y = composer_bottom + composer_height - 48 - (index // 2) * 50 - goal_row
        view.shape(x, y, chip_width, 40, ui_tokens.TEAL, 9)
        view.shape(x + 1, y + 1, chip_width - 2, 38, ui_tokens.TEAL_TINT, 8)   # Reads as an action.
        label, kind = chosen_tools.chip(choice)
        view.text(view.elide(label, chip_width - 30, 12), x + 10, y + 23, 12)
        view.text(view.elide(iface_(kind), chip_width - 20, ui_tokens.TYPE_MICRO), x + 10, y + 8, ui_tokens.TYPE_MICRO,
                  ui_tokens.TEAL)
        view.icon("close", x + chip_width - 12, y + 25)
        state["hits"].append(((x + chip_width - 26, y + 13, 26, 27), "remove_chosen", offset))
        canvas_controls.label(state, "remove_chosen", offset, LABELS["remove_chosen"])
    state["add_bottom"] = composer_bottom + composer_height + 8   # The + menu opens above the composer.
    options = clean_options(controller.state.get("options"))
    model = selected_model(controller)
    fast_supported = supports_fast(model)
    team = ui.connection_record(controller) if not (model or options["model"]) else None  # A team connection names itself.
    from .chat_start_states import default_label
    label = model_name(model) if model else options["model"] or (team["name"] if team and team.get("method") == "TEAM"
                                                                  else default_label(controller.selection_models))
    short_label = label
    label += " " + effort_name(effective_effort(model, options["effort"])) if model and model["efforts"] else ""
    label_inset = 21 if fast_supported else 9
    from .voice_ui import mic_visible
    show_mic = connected and mic_visible(controller)  # Off by default at 1.0; never shown where voice can't work.
    # During a turn a typed message has its own send arrow beside Stop: steered in, or queued (chat_pending).
    hold_send = controller.busy and bool(draft.strip() or controller.attachments) and controller.voice is None
    show_mic = show_mic and not hold_send   # Voice can't start during a turn; the arrow takes its place.
    chip_right = (geometry["mic_x"] if show_mic or hold_send else geometry["send_x"] - 10) - 24
    show_chip = bool(connected or ui.ready_connections())  # With nothing connected, the Connect card replaces it.
    label_approval = {"ASK": n_("Ask for approval"), "READ_ONLY": n_("Read only"), "AUTO_BOUNDED": n_("Approve for me")}[options["approval"]]
    if controller.trusted_scripts:
        label_approval = n_("Trusted scripts")
    elif controller.always_allow_scripts:   # Auto with the person's Always allow scripts: say scripts run unasked.
        label_approval = n_("Approve for me · scripts allowed")
    full_label = label
    approval_text, model_width, label = footer_fit(view, iface_(label_approval), label, short_label, label_inset,
                                                   chip_right, show_chip)
    model_x = chip_right - model_width
    state["model_label"] = full_label  # For window fixtures: the model and effort, as chosen.
    label = view.elide(label, model_width - label_inset - 14, 13)   # Whole unless even the icon alone leaves no room.
    cy = geometry["toolbar_y"]
    view.icon("plus", geometry["plus_x"], cy, ui_tokens.TEXT)
    state["hits"].append(((23, cy - 14, 28, 28), "attach", None))
    canvas_controls.label(state, "attach", None, LABELS["attach"])
    view.icon("approval", geometry["approval_x"] + 5, cy, ui_tokens.MUTED)
    if approval_text:
        view.text(approval_text, APPROVAL_TEXT_X, cy - 5, 13, ui_tokens.MUTED)
    # The control is its icon and its label: hover and focus outline what is drawn, never the empty run up to the
    # model chip (1.0.5 review: a ~470 px outline around a ~100 px label).
    approval_hit = APPROVAL_TEXT_X - 59 + (view.measure(approval_text, 13) + 10 if approval_text else 0)
    state["hits"].append(((59, cy - 14, approval_hit, 28), "approval", None))
    if not approval_text:   # Its icon alone in a narrow chat: the tooltip names the mode.
        canvas_controls.label(state, "approval", None, label_approval)
    state["picker_anchor"] = (model_x - 4, cy - 14, model_width + 10, 28)
    if show_chip:
        view.shape(*state["picker_anchor"], ui_tokens.HIGHLIGHT, 14)
        if fast_supported:
            view.icon("fast", model_x + 7, cy, ui_tokens.TEAL if options["fast"] else ui_tokens.TEXT)
            state["hits"].append(((model_x - 4, cy - 14, 25, 28), "fast", None))
            canvas_controls.label(state, "fast", None, LABELS["fast_on" if options["fast"] else "fast_off"])
        view.text(label, model_x + label_inset, cy - 5, 13, ui_tokens.MUTED)
        view.icon("chevron", model_x + model_width - 4, cy, ui_tokens.MUTED)
        state["hits"].append((state["picker_anchor"], "model", None))
    voice_active = controller.voice is not None or controller.status == "Starting voice"
    if show_mic or voice_active:
        view.icon("mic", geometry["mic_x"], cy, ui_tokens.RECORDING if voice_active else ui_tokens.TEXT)
        state["hits"].append(((geometry["mic_x"] - 14, cy - 14, 28, 28), "voice", None))
        canvas_controls.label(state, "voice", None, LABELS["voice_active" if voice_active else "voice"])
    if hold_send:
        view.shape(geometry["mic_x"] - 14, cy - 14, 28, 28, view.TEXT, 14)
        view.icon("arrow", geometry["mic_x"], cy, view.BG)
        state["hits"].append(((geometry["mic_x"] - 14, cy - 14, 28, 28), "send", None))
    enabled = bool(draft.strip() or controller.attachments or controller.busy)
    sx = geometry["send_x"]
    view.shape(sx - 14, cy - 14, 28, 28, ui_tokens.TEXT if enabled else ui_tokens.CONTROL_OFF, 14)
    if controller.busy:
        view.shape(sx - 4, cy - 4, 8, 8, ui_tokens.CANVAS, 2)
    else:
        view.icon("arrow", sx, cy, ui_tokens.CANVAS if enabled else ui_tokens.MUTED)
    state["hits"].append(((sx - 14, cy - 14, 28, 28), *(("stop" if controller.busy else "send", None) if enabled else (canvas_controls.DISABLED, canvas_controls.SEND_EMPTY))))
    if enabled:
        action = "stop" if controller.busy else "send"
        canvas_controls.label(state, action, None, LABELS[action])


def footer_fit(view, approval, label, short_label, label_inset, chip_right, show_chip, gap=GAP):
    """The toolbar's room in a narrow chat: (approval text or "", model chip width, model label).

    Nothing is ellipsized while it can be avoided: the model chip names the model whole (dropping its effort first,
    then a trailing parenthesised qualifier such as "(recommended)"; the model list keeps the full name),
    and the approval mode keeps its label only where it fits beside the chip; otherwise its icon stands alone (its
    popover names the mode). Only when even the icon alone leaves too little room is the model label elided.
    1.0.5 review: a 260 px chat drew "Approve …" and "Defau…" beside each other.
    """
    def natural(value):
        return view.measure(value, 13) + label_inset + 23

    def chip(value):
        return min(CHIP_MAX, natural(value)) if show_chip else 0
    plain = re.sub(r"\s*\([^()]*\)$", "", short_label) or short_label   # A trailing qualifier ("(recommended)") goes last.
    labels = list(dict.fromkeys((label, short_label, plain)))
    labels.sort(key=lambda value: natural(value) > CHIP_MAX)   # A label the widest chip holds whole comes first.
    approval_end = APPROVAL_TEXT_X + view.measure(approval, 13)
    for left_edge, text in ((approval_end, approval), (APPROVAL_ICON_END, "")):
        for value in labels:
            if left_edge <= (chip_right - chip(value) - 4 - gap if show_chip else chip_right):
                return text, chip(value), value
    return "", max(60, min(chip(label), chip_right - APPROVAL_ICON_END - gap - 4)) if show_chip else 0, label


def _draw_draft(view, state, size, line_height):
    """The typed text and its selection, as laid out (never shaped or mirrored: the caret follows the characters)."""
    for value, offset, y in state["draft_rows"]:
        selected = state.get("selection")
        if selected:
            begin, end = max(offset, selected[0]), min(offset + len(value), selected[1])
            if end > begin:
                left = column_x(value, begin - offset, size)
                view.shape(left, y - 3, column_x(value, end - offset, size) - left, line_height, ui_tokens.SELECTION)
        view.text(value.rstrip(), DRAFT_LEFT, y, size, shaped=False)
