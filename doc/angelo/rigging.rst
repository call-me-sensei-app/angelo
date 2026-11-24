.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Rigging: retargeting, game rigs, root motion, clips and IK
**********************************************************

Angelo retargets animation between humanoids, turns a control rig into an engine game rig with root motion and one
clip per action, and adds IK controls with an IK/FK switch. It is written from public behaviour, the engines' public
skeleton documentation and published research. Every piece pairs bones by the **role** each plays, found from the
rig's layout by the rig check's mapper (``skeleton_roles.map_roles``, ``engine_skeleton.rst``), never by name, so
Rigify, Mixamo, Auto-Rig Pro, VRoid, a 3ds Max Biped, a BVH and a hand-made rig are handled alike. The rig check
(``angelo_skeleton_check``) is the gate before and after.

Status: built and covered by fail-first suites, with background and GUI proofs in a built app. The agent's typed
operations are owed, and auto-rigging moves to the next release (the maintainer's ruling, 2026-11-20; see the end).

Modules
=======

.. list-table::
   :header-rows: 1

   * - Module
     - Responsibility
   * - ``retarget.py``
     - Retargeting by role, the IK/FK choice and the foot-contact pass
   * - ``game_rig.py``
     - The engine game rig: build, bake, mesh copies, the rig check's verdict
   * - ``game_rig_names.py``
     - The game rig's names, parents and filler bones, as plain data (no Blender)
   * - ``root_motion.py``
     - Root motion from the hips
   * - ``action_clips.py``
     - One clip per action, glTF and Unity's FBX convention
   * - ``clip_set.py``, ``clip_stage.py``
     - What one clip is, for both formats; the stage of copies the clips play on (the person's scene never changes)
   * - ``ik_controls.py``
     - IK controls and the IK/FK switch
   * - ``body_ik_solve.py``
     - The full-body pose solver, as plain data (no Blender)
   * - ``body_pose.py``
     - Full-body posing on a rig: its pose by role in, the solved rotations out
   * - ``rig_pose_write.py``
     - Writing a pose by armature-space matrices without side effects
   * - ``rig_edit.py``
     - Edit Mode from a tool, NLA off, actions and slots, the shared error codes
   * - ``rigging_ops.py``
     - The F3 operators

Operators: ``angelo.retarget_animation``, ``angelo.make_game_rig``, ``angelo.extract_root_motion``,
``angelo.export_action_clips``, ``angelo.add_ik_controls``, ``angelo.set_ik_fk``, ``angelo.pose_body``. Each refuses with a translated
message from ``rig_edit.explain`` (no hips, no root, no action, an excluded armature, a driven limb, an NLA track of
several strips) and changes nothing when it refuses.

How poses are written
=====================

Every step that knows where a bone should end up (retargeting, the game rig's bake, root motion, the IK/FK snap)
writes it with ``rig_pose_write.write``: parents first from the rest pose and the bones' current local values, so no
view-layer update runs per bone. It writes and keys only the bones it is told to; every other bone keeps its values
and curves. Each bone is written in its own rotation mode (quaternion, axis-angle, any Euler order, continuous with
the value it had), so a rig keyed in Euler keeps playing its curves; quaternions keep the previous key's side.

Retargeting
===========

``retarget(source, target, root_motion, limbs, plant_feet)`` bakes the source's active action onto the target as a new
action; the source never changes.

* **Pose matching.** Each paired bone takes the source bone's change from its rest pose, turned into the target's
  facing (the rigs may face different ways), after the target's rest direction is turned onto the source's, joint to
  joint (``Rig.ends``, never bone tails, which imported rigs set anywhere). A T-pose source drives an A-pose target.
* **Hips.** Their travel scales by the rigs' hip heights above their floors, turned into the target's facing. Root
  motion: **Keep**; **In Place** (the travel along the floor is dropped); **On the Root** (the bone above the hips
  carries it).
* **Unpaired target bones** (extra fingers, twist bones, props) keep their own values.
* **IK controls on the target.** ``limbs='FK'`` keys them off; ``'IK'`` keys each effector where the retargeted hand
  or foot is and each pole on the side that limb bends, so IK bends it the way the source does (with the poles
  left at rest, limbs strayed up to 8 degrees from the source).
* **Feet planted** (``plant_feet``). Stretches of at least two frames where a source foot is within 3% of the
  source's height of its lowest point and moves less than 0.6% of the height per frame are contacts. On the target,
  that foot's effector holds one floor place for the stretch (the stretch's mean, in the travelling frame when the
  clip plays in place, so the foot moves back with the floor); the legs are in IK for it, and IK controls are added
  when the rig has none (the receipt says so).

Game rig
========

``make_game_rig(control, engine)`` makes a deform-only, engine-named copy of a control rig. Nothing on the control
rig changes.

**The skeleton** (``game_rig_names.plan``, plain data):

* The control rig's deforming bones, at their rest placement, each under its nearest deforming ancestor (the hierarchy
  a deform-only export writes): Rigify's DEF bones hang from DEF bones, not ORG bones.
* A bone that plays a role takes the engine's name for it. Any other bone keeps its name unless the engine's skeleton
  uses it (a prop bone called ``pelvis``), which would put two bones under one name: it becomes ``pelvis_extra`` and
  the receipt says so.
* A deforming bone above the hips with no role is the rig's root and takes the root's name.
* **Unreal** finds bones by exact name under their exact parent. The plan therefore lays the rig on the UE5
  Mannequin's skeleton (``skeleton_profiles.UE_SKELETON``):

  * a role bone below twist or helper bones (Rigify's forearm below an upper-arm twist segment) hangs from its
    Mannequin parent directly; the helpers stay below that parent;
  * role-less bones between two Mannequin bones take the Mannequin's names on that path in order (a five-bone spine
    is ``spine_01``..``spine_05``);
  * a Mannequin bone the rig lacks is inserted as a **filler** between its parent and the bones that hang below it:
    ``spine_03``..``spine_05`` for a two-bone spine, ``neck_02``, clavicles, metacarpals, ``root``. A filler deforms
    nothing (no vertex group), so skinning is unchanged; a run of fillers is spaced evenly between the bones it joins,
    and a root sits at the armature's origin.

* **Godot** gets a ``Root`` above the hips when the rig has none, and every bone it requires that the rig lacks
  (both shoulders on a rig without clavicles), as fillers from the role tree; nothing optional is added. **Unity**
  gets no root (a Humanoid avatar ignores bones above the hips and reads root motion from the body): a role-less
  deforming bone above the hips is left out, the hips carry its motion, and its vertex group merges into theirs.

**The bake.** Every action the control rig plays (the active one and each NLA strip's) is baked, one action each,
from its evaluated pose (constraints and IK included), with the rig's NLA off so other tracks never blend in.

**Meshes.** Each mesh skinned to the control rig is copied in place (its world placement kept), bound to the game
rig, with its vertex groups renamed (two passes, so a new name may be another group's old one). The game rig and its
meshes go in one collection of their own (``<rig> <engine> rig``) beside the control rig.

**The receipt** carries the game rig, the bones and fillers, the notes, the baked actions, the mesh copies and the
engine's rig check verdict with any warning or failure by check.

Root motion
===========

``extract_root_motion(armature, mode, yaw)``, for every action the armature plays, each with its NLA off:

* **On the Root**: the hips' travel along the floor moves to the bone above the hips; with ``yaw``, their turn about
  the vertical too (a turning clip turns the root, as Unreal and Godot read it). The root's other children (foot
  effectors, props) keep their place in the world, so planted feet stay planted.
* **In Place**: the travel (and with ``yaw`` the turn) is taken out of the hips; the root, if any, is left alone.

The hips' rest place is the reference, so a clip that starts away from the origin keeps that offset on the root, and
running it again changes nothing.

Clips
=====

``export_action_clips(armature, filepath, fmt, replace)`` writes the armature and the meshes skinned to it with one
animation per clip (``clip_set``). A clip is the active action (with the armature's slot) or the one unmuted strip
of an unmuted track (with the strip's own slot, trim, scale and repeat); muted strips and tracks are not clips, a
track with several unmuted strips is a sequence and is refused by name, and two clips of one action take the track's
name beside the action's. A clip lasts what it plays: the strip's frames, or the action's own range, subframes and
frames below 0 included. A clip without a slot for the armature is refused by name: nothing adds a slot to the
person's action. A hidden or excluded object is refused by name rather than left out of a file that says it was
written, and so is data linked from another file or a library override (the stage cannot rename or copy it).

Both formats play the clips on a stage of copies (``clip_stage``): a copy of the armature (its armature data) and
of each skinned mesh (its own mesh data and shape keys; two objects sharing one mesh get a mesh each, in the file
too), under the originals' names while the originals are renamed aside, one NLA track per clip. The copies keep no
action or track of the person's but keep their drivers, pointed at the copies, so a corrective shape key driven by a
bone plays in every clip. Each clip's strip carries the clip's slot, frames, trim, scale, repeat, blend in and out
and animated influence and time. A scene's range cannot start below frame 0, so each clip plays from a non-negative
whole frame (the first at or after its start). The person's actions, NLA, slots, drivers and selection are never
changed; the names, the scene's frame range and its current frame (subframe included) are put back, each step on its
own whatever another raised, and the copies removed.

* **glTF** (Godot, Unreal, the web): one file whose animations are exactly the clips (Blender's exporter writes one
  animation per track of the stage). A mesh whose shape keys have an unmuted track named like a clip plays it in that
  clip. Options: ``engine_link_assets.GLTF_DURABLE``.
* **FBX** (Unity's convention for clips in separate files): ``<model>.fbx`` with the meshes and skeleton and no
  animation, posed as the first clip's first frame, and one ``<model>@<clip>.fbx`` per clip, baked over every whole
  frame of the clip (the scene's range is the clip's while it plays), holding the armature alone and the meshes whose
  shape keys play in that clip (a track named like it, or drivers). Unity's FBX
  options (``unity_link_assets.UNITY_FBX``). The model file is the file picked when it already ends in ``.fbx``. Two
  clips that would write one clip file (their names clean to one, or differ only in case: one file on macOS and
  Windows) are refused by name, never one clip lost.

Where the export writes (the 2.1.0 exports review; the clip export break-it review of 980206f3f0a): the save browser
starts in the document's folder with one clean file name (``export_names.default_export_path``), never a folder
taken from the armature's name. C counts the person's pick for that operator's run only, and nothing in the export
promotes it. A clip file already beside the picked one that Angelo did not make this session is the person's: the
browser's own panel lists those files with a **Replace Existing Clip Files** box (unticked whenever the browser
opens; eight names, the last line counting any more), and only with it ticked are they replaced, in that same run,
vouched for that one call (``owned_paths.vouched_during``); unticked, the export is refused naming them. Replace
covers exactly the files the box showed when it last drew (``clip_paths.confirmed``): one that appeared after is
asked about again. A folder or a link at a clip name is
never a file to replace: it is not offered, and the export is refused naming it (``clip_paths.NotAFile``). That is
the same whether or not an assistant script has run (before one, C itself allows every path). An assistant script's export has no pick: C
refuses it the picked file and any file of the person's; it cannot make new clip files beside one (C makes those
only for Angelo's own code, never while a script runs), and can replace only clip files Angelo made this session.
The refusals in the running, confined app are ``test_blender_clip_export_director_gui``'s; what each clip holds is
``test_blender_clip_export``'s, and its edges (frames below 0, drivers, linked data, a failed restore step, subframes,
long names, strip blends, the model's pose) ``test_blender_clip_export_edges``'s.

IK controls
===========

``add_ik_controls`` adds, per arm and leg found by role:

* an **effector** at the hand's or foot's joint, oriented like it, hanging from the bone above the hips (or from
  nothing), never from the hips, so moving the hips leaves planted feet where they are;
* a **pole** off the elbow or knee on the side the limb bends to (for a straight limb, by the rig's facing: knees
  forward, elbows back), hanging from the hips so knees and elbows follow the body;
* an **IK mechanism**: hidden, non-deforming exact rest copies of the upper and lower bone and of the hand or foot.
  The IK sits on the copied hand or foot, reaching its head for the effector over the two copies above it (without
  the tail, Blender's solver leaves the owner out), so it doesn't depend on where an imported rig's tails point; the
  pole angle is measured, not predicted: with each effector moved a fifth of the way in, it is turned until the
  bent elbow or knee lies toward its pole, so neither a rig's bone roll nor the solver's frame matters. The copied
  forearm or shin starts bent by a degree in its own pose: Blender's IK can't shorten a perfectly straight chain,
  and an imported T-pose limb often is one. The deforming upper and lower bones follow their copies (Copy Transforms) and the hand or foot
  turns with the effector (Copy Rotation): those three constraints are what the IK/FK switch sets and keys.

Controls and the mechanism are built at their bone's rest place with their length first: a new bone given a matrix
while it has no length loses its direction. The game rig copies its bones the same way.

A limb whose bones are already driven by the rig's own constraints or drivers (a generated Rigify rig's deforming
bones follow its control bones) is refused by name: that rig has its own IK/FK switch. A hidden armature is shown for
the edit and hidden again; one in an excluded collection is refused. A rerun replaces the controls.

``set_ik_fk(obj, mode, ends, snap, frame)`` switches limbs through the constraints' influence. With ``snap`` the limb
keeps its pose: to FK the bones take the pose IK gave them; to IK the effector moves to the hand or foot and the pole
to the side the limb bends. With ``frame`` the switch is keyed.

Full-body pose
==============

``body_pose.pose_body(obj, targets, pin_feet)`` poses the whole body from targets for any of the hands, the feet, the
head and the hips, the feet held where they are unless targeted. A hand reaching past the arm's reach pulls the chest
and moves the hips as far as the planted legs allow.

* **The solver** (``body_ik_solve.solve``, plain data) is the multi-effector form of FABRIK (Aristidou and Lasenby,
  Graphical Models 73(5), 2011, section 5) on the role tree: backward from every target and pinned joint to the hips
  (a joint with several children, the chest or the hips, takes the mean of what each asks), then forward from the
  hips, which float. The children of a joint with several (the hips' spine and legs, the chest's neck and arms) hang
  from one bone, so the forward pass places them by one rotation, the best fit to where those on a path ask to be
  (Horn's quaternion fit): placed one by one, the branch's shape would deform and no rotation of the bone could
  reproduce the solve. Joints on no path to a target keep their place relative to their parent. Elbows and knees keep
  bending the side they bend at rest (by the rig's facing for a straight limb): a bend that flips is mirrored back
  across the limb's base-to-end line, which keeps both bone lengths. Bone lengths never change. A target out of reach
  is reported with the distance left, never faked; the iteration count is bounded.
* **On the rig** the solve starts from the current pose (posing is incremental). A bone with one child on the role
  tree turns by the smallest rotation that points it at its child's new place; the hips and the chest by the
  rotation that turns all their children's offsets (Kabsch; exact, since the solve kept the branch rigid); the hips
  also move. A pinned foot keeps its
  orientation; a hand and the head turn with the bone they hang from. Other bones keep their values. The root bone
  (which carries travel) is not part of the body. ``apply`` writes it (``rig_pose_write``, each bone in its own
  rotation mode).
* **Range of motion** (``body_ik_limits.enforce``; ``limits=False`` turns it off): the constrained form of FABRIK
  (Aristidou and Lasenby 2011, section 4) as swing cones. A link's frame is carried down the body, its parent's frame
  turned by the smallest arc onto where the link points; a link points at most its role's range from its rest
  direction in that frame, and what hangs below turns with it. The chest turns its neck and arms as one bone, held to
  the tightest of their ranges; the hips' children are free. Ranges (``body_pose.RANGE``) are typical active ranges
  of motion (elbow 150, knee 135 degrees from straight, less the rest bend; hip 120; neck 45; clavicle 30). Pinned
  chains are left to their pins. The receipt names the joints a limit held.
* **Balance** (``balance=False`` turns it off; only with a foot planted): the centre of mass of the body's segments
  (``body_pose.MASS``, de Leva 1996 fractions; a missing role's share goes to the nearest present one above it), seen
  from above, stays over the planted feet and toes widened by half a foot's breadth (0.055 of the height). Each
  iteration the hips are shifted by the deficit summed so far, before the pins' reach is enforced: pins first, then
  balance, then the range of motion, then the targets. A pose that already balances is unchanged. The receipt gives
  the distance the centre of mass is left outside.

Where a person finds them
=========================

Chat bar **+** > **Rig** (the group in the hands tile's place) > Retarget animation (the active armature takes the
other selected one's animation), Make game rig, Root motion, Export clips (a save browser), IK controls and Switch
IK/FK, each also in F3. Pose Body is F3 and the agent's: it needs the points the hands, feet, head or hips go to.

Validation
==========

* Host (``test_native_game_rig_names``): the plan on rigs shaped the ways real rigs differ (two-, three- and five-bone
  spines, one- and two-bone necks, no clavicles, no root, a root of another name, twist bones, DEF under ORG, a prop
  named like an engine bone): every Mannequin name under its Mannequin parent, fillers deform nothing, runs spaced
  between their ends, Godot's Root, Unity's none.
* Background suites: ``test_blender_retarget`` (unlike rigs, T/A pose, facing, root motion modes, feet slide, planted
  feet), ``test_blender_game_rig`` (NLA isolation, the prop name, mesh copy placement, the collection, all three
  engines' rig checks, clips with an existing track, Unity's FBX set, the multi-strip refusal),
  ``test_blender_root_motion`` (Euler-keyed walks: in place with a root, on the root with a prop, reruns, yaw),
  ``test_blender_ik_controls`` (rest unchanged, reach, poles, the IK/FK snap, retargeting onto the controls, the
  driven-limb refusal, a hidden armature).
* Full-body pose: host (``test_native_body_ik_solve``: an adult in T and A pose and a chibi; lengths, pinned feet,
  knees forward, out of reach reported, joints off every path rigid, no pins, determinism; ``test_native_body_ik_limits``:
  elbow, neck and chest-branch cones, wide limits change nothing, the centre of mass over two feet and one on far
  reaches forward, sideways and back, a balanced pose unchanged) and background (``test_blender_body_pose``: two
  unlike rigs, a hand past the arm's reach, feet planted, rotation modes kept, balance on a far reach against the
  same pose without it, the elbow's fold within range).
* Owed: real rigs held out from development (frozen manifest before the first run), the engine imports
  (``skeleton_engine_e2e``) with root motion and clips, solve and bake time per character.

Owed
====

* The agent runs the rigging operators in its scripts today, as the ``feature_guide`` entry "rigging" tells it. Their
  typed operations (declared in ``rigging_wrapped_ops``; their time bounds are the Linux VM's, provisional, until
  Mac 1 and the Windows VM measure them) reach the agent once the wrapped-operation runner calls them, gated by
  held-out scenario runs on Opus 5.5 and GPT 6.1 Sol with identical grading.
* Twist limits (rotation about a bone's own axis); the cones bound swing only.
* Auto-rigging on Rigify, in the next release (not in 2.1.0: no row, operator, agent verb or string of it ships;
  ``test_native_withheld_features``): landmarks from the mesh, markers the person checks before anything is built,
  the metarig fitted and generated, automatic weights with a fallback for non-manifold meshes.
* A filler whose only bones below are other fillers (a rig with clavicles' children but no neck) is placed at the
  origin; no tested rig has that shape.
