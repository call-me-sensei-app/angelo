# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A model's copy of a finished typed program's result: what it acts on, and one short row per object.

Recorded (GPT 6.1 Sol and Astra): a program that added a
62-object batch returned 13,319 tokens and Codex cut the result; 36 of 138 recorded program results were over
16 KB. Each object was listed three times (``live_revisions``, ``changes`` and ``delta.geometry``, the last with
full-precision bounds, a 16-number matrix and mesh counts), each passed check repeated its verdict four times,
and each operation read-back repeated its object's counts.

``compact`` keeps every handle and post-commit revision, every check and every failure, and gives each object
one row:

* a ``live_revisions`` row (handle, name, type, role, alias, revision) gains its object's geometry: world
  ``bounds`` to 1 mm (``object_bounds``), vertex and face counts for a mesh, and a light's or camera's settings,
  as ``delta.geometry`` stated them; that geometry row and the object's ``changes.added`` row are then left out.
  Rows are matched by identity, never by a name alone: an input's geometry row carries its handle (its logical
  name is the name before a rename), and a created object's row joins the one CREATED live row of that name only
  when the name is unique on both sides. An object without a live row (a preview, an input only read) keeps
  its geometry row, without the matrix;
* ``delta``'s added, modified and removed rows are left out of a committed result, whose ``changes`` lists the
  same objects; its two scope sentences, which the tool states, are left out too;
