# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixture sign-ins for host tests (provider_accounts): JWTs built here from made-up claims, never a real token, and
files written only into a test's own temporary folders, never a real ``auth.json`` or ``.claude.json``."""

import base64
import json
from pathlib import Path

EVAL_ACCOUNT_ID = "7684a4d7-1111-4111-8111-111111111111"   # The evaluation account's real prefix, a made-up rest.
EVAL_ACCOUNT_EMAIL = "jack@hyperbond.studio"
OTHER_ID = "0badc0de-2222-4222-8222-222222222222"
OTHER_EMAIL = "someone@example.com"
CLAUDE_EMAIL = "claude-tests@example.org"
CLAUDE_UUID = "5ca1ab1e-3333-4333-8333-333333333333"
ACCESS, REFRESH = "fixture-access-token-never-shown", "fixture-refresh-token-never-shown"
UNSET = object()


def _part(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def jwt(claims):
    """An unsigned JWT carrying ``claims`` (a dict, or raw bytes for a malformed payload)."""
    return "%s.%s.%s" % (_part({"alg": "RS256", "typ": "JWT"}), _part(claims), "fixture-signature")


def codex_claims(account_id=EVAL_ACCOUNT_ID, email=EVAL_ACCOUNT_EMAIL):
    claims = {"iss": "https://auth.openai.com", "aud": ["app_fixture"], "exp": 4102444800}
    if email is not None:
        claims["email"] = email
    if account_id is not None:
        claims["https://api.openai.com/auth"] = {"chatgpt_account_id": account_id, "chatgpt_plan_type": "pro"}
    return claims


def codex_sign_in(profile, account_id=EVAL_ACCOUNT_ID, email=EVAL_ACCOUNT_EMAIL, id_token=None, stored=UNSET, **top):
    """Write a Codex ``auth.json`` into ``profile`` as ``codex login`` leaves it; returns its path."""
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    tokens = {"id_token": jwt(codex_claims(account_id, email)) if id_token is None else id_token,
              "access_token": ACCESS, "refresh_token": REFRESH,
              "account_id": account_id if stored is UNSET else stored}
    if tokens["account_id"] is None:
        del tokens["account_id"]
    data = dict({"OPENAI_API_KEY": None, "tokens": tokens, "last_refresh": "2026-11-05T00:00:00Z"}, **top)
    (profile / "auth.json").write_text(json.dumps(data), encoding="utf-8")
    return profile / "auth.json"


def claude_sign_in(profile, email=CLAUDE_EMAIL, account_uuid=CLAUDE_UUID):
    """Write a Claude Code ``.claude.json`` into ``profile`` with its signed-in ``oauthAccount``; returns its path."""
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    account = {"accountUuid": account_uuid, "emailAddress": email, "organizationUuid": OTHER_ID,
               "displayName": "Fixture"}
    data = {"numStartups": 3, "oauthAccount": {k: v for k, v in account.items() if v is not None}}
    (profile / ".claude.json").write_text(json.dumps(data), encoding="utf-8")
    return profile / ".claude.json"


def store(directory, *records, name="connections.json"):
    """A connection store holding ``records`` (``{"id", "mode", ...}``); returns its folder."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({"version": 3, "active": None, "connections": list(records)}),
                                  encoding="utf-8")
    return directory
