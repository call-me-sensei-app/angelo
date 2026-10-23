# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page's "External AI apps (MCP)" card, as data (``providers_page_model`` draws it).

Angelo's own MCP server: the access switch (the CLI's) on top, then one row per
app Angelo sets up (Claude Code and Codex), each with a button that names the
app it connects. A connected app can read, edit, run scripts the person reviews
and start tasks. Blender Lab's MCP add-on is not part of this card. Every string
is translated here.
"""

import sys

from . import external_setup, external_ui, managed_policy, script_files
from .display_text import Text, shown
from .external_clients import APP_NAMES, AppRegistry, DEFAULT_CLIENT
from .ui_text import iface_, plural_

APP_ORDER = ("claude_code", "codex")
# Actions providers_page_host.act runs: action -> operator.
ACTIONS = {"mcp_connect": "external_app_connect", "mcp_disconnect": "external_app_disconnect",
           "mcp_copy": "external_app_copy_setup", "mcp_forget_scripts": "external_app_forget_scripts"}


def act(ops, action, value):
    getattr(ops, ACTIONS[action])('EXEC_DEFAULT', app=value)


def _app_rows(state, locked):
    apps = AppRegistry(state).load()
    rows = []
    for app in APP_ORDER:
        name = iface_(APP_NAMES[app])
        record = apps.get(DEFAULT_CLIENT[app])
        row = {"kind": "connection", "tile": name[:1], "name": name, "lines": [], "buttons": []}
        if record is not None:
            row["chip"] = (iface_("Connected"), "teal", False)
            row["lines"].append(iface_("Can read and edit the scene, run scripts you review and start tasks."))
            allowed = len(script_files.entries(DEFAULT_CLIENT[app]))
            if allowed:   # Script files the person always allows while unchanged (script_files).
                row["lines"].append(plural_(allowed, "Runs {count} script file you allowed without asking while it's "
                                            "unchanged.", "Runs {count} script files you allowed without asking while "
                                            "they're unchanged."))
                # The paths themselves (user data, never translated), so the person sees exactly what is allowed.
                row["lines"] += [script_files.shown_path(e["path"]) for e in script_files.entries(DEFAULT_CLIENT[app])[:5]]
                row["buttons"].append((iface_("Forget script files"), "mcp_forget_scripts", app, "ghost"))
            row["buttons"].append((iface_("Disconnect"), "mcp_disconnect", app, "ghost"))
        elif external_setup.installed(app):
            row["lines"].append(iface_("Adds Angelo to {app}'s MCP settings for your user.", app=name))
            row["buttons"].append((iface_("Connect {app}", app=name), "mcp_connect", app, "primary"))
        else:
            row["lines"].append(iface_("Not found on this computer."))
            row["buttons"].append((iface_("Copy setup"), "mcp_copy", app, "ghost"))
        if app in external_ui._busy:
            row["chip"] = (iface_("Working…"), "muted", True)
        result = external_ui.line(app)
        if result:
            row["lines"].append(result)
            if not external_ui.results[app][0]:
                row["buttons"].insert(0, (iface_("Copy setup"), "mcp_copy", app, "secondary"))
        if locked:
            row["buttons"] = []
        rows.append(row)
    return rows


def external_apps(ui, prefs, policy):
    from . import external_host
    status = external_host.status_text()
    locked = not policy.allow_local_cli_access
    access = {"kind": "switch", "name": iface_("Allow apps on this computer to reach Angelo"),
              "chip": (iface_("Status: {status}", status=iface_(status)),
                       "teal" if status.lower().startswith(("on", "listening", "running")) else "muted", False),
              "lines": [iface_("AI apps and coding agents running as you can use Angelo's tools, or hand Angelo's "
                               "agent a task. Their edits and scripts wait for your approval in their own chat; you "
                               "can stop them and undo as in any chat. Turning this off closes the connection.")],
              "on": prefs.allow_local_cli and not locked, "action": "toggle", "value": "allow_local_cli",
              "enabled": not locked,
              "facts": [("shield", iface_("Apps can never approve their own changes."), "muted"),
                        ("terminal", iface_("Private local connection with a key per app. No network port."), "muted")]}
    if locked:
        access["lock"] = shown(Text(managed_policy.CLI_OFF))
    description = iface_("Angelo's own MCP server, so Claude Code and Codex can use Angelo.")
    if sys.platform == "win32":   # The local connection is a Unix socket; Windows needs its named pipe first.
        access.update(enabled=False, on=False, lines=[iface_("Coming to Windows in a later update.")])
        return {"title": iface_("External AI apps (MCP)"), "description": description, "rows": [access]}
    return {"title": iface_("External AI apps (MCP)"), "description": description,
            "rows": [access] + _app_rows(ui.state_directory(), locked)}
