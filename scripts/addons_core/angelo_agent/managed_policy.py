# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The machine-wide policy an IT department sets for Angelo, read once at startup.

Contract and the administrator's guide: doc/angelo/enterprise_policy.rst.

Sources, highest precedence first (a key set by an earlier source wins):

* macOS: managed preferences for the domain ``studio.hyperbond.angelo``,
  delivered by an MDM configuration profile. The device-level file
  ``/Library/Managed Preferences/studio.hyperbond.angelo.plist`` wins over
  the user-level ``/Library/Managed Preferences/<user>/…``. Both are written
  by the system (root-owned); a person can't change them.
* Windows: Group Policy registry values under
  ``HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Hyperbond\\Angelo``, then the same key
  under ``HKEY_CURRENT_USER``.
* Linux: ``/etc/angelo/policy.json``.
* ``ANGELO_POLICY_FILE`` (a JSON file), lowest: for testing a policy without
  MDM. It can't override any key a managed source sets.

Every key is optional; an unset key keeps Angelo's default. A value of the
wrong type or form is never ignored silently: it is reported in
``Policy.problems`` and applied in the restrictive direction (an ``Allow…``
switch reads as off, ``RequireTeamSignIn`` as on, an invalid URL or
organization stops team sign-in), so a typo can't widen access.

Standard library only; no bpy. ``current()`` caches the policy for the
session; nothing a person does in the app changes it.
"""

import json
import os
import plistlib
import re
import sys
import uuid
from dataclasses import dataclass, replace

from . import product_env
from .display_text import Text
from .team_view import UNSAFE_TEXT
from .ui_text import n_

DOMAIN = "studio.hyperbond.angelo"  # The bundle identifier (doc/angelo/app_identity.rst).
MAC_DIRECTORY = "/Library/Managed Preferences"  # i18n: filesystem path
WINDOWS_KEY = r"SOFTWARE\Policies\Hyperbond\Angelo"  # i18n: registry key path
LINUX_FILE = "/etc/angelo/policy.json"
TEST_FILE_VARIABLE = "POLICY_FILE"  # ANGELO_POLICY_FILE
MAX_FILE = 64 * 1024

# Policy key -> (Policy field, kind).
KEYS = {
    "ConsoleURL": ("console_url", "url"),
    "GatewayURL": ("gateway_url", "url"),
    "RequireTeamSignIn": ("require_team_sign_in", "require"),
    "Organization": ("organization", "organization"),
    "EmailDomains": ("email_domains", "domains"),
    "AllowPersonalConnections": ("allow_personal_connections", "allow"),
    "AllowLocalCLIAccess": ("allow_local_cli_access", "allow"),
    "AllowVoice": ("allow_voice", "allow"),
    "AllowAutoApproval": ("allow_auto_approval", "allow"),
    "RequireAskForEdits": ("require_ask_for_edits", "require"),
    "AllowCollaborators": ("allow_collaborators", "allow"),
    "AdminContact": ("admin_contact", "text"),
    "ExtraCACertificates": ("extra_ca_certificates", "file"),
    "DisableUpdateCheck": ("disable_update_check", "disable"),
    "DisableCrashReporting": ("disable_crash_reporting", "disable"),
    "UpdateFeedURL": ("update_feed_url", "feed"),
}
INVALID = "invalid"  # console_url / gateway_url / organization when the managed value can't be used.
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}
_DOMAIN = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


@dataclass(frozen=True)
class Policy:
    """The effective policy. Defaults are Angelo's unmanaged behavior."""

    console_url: str = ""
    gateway_url: str = ""
    require_team_sign_in: bool = False
    organization: str = ""
    email_domains: tuple = ()
    allow_personal_connections: bool = True
    allow_local_cli_access: bool = True
    allow_voice: bool = True
    allow_collaborators: bool = True
    allow_auto_approval: bool = True      # False: no script runs unreviewed (chat trust, Always allow scripts).
    require_ask_for_edits: bool = False   # True: every chat uses Ask before changes; Auto bounded edits is off.
    admin_contact: str = ""
    extra_ca_certificates: str = ""   # A PEM file of extra trusted roots (an organization's TLS inspection).
    disable_update_check: bool = False     # No update feed is fetched (updates_and_reports.rst).
    disable_crash_reporting: bool = False  # No crash report is offered or sent; the crash file stays local.
    update_feed_url: str = ""   # An https mirror of the signed update feed; INVALID stops the check.
    keys: frozenset = frozenset()   # Keys a source set.
    sources: tuple = ()             # Where they came from, for the About text.
    problems: tuple = ()            # Keys whose values were unusable and applied restrictively.
    organization_keys: frozenset = frozenset()  # The organization's app-policy switches that restrict (off, or required).

    @property
    def managed(self):
        return bool(self.keys or self.organization_keys)

    @property
    def team_only(self):
        """Only team connections may be used: personal ones are off, or a team sign-in is required first."""
        return not self.allow_personal_connections or self.require_team_sign_in

    def locked(self, key):
        return key in self.keys


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return {0: False, 1: True}.get(value)
    if isinstance(value, str):
        text = value.strip().lower()
        return True if text in _TRUE else False if text in _FALSE else None
    return None


