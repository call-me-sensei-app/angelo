# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hand-rigging tools (the RIG family): their schemas and argument checks. No Blender dependency.

Every RIG tool only reads: it measures an armature's hand and the mesh skinned to it, skins candidate poses itself
(``hand_rig``, ``hand_pose``) and draws pictures off-screen (``rig_views``), so the scene, its pose and Undo never
change. A pose the tools solve comes back as ``bone_set`` operations, which the agent applies through a scene
program like any other edit (approval, preview and one Undo step). The workflow guidance is
``agent_guidance.HAND_GUIDANCE`` (doc/angelo/hand_rigging.rst).
"""

import math
import re

from .scene_spec import HANDLE

MAX_LIMITS = 64
BONE_NAME = {"type": "string", "minLength": 1, "maxLength": 64}
VIEWS = ("BACK", "PALM", "THUMB_SIDE", "LITTLE_SIDE", "PALM_THUMB")
DEFAULT_VIEWS = ("BACK", "PALM", "THUMB_SIDE", "LITTLE_SIDE")
MAX_SHEETS = 5
_SPAN = {"type": "array", "items": {"type": "number", "minimum": -180, "maximum": 180}, "minItems": 2, "maxItems": 2}
_LIMIT = {"type": "object", "properties": {"bone": BONE_NAME, "flex": _SPAN, "spread": _SPAN, "twist": _SPAN},
          "required": ["bone"], "additionalProperties": False}
_COMMON = {
    "armature": dict(HANDLE),
    "mesh": dict(HANDLE),
    "hand": BONE_NAME,
    "thumb": BONE_NAME,
    "palm_normal": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
}
_LIMITS = {"type": "array", "items": _LIMIT, "minItems": 1, "maxItems": MAX_LIMITS}
_ANGLE = {"type": "object", "properties": {"bone": BONE_NAME, "flex": {"type": "number", "minimum": -180, "maximum": 180},
                                           "spread": {"type": "number", "minimum": -180, "maximum": 180},
                                           "twist": {"type": "number", "minimum": -180, "maximum": 180}},
          "required": ["bone"], "additionalProperties": False}
_HAND_NOTE = (" armature is the rig; mesh the skinned mesh (default: the one mesh whose Armature modifier uses it). "
              "hand names a hand bone (default: every hand found); thumb a bone of the thumb's chain when the "
              "tools can't tell. palm_normal (world, out of the palm) only when asked for.")


def _tool(name, description, properties, required=("armature",)):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": list(required),
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}


RIG_TOOLS = [
    _tool("angelo_rig_hand_inspect",
          "Read-only. Find each hand of an armature (thumb and fingers by bone layout, not names), its anatomical "
          "joint frames, the anatomy checklist measured on the bones, the current pose's joint angles outside "
          "their limits, fused (mitten) fingers, and weight problems: hard cuts, finger weight behind the knuckle, "
          "weight from bones outside the arm." + _HAND_NOTE + " limits overrides default ranges: [{bone, "
          "flex|spread|twist: [low, high] degrees}].",
          dict(_COMMON, limits=_LIMITS)),
    _tool("angelo_rig_hand_contact",
          "Read-only. Skin contact of each hand in its current pose: gap and depth (finger radii) between "
          "neighbouring fingers and between the thumb and each finger, fingertip depth in the palm, and joints "
          "outside their limits. Skin, not capsules; contact already there at rest (webs) is left out."
          + _HAND_NOTE,
          dict(_COMMON, limits=_LIMITS)),
    _tool("angelo_rig_hand_range",
          "Read-only. Move each joint of one hand alone through its range (back limit, rest, forward limit), "
          "stopping where its skin sinks into another part; returns each joint's reach and what stopped it. "
          "pictures=true adds one sheet per digit (rest, back reach, half, forward reach) at the view given."
          + _HAND_NOTE + " joints limits the run to those bones.",
          dict(_COMMON, limits=_LIMITS, joints={"type": "array", "items": BONE_NAME, "minItems": 1, "maxItems": 20},
               pictures={"type": "boolean"}, view={"type": "string", "enum": list(VIEWS)})),
    _tool("angelo_rig_hand_fist",
          "Read-only solver. A closed fist for each hand, folded joint by joint to its limits with skin contact: "
          "knuckles to their limit, middle joints until the fingertips meet the palm (end joints following), "
          "fingers closed side by side, the thumb across the index and middle fingers resting on them. Returns "
          "the joint angles, contacts and operations (bone_set rotations) to apply unchanged in one "
          "angelo_scene_program. pictures=true adds review views of the solved fist beside the rest pose."
          + _HAND_NOTE,
          dict(_COMMON, limits=_LIMITS, pictures={"type": "boolean"},
               views={"type": "array", "items": {"type": "string", "enum": list(VIEWS)}, "minItems": 1,
                      "maxItems": len(VIEWS), "uniqueItems": True})),
    _tool("angelo_rig_hand_pose",
          "Read-only solver. A hand pose from anatomical joint angles (degrees from rest: flex toward the palm, spread "
          "toward the thumb side, twist): returns the operations (bone_set rotations) to apply unchanged in one "
          "angelo_scene_program, whatever each bone's roll, with the pose's contacts. Joints not listed keep their "
          "current angles; an end joint not listed follows its middle joint. Angles beyond a limit are clamped and "
          "named. pictures=true adds views of the pose beside the rest pose." + _HAND_NOTE,
          dict(_COMMON, limits=_LIMITS, angles={"type": "array", "items": _ANGLE, "minItems": 1, "maxItems": 20},
               pictures={"type": "boolean"},
               views={"type": "array", "items": {"type": "string", "enum": list(VIEWS)}, "minItems": 1,
                      "maxItems": len(VIEWS), "uniqueItems": True}),
          required=("armature", "angles")),
    _tool("angelo_rig_hand_views",
          "Read-only pictures of one hand in its current pose, each beside its rest pose (an edge already in the "
          "rest mesh is a mesh problem, not a weight problem): BACK, PALM, THUMB_SIDE, LITTLE_SIDE and PALM_THUMB "
          "(the palm turned toward the thumb), framed on the hand from its own frame. Skin deeper than contact in "
          "another part is red; markers=true dots the joints." + _HAND_NOTE,
          dict(_COMMON, views={"type": "array", "items": {"type": "string", "enum": list(VIEWS)}, "minItems": 1,
                               "maxItems": len(VIEWS), "uniqueItems": True},
               markers={"type": "boolean"})),
]
RIG_NAMES = frozenset(tool["name"] for tool in RIG_TOOLS)
# The RIG tools whose results carry pictures (plan_recovery, providers.tool_content, the controller's view store).
RIG_IMAGE_TOOLS = frozenset({"angelo_rig_hand_range", "angelo_rig_hand_fist", "angelo_rig_hand_pose",
                             "angelo_rig_hand_views"})
_HANDLE = re.compile(HANDLE["pattern"])


def validate_rig(name, args):
    """The checked arguments of a RIG tool call (ValueError with the reason)."""
    tool = next((t for t in RIG_TOOLS if t["name"] == name), None)
    if tool is None:
        raise ValueError("Unknown hand-rigging tool")
    properties = tool["inputSchema"]["properties"]
    extra = set(args) - set(properties)
    if extra:
        raise ValueError("%s does not take: %s" % (name, ", ".join(sorted(extra))))
    out = {}
    for key in ("armature", "mesh"):
        if key in args:
            if not isinstance(args[key], str) or not _HANDLE.fullmatch(args[key]):
                raise ValueError("%s must be an observed object handle" % key)
            out[key] = args[key]
    if "armature" not in out:
        raise ValueError("Name the armature (an object handle)")
    for key in ("hand", "thumb"):
        if key in args:
            if not isinstance(args[key], str) or not 1 <= len(args[key]) <= 64:
                raise ValueError("%s must be a bone name of 1-64 characters" % key)
            out[key] = args[key]
    if "palm_normal" in args:
        v = args["palm_normal"]
        if (not isinstance(v, list) or len(v) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float))
                                                          or not math.isfinite(x) for x in v)
                or math.sqrt(sum(x * x for x in v)) < 1e-9):
            raise ValueError("palm_normal must be a non-zero direction [x, y, z]")
        out["palm_normal"] = [float(x) for x in v]
    if "limits" in args:
        out["limits"] = _limits(args["limits"])
    for key in ("pictures", "markers"):
        if key in args:
            if type(args[key]) is not bool:
                raise ValueError("%s must be true or false" % key)
            out[key] = args[key]
    if "view" in args:
        if args["view"] not in VIEWS:
            raise ValueError("view must be one of: " + ", ".join(VIEWS))
        out["view"] = args["view"]
    if "views" in args:
        views = args["views"]
        if (not isinstance(views, list) or not 1 <= len(views) <= len(VIEWS) or len(set(map(str, views))) != len(views)
                or any(v not in VIEWS for v in views)):
            raise ValueError("views must list 1-%d distinct of: %s" % (len(VIEWS), ", ".join(VIEWS)))
        out["views"] = list(views)
    if "joints" in args:
        joints = args["joints"]
        if (not isinstance(joints, list) or not 1 <= len(joints) <= 20
                or any(not isinstance(j, str) or not 1 <= len(j) <= 64 for j in joints)):
            raise ValueError("joints must list 1-20 bone names")
        out["joints"] = list(dict.fromkeys(joints))
    if "angles" in args:
        out["angles"] = _angles(args["angles"])
    elif name == "angelo_rig_hand_pose":
        raise ValueError("Give angles: [{bone, flex, spread, twist}] in degrees")
    return out


def _angles(value):
    """[{bone, flex, spread, twist}] -> {bone: {axis: degrees}}; an axis left out keeps its current angle. Checked
    arguments are checked again by the host (``rig_host.execute``): the map form is accepted as the list's."""
    if isinstance(value, dict):
        if not all(isinstance(axes, dict) for axes in value.values()):
            raise ValueError("angles must list 1-20 joints: [{bone, flex, spread, twist}] in degrees")
        value = [dict(axes, bone=bone) for bone, axes in value.items()]
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError("angles must list 1-20 joints: [{bone, flex, spread, twist}] in degrees")
    out = {}
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("bone"), str) or not 1 <= len(entry["bone"]) <= 64:
            raise ValueError("Each angles entry names its bone: {bone, flex, spread, twist}")
        axes = {k: v for k, v in entry.items() if k != "bone"}
        if not axes or entry["bone"] in out or set(axes) - {"flex", "spread", "twist"}:
            raise ValueError("Angles for %r: give each bone once, with flex, spread or twist" % entry["bone"][:64])
        for axis, v in axes.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not -180 <= v <= 180:
                raise ValueError("Angle %s of %r must be degrees within -180..180" % (axis, entry["bone"][:64]))
        out[entry["bone"]] = {axis: float(v) for axis, v in axes.items()}
    return out


