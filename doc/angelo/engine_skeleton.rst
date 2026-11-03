.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Rig check for a game engine
***************************

Does this armature fit a game engine's humanoid skeleton? The engine-skeleton check answers it for **Unreal Engine**
(UE5 Mannequin names, and whether the IK Retargeter's auto-retarget recognises the rig), **Unity** (Humanoid) and
**Godot** (``SkeletonProfileHumanoid``), before anything is exported. It is read-only: it reads the armature and
the meshes skinned to it, and changes nothing in the scene, its selection or Undo. It is the first, read-only step of
engine rigs (the research's item 4); converting a control rig into a game rig comes after it, once it has been
validated on held-out rigs from several rigging systems.

The person and the agent get the same report:

* **The person**: chat bar **+** > **Inspect** > **Check a rig for a game engine**, or F3 > **Check Rig for Game
  Engine** (``angelo.skeleton_check``, ``skeleton_check_ui``). It checks the active armature, the armature of an
  active skinned mesh, or the scene's only armature, and opens a corner card (``skeleton_report_card``): the engine
  and file format switch on the card, problems come first with their fixes (what each check looked for under
  Details), notes fold away, passed checks are chips, and Copy Report copies it as shown. Check Again runs it after a
  change. A report taller than the 3D view keeps the card at full size: the findings scroll under the wheel between
  the engine switches and the buttons, which stay put (``card_kit.Builder.scroll_begin``/``limit``). Every word on it
  is translated; bone and object names are shown as they are.
* **The agent**: ``angelo_skeleton_check`` {``target``: ``UNREAL`` | ``UNITY`` | ``GODOT``, ``armature``: a handle
  (default: the scene's only armature, else the active one), ``format``: ``AUTO`` | ``GLB`` | ``FBX``}. A deferred,
  read-only tool (toolset 47): a turn that never asks about an engine's skeleton carries only its name. Connected MCP
  apps get it at the Read level (``external_clients.READ_TOOLS``), headless included.

``AUTO`` is the target's usual export, the one Angelo's engine link uses: Godot ``.glb`` with Godot's own Blender
import options (``godot_link_assets.GODOT_DEFAULTS``), Unity ``.fbx`` with Unity's own Blender-to-FBX options
(``unity_link_assets.UNITY_FBX``), Unreal ``.glb`` with the shared glTF profile (``GLTF_DURABLE``), each over
Blender's defaults.

The report
==========

``skeleton_check.check(facts, target, fmt)`` returns, as plain data: the armature, target and format; the exporter
settings it assumed (deform bones only, leaf bones, influences written); the verdict (the worst status: ``fail``,
``warn``, ``info`` or ``pass``) and counts; the bones in the rig and in the exported skeleton; ``roles`` (the
humanoid role each mapped bone plays, by name); ``notes`` (what the mapping couldn't settle); ``sources`` (the rules'
sources and engine versions); and ``findings``, one per check:

``check``, ``status``, ``looked_for``, ``found``, ``fix`` (for ``warn`` and ``fail``) or ``note`` (for ``info``),
``source`` (a key of ``sources``) and, where useful, ``details`` (misses with the bone the layout found and why, the
renames to the Mannequin's names, per-mesh influence counts, the shared-prefix diagnosis).

The agent's copy (``for_agent``) lists passed checks by name only and drops the card's templates, so a clean rig
costs few tokens. Finding text is English templates with data-only placeholders (``skeleton_checks.say``): the agent
reads them filled in English, and the card translates each template before filling it (``skeleton_checks.render``).

Profiles
========

Profiles are data (``skeleton_profiles``). Every rule names its source and the engine versions it was read or run
on (``SOURCES``); the report repeats them.

.. list-table::
   :header-rows: 1

   * - Engine
     - Bones and names
     - Required
     - Parents
     - Rest pose, root, influences
   * - Godot 4.4-4.7
     - The 56 roles of ``SkeletonProfileHumanoid`` (MIT), ``Root`` > ``Hips``; names map through a BoneMap
     - Its ``is_required`` bones, read from Godot 4.7.2: Hips, Spine, Head, both shoulders and the limbs (not
       Chest or Neck)
     - Each mapped bone below its profile parent's bone
     - T-pose; ``Root`` above ``Hips`` (warning without it); 8 per vertex
   * - Unity 6000.0-6000.5
     - The 55 ``HumanBodyBones`` slots; any names (automap by hierarchy)
     - The 15 of ``HumanTrait.RequiredBone`` (read from Unity 6000.5.6f1)
     - Each mapped bone below its slot's parent's bone
     - T-pose (Enforce T-Pose fixes it); no root bone; 4 per vertex
   * - Unreal 5.4-5.8
     - UE5 Mannequin-compatible names (64 bones under ``root``), matched exactly, case-insensitively
     - ``root``, ``pelvis``, ``spine_01``, ``head`` and the limbs
     - The exact parent name
     - Any rest pose (the retargeter aligns poses); a root bone at the origin; 12 per vertex (.fbx), 4 (.glb)

**Independent authoring.** The Unreal names are short functional identifiers authored for Angelo from Epic's public
documentation of the Mannequin's naming and from rigs observed after import. No array, file or asset of Epic's is
copied into Angelo, and no Mannequin mesh, skeleton or animation ships. The Mixamo template is authored from
Mixamo-rigged files; the Rigify template from Blender's own bundled metarig. The Godot profile is Godot's, which is
MIT.

Roles by layout
===============

``skeleton_roles.map_roles`` (legs, hips, facing) and ``skeleton_upper`` (hands, arms, trunk, head) find which bone
plays each role from the rig's layout, never its names, so Rigify, Mixamo, Auto-Rig Pro, VRoid, a 3ds Max Biped, an
auto-rigger's output and a hand-made rig are judged on which bone *is* the thigh. World space, Z up, on the deforming
bones (all bones when none deforms), each parented to its nearest deforming ancestor (the hierarchy a deform-only
glTF export writes). A bone that moves nothing (no vertex weight, no object parented to it) and has no children is an
end bone and is left out.

* **Joints, not tails.** An imported skeleton (glTF, FBX without automatic bone orientation, a Biped) stores no bone
  ends; Blender's tails for it are guesses that point anywhere and can run metres long. A bone therefore ends at the
  joint of the child that carries the most of the chain below it (a VRM thigh's skirt bones are leaves; fingers
  tie and are averaged); a child starting on the bone's own joint (Rigify's face organiser) says nothing; a leaf
  ends no farther than twice its parent's joint-to-joint length.

1. **Legs.** A leg's lowest bones reach the floor and hang from a thigh or shin: a bone pointing down within about
   41 degrees of vertical that covers at least a quarter of its joint's height above the floor (relative to its own
   drop, so a chibi's short legs count). Floor bones group by the topmost thigh they hang from; when they hang from
   none (IK foot controls under the root), the legs are the lowest down-pointing chains, with a note. Hips is the
   lowest bone both legs hang from at hip height, else the bone nearest the legs' tops. Down each leg: the upper leg
   is the first bone pointing down, the foot the first bone in the leg's lowest quarter, the toes the bone after it,
   the knee the joint nearest half way; a leg ends at its farthest-reaching bone (toes, not a heel).
2. **Facing.** Forward is from the ankle toward the joint below it that reaches farthest (the toes); left is up x
   forward. Without feet, -Y in the world, Blender's convention (an imported armature's own rotation is often only
   the file format's axes), with a note.
3. **Hands** by their digits (``hand_rig.find_hands``, shared with the hand-rigging tools), never on the body's
   midline (a chibi's upper spine has three digit-like chains); without digits, the outermost weighted joint on
   each side (an IK control moves no skin).
4. **Trunk and head.** From Hips the trunk follows the child carrying the most of the body (arms and legs left out),
   not the highest point (a backpack or hat can be higher than the head). The arms branch from the lowest trunk bone
   both hands hang from, moved down while it starts above the shoulder joints (a Biped's clavicles hang from its
   neck); when the hierarchy doesn't join the hands to the trunk (Rigify's DEF arms parented through ORG bones), the
   arm is the deforming chain above each hand and the branch the trunk bone at shoulder height. Spine, Chest and
   UpperChest are the trunk bones up to the branch; above it, the neck is the run of single links and the Head the
   bone where it branches, ends, or turns away from up.
5. **Arms.** An arm whose first bone starts within 15% of the arm's reach from the midline starts with a clavicle;
   otherwise a deforming bone that starts near the midline and ends where the upper arm starts is the clavicle. The
   elbow is the joint nearest half way from the shoulder joint to the wrist.
6. **Eyes and jaw** (optional): one mirrored pair below the head and forward of its joint; one midline child of the
   head pointing forward and down.

Checks
======

.. list-table::
   :header-rows: 1

   * - Check
     - Looks for
     - Status when it misses
   * - ``bone_match``
     - Unity/Godot: every required slot found by layout, each below the bone of its parent slot. Unreal: the
       Mannequin's names under their exact parents, after a prefix every bone shares; each miss names the bone that
       plays the role (rename it) or the closest name
     - Unity and Godot ``fail``; Unreal ``warn``; Godot ``info`` when it passes (its BoneMap maps the names)
   * - ``auto_retarget`` (Unreal)
     - The template Unreal's IK Retargeter would choose, as its characteriser chooses: after the prefix every bone
       shares, the template with the most bones under their template parent by name, used only if its score (the
       mean of the shares of its bones found by name and found under that parent) is above 0.1. Angelo models 3 of
       Unreal's 23 templates (UE5 Mannequin, Mixamo, Blender Rigify, each a subset of Unreal's own), so the finding's
       ``prediction`` is ``recognised`` only when no other template could change the outcome: one chosen instead has
       at least as many matching bones, so its score is at least that count over 93 (Unreal's largest template), and
       above 0.1 some template is used whichever wins. Otherwise ``uncertain`` (a bigger unmodelled template could be
       chosen and fall under the threshold) or ``unmatched`` (none of Angelo's would be used; an unmodelled one may
       still match a few generic names). Says when most bones but not all share a prefix (Rigify's ``DEF-``), which
       Unreal then keeps, and lists the templates Angelo doesn't model
     - ``warn`` when uncertain or unmatched, and when recognised without the root or pelvis
   * - ``single_root``
     - One root bone in the exported skeleton (an FBX's armature object counts for Unreal unless it is named
       Armature and holds one root bone)
     - Unreal ``fail``; others ``info``
   * - ``root_motion``
     - Unreal and Godot: a root bone above Hips at the armature's origin. Unity Humanoid needs none
     - Unreal ``fail``, Godot ``warn``; ``warn`` when away from the origin
   * - ``deform_only``
     - Only deforming bones exported (FBX keeps a deforming bone's non-deforming ancestors)
     - ``warn``
   * - ``leaf_bones``
     - No ``_end`` bones added (FBX's Add Leaf Bones, on by default and in Unity's own import)
     - ``warn``; end bones already in the rig ``info``
   * - ``rest_pose``
     - Unity and Godot: a T-pose within 15 degrees (upper arm, forearm, leg, spine measured joint to joint)
     - ``warn``
   * - ``facing``
     - The character facing -Y by its feet
     - ``warn``
   * - ``bone_axes``
     - +Y toward the chain's next joint within 20 degrees; +Z the way each hinge bends (forearm forward, shin back,
       fingers toward the palm), Godot's +X-bends rule
     - ``info`` (Godot's Overwrite Axis rewrites them on import)
   * - ``transform``
     - The armature at rotation 0, scale 1, unit scale 1
     - ``warn``
   * - ``constraints``
     - No enabled constraints or drivers on exported bones
     - ``warn`` (engines play baked keys only)
   * - ``bendy_bones``
     - Deforming bones with one segment
     - ``warn``
   * - ``influences``
     - At most the engine's limit per vertex, and the glTF exporter's 4 unless it writes all
     - ``warn``

The exported skeleton (``skeleton_export``) is read from Blender 5.2's exporters: deform-only is off by default in
both; FBX keeps every ancestor of a deforming bone, glTF parents a kept bone to its nearest kept ancestor; FBX adds
``_end`` leaf bones by default; Unreal reads an FBX's armature object as a root bone unless it is named Armature
and holds one root bone. Names are compared as Unreal's import writes them: an FBX name loses its namespace (up to
the last ``:``), a space becomes ``-``, and ``.``, ``:`` and the other characters an Unreal object name may not hold
become ``_``.

Cost
====

Nothing runs until the tool is called or the card opens. Measured with ``bench_engine_skeleton.py`` on an M4 Pro (Mac
1, under the run's own lock, load average 4-6), against release/v1.0.8 ``ef6a846b7a6`` with the same 1.0.8 build:

* **Model-visible declaration bytes** (``coverage_prefix.py``; compact JSON sent with every request, not billed
  tokens): Claude's plan schema +24 B (the deferred name; 57,091 to 57,115, or 57,161 to 57,185 with parallel edit
  on); Codex's code-mode tool declaration +917 B (94,656 to 95,573, or 100,110 to 101,027); Codex's deferred-families
  sentence +23 B. Claude's instructions and every voice declaration are unchanged (voice threads and delegated
  collaborators never declare the tool). Its 0.9 KB schema loads with the first lookup.
* **Start-up**: ``skeleton_check_ui`` imports only ``skeleton_menu`` (the + menu row's words and glyph) and the
  tool's spec; the card, profiles and checks load on first use. Median 0.46 ms to import (40 rounds, compiled
  bytecode) and 0.02 ms to register; no handler or timer. The whole add-on's enable, base against this branch
  alternated 6 times each: 824.8 ms against 827.6 ms medians, inside the runs' spread (816-829 ms).
* **The + menu**: building its rows takes 0.020 ms with the row and 0.019 ms without (median of 400).
* **Other cards**: the shared card layout gained the scroll section the report uses. Laying out a card of 31
  paragraphs and 31 buttons without one (build and place; Blender's Python 3.13 outside the app, same lock), base
  ``card_kit`` against this branch's in one process, alternated over 31 rounds of 200: 128.4 µs against 129.4 µs medians (+0.8%), minimums 126.0 and 126.1 µs, inside each other's range.
* **One check** (median of 8): 8 ms on Rigify's generated rig (706 bones); 42 ms on a 15-bone humanoid with a
  59,702-vertex skinned mesh, nearly all of it reading the vertex groups for the influence count.

Validation
==========

* Host (``test_native_engine_skeleton``): development rigs built from joint positions, the same humanoid named four
  ways (UE5 Mannequin, Mixamo, Rigify metarig, arbitrary), posed T and A, with twist segments, turned and scaled, with
  control bones, without a root or clavicles, with a broken leg hierarchy and too many influences; every finding's
  fields and source; the agent's copy; the card's layout, actions and translation of each template.
* Blender (``test_blender_engine_skeleton``, BACKGROUND): Blender's Rigify metarig and generated rig against all three
  engines, a skinned rig's influences, constraint, driver, scale and A-pose, the refusals, and nothing changed.
* Director (``test_blender_engine_skeleton_gui``, GUI): the + menu's row opens the card; clicks switch engines and
  formats, open More problems, Notes and Details, copy the report and check again after a fix; the selection never
  changes.
* Real rigs (``skeleton_rig_survey.py`` FILE...), inside the app. Rigs a fix was developed on never judge it: each
  held-out set was fixed before it was run, and run once.

  .. list-table::
     :header-rows: 1

     * - Set
       - Rigs
       - Result
     * - Looked at while developing
       - Blender's Rigify metarig and generated rig; RobotExpressive (three.js); Rocketbox Male_Adult_04 (3ds Max
         Biped)
       - Rigify and the Biped map fully; RobotExpressive's IK feet and hand-less forearms map as far as its anatomy
         goes, with notes
     * - First held-out pass (code at ``ac947eeba1e``)
       - X Bot (Mixamo), Polygonal Mind Jennifer, Seed-san, three-vrm-girl (VRoid), Quaternius SWAT, Tiby, a Meshy
         auto-rig, the studio's chibi, an Auto-Rig Pro file, Khronos Fox
       - **Failed.** X Bot and Jennifer mapped nothing; five more mapped partly. These rigs then became development
         rigs: imported tails, chibi proportions, VRM secondary bones, a narrow stance, neck-hung clavicles, a backpack
         higher than the head and IK controls each led to a rule above. Afterwards X Bot, Jennifer, the VRM girl, the
         Meshy rig, the chibis and the ARP game rigs map fully; Seed-san's left arm is its robot-arm accessory; SWAT (IK
         feet), Tiby and the ARP control rig do not map
     * - Second held-out pass (code at ``bb4e404b97f``)
       - Quaternius Astronaut, Tiby female, VRM1_Constraint_Twist_Sample, a Meshy kunoichi, the studio's
         jake-native-rig-qa-baseline (8 armatures of one skeleton), UE 5.8's FbxEditorAutomation AnimatedCharacter,
         toonlab's Rigify-based mannequin
       - 4 of 7 rig families right on every mapped role, judged by their own bone names (VRM1, the kunoichi, the
         chibis, the mannequin). Partly: the Astronaut (IK feet outside the legs; its clavicle read as the upper arm),
         Tiby female (arms) and UE's 18-bone test character (arms)

* Engine imports (``skeleton_engine_e2e/run.py``): each rig built and judged by Angelo before any engine saw it, then
  imported with the engine's defaults.

  * **Godot 4.7.2** (headless): ``SkeletonProfileHumanoid`` read back has Angelo's 56 names and parents; its
    ``is_required`` corrected Angelo's list (both shoulders, not Chest or Neck). With BoneMaps from Angelo's roles,
    Godot's renamed skeletons agree with Angelo on all four rigs: the UE-named humanoid and the metarig accepted; the
    one-bone-arm rig (no LowerArm) and Rigify's generated rig (DEF arms and legs hanging from ORG and control bones,
    not the shoulders, chest or hips) refused.
  * **Unity 6000.5.6f1** (batchmode, the machine's licence): its 15 required slots (``HumanTrait.RequiredBone``) are
    Angelo's. Avatars agree on three of four rigs (UE-named and metarig valid humans, one-bone arms invalid).
    Rigify's generated rig disagrees: Unity's automap builds a valid avatar from its control bones (Hips ``torso``,
    Spine ``spine_fk.003``, Head ``ORG-face``), which deform nothing in an engine that runs no constraints; Angelo
    reports that the deforming chain doesn't hang together. Valid is not working here, and the report says why.
  * **Unreal Engine 5.8.2** (``-nullrhi``), every rig as .fbx and .glb, an IK Rig's Auto Create Retarget Chains on each
    imported skeleton, graded on Angelo's ``prediction``: only ``recognised`` can agree or disagree, and an
    ``uncertain`` or ``unmatched`` one is never counted as agreement. Of 32 imports (the 4 development rigs and 12
    held-out armatures from 12 files, chosen before any run, each in both formats), 10 are ``recognised`` and Unreal
    applied every one (UE-named and metarig with 29 chains; X Bot, Jennifer and Seed-san with 17 to 26), with no
    disagreement. The other 22 are ``uncertain``/``unmatched``: Unreal applied nothing on 9, a template with no chain
    on 3 (the VRM samples, toonlab's mannequin), and chains from a template Angelo doesn't model on 10 (Quaternius,
    RobotExpressive, Tiby, Rocketbox .glb: 2 to 5 chains, no pelvis; Rigify's generated .fbx: one chain, pelvis
    ``root``, unexplained by the source's rule, whose score is 0.06).

    Two earlier misses fixed by class, from Unreal 5.8.2's source (read for behaviour; nothing copied): the template
    is chosen by bones under their template parent, not by score (Rigify's generated .glb read as recognised while
    Unreal applied nothing), and names are read as the Interchange import writes them, an FBX losing its ``name:``
    namespace (the held-out Jennifer .fbx, Mixamo names under an armature root, read as unmatched while Unreal applied
    17 chains; it is a development case for that rule since). The import also confirmed an FBX's armature object
    becoming the root bone and a glTF skin with several roots getting a ``<armature>_ProxyTrueRootJoint``.

Limits
======

* Humanoids only. A quadruped or a rig without two legs and a spine is reported as not mapped, with notes.
* Unreal templates other than the three modelled ones (UE4 Mannequin, Fortnite, Daz, Character Creator 4, mGear,
  Advanced Skeleton, HumanIK and the motion-capture vendors') are named but not scored: a rig that matches only one of
  them, or matches Angelo's with fewer than 10 bones under their template parent, is reported as uncertain, never as
  recognised or not.
* The checks assume the export settings ``AUTO`` names; a person who changed them in the engine's import settings
  (Godot's ``.import``) can pass ``format`` but not each setting.
* The influence count reads at most 500,000 skinned vertices per call; beyond it the check says so.
* The Unreal prediction is a model of the characteriser with Angelo's own template lists: ``recognised`` agreed with
  Unreal on all 10 of 32 imports that got it; the other 22 say only that Angelo can't tell, and on 10 of those Unreal
  applied a few chains from a template Angelo doesn't model.
* Layouts the mapper gets wrong today: IK foot controls outside the legs (Quaternius), arms parented below the
  spine's top (Tiby), a second arm-like accessory chain (Seed-san), UE's 18-bone test character. Each says so in the
  report's notes; none is a reason to trust a mapping without reading its roles.
