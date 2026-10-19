# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Human-owned snapshot settings and conservative, pure retention planning.

Nothing here uses bpy or changes a policy on behalf of a model. The saved
policy is the human's standing retention choice: its limits are enforced
automatically (snapshot_retention.py), oldest unprotected point first. Storage
defaults to 2 GiB; zero means unlimited for every limit. Protected points,
active requests in every chat and required file-restore chains are never
planned for removal, even when that leaves a budget unmet.
"""

import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path
from .background_pauses import pause
from .files import BLOCKED, MAX_FILE, relative_path
from .checkpoint_filesystem import (MAX_METADATA_BYTES, _file_info, _open_file,
    _private_directory, _read_json, _replace_file, _unlink_file, _write_json)

EVERY_REQUEST = "EVERY_REQUEST"
BEFORE_FIRST_EDIT = "BEFORE_FIRST_EDIT"
CAPTURE_MODES = frozenset({EVERY_REQUEST, BEFORE_FIRST_EDIT})
POLICY_VERSION = 2
DEFAULT_MAX_BYTES = 2 * 1024 ** 3
DEFAULT_POLICY = {"version": POLICY_VERSION, "capture_mode": EVERY_REQUEST,
                  "max_count": 0, "max_bytes": DEFAULT_MAX_BYTES, "max_age_days": 0}
# A tombstone has only fixed-size UUID/enums and bounded numeric fields (<1 KiB).
# Reserve that upper bound rather than claiming candidate metadata disappears.
TOMBSTONE_BYTES_BUDGET = 1024
MAX_CREATED_FILE = 150 * 1024 * 1024
JOURNAL_KINDS = frozenset({"file_tool", "studio_export", "studio_export_safety"})


def journal_file_limit(item, *, backup=False):
    """Ordinary backups stay 20 MiB; only host safety copies of exports use 150."""
    kind = item.get("kind", "file_tool") if backup else item.get("current_kind", item.get("kind", "file_tool"))
    if backup and kind != "studio_export_safety":
        return MAX_FILE
    return MAX_CREATED_FILE if kind in {"studio_export", "studio_export_safety"} else MAX_FILE


def clean_journal(value, *, purpose="request"):
    """Validate legacy entries and the bounded, host-owned export marker."""
    if not isinstance(value, list) or len(value) > 2048:
        raise ValueError("Invalid or oversized file journal")
    seen, result = set(), []
    fields = {"root", "path", "existed", "backup", "mode"}
    for item in value:
        if not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - {"kind", "sequence", "window", "phase", "effect_sequence", "effect_kind"}:
            raise ValueError("Invalid file journal entry")
        kind = item.get("kind", "file_tool")
        if type(kind) is not str or kind not in JOURNAL_KINDS or (kind == "studio_export_safety" and purpose != "safety"):
            raise ValueError("Invalid journal kind or unprotected export safety backup")
        if (type(item["root"]) is not str or not Path(item["root"]).is_absolute() or len(item["root"]) > 16384
                or any(part in BLOCKED for part in Path(item["root"]).parts)):
            raise ValueError("Invalid or credential-bearing journal workspace")
        if type(item["path"]) is not str or not item["path"] or len(item["path"]) > 4096:
            raise ValueError("Invalid journal relative path")
        relative_path(item["path"])
        if type(item["backup"]) is not str or not item["backup"].endswith(".bin"):
            raise ValueError("Invalid journal backup identity")
        _identity(item["backup"][:-4])
        if type(item["existed"]) is not bool or type(item["mode"]) is not int or not 0 <= item["mode"] <= 0o777:
            raise ValueError("Invalid journal file state")
        ordered_fields = {"sequence", "window", "phase", "effect_sequence", "effect_kind"}
        ordered = ordered_fields & set(item)
        if ordered:
            if ordered != ordered_fields:
                raise ValueError("Incomplete ordered journal metadata")
            if (type(item["sequence"]) is not int or type(item["window"]) is not int or
                    not 1 <= item["window"] < item["sequence"] <= 2**53 - 1 or
                    type(item["phase"]) is not str or item["phase"] not in {"pending", "ready", "discarded"} or
                    type(item["effect_sequence"]) is not int or not item["sequence"] <= item["effect_sequence"] <= 2**53 - 1 or
                    type(item["effect_kind"]) is not str or item["effect_kind"] not in {"file_tool", "studio_export"}):
                raise ValueError("Invalid ordered journal metadata")
        key = item["root"], item["path"], item.get("window")
        if key in seen:
            raise ValueError("A journal contains duplicate pre-write versions")
        seen.add(key)
        result.append(dict(item))
    return result


def restore_evidence(item):
    """A plan's current-output evidence does not widen its historical backup."""
    value = dict(item)
    current_kind = value.pop("current_kind", value.get("kind", "file_tool"))
    if type(current_kind) is not str or current_kind not in {"file_tool", "studio_export"}:
        raise ValueError("Invalid current-output restore evidence")
    clean = clean_journal([value], purpose="safety")[0]
    clean["current_kind"] = current_kind
    return clean


