# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check tool: its schema and argument checks. No Blender dependency.

One deferred tool (``plan_kinds.DEFERRED_TOOLS``), so a turn that never asks about a game engine's skeleton sends
only its name. ``angelo_skeleton_check`` is read-only and runs without a card; its host side is
``skeleton_check_host``, its behaviour doc/angelo/engine_skeleton.rst.
"""

import re

from .scene_spec import HANDLE

SKELETON = "angelo_skeleton_check"
TARGETS = ("GODOT", "UNITY", "UNREAL")   # skeleton_profiles.PROFILES' keys; held here so start-up loads no profile
FORMATS = ("GLB", "FBX")
FORMAT_CHOICES = ("AUTO",) + FORMATS

SKELETON_TOOLS = [
    {"name": SKELETON,
     "description": (
         "Read-only: does this armature fit a game engine's humanoid skeleton: UNREAL (UE5 Mannequin names, IK "
         "Retargeter auto-retarget), UNITY (Humanoid) or GODOT (SkeletonProfileHumanoid). Finds each bone's role by "
         "layout, then checks names and hierarchy, root bone, deforming and leaf bones, rest pose, facing, bone "
         "axes, transform, constraints and influences; each finding says what it looked for and found, the fix, "
         "and its engine source and versions. armature: a handle (default: the scene's only or active armature); "
         "format AUTO is the engine's usual export."),
     "inputSchema": {"type": "object", "properties": {
         "target": {"type": "string", "enum": list(TARGETS)},
         "armature": dict(HANDLE),
         "format": {"type": "string", "enum": list(FORMAT_CHOICES)}},
         "required": ["target"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
]
SKELETON_NAMES = frozenset(tool["name"] for tool in SKELETON_TOOLS)
_HANDLE = re.compile(HANDLE["pattern"])


def validate_skeleton(name, args):
    """The canonical arguments of a skeleton check call; ValueError for any that is not valid."""
    if name != SKELETON:
        raise ValueError("Unknown engine skeleton tool")
    extra = set(args) - {"target", "armature", "format"}
    if extra:
        raise ValueError("%s does not take: %s" % (name, ", ".join(sorted(extra))))
    if args.get("target") not in TARGETS:
        raise ValueError("target must be one of " + ", ".join(TARGETS))
    out = {"target": args["target"], "format": args.get("format", "AUTO")}
    if out["format"] not in FORMAT_CHOICES:
        raise ValueError("format must be AUTO, GLB or FBX")
    if "armature" in args:
        if not isinstance(args["armature"], str) or not _HANDLE.fullmatch(args["armature"]):
            raise ValueError("armature must be an observed object handle")
        out["armature"] = args["armature"]
    return out
