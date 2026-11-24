# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's ``scatter`` operation, declared for Angelo's shared typed-operation pattern (``wrapped_ops``; the
agent-verbs design, Part E, revisions 1-4). Plain data with no import until ``wrapped_ops.WrappedOp`` lands: the
shared code turns ``WRAPPED_OPS`` into its declaration, runs ``angelo.scatter`` with ``EXEC_DEFAULT`` on the
program's stage (the surface is the staged target; asset and camera handles are staged and checked unchanged), and
passes ``checks`` and ``receipt`` its snapshots. Contract: doc/angelo/scatter.rst.

The surface and the assets are only read, and the system is a new object running a new node group, so ``mutates``
names nothing (revision 4: only data that existed before the run).

Snapshot shape these functions read (adapted to the shared code's accessors when it lands): ``{"target": {"digest"},
"created": [{"name", "type", "modifiers": [type, ...]}], "scatter": {"instances", "layers": [{"name", "instances"}],
"min_spacing"}}``; ``scatter`` is ``scatter_system.facts`` of the new object with ``min_spacing`` its
``scatter_system.min_spacing`` (1 or more: no two instances closer than the sum of their radii). Checks are pure.
"""

SPACING_TOLERANCE = 1e-3   # Poisson-disk spacing in float32 positions: a hair under 1 is not an overlap.

_HANDLE = {"type": "string", "minLength": 1, "maxLength": 63}
_BAND = {"type": "object", "additionalProperties": False, "required": ["min", "max"],
         "properties": {"min": {"type": "number"}, "max": {"type": "number"}, "soft": {"type": "number", "minimum": 0},
                        "relative": {"const": True}}}
_LAYER = {
    "type": "object", "additionalProperties": False, "required": ["name", "assets", "density"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 64},
        "assets": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"object": dict(_HANDLE, handle={"object_type": None, "role": "read"}),
                           "object_name": _HANDLE, "weight": {"type": "number", "exclusiveMinimum": 0}}}},
        "density": {"type": "number", "exclusiveMinimum": 0, "maximum": 100,
                    "description": "Points per square metre, Poisson-disk spaced"},
        "spacing": {"type": "number", "minimum": 0, "maximum": 10,
                    "description": "Multiplies each asset's footprint radius: 1 lets footprints touch"},
        "rules": {"type": "object", "additionalProperties": False, "properties": {
            "slope": _BAND, "height": _BAND,
            "mask": {"type": "object", "required": ["name"],
                     "properties": {"name": {"type": "string"}, "invert": {"type": "boolean"}}},
            "distance": {"type": "object", "required": ["objects"], "properties": {
                "objects": {"type": "array", "minItems": 1, "maxItems": 16,
                            "items": dict(_HANDLE, handle={"object_type": None, "role": "read"})},
                "keep_away": {"type": "number", "minimum": 0}, "near": {"type": "number", "minimum": 0},
                "falloff": {"type": "number", "minimum": 0}}},
            "noise": {"type": "object", "properties": {"scale": {"type": "number", "exclusiveMinimum": 0},
                                                       "amount": {"type": "number", "minimum": 0, "maximum": 1}}}}},
        "transform": {"type": "object", "additionalProperties": False, "properties": {
            "scale": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "align": {"type": "number", "minimum": 0, "maximum": 1}, "yaw": {"type": "boolean"},
            "tilt": {"type": "number", "minimum": 0, "maximum": 45}}},
        "seed": {"type": "integer", "minimum": 0}}}
BIOMES = ("conifer-slopes", "desert-scrub", "dune-grass", "meadow", "mixed-temperate-forest", "rocky-scree")

PARAMETERS = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "layers": {"type": "array", "maxItems": 12, "items": _LAYER, "default": [],
                   "description": "Layers placed in order (empty with biome)"},
        "biome": {"enum": [""] + list(BIOMES), "default": "",
                  "description": "A preset's layers and rules; biome_assets names each layer's objects"},
        "biome_assets": {"type": "object", "default": {}, "additionalProperties": {
            "type": "array", "minItems": 1, "maxItems": 16, "items": dict(_HANDLE, handle={"object_type": None,
                                                                                           "role": "read"})}},
        "seed": {"type": "integer", "minimum": 0, "maximum": 2 ** 31 - 1, "default": 0},
        "density_scale": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0,
                          "description": "Multiplies every layer's density"},
        "cull_camera": dict(_HANDLE, minLength=0, default="", handle={"object_type": "CAMERA", "role": "read"},
                            description="The camera culling keeps the view of (empty: the scene's)"),
        "cull_viewport": {"type": "boolean", "default": False},
        "cull_render": {"type": "boolean", "default": False},
        "cull_distance": {"type": "number", "minimum": 0, "maximum": 1e6, "default": 0.0,
                          "description": "Drop instances farther from the camera (0: no limit)"},
        "proxy": {"type": "boolean", "default": False,
                  "description": "Draw points in the viewport instead of the assets; renders keep them"},
        "viewport_display": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0,
                             "description": "The share of instances drawn in the viewport"},
        "system_name": {"type": "string", "minLength": 1, "maxLength": 63, "default": "Scatter"},
    },
}

# Structured values reach the operator as canonical JSON in one StringProperty each (revision 1).
JSON_PARAMETERS = ("layers", "biome_assets")


def scatter_checks(parameters, before, after):
    """What the turn's verifier states after a scatter: one check per fact, each ``{"name", "passed", "detail"}``."""
    created = after.get("created", [])
    facts = after.get("scatter", {})
    empty = [layer["name"] for layer in facts.get("layers", ()) if not layer["instances"]]
    spacing = facts.get("min_spacing")
    return [
        {"name": "surface_unchanged", "passed": before["target"]["digest"] == after["target"]["digest"],
         "detail": "the surface is only read"},
        {"name": "one_system", "passed": len(created) == 1 and created[0]["modifiers"] == ["NODES"],
         "detail": "%d new object(s)" % len(created)},
        {"name": "every_layer_placed", "passed": not empty,
         "detail": ("nothing placed for: %s (loosen its rules or raise its density)" % ", ".join(empty)) if empty
         else "%d instances" % facts.get("instances", 0)},
        {"name": "spacing_kept", "passed": spacing is None or spacing >= 1.0 - SPACING_TOLERANCE,
         "detail": "closest pair at %s of their radii" % ("-" if spacing is None else round(spacing, 3))},
    ]


def scatter_receipt(after):
    """What was placed, for the agent: names and counts only."""
    created = after.get("created", [])
    facts = after.get("scatter", {})
    return {"object": created[0]["name"] if created else None, "instances": facts.get("instances", 0),
            "layers": [{"name": layer["name"], "instances": layer["instances"]} for layer in facts.get("layers", ())]}


WRAPPED_OPS = (
    dict(kind="scatter", operator="angelo.scatter", source="angelo", target="MESH", mode="OBJECT",
         parameters=PARAMETERS, map={name: name for name in PARAMETERS["properties"]},
         reads_only=False, creates={"object"}, new_ids={"NODETREE"},
         mutates=(),                       # The surface and assets are read; the system and its group are new.
         # The turn budget is shared (wrapped_op_budget.TURN_BUDGET_S). The bound comes from bound_from_timings on
         # Mac 1 and the Windows VM (20+ trials per size), never guessed; None until those runs.
         input_bound=None,
         job_kind=None,
         levers=("density_scale", "cull_distance", "proxy"),   # Fewer instances, fewer kept, cheaper viewport.
         checks=scatter_checks, receipt=scatter_receipt, guide="scatter"),
)
