.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Hand rigging
************

The Coding Agent rigs and poses hands with six read-only tools (the RIG
family, ``rig_spec``) and the guidance that comes with them
(``agent_guidance.HAND_GUIDANCE``). The tools measure an armature's hands and
the mesh skinned to it, skin candidate poses themselves and draw them
off-screen, so the scene, its pose and Undo never change. A pose they solve
comes back as ``bone_set`` operations, which the agent applies through a scene
program like any other edit: approval, preview, checks and one Undo step.

What the tools and guidance carry was learned rigging two chibi base bodies
(rigs A and B, 2026-10-21 to 2026-10-24): the anatomy
checklist the art director approved, and one rule per failure found on the way.

Tools
=====

``angelo_rig_hand_inspect``
   Finds every hand of an armature, or the one named (``hand``), by bone
   layout, not names: a bone with 3-6 digit chains of 2-3 deforming bones that
   end the limb; a chain of four starts with a metacarpal, which belongs to the
   palm. The thumb is the chain that points furthest from the hand's axis or is
   based nearest the wrist; when neither margin is clear the call is refused
   with a request for ``thumb``. The fingers are ordered from the thumb side and
   named index, middle, ring and little (three fingers: index, middle, little).
   It reports the palm normal and where it came from, the finger radius, the
   checklist items the bones can show, the current pose's joints outside their
   limits, fused (mitten) fingers and the weight audit.
``angelo_rig_hand_contact``
   The current pose's skin contact: gap and depth, in finger radii, between
   neighbouring fingers and between the thumb and each finger, how deep each
   fingertip is in the palm, and joints outside their limits.
``angelo_rig_hand_range``
   Each joint of one hand moved alone through its range, the bones past it
   following, from rest toward each limit in 2-degree steps, stopping where its
   skin sinks a third of a finger radius into the still rest of the hand. Each
   joint's reach and what stopped it; ``pictures`` adds one sheet per digit
   (rows: its joints; columns: rest, back reach, half forward, forward reach).
``angelo_rig_hand_fist``
   The fold-to-limit fist (below) for each hand: its joint angles, contacts and
   the ``bone_set`` operations that pose it; ``pictures`` adds review views.
``angelo_rig_hand_pose``
   Any other pose of one hand from anatomical joint angles (degrees from rest:
   flex, spread, twist), as ``bone_set`` operations whatever each bone's roll.
   Joints not listed keep their current angles; an end joint not listed follows
   its middle joint at 0.67; angles past a limit are clamped and named; the
   pose's contacts come with it. Writing a finger bone's rotation by hand is
   what failed every trial task that tried it: a bone's own axes follow its
   roll, and rotations about them bent the middle and end joints sideways.
``angelo_rig_hand_views``
   Pictures of one hand's current pose, each beside its rest pose: BACK, PALM,
   THUMB_SIDE, LITTLE_SIDE and PALM_THUMB (the palm turned toward the thumb),
   framed from the hand's own frame with the fingers up. Skin sunk deeper than
   contact into another part is red; ``markers`` dots the joints.

Every tool takes the armature's handle; ``mesh`` names the skinned mesh when
more than one mesh's Armature modifier uses the armature, ``palm_normal`` (a
world direction out of the palm) overrides the palm normal of one named hand,
and ``limits`` (``[{bone, flex|spread|twist: [low, high]}]``, degrees) overrides
default ranges for that call. The family is deferred: a Claude plan looks it
up with ``angelo_agent_capabilities`` (family TOOLS and the names), a Codex
thread calls it in the ``more__`` namespace, and either receives
``HAND_GUIDANCE`` with the exact schemas.

Worker chats
============

The rig tools read the scene of Angelo's own window and draw pictures there, so they are the lead chat's in 1.0.6. A
worker's chat, hosted in its worker Blender (A′) or on the lead's lanes, is not offered them
(``parallel_access.restrict``), is refused them with nothing run (``worker_refusals``), and is not told of them by
discovery (``parallel_access.discovery_hidden``: a worker chat's ``angelo_agent_capabilities`` lists only its own
set). Its + menu has no Work on hands (``agent_chat.rst``, "The + menu"). ``test_native_rig_tools`` pins all three.

