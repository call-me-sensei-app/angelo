# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unity's engine-link adapter: what a Unity model is to Angelo and how it is written for Unity
(``engine_link_assets`` has the adapter contract; doc/angelo/unity_link.rst is Unity's).

* A project is a folder holding ``Assets/`` and ``ProjectSettings/ProjectVersion.txt``. An asset is a model file
  under ``Assets/``, named on the wire by its project path as Unity writes it (``Assets/Props/crate.fbx``).
* ``.blend``: Angelo opens and saves it; Unity re-imports it by running Blender itself. ``.glb`` and ``.fbx``: Angelo
  edits a working .blend in the project's ``.angelo`` folder (Unity imports only ``Assets`` and ``Packages``) and
  writes the model back on every save; the model's ``.meta`` (its GUID and the person's import settings) is never
  touched, so Unity re-applies them on re-import.
* **The FBX profile** (``UNITY_FBX``) is the one Unity itself passes to Blender to turn a .blend into FBX
  (``Contents/Resources/AssetImporting/Unity-BlenderToFBX.py`` in Unity 6000.5.5f1 and 6000.5.6f1, identical), so a
  .fbx Angelo writes imports as a .blend would. ``.glb`` uses the shared glTF profile (``GLTF_DURABLE``), which the
  package's glTFast preview reads too.
"""

import os
from pathlib import Path

from .engine_link_assets import GLTF_DURABLE, Asset, AssetError, inside, live_gltf

ENGINE, DISPLAY = "unity", "Unity"
CAPABILITIES = ("durable", "live")   # The package shows live scenes (with glTFast) and re-imports on save.
KINDS = (".blend", ".glb", ".fbx")
PLUGIN_SOURCE = Path(__file__).with_name("unity_package") / "com.hyperbond.angelo-link"
PLUGIN_FOLDER = ("Packages", "com.hyperbond.angelo-link")   # An embedded package: no manifest.json edit.
PLUGIN_IGNORE = (".DS_Store",)   # The .meta files stay: they carry the package's GUIDs.
ROOT = "Assets"

REFUSED = {   # code -> English, shown by the Unity package (its text is English); ``asset`` is the project path.
    "not_project": "That folder isn't a Unity project (it has no Assets folder and ProjectSettings/ProjectVersion.txt)",
    "outside": "{asset} is outside the project's Assets folder",
    "kind": "Angelo can edit .blend, .glb and .fbx models; {asset} is another kind",
    "missing": "{asset} doesn't exist yet. Save it in Unity first",
}

# Unity-BlenderToFBX.py's export_scene.fbx call (Blender 2.80 and later); the write-back adds filepath,
# check_existing=False and use_selection=False, as that script does.
UNITY_FBX = {
    "use_active_collection": False,
    "object_types": ["ARMATURE", "CAMERA", "LIGHT", "MESH", "OTHER", "EMPTY"],
    "use_mesh_modifiers": True,
    "mesh_smooth_type": "OFF",
    "use_custom_props": True,
    "bake_anim_use_nla_strips": False,
    "bake_anim_use_all_actions": False,
    "apply_scale_options": "FBX_SCALE_ALL",
}


def is_project(folder):
    return (isinstance(folder, str) and os.path.isdir(os.path.join(folder, ROOT))
            and os.path.isfile(os.path.join(folder, "ProjectSettings", "ProjectVersion.txt")))


def resolve(request):
    """The ``Asset`` for the request's ``asset`` (``Assets/...``) in the Unity project ``project``; raises
    AssetError."""
    project, path = request.get("project"), request.get("asset")
    if not isinstance(path, str) or not path.startswith(ROOT + "/") or len(path) > 1024:
        raise AssetError("outside", str(path)[:200], REFUSED["outside"])
    if not isinstance(project, str) or not os.path.isabs(project) or not is_project(project):
        raise AssetError("not_project", path, REFUSED["not_project"])
    root, file, parts = inside(project, path, REFUSED, path, KINDS)
    relative = "/".join(parts)
    return Asset(ENGINE, str(root), relative, str(file), file.suffix.lower(), relative)


def import_params(asset):
    """None read by Angelo: the package applies the importer's settings to its preview, and the re-import keeps the
    ``.meta``'s (doc/angelo/unity_link.rst, "Import settings")."""
    return {}


def live_options(params):
    return live_gltf(GLTF_DURABLE)


def durable(asset, params):
    """How saving writes the model: None for a .blend, glTF for a .glb, Unity's own FBX profile for a .fbx."""
    if asset.kind == ".blend":
        return None
    if asset.kind == ".fbx":
        return "fbx", dict(UNITY_FBX, object_types=list(UNITY_FBX["object_types"]))
    return "gltf", dict(GLTF_DURABLE)
