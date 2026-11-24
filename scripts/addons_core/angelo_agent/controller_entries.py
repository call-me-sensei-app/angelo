# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The entry points behind a chat's grants (grant_guard.entry), wrapped once ``controller`` defines them: approving and
running a tool, voice, and changing the chat's options, where Ask, Read only and Fast off narrow (chat_settings).

An approval Angelo acts on is held in C (grant_authority.APPROVED_CALLS, the C guard review's SHOULD-4): approving a
native job records the call's exact invocation there, in the grant scope of the person's click or of Angelo's own
Auto decision, and the commit checks it (program_commit_controller). The call's Python flags alone commit nothing."""

from . import approval_modes, grant_authority, grant_guard
from .chat_settings import options_widen, planning, stored_options
from .controller import Controller


def _named(wrapper, original):
    """``wrapper`` named and documented as ``original`` (grant_guard.ENTRIES names the entry by it), with no
    ``__wrapped__``: the unguarded original stays only in the wrapper's closure, and the wrapper itself only in the
    entry's, sealed in C (grant_guard.entry; the Fusion audit's G3)."""
    for attribute in ("__module__", "__name__", "__qualname__", "__doc__"):
        setattr(wrapper, attribute, getattr(original, attribute))
    return wrapper


def _approving(original):
    def approve_tool(self, call, executor, document):
        if not grant_authority.granting():   # Before anything changes: the approval is recorded in C or not at all.
            raise PermissionError(grant_guard.NOT_YOURS)
        result = original(self, call, executor, document)
        if getattr(call, "authorized", False):
            grant_authority.record(grant_authority.APPROVED_CALLS,
                                   grant_authority.call_key(self.state["id"], call.id), call.invocation_digest)
        return result
    return _named(approve_tool, original)


def _holding_modes(original):
    def set_options(self, **changes):
        current = self.state.get("options")
        if planning(current) and not planning(stored_options(stored_options(current) | changes)) \
                and grant_guard.inside_agent_code():   # Only the person ends plan mode (plan_mode.rst, "Safety").
            raise grant_guard.Refused()
        if "approval" in changes:   # Auto held in C first (a scope for Auto; Ask and Read only narrow): approval_modes.
            approval_modes.record(self.state["id"], changes["approval"])
        try:
            return original(self, **changes)
        finally:   # Every route into planning, not only START; keyed on the state, so a failed save still cancels.
            if not planning(current) and planning(self.state.get("options")):
                from . import planning_controls
                planning_controls.planning_started(self)
    return _named(set_options, original)


Controller.approve_tool = _approving(Controller.approve_tool)
Controller.set_options = _holding_modes(Controller.set_options)
grant_guard.entry_methods(Controller, "approve_tool", "execute_tool", "start_voice")
grant_guard.entry_methods(Controller, "set_options", widens=options_widen)
