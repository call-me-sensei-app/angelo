# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adding Angelo to an AI app's MCP settings, on the person's click (Preferences > AI Providers).

Each supported app is found where it keeps its settings; Connect adds one
``angelo`` server entry that starts Angelo's stdio launcher with Angelo's
bundled Python (``external_mcp``), and Disconnect removes exactly that entry.
Nothing else in an app's settings is changed, an edited file is replaced
atomically with a backup of its previous content beside it, and a file Angelo
cannot read safely (comments, another format) is never rewritten: the person
gets the snippet to paste instead.

* Claude Code: its own CLI (``claude mcp add --scope user``), which owns its
  settings file while it runs.
* Codex: ``$CODEX_HOME/config.toml`` (``~/.codex``), table ``[mcp_servers.angelo]``
  with a tool timeout long enough for a person deciding on an approval card.
* Claude Desktop: ``claude_desktop_config.json`` ``mcpServers.angelo``.
* Cursor: ``~/.cursor/mcp.json`` ``mcpServers.angelo``.
* VS Code: the user ``mcp.json`` ``servers.angelo`` (``type: stdio``).

Angelo never connects on its own: these run only from a button whose label
says which app it adds Angelo to. Standard library only; no ``bpy``.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

from .external_clients import APP_NAMES, DEFAULT_CLIENT

SERVER = "angelo"
CODEX_TOOL_TIMEOUT = 900   # external_mcp.CALL_DEADLINE: a call may wait that long for the person.
CLI_TIMEOUT = 30.0


class SetupError(RuntimeError):
    """Why an app's settings were not changed; the person gets the snippet instead."""


def launcher_path():
    """The shipped launcher next to the add-on: ``<scripts>/angelo/angelo_mcp.py``."""
    return Path(__file__).resolve().parents[2] / "angelo" / "angelo_mcp.py"


def server_command(app, python, state_directory, name=None):
    """(command, args) an app runs: the bundled Python, isolated, the launcher, this app's name and Angelo's state."""
    return str(python), ["-I", str(launcher_path()), "--client", name or DEFAULT_CLIENT[app],
                         "--state-dir", str(state_directory)]


def snippet(app, python, state_directory, name=None):
    """The JSON to paste into any MCP app's settings (``mcpServers`` form)."""
    command, args = server_command(app, python, state_directory, name)
    return json.dumps({"mcpServers": {SERVER: {"command": command, "args": args}}}, indent=2)


def _home(home):
    return Path(home) if home is not None else Path.home()


def _app_support(home, platform):
    if platform == "darwin":
        return _home(home) / "Library" / "Application Support"
    if platform == "win32":
        return Path(os.environ.get("APPDATA") or _home(home) / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home(home) / ".config")


