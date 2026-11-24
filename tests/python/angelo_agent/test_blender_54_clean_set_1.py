# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender 5.4 features taken early into Angelo 2.1.0, clean set 1, work in a real background Blender.

Angelo 2.1.0 takes these upstream ``main`` commits unchanged (``git cherry-pick -x``, upstream order) onto its
Blender 5.3 base (the 2.1.0 release plan, sections 2.1 and 4.4). Each is checked
here by what a person or a script can now do, never by reading the source:

* workspace tabs reorder left and right (``43c03f22bd4``): ``workspace.reorder_to_left_right`` moves a workspace one
  place in the tab order;
* file handlers can offer collection import (``8fbd862b418``), and FBX does (``9d634c100b0``): an FBX file imports
  through a collection's importer into that collection, and a handler without collection import is refused, from
  Python too (``30fe8ca7387``);
* bone selection by deform and scale (``0feb38d08bf``): ``pose.select_deform`` selects exactly the deforming bones;
  Pose mode's Select Mirror mirrors only the selection, never a bone's other flags (``60ecc2fec40``, a 5.3 bug);
* NLA sound strips from Python (``bfd982cce6a``) and the OpenXR runtime name (``061b243091c``, with its buffer fix
  ``2d4329299e6``) are in the API;
* Grease Pencil: Select Linked Pick (``70812f0d8fe``) and an image user for stroke and fill textures, so a video
  plays on them (``7f8919bcfec``).

Covered elsewhere: ``bpy.path.template_escape`` (``2e322511edf``) by upstream's ``tests/python/bl_pyapi_bpy_path.py``
and append never localizing linked packed data (``295a5e3fc48``) by upstream's ``bl_blendfile_liblink.py``, both
carried by the picks; the interface picks (Color Ramp snap and precision drag, hue wrap, the recent-file project
tip) and Grease Pencil's Armature modifier on Bézier strokes, Lasso as its Edit Mode select tool (``eab8ae441b9``)
and Pull as its default sculpt brush (``b39caf6027d``) by GUI proof in the running app.

Not in this set: the selection history kept from evaluated to original mesh (``168c4371d93``) now conflicts with
Angelo's own ``BKE_mesh.hh`` (a port, not a pick); Grease Pencil's stroke type ``fill_opacity`` fix needs upstream's
attribute storage refactor (still moving). The OBJ export of non-Principled materials (``a35af3105f2``) and the
Grease Pencil randomize seed (``e50b38b9253`` with ``3524a9e9533``) re-baseline upstream LFS test files, so they ride
a local branch on top of this one.

On a build without the picks each check fails without running the missing feature (a check whose API is absent
records the absence), so nothing crashes. Run through ``run_native_suites.py --include test_blender_54_clean_set_1``;
check ``54-clean-set-1.json`` for ``success``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-54-clean-set-1-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "54-clean-set-1.json"
bpy.context.preferences.use_preferences_save = False

state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def has_operator(group, name):
    return hasattr(getattr(bpy.ops, group), name) and name in dir(getattr(bpy.ops, group))


def shown_tabs():
    """The workspace tabs in the order the tab strip shows them (BKE_id_ordered_list: tab order, then name);
    bpy.data.workspaces is always alphabetical."""
    return [w.name for w in sorted(bpy.data.workspaces, key=lambda w: (w.angelo_tab_order, w.name))]


def workspace_reorder():
    """Moving the second tab left makes it the first, by the operator a tab's context menu runs (upstream returns
    INTERFACE: it only swaps the two tabs' order and redraws)."""
    if not has_operator("workspace", "reorder_to_left_right"):
        check("workspace_reorder_left_right", False, "operator missing")
        return
    before = shown_tabs()
    second = bpy.data.workspaces[before[1]]
    with bpy.context.temp_override(workspace=second):
        result = bpy.ops.workspace.reorder_to_left_right(direction='LEFT')
    after = shown_tabs()
    check("workspace_reorder_left_right", result & {'FINISHED', 'INTERFACE'} and after[:2] == [before[1], before[0]],
          {"result": sorted(result), "before": before[:3], "after": after[:3]})


def fbx_collection_import():
    """A cube exported to FBX imports through a collection's FBX importer into that collection."""
    if "bl_collection_import_enabled" not in bpy.types.FileHandler.bl_rna.properties:
        check("file_handler_collection_import", False, "FileHandler.bl_collection_import_enabled missing")
        check("fbx_collection_import", False, "not tried: the feature's API is missing")
        return
    check("file_handler_collection_import", True)
    path = OUT / "cube.fbx"
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    cube.name = "ExportedCube"
    bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True)
    bpy.data.objects.remove(cube)
    target = bpy.data.collections.new("FBXTarget")
    bpy.context.scene.collection.children.link(target)
    layer = bpy.context.view_layer.layer_collection.children[target.name]
    bpy.context.view_layer.active_layer_collection = layer
    with bpy.context.temp_override(collection=target):
        added = bpy.ops.collection.importer_add(name="IO_FH_fbx", filepath=str(path))
        imported = bpy.ops.collection.importer_import()
    meshes = [o.name for o in target.all_objects if o.type == 'MESH']
    check("fbx_collection_import", added == {'FINISHED'} and imported == {'FINISHED'} and meshes,
          {"added": sorted(added), "imported": sorted(imported), "meshes": meshes})


