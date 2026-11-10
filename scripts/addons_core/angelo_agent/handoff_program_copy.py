# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded counterpart of the existing typed-program model copy, presentation only."""

from . import program_model_result as P
from .handoff_json import INVALID, UNSUPPORTED, WORK_LIMIT
from .handoff_projection import compact as primitive_copy
from .handoff_receipts import Refused


def copied(mapping, reader, without=()):
    return {key: value for key, value in reader.items(mapping) if key not in without}


def _name(row, reader, key="name"):
    value = reader.read(row, key)
    if value is not None and type(value) is not str:
        raise Refused(UNSUPPORTED)
    return value


def _bounds(value, reader):
    from .object_bounds import rounded
    if type(value) is not list or len(value) != 2:
        return None
    for corner in reader.rows(value):
        if type(corner) is not list or len(corner) != 3:
            return None
        for number in reader.rows(corner):
            kind = type(number)
            if kind is str:
                if number and not reader.budget.scan(len(number)):
                    raise Refused(WORK_LIMIT)
            elif kind is int:
                if not reader.budget.scan(max(1, (number.bit_length() + 3) // 4)):
                    raise Refused(WORK_LIMIT)
            elif kind is not float and kind is not bool:
                return None
    try:
        return rounded(value)  # Exactly six admitted builtin scalars; never a raw iterator or callback.
    except (OverflowError, ValueError):
        raise Refused(INVALID) from None


def _geometry(row, reader):
    if type(row) is not dict:
        return row
    output = copied(row, reader, P.GEOMETRY_DROPPED)
    bounds = _bounds(reader.read(row, "bounds"), reader) if reader.present(row, "bounds") else None
    counts = reader.read(row, "mesh_counts")
    if type(counts) is dict and reader.read(counts, "vertices") == 0 and bounds is not None and bounds[0] == bounds[1]:
        bounds = None
        output.pop("bounds", None)
    instances = reader.read(row, "instances")
    if type(instances) is dict:
        own = (reader.read(instances, "own_geometry") is True if reader.present(instances, "unknown")
               else reader.read(instances, "own_geometry", True))
        if not own:
            bounds = None
            output.pop("bounds", None)
    if bounds is not None:
        output["bounds"] = bounds
    if type(counts) is dict:
        for key in ("vertices", "faces"):
            if reader.present(counts, key):
                output[key] = reader.read(counts, key)
    return output


def _delta(delta, reader, committed):
    without = (*P.DELTA_SCOPES, *P.DELTA_LISTS) if committed else P.DELTA_SCOPES
    output, rows = copied(delta, reader, without), []
    values = reader.read(delta, "geometry")
    if type(values) is list:
        rows = [_geometry(row, reader) for row in reader.rows(values)]
        output["geometry"] = rows
    return output, rows


def _live(output, rows, reader):
    live = output.get("live_revisions")  # output is the string-key dictionary built here.
    if type(live) is not dict:
        return set(), set()
    by_handle, created, created_counts, live_counts = {}, {}, {}, {}
    for row in reader.rows(rows):
        if type(row) is not dict:
            continue
        handle = _name(row, reader, "handle")
        if handle is not None:
            by_handle[handle] = row
        if not reader.present(row, "handle"):
            name = _name(row, reader)
            created.setdefault(name, row)
            created_counts[name] = created_counts.get(name, 0) + 1
    values = reader.read(live, "objects") or []
    for row in reader.rows(values):
        if type(row) is dict:
            name = _name(row, reader)
            live_counts[name] = live_counts.get(name, 0) + 1
    merged, names, shown = set(), set(), []
    for row in reader.rows(values):
        extra = None
        if type(row) is dict:
            extra = by_handle.get(_name(row, reader, "handle"))
            name = _name(row, reader)
            if (extra is None and reader.read(row, "role") == "CREATED" and live_counts.get(name) == 1
                    and created_counts.get(name) == 1):
                extra = created[name]
                if name is None:
                    raise Refused(UNSUPPORTED)
                names.add(name)
        if extra is not None:
            row_type = _name(row, reader, "type")
            extra_type = reader.read(extra, "type", row_type)
            if extra_type is not None and type(extra_type) is not str:
                raise Refused(UNSUPPORTED)
            if extra_type == row_type:
                row = dict(copied(row, reader), **copied(extra, reader, ("name", "type", "handle")))
                merged.add(id(extra))
        shown.append(row)
    output["live_revisions"] = dict(copied(live, reader, ("note",)), objects=shown)
    return merged, names


def _checks(values, reader):
    shown = []
    for row in reader.rows(values):
        if type(row) is not dict:
            shown.append(row)
            continue
        inner = reader.read(row, "checks")
        positive = (reader.read(row, "passed") is True and reader.read(row, "success") is True
                    and reader.read(row, "truncated") is not True and type(inner) is list and bool(inner))
        if positive:
            for key, _value in reader.items(row):
                if key not in P.CHECK_WRAPPER:
                    positive = False
                    break
        if positive:
            for item in reader.rows(inner):
                if type(item) is not dict or reader.read(item, "passed") is not True:
                    positive = False
                    break
        if not positive:
            shown.append(row)
            continue
        for item in reader.rows(inner):
            entry = {}
            for key, value in reader.items(item):
                if key in P.CHECK_STATUS:
                    continue
                if key == "observed":
                    projected = primitive_copy(value, budget=reader.budget, observed_numbers=True)
                    if projected.reason:
                        raise Refused(projected.reason)
                    value = projected.value
                entry[key] = value
            shown.append(entry)
    return shown


def _operation(row, finals, reader):
    if type(row) is not dict:
        return row
    target = reader.read(row, "target")
    identity = target if type(target) is str else _name(row, reader, "alias")
    final = finals.get(identity)
    same = final is not None
    if same:
        for key in ("vertices", "faces"):
            if reader.present(row, key):
                before, after = reader.read(row, key), reader.read(final, key)
                if ((before is not None and type(before) is not int and type(before) is not float)
                        or (after is not None and type(after) is not int and type(after) is not float)):
                    raise Refused(UNSUPPORTED)
                if before != after:
                    same = False
    return {key: value for key, value in reader.items(row)
            if not (final is not None and key == "object_type") and not (same and key in ("vertices", "edges", "faces"))
            and not (key == "created" and value is True) and not (key == "target" and value is None)}


def compact(tool, result, reader):
    """Same finished-program copy, with bounded traversal and linear identity/name joins."""
    if (tool not in P.TOOLS or type(result) is not dict or reader.read(result, "error")
            or reader.read(result, "success") is not True or reader.read(result, "status") not in ("committed", "preview")):
        return result
    output, rows = copied(result, reader), []
    delta = reader.read(result, "delta")
    if type(delta) is dict:
        output["delta"], rows = _delta(delta, reader, type(reader.read(result, "changes")) is dict)
    merged, names = _live(output, rows, reader) if reader.read(result, "status") == "committed" else (set(), set())
    if type(output.get("delta")) is dict and type(output["delta"].get("geometry")) is list:
        kept = [row for row in reader.rows(rows) if id(row) not in merged]
        output["delta"] = (dict(output["delta"], geometry=kept) if kept
                           else copied(output["delta"], reader, ("geometry",)))
    changes = output.get("changes")
    if type(changes) is dict:
        changes = copied(changes, reader, ("scope",))
        added = changes.get("added")
        if type(added) is list and names:
            added = [row for row in reader.rows(added) if type(row) is not dict or _name(row, reader) not in names]
            changes = dict(changes, added=added) if added else copied(changes, reader, ("added",))
        output["changes"] = changes
    if type(output.get("checks")) is list:
        output["checks"] = _checks(output["checks"], reader)
    if type(output.get("operation_results")) is list:
        finals = {}
        live = reader.read(output, "live_revisions")
        live = live if type(live) is dict else {}
        for values in (rows, (live.get("objects") or []) if merged else []):
            for row in reader.rows(values):
                if type(row) is dict and reader.present(row, "bounds"):
                    for key in ("handle", "alias"):
                        identity = _name(row, reader, key)
                        if identity is not None:
                            finals[identity] = row
        output["operation_results"] = [_operation(row, finals, reader) for row in reader.rows(output["operation_results"])]
    if rows:
        output["detail"] = P.DETAIL if merged else P.PREVIEW_DETAIL
    return output
