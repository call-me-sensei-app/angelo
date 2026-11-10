# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_goal``: the model's way to a goal Angelo holds across turns (doc/angelo/goal_mode.rst, "Setting a goal").

Schema and validation only; ``goal_loop`` executes it. One core tool on both runtimes, small enough for Codex code
mode's declaration budget (about 2,000 characters, gpt_launch.rst): a measure's camera is ``angelo_view``'s free
camera, validated by the same function; targets are a rule string, never handles; scene checks are
``angelo_scene_verify``'s closed checks, declared by kind only. The description states what the tool does and when
the person has asked for it, never a workflow.
"""

import re
import uuid

from .memory_spec import UNSAFE_CHARACTER
from .view_spec import _camera_item

TOOL_NAME = "angelo_goal"
ACTIONS = ("SET", "MEASURE", "STOP")
FIELDS = {"SET": frozenset({"objective", "measure"}), "MEASURE": frozenset(),
          "STOP": frozenset({"reason", "message"})}
SHARED_FIELDS = ()


def fields_by_action():
    """The declaration and validator share this action-specific field contract."""
    order = ("objective", "measure", "reason", "message")
    entries = []
    for action in ACTIONS:
        fields = " ".join(field + ("*" if action == "STOP" else "")
                          for field in order if field in FIELDS[action]) or "no other field"
        entries.append(action + ": " + fields)
    return ("Fields by action (* required; any other is refused): " + "; ".join(entries)
            + ". SET needs objective or measure; STOP needs reason and message.")

REASONS = ("DONE", "NEEDS_INPUT", "BLOCKED")
MAX_TEXT = 500
VISIBLE, COLLECTION = "VISIBLE", "COLLECTION:"
_CAMERA_FIELDS = frozenset({"location", "look_at", "lens", "ortho_scale"})
_COLLECTION = re.compile(r"^COLLECTION:[^\x00-\x1f\x7f]{1,63}$")
_VECTOR = {"type": "array", "items": {"type": "number"}}

GOAL_TOOLS = [{
    "name": TOOL_NAME,
    "description": (
        "Hold a goal across turns, only when the person asks for work to continue until a condition holds. Angelo "
        "measures it after each of your turns and continues this chat with the result until it passes, makes no "
        "progress, or its budget (shown to the person) runs out. measure: an attached image's reference_id, a free "
        "camera at the image's viewpoint and targets (VISIBLE: visible meshes in that view; COLLECTION:<name>) give "
        "an outline overlap that must reach the person's threshold; checks (angelo_scene_verify kinds) pass when "
        "all pass; no measure: your judgment. The measure locks after its first measured round. MEASURE returns it "
        "now. STOP: DONE or BLOCKED ends the goal, NEEDS_INPUT pauses it; message is shown to the person. "
        + fields_by_action()),
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "objective": {"type": "string", "maxLength": MAX_TEXT},
        "measure": {"type": "object", "properties": {
            "reference": {"type": "string"},
            "camera": {"type": "object", "properties": {"location": _VECTOR, "look_at": _VECTOR,
                                                        "lens": {"type": "number"}, "ortho_scale": {"type": "number"}},
                       "required": ["location", "look_at"]},
            "targets": {"type": "string"},
            "checks": {"type": "array", "items": {"type": "object", "properties": {"kind": {"type": "string"}},
                                                  "required": ["kind"]}}}},
        "reason": {"type": "string", "enum": list(REASONS)},
        "message": {"type": "string", "maxLength": MAX_TEXT}},
        "required": ["action"], "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}]
GOAL_NAMES = frozenset({TOOL_NAME})


def _text(value, field, required=True):
    if type(value) is not str or len(value) > MAX_TEXT or (required and not value.strip()):
        raise ValueError("%s is text of 1-%d characters" % (field, MAX_TEXT))
    if UNSAFE_CHARACTER.search(value):
        raise ValueError("%s holds a control or direction-override character" % field)
    return value


def _measure(value):
    if type(value) is not dict:
        raise ValueError("measure is {reference, camera, targets} or {checks}")
    if "checks" in value:
        if set(value) != {"checks"}:
            raise ValueError("measure is one of {reference, camera, targets} or {checks}, never both")
        from .validation_spec import validate_checks
        return {"checks": validate_checks(value["checks"])}
    unknown = sorted(set(value) - {"reference", "camera", "targets"}, key=str)
    if unknown:
        raise ValueError("measure has unknown field %s" % str(unknown[0])[:40])
    try:
        reference = str(uuid.UUID(value.get("reference")))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("measure.reference is the reference_id of an image attached to this conversation") from None
    if type(value.get("camera")) is not dict:
        raise ValueError("measure.camera is a free camera: {location, look_at, lens | ortho_scale}")
    camera = _camera_item(value["camera"], 0, "measure.camera", _CAMERA_FIELDS)
    targets = value.get("targets", VISIBLE)
    if targets != VISIBLE and (type(targets) is not str or not _COLLECTION.match(targets)):
        raise ValueError("measure.targets is VISIBLE or COLLECTION:<name> (a collection's name, 1-63 characters)")
    return {"reference": reference, "camera": camera, "targets": targets}


def validate_goal(name, args):
    """The canonical arguments of an ``angelo_goal`` call; ValueError naming the field for anything else."""
    if name != TOOL_NAME or type(args) is not dict:
        raise ValueError("angelo_goal takes an action: SET, MEASURE or STOP")
    action = args.get("action")
    if action not in ACTIONS:
        raise ValueError("action is SET, MEASURE or STOP")
    allowed = FIELDS[action] | {"action"}
    extra = sorted(set(args) - allowed, key=str)
    if extra:
        raise ValueError("%s takes no %s" % (action, str(extra[0])[:40]))
    if action == "MEASURE":
        return {"action": "MEASURE"}
    if action == "STOP":
        if args.get("reason") not in REASONS:
            raise ValueError("reason is DONE, NEEDS_INPUT or BLOCKED")
        return {"action": "STOP", "reason": args["reason"], "message": _text(args.get("message"), "message")}
    checked = {"action": "SET"}
    if "objective" in args:
        checked["objective"] = _text(args["objective"], "objective")
    if "measure" in args:
        checked["measure"] = _measure(args["measure"])
    if len(checked) == 1:
        raise ValueError("SET takes objective (a new goal) or measure (how a held goal is measured)")
    return checked
