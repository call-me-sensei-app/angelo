# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake every map at once (``BAKE_MAPS``): the channels, the packing presets and the request's own fields. Pure.

Contract: doc/angelo/long_jobs.rst, "Texture sets". One job bakes every PBR
channel the target's materials use into a texture set of files in ``output``:
base colour, metallic, roughness, tangent normal, emission, alpha, ambient
occlusion and height. A packing preset decides which file holds which channel
(``files``); ``job_spec`` declares the fields in ``angelo_job_start`` and calls
``validate`` for this kind. What each channel reads from a node tree is
``bake_maps_graph``'s, how the arrays become files ``bake_maps_pack``'s.

Model-facing refusals are English.
"""

from .refusal_codes import RefusalError

KIND = "BAKE_MAPS"
CHANNELS = ("BASE_COLOR", "METALLIC", "ROUGHNESS", "NORMAL", "EMISSION", "ALPHA", "AO", "HEIGHT")
# How many components each channel's array keeps.
WIDTH = {"BASE_COLOR": 3, "METALLIC": 1, "ROUGHNESS": 1, "NORMAL": 3, "EMISSION": 3, "ALPHA": 1, "AO": 1, "HEIGHT": 1}
# The value a slot gets when the channel was not baked: no occlusion, no metal, half rough, opaque.
DEFAULTS = {"AO": 1.0, "METALLIC": 0.0, "ROUGHNESS": 0.5, "ALPHA": 1.0, "HEIGHT": 0.5, "EMISSION": 0.0,
            "BASE_COLOR": 0.8, "NORMAL": 0.5}
# A channel whose material value is this constant everywhere carries nothing, so a separate map of it is not written.
UNUSED_WHEN = {"METALLIC": 0.0, "EMISSION": 0.0, "ALPHA": 1.0}
# A high-to-low height's three bakes: where the sources' surface is, where the target's is, its normal there.
HIGH_POSITION, LOW_POSITION, LOW_NORMAL = "HIGH_POSITION", "LOW_POSITION", "LOW_NORMAL"
PACKINGS = ("SEPARATE", "ORM", "GLTF", "UNREAL", "UNITY")
FORMATS = {"PNG": ".png", "OPEN_EXR": ".exr"}
DEPTHS = (8, 16, 32)
# 16-bit by default where 8 bits band: a tangent normal and a height map.
DEEP_BY_DEFAULT = frozenset({"Normal", "Height"})
DEFAULT_RESOLUTION = [2048, 2048]
DEFAULT_MARGIN, MAX_MARGIN = 16, 256
MAX_TARGETS = 16
# Samples per texel for the channels read from the material (antialiasing only; they hold no noise) and for AO.
DATA_SAMPLES, AO_SAMPLES = 8, 64
DEFAULT_MINUTES = 60
# The engines' own normal-map conventions: OpenGL (+Y) except Unreal (DirectX, -Y).
DIRECTX = frozenset({"UNREAL"})


class BakeMapsError(RefusalError):
    partial_changes_possible = False


# Components of a file: (channel, index, transform), or a float for a slot nothing fills. A transform is "" (as
# baked), "invert" (1 - value: Unity's smoothness) or "flip" (1 - value: a DirectX normal's green).
def _rgb(channel, flip_green=False):
    return ((channel, 0, ""), (channel, 1, "flip" if flip_green else ""), (channel, 2, ""))


def _one(channel, transform=""):
    return ((channel, 0, transform),)


ORM_SLOTS = (("AO", 0, ""), ("ROUGHNESS", 0, ""), ("METALLIC", 0, ""))
# Unity's mask map (HDRP), which URP's metallic map reads too: R metallic, G occlusion, B detail mask, A smoothness.
UNITY_MASK = (("METALLIC", 0, ""), ("AO", 0, ""), 0.0, ("ROUGHNESS", 0, "invert"))
BASE_NAMES = {"ORM": "BaseColor", "GLTF": "BaseColor", "UNREAL": "BaseColor", "UNITY": "BaseMap"}
PACKED_NAMES = {"ORM": "ORM", "GLTF": "OcclusionRoughnessMetallic", "UNREAL": "ORM", "UNITY": "MaskMap"}


def files(packing, channels):
    """The files a texture set of ``channels`` (the ones baked or filled) is written as: ``[(name, slots, colour)]``.

    ``colour`` files are stored with the sRGB transfer in 8- and 16-bit files (base colour, emission); every other
    file holds data as it is. A packed preset always writes its base colour and its packed map, filling a slot
    nothing baked with ``DEFAULTS``; the other maps are written only for the channels the set has.
    """
    used = set(channels)
    out = []
    if packing == "SEPARATE":
        out.append(("BaseColor", _rgb("BASE_COLOR"), True))
        for channel, name in (("METALLIC", "Metallic"), ("ROUGHNESS", "Roughness")):
            if channel in used:
                out.append((name, _one(channel), False))
        if "NORMAL" in used:
            out.append(("Normal", _rgb("NORMAL"), False))
        if "EMISSION" in used:
            out.append(("Emission", _rgb("EMISSION"), True))
        for channel, name in (("ALPHA", "Alpha"), ("AO", "AO"), ("HEIGHT", "Height")):
            if channel in used:
                out.append((name, _one(channel), False))
        return out
    base = _rgb("BASE_COLOR") + ((("ALPHA", 0, ""),) if "ALPHA" in used else ())
    out.append((BASE_NAMES[packing], base, True))
    out.append((PACKED_NAMES[packing], UNITY_MASK if packing == "UNITY" else ORM_SLOTS, False))
    if "NORMAL" in used:
        out.append(("Normal", _rgb("NORMAL", flip_green=packing in DIRECTX), False))
    if "EMISSION" in used:
        out.append(("Emission", _rgb("EMISSION"), True))
    if "HEIGHT" in used:
        out.append(("Height", _one("HEIGHT"), False))
    return out


def slot_channels(packing):
    """The channels a packed preset's packed map reads (a bake fills them, else ``DEFAULTS``)."""
    if packing == "SEPARATE":
        return ()
    return tuple(slot[0] for slot in (UNITY_MASK if packing == "UNITY" else ORM_SLOTS) if isinstance(slot, tuple))


