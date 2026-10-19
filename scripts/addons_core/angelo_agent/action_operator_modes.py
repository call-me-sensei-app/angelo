# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``operator_run`` contexts: the mode an operator runs in, its staged context members and selection.

Every operator runs inside ``mode_override``: the disposable stage's scene and
view layer, the staged target alone selected and active, no window, area or
region (every context member Blender would otherwise resolve through the
artist's window is overridden), and the target in Object, Edit or Pose mode.
Mode switches run in an override of their own (``action_edit_context``).

Edit-mode ``transform`` operators also need a context window: their
status-bar text dereferences it. ``call_override`` gives only their call the
program's window, with the stage's scene, view layer and members entered
again inside it, and ``pinned_tool_settings`` pins the stage tool settings
they read when an operator property is unset (snapping, auto-merge,
attribute correction, proportional editing). Without an editor area the
transform system pivots on the selection's bounding-box centre.
"""

from contextlib import contextmanager

import bpy

from .refusal_codes import RefusalError
from .action_budget import _update_stage
from .action_edit_context import stage_override, switch_mode
from .action_operator_spec import POSE_MODULES, OperatorError, edit_only, module_of, needs_window

# Object types whose Edit mode the object subset may use (Grease Pencil, hair and text edit only through their
# own Edit-mode operators).
EDITABLE = frozenset({"MESH", "CURVE", "ARMATURE", "LATTICE"})
_SELECT_ALL = {"MESH": "mesh", "CURVE": "curve", "ARMATURE": "armature", "LATTICE": "lattice",
               "GREASEPENCIL": "grease_pencil", "CURVES": "curves"}
# Stage tool settings the transform system reads when an operator property is unset.
_TRANSFORM_SETTINGS = {"use_snap": False, "use_mesh_automerge": False, "use_transform_correct_face_attributes": False,
                       "use_transform_correct_keep_connected": False, "use_proportional_edit": False}


@contextmanager
def mode_override(context, obj, mode):
    """The staged target alone, selected and active in the disposable view layer, in ``mode`` (OBJECT, EDIT or POSE).

    Yields the context members it overrides, so ``call_override`` can enter
    them again. No editor area or region: no operator reads the artist's view.
    The stage view layer's selection and active object are restored afterwards.
    """
    context.assert_staged(obj)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        raise RefusalError("Do not interrupt an artist in Edit, Sculpt or Pose mode", code='UNSUPPORTED')
    _update_stage(context)
    layer = context.view_layer
    if obj.name not in layer.objects:
        raise RefusalError("The staged object must be visible in its staged view layer", code='INTERNAL_ERROR')
    active = layer.objects.active
    selection = [(item, item.select_get(view_layer=layer)) for item in layer.objects]
    try:
        for item, _before in selection:
            item.select_set(item == obj, view_layer=layer)
        layer.objects.active = obj
        # No window either: Blender resolves every context member not overridden here (visible, editable
        # and selectable objects, bones...) through the window's own view layer, the artist's. A mode switch
        # drops the scene override when other windows exist, so it gets an override of its own and the work
        # after it a newly entered one (action_edit_context.switch_mode, stage_override).
        objects = [obj]
        members = dict(editable_objects=objects, visible_objects=objects, selectable_objects=objects)
        with bpy.context.temp_override(window=None, area=None, region=None):
            if mode == "OBJECT":
                with stage_override(context, obj, **members):
                    yield members
                return
            switch_mode(context, obj, mode, **members)
            try:
                bones = _bone_members(obj) if mode == "EDIT" else _pose_members(obj)  # Never kept past the mode.
                members = dict(members, objects_in_mode=objects, objects_in_mode_unique_data=objects,
                               **({"edit_object": obj} if mode == "EDIT" else {}), **bones)
                with stage_override(context, obj, **members):
                    yield members
            finally:
                if obj.mode != "OBJECT":
                    switch_mode(context, obj, "OBJECT", **dict(editable_objects=objects, visible_objects=objects,
                                                               selectable_objects=objects))
    finally:
        layer.objects.active = active
        for item, selected_before in selection:
            item.select_set(selected_before, view_layer=layer)


def program_window():
    """The window an operator call may use (the program's), or None. Read before ``mode_override``."""
    manager = bpy.context.window_manager
    return bpy.context.window or (manager.windows[0] if manager is not None and len(manager.windows) else None)


@contextmanager
def call_override(context, obj, idname, window, members):
    """The context of the operator call itself: inside ``mode_override``; transforms also get ``window``."""
    if not needs_window(idname):
        yield
        return
    if window is None:
        raise OperatorError("NO_WINDOW", "%s needs a window context, and this Blender session has none" % idname)
    # The window first, then the stage's scene, view layer and members again inside it: a window override
    # given in the same call would reset the scene to the window's (CTX_wm_window_set).
    with bpy.context.temp_override(window=window, area=None, region=None), stage_override(context, obj, **members):
        yield


@contextmanager
def pinned_tool_settings(context, idname):
    """Pin the stage tool settings a transform reads when its properties are unset; restore them after."""
    if not needs_window(idname):
        yield
        return
    settings = context.scene.tool_settings
    saved = {name: getattr(settings, name) for name in _TRANSFORM_SETTINGS if hasattr(settings, name)}
    try:
        for name in saved:
            setattr(settings, name, _TRANSFORM_SETTINGS[name])
        yield
    finally:
        for name, value in saved.items():
            setattr(settings, name, value)


def _bone_members(obj):
    """The staged armature's own edit-bone context lists (Blender would read the window's view layer)."""
    if obj.type != "ARMATURE":
        return {}
    bones = list(obj.data.edit_bones)
    visible = [bone for bone in bones if not bone.hide]
    selected = [bone for bone in visible if bone.select or bone.select_head or bone.select_tail]
    return {"editable_bones": visible, "visible_bones": visible, "selected_bones": selected,
            "selected_editable_bones": selected, "active_bone": obj.data.edit_bones.active}


def _pose_members(obj):
    """The staged armature's own pose-bone context lists (Blender would read the window's view layer)."""
    bones = list(obj.pose.bones)
    visible = [bone for bone in bones if not bone.hide]
    selected = [bone for bone in visible if bone.select]
    active = obj.data.bones.active
    return {"pose_object": obj, "visible_pose_bones": visible, "selected_pose_bones": selected,
            "selected_pose_bones_from_active_object": selected,
            "active_pose_bone": obj.pose.bones.get(active.name) if active is not None else None}


def select(obj, params):
    """Edit- or Pose-mode selection before the operator: ALL (default), NONE, KEEP or mesh INDICES.

    Text has no observable cursor or selection: ALL selects the whole body,
    NONE puts the cursor after the last character with nothing selected.
    """
    choice = params["select"] or "ALL"
    if choice == "KEEP":
        return
    if obj.type == "FONT":
        result = bpy.ops.font.select_all() if choice == "ALL" else bpy.ops.font.move(type="TEXT_END")
        if "FINISHED" not in result and obj.data.body:
            raise OperatorError("SELECTION", "the staged text could not be selected")
        return
    if choice in {"ALL", "NONE"}:
        select_all = (bpy.ops.pose if obj.mode == "POSE" else getattr(bpy.ops, _SELECT_ALL[obj.type])).select_all
        if "FINISHED" not in select_all(action="SELECT" if choice == "ALL" else "DESELECT"):
            raise OperatorError("SELECTION", "the staged elements could not be selected")
        return
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    key = next(key for key in ("vertex_indices", "edge_indices", "face_indices") if params[key])
    elements = {"vertex_indices": bm.verts, "edge_indices": bm.edges, "face_indices": bm.faces}[key]
    elements.ensure_lookup_table()
    if max(params[key]) >= len(elements):
        raise OperatorError("INDEX", "an element index is outside the staged mesh")
    for sequence in (bm.verts, bm.edges, bm.faces):
        for element in sequence:
            element.select = False
    for index in params[key]:
        elements[index].select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def modes(idname, obj, params):
    """The modes to try, in order: Edit modules and strokes in Edit, pose in Pose, constraints in Object."""
    module = module_of(idname)
    if edit_only(idname):
        return ("EDIT",)
    if module in POSE_MODULES:
        return ("POSE",)
    if module == "constraint":
        return ("OBJECT",)
    if params["select"] is not None:  # Element selection is an Edit-mode notion.
        return ("EDIT",) if obj.type in EDITABLE else ()
    return ("OBJECT", "EDIT") if obj.type in EDITABLE else ("OBJECT",)

