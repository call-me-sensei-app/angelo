# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""External AI apps connected over MCP: who they are and their keys.

Pure standard library and no ``bpy``: the listener's connection threads read a
published snapshot of this registry, and the main thread changes it only on a
person's click (Preferences > AI Providers > External AI apps).

``<state>/mcp/clients.json`` (owner-only, atomic replace) records each app
by its client name (a CLI label: ``claude-code``, ``codex``...), the app kind
and when it was connected. Each app's 32-byte key lives in its own owner-only
file, ``<state>/mcp/keys/<name>``; the app's own MCP configuration carries only
``--client <name>``, so a key never lands in a configuration file that an editor
may sync or a dotfile repository may hold. The stdio launcher reads the key
(same user) and proves it in the handshake (``external_cli_protocol.app_proof``).
Disconnecting deletes the key; the app's configuration entry then gets "this app
was disconnected in Angelo".

A connected app talks to Angelo's agent and never calls a tool (``external_agent``). 2.0.0's per-app levels are gone;
a record that still carries them reads the same.

The threat model is the CLI's: same user, local machine. A same-user process
can read the key, so it buys revocation, attribution and no accidental use;
approvals in Angelo stay the boundary (doc/angelo/external_mcp.rst).
"""

import json
import os
import secrets
import time
import uuid
from pathlib import Path

from . import owned_paths, shared_replace
from .external_cli_protocol import read_key, valid_label
from .team_org_policy import read_regular
from .tool_specs import TOOL_MAP
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)

APPROVAL_FLOORS = ("AUTO_BOUNDED", "ASK", "READ_ONLY")   # chat_settings' approval modes.
# The apps Angelo knows how to set up (external_setup). Any other MCP client connects as "other".
APPS = ("claude_code", "codex", "claude_desktop", "cursor", "vscode", "other")
APP_NAMES = {"claude_code": "Claude Code", "codex": "Codex", "claude_desktop": "Claude Desktop",
             "cursor": "Cursor", "vscode": "VS Code", "other": "MCP app"}
DEFAULT_CLIENT = {"claude_code": "claude-code", "codex": "codex", "claude_desktop": "claude-desktop",
                  "cursor": "cursor", "vscode": "vscode", "other": "mcp-app"}

# Families Angelo's agent never reaches in an app's External conversation (``RuntimeContext.begin`` narrows its
# allowed tools): workspace files, task-memory updates, Media Studio, the team library, Generation Flows, parallel
# workers, delegation and the person's workspace mode.
NEVER_FOR_APPS = frozenset(name for name in TOOL_MAP if name.startswith(("angelo_studio_", "angelo_team_",
                                                                         "angelo_flow_", "angelo_workers_"))
                           or name in {"angelo_write_file", "angelo_delete_file", "angelo_read_file", "angelo_list_files",
                                       "angelo_context_folder_write",   # The person's folder: never an app's to write.
                                       "angelo_memory_update", "angelo_delegate", "angelo_set_workspace_mode",
                                       "angelo_read_reference",
                                       # Turning on the engine link lets an engine connect: the person's call only.
                                       "angelo_app_feature",
                                       # Plan mode's card and Go are the person's own chat's (plan_mode.rst; an
                                       # External conversation's START is refused too: planning_controls).
                                       "angelo_plan"})
# angelo_goal is offered (2.0.1, maintainer: "same benefit, no handicaps"): a goal held from the app's words follows the
# person's-words rule (goal_mode.rst); its Start under Ask, Resume and the budget stay the person's presses.


def _write_new(temporary, encoding, write, target):
    """Fill the new owner-only file ``temporary`` with ``write(handle)``, then replace ``target`` with it (readers
    share delete, ``shared_replace``); on any failure the temporary file is removed and the error raised."""
    owned_paths.require_writable(target), owned_paths.require_writable(temporary)   # C first, for both names.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, target)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


class AppRegistry:
    """``<state>/mcp/clients.json`` plus one key file per app (module docstring)."""

    def __init__(self, state_directory):
        self.root = Path(state_directory) / "mcp"
        self.path = self.root / "clients.json"
        self.keys = self.root / "keys"

    def load(self):
        """name -> record, skipping anything malformed (a damaged file never grants more)."""
        try:
            raw = json.loads(read_regular(self.path))   # Never waits on a FIFO there (break-it review FR-L2).
        except (OSError, ValueError):
            return {}
        apps = raw.get("apps") if isinstance(raw, dict) else None
        output = {}
        for name, record in (apps or {}).items() if isinstance(apps, dict) else ():
            if not valid_label(name) or not isinstance(record, dict) or record.get("app") not in APPS:
                continue
            stamp = record.get("connected_at")
            output[name] = {"app": record["app"], "connected_at": stamp if isinstance(stamp, (int, float)) else None}
            if record.get("approval") in APPROVAL_FLOORS:   # The person's mode a new conversation never loosens.
                output[name]["approval"] = record["approval"]
            if record.get("goal_ended") is True:   # The person ended a goal of the app's: the next is only proposed.
                output[name]["goal_ended"] = True
        return output

    def _save(self, apps):
        owned_paths.require_writable(self.root).mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        temporary = self.path.with_name("clients." + uuid.uuid4().hex + ".tmp")
        _write_new(temporary, "utf-8", lambda handle: json.dump(
            {"version": 1, "apps": {name: _saved(record) for name, record in apps.items()}}, handle), self.path)

    def connect(self, app, name=None):
        """Record an app and write a fresh key; returns its client name. A reconnect replaces the key."""
        if app not in APPS:
            raise ValueError("Unknown app")
        name = name or DEFAULT_CLIENT[app]
        if not valid_label(name):
            raise ValueError("Use a client name of 1-32 characters from [A-Za-z0-9._-]")
        owned_paths.require_writable(self.keys).mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.keys, 0o700)
        key = self.keys / name
        temporary = key.with_name("." + name + "." + uuid.uuid4().hex + ".tmp")
        _write_new(temporary, "ascii", lambda handle: handle.write(secrets.token_hex(32)), key)
        apps = self.load()
        apps[name] = {"app": app, "connected_at": round(time.time())}
        self._save(apps)
        return name

    def record_end(self, name, mode, goal_ended=False):
        """What the app's conversation that ends (a move, a new chat, the person's delete) leaves for its next one:
        ``mode``, its approval, as the floor the next starts from (``ExternalHost.create_conversation``), and
        ``goal_ended`` when the person stopped its goal or its budget ran out, so the app's next goal is only
        proposed (``goal_controls``) until the person starts one. Neither is a grant: both only narrow."""
        apps = self.load()
        if name not in apps:
            return
        record = dict(apps[name])
        if mode in APPROVAL_FLOORS:
            record["approval"] = mode
        if goal_ended:
            record["goal_ended"] = True
        if record != apps[name]:
            apps[name] = record
            self._save(apps)

    def goal_started(self, name):
        """The person's Start on a goal in the app's conversation: its next goals follow the chat's own rule again."""
        apps = self.load()
        if apps.get(name, {}).pop("goal_ended", None):
            self._save(apps)

    def disconnect(self, name):
        """Forget the app and delete its key; True when it was connected."""
        apps = self.load()
        known = apps.pop(name, None) is not None
        try:
            owned_paths.require_writable(self.keys)
            (self.keys / name).unlink()
        except FileNotFoundError:
            pass
        if known:
            self._save(apps)
        return known

    def key(self, name):
        """The app's 32-byte key, or None (missing, unsafe or malformed: never a weaker check)."""
        return read_key(self.keys / name) if valid_label(name) else None

    def snapshot(self):
        """name -> key for the listener; immutable, read off the main thread."""
        output = {}
        for name in self.load():
            key = self.key(name)
            if key is not None:
                output[name] = key
        return output


def _saved(record):
    saved = {"app": record["app"], "connected_at": record.get("connected_at")}
    if record.get("approval") in APPROVAL_FLOORS:
        saved["approval"] = record["approval"]
    if record.get("goal_ended") is True:
        saved["goal_ended"] = True
    return saved


_grant_guard.entry_methods(AppRegistry, "connect")
_grant_guard.entry_methods(AppRegistry, "disconnect")   # Never a script's: ACCOUNT_OPERATORS.
