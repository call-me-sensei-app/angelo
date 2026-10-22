# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's welcome card and provider notices, drawn inside the native chat canvas.

An empty chat without a connection lists the connect options of
``first_run.connect_options`` (the Angelo team's row first, from
``team_account_ui.welcome_row``; then detected subscriptions and an API key,
unless the managed policy allows only team connections) and a quiet link to
Preferences.
A connected empty chat offers example prompts and, under them, one tip
(``first_run.TIP``) that links to the website's docs section explaining it. A
provider limit or connection failure gets a readable headline with Switch
connection.

Every choice is drawn the same way, as the console's ghost button: a faint
fill and a hairline (``ghost``). A sign-in in progress turns its row teal with a
spinner and a Cancel button; a failure keeps the row, says why in coral, and a
click tries again. Hover, pressed and focus states come from
``canvas_controls``; "More options in Preferences" and the tip are links, drawn
by one function (``_draw_link``).

Layout is measured with the real font: every string is translated first, rows
wrap instead of truncating, and button rows flow onto more lines, so longer
languages fit. Drawing reads only cached state (statuses, flows, the
transcript); it never touches disk, starts processes or changes Blender data.
"""

import math
import time

import bpy
import gpu

from . import canvas_controls, first_run, quick_connect
from .display_text import shown
from .ui_text import iface_

FAILED = (0.937, 0.533, 0.412, 1)  # --coral-500
GHOST_FILL = (0.902, 1.0, 0.957, 0.04)  # .btn--ghost
GHOST_LINE = (0.953, 0.925, 0.886, 0.14)  # --line-2
TEAL = (0.153, 0.741, 0.749, 1)  # --teal-500
WORKING_FILL = (0.153, 0.741, 0.749, 0.10)
WORKING_LINE = (0.153, 0.741, 0.749, 0.55)
PAD = 14


def _options(ui):
    from . import team_account_ui
    ready = {r["mode"] for r in ui.connections().records.values() if ui.connection_ready(r)}
    rows = first_run.connect_options(ui._authentication.status, ready, team=team_account_ui.welcome_row())
    return team_account_ui.team_only_options(rows)


def _width(view, text, size):
    return view.measure(text, size)  # As drawn.


def _lines(view, text, width, size):
    return [value.rstrip() for value, _ in view.wrap(text, max(40, width), size, view._font)] if text else []


def _option_lines(view, option, column, size):
    """Translated (label, note, note colour, working, cancel action) of one connect option.

    Working is a Connect flow in progress or a row marked ``busy`` (the team sign-in); its Cancel
    button runs ``welcome_cancel`` for a Connect flow, else the row's own ``cancel`` action, if any.
    """
    item = quick_connect.flow(option["value"]) if option["action"] == "welcome_connect" else None
    working = (item is not None and item["phase"] not in {"failed", "done"}) or bool(option.get("busy"))
    cancel = ("welcome_cancel" if item else option.get("cancel")) if working else None
    label = iface_(first_run.phase_text(item["phase"], option["value"])) if working and item else shown(option["label"])
    if item and item["phase"] == "failed":
        note, colour = iface_(item["reason"], **item["fields"]), FAILED
    elif working and item:
        note, colour = "", view.MUTED  # The label already says what is happening.
    else:
        note, colour = shown(option["note"]), view.MUTED
    inner = column - 2 * PAD
    return _lines(view, label, inner, size), _lines(view, note, inner, size - 3), colour, working, cancel


def _option_height(lines, size):
    labels, notes, _, _, cancel = lines
    return PAD + len(labels) * (size + 6) + len(notes) * (size + 2) + (size + 18 if cancel else 0) + PAD - 4


def ghost(view, rect, radius, working=False):
    """The console's ghost button: a faint fill and a hairline; teal while something runs."""
    gpu.state.blend_set('ALPHA')  # Text drawing resets blending; the fill is translucent.
    view.shape(*rect, WORKING_FILL if working else GHOST_FILL, radius)
    canvas_controls.ring(*rect, radius, 1, WORKING_LINE if working else GHOST_LINE)


def spinner(view, cx, cy, radius=7):
    """Eight dots, the brightest turning; still with Reduce Motion."""
    still = getattr(bpy.context.preferences.view, "use_reduce_motion", False)
    head = 0 if still else int(time.monotonic() * 10) % 8
    gpu.state.blend_set('ALPHA')
    for index in range(8):
        angle = math.radians(90 - index * 45)
        alpha = 1 - ((head - index) % 8) * 0.11
        view.shape(cx + radius * math.cos(angle) - 1.6, cy + radius * math.sin(angle) - 1.6, 3.2, 3.2,
                   TEAL[:3] + (alpha,), 1.6)


