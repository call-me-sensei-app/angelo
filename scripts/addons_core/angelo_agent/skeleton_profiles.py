# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-engine skeleton profiles the skeleton check (``skeleton_checks``) measures a rig against, as data. No
Blender dependency.

* **Roles** are one humanoid role set, shaped like Godot's ``SkeletonProfileHumanoid`` (MIT): 56 bones under
  ``Root`` > ``Hips``. ``skeleton_roles`` finds which bone of a rig plays each role from its layout, never from its
  names, so every engine profile is a table from these roles to the engine's own names and parents.
* **Profiles** say, per engine, which roles it requires, whether names must match exactly, how parents are compared,
  the rest pose and root bone it wants, and its bone-influence limit per file format.
* **Unreal templates** model the IK Retargeter's auto-characterisation (``skeleton_unreal``). Every name list here
  is authored for Angelo from public documentation, Blender's own Rigify metarig and rigs observed after import;
  none is copied from an engine's source or assets, and no engine asset ships with Angelo.

Every rule names its ``SOURCES`` entry: where the fact comes from and the engine versions it was read or run on.
doc/angelo/engine_skeleton.rst is the contract.
"""

from .skeleton_check_spec import FORMATS, TARGETS  # noqa: F401  (the tool's own lists; re-exported)

SIDES = ("Left", "Right")
FINGERS = ("Index", "Middle", "Ring", "Little")
FINGER_JOINTS = ("Proximal", "Intermediate", "Distal")
THUMB_JOINTS = ("Metacarpal", "Proximal", "Distal")


def _humanoid_roles():
    """(role, parent role) for the 56 roles, parents first."""
    roles = [("Root", None), ("Hips", "Root"), ("Spine", "Hips"), ("Chest", "Spine"), ("UpperChest", "Chest"),
             ("Neck", "UpperChest"), ("Head", "Neck"), ("LeftEye", "Head"), ("RightEye", "Head"), ("Jaw", "Head")]
    for side in SIDES:
        roles += [(side + "Shoulder", "UpperChest"), (side + "UpperArm", side + "Shoulder"),
                  (side + "LowerArm", side + "UpperArm"), (side + "Hand", side + "LowerArm")]
        parent = side + "Hand"
        for joint in THUMB_JOINTS:
            roles.append((side + "Thumb" + joint, parent))
            parent = side + "Thumb" + joint
        for finger in FINGERS:
            parent = side + "Hand"
            for joint in FINGER_JOINTS:
                roles.append((side + finger + joint, parent))
                parent = side + finger + joint
    for side in SIDES:
        roles += [(side + "UpperLeg", "Hips"), (side + "LowerLeg", side + "UpperLeg"), (side + "Foot", side + "LowerLeg"),
                  (side + "Toes", side + "Foot")]
    return tuple(roles)


ROLES = _humanoid_roles()
ROLE_PARENT = dict(ROLES)
# Roles the mapper may also report but no humanoid profile names: a finger's metacarpal (Unreal has them).
EXTRA_ROLES = tuple(side + finger + "Metacarpal" for side in SIDES for finger in FINGERS)


SOURCES = {
    "godot_profile": ("Godot SkeletonProfileHumanoid class reference and 'Retargeting 3D Skeletons' (docs 4.7): 56 "
                      "bones, Root > Hips, T-pose, +Y from parent to child joint, +X rotation bends like a "
                      "contracting muscle; bones and required flags read from SkeletonProfileHumanoid in Godot "
                      "4.7.2 (MIT)", "4.4-4.7"),
    "unity_humanoid": ("Unity 6 Manual 'Using Humanoid Characters' (at least 15 bones, T-pose, no root bone needed) "
                       "and 'Avatar Mapping'; required bones read from HumanTrait.RequiredBone in Unity 6000.5.6f1",
                       "6000.0-6000.5"),
    "unity_hierarchy": ("Unity 6 Manual 'Configuring the Avatar': each mapped bone must sit below the bone mapped "
                        "to its parent slot; bones between them are allowed", "6000.0-6000.5"),
    "ue_characterizer": ("Unreal Engine 5.8.2 IK Rig auto-characterisation (IKRigAutoCharacterizer.cpp, read for "
                         "behaviour): after removing a prefix every bone shares, the template with the most bones "
                         "found by name (case-insensitive) under the same parent name is chosen, the first on a tie; "
                         "it is used only if the mean of its share of bones found and found under the same parent "
                         "is above 0.1, else nothing is set up. 23 templates, the largest 93 bones. Template lists "
                         "here are Angelo's own", "5.4-5.8"),
    "ue_root_motion": ("Unreal Engine 5.8 'Root Motion': motion is read from the skeleton's root bone; the "
                       "retargeter reports 'could not find target root bone' without it", "5.0-5.8"),
    "ue_single_root": ("UE 5.8 skeletal mesh import: a reference skeleton has exactly one root bone", "5.0-5.8"),
    "ue_fbx_armature": ("UE 5.8 Interchange FBX parser: only a root node named 'armature' (any case) is dropped; any "
                        "other armature object becomes an extra root bone. Bone names: an FBX name loses its namespace "
                        "(up to the last ':'), a space becomes '-', and characters an object name may not hold "
                        "('.', ':' and others) become '_' (FBX and glTF; observed in UE 5.8.2). A glTF skin with "
                        "several roots gets one '<armature>_ProxyTrueRootJoint' above them", "5.0-5.8"),
    "ue_pose": ("Unreal Engine 5.8 'IK Rig Retargeting': the retargeter aligns the source and target retarget "
                "poses itself (Auto Align), so no rest pose is required", "5.4-5.8"),
    "fbx_leaf": ("Blender 5.2 io_scene_fbx: add_leaf_bones defaults to True and appends '_end' to every chain; "
                 "Unity's own Blender import (Unity-BlenderToFBX.py, 6000.5) leaves it on", "Blender 5.2"),
    "deform_only": ("Blender 5.2 exporters: glTF export_def_bones and FBX use_armature_deform_only default to off; "
                    "FBX keeps a deforming bone's non-deforming ancestors, glTF parents it to its nearest deforming "
                    "ancestor (io_scene_gltf2 tree.py). Epic's Send to Unreal exports deforming bones only",
                    "Blender 5.2"),
    "influences": ("three.js r185 / glTFast 6 read 4 (JOINTS_0); Godot 4.7.2 reads 8 (JOINTS_0..1); Unity 6 Rig tab "
                   "Skin Weights 'Standard (4 bones)'; UE 5.8 GPUSkinPublicDefs MAX_TOTAL_INFLUENCES 12; Blender's "
                   "glTF exporter writes 4 unless export_all_influences (Godot's Blender import turns it on)",
                   "Godot 4.7, Unity 6000.5, UE 5.8, Blender 5.2"),
    "constraints": ("Engines play baked keys only: Blender's FBX (bake_anim) and glTF (export_force_sampling) "
                    "exporters sample constraints and drivers into keys; nothing in the engine evaluates them",
                    "Blender 5.2"),
    "transform": ("Epic Send to Unreal validate_scene_scale and the FBX Skeletal Mesh Pipeline (UE 5.8): unit scale "
                  "1 and applied object transforms; a scaled armature stretches retargeted poses", "UE 5.0-5.8"),
    "bendy": ("Engines skin with plain linear blending: a B-Bone's segments reach the engine as one rigid bone "
              "(GameRig's statement of Rigify's game problems; Auto-Rig Pro export documentation)", "all"),
    "godot_axes": ("Godot 'Retargeting 3D Skeletons' (4.7): the importer's Rest Fixer > Overwrite Axis, on by "
                   "default once a BoneMap is set, rewrites bone axes to the profile's", "4.4-4.7"),
    "facing": ("Blender: characters face -Y, Z up; the glTF and FBX exporters turn that into each engine's forward",
               "Blender 5.2"),
}


class Profile:
    """One engine's humanoid skeleton contract. ``names``: role -> the engine's bone name; ``parents``: role -> the
    role the engine compares as its parent; ``required``: roles without which the engine refuses or maps nothing."""

    def __init__(self, target, display, names, parents, required, exact_names, parent_rule, root, rest_pose,
                 axes, influences, default_format, sources):
        self.target, self.display = target, display
        self.names, self.parents = dict(names), dict(parents)
        self.required = tuple(required)
        self.exact_names = exact_names      # True: the engine finds bones by these names (Unreal)
        self.parent_rule = parent_rule      # "direct": the exact parent; "ancestor": anywhere above
        self.root = root                    # "required" | "optional" | "none": a root bone above Hips
        self.rest_pose = rest_pose          # "T" | None
        self.axes = axes                    # "fixed_on_import" | "info"
        self.influences = dict(influences)  # format -> the most bone influences per vertex that reach the engine
        self.default_format = default_format
        self.sources = dict(sources)

    def roles(self):
        return [role for role, _ in ROLES if role in self.names]


# --- Godot -----------------------------------------------------------------------------------------------------------

# Required in SkeletonProfileHumanoid (is_required), read from Godot 4.7.2 (skeleton_engine_e2e): no Chest or Neck,
# but both shoulders.
GODOT_REQUIRED = ("Hips", "Spine", "Head", "LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
                  "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand", "LeftUpperLeg", "LeftLowerLeg",
                  "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot")

GODOT = Profile(
    "GODOT", "Godot", names={role: role for role, _ in ROLES}, parents=ROLE_PARENT, required=GODOT_REQUIRED,
    exact_names=False, parent_rule="ancestor", root="optional", rest_pose="T", axes="fixed_on_import",
    influences={"GLB": 8, "FBX": 8}, default_format="GLB",
    sources={"names": "godot_profile", "hierarchy": "godot_profile", "root": "godot_profile", "rest": "godot_profile",
             "axes": "godot_axes", "influences": "influences"})


# --- Unity Humanoid ----------------------------------------------------------------------------------------------------

def _unity_name(role):
    """Unity's HumanBodyBones slot for a role: the thumb's three bones are Proximal, Intermediate and Distal there."""
    for side in SIDES:
        for joint, unity in zip(THUMB_JOINTS, ("Proximal", "Intermediate", "Distal")):
            if role == side + "Thumb" + joint:
                return side + "Thumb" + unity
    return role


UNITY_REQUIRED = ("Hips", "Spine", "Head", "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm",
                  "RightLowerArm", "RightHand", "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg",
                  "RightLowerLeg", "RightFoot")
UNITY = Profile(
    "UNITY", "Unity", names={role: _unity_name(role) for role, _ in ROLES if role != "Root"},
    parents={role: parent for role, parent in ROLES if role != "Root" and parent != "Root"},
    required=UNITY_REQUIRED, exact_names=False, parent_rule="ancestor", root="none", rest_pose="T", axes="info",
    influences={"FBX": 4, "GLB": 4}, default_format="FBX",
    sources={"names": "unity_humanoid", "hierarchy": "unity_hierarchy", "root": "unity_humanoid",
             "rest": "unity_humanoid", "axes": "unity_humanoid", "influences": "influences"})


# --- Unreal Engine (UE5 Mannequin-compatible names) ------------------------------------------------------------------

def _ue_skeleton():
    """(name, parent name, role) of a UE5 Mannequin-compatible skeleton, authored for Angelo from Epic's public
    documentation of the Mannequin's bone naming (root, pelvis, spine_01..05, neck_01..02, head, clavicle, upperarm,
    lowerarm, hand, thumb_01..03, <finger>_metacarpal and _01..03, thigh, calf, foot, ball; _l and _r)."""
    out = [("root", None, "Root"), ("pelvis", "root", "Hips")]
    spine_roles = {1: "Spine", 2: "Chest", 5: "UpperChest"}
    parent = "pelvis"
    for k in range(1, 6):
        out.append(("spine_%02d" % k, parent, spine_roles.get(k)))
        parent = "spine_%02d" % k
    out += [("neck_01", "spine_05", "Neck"), ("neck_02", "neck_01", None), ("head", "neck_02", "Head")]
    ue_fingers = {"Index": "index", "Middle": "middle", "Ring": "ring", "Little": "pinky"}
    for side, s in zip(SIDES, ("l", "r")):
        out += [("clavicle_" + s, "spine_05", side + "Shoulder"), ("upperarm_" + s, "clavicle_" + s, side + "UpperArm"),
                ("lowerarm_" + s, "upperarm_" + s, side + "LowerArm"), ("hand_" + s, "lowerarm_" + s, side + "Hand")]
        parent = "hand_" + s
        for k, joint in enumerate(THUMB_JOINTS, 1):
            out.append(("thumb_%02d_%s" % (k, s), parent, side + "Thumb" + joint))
            parent = "thumb_%02d_%s" % (k, s)
        for finger, ue in ue_fingers.items():
            metacarpal = "%s_metacarpal_%s" % (ue, s)
            out.append((metacarpal, "hand_" + s, side + finger + "Metacarpal"))
            parent = metacarpal
            for k, joint in enumerate(FINGER_JOINTS, 1):
                out.append(("%s_%02d_%s" % (ue, k, s), parent, side + finger + joint))
                parent = "%s_%02d_%s" % (ue, k, s)
    for side, s in zip(SIDES, ("l", "r")):
        out += [("thigh_" + s, "pelvis", side + "UpperLeg"), ("calf_" + s, "thigh_" + s, side + "LowerLeg"),
                ("foot_" + s, "calf_" + s, side + "Foot"), ("ball_" + s, "foot_" + s, side + "Toes")]
    return tuple(out)


UE_SKELETON = _ue_skeleton()
UE_REQUIRED = ("Root", "Hips", "Spine", "Head", "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm",
               "RightLowerArm", "RightHand", "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg",
               "RightLowerLeg", "RightFoot")
UNREAL = Profile(
    "UNREAL", "Unreal Engine", names={role: name for name, _, role in UE_SKELETON if role},
    parents={role: next((r for n, _, r in UE_SKELETON if n == parent), None)
             for name, parent, role in UE_SKELETON if role},
    required=UE_REQUIRED, exact_names=True, parent_rule="direct", root="required", rest_pose=None, axes="info",
    influences={"GLB": 4, "FBX": 12}, default_format="GLB",
    sources={"names": "ue_characterizer", "hierarchy": "ue_characterizer", "root": "ue_root_motion",
             "rest": "ue_pose", "axes": "ue_characterizer", "influences": "influences"})

PROFILES = {"GODOT": GODOT, "UNITY": UNITY, "UNREAL": UNREAL}
if tuple(PROFILES) != TARGETS:
    raise RuntimeError("skeleton_check_spec.TARGETS must list every profile")


# --- Unreal auto-characterisation templates ---------------------------------------------------------------------------

def _mixamo():
    """Mixamo's auto-rigger skeleton (observed in Mixamo-rigged files; Mixamo prefixes every name with
    ``mixamorig:``, which the characteriser's shared-prefix rule removes)."""
    out = [("Hips", None), ("Spine", "Hips"), ("Spine1", "Spine"), ("Spine2", "Spine1"), ("Neck", "Spine2"),
           ("Head", "Neck")]
    for side in SIDES:
        out += [(side + "Shoulder", "Spine2"), (side + "Arm", side + "Shoulder"), (side + "ForeArm", side + "Arm"),
                (side + "Hand", side + "ForeArm")]
        for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky"):
            parent = side + "Hand"
            for k in (1, 2, 3):
                out.append(("%sHand%s%d" % (side, finger, k), parent))
                parent = "%sHand%s%d" % (side, finger, k)
        out += [(side + "UpLeg", "Hips"), (side + "Leg", side + "UpLeg"), (side + "Foot", side + "Leg"),
                (side + "ToeBase", side + "Foot")]
    return tuple(out)