Choosing the hand tools
=======================

A model that has the hand tools does not always reach for them: in the trials
below GPT-6.1 Sol looked them up in 8 of 24 hand-posing tasks, and every task
it posed by hand failed. The person can choose them for a request: **Work on
hands** in the composer's **+** menu (``chosen_tools``), a chip until Send. At
Send, before the observation is built, ``chosen_tools_host`` reads the selected
armature's hands (the active armature, the armature of the active skinned mesh,
or the scene's only armature; otherwise the chat row asks which) exactly as
``angelo_rig_hand_inspect`` does and adds them, with the tools' names and
``HAND_GUIDANCE``, to that request's observation under ``hands``. ``hands`` is an
event key of the context ledger: sent with that request only, never repeated as
state. Nothing in the scene changes and the person's mode stays as it was; the
chat row reads "Gave the agent the hands of <armature>".

Planned, not built: detecting a hand request without the person's choice (a
request that names a hand pose on a rigged character), so the agent starts
primed as it does when the person chooses **Work on hands**.

Joint frames and limits
=======================

A joint's frame comes from the rest joint positions (the bones' heads), never
from bone roll, so it means the same in every rig. The palm normal points out
of the palm, perpendicular to the forearm (``hand_rig.set_palm_normals``):

1. as given (``palm_normal``);
2. else toward the body's midline, between the two hands or toward armature
   x = 0 with one, which is the palm side of a rest pose with the arms down and
   the palms toward the thighs; when the fingers' rest curl points the other way
   the palm faces outward and the curl's side wins (the result says so);
3. else, when the forearm runs across the body (a T-pose) and the midline is
   lost, the side the fingers' rest curl turns them, which is the palm side by
   anatomy;
4. else the hand is refused with a request for ``palm_normal``.

It is never the normal of the knuckles' plane: the knuckles arch, which tilts
that plane by about 20 degrees, and fingers bent about it curl askew (tried on
rig A and rejected against the reference photos). On the reference rigs the
midline normal and the knuckle-plane normal are 21 (rig A) and 14 (rig B)
degrees apart.

A joint's rotation relative to its parent, in the rest frame, is split as the
rigs' skeleton contract splits it: flex turns the bone toward the palm (the
thumb toward the fingers), spread toward the thumb side (the thumb toward the
palm), twist about the bone. Default limits, by role, are the contract's
(degrees, back to forward):

.. list-table::
   :header-rows: 1

   * - Joint
     - Flex
     - Spread
     - Twist
   * - Finger knuckle (MCP)
     - -30 to 90 (ring 95, little 100)
     - -15 to 20 (index), ±15, -25 to 15 (little)
     - ±5
   * - Finger middle joint (PIP)
     - 0 to 110
     - 0
     - 0
   * - Finger end joint (DIP)
     - -10 to 90, following the middle joint by 0.67
     - 0
     - 0
   * - Thumb base (CMC)
     - -20 to 50
     - -15 to 70
     - ±30
   * - Thumb MCP
     - -10 to 60
     - ±10
     - ±10
   * - Thumb IP
     - -20 to 80
     - ±5
     - ±5

Bone settings
=============

