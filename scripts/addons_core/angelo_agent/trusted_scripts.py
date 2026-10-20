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
one Angelo's agent writes for a task the app phrased, always gets the
person's exact-code review (doc/angelo/external_mcp.rst).
"""

import hashlib
import json
from . import always_allow
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

    def admission_script_consent(self):
        """The standing consent a call admitted now carries (host-only, never serialized), or None."""
        if self.trusted_scripts:
            return self._script_consent
        return always_allow.token() if self.always_allow_scripts else None

    def script_consent_kind(self, call):
        """Which standing consent still covers this admitted script: "chat_session", "always_allow" or None.

        None for a script that touches preferences or Angelo's own add-on
        (``consent_excluded``): it always asks, so a standing consent can't
        widen itself (the card shows the preferences warning).
        """
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
