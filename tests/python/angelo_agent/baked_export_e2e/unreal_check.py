# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal side of the baked-material proof, run inside a headless Unreal Editor as the throwaway project's
``Content/Python/init_unreal.py`` (``run.py`` writes it there). Test only; never shipped.

1. Imports Angelo's ``link.glb`` through Interchange (the project's default stack, as a person's first import) and
   records each static mesh's material instances: parent material and texture parameters, and for each normal map
   whether Unreal flips its green channel (``flip_green_channel``), which settles the packing a .glb needs.
2. FBX axis calibration: imports ``axes.glb`` and ``axes_<forward>.fbx`` (the same asymmetric mesh, written with
   ``UNREAL_FBX`` and each candidate forward axis) and records each static mesh's bounds; the forward whose bounds
   equal the .glb's is the one where Angelo's two paths agree.

Writes ``ANGELO_UNREAL_REPORT`` and quits the editor.
"""

import json
import os
import traceback

import unreal

REPORT = os.environ["ANGELO_UNREAL_REPORT"]
FOLDER = os.environ["ANGELO_UNREAL_ASSETS"]
state = {"engine": unreal.SystemLibrary.get_engine_version(), "meshes": {}, "axes": {}}


def import_file(path, destination):
    task = unreal.AssetImportTask()
    for key, value in (("filename", path), ("destination_path", destination), ("automated", True), ("save", True),
                       ("replace_existing", True)):
        task.set_editor_property(key, value)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    loaded = [unreal.EditorAssetLibrary.load_asset(str(item)) for item in task.get_editor_property("imported_object_paths")]
    return [item for item in loaded if item is not None]


def describe_material(material):
    found = {"class": type(material).__name__, "name": material.get_name()}
    if isinstance(material, unreal.MaterialInstance):
        parent = material.get_editor_property("parent")
        found["parent"] = parent.get_path_name() if parent else None
        textures = {}
        for value in material.get_editor_property("texture_parameter_values"):
            texture = value.get_editor_property("parameter_value")
            name = str(value.get_editor_property("parameter_info").get_editor_property("name"))
            textures[name] = None if texture is None else {
                "texture": texture.get_name(), "srgb": texture.get_editor_property("srgb"),
                "flip_green_channel": texture.get_editor_property("flip_green_channel"),
                "compression": str(texture.get_editor_property("compression_settings"))}
        found["textures"] = textures
    return found


def box(mesh):
    bounds = mesh.get_bounding_box()
    return [[round(v, 2) for v in (corner.x, corner.y, corner.z)] for corner in (bounds.min, bounds.max)]


def main(_delta):
    unreal.unregister_slate_post_tick_callback(state.pop("tick"))
    try:
        for item in import_file(os.path.join(FOLDER, "link.glb"), "/Game/Baked"):
            if isinstance(item, unreal.StaticMesh):
                state["meshes"][item.get_name()] = [describe_material(slot.get_editor_property("material_interface"))
                                                    for slot in item.get_editor_property("static_materials")]
            elif isinstance(item, unreal.SkeletalMesh):   # Parts of a rigged model become one skeletal mesh.
                state.setdefault("skeletal", {})[item.get_name()] = [
                    describe_material(slot.get_editor_property("material_interface"))
                    for slot in item.get_editor_property("materials")]
        for name in sorted(os.listdir(FOLDER)):
            if name.startswith("axes"):
                meshes = [item for item in import_file(os.path.join(FOLDER, name), "/Game/Axes/" + name.replace(".", "_"))
                          if isinstance(item, unreal.StaticMesh)]
                state["axes"][name] = [box(mesh) for mesh in meshes]
    except Exception:
        state["traceback"] = traceback.format_exc()
    with open(REPORT, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, default=str)
    state["quit"] = unreal.register_slate_post_tick_callback(quit_later)


def quit_later(_delta):
    """Quit from a later tick than the imports' (a quit in the same tick waited forever on UE 5.8.2)."""
    state["ticks"] = state.get("ticks", 0) + 1
    if state["ticks"] >= 30:
        unreal.unregister_slate_post_tick_callback(state.pop("quit"))
        unreal.SystemLibrary.quit_editor()


state["tick"] = unreal.register_slate_post_tick_callback(main)