def pill_button(view, hits, label, x, y, size, action, value):
    """A small ghost pill with a centred label; returns its width."""
    width = _width(view, label, size) + 24
    rect = (x, y, width, size + 12)
    ghost(view, rect, rect[3] / 2)
    view.text(label, x + 12, y + 7, size)
    hits.append((rect, action, value))
    return width


def draw_empty(state, ui, width, height, floor, size, connected):
    """Heading plus either the connect options or example prompts, kept above ``floor``."""
    from . import chat_view as view
    hits = state["hits"]
    column = max(120, min(420, width - 48))
    heading = _lines(view, iface_("What would you like to make?"), column, min(21, size + 5))
    from . import managed_policy, team_chat
    if connected:
        line = iface_("Try one of these, or describe anything. Your scene stays visible while the agent works.")
    elif managed_policy.current().team_only:  # No personal account can be connected: the team is the way in.
        line = iface_("Angelo starts once your team's connection is ready. Your scene stays visible while the agent works.")
    else:
        line = iface_("Connect an AI account to start. Your scene stays visible while the agent works.")
    subtitle = _lines(view, line, column, size - 1)
    # On the team's connection the chat says so, in one designed line.
    team_line = team_chat.ready_line(ui.connection_record()) if connected else None
    team_lines = _lines(view, shown(team_line), column - 40, size - 1) if team_line else []
    state["team_ready_line"] = str(team_line) if team_line else None  # For window fixtures.
    if connected:
        state["drawn_options"] = []
        blocks = [("example", index, _lines(view, iface_(prompt), column - 28, size - 1))
                  for index, prompt in enumerate(first_run.EXAMPLES)]
        heights = [len(lines) * (size + 4) + 18 for _, _, lines in blocks]
    else:
        options = _options(ui)
        state["drawn_options"] = [(o["id"], o["label"], o["note"]) for o in options]  # For window fixtures.
        blocks = [("option", option, _option_lines(view, option, column, size)) for option in options]
        heights = [_option_height(lines, size) for _, _, lines in blocks]
        if [option["id"] for option in options] == ["api_key"]:  # Say which CLI would enable one-click connect.
            blocks.insert(0, ("text", None, _lines(view, iface_(first_run.NO_CLI), column, size - 2)))
            heights.insert(0, len(blocks[0][2]) * (size + 4) + 10)
    # One tip under the examples, linked to the docs section that explains it.
    tip = _lines(view, iface_(first_run.TIP), column, size - 3) if connected else []
    state["drawn_tip"] = (first_run.TIP, first_run.TIP_URL) if tip else None  # For window fixtures.
    banner = len(team_lines) * (size + 4) + 20 if team_lines else 0
    # Connected but no model: why, and the one action that fixes it (chat_start_states), above the examples.
    card = model_card(ui, ui.get_controller(), state) if connected else None
    state["drawn_model_card"] = card["title"] if card else None  # For window fixtures.
    card_height = _notice(view, card, 24, 0, column, state, False)[0] + 14 if card else 0
    total = (len(heading) * (size + 12) + len(subtitle) * (size + 6) + 20 + sum(h + 10 for h in heights) + 40 + banner
             + card_height + (_link_height(len(tip), size) if tip else 0))
    y = max(min(height * 0.62, height - 40), floor + total)
    for line in heading:
        view.text(line, 24, y, min(21, size + 5))
        y -= size + 12
    if team_lines:  # Teal like a running row, with a check: the team is ready.
        rect = (24, y - banner + size + 2, column, banner - 6)
        ghost(view, rect, 12, working=True)
        view.icon("check", 24 + 20, rect[1] + rect[3] / 2, TEAL)
        for index, line in enumerate(team_lines):
            view.text(line, 24 + 36, rect[1] + rect[3] - 10 - size + 2 - index * (size + 4), size - 1)
        y -= banner + 4
    for line in subtitle:
        view.text(line, 24, y, size - 1, view.MUTED)
        y -= size + 6
    y -= 14
    if card:
        _notice(view, card, 24, y, column, state, True)
        y -= card_height
    for (kind, value, lines), block_height in zip(blocks, heights):
        if kind == "example":
            rect = (24, y - block_height, column, block_height)
            ghost(view, rect, 12)
            for index, line in enumerate(lines):
                view.text(line, 38, y - 22 - index * (size + 4), size - 1)
            hits.append((rect, "welcome_example", value))
        elif kind == "text":
            for index, line in enumerate(lines):
                view.text(line, 24, y - 14 - index * (size + 4), size - 2, view.MUTED)
        else:
            _draw_option(view, hits, value, lines, (24, y - block_height, column, block_height), size)
        y -= block_height + 10
    if tip:
        y -= _draw_link(view, state, tip, y, size, "url", first_run.TIP_URL)
    personal = managed_policy.current().allow_personal_connections
    if not connected or (team_line and personal):
        # On the team's connection, personal accounts stay one link away, and only where the policy allows them.
        label = iface_("More options") if connected else iface_("More options in Preferences")
        _draw_link(view, state, [label], y, size, "settings", None)


