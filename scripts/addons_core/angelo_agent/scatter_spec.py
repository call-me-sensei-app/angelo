# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rule-based scattering: layers, their rules and the biome file format. Pure (NumPy).

Contract: doc/angelo/scatter.rst. A scatter system places one or more layers of assets on a surface. Each layer's
density (points per square metre, Poisson-disk spaced) is multiplied by its rules, each a factor from 0 to 1:

* ``slope`` {min, max, soft}: the surface's angle from horizontal, in degrees.
* ``height`` {min, max, soft, relative}: world Z in scene units, or with ``relative`` 0 to 1 over the surface's
  height.
* ``mask`` {name, invert}: a float attribute (a vertex group or a painted attribute) on the surface.
* ``distance`` {objects, keep_away | near, falloff}: distance to other objects (paths, buildings): keep out of a
  radius, or stay within one.
* ``noise`` {scale, amount}: natural patchiness (built in nodes only; it is not part of the audit).

``band`` is the Map Range node's Smooth Step (Hermite) that ``scatter_nodes`` builds; the factors here are that math
for the rule audit and tests. Layers are placed in order and each avoids the earlier ones' points by the sum of
their collision radii (``collision_radius``). A biome file (``angelo-biome/1``, JSON) holds layers whose assets are
Library items (``library_id``, or a ``query`` resolved when it is applied) or objects by ``object_name``; it never
holds a scene handle, so it means the same in any file.

