# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which account a billed run's provider profile is signed in to, checked before the run (stdlib only, no network).

A profile folder's name (``eval-account``) never proves its account: a browser sign-in signs in whatever account that
browser holds, and a folder was re-signed into the maintainer's main account before (2026-10-30, 2026-11-05). So every
billed start reads the account itself and refuses another one (``provider_isolation`` for each
``ANGELO_TEST_REAL_PROVIDERS`` opt-in and its connection store, and ``check_billed`` for the billed harnesses outside
it).

* A Codex profile (``codex-profile/<uuid>``, its ``CODEX_HOME``): the claims of ``auth.json``'s ``tokens.id_token``, a
  JWT whose payload alone is decoded, with no signature check. Its ``chatgpt_account_id``
  (``https://api.openai.com/auth``) is required and must start with ``CODEX_ACCOUNT["account_id_prefix"]``: one email
  can sign in to several ChatGPT accounts (a team workspace), so an email alone never decides (every current id_token
  carries the account id Codex bills). ``tokens.account_id`` must agree when present. The sign-in must be the file
  Codex reads (its ``config.toml`` names no other credential store) and hold no API key Codex would bill instead.
  The access and refresh tokens are never used, kept or shown.
* A Claude Code profile (``claude-profile/<uuid>``, its ``CLAUDE_CONFIG_DIR``): Claude Code (2.1.x) keeps the
  signed-in account in ``$CLAUDE_CONFIG_DIR/.claude.json`` as ``oauthAccount`` (``emailAddress``, ``accountUuid``); its
  credentials are elsewhere (the Keychain or ``.credentials.json``) and never read. A profile that could bill another
  credential from inside it is refused: an ``apiKeyHelper`` or a credential in an ``env`` block of its
  ``settings.json``/``settings.local.json``, or a ``primaryApiKey`` in ``.claude.json``. No expected Claude account is
  recorded anywhere this repository or its release kit can rely on, so the caller must name it (an email or account
  UUID: the opt-in's ``|account=`` field, or ``CLAUDE_ACCOUNT_ENV`` for the harnesses outside the opt-in); a Claude
  profile without one is refused.
* A connection store (``store_accounts``): every subscription connection in it, and in the copies the app reads back
  beside ``connections.json`` (``kept_state``), is checked as above at its profile; one that reuses the person's own
  sign-in is refused. An API-key or team connection has no account to read: it bills its own key (pay-per-token;
  ``eval_live_agent`` refuses coding-plan keys) or the team's gateway, never a personal plan.

Only an account id's first 8 characters and an email's domain are ever shown (``summary``). Missing, unreadable or
malformed claims refuse.
"""

import base64
import json
import os
import time
import uuid
from pathlib import Path

# The dedicated evaluation account: the ChatGPT account every ChatGPT eval bills, never the maintainer's main one.
# The one place it is named.
CODEX_ACCOUNT = {"account_id_prefix": "7684a4d7", "email": "jack@hyperbond.studio"}
CLAUDE_ACCOUNT_ENV = "ANGELO_TEST_CLAUDE_ACCOUNT"   # The expected Claude account (email or account UUID).
AUTH_CLAIMS = "https://api.openai.com/auth"
PROFILE_CLAIMS = "https://api.openai.com/profile"
CODEX_SIGN_IN = "auth.json"
CODEX_STORE_KEY = "cli_auth_credentials_store"
CLAUDE_CONFIG = ".claude.json"
CLAUDE_SETTINGS = ("settings.json", "settings.local.json")
CONNECTIONS = "connections.json"
FAMILIES = {"codex_": "codex-profile", "claude_": "claude-profile"}
# A sign-in file of either CLI: a connection store holding one itself is a profile, not a store.
SIGN_IN_FILES = (CODEX_SIGN_IN, CLAUDE_CONFIG, ".credentials.json")
READ_ATTEMPTS = 3   # A CLI rewrites its file in place (truncate, then write): a read mid-write retries.
READ_RETRY_S = 0.05

# Provider credentials and identities, and locations of the person's own runtime state: a test process never inherits
# one (provider_isolation), and a Claude profile's settings may set none. A superset of
# angelo_agent.runtime_environment.CREDENTIALS (pinned by test_native_suite_isolation).
CREDENTIALS = frozenset({
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
    "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_WORKSPACE_ID",
    "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    "AWS_BEARER_TOKEN_BEDROCK", "XAI_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY", "MINIMAX_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "ARK_API_KEY", "TRIPO_API_KEY", "MESHY_API_KEY",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_HOST_CREDS_FILE", "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CODEX_SQLITE_HOME",   # The person's Codex thread store, outside CODEX_HOME when set.
})
CREDENTIAL_SUFFIXES = ("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN", "_OAUTH_TOKEN", "_REFRESH_TOKEN", "_GRANT_KEY")


def credential(name):
    """Whether a variable is a provider credential or names the person's runtime state."""
    upper = str(name).upper()
    return upper in CREDENTIALS or upper.endswith(CREDENTIAL_SUFFIXES)


class AccountRefusal(Exception):
    """The profile is not signed in to the expected account, or its account cannot be read."""


def summary(account_id=None, email=None):
    """What may be shown of an account: the id's first 8 characters and the email's domain."""
    shown = {}
    if account_id:
        shown["account_id"] = str(account_id)[:8]
    if email:
        local, at, domain = str(email).rpartition("@")
        shown["email_domain"] = domain if at and local and domain else "?"
    return shown


def _shown(account_id=None, email=None):
    return ", ".join("%s %s" % pair for pair in summary(account_id, email).items()) or "no account claims"


def jwt_claims(token):
    """The payload of a JWT as a dict, decoded without verifying it; raises ``AccountRefusal`` when malformed."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or not parts[1]:
        raise AccountRefusal("its id_token is not a JWT")
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise AccountRefusal("its id_token's claims cannot be decoded") from None
    if not isinstance(claims, dict):
        raise AccountRefusal("its id_token's claims are not an object")
    return claims


def _read_json(path, what, sleep=time.sleep):
    for attempt in range(READ_ATTEMPTS):
        try:
            value = json.loads(Path(path).read_bytes())   # As the app reads it (kept_state.read): BOM, UTF-16.
            break
        except FileNotFoundError:
            raise AccountRefusal("%s is missing" % what) from None
        except (ValueError, UnicodeDecodeError, RecursionError):
            if attempt + 1 == READ_ATTEMPTS:
                raise AccountRefusal("%s cannot be parsed" % what) from None
            sleep(READ_RETRY_S)
        except OSError:
            raise AccountRefusal("%s cannot be read" % what) from None
    if not isinstance(value, dict):
        raise AccountRefusal("%s is not an object" % what)
    return value


def _text(value):
    return value if isinstance(value, str) and value else None


def codex_credentials_store(profile):
    """The credential store a Codex profile's ``config.toml`` names (None when it names none: Codex's default, the
    ``auth.json`` file); raises ``AccountRefusal`` when that file cannot be read."""
    path = Path(profile) / "config.toml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise AccountRefusal("config.toml cannot be read") from None
    try:
        import tomllib
    except ImportError:   # A Python without tomllib (before 3.11): only a file that never names the key passes.
        return "unknown" if "credentials_store" in text else None
    try:
        value = tomllib.loads(text).get(CODEX_STORE_KEY)
    except ValueError:
        raise AccountRefusal("config.toml cannot be parsed") from None
    return value if value is None else str(value)


def codex_account(profile, expected=None):
    """``summary`` of the Codex profile's account; raises ``AccountRefusal`` unless it is ``expected``
    (``CODEX_ACCOUNT`` by default)."""
    expected = CODEX_ACCOUNT if expected is None else expected
    store = codex_credentials_store(profile)
    if store not in (None, "file"):
        raise AccountRefusal("its config.toml keeps the sign-in in the %r credential store, not the auth.json this "
                             "check reads (sign in with -c %s='\"file\"')" % (store, CODEX_STORE_KEY))
    sign_in = _read_json(Path(profile) / CODEX_SIGN_IN, CODEX_SIGN_IN)
    if sign_in.get("OPENAI_API_KEY"):
        raise AccountRefusal("auth.json holds an API key, which Codex would bill instead of the ChatGPT sign-in")
    tokens = sign_in.get("tokens")
    if not isinstance(tokens, dict) or not tokens.get("id_token"):
        raise AccountRefusal("auth.json holds no ChatGPT sign-in (no tokens.id_token)")
    claims = jwt_claims(tokens["id_token"])
    auth = claims.get(AUTH_CLAIMS) if isinstance(claims.get(AUTH_CLAIMS), dict) else {}
    profile_claims = claims.get(PROFILE_CLAIMS) if isinstance(claims.get(PROFILE_CLAIMS), dict) else {}
    account_id = _text(auth.get("chatgpt_account_id"))
    email = _text(claims.get("email")) or _text(profile_claims.get("email"))
    stored = tokens.get("account_id")
    if stored is not None and stored != account_id:
        raise AccountRefusal("auth.json's account_id disagrees with its id_token (%s, stored %s)"
                             % (_shown(account_id, email), _shown(_text(stored)) if _text(stored) else "none"))
    if not account_id:   # An email can sign in to several ChatGPT accounts: never decided by it alone.
        raise AccountRefusal("its id_token names no ChatGPT account id (%s)" % _shown(None, email))
    if not account_id.startswith(expected["account_id_prefix"]):
        raise AccountRefusal("signed in to another ChatGPT account (%s; expected account %s)"
                             % (_shown(account_id, email), expected["account_id_prefix"]))
    return summary(account_id, email)


def claude_account(profile, expected):
    """``summary`` of the Claude Code profile's account; raises ``AccountRefusal`` unless it is ``expected`` (an email
    or an account UUID, required)."""
    expected = str(expected or "").strip()
    if not expected:
        raise AccountRefusal("a Claude profile needs its expected account named (an email or account UUID: the "
                             "opt-in's |account= field or %s)" % CLAUDE_ACCOUNT_ENV)
    if "@" not in expected:
        try:
            expected_uuid = uuid.UUID(expected)
        except ValueError:
            raise AccountRefusal("the expected Claude account is neither an email nor an account UUID") from None
    config = _read_json(Path(profile) / CLAUDE_CONFIG, CLAUDE_CONFIG)
    _claude_credentials_inside(profile, config)
    account = config.get("oauthAccount")
    if not isinstance(account, dict):
        raise AccountRefusal("%s holds no signed-in account (oauthAccount)" % CLAUDE_CONFIG)
    email, account_uuid = _text(account.get("emailAddress")), _text(account.get("accountUuid"))
    if not (email or account_uuid):
        raise AccountRefusal("%s's oauthAccount names no account" % CLAUDE_CONFIG)
    if "@" in expected:
        matched = bool(email) and email.lower() == expected.lower()
    else:
        try:
            matched = bool(account_uuid) and uuid.UUID(account_uuid) == expected_uuid
        except ValueError:
            matched = False
    if not matched:
        raise AccountRefusal("signed in to another Claude account (%s)" % _shown(account_uuid, email))
    return summary(account_uuid, email)


def _claude_credentials_inside(profile, config):
    """Refuse a Claude profile that could bill a credential of its own instead of its signed-in account."""
    if config.get("primaryApiKey"):
        raise AccountRefusal("%s holds an API key (primaryApiKey), which Claude Code would bill" % CLAUDE_CONFIG)
    for name in CLAUDE_SETTINGS:
        if not (Path(profile) / name).exists():
            continue
        settings = _read_json(Path(profile) / name, name)
        env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
        held = sorted(key for key in env if credential(key))
        held += ["apiKeyHelper"] if settings.get("apiKeyHelper") else []
        if held:
            raise AccountRefusal("its %s sets a credential of its own (%s)" % (name, ", ".join(held)))


def _store_files(store):
    """``connections.json`` and the copies the app reads back beside it (``session_kept``: ``connections.json.*``)."""
    main = Path(store) / CONNECTIONS
    try:
        kept = sorted(path for path in Path(store).glob(CONNECTIONS + ".*") if path.is_file())
    except OSError:
        raise AccountRefusal("the connection store cannot be listed") from None
    return ([main] if main.exists() else []) + kept


def store_accounts(store, claude_expected=None):
    """``{"connections": [summary, ...]}`` of every subscription connection in the connection store ``store``; raises
    ``AccountRefusal`` for one that reuses the person's own sign-in or is not signed in to its expected account."""
    store = Path(store)
    held = [name for name in SIGN_IN_FILES if (store / name).exists()]
    if held:
        raise AccountRefusal("the connection store holds a sign-in of its own (%s)" % ", ".join(held))
    found = []
    for path in _store_files(store):
        try:
            connections = _read_json(path, path.name).get("connections", [])
        except AccountRefusal:
            if path.name == CONNECTIONS:
                raise
            continue   # The app reads back only a copy that parses (kept_state.kept_values).
        if not isinstance(connections, list):
            raise AccountRefusal("%s's connections are not a list" % path.name)
        for record in connections:
            if not isinstance(record, dict):
                raise AccountRefusal("%s holds a connection that is not an object" % path.name)
            mode = str(record.get("mode", ""))
            if not mode.endswith("_subscription"):
                continue   # API key, team or media: billed to its own key, never a personal plan.
            family = next((name for prefix, name in FAMILIES.items() if mode.startswith(prefix)), None)
            if family is None:
                raise AccountRefusal("%s holds a subscription this check cannot read (%s)" % (path.name, mode))
            if record.get("reuse_native"):
                raise AccountRefusal("%s holds a connection that reuses the person's own sign-in" % path.name)
            try:   # The app's own spelling of the id (connections._parsed): a case-sensitive file system.
                identity = str(uuid.UUID(str(record.get("id"))))
            except ValueError:
                continue   # The app drops a connection whose id is no UUID.
            profile = _text(record.get("native_profile")) or str(store / family / identity)
            if not os.path.isdir(profile):
                raise AccountRefusal("%s's %s connection has no profile folder to check" % (path.name, mode))
            try:
                found.append(verify(family, profile, claude_expected))
            except AccountRefusal as error:
                raise AccountRefusal("its %s connection %s" % (mode, error)) from None
    return {"connections": found}


def verify(family, profile, expected=None):
    """``summary`` of the account ``profile`` (``family``: ``codex-profile``, ``claude-profile``, or None for a
    connection store) reaches; raises ``AccountRefusal`` unless it is the expected one. ``expected`` names the expected
    Claude account; a Codex profile's is always the evaluation account (``CODEX_ACCOUNT``)."""
    if family == "codex-profile":
        return codex_account(profile)
    if family == "claude-profile":
        return claude_account(profile, expected)
    return store_accounts(profile, expected)

