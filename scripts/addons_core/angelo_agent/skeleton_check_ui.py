# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person's way to the engine-skeleton check: ``angelo.skeleton_check`` (F3, and the chat bar's + menu under
Inspect), which opens the report card (``skeleton_report_card``) for the armature in hand. doc/angelo/engine_skeleton.rst.
"""

import bpy

from .skeleton_menu import GLYPH, KIND, LAST_TARGET, MENU_TITLE, MENU_WHY
from .ui_text import n_, rpt_

NO_ARMATURE = n_("Select an armature, or a mesh skinned to one, to check it for a game engine")


def armature_in(context):
    """The armature to check: the active object when it is one, the armature of an active skinned mesh, else the
    scene's only armature; None when none of those is clear."""
    from . import skeleton_facts
    active = context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        return active
    if active is not None and active.type == "MESH":
        owners = {m.object for m in active.modifiers if m.type == "ARMATURE" and m.object is not None}
        if len(owners) == 1:
            return owners.pop()
    found = skeleton_facts.armatures(context.scene)
    return found[0] if len(found) == 1 else None


class ANGELO_OT_skeleton_check(bpy.types.Operator):
    bl_idname = "angelo.skeleton_check"
    bl_label = "Check Rig for Game Engine"
    bl_description = ("Check whether an armature fits the humanoid skeleton of Unreal Engine, Unity or Godot: "
                      "bone names and hierarchy, root, rest pose, axes, weights. Read-only")
    bl_options = {'REGISTER'}

    target: bpy.props.EnumProperty(   # skeleton_profiles.TARGETS, by the engines' own names, and the last choice
        name="Engine", items=(("GODOT", "Godot", ""), ("UNITY", "Unity", ""), ("UNREAL", "Unreal Engine", ""),
                              ("LAST", "Last Used", "")),
        default="LAST", options={'SKIP_SAVE'})

    def execute(self, context):
        from . import card_host, live_target
        from .skeleton_report_card import SkeletonReportCard   # loaded on first use, never at start-up
        armature = armature_in(context)
        if armature is None:
            self.report({'WARNING'}, rpt_(NO_ARMATURE))
            return {'CANCELLED'}
        if card_host.is_active(KIND):   # A new check replaces the open one.
            card_host.close("replaced")
        target = LAST_TARGET[0] if self.target == "LAST" else self.target
        card_host.request(SkeletonReportCard(armature.name, live_target.scene_ref(context.scene), target))
        card_host.pump(lambda: True)
        return {'FINISHED'}


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_skeleton_check)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "engine_skeleton", MENU_TITLE, MENU_WHY, glyph=GLYPH, order=49, section=n_("Inspect"),
        operator=ANGELO_OT_skeleton_check.bl_idname, owner="skeleton_check_ui"))


def unregister():
    from . import card_host, chat_destinations
    chat_destinations.unregister("engine_skeleton")
    card_host.withdraw(KIND)
    if card_host.is_active(KIND):
        card_host.close("closed")
    bpy.utils.unregister_class(ANGELO_OT_skeleton_check)