def _url(value):
    from .team_endpoints import parse_origin
    try:
        origin = parse_origin(value, "policy")
    except ValueError:
        return None
    return origin.url if origin.scheme == "https" else None  # Managed origins are always https.


def _feed_url(value):
    """An https link with a host and path, no credentials, query or spaces (the feed itself stays signed)."""
    from urllib.parse import urlsplit
    text = value.strip() if isinstance(value, str) else ""
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password or port not in {None, 443}
            or parts.query or parts.fragment or len(text) > 2048 or any(ord(c) < 33 for c in text)):
        return None
    return text


def _organization(value):
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError):
        return None


def _domains(value):
    items = value.replace(",", " ").split() if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)) or not items:
        return None
    domains = tuple(dict.fromkeys(str(item).strip().lower().lstrip("@") for item in items))
    return domains if all(_DOMAIN.fullmatch(d) for d in domains) else None


def _file(value):
    path = value.strip() if isinstance(value, str) else ""
    return path if path and os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.R_OK) else None


def _text(value):
    text = value.strip() if isinstance(value, str) else ""
    return text if 0 < len(text) <= 200 and not UNSAFE_TEXT.search(text) else None


def parse(entries):
    """``(fields, problems)`` from one source's ``{key: value}``; unknown keys are ignored."""
    fields, problems = {}, []
    for key, value in entries.items():
        if key not in KEYS:
            continue
        name, kind = KEYS[key]
        if kind in {"allow", "require", "disable"}:
            parsed = _boolean(value)
            if parsed is None:
                parsed = kind != "allow"  # Restrictive: a typo can't switch a lock off.
                problems.append(key)
        elif kind == "url":
            parsed = _url(value)
            if parsed is None:
                parsed, problems = INVALID, problems + [key]
        elif kind == "feed":
            parsed = _feed_url(value)
            if parsed is None:   # Restrictive: no check at all rather than a silent fall back to the public feed.
                parsed, problems = INVALID, problems + [key]
        elif kind == "organization":
            parsed = _organization(value)
            if parsed is None:
                parsed, problems = INVALID, problems + [key]
        elif kind == "file":
            parsed = _file(value)
            if parsed is None:  # Unusable roots: nothing is added; the problem is shown to the person.
                problems.append(key)
                continue
        elif kind == "domains":
            parsed = _domains(value)
            if parsed is None:  # Unusable domains admit no email: sign-in is refused, never widened.
                parsed, problems = (INVALID,), problems + [key]
        else:
            parsed = _text(value)
            if parsed is None:
                problems.append(key)
                continue
        fields[name] = parsed
    return fields, problems


