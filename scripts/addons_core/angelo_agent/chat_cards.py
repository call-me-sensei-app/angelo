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
from .ui_text import iface_, n_

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
    record = jobs.record(result["job"]) if tool == "angelo_job_start" and isinstance(result.get("job"), str) else None
    for _ in range(16):   # A render whose rest was rendered later shows that job: its card is this row's.
        later = jobs.record(record["detail"].get("resumed_by")) if record is not None else None
        if later is None or (later["status"] == "failed" and "recovery" not in later["detail"]):
            break   # One refused at its start rendered nothing: this card keeps offering the rest.
        record = later
    return record


def _ui(state, record):
    return {"expanded": ("job", record["id"]) in state.get("expanded", ())}


def _measure(label, size):
    from . import chat_view
    return chat_view.measure(label, size)  # As drawn.


def _job_layout(message, record, state, x, top, width, wrap, line_height):
    from . import job_chat, job_ui
    return job_chat.card_layout(record, x, top, width, wrap, line_height, _ui(state, record),
                                has_output=job_ui.output_path(record) is not None, measure=_measure,
                                resume=job_ui.resume_state(record))


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


# Headless runs load the person's preferences (doc/angelo/external_mcp.rst, "Preferences"), so their add-ons run too.
HEADLESS_PREFERENCES = n_("Without Angelo's window, a file you always allow runs with your Angelo preferences: the "
                          "add-ons, extensions and script folders you turned on.")


def always_allow_rows(controller, call, wrap, width, size, font):
    """The note a script card shows above its standing-consent button, else ().

    Always allow scripts in an Auto chat (``offers_always_allow``); in an External conversation, an app's script
    file (``offers_script_file_allow``), with a line first when the file changed since the person allowed it."""
    if controller.offers_always_allow(call):
        return wrap(iface_("Always allow scripts runs this and future scripts in Auto chats without asking. Scripts "
                           "have full access to files, the network and other apps on this computer. You can turn it "
                           "off in Preferences."), width, size, font)
    from .script_files import shown_path
    rows = []
    # The full path: approvals are per real path, and two files can share a name.
    shown = shown_path(call.script_file.path) if getattr(call, "script_file", None) is not None else ""
    changed = controller.script_file_changed(call)
    if changed is not None:
        rows += wrap(iface_("{file} changed since you allowed it on {date}: review its new text.",
                            file=shown, date=changed.get("allowed_at", "")[:10]), width, size, font)
    if controller.offers_script_file_allow(call):
        rows += wrap(iface_("Always allow this file runs {file} for this app in this project without asking while its "
                            "text stays exactly the same; any change asks again. Scripts have full access to files, "
                            "the network and other apps on this computer. You can remove it in Preferences > AI "
                            "Providers. Files it reads or imports aren't covered: a change to them doesn't ask again.",
                            file=shown), width, size, font)
        rows += wrap(iface_(HEADLESS_PREFERENCES), width, size, font)
    return tuple(rows)


def always_allow_button(controller, call):
    """``(action, label)`` of the card's standing-consent button, or None."""
    if controller.offers_always_allow(call):
        return "script_always_allow", iface_("Always allow scripts")
    if controller.offers_script_file_allow(call):
        return "script_file_allow", iface_("Always allow this file while it's unchanged")
    return None


def draw_always_allow(call_id, state, x, y, button=None):
    """The standing-consent button at (``x``, ``y``), on its own row above Approve and Decline.

    ``chat_card_draw`` draws the note above it and reserves the row, so a long
    translation never pushes Approve or Decline off the card. ``button`` is
    ``always_allow_button``'s ``(action, label)``; the default is Always allow
    scripts (``script_always_allow``). Only its look follows the card.
    """
    from . import chat_card_draw, chat_view
    from .card_kit import LINE, PANEL_HOVER, TEXT
    action, label = button or ("script_always_allow", iface_("Always allow scripts"))
    width = max(92, chat_card_draw._measure(label, 12) + 28)
    chat_view.shape(x, y, width, 28, LINE, 8)
    chat_view.shape(x + 1, y + 1, width - 2, 26, PANEL_HOVER, 7)
    chat_view.text(label, x + 14, y + 9, 12, TEXT)
    state["hits"].append(((x, y, width, 28), action, call_id))


def dispatch(context, controller, state, action, value):
    """A flow, job or script card click, on the main thread."""
    if action == "script_always_allow":
        from .trusted_scripts_ui import always_allow_from_card
        always_allow_from_card(context, controller, value)
        return
    if action == "script_file_allow":
        from .trusted_scripts_ui import script_file_allow_from_card
        script_file_allow_from_card(context, controller, value)
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
    elif action == "job_resume":   # A disabled one (another file open, say) is never dispatched.
        record = job_ui.manager().record(value)
        if record is not None:
            job_ui.resume(record, controller, context.window)
    elif action == "job_details":
        state.setdefault("expanded", set()).symmetric_difference_update({("job", value)})
    job_ui.redraw()
