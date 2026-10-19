# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether this session may offer the team-library tools, and to whom.

The tools are offered only while the team account is signed in with the
console scope ``api`` (library and IPs need no seat, contract 2026-10-08).
Signed out, ``allowed_tools`` leaves the whole family out of the request, so
every plan schema, Codex declaration and discovery listing is byte-identical to
a build without it. The account itself stays the last gate: every call needs
its access token.

``team_account_ui`` imports bpy, so it is resolved at call time; host processes
without Blender simply have no team account. No bpy here; safe on any thread
(``TeamAccount.view`` returns an immutable view without locking).
"""

from .team_library_spec import TEAM_NAMES

SIGN_IN = "Sign in to your Angelo team in Preferences > AI Providers to use the team library and IPs."
SCOPE = ("This Angelo sign-in can't read the team library (it lacks the console permission). Sign out and sign in "
         "again in Preferences > AI Providers.")


def account():
    """The team account, or None (no Blender, not started, or an invalid console origin)."""
    try:
        from . import team_account_ui
    except ImportError:
        return None
    return team_account_ui.account()


def refusal(current=None):
    """Why the team-library tools can't be used now, or "" when they can."""
    current = account() if current is None else current
    view = current.view() if current is not None else None
    if view is None or not view.signed_in:
        return SIGN_IN
    if "api" not in view.scopes:
        return SCOPE
    return ""


def enabled(current=None):
    return not refusal(current)


def allowed_tools(tool_names, current=None):
    """A request's allowed tool set: None (every tool) when signed in, else every tool but the family."""
    return None if enabled(current) else frozenset(tool_names) - TEAM_NAMES


def hidden_tools(current=None):
    """Tool names discovery must not list or describe in this session."""
    return frozenset() if enabled(current) else TEAM_NAMES
