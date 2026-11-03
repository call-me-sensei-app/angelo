# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal side of skeleton_engine_e2e/run.py, run as the test project's ``Content/Python/init_unreal.py`` in a
headless Unreal Editor. Imports every rig in ``ANGELO_UNREAL_RIGS`` (.glb and .fbx) as a skeletal mesh with the
editor's default import pipeline, reads the skeleton's bone names and parents as Unreal stored them, then makes an IK
Rig for the mesh and asks for its auto-generated retarget definition (what IK Retargeter's auto setup runs): whether
it applied, the retarget root, and the chains it made. Writes ``ANGELO_UNREAL_REPORT`` and quits. Test only.
"""

import json
import os
import traceback

import unreal

RIGS = os.environ["ANGELO_UNREAL_RIGS"]
REPORT = os.environ["ANGELO_UNREAL_REPORT"]
report = {"engine": unreal.SystemLibrary.get_engine_version(), "rigs": {}}


def skeleton_bones(mesh):
    """The skeleton's bone names and parents as Unreal stored them (SkeletonModifier, MeshModelingToolset)."""
    modifier = unreal.SkeletonModifier()
    modifier.set_skeletal_mesh(mesh)
    out = []
    for name in modifier.get_all_bone_names():
        parent = str(modifier.get_parent_name(name))
        out.append({"name": str(name), "parent": None if parent == "None" else parent})
    return out


def import_rig(path, folder):
    task = unreal.AssetImportTask()
    task.filename = path
    task.destination_path = folder
    task.automated = True
    task.save = False
    task.replace_existing = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    meshes = [unreal.EditorAssetLibrary.load_asset(p) for p in task.imported_object_paths]
    return [m for m in meshes if isinstance(m, unreal.SkeletalMesh)]


def retarget(mesh, folder, name):
    """Auto Create Retarget Chains on a new IK Rig for ``mesh``: applied (a template matched), pelvis and chains."""
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    rig = tools.create_asset("IK_" + name, folder, unreal.IKRigDefinition, unreal.IKRigDefinitionFactory())
    controller = unreal.IKRigController.get_controller(rig)
    controller.set_skeletal_mesh(mesh)
    applied = controller.apply_auto_generated_retarget_definition()
    chains = []
    for chain in controller.get_retarget_chains():
        chain_name = chain.get_editor_property("chain_name")
        chains.append({"name": str(chain_name),
                       "start": str(controller.get_retarget_chain_start_bone(chain_name)),
                       "end": str(controller.get_retarget_chain_end_bone(chain_name))})
    return {"applied": bool(applied), "pelvis": str(controller.get_retarget_root()),
            "root_motion": str(controller.get_root_motion_bone()), "chains": chains}


def main():
    for index, file in enumerate(sorted(os.listdir(RIGS))):
        if not file.endswith((".glb", ".fbx")):
            continue
        name = file.replace(".", "_")
        entry = report["rigs"].setdefault(file, {})
        try:
            meshes = import_rig(os.path.join(RIGS, file), "/Game/Rigs/" + name)
            entry["skeletal_meshes"] = len(meshes)
            if meshes:
                entry["bones"] = skeleton_bones(meshes[0])
                entry["retarget"] = retarget(meshes[0], "/Game/Rigs/" + name, name)
        except Exception:
            entry["error"] = traceback.format_exc()[-1500:]
    with open(REPORT, "w") as stream:
        json.dump(report, stream, indent=1)
    unreal.SystemLibrary.quit_editor()


try:
    main()
except Exception:
    report["error"] = traceback.format_exc()
    with open(REPORT, "w") as stream:
        json.dump(report, stream, indent=1)
    unreal.SystemLibrary.quit_editor()