def depth_of(name, request):
    """A file's bit depth: the request's, else 8 (16 for a normal or height map) in PNG and 16 (half) in OpenEXR."""
    if "depth" in request:
        return request["depth"]
    if request.get("format", "PNG") == "OPEN_EXR":
        return 16
    return 16 if name in DEEP_BY_DEFAULT else 8


def safe_stem(target_name):
    """An object name as a file-name stem: letters, digits, '-', '_' and '.' (others become '_'), at most 80."""
    return ("".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in target_name).strip("._") or "Object")[:80]


def stems(target_names):
    """One file-name stem per target, unique however a file system folds case: names that clean to the same stem
    ("Hull A", "Hull_A", "hull_a") get "-2", "-3" ... in their order."""
    taken, out = set(), []
    for name in target_names:
        base = safe_stem(name)
        stem, number = base, 1
        while stem.lower() in taken:
            number += 1
            stem = "%s-%d" % (base[:76], number)
        taken.add(stem.lower())
        out.append(stem)
    return out


def file_name(stem, name, extension, tile=None):
    """``<stem>_<Map>.png`` (``stems``), with the UDIM tile number before the extension: ``Crate_Normal.1002.png``."""
    return "%s_%s%s%s" % (stem, name, "" if tile is None else ".%d" % tile, extension)


def set_files(packing, extension, targets):
    """Every file a set writes, ``[(stem, map, tile, file)]`` for ``targets`` ``[(stem, channels, tiles)]``; raises
    BakeMapsError when two would be the same file on a case-folding file system."""
    out, seen = [], {}
    for stem, channels, tiles in targets:
        for name, _slots, _colour in files(packing, channels):
            for tile in tiles or [None]:
                path = file_name(stem, name, extension, tile)
                if path.lower() in seen:
                    raise BakeMapsError("Two maps would both be written as %s; rename one of the objects" % path,
                                        code="VALIDATION")
                seen[path.lower()] = path
                out.append((stem, name, tile, path))
    return out


