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
  and metres). ``.fbx`` is refused with the reason until its export profile is recorded against Unreal's importer
  (unreal_link.rst, "Scale and axes").
* **Durable only**: the Python plugin re-imports on save and shows no live scenes (``CAPABILITIES``); a later C++
  layer that does says ``live`` in its handshake.
"""

import os
from pathlib import Path

from .engine_link_assets import GLTF_DURABLE, Asset, AssetError, inside, live_gltf

ENGINE, DISPLAY = "unreal", "Unreal Engine"
CAPABILITIES = ("durable",)
KINDS = (".glb",)
PLUGIN_SOURCE = Path(__file__).with_name("unreal_plugin") / "AngeloLink"
PLUGIN_FOLDER = ("Plugins", "AngeloLink")
PLUGIN_IGNORE = (".DS_Store", "__pycache__")

REFUSED = {   # code -> English, shown by the Unreal plugin (its text is English); ``asset`` is the package path.
    "not_project": "That folder isn't an Unreal project (it has no .uproject file)",
    "outside": "{asset}'s source file is outside the project folder. Move it into the project and re-import first",
    "kind": "Angelo can edit Unreal assets imported from .glb for now; {asset} was imported from another kind",
    "missing": "{asset}'s source file doesn't exist. Re-import it from a .glb first",
    "package": "{asset} isn't a package path (such as /Game/Props/SM_Crate)",
}


def is_project(folder):
    try:
        return isinstance(folder, str) and any(name.lower().endswith(".uproject") for name in os.listdir(folder))
    except OSError:
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
