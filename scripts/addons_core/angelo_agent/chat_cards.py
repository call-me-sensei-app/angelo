# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cards attached below an activity row (a Generation Flow run, a background job), and their input.

``chat_view`` asks here for a card's height and draws it below its row, and sends
clicks on ``flow_*``, ``job_*`` and ``script_*`` hits back here. The card modules
keep their own pure layouts (``flow_chat.run_layout``, ``job_chat.card_layout``);
hover, pressed, keyboard focus and disabled states are ``canvas_controls``' for
every hit on the canvas. A script's approval card also gets its Always allow
scripts part here: its standing-consent note and buttons, Allow for this session and Always allow
(``always_allow_rows``, ``always_allow_button``, ``draw_always_allow``).
"""

import sys

from .job_spec import PREVIEW
from .legacy_names import current_tool_name
from .ui_text import iface_, n_, notice

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




def always_allow_rows(controller, call, wrap, width, size, font):
    """The note a script card shows above its standing-consent buttons, else ().

    Allow for this session (``offers_session_allow``) and Always allow (``offers_always_allow``), in every chat, an
    External conversation's too."""
    rows = []
    if controller.offers_session_allow(call) or controller.offers_always_allow(call):
        rows += wrap(iface_("Allow for this session runs this chat's scripts without asking until Angelo restarts. "
                            "Always allow runs Angelo's agent's scripts in every Auto chat, connected apps' chats "
                            "included, until you turn it off in Preferences. Scripts have full access to files, the "
                            "network and other apps on this computer."), width, size, font)
    return tuple(rows)


def always_allow_button(controller, call):
    """``((action, label), ...)``: the card's standing-consent buttons, in order, or () when it offers none."""
    buttons = []
    if controller.offers_session_allow(call):
        buttons.append(("script_session_allow", iface_("Allow for this session")))
    if controller.offers_always_allow(call):
        buttons.append(("script_always_allow", iface_("Always allow")))
    return tuple(buttons)


def button_width(label):
    from . import chat_card_draw
    return max(92, chat_card_draw._measure(label, 12) + 28)


def standing_rows(buttons, width):
    """The standing-consent buttons laid out in rows that fit ``width`` (the card's inner width from x = 32), top
    row first: ``((button, x offset), ...)`` per row. A button wider than the row gets a row of its own."""
    rows, row, x = [], [], 0
    for button in buttons or ():
        w = button_width(button[1])
        if row and x + w > width:
            rows.append(tuple(row))
            row, x = [], 0
        row.append((button, x))
        x += w + 8
    if row:
        rows.append(tuple(row))
    return tuple(rows)


def draw_always_allow(call_id, state, x, y, button=None):
    """One standing-consent button at (``x``, ``y``), in its row above Run once and Decline; returns its right edge.

    ``chat_card_draw`` draws the note above the buttons and reserves their rows
    (``standing_rows``), so a long translation never pushes Run once or Decline
    off the card. ``button`` is one of ``always_allow_button``'s ``(action,
    label)``; the default is Always allow (``script_always_allow``). Only its
    look follows the card.
    """
    from . import chat_view, ui_tokens as tk
    action, label = button or ("script_always_allow", iface_("Always allow"))
    width = button_width(label)
    chat_view.shape(x, y, width, 28, tk.LINE, 8)
    chat_view.shape(x + 1, y + 1, width - 2, 26, tk.PANEL_HOVER, 7)
    chat_view.text(label, x + 14, y + 9, 12, tk.TEXT)
    state["hits"].append(((x, y, width, 28), action, call_id))
    return x + width


def standing_line(controller):
    """``(text, kind)`` of the chat's line while a standing consent runs its scripts without asking, else None:
    the person always sees it, and its Turn off ends it (``script_standing_off``)."""
    consent = controller.standing_script_consent() if hasattr(controller, "standing_script_consent") else None
    if consent is None:
        return None
    kind = consent[0]
    text = (iface_("Scripts in this chat run without asking for this session") if kind == "session"
            else iface_("Scripts run without asking in Auto chats"))
    return text, kind


def script_rule(controller, approval):
    """The rule this chat's scripts follow now, in one or two short sentences: the Approvals popover's footer. Says
    the organization's lock, Ask and Read only first, then the standing consent that runs scripts unasked, if any."""
    from . import managed_policy
    if approval == "READ_ONLY":
        return iface_("Read only: scripts and other changes don't run.")
    if not managed_policy.current().allow_auto_approval:
        return iface_("Your organization requires your review for every script.")
    consent = controller.standing_script_consent() if hasattr(controller, "standing_script_consent") else None
    if consent is not None and consent[0] == "session":   # Trust scripts for this chat: Ask and Auto alike.
        return iface_("This chat's scripts run without asking for this session.")
    if approval == "ASK":
        return iface_("Every script asks for your review before it runs.")
    if consent is not None:
        return iface_("Scripts run without asking in Auto chats.")
    return iface_("Each script asks: run it once, or allow it for this session or always.")


def draw_rule(controller, approval, wrap, text, rect, busy, font):
    """The Approvals popover's footer at the bottom of ``rect`` (x, y, width): this chat's real rule for scripts
    (``script_rule``), up to two lines, and while a turn runs, when a change applies."""
    from . import ui_tokens as tk
    x, y, w = rect
    footer = wrap(script_rule(controller, approval), w - 26, 11, font)[:2]
    for i, (line, _) in enumerate(reversed(footer)):
        text(line, x + 13, y + 19 + i * 15, 11, tk.MUTED)
    if busy:
        text(iface_("Applies to queued / future changes"), x + 13, y + 21 + len(footer) * 15, 11, tk.MUTED)