class NoCollectionImport(bpy.types.FileHandler):
    """A handler that imports files but never offered collection import."""
    bl_idname = "TEST_FH_no_collection_import"
    bl_label = "No collection import"
    bl_import_operator = "wm.obj_import"
    bl_file_extensions = ".obj"


def collection_import_refused():
    """``collection.importer_add`` refuses a handler that does not offer collection import, called from Python."""
    if not has_operator("collection", "importer_add"):
        check("collection_import_refuses_unsupported_handler", False, "operator missing")
        return
    bpy.utils.register_class(NoCollectionImport)
    try:
        target = bpy.data.collections.new("RefuseTarget")
        bpy.context.scene.collection.children.link(target)
        with bpy.context.temp_override(collection=target):
            try:
                result = bpy.ops.collection.importer_add(name=NoCollectionImport.bl_idname)
            except RuntimeError as error:   # Its report is an error: Python raises it.
                result = {"refused": str(error)}
        importers = len(getattr(target, "importers", ()))
        check("collection_import_refuses_unsupported_handler", result != {'FINISHED'} and importers == 0,
              {"result": sorted(result) if isinstance(result, set) else result, "importers": importers})
    finally:
        bpy.utils.unregister_class(NoCollectionImport)


def make_rig(bones):
    armature = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for index, (name, deform) in enumerate(bones):
        bone = armature.edit_bones.new(name)
        bone.head, bone.tail = (index, 0, 0), (index, 0, 1)
        bone.use_deform = deform
    bpy.ops.object.mode_set(mode='POSE')
    return rig


def select_mirror_keeps_other_flags():
    """Select Mirror swaps the selection of Arm.L and Arm.R and leaves each bone's own Transform at Custom Shape."""
    rig = make_rig((("Arm.L", True), ("Arm.R", True)))
    left, right = rig.pose.bones["Arm.L"], rig.pose.bones["Arm.R"]
    left.use_transform_at_custom_shape, right.use_transform_at_custom_shape = True, False
    bpy.ops.pose.select_all(action='DESELECT')
    left.select = True
    result = bpy.ops.pose.select_mirror(extend=False)
    state_after = {"left_select": left.select, "right_select": right.select,
                   "left_custom": left.use_transform_at_custom_shape,
                   "right_custom": right.use_transform_at_custom_shape}
    bpy.ops.object.mode_set(mode='OBJECT')
    check("pose_select_mirror_moves_selection_only",
          result == {'FINISHED'} and state_after == {"left_select": False, "right_select": True,
                                                     "left_custom": True, "right_custom": False},
          state_after)


def bone_selection():
    """``pose.select_deform`` selects the deforming bones and only those."""
    if not has_operator("pose", "select_deform"):
        check("pose_select_deform", False, "operator missing")
        return
    rig = make_rig((("Deform", True), ("Control", False), ("Deform2", True)))
    bpy.ops.pose.select_all(action='DESELECT')
    result = bpy.ops.pose.select_deform(mode='DEFORM')
    selected = sorted(b.name for b in rig.pose.bones if b.select)
    bpy.ops.object.mode_set(mode='OBJECT')
    check("pose_select_deform", result == {'FINISHED'} and selected == ["Deform", "Deform2"], {"selected": selected})


def api_additions():
    functions = bpy.types.NlaStrips.bl_rna.functions
    check("nla_new_sound", "new_sound" in functions)
    check("xr_runtime_name", "runtime_name" in bpy.types.XrSessionState.bl_rna.properties)
    check("grease_pencil_select_linked_pick", has_operator("grease_pencil", "select_linked_pick"))
    style = bpy.types.MaterialGPencilStyle.bl_rna.properties
    check("grease_pencil_stroke_fill_image_user", "stroke_image_user" in style and "fill_image_user" in style)


def main():
    try:
        for step in (workspace_reorder, fbx_collection_import, collection_import_refused, bone_selection,
                     select_mirror_keeps_other_flags, api_additions):
            try:
                step()
            except Exception:
                state["details"][step.__name__ + "_error"] = traceback.format_exc()
                check(step.__name__, False)
        state["success"] = len(state["checks"]) >= 10 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_54_CLEAN_SET_1", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