def _rigify_metarig():
    """Blender's Rigify human metarig (bundled rigify/metarigs/human.py), body and hands, under the armature object
    an FBX names ``metarig``; Unreal reads Blender's ``.L`` as ``_L``."""
    out = [("metarig", None), ("spine", "metarig")]
    for k in range(1, 7):
        out.append(("spine_%03d" % k, "spine" if k == 1 else "spine_%03d" % (k - 1)))
    for s in ("L", "R"):
        out += [("shoulder_" + s, "spine_003"), ("upper_arm_" + s, "shoulder_" + s), ("forearm_" + s, "upper_arm_" + s),
                ("hand_" + s, "forearm_" + s)]
        for k, finger in enumerate(("f_index", "f_middle", "f_ring", "f_pinky"), 1):
            palm = "palm_%02d_%s" % (k, s)
            out.append((palm, "hand_" + s))
            chains = [finger] + (["thumb"] if k == 1 else [])
            for chain in chains:
                parent = palm
                for j in (1, 2, 3):
                    out.append(("%s_%02d_%s" % (chain, j, s), parent))
                    parent = "%s_%02d_%s" % (chain, j, s)
        out += [("thigh_" + s, "spine"), ("shin_" + s, "thigh_" + s), ("foot_" + s, "shin_" + s),
                ("toe_" + s, "foot_" + s)]
    return tuple(out)


