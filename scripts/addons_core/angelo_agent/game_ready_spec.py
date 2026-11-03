# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check tool: its schema and argument checks. No Blender dependency.

``angelo_game_ready_check`` is read-only and deferred (``plan_kinds.DEFERRED_TOOLS``), so a turn that never prepares a
game asset sends only its name. It measures a mesh against reductions of it: LOD objects the person or agent made,
and reductions it computes in memory (meshoptimizer, and Blender's Decimate on the same input) without touching the
scene. Every fact says what it measured. The behaviour is in doc/angelo/game_ready_check.rst; the host side is
``game_ready_host``.
"""

import math
import re

from .scene_spec import HANDLE

CHECK = "angelo_game_ready_check"
METHODS = ("MESHOPT", "DECIMATE")
MAX_LODS = 6
MAX_RATIOS = 6
DEFAULT_RESOLUTION = 1080
DEFAULT_FOV = 60.0
DEFAULT_TEXTURE = 1024
DEFAULT_UV_WEIGHT = 1.0
DEFAULT_NORMAL_WEIGHT = 0.5
DEFAULT_SKIN_WEIGHT = 1.0
_HANDLE = re.compile(HANDLE["pattern"])
_WEIGHT = {"type": "number", "minimum": 0, "maximum": 100}

GAME_READY_TOOLS = [
    {"name": CHECK,
     "description": (
         "Read-only facts for a game asset: a mesh (object) against LODs, given (lods: objects) or reduced in "
         "memory (simplify: meshopt index-only chains sharing LOD0's vertices, and Blender's Decimate on the same "
         "input), and a collider. Per LOD: triangles, two-sided Hausdorff (m, fraction of bounds), worst silhouette "
         "IoU over 7 views, pixel error at switch distances (and where it falls to 1 px), UV seams, stretch, "
         "overlap and texel density, normal and skin weight change. Each fact states what it measured."),
     "inputSchema": {"type": "object", "properties": {
         "object": dict(HANDLE),
         "lods": {"type": "array", "items": dict(HANDLE), "minItems": 1, "maxItems": MAX_LODS, "uniqueItems": True},
         "simplify": {"type": "object", "properties": {
             "ratios": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                        "minItems": 1, "maxItems": MAX_RATIOS},
             "methods": {"type": "array", "items": {"type": "string", "enum": list(METHODS)}, "minItems": 1,
                         "maxItems": len(METHODS), "uniqueItems": True},
             "max_error": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
             "uv_weight": dict(_WEIGHT), "normal_weight": dict(_WEIGHT), "skin_weight": dict(_WEIGHT),
             "lock_border": {"type": "boolean"}},
             "required": ["ratios"], "additionalProperties": False},
         "switch_distances": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0},
                              "minItems": 1, "maxItems": MAX_LODS + MAX_RATIOS},
         "resolution": {"type": "integer", "minimum": 64, "maximum": 8192},
         "fov_degrees": {"type": "number", "minimum": 1, "maximum": 170},
         "texture_size": {"type": "integer", "minimum": 16, "maximum": 16384},
         "collider": dict(HANDLE)},
         "required": ["object"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
]
GAME_READY_NAMES = frozenset(tool["name"] for tool in GAME_READY_TOOLS)


def _handle(value, what):
    if not isinstance(value, str) or not _HANDLE.fullmatch(value):
        raise ValueError("%s must be an observed object handle" % what)
    return value


def _number(value, what, low, high, *, low_open=False, high_open=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a number" % what)
    if value < low or value > high or (low_open and value == low) or (high_open and value == high):
        raise ValueError("%s must be %s %g to %g %s" % (what, "above" if low_open else "from", low, high,
                                                       "(exclusive)" if high_open else "(inclusive)"))
    return float(value)


def _integer(value, what, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("%s must be a whole number from %d to %d" % (what, low, high))
    return value


def _simplify(value):
    if not isinstance(value, dict):
        raise ValueError("simplify must be an object")
    extra = set(value) - {"ratios", "methods", "max_error", "uv_weight", "normal_weight", "skin_weight",
                          "lock_border"}
    if extra:
        raise ValueError("simplify does not take: " + ", ".join(sorted(extra)))
    ratios = value.get("ratios")
    if not isinstance(ratios, list) or not 1 <= len(ratios) <= MAX_RATIOS:
        raise ValueError("simplify.ratios must list 1 to %d ratios" % MAX_RATIOS)
    ratios = [_number(r, "each ratio", 0, 1, low_open=True, high_open=True) for r in ratios]
    # Each ratio is reduced from LOD0; they are listed from the largest down, as a LOD chain runs.
    ratios = sorted(set(ratios), reverse=True)
    methods = value.get("methods", list(METHODS))
    if (not isinstance(methods, list) or not methods or len(set(methods)) != len(methods)
            or any(m not in METHODS for m in methods)):
        raise ValueError("simplify.methods must list MESHOPT and/or DECIMATE once each")
    out = {"ratios": ratios, "methods": [m for m in METHODS if m in methods],
           "uv_weight": DEFAULT_UV_WEIGHT, "normal_weight": DEFAULT_NORMAL_WEIGHT, "skin_weight": DEFAULT_SKIN_WEIGHT,
           "lock_border": False, "max_error": None}
    for key in ("uv_weight", "normal_weight", "skin_weight"):
        if key in value:
            out[key] = _number(value[key], "simplify." + key, 0, 100)
    if "max_error" in value:
        out["max_error"] = _number(value["max_error"], "simplify.max_error", 0, 1, low_open=True)
    if "lock_border" in value:
        if not isinstance(value["lock_border"], bool):
            raise ValueError("simplify.lock_border must be true or false")
        out["lock_border"] = value["lock_border"]
    return out


def validate_game_ready(name, args):
    """The canonical arguments of a game-ready check; ValueError for any that is not valid."""
    if name != CHECK:
        raise ValueError("Unknown game-ready tool")
    if not isinstance(args, dict):
        raise ValueError("Arguments must be an object")
    allowed = set(GAME_READY_TOOLS[0]["inputSchema"]["properties"])
    extra = set(args) - allowed
    if extra:
        raise ValueError("%s does not take: %s" % (CHECK, ", ".join(sorted(extra))))
    if "object" not in args:
        raise ValueError("Name the mesh to check (object, an observed object handle)")
    out = {"object": _handle(args["object"], "object"), "lods": [], "simplify": None,
           "switch_distances": None, "resolution": DEFAULT_RESOLUTION, "fov_degrees": DEFAULT_FOV,
           "texture_size": DEFAULT_TEXTURE, "collider": None}
    if "lods" in args:
        lods = args["lods"]
        if not isinstance(lods, list) or not 1 <= len(lods) <= MAX_LODS:
            raise ValueError("lods must list 1 to %d object handles" % MAX_LODS)
        out["lods"] = [_handle(h, "each lod") for h in lods]
        if len(set(out["lods"])) != len(out["lods"]) or out["object"] in out["lods"]:
            raise ValueError("lods must name other objects than object, each once")
    if "simplify" in args:
        out["simplify"] = _simplify(args["simplify"])
    # Each simplify ratio is one LOD whichever methods reduce it; a distance applies to every method's result.
    count =len(out["lods"]) + (len(out["simplify"]["ratios"]) if out["simplify"] else 0)
    if "switch_distances" in args:
        distances = args["switch_distances"]
        if not isinstance(distances, list) or not distances:
            raise ValueError("switch_distances must list distances in meters")
        out["switch_distances"] = [_number(d, "each switch distance", 0, 1e9, low_open=True) for d in distances]
        if len(out["switch_distances"]) != count:
            raise ValueError("switch_distances must give one distance per LOD (%d: lods in order, then each "
                             "simplify ratio from the largest)" % count)
    if "resolution" in args:
        out["resolution"] = _integer(args["resolution"], "resolution", 64, 8192)
    if "fov_degrees" in args:
        out["fov_degrees"] = _number(args["fov_degrees"], "fov_degrees", 1, 170)
    if "texture_size" in args:
        out["texture_size"] = _integer(args["texture_size"], "texture_size", 16, 16384)
    if "collider" in args:
        out["collider"] = _handle(args["collider"], "collider")
        if out["collider"] == out["object"] or out["collider"] in out["lods"]:
            raise ValueError("collider must be another object than object and the lods")
    return out