Model-facing refusals are English.
"""

import json
import math
from pathlib import Path

import numpy as np

from . import read_only
from .refusal_codes import RefusalError

BIOME_SCHEMA = "angelo-biome/1"
MAX_BIOME_BYTES = 64 * 1024
PRESET_DIR = Path(__file__).resolve().parent / "assets" / "biomes"
MAX_LAYERS = 12
MAX_ASSETS = 16
MAX_DENSITY = 100.0      # Points per square metre: grass blades are a texture or a hair system, not instances.
MAX_SPACING = 10.0
DEFAULT_SPACING = 1.0    # Collision radius multiplier: 1 lets footprints touch.
DEFAULT_TRANSFORM = {"scale": [0.8, 1.2], "align": 0.0, "yaw": True, "tilt": 0.0}
RULES = ("slope", "height", "mask", "distance", "noise")
ASSET_KEYS = ("object", "library_id", "query", "object_name")
KINDS = ("model",)


class ScatterError(RefusalError):
    partial_changes_possible = False


def _error(message):
    return ScatterError(message, code="VALIDATION")


# -- the rules' math --------------------------------------------------------------------------------------------
def _hermite(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def band(x, low, high, soft):
    """1 between ``low`` and ``high``, easing to 0 over ``soft`` outside them (a hard edge when ``soft`` is 0)."""
    x = np.asarray(x, dtype=np.float64)
    if soft > 0.0:
        rise = _hermite(low - soft, low, x) if math.isfinite(low) else np.ones_like(x)
        fall = 1.0 - _hermite(high, high + soft, x) if math.isfinite(high) else np.ones_like(x)
        return rise * fall
    return ((x >= low) & (x <= high)).astype(np.float64)


def slope_degrees(normals):
    """The angle of each unit normal from straight up, in degrees."""
    normals = np.asarray(normals, dtype=np.float64)
    return np.degrees(np.arccos(np.clip(normals[:, 2], -1.0, 1.0)))


def distance_factor(distances, rule):
    falloff = float(rule.get("falloff", 0.0))
    if "keep_away" in rule:
        return band(distances, float(rule["keep_away"]) + falloff, math.inf, falloff)
    return band(distances, -math.inf, float(rule["near"]), falloff)


def mask_factor(values, rule):
    values = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
    return 1.0 - values if rule.get("invert") else values


def factor(rules, normals, positions, masks=None, distances=None, bounds=None):
    """The product of ``rules`` at points with ``normals`` and ``positions`` (world space); ``masks`` maps an
    attribute name to its values there, ``distances`` the distance rule's distances, ``bounds`` the surface's
    (z min, z max) for a relative height (default: the points')."""
    positions = np.asarray(positions, dtype=np.float64)
    out = np.ones(len(positions))
    if "slope" in rules:
        rule = rules["slope"]
        out *= band(slope_degrees(normals), rule["min"], rule["max"], rule.get("soft", 0.0))
    if "height" in rules:
        rule, z = rules["height"], positions[:, 2]
        if rule.get("relative"):
            low, high = bounds if bounds is not None else (float(z.min()), float(z.max()))
            z = (z - low) / (high - low) if high > low else np.zeros_like(z)
        out *= band(z, rule["min"], rule["max"], rule.get("soft", 0.0))
    if "mask" in rules and masks is not None:
        out *= mask_factor(masks[rules["mask"]["name"]], rules["mask"])
    if "distance" in rules and distances is not None:
        out *= distance_factor(distances, rules["distance"])
    return out


def collision_radius(dimensions, scale_max, spacing):
    """How far another instance's centre must stay: half the footprint's ground-plane diagonal, at the layer's
    largest scale, times its spacing."""
    return math.hypot(dimensions[0] * 0.5, dimensions[1] * 0.5) * scale_max * spacing


# -- checking layers ---------------------------------------------------------------------------------------------
def _number(value, what, low, high, low_open=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value > high or value < low or (low_open and value == low):
        raise _error("%s must be a number from %s%g to %g" % (what, "above " if low_open else "", low, high))
    return float(value)


def _band_rule(name, rule, low, high):
    if not isinstance(rule, dict) or not {"min", "max"} <= set(rule) or set(rule) - {"min", "max", "soft",
                                                                                         "relative"}:
        raise _error("%s takes min, max and optionally soft%s" % (name, ", relative" if name == "height" else ""))
    out = {"min": _number(rule["min"], name + ".min", low, high), "max": _number(rule["max"], name + ".max", low, high)}
    if out["min"] > out["max"]:
        raise _error("%s.min must not be above %s.max" % (name, name))
    out["soft"] = _number(rule.get("soft", 0.0), name + ".soft", 0.0, high - low if math.isfinite(high - low) else 1e6)
    if name == "height" and rule.get("relative"):
        if rule["relative"] is not True:
            raise _error("height.relative must be true or left out")
        out["relative"] = True
        if out["min"] < 0.0 or out["max"] > 1.0:
            raise _error("A relative height runs from 0 (the surface's lowest point) to 1 (its highest)")
    return out


def _rules(rules, handles):
    if not isinstance(rules, dict):
        raise _error("rules is an object")
    unknown = set(rules) - set(RULES)
    if unknown:
        raise _error("rules take: " + ", ".join(RULES) + " (not " + ", ".join(sorted(unknown)) + ")")
    out = {}
    if "slope" in rules:
        out["slope"] = _band_rule("slope", rules["slope"], 0.0, 180.0)
    if "height" in rules:
        out["height"] = _band_rule("height", rules["height"], -1e6, 1e6)
    if "mask" in rules:
        rule = rules["mask"]
        if not isinstance(rule, dict) or not isinstance(rule.get("name"), str) or not rule["name"] \
                or set(rule) - {"name", "invert"} or not isinstance(rule.get("invert", False), bool):
            raise _error("mask takes name (the surface's attribute or vertex group) and optionally invert")
        out["mask"] = {"name": rule["name"][:64], "invert": rule.get("invert", False)}
    if "distance" in rules:
        rule = rules["distance"]
        if not isinstance(rule, dict) or ("keep_away" in rule) == ("near" in rule) or "objects" not in rule:
            raise _error("distance takes objects and either keep_away or near (a radius), optionally falloff")
        objects = rule["objects"]
        if not isinstance(objects, list) or not 1 <= len(objects) <= 16:
            raise _error("distance.objects lists 1 to 16 objects")
        key = "keep_away" if "keep_away" in rule else "near"
        out["distance"] = {"objects": [handles(item, "distance.objects") for item in objects],
                           key: _number(rule[key], "distance." + key, 0.0, 1e5),
                           "falloff": _number(rule.get("falloff", 0.0), "distance.falloff", 0.0, 1e5)}
    if "noise" in rules:
        rule = rules["noise"]
        if not isinstance(rule, dict) or set(rule) - {"scale", "amount"}:
            raise _error("noise takes scale (scene units) and amount (0 to 1)")
        out["noise"] = {"scale": _number(rule.get("scale", 10.0), "noise.scale", 0.0, 1e5, low_open=True),
                        "amount": _number(rule.get("amount", 0.5), "noise.amount", 0.0, 1.0)}
    return out


def _asset(item, in_file, handles):
    if not isinstance(item, dict):
        raise _error("Each asset is an object")
    given = [key for key in ASSET_KEYS if key in item]
    if len(given) != 1:
        raise _error("Each asset names exactly one of: " + ", ".join(
            key for key in ASSET_KEYS if not (in_file and key == "object")))
    key = given[0]
    if in_file and key == "object":
        raise _error("A biome file names a scene object by object_name, never by a handle")
    out = {key: handles(item[key], "assets.object") if key == "object" else item[key]}
    if key != "object" and (not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > 200):
        raise _error("assets.%s is a non-empty text of at most 200 characters" % key)
    if key == "query":
        out["kind"] = item.get("kind", "model")
        if out["kind"] not in KINDS:
            raise _error("assets.kind must be one of: " + ", ".join(KINDS))
    out["weight"] = _number(item.get("weight", 1.0), "assets.weight", 0.0, 1e3, low_open=True)
    return out


def _transform(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_TRANSFORM):
        raise _error("transform takes: " + ", ".join(DEFAULT_TRANSFORM))
    out = read_only.thawed(DEFAULT_TRANSFORM)
    if "scale" in value:
        scale = value["scale"]
        if not isinstance(scale, list) or len(scale) != 2:
            raise _error("transform.scale is [smallest, largest]")
        low, high = (_number(v, "transform.scale", 0.01, 100.0) for v in scale)
        if low > high:
            raise _error("transform.scale is [smallest, largest], smallest first")
        out["scale"] = [low, high]
    if "align" in value:
        out["align"] = _number(value["align"], "transform.align", 0.0, 1.0)
    if "yaw" in value:
        if not isinstance(value["yaw"], bool):
            raise _error("transform.yaw must be true or false")
        out["yaw"] = value["yaw"]
    if "tilt" in value:
        out["tilt"] = _number(value["tilt"], "transform.tilt", 0.0, 45.0)
    return out


def _handle(value, what):
    if not isinstance(value, str) or not value:
        raise _error("%s is an object handle" % what)
    return value


def check_layer(value, in_file=False, handles=_handle):
    """A layer checked and filled with defaults. ``in_file``: from a biome file (no scene handles). ``handles(value,
    what)`` checks an object handle (the typed operation resolves it)."""
    if not isinstance(value, dict):
        raise _error("Each layer is an object")
    allowed = {"name", "assets", "density", "spacing", "rules", "transform", "seed"}
    unknown = set(value) - allowed
    if unknown:
        raise _error("A layer takes: " + ", ".join(sorted(allowed)) + " (not " + ", ".join(sorted(unknown)) + ")")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise _error("Each layer has a name of 1 to 64 characters")
    assets = value.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= MAX_ASSETS:
        raise _error("assets lists 1 to %d assets" % MAX_ASSETS)
    seed = value.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 31:
        raise _error("seed is a whole number from 0 to 2147483647")
    return {"name": name.strip(),
            "assets": [_asset(item, in_file, handles) for item in assets],
            "density": _number(value.get("density"), "density (points per square metre)", 0.0, MAX_DENSITY,
                               low_open=True),
            "spacing": _number(value.get("spacing", DEFAULT_SPACING), "spacing", 0.0, MAX_SPACING),
            "rules": _rules(value.get("rules", {}), handles),
            "transform": _transform(value.get("transform", {})),
            "seed": seed}


def check_layers(layers, in_file=False, handles=_handle):
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS:
        raise _error("layers lists 1 to %d layers" % MAX_LAYERS)
    out = [check_layer(item, in_file, handles) for item in layers]
    names = [item["name"].lower() for item in out]
    for name in names:
        if names.count(name) > 1:
            raise _error("The layer name %s is used twice; name each layer once (names tell the facts apart)" % name)
    return out


# -- biome files ------------------------------------------------------------------------------------------------
def load_biome(text):
    """A biome file's text checked into ``{schema, name, description, layers}``."""
    if len(text.encode("utf-8")) > MAX_BIOME_BYTES:
        raise _error("A biome file is at most %d KiB" % (MAX_BIOME_BYTES // 1024))
    try:
        value = json.loads(text)
    except ValueError:
        raise _error("A biome file is JSON") from None
    if not isinstance(value, dict) or value.get("schema") != BIOME_SCHEMA:
        raise _error("A biome file's schema is " + BIOME_SCHEMA)
    unknown = set(value) - {"schema", "name", "description", "layers"}
    if unknown:
        raise _error("A biome file holds schema, name, description and layers (not " + ", ".join(sorted(unknown)) + ")")
    name, description = value.get("name"), value.get("description", "")
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise _error("A biome has a name of 1 to 64 characters")
    if not isinstance(description, str) or len(description) > 300:
        raise _error("A biome's description is at most 300 characters")
    return {"schema": BIOME_SCHEMA, "name": name.strip(), "description": description,
            "layers": check_layers(value.get("layers"), in_file=True)}


def dump_biome(biome):
    """A biome as the file's text (checked first, so a written file always loads)."""
    checked = load_biome(json.dumps(biome))
    return json.dumps(checked, indent=1, ensure_ascii=False) + "\n"


# The validators above read these as registered, read only (read_only.seal).
read_only.seal(globals(), 'DEFAULT_TRANSFORM')
