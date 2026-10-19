# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cards attached below an activity row (a Generation Flow run, a background job), and their input.

``chat_view`` asks here for a card's height and draws it below its row, and sends
clicks on ``flow_*``, ``job_*`` and ``script_*`` hits back here. The card modules
keep their own pure layouts (``flow_chat.run_layout``, ``job_chat.card_layout``);
hover, pressed, keyboard focus and disabled states are ``canvas_controls``' for
every hit on the canvas. A script's approval card also gets its Always allow
scripts part here (``always_allow_rows``, ``draw_always_allow``).
"""

from .job_spec import PREVIEW
from .legacy_names import current_tool_name
from .ui_text import iface_

def job_record(message):
    """The live job record behind an ``angelo_job_start`` activity row, or a running look's, or None."""
    if message.get("role") != "activity":
        return None
    tool, result = current_tool_name(message.get("tool", "")), message.get("result") or {}
    from .job_manager import shared
    jobs = shared()
    if jobs is None:
        return None
    if tool == "angelo_view":
        # A RENDERED look (view_render) is a job only while it renders: found by its call, gone when it ends.
        active = jobs.active() if message.get("status") == "running" else None
        return active if active is not None and active["kind"] == PREVIEW and active["call_id"] == message.get("call_id") else None
    return jobs.record(result["job"]) if tool == "angelo_job_start" and isinstance(result.get("job"), str) else None


def _ui(state, record):
    return {"expanded": ("job", record["id"]) in state.get("expanded", ())}


def _measure(label, size):
    import blf
    from . import chat_view
    blf.size(chat_view._font, size)
    return blf.dimensions(chat_view._font, label)[0]


def _job_layout(message, record, state, x, top, width, wrap, line_height):
    from . import job_chat, job_ui
    return job_chat.card_layout(record, x, top, width, wrap, line_height, _ui(state, record),
                                has_output=job_ui.output_path(record) is not None, measure=_measure)


def attached_height(message, width, wrap, line_height, state):
    """The height of the card below this activity row (0 when it has none)."""
    from . import flow_chat
    if flow_chat.has_card(message):
        return flow_chat.run_layout(message, 16, 0, width, wrap, line_height)[1]
    record = job_record(message)
    return _job_layout(message, record, state, 16, 0, width, wrap, line_height)[1] if record else 0


def draw_attached(message, state, x, top, width, wrap, line_height):
    from . import chat_view, flow_chat, studio
    if flow_chat.has_card(message):
        ops, _ = flow_chat.run_layout(message, x, top, width, wrap, line_height)
        flow_chat.draw(ops, state["hits"], chat_view.elide, chat_view.shape, chat_view.text, studio.thumbnail)
        return
    record = job_record(message)
    if record is None:
        return
    ops, _ = _job_layout(message, record, state, x, top, width, wrap, line_height)
    for op in ops:
        kind = op[0]
        if kind == "rect":
            chat_view.shape(*op[1:7])
        elif kind == "text":
            _, value, tx, ty, size, color, limit = op
            chat_view.text(chat_view.elide(value, max(8, limit), size), tx, ty, size, color)
        elif kind == "hit":  # canvas_controls draws its hover, pressed, focus and disabled states.
            state["hits"].append(op[1:])


def always_allow_rows(controller, call, wrap, width, size, font):
    """The note a script card shows with Always allow scripts (``offers_always_allow``: an Auto chat), else ()."""
    if not controller.offers_always_allow(call):
        return ()
    return wrap(iface_("Always allow scripts runs this and future scripts in Auto chats without asking. Scripts have "
                       "full access to files, the network and other apps on this computer. You can turn it off in "
                       "Preferences."), width, size, font)


def draw_always_allow(call_id, state, x, y):
    """The Always allow scripts button at (``x``, ``y``), on its own row above Approve and Decline.

    ``chat_card_draw`` draws the note above it and reserves the row, so a long
    translation never pushes Approve or Decline off the card. The button keeps
    its action, ``script_always_allow``; only its look follows the card.
    """
    from . import chat_card_draw, chat_view
    from .card_kit import LINE, PANEL_HOVER, TEXT
    label = iface_("Always allow scripts")
    width = max(92, chat_card_draw._measure(label, 12) + 28)
    chat_view.shape(x, y, width, 28, LINE, 8)
    chat_view.shape(x + 1, y + 1, width - 2, 26, PANEL_HOVER, 7)
    chat_view.text(label, x + 14, y + 9, 12, TEXT)
    state["hits"].append(((x, y, width, 28), "script_always_allow", call_id))


def dispatch(context, controller, state, action, value):
    """A flow, job or script card click, on the main thread."""
    if action == "script_always_allow":
        from .trusted_scripts_ui import always_allow_from_card
        always_allow_from_card(context, controller, value)
        return
    if action.startswith("flow_"):
        from . import flow_chat
        flow_chat.dispatch(context, controller, action, value)
        return
    from . import job_ui
    if action == "job_cancel":
        job_ui.manager().cancel(value, by="user")
    elif action == "job_open":  # A moved output's button is disabled with its reason, never dispatched.
        record = job_ui.manager().record(value)
        if record is not None and job_ui.output_path(record) is not None:
            job_ui.open_output(record)
    elif action == "job_details":
        state.setdefault("expanded", set()).symmetric_difference_update({("job", value)})
    job_ui.redraw()