A predicted pose is composed as Blender composes it (``hand_inherit``, a port of armature.cc's
``BKE_bone_parent_transform_calc_from_matrices``), so applying its ``bone_set`` operations gives the pose the tools
judged. The reader captures every setting that changes that composition: Inherit Rotation (off, a hinge, keeps
its parent's rest orientation), each Inherit Scale mode (Full, Fix Shear, Aligned, Average, None, None Legacy),
Local Location and Connected (whose location channel Blender ignores); Relative Parenting changes only objects
parented to a bone. Joint angles stay relative to the parent's posed bone, so a hinge finger's ``bone_set`` carries
its parent's turn. The skinning check recomputes the current pose with this model from the pose bases, not
Blender's matrices, so a setting it got wrong shows as a difference. ``test_blender_hand_inherit`` compares the
model with Blender's evaluated pose for each setting (all 0.0000 finger radii; the old full-inheritance model was
up to 3.4 off, 2.8 for the audit's Inherit Rotation case).

The pose tools refuse, with the reason, what the model cannot reproduce: a bone under the hand that follows a
constraint or driver and deforms or carries deforming bones; a deforming bone elsewhere whose constraint targets,
or whose driver reads, a bone under the hand; a skinned mesh parented to a bone under the hand; and an armature
showing its Rest Position.

Skinning and contact
====================

``hand_skin.Skin`` blends the deforming bones' vertex-group weights linearly,
normalised per vertex, as Blender's Armature modifier does. Every call compares
it with Blender's own deformed mesh on the current pose and reports the
largest difference in finger radii (``skinning_check``); above 0.05 the result
is marked uncertain, above 0.5 the pose tools refuse. On both kit bodies the
difference is 0.0. That check cannot see a control rig: finger bones that
follow a constraint or a driver deform as Blender shows them, but a
``bone_set`` rotation on them would be overridden. Such bones are named
(``rig_read``) and the pose tools refuse them, asking for the bones that drive
them to be posed (Bone settings, above). Bones whose chains are no hand (a skirt, hair, a tail) are
skipped unless named, and so is a hand without skin of its own, with the reason
in the result. Dual-quaternion (Preserve Volume) skinning, modifiers before
the Armature modifier, vertex-group-masked shape keys and envelopes are stated
as uncertainty, not reproduced.

A hand's skin is flooded from its fingertips along mesh edges without crossing
the wrist plane, so a thigh or the other hand lying close by is never part of
it. A vertex belongs to the part (a digit or the palm) whose bones carry most
of its weight. The finger radius is the median distance of the fingers' middle
segment skin from its bone; every distance the tools judge by is a fraction of
it, never millimetres of one hand. The rigs' millimetre thresholds became
fractions of rig A's finger radius (9.5 mm).

Each hand works on its own skin only: ``HandSkin`` keeps the drawn region's vertices (``mesh_index`` maps them
back), each part's search tree holds only the vertices its triangles use and is built once per pose, and the
region flood walks a sorted adjacency. Unrelated vertices in the mesh cost only the read: the 600-vertex fixture
fist with 199,000 unrelated vertices took 2.7-5.0 s against 3.2-5.5 s without them, where the base took 228-326 s
(both packages interleaved in one background Blender on a loaded machine, ``nice``; not a timed claim). The read
still visits every vertex's groups in Python (0.5-1.7 s there).

