# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The connection form's "API key from" choice and what a shared key changes in the window (``shared_keys``).

A connection of a key family (``provider_registry.key_family``: a Google AI Studio key runs Gemini Images and the
Gemini coding agent) may use another stored connection's key instead of a key of its own. The New/Edit connection
form offers the choice only when such a connection exists: "Enter a new key" (the default) or "Use the key of
<name>". A linked connection keeps no saved-key item; the source's key reaches it whenever that key is set or
loaded (``share``), and its open chats and checks are refreshed as if its own key had been replaced.
"""

import threading

from . import shared_keys
from .display_text import Text
from .ui_text import iface_, n_

NEW = "NEW"
_items_cache = {}   # Blender needs dynamic enum items kept alive while the form shows them.


def _ui():
    from . import ui
    return ui


def key_source_items(operator, context):
    """The form's choices: a new key, or the key of each stored connection whose key this mode may use."""
    from .provider_registry import MODE_BY_CONNECTION
    items = [(NEW, iface_("Enter a new key"), iface_("Give this connection its own API key"))]
    mode = MODE_BY_CONNECTION.get((operator.group, operator.provider, "API_KEY"))
    if mode:
        for record in shared_keys.sources(_ui().connections().records, mode, exclude=operator.connection_id or None):
            items.append((record["id"], iface_("Use the key of {name}", name=record["name"]),
                          iface_("One key for both: replacing {name}'s key changes this connection's too",
                                 name=record["name"])))
    key = tuple(items)
    return _items_cache.setdefault(key, items)


def offers_choice(operator, mode):
    """Whether the form shows "API key from": the mode shares keys and a usable source exists."""
    return bool(shared_keys.sources(_ui().connections().records, mode, exclude=operator.connection_id or None))


def chosen_source(operator, mode):
    """The source connection id the form chose, or None for a key of its own (also when no choice is offered)."""
    if not offers_choice(operator, mode) or operator.key_source in ("", NEW):
        return None
    return operator.key_source


def initial_choice(record):
    """The form's choice when it opens on ``record``: its valid source, else a new key."""
    source = shared_keys.source_of(_ui().connections().records, record) if record else None
    return source["id"] if source else NEW


def in_use(source_id):
    """Whether any connection that uses ``source_id``'s key has an active turn (its key cannot change now)."""
    ui = _ui()
    return any(ui.connection_in_use(identity) for identity in shared_keys.dependents(ui.connections().records, source_id))


def _refresh(ui, identity):
    """What holds a copy of ``identity``'s key picks up its new (or no) key, as ``ui.replace_connection_key`` does."""
    for controller in ui._controllers.values():
        controller.invalidate_runtime(identity)
        if controller.state.get("connection_id") == identity:
            if controller.provider:
                threading.Thread(target=controller.provider.close, daemon=True).start()
            controller.provider = None
            controller.api_key, controller.models = ui._memory_keys.get(identity, ""), []
    ui.catalogs().invalidate(identity)
    ui._api_connections.invalidate(identity)
    ui.runtime_bindings.retire_connection(identity)


def share(source_id):
    """Give ``source_id``'s current key to each connection that uses it, then check those connections."""
    ui = _ui()
    records = ui.connections().records
    for identity in shared_keys.mirror(records, ui._memory_keys, source_id):
        _refresh(ui, identity)
        record = records[identity]
        if record["mode"] in ui.HTTP_MODES and ui._memory_keys.get(identity):
            ui._api_connections.start(record, ui._memory_keys[identity])


def link(record, source_id):
    """Make ``record`` use ``source_id``'s key: its own saved key (if any) is deleted so an older key cannot come
    back, the link is saved, and the source's key reaches it now."""
    ui = _ui()
    records = ui.connections().records
    from . import saved_keys_ui
    had_own = saved_keys_ui.uses_saved_key(record)
    shared_keys.link(records, record, source_id)
    ui.connections().save()
    if had_own:
        saved_keys_ui.forget(record["id"])
    share(source_id)


def unlink(record):
    """``record`` takes a key of its own (entered in the same form); its mirrored copy is dropped first."""
    ui = _ui()
    shared_keys.unlink(record)
    ui.connections().save()
    ui._memory_keys.pop(record["id"], None)
    _refresh(ui, record["id"])


def strand(source_id):
    """After ``source_id`` was removed: its dependents keep their records but lose the key (they need their own);
    their open turns stop, as a removed connection's do. Returns their ids; the caller saves the unlinked records."""
    ui = _ui()
    stranded = shared_keys.orphans(ui.connections().records, source_id)
    for identity in stranded:
        ui._memory_keys.pop(identity, None)
        for controller in ui._controllers.values():
            if controller.state.get("connection_id") == identity:
                controller.stop()
        _refresh(ui, identity)
    return stranded


def storage_label(record):
    """The AI Providers line of a connection that uses another's key, or None for one with a key of its own."""
    if not record.get("key_from"):
        return None
    source = shared_keys.source_of(_ui().connections().records, record)
    if source is None:
        return Text(n_("The connection whose key this one used was removed · enter a key"))
    return Text(n_("Uses the API key of {name}"), name=source["name"])
