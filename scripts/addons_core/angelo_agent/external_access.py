# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one path that ends an app's access to Angelo's agent (doc/angelo/external_mcp.rst, "Revoking access").

The person's Disconnect, the access switch turned off and an organization policy that closes local access all come
here (``ExternalHost.reload_apps`` and ``ExternalHost.stop``). For each of the app's conversations: a running turn
stops as the person's Stop stops it, a held goal ends (``goal_controls.person_stopped``), the conversation is marked
``external_revoked`` so nothing it left waiting starts a turn (``chat_pending.dispatch_due``) and no goal round
starts (``Controller.require_model_turn``), and a notice says so. The app's parked reads are answered at once with
the reason, its queued commands fail (``WorkQueue.fail_label``), its live pages close and the host forgets what it
held for it. Every command is authorized again on the main thread when it is drained (``ExternalHost.drain``).

Whether a conversation's access ended is read from the app registry, never only from memory, so it holds across a
restart: each conversation keeps the ``connected_at`` of the connection its app's last message came on
(``stamp``), and it is revoked once the registry no longer holds that connection (a Disconnect, or a reconnect with
a new key). Only the app's next message on its current connection stamps it again. Main thread; no bpy.
"""

import os

from .ui_text import n_

REVOKED = n_("Access for '{label}' ended, so its work here stopped. Messages it left waiting are not sent.")


_registries = {}   # clients.json path -> (its stat, its records)


def clean_stamp(value):
    """A conversation's saved app connection (``sessions``), or None: ``connected_at`` a number or None, ``key`` three
    whole numbers or None. Anything else reads as no stamp, never as a connection."""
    if not isinstance(value, dict) or set(value) - {"connected_at", "key"}:
        return None
    stamp, key = value.get("connected_at"), value.get("key")
    if not (stamp is None or type(stamp) in (int, float)):
        return None
    if not (key is None or isinstance(key, list) and len(key) == 3 and all(type(part) is int for part in key)):
        return None
    return {"connected_at": stamp, "key": list(key) if key is not None else None}


def stamp_value(directory, label, record):
    """The connection an app's message comes on: ``record``'s (``AppRegistry``) and its key file (a reconnect writes a
    new one, also within the same second)."""
    return {"connected_at": record.get("connected_at"), "key": _key(directory, label)}


def stamp(controller, record):
    """The app's message reached ``controller`` on the connection ``record`` describes."""
    controller.external_revoked = False
    value = stamp_value(controller.workspace.parent, getattr(controller, "external_client", ""), record)
    if controller.state.get("external_app") != value:
        controller.state["external_app"] = value
        controller.save()


def revoked(controller):
    """Whether this External conversation's app lost the access its last message came with: revoked this session,
    or its stamped connection is no longer the registry's (``stamp``). A CLI client's conversation never is."""
    if getattr(controller, "external_revoked", False):
        return True
    stamped = controller.state.get("external_app") if isinstance(getattr(controller, "state", None), dict) else None
    if not stamped:
        return False
    client = getattr(controller, "external_client", "")
    record = _record(controller.workspace.parent, client)
    return (record is None or record.get("connected_at") != stamped.get("connected_at")
            or _key(controller.workspace.parent, client) != stamped.get("key"))


def _key(directory, client):
    """The app's key file as it is now (device, inode, change time): a new key is a new connection."""
    from .external_clients import AppRegistry
    try:
        info = os.stat(AppRegistry(directory).keys / client)
    except OSError:
        return None
    return [info.st_dev, info.st_ino, info.st_mtime_ns]


def _record(directory, client):
    """The app's registry record now, re-read only when clients.json changed; None when it is not connected."""
    from .external_clients import AppRegistry
    registry = AppRegistry(directory)
    try:
        info = os.stat(registry.path)
    except OSError:
        return None
    key = (info.st_ino, info.st_size, info.st_mtime_ns)
    cached = _registries.get(str(registry.path))
    if cached is None or cached[0] != key:
        cached = _registries[str(registry.path)] = (key, registry.load())
    return cached[1].get(client)


def revoke(host, label, why):
    """End ``label``'s access now (module docstring). ``host``: the ``ExternalHost``. Never raises for one
    conversation's failure: the others still stop."""
    from . import goal_controls
    from .external_agent import _end_page
    from .stop_notice import stop_notice_for
    from .ui_text import notice
    for controller in [c for c in host.env.controllers().values() if getattr(c, "external_client", "") == label]:
        if getattr(controller, "external_revoked", False):
            continue
        controller.external_revoked = True
        try:
            goal_controls.person_stopped(controller)   # A held goal ends, as with the person's Stop.
            if controller.turn_running() or controller.pending:
                try:   # What the Stop leaves running (a script, billed work), as the person's Stop says it.
                    controller.emit("message", {"role": "system", **stop_notice_for(controller)})
                except Exception:  # noqa: BLE001 - the Stop itself never waits on its notice.
                    pass
                controller.stop()
            controller.emit("message", {"role": "system", **notice(REVOKED, label=label)})
        except Exception as error:  # noqa: BLE001 - the mark holds; the next conversation still stops.
            print("Angelo: an app's work could not be stopped:", type(error).__name__, error)
    close_waiters(host, label, why)
    for identity in host._owned.pop(label, ()):   # Its live pages end; nothing it held is kept for a later app.
        _end_page(identity, why)
    for held in (host._tasks, host._views, host._looks, host._sends, host._goals, host._held):
        held.pop(label, None)


def close_waiters(host, label, why):
    """Answer ``label``'s parked reads at once (``unavailable``, with ``why``)."""
    for waiter in [w for w in host._agent_waiters if w["label"] == label]:
        host._agent_waiters.remove(waiter)
        if waiter["work"].response is None:
            waiter["work"].complete({"code": "unavailable", "message": why})
