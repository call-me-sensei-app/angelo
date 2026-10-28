# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long a native call may wait for the person, and who waits for it.

One table serves both sides. The host (``Controller.request_tool``) expires a
call nobody answered after ``tool_seconds``. A provider that stops waiting
earlier ends the whole turn while the call is still on screen (the Claude plan
used a flat 190 s against the 600 s a reviewed Python script is given), so the
provider waits on the host's own answer instead: until the call's *current*
expiry plus a grace for the UI tick that delivers it.

A turn that ends revokes what it admitted at once (``revoke_turn_calls``), on the
worker thread, so no approval click between the end of the turn and the UI
thread handling it can run a call whose answer nobody is waiting for. A call's start
(``started``) is atomic with it: the revocation either lands first (the call is
refused and nothing ran) or finds the call started (it runs and reports what it
changed), never in between, since the last start check runs without the lock.
"""

import time

from .flow_tool_spec import FLOW_USAGE
from .studio_spec import STUDIO_USAGE

APPROVAL_SECONDS = 180        # Queueing, preparation and the click, for every call.
PYTHON_REVIEW_SECONDS = 600   # angelo_execute_python: the exact code is read first.
MEDIA_SECONDS = 1500          # Generation and Flow runs; a started job sets its own, longer bound.
PROVIDER_GRACE_SECONDS = 15   # How much longer than the host a provider waits for its answer.
POLL_SECONDS = 1.0
TURN_ENDED = "The agent turn ended before this was approved; nothing was run. Ask again if it is still needed."


def tool_seconds(name):
    """The first lifetime of a pending call, in seconds (the host extends a started media job's)."""
    if name == "angelo_execute_python":
        return PYTHON_REVIEW_SECONDS
    if name in STUDIO_USAGE:
        return MEDIA_SECONDS if name.endswith("generate") else APPROVAL_SECONDS
    return MEDIA_SECONDS if name in FLOW_USAGE else APPROVAL_SECONDS


def held(context):
    """Whether ``context``'s chat waits for its scene to be shown (``document_scene.hold``): no lifetime of its runs."""
    return bool(getattr(getattr(context, "host", context), "_held", False))


def await_host(done, context, seconds):
    """Wait until ``done`` is set; False after ``seconds`` during which ``context``'s chat was not held for its scene
    (a held chat's observation or dispatch is not late: break-it R5M1 R2-M-1)."""
    left = seconds
    while not done.wait(POLL_SECONDS):
        left -= 0 if held(context) else POLL_SECONDS
        if left < 0:
            return False
    return True


def await_native(done, context, call_id, name):
    """Wait until ``done`` is set; False only if the host never answered by its call's expiry plus the grace.

    The host answers every call itself when it expires, so a False means its UI thread stopped ticking.
    ``context.tool_expiry`` reads the call's live expiry (paused while the chat is held: ``document_scene.hold``);
    without it the first lifetime applies, paused the same way.
    """
    started, expiry = time.monotonic(), getattr(context, "tool_expiry", None)
    while not done.wait(POLL_SECONDS):
        if held(context):
            started += POLL_SECONDS
            continue
        expires = expiry(call_id) if callable(expiry) else None
        if time.monotonic() > (started + tool_seconds(name) if expires is None else expires) + PROVIDER_GRACE_SECONDS:
            return False
    return True


def revoke_turn_calls(host, fence):
    """Revoke admitted work before live apply, including a running private stage.

    ``Controller.expire_tool`` then answers each on its next check (the UI approve operators, ``_tool_available``)
    with ``TURN_ENDED``; ``cancel_tools`` does the same when the ``finished`` event is handled. Thread-safe: only
    a field write under the host lock, no Blender data.
    """
    with host.lock:
        for call in list(host.pending.values()):
            staged = getattr(call, "staging", False) and not getattr(call, "mutation_started", False)
            if ((call.epoch, call.generation) == (fence.epoch, fence.generation)
                    and (staged or not (call.committing or call.executing))):
                call.revoked = ("The agent turn ended before the staged edit was applied. Inspect the scene before retrying."
                                if staged else TURN_ENDED)


def started(host, call, **marks):
    """Set ``marks`` (``executing``, ``committing``, ``mutation_started``) on ``call`` unless a revocation landed since
    its last start check: under the lock ``revoke_turn_calls`` takes, so a turn's end never marks a call that then
    runs anyway (the start gap). False when revoked: nothing was started, and the caller refuses it as revoked.
    A host with ``start_refusal`` (a worker's hosted chat: a Stop or consent marked on arrival) is asked here too."""
    with host.lock:
        refusal = getattr(host, "start_refusal", None)
        if not getattr(call, "revoked", "") and callable(refusal):
            reason = refusal(call)
            if reason:
                call.revoked = reason
        if getattr(call, "revoked", ""):
            return False
        for name, value in marks.items():
            setattr(call, name, value)
        return True
