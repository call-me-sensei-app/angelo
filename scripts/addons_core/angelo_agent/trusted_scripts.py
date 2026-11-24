# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host UI consent for unrestricted scripts; in-memory Controller ownership.

Two standing consents let a script run without its own review, and nothing
else does: Trust scripts for this chat (in memory, this chat and document) and
the person's Always allow scripts (``always_allow``: saved per user, Auto chats
only). A script carries the consent current at its admission and runs unasked
only while that same consent still covers it at dispatch. A worker chat (a part
of parallel edit) inherits its lead's Trust scripts for this chat, in Auto only:
in Ask every worker script asks (``worker_trust``).

A script's card offers both beside Run once (its exact text, once): Allow for this session is Trust scripts for this
chat, with the same rule as the Approval Policy menu's (``offers_session_allow``), and Always allow is the person's
Always allow scripts (Auto only). 2.0.1: both cover an External conversation (a connected app's,
``Controller.external_client``) as any chat, since every script there is Angelo's own agent's: an app never sends
code or calls a tool (doc/angelo/external_mcp.rst). Before, every script there asked. The trust is the
conversation's, in memory: a new conversation, a changed document, a restart or the app's disconnect or new key
ends it (``_app_identity``). A CLI client's conversation has none of these: it only reads.

A third, the person's own (``reviewed_scripts``; off in 2.1.0, ``reviewed_scripts.OFFERED``): **Always run this exact
script in this project**, offered on a script's card once the person opened its exact text, records the SHA-256 of
those bytes for this project in their state folder. The very same bytes then run without asking in any chat of that
project but Read only and an External conversation; one changed byte asks again. It is checked at admission and again
at dispatch, so a removal on the Rules page reaches a script already waiting. Never a worker chat's (its document is a
copy), never for a script ``consent_excluded`` names, never with ``AllowAutoApproval`` off.
"""

import hashlib
import json
import os
from . import always_allow, grant_authority, reviewed_scripts
from .chat_settings import clean_options
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)


def consent_exclusion(call):
    """Why a script must always ask, as its reason code (``auto_reasons``), or None when a standing consent may run it.

    ``SCRIPT_EXCLUDED``: it refers to preferences (``uses_preferences``, where the person's Always allow scripts
    lives) or to Angelo's own add-on. ``SCRIPT_FILE_LOAD``: it names an operator that replaces the open file
    (``worker_script.file_load``: load, revert, recover, read the startup file), unsaved work included, which Undo
    cannot take back; a consent given before the script existed is not a consent to that. A heuristic over the
    code, as the card's warning is; a script that hides the names is still covered by the property's guard
    (trusted_scripts_ui) and ends as ``DOCUMENT_REPLACED`` (script_document)."""
    from . import auto_reasons
    from .fallback_categories import uses_preferences
    from .worker_script import file_load
    code = call.arguments.get("code") if isinstance(call.arguments, dict) else None
    if not isinstance(code, str) or uses_preferences(code) or "angelo_agent" in code:
        return auto_reasons.SCRIPT_EXCLUDED
    return auto_reasons.SCRIPT_FILE_LOAD if file_load(call.arguments) is not None else None


_registries = {}   # clients.json path -> (its stat, its records): ``_app_identity``'s cache.
APP_CALL = "external-call/"   # The id an app's own direct tool call carried (2.0.0); since 2.0.1 none exists.


def app_supplied(call):
    """Whether ``call`` carries an app's own code (an app's direct tool call, by its id). Since 2.0.1 no such call is
    made (doc/angelo/external_mcp.rst, "What changed"): kept as a guard that never matches, so a direct path added
    later could never inherit a consent given for Angelo's agent (break-it review M2, 2.0.1)."""
    return APP_CALL in str(getattr(call, "id", ""))


def consent_excluded(call):
    """Whether a script must always ask, whatever standing consent the person gave (``consent_exclusion``)."""
    return consent_exclusion(call) is not None


