# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Through (Blender #73479) in the compiled app, without a window.

The property is off by default, saved per 3D viewport and never animatable; its bit leaves every
other View3D flag alone; the header offers it exactly where box, lasso and circle selection would
otherwise skip hidden elements; Save Startup File makes it the default for new files; the icon is
built in; no default keymap binds it. The selections
themselves need a window: test_blender_select_through_gui.py.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]


def view3d_spaces():
    return [space for screen in bpy.data.screens for area in screen.areas for space in area.spaces
            if space.type == 'VIEW_3D']


def header_offers(mode, obj):
    from bl_ui.space_view3d import _select_through_applies
    return _select_through_applies(SimpleNamespace(mode=mode, active_object=obj))


try:
    spaces = view3d_spaces()
    observed["view3d_spaces"] = len(spaces)
    prop = bpy.types.SpaceView3D.bl_rna.properties["use_select_through"]
    checks["property_is_boolean_off_by_default"] = (prop.type == 'BOOLEAN' and prop.default is False
                                                    and all(not space.use_select_through for space in spaces))
    checks["property_is_not_animatable"] = not prop.is_animatable
    checks["property_names_itself_and_its_scope"] = (prop.name == "Select Through"
                                                     and "Box, lasso and circle" in prop.description)
    checks["property_shows_the_built_in_icon"] = prop.icon == 'ANGELO_SELECT_THROUGH'
    icons = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items
    checks["icon_is_built_in"] = "ANGELO_SELECT_THROUGH" in icons.keys()

    # Per viewport: turning one on leaves the others and every other View3D setting as they were.
    first, rest = spaces[0], spaces[1:]
    others = {name: getattr(first, name) for name in ("lock_camera", "show_gizmo", "show_region_header",
                                                      "show_reconstruction", "show_camera_path")}
    first.use_select_through = True
    checks["per_viewport"] = first.use_select_through and not any(space.use_select_through for space in rest)
    checks["other_settings_untouched"] = others == {name: getattr(first, name) for name in others}
    first.use_select_through = False
    first.use_select_through = True

    # Saved with the file and restored per viewport.
    path = Path(tempfile.mkdtemp(prefix="angelo-select-through-")) / "select_through.blend"
    marked = (first.id_data.name, [index for index, area in enumerate(first.id_data.areas)
                                   if first in list(area.spaces)][0])
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=True)
    reloaded = view3d_spaces()
    marked_space = [space for space in bpy.data.screens[marked[0]].areas[marked[1]].spaces if space.type == 'VIEW_3D']
    checks["saved_and_restored_per_viewport"] = (len(marked_space) == 1 and marked_space[0].use_select_through
                                                 and sum(space.use_select_through for space in reloaded) == 1)

    # The documented way to make it the default for new files: Save Startup File (the runner gives this suite
    # its own user folders, so the person's startup file is never touched).
    bpy.ops.wm.read_factory_settings()
    target = view3d_spaces()[0]
    target_place = (target.id_data.name, [index for index, area in enumerate(target.id_data.areas)
                                          if target in list(area.spaces)][0])
    target.use_select_through = True
    bpy.ops.wm.save_homefile()
    bpy.ops.wm.read_factory_settings()
    factory_off = not any(space.use_select_through for space in view3d_spaces())
    bpy.ops.wm.read_homefile()
    reread = [space for space in bpy.data.screens[target_place[0]].areas[target_place[1]].spaces
              if space.type == 'VIEW_3D']
    observed["startup_file"] = {"factory_off": factory_off, "kept": bool(reread and reread[0].use_select_through)}
    checks["save_startup_file_makes_it_the_default_for_new_files"] = factory_off and bool(reread) and reread[0].use_select_through
    os.remove(bpy.utils.user_resource('CONFIG', path="startup.blend"))

    # Where the header offers it.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    offered = {}
    offered["OBJECT"] = header_offers('OBJECT', cube)
    bpy.ops.object.mode_set(mode='EDIT')
    offered["EDIT_MESH"] = header_offers(bpy.context.mode, cube)
    bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    cube.data.use_paint_mask_vertex = False
    offered["PAINT_WEIGHT_no_mask"] = header_offers(bpy.context.mode, cube)
    cube.data.use_paint_mask_vertex = True
    offered["PAINT_WEIGHT_vertex_mask"] = header_offers(bpy.context.mode, cube)
    bpy.ops.object.mode_set(mode='VERTEX_PAINT')
    offered["PAINT_VERTEX_vertex_mask"] = header_offers(bpy.context.mode, cube)
    cube.data.use_paint_mask_vertex = False
    cube.data.use_paint_mask = True
    offered["PAINT_VERTEX_face_mask"] = header_offers(bpy.context.mode, cube)
    bpy.ops.object.mode_set(mode='OBJECT')
    cube.modifiers.new("Hair", 'PARTICLE_SYSTEM').particle_system.settings.type = 'HAIR'
    bpy.ops.object.mode_set(mode='PARTICLE_EDIT')
    offered["PARTICLE"] = header_offers(bpy.context.mode, cube)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.armature_add()
    bpy.ops.object.mode_set(mode='EDIT')
    offered["EDIT_ARMATURE"] = header_offers(bpy.context.mode, bpy.context.object)
    bpy.ops.object.mode_set(mode='POSE')
    offered["POSE"] = header_offers(bpy.context.mode, bpy.context.object)
    bpy.ops.object.mode_set(mode='OBJECT')
    offered["SCULPT"] = header_offers('SCULPT', cube)
    observed["offered"] = offered
    checks["header_offers_it_only_where_it_changes_selection"] = offered == {
        "OBJECT": False, "EDIT_MESH": True, "PAINT_WEIGHT_no_mask": False, "PAINT_WEIGHT_vertex_mask": True,
        "PAINT_VERTEX_vertex_mask": True, "PAINT_VERTEX_face_mask": False, "PARTICLE": True,
        "EDIT_ARMATURE": False, "POSE": False, "SCULPT": False}

    # No default key binds it (Blender's keymaps stay as they are); a user can still bind the property.
    keyconfig = bpy.context.window_manager.keyconfigs.default
    bound = [(keymap.name, item.idname) for keymap in keyconfig.keymaps for item in keymap.keymap_items
             if "use_select_through" in str(getattr(item.properties, "data_path", "") or "")]
    observed["default_bindings"] = bound
    checks["no_default_binding"] = bound == []
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("SELECT_THROUGH_BACKGROUND failed checks:", json.dumps(failed))
assert report["success"], failed