def combine(sources):
    """The effective policy from ``[(label, {key: value}), …]``, highest precedence first."""
    fields, keys, labels, problems = {}, set(), [], []
    for label, entries in sources:
        entries = {k: v for k, v in entries.items() if k in KEYS and k not in keys}
        if not entries:
            continue
        parsed, invalid = parse(entries)
        labels.append(label)
        problems += invalid
        keys.update(entries)
        fields.update(parsed)
    return replace(Policy(), **fields, keys=frozenset(keys), sources=tuple(labels), problems=tuple(problems))


def _read_plist(path):
    """A plist file's top-level dictionary; None when it is missing, oversized or not a dictionary."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_FILE + 1)
        value = plistlib.loads(data) if len(data) <= MAX_FILE else None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, dict) else None


def _read_json(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_FILE + 1)
        value = json.loads(data) if len(data) <= MAX_FILE else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _user_name():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, OSError):
        return ""


def mac_sources(read=_read_plist, user=None):
    user = _user_name() if user is None else user
    paths = [os.path.join(MAC_DIRECTORY, DOMAIN + ".plist")]
    if user and "/" not in user and user not in {".", ".."}:
        paths.append(os.path.join(MAC_DIRECTORY, user, DOMAIN + ".plist"))
    return [(path, value) for path in paths for value in [read(path)] if value is not None]


def windows_sources(registry=None):
    """HKLM then HKCU policy values; ``registry`` is ``winreg`` (a fake in tests)."""
    if registry is None:
        import winreg as registry
    output = []
    for hive, label in ((registry.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE"),
                        (registry.HKEY_CURRENT_USER, "HKEY_CURRENT_USER")):
        try:
            key = registry.OpenKey(hive, WINDOWS_KEY, 0, registry.KEY_READ | getattr(registry, "KEY_WOW64_64KEY", 0))
        except OSError:
            continue  # No policy at this level.
        values = {}
        try:
            index = 0
            while index < 256:
                try:
                    name, value, kind = registry.EnumValue(key, index)
                except OSError:
                    break
                if kind == registry.REG_EXPAND_SZ and isinstance(value, str):
                    # Group Policy's "expandable string" (e.g. %ProgramData%\roots.pem): Windows expands it.
                    value = getattr(registry, "ExpandEnvironmentStrings", os.path.expandvars)(value)
                if kind in {registry.REG_DWORD, registry.REG_SZ, registry.REG_EXPAND_SZ, registry.REG_MULTI_SZ}:
                    values[name] = value
                index += 1
        finally:
            registry.CloseKey(key)
        if values:
            output.append(("%s\\%s" % (label, WINDOWS_KEY), values))
    return output


def load(platform=None, environ=None, *, mac=mac_sources, windows=windows_sources, read_json=_read_json):
    """The effective policy of this computer and user (``platform`` and readers are injectable)."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        sources = mac()
    elif platform.startswith("win"):
        try:
            sources = windows()
        except (ImportError, OSError):
            sources = []
    else:
        value = read_json(LINUX_FILE)
        sources = [(LINUX_FILE, value)] if value is not None else []
    test_file, _variable = product_env.lookup(TEST_FILE_VARIABLE, environ)
    value = read_json(test_file) if test_file else None
    if value is not None:
        sources.append((test_file, value))
    return combine(sources)


_current = None
_organization_source = None   # A callable returning the signed-in organization's app policy (team_view.org_policy).
ORG_FIELDS = {"allowPersonalConnections": "allow_personal_connections", "allowLocalCliAccess": "allow_local_cli_access",
              "allowAutoApproval": "allow_auto_approval", "allowVoice": "allow_voice", "allowCollaborators": "allow_collaborators"}
ORG_REQUIREMENTS = {"requireAskForEdits": "require_ask_for_edits"}  # On when either policy requires it.


def machine():
    """This computer's policy, read on first use (the organization's never changes it)."""
    global _current
    if _current is None:
        _current = load()
    return _current


def current():
    """The effective policy: the machine policy, made stricter by the signed-in organization's app policy."""
    organization = _organization_source() if _organization_source is not None else None
    return with_organization(machine(), organization) if organization else machine()


