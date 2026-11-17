# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Copy setup in Angelo's window connects what it copies (external_mcp.rst, "Connecting").

The snippet Copy setup gives runs the launcher with ``--client <name>``; the launcher serves only a client Angelo
registered (``external_clients.AppRegistry``) whose key it can read. A person's click on Copy setup in the window is
the same kind of act as Connect (and is guarded as one: ``grant_guard``), so it makes sure the app's client is
connected: registered with the default levels and its own key in Angelo's private store, or registered again, with
the levels it had, when its key is missing or unusable. Then it returns the snippet in the form the app's settings
take. Unlike Connect it writes nothing into the app's own settings: the person pastes the snippet. The snippet never
holds a key or token. Disconnect revokes it as any connection.

Each copied app is its own client, so attribution, script-file approvals and revocation stay per app: the apps
Angelo knows have theirs (``DEFAULT_CLIENT``), and each Copy setup for another MCP app makes a new one
(``mcp-app``, ``mcp-app-2``, ...), unless it copies again for one already connected. Found by the showcase pilot on
06259fa, where the launcher answered "not connected" to a copied snippet. Standard library only.
"""

from . import external_setup, managed_policy
from .external_clients import APPS, AppRegistry, DEFAULT_CLIENT, DEFAULT_LEVELS
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)

GENERIC = DEFAULT_CLIENT["other"]   # "mcp-app": the first generic MCP app's client.


def generic_clients(records):
    """The clients of other MCP apps, in the order they were added (``mcp-app``, ``mcp-app-2``, ...)."""
    return sorted((name for name, record in records.items() if record.get("app") == "other"), key=number)


def number(client):
    """``mcp-app`` is 1, ``mcp-app-N`` is N."""
    tail = client[len(GENERIC) + 1:]
    return int(tail) if client != GENERIC and tail.isdigit() else 1


def new_generic(records):
    taken = {number(name) for name in generic_clients(records)}
    index = next(n for n in range(1, len(taken) + 2) if n not in taken)
    return GENERIC if index == 1 else "%s-%d" % (GENERIC, index)


def client_for(records, app, client=""):
    """The client a Copy setup for ``app`` connects: the app's own, the given generic one if it is connected, else a
    new generic one."""
    if app != "other":
        return DEFAULT_CLIENT[app]
    if client and records.get(client, {}).get("app") == "other" and number(client) > 0:
        return client
    return new_generic(records)


def copy_setup(state_directory, app, python, client=""):
    """``(client, snippet)`` for ``app``, its client connected first. ``PermissionError`` when the policy turns access
    for external apps off; ``OSError`` when the connection can't be saved (nothing new is left behind)."""
    if app not in APPS:
        raise ValueError("Not an app Angelo knows")
    if not managed_policy.current().allow_local_cli_access:
        raise PermissionError("Turned off by your organization.")
    registry = AppRegistry(state_directory)
    records = registry.load()
    name = client_for(records, app, client)
    record = records.get(name)
    if record is None or registry.key(name) is None:
        try:
            registry.connect(app, name=name, levels=record["levels"] if record else DEFAULT_LEVELS)
        except OSError:
            if record is None:   # Never a key without its record, or a record without its key.
                try:
                    registry.disconnect(name)
                except OSError:
                    pass
            raise
    return name, external_setup.snippet(app, python, state_directory, name=name)


copy_setup = _grant_guard.entry(copy_setup)
