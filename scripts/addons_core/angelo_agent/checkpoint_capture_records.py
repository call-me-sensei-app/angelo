# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private structural v4 declarations, separate from native/resource authority.

Validation copies data. It does not inspect files, verify hashes or native
headers, establish owner/freshness, publish metadata, or authorize recovery.
Even VALIDATED is only a declared state here. The store may load these facts to
preserve journals; legacy _record and the legacy scene-copy route reject v4.
"""

import copy
import json
import math

from .checkpoint_filesystem import MAX_METADATA_BYTES
from .checkpoint_policy import _identity
from .checkpoint_records import _record
from .checkpoint_seal import well_formed as well_formed_seal

MAX_COMPONENT_INTEGER = (1 << 63) - 1
_COMMON_FIELDS = frozenset({
    "version", "id", "original_path", "created", "files", "state", "purpose",
    "capture_mode", "capture_ms", "scene_bytes", "protected", "pruned_at",
    "prune_reason", "bytes_freed", "boundary", "storage", "scene_hash",
})
_COMPONENT_FIELDS = frozenset({"scene_bytes", "scene_hash", "sculpt", "storage", "component_seal"})
_VALIDATION_STATES = frozenset({"PENDING", "VALIDATED", "INTERRUPTED_VALID", "INVALID"})
_VALID_STATES = frozenset({"VALIDATED", "INTERRUPTED_VALID"})
_TOMBSTONE_FIELDS = frozenset({"pruned_at", "prune_reason", "bytes_freed"})
_HEX = frozenset("0123456789abcdef")
_OUTPUT_NAMES = {
    "BASE": ("scene.blend@", "scene.blend"),
    "RUNTIME": ("sculpt.runtime@", "sculpt.runtime"),
}


def output_names(role):
    """Closed private destination names; names alone grant no file authority."""
    if type(role) is not str or role not in _OUTPUT_NAMES:
        raise ValueError("Unknown capture output role")
    return _OUTPUT_NAMES[role]


def _identity_word(value):
    if (type(value) is not str or not 1 <= len(value) <= 128
            or any(char not in "-0123456789" for char in value)):
        raise ValueError("A file identity needs a bounded canonical decimal string")
    try:
        canonical = str(int(value))
    except ValueError as error:
        raise ValueError("Invalid file identity") from error
    if canonical != value:
        raise ValueError("A file identity must preserve its exact canonical spelling")


def file_identity(device, inode):
    """Keep actual stat identity bits outside the signed63 component counters."""
    if type(device) is not int or type(inode) is not int:
        raise ValueError("File identity words must be exact integers")
    result = {"device": str(device), "inode": str(inode)}
    for value in result.values():
        _identity_word(value)
    return result


def _output_identity(value):
    if type(value) is not dict or set(value) != {"device", "inode"}:
        raise ValueError("Incomplete capture output identity")
    for word in value.values():
        _identity_word(word)


def _outputs(value):
    if (type(value) is not dict or set(value) != {"version", "directory", "roles"}
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("Invalid capture output ledger")
    _output_identity(value["directory"])
    roles = value["roles"]
    if type(roles) is not dict or not 1 <= len(roles) <= 2:
        raise ValueError("A capture ledger needs one or two closed roles")
    for role, slot in roles.items():
        output_names(role)
        if type(slot) is not dict or set(slot) != {"created"}:
            raise ValueError("Invalid capture output slot")
        if slot["created"] is not None:
            _output_identity(slot["created"])


def _data(value):
    """Bound/copy exact JSON primitives before any borrowed validator sees them."""
    active = set()
    remaining = MAX_METADATA_BYTES

    def copy(item, depth=0):
        nonlocal remaining
        if depth > 64 or remaining <= 0:
            raise ValueError("Capture metadata exceeds its structural bounds")
        remaining -= 1
        kind = type(item)
        if kind is dict or kind is list:
            if id(item) in active or len(item) > remaining:
                raise ValueError("Capture metadata is cyclic or oversized")
            active.add(id(item))
            try:
                if kind is list:
                    return [copy(child, depth + 1) for child in item]
                result = {}
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("Capture metadata keys must be exact strings")
                    result[copy(key, depth + 1)] = copy(child, depth + 1)
                return result
            finally:
                active.remove(id(item))
        if kind is str:
            if len(item) > remaining:
                raise ValueError("Capture metadata exceeds its size limit")
            remaining -= len(item.encode("utf-8"))
            return item
        if kind is bool or item is None:
            return item
        if kind is int:
            if not -MAX_COMPONENT_INTEGER <= item <= MAX_COMPONENT_INTEGER:
                raise ValueError("Capture metadata integer exceeds its range")
            return item
        if kind is float and math.isfinite(item):
            return item
        raise ValueError("Capture metadata needs finite exact JSON primitives")

    result = copy(value)
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("Capture metadata exceeds its size limit")
    return result


def _count(value):
    if type(value) is not int or not 1 <= value <= MAX_COMPONENT_INTEGER:
        raise ValueError("A published capture component needs a positive exact integer count")


def _hash(value):
    if type(value) is not str or len(value) != 64 or not set(value) <= _HEX:
        raise ValueError("A capture component needs a lowercase BLAKE2b-256 declaration")


def _sculpt(value):
    if type(value) is not dict or set(value) != {"filename", "format_version", "bytes", "hash", "owner_count"}:
        raise ValueError("Incomplete or unknown Sculpt component fields")
    if (type(value["filename"]) is not str or value["filename"] != "sculpt.runtime"
            or type(value["format_version"]) is not int or value["format_version"] != 1):
        raise ValueError("Unsupported Sculpt component name or format")
    _count(value["bytes"])
    _count(value["owner_count"])
    _hash(value["hash"])


def validate_receipt(value, capture_id):
    """Check a complete receipt's DECLARED structure, never its bytes or source."""
    identity = _identity(capture_id)
    result = _data(value)
    if type(result) is not dict or set(result) != {"capture_id", "scene_bytes", "scene_hash", "sculpt"}:
        raise ValueError("Incomplete or unknown capture receipt fields")
    if _identity(result["capture_id"]) != identity:
        raise ValueError("The receipt names a different capture")
    _count(result["scene_bytes"])
    _hash(result["scene_hash"])
    _sculpt(result["sculpt"])
    return result