def card_request(size, packing, image_format, margin, material, maps, deep, stem):
    """The request the Bake Maps card's choices make: ``maps`` None for every map the materials use, ``deep`` for 16
    bits in every PNG, ``stem`` the folder's object name (already safe as a file name)."""
    request = {"kind": KIND, "resolution": [size, size], "packing": packing, "format": image_format,
               "margin": margin, "material": material, "queue": True}
    if maps is not None:
        request["maps"] = [channel for channel in CHANNELS if channel in maps]
    if deep and image_format == "PNG":
        request["depth"] = 16
    request["output"] = "Texture sets/%s maps" % stem   # i18n: a workspace folder the agent reads, named in English
    return request


def _error(message):
    return BakeMapsError(message, code="VALIDATION")


def validate(args, request, handle_check):
    """The BAKE_MAPS fields of ``args`` checked into ``request`` (``job_spec.validate_job`` did the shared ones).

    ``target`` (one object) or ``targets`` (several) name what is baked onto; ``sources`` makes it a high-to-low
    bake onto exactly one target. ``handle_check(value, what)`` checks an object handle.
    """
    if ("target" in args) == ("targets" in args):
        raise _error("BAKE_MAPS needs target (one object) or targets (several), not both")
    if "targets" in args:
        targets = args["targets"]
        if type(targets) is not list or not 1 <= len(targets) <= MAX_TARGETS:
            raise _error("targets lists 1 to %d observed object handles" % MAX_TARGETS)
        request["targets"] = [handle_check(item, "Each of targets") for item in targets]
        if len(set(request["targets"])) != len(targets):
            raise _error("targets names each object once")
    else:
        request["targets"] = [request.pop("target")]
    request.pop("target", None)
    if "sources" in args and len(request["targets"]) != 1:
        raise _error("A bake from sources bakes onto exactly one target")
    if "maps" in args:
        maps = args["maps"]
        if type(maps) is not list or not maps or any(item not in CHANNELS for item in maps) \
                or len(set(maps)) != len(maps):
            raise _error("maps lists channels from: " + ", ".join(CHANNELS) + ", each once")
        request["maps"] = [channel for channel in CHANNELS if channel in maps]
    packing = args.get("packing", "SEPARATE")
    if packing not in PACKINGS:
        raise _error("packing must be one of: " + ", ".join(PACKINGS))
    request["packing"] = packing
    if "margin" in args:
        margin = args["margin"]
        if type(margin) is not int or not 0 <= margin <= MAX_MARGIN:
            raise _error("margin is a whole number of pixels from 0 to %d" % MAX_MARGIN)
        request["margin"] = margin
    request.setdefault("margin", DEFAULT_MARGIN)
    image_format = request.setdefault("format", "PNG")
    if image_format not in FORMATS:
        raise _error("BAKE_MAPS writes PNG or OPEN_EXR files")
    if "depth" in args:
        depth = args["depth"]
        if type(depth) is not int or depth not in DEPTHS:
            raise _error("depth is 8, 16 or 32 bits")
        if (image_format == "PNG") != (depth in (8, 16)):
            raise _error("PNG files are 8 or 16 bits; OPEN_EXR files 16 (half) or 32 (float)")
        request["depth"] = depth
    if "material" in args:
        if type(args["material"]) is not bool:
            raise _error("material must be true or false")
        request["material"] = args["material"]
    request.setdefault("resolution", list(DEFAULT_RESOLUTION))
    if "output" not in request:
        raise _error("BAKE_MAPS needs output: a new workspace folder for the texture set")
    return request


# What angelo_job_start declares to the model: every request carries it, so only what an agent needs. The Bake
# Maps card's own choices (several targets in one job, a chosen list of maps, the margin, 16 bits for every map) are
# fields the validator accepts (``FIELDS``) but the declaration leaves out: an agent bakes several objects as one job
# each, and one map with BAKE_TEXTURE.
SCHEMA = {
    "packing": {"type": "string", "enum": list(PACKINGS)},
    "material": {"type": "boolean"},
}
CARD_FIELDS = frozenset({"targets", "maps", "margin", "depth"})   # job_spec.validate_job(..., person=True) only.
FIELDS = frozenset({"target", "sources", "cage", "extrusion", "ray_distance", "output", "resolution", "samples",
                    "format", *SCHEMA})
DESCRIPTION = "BAKE_MAPS bakes a texture set: every PBR map the materials use, as files in output. "
