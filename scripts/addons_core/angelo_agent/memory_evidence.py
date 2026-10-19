# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded memory identities, historical provenance and host-only receipts.

JSON attribution is never a capability. The host constructs AdmittedEvidence
only after checking actual source existence and real user origin/approval.
This module has no runtime, storage, Blender, credential or network access.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass

from .memory_spec import MAX_POINTER, pointer, text

PROVENANCE_KINDS = frozenset({"user_message", "approved_update", "host_result"})
SCOPE_TOKEN_PATTERN = re.compile(r"sha256:[a-f0-9]{64}\Z")


def _identity(value, depth=0):
    # Never call repr/str on arbitrary runtime objects or serialize their state.
    if depth > 4:
        raise ValueError("Document/profile identity is too deeply nested")
    if value is None or type(value) is bool:
        return value
    if type(value) is int and value.bit_length() <= 128:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 4096:
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 16:
        return [_identity(item, depth + 1) for item in value]
    if isinstance(value, dict) and len(value) <= 16 and all(isinstance(key, str) and len(key) <= 64 for key in value):
        return {key: _identity(value[key], depth + 1) for key in sorted(value)}
    raise ValueError("Use an explicit bounded document/profile identity, not a runtime object")


def scope_token(value):
    """Opaque identity: filesystem paths, scene names and credentials aren't stored."""
    if value is None:
        return None
    if isinstance(value, str) and SCOPE_TOKEN_PATTERN.fullmatch(value):
        return value
    encoded = json.dumps(_identity(value), sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_provenance(value, allowed=PROVENANCE_KINDS):
    if not isinstance(value, dict) or set(value) != {"kind", "id"} or value.get("kind") not in allowed:
        raise ValueError("Memory provenance must be supplied by the host's actual user/approval/result ledger")
    return {"kind": value["kind"], "id": text(value["id"], MAX_POINTER, 1)}


@dataclass(frozen=True)
class AdmittedEvidence:
    """Runtime-only receipt made by the host after checking existence + origin.

    Plain JSON dictionaries (especially ``approved: true``) are not receipts.
    Do not construct this from model arguments or rehydrate it from memory.
    """
    source: str
    id: str
    profile: str
    document: str
    origin_kind: str
    origin_id: str


def admit_evidence(source, identity, profile_id, document_identity, origin):
    """Host-only: call AFTER the attachment/library/file and user origin check.

    For an attachment, check ReferenceStore.checked_path and locate its real
    user-message pointer. For other assets/URLs require the actual user approval
    receipt, not a model claim or memory's previous approval/provenance fields.
    """
    checked = validate_provenance(origin, {"user_message", "approved_update"})
    return AdmittedEvidence(source, pointer(source, identity), scope_token(profile_id),
                            scope_token(document_identity), checked["kind"], checked["id"])


def checked_handles(value, document, generation):
    """Validate bounded historical handle records for one exact native instance.

    This validates record shape/scope, not object existence or permission. The
    task-memory lifecycle still drops all handles after reload/Undo/restore.
    """
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:16]:
        if (not isinstance(item, dict) or set(item) != {"id", "document", "generation"}
                or item.get("document") != document or item.get("generation") != generation):
            continue
        try:
            identity = text(item["id"], 128, 1)
        except ValueError:
            continue
        result.append({"id": identity, "document": document, "generation": generation})
    return result
