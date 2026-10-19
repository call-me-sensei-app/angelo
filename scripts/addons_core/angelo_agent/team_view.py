# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the team account shows, and the console's token answer, without any token in the view.

Standard library only; no bpy. ``team_account`` owns the state transitions.
"""

import base64
import json
import re
from dataclasses import dataclass

_PRINTABLE = re.compile(r"[\x21-\x7e]{16,4096}")

SIGNED_OUT, SIGNING_IN, RESTORING, SIGNED_IN, OFFLINE = "signed_out", "signing_in", "restoring", "signed_in", "offline"


@dataclass(frozen=True)
class AccountView:
    """What the UI shows; never a token. ``stored`` says where the sign-in is kept."""

    status: str = SIGNED_OUT
    message: str = ""
    org_id: str = ""
    org_name: str = ""
    user_id: str = ""
    email: str = ""
    seat: bool = False
    role: str = ""            # "owner", "admin" or "member" from the access token, for what to suggest; "" unknown.
    scopes: frozenset = frozenset()
    providers: tuple | None = None
    providers_error: str = ""
    grant_key_expires: float | None = None
    grant_error: str = ""
    usage: dict | None = None  # The person's own usage numbers (team_reads.usage), for the account menu.
    org_policy: dict | None = None  # The organization's app policy from the token answer (org_policy), or None.
    stored: str = ""          # "saved", "unreadable" (saved; this build can't read it), "session" or "failed"
    sign_in_url: str = ""
    version: int = 0

    @property
    def signed_in(self):
        return self.status == SIGNED_IN

    @property
    def is_admin(self):
        return self.role in {"owner", "admin"}

    @property
    def can_generate(self):
        return self.signed_in and self.seat and "generate" in self.scopes


def parse_tokens(value):
    """The token response's fields, or None when it isn't the documented shape."""
    if not isinstance(value, dict):
        return None
    access, refresh, expires = value.get("access_token"), value.get("refresh_token"), value.get("expires_in")
    org, user = value.get("org") or {}, value.get("user") or {}
    if (not isinstance(access, str) or not _PRINTABLE.fullmatch(access) or not isinstance(refresh, str)
            or not _PRINTABLE.fullmatch(refresh) or isinstance(expires, bool) or not isinstance(expires, int)
            or not 1 <= expires <= 86400 or not isinstance(org, dict) or not isinstance(org.get("id"), str)
            or not org["id"]):
        return None
    text = lambda v: v[:200] if isinstance(v, str) else ""  # noqa: E731 - display fields only.
    return {"access": access, "refresh": refresh, "expires_in": expires, "seat": value.get("seat") is True,
            "org_policy": org_policy(value.get("policy")), "role": token_role(access),
            "scopes": frozenset(str(value.get("scope", "")).split()), "org_id": org["id"][:200],
            "org_name": text(org.get("name")) or "Angelo team", "user_id": text(user.get("id")),
            "email": text(user.get("email"))}


# The organization's app policy (the console's ``orgs.app_policy``):
# the switches an owner or admin sets. The console always sends every field, filled with its defaults. A console
# without it sends no ``policy``; a missing switch keeps the default (on), one that isn't a boolean reads as off
# (the stricter value), and unknown fields are ignored. ``adminContact`` is one line of plain text or null.
ROLES = ("owner", "admin", "member")


def token_role(access):
    """The organization role an access token carries (claims ``role``), or "".

    Read without verification, only to choose what the app suggests (an admin is offered the console page that
    fixes a problem, a member is asked to contact their admin); the console enforces every permission itself.
    """
    try:
        payload = access.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, TypeError):
        return ""
    role = claims.get("role") if isinstance(claims, dict) else None
    return role if role in ROLES else ""


ORG_SWITCHES = ("allowPersonalConnections", "allowLocalCliAccess", "allowVoice", "allowCollaborators")
# The console's UNSAFE_TEXT: control characters, line and paragraph separators, bidirectional overrides.
UNSAFE_TEXT = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def admin_contact(value):
    """The console's ``adminContact`` as shown text: "" for none, or for one it would refuse (at most 200)."""
    contact = value.strip() if isinstance(value, str) else ""
    return "" if UNSAFE_TEXT.search(contact) else contact[:200]


def org_policy(value):
    """The token answer's ``policy`` as ``{switch: bool, "adminContact": str}``, or None when there is none."""
    if not isinstance(value, dict):
        return None
    policy = {name: value.get(name, True) is True for name in ORG_SWITCHES}
    if "allowAutoApproval" in value:
        policy["allowAutoApproval"] = value["allowAutoApproval"] is True
    if value.get("requireAskForEdits") is not None:
        policy["requireAskForEdits"] = value["requireAskForEdits"] is not False  # Restrictive: only false lifts it.
    policy["adminContact"] = admin_contact(value.get("adminContact"))
    return policy
