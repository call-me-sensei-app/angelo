# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The device budget check: what a written .glb costs on a target device, against that device's profile. No Blender.

A **profile** is data (a JSON file), never code: Angelo ships a few in ``device_profiles/``; a team adds its own in
its config folder or project, and a later folder replaces a profile with the same ``id``. A profile names the
frame rate it is for, how the device stores each kind of texture (``texture_formats``: a role -> format, with a
``default``), optional engine facts (``passes_per_primitive``: an outline or shadow pass draws each primitive again;
``max_texture_size``) and **budgets**. Every budget is optional: an unset one is measured and reported with no
verdict, and a profile with none set never passes. ``basis`` says where its numbers come from.

``measure_bytes`` adds to ``web_budget.measure_bytes`` what a phone pays and a web page does not: vertices, bones,
skin influences per vertex, morph targets and which role each texture plays. ``check`` compares one asset with a
profile; ``check_app`` sums the files that ship against the profile's app-size budget. Results are plain data with
names, never local paths. doc/angelo/device_budget.rst is the contract.
"""

import json
import math
import os

from . import web_budget

SCHEMA = 1

# Compressed formats as a GPU stores them: (block width, block height, bytes per block). ASTC is always 128-bit
# blocks (Khronos Data Format Specification, ASTC); ETC2 RGB is 64-bit and ETC2 RGBA (with EAC alpha) 128-bit blocks
# (same spec, ETC2); BC1 64-bit, BC3/BC7 128-bit; RGBA8 is 4 bytes a texel.
FORMATS = {
    "ASTC_4x4": (4, 4, 16), "ASTC_5x5": (5, 5, 16), "ASTC_6x6": (6, 6, 16), "ASTC_8x8": (8, 8, 16),
    "ASTC_10x10": (10, 10, 16), "ASTC_12x12": (12, 12, 16),
    "ETC2_RGB": (4, 4, 8), "ETC2_RGBA": (4, 4, 16),
    "BC1": (4, 4, 8), "BC3": (4, 4, 16), "BC7": (4, 4, 16),
    "RGBA8": (1, 1, 4),
}
ROLES = ("base_color", "metallic_roughness", "normal", "occlusion", "emissive", "other")

# Budget name -> (measurement it reads, unit). Lower is better for every one.
BUDGETS = {
    "triangles": "triangles",
    "vertices": "vertices",
    "draw_calls": "draw calls",
    "texture_memory_mb": "MiB of GPU memory",
    "bones": "bones in the largest skin",
    "influences": "bone influences per vertex",
    "morph_targets": "morph targets on the largest mesh",
    "file_mb": "MiB",
}
APP_BUDGET = "app_mb"
TOP = 5   # Contributors listed per row.


class ProfileError(ValueError):
    """A profile that can't be used, or a format this check doesn't know; the message names the field."""


def texture_bytes(width, height, fmt, mips=True):
    """Bytes a ``width`` x ``height`` texture takes on a GPU in ``fmt``, with its full mip chain unless ``mips`` is
    false. Each level pays whole blocks, so small levels of a block format cost at least one block."""
    if fmt not in FORMATS:
        raise ProfileError(f"unknown texture format {fmt}; known: {', '.join(sorted(FORMATS))}")
    block_w, block_h, size = FORMATS[fmt]
    total, w, h = 0, max(int(width), 1), max(int(height), 1)
    while True:
        total += math.ceil(w / block_w) * math.ceil(h / block_h) * size
        if not mips or (w == 1 and h == 1):
            return total
        w, h = max(w // 2, 1), max(h // 2, 1)


def validate_profile(profile):
    """The profile with defaults filled in, or ProfileError naming what's wrong."""
    if not isinstance(profile, dict):
        raise ProfileError("a profile is a JSON object")
    if profile.get("schema") != SCHEMA:
        raise ProfileError(f"schema must be {SCHEMA}")
    for field in ("id", "name"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise ProfileError(f"{field} must be a non-empty text")
    fps = profile.get("fps")
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise ProfileError("fps must be a positive number")
    formats = profile.get("texture_formats") or {"default": "RGBA8"}
    if not isinstance(formats, dict) or "default" not in formats:
        raise ProfileError("texture_formats needs a default")
    for role, fmt in formats.items():
        if role != "default" and role not in ROLES:
            raise ProfileError(f"texture_formats: unknown role {role}; known: {', '.join(ROLES)}")
        if fmt not in FORMATS:
            raise ProfileError(f"texture_formats.{role}: unknown format {fmt}")
    budgets = profile.get("budgets") or {}
    if not isinstance(budgets, dict):
        raise ProfileError("budgets must be an object")
    for name, value in budgets.items():
        if name not in BUDGETS and name != APP_BUDGET:
            raise ProfileError(f"budgets: unknown budget {name}; known: {', '.join(sorted(BUDGETS) + [APP_BUDGET])}")
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
            raise ProfileError(f"budgets.{name} must be a number of at least 0, or null")
    passes = profile.get("passes_per_primitive", 1)
    if not isinstance(passes, (int, float)) or passes < 1:
        raise ProfileError("passes_per_primitive must be at least 1")
    limit = profile.get("max_texture_size")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ProfileError("max_texture_size must be a positive whole number")
    return dict(profile, texture_formats=formats, budgets=budgets, passes_per_primitive=passes)


def load_profiles(folders):
    """({id: profile}, [problem]) from the ``*.json`` files of each folder in order; a later folder replaces an
    earlier profile with the same id. A missing folder is skipped; a file that can't be used is listed by its name
    with the reason, and never stops the others."""
    profiles, problems = {}, []
    for folder in folders:
        try:
            names = sorted(name for name in os.listdir(folder) if name.endswith(".json"))
        except OSError:
            continue
        for name in names:
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    profile = validate_profile(json.load(handle))
            except (OSError, ValueError) as error:   # json.JSONDecodeError and ProfileError are ValueErrors.
                problems.append({"file": name, "error": str(error)})
                continue
            profiles[profile["id"]] = profile
    return profiles, problems


def _texture_roles(document):
    """{image index: sorted roles} from the materials that sample each image."""
    textures = document.get("textures", [])
    roles = {}

    def note(info, role):
        if isinstance(info, dict) and isinstance(info.get("index"), int) and info["index"] < len(textures):
            source = textures[info["index"]].get("source")
            if isinstance(source, int):
                roles.setdefault(source, set()).add(role)

    for material in document.get("materials", []):
        pbr = material.get("pbrMetallicRoughness") or {}
        note(pbr.get("baseColorTexture"), "base_color")
        note(pbr.get("metallicRoughnessTexture"), "metallic_roughness")
        for slot, role in (("normalTexture", "normal"), ("occlusionTexture", "occlusion"),
                           ("emissiveTexture", "emissive")):
            note(material.get(slot), role)
        for extension in (material.get("extensions") or {}).values():
            for key, value in (extension.items() if isinstance(extension, dict) else ()):
                if key.endswith("Texture"):
                    note(value, "other")
    return {index: sorted(found) for index, found in roles.items()}


def measure_bytes(data):
    """``web_budget.measure_bytes`` plus vertices, bones, influences, morph targets and texture roles."""
    found = web_budget.measure_bytes(data)
    document, _ = web_budget.read_glb(data)
    accessors, meshes = document.get("accessors", []), document.get("meshes", [])
    instances = web_budget._instances(document)

    def count(index):
        return accessors[index]["count"] if isinstance(index, int) and index < len(accessors) else 0

    vertices = influences = largest_morphs = total_morphs = morph_vertices = 0
    for index, (copies, _) in instances.items():
        mesh_morphs = 0
        for primitive in (meshes[index].get("primitives", []) if index < len(meshes) else []):
            attributes = primitive.get("attributes", {})
            vertex_count = count(attributes.get("POSITION"))
            vertices += copies * vertex_count
            sets = sum(1 for name in attributes if name.startswith("JOINTS_"))
            influences = max(influences, 4 * sets)
            targets = primitive.get("targets", [])
            mesh_morphs = max(mesh_morphs, len(targets))
            morph_vertices += copies * len(targets) * vertex_count
        largest_morphs = max(largest_morphs, mesh_morphs)
        total_morphs += mesh_morphs
    skins = document.get("skins", [])
    roles = _texture_roles(document)
    for index, image in enumerate(found["textures"]["images"]):
        image["index"] = index
        image["roles"] = roles.get(index, [])
    found.update(vertices=vertices, influences=influences,
                 bones={"largest_skin": max((len(skin.get("joints", [])) for skin in skins), default=0),
                        "skins": len(skins)},
                 morph_targets={"largest_mesh": largest_morphs, "total": total_morphs, "vertices": morph_vertices})
    return found


def measure(path):
    with open(path, "rb") as handle:
        found = measure_bytes(handle.read())
    found["name"] = os.path.basename(path)
    return found


def _format(profile, roles):
    """The format a texture with ``roles`` is stored in: the costliest of its roles' formats."""
    formats = profile["texture_formats"]
    choices = [formats.get(role, formats["default"]) for role in roles] or [formats["default"]]
    return max(choices, key=lambda fmt: FORMATS[fmt][2] / (FORMATS[fmt][0] * FORMATS[fmt][1]))


def _row(metric, value, budget, contributors=None):
    passed = None if budget is None else value <= budget
    row = {"metric": metric, "unit": BUDGETS[metric], "value": value, "budget": budget, "passed": passed,
           "ratio": round(value / budget, 3) if budget else None}
    if contributors is not None:
        row["contributors"] = contributors[:TOP]
    return row


def check(measured, profile):
    """One asset's measurement (``measure``/``measure_bytes``) against ``profile``: a row per budget, the textures
    over the device's size limit, and ``passed`` (True only when at least one budget is set and nothing failed;
    None when no budget is set)."""
    profile = validate_profile(profile)
    budgets = profile["budgets"]
    textures = []
    for image in measured["textures"]["images"]:
        fmt = _format(profile, image.get("roles", []))
        textures.append({"index": image.get("index"), "name": image.get("name", ""), "roles": image.get("roles", []),
                         "width": image["width"], "height": image["height"], "format": fmt,
                         "bytes": texture_bytes(image["width"], image["height"], fmt)})
    textures.sort(key=lambda item: -item["bytes"])
    gpu = sum(item["bytes"] for item in textures)
    rows = [
        _row("triangles", measured["triangles"], budgets.get("triangles")),
        _row("vertices", measured["vertices"], budgets.get("vertices")),
        _row("draw_calls", int(math.ceil(measured["draw_calls"] * profile["passes_per_primitive"])),
             budgets.get("draw_calls")),
        _row("texture_memory_mb", round(gpu / 2 ** 20, 2), budgets.get("texture_memory_mb"), textures),
        _row("bones", measured["bones"]["largest_skin"], budgets.get("bones")),
        _row("influences", measured["influences"], budgets.get("influences")),
        _row("morph_targets", measured["morph_targets"]["largest_mesh"], budgets.get("morph_targets")),
        _row("file_mb", round(measured["bytes"] / 2 ** 20, 2), budgets.get("file_mb")),
    ]
    limit = profile.get("max_texture_size")
    oversize = sorted((item for item in textures if limit and max(item["width"], item["height"]) > limit),
                      key=lambda item: item["index"] if item["index"] is not None else -1)
    failed = [row["metric"] for row in rows if row["passed"] is False]
    judged = any(row["passed"] is not None for row in rows) or bool(limit)
    passed = None if not judged else not failed and not oversize
    return {"profile": profile["id"], "name": profile["name"], "fps": profile["fps"],
            "basis": profile.get("basis", ""), "rows": rows, "oversize_textures": oversize,
            "max_texture_size": limit, "failed": failed, "passed": passed}


def check_app(paths, profile):
    """The files that ship, summed against the profile's ``app_mb`` (None when unset). Files by name only."""
    profile = validate_profile(profile)
    files = [{"name": os.path.basename(path), "bytes": os.path.getsize(path)} for path in paths]
    total = sum(item["bytes"] for item in files)
    budget = profile["budgets"].get(APP_BUDGET)
    return {"profile": profile["id"], "bytes": total, "mb": round(total / 2 ** 20, 2), "budget_mb": budget,
            "files": files, "passed": None if budget is None else total / 2 ** 20 <= budget}
