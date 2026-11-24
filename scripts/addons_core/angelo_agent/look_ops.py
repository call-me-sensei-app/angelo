# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's ``camera_look`` operation, declared for Angelo's shared typed-operation pattern (``wrapped_ops``; the
agent-verbs design, Part E, revisions 1-4). Plain data with no import until ``wrapped_ops.WrappedOp`` lands: the
shared code turns ``WRAPPED_OPS`` into its declaration, runs ``angelo.camera_look`` with ``EXEC_DEFAULT`` on the
program's stage and passes ``checks`` and ``receipt`` its snapshots. Contract: doc/angelo/camera_look.rst.

Scene-level (no target object): the camera is a write handle. It changes data that existed before the run: the
camera (bokeh, and its focal length's driver with breathing), the scene (white balance, the compositing group's
assignment), the compositing group's nodes and the drivers on it; the look's own node group is new.

Snapshot shape these functions read (adapted to the shared code's accessors when it lands): ``{"look": {"preset",
"stages", "exposure_stops"?} or None, "compositor": {"look_nodes", "output_through_look"}, "drivers": [{"path", "simple"}]}``, read from
the scene's ``look_system.STATE``, its compositing group and the drivers on it and on the camera. Checks are pure.
"""

PRESETS = ("CLEAN", "VINTAGE", "ANAMORPHIC", "DOCUMENTARY", "WARM_PORTRAIT", "BLEACH_BYPASS", "NOIR")
STAGES = ("lens", "vignette", "glare", "halation", "film", "grain")

EXPOSURE_SETTING = {"f_stop": {"type": "number", "minimum": 0.5, "maximum": 128.0},
                    "shutter": {"type": "number", "minimum": 1.0 / 64000.0,
                                "maximum": 3600.0, "description": "seconds"},
                    "iso": {"type": "number", "minimum": 1.0, "maximum": 1000000.0}}
PARAMETERS = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "preset": {"enum": list(PRESETS), "default": "CLEAN",
                   "description": "The look; CLEAN with nothing else takes an applied look off"},
        "stages": {"type": "object", "default": {}, "additionalProperties": False,
                   "properties": {name: {"type": "object", "additionalProperties": {"type": "number"}}
                                  for name in STAGES},
                   "description": "Values over the preset's, per stage (look_spec.STAGES)"},
        "camera_values": {"type": "object", "default": {}, "additionalProperties": False,
                          "properties": {"aperture_blades": {"type": "integer", "minimum": 0, "maximum": 16},
                                         "aperture_rotation": {"type": "number", "minimum": -3.1416,
                                                               "maximum": 3.1416},
                                         "aperture_ratio": {"type": "number", "minimum": 0.25, "maximum": 4.0}}},
        "breathing": {"type": "boolean", "default": False,
                      "description": "The focal length follows the focus distance (a driver: hand edits of the "
                                     "focal length wait until the look is taken off)"},
        "breathing_amount": {"type": "number", "minimum": -0.5, "maximum": 0.5, "default": 0.0,
                             "description": "0: the preset's coefficient"},
        "exposure": {"type": "object", "default": {}, "additionalProperties": False,
                     "properties": {"reference": {"type": "object", "additionalProperties": False,
                                                  "required": ["f_stop", "shutter", "iso"],
                                                  "properties": EXPOSURE_SETTING},
                                    "setting": {"type": "object", "additionalProperties": False,
                                                "required": ["shutter", "iso"], "properties": EXPOSURE_SETTING}},
                     "dependentRequired": {"reference": ["setting"], "setting": ["reference"]},
                     "description": "The film's exposure in stops from reference settings (the scene's light is "
                                    "right for them) to the camera's; the setting's f_stop defaults to the "
                                    "camera's"},
        "camera": {"type": "string", "minLength": 0, "maxLength": 63, "default": "",
                   "handle": {"object_type": "CAMERA", "role": "write"},
                   "description": "The camera (empty: the scene's)"},
        "show_in_viewport": {"type": "boolean", "default": False,
                             "description": "The 3D views' camera view runs the compositor (a view setting)"},
    },
}
JSON_PARAMETERS = ("stages", "camera_values", "exposure")


def camera_look_checks(parameters, before, after):
    """What the turn's verifier states after a look: one check per fact, each ``{"name", "passed", "detail"}``."""
    look = after.get("look")
    compositor = after.get("compositor", {})
    built = bool(look and look.get("stages"))
    drivers = after.get("drivers", [])
    python = [item["path"] for item in drivers if not item["simple"]]
    return [
        {"name": "one_look_node", "passed": compositor.get("look_nodes", 0) == (1 if built else 0),
         "detail": "%d look node(s) for %d stage(s)" % (compositor.get("look_nodes", 0),
                                                       len(look["stages"]) if look else 0)},
        {"name": "output_through_look", "passed": compositor.get("output_through_look", False) == built,
         "detail": "the compositor's output reads the look" if built else "no look in the compositor"},
        {"name": "drivers_need_no_python", "passed": not python,
         "detail": ("needs Python: " + ", ".join(python)) if python else "%d simple driver(s)" % len(drivers)},
    ]


def camera_look_receipt(after):
    """The look now on the scene: names only."""
    look = after.get("look") or {}
    receipt = {"preset": look.get("preset", "CLEAN"), "stages": list(look.get("stages", ())),
               "camera": look.get("camera"), "breathing": look.get("breathing", 0.0)}
    if "exposure_stops" in look:
        receipt["exposure_stops"] = look["exposure_stops"]
    return receipt


WRAPPED_OPS = (
    dict(kind="camera_look", operator="angelo.camera_look", source="angelo", target=None, mode="OBJECT",
         parameters=PARAMETERS, map={name: name for name in PARAMETERS["properties"]},
         reads_only=False, creates=None, new_ids={"NODETREE"},
         mutates={"camera", "scene", "node_trees", "animation"},
         # The turn budget is shared (wrapped_op_budget.TURN_BUDGET_S). A look's cost does not grow with the scene,
         # so no bound; its measured p95 on Mac 1 and the Windows VM must still fit the budget.
         input_bound=None,
         job_kind=None,
         checks=camera_look_checks, receipt=camera_look_receipt, guide="camera_look"),
)