def model_card(ui, controller, state):
    """The welcome's model card (``chat_start_states.model_card``), or None when the chat has a model."""
    from . import chat_start_states as states
    from .chat_settings import clean_options, selected_model
    options = clean_options(controller.state.get("options"))
    model = selected_model(controller)
    record = ui.connection_record(controller) if not (model or options["model"]) else None
    if not states.needs_model(model, options["model"], bool(record and record.get("method") == "TEAM")):
        return None
    loading, error = ui.model_catalog_notice(controller)
    problem = states.model_problem(controller.selection_models, ui.model_choices(controller), loading, error)
    return states.model_card(problem, error, states.EXPANDED in state.get("expanded", ())) if problem else None


def _notice(view, card, x, top, width, state, draw):
    """Lay out (``draw``: and draw) a kit notice on the chat canvas; its Details is the chat's expand action."""
    from . import chat_start_states as states
    from . import ui_kit_status
    pointer = dict(state)
    for name in ("hover", "pressed", "focus"):
        if pointer.get(name) == ("expand", states.EXPANDED):
            pointer[name] = (states.DETAILS, None)
    height, hits = ui_kit_status.notice(view, x, top, width, 1.0, pointer if draw else None, draw, **card)
    if draw:
        state["hits"].extend((rect, "expand", states.EXPANDED) if action == states.DETAILS else (rect, action, value)
                             for rect, action, value in hits)
    return height, hits


def _link_height(count, size):
    return 22 + (count - 1) * (size + 1)


def _draw_link(view, state, lines, y, size, action, value):
    """The card's one link style, as in the console: mint and underlined, brighter on hover.

    Draws wrapped ``lines`` (already translated) from ``y`` down, one hit for all of them, and returns the
    height used.
    """
    lit = canvas_controls.state_of(state, action, value) in {"hover", "pressed"}
    colour = canvas_controls.LINK_HOVER if lit else canvas_controls.LINK
    widest = 0
    for index, line in enumerate(lines):
        width = _width(view, line, size - 3)
        widest = max(widest, width)
        baseline = y - 12 - index * (size + 1)
        view.text(line, 24, baseline, size - 3, colour)
        view.stroke(24, baseline - 3, 24 + width, baseline - 3, colour, 1)
    height = _link_height(len(lines), size)
    state["hits"].append(((20, y + 3 - height, widest + 8, height), action, value))
    canvas_controls.style(state, action, value, "link")
    return height


def _draw_option(view, hits, option, lines, rect, size):
    labels, notes, colour, working, cancel = lines
    x, bottom, column, block_height = rect
    ghost(view, rect, 12, working)  # One style for every choice; the order says which comes first.
    y = bottom + block_height - PAD - size + 2
    if working:
        spinner(view, x + column - PAD - 8, y + size / 2 - 1)
    for line in labels:
        view.text(line, x + PAD, y, size)
        y -= size + 6
    for line in notes:
        view.text(line, x + PAD, y + 2, size - 3, colour)
        y -= size + 2
    if cancel:
        pill_button(view, hits, iface_("Cancel"), x + PAD, y - 12, size - 3, cancel, option["value"])
    elif not working:
        hits.append((rect, option["action"], option["value"]))


def _flow(view, items, width, size, x0=16, gap=8, row_height=30):
    """Button rectangles placed left to right, wrapping to new rows; returns (placed, total height)."""
    placed, x, row = [], x0, 0
    for label, action, value in items:
        button = min(width - 2 * x0, _width(view, label, size - 2) + 24)
        if x > x0 and x + button > width - x0:
            x, row = x0, row + 1
        placed.append(((x, row, button), label, action, value))
        x += button + gap
    rows = row + 1 if placed else 0
    return placed, rows * (row_height + gap)


def _draw_flow(view, hits, placed, top, size, row_height=30, gap=8):
    for (x, row, button), label, action, value in placed:
        y = top - (row + 1) * (row_height + gap) + gap
        ghost(view, (x, y, button, row_height), row_height / 2)
        view.text(view.elide(label, button - 20, size - 2), x + 12, y + 10, size - 2)
        hits.append(((x, y, button, row_height), action, value))