class Template:
    """A characterisation template as Angelo models it: ``bones`` (name, parent name) and the retarget root and
    pelvis the retargeter needs."""

    def __init__(self, name, bones, root, pelvis):
        self.name, self.bones, self.root, self.pelvis = name, tuple(bones), root, pelvis


UE_TEMPLATES = (
    Template("UE5 Mannequin", [(name, parent) for name, parent, _ in UE_SKELETON], "root", "pelvis"),
    Template("Mixamo", _mixamo(), "Hips", "Hips"),
    Template("Blender Rigify", _rigify_metarig(), "spine", "spine"),
)
# Templates Unreal 5.8.2 also has that Angelo does not model (their names are not public documentation).
UE_UNMODELLED = ("UE4 Mannequin", "Fortnite", "Daz 3D", "Character Creator 4", "mGear", "Advanced Skeleton",
                 "HumanIK", "Xsens", "Vicon", "OptiTrack", "Mocopi", "Move One", "Qualisys", "Rokoko", "Meshcapade")
UE_THRESHOLD = 0.1   # the characteriser uses the chosen template only above this score
UE_LARGEST_TEMPLATE = 93   # bones in Unreal 5.8.2's largest template: bounds the score of one Angelo doesn't model
UE_TEMPLATE_COUNT = 23     # Unreal 5.8.2's templates (UE_UNMODELLED groups Fortnite's six under one name)
