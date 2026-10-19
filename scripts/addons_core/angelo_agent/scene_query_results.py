# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded result shaping, literal search filters and immutable snapshot comparisons."""

from itertools import islice
from .scene_read_values import BudgetExceeded, dump


def bounded_nested(value, output, path):
    if isinstance(value, dict):
        return {k: bounded_nested(v, output, path+"."+k) for k, v in value.items()}
    if isinstance(value, list):
        # Scalar numeric arrays are semantic values, not paginated collections.
        if not value or all(v is None or type(v) in (int, float, bool, str) for v in value) and len(value) <= 16:
            return list(value)
        return [bounded_nested(v, output, path) for v in output.listing(value, path)]
    return value


def section_fallback(obj, section, work, metadata, geometry):
    """Over-budget geometry must not suppress inexpensive material/rig data."""
    if section == "MODIFIERS":
        return metadata.modifiers(obj, work)[0]
    if section == "MATERIALS":
        return metadata.materials(obj, work)
    if section == "RIG":
        return metadata.rig(obj, work)
    if section == "ANIMATION":
        return {"object": metadata.animation(obj, work), "data": metadata.animation(obj.data, work) if obj.data else None,
                "shape_keys": metadata.animation(obj.data.shape_keys, work) if obj.type == "MESH" and obj.data.shape_keys else None,
                "materials": [metadata.animation(s.material, work) for s in obj.material_slots if s.material]}
    if section == "UV":
        return geometry.uv_metadata(obj, work)
    if section == "CAMERA_LIGHT":
        return metadata.camera_light(obj, work)
    return None


def detail_sections(obj, sections, state, relationships, output, budget, metadata, geometry):
    """Bounded detail sections for one fresh capture; over-budget reads are explicit.

    ``relationships`` is the (edges, complete) pair read before the cache lookup,
    and ``budget`` creates a read budget sharing the detail's deadline.
    """
    shaped = {}
    for section in sections:
        field = section.lower()
        if section == "MESH":
            try:
                value = geometry.mesh(obj, budget(), details=True, output=output)
                if value:
                    value.pop("uv_layers", None)
            except BudgetExceeded:
                value = {"source": "LIVE_EDIT_MESH" if obj.mode == "EDIT" else "ORIGINAL_MESH", "unavailable": "work_budget"}
                if obj.type == "MESH" and obj.mode != "EDIT":
                    value.update(vertices=len(obj.data.vertices), edges=len(obj.data.edges), faces=len(obj.data.polygons), corners=len(obj.data.loops))
                output.omitted.append({"field": "mesh", "reason": "work_budget"})
        elif section == "UV":
            try:
                value = state["captured"]["mesh"]["uv_layers"] if state["captured"].get("mesh") else geometry.uv_metadata(obj, budget())
            except BudgetExceeded:
                value = {"unavailable": "work_budget"}
                output.omitted.append({"field": "uv", "reason": "work_budget"})
        elif section == "RELATIONSHIPS":
            value = relationships[0]
            if not relationships[1]:
                output.omitted.append({"field": "relationships", "reason": "work_budget"})
        else:
            value = state["captured"].get(field)
            if value is None:
                try:
                    value = section_fallback(obj, section, budget(), metadata, geometry)
                except BudgetExceeded:
                    value = {"unavailable": "work_budget"}
                    output.omitted.append({"field": field, "reason": "work_budget"})
        shaped[field] = bounded_nested(value, output, field)
    return shaped


