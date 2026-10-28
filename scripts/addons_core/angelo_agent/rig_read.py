# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The RIG tools' reading of an armature and its skinned mesh into arrays (``Rig``), with the checks that say how far
the tools can trust them: their skinning and pose composition (``hand_inherit``) against Blender's own on the
current pose, and what the forward model cannot reproduce, which the pose tools refuse: bones under the hand that
follow constraints or drivers (a control rig), bones elsewhere that follow them, a mesh parented to one of them and an
armature showing its rest position. Main thread; reads only.
"""

import numpy as np

from .refusal_codes import RefusalError
from . import hand_rig as H, hand_skin as S
from .live_target import WindowAttribute

MAX_VERTICES = 300_000      # skinned mesh vertices read per call
MAX_BONES = 1024
SKINNING_NOTE = 0.05        # finger radii between this skinning and Blender's that the result reports
SKINNING_REFUSE = 0.5       # finger radii beyond which pose tools refuse


class Rig:
    """One armature and its mesh as arrays, plus the hands found on them."""

    window = WindowAttribute()   # Read live each time: never a freed window held past its file (live_target).
    budget = None                # The call's hand_math.Budget (rig_host); a solver's own when None.

    def __init__(self, window, args):
        from . import scene_queries
        armature = scene_queries.resolve_object(window, args["armature"])
        if armature.type != "ARMATURE":
            raise RefusalError("armature must be an armature object", code="VALIDATION")
        self.window, self.armature = window, armature
        self._read(self._mesh(args.get("mesh")), args)

    @classmethod
    def of(cls, window, armature, mesh, args):
        """The same reading for objects already in hand (the live eval's hand checks grade with it)."""
        rig = cls.__new__(cls)
        rig.window, rig.armature = window, armature
        rig._read(mesh, args)
        return rig

    def _read(self, mesh, args):
        from . import scene_queries
        self.mesh = mesh
        self.handle = scene_queries.object_handle(self.armature)
        self.mesh_handle = scene_queries.object_handle(self.mesh)
        self.uncertainty = []
        self.skeleton = self._skeleton()
        self.skin, self.blender_positions = self._skin()
        world = np.array(self.armature.matrix_world, dtype=np.float64)
        self.world = world
        given = None
        if "palm_normal" in args:
            # A world direction into armature space (rotation only).
            local = np.linalg.inv(world[:3, :3]) @ np.asarray(args["palm_normal"], dtype=np.float64)
            given = {}
        try:
            hands = H.find_hands(self.skeleton, args.get("hand"), args.get("thumb"))
            if not hands:
                raise H.HandError("No bone of this armature has 3-6 digit chains of 2-3 deforming bones below "
                                  "it; name the hand bone (hand) if its digits are laid out differently")
            if given is not None:
                if len(hands) != 1:
                    raise H.HandError("palm_normal applies to one hand: name it (hand)")
                given[hands[0].name] = local
            H.set_palm_normals(hands, given)
            self.hands, self.skins = [], []
            for hand in hands:
                try:
                    self.skins.append(S.HandSkin(hand, self.skin))
                    self.hands.append(hand)
                except H.HandError as error:
                    if args.get("hand"):
                        raise
                    # Unnamed: one hand without skin of its own (a Mirror modifier before the Armature modifier
                    # leaves the other side's groups empty) never refuses the other hand.
                    self.uncertainty.append({"code": "hand_skipped", "detail": str(error)})
            if not self.skins:
                raise H.HandError(self.uncertainty[-1]["detail"])
        except H.HandError as error:
            raise RefusalError(str(error), code="VALIDATION") from None
        self.checks = [self._skinning_check(hs) for hs in self.skins]
        self.driven = {hs.hand.name: _driven(self.armature, self.skeleton, hs.hand.bone) for hs in self.skins}
        for name, bones in self.driven.items():
            if bones:
                self.uncertainty.append({"code": "driven_fingers", "detail": (
                    "Bones of hand %r follow constraints or drivers (%s): their stored rotations are not what "
                    "Blender shows, so the pose tools refuse; pose the bones that drive them"
                    % (name[:64], ", ".join(b[:64] for b in bones[:6])))})
        self.unreproducible = {hs.hand.name: _unreproducible(self.armature, self.mesh, self.skeleton, hs.hand.bone)
                               for hs in self.skins}

    def _mesh(self, handle):
        from . import scene_queries
        if handle is not None:
            mesh = scene_queries.resolve_object(self.window, handle)
            if mesh.type != "MESH" or not _armature_modifier(mesh, self.armature):
                raise RefusalError("mesh must be a mesh with an Armature modifier using this armature",
                                   code="VALIDATION")
            return mesh
        found = [obj for obj in self.window.scene.objects if obj.type == "MESH" and _armature_modifier(obj, self.armature)]
        if len(found) == 1:
            return found[0]
        bones = {b.name for b in self.armature.data.bones if b.use_deform}
        weighted = [obj for obj in found if sum(g.name in bones for g in obj.vertex_groups) >= 10]
        if len(weighted) == 1:
            return weighted[0]
        if not found:
            raise RefusalError("No mesh in the scene has an Armature modifier using this armature; bind one first",
                               code="VALIDATION")
        handles = ", ".join(scene_queries.object_handle(obj) for obj in (weighted or found)[:8])
        raise RefusalError("Several meshes use this armature; name one (mesh): " + handles, code="VALIDATION")

    def _skeleton(self):
        arm, pose = self.armature.data, self.armature.pose
        bones = list(arm.bones)
        if len(bones) > MAX_BONES:
            raise RefusalError("The armature has more than %d bones" % MAX_BONES, code="LIMIT_EXCEEDED")
        index = {b.name: i for i, b in enumerate(bones)}
        parents = [index[b.parent.name] if b.parent else -1 for b in bones]
        rest = [np.array(b.matrix_local, dtype=np.float64) for b in bones]
        tails = [np.array(b.tail_local, dtype=np.float64) for b in bones]
        basis = [np.array(pose.bones[b.name].matrix_basis, dtype=np.float64) for b in bones]
        posed = [np.array(pose.bones[b.name].matrix, dtype=np.float64) for b in bones]
        deform = [b.use_deform for b in bones]
        # Every setting that changes how a pose bone's matrix composes (hand_inherit honours each).
        return H.Skeleton([b.name for b in bones], parents, rest, tails, basis, posed, deform,
                          inherit_rotation=[b.use_inherit_rotation for b in bones],
                          inherit_scale=[b.inherit_scale for b in bones],
                          local_location=[b.use_local_location for b in bones],
                          connect=[b.use_connect for b in bones],
                          relative_parent=[b.use_relative_parent for b in bones])

    def _skin(self):
        mesh_obj, skel = self.mesh, self.skeleton
        data = mesh_obj.data
        count = len(data.vertices)
        if count > MAX_VERTICES:
            raise RefusalError("The mesh has more than %d vertices" % MAX_VERTICES, code="LIMIT_EXCEEDED")
        if data.is_editmode:
            raise RefusalError("The mesh is in Edit Mode; leave it so its weights are current", code="VALIDATION")
        modifier = _armature_modifier(mesh_obj, self.armature)
        if modifier.use_bone_envelopes or not modifier.use_vertex_groups:
            self.uncertainty.append({"code": "envelopes", "detail": "The Armature modifier uses bone envelopes or "
                                     "no vertex groups; the tools skin by vertex groups only"})
        if modifier.use_deform_preserve_volume:
            self.uncertainty.append({"code": "preserve_volume", "detail": "The Armature modifier preserves volume "
                                     "(dual quaternions); the tools blend linearly, so bent joints differ slightly"})
        before = [m for m in mesh_obj.modifiers[:list(mesh_obj.modifiers).index(modifier)] if m.show_viewport]
        if before:
            self.uncertainty.append({"code": "modifiers_before_armature", "detail": "Modifiers before the "
                                     "Armature modifier are not applied: " + ", ".join(m.type for m in before[:4])})
        co = np.empty(count * 3)
        data.vertices.foreach_get("co", co)
        co = co.reshape(count, 3)
        keys = data.shape_keys
        if keys is not None and not mesh_obj.show_only_shape_key:
            co = _shape_mix(keys, co, self.uncertainty)
        to_armature = np.linalg.inv(np.array(self.armature.matrix_world)) @ np.array(mesh_obj.matrix_world)
        rest = co @ to_armature[:3, :3].T + to_armature[:3, 3]
        names = {g.index: skel.index.get(g.name) for g in mesh_obj.vertex_groups}
        deforming = {gi: bi for gi, bi in names.items() if bi is not None and skel.deform[bi]}
        vertex, bone, weight = [], [], []
        for v in data.vertices:
            for g in v.groups:
                b = deforming.get(g.group)
                if b is not None and g.weight > 0.0:
                    vertex.append(v.index)
                    bone.append(b)
                    weight.append(g.weight)
        tris = np.empty(len(data.loop_triangles) * 3, dtype=np.int64)
        data.loop_triangles.foreach_get("vertices", tris)
        edges = np.empty(len(data.edges) * 2, dtype=np.int64)
        data.edges.foreach_get("vertices", edges)
        skin = S.Skin(rest, vertex, bone, weight, tris.reshape(-1, 3), edges.reshape(-1, 2))
        # Blender's own deformed positions now, for the skinning check.
        evaluated = mesh_obj.evaluated_get(self.window.view_layer.depsgraph)
        temporary = evaluated.to_mesh()
        try:
            if len(temporary.vertices) == count:
                now = np.empty(count * 3)
                temporary.vertices.foreach_get("co", now)
                now = now.reshape(count, 3) @ to_armature[:3, :3].T + to_armature[:3, 3]
            else:
                now = None
                self.uncertainty.append({"code": "modifiers_change_topology", "detail": "Modifiers change the "
                                         "mesh's vertex count; contact is judged on the Armature modifier's cage"})
        finally:
            evaluated.to_mesh_clear()
        return skin, now

    def _skinning_check(self, hand_skin):
        if self.blender_positions is None:
            return {"hand": hand_skin.hand.name[:64], "compared": False}
        # The current pose recomputed by the tools' own forward model from the bases, not Blender's matrices: a
        # setting the model got wrong shows here as a difference (hand_inherit).
        idx = hand_skin.vertices
        posed = H.forward_kinematics(self.skeleton, hand_skin.hand.bone, {})
        ours = hand_skin.skin.deform(self.skeleton, posed, idx)
        theirs = self.blender_positions[hand_skin.mesh_index[idx]]
        error = float(np.max(np.linalg.norm(ours - theirs, axis=1))) / hand_skin.finger_radius
        check = {"hand": hand_skin.hand.name[:64], "compared": True, "largest_difference_finger_radii": round(error, 4)}
        if error > SKINNING_NOTE:
            self.uncertainty.append({"code": "skinning_differs", "detail": (
                "The tools' skinning of hand %r differs from Blender's by up to %.2f finger radii (constraints, "
                "drivers, shape keys or other modifiers)" % (hand_skin.hand.name[:64], error))})
        return check

    def require_reproducible(self, hand_skin):
        reason = self.unreproducible.get(hand_skin.hand.name)
        if reason:
            raise RefusalError(reason, code="UNSUPPORTED")
        driven = self.driven.get(hand_skin.hand.name)
        if driven:
            raise RefusalError("Bones of hand %r follow constraints or drivers (%s), as in a control rig: "
                               "bone_set rotations on them would be overridden. Pose the bones that drive them."
                               % (hand_skin.hand.name[:64], ", ".join(b[:64] for b in driven[:6])),
                               code="UNSUPPORTED")
        for check in self.checks:
            if check["hand"] == hand_skin.hand.name[:64] and check.get("largest_difference_finger_radii", 0) > SKINNING_REFUSE:
                raise RefusalError("The tools cannot reproduce how Blender deforms hand %r (off by %.2f finger radii), "
                                   "so they can't judge its skin contact; check the Armature modifier, constraints "
                                   "and shape keys" % (hand_skin.hand.name[:64], check["largest_difference_finger_radii"]),
                                   code="UNSUPPORTED")

    def to_world(self, points):
        return points @ self.world[:3, :3].T + self.world[:3, 3]

    def direction_to_world(self, v):
        """A direction (armature space) in world space: through the full linear map, so a mirrored (negatively
        scaled) armature keeps its sides."""
        return H.unit(self.world[:3, :3] @ v)


def _driven(armature, skel, root):
    """The bones whose pose follows something else and moves the hand's skin: under the hand (``root``), a bone with
    an enabled constraint with influence or a driver on a channel that deforms or carries deforming bones; and any
    deforming bone elsewhere whose constraint targets or driver reads a bone under the hand (it would move with a
    pose the tools predict without it)."""
    drivers = list(getattr(armature.animation_data, "drivers", None) or ())
    paths = {driver.data_path for driver in drivers}
    below = set(skel.subtree(root)) - {root}
    below_names = {skel.names[i] for i in below}
    found = []
    for i in skel.order:
        name = skel.names[i]
        pose_bone = armature.pose.bones.get(name)
        if pose_bone is None:
            continue
        constraints = [c for c in pose_bone.constraints if c.enabled and c.influence > 0]
        driven = [d for d in drivers if d.data_path.startswith('pose.bones["%s"].' % name)]
        if i in below:
            if (constraints or any(p.startswith('pose.bones["%s"].' % name) for p in paths)) and any(
                    skel.deform[b] for b in skel.subtree(i)):
                found.append(name)
        elif skel.deform[i] and i != root:
            targets = {getattr(c, "subtarget", "") for c in constraints if getattr(c, "target", None) == armature}
            for d in driven:
                for variable in d.driver.variables:
                    targets.update(t.bone_target for t in variable.targets if t.id == armature)
            if targets & below_names:
                found.append(name)
    return found


def _unreproducible(armature, mesh, skel, root):
    """Why a predicted pose of the hand under ``root`` would not be what Blender shows once applied, or None."""
    if getattr(armature.data, "pose_position", "POSE") == "REST":
        return ("The armature shows its Rest Position, so Blender ignores every pose and a bone_set rotation would "
                "not show; set the armature to Pose Position first")
    below = {skel.names[i] for i in skel.subtree(root)} - {skel.names[root]}
    if mesh.parent == armature and mesh.parent_type == "BONE" and mesh.parent_bone in below:
        return ("The mesh is parented to bone %r of the hand, so posing the hand also moves the whole mesh; the "
                "tools predict skin with the mesh held still. Parent it to the armature or a bone above the hand"
                % mesh.parent_bone[:64])
    return None


def _armature_modifier(obj, armature):
    return next((m for m in obj.modifiers if m.type == "ARMATURE" and m.object == armature), None)


def _shape_mix(keys, co, uncertainty):
    """The mesh's active shape-key mix (relative keys, no vertex-group masks), as the Armature modifier receives it."""
    if not keys.use_relative:
        uncertainty.append({"code": "absolute_shape_keys", "detail": "Absolute shape keys are not mixed"})
        return co
    out = co.copy()
    count = len(co)
    basis = np.empty(count * 3)
    keys.key_blocks[0].data.foreach_get("co", basis)
    out = basis.reshape(count, 3).copy()
    for block in keys.key_blocks[1:]:
        if block.mute or block.value == 0.0:
            continue
        if block.vertex_group:
            uncertainty.append({"code": "masked_shape_key", "detail": "Shape key %r is masked by a vertex group; "
                                "it is mixed unmasked" % block.name[:64]})
        a, b = np.empty(count * 3), np.empty(count * 3)
        block.data.foreach_get("co", a)
        block.relative_key.data.foreach_get("co", b)
        out += (a - b).reshape(count, 3) * block.value
    return out