* a check whose verdict passed is its inner row(s) without the repeated status, observed numbers to seven
  significant digits (float32's own precision); any other check is kept whole;
* an operation read-back without the type, and without counts equal to its object's final ones, and without a
  ``created: true`` or ``target: null`` that the role and the program state;
* a committed result's ``live_revisions`` without its constant note (the instructions state how to bind them).

A failed result is never compacted. Everything left out is in the result the chat keeps (the activity row and
the journal); ``detail`` says what the rows hold and, once they are live rows, that ``angelo_scene_object`` returns
an object's full transform and topology by its handle. Pure dictionaries; the raw result is never modified.
"""

import math

from .object_bounds import rounded

TOOLS = frozenset({"angelo_scene_program", "angelo_accept_preview", "angelo_apply_recipe"})
DETAIL = ("Compact: an object's row gives its world bounds to 1 mm and counts; angelo_scene_object returns its full "
          "transform and topology by handle")
PREVIEW_DETAIL = "Compact: geometry rows give world bounds to 1 mm and counts, without matrices"
GEOMETRY_DROPPED = ("matrix", "mesh_counts")
DELTA_SCOPES = ("scope", "geometry_scope")
DELTA_LISTS = ("added", "removed", "modified")
ECHO = ("object_type", "vertices", "edges", "faces")
CHECK_STATUS = ("status",)
# A passed check row's own keys; a row with any other, or a truncated one, is kept whole.
CHECK_WRAPPER = frozenset({"success", "passed", "status", "checks", "truncated", "read_only", "scope", "kind",
                           "message"})
OBSERVED_DIGITS = 7  # Significant digits: a float32 value's own precision.


def compact(tool, result):
    """``result``'s compact model copy (module docstring), or ``result`` itself when nothing applies."""
    if (tool not in TOOLS or not isinstance(result, dict) or result.get("error") or result.get("success") is not True
            or result.get("status") not in ("committed", "preview")):
        return result
    output = dict(result)
    rows = []
    delta = output.get("delta")
    if isinstance(delta, dict):
        output["delta"], rows = _delta(delta, committed=isinstance(output.get("changes"), dict))
    # Only a commit's live rows are the objects' new state: a preview's are its declared inputs, unchanged until Accept.
    merged, names = _live(output, rows) if result.get("status") == "committed" else (set(), set())
    if isinstance(output.get("delta"), dict) and isinstance(output["delta"].get("geometry"), list):
        kept = [row for row in rows if id(row) not in merged]
        output["delta"] = dict(output["delta"], geometry=kept) if kept else {
            key: value for key, value in output["delta"].items() if key != "geometry"}
    changes = output.get("changes")
    if isinstance(changes, dict):
        changes = {key: value for key, value in changes.items() if key != "scope"}
        added = changes.get("added")
        if isinstance(added, list) and names:
            added = [row for row in added if not (isinstance(row, dict) and row.get("name") in names)]
            changes = dict(changes, added=added) if added else {key: value for key, value in changes.items()
                                                                if key != "added"}
        output["changes"] = changes
    if isinstance(output.get("checks"), list):
        output["checks"] = _checks(output["checks"])
    if isinstance(output.get("operation_results"), list):
        finals = {}  # An object's final row by identity: an input's handle, a created object's $alias.
        live = (output.get("live_revisions") or {}).get("objects") or () if merged else ()
        for row in [*rows, *live]:
            if isinstance(row, dict) and "bounds" in row:
                finals.update((key, row) for key in (row.get("handle"), row.get("alias")) if isinstance(key, str))
        output["operation_results"] = [_operation(row, finals) for row in output["operation_results"]]
    if rows:
        output["detail"] = DETAIL if merged else PREVIEW_DETAIL
    return output


def _delta(delta, committed):
    """The delta without its scope sentences (and, committed, its lists), and its compact geometry rows."""
    output = {key: value for key, value in delta.items()
              if key not in DELTA_SCOPES and not (committed and key in DELTA_LISTS)}
    rows = []
    if isinstance(delta.get("geometry"), list):
        rows = output["geometry"] = [_geometry(row) for row in delta["geometry"]]
    return output, rows


def _geometry(row):
    if not isinstance(row, dict):
        return row
    output = {key: value for key, value in row.items() if key not in GEOMETRY_DROPPED}
    bounds = rounded(row["bounds"]) if "bounds" in row else None
    counts = row.get("mesh_counts")
    if isinstance(counts, dict) and counts.get("vertices") == 0 and bounds is not None and bounds[0] == bounds[1]:
        bounds = None  # No vertices and a point box: nothing to measure (a modifier's geometry has a real box).
        output.pop("bounds", None)
    if bounds is not None:
        output["bounds"] = bounds
    if isinstance(counts, dict):
        output.update({key: counts[key] for key in ("vertices", "faces") if key in counts})
    return output


def _live(output, rows):
    """Each live row with its object's geometry row, matched by identity: ``(ids of merged geometry rows, names of
    merged CREATED rows)``."""
    live = output.get("live_revisions")
    if not isinstance(live, dict):
        return set(), set()
    geometry = [row for row in rows if isinstance(row, dict)]
    by_handle = {row["handle"]: row for row in geometry if isinstance(row.get("handle"), str)}
    created = [row for row in geometry if "handle" not in row]
    lives = [row for row in live.get("objects") or () if isinstance(row, dict)]
    unique = lambda name, group: sum(1 for row in group if row.get("name") == name) == 1  # noqa: E731
    merged, names, shown = set(), set(), []
    for row in live.get("objects") or ():
        extra = None
        if isinstance(row, dict):
            extra = by_handle.get(row.get("handle"))
            if extra is None and row.get("role") == "CREATED" and unique(row.get("name"), lives):
                extra = next((item for item in created if item.get("name") == row.get("name")), None)
                extra = extra if extra is not None and unique(row.get("name"), created) else None
                if extra is not None:
                    names.add(row["name"])
        if extra is not None and extra.get("type", row.get("type")) == row.get("type"):
            row = dict(row, **{key: value for key, value in extra.items() if key not in ("name", "type", "handle")})
            merged.add(id(extra))
        shown.append(row)
    output["live_revisions"] = dict({key: value for key, value in live.items() if key != "note"}, objects=shown)
    return merged, names


def _checks(rows):
    shown = []
    for row in rows:
        inner = row.get("checks") if isinstance(row, dict) else None
        passed = (isinstance(row, dict) and row.get("passed") is True and row.get("success") is True
                  and row.get("truncated") is not True and set(row) <= CHECK_WRAPPER
                  and isinstance(inner, list) and inner
                  and all(isinstance(item, dict) and item.get("passed") is True for item in inner))
        if not passed:
            shown.append(row)
            continue
        shown.extend({key: (_numbers(value) if key == "observed" else value) for key, value in item.items()
                      if key not in CHECK_STATUS} for item in inner)
    return shown


def _numbers(value):
    """``value`` with every float to ``OBSERVED_DIGITS`` significant digits."""
    if isinstance(value, float):
        return float("%.*g" % (OBSERVED_DIGITS, value)) if math.isfinite(value) else value
    if isinstance(value, list):
        return [_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _numbers(item) for key, item in value.items()}
    return value


def _operation(row, finals):
    """A read-back without what its object's final row states: its type, and its counts when they are the final ones."""
    if not isinstance(row, dict):
        return row
    final = finals.get(row.get("target") if isinstance(row.get("target"), str) else row.get("alias"))
    same = isinstance(final, dict) and all(row.get(key) == final.get(key)
                                           for key in ("vertices", "faces") if key in row)
    return {key: value for key, value in row.items()
            if not (final is not None and key == "object_type") and not (same and key in ("vertices", "edges", "faces"))
            and not (key == "created" and value is True) and not (key == "target" and value is None)}
