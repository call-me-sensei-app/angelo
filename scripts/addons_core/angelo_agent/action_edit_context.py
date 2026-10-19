# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated Edit-mode override for private staged meshes and armatures.

Only the disposable view layer is selected/activated. Artist Edit/Sculpt/Pose
modes are never interrupted; all staged mode and selection state is restored.

Mode switches run in an override of their own (``switch_mode``). Outside
``--background`` with two or more windows, entering or leaving Edit or Pose
mode refreshes the tool system of the other windows
(``WM_toolsystem_update_from_context_view3d``), making each of them the context
window in turn; that drops a Python ``scene`` override (``CTX_wm_window_set``)
while an inherited ``view_layer`` override stays. Blender's view-layer update
after the operator call (``bpy_op_view_layer_update``) would then build a
depsgraph for the artist's scene with the stage's view layer, which asserts and
crashes. So a switch runs with no context window and with the view layer
resolved from the overridden scene, and work after a switch runs in a newly
entered override whose scene is checked before any operator
(``stage_override``). The Edit/Pose toggle operators are used because
``object.mode_set`` also writes a pending autosave of the whole file, private
stage included.
"""

from contextlib import contextmanager
import bpy
from .refusal_codes import RefusalError
from .action_budget import _update_stage

# Hides an inherited context member: a value that is neither RNA, a sequence nor None counts as "not
# overridden" (BPY_context_member_get), so Blender resolves the view layer from the overridden scene.
_FROM_SCENE = False


def require_stage_context(context):
    """Raise unless Blender's context scene and view layer are the private stage's."""
    if bpy.context.scene != context.scene or bpy.context.view_layer != context.view_layer:
        raise RefusalError('The staged context lost its private scene; no operator ran', code='INTERNAL_ERROR')


@contextmanager
def stage_override(context, obj, **members):
    """A newly entered override of the stage's scene and view layer, ``obj`` alone selected and active."""
    objects = [obj]
    values = dict(scene=context.scene, view_layer=context.view_layer, object=obj, active_object=obj,
                  selected_objects=objects, selected_editable_objects=objects)
    values.update(members)
    with bpy.context.temp_override(**values):
        require_stage_context(context)
        yield


def switch_mode(context, obj, mode, **members):
    """Enter ``mode`` (EDIT or POSE) on the staged target, or leave it for OBJECT, in its own override."""
    leaving = obj.mode
    toggle = bpy.ops.object.posemode_toggle if 'POSE' in (mode, leaving) else bpy.ops.object.editmode_toggle
    with bpy.context.temp_override(window=None, area=None, region=None), \
            stage_override(context, obj, **dict(members, view_layer=_FROM_SCENE)):
        if not toggle.poll():
            raise RefusalError('This Blender context cannot edit a private staged target', code='UNSUPPORTED')
        result = toggle()
    if 'FINISHED' not in result or obj.mode != mode:
        if mode == 'OBJECT':
            raise RefusalError('Unable to leave isolated staged %s mode' % leaving.title(), code='INTERNAL_ERROR')
        raise RefusalError('Unable to enter isolated staged %s mode' % mode.title(), code='INTERNAL_ERROR')


@contextmanager
def _edit_override(context, obj):
    """An override of only the private target; preserve live active/selection IDs."""
    context.assert_staged(obj)
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        raise RefusalError('Do not interrupt an artist in Edit, Sculpt or Pose mode', code='UNSUPPORTED')
    _update_stage(context)
    if obj.name not in context.view_layer.objects:
        raise RefusalError('The staged object must be visible in its staged view layer', code='INTERNAL_ERROR')
    # Mode operators consult the view-layer active base as well as overridden
    # context members. Change only this *disposable* view layer, never the host.
    active = context.view_layer.objects.active
    selection = [(item, item.select_get(view_layer=context.view_layer)) for item in context.view_layer.objects]
    try:
        for item, _selected_before in selection:
            item.select_set(item == obj, view_layer=context.view_layer)
        context.view_layer.objects.active = obj
        switch_mode(context, obj, 'EDIT')
        try:
            with stage_override(context, obj, edit_object=obj, objects_in_mode=[obj], objects_in_mode_unique_data=[obj]):
                yield
        finally:
            if obj.mode != 'OBJECT':
                switch_mode(context, obj, 'OBJECT')
    finally:
        context.view_layer.objects.active = active
        for item, selected_before in selection:
            item.select_set(selected_before, view_layer=context.view_layer)
