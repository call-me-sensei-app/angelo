# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unreal's engine-link adapter: what an Unreal mesh asset is to Angelo and how its source is written back
(``engine_link_assets`` has the adapter contract; doc/angelo/unreal_link.rst is Unreal's).

* A project is a folder holding a ``.uproject``. An asset is named on the wire by its package path
  (``/Game/Props/SM_Crate``), and the request names its ``source``: the one file its import data records, which must
  be inside the project. Angelo edits a working .blend at ``<project>/.angelo/<source path>.blend`` and writes the
  source back on every save; the plugin then re-imports the asset with its own import settings (Interchange replays
  them), so Angelo reads none.
* **Kinds: .glb** for now, written with the shared glTF profile (``GLTF_DURABLE``; Interchange converts glTF's axes
  and metres). ``.fbx`` is still refused with the reason: its export profile is recorded (``UNREAL_FBX``) and its axes
  calibrated against Unreal's importer (unreal_link.rst, "Scale and axes"), but no FBX round trip has been proved.
* **Durable only**: the Python plugin re-imports on save and shows no live scenes (``CAPABILITIES``); a later C++
  layer that does says ``live`` in its handshake.
"""

import os
from pathlib import Path

from .engine_link_assets import GLTF_DURABLE, Asset, AssetError, _listing, inside, live_gltf

ENGINE, DISPLAY = "unreal", "Unreal Engine"
CAPABILITIES = ("durable",)
# An asset can't be opened by its name alone: the request names the source file its import data records, which only
# the plugin can read (the agent's OPEN refuses, engine_link_tool_host).
OPENS_BY_NAME = False
KINDS = (".glb",)
PLUGIN_SOURCE = Path(__file__).with_name("unreal_plugin") / "AngeloLink"
PLUGIN_FOLDER = ("Plugins", "AngeloLink")
PLUGIN_IGNORE = (".DS_Store", "__pycache__")

# The FBX profile for Unreal: Epic's Send to Unreal defaults (EpicGames/BlenderTools, send2ue
# resources/setting_templates/default.json, "blender" > "export_method" > "fbx"), with the forward axis calibrated in
# UE 5.8.2 (unreal_link.rst, "Scale and axes"). Not used yet: Unreal's .fbx sources are refused (``KINDS``).
UNREAL_FBX = {
    # Scale: 1 Blender unit is 1 m, and Unreal's importer converts to cm; the scene's unit scale must be 1.
    "global_scale": 1.0, "apply_unit_scale": True, "apply_scale_options": "FBX_SCALE_NONE",
    "bake_space_transform": False, "axis_forward": "Y", "axis_up": "Z",
    # Geometry: modifiers applied, face smoothing (Epic: let Blender triangulate, not the exporter), no tangents.
    "use_mesh_modifiers": True, "use_mesh_modifiers_render": True, "mesh_smooth_type": "FACE", "use_tspace": False,
    "use_subsurf": False, "use_mesh_edges": False, "use_custom_props": False,
    # Skeleton: no leaf bones, Y along the bone; the armature as a null.
    "add_leaf_bones": False, "primary_bone_axis": "Y", "secondary_bone_axis": "X", "armature_nodetype": "NULL",
    "use_armature_deform_only": False,
    # Animation: every bone, every frame, nothing simplified away (Blender's default 1.0 is lossy).
    "bake_anim": True, "bake_anim_use_all_bones": True, "bake_anim_force_startend_keying": True,
    "bake_anim_step": 1.0, "bake_anim_simplify_factor": 0.0,
}

REFUSED = {   # code -> English, shown by the Unreal plugin (its text is English); ``asset`` is the package path.
    "not_project": "That folder isn't an Unreal project (it has no .uproject file)",
    "outside": "{asset}'s source file is outside the project folder. Move it into the project and re-import first",
    "kind": "Angelo can edit Unreal assets imported from .glb for now; {asset} was imported from another kind",
    "missing": "{asset}'s source file doesn't exist. Re-import it from a .glb first",
    "package": "{asset} isn't a package path (such as /Game/Props/SM_Crate)",
}


PROJECT_SCAN = 5000   # Entries of a folder read at most for its .uproject; past it, it isn't taken for a project.


def is_project(folder):
    """Whether ``folder`` holds a .uproject, read entry by entry (``engine_link_facts.listing``: never the whole
    folder of a huge or slow drive first, break-it 9-L3)."""
    if not isinstance(folder, str):
        return False
    try:
        for read, entry in enumerate(_listing(folder), 1):
            if entry.name.lower().endswith(".uproject"):
                return True
            if read >= PROJECT_SCAN:
                return False
    except OSError:
        return False
    return False


def _package(path):
    parts = path.split("/") if isinstance(path, str) and path.startswith("/") and len(path) <= 1024 else []
    return len(parts) >= 3 and all(part and part not in {".", ".."} and "\\" not in part for part in parts[1:])


def resolve(request):
    """The ``Asset`` for the request's package path ``asset`` and its ``source`` file in the Unreal project
    ``project``; raises AssetError."""
    project, package, source = request.get("project"), request.get("asset"), request.get("source")
    if not _package(package):
        raise AssetError("package", str(package)[:200], REFUSED["package"])
    if not isinstance(project, str) or not os.path.isabs(project) or not is_project(project):
        raise AssetError("not_project", package, REFUSED["not_project"])
    if not isinstance(source, str) or not os.path.isabs(source) or len(source) > 4096:
        raise AssetError("outside", package, REFUSED["outside"])
    root = os.path.realpath(project)
    try:
        relative = os.path.relpath(os.path.abspath(source), root)
    except ValueError:   # Another drive.
        raise AssetError("outside", package, REFUSED["outside"]) from None
    root, path, parts = inside(project, relative.replace(os.sep, "/"), REFUSED, package, KINDS)
    return Asset(ENGINE, str(root), package, str(path), path.suffix.lower(), "/".join(parts))


def import_params(asset):
    return {}


def live_options(params):
    return live_gltf(GLTF_DURABLE)


def durable(asset, params):
    return "gltf", dict(GLTF_DURABLE)