def with_organization(policy, organization):
    """The stricter of the two for every switch; the machine's admin contact first.

    Only the machine policy pins where to sign in (ConsoleURL, GatewayURL,
    Organization, EmailDomains, RequireTeamSignIn) and adds TLS roots: the
    organization's policy arrives only after a sign-in it could not have chosen.
    """
    stricter = {name: getattr(policy, name) and organization.get(key, True) is True for key, name in ORG_FIELDS.items()}
    stricter.update({name: getattr(policy, name) or organization.get(key) is True
                     for key, name in ORG_REQUIREMENTS.items()})
    restricted = frozenset(key for key, name in ORG_FIELDS.items() if not organization.get(key, True)) | frozenset(
        key for key in ORG_REQUIREMENTS if organization.get(key) is True)
    return replace(policy, **stricter, admin_contact=policy.admin_contact or organization.get("adminContact", ""),
                   organization_keys=restricted)


def set_organization_source(source):
    """``source()`` returns the signed-in organization's app policy or None (team_account_ui)."""
    global _organization_source
    _organization_source = source


def reset(policy=None):
    """Tests and add-on reload: forget (or replace) the cached machine policy and the organization's."""
    global _current, _organization_source
    _current, _organization_source = policy, None


# What the person sees. Templates (ui_text.n_) with named fields, translated where shown.
CONTACT_ADMIN = n_("Contact your admin for access.")
CONTACT_ADMIN_AT = n_("Contact your admin for access: {contact}")
PERSONAL_OFF = n_("Your organization allows only its Angelo team connections.")
SIGN_IN_REQUIRED = n_("Your organization requires signing in to its Angelo team first.")
WRONG_ORGANIZATION = n_("Your organization requires a different Angelo team. Sign in again and choose it.")
WRONG_EMAIL = n_("Your organization requires signing in with your work account ({domains}).")
INVALID_POLICY = n_("Your organization's Angelo policy has an invalid {keys} setting, so team sign-in is off. "
                    "Contact your admin.")
CLI_OFF = n_("Turned off by your organization.")
# The same sentence as CLI_OFF, where AllowAutoApproval locks Always allow scripts and where RequireAskForEdits
# makes Auto · bounded edits unavailable.
AUTO_APPROVAL_OFF = CLI_OFF
ASK_REQUIRED = CLI_OFF
VOICE_OFF = n_("Voice is turned off by your organization.")
COLLABORATORS_OFF = n_("Collaborators are turned off by your organization.")


def contact(policy):
    """Asks the person to contact their administrator (with the administrator's contact when set)."""
    return Text(CONTACT_ADMIN_AT, contact=policy.admin_contact) if policy.admin_contact else Text(CONTACT_ADMIN)


def sign_in_problem(policy):
    """Why the policy itself stops team sign-in (an unusable managed value), or None."""
    invalid = [key for key, (name, kind) in KEYS.items()
               if kind in {"url", "organization"} and getattr(policy, name) == INVALID]
    if INVALID in policy.email_domains:
        invalid.append("EmailDomains")
    return Text(INVALID_POLICY, keys=", ".join(invalid)) if invalid else None


def admit(policy, org_id, email):
    """Why a completed sign-in is for an organization or account the policy refuses, or None."""
    problem = sign_in_problem(policy)
    if problem:
        return problem
    if policy.organization and org_id != policy.organization:
        return Text(WRONG_ORGANIZATION)
    if policy.email_domains:
        domain = email.rpartition("@")[2].lower() if "@" in email else ""
        if domain not in policy.email_domains:
            return Text(WRONG_EMAIL, domains=", ".join("@" + d for d in policy.email_domains))
    return None


def personal_refusal(policy, team_signed_in):
    """Why a personal connection (a subscription or an API key) may not be used now, or None."""
    if not policy.allow_personal_connections:
        return Text(PERSONAL_OFF)
    if policy.require_team_sign_in and not team_signed_in:
        return Text(SIGN_IN_REQUIRED)
    return None