def _limits(value):
    """[{bone, flex|spread|twist: [low, high]}] -> {bone: {axis: [low, high]}} (hand_rig.limits_for); the map form
    is accepted as the list's (the host checks checked arguments again)."""
    if isinstance(value, dict):
        if not all(isinstance(axes, dict) for axes in value.values()):
            raise ValueError("limits must list 1-%d bones with their ranges" % MAX_LIMITS)
        value = [dict(axes, bone=bone) for bone, axes in value.items()]
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_LIMITS:
        raise ValueError("limits must list 1-%d bones with their ranges" % MAX_LIMITS)
    out = {}
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("bone"), str) or not 1 <= len(entry["bone"]) <= 64:
            raise ValueError("Each limits entry names its bone: {bone, flex|spread|twist: [low, high]}")
        bone = entry["bone"]
        axes = {k: v for k, v in entry.items() if k != "bone"}
        if not axes or bone in out:
            raise ValueError("Limits for %r: give each bone once, with at least one of flex, spread, twist" % bone[:64])
        out[bone] = {}
        for axis, span in axes.items():
            if axis not in ("flex", "spread", "twist"):
                raise ValueError("A joint limit axis is flex, spread or twist")
            if (not isinstance(span, list) or len(span) != 2
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not -180 <= x <= 180 for x in span)
                    or span[0] > span[1]):
                raise ValueError("Limit %s of %r must be [low, high] degrees within -180..180, low first"
                                 % (axis, bone[:64]))
            out[bone][axis] = [float(span[0]), float(span[1])]
    return out