def clean_policy(value=None):
    """Validate without coercion; booleans, unknown keys and NaN are errors."""
    if value is None:
        return dict(DEFAULT_POLICY)
    if not isinstance(value, dict) or set(value) - DEFAULT_POLICY.keys():
        raise ValueError("Invalid snapshot policy fields")
    result = dict(DEFAULT_POLICY, **value)
    if type(result["version"]) is not int or result["version"] not in {1, POLICY_VERSION}:
        raise ValueError("Unsupported snapshot policy version")
    if result["version"] == 1:
        # Version 1 limits were proposals and zero bytes was simply the only
        # default, not a deliberate unlimited choice. Storage now has a
        # default budget; unlimited remains one explicit setting away.
        if result["max_bytes"] == 0:
            result["max_bytes"] = DEFAULT_MAX_BYTES
        result["version"] = POLICY_VERSION
    if type(result["capture_mode"]) is not str or result["capture_mode"] not in CAPTURE_MODES:
        raise ValueError("Choose Every Request or Before First Approved Edit")
    for key, maximum in (("max_count", 1000000), ("max_bytes", 2**63 - 1), ("max_age_days", 36500)):
        if type(result[key]) is not int or not 0 <= result[key] <= maximum:
            raise ValueError(key + " must be a non-negative integer; zero means unlimited")
    return result


def requires_capture(policy=None, *, before_edit=False):
    return before_edit or clean_policy(policy)["capture_mode"] == EVERY_REQUEST


def capture_scope(policy=None):
    return ("before_request" if clean_policy(policy)["capture_mode"] == EVERY_REQUEST
            else "before_first_approved_edit")


