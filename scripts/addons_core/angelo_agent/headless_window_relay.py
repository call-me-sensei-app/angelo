# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Relayed approvals of a hidden-window session (doc/angelo/mcp_headless_window.rst, "Relayed approvals"): read-only.

Nobody can press a card in a hidden app, so a call waiting for approval in the session's External conversation is
relayed to the app in full (``relayed``: what, why, the exact edit or script, the card's choices), so its human knows
what waits. Only the person answers it, in Angelo: the app calls ``angelo_show`` and the hidden app's window comes up
with the card. Nothing here grants, and no answer can come from the app: the break-it review (2026-11-18, M1/M2)
refused an app-side answer whose id sits in the app's model context and that no person ever allowed. Main thread; no
bpy at import.
"""

RELAYED = ("Waiting for the person's approval of {tool}: Angelo has no window here, so the card is "  # i18n: model
           "relayed in state.approval. Tell your human and call angelo_show; only the person approves it, in Angelo "
           "(you may decline it with angelo_answer_approval).")
SHOW_TO_ANSWER = ("Only the person can approve this, in Angelo. Tell your human what waits (exactly as "  # i18n: model
                  "shown here) and call angelo_show so Angelo's window comes up with the card. You may decline it "
                  "(angelo_answer_approval with this card and decline); you can never approve it.")


def app_controller(ui):
    """The session's External conversation (the launcher's app), or None."""
    from . import external_host, headless_window_app
    host = external_host._host
    label = headless_window_app.client()
    if host is None or not label:
        return None
    try:
        return host.conversation(label, create=False)
    except Exception:   # noqa: BLE001 - no conversation yet: nothing to read.
        return None


def relayed(controller, call):
    """What the card shows, for the app's human: the tool, why it asks, the exact edit or script, the choices."""
    from .approval_wait import reason_text
    arguments = getattr(call, "arguments", {}) or {}
    exact = {"script": arguments.get("code")} if isinstance(arguments.get("code"), str) else {"arguments": arguments}
    try:
        why = str(reason_text(controller, call) or "")
    except Exception:   # noqa: BLE001 - the card's own words when they can be read; the edit itself always.
        why = ""
    choices = ["approve once", "decline"]  # i18n: to the app's model
    if getattr(controller, "offers_always_allow", lambda c: False)(call):
        choices.append("always allow scripts")  # i18n: to the app's model
    return {"tool": call.name, "why": why, **exact, "choices": choices, "card": card_id(call),
            "approved_by": "the person, in Angelo", "how": SHOW_TO_ANSWER}  # i18n: to the app's model


def card_id(call):
    """A name for this exact card (its call and invocation digest): it can only decline, never approve."""
    import hashlib
    text = "%s\n%s" % (getattr(call, "id", ""), getattr(call, "invocation_digest", "") or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def record(controller, call, app):
    """The app's decline as a row in the conversation, kept in Angelo's chat."""
    from . import ui_text
    try:
        controller._append_row({"role": "system", **ui_text.notice("Declined {tool}, answered through {app}.",
                                                                   tool=call.name, app=app)})
        controller.version += 1
    except Exception as error:   # noqa: BLE001 - the decline stands; its row is the record's second copy.
        print("Angelo: could not record a decline from an app:", error)


def apply(state, controller, call):
    """``external_agent.conversation_state`` in a hidden-window session: its caps and use, and a waiting card
    relayed in full; unchanged outside one."""
    from . import headless_window_app
    hidden = headless_window_app.state(controller)
    if hidden is None:
        return state
    state["session"] = hidden
    if call is not None:
        state["approval"] = relayed(controller, call)
        state["needs"] = RELAYED.format(tool=call.name)
    return state
