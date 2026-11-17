# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One API key for several connections of one account (doc/angelo/ai_providers.rst, "One key for several
connections").

Some providers bill one key for several services Angelo connects separately: a Google AI Studio key runs both
Gemini Images (Media Studio) and the Gemini coding agent. The specs of such modes name the same ``key_family``
(``provider_registry``). A stored connection may then record ``key_from``: the id of another stored connection of
its family whose key it uses (AI Providers, "API key from"), so the person enters the key once. Separate keys stay
possible: a connection with its own key simply has no ``key_from``.

A linked connection owns no key: it has no saved-key item of its own (``saved_keys_ui.uses_saved_key``), and its
in-memory key is a mirror of its source's, set whenever the source's key is set or loaded (``mirror``). Replacing
the source's key therefore changes both; entering a key on the linked connection makes it its own again
(``unlink``). Links are one level deep: a source is a connection with its own key, never another linked one, so
removing a source can strand only its direct dependents, which then need a key of their own (``orphans``).

Pure functions over the connection records and the in-memory key map; no ``bpy``, no I/O.
"""

from .provider_registry import key_family


def _family(record):
    try:
        return key_family(record["mode"])
    except ValueError:   # A mode this build does not know.
        return None


def can_share(record):
    """Whether ``record``'s mode belongs to a family whose connections may share one key."""
    return _family(record) is not None and record.get("method") == "API_KEY"


def sources(records, mode, exclude=None):
    """Stored connections whose own key a connection of ``mode`` may use: same key family, an API key of their
    own (never a linked one), not ``exclude`` itself. Sorted by name so the choice list is stable."""
    try:
        family = key_family(mode)
    except ValueError:
        return []
    if family is None:
        return []
    found = [record for identity, record in records.items()
             if identity != exclude and record.get("method") == "API_KEY" and not record.get("key_from")
             and _family(record) == family]
    return sorted(found, key=lambda record: (str(record.get("name") or "").lower(), record["id"]))


def source_of(records, record):
    """The connection whose key ``record`` uses, or None when it has its own key or its link no longer holds
    (the source was removed, links to another, or is of another family)."""
    identity = record.get("key_from")
    if not identity:
        return None
    source = records.get(identity)
    if source is None or source is record or source.get("key_from") or source.get("method") != "API_KEY":
        return None
    family = _family(record)
    return source if family is not None and _family(source) == family else None


def dependents(records, source_id):
    """Ids of the connections that use ``source_id``'s key through a valid link."""
    return [identity for identity, record in records.items()
            if record.get("key_from") == source_id and source_of(records, record) is not None]


def link(records, record, source_id):
    """Make ``record`` use ``source_id``'s key. Raises ValueError for a source it may not use."""
    if source_id not in {source["id"] for source in sources(records, record["mode"], exclude=record["id"])}:
        raise ValueError("That connection's key can't be used here")
    if dependents(records, record["id"]):
        raise ValueError("Other connections use this connection's key; it keeps its own key")
    record["key_from"] = source_id


def unlink(record):
    """Give ``record`` back its own key slot (the person entered a key for it)."""
    record["key_from"] = None


def mirror(records, memory, source_id):
    """Copy ``source_id``'s in-memory key to each of its dependents (or clear theirs when it has none).
    Returns the dependents' ids, so callers can refresh what holds a copy of their key."""
    linked = dependents(records, source_id)
    secret = memory.get(source_id)
    for identity in linked:
        if secret:
            memory[identity] = secret
        else:
            memory.pop(identity, None)
    return linked


def orphans(records, source_id):
    """On removing ``source_id``: unlink its dependents (they now need a key of their own). Returns their ids."""
    stranded = [identity for identity, record in records.items() if record.get("key_from") == source_id]
    for identity in stranded:
        records[identity]["key_from"] = None
    return stranded
