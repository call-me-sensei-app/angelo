# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host UI consent for unrestricted scripts; in-memory Controller ownership.

Two standing consents let a script run without its own review, and nothing
else does: Trust scripts for this chat (in memory, this chat and document) and
the person's Always allow scripts (``always_allow``: saved per user, Auto chats
only). A script carries the consent current at its admission and runs unasked
only while that same consent still covers it at dispatch.

Neither covers an External conversation (a connected MCP app's or a CLI
client's, ``Controller.external_client``): a script an outside app sends, or
one Angelo's agent writes for a task the app phrased, gets the person's
exact-code review (doc/angelo/external_mcp.rst). The one standing consent an
External conversation has is narrower (maintainer decision, 2026-10-23): a
script file the app names (``angelo_run_script_file``) that the person chose
to "Always allow while it's unchanged" (``script_files``), keyed by the app,
the saved project, the file's real path and its content hash, in Approve for
me, with ``AllowAutoApproval``. Any change to the file, the project, the app
or those settings asks again.
"""

import hashlib
import json
import os
from . import always_allow, script_files
from .chat_settings import clean_options


def consent_excluded(call):
    """Whether a script must always ask: it refers to preferences (``uses_preferences``, where the person's
    Always allow scripts lives) or to Angelo's own add-on. A heuristic over the code, as the card's warning is;
    a script that hides the names is still covered by the property's guard (trusted_scripts_ui)."""
    from .fallback_categories import uses_preferences
    code = call.arguments.get("code") if isinstance(call.arguments, dict) else None
    return not isinstance(code, str) or uses_preferences(code) or "angelo_agent" in code


class TrustedScripts:
    """Consent policy mixed into the existing Controller, never a separate executor."""
    @property
    def trusted_scripts(self):
        from . import managed_policy
        return (self._script_consent is not None and not getattr(self, "external_client", "")
                and self._script_consent[:2] == (self.state["id"], self.document)
                and not self.document_invalid and not self._closing
                and clean_options(self.state.get("options"))["approval"] != "READ_ONLY"
                and managed_policy.current().allow_auto_approval)

    def set_trusted_scripts(self, enabled):
        """Human UI consent, in memory only, scoped to this chat/document.

        Saved options and tools cannot set this. Queued scripts retain their
        old admission token and still need exact-code manual approval.
        """
        from . import managed_policy
        if enabled and (self.document is None or self.document_invalid or self._closing
                        or getattr(self, "external_client", "")
                        or clean_options(self.state.get("options"))["approval"] == "READ_ONLY"
                        or not managed_policy.current().allow_auto_approval):
            raise RuntimeError("Trusted scripts are unavailable for this document or approval policy")
        self._script_consent = (self.state["id"], self.document, object()) if enabled else None
        self.version += 1

    @property
    def always_allow_scripts(self):
        """Whether the person's Always allow scripts covers this chat now: on, allowed by policy, in Auto.

        Ask still asks and Read only still refuses; a changed document or a closing chat never runs a script unasked.
        """
        return (always_allow.token() is not None and self.document is not None and not self.document_invalid
                and not self._closing and not getattr(self, "external_client", "")
                and clean_options(self.state.get("options"))["approval"] == "AUTO_BOUNDED")

    def script_project(self):
        """The project a script file approval is keyed by: the open .blend's real path, "" while unsaved."""
        try:
            import bpy
        except ImportError:
            return ""
        path = bpy.data.filepath
        return os.path.realpath(path) if path else ""

    def _script_file_applies(self, call):
        """Whether a script file approval may apply to ``call`` at all (any approval, not this file's)."""
        from . import managed_policy
        script = getattr(call, "script_file", None)
        code = call.arguments.get("code") if isinstance(call.arguments, dict) else None
        return (call.name == "angelo_execute_python" and script is not None and bool(getattr(self, "external_client", ""))
                and isinstance(code, str) and code == script.text and script_files.text_digest(code) == script.sha256
                and not consent_excluded(call) and self.document is not None and not self.document_invalid
                and not self._closing and clean_options(self.state.get("options"))["approval"] == "AUTO_BOUNDED"
                and managed_policy.current().allow_auto_approval and bool(self.script_project())
                and self._app_runs_python())

    def _app_runs_python(self):
        """Whether the connected app still has the python level (``external_clients``, read as it is now). A headless
        child has the levels the launcher read for this call (``external_levels``; a change restarts the child)."""
        from .external_clients import PYTHON, AppRegistry
        levels = getattr(self, "external_levels", None)
        if levels is not None:
            return PYTHON in levels
        record = AppRegistry(self.workspace.parent).load().get(self.external_client)
        return record is not None and PYTHON in record.get("levels", ())

    def script_file_refusal(self, call):
        """Why the person's approval does not cover this script file now, as a key: ``excluded`` (it names
        preferences or Angelo's add-on), ``mode`` (Ask or Read only), ``policy`` (``AllowAutoApproval`` off),
        ``levels`` (no python level), ``project`` (no saved project, or not the session's own file), ``changed``
        (allowed before with other content) or ``not_approved``. Headless says which (headless_host)."""
        from . import managed_policy
        if consent_excluded(call):
            return "excluded"
        if clean_options(self.state.get("options"))["approval"] != "AUTO_BOUNDED":
            return "mode"
        if not managed_policy.current().allow_auto_approval:
            return "policy"
        if not self._app_runs_python():
            return "levels"
        if not self.script_project():
            return "project"
        return "changed" if self.script_file_changed(call) is not None else "not_approved"

    def script_file_allowed(self, call):
        """Whether the person's saved approval covers this exact script file now (checked again at dispatch)."""
        if not self._script_file_applies(call):
            return False
        script = call.script_file
        return script_files.allowed(self.external_client, self.script_project(), script.path, script.sha256)

    def offers_script_file_allow(self, call):
        """Whether this script's card offers "Always allow this file while it's unchanged": an app's script file in
        an External conversation in Approve for me, policy allowing, the project saved, outside a recovery review,
        once the person has opened its exact text (``review_script``)."""
        return (call.approval and not call.executing and not call.recovery_review
                and call.id in self.reviewed_calls and call.reviewed_script == call.invocation_digest
                and self._script_file_applies(call) and not self.script_file_allowed(call))

    def script_file_changed(self, call):
        """The earlier approval of this app's file at this path and project when its content changed since, or None."""
        script = getattr(call, "script_file", None)
        if script is None or not getattr(self, "external_client", ""):
            return None
        earlier = script_files.previous(self.external_client, self.script_project(), script.path)
        return earlier if earlier is not None and earlier["sha256"] != script.sha256 else None

    def allow_script_file(self, call):
        """The card's "Always allow this file while it's unchanged", once the person chose it: saved first
        (``script_files.allow``); the pump then runs this script through the same identity and consent checks as
        any later run of the file (``script_authorized``).

        False when it does not apply; ``OSError`` when it can't be saved (nothing changes then).
        """
        if self.pending.get(call.id) is not call or not self.offers_script_file_allow(call) or self.expire_tool(call):
            return False
        script_files.allow(self.external_client, self.script_project(), call.script_file)
        self.version += 1
        return True

    def admission_script_consent(self):
        """The standing consent a call admitted now carries (host-only, never serialized), or None."""
        if self.trusted_scripts:
            return self._script_consent
        return always_allow.token() if self.always_allow_scripts else None

    def script_consent_kind(self, call):
        """Which standing consent still covers this admitted script: "chat_session", "always_allow",
        "script_file" (an External conversation's allowed script file, ``script_file_allowed``) or None.

        None for a script that touches preferences or Angelo's own add-on
        (``consent_excluded``): it always asks, so a standing consent can't
        widen itself (the card shows the preferences warning).
        """
        if getattr(call, "script_file", None) is not None and self.script_file_allowed(call):
            return "script_file"
        consent = call.script_consent
        if call.name != "angelo_execute_python" or consent is None or consent_excluded(call):
            return None
        if consent is self._script_consent and self.trusted_scripts:
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
                and not call.recovery_review and not consent_excluded(call) and not getattr(self, "external_client", "")
                and self.document is not None and not self.document_invalid
                and not self._closing and clean_options(self.state.get("options"))["approval"] == "AUTO_BOUNDED"
                and managed_policy.current().allow_auto_approval)

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
            raise RuntimeError("Script identity or consent changed; review the exact script before dispatch. No script ran")

    @staticmethod
    def script_result(call, result):
        if call.name == "angelo_execute_python" and call.executing and isinstance(result, dict):
            result.update(execution_mode="trusted_unconfined", confined=False,
                          consent=(call.consent_kind or "chat_session") if call.automatic else "exact_code_review",
                          external_effects_undoable=False, stop_interruptible=False)
        return result
