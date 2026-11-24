# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""IK controls and the IK/FK switch on any humanoid, by role (doc/angelo/rigging.rst, "IK controls").

``add_ik_controls`` finds the limbs by role (``skeleton_roles.map_roles``), never by name, and adds, per arm and leg:

* an **effector**: a non-deforming bone at the hand's or foot's joint, oriented like it, hanging from the bone above
  the hips (or from nothing), never from the hips, so moving the hips leaves planted feet where they are;
* a **pole**: a non-deforming bone off the elbow or knee on the side the limb bends to, or, for a straight limb, by
  the rig's facing (knees forward, elbows back). It hangs from the hips, so knees and elbows follow the body;
* an **IK mechanism**: hidden, non-deforming exact rest copies of the upper and lower bone and of the hand or foot,
  with an IK on the copied hand or foot reaching its head (joints, not tails: an imported rig's tails point
  anywhere) for the effector over the two copies above it, the pole angle measured (``_calibrate_poles``) so a bent limb
  points its elbow or knee at its pole. The
  copied forearm or shin starts bent by a degree in its own pose: Blender's IK can't shorten a perfectly straight
  chain, and an imported T-pose limb often is one. The deforming upper and lower bones follow their copies
  (Copy Transforms); the hand or foot turns with the effector (Copy Rotation). These three are what the switch sets.

A limb whose bones are already driven (constraints or drivers of the rig's own, as in a generated Rigify rig, whose
deforming bones follow its control bones) is refused by name: that rig has its own IK/FK switch.

