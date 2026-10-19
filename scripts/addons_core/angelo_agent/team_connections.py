# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team connections: AI Providers connections derived from the signed-in team account.

A team connection is virtual. It exists while the account is signed in and
the organization has an active key for its provider (``providers/available``),
and it is NEVER written to connections.json (``connections.ConnectionStore``
refuses method TEAM). Its record has the stored records' fields, so chat,
collaborator, Media Studio and Flows code reads it like any other.

Its id is a UUID derived from the console, organization, person and mode, so a
chat bound to it finds it again after signing out and in, and a different
person or organization on the same computer gets different connections (and
native contexts).

Usability: signed in, a seat, the ``generate`` scope, the provider offered by
the organization and, for coding agents, a current grant key. Health for Media
Studio is the provider's availability plus the registry's pinned models;
balance and catalog probes are refused by the gateway and never sent.

Standard library only; no bpy.
"""

import uuid

from .api_connections import CheckResult
from .display_text import Text
from .provider_registry import CODING, SPECS, TEAM
from .team_endpoints import PROVIDER_NAMES
from .team_errors import NO_SEAT, missing_key_message
from .ui_text import n_

SIGN_IN = n_("Sign in to your Angelo team")
NO_SEAT_IN = n_("You don't have a seat in {org}. Contact your admin for access.")
NO_MODEL_USE = n_("This sign-in doesn't allow model use; sign out and sign in again.")
PREPARING = n_("Preparing coding-agent access…")
AVAILABLE = n_("Available in chat")
TEAM_KEY = n_("Team key available")
HEALTH = n_("Team key available · model access is checked when you generate")

_NAMESPACE = uuid.UUID("0c3e2b5e-8d0a-4d4f-a7f1-7a8e5c2b9d64")  # Angelo team connections.
TEAM_MODES_IN_ORDER = tuple(mode for mode, spec in SPECS.items() if spec["method"] == TEAM)


def connection_id(console_url, org_id, user_id, mode):
    return str(uuid.uuid5(_NAMESPACE, "|".join((console_url, org_id, user_id, mode))))


def records(view, console_url):
    """``{id: record}`` for every team mode whose provider the organization offers; empty when signed out."""
    if not view.signed_in or not view.providers:
        return {}
    output = {}
    for mode in TEAM_MODES_IN_ORDER:
        spec = SPECS[mode]
        if spec["team_provider"] not in view.providers:
            continue
        identity = connection_id(console_url, view.org_id, view.user_id, mode)
        output[identity] = {"id": identity, "name": ("%s · %s" % (spec["name"], view.org_name))[:64],
                            "provider": spec["provider"], "method": TEAM, "mode": mode, "model": "",
                            "reuse_native": False, "group": spec["group"], "native_profile": None}
    return output


def missing_providers(view):
    """Display names of team services the organization doesn't offer, for one short line."""
    if not view.signed_in or view.providers is None:
        return []
    names = []
    for mode in TEAM_MODES_IN_ORDER:
        provider = SPECS[mode]["team_provider"]
        if provider not in view.providers and PROVIDER_NAMES[provider] not in names:
            names.append(PROVIDER_NAMES[provider])
    return names


def unavailable_reason(view, record, has_grant_key):
    """Why this team connection can't be used now, or ""."""
    spec = SPECS[record["mode"]]
    if not view.signed_in:
        return Text(SIGN_IN)
    if not view.seat:
        return Text(NO_SEAT_IN, org=view.org_name) if view.org_name else Text(NO_SEAT)
    if "generate" not in view.scopes:
        return Text(NO_MODEL_USE)
    if spec["team_provider"] not in (view.providers or ()):
        return missing_key_message(spec["team_provider"])
    if spec["group"] == CODING and not has_grant_key:
        return view.grant_error or Text(PREPARING)
    return ""


def status(view, record, has_grant_key):
    reason = unavailable_reason(view, record, has_grant_key)
    if reason:
        return reason
    return Text(AVAILABLE) if SPECS[record["mode"]]["group"] == CODING else Text(TEAM_KEY)


def health(view, record):
    """Media Studio health of a usable team generation connection, or None."""
    spec = SPECS[record["mode"]]
    if spec["group"] == CODING or unavailable_reason(view, record, False):
        return None
    return CheckResult("configured", Text(HEALTH), False,
                       [dict(m, access="team") for m in spec["models"]])
