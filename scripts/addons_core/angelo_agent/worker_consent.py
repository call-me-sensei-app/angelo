# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat hosted in its worker Blender (design A′, contract C6): what the person approved, and what standing
consent the worker may act on. Worker side; the lead side is ``worker_consent_lead``. No bpy.

The worker's ``Controller`` evaluates every consent rule with the window's own code (``trusted_scripts``, the
pump's Auto admission, script review), but the person's answers and choices live in Angelo's window. Two things cross:

* **An approval digest** (``approval_digest``). Every call awaiting the person is summarized for the lead with the
  digest of its whole approval binding: the call's id, tool, admitted invocation digest (a script's exact code), its
  current arguments and expanded program, the scene binding its card shows (``precondition``), the part's document
  and its recovery review. Approve carries the digest of the card the person saw; the worker approves only on an
  exact match, else it refuses and nothing runs (``check_approval``). A digest that cannot be computed (a host value
  ``worker_values`` cannot encode) never matches.
* **A consent lease** (``apply_consent``). Standing consent (Auto, Trust scripts for this chat, Always allow scripts,
  with Team and managed policy folded in by the lead) arrives as ``chat.consent {epoch, grants}``. Epochs only move
  forward: an older epoch is refused, the same epoch is acknowledged again only with the same grants. Until a lease
  is applied, nothing runs without the person. A script grant is a nonce; each new nonce is a new consent token, so a
  script queued under a revoked or replaced consent asks, exactly as ``TrustedScripts`` tokens do in the window, and a
  token never comes back. A narrowing lease also revokes the autonomous calls it no longer covers that have not
  started (``tool_deadlines.revoke_turn_calls``' rule), so an admission in flight never outlives the revocation the
  lead waits for.

``LeasedConsent`` mixes this into the hosted chat's ``Controller`` (``worker_chat_host``): it replaces the window's
local consent sources (the person's Always allow file, a lead chat's trust, collaborator consent) with the lease,
and refuses to set consent here. The window's chats never use it, so ``ANGELO_WORKER_CHAT_HOST=0`` is unchanged.
"""

import hashlib
import hmac
import json

from . import worker_values as values
from .chat_settings import clean_options

SCRIPT = "angelo_execute_python"
GRANTS = ("auto", "scripts", "always_allow")   # What a lease may grant (worker_consent_lead.grants).
NONCE_LIMIT = 128

STALE_APPROVAL = ("This call changed after it was shown for approval (its scene binding, arguments or document), so "
                  "the approval does not cover it; nothing ran, and a fresh observation shows the scene as it is now.")
NOT_AWAITING = "No call of this chat awaits approval under this id; nothing ran."
TURN_ENDED = "This call's turn was stopped or ended before the approval arrived; nothing ran."
CLOSED = "This chat was closed before the approval arrived; nothing ran."
ALREADY = "This call was already approved; it was not approved again."
CONSENT_REVOKED = "The standing consent that let this call run without asking was revoked before it ran; nothing ran."


def _refusal(code, text):
    return {"approved": False, "error_code": code, "error": text, "scene_changed": False}


# Approval digests ---------------------------------------------------------------------------------------------------

def _canonical(value):
    return json.dumps(values.encode(value), sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def approval_digest(call):
    """The hex digest of ``call``'s approval binding (module docstring), or None when a part cannot be encoded."""
    try:
        canonical = json.dumps({
            "id": call.id, "tool": call.name, "invocation": call.invocation_digest,
            "arguments": _canonical(call.arguments), "program": _canonical(getattr(call, "program_arguments", None)),
            "binding": _canonical(call.precondition), "document": _canonical(call.document),
            "recovery_review": bool(getattr(call, "recovery_review", False)),
            "recovery_evidence": _canonical(getattr(call, "recovery_evidence", None)),
        }, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError, OverflowError, UnicodeError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def awaiting_person(call):
    """Whether ``call`` waits for the person's Approve or Decline (its card is shown)."""
    return bool(call.approval and not (call.authorized or call.executing or call.approved or call.automatic
                                       or call.revoked or call.__dict__.get("consent_approved")))


def pending_digests(controller):
    """{call id: digest} of the calls awaiting the person, for the ``pending`` event (C3). A call whose digest
    cannot be computed is listed with None: its card shows, and Approve can never match it."""
    with controller.lock:
        calls = [call for call in controller.pending.values() if awaiting_person(call)]
    return {call.id: approval_digest(call) for call in calls}


def check_approval(controller, call_id, digest):
    """``(call, None)`` when the person's Approve of ``call_id`` with ``digest`` covers that call exactly now, else
    ``(None, refusal)`` with nothing run. A stale digest also ends the call (``STALE_REVISION``), as the worker's
    shadow did when the approved binding differed: the model gets a fresh observation instead of a card that can
    never be approved. Main thread (the worker's loop), like the window's approve operator."""
    with controller.lock:
        if getattr(controller, "_closing", False):
            return None, _refusal("OWNERSHIP", CLOSED)
        call = controller.pending.get(call_id) if isinstance(call_id, str) else None
        if call is None or not call.approval:
            return None, _refusal("OWNERSHIP", NOT_AWAITING)
        if (controller.cancelled or not controller.busy or call.revoked or call.epoch != controller._epoch
                or call.generation != controller._request_generation or _arriving(controller, "stop_arrived")):
            return None, _refusal("CANCELLED", TURN_ENDED)
        if not awaiting_person(call):
            return None, _refusal("OWNERSHIP", ALREADY)
        current = approval_digest(call)
        if current is not None and isinstance(digest, str) and hmac.compare_digest(current, digest):
            return call, None
    refusal = _refusal("STALE_REVISION", STALE_APPROVAL)
    controller.finish_tool(call, {key: value for key, value in refusal.items() if key != "approved"})
    return None, refusal


def approve(controller, call_id, digest, apply):
    """``chat.approve``: approve ``call_id`` only when ``digest`` matches (``check_approval``), then ``apply(call)``
    (the worker's own approve operator for this chat, as the window's card runs it). A script's exact text is what
    the digest covers, so the match is its exact-code review here. A call is approved once: a repeated Approve, or
    one arriving after the call started, changes nothing. ``{"approved": True}`` or the refusal."""
    call, refusal = check_approval(controller, call_id, digest)
    if refusal is not None:
        return refusal
    call.__dict__["consent_approved"] = digest   # Before apply: a re-entrant or repeated Approve finds it taken.
    if call.name == SCRIPT:
        controller.review_script(call)
    apply(call)
    return {"approved": True}


# The consent lease --------------------------------------------------------------------------------------------------

class Lease:
    """The newest standing consent this worker acknowledged: ``epoch`` (None: none yet) and its grants."""

    def __init__(self):
        self.epoch, self.auto, self._tokens = None, False, {}   # kind -> (nonce, token)

    def grants(self):
        return dict({"auto": self.auto}, **{kind: nonce for kind, (nonce, _token) in self._tokens.items()})

    def token(self, kind):
        """The consent token of grant ``kind`` ("scripts", "always_allow") under this lease, or None."""
        entry = self._tokens.get(kind) if self.epoch is not None else None
        return entry[1] if entry is not None else None

    def granted_auto(self):
        return self.epoch is not None and self.auto


def clean_grants(grants):
    """The grants a lease holds from ``grants`` (a dict), or None when it is not one. Anything not exactly a grant
    (a non-boolean ``auto``, an empty or oversized nonce, an unknown key) grants nothing."""
    if not isinstance(grants, dict):
        return None
    clean = {"auto": grants.get("auto") is True}
    for kind in GRANTS[1:]:
        nonce = grants.get(kind)
        if isinstance(nonce, str) and 0 < len(nonce) <= NONCE_LIMIT:
            clean[kind] = nonce
    return clean


def lease_of(controller):
    return getattr(controller, "consent_lease", None)


def apply_consent(controller, epoch, grants):
    """``chat.consent``: ``{"epoch": acknowledged}`` once the lease of ``epoch`` holds, or ``{"error_code", "error"}``
    with the current lease unchanged (module docstring). Conflicting grants under the epoch already held revoke
    everything: a lead never sends two leases under one epoch, so the worker trusts neither."""
    lease = lease_of(controller)
    clean = clean_grants(grants)
    if not isinstance(lease, Lease) or type(epoch) is not int or epoch < 0 or clean is None:
        return {"error_code": "VALIDATION", "error": "A consent lease needs a whole epoch and its grants"}
    with controller.lock:
        if lease.epoch is not None and epoch < lease.epoch:
            return {"error_code": "STALE_REVISION", "epoch": lease.epoch,
                    "error": "This consent is older than the one already applied; it was refused"}
        conflict = lease.epoch == epoch and clean != lease.grants()
        if lease.epoch == epoch and not conflict:
            return {"epoch": epoch}   # A repeat (its first answer was lost): acknowledged again.
        if conflict:
            clean = {"auto": False}
        withdrawn = {"auto"} if lease.auto and not clean["auto"] else set()
        for kind in GRANTS[1:]:
            held = lease._tokens.get(kind)
            if held is not None and held[0] != clean.get(kind):
                withdrawn.add(kind)
                del lease._tokens[kind]   # A token never comes back, even under the same nonce.
            if clean.get(kind) and kind not in lease._tokens:
                lease._tokens[kind] = (clean[kind], object())
        lease.epoch, lease.auto = epoch, clean["auto"]
        _revoke_uncovered(controller, withdrawn)
        controller.version += 1
    if conflict:
        return {"error_code": "STALE_REVISION", "epoch": epoch,
                "error": "Conflicting consent under one epoch; all revoked"}
    return {"epoch": epoch}


_KINDS = {"chat_session": "scripts", "always_allow": "always_allow"}


def _revoke_uncovered(controller, withdrawn):
    """Revoke each autonomous call the withdrawn grants covered and that has not started, as Stop revokes a turn's
    calls (``tool_deadlines.revoke_turn_calls``): ``expire_tool`` answers it at its next check. Under the lock."""
    if not withdrawn:
        return
    for call in list(controller.pending.values()):
        if not call.automatic:
            continue
        kind = _KINDS.get(call.consent_kind) if call.name == SCRIPT else None
        if ("auto" in withdrawn or kind in withdrawn) and revocable(call):
            call.revoked = CONSENT_REVOKED


def revocable(call):
    """Whether a revocation still stops ``call``: it has not started, or it only stages (no live change yet). A call
    starts only through ``tool_deadlines.started``, under the chat's lock and only while unrevoked, so a revocation
    after its last start check refuses it with nothing run, and one after its start finds it started (the start gap;
    one rule for consent and a turn's end, window and worker chats alike)."""
    return call.staging and not call.mutation_started or not (call.committing or call.executing)


def _arriving(controller, name):
    """A hosted chat's pending Stop or queued consent (``worker_chat_arrival``); False for any other controller."""
    check = getattr(controller, name, None)
    return callable(check) and check() is True


def _arrived_cover(controller, grant):
    """Whether the consents that arrived for a hosted chat and are not applied yet still grant ``grant``
    (``worker_consent_marks``); True for any other controller, which has none."""
    check = getattr(controller, "consent_marks_cover", None)
    return not callable(check) or check(grant) is True


def admit_autonomous(controller, call):
    """Whether ``call`` may run without the person now: only under the newest acknowledged lease, which grants Auto
    (a worker chat runs nothing unasked in Ask, ``worker_trust.covers_mode``), and for a script also a script grant
    whose token it carries. Never for a chat that is closing or on a changed document, nor while a Stop or a consent
    that arrived is not yet applied and withdraws the grant (``worker_chat_arrival``)."""
    lease = lease_of(controller)
    if (not isinstance(lease, Lease) or not _arrived_cover(controller, "auto") or not lease.granted_auto()
            or controller._closing
            or _arriving(controller, "consent_arriving") or _arriving(controller, "stop_arrived")
            or controller.document is None or controller.document_invalid
            or clean_options(controller.state.get("options"))["approval"] != "AUTO_BOUNDED"):
        return False
    return call.name != SCRIPT or controller.script_consent_kind(call) is not None


class LeasedConsent:
    """Mixed in before ``Controller`` for a chat hosted in its worker: every standing consent comes from the lease
    (``consent_lease``), none from this process. The window's consent checks run unchanged on top of it."""

    @property
    def consent_lease(self):
        lease = self.__dict__.get("_consent_lease")
        if lease is None:
            lease = self.__dict__["_consent_lease"] = Lease()
        return lease

    def _lease_may_cover(self):
        return admit_autonomous(self, _NOT_A_SCRIPT)

    def _granted_token(self, grant):
        """The lease's token of ``grant`` while no consent that arrived since withdraws it, else None."""
        return self.consent_lease.token(grant) if _arrived_cover(self, grant) else None   # The marks first.

    def chat_consents(self):
        token = self._granted_token("scripts")
        return (token,) if token is not None and self._lease_may_cover() else ()

    def own_chat_consent(self):
        return None   # No chat in a worker follows another's trust: the lead's arrives as a grant.

    def set_trusted_scripts(self, enabled):
        raise RuntimeError("A worker chat's trust is set in Angelo's window")

    @property
    def always_allow_scripts(self):
        return self._granted_token("always_allow") is not None and self._lease_may_cover()

    def admission_script_consent(self):
        consents = self.chat_consents()
        if consents:
            return consents[0]
        return self._granted_token("always_allow") if self.always_allow_scripts else None

    def script_consent_kind(self, call):
        from .trusted_scripts import consent_excluded
        consent = call.script_consent
        if call.name != SCRIPT or consent is None or consent_excluded(call) or not self._lease_may_cover():
            return None
        for kind, grant in _KINDS.items():
            if consent is self._granted_token(grant):
                return kind
        return None

    def _automatic_script(self, call):
        return admit_autonomous(self, call)

    def _auto_bounded(self, call, mode=None):
        return admit_autonomous(self, call) and super()._auto_bounded(call, mode)

    def standing_consent(self, call, binding):
        return False   # Collaborators are not offered in worker chats (C5); each would ask.

    def offers_always_allow(self, call):
        return False   # The person's choices are made in Angelo's window.

    def offers_script_file_allow(self, call):
        return False


class _NotAScript:
    name = ""


_NOT_A_SCRIPT = _NotAScript()
