# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Permanent bounded mutation IDs, separate from evictable result payloads.

Entries contain only the invocation digest and opaque document digest. They are
never pruned or evicted. At capacity, new mutations fail closed; known IDs remain
protected and read-only observations can continue. A new chat is an explicit new
ID namespace, not an automatic retry or permission to repeat an old mutation.
Legacy nonempty sessions cannot prove coverage of already-evicted IDs, so unknown
mutations are closed after migration; safe observations can continue. Known IDs
still reject changed arguments even when reused for a read-only observation.
"""

import hashlib
import re
from itertools import islice

MAX_MUTATION_IDS = 4096
MAX_ID_BYTES = 2048
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_FIELDS = {"version", "closed", "entries"}


class InvocationGuardError(ValueError):
    pass


def new_invocations():
    return {"version": 1, "closed": False, "entries": {}}


def _digest(value):
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _key(value):
    if type(value) is not str or not 0 < len(value) <= MAX_ID_BYTES or any(ord(char) < 32 for char in value):
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_ID_BYTES
    except UnicodeError:
        return False


def document_digest(document):
    """Hash the host's already-bounded document representation, never an object."""
    if type(document) is not str or len(document) > 8192:
        raise InvocationGuardError("The invocation document identity is not bounded")
    try:
        return hashlib.sha256(document.encode("utf-8")).hexdigest()
    except UnicodeError as error:
        raise InvocationGuardError("The invocation document identity is not valid UTF-8") from error


def _entry(value):
    if (type(value) is not dict or set(value) != {"digest", "document"}
            or not _digest(value.get("digest")) or not _digest(value.get("document"))):
        return None
    return {"digest": value["digest"], "document": value["document"]}


def clean_invocations(value):
    """Strict finite JSON whitelist; corruption closes coverage, never resets it."""
    output = new_invocations()
    if type(value) is not dict:
        output["closed"] = True
        return output
    if (set(value) != _FIELDS or type(value.get("version")) is not int or value.get("version") != 1
            or type(value.get("closed")) is not bool):
        output["closed"] = True
    else:
        output["closed"] = value["closed"]
    entries = value.get("entries")
    if type(entries) is not dict:
        output["closed"] = True
        return output
    if len(entries) > MAX_MUTATION_IDS:
        output["closed"] = True
    for identity, raw in islice(entries.items(), MAX_MUTATION_IDS):
        entry = _entry(raw)
        if not _key(identity):
            output["closed"] = True
            continue
        if entry is None:
            output["closed"] = True
            entry = {"digest": None, "document": None}  # Retain a known ID even when its signature is damaged.
        output["entries"][identity] = entry
    return output


def migrate_invocations(results, has_history=False):
    """Old payload caches cannot prove whether an earlier mutation was evicted."""
    output = new_invocations()
    output["closed"] = bool(has_history or results)
    if type(results) is not dict:
        output["closed"] = True
        return output
    for identity, result in islice(results.items(), MAX_MUTATION_IDS):
        if not _key(identity):
            continue
        output["entries"][identity] = {"digest": None, "document": None}
        if type(result) is not dict or not _digest(result.get("invocation_digest")):
            continue
        try:
            document = document_digest(result.get("document"))
        except InvocationGuardError:
            continue
        output["entries"][identity] = {"digest": result["invocation_digest"], "document": document}
    return output


def well_formed(value):
    """The ledger's shape, without reading its entries (O(1))."""
    return (type(value) is dict and set(value) == _FIELDS and type(value.get("version")) is int
            and value.get("version") == 1 and type(value.get("closed")) is bool
            and type(value.get("entries")) is dict and len(value["entries"]) <= MAX_MUTATION_IDS)


def valid_entry(identity, value):
    """One entry exactly as ``reserve_invocation`` adds it: checked when it is added and written, never again."""
    return _key(identity) and _entry(value) == value


def _ledger(state):
    value = state.get("invocations")
    # Loaded entries are validated once by SessionStore.load, and each entry
    # added later by reserve_invocation (and again as the journal writes it).
    # Never rescan the complete ledger per request or per save; its host owner
    # adds only checked entries and cannot evict them through the payload-cache path.
    if not well_formed(value):
        value = clean_invocations(value)
        state["invocations"] = value
    return value


def check_invocation(state, identity, digest, document, *, read_only=False):
    """Return True for a protected duplicate; reject mismatched or unknown scope."""
    ledger = _ledger(state)
    if identity in ledger["entries"]:
        entry = _entry(ledger["entries"][identity])
        if entry is None:
            ledger["closed"] = True
            raise InvocationGuardError("This invocation's duplicate protection is invalid; no operation was repeated. Start a new chat.")
        if entry["digest"] != digest:
            raise InvocationGuardError("A tool invocation ID cannot be reused with different operations or arguments")
        if entry["document"] != document_digest(document):
            raise InvocationGuardError("This call belongs to an earlier document instance; inspect the scene")
        return True
    if ledger["closed"] and not read_only:
        raise InvocationGuardError("This legacy/corrupt conversation cannot prove complete mutation-ID coverage. No new mutation was admitted; start a new chat. Safe observations remain available.")
    return False


def reserve_invocation(state, identity, digest, document):
    """Reserve before durable admission. Capacity never removes an existing ID."""
    ledger = _ledger(state)
    if check_invocation(state, identity, digest, document):
        return
    if not _key(identity) or not _digest(digest):
        raise InvocationGuardError("Use a bounded canonical mutation invocation identity")
    if len(ledger["entries"]) >= MAX_MUTATION_IDS:
        raise InvocationGuardError("This conversation's permanent mutation-ID guard is full. No mutation was admitted; start a new chat. Existing IDs were not evicted.")
    ledger["entries"][identity] = {"digest": digest, "document": document_digest(document)}
