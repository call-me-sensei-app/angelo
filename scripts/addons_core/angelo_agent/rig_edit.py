# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Edit Mode on an armature from a tool, and the rigging tools' shared errors (doc/angelo/rigging.rst).

``edit_bones(obj)`` enters Edit Mode on ``obj`` and puts everything back afterwards: the active object, the mode the
person was in, and the object's own hidden state. An armature hidden in the viewport is shown for the edit and hidden
again; one outside the view layer (an excluded collection) can't be edited, and the tool says so instead of raising.
"""

import contextlib

import bpy

from .ui_text import rpt_


class RigError(Exception):
    """``args[0]`` is a code; ``explain`` turns it into the translated message; ``args[1:]`` are names it lists."""


def explain(error):
    code = error.args[0]
    names = ", ".join(str(n) for n in error.args[1:])
    messages = {
        "no_action": rpt_("The source armature plays no action"),
        "no_hips": rpt_("No hips were found on both rigs"),
        "no_root": rpt_("There is no bone above the hips to carry root motion; use In Place"),
        "no_deform": rpt_("The control rig has no deforming bones"),
        "not_in_view_layer": rpt_("The armature is not in the view layer (its collection is excluded)"),
        "same_rig": rpt_("The source and the target are the same armature"),
        "multi_strip_track": rpt_("An NLA track holds several strips: bake or split it first ({names})", names=names),
        "not_local": rpt_("These are linked from another file or are library overrides, so the export cannot "
                          "stage them: make them local first ({names})", names=names),
        "hidden": rpt_("These are hidden or excluded from the view layer, so the file would miss them: show them first "
                       "({names})", names=names),
        "no_slot": rpt_("These clips have no action slot for the armature: assign one first ({names})", names=names),
        "constrained": rpt_("These bones are already driven by the rig's own controls; use its IK/FK switch "
                            "({names})", names=names),
        "no_limbs": rpt_("No arms or legs were found on this rig"),
        "no_ik": rpt_("This rig has no IK controls; add them first"),
        "no_role": rpt_("These parts were not found on this rig ({names})", names=names),
    }
    return messages.get(code, rpt_("The rigs could not be paired"))


@contextlib.contextmanager
def edit_bones(obj):
    """Yield ``obj.data.edit_bones`` in Edit Mode; restore the active object, mode and visibility after."""
    view_layer = bpy.context.view_layer
    if obj.name not in view_layer.objects:
        # The view layer lists an object linked a moment ago (a new collection) only once it resyncs; neither the
        # lookup nor iteration does that. Resync once, then an object still missing is truly excluded.
        view_layer.update()
        if obj.name not in view_layer.objects:
            raise RigError("not_in_view_layer")
    previous = view_layer.objects.active
    previous_mode = obj.mode
    hidden, hidden_viewport = obj.hide_get(), obj.hide_viewport
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    obj.hide_viewport = False
    obj.hide_set(False)
    view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        yield obj.data.edit_bones
    finally:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        obj.hide_set(hidden)
        obj.hide_viewport = hidden_viewport
        view_layer.objects.active = previous
        if previous is obj and previous_mode in ('POSE', 'EDIT') and not hidden:
            bpy.ops.object.mode_set(mode=previous_mode)


def new_bone(edit, name, matrix, length):
    """A new edit bone at the armature-space ``matrix`` (head, axes, roll) with ``length``. The length comes first:
    a bone given a matrix while it has none loses its direction, and a length set after points it along +Z."""
    bone = edit.new(name)
    bone.head, bone.tail = (0.0, 0.0, 0.0), (0.0, max(length, 1e-4), 0.0)
    bone.matrix = matrix
    return bone


@contextlib.contextmanager
def nla_off(obj):
    """Evaluate ``obj``'s active action alone: NLA tracks off, then restored."""
    ad = obj.animation_data
    used = ad.use_nla if ad else None
    if ad:
        ad.use_nla = False
    try:
        yield
    finally:
        if ad:
            ad.use_nla = used


def frames(action):
    start, end = action.frame_range
    return range(int(round(start)), int(round(end)) + 1)


def played_actions(obj):
    """The active action, then each NLA strip's action, once each."""
    ad = obj.animation_data
    if ad is None:
        return []
    out = [ad.action] if ad.action else []
    for track in ad.nla_tracks:
        out += [s.action for s in track.strips if s.action and s.action not in out]
    return out


def use_action(obj, action):
    """Make ``action`` the active one, with a slot for ``obj`` (Blender assigns one by name when it can)."""
    ad = obj.animation_data_create()
    ad.action = action
    if ad.action_slot is None:
        slot = next((s for s in action.slots if s.target_id_type == 'OBJECT'), None)
        ad.action_slot = slot if slot else action.slots.new('OBJECT', obj.name)


def top_above(obj, bone_name):
    """The topmost bone above ``bone_name``, or None when it has no parent."""
    bone = obj.data.bones[bone_name]
    return bone.parent_recursive[-1].name if bone.parent_recursive else None