Contact is skin to skin, never capsules (``hand_contact``). A vertex is inside
a finger when it is behind that finger's nearest skin face and nearer its bone
axis than that skin is (each part's skin is an open surface ending at the webs,
so a face's side alone misreads vertices beyond its edge); inside the palm when
it is behind the palm's nearest face. Skin that already touches another part at
rest (a web) is left out, so only contact the pose makes counts. Skin resting
on skin is a gap under 0.035 finger radii; pressing in up to 0.07 is how flesh
meets in a fist; deeper is through.

The fist
========

The fist is folded, not aimed (the art director: "fold each finger to the max
for each joint"):

1. each knuckle to its flex limit, turning toward the base of the thumb as it
   bends (0, 4, 8 and 12 degrees of spread for the index, middle, ring and
   little fingers at a full bend);
2. the middle joints in 2-degree steps, each end joint following at 0.67, until
   a finger's middle and end segments sink 0.11 finger radii into the palm's
   skin, then one step back, or to the limit;
3. each finger closes sideways toward its neighbour nearer the middle until
   their skins rest on each other (or opens if the curl already sank one into
   the other);
4. the thumb: its five angles (base flex, spread, twist; MCP and IP flex)
   searched within its limits for its last joint at the index finger's middle
   knuckle and its tip on the middle finger's middle segment, its last segment
   across the row of fingers and its base out of the fist's front silhouette;
   the best placements are checked on the skin, settled onto it, and folded on
   (IP, then MCP) until the thumb rests on the fingers without sinking in;
5. every joint clamped to its limits; ``outside_limits`` is then empty.

One budget, ``hand_pose.MAX_SECONDS`` (20 s), bounds the whole call, reading the rig included
(``hand_math.Budget``). Spent before the solve, the fist is refused with the time the read took; spent during it,
each stage stops where it is and the result says so: ``complete`` false, ``budget.ran_out_during`` (folding the
fingers, closing them sideways, placing, settling or resting the thumb), and an ``apply`` text calling the
operations a partial fist. A range call lists the joints it did not measure.

On the reference rigs, solved from the rigs alone, the fingers fold as the rigs' own
fists do: middle joints 94, 94, 74, 76 degrees on rig A (the rigs' 100, 94, 74,
74) and 98, 96, 74, 72 on rig B (104, 96, 74, 72), the sideways closing within
half a degree, every fingertip on the palm, nothing outside the limits, in
about 3-5 s a hand. The thumb lies across the index and middle fingers and
rests on them as in the rigs' photographs, placed differently from the rigs'
(its base 9-18 degrees, its last joint up to 21): the kit placed it against
capsules and photo targets, these tools against the skin alone. Rig A's last
thumb joint bends 73 degrees, where the rigs' handover expects about 70 once its
thumb base is rebuilt with the rigs' later fixes (52 on its current rig).

Per-joint ranges reproduce the rigs' joint sheets within 4 degrees for 19 of
rig A's 20 joint axes and 18 of rig B's. The ring finger's knuckle reaches 10-16
degrees further before its palm-side skin meets the palm, and rig B's little
finger's knuckle 8 degrees further, where the stop is a crease of skin folding
against skin; the tools stop on the still skin outside the fold zone, as the
kit does, and differ there in which skin they count as still.

Checklist and weights
=====================

``hand_skin.checklist`` measures what the bones can show: the middle finger
longest; ring and index about equal; the little finger's tip at about the ring
finger's end joint; the knuckles' arc along the hand's length (wrist to middle
knuckle), middle farthest and little nearest; finger bones about 50/30/20 %;
the palm about as long as the middle finger; the thumb's base low on the palm;
the rest thumb forward of the palm's own plane (the rigs' thumbs: 26 degrees).
Both kit bodies pass every item.

``hand_skin.weight_audit`` reports:

