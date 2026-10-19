# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a signed-in person can use their team in the chat yet, and if not, which reason applies.

One answer for the chat's "Your team" row and the AI Providers team section,
taken from the state the account already has (seat, role, providers, the
organization's admin contact). An admin or owner is offered the console page
that fixes the problem; a member is asked to contact their admin, with the
admin contact from the organization policy when it is set. Every text is a
message id or a ``display_text.Text`` template, translated where it is drawn.

Standard library only; no bpy.
"""

from .display_text import Text
from .ui_text import n_

READY, CHECKING, NO_SEAT, NO_PROVIDERS, NO_CODING, ERROR = "ready", "checking", "no_seat", "no_providers", "no_coding", "error"

NO_SEAT_TITLE = n_("No seat in {org}")
NO_PROVIDERS_TITLE = n_("{org} has no AI provider keys yet")
NO_CODING_TITLE = n_("No coding provider (Claude or OpenAI) is set up for this team")
ASSIGN_SEAT = n_("Assign yourself a seat in People")
ADD_KEYS = n_("Add keys in the console → Keys")
ADD_CODING_KEY = n_("Add a Claude or OpenAI key in the console → Keys")
CONTACT = n_("Contact your admin")
CONTACT_AT = n_("Contact your admin: {contact}")
CHECKING_NOTE = n_("Checking which providers your team offers…")
PREPARING_NOTE = n_("Preparing your team's connection…")
READY_LINE = n_("Signed in to {org}. Your team's {providers} keys are ready.")
READY_LINE_ONE = n_("Signed in to {org}. Your team's {provider} key is ready.")
AND = n_("{first} and {second}")
TEAM = n_("Your team")
CODING_NAMES = (("anthropic", "Claude"), ("openai", "OpenAI"), ("xai", "Grok"), ("minimax", "MiniMax"), ("moonshot", "Kimi"))


def access(view, coding_ready, admin_contact=""):
    """``{"state", "title", "note", "page", "busy"}`` for a signed-in person, or None when not signed in.

    ``coding_ready``: whether a team coding connection is usable now. ``page`` is the console page that fixes
    the problem ("people", "keys") when the person can fix it, else "".
    """
    if view is None or not view.signed_in:
        return None
    org = view.org_name
    contact = Text(CONTACT_AT, contact=admin_contact) if admin_contact else CONTACT

    def blocked(state, title, admin_note, page):
        if view.is_admin:
            return {"state": state, "title": title, "note": admin_note, "page": page, "busy": False}
        return {"state": state, "title": title, "note": contact, "page": "", "busy": False}

    if not view.seat:
        return blocked(NO_SEAT, Text(NO_SEAT_TITLE, org=org), ASSIGN_SEAT, "people")
    if view.providers is None:
        if view.providers_error:
            return {"state": ERROR, "title": TEAM, "note": view.providers_error, "page": "", "busy": False}
        return {"state": CHECKING, "title": TEAM, "note": CHECKING_NOTE, "page": "", "busy": True}
    if not view.providers:
        return blocked(NO_PROVIDERS, Text(NO_PROVIDERS_TITLE, org=org), ADD_KEYS, "keys")
    if not coding_providers(view.providers):
        return blocked(NO_CODING, NO_CODING_TITLE, ADD_CODING_KEY, "keys")
    if not coding_ready:  # Offered, but the gateway key for this device is still being issued.
        if view.grant_error:
            return {"state": ERROR, "title": TEAM, "note": view.grant_error, "page": "", "busy": False}
        return {"state": CHECKING, "title": TEAM, "note": PREPARING_NOTE, "page": "", "busy": True}
    return {"state": READY, "title": TEAM, "note": ready_line(view), "page": "", "busy": False}


def coding_providers(providers):
    """Display names of the coding providers among the team's, in the order the chat prefers them."""
    offered = set(providers or ())
    return [name for provider, name in CODING_NAMES if provider in offered]


def ready_line(view):
    """"Signed in to <org>. Your team's Claude and OpenAI keys are ready." (two names at most, joined whole)."""
    names = coding_providers(view.providers)
    if len(names) == 1:
        return Text(READY_LINE_ONE, org=view.org_name, provider=names[0])
    return Text(READY_LINE, org=view.org_name, providers=Text(AND, first=names[0], second=", ".join(names[1:])))
