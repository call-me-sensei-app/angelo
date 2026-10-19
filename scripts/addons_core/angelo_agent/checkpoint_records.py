# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict snapshot metadata validation; legacy records are never rewritten."""

import math
from .checkpoint_policy import CAPTURE_MODES, EVERY_REQUEST, clean_journal
from .checkpoint_seal import well_formed as _well_formed_seal

_FIELDS = {"version", "id", "original_path", "created", "files", "state", "purpose",
           "capture_mode", "capture_ms", "scene_bytes", "protected", "pruned_at", "prune_reason", "bytes_freed", "boundary",
           "storage", "scene_hash", "component_seal"}
_HEX = frozenset("0123456789abcdef")


def _number(value):
    return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 2**53


def _record(value, identity):
    if not isinstance(value, dict) or set(value) - _FIELDS or value.get("id") != identity:
        raise ValueError("Invalid snapshot metadata")
    if type(value.get("version", 1)) is not int or value.get("version", 1) not in {1, 2, 3}:
        raise ValueError("Unsupported snapshot metadata version")
    if not _number(value.get("created")) or type(value.get("original_path")) is not str or len(value["original_path"]) > 16384:
        raise ValueError("Invalid snapshot date or original project path")
    if value.get("version", 1) == 3 and (type(value.get("boundary")) is not int or not 1 <= value["boundary"] <= 2**53 - 1):
        raise ValueError("Invalid snapshot capture boundary")
    if value.get("version", 1) < 3 and "boundary" in value:
        raise ValueError("Legacy snapshots cannot claim ordered capture boundaries")
    result = dict(value)
    result.setdefault("state", "READY" if value.get("scene_bytes") else "RESERVED")
    result.setdefault("purpose", "request")
    result.setdefault("capture_mode", EVERY_REQUEST)
    result.setdefault("protected", False)
    if (any(type(result[key]) is not str for key in ("state", "purpose", "capture_mode")) or
            result["state"] not in {"RESERVED", "READY", "PRUNED"} or result["purpose"] not in {"request", "safety"}
            or result["capture_mode"] not in CAPTURE_MODES or type(result["protected"]) is not bool):
        raise ValueError("Invalid snapshot lifecycle metadata")
    for name in ("scene_bytes", "capture_ms", "pruned_at", "bytes_freed"):
        if name in result and not _number(result[name]):
            raise ValueError("Invalid snapshot measurement")
    if "prune_reason" in result and (type(result["prune_reason"]) is not str or result["prune_reason"] not in {"retention", "incomplete"}):
        raise ValueError("Invalid snapshot removal reason")
    # "store": the scene lives in the deduplicated snapshot store and is
    # verified against scene_hash (BLAKE2b-256 of the exact captured file).
    storage = result.get("storage", "full")
    if type(storage) is not str or storage not in {"full", "store"} or ("scene_hash" in result) != (storage == "store"):
        raise ValueError("Invalid snapshot storage metadata")
    if storage == "store" and (result["state"] != "READY" or result.get("version", 1) != 3 or
                               not result.get("scene_bytes") or type(result["scene_hash"]) is not str or
                               len(result["scene_hash"]) != 64 or not set(result["scene_hash"]) <= _HEX):
        raise ValueError("Invalid deduplicated snapshot metadata")
    if "component_seal" in result and (result.get("version", 1) != 3 or result["state"] != "READY"
                                       or not _well_formed_seal(result["component_seal"])):
        raise ValueError("Invalid snapshot seal metadata")
    files = clean_journal(value.get("files"), purpose=result["purpose"])
    if result.get("version", 1) < 3 and any("sequence" in item for item in files):
        raise ValueError("Legacy snapshots cannot claim ordered file-write versions")
    if not result["protected"] and any(item.get("kind") == "studio_export_safety" for item in files):
        raise ValueError("Large export safety backups must stay protected")
    if result["state"] == "PRUNED" and files:
        raise ValueError("A removed snapshot cannot contain an active journal")
    if result.get("version", 1) == 3 and any("sequence" not in item or item["window"] < result["boundary"] for item in files):
        raise ValueError("Ordered snapshots require ordered per-boundary journals")
    result["files"] = [dict(item) for item in files]
    return result
