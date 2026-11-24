# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's ``shape_cut`` operation, declared for Angelo's shared typed-operation pattern (``wrapped_ops``; the
agent-verbs design, Part E, revisions 1-6). Plain data: ``wrapped_ops.registry`` registers it (listed in
``wrapped_op_modules.MODULES``) and refuses it if it breaks the contract; the shared runner, once built, runs
``angelo.shape_cut`` with ``EXEC_DEFAULT`` on the program's stage and passes ``checks`` and ``receipt`` its snapshots.

Snapshot shape these functions read (revision 2's facts; adapted to the shared code's accessors when it lands):
``{"target": facts, "created": [facts, ...]}``, where facts are ``{"base": counts, "evaluated": counts,
"open_edges": int, "volume": float or None, "modifiers": [[name, type], ...], "children": [name, ...]}`` and counts
are ``{"vertices", "edges", "faces", "triangles"}``. Checks are pure: they read, never write.
"""

OPERATIONS = ("CUT", "UNION", "INTERSECT", "SLICE", "INSET", "PANEL")
GROWS = ("UNION",)                       # The target's volume goes up; every other operation takes volume away.
TWO_SOLIDS = ("INSET",)                  # A pocket and its cup; every other operation makes one cutter.
VOLUME_TOLERANCE = 1e-9                  # Cubic metres: a change smaller than this is no change.

_VECTOR2 = {"type": "array", "minItems": 2, "maxItems": 2}
PARAMETERS = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "face": {"type": "integer", "minimum": -1, "default": -1,
                 "description": "Face index on the evaluated mesh; -1 uses towards"},
        "towards": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                    "default": [0.0, 0.0, 1.0], "description": "The largest face within 30 degrees of this direction"},
        "shape": {"enum": ["BOX", "CIRCLE"], "default": "BOX"},
        "size": dict(_VECTOR2, items={"type": "number", "minimum": 1e-5, "maximum": 1000.0}, default=[0.1, 0.1],
                     description="BOX: width and height in metres"),
        "radius": {"type": "number", "minimum": 1e-5, "maximum": 1000.0, "default": 0.05,
                   "description": "CIRCLE: metres"},
        "segments": {"type": "integer", "minimum": 3, "maximum": 256, "default": 32},
        "offset": dict(_VECTOR2, items={"type": "number", "minimum": -1000.0, "maximum": 1000.0}, default=[0.0, 0.0],
                       description="From the face's centre in metres, u along its longest edge (or world X, align WORLD)"),
        "rotation": {"type": "number", "default": 0.0, "description": "Radians"},
        "operation": {"enum": list(OPERATIONS), "default": "CUT"},
        "depth": {"type": "number", "minimum": 1e-5, "maximum": 1000.0, "default": 0.01,
                  "description": "Metres below the surface; a union's height above it"},
        "through": {"type": "boolean", "default": False, "description": "CUT and INTERSECT: all the way through"},
        "width": {"type": "number", "minimum": 1e-5, "maximum": 10.0, "default": 0.005,
                  "description": "INSET wall or PANEL line width in metres"},
        "columns": {"type": "integer", "minimum": 1, "maximum": 400, "default": 1},
        "rows": {"type": "integer", "minimum": 1, "maximum": 400, "default": 1},
        "spacing": dict(_VECTOR2, items={"type": "number", "minimum": 0.0, "maximum": 1000.0}, default=[0.0, 0.0],
                        description="Centre to centre in metres between copies"),
        "align": {"enum": ["EDGE", "WORLD"], "default": "EDGE"},
    },
}


def _counts(facts, kind):
    return {key: facts[kind][key] for key in ("vertices", "edges", "faces", "triangles")}


def shape_cut_checks(parameters, before, after):
    """What the turn's verifier states after a shape cut: one check per fact, each ``{"name", "passed", "detail"}``."""
    operation = parameters.get("operation", "CUT")
    old, new = before["target"], after["target"]
    added = [name for name in new["children"] if name not in old["children"]]
    solids = 2 if operation in TWO_SOLIDS else 1
    checks = [
        {"name": "cutters_added", "passed": len(added) == solids,
         "detail": "%d new cutter(s) below the target, %d expected" % (len(added), solids)},
        {"name": "base_mesh_unchanged", "passed": _counts(old, "base") == _counts(new, "base"),
         "detail": "the cut is a live Boolean: the target's own mesh keeps its counts"},
        {"name": "booleans_added",
         "passed": sum(t == "BOOLEAN" for _, t in new["modifiers"]) - sum(t == "BOOLEAN" for _, t in old["modifiers"])
         == solids, "detail": "one Boolean modifier per cutter"},
    ]
    if old["volume"] is not None:
        change = (new["volume"] or 0.0) - old["volume"]
        grows = operation in GROWS
        checks.append({"name": "volume_" + ("grew" if grows else "shrank"),
                       "passed": (change > VOLUME_TOLERANCE) if grows else (change < -VOLUME_TOLERANCE),
                       "detail": "evaluated volume %+.6g m3" % change})
        checks.append({"name": "stays_closed", "passed": old["open_edges"] > 0 or new["open_edges"] == 0,
                       "detail": "%d open edge(s) after a cut of a closed mesh" % new["open_edges"]})
    if operation == "SLICE":
        pieces = [f for f in after.get("created", ()) if f.get("volume") and f["volume"] > VOLUME_TOLERANCE]
        checks.append({"name": "slice_piece", "passed": len(pieces) == 1,
                       "detail": "the slice made %d piece(s) with volume" % len(pieces)})
    return checks


def shape_cut_receipt(after):
    """A few measured facts about the result for the agent: counts only, no paths."""
    target = after["target"]
    return {"evaluated_triangles": target["evaluated"]["triangles"], "open_edges": target["open_edges"],
            "volume": None if target["volume"] is None else round(target["volume"], 9),
            "booleans": sum(t == "BOOLEAN" for _, t in target["modifiers"])}


WRAPPED_OPS = (
    dict(kind="shape_cut", operator="angelo.shape_cut", source="angelo", target="MESH", mode="OBJECT",
         parameters=PARAMETERS, map={name: name for name in PARAMETERS["properties"]},
         reads_only=False,
         creates={"object", "children"},   # Upper bound: cutters below the target; SLICE also makes the sibling piece.
         creates_if={"object": ("operation", {"SLICE"})},   # Only a slice's preview names a new object.
         new_ids=("COLLECTION",),          # "Angelo Cutters": made only if missing, reused after.
         mutates={"modifiers"},            # A cut changes nothing else on the existing target: Booleans only.
         # Bounded by the target's triangles (coordinator ruling 2026-11-15): the cut and its first evaluation took
         # about 2 us a face on the pool VM, the same for a box, an 18-slot grid, a 64-sided circle and a slice
         # (0.63 s at 327,680 faces). input_bound={"triangles": N} is set by wrapped_op_budget.bound_from_timings
         # (p95 on Mac 1 and the Windows VM within the one shared TURN_BUDGET_S; never one of its own).
         input_bound=None,
         job_kind=None,                    # No Background Job does booleans: above the bound the shared code refuses.
         levers=("columns", "rows", "segments"),   # Fewer copies or circle segments: fewer cutter faces to cut.
         checks=shape_cut_checks, receipt=shape_cut_receipt, guide="shape_cut"),
)