class TrustedScripts:
    """Consent policy mixed into the existing Controller, never a separate executor."""
    explains_auto = True   # Its Auto decision is the base rule, so a card may say why it asks (approval_wait).

    def _approve_automatically(self, call, executor, document):
        """Angelo's own approval of ``call`` (Auto's bounded edit, a script the person's standing consent covers): in
        its automatic C grant scope, where the approval is recorded in C (controller_entries). Refused while an agent
        script runs, as every automatic decision is (grant_authority.automatic)."""
        from . import grant_authority
        return grant_authority.automatic(self.approve_tool, call, executor, document)

    @property
    def trusted_scripts(self):
        """Whether Trust scripts for this chat covers this chat's scripts now: its own, or a worker chat's lead's."""
        return bool(self.chat_consents())

    def _chat_trust_allowed(self):
        """Whether any chat trust may cover this chat's scripts now: never a missing or
        changed document, a closing chat, Read only, a worker chat in Ask (``worker_trust.covers_mode``) or
        ``AllowAutoApproval`` off."""
        from . import managed_policy, worker_trust
        return (self.document is not None and not self.document_invalid
                and not self._closing
                and worker_trust.covers_mode(self, clean_options(self.state.get("options"))["approval"])
                and managed_policy.current().allow_auto_approval
                and (not getattr(self, "external_client", "")
                     or self._app_identity() == getattr(self, "_script_consent_app", None) is not None))

    def _app_identity(self):
        """An External conversation's app as its connection is now: client, when it was connected and its key file;
        None when it is not connected. Its session trust holds only while this stays the same: a disconnect or a
        reconnect (a new key) ends it (break-it review S3, 2.0.1)."""
        from .external_clients import AppRegistry
        client = getattr(self, "external_client", "")
        if not client:
            return None
        registry = AppRegistry(self.workspace.parent)
        try:   # A stat of each file per check; the registry is read again only when it changed (every draw asks).
            listed, key = os.stat(registry.path), os.stat(registry.keys / client)
        except OSError:
            return None
        stamp = (listed.st_ino, listed.st_size, listed.st_mtime_ns)
        cached = _registries.get(str(registry.path))
        if cached is None or cached[0] != stamp:
            cached = _registries[str(registry.path)] = (stamp, registry.load())
        record = cached[1].get(client)
        if record is None:
            return None
        return client, record.get("connected_at"), key.st_ino, key.st_mtime_ns

    def _revoke_if_app_changed(self):
        """An External conversation's session trust ends for good once its app is disconnected or connected again
        (a new key): revoked here and in C, never only suspended (break-it review round 2)."""
        if (self._script_consent is None or not getattr(self, "external_client", "")
                or self._app_identity() == getattr(self, "_script_consent_app", None)):
            return
        self._script_consent, self._script_consent_app = None, None
        try:
            grant_authority.forget(grant_authority.TRUSTED_CHATS, self.state["id"])
        except PermissionError:   # An agent script's turn: C keeps it until Angelo's own next check; Python never covers.
            pass
        self.version += 1

    def chat_consents(self):
        """The chat trusts that cover this chat's scripts now: its own, then the lead's a worker chat inherits."""
        from . import worker_trust
        self._revoke_if_app_changed()
        if self._script_consent is None and not worker_trust.follows(self):
            return ()   # Neither (most chats): no policy read on every draw that asks.
        if not self._chat_trust_allowed():
            return ()
        own = self._script_consent
        own = own if own is not None and own[:2] == (self.state["id"], self.document) else None
        # Each trust covers scripts only while C holds it for its chat (grant_authority): Python's copy alone never.
        return tuple(token for token in (own, worker_trust.inherited(self))
                     if token is not None and grant_authority.holds(grant_authority.TRUSTED_CHATS, token[0]))

    def own_chat_consent(self):
        """This chat's own Trust scripts for this chat while it covers its scripts, else None: what the worker chats a
        lead starts inherit (``worker_trust``)."""
        consents = self.chat_consents()
        return consents[0] if consents and consents[0] is self._script_consent else None

    def set_trusted_scripts(self, enabled):
        """Human UI consent, in memory only, scoped to this chat/document.

        Saved options and tools cannot set this. Queued scripts retain their
        old admission token and still need exact-code manual approval. A lead's
        change reaches the worker chats it started (``worker_trust.changed``).
        """
        from . import managed_policy, worker_trust
        external = bool(getattr(self, "external_client", ""))
        if enabled and (self.document is None or self.document_invalid or self._closing
                        or (external and self._app_identity() is None)
                        or not worker_trust.covers_mode(self, clean_options(self.state.get("options"))["approval"])
                        or not managed_policy.current().allow_auto_approval):
            raise RuntimeError("Trusted scripts are unavailable for this document or approval policy")
        # Held in C first (grant_authority): a widening refused outside the person's own click changes nothing here.
        grant_authority.record(grant_authority.TRUSTED_CHATS, self.state["id"], bool(enabled))
        self._script_consent = (self.state["id"], self.document, object()) if enabled else None
        self._script_consent_app = self._app_identity() if enabled and external else None
        worker_trust.changed(self, enabled)
        self.version += 1
        from . import worker_consent_lead
        worker_consent_lead.changed()   # Chats hosted in their workers: returns once a revocation is acknowledged.

    @property
    def always_allow_scripts(self):
        """Whether the person's Always allow scripts covers this chat now: on, allowed by policy, in Auto.

        Ask still asks and Read only still refuses; a changed document or a closing chat never runs a script unasked.
        In a connected app's chat only a consent saved by 2.0.1 or later, or widened there by the person's press,
        covers it (``always_allow.covers_external``): a 2.0.0 consent never silently starts to.
        """
        return (always_allow.token() is not None and self.document is not None and not self.document_invalid
                and not self._closing and clean_options(self.state.get("options"))["approval"] == "AUTO_BOUNDED"
                and (not getattr(self, "external_client", "") or always_allow.covers_external()))

    def standing_script_consent(self):
        """``(kind, app)`` of the standing consent that runs this chat's scripts unasked now, or None: what the chat
        says (its line, the Approvals popover). ``kind``: "session" (Trust scripts for this chat) or "always";
        ``app``: an External conversation's client, else ""."""
        app = getattr(self, "external_client", "")
        if self.chat_consents():
            return "session", app
        return ("always", app) if self.always_allow_scripts else None

    def script_review_project(self):
        """The project a remembered script belongs to (``reviewed_scripts.project_key``): "" while unsaved."""
        try:
            from . import document_identity, ui
            identity, path, _file = document_identity.open_document(ui)
        except (ImportError, AttributeError, OSError, ValueError):
            return ""
        return reviewed_scripts.project_key(identity, path)

    def _reviewed_script_applies(self, call):
        """Whether a remembered script may cover ``call`` at all (any script's approval, not yet these bytes)."""
        from . import managed_policy
        code = call.arguments.get("code") if isinstance(call.arguments, dict) else None
        return (reviewed_scripts.OFFERED and call.name == "angelo_execute_python" and isinstance(code, str)
                and not consent_excluded(call)
                and not reviewed_scripts.runs_other_code(code)
                and not getattr(self, "external_client", "") and getattr(self, "remote_executor", None) is None
                and self.document is not None and not self.document_invalid and not self._closing
                and clean_options(self.state.get("options"))["approval"] != "READ_ONLY"
                and managed_policy.current().allow_auto_approval and bool(self.script_review_project()))

    def reviewed_script_allowed(self, call):
        """Whether the person remembered these exact bytes for this project (checked again at dispatch)."""
        return (self._reviewed_script_applies(call) and reviewed_scripts.store(self.store.directory).allowed(
            self.script_review_project(), call.arguments["code"]))

    def offers_remember_script(self, call):
        """Whether the card offers Always run this exact script in this project: the person opened this exact text
        (``review_script``), outside a recovery review, where a remembered script could run."""
        return (call.approval and not call.executing and not call.recovery_review
                and call.id in self.reviewed_calls and call.reviewed_script == call.invocation_digest
                and self._reviewed_script_applies(call) and not self.reviewed_script_allowed(call))

    def remember_script(self, call):
        """The card's Always run this exact script in this project, the person's click: saved first, then the pump
        runs it through the same identity and consent checks as any later run. False when it does not apply;
        ``OSError`` when it can't be saved (nothing changes then)."""
        if self.pending.get(call.id) is not call or not self.offers_remember_script(call) or self.expire_tool(call):
            return False
        code = call.arguments["code"]
        try:   # The text C approved at the person's review, never what the call holds by now (break-it review, A-1).
            reviewed = grant_authority.value(grant_authority.APPROVED_SCRIPTS,
                                             grant_authority.call_key(self.state["id"], call.id))
            if reviewed is None or reviewed != grant_authority.text_digest(code):
                return False
        except UnicodeError:
            return False
        reviewed_scripts.store(self.store.directory).add(self.script_review_project(), code,
                                                        call.arguments.get("description", ""))
        self.version += 1
        return True

    def admission_script_consent(self):
        """The standing consent a call admitted now carries (host-only, never serialized), or None."""
        consents = self.chat_consents()
        if consents:
            return consents[0]
        return always_allow.token() if self.always_allow_scripts else None

    def script_consent_kind(self, call):
        """Which standing consent still covers this admitted script: "chat_session", "always_allow" or None.

        None for a script that touches preferences or Angelo's own add-on, or
        that would replace the open file (``consent_excluded``): it always
        asks, so a standing consent can't widen itself or cost the person
        their unsaved work (the card says which).
        """
        if self.reviewed_script_allowed(call):   # The person remembered these exact bytes for this project.
            return "reviewed_script"
        consent = call.script_consent
        if call.name != "angelo_execute_python" or consent is None or consent_excluded(call) or app_supplied(call):
            return None   # An app's own code never runs under a standing consent (module docstring).
        if any(consent is token for token in self.chat_consents()):   # Its own, or the lead's a worker inherits.
            return "chat_session"
        if consent is always_allow.token() and self.always_allow_scripts:
            return "always_allow"
        return None

    def offers_always_allow(self, call):
        """Whether this script's card offers Always allow scripts: in Auto, outside a recovery review, policy allowing.

        Never for a script a standing consent would not run (``consent_excluded``).
        """
        from . import managed_policy
        return (call.name == "angelo_execute_python" and call.approval and not call.executing
                and not call.recovery_review and not consent_excluded(call) and not app_supplied(call)
                and self.document is not None and not self.document_invalid
                and not self._closing and clean_options(self.state.get("options"))["approval"] == "AUTO_BOUNDED"
                and managed_policy.current().allow_auto_approval)

    def offers_session_allow(self, call):
        """Whether this script's card offers Allow for this session: Trust scripts for this chat, offered where the
        Approval Policy menu's would take (``_chat_trust_allowed``'s rule: any mode but Read only, a worker chat in
        Auto only), outside a recovery review, while it does not cover this chat already, never for a script a
        standing consent would not run (``consent_excluded``)."""
        from . import managed_policy, worker_trust
        return (call.name == "angelo_execute_python" and call.approval and not call.executing
                and not call.recovery_review and not consent_excluded(call) and not self.trusted_scripts
                and not app_supplied(call)
                and self.document is not None and not self.document_invalid and not self._closing
                and worker_trust.covers_mode(self, clean_options(self.state.get("options"))["approval"])
                and managed_policy.current().allow_auto_approval)

    def session_allow_script(self, call):
        """The card's Allow for this session, from the person's press: Trust scripts for this chat (held in C from
        that press, ``set_trusted_scripts``), covering this script too through the usual checks. False when it does
        not apply."""
        if self.pending.get(call.id) is not call or not self.offers_session_allow(call) or self.expire_tool(call):
            return False
        self.set_trusted_scripts(True)
        consents = self.chat_consents()
        if not consents:
            return False
        call.script_consent = consents[0]
        self.version += 1
        return True

    def always_allow_script(self, call):
        """The card's Always allow scripts, once the caller has saved the choice: it covers this script too.

        The pump then runs it through the same identity and consent checks as
        any later script (``script_authorized``). False when it does not apply.
        """
        if (self.pending.get(call.id) is not call or not self.offers_always_allow(call) or self.expire_tool(call)
                or not self.always_allow_scripts):
            return False
        call.script_consent = always_allow.token()
        self.version += 1
        return True

    def review_script(self, call):
        if self.pending.get(call.id) is call and call.approval and not self.expire_tool(call):
            call.automatic = False  # A fresh human review can replace revoked standing consent.
            if call.name == "angelo_execute_python":   # Its exact text, approved in C for this call (once).
                grant_authority.approve_script(self.state["id"], call.id, call.arguments.get("code", ""))
            call.reviewed_script = call.invocation_digest
            self.reviewed_calls.add(call.id)

    def script_authorized(self, call):
        if self.pending.get(call.id) is not call or not call.approval:
            return False
        try:
            digest = hashlib.sha256(json.dumps({"tool": call.name, "arguments": call.arguments}, sort_keys=True,
                ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        except (ValueError, TypeError, UnicodeError):
            return False
        if digest != call.invocation_digest:
            return False
        if call.automatic:
            return self.script_consent_kind(call) is not None
        return bool(call.reviewed_script and call.reviewed_script == call.invocation_digest)

    def _automatic_script(self, call):
        return self.script_consent_kind(call) is not None

    def require_script_authorized(self, call):
        if call.name == "angelo_execute_python" and not self.script_authorized(call):
            # Classified (C7b review L-2): raised only before the run starts, so it holds nothing of the request.
            from .refusal_codes import RefusalError
            raise RefusalError("Script identity or consent changed; review the exact script before dispatch. No script ran",
                               code="CANCELLED")

    def script_result(self, call, result):
        """The host's facts on an executed call's result: a script's authority and consent; in a worker chat, recovery
        words that never send its model to a request snapshot Restore refuses there (``script_errors.in_worker``): the
        chats Restore refuses (``worker_routing.worker_snapshots``), on the lane path and hosted in its worker alike."""
        if call.name == "angelo_execute_python" and call.executing and isinstance(result, dict):
            result.update(execution_mode="trusted_unconfined", confined=False,
                          consent=(call.consent_kind or "chat_session") if call.automatic else "exact_code_review",
                          external_effects_undoable=False, stop_interruptible=False)
            if getattr(self, "external_client", ""):   # The receipt names the app whose chat ran it.
                result["consent_app"] = self.external_client
        from . import script_errors, worker_routing
        return script_errors.in_worker(result) if worker_routing.worker_snapshots(self) else result


_grant_guard.entry_methods(TrustedScripts, "set_trusted_scripts", widens="enabled")
_grant_guard.entry_methods(TrustedScripts, "review_script")   # The person's review approves that exact text.
_grant_guard.entry_methods(TrustedScripts, "remember_script")   # The card's Always run this exact script (2.1.0).