``set_ik_fk(obj, mode, ends, snap, frame)`` switches limbs between IK and FK through the constraints' influence. With
``snap`` the limb keeps its pose: to FK, the bones take the pose IK gave them; to IK, the effector moves to the hand or
foot and the pole to the side the limb bends. With ``frame`` the switch is keyed. Controls carry
``angelo_ik_control``, the mechanism ``angelo_ik_mechanism``; a rerun replaces both and the constraints. The deforming
bones' own values are never changed by adding them.
"""

import math

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Quaternion, Vector

from . import rig_pose_write
from .game_rig import role_map
from .rig_edit import RigError, edit_bones, explain, new_bone, top_above
from .ui_text import n_, plural_, rpt_

MARK, FOR, KIND = "angelo_ik_control", "angelo_ik_for", "angelo_ik_kind"
UPPER, LOWER, MECHANISM = "angelo_ik_upper", "angelo_ik_lower", "angelo_ik_mechanism"
IK_NAME, ROT_NAME, FOLLOW_NAME = "Angelo IK", "Angelo IK Rotation", "Angelo IK Follow"  # i18n: constraint names
OURS = (IK_NAME, ROT_NAME, FOLLOW_NAME)
LIMBS = (("UpperLeg", "LowerLeg", "Foot", 1.0), ("UpperArm", "LowerArm", "Hand", -1.0))
SEED_DEGREES = 1.0   # The mechanism's starting bend: Blender's IK can't shorten a perfectly straight chain.


def _signed_angle(a, b, normal):
    """The angle from ``a`` to ``b`` about ``normal`` (Blender's pole-angle convention)."""
    angle = a.angle(b)
    return -angle if normal.dot(a.cross(b)) > 0 else angle


def _driven(obj, names):
    """The bones in ``names`` with a constraint or driver that is not Angelo's."""
    out = [n for n in names if any(c.name not in OURS for c in obj.pose.bones[n].constraints)]
    drivers = obj.animation_data.drivers if obj.animation_data else ()
    for fcurve in drivers:
        out += [n for n in names if fcurve.data_path.startswith('pose.bones["%s"]' % n) and n not in out]
    return out


def limbs(obj):
    """[{end, lower, upper, effector, pole}] for each limb with Angelo's IK on it."""
    out = []
    for bone in obj.data.bones:
        if bone.get(KIND) != "effector":
            continue
        end, upper, lower = bone.get(FOR), bone.get(UPPER), bone.get(LOWER)
        if not all(n in obj.pose.bones for n in (end, upper, lower)):
            continue
        if obj.pose.bones[end].constraints.get(ROT_NAME) is None:
            continue
        pole = next((b.name for b in obj.data.bones if b.get(KIND) == "pole" and b.get(FOR) == end), None)
        out.append({"end": end, "lower": lower, "upper": upper, "effector": bone.name, "pole": pole})
    return out


def switches(obj, limb):
    """The constraints the IK/FK switch sets: the limb's upper and lower bones follow the IK mechanism, the end
    turns with the effector."""
    return [obj.pose.bones[limb["upper"]].constraints[FOLLOW_NAME],
            obj.pose.bones[limb["lower"]].constraints[FOLLOW_NAME],
            obj.pose.bones[limb["end"]].constraints[ROT_NAME]]


def pole_place(limb, pose, pole_matrix):
    """The pole's armature-space matrix for a limb posed as ``pose`` ({bone: armature-space matrix}): off the elbow or
    knee on the side the limb bends, half the limb's length out, so IK bends it the same way. A straight limb keeps
    ``pole_matrix``'s place."""
    mid, base = pose[limb["lower"]].translation, pose[limb["upper"]].translation
    tip = pose[limb["end"]].translation
    along = (tip - base).normalized()
    bend = (mid - base) - along * (mid - base).dot(along)
    out = pole_matrix.copy()
    if bend.length > 1e-6 * max((tip - base).length, 1e-9):
        out.translation = mid + bend.normalized() * ((mid - base).length + (tip - mid).length) * 0.5
    return out


def _remove(obj):
    for pb in obj.pose.bones:
        for constraint in [c for c in pb.constraints if c.name in OURS]:
            pb.constraints.remove(constraint)
    old = [b.name for b in obj.data.bones if b.get(MARK) or b.get(MECHANISM)]
    if old:
        with edit_bones(obj) as edit:
            for name in old:
                edit.remove(edit[name])


def add_ik_controls(obj):
    """Add (or replace) effectors, poles and IK on ``obj``'s limbs; returns the number of limbs controlled."""
    roles, mapping, _ = role_map(obj)
    found = []
    for side in ("Left", "Right"):
        for upper, lower, end, bend_sign in LIMBS:
            names = [roles.get(side + part) for part in (upper, lower, end)]
            if all(names):
                found.append((names, bend_sign))
    if not found:
        raise RigError("no_limbs")
    driven = _driven(obj, [n for names, _ in found for n in names])
    if driven:
        raise RigError("constrained", *driven[:8])
    _remove(obj)
    forward = Vector(mapping.forward) if mapping.forward is not None else Vector((0.0, -1.0, 0.0))
    forward_arm = (obj.matrix_world.inverted().to_3x3() @ forward).normalized()
    anchor = top_above(obj, roles["Hips"]) if "Hips" in roles else None
    hips = roles.get("Hips")
    made = []
    with edit_bones(obj) as edit:
        for (upper, lower, end), bend_sign in found:
            base, mid, tip = edit[upper].head.copy(), edit[lower].head.copy(), edit[end].head.copy()
            span = tip - base
            along = span.normalized()
            bend = (mid - base) - along * (mid - base).dot(along)
            if bend.length < 1e-4 * span.length:
                bend = forward_arm * bend_sign
            length = (mid - base).length + (tip - mid).length
            effector = new_bone(edit, "IK_" + end, edit[end].matrix.copy(), edit[end].length)
            pole = edit.new("Pole_" + lower)
            pole.head = mid + bend.normalized() * length * 0.5
            pole.tail = pole.head + Vector((0.0, 0.0, 0.05 * length))
            effector.parent = edit[anchor] if anchor else None
            pole.parent = edit[hips] if hips else effector.parent
            # The mechanism: exact rest copies of the limb's bones (so following them moves nothing at rest),
            # its own tip at the end's joint (joints, not tails: an imported rig's tails point anywhere).
            m_upper = new_bone(edit, "MCH_IK_" + upper, edit[upper].matrix.copy(), edit[upper].length)
            m_lower = new_bone(edit, "MCH_IK_" + lower, edit[lower].matrix.copy(), edit[lower].length)
            m_tip = new_bone(edit, "MCH_IK_" + end, edit[end].matrix.copy(), edit[end].length)
            m_upper.parent, m_lower.parent, m_tip.parent = edit[upper].parent, m_upper, m_lower
            for bone in (effector, pole, m_upper, m_lower, m_tip):
                bone.use_deform = False
                bone.use_connect = False
            made.append((upper, lower, end, effector.name, pole.name, (m_upper.name, m_lower.name, m_tip.name),
                         (mid - base).cross(bend)))
    for upper, lower, end, effector, pole, mechanism, hinge_axis in made:
        for name, kind in ((effector, "effector"), (pole, "pole")):
            bone = obj.data.bones[name]
            bone[MARK], bone[FOR], bone[KIND] = True, end, kind
        obj.data.bones[effector][UPPER], obj.data.bones[effector][LOWER] = upper, lower
        for name in mechanism:
            obj.data.bones[name][MECHANISM] = end
        m_upper, m_lower, m_tip = mechanism
        base_bone, end_head = obj.data.bones[m_upper], obj.data.bones[m_tip].head_local
        pole_head = obj.data.bones[pole].head_local
        chain = end_head - base_bone.head_local
        projected = chain.cross(pole_head - base_bone.head_local).cross(base_bone.tail_local - base_bone.head_local)
        angle = _signed_angle(base_bone.x_axis, projected, base_bone.tail_local - base_bone.head_local)
        ik = obj.pose.bones[m_tip].constraints.new('IK')
        ik.name = IK_NAME
        ik.target, ik.subtarget = obj, effector
        ik.pole_target, ik.pole_subtarget, ik.pole_angle = obj, pole, angle
        # Without the tail, Blender's solver drops the owner and counts from its parent (iksolver_plugin.cc):
        # 2 is the mechanism's upper and lower bones, the chain the pole angle is solved for.
        ik.use_tail, ik.chain_count = False, 2
        seed = obj.pose.bones[m_lower]   # Its own bone: a starting bend toward the limb's bend side.
        axis = obj.data.bones[m_lower].matrix_local.inverted().to_3x3() @ hinge_axis
        seed.rotation_mode = 'QUATERNION'
        seed.rotation_quaternion = Quaternion(axis.normalized(), -math.radians(SEED_DEGREES)) \
            if axis.length > 1e-9 else Quaternion()
        for name, source in ((upper, m_upper), (lower, m_lower)):
            follow = obj.pose.bones[name].constraints.new('COPY_TRANSFORMS')
            follow.name = FOLLOW_NAME
            follow.target, follow.subtarget = obj, source
        rotation = obj.pose.bones[end].constraints.new('COPY_ROTATION')
        rotation.name = ROT_NAME
        rotation.target, rotation.subtarget = obj, effector
    _calibrate_poles(obj, [(effector, pole, mechanism) for _, _, _, effector, pole, mechanism, _ in made])
    return len(made)


def _about(a, b, axis):
    """The signed angle from ``a`` to ``b`` about ``axis``, of their parts square to it."""
    a = a - axis * a.dot(axis)
    b = b - axis * b.dot(axis)
    return math.atan2(axis.dot(a.cross(b)), a.dot(b))


def _calibrate_poles(obj, limbs_made):
    """Measure each limb's pole angle instead of predicting it: with every effector moved a fifth of the way toward
    its limb's root (so the limb bends), turn the pole angle by the angle between where the elbow or knee went and
    where the pole is, about the root-to-effector line; if that made it worse, turn the other way. The solver's
    frame and a rig's bone roll then never matter. Effectors go back after; the armature is shown meanwhile (a
    viewport-disabled object isn't evaluated)."""
    hidden, hidden_viewport = obj.hide_get(), obj.hide_viewport
    obj.hide_viewport = False
    obj.hide_set(False)
    view_layer = bpy.context.view_layer
    pb = obj.pose.bones
    saved = {eff: (pb[eff].location.copy(), pb[eff].rotation_quaternion.copy(), pb[eff].rotation_euler.copy())
             for eff, _, _ in limbs_made}
    try:
        view_layer.update()
        for eff, _, (m_upper, _, m_tip) in limbs_made:
            m = pb[eff].matrix.copy()
            m.translation = m.translation + (pb[m_upper].head - pb[m_tip].head) * 0.2
            pb[eff].matrix = m
        view_layer.update()

        def errors():
            out = {}
            for eff, pole, (m_upper, m_lower, _) in limbs_made:
                root = pb[m_upper].head
                axis = (pb[eff].head - root).normalized()
                out[eff] = _about(pb[m_lower].head - root, pb[pole].head - root, axis)
            return out
        first = errors()
        iks = {eff: pb[m_tip].constraints[IK_NAME] for eff, _, (_, _, m_tip) in limbs_made}
        for eff, ik in iks.items():
            ik.pole_angle = _wrap(ik.pole_angle + first[eff])
        view_layer.update()
        second = errors()
        for eff, ik in iks.items():
            if abs(second[eff]) > 0.5 * abs(first[eff]):   # The solver turns the other way: undo and go back.
                ik.pole_angle = _wrap(ik.pole_angle - 2.0 * first[eff])
    finally:
        for eff, (location, quaternion, euler) in saved.items():
            pb[eff].location, pb[eff].rotation_quaternion, pb[eff].rotation_euler = location, quaternion, euler
        obj.hide_set(hidden)
        obj.hide_viewport = hidden_viewport
        view_layer.update()


def _wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def set_ik_fk(obj, mode, ends=None, snap=True, frame=None):
    """Switch the limbs ending in ``ends`` (default all) to ``mode`` 'IK' or 'FK'; returns the limbs switched."""
    chosen = [limb for limb in limbs(obj) if ends is None or limb["end"] in ends]
    if not chosen:
        raise RigError("no_ik")
    bpy.context.view_layer.update()
    visual = {pb.name: pb.matrix.copy() for pb in obj.pose.bones}
    influence = 1.0 if mode == 'IK' else 0.0
    for limb in chosen:
        constraints = switches(obj, limb)
        if snap and mode == 'FK':
            names = (limb["upper"], limb["lower"], limb["end"])
            parent = obj.data.bones[limb["upper"]].parent
            desired = {n: visual[n] for n in names}
            if parent:
                desired[parent.name] = visual[parent.name]
            rig_pose_write.write(obj, desired, frame, keyed=names)
        elif snap:
            pole = pole_place(limb, visual, obj.pose.bones[limb["pole"]].matrix)
            desired = {limb["effector"]: visual[limb["end"]], limb["pole"]: pole}
            for name in (limb["effector"], limb["pole"]):
                parent = obj.data.bones[name].parent
                if parent:
                    desired[parent.name] = visual[parent.name]
            rig_pose_write.write(obj, desired, frame, keyed=(limb["effector"], limb["pole"]))
        for constraint in constraints:
            constraint.influence = influence
            if frame is not None:
                constraint.keyframe_insert("influence", frame=frame)
    return [limb["end"] for limb in chosen]


class ANGELO_OT_add_ik_controls(bpy.types.Operator):
    bl_idname = "angelo.add_ik_controls"
    bl_label = "Add IK Controls"
    bl_description = "Add hand and foot effectors with elbow and knee poles to a humanoid, found by role"
    bl_options = {'REGISTER', 'UNDO'}

    armature: StringProperty(name="Armature")

    def execute(self, context):
        obj = bpy.data.objects.get(self.armature) or context.active_object
        if not (obj and obj.type == 'ARMATURE'):
            self.report({'ERROR'}, rpt_("Choose an armature"))
            return {'CANCELLED'}
        try:
            count = add_ik_controls(obj)
        except RigError as error:
            self.report({'ERROR'}, explain(error))
            return {'CANCELLED'}
        self.report({'INFO'}, plural_(count, "IK controls on {count} limb", "IK controls on {count} limbs"))
        return {'FINISHED'}


class ANGELO_OT_set_ik_fk(bpy.types.Operator):
    bl_idname = "angelo.set_ik_fk"
    bl_label = "Switch IK/FK"
    bl_description = "Switch a humanoid's arms and legs between IK and FK, keeping their pose"
    bl_options = {'REGISTER', 'UNDO'}

    armature: StringProperty(name="Armature")
    mode: EnumProperty(name="Mode", items=(('IK', "IK", "Hands and feet follow their effectors"),
                                           ('FK', "FK", "The arm and leg bones are posed directly")), default='IK')
    limb: EnumProperty(name="Limbs", items=(('ALL', "All", ""), ('ARMS', "Arms", ""), ('LEGS', "Legs", "")),
                       default='ALL')
    snap: BoolProperty(name="Keep Pose", default=True)
    key: BoolProperty(name="Key", description="Key the switch at the current frame", default=False)

    def execute(self, context):
        obj = bpy.data.objects.get(self.armature) or context.active_object
        if not (obj and obj.type == 'ARMATURE'):
            self.report({'ERROR'}, rpt_("Choose an armature"))
            return {'CANCELLED'}
        ends = None
        if self.limb != 'ALL':
            roles, _, _ = role_map(obj)
            part = "Hand" if self.limb == 'ARMS' else "Foot"
            ends = {roles.get(side + part) for side in ("Left", "Right")} - {None}
        try:
            switched = set_ik_fk(obj, self.mode, ends, self.snap, context.scene.frame_current if self.key else None)
        except RigError as error:
            self.report({'ERROR'}, explain(error))
            return {'CANCELLED'}
        self.report({'INFO'}, plural_(len(switched), "{count} limb in {mode}", "{count} limbs in {mode}",
                                      mode=self.mode))
        return {'FINISHED'}


CLASSES = (ANGELO_OT_add_ik_controls, ANGELO_OT_set_ik_fk)


MENU_IDS = ("rig_ik_controls", "rig_ik_fk")   # The Rig group's rows (chat_destinations.RIG_GROUP).


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    menu = (   # The chat bar's + menu (chat_destinations.RIG_GROUP).
        chat_destinations.Feature(
            "rig_ik_controls", n_("IK controls"), n_("Hand and foot effectors with elbow and knee poles, by role"),
            glyph="skeleton", order=50, operator="angelo.add_ik_controls", owner="ik_controls"),
        chat_destinations.Feature(
            "rig_ik_fk", n_("Switch IK/FK"), n_("Arms and legs between IK and FK, keeping their pose"),
            glyph="skeleton", order=51, operator="angelo.set_ik_fk", owner="ik_controls"))
    for item in menu:
        chat_destinations.register(item)


def unregister():
    from . import chat_destinations
    for feature_id in MENU_IDS:
        chat_destinations.unregister(feature_id)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
