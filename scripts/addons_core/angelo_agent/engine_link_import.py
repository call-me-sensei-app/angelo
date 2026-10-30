# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The working .blend of a linked asset Angelo can't open itself (.glb, .fbx): a new document with the asset imported
by Blender's importer for its kind, saved at the asset's working path (doc/angelo/engine_link.rst, "Saving and working
files"). Main thread; ``engine_link.accept`` runs it in the window the asset opens in.
"""

import os

import bpy

from . import engine_link_assets as assets


def working_file(asset, writing):
    """Import ``asset`` into a new document and save it as its working .blend; returns the operator result. A working
    .blend the asset no longer matches (changed outside Angelo) is kept beside it as ``<name>.previous.blend`` (the
    card says so). ``writing(True/False)`` brackets the save, so the link doesn't take it for a person's edit."""
    assets.ensure_working_folder(asset)
    if os.path.isfile(asset.editable):
        os.replace(asset.editable, assets.previous_path(asset))
    result = bpy.ops.wm.read_homefile(use_empty=True)
    if "FINISHED" not in result:
        return result
    result = import_file(asset.path, asset.kind)
    if "FINISHED" not in result:
        return result
    writing(True)
    try:
        result = bpy.ops.wm.save_as_mainfile(filepath=asset.editable, check_existing=False)
    finally:
        writing(False)
    if "FINISHED" in result:
        assets.write_stamp(asset)
    return result


def import_file(path, kind):
    """Blender's importer for a linked asset's kind, into the current (empty) document; Solid view then shows what
    each imported material's graph shows (``material_display_sync``, as every import does)."""
    from . import material_display_sync as display
    before = display.known()
    if kind == ".fbx":
        import addon_utils
        addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
        result = bpy.ops.import_scene.fbx(filepath=path)
    else:
        from .web_export import _addon
        _addon()
        from .gltf_morph import import_options
        result = bpy.ops.import_scene.gltf(filepath=path, **import_options(path))
    if "FINISHED" in result:
        display.sync_added(before)
    return result