def validate_record(value, identity):
    """Validate v4 only; the existing legacy validator is deliberately unchanged."""
    identity = _identity(identity)
    value = _data(value)
    if (type(value) is not dict or set(value) - _COMMON_FIELDS - {"capture_validation", "sculpt", "capture_outputs",
                                                                  "component_seal"}
            or type(value.get("version")) is not int or value["version"] != 4
            or _identity(value.get("id")) != identity):
        raise ValueError("Invalid private v4 capture record")
    validation = value.get("capture_validation")
    if type(validation) is not str or validation not in _VALIDATION_STATES:
        raise ValueError("Invalid capture validation declaration")
    # Legacy common measurements retain _record's finite <=2**53 bounds.
    # Component byte/count fields instead use their explicit signed63-bit range;
    # projecting them through legacy _number would incorrectly restrict them.
    common = {key: item for key, item in value.items()
              if key not in _COMPONENT_FIELDS and key not in {"capture_validation", "capture_outputs"}}
    common["version"] = 3
    result = _record(common, identity)
    result.update(version=4, capture_validation=validation)
    state = result["state"]
    if "capture_outputs" in value:
        if state == "PRUNED":
            raise ValueError("A capture tombstone cannot retain output obligations")
        _outputs(value["capture_outputs"])
        result["capture_outputs"] = value["capture_outputs"]
    if state != "PRUNED" and _TOMBSTONE_FIELDS & set(value):
        raise ValueError("An unpruned capture cannot contain tombstone facts")
    if state == "RESERVED":
        if validation != "PENDING" or _COMPONENT_FIELDS & set(value):
            raise ValueError("A reservation must remain PENDING without component declarations")
    elif state == "READY":
        if not {"scene_bytes", "scene_hash", "sculpt"} <= set(value):
            raise ValueError("A READY capture needs its complete declared component pair")
        receipt = validate_receipt({"capture_id": identity, "scene_bytes": value["scene_bytes"],
                                    "scene_hash": value["scene_hash"], "sculpt": value["sculpt"]}, identity)
        storage = value.get("storage", "full")
        if type(storage) is not str or storage not in {"full", "store"}:
            raise ValueError("Invalid BASE storage declaration")
        if validation == "PENDING" and storage != "full":
            raise ValueError("A pending capture cannot declare BASE adoption")
        result.update(scene_bytes=receipt["scene_bytes"], scene_hash=receipt["scene_hash"],
                      sculpt=receipt["sculpt"], storage=storage)
        if "component_seal" in value:
            # Structure only: checkpoint_seal verifies it against the profile key at Restore.
            if not well_formed_seal(value["component_seal"]):
                raise ValueError("Invalid component seal declaration")
            result["component_seal"] = value["component_seal"]
    else:  # _record already checked that this is PRUNED.
        if (_COMPONENT_FIELDS & set(value) or not _TOMBSTONE_FIELDS <= set(value)
                or result["files"] or result["protected"]):
            raise ValueError("A capture tombstone needs removal facts and no payload/protection")
    return result


def validate_record_bytes(payload, identity, previous=None):
    """``validate_record`` of exact record bytes; returns (record, retained) for the next call.

    The capture owner rereads its record for every metadata operation and most
    reads find exactly the bytes the previous one validated. Validation is a
    pure function of the bytes and the identity, so identical bytes reuse that
    result (an independent copy); any other bytes are parsed and validated.
    """
    if previous is not None and previous[0] == payload and previous[1]["id"] == _identity(identity):
        return copy.deepcopy(previous[1]), previous
    record = validate_record(json.loads(payload.decode("utf-8")), identity)
    return record, (payload, copy.deepcopy(record))