def draw_standing_line(state, width, y, text, kind):
    """The consent's line at ``y``, centred: what runs unasked, then its Turn off (a narrowing: no arming)."""
    from . import chat_card_draw, chat_view, ui_tokens as tk
    off = iface_("Turn off")
    off_width = chat_card_draw._measure(off, 13)
    room = max(40, width - 32 - 24 - off_width - 18)
    shown = chat_view.elide(text, room, 13)
    pill_width = min(width - 32, 12 + chat_card_draw._measure(shown, 13) + 18 + off_width + 14)
    x = (width - pill_width) / 2
    chat_view.shape(x, y, pill_width, 35, tk.LINE, 14)
    chat_view.shape(x + 1, y + 1, pill_width - 2, 33, tk.PANEL, 13)
    chat_view.text(shown, x + 12, y + 12, 13, tk.MUTED)
    off_x = x + pill_width - 14 - off_width
    chat_view.text(off, off_x, y + 12, 13, tk.TEXT)
    state["standing_line"] = text   # For QA state asserts: the line as drawn.
    state["hits"].append(((off_x - 8, y + 3, off_width + 16, 29), "script_standing_off", kind))


def dispatch(context, controller, state, action, value):
    """A flow, job or script card click, or the pointer to a waiting approval card, on the main thread."""
    if action == "approval_switch":
        from . import launcher_waiting
        launcher_waiting.select(value)
        return
    if action == "approval_reveal":   # approval_wait.draw_pointer: the next draw scrolls the card into view.
        from . import approval_wait
        approval_wait.reveal(state, controller)
        return
    from .review_again import shielded
    if action in {"script_session_allow", "script_always_allow"} and shielded(value):
        return   # A card asked again a moment ago: never a consent the person has not seen (review_again).
    if action == "script_standing_off":   # Narrowing: the chat's line's Turn off.
        from .trusted_scripts_ui import turn_off_standing
        return turn_off_standing(controller, value)
    if action == "script_session_allow":
        from .trusted_scripts_ui import session_allow_from_card
        return bool(session_allow_from_card(context, controller, value))
    if action == "script_always_allow":
        from .trusted_scripts_ui import always_allow_from_card
        return bool(always_allow_from_card(context, controller, value))
    if action.startswith("flow_"):
        from . import flow_chat
        return flow_chat.dispatch(context, controller, action, value)
    from . import job_ui
    done = True
    if action == "job_cancel":
        done = job_ui.manager().cancel(value, by="user") is not None
    elif action == "job_open":  # A moved output's button is disabled with its reason; one moved since says so here.
        record = job_ui.manager().record(value)
        done = record is not None and job_ui.output_path(record) is not None
        if done:
            job_ui.open_output(record)
    elif action == "job_resume":   # A disabled one (another file open, say) is never dispatched.
        record = job_ui.manager().record(value)
        done = record is not None and job_ui.resume(record, controller, context.window) is not None
    elif action == "job_details":
        state.setdefault("expanded", set()).symmetric_difference_update({("job", value)})
    job_ui.redraw()
    return done


# A card button that did nothing says why in the chat, as an error notice row (``chat_notices``): one plain sentence
# per button. An exception that carries Angelo's own translatable sentence (a ``display_text.Text``) says that one
# instead; raw exception text is never shown (it is logged). ``controller.status`` is no channel for this: it is drawn
# only while a turn runs.
REFUSED = {
    "open_studio": n_("Media Studio couldn't open here. Try again in a moment."),
    "flow_toggle": n_("This run's steps can't be changed now: it is no longer waiting for your approval."),
    "flow_stop": n_("This run's steps can't be changed now: it is no longer waiting for your approval."),
    "flow_choose": n_("That result couldn't be chosen for its step. The flow may have changed since it ran."),
    "flow_attach": n_("That image couldn't be attached. It may not be ready yet, or the message already holds "
                      "as many files as it can."),
    "flow_stop_run": n_("This run had already finished, so there was nothing to stop."),
    "job_cancel": n_("This background job couldn't be stopped. It may have ended already."),
    "job_open": n_("The file this job made couldn't be opened. It may have been moved or deleted."),
    "job_resume": n_("The missing frames weren't rendered. Check the job's card, then try again."),
    "script_standing_off": n_("Scripts still run without asking: turning it off was refused. Try again in a moment."),
    "script_session_allow": n_("Scripts weren't allowed: this script is no longer waiting, or a script is running "
                               "right now."),
    "script_always_allow": n_("Scripts weren't allowed: this script is no longer waiting, or a script is running "
                              "right now."),
}
NOTHING_HAPPENED = n_("That button couldn't do anything right now. Try again in a moment.")


def refuse(controller, action, reason=None):
    """Say in ``controller``'s chat why the card button ``action`` did nothing (``reason``: a ``display_text.Text``)."""
    from .display_text import Text
    body = (notice(reason.template, **reason.fields) if isinstance(reason, Text)
            else notice(REFUSED.get(action, NOTHING_HAPPENED)))
    controller.emit("message", {"role": "error", **body})


def perform(controller, action, function):
    """A card button's ``function()``: a falsy result is a refusal and an exception a failure; either is said in the
    chat (``refuse``) whether or not a turn is running. Returns whether it did what its button says."""
    from .display_text import Text
    try:
        done = function()
    except Exception as error:   # noqa: BLE001 - every failure of a click is said, never only logged.
        print("Angelo: the card button %s failed (%s: %s)" % (action, type(error).__name__, str(error)[:200]),   # i18n: a log line
              file=sys.stderr)
        first = error.args[0] if error.args else None
        refuse(controller, action, first if isinstance(first, Text) else None)
        return False
    if not done:
        refuse(controller, action)
    return bool(done)
