# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A parked worker-hosted chat's next message (design A′): its worker starts again and the chat reopens there,
resumed, before the message goes.

A done worker chat's worker stops once its part is saved (``worker_queue``: parked), and its chat closes in that
worker first, so its mirror has no ``send_op``. Its next message, the person's or Angelo's own, takes the lanes' own
route (``worker_queue.resume``): it waits for a place ahead of queued parts, a worker starts on the chat's own part
file, the chat opens there with ``resume`` (``worker_routing.adopt``), and only then the message goes, once, as the
mirror's asynchronous Send (``worker_mirror_ops``). Meanwhile the turn shows as sending; one message at a time, as
any Send (the mirror is busy). A relaunch that fails, or a merge or a load that ends the chat meanwhile, returns the
message where the person types, with its attachments, and says why as a window's chat does ("This chat's worker could
not start again (<reason>); nothing was sent": ``worker_queue.RESTART_FAILED``, its reason translated when it is a
``Text``, such as a replaced or merged lead's); a Stop meanwhile withdraws it (nothing was sent).

Standard library only, no bpy.
"""

from .ui_text import notice
from . import worker_mirror_ops as ops


def waits(mirror):
    """Whether ``mirror``'s chat is parked, parking (its chat's close under way: the worker would answer that it
    hosts no such chat), or its worker is already starting again for another message: a Send to it relaunches its
    worker once it parked (``held``). Any other mirror without a worker refuses, as before."""
    from . import worker_queue
    session = mirror.state.get("id")
    return session in worker_queue.parked or session in worker_queue._parking or session in worker_queue._resuming


def held(mirror, op, kind, before=None, **fields):
    """``worker_mirror_ops.submit`` for a parked chat: the request waits on no lane until its chat reopened in its
    relaunched worker, then goes with that open's ``send_op``. The mirror's ``_sending`` afterwards (None when it
    failed at once: the chat can no longer start, ``worker_queue.resume``)."""
    from . import worker_queue
    request = ops.Request(mirror, op, kind, fields)
    request.before = before
    if mirror.__dict__.get("_op_lane") is None:   # Where a Stop withdraws it while it waits (``ops.withdraw``).
        mirror.__dict__["_op_lane"] = ops.Lane()
    mirror._sending = request   # Before the resume: a background Angelo relaunches inside it (``worker_queue.drive``).
    if not worker_queue.resume(mirror, lambda: _go(mirror, request), lambda error: _failed(mirror, request, error)):
        _failed(mirror, request, RuntimeError(_cannot_restart(mirror.state.get("id"))))
    return mirror._sending


def _cannot_restart(session):
    """``RESTART_FAILED`` for a parked chat ``resume`` refused at once: why its worker stopped for good, as recorded
    (``worker_sessions.died``: a replaced or merged lead), else that its chat is not open in a worker."""
    from . import worker_queue, worker_sessions
    from .display_text import Text
    from .worker_reasons import NOT_OPEN
    why = worker_sessions.died.get(session)
    return Text(worker_queue.RESTART_FAILED, why=why if isinstance(why, Text) else Text(NOT_OPEN))


def _go(mirror, request):
    """Main thread, the chat open again in its worker: the held Send goes on the chat's lane, once."""
    if getattr(mirror, "_sending", None) is not request or request.state != "queued":
        return   # Withdrawn by a Stop, or settled: nothing is sent.
    request.send = mirror.send_op   # The reopened chat's; none (it closed again) answers "gone" and returns it.
    request.before = consent(mirror)   # Also the reopened chat's: the open registered it, with its lease.
    mirror.__dict__["_op_lane"].submit(request)


def consent(mirror):
    """Main thread, as a Send goes on its lane: its ``before`` hook, for a chat marked as needing approval
    (``worker_consent_lead.resume_later``, which re-sends the grants of the chat registered now), else None. A held
    Send's is taken only once its chat reopened (``_go``): taken when the person sent it, while the chat was not
    registered, it refused the first message after every relaunch."""
    from . import worker_consent_lead as lead
    session = mirror.state["id"]
    return lead.resume_later(session) if lead.needs_approval(session) else None


def _failed(mirror, request, error):
    """Main thread: the chat's worker could not start again (or the chat ended meanwhile): nothing was sent. A
    ``Text`` error (``RESTART_FAILED``) is shown as it is; any other says why with its support code."""
    if getattr(mirror, "_sending", None) is not request or not mirror.__dict__["_op_lane"].withdraw(request):
        return
    ops.return_unsent(mirror, request)
    from .display_text import Text, error_notice
    if error.args and isinstance(error.args[0], Text):
        ops.add_row(mirror, "error", error_notice(error))
        return
    from .worker_reasons import of_error
    text, code = of_error(error, "send")   # A ``RefusalError`` or ``ChatOpFailed`` keeps its code.
    ops.add_row(mirror, "error", notice(ops.SEND_REFUSED, why=text, code=code))
