# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Human Base Meshes Angelo bundles: their table and whether a file is whole.

Blender publishes the Human Base Meshes (CC0): heads with eyes, bodies, hands,
feet, eyes, jaws, a skull and a skeleton, with quad topology for Multires
sculpting, closed volumes, UVs and face sets. Angelo ships 21 of them, one
``.blend`` each, in ``human_base_meshes/`` (the four with several Multires
levels, 85 % of the bundle's bytes, are left out). A person starts from one in
the chat (the composer's + menu, ``chosen_tools``); ``asset_start`` places it.

The facts come from ``human_base_meshes.json``, made with the files from
Blender's official bundle by ``tests/python/angelo_agent/make_human_base_meshes.py``
(pinned SHA-256, provenance in the table) and checked against them by
``test_native_human_base_meshes``. The file is reached through
``bpy.utils.system_resource`` (never an absolute path: a Windows install folder
names the person).
"""

import functools
import json
import os
from pathlib import Path

TABLE = Path(__file__).with_name("human_base_meshes.json")
FOLDER = Path(__file__).with_name("human_base_meshes")
RELATIVE = "addons_core/angelo_agent/human_base_meshes"   # system_resource finds folders only, never a file
PROPERTY = "angelo_base_mesh"   # On objects Angelo placed from an asset: the asset's name (asset_start).


@functools.lru_cache(maxsize=1)
def table():
    return json.loads(TABLE.read_text(encoding="utf-8"))


def _key(name):
    return " ".join(name.split()).casefold()


def _matches(asset, name):
    """The asset's name (spacing and case ignored) or its file's stem ("head_sculpting_realistic")."""
    return _key(asset["name"]) == _key(name) or asset["file"][:-len(".blend")] == name.strip().casefold()


def whole(path, asset):
    """``path`` holds the asset's whole file (a build or checkout can hold a Git LFS pointer instead)."""
    try:
        return bool(path) and os.path.getsize(path) == asset["bytes"]
    except OSError:
        return False
