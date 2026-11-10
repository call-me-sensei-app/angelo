# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the chat says while a step waits for the person's approval, why the step asks, and the way to its card.

The card is the one source of truth. A step waits for the person exactly while a pending call has ``approval``
(``awaiting``, the call ``chat_blocks.cards`` draws a card for, in place of the live row). The controller's status
never says so: a status outlives the card it named. In 1.0.9 (Mac 2 dogfood, a GPT build turn in Auto) an
unanswered web-export card expired after 180 s and the live row read "Waiting for your approval" for the next four
minutes of build steps with no card anywhere, and again after every card the person approved. Display only:
nothing here decides or changes what is approved; only ``status_for_apps`` reaches a model (a connected app's task
reply, in 1.0.9's own words).

* ``label``: the words a surface that is not the card shows while it waits (the session rail's tile), naming the
  card's own title ("Waiting for your approval: Add from the Angelo Library").
* ``reason_rows``: one muted line on the card saying why it asks although the chat is in Auto, chosen by the reason
  code of the rule that made it ask (``auto_reasons``: a kind of step Auto never runs, a text file, an add-on render
  engine, a worker limit, or a render or export that failed a check of its own rule), or by a script's
  ``consent_excluded``.
* ``place``/``draw_pointer``/``reveal``: when the card's buttons are scrolled out of the transcript, a pill at the
  transcript's top names it and brings it into view; a rail tile's click (``session_rail_input.switch``) does the
  same. The pill sits at the top, never over the bottom where a revealed card's Approve lands: a second click on the
  pill's spot (a double-click) can never approve a step the person has not seen (break-it review P1). Each part runs
  under ``chat_guard``: a failure here never takes the chat, or the card, down with it.
"""

import weakref

from . import auto_reasons
from .ui_text import iface_, n_

WAITING = n_("Waiting for your approval: {action}")
UNTIL = n_("Waits for your answer until {time}")
CARD_ROLES = frozenset({"approval", "flow_approval", "approval_plain"})   # chat_blocks.cards, chat_guard.APPROVALS.
BUTTONS = 64   # From a card's bottom edge: its action row (chat_card_draw.BUTTON_Y + BUTTON_H, with a margin).
SCRIPTS = frozenset({"angelo_execute_python", "angelo_run_script_file"})
SHIELD_SECONDS = 1.0   # After a pill's click: Blender's longest double-click time (Preferences > Input, 1000 ms).
PAD = 12   # Around the pill's spot: past Blender's largest default drag threshold (Preferences > Input, mouse 3 px).
_shield_pending = {}  # Chat -> exact waiting card; blocked until its first guarded draw.
_reveal = {}   # Chat id -> the card the next draw of that chat scrolls into view (a pointer's or a rail tile's click).


def awaiting(controller):
    """The pending call whose card the chat shows (``chat_blocks.cards``), or None: the first that waits for the
    person's Approve or Decline (``worker_consent.awaiting_person``: never one approved, automatic, running or
    revoked, which keeps ``approval`` while it runs). The card, the rail, the pill and a worker's pending digests
    read this one predicate (break-it review: a running call read as waiting on the rail)."""
    from .worker_consent import awaiting_person
    pending = getattr(controller, "pending", None) or {}
    return next((call for call in tuple(pending.values()) if awaiting_person(call)), None)


def unseen(controllers, active_id, chat_shown):
    """Chats with unanswered cards the person cannot currently see; display only."""
    return [controller for controller in controllers
            if not (chat_shown and controller.state.get("id") == active_id)
            and awaiting(controller) is not None]


def pending_shield(session_id):
    """A hidden-chat reveal blocks presses before the reopened transcript's first draw."""
    return session_id in _shield_pending


def title(call):
    """The card's own title for ``call`` (``approval_review.preview_text``'s first line), its home folders hidden.
    A program's card is laid out on a timer from a prepared review (costly to build per draw): its step title then."""
    from .ui_text import language
    shown = language()
    cached = vars(call).get("approval_title")   # On the call itself: a worker's changed card is a new call.
    if cached is None or cached[0] != shown:
        cached = call.approval_title = (shown, _title(call))
    return cached[1]


def _title(call):
    from . import activity_text, approval_review
    from .live_progress import personal
    try:
        if call.name not in approval_review._PROGRAM_TOOLS:
            found = approval_review.preview_text(call).partition("\n")[0].strip()
            if found:
                return personal(found)
    except Exception:   # noqa: BLE001 - a status line never breaks a draw: the step's own title instead.
        pass
    try:
        return personal(activity_text.step_title({"tool": call.name, "arguments": call.arguments,
                                                  "status": "approval"}))
    except Exception:   # noqa: BLE001
        return iface_("Approve this step")


def status_for_apps(controller):
    """The raw ``status`` a connected app's task reply carries (``external_apps``): 1.0.9's own words while a card
    waits ("Approval required", "Approve collaborator usage" for a collaborator's), derived from the card so they
    never outlive it, else the controller's status. Model-facing: English, never translated."""
    call = awaiting(controller)
    if call is None:
        return controller.status
    collaborator = call.name == "angelo_delegate"
    return "Approve collaborator usage" if collaborator else "Approval required"   # i18n: model-facing, for apps


def label(controller):
    """"Waiting for your approval: <the card's title>" while a card waits in ``controller``'s chat, else ""."""
    call = awaiting(controller)
    return iface_(WAITING, action=title(call)) if call is not None else ""


def auto_reason(controller, call):
    """``(msgid, fields)``: why ``call`` asks although its chat is in Auto, from the rule that made it ask
    (``auto_reasons``), or None: not Auto, Auto runs it (a switch to Auto while it waited), the rule has not decided
    yet, or a card that says its own reason (a recovery review, a collaborator's usage, a script a standing consent
    would run once the person allows scripts, which its card offers) or whose ask is a worker chat's own verdict. The
    reason comes from the rule itself (``bounded_reason``, ``consent_excluded``), never from a list of tools here: a
    kind of step Auto never runs (a bake, a download, a deletion), a text file, an add-on engine, a worker limit, or a
    render or export that failed one of its rule's checks (the reason that rule returns with its fields)."""
    from .chat_settings import clean_options
    bounded, automatic = getattr(controller, "_auto_bounded", None), getattr(controller, "_automatic_script", None)
    # explains_auto: this chat's Auto decision is the base rule (TrustedScripts); a worker's lease or the lead's
    # mirror of a worker decides by its own verdict, which no line here can state (break-it review).
    if (not getattr(controller, "explains_auto", False) or not callable(bounded) or not callable(automatic)
            or getattr(call, "recovery_review", False)
            or clean_options(controller.state.get("options"))["approval"] != "AUTO_BOUNDED"
            or automatic(call) or call.name == "angelo_delegate"):
        return None
    if call.name in SCRIPTS:   # Scripts: script_consent_kind's rule. Only an excluded one asks whatever is allowed.
        from .trusted_scripts import consent_excluded
        code = auto_reasons.SCRIPT_EXCLUDED if call.name == "angelo_execute_python" and consent_excluded(call) else None
    else:
        # _auto_bounded's own rule, evaluated once (None: it runs; a worker chat's extra gate only ever adds a card).
        code = auto_reasons.bounded_reason(controller, call, "AUTO_BOUNDED")
    if isinstance(code, tuple):   # A branch's own reason with its fields (msgid, fields).
        return code
    return (auto_reasons.LINES[code], auto_reasons.line_fields(code)) if code in auto_reasons.LINES else None


def reason_text(controller, call):
    """The card's muted line saying why it asks in Auto (``auto_reason``'s ``(msgid, fields)``), translated, or ""."""
    try:
        reason = auto_reason(controller, call)
        return iface_(reason[0], **reason[1]) if reason else ""
    except Exception:   # noqa: BLE001 - an explanation never stops the card that holds Approve and Decline.
        return ""


def reason_rows(controller, call, wrap, width, size, font):
    """``reason_text`` wrapped for a decision card, or ()."""
    text = reason_text(controller, call)
    return tuple(wrap(text, width, size, font)) if text else ()


def until_rows(call, wrap, width, size, font):
    """``until_text`` wrapped for a decision card, or ()."""
    text = until_text(call)
    return tuple(wrap(text, width, size, font)) if text else ()


def until_text(call, wall=None, now=None):
    """The card's line saying until when it waits ("Waits for your answer until 18:42"), or "": the local clock time
    of its deadline (``call.expires``, which a held scene moves later: the next draw follows it), with seconds when
    it falls within the current minute, so a card about to expire never reads as a minute away. Recomputed on every
    draw; no timer. Never raises: a call without a deadline says nothing."""
    import time
    try:
        wall = time.time() if wall is None else wall
        at = wall + float(call.expires) - (time.monotonic() if now is None else now)
        if at <= wall:
            return ""
        shown = time.localtime(at)
        same_minute = time.localtime(wall)[:5] == shown[:5]
        return iface_(UNTIL, time=time.strftime("%H:%M:%S" if same_minute else "%H:%M", shown))
    except Exception:   # noqa: BLE001 - a deadline line never stops the card that holds Approve and Decline.
        return ""



# The way to the card --------------------------------------------------------------------------------------------

def place(state, controller, blocks, scroll, visible):
    """The transcript's scroll for this draw: where the card's action row would be shown, when a pointer's or a
    rail tile's click asked for it (``reveal``). Records where the card is for ``draw_pointer``. ``scroll`` is
    measured up from the newest row (``chat_view``), ``blocks`` are the drawn ``(role, message, rows, height,
    identity, bubble)`` and ``visible`` the transcript's height. Never raises: the scroll unchanged instead."""
    from .chat_guard import call
    state.pop("approval_pointer", None)
    return call("The approval pointer", _place, state, controller, blocks, scroll, visible, default=scroll)


def _place(state, controller, blocks, scroll, visible):
    identity = controller.state["id"]
    state["session"] = identity  # Canvas input must bind its pending reveal to this exact chat.
    waiting = awaiting(controller)
    # An earlier card or request cannot redirect a later request, even if its call ID is reused.
    if waiting is None or (identity in _reveal and not _same_reveal(_reveal[identity], waiting, controller)):
        _reveal.pop(identity, None)
    if identity in _shield_pending and state.get("message_viewport"):
        state["approval_shield"] = (state["message_viewport"], None)
        _shield_pending.pop(identity, None)
    if waiting is None:
        from .launcher_waiting import first
        other = first()
        if other is not None and other[0] != identity:
            state["approval_pointer"] = (other[0], other[2], False, "approval_switch")
        return scroll
    below = 0
    for block in reversed(blocks):
        if block[0] in CARD_ROLES and block[4] == waiting.id:
            break
        below += block[3]
    else:   # Its card is not drawn yet (a program's rows still being prepared): a reveal waits for it.
        return scroll
    if _reveal.pop(identity, None) is not None:
        scroll = below   # The card's bottom edge at the transcript's bottom: its buttons in view.
    shown = max(0, min(scroll, sum(block[3] for block in blocks) - visible))   # As chat_view clamps it.
    if not shown <= below <= shown + max(0, visible - BUTTONS):
        state["approval_pointer"] = (waiting.id, label(controller), below < shown)
    return scroll


def reveal(state, controller):
    """A pointer's click: the next draw brings the card's buttons into view, and for ``SHIELD_SECONDS`` from that draw
    the pill's spot takes no click (``draw_pointer``): in a short transcript the revealed card's Approve or Always
    allow can lie under it, and a double-click's second press must never answer a card the person has not seen
    (break-it P1). Timed from the draw, not the click: a stall before it never shortens the shield."""
    request_reveal(controller.state["id"], awaiting(controller), controller=controller)
    spot = next((rect for rect, action, _value in state.get("hits", ()) if action == "approval_reveal"), None)
    if spot is not None:   # Padded past Blender's drag threshold: a hand drifting between the two presses.
        x, y, width, height = spot
        state["approval_shield"] = ((x - PAD, y - PAD, width + 2 * PAD, height + 2 * PAD), None)


def _request_fence(controller):
    """Snapshot both ordinary-controller and mirrored-worker request ownership."""
    return tuple(getattr(controller, key, None) for key in ("_epoch", "_request_generation", "_worker_request"))


def _same_reveal(target, waiting, controller):
    call, epoch, generation, owner, fence = target
    return (call() is waiting and epoch == getattr(waiting, "epoch", None)
            and generation == getattr(waiting, "generation", None)
            and owner is getattr(waiting, "delivery_owner", None)
            and fence == _request_fence(controller))


def request_reveal(session_id, waiting, *, shield=False, controller=None):
    """A rail tile's click on a chat whose card ``waiting`` waits: shown, it opens at that card (never a later one)."""
    if waiting is not None:
        try:
            target = weakref.ref(waiting)
        except TypeError:  # Lightweight test/embedded call objects may not support weak references.
            target = lambda: waiting
        _reveal[session_id] = (target, getattr(waiting, "epoch", None), getattr(waiting, "generation", None),
                               getattr(waiting, "delivery_owner", None), _request_fence(controller))
        if shield:
            from .launcher_waiting import clear_hidden_hits
            clear_hidden_hits()  # Every opening/switch route drops old-session canvas authority first.
            _shield_pending[session_id] = waiting.id


def draw_pointer(state, width, top):
    """The pill naming the card whose buttons are out of view, centred under ``top``, the transcript's top edge
    (canvas pixels); its click reveals the card (``approval_reveal``). Its hit goes first: it lies over transcript
    rows, whose hits come earlier. Never raises (``chat_guard``)."""
    import time
    found, shield = state.get("approval_pointer"), state.get("approval_shield")
    if shield and shield[1] is None:   # The first draw showing the revealed card: the shield's second starts now.
        shield = state["approval_shield"] = (shield[0], time.monotonic() + SHIELD_SECONDS)
    if found:
        from .chat_guard import call
        call("The approval pointer", _draw_pointer, state, width, top - 43, found)
    if shield and time.monotonic() < shield[1]:   # Over everything, the pill too, inside the transcript only (never
        from .canvas_controls import INERT        # the usage bar or the pills under it): an inert hit, never drawn
        from .chat_hit_testing import clip_rect   # or focused, and no hit at all once lapsed (canvas_controls.live).
        x, y, _w, _h = shield[0]
        rect = clip_rect(shield[0], state.get("message_viewport") or (x, y, shield[0][2], top - y))
        if rect is not None:
            state["hits"].insert(0, (rect, INERT, shield[1]))
    elif shield:
        state.pop("approval_shield", None)


def _draw_pointer(state, width, y, found):
    from .card_kit import AMBER, LINE, PANEL, TEXT
    from .chat_card_draw import _measure, _view
    view = _view()
    call_id, text, down = found[:3]
    action = found[3] if len(found) > 3 else "approval_reveal"
    arrow = "↓" if down else "↑"
    room = max(60, width - 32 - 52)
    shown = view.elide(text, room, 14)
    pill_width = min(width - 32, 34 + _measure(shown, 14) + 16 + _measure(arrow, 14) + 14)
    x = (width - pill_width) / 2
    view.shape(x, y, pill_width, 35, LINE, 14)
    view.shape(x + 1, y + 1, pill_width - 2, 33, PANEL, 13)
    view.shape(x + 14, y + 13, 9, 9, AMBER, 4)
    view.text(shown, x + 34, y + 12, 14, TEXT)
    view.text(arrow, x + pill_width - 14 - _measure(arrow, 14), y + 12, 14, AMBER)
    state["hits"].insert(0, ((x, y, pill_width, 35), action, call_id))
