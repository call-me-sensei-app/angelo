# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an administrator's machine-wide Claude Code policy says about hooks, read before a team launch.

Contract: doc/angelo/ai_providers.rst, "Team connections reach only the gateway".
Claude Code applies its managed (policy-tier) settings whatever ``--setting-sources`` says. A policy with
``disableAllHooks`` or ``allowManagedHooksOnly`` stops the WebFetch hook (``web_fetch_hook``); a team session
then keeps Claude Code's own preflight instead of skipping it (``team_network.settings``), so WebFetch fails
closed rather than fetching unchecked. Sources, as Claude Code 2.1.292-2.1.316 reads them: the
``managed-settings.json`` file (and ``managed-settings.d/*.json``) in its system folder, the macOS managed
preferences domain ``com.anthropic.claudecode`` and the Windows ``Policies\\ClaudeCode`` registry keys
(``Settings``, a JSON string). An unreadable source is skipped.

The policy's ``env`` block also wins over a child's environment: a proxy it names replaces the fence, and the
agent's traffic follows that proxy (the administrator's choice). ``env_overrides`` names those variables for the AI
Providers page (``providers_page_model``), so IT can see why.

Standard library only; no bpy.
"""

import json
import os
import plistlib
import sys
from pathlib import Path

DOMAIN = "com.anthropic.claudecode"
FOLDERS = {"darwin": "/Library/Application Support/ClaudeCode", "win32": r"C:\Program Files\ClaudeCode"}
LINUX_FOLDER = "/etc/claude-code"
REGISTRY_KEY = r"SOFTWARE\Policies\ClaudeCode"   # i18n: registry key path
HOOK_SWITCHES = ("disableAllHooks", "allowManagedHooksOnly")
# Variables a policy's ``env`` block can set that send Claude Code's traffic elsewhere: a proxy of its own (which
# replaces the team fence, team_network) or a telemetry exporter.
ROUTE_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "GRPC_PROXY", "CLAUDE_CODE_ENABLE_TELEMETRY")
ROUTE_PREFIXES = ("OTEL_",)
MAX_FILE = 1024 * 1024


def _json(path):
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _files(platform):
    folder = Path(FOLDERS.get(platform, LINUX_FOLDER))
    found = [_json(folder / "managed-settings.json")]
    try:
        found += [_json(path) for path in sorted((folder / "managed-settings.d").glob("*.json"))]
    except OSError:
        pass
    return found


def _mac_preferences():
    found = []
    for path in (Path("/Library/Managed Preferences") / (DOMAIN + ".plist"),
                 Path("/Library/Managed Preferences") / os.environ.get("USER", "") / (DOMAIN + ".plist")):
        try:
            with path.open("rb") as handle:
                value = plistlib.load(handle)
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        found.append(value if isinstance(value, dict) else None)
    return found


def _windows_registry():
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, REGISTRY_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, "Settings")
            found.append(json.loads(value) if isinstance(value, str) else None)
        except (OSError, ValueError):
            continue
    return found


def sources(platform=None):
    """Every readable managed settings object on this machine (dicts only)."""
    platform = platform or sys.platform
    found = _files(platform)
    if platform == "darwin":
        found += _mac_preferences()
    elif platform == "win32":
        found += _windows_registry()
    return [value for value in found if isinstance(value, dict)]


def hooks_blocked(found=None):
    """Whether a managed policy stops Angelo's hooks from running (``HOOK_SWITCHES`` set to true)."""
    found = sources() if found is None else found
    return any(value.get(name) is True for value in found for name in HOOK_SWITCHES)


def env_overrides(found=None):
    """The routing variables a managed policy's ``env`` block sets (``ROUTE_VARIABLES``), upper case and sorted."""
    found = sources() if found is None else found
    names = set()
    for value in found:
        env = value.get("env")
        for name in env if isinstance(env, dict) else ():
            upper = str(name).upper()
            if upper in ROUTE_VARIABLES or upper.startswith(ROUTE_PREFIXES):
                names.add(upper)
    return sorted(names)
