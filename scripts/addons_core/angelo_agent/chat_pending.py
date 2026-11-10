# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's pending messages in the chat (``steering``): held on Enter during a turn, drawn in a tray between
the transcript and the message box, their controls, the sends after a turn ends, and their file.

* ``hold``: Enter (or the send arrow) while the chat's turn runs. The message and the message box's pictures become
  a pending message: steered into the turn when it can take one, else queued. Never an error, never a connection
  prepared under a running request (1.0.9 showed "Something went wrong" here).
* ``tray_height``/``draw_tray``: one compact bubble per pending message, oldest first, pinned above the message box
  (outside the transcript's scroll, so it stays in view), each saying which happened (a chip and a line) with
  Now · After this turn, Edit and Remove; a paused one has Send.
* ``act``/``edit_newest``: those controls, and Up arrow in an empty message box. Edit puts the text (and its
  pictures) back in this chat's message box, after anything typed; nothing moves into another chat.
* ``dispatch_due``: on Blender's main thread, from the UI timer, once the chat is idle: every waiting message goes
  out as one request, oldest first, through the same path as Enter (``console.submit``). A refusal pauses them with
  its reason on the bubble, never an error card.
* ``turn_ended``: Stop, a failure or a request that never started pauses what waits; unread steers' rows are
  withdrawn (never deleted: rows are index-mapped).
* ``save``/``load``: each chat's pending messages in ``pending/<chat id>.json`` beside its session, never in the chat
  file (an older Angelo keeps a chat file with fields it doesn't know unchanged instead of saving it).
"""

import json
import os
import time

from . import shared_replace
from .steering import DELIVERING, PAUSED, QUEUE, SENDING, STEER
from .ui_text import iface_, n_

NOW, AFTER, SEND, EDIT, REMOVE = n_("Now", "Angelo"), n_("After this turn"), n_("Send"), n_("Edit"), n_("Remove")
PICTURE = n_("Picture")   # A pending message that is a picture alone.
CHIPS = {STEER: n_("Steering"), QUEUE: n_("Queued"), PAUSED: n_("Not sent"), SENDING: n_("Sending")}
LINES = {
    STEER: n_("Angelo reads this at its next step."),
    "read": n_("Angelo is reading this."),
    QUEUE: n_("Sends when this turn ends."),
    "approval": n_("Angelo reads this once you answer the approval above."),
    "pictures": n_("Messages with pictures send when this turn ends."),
    "cannot_steer": n_("This turn can't take a message midway; it sends when the turn ends."),
    "idle": n_("Sending now."),
}
PAUSED_TEXT = {
    "stopped": n_("You stopped the turn above, so this wasn't sent."),
    "failed": n_("The turn above ended with a problem, so this wasn't sent."),
    "not_started": n_("This message didn't reach Angelo. Send it again when you're ready."),
    "relaunch": n_("Angelo closed before this was sent."),
}
MORE = n_("{count} more waiting")
STEER_CAPTION = n_("Sent while Angelo worked")   # Under a delivered steer's bubble in the transcript.
PAD, CONTROL_H, MAX_ROWS, GAP = 12, 26, 3, 6


# Holding, steering and sending --------------------------------------------------------------------------------------

def hold(controller, text, references=None, *, run_chosen_tools=True, separate=False):
    """Keep ``text`` (and the message box's pictures, or ``references``) as a pending message of the running turn.

    Refused as an idle send is (the same sentences): an empty or over-long message, or one more than the tray
    keeps. While a worker's lane is still preparing the previous message, the box's pictures are that message's
    (its send takes them), so this one holds its words only."""
    from .display_text import Text
    from .steering import MAX_PENDING, MAX_TEXT
    preparing = not controller.busy and isinstance(getattr(controller, "_delivery_stops", None), int)
    box = references is None
    pictures = list(references) if not box else [] if preparing else controller.attachments
    if preparing and box and controller.attachments and not str(text).strip():
        raise ValueError(Text(n_("These pictures go with the message you just sent.")))
    if (not str(text).strip() and not pictures) or len(str(text)) > MAX_TEXT:
        raise ValueError(Text(n_("Enter a message of 1-32000 characters")))
    if len(controller.inbox) >= MAX_PENDING:
        raise OverflowError(Text(n_("Too many messages are waiting. Send or remove some of them first.")))
    item = controller.inbox.hold(text, pictures, steerable=steerable(controller),
                                 run_chosen_tools=run_chosen_tools, separate=separate)
    if box and not preparing:
        del controller.attachments[:len(item.references)]
    controller.person_spoke_at = time.monotonic()   # When they spoke, not when it is sent (engine_link_agent).
    offer(controller)
    controller.version += 1
    return item


def offer(controller):
    """A Codex turn with no host call outstanding takes the steers now (``steering_delivery.codex_offer``)."""
    from .steering_delivery import codex_offer
    provider = getattr(controller, "provider", None)
    if provider is not None and hasattr(provider, "_scope") and controller.inbox.waiting_steers():
        codex_offer(provider)


def steerable(controller):
    """Whether the running turn can take the person's words midway: a route with safe steps the host owns, on a
    turn the person started (never another requester's: a connected app's task, Angelo's own event, a worker-hosted
    chat's turn running in its worker)."""
    from . import claude_direct, worker_routing
    from .provider_registry import runtime_kind
    if (controller.voice is not None or getattr(controller, "_host_request", None) is not None
            or getattr(controller, "_external_task", False) or worker_routing.hosted(controller)):
        return False
    try:
        kind = runtime_kind(controller.state["provider"])
    except ValueError:
        return False
    if kind == "codex":
        provider = getattr(controller, "provider", None)
        return provider is None or getattr(provider, "steer_ready", False)   # Not yet connected: Angelo's runtime.
    context = getattr(controller, "_runtime_context", None)
    return not (context is not None and claude_direct.enabled(context))


def show_steer(controller, data):
    """Main thread (``pump_events``): a steer the model read, as the person's row where it read it. Redacted as every
    row is before it is drawn, saved or handed off (``Controller.redact``), as the person's own message row is."""
    if isinstance(data.get("text"), str) and type(data.get("id")) is int:
        controller._append_row({"role": "user", "text": controller.redact(data["text"]), "steer": True,
                                "steer_id": data["id"]})


def turn_ended(controller, status, *, cancelled, not_sent):
    """The turn's end: what reached the agent stays the request's (its row added if it had none), the rest comes
    back; an abnormal end pauses everything waiting."""
    if controller.busy:   # Auto's escalation started the request again: its steps take them. Never the turn thread
        return            # still closing its runtime after "finished" (break-it round 2 B2).
    normal = not cancelled and not not_sent and status in ("completed", "escalated")
    for item in controller.inbox.turn_ended(normal, "stopped" if cancelled else "failed"):
        show_steer(controller, {"id": item.id, "text": item.text})   # Codex has it; it never read it this turn.


def when_idle(controller):
    """Main thread, every UI pump: a connection or model chosen during the turn takes over first, so the pending
    messages go out on it (``Controller.apply_next_connection`` waits for the turn itself)."""
    controller.apply_next_connection()
    return dispatch_due(controller)


def give_back(chat):
    """A request the agent never got: the pending messages it carried go back on their own bubbles, paused, and
    True; False when it carried none (the caller puts the person's typed message back in the box instead)."""
    pending = list(getattr(chat, "_turn_pending", None) or ())
    if not pending:
        return False
    chat._turn_pending = []
    chat.inbox.not_sent(pending, "not_started")
    chat.version += 1
    return True


def dispatch_due(controller):
    """Main thread: every waiting message as one request, once the chat is idle. True when it went out."""
    from . import console
    from .display_text import error_notice
    if controller.turn_running() or not len(controller.inbox):
        return False
    due = controller.inbox.next_due()
    if not due:
        return False
    text = "\n\n".join(item.text for item in due if item.text.strip())
    references = [reference for item in due for reference in item.references]
    options = due[0].send_options if due[0].host else {}   # Angelo's own follow-up goes alone (next_due).

    refusals = []

    def refused(error):   # Never reached the agent (at once, or later on a worker's lane): back, paused, saying why.
        refusals.append(error)
        controller.inbox.not_sent(due, error_notice(error))
        controller.version += 1
    try:
        console.submit(controller, text, references=references, pending=due, unsent=refused,
                       **({"run_chosen_tools": False} if not due[0].run_chosen_tools else {}), **options)
    except Exception as error:   # noqa: BLE001
        refused(error)
    if refusals:
        return False
    controller.inbox.sent(due)   # A worker lane's later refusal brings them back (``Inbox.not_sent``).
    controller.version += 1
    return True


def send_or_queue(controller, text, label, **send_options):
    """Angelo's own follow-up request (a stop card's Continue, goal mode's next step): never ahead of the person.

    While a turn runs, or while any of the person's messages waits to be sent, it waits after them in the tray as
    ``label`` (an ``n_()`` recipe, e.g. "Continue"; the person can remove it), and goes out as a request of its own
    once theirs has. Never steered, never joined to the person's words, never saved with the chat. Otherwise it is
    sent at once through the same path as Enter (``console.submit``, with ``send_options`` for ``Controller.send``,
    e.g. ``host_request``), raising what refused it. Returns the pending message, or None when it was sent."""
    if not label:
        raise ValueError("Angelo's own follow-up needs the label its bubble shows")
    if controller.turn_running() or controller.inbox.has_due():
        item = controller.inbox.hold(text, (), steerable=False, label=label, send_options=send_options)
        controller.version += 1
        return item
    from . import console
    console.submit(controller, text, **send_options)
    return None


def person_waiting(controller):
    """Whether the person's own messages wait to be sent after the turn: an automatic next step yields to them."""
    return controller.inbox.has_due()


# The person's controls --------------------------------------------------------------------------------------------

def act(controller, space, action, value):
    """A pending bubble's control: ``value`` is the message's id (and the mode for ``pending_mode``)."""
    inbox = controller.inbox
    if action == "pending_mode" and inbox.set_mode(value[0], value[1]):
        offer(controller)
    elif action == "pending_send" and inbox.resume(value):
        offer(controller)
    elif action == "pending_remove":
        inbox.take(value)
    elif action == "pending_edit" and not any(item.host for item in inbox.items() if item.id == value):
        item = inbox.take(value)
        if item is not None and space is not None:
            put_back(controller, space, item)
    controller.version += 1


def put_back(controller, space, item):
    """The message's text after anything already typed (a blank line between), its pictures with the box's."""
    from .drafts import read_chat_draft, write_chat_draft
    current = (read_chat_draft(space, {}) or ("", 0))[0]
    text = current + ("\n\n" if current.strip() and item.text else "") + item.text
    write_chat_draft(space, (text, len(text)), {})
    controller.attachments.extend(dict(reference) for reference in item.references)


def edit_newest(controller, line):
    """Up arrow in an empty message box: the newest pending message the person can still change, back in ``line``."""
    item = controller.inbox.take_newest()
    if item is None:
        return False
    line.body, line.current_character = item.text, len(item.text)
    controller.attachments.extend(dict(reference) for reference in item.references)
    controller.version += 1
    return True


def recall_newest(line, session=None):
    """The composer's hook for Up arrow in an empty message box (``composer_keys.EXTERNAL``, condition "empty"):
    the newest pending message the person can still change comes back into ``line``. True when it took the key."""
    from . import ui
    return edit_newest(ui.get_controller(), line)


# Saved with the chat ------------------------------------------------------------------------------------------------

def _path(controller):
    return controller.store.directory / "pending" / (controller.state["id"] + ".json")


def save(controller, final=False):
    """Write the chat's pending messages when they changed (atomically; the file is removed when none wait). After
    a refusal (another program holds the file) the pump's saves wait ``shared_replace.RETRY_SECONDS``; a ``final``
    one (the chat switched or closed: its inbox is about to go) is always tried."""
    version = controller.inbox.version
    if getattr(controller, "_pending_saved", None) == version:
        return
    refused = getattr(controller, "_pending_refused_at", None)   # Held by another program: tried again after a pause.
    if not final and refused is not None and time.monotonic() - refused < shared_replace.RETRY_SECONDS:
        return
    path, records = _path(controller), controller.inbox.records()
    try:
        if not records:
            shared_replace.remove(path)   # Windows: a holder refuses a delete too (retried, else said).
        else:
            path.parent.mkdir(mode=0o700, exist_ok=True)
            temporary = path.with_name(path.name + ".%d.tmp" % os.getpid())
            try:
                temporary.write_text(json.dumps({"version": 1, "pending": records}, ensure_ascii=False),
                                     encoding="utf-8")
                shared_replace.replace(temporary, path)   # Refused while held: retried, else said (refused_saves).
            except OSError:
                shared_replace.discard(temporary)
                raise
        controller._pending_saved, controller._pending_refused_at = version, None
    except shared_replace.Refused:
        controller._pending_refused_at = time.monotonic()   # Never the retries' pauses at every pump tick.
    except OSError:
        pass   # Kept in memory; the next change tries again.


def load(controller):
    """A chat's saved pending messages, back paused (a relaunch never sends them by itself)."""
    try:
        saved = json.loads(shared_replace.read_text(_path(controller)))   # Sharing delete: never refuses a save.
    except (OSError, ValueError):
        return
    if isinstance(saved, dict) and saved.get("version") == 1:
        controller.inbox.load(saved.get("pending"), "relaunch")
        controller._pending_saved = controller.inbox.version


# The tray -----------------------------------------------------------------------------------------------------------

def _rows(view, item, width, size):
    text = iface_(item.label) if item.host else item.text if item.text.strip() else iface_(PICTURE)
    rows = view.wrap(text, max(40, width - 2 * PAD), size, view._font)
    if len(rows) > MAX_ROWS:   # A long message shows its start; Edit brings all of it back.
        rows = rows[:MAX_ROWS - 1] + [(view.elide(rows[MAX_ROWS - 1][0].rstrip() + " …", width - 2 * PAD, size), 0)]
    return rows


def _layout(controller, width, size, line_height, room):
    """``(items shown, how many older ones are not, their heights)``: the newest that fit in ``room``."""
    from . import chat_view as view
    bubble = max(120, (width - 32) * 0.7)
    shown, heights, used = [], [], 0
    for item in reversed(controller.inbox.items()):
        height = PAD + len(_rows(view, item, bubble, size)) * line_height + 8 + CONTROL_H + GAP
        if shown and used + height > room:
            break
        shown.insert(0, item)
        heights.insert(0, height)
        used += height
    hidden = len(controller.inbox) - len(shown)
    return shown, hidden, heights


def tray_height(controller, width, size, line_height, room):
    if not len(controller.inbox):
        return 0
    _, hidden, heights = _layout(controller, width, size, line_height, room)
    return sum(heights) + (20 if hidden else 0) + GAP


def draw_tray(controller, state, width, bottom, size, line_height, room):
    """The tray from ``bottom`` up: an "N more waiting" line when older ones don't fit, then each bubble."""
    from . import chat_view as view, ui_tokens as tk
    shown, hidden, heights = _layout(controller, width, size, line_height, room)
    top = bottom + sum(heights) + (20 if hidden else 0) + GAP
    if hidden:
        label = iface_(MORE).format(count=hidden)
        view.text(label, width - 16 - view.measure(label, tk.TYPE_SMALL), top - 16, tk.TYPE_SMALL, view.MUTED)
        top -= 20
    for item, height in zip(shown, heights):
        _draw_bubble(controller, state, item, width, top, size, line_height)
        top -= height


def _line(controller, item):
    if item.phase == PAUSED:
        if isinstance(item.reason, dict):   # A refused send's own notice, in the interface language.
            from . import console
            return console.display_text(item.reason)
        return iface_(PAUSED_TEXT.get(item.reason, PAUSED_TEXT["failed"]))
    if item.phase == SENDING or not controller.turn_running():
        return iface_(LINES["idle"])
    if item.phase == DELIVERING:
        return iface_(LINES["read"])
    if item.mode == QUEUE:
        return iface_(LINES[QUEUE] if item.can_steer or item.host else LINES["pictures"])
    if not steerable(controller):
        return iface_(LINES["cannot_steer"])
    if any(call.approval for call in tuple(controller.pending.values())):
        return iface_(LINES["approval"])
    return iface_(LINES[STEER])


def _chip(controller, item):
    if item.phase in (PAUSED, SENDING):
        return item.phase, iface_(CHIPS[item.phase])
    if not controller.turn_running():
        return SENDING, iface_(CHIPS[SENDING])
    mode = STEER if item.mode == STEER and item.can_steer and steerable(controller) else QUEUE
    return mode, iface_(CHIPS[mode])


def _draw_bubble(controller, state, item, width, top, size, line_height):
    from . import chat_view as view, ui_kit_controls, ui_kit_status, ui_tokens as tk
    ui = state.get("ui")
    bubble = max(120, (width - 32) * 0.7)
    rows = _rows(view, item, bubble, size)
    bubble_width = min(bubble, max(view.measure(value, size) for value, _ in rows) + 2 * PAD)
    x, text_bottom = width - 16 - bubble_width, top - PAD - len(rows) * line_height
    view.shape(x - 1, text_bottom - 1, bubble_width + 2, top - text_bottom + 2, tk.EDGE, 15)   # Outlined: not sent.
    view.shape(x, text_bottom, bubble_width, top - text_bottom, view.BG, 14)
    y = top - line_height - 2
    for value, _ in rows:
        view.text(value.rstrip(), x + PAD, y, size, view.MUTED)
        y -= line_height
    controls_y = text_bottom - 8 - CONTROL_H
    right = width - 16
    if item.phase not in (DELIVERING, SENDING):   # Remove and Edit, then Send or Now · After this turn.
        for label, action in ((REMOVE, "pending_remove"), (EDIT, "pending_edit"))[:1 if item.host else 2]:
            label = iface_(label)
            w = view.measure(label, tk.TYPE_SMALL) + 18
            right -= w
            view.text(label, right + 9, controls_y + 8, tk.TYPE_SMALL, view.TEXT)
            state["hits"].append(((right, controls_y, w, CONTROL_H), action, item.id))
        right -= 6
    if item.phase == PAUSED:
        label = iface_(SEND)
        w = view.measure(label, tk.TYPE_SMALL) + 28
        right -= w
        view.shape(right, controls_y, w, CONTROL_H, tk.TEAL, CONTROL_H / 2)
        view.text(label, right + 14, controls_y + 8, tk.TYPE_SMALL, tk.INK)
        state["hits"].append(((right, controls_y, w, CONTROL_H), "pending_send", item.id))
        right -= 8
    elif item.phase not in (DELIVERING, SENDING) and controller.turn_running() and item.can_steer \
            and steerable(controller):
        segments = ((STEER, iface_(NOW)), (QUEUE, iface_(AFTER)))
        w = min(ui_kit_controls.segmented_width(view, 1.0, segments), max(96, right - 160))
        rect = (right - w, controls_y, w, CONTROL_H)
        ui_kit_controls.draw_segmented(view, rect, 1.0, ui, segments, item.mode, action="pending_mode")
        for hit_rect, _, mode in ui_kit_controls.segmented_hits(rect, segments, "pending_mode"):
            state["hits"].append((hit_rect, "pending_mode", (item.id, mode)))
        right -= w + 8
    kind, chip = _chip(controller, item)
    chip_kind = {PAUSED: "failed", SENDING: "running", STEER: "needs_you"}.get(kind, "preview")
    cw, ch = ui_kit_status.chip_size(view, chip_kind, 1.0, chip)
    left = 16
    ui_kit_status.draw_chip(view, (left, controls_y + (CONTROL_H - ch) / 2, cw, ch), 1.0, ui, kind=chip_kind,
                            label=chip)
    room = right - (left + cw + 8)
    if room > 40:   # The line says why; a narrow chat keeps the chip alone.
        view.text(view.elide(_line(controller, item), room, tk.TYPE_SMALL), left + cw + 8, controls_y + 8,
                  tk.TYPE_SMALL, view.MUTED)


def steer_caption(message):
    """The line under a delivered steer's bubble, or None for any other row."""
    return iface_(STEER_CAPTION) if message.get("steer") is True else None