def payload_bound(result, max_payload_bytes):
    """Hard byte budget, with explicit section omission rather than hidden loss."""
    truncations = result.get("truncations", [])
    if len(truncations) > 128:
        result.update(truncation_report_total=len(truncations), truncation_report_truncated=True)
        result["truncations"] = truncations[:128] + [{"reason": "truncation_report_limit", "omitted_records": len(truncations)-128}]
    if len(dump(result).encode("utf-8")) <= max_payload_bytes:
        return result
    result["truncated"] = True
    result.setdefault("truncations", []).append({"reason": "payload_byte_budget", "limit": max_payload_bytes})
    if "sections" in result:
        for section in reversed(list(result["sections"])):
            result["sections"][section] = {"unavailable": "payload_byte_budget"}
            if len(dump(result).encode("utf-8")) <= max_payload_bytes:
                return result
    for field in ("objects", "relationships", "changes"):
        if field in result:
            original = len(result[field])
            while result[field] and len(dump(result).encode("utf-8")) > max_payload_bytes:
                result[field].pop()
            result["truncations"].append({"field": field, "returned": len(result[field]), "page_items_before_byte_limit": original,
                                           "recovery": "Query again with a smaller limit; this cursor must not be used to skip omitted entries"})
            result["next_cursor"] = None
            result["page"]["returned"] = len(result[field])
            while result[field] and len(dump(result).encode("utf-8")) > max_payload_bytes:
                result[field].pop()
                result["page"]["returned"] = len(result[field])
                result["truncations"][-1]["returned"] = len(result[field])
    if len(dump(result).encode("utf-8")) > max_payload_bytes:
        return {"read_only": True, "truncated": True, "document_epoch": result.get("document_epoch"),
                "observation_token": result.get("observation_token"), "next_cursor": None,
                "truncations": [{"reason": "payload_byte_budget", "limit": max_payload_bytes}],
                "recovery": "Repeat with a smaller limit or fewer detail sections; no omitted page entries can be skipped"}
    return result


def object_matches(obj, args, selected, visible, collections_of):
    """The literal search filters. Every search coverage applies exactly these."""
    if args["query"].casefold() not in obj.name.casefold() or (args["types"] and obj.type not in args["types"]):
        return False
    if args.get("selected", selected) != selected or args.get("visible", visible) != visible:
        return False
    if "collection" in args and not any(c.name == args["collection"] for c in collections_of(obj)):
        return False
    if "modifier" in args and not any(m.name.casefold() == args["modifier"].casefold() or m.type.casefold() == args["modifier"].casefold() for m in obj.modifiers):
        return False
    if "material" in args and not any(s.material and args["material"].casefold() in s.material.name.casefold() for s in obj.material_slots):
        return False
    return True


def matching_objects(window, args, snapshot, object_handle, scan_limit, collections_of):
    """collections_of is the snapshot pass's reader from membership_reader()."""
    matches = []
    for obj in islice(window.scene.objects, scan_limit):
        row = snapshot["objects"].get(object_handle(obj))
        if row is None:
            break
        summary = row["summary"]
        if object_matches(obj, args, summary["selected"], summary["visible"], collections_of):
            matches.append(summary)
    matches.sort(key=lambda o: int(o["object"].rsplit(":", 1)[1]))
    return matches


def delta_changes(before, after):
    old, new = before["objects"], after["objects"]
    changes = []
    for key in sorted(new.keys() - old.keys()):
        changes.append({"change": "ADDED" if before["membership_complete"] else "NEWLY_OBSERVED", "object": new[key]["summary"]})
    for key in sorted(old.keys() - new.keys()):
        changes.append({"change": "REMOVED" if after["membership_complete"] else "NO_LONGER_OBSERVED", "object": old[key]["summary"]})
    unchanged = 0
    uncertain = 0
    for key in sorted(new.keys() & old.keys()):
        fields = sorted(field for field in new[key]["fields"] if field not in old[key]["fields"] or new[key]["fields"][field] != old[key]["fields"][field])
        exact = old[key]["exact"] and new[key]["exact"]
        if fields:
            changes.append({"change": "MODIFIED", "object": new[key]["summary"], "fields": fields, "exact": exact})
        elif exact:
            unchanged += 1
        else:
            changes.append({"change": "UNCERTAIN", "object": new[key]["summary"], "reason": "baseline_or_current_work_budget"})
        uncertain += not exact
    return changes, unchanged, uncertain
