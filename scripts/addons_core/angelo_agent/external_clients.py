# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""External AI apps connected over MCP: who they are, their keys and what each may do.

Pure standard library and no ``bpy``: the listener's connection threads read a
published snapshot of this registry, and the main thread changes it only on a
person's click (Preferences > AI Providers > External AI apps).

``<state>/mcp/clients.json`` (owner-only, atomic replace) records each app
by its client name (a CLI label: ``claude-code``, ``codex``...), the app kind,
the levels the person allowed and when it was connected. Each app's 32-byte
key lives in its own owner-only file, ``<state>/mcp/keys/<name>``; the app's
own MCP configuration carries only ``--client <name>``, so a key never lands in
a configuration file that an editor may sync or a dotfile repository may hold.
The stdio launcher reads the key (same user) and proves it in the handshake
(``external_cli_protocol.app_proof``). Disconnecting deletes the key; the
app's configuration entry then gets "this app was disconnected in Angelo".

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

from .external_cli_protocol import read_key, valid_label
from .tool_specs import BOUNDED_MUTATIONS, TOOL_MAP

# What the person can allow per app. Reading is always on: an app that may do nothing cannot be useful.
READ, EDIT, PYTHON, TASKS = "read", "edit", "python", "tasks"
LEVELS = (READ, EDIT, PYTHON, TASKS)
DEFAULT_LEVELS = (READ, EDIT, PYTHON, TASKS)

# The apps Angelo knows how to set up (external_setup). Any other MCP client connects as "other".
APPS = ("claude_code", "codex", "claude_desktop", "cursor", "vscode", "other")
APP_NAMES = {"claude_code": "Claude Code", "codex": "Codex", "claude_desktop": "Claude Desktop",
             "cursor": "Cursor", "vscode": "VS Code", "other": "MCP app"}
DEFAULT_CLIENT = {"claude_code": "claude-code", "codex": "codex", "claude_desktop": "claude-desktop",
                  "cursor": "cursor", "vscode": "vscode", "other": "mcp-app"}

READ_TOOLS = frozenset({
    "angelo_inspect_scene", "angelo_inspect_sculpt", "angelo_scene_search", "angelo_scene_object",
    "angelo_scene_relationships", "angelo_scene_delta", "angelo_scene_verify", "angelo_scene_settings",
    "angelo_read_data", "angelo_blend_file", "angelo_inspect_preview", "angelo_expand_recipe",
    "angelo_blender_api", "angelo_agent_capabilities", "angelo_agent_metrics", "angelo_memory_read",
    "angelo_script", "angelo_view", "angelo_sculpt_view", "angelo_sculpt_review", "angelo_sculpt_target",
    "angelo_job_cancel",
})
# Typed, bounded scene edits (each one Undo step) and background jobs (renders, bakes, exports; always a card).
# Generation Flows are Media Studio work (paid generation), not scene edits.
EDIT_TOOLS = frozenset(BOUNDED_MUTATIONS - {"angelo_flow_build", "angelo_flow_choose"}) | {"angelo_job_start"}
PYTHON_TOOLS = frozenset({"angelo_execute_python"})
LEVEL_TOOLS = {READ: READ_TOOLS, EDIT: EDIT_TOOLS, PYTHON: PYTHON_TOOLS, TASKS: frozenset()}

# A tool annotation change must not silently widen what reading allows.
if any(not TOOL_MAP[name]["annotations"]["readOnlyHint"] for name in READ_TOOLS):
    raise RuntimeError("The external read level may contain only read-only tools")
if not (READ_TOOLS | EDIT_TOOLS | PYTHON_TOOLS) <= set(TOOL_MAP):
    raise RuntimeError("An external level names a tool Angelo does not have")


# Families an app never reaches, also in the tasks it starts (Angelo's agent in its External conversation):
# workspace files, task-memory updates, Media Studio and the team library, Generation Flows, delegation and the
# person's workspace mode.
NEVER_FOR_APPS = frozenset(name for name in TOOL_MAP if name.startswith(("angelo_studio_", "angelo_team_", "angelo_flow_"))
                           or name in {"angelo_write_file", "angelo_delete_file", "angelo_read_file", "angelo_list_files",
                                       "angelo_memory_update", "angelo_delegate", "angelo_set_workspace_mode",
                                       "angelo_read_reference"})


def tools_for(levels):
    """The Angelo tools an app with these levels may call (task tools are not Angelo tools)."""
    allowed = set()
    for level in levels:
        allowed |= LEVEL_TOOLS.get(level, frozenset())
    return frozenset(allowed)


def clean_levels(levels):
    """Known levels in canonical order; reading is always included."""
    chosen = set(levels or ()) | {READ}
    return tuple(level for level in LEVELS if level in chosen)


class AppRegistry:
    """``<state>/mcp/clients.json`` plus one key file per app (module docstring)."""

    def __init__(self, state_directory):
        self.root = Path(state_directory) / "mcp"
        self.path = self.root / "clients.json"
        self.keys = self.root / "keys"

    def load(self):
        """name -> record, skipping anything malformed (a damaged file never grants more)."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        apps = raw.get("apps") if isinstance(raw, dict) else None
        output = {}
        for name, record in (apps or {}).items() if isinstance(apps, dict) else ():
            if not valid_label(name) or not isinstance(record, dict) or record.get("app") not in APPS:
                continue
            levels = record.get("levels")
            output[name] = {"app": record["app"], "levels": clean_levels(levels if isinstance(levels, list) else ()),
                            "connected_at": record.get("connected_at") if isinstance(record.get("connected_at"), (int, float)) else None}
        return output

    def _save(self, apps):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        temporary = self.path.with_name("clients." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "apps": {name: {"app": record["app"], "levels": list(record["levels"]),
                                                     "connected_at": record.get("connected_at")}
                                              for name, record in apps.items()}}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def connect(self, app, name=None, levels=DEFAULT_LEVELS):
        """Record an app and write a fresh key; returns its client name. A reconnect replaces the key."""
        if app not in APPS:
            raise ValueError("Unknown app")
        name = name or DEFAULT_CLIENT[app]
        if not valid_label(name):
            raise ValueError("Use a client name of 1-32 characters from [A-Za-z0-9._-]")
        self.keys.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.keys, 0o700)
        key = self.keys / name
        temporary = key.with_name("." + name + "." + uuid.uuid4().hex + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, key)
        apps = self.load()
        apps[name] = {"app": app, "levels": clean_levels(levels), "connected_at": round(time.time())}
        self._save(apps)
        return name

    def set_levels(self, name, levels):
        apps = self.load()
        if name not in apps:
            raise KeyError(name)
        apps[name]["levels"] = clean_levels(levels)
        self._save(apps)

    def disconnect(self, name):
        """Forget the app and delete its key; True when it was connected."""
        apps = self.load()
        known = apps.pop(name, None) is not None
        try:
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
        """name -> (levels, key) for the listener; immutable, read off the main thread."""
        output = {}
        for name, record in self.load().items():
            key = self.key(name)
            if key is not None:
                output[name] = (frozenset(record["levels"]), key)
        return output
