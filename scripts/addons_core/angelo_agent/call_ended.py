# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a step ended when the host, not the step, ended it, as the person's row says it (doc/angelo/agent_chat.rst).

A step's result says what the model reads; it cannot say whether the step ever started: ``EXPIRED`` is both the
answer to a card nobody answered and a download that failed while it ran. The host knows at the moment it ends a call,
so it records one closed value on the call's ``activity`` event (``ended``, kept on the transcript row by
``activity_rows.FIELDS``), never in the result: no model reads it (``routing.handoff`` and ``external_apps._row`` read
other fields). Rows saved before it existed have none and keep their status, since a started step's expiry cannot be
told from an unstarted one after the fact.

* ``expired``: the step's approval card was shown and nobody answered before its deadline (``tool_deadlines``).
* ``timed_out``: the step ran out of time before it could start (queued, held for its scene, an approved edit waiting
  for playback or the writer lease); nothing live ran.

An ended step reads as a neutral *Expired · nothing ran* chip (``status`` / ``activity_text``), never a red *Failed*,
and its opened row says why (``detail``). No ``bpy``: host-testable.
"""

from .ui_text import iface_, n_

EXPIRED, TIMED_OUT = "expired", "timed_out"
VALUES = frozenset({EXPIRED, TIMED_OUT})
STATUS = {EXPIRED: "expired", TIMED_OUT: "expired"}   # activity_text.step_status's value for each.
DETAIL = {EXPIRED: n_("Nobody answered in time, so this step didn't run. Ask again if you still want it."),
          TIMED_OUT: n_("This step ran out of time before it could start, so it didn't run.")}


def nothing_live_ran(call):
    """Whether nothing of ``call`` ran in the scene or at a provider: never executing (``started_call`` sets it before
    any job, generation, flow or collaborator is submitted, and before a preparation's callbacks), never mutating or
    committing, and no preparation that may have had effects. A private stage alone (``staging``) changed nothing."""
    return not (getattr(call, "executing", False) or getattr(call, "mutation_started", False)
                or getattr(call, "committing", False) or getattr(call, "preparation_effects_possible", False))


def event(call, result):
    """The ``activity`` event's ``ended`` field for ``call`` finishing with ``result``: ``{}`` unless the host ended it.

    An expiry (``Controller.expire_tool``: ``EXPIRED`` with ``expired: True``) of a call nothing of which ran is
    ``expired`` when its card waited for the person, else ``timed_out``."""
    if not isinstance(result, dict) or result.get("error_code") != "EXPIRED" or result.get("expired") is not True:
        return {}
    if not nothing_live_ran(call):
        return {}
    from .worker_consent import awaiting_person
    return {"ended": EXPIRED if awaiting_person(call) else TIMED_OUT}


def ended(message):
    """The row's ``ended`` value, or None (rows saved before it, or a step the host did not end)."""
    value = message.get("ended") if isinstance(message, dict) else None
    return value if value in VALUES else None


def status(message):
    """``activity_text.step_status``'s value for an ended row, or None."""
    value = ended(message)
    return STATUS[value] if value else None


def detail(message):
    """The opened row's first line for an ended row, translated, or ""."""
    value = ended(message)
    return iface_(DETAIL[value]) if value else ""