def settings_file(app, home=None, environ=None, platform=None):
    """Where the app keeps its MCP servers (None for Claude Code, whose own CLI edits them)."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if app == "codex":
        return Path(environ.get("CODEX_HOME") or _home(home) / ".codex") / "config.toml"
    if app == "claude_desktop":
        return _app_support(home, platform) / "Claude" / "claude_desktop_config.json"
    if app == "cursor":
        return _home(home) / ".cursor" / "mcp.json"
    if app == "vscode":
        return _app_support(home, platform) / "Code" / "User" / "mcp.json"
    return None


def installed(app, home=None, environ=None, platform=None, which=shutil.which):
    """Whether the app looks installed: its settings folder exists, or its CLI is on the path."""
    if app == "claude_code":
        return bool(which("claude")) or (_home(home) / ".claude").is_dir()
    if app == "other":
        return True
    path = settings_file(app, home, environ, platform)
    applications = {"claude_desktop": "Claude.app", "cursor": "Cursor.app", "vscode": "Visual Studio Code.app"}
    bundle = Path("/Applications") / applications[app] if app in applications else None
    return path.parent.is_dir() or bool(bundle and bundle.exists()) or (app == "codex" and bool(which("codex")))


def has_entry(app, home=None, environ=None, platform=None):
    """Whether the app's settings already name an ``angelo`` server (read-only)."""
    if app == "claude_code":
        path = Path((environ or os.environ).get("CLAUDE_CONFIG_DIR") or _home(home)) / ".claude.json"
        data = _read_json(path, missing={})
        return isinstance(data, dict) and SERVER in (data.get("mcpServers") or {})
    path = settings_file(app, home, environ, platform)
    if path is None:
        return False
    if app == "codex":
        try:
            return SERVER in (tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers") or {})
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            return False
    data = _read_json(path, missing={})
    key = "servers" if app == "vscode" else "mcpServers"
    return isinstance(data, dict) and isinstance(data.get(key), dict) and SERVER in data[key]


# Writing --------------------------------------------------------------------------------------------

def connect(app, python, state_directory, *, name=None, home=None, environ=None, platform=None, run=subprocess.run,
            which=shutil.which):
    """Add (or replace) the ``angelo`` entry in the app's settings; returns a short English result line."""
    command, args = server_command(app, python, state_directory, name)
    if app == "claude_code":
        claude = which("claude")
        if not claude:
            raise SetupError("Claude Code's command (claude) was not found")
        _run(run, [claude, "mcp", "remove", "--scope", "user", SERVER], check=False, environ=environ)
        _run(run, [claude, "mcp", "add", "--scope", "user", SERVER, "--", command, *args], environ=environ)
        return "Added Angelo to Claude Code (user scope). Start a new Claude Code session to use it."
    path = settings_file(app, home, environ, platform)
    if app == "codex":
        text = _read_text(path)
        _write(path, _checked_toml(_codex_with(text, command, args), command, text))
        return "Added Angelo to Codex (%s). Start a new Codex session to use it." % path.name
    key = "servers" if app == "vscode" else "mcpServers"
    entry = {"type": "stdio", "command": command, "args": args} if app == "vscode" else {"command": command, "args": args}
    data = _read_json(path, missing={})
    if not isinstance(data, dict) or (key in data and not isinstance(data[key], dict)):
        raise SetupError("%s's settings file is not in a form Angelo can edit safely" % APP_NAMES[app])
    data = dict(data)
    data[key] = dict(data.get(key) or {}, **{SERVER: entry})
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return "Added Angelo to %s. Restart %s to load it." % (APP_NAMES[app], APP_NAMES[app])


def disconnect(app, *, home=None, environ=None, platform=None, run=subprocess.run, which=shutil.which):
    """Remove only the ``angelo`` entry; True when one was removed."""
    if app == "claude_code":
        claude = which("claude")
        if not claude:
            return False
        return _run(run, [claude, "mcp", "remove", "--scope", "user", SERVER], check=False, environ=environ) == 0
    path = settings_file(app, home, environ, platform)
    if path is None or not path.exists():
        return False
    if app == "codex":
        text = _read_text(path)
        if _codex_table(text) is None:
            return False
        _write(path, _checked_toml(_codex_without(text), None, text))
        return True
    key = "servers" if app == "vscode" else "mcpServers"
    data = _read_json(path, missing={})
    if not isinstance(data, dict) or not isinstance(data.get(key), dict) or SERVER not in data[key]:
        return False
    data = dict(data)
    data[key] = {name: value for name, value in data[key].items() if name != SERVER}
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def _run(run, argv, *, check=True, environ=None):
    completed = run(argv, capture_output=True, text=True, timeout=CLI_TIMEOUT, env=dict(environ) if environ else None)
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise SetupError("%s failed: %s" % (Path(argv[0]).name, detail[-1][:200] if detail else completed.returncode))
    return completed.returncode


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        raise SetupError("Cannot read %s: %s" % (path.name, error)) from error


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _finite(text):
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise ValueError("number out of range")
    return value


def _not_a_number(name):
    raise ValueError(name)


def _read_json(path, missing):
    text = _read_text(path) if path is not None else ""
    if not text.strip():
        return missing
    try:
        return json.loads(text, object_pairs_hook=_unique, parse_float=_finite, parse_constant=_not_a_number)
    except ValueError:
        return None   # Comments, duplicate keys, out-of-range numbers or another format: never rewritten.


def _write(path, text):
    """Atomic replace, keeping the previous content as ``<name>.angelo-backup``; owner-only when new.

    A settings file that is a link (a dotfiles checkout) is written through it, so the link stays a link.
    """
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat().st_mode & 0o777 if path.exists() else 0o600
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".angelo-backup"))
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), previous)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


# Codex's TOML: only the [mcp_servers.angelo] table (and its sub-tables) is ever touched -------------

_TABLE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _codex_table(text):
    """(start, end) line indexes of ``[mcp_servers.angelo]`` and its sub-tables, or None."""
    lines = text.splitlines()
    start = end = None
    for index, line in enumerate(lines):
        match = _TABLE.match(line)
        if not match:
            continue
        name = match.group(1).replace('"', "").replace("'", "")
        ours = name == "mcp_servers." + SERVER or name.startswith("mcp_servers." + SERVER + ".")
        if ours and start is None:
            start = index
        elif start is not None and end is None and not ours:
            end = index
            while end - 1 > start and lines[end - 1].lstrip().startswith("#"):
                end -= 1   # Comments just above the next table belong to it.
    if start is None:
        return None
    return start, len(lines) if end is None else end


def _toml_string(value):
    return json.dumps(str(value), ensure_ascii=False)   # A JSON string is a valid TOML basic string.


def _codex_without(text):
    span = _codex_table(text)
    if span is None:
        return text
    lines = text.splitlines()
    kept = lines[:span[0]] + lines[span[1]:]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + ("\n" if kept else "")


def _without_ours(data):
    servers = dict(data.get("mcp_servers") or {})
    servers.pop(SERVER, None)
    return dict(data, mcp_servers=servers)


def _checked_toml(text, command, before):
    """The new config.toml, refused unless it parses, holds exactly our entry (or, removing, none) and differs from
    the old file in nothing else, as parsed (so a look-alike line inside a string or table is never edited)."""
    try:
        data, old = tomllib.loads(text), tomllib.loads(before)
    except tomllib.TOMLDecodeError as error:
        raise SetupError("Codex's config.toml is not in a form Angelo can edit safely (%s)" % error) from error
    entry = (data.get("mcp_servers") or {}).get(SERVER)
    if ((command is None) != (entry is None) or (command is not None and entry.get("command") != command)
            or _without_ours(data) != _without_ours(old)):
        raise SetupError("Codex's config.toml defines Angelo's entry in a form Angelo can't edit safely")
    return text


def _codex_with(text, command, args):
    block = ["[mcp_servers.%s]" % SERVER, "command = " + _toml_string(command),
             "args = [" + ", ".join(_toml_string(arg) for arg in args) + "]",
             "startup_timeout_sec = 30", "tool_timeout_sec = %d" % CODEX_TOOL_TIMEOUT]
    base = _codex_without(text)
    return base + ("\n" if base else "") + "\n".join(block) + "\n"

