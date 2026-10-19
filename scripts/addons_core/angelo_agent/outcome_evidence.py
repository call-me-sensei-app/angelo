# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a tool result is positive evidence of a scene change and of a read-back.

Moved out of telemetry.py unchanged (``telemetry.scene_outcome_evidence``).
"""


def scene_outcome_evidence(result):
    """Positive fresh native outcome/read-back, not intent or historical output.

    The controller additionally requires a current executed scene-mutation call.
    These timestamps measure host receipt, not an inferred kernel write instant.
    """
    if not isinstance(result, dict):
        return False, False
    changes = result.get("changes") if isinstance(result.get("changes"), dict) else {}
    status = result.get("status")
    if (result.get("error") or result.get("success") is False or result.get("partial_changes_possible")
            or result.get("serialization_failed") or result.get("uncertain") or changes.get("uncertain")
            or result.get("replayed") or result.get("final") is False
            or status is not None and not isinstance(status, str)
            or status in {"preview", "progress", "discarded", "cancelled", "failed", "interrupted", "rejected"}):
        return False, False
    measured_change = False
    for kind in ("added", "removed", "modified"):
        records = changes.get(kind)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("name"), str) or not record["name"]:
                continue
            fields = record.get("fields")
            if kind != "modified" or isinstance(fields, list) and any(isinstance(field, str) and field for field in fields):
                measured_change = True
    changed = result.get("scene_changed") is True or measured_change
    if result.get("scene_changed") is False:
        changed = False
    readback = result.get("verified") if isinstance(result.get("verified"), dict) else {}
    verified = any(type(readback.get(key)) in {int, float} and readback[key] >= 0
                   for key in ("object_count", "vertices", "faces", "write_count", "added_count"))
    verified |= any(isinstance(readback.get(key), list) and bool(readback[key])
                    for key in ("location", "dimensions", "bounds_world"))
    verified |= any(isinstance(readback.get(key), str) and bool(readback[key])
                    for key in ("scene", "object", "mesh_revision", "revision"))
    verified |= readback.get("locations_read_back") is True
    verified |= measured_change
    return changed, verified
