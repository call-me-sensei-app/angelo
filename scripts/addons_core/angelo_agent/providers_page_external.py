# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page's "External AI apps (MCP)" card, as data (``providers_page_model`` draws it).

Angelo's own MCP server: the access switch (the CLI's) on top, then one row per
app Angelo sets up (Claude Code and Codex), each with a button that names the
app it connects. A connected app works through Angelo's agent: it sends it
messages, follows its progress and sees what it sees; approvals and Stop stay
the person's. Every string is translated here.
"""

import sys

from . import external_setup, external_ui, managed_policy
from .display_text import Text, shown
from .external_clients import APP_NAMES, AppRegistry, DEFAULT_CLIENT
from .ui_text import iface_, n_

APP_ORDER = ("claude_code", "codex", "claude_desktop", "cursor", "vscode")   # Then each other MCP app, then "other".
OTHER_NAME = n_("MCP app")   # external_clients.APP_NAMES["other"], as the card shows it.
OTHER_NUMBERED = n_("MCP app {number}")   # The second and later other MCP apps (external_copy: one client each).
OTHER_APP = n_("Any other app that speaks MCP.")
COPY_CONNECTS = n_("Copy setup connects it in Angelo and copies what to paste into its MCP settings.")
# A generic app's record whose key went missing (Copy setup break-it r2, S5): its row stays, so it can be repaired.
KEY_MISSING = n_("Not connected: its key is missing. Copy setup connects it again; what you pasted keeps working.")
# Actions providers_page_host.act runs: action -> operator. A row's value is "app" or "app/client".
# An app's entry left by another Angelo build or copy (external_setup.elsewhere): Connect points it here.
ELSEWHERE_CHIP = n_("Starts another Angelo")
ELSEWHERE = n_("{app}'s Angelo entry starts the MCP server in {where}. Connect {app} points it at this Angelo.")
# Another Angelo build has access for this user (external_host.holder): this one waits, never takes it.
HELD_CHIP, HELD_CHIP_UNKNOWN = n_("Waiting for Angelo {version}"), n_("Waiting for another Angelo")
HELD = n_("Angelo {version} (PID {pid}) has access for external apps for your user. Quit it, or turn off access "
          "there; this Angelo takes over once it is free.")
HELD_UNKNOWN = n_("Another Angelo (PID {pid}) has access for external apps for your user. Quit it, or turn off "
                  "access there; this Angelo takes over once it is free.")
ACTIONS = {"mcp_connect": "external_app_connect", "mcp_disconnect": "external_app_disconnect",
           "mcp_copy": "external_app_copy_setup"}


def act(ops, action, value):
    app, _, client = value.partition("/")
    properties = {"app": app}
    if client:
        properties["client"] = client
    getattr(ops, ACTIONS[action])('EXEC_DEFAULT', **properties)


def _button(row, label, action, value, style, first=False):
    """At most one button per action on a row (break-it review S4): a second asks for its place and style."""
    row["buttons"] = [button for button in row["buttons"] if button[1] != action]
    row["buttons"].insert(0 if first else len(row["buttons"]), (label, action, value, style))


def _connected(row, client, value):
    row["chip"] = (iface_("Connected"), "teal", False)
    row["lines"].append(iface_("Works through Angelo's agent: sends it messages, follows its progress and sees "
                               "what it sees."))
    _button(row, iface_("Copy setup"), "mcp_copy", value, "ghost")   # Copies again; no new key.
    _button(row, iface_("Disconnect"), "mcp_disconnect", value, "ghost")


def _result(row, key, value):
    result = external_ui.line(key)
    if result:
        row["lines"].append(result)
        if not external_ui.results[key][0]:
            _button(row, iface_("Copy setup"), "mcp_copy", value, "secondary", first=True)
    if key in external_ui._busy:
        row["chip"] = (iface_("Working…"), "muted", True)


def _app_rows(state, locked):
    """One row per app Angelo knows, one per other MCP app connected (each its own client), then one to add another.
    Connected means a record with a readable key (``snapshot``: break-it review S1)."""
    from .external_copy import generic_clients, number
    registry = AppRegistry(state)
    connected = registry.snapshot()
    rows = []
    for app in APP_ORDER:
        name = iface_(APP_NAMES[app])
        row = {"kind": "connection", "tile": name[:1], "name": name, "lines": [], "buttons": []}
        where = (external_setup.elsewhere(app, external_ui._python(), state, DEFAULT_CLIENT[app])
                 if app in external_ui.OFFERED and external_setup.installed(app) else "")
        if DEFAULT_CLIENT[app] in connected:
            _connected(row, DEFAULT_CLIENT[app], app)
            if where:   # It would start another build's server: never "Connected" (2.0.1 internal test).
                row["chip"] = (iface_(ELSEWHERE_CHIP), "muted", False)
                row["lines"][-1:] = [iface_(ELSEWHERE, app=name, where=where)]
                _button(row, iface_("Connect {app}", app=name), "mcp_connect", app, "primary", first=True)
            elif app in external_ui.OFFERED and external_setup.installed(app) and not external_setup.has_entry(app):
                # Copied before it was installed, or its settings lost the entry: Connect writes them (S3).
                _button(row, iface_("Connect {app}", app=name), "mcp_connect", app, "primary", first=True)
        elif app in external_ui.OFFERED and external_setup.installed(app):
            row["lines"].append(iface_(ELSEWHERE, app=name, where=where) if where else
                                iface_("Adds Angelo to {app}'s MCP settings for your user.", app=name))
            _button(row, iface_("Connect {app}", app=name), "mcp_connect", app, "primary")
        else:
            if app in external_ui.OFFERED:
                row["lines"].append(iface_("Not found on this computer."))
            row["lines"].append(iface_(COPY_CONNECTS))
            _button(row, iface_("Copy setup"), "mcp_copy", app, "ghost" if app in external_ui.OFFERED else "primary")
        _result(row, app, app)
        rows.append(row)
    for client in generic_clients(registry.load()):
        index = number(client)
        name = iface_(OTHER_NAME) if index == 1 else iface_(OTHER_NUMBERED, number=index)
        row = {"kind": "connection", "tile": name[:1], "name": name, "lines": [], "buttons": []}
        if client in connected:
            _connected(row, client, "other/" + client)
        else:   # Its record without a readable key: Copy setup repairs this client, Disconnect removes it.
            row["lines"].append(iface_(KEY_MISSING))
            _button(row, iface_("Copy setup"), "mcp_copy", "other/" + client, "primary")
            _button(row, iface_("Disconnect"), "mcp_disconnect", "other/" + client, "ghost")
        _result(row, client, "other/" + client)
        rows.append(row)
    name = iface_(OTHER_NAME)
    another = {"kind": "connection", "tile": name[:1], "name": name, "buttons": [],
               "lines": [iface_(OTHER_APP), iface_(COPY_CONNECTS)]}
    _button(another, iface_("Copy setup"), "mcp_copy", "other", "primary")   # A new client for each app copied.
    rows.append(another)
    if locked:
        for row in rows:
            row["buttons"] = []
    return rows


def section(ui, prefs, policy):
    from . import external_host
    status, holder = external_host.status_text(), external_host.holder()
    locked = not policy.allow_local_cli_access
    if holder is not None:   # Another build has access: which, and what to do (never the raw English error).
        version, pid = holder
        status = iface_(HELD_CHIP, version=version) if version else iface_(HELD_CHIP_UNKNOWN)
    access = {"kind": "switch", "name": iface_("Allow apps on this computer to reach Angelo"),
              "chip": (iface_("Status: {status}", status=iface_(status)),
                       "teal" if status.lower().startswith(("on", "listening", "running")) else "muted", False),
              "lines": [iface_("AI apps and coding agents running as you can work with Angelo's agent: they send it "
                               "messages, follow its progress and see what it sees, in their own chat. Approvals "
                               "stay yours; you can stop them and undo as in any chat. Turning this off "
                               "closes the connection.")],
              "on": prefs.allow_local_cli and not locked, "action": "toggle", "value": "allow_local_cli",
              "enabled": not locked,
              "facts": [("shield", iface_("Apps can never approve their own changes."), "muted"),
                        ("terminal", iface_("Private local connection with a key per app. No network port."), "muted")]}
    if holder is not None:
        access["lines"].insert(0, iface_(HELD, version=version, pid=pid) if version else
                               iface_(HELD_UNKNOWN, pid=pid))
    if locked:
        access["lock"] = shown(Text(managed_policy.CLI_OFF))
    description = iface_("Angelo's own MCP server, so Claude Code and Codex can use Angelo.")
    if sys.platform == "win32":   # The local connection is a Unix socket; Windows needs its named pipe first.
        access.update(enabled=False, on=False, lines=[iface_("Coming to Windows in a later update.")])
        return {"title": iface_("External AI apps (MCP)"), "description": description, "rows": [access]}
    return {"title": iface_("External AI apps (MCP)"), "description": description,
            "rows": [access] + _app_rows(ui.state_directory(), locked)}
