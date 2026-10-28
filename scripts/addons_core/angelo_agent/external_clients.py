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
from .team_org_policy import STRICTEST, read_regular
from .team_view import org_policy
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
    "angelo_job_cancel", "angelo_web_check",
})
# Typed, bounded scene edits (each one Undo step), background jobs (renders, bakes, exports; always a card) and the
# web export (a .glb in the workspace, never over a file; the loopback preview).
# Generation Flows are Media Studio work (paid generation), not scene edits; a merge of parallel workers' parts is
# the lead chat's own (worker_merge), never a connected app's.
EDIT_TOOLS = frozenset(BOUNDED_MUTATIONS - {"angelo_flow_build", "angelo_flow_choose", "angelo_workers_start",
                                            "angelo_workers_merge"}) | {"angelo_job_start", "angelo_web_export",
                                                                        "angelo_web_preview"}
PYTHON_TOOLS = frozenset({"angelo_execute_python"})
# A connected app's script file (external_work, script_files): offered with the python level, never an Angelo tool.
SCRIPT_FILE_TOOL = "angelo_run_script_file"
LEVEL_TOOLS = {READ: READ_TOOLS, EDIT: EDIT_TOOLS, PYTHON: PYTHON_TOOLS, TASKS: frozenset()}

# A tool annotation change must not silently widen what reading allows.
if any(not TOOL_MAP[name]["annotations"]["readOnlyHint"] for name in READ_TOOLS):
    raise RuntimeError("The external read level may contain only read-only tools")
if not (READ_TOOLS | EDIT_TOOLS | PYTHON_TOOLS) <= set(TOOL_MAP):
    raise RuntimeError("An external level names a tool Angelo does not have")


# Families an app never reaches, also in the tasks it starts (Angelo's agent in its External conversation):
# workspace files, task-memory updates, Media Studio and the team library, Generation Flows, delegation and the
# person's workspace mode.
NEVER_FOR_APPS = frozenset(name for name in TOOL_MAP if name.startswith(("angelo_studio_", "angelo_team_", "angelo_flow_",
                                                                         "angelo_workers_"))
                           or name in {"angelo_write_file", "angelo_delete_file", "angelo_read_file", "angelo_list_files",
                                       "angelo_memory_update", "angelo_delegate", "angelo_set_workspace_mode",
                                       "angelo_read_reference"})


# Headless (no Angelo window, doc/angelo/external_mcp.rst "Headless"): nothing may wait for a person there, so only
# reads and the Auto lane's bounded typed edits run. Background jobs and the web export write files, the web preview
# serves the scene and opens a browser; Python and accepting a preview need a person's review (an app's preview
# acceptance always opens the human review in the window).
WINDOW_ONLY_EDITS = frozenset({"angelo_job_start", "angelo_web_export", "angelo_web_preview"})
HEADLESS_TOOLS = frozenset((READ_TOOLS | EDIT_TOOLS) - WINDOW_ONLY_EDITS - {"angelo_job_cancel", "angelo_accept_preview"})
if not EDIT_TOOLS - WINDOW_ONLY_EDITS <= BOUNDED_MUTATIONS:
    raise RuntimeError("Headless edits may contain only the Auto lane's bounded typed edits")


def tools_for(levels, headless=False):
    """The Angelo tools an app with these levels may call (task tools are not Angelo tools)."""
    allowed = set()
    for level in levels:
        allowed |= LEVEL_TOOLS.get(level, frozenset())
    return frozenset(allowed & HEADLESS_TOOLS if headless else allowed)


def clean_levels(levels):
    """Known levels in canonical order; reading is always included."""
    chosen = set(levels or ()) | {READ}
    return tuple(level for level in LEVELS if level in chosen)


ACCESS_FILE = "access.json"   # <state>/mcp/access.json: the window's "Allow apps on this computer" switch
# <state>/mcp/organization.json: the signed-in organization's app policy as the window applies it, or "none".
ORGANIZATION_FILE = "organization.json"


def _write_record(state_directory, name, record):
    """One owner-only record the window keeps for the launcher, replaced atomically."""
    root = Path(state_directory) / "mcp"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = root / (".%s.%s.tmp" % (name, uuid.uuid4().hex))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(temporary, root / name)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _read_record(state_directory, name):
    """A record the window wrote, or None when it is missing or can't be read: only a regular file of at most
    ``team_org_policy.MAX_FILE`` bytes, never through a link at its name or by waiting on a FIFO (break-it M2)."""
    try:
        saved = json.loads(read_regular(Path(state_directory) / "mcp" / name))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and saved.get("version") == 1 else None


def write_access(state_directory, allowed):
    """Record the person's "Allow apps on this computer to reach Angelo" switch where the launcher reads it
    (owner-only, atomic). Angelo's window writes it whenever the switch differs from what is recorded."""
    _write_record(state_directory, ACCESS_FILE, {"version": 1, "allow_local_cli": bool(allowed)})


def read_access(state_directory):
    """The recorded switch: True or False; None when it was never recorded or can't be read (callers fail closed)."""
    value = (_read_record(state_directory, ACCESS_FILE) or {}).get("allow_local_cli")
    return value if isinstance(value, bool) else None


def write_organization(state_directory, policy):
    """Record the organization's app policy Angelo's window applies now (``team_view.org_policy``), or None for no
    organization (signed out, or no team sign-in in this build), where the launcher reads it (owner-only, atomic).
    The window writes it whenever it differs from what is recorded (``ExternalHost.record_organization``)."""
    _write_record(state_directory, ORGANIZATION_FILE, {"version": 1, "organization": policy})


def read_organization(state_directory):
    """The organization's app policy for a process without the window, as the window last recorded it.

    None only when the window recorded no organization. A record that is missing (never written, a write that
    failed, another state folder, deleted), unreadable, malformed, oversized, a link or not a regular file counts
    as the strictest policy, like an access switch never recorded counts as off (break-it review S1)."""
    saved = _read_record(state_directory, ORGANIZATION_FILE)
    if saved is not None and "organization" in saved:
        if saved["organization"] is None:
            return None
        policy = org_policy(saved["organization"])
        if policy is not None:
            return policy
    return org_policy(STRICTEST)


def organization_recorded(state_directory):
    """Whether the window has recorded this computer's organization policy (none or a policy) in a form
    ``read_organization`` uses; False is what it reads as the strictest policy for want of a record."""
    saved = _read_record(state_directory, ORGANIZATION_FILE)
    return saved is not None and "organization" in saved and (
        saved["organization"] is None or org_policy(saved["organization"]) is not None)


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
        new = name not in apps
        apps[name] = {"app": app, "levels": clean_levels(levels), "connected_at": round(time.time())}
        self._save(apps)
        if new:   # A new connection starts with none, also after a Disconnect whose removal failed (FR-L1).
            self._forget_script_files(name)
        return name

    def set_levels(self, name, levels):
        apps = self.load()
        if name not in apps:
            raise KeyError(name)
        apps[name]["levels"] = clean_levels(levels)
        self._save(apps)
        if PYTHON not in apps[name]["levels"]:   # Script files it may no longer run are forgotten, not kept.
            self._forget_script_files(name)

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
        self._forget_script_files(name)   # A reconnected app starts with no allowed script files.
        return known

    def _forget_script_files(self, name):
        """The app's script file approvals (``script_files``) end with its python level or its connection. The
        check also reads the app's levels at every run, so a write that fails here still never runs one."""
        from . import script_files
        try:
            script_files.forget_at(self.root.parent, name)
        except OSError as error:
            print("Angelo: the app's script file approvals could not be removed from disk:", error)

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
