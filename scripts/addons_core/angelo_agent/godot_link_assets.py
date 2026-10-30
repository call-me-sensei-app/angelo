# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Godot's engine-link adapter: what a Godot asset is to Angelo and how it is exported for Godot
(``engine_link_assets`` has the adapter contract and what every engine shares).

* An asset is a ``res://`` path in a Godot project (a folder holding ``project.godot``): a ``.blend`` (Angelo opens
  and saves it; Godot re-imports it with its own Blender importer) or a ``.glb`` (Angelo edits a working ``.blend``
  in the project's ``.angelo`` folder, which Godot ignores, and writes the ``.glb`` back on every save). Other formats
  are refused with the reason, never half-supported.
* **Exporter options** are the ones Godot's own Blender importer passes (``godot_options``), read from the asset's
  ``.import`` file where the person changed them, so the live preview and the saved asset look the same in Godot.
  The table was recorded from Godot 4.7.2's requests to Blender (doc/angelo/godot_link.rst, "Exporter options").
"""

import os
from pathlib import Path

from .engine_link_assets import Asset, AssetError, inside, live_gltf

ENGINE, DISPLAY = "godot", "Godot"
CAPABILITIES = ("durable", "live")   # The plugin shows live scenes and re-imports on save.
KINDS = (".blend", ".glb")
PLUGIN_SOURCE = Path(__file__).with_name("godot_addon") / "angelo_link"   # The Godot plugin Angelo ships.
PLUGIN_FOLDER = ("addons", "angelo_link")
PLUGIN_IGNORE = ("*.import", ".DS_Store", "__pycache__")

REFUSED = {   # code -> English, shown by the Godot plugin (its text is English); ``asset`` is the res:// path.
    "not_project": "That folder isn't a Godot project (it has no project.godot)",
    "outside": "{asset} is outside the Godot project",
    "kind": "Angelo can edit .blend and .glb assets; {asset} is another kind. Export it as .glb in Godot first",
    "missing": "{asset} doesn't exist yet. Save it in Godot first",
}

# Godot 4.7.2's Blender-import defaults, as it sends them to Blender's glTF exporter.
GODOT_DEFAULTS = {
    "use_visible": False, "use_renderable": False, "use_active_collection": False,
    "export_lights": True, "export_cameras": True, "export_extras": True, "export_apply": True,
    "export_vertex_color": "ACTIVE", "export_texcoords": True, "export_normals": True, "export_tangents": True,
    "export_gn_mesh": False, "export_gpu_instances": False, "export_skins": True, "export_all_influences": True,
    "export_def_bones": False, "export_materials": "EXPORT", "export_image_format": "AUTO",
    "export_frame_range": True, "export_force_sampling": True, "export_animation_mode": "ACTIONS",
    "export_nla_strips": True, "export_animations": True, "export_yup": True,
    "export_import_convert_lighting_mode": "COMPAT",
}
_BOOLEANS = {   # .import key -> exporter option, both booleans.
    "blender/nodes/active_collection_only": "use_active_collection",
    "blender/nodes/punctual_lights": "export_lights",
    "blender/nodes/cameras": "export_cameras",
    "blender/nodes/custom_properties": "export_extras",
    "blender/meshes/uvs": "export_texcoords",
    "blender/meshes/normals": "export_normals",
    "blender/meshes/tangents": "export_tangents",
    "blender/meshes/export_geometry_nodes_instances": "export_gn_mesh",
    "blender/meshes/gpu_instances": "export_gpu_instances",
    "blender/meshes/export_bones_deforming_mesh_only": "export_def_bones",
    "blender/animation/limit_playback": "export_frame_range",
    "blender/animation/always_sample": "export_force_sampling",
}


def is_project(folder):
    return isinstance(folder, str) and os.path.isfile(os.path.join(folder, "project.godot"))


def resolve(request):
    """The ``Asset`` for the request's ``asset`` (a res:// path) in the Godot project ``project``; raises AssetError."""
    project, res = request.get("project"), request.get("asset")
    if not isinstance(res, str) or not res.startswith("res://") or len(res) > 1024:
        raise AssetError("outside", str(res)[:200], REFUSED["outside"])
    if not isinstance(project, str) or not os.path.isabs(project) or not is_project(project):
        raise AssetError("not_project", res, REFUSED["not_project"])
    root, path, parts = inside(project, res[len("res://"):], REFUSED, res, KINDS)
    relative = "/".join(parts)
    return Asset(ENGINE, str(root), "res://" + relative, str(path), path.suffix.lower(), relative)


def import_params(asset):
    return read_import_params(asset.path)


def read_import_params(asset_path):
    """The ``[params]`` of Godot's ``<asset>.import`` file as {key: bool | int | str}; {} when there is none."""
    params, section = {}, None
    try:
        with open(asset_path + ".import", encoding="utf-8") as handle:
            lines = handle.read(256 * 1024).splitlines()
    except (OSError, UnicodeError):
        return params
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "params" or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if value in {"true", "false"}:
            params[key] = value == "true"
        elif value.lstrip("-").isdigit():
            params[key] = int(value)
        else:
            params[key] = value.strip('"')
    return params


def godot_options(params):
    """The glTF exporter options Godot's Blender importer would use for an asset with these import ``params``."""
    options = dict(GODOT_DEFAULTS)
    for key, option in _BOOLEANS.items():
        if isinstance(params.get(key), bool):
            options[option] = params[key]
    visible = params.get("blender/nodes/visible")
    if visible in (0, 1, 2):
        options.update(use_visible=visible == 1, use_renderable=visible == 2)
    if params.get("blender/nodes/modifiers") in (0, 1):
        options["export_apply"] = params["blender/nodes/modifiers"] == 1
    colors = params.get("blender/meshes/vertex_colors")
    if colors in (0, 1, 2):
        options["export_vertex_color"] = ("MATERIAL", "ACTIVE", "NONE")[colors]
    skins = params.get("blender/meshes/skins")
    if skins in (0, 1, 2):
        options.update(export_skins=skins > 0, export_all_influences=skins == 2)
    if params.get("blender/materials/export_materials") in (0, 1, 2):
        options["export_materials"] = "PLACEHOLDER" if params["blender/materials/export_materials"] == 0 else "EXPORT"
    if isinstance(params.get("blender/animation/group_tracks"), bool):
        options["export_animation_mode"] = "ACTIONS" if params["blender/animation/group_tracks"] else "ACTIVE_ACTIONS"
    return options


def live_options(params):
    """The live preview's options: Godot's, with no animation and at the current frame (``live_gltf``). The re-import
    on save brings the animations."""
    return live_gltf(godot_options(params))


def durable(asset, params):
    """How saving writes the asset: None for a .blend (Angelo's save is the asset), else (exporter, options)."""
    return None if asset.kind == ".blend" else ("gltf", godot_options(params))
