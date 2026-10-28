# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's own record of what a worker-hosted chat runs on and may do (design A′): its route and its approval.

A mirror's ``state`` is replaced from the chat's file on every read (``worker_mirror_events.reload``) and when its
worker ends (``MirrorController._recovered``), and that file is the worker's: no lead decision is read from it. When
the mirror is made (``pin``, from the file Angelo itself saved before the worker was given it) the lead keeps, in
fields only it writes:

* the chat's **route**, its ``id``, ``provider`` and ``connection_id``: the connection, mode and key its model
  runtime children are launched with (``worker_chat_wiring.launch_chat``) and the connection its standing consent
  depends on (``worker_consent_lead.grants``). Nothing changes it: a worker chat keeps the connection it started
  with (``MirrorController.choose_connection``).
* its **approval** mode: changed only by the lead's own ``chat.options`` (``requested``) and confirmed by the
  worker's answer (``settled``); a refused one is dropped. Standing consent is Auto only while the confirmed approval
  and every change still waiting are Auto (``auto``). The model, effort and Fast are the worker's to report (it turns
  Fast off itself when a model has no Fast tier, as a window's chat does): shown as its file says, decided on by
  nothing here.

After every replacement of the state (``restore``, from ``worker_mirror_ops.keep_rows``) the lead's values are put
back, so every reader, a decision or a drawing, sees the lead's. A file (or an answer) that reports another route, or
an approval the lead neither set nor asked for, is never adopted: the chat is marked (``tampered``), its model children
end, it gets no standing consent and no runtime launch any more, and it says so in its own row.

A chat that is not a mirror (a window's chat, a test's fake) is read from its state as before. Standard library only,
no bpy.
"""

from .chat_settings import clean_options, stored_options
from .ui_text import n_, notice

ROUTE = ("id", "provider", "connection_id")
TAMPERED = n_("Angelo stopped this chat: its worker reported a connection or settings that Angelo did not choose, "
              "so it runs nothing more. Start a new worker to continue.")


def _mirror(chat):
    return getattr(chat, "hosted_in_worker", False) is True


def pin(mirror, state):
    """Keep ``state``'s route and approval as the lead's own (the mirror was just made from Angelo's own file)."""
    mirror.__dict__["_pins"] = dict({name: state.get(name) for name in ROUTE},
                                    approval=_approval(state.get("options")))
    mirror.__dict__["_pin_requests"] = []
    mirror.__dict__["_pin_tampered"] = None


def _pins(mirror):
    pins = mirror.__dict__.get("_pins")
    return None if pins is None or mirror.__dict__.get("_pin_tampered") else pins


def tampered(chat):
    """Why ``chat``'s mirror refuses all work (what its worker reported), or None."""
    return chat.__dict__.get("_pin_tampered") if _mirror(chat) else None


def route(chat):
    """``(provider, connection_id)`` the lead launches and consents for, or None: a mirror never pinned, or marked."""
    if not _mirror(chat):
        return chat.state.get("provider"), chat.state.get("connection_id")
    pins = _pins(chat)
    return (pins["provider"], pins["connection_id"]) if pins is not None else None


def _approval(options):
    return stored_options(options)["approval"]


def _waiting(mirror):
    """The lead's ``chat.options`` not answered yet that change the approval, in the order sent."""
    return [request for request in mirror.__dict__.get("_pin_requests", ())
            if "approval" in dict(request.fields.get("options") or {})]


def shown(mirror):
    """The approval shown: the newest one the lead asked for, else the confirmed one."""
    waiting = _waiting(mirror)
    return (_approval(dict(waiting[-1].fields["options"])) if waiting else mirror.__dict__["_pins"]["approval"])


def _possible(mirror):
    """Every approval the worker may hold now: the confirmed one, or one the lead asked for and has no answer to yet
    (a change whose answer is unknown may or may not have been applied)."""
    return {mirror.__dict__["_pins"]["approval"]} | {_approval(dict(r.fields["options"])) for r in _waiting(mirror)}


def auto(chat):
    """Whether ``chat``'s approval is Auto for its standing consent (``clean_options`` applies RequireAskForEdits):
    for a mirror, the confirmed one and every one it may hold now."""
    if not _mirror(chat):
        return clean_options(chat.state.get("options"))["approval"] == "AUTO_BOUNDED"
    if _pins(chat) is None:
        return False
    return all(clean_options({"approval": approval})["approval"] == "AUTO_BOUNDED" for approval in _possible(chat))


def options(chat):
    """The options the lead sends with ``chat.open``: the chat's, with the approval the lead shows (never the file's)."""
    current = dict(chat.state.get("options") or {})
    if _mirror(chat) and chat.__dict__.get("_pins") is not None:
        current["approval"] = shown(chat)
    return current


def requested(mirror, request):
    """The lead sent ``request`` (a ``chat.options``, ``worker_mirror_ops.Request``)."""
    if mirror.__dict__.get("_pins") is not None:
        mirror.__dict__["_pin_requests"].append(request)
    return request


def settled(request, answer):
    """``request``'s answer, applied once on the main thread: an approval change the worker took is the lead's
    confirmed one; an answer naming an approval the lead never asked for marks the chat."""
    mirror = request.mirror
    if request.kind != "options" or mirror.__dict__.get("_pins") is None:
        return False
    sent = mirror.__dict__["_pin_requests"]
    if request not in sent:
        return False
    asked = dict(request.fields.get("options") or {})
    if isinstance(answer, dict) and answer.get("ok") is True:
        reported = answer.get("options")
        if isinstance(reported, dict) and _approval(reported) not in _possible(mirror):
            return _tamper(mirror, "approval")
        if "approval" in asked:
            mirror.__dict__["_pins"]["approval"] = _approval(asked)
        del sent[:sent.index(request) + 1]   # Answered in order: an earlier one without an answer is settled too.
    else:
        sent.remove(request)
    with mirror.lock:
        mirror.state["options"] = dict(mirror.state.get("options") or {}, approval=shown(mirror))
    mirror.version += 1
    return True


def restore(mirror):
    """After the mirror's state was replaced from its worker's file: the lead's values go back (module docstring); a
    file that reports another route, or an approval the lead never set or asked for, marks the chat."""
    pins = mirror.__dict__.get("_pins")
    if pins is None:
        return
    with mirror.lock:
        state = mirror.state
        changed = [name for name in ROUTE if state.get(name) != pins[name]]
        if not changed and _approval(state.get("options")) not in _possible(mirror):
            changed = ["approval"]
        for name in ROUTE:
            state[name] = pins[name]
        state["options"] = dict(stored_options(state.get("options")), approval=shown(mirror))
    if changed:
        _tamper(mirror, changed[0])


def _tamper(mirror, what):
    """The worker reported a ``what`` the lead did not choose: never adopted; the chat runs nothing more."""
    if mirror.__dict__.get("_pin_tampered"):
        return False
    mirror.__dict__["_pin_tampered"] = what
    print("Angelo: a worker chat reported a %s Angelo did not choose; it is stopped" % what)
    from . import worker_consent_lead, worker_mirror_ops
    worker_mirror_ops.add_row(mirror, "error", notice(TAMPERED))
    if worker_consent_lead.hosted(mirror):   # Its model children end now; its next lease grants nothing (``tick``).
        worker_consent_lead._kill(mirror.state["id"])
    return True