* cuts: a bone's weight stepping by 0.4 or more across one edge where that step
  is at least 0.8 of the change over the edge's two vertices and their
  neighbours. A fade done over two edges is a ramp there on any mesh density
  (the rigs' knuckle fade, 0.5 an edge, approved); the rigs' thumb cut (0.55 to
  0 across one edge, flat both sides) is a step, and it folded the back of the
  hand when the thumb turned. Each names its joint and distance.
* finger weight more than two finger radii behind its knuckle (heat weights
  gave the rigs' first rig palm skin 25 mm behind the knuckles);
* weight from bones outside the arm on the hand (heat weights put thigh weight
  on 626 of rig B's hand vertices; the fix drops it and renormalises, never moves
  it to the hand bone, which starved the thumb's base).

A mitten hand, whose fingers are one blob, can't be measured or folded: a
segment between neighbouring fingers' middle bones that never leaves the skin
marks them fused. The guidance says to replace such a hand from a known-good
hand's fit inputs (its joints, the skin near the hand, its forearm frame),
placed by the forearm's frame and scaled by a true cross-section of the forearm
above the wrist; refitting a fitted hand compounded its error on rig B.

Pictures
========

``rig_views`` draws the hand's skin (the hand and a stretch of forearm past the
wrist) through ``view_weights``' off-screen drawer, with its own colours:
skin, red where sunk deeper than contact (skin already inside another part at
rest is the mesh's fold, shown by the rest picture beside, not red), yellow
joint markers. Pictures are
delivered once with the result, like staged previews (``plan_recovery``'s
``PREVIEW_IMAGE_TOOLS``); a model without images gets the result as text. The
drawer needs a GPU context, so the picture tools work in the app, not in a
background process.

glTF imports
============

Angelo's own glTF imports weld a file that has morph targets (``gltf_morph``;
:doc:`generative_studio`, "Models with morph targets"). A skinned file without
morph targets imports as Blender's default does. Blender's importer drops
morph-target normals, and separate meshes meeting at a seam can show a shading
line whether welded or not, so a pose or morph can look faceted where the asset
is fine: the guidance says to confirm it in the target runtime.

Applying a pose
===============

A fist's ``bone_set`` operations are one scene program on the armature. Its
admission walks the armature's embedded data for unsupported custom links with
a depth bound of 16 links; that walk went depth first, so a long chain of
parent and child pointers reached the bones of a humanoid rig 17 to 21 links
deep and every program on such a rig was refused ("Embedded settings exceed
bounded native admission limits"), a single ``bone_set`` included. The walk is
breadth first now (``program_admission``): the same holders and budget, each at
its shortest link distance, 4 on both kit rigs and the fixture rig.

Trials
======

Pre-registered before any billed run (2026-10-24): the ``rig`` task set
(``eval_rig_tasks``), four tasks on harness-made skinned hands (a fist arms down,
a fist in a T-pose, a fist on a three-fingered hand, the index finger curled),
each in a new chat, three trials per arm; an arm with ``HAND_GUIDANCE`` and one
without it (``--hand-guidance off``), both with the tools; graded by the closed
``hand_pose`` check; the guidance "helps" only if it passes at least 3 more of
12. GPT-6.1 Sol, effort low, on the 1.0.5-tree development build:

.. list-table::
   :header-rows: 1

   * - Arm
     - Passed
     - Used the hand tools
     - Passed with them
     - Passed without them
   * - With the guidance
     - 5 of 12
     - 5
     - 5 of 5
     - 0 of 7
   * - Without it
     - 3 of 12
     - 3
     - 3 of 3
     - 0 of 9

By the registered rule there is no measurable difference at this size. The
tools decide the outcome: every task in which the model used
``angelo_rig_hand_fist`` passed, and none in which it posed bones itself did.
Those bent the middle and end joints 8-32 degrees sideways (rotations about a
bone axis that is not the joint's flex axis), outside the limits, and twice
left the thumb in the palm. The guidance cannot raise the tools' use: it
arrives with their schemas, after a model has chosen to look them up, and Sol
looked them up in 8 of 24 tasks. The index-finger curl has no solver of its
own; it passed once, when the model took the fist's index rotations.

A second registered run (the same tasks and grading, GPT-6.1 Sol) added
``angelo_rig_hand_pose``, its line in the guidance, and the person's **Work on
hands** choice, and compared the choice made against not made:

.. list-table::
   :header-rows: 1

   * - Arm
     - Passed
     - Used the hand tools
   * - Work on hands chosen
     - 12 of 12
     - 12
   * - Not chosen
     - 11 of 12
     - 11 (the one task without them failed: joints outside their limits,
       skin through skin)

By the registered rule the choice makes no measurable difference at this size
(+1). Every index-finger curl passed with ``angelo_rig_hand_pose`` (6 of 6).
Without the choice the tools were used in 11 of 12 tasks against 5 of 12 in the
first run's guidance arm: the new tool and guidance line are one difference,
the time of day and the run are others, so that comparison is not a measured
effect.

Claude Opus 5.5 was not run: the bench account's Claude connection was not
signed in on the machine. One earlier Sol trial was lost to a harness fault
(the task records lacked their empty ``checks``), fixed before these runs; its
one message is not counted.

Validation
==========

* ``test_native_hand_rig.py``: detection across layouts (metacarpals, end
  bones, three fingers, T-pose, either side), palm normals, roll-independent
  frames, angle round trips, kinematics, skinning, parts, checklist, limits
  and the weight audit, on synthetic hands (``hand_fixtures``).
* ``test_native_hand_inherit.py``: every inheritance setting's composition, the refusals, the compact skin's
  answers equal to the whole mesh's, the flood, and the budget's refusal and partial result.
* ``test_blender_hand_inherit.py``: predictions against Blender's evaluated pose for each setting and the refusals
  on real objects; ``--timing`` times the fist with unrelated vertices against a base package.
* ``test_native_rig_tools.py``: the family's declaration, deferral, guidance,
  picture delivery and argument checks.
* ``test_blender_rig_tools.py``: the tools in Blender on skinned metaball hands
  (``hand_fixture_scenes``: hanging, T-pose, cartoon, mitten, beside a thigh),
  the skinning check and the fist's operations applied through a scene
  program.
* Reference rigs A and B, measured locally (not in the repository): the
  figures above.
