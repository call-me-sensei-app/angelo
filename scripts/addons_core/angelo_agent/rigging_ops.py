# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rigging operators a person runs from F3 (doc/angelo/rigging.rst): retarget, game rig, root motion, clips,
full-body pose.

Each wraps one module's function and reports its receipt in one line; errors are ``rig_edit.RigError`` codes turned
into translated messages (``rig_edit.explain``). ``ik_controls`` registers its own two operators.
"""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, StringProperty

from . import action_clips, body_pose, export_names, game_rig, retarget, root_motion, skeleton_profiles
from .rig_edit import RigError, explain
from .ui_text import iface_, n_, plural_, rpt_

ENGINES = tuple((key, profile.display, "") for key, profile in skeleton_profiles.PROFILES.items())


def _armature(name, context=None):
    obj = bpy.data.objects.get(name) if name else (context.active_object if context else None)
    return obj if obj and obj.type == 'ARMATURE' else None


class _RigOperator:
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            message = self.run(context)
        except RigError as error:
            self.report({'ERROR'}, explain(error))
            return {'CANCELLED'}
        if message is None:
            return {'CANCELLED'}
        self.report({'INFO'}, message)
        return {'FINISHED'}


class ANGELO_OT_retarget_animation(_RigOperator, bpy.types.Operator):
    bl_idname = "angelo.retarget_animation"
    bl_label = "Retarget Animation"
    bl_description = "Bake the source armature's animation onto the target, pairing bones by the role each plays"

    source: StringProperty(name="Source", description="The armature whose animation is copied")
    target: StringProperty(name="Target", description="The armature that receives it")
    root_motion: EnumProperty(name="Root Motion", items=(
        ('KEEP', "Keep", "The hips travel as the source's do, scaled"),
        ('IN_PLACE', "In Place", "The hips stay over the origin"),
        ('ROOT', "On the Root", "The bone above the hips carries the travel"),
    ), default='KEEP')
    limbs: EnumProperty(name="Limbs", items=(
        ('FK', "FK", "Arms and legs are keyed bone by bone; IK controls are switched off"),
        ('IK', "IK", "Hand and foot effectors are keyed where the retargeted hands and feet are"),
    ), default='FK')
    plant_feet: BoolProperty(name="Plant Feet", default=False,
                             description="Hold each foot still while the source's foot is on the floor (legs in IK)")

    def run(self, context):
        source, target = _armature(self.source), _armature(self.target)
        if not (self.source or self.target):   # Menu or F3: the active armature takes the other selected one's.
            target = _armature("", context)
            others = [o for o in context.selected_objects if o.type == 'ARMATURE' and o is not target]
            source = others[0] if len(others) == 1 else None
        if not (source and target):
            self.report({'ERROR'}, rpt_("Choose a source and a target armature"))
            return None
        receipt = retarget.retarget(source, target, self.root_motion, self.limbs, self.plant_feet)
        return rpt_("Retargeted onto {target} as {action}", target=target.name, action=receipt["action"].name)


class ANGELO_OT_make_game_rig(_RigOperator, bpy.types.Operator):
    bl_idname = "angelo.make_game_rig"
    bl_label = "Make Game Rig"
    bl_description = "Make a deform-only, engine-named copy of a control rig with each of its actions baked"

    source: StringProperty(name="Control Rig")
    target_engine: EnumProperty(name="Engine", items=ENGINES, default="UNREAL")

    def run(self, context):
        control = _armature(self.source, context)
        if control is None:
            self.report({'ERROR'}, rpt_("Choose a control rig"))
            return None
        receipt = game_rig.make_game_rig(control, self.target_engine)
        from .skeleton_report_card import VERDICT   # The rig check card's own verdict words.
        return plural_(len(receipt["actions"]), "{rig}: {count} action baked; rig check: {verdict}",
                       "{rig}: {count} actions baked; rig check: {verdict}", rig=receipt["game_rig"].name,
                       verdict=iface_(VERDICT[receipt["check"]["verdict"]][1]))


class ANGELO_OT_extract_root_motion(_RigOperator, bpy.types.Operator):
    bl_idname = "angelo.extract_root_motion"
    bl_label = "Extract Root Motion"
    bl_description = "Move the hips' travel to the root bone, or keep the animation in place"

    armature: StringProperty(name="Armature")
    mode: EnumProperty(name="Mode", items=(
        ('ROOT', "To the Root", "The root bone carries the travel"),
        ('IN_PLACE', "In Place", "The travel is removed"),
    ), default='ROOT')
    yaw: BoolProperty(name="Turn", description="Also move the hips' turn about the vertical", default=False)

    def run(self, context):
        obj = _armature(self.armature, context)
        if obj is None:
            self.report({'ERROR'}, rpt_("Choose an armature"))
            return None
        receipt = root_motion.extract_root_motion(obj, self.mode, self.yaw)
        return plural_(len(receipt["actions"]), "Root motion in {count} action", "Root motion in {count} actions")


# The clip files each open export browser listed when it last drew (by operator): Replace covers exactly those. A
# Python dict, not a property, so drawing changes no Blender data.
_SHOWN = {}
SHOWN_NAMES = 8   # Names the box lists before it counts the rest.


class ANGELO_OT_export_action_clips(_RigOperator, bpy.types.Operator):
    bl_idname = "angelo.export_action_clips"
    bl_label = "Export Action Clips"
    bl_description = "Write the armature and its meshes with one animation clip per action"
    bl_options = {'REGISTER'}

    armature: StringProperty(name="Armature")
    filepath: StringProperty(subtype='FILE_PATH')
    check_existing: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})   # The browser asks before replacing.
    filter_glob: StringProperty(default="*.glb;*.fbx", options={'HIDDEN'})
    format: EnumProperty(name="Format", items=(
        ('GLB', "glTF Binary", "One file, one animation per action (Godot, Unreal, the web)"),
        ('FBX', "FBX", "A model file and one model@clip file per action (Unity)"),
    ), default='GLB')
    replace_existing: BoolProperty(
        name="Replace Existing Clip Files", default=False, options={'SKIP_SAVE'},
        description="Replace the clip files already beside the picked file (listed above), in this export only")

    def _extension(self):
        return ".fbx" if self.format == 'FBX' else ".glb"

    def check(self, context):
        """Keep the browser's file name ending in the format's extension (the file picked is the file written)."""
        stem, ext = os.path.splitext(self.filepath)
        if self.filepath and ext.lower() != self._extension():
            self.filepath = (stem if ext.lower() in (".glb", ".fbx") else self.filepath) + self._extension()
            return True
        return False

    def _theirs(self, context):
        """The clip files already beside the picked file that Angelo did not make: the person's, to be confirmed."""
        from . import clip_paths, clip_set
        obj = _armature(self.armature, context)
        if obj is None or self.format != 'FBX' or not self.filepath:
            return []
        try:
            names = [clip.name for clip in clip_set.clips(obj)]
            model = clip_paths.model_file(bpy.path.abspath(self.filepath))
            return clip_paths.to_replace(list(clip_paths.clip_files(model, names).values()))
        except (RigError, ValueError):
            return []

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "format")
        theirs = self._theirs(context)
        _SHOWN[self.as_pointer()] = tuple(theirs)   # What Replace covers: what this box says (re-read S-D).
        if theirs:   # The confirm is part of this browser: the person's pick and the replace are one run.
            box = layout.box()
            box.label(text=iface_("Already there:"), icon='ERROR', translate=False)
            names = [os.path.basename(path) for path in theirs]
            for name in names[:SHOWN_NAMES - 1] if len(names) > SHOWN_NAMES else names:
                box.label(text=name, translate=False)
            if len(names) > SHOWN_NAMES:   # The last line names one more and counts the rest.
                box.label(text=iface_("{names} and {count} more", names=names[SHOWN_NAMES - 1],
                                      count=len(names) - SHOWN_NAMES), translate=False)
            box.prop(self, "replace_existing")

    def invoke(self, context, event):
        obj = _armature(self.armature, context)
        self.replace_existing = False   # Never ticked before the person sees what it replaces.
        _SHOWN.pop(self.as_pointer(), None)
        if not self.filepath:   # One clean name in the document's folder: never a path from an object's name.
            self.filepath = export_names.default_export_path(obj.name if obj else "clips", self._extension(),
                                                             bpy.data.filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def run(self, context):
        from . import clip_paths, grant_guard
        obj = _armature(self.armature, context)
        if obj is None:
            self.report({'ERROR'}, rpt_("Choose an animated armature"))
            return None
        filepath = bpy.path.abspath(self.filepath)
        shown = _SHOWN.pop(self.as_pointer(), ())   # Replace covers the files the browser showed, never more.
        replace = clip_paths.confirmed(shown, self._theirs(context)) \
            if self.replace_existing and not grant_guard.script_running() else ()
        try:
            receipt = action_clips.export_action_clips(obj, filepath, self.format, replace)
        except clip_paths.ClipNameClash as error:
            self.report({'ERROR'}, rpt_("These actions would write one clip file; rename one of them ({names})",
                                        names=", ".join(error.names)))
            return None
        except clip_paths.NotAFile as error:
            self.report({'ERROR'}, rpt_("A folder or a link is where these clip files would go, and Angelo never "
                                        "replaces one: move or rename it ({names})",
                                        names=", ".join(os.path.basename(p) for p in error.paths)))
            return None
        except clip_paths.ReplaceUnconfirmed as error:
            self.report({'ERROR'}, rpt_("These clip files are already there; tick Replace Existing Clip Files in the "
                                        "export browser to replace them ({names})",
                                        names=", ".join(os.path.basename(p) for p in error.paths)))
            return None
        except PermissionError as error:
            self.report({'ERROR'}, rpt_("Angelo writes only where you chose or where it made the file: {name}",
                                        name=str(error).rsplit(": ", 1)[-1]))
            return None
        return plural_(len(receipt["clips"]), "Wrote {count} clip", "Wrote {count} clips")


# The pose targets: (enum item, role, property).
POSE_TARGETS = (("HIPS", "Hips", "hips"), ("HEAD", "Head", "head"), ("LEFT_HAND", "LeftHand", "left_hand"),
                ("RIGHT_HAND", "RightHand", "right_hand"), ("LEFT_FOOT", "LeftFoot", "left_foot"),
                ("RIGHT_FOOT", "RightFoot", "right_foot"))


class ANGELO_OT_pose_body(_RigOperator, bpy.types.Operator):
    bl_idname = "angelo.pose_body"
    bl_label = "Pose Body"
    bl_description = ("Pose the whole body from where the hands, feet, head or hips should be, by role; planted "
                      "feet stay, joints keep a human range of motion and the body its balance")

    armature: StringProperty(name="Armature")
    targets: EnumProperty(name="Targets", options={'ENUM_FLAG'}, items=(
        ('HIPS', "Hips", ""), ('HEAD', "Head", ""), ('LEFT_HAND', "Left Hand", ""), ('RIGHT_HAND', "Right Hand", ""),
        ('LEFT_FOOT', "Left Foot", ""), ('RIGHT_FOOT', "Right Foot", "")))
    hips: FloatVectorProperty(name="Hips", subtype='TRANSLATION', size=3,
                              description="Where it goes, in world space")
    head: FloatVectorProperty(name="Head", subtype='TRANSLATION', size=3,
                              description="Where it goes, in world space")
    left_hand: FloatVectorProperty(name="Left Hand", subtype='TRANSLATION', size=3,
                                   description="Where it goes, in world space")
    right_hand: FloatVectorProperty(name="Right Hand", subtype='TRANSLATION', size=3,
                                    description="Where it goes, in world space")
    left_foot: FloatVectorProperty(name="Left Foot", subtype='TRANSLATION', size=3,
                                   description="Where it goes, in world space")
    right_foot: FloatVectorProperty(name="Right Foot", subtype='TRANSLATION', size=3,
                                    description="Where it goes, in world space")
    pin_feet: BoolProperty(name="Plant Feet", default=True, description="Feet with no target stay where they are")
    limits: BoolProperty(name="Range of Motion", default=True, description="Joints bend no further than a body's")
    balance: BoolProperty(name="Balance", default=True,
                          description="Keep the centre of mass over the planted feet")

    def run(self, context):
        obj = _armature(self.armature, context)
        if obj is None:
            self.report({'ERROR'}, rpt_("Choose an armature"))
            return None
        if not self.targets:
            self.report({'ERROR'}, rpt_("Choose where a hand, foot, the head or the hips should go"))
            return None
        targets = {role: tuple(getattr(self, prop)) for item, role, prop in POSE_TARGETS if item in self.targets}
        solved = body_pose.pose_body(obj, targets, self.pin_feet, limits=self.limits, balance=self.balance)
        body_pose.apply(obj, solved)
        short = sorted(role for role, left in solved["errors"].items() if role in targets and not solved["reached"]
                       and left > 1e-3 * max(obj.dimensions.z, 0.1))
        if not short:
            return rpt_("Posed; every target reached")
        return plural_(len(short), "Posed; {count} target out of reach", "Posed; {count} targets out of reach")


CLASSES = (ANGELO_OT_retarget_animation, ANGELO_OT_make_game_rig, ANGELO_OT_extract_root_motion,
           ANGELO_OT_export_action_clips, ANGELO_OT_pose_body)


MENU_IDS = ("rig_retarget", "rig_game_rig", "rig_root_motion", "rig_clips")   # The Rig group's rows (RIG_GROUP).


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # The chat bar's + menu: the person's way to each operator.
    menu = (
        chat_destinations.Feature(
            "rig_retarget", n_("Retarget animation"), n_("Copy the other selected rig's animation onto the active one"),
            glyph="skeleton", order=46, operator="angelo.retarget_animation", owner="rigging_ops"),
        chat_destinations.Feature(
            "rig_game_rig", n_("Make game rig"),
            n_("A deform-only copy named for Unreal, Unity or Godot, actions baked"), glyph="skeleton", order=47,
            operator="angelo.make_game_rig", owner="rigging_ops"),
        chat_destinations.Feature(
            "rig_root_motion", n_("Root motion"), n_("Move the hips' travel to the root bone, or keep it in place"),
            glyph="skeleton", order=48, operator="angelo.extract_root_motion", owner="rigging_ops"),
        chat_destinations.Feature(
            "rig_clips", n_("Export clips"), n_("One animation clip per action, the way each engine imports them"),
            glyph="skeleton", order=49, operator="angelo.export_action_clips", owner="rigging_ops"))
    for item in menu:
        chat_destinations.register(item)


def unregister():
    from . import chat_destinations
    for feature_id in MENU_IDS:
        chat_destinations.unregister(feature_id)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