def draw_reconnect_row(state, ui, width, y, size):
    """Connect options above the composer when a chat with history has no usable connection."""
    from . import chat_view as view
    hits = state["hits"]
    working = next(((mode, item) for mode, item in quick_connect._flows.items()
                    if item["phase"] not in {"failed", "done"}), None)
    if working:
        mode, item = working
        cancel = iface_("Cancel")
        view.shape(0, y - 4, width, 40, view.BG)
        spinner(view, 32, y + 16)
        view.text(view.elide(iface_(first_run.phase_text(item["phase"], mode)), width - 160, size - 1), 48, y + 12, size - 1, view.MUTED)
        pill_button(view, hits, cancel, width - 24 - _width(view, cancel, size - 3) - 24, y + 4, size - 3, "welcome_cancel", mode)
        return
    items = [(shown(option["label"]), option["action"], option["value"]) for option in _options(ui)]
    placed, flow_height = _flow(view, items, width, size, x0=24)
    view.shape(0, y - 4, width, flow_height + 4, view.BG)
    _draw_flow(view, hits, placed, y + flow_height, size)


def reconnect_height(ui, width, size):
    """Height the reconnect row needs above the composer, so the transcript ends above it instead of under it."""
    from . import chat_view as view
    if any(item["phase"] not in {"failed", "done"} for item in quick_connect._flows.values()):
        return 44
    items = [(shown(option["label"]), option["action"], option["value"]) for option in _options(ui)]
    return _flow(view, items, width, size, x0=24)[1] + 12


def notice_for(message, latest):
    """The display-only notice for an error row; actions only on the latest row. Stored text is unchanged."""
    if message.get("role") != "error":
        return None
    found = first_run.notice(message.get("text"))
    if not found:
        return None
    return dict(found, actions=latest)


def _notice_items(ui, controller):
    """Switch connection, plus Connect/Use for each detected subscription this chat isn't using.

    Without a usable connection the row above the composer already offers the
    connect options, and a picker with nothing in it would be a dead button.
    """
    if not ui.connection_available() or not ui.ready_connections():
        return []
    current = controller.selection_provider
    return [(iface_("Switch connection"), "welcome_switch", None)] + [
        (shown(option["label"]), option["action"], option["value"]) for option in _options(ui)
        if option["action"] == "welcome_connect" and option["value"] != current]


notice_items = _notice_items  # The transcript's notice cards (chat_notices) draw these as kit buttons.


def sign_in_items(ui, controller):
    """An expired sign-in's actions: the team's sign-in row and this chat's own Connect row, as on the welcome card."""
    current = controller.selection_provider
    return [(shown(option["label"]), option["action"], option["value"]) for option in _options(ui)
            if option["action"] == "welcome_team" or (option["action"] == "welcome_connect" and option["value"] == current)]


def actions_height(notice, ui, controller, width, size):
    """Vertical space an error row reserves for its notice actions."""
    from . import chat_view as view
    if not notice or not notice["actions"]:
        return 0
    return _flow(view, _notice_items(ui, controller), width, size)[1] + 8


def draw_notice_actions(state, ui, controller, bottom, width, size):
    from . import chat_view as view
    placed, flow_height = _flow(view, _notice_items(ui, controller), width, size)
    _draw_flow(view, state["hits"], placed, bottom + 8 + flow_height, size)


def dispatch(context, controller, state, action, value):
    from . import ui
    if action == "welcome_connect":
        bpy.ops.angelo.quick_connect(mode=value)
    elif action == "welcome_cancel":
        bpy.ops.angelo.quick_connect_cancel(mode=value)
    elif action == "welcome_team":
        from . import team_account_ui
        team_account_ui.welcome_action(value)
    elif action == "welcome_team_cancel":
        bpy.ops.angelo.team_cancel_sign_in()
    elif action == "welcome_api_key":
        quick_connect.watch_key_form(ui, controller)
        from .providers_sheet import open_over_chat  # The connection sheet over the 3D view (never Preferences).
        open_over_chat(method='API_KEY')
    elif action == "welcome_example":
        bpy.ops.angelo.chat_example(index=value)
        state["focus_request"] = True
    elif action == "welcome_choose_model":   # The model card's action: the model list, as from the composer.
        from . import chat_view
        if open_models(ui, controller):
            chat_view.show_popover(context, "models")
    elif action == "welcome_retry_models":
        bpy.ops.angelo.refresh_models()
    elif action == "welcome_switch":
        from . import chat_view
        if ui.ready_connections():
            chat_view.show_popover(context, "models")


def open_models(ui, controller):
    """Bind the chat and load its models before the model list opens; a failure becomes the chat's notice row."""
    if not (ui.connection_available() or ui.ready_connections()):
        return False
    try:
        if not controller.busy and ui.connection_available():
            ui.prepare_controller()
            controller = ui.get_controller()
        if not controller.models and not controller.models_loading:
            controller.refresh_models()
        ui.refresh_connected_models()
    except (RuntimeError, ValueError, OSError) as error:
        from .display_text import error_notice
        controller.emit("message", {"role": "error", **error_notice(error)})
        return False
    return True
