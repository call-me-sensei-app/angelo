# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page's "Angelo team" section, as page model data (``providers_page_layout._team``).

The same states, texts and operators as ``team_account_ui.draw``: a slim bar while signed out, a section card
while signing in, restoring, offline or signed in. Strings are translated here (``display_text.shown``).
"""

from . import managed_policy, team_account_ui as team
from .display_text import Text, shown
from .provider_registry import PROVIDER_NAMES
from .team_view import OFFLINE, RESTORING, SIGNED_IN, SIGNING_IN
from .ui_text import iface_


def model():
    policy = managed_policy.current()
    title = iface_("Angelo team")
    if team._account is None:
        return {"slim": True, "title": title, "lines": [shown(team._reason or Text(team.UNAVAILABLE))],
                "chip": iface_("Unavailable")}
    view = team._account.view()
    if view.status not in {SIGNED_IN, SIGNING_IN, RESTORING, OFFLINE}:
        lines = [shown(Text(team.INVITE_REQUIRED if policy.team_only else team.INVITE))]
        if view.message:
            lines.append(shown(view.message))
        problem = managed_policy.sign_in_problem(policy)
        return {"slim": True, "title": title, "lines": lines, "problems": [shown(problem)] if problem else [],
                "button": None if problem else (iface_("Sign in"), "team_sign_in", "")}
    section = {"title": title, "rows": []}
    if view.status == SIGNING_IN:
        section["rows"].append({"kind": "connection", "glyph": "people", "name": iface_("Signing in"),
                                "chip": (iface_("Waiting"), "muted", True),
                                "lines": [shown(Text(team.WAITING, console=team._account.console_url))],
                                "buttons": [(iface_("Copy sign-in link"), "team_copy_link", "", "secondary"),
                                            (iface_("Cancel"), "team_cancel", "", "ghost")]})
        return section
    if view.status == RESTORING:
        section["rows"].append({"kind": "connection", "glyph": "people", "name": iface_("Restoring your Angelo sign-in…"),
                                "chip": (iface_("Restoring"), "muted", True)})
        return section
    if view.status == OFFLINE:
        lines = [shown(view.message or Text(team.team_connections.SIGN_IN))]
        if policy.allow_personal_connections:
            lines.append(shown(Text(team.OFFLINE_RETRY)))
        section["rows"].append({"kind": "connection", "glyph": "people", "name": view.org_name or title,
                                "chip": (iface_("Offline"), "amber", False), "lines": lines,
                                "buttons": [(iface_("Sign out"), "team_sign_out", "", "secondary")]})
        return section
    return _signed_in(section, view)


def _signed_in(section, view):
    from . import team_access
    from .team_chat import access
    lines, facts = [], []
    if team._storage(view):
        facts.append(("lock", shown(team._storage(view)), "muted"))
    state = access()
    head = {"kind": "connection", "glyph": "people", "name": view.org_name or iface_("Angelo team"),
            "lines": lines, "facts": facts,
            "icons": [("refresh", "team_refresh", "")],
            "buttons": [(iface_("Sign out"), "team_sign_out", "", "secondary")]}
    head["lines"].insert(0, view.email or iface_("signed in"))
    section["rows"].append(head)
    if state["state"] == team_access.NO_SEAT:
        head["chip"] = (iface_("No seat"), "amber", False)
        lines.append(shown(state["title"]))
        if state["page"]:
            head["fix"] = (shown(state["note"]), "team_console", state["page"].upper())
        else:
            lines.append(shown(state["note"]))
        return section
    head["chip"] = (iface_("Seat assigned"), "teal", False)
    lines.append(shown(Text(team.SEAT)))
    if state["state"] in {team_access.NO_PROVIDERS, team_access.NO_CODING}:
        lines.append(shown(state["title"]))
        if state["page"]:
            head["fix"] = (shown(state["note"]), "team_console", state["page"].upper())
    if view.providers is None and not view.providers_error:
        head["chip"] = (iface_("Checking…"), "muted", True)
        lines.append(iface_("Checking which providers your team offers…"))
    for problem in (view.providers_error, view.grant_error, view.message):
        if problem:
            lines.append(shown(problem))
    for record in team._records.values():
        ready = team.ready(record)
        label = (iface_("Available in chat") if team.is_coding(record) else iface_("Ready")) if ready else iface_("Not ready")
        section["rows"].append({"kind": "connection", "tile": record["name"][:1].upper(), "dot": "teal" if ready else "muted",
                                "name": record["name"], "tag": iface_("Angelo team"),
                                "lines": [shown(Text(team.ENTRY, provider=PROVIDER_NAMES[record["provider"]],
                                                     status=team.status(record)))],
                                "chip": (label, "teal" if ready else "muted", False)})
    missing = team.team_connections.missing_providers(view)
    if missing and view.providers:
        section["description"] = shown(Text(team.NOT_OFFERED, providers=", ".join(missing)))
    return section