def _preserved(before, after, changed):
    if ({key: value for key, value in before.items() if key not in changed}
            != {key: value for key, value in after.items() if key not in changed}):
        raise ValueError("The candidate changes sealed or current journal/protection facts")


def validate_transition(current, candidate, *, kind):
    """Validate explicit before/after DTOs; no transition is executed or authorized.

    The caller still needs its exact owner, fresh current metadata and external
    native/byte verification. A kind string or accepted dictionary grants none.
    """
    if type(kind) is not str or kind not in {
        "publish_bundle", "finish", "invalidate", "adopt_base", "no_runtime", "prune",
        "declare_output", "record_output",
    }:
        raise ValueError("Unknown capture transition kind")
    before = _data(current)
    if type(before) is not dict:
        raise ValueError("A capture transition needs a record")
    before = validate_record(before, before.get("id"))
    identity = before["id"]
    if kind == "no_runtime":
        after = _data(candidate)
        if type(after) is not dict or type(after.get("version")) is not int or after["version"] != 3:
            raise ValueError("No-runtime completion needs an ordinary v3 candidate")
        after = _record(after, identity)
        if (before["state"] != "RESERVED" or before["capture_validation"] != "PENDING"
                or after["state"] != "READY" or after.get("storage", "full") != "full"):
            raise ValueError("No-runtime completion starts only from a pending reservation")
        _count(after.get("scene_bytes"))
        # Only the live native owner can prove the zero-runtime outcome and
        # discharge its real BASE output. This function checks declarations.
        if "capture_outputs" in after:
            raise ValueError("An ordinary completed capture cannot retain output obligations")
        ledger = before.get("capture_outputs")
        if ledger is not None and (set(ledger["roles"]) != {"BASE"}
                                   or ledger["roles"]["BASE"]["created"] is None):
            raise ValueError("No-runtime completion needs exactly its created BASE output")
        if not well_formed_seal(after.get("component_seal")):
            raise ValueError("A completed ordinary capture needs its seal")
        _preserved(before, after, {"version", "state", "capture_validation", "capture_outputs",
                                  "scene_bytes", "capture_ms", "storage", "component_seal"})
        return after
    after = validate_record(candidate, identity)
    if kind in {"declare_output", "record_output"}:
        old, new = before.get("capture_outputs"), after.get("capture_outputs")
        legal = (before["state"] == after["state"] == "RESERVED"
                 and before["capture_validation"] == after["capture_validation"] == "PENDING"
                 and new is not None)
        if legal and kind == "declare_output":
            previous = old["roles"] if old is not None else {}
            added = set(new["roles"]) - set(previous)
            legal = (len(added) == 1 and set(new["roles"]) == set(previous) | added
                     and all(new["roles"][role] == slot for role, slot in previous.items())
                     and new["roles"][next(iter(added))]["created"] is None
                     and (old is None or old["directory"] == new["directory"]))
        elif legal:
            legal = (old is not None and old["directory"] == new["directory"]
                     and set(old["roles"]) == set(new["roles"]))
            if legal:
                changed_roles = [role for role in old["roles"] if old["roles"][role] != new["roles"][role]]
                legal = (len(changed_roles) == 1
                         and old["roles"][changed_roles[0]]["created"] is None
                         and new["roles"][changed_roles[0]]["created"] is not None)
        changed = {"capture_outputs"}
    elif kind == "publish_bundle":
        legal = (before["state"] == "RESERVED" and after["state"] == "READY"
                 and after["capture_validation"] == "PENDING")
        changed = _COMPONENT_FIELDS | {"state", "capture_ms"}
    elif kind == "finish":
        legal = (before["state"] == "READY" and before["capture_validation"] == "PENDING"
                 and after["state"] == "READY" and after["capture_validation"] != "PENDING")
        changed = {"capture_validation"}
    elif kind == "invalidate":
        legal = (before["state"] == "READY" and before["capture_validation"] in _VALID_STATES
                 and after["state"] == "READY" and after["capture_validation"] == "INVALID")
        changed = {"capture_validation"}
    elif kind == "adopt_base":
        legal = (before["state"] == "READY" and before["capture_validation"] in _VALID_STATES
                 and before["storage"] == "full" and after.get("storage") == "store")
        changed = {"storage"}
    else:
        legal = before["state"] != "PRUNED" and after["state"] == "PRUNED"
        # Existing tombstones clear the project path and omit capture timing.
        # Only preservation/omission is structural here, never a new binding.
        legal = legal and after["original_path"] in ("", before["original_path"])
        if "capture_ms" in after:
            legal = (legal and "capture_ms" in before
                     and type(after["capture_ms"]) is type(before["capture_ms"])
                     and after["capture_ms"] == before["capture_ms"])
        changed = _COMPONENT_FIELDS | _TOMBSTONE_FIELDS | {
            "state", "files", "protected", "original_path", "capture_ms", "capture_outputs",
        }
    if not legal:
        raise ValueError("Illegal capture state transition")
    _preserved(before, after, changed)
    return after