def _identity(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        raise ValueError("Use a canonical snapshot/session UUID")
    return value


def checkpoint_ref(session_id, checkpoint_id):
    """Unambiguous cross-conversation reference used by protected_refs."""
    return _identity(session_id) + "/" + _identity(checkpoint_id)


def _protected_refs(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("Protected snapshots must be a collection of references")
    result = set()
    for value in values:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            result.add(checkpoint_ref(*value))
        elif isinstance(value, str) and len(value.split("/")) == 2:
            result.add(checkpoint_ref(*value.split("/")))
        else:
            raise ValueError("A protected reference needs its session and snapshot UUIDs")
    return sorted(result)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


class CheckpointPolicyStore:
    """Private profile settings. Only explicit human UI confirmation may save."""

    def __init__(self, root):
        supplied = Path(root).absolute()
        if supplied.is_symlink():
            raise ValueError("Snapshot policy storage cannot be a directory link")
        self.root = supplied.resolve()
        self.error = ""

    def load(self):
        self.error = ""
        try:
            with _private_directory(self.root) as folder:
                return clean_policy(_read_json(folder, "checkpoint-policy.json"))
        except FileNotFoundError:
            return clean_policy()
        except (OSError, ValueError, TypeError, RecursionError) as error:
            self.error = ("Snapshot settings could not be read; Every Request applies and automatic removal is "
                          "paused until the settings are saved again: " + str(error))
            return clean_policy()

    def save(self, value, *, user_confirmed=False):
        if user_confirmed is not True:
            raise ValueError("A human must explicitly confirm snapshot policy changes")
        settings = clean_policy(value)
        with _private_directory(self.root, create=True) as folder:
            _write_json(folder, "checkpoint-policy.json", settings)
        self.error = ""
        return settings


def scene_usable(record):
    """Structural candidacy; actual byte/native admission belongs to consumers."""
    return (record.get("state") == "READY" and record.get("scene_usable", True) is True
            and (record.get("version") != 4
                 or record.get("capture_validation") in {"VALIDATED", "INTERRUPTED_VALID"}))


def _pressure_record_bytes(item):
    """Unavailable capture bytes are real usage, not a reason to evict history.

    Keep the prior physical-byte accounting for legacy inventory fixtures that
    do not supply an explicit usability fact. Tombstone metadata remains normal
    overhead after removal, matching the reserve in a new removal plan.
    """
    if item["state"] == "PRUNED":
        return item["bytes"]
    if (item.get("version") == 4 or "scene_usable" in item) and not scene_usable(item):
        return 0
    return item["bytes"]


def _dependencies(records, candidates, reasons):
    """Candidates owning write versions a retained restore point still needs."""
    proposed = {item["ref"] for item in candidates}
    anchors = [item.get("boundary") or 0 for item in records if item["ref"] not in proposed and
               item["state"] != "PRUNED" and
               (item["state"] == "READY" or item["files"] or item["ref"] in reasons)]
    earliest = min(anchors) if anchors else None
    return [item for item in candidates if earliest is not None and any(
        entry.get("phase", "ready") != "discarded" and entry.get("sequence", 0) >= earliest
        for entry in item["files"])]


def plan_retention(inventory, policy=None, *, protected_refs=(), now=None, accounting=None, preferred_refs=()):
    """Plan oldest-first removal, preserving every retained restore dependency.

    Journals at/after the earliest retained restore point are conservatively kept,
    even when another backup might suffice. This avoids journal compaction and
    guarantees no retained scene point loses its cross-chat file restore chain.
    A reinstated dependency can expose a later candidate, so selection repeats
    until no candidate is a dependency. Protected/required entries can make a
    requested budget impossible to meet.

    ``accounting`` (snapshot_accounting.StoreAccounting) adds deduplicated store
    bytes: a candidate's bytes are its own files plus the shared chunks its
    removal actually releases after the earlier candidates. Eviction pressure
    counts only admitted scene bytes; unavailable captures remain in actual
    usage and can leave the budget unmet without evicting unrelated history.
    ``preferred_refs``
    (snapshots already superseded by a matching git commit) are removed first.
    """
    policy = clean_policy(policy)
    refs = _protected_refs(protected_refs)
    preferred = set(_protected_refs(preferred_refs))
    now = time.time() if now is None else now
    if type(now) not in {int, float} or not math.isfinite(now) or not 0 <= now <= 2**53:
        raise ValueError("Retention inspection needs a finite non-negative time")
    records = sorted(inventory["checkpoints"], key=lambda item: (item["created"], item["ref"]))
    reasons = {item["ref"]: ("active/request reference" if item["ref"] in refs else
               "safety/protected snapshot") for item in records
               if item["ref"] in refs or item["protected"] or item["purpose"] == "safety"}
    for item in records:
        if item.get("capture_active") is True:
            reasons[item["ref"]] = "active capture pin"
        if any("sequence" not in entry for entry in item["files"]):
            reasons[item["ref"]] = "legacy journal ordering cannot be proved"
    usable = [item for item in records if scene_usable(item)]
    order = sorted(usable, key=lambda item: (item["ref"] not in preferred, item["created"], item["ref"]))
    store_bytes = accounting.total_bytes if accounting else 0
    actual_bytes = sum(item["bytes"] for item in records) + store_bytes
    pressure_bytes = (sum(_pressure_record_bytes(item) for item in records) +
                      (accounting.pressure_bytes if accounting else 0))
    age = policy["max_age_days"] * 86400
    while True:
        simulation = accounting.simulation() if accounting else None
        pressure_simulation = accounting.pressure_simulation() if accounting else None
        remaining_count = len(usable)
        remaining_bytes, remaining_pressure_bytes = actual_bytes, pressure_bytes
        candidates = []
        for item in order:
            pause()  # The snapshot worker's probe plans the whole history: let the main thread in (background_pauses).
            over = ((policy["max_count"] and remaining_count > policy["max_count"]) or
                    (policy["max_bytes"] and remaining_pressure_bytes > policy["max_bytes"]) or
                    (age and item["created"] < now - age))
            if over and item["ref"] not in reasons:
                released = simulation.release(item["ref"]) if simulation else 0
                pressure_released = pressure_simulation.release(item["ref"]) if pressure_simulation else 0
                candidates.append(dict(item, bytes=item["bytes"] + released, store_bytes=released))
                remaining_count -= 1
                remaining_bytes -= item["bytes"] + released - TOMBSTONE_BYTES_BUDGET
                remaining_pressure_bytes -= item["bytes"] + pressure_released - TOMBSTONE_BYTES_BUDGET
        dependencies = _dependencies(records, candidates, reasons)
        if not dependencies:
            break
        for item in dependencies:
            reasons[item["ref"]] = "required by a retained file restore chain"
    if inventory["issues"]:
        candidates = []  # Unknown journals must not be silently dropped.
        remaining_count, remaining_bytes = len(usable), inventory["total_bytes"] + store_bytes
        remaining_pressure_bytes = pressure_bytes
    removed = {candidate["ref"] for candidate in candidates}
    plan = {"version": 1, "kind": "retention", "policy": policy, "protected_refs": refs,
            "preferred_refs": sorted(preferred), "planned_at": now, "inventory_token": inventory["token"],
            "candidates": [{key: item[key] for key in ("ref", "session_id", "id", "bytes", "store_bytes", "fingerprint")}
                           for item in candidates],
            "candidate_bytes": sum(item["bytes"] for item in candidates),
            "store_bytes": store_bytes,
            "pressure_bytes": pressure_bytes, "unavailable_bytes": actual_bytes - pressure_bytes,
            "remaining_pressure_bytes": remaining_pressure_bytes,
            "remaining_unavailable_bytes": remaining_bytes - remaining_pressure_bytes,
            "remaining_count": remaining_count, "remaining_bytes": remaining_bytes,
            "remaining_bytes_is_upper_bound": True, "tombstone_reserve_bytes": len(candidates) * TOMBSTONE_BYTES_BUDGET,
            "protected": [{"ref": ref, "reason": reason} for ref, reason in sorted(reasons.items())],
            "blocked": bool(inventory["issues"]),
            "budget_met": not ((policy["max_count"] and remaining_count > policy["max_count"]) or
                               (policy["max_bytes"] and remaining_bytes > policy["max_bytes"]) or
                               (age and any(item["created"] < now - age and item["ref"] not in removed
                                            for item in usable)))}
    plan["token"] = _digest(plan)
    return plan
