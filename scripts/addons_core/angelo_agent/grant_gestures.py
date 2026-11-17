# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's consent for a gesture that outlasts its press (doc/angelo/grant_operators.rst, "Authority in C").

``grant_guard.ticketed`` runs a click in the grant scope of the person's own press. Some gestures reach their grant
later: a picker a card opens once it has closed (``deferred``), a slider the person drags (``press_hold`` then
``dragged`` for each move). Each keeps that press's consent in C (``hold_dialog``: held only while the person's scope
is open, never for agent code) and spends it once, re-holding for the next move of the same drag. Made outside the
person's scope they carry nothing, so what they reach that grants refuses, as a script's call would.
"""

import time

from . import grant_guard   # A module, read at call time: one guard per process (test_native_grant_gestures).
from .ui_text import n_

# A dialog's or file browser's confirm whose subject (``dialog_target``) changed while it was open: an agent's tool,
# a job's end, or the person's own edit during a long file pick. Not their click at fault (2026-11-17 review).
TARGET_CHANGED = n_("What this acts on changed while it was open, so nothing ran. Open it again.")


def deferred(idname, function):
    """``function`` (no arguments) to run after the person's click that asked for it has returned (a file browser
    opened once a card closed), keeping that click's consent for guarded operator ``idname``: held in C while the
    person's scope is open (``hold_dialog``, as a dialog keeps it), taken once when it runs. Made outside the person's
    scope it carries nothing, so its grant refuses as a script's would."""
    grant_authority, key = grant_guard._authority(), "deferred:%x:%d" % (id(function), time.monotonic_ns())
    held = (grant_authority.available() and not grant_guard.script_running()
            and grant_authority.GUARD.hold_dialog(idname, key))

    def run():
        try:
            if held and grant_authority.GUARD.peek(idname, key):
                return grant_authority.GUARD.grant(idname, key, function)
            return function()
        except Exception as error:   # Refused (the press carried no consent): said in one line, never a fault.
            if not grant_guard.said_refusal(error):
                raise
            return None
    return run


def press_hold(prefix, action, value, function):
    """``function()`` in the grant scope of the person's press on ``prefix + action`` (``ticketed``), and that press's
    consent kept while their button stays down (a slider's drag): returns the hold ``dragged`` runs each later move in,
    or None when the press minted nothing (then every move runs outside any scope and what grants refuses)."""
    grant_authority, idname, digest = grant_guard._authority(), prefix + action, grant_guard.value_digest(value)
    hold = [None]

    def pressed():
        hold[0] = _hold(idname)
        return function()
    if grant_authority.available() and grant_authority.GUARD.peek(idname, digest):
        grant_authority.GUARD.grant(idname, digest, pressed)
    else:
        function()
    return hold[0]


def dragged(hold, function, final=False):
    """``function()`` for a move of the drag ``press_hold`` started, in its press's consent, kept for the next move;
    returns the next hold (None once there is none). The release passes ``final``: nothing is held past it (merge-11's
    review: an unused hold would stay a live ticket until it expired)."""
    if hold is None or not grant_guard._authority().GUARD.peek(*hold):
        function()
        return None
    following = [None]

    def moved():
        following[0] = None if final else _hold(hold[0])
        return function()
    grant_guard._authority().GUARD.grant(hold[0], hold[1], moved)
    return following[0]


def _hold(idname):
    """In the person's scope: C keeps it for one later step of the same gesture, as ``(idname, key)``: seconds, gone
    when any window loses focus or an agent script runs (``hold_gesture``), so a lost release ends it too."""
    key = "held:%d" % time.monotonic_ns()
    if grant_guard.script_running() or not grant_guard._authority().GUARD.hold_gesture(idname, key):
        return None
    return (idname, key)


def dialog_target(operator, context):
    """What a guarded operator's dialog acts on, as its confirm will read it: the document and scene Angelo's window
    shows (``ui.document_key``), the scene of the dialog's own window, the chat (its id, document and connection), and
    its subject in Blender: the active object, the selection and the flow its editor shows (``_subject``), plus the
    operator's own ``consent_target(context)`` when it defines one, which adds fields and never replaces these (the
    coordinator's reads of 927cd1a81a4 and e591ececc7f). An operator's properties are not here: an open dialog's
    operator is reached only through that dialog's own drawing, the person's edits."""
    fields = []
    try:
        from . import ui
        controller = ui.get_controller()
        fields += [list(ui.document_key()), controller.state.get("id"), controller.document,
                   controller.state.get("connection_id")]
    except Exception:   # noqa: BLE001 - no window or chat yet: those fields are absent at invoke and execute alike.
        fields += [None, None, None, None]
    fields.append(getattr(getattr(context, "scene", None), "session_uid", None))
    fields += _subject(context)
    target = getattr(operator, "consent_target", None)
    if callable(target):   # Every guarded dialog operator declares one (test_native_dialog_consent).
        try:
            fields.append(target(context))
        except (LookupError, ReferenceError):   # What it reads is gone (an area closed, a freed ID): absent at both
            fields.append(None)                  # ends. Any other error is a bug and raises (the review of c76f2f1).
    return fields


def _subject(context):
    """What an operator reads from the context it runs in, which a read-only tool or a job's end may change while its
    dialog is open (the person can't: their input goes to the dialog): the active object, the selected objects, and
    the node tree the editor shows (a flow), each by session uid."""
    layer = getattr(context, "view_layer", None)
    objects = getattr(layer, "objects", None)
    try:   # The view layer's own list of selected objects: C filters it, no Python walk over every object.
        selected = sorted(o.session_uid for o in objects.selected) if objects is not None else None
    except (AttributeError, ReferenceError, RuntimeError, TypeError):   # A layer being freed: absent both times.
        selected = None
    tree = getattr(getattr(context, "space_data", None), "edit_tree", None)
    return [getattr(getattr(objects, "active", None), "session_uid", None), selected,
            getattr(tree, "session_uid", None)]


def target_key(operator, context):
    """The digest of ``dialog_target``: ``grant_guard.grant`` binds it into the dialog's held consent at invoke and
    checks it at execute, for every guarded operator (the reviewer's reads of f5604750e18, (e), and 927cd1a81a4)."""
    return grant_guard.value_digest(dialog_target(operator, context))[:16]


def landed():
    """A result from outside the person's present input just applied on the main thread (a flow step's output, a
    generation's files, a placed or imported asset, a job's end): an open dialog's OK and a gesture's next step were
    not for it, so they end (C's ``outside_work``). The person's own presses, moves and focus stay: outside work
    synthesizes no input, so a generation's results never keep them from granting meanwhile. Called where a result
    applies, never for progress or a status (test_native_dialog_consent: progress lands nothing, a result once)."""
    note = getattr(grant_guard._authority().GUARD, "outside_work", None)
    if note is not None:
        note()


def agent_changed():
    """An agent tool or native job may change Blender or Angelo now (``BlenderExecutor``; an agent script's run
    notes itself in C): every dialog's held consent and gesture's hold ends, so a confirm or a drag's next step
    after it needs the person's fresh press (the coordinator's read of 927cd1a81a4). Narrowing: any caller may."""
    note = getattr(grant_guard._authority().GUARD, "agent_changed", None)
    if note is not None:
        note()
