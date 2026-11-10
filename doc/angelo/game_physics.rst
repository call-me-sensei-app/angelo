.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

############
Game physics
############

A person marks what an object is to a game engine (a body and a collision
shape) and every glTF export Angelo performs writes it as the standard
``OMI_physics_body`` and ``OMI_physics_shape`` extensions, which Godot 4.4 and
newer import as physics nodes. Nothing in the file is Angelo's own: it is
plain, inspectable glTF that any OMI reader understands, and it replaces
Godot's name-suffix conventions (``-col``, ``-colonly``, ``-rigid``). This
document is the contract (Angelo 1.0.9).

.. contents::
   :local:

The settings
============

``Object.angelo_physics`` (a property group the glTF add-on registers in every
session, ``io_scene_gltf2/angelo_physics/props.py``) on meshes, curves, text,
surfaces, metaballs and empties:

* **Body**: None, Static (level geometry), Rigid (moved by the simulation, with
  **Mass**), Kinematic (moved by animation or scripts) or Trigger (detects what
  enters it, never collides).
* **Shape**: None, Box, Sphere, Capsule, Cylinder, Convex Hull or Mesh (exact,
  static only). Without a body, a shape makes a static collider. Below a body,
  a shape is part of it (a compound body); below a Trigger it is a trigger
  shape.
* **Size**: Fit to Mesh (default) fits the primitive, at every export, to the
  bounds of the object's geometry as the export writes it: the evaluated
  geometry, or for a mesh an armature deforms the rest pose its skin binds
  (what an engine loads, not the pose of the current frame): a box is the
  bounds, a sphere's radius half the longest side, a capsule or cylinder runs
  along **Axis** (Longest by default; Z on a tie, then Y) with the radius of
  the larger other side; a capsule is at least its two hemispheres. The
  export, the panel's fitted size and Edit Fitted Size share this one
  measurement (``io_scene_gltf2/angelo_physics/fitting.py``), so they agree.
  An export that doesn't apply modifiers writes the mesh without them; when
  that changes the bounds, its log says so. Manual takes **Box Size**,
  **Radius**, **Height** (full, end to
  end) and **Center** (the shape's centre in the object's axes); an empty is
  always Manual. Edit Fitted Size switches to Manual starting from the fit.
* **Collision Only**: the mesh is exported only as its shape (a convex hull or
  mesh shape points at it, no node draws it), like Godot's ``-colonly``.
* ``Scene.angelo_physics_format``: Standard glTF (OMI) or Godot Name Suffixes
  (below).

None has a get, set or update callback, every enum's items are constant and
fixed-numbered (the .blend stores the number), and none can be keyed.

The person
==========

* **Properties > Physics > Game Physics** (closed by default), on the active
  object, and the same settings as a popup from the chat bar's + menu > Game
  engine live link > Game physics for the selected object… The panel shows the
  fitted size, the scene's physics format and the findings about the object
  and everything below it, or a line saying what the export writes.
* The panel never measures in its draw. The evaluated bounds cost nothing; an
  armature-deformed mesh's rest pose is measured once by a timer into a
  per-object cache (the first draw shows the size a moment later). The
  depsgraph-update handler only notes cached objects whose geometry updated; a
  timer then compares the object's data, a digest of its vertices and its
  modifier settings with what was measured, and re-measures only on a real
  change. A pose is not among them, so playback measures nothing, and an
  export's own switching of Armature modifiers changes nothing. A load, undo
  or redo clears the cache.

The agent
=========

No tool of its own: ``property_set`` writes ``angelo_physics.body``, ``mass``,
``shape``, ``fit``, ``axis``, ``size``, ``radius``, ``height``, ``offset`` and
``collision_only`` on an object, through staging, review and one Undo like any
setting. They are Python-defined, so ``rna_rules`` admits them by name
(``GAME_PHYSICS``) because they run no Python, and ``action_properties``
lists them with their items and bounds. The scene's format stays the person's.
``data_read`` reads them back.

What the export writes
======================

``io_scene_gltf2/angelo_physics/export.py`` runs as a user extension that
``ExportGLTF2`` adds to every export, so the engine link (live and save-time),
the web export, File > Export > glTF, Godot's own .blend import through Angelo
and the agent's exports all write it. The glTF add-on owns it, not Angelo's
agent add-on, because only the glTF add-on is enabled in a background Angelo
(Godot's .blend import, the engine link's write-back with
``--factory-startup``).

Godot 4.4 to 4.7.2 is the reference (``modules/gltf/extensions/physics``,
identical in both), and every rule is valid OMI:

* **A body keeps its mesh on a child.** Godot builds the body *instead of* the
  mesh for a glTF node with physics, so a body's mesh (skin, morph weights and
  weight animations with it) moves to an untransformed ``<name>Mesh`` child.
  The body keeps the object's name, transform, animation and children.
* **Shapes sit directly below their body.** Godot attaches a shape only to its
  parent body (``_get_ancestor_collision_object`` checks the parent alone;
  godot-proposals#13419). A shape deeper in a body's hierarchy is written on a
  ``<name>Collider`` (``<name>Trigger``) node directly below the body with the
  composed transform, when nothing on the way is animated. A shape that moves
  inside its body (a mesh on an animated bone) stays on its own node, which is
  correct OMI and which Godot 4.7.2 reads as a separate static body or area;
  the export log and the ``shape_indirect`` finding say so. Godot 4.4.1 turns
  every node below a skeleton joint into a bone, so such a shape is lost there
  (its mesh survives on a ``BoneAttachment3D``). An object's own node
  carries its shape when it draws nothing and the shape sits at its origin.
* **Fitted shapes** are written in the shape node's axes with any scale on the
  way baked into the size (OMI discourages scaled shape nodes); a mesh shape
  can't be resized, so its node keeps the scale. A skinned mesh's shape, a
  primitive or a mesh shape (which points at vertices the file holds in world
  space), is placed where the skin draws the mesh: its first joint times its
  inverse bind matrix, as every glTF reader places it (glTF ignores a skinned
  mesh's own node).
* **A skinned mesh's shape is hoisted** like any other: posed bones deform it
  but don't move its node, so it doesn't move inside its body. Its shape keeps
  the rest pose; Godot's shape doesn't follow the deformation.
* **Triggers**: a Trigger body lists its shape nodes (OMI's compound trigger,
  ``trigger.nodes``); a Trigger with its own shape carries ``trigger.shape``.
* **Motion**: ``static``, ``dynamic`` (with ``mass``) or ``kinematic``.
* **Capsules** are written as Godot reads them: OMI's 2025-01 revision made a
  capsule's ``height`` the distance between its hemisphere centres and split
  ``radius`` into ``radiusTop``/``radiusBottom``, but Godot 4.4 to 4.7.2 (and
  its development branch on 2026-11-02) reads ``radius`` and a full height. The
  export writes ``radius`` and the full ``height``, plus ``radiusTop`` and
  ``radiusBottom`` so a newer reader gets the radius; a newer reader's capsule
  is therefore taller by two radii. Cylinders agree in both revisions.

An export with nothing marked is unchanged: each node costs one lookup of the
object's stored settings, which never creates storage
(``bl_system_properties_get``). Measured with ``bench_game_physics_export.py``
(2,000 meshes, 9 interleaved rounds, the 1.0.8 RC app with this branch's
sources, Blender 5.2.2, Mac 1 under the billed lock): median export 1,096 ms
without the extension, 1,103 ms with it and nothing marked (+0.7%), 1,106 ms
with 200 rigid boxes (+0.9%); each arm's rounds spread over about 6%, so
neither difference is outside the noise. The web export's merge (``web_merge``) leaves
an object with physics, and everything below it, unmerged.

Godot name suffixes
===================

With the scene's format set to Godot Name Suffixes, the export writes no OMI
and names nodes the way Godot's importer reads them
(``ResourceImporterScene::_pre_fix_node``): ``-col`` / ``-convcol`` for a
static Mesh / Convex Hull shape, ``-colonly`` / ``-convcolonly`` for a
collision-only one, ``-rigid`` for a rigid body (Godot makes convex hulls).
A box, sphere, capsule or cylinder becomes a convex hull; a kinematic or
trigger body, and any shape on an empty, have no suffix (Godot reads
``-colonly`` on an empty only from Collada, never glTF: godot#32363). The
export log and the findings say which.

Reading it back
===============

``io_scene_gltf2/angelo_physics/imports.py`` reads ``OMI_physics_body``,
``OMI_physics_shape`` and the older ``OMI_collider`` and flat ``type`` forms
on every glTF import (a file without them costs one list lookup). Each node
stays the object it always was, with its physics in its settings, and the two
rearrangements above fold back so a round trip gives the person their objects
and names: a visual child (unnamed, named like its parent or
``<name>Mesh``, untransformed, unanimated, with nothing else on it) becomes one
mesh object with its parent, and a mesh shape of the parent's own mesh becomes
the parent's shape. A primitive on a node of its own becomes an empty with that
shape (Manual); a mesh shape of a mesh no node draws becomes a collision-only
mesh object, shown as a wireframe and kept out of renders. A capsule with
``radius`` is read as the earlier form (full height), one without as the 2025
form. Velocities, inertia, gravity factor, physics materials and collision
filters have no setting and are not kept.

Findings
========

``angelo_agent/game_physics.py`` checks the settings (``scene_findings``;
pure ``findings`` over plain records), in the shape of the web export's
findings (code, severity, subject, message and fix templates translated where
drawn):

* ``shape_indirect`` (warn): a shape moves inside its body: an object between
  them, or the shape's own object when it has a visible mesh or an offset
  shape, has an animated transform (location, rotation, scale or their deltas)
  or follows a bone of an animated armature. It is exactly what the export
  leaves in place; an armature's animated pose alone moves only its bones.
* ``colonly_empty`` (warn): an empty named with ``-colonly`` or
  ``-convcolonly``, which glTF ignores (godot#32363).
* ``suffix_and_physics`` (warn): a Godot physics suffix and game physics on one
  object, which Godot would build twice.
* ``trimesh_moving`` (warn): a Mesh shape in a rigid or kinematic body;
  ``trimesh_trigger`` (warn): one in a trigger (a mesh has no inside).
* ``no_mesh`` (error): a Convex Hull or Mesh shape on an object without a mesh.
* ``flat_shape`` (warn): a primitive with no thickness.
* ``body_without_shape`` (warn): a body with no shape on it or below it.
* ``scaled_body`` (warn): a rigid body with any scale, or any body scaled
  unevenly (Godot resets a rigid body's scale and can't stretch a shape
  unevenly).
* ``collision_only_without_shape`` (warn): Collision Only without a shape.
* ``not_geometry`` (warn): physics on a light, camera, armature or other object
  that can't carry it.
* ``suffix_unsupported`` (warn) and ``suffix_approximate`` (info): in the
  suffix format, physics no suffix expresses, or a shape one approximates.

A suffix is recognised as Godot's ``_teststr`` does: after a Blender duplicate
number and trailing digits, spaces and underscores, the name ends in
``-<suffix>`` or holds ``$<suffix>``.

Limits and follow-ups
=====================

* Navigation meshes, occluders and animation loop flags: no OMI extension or
  Godot glTF extra imports them cleanly today; Godot's suffixes (``-navmesh``,
  ``-occ``, ``-loop``) remain the way.
* Physics materials (friction, bounciness), collision filters (layers),
  initial velocities and centre of mass: OMI has them and Godot reads them;
  Angelo has no settings for them yet.
* Blender's own Rigid Body settings are not read; a person sets Game Physics
  separately.
* The live preview in Godot is built by Godot's runtime glTF loader, which
  also builds the physics nodes; the editor doesn't simulate them.
* Run with Godot 4.4.1 and 4.7.2 on macOS (Validation); Godot's importer code
  is the same on every platform. On 4.4.1 a shape on an object parented to a
  bone is dropped (above); nothing in OMI attaches a moving shape there.

Validation
==========

* Host: ``test_native_game_physics`` (fitting, axes, transforms, both capsule
  forms, the suffix table, every finding and a clean scene, and that the
  settings run no Python, can't be keyed and match the agent's paths), and
  the shared fit: what each object costs to measure, its signature (a pose is
  not in it), the handler and timer keeping or dropping cache entries, and
  that the export, the panel and Edit Fitted Size all use it.
* ``test_blender_game_physics`` (native, background): the installed
  definitions, ``property_set`` through a staged program with commit, one Undo
  and a refusal, the exported tree (mesh child, shapes directly below bodies, a
  grandchild hoisted with its composed transform, an animated one left in
  place, compound triggers, collision only, the capsule form, baked scale, a
  posed skinned mesh's shape fitted to its rest pose and hoisted, its convex
  hull placed where its skin draws it, a mesh on an animated bone left in place
  and flagged alone), the panel's fitted size and Edit Fitted Size equal to the
  exported shape on both (also with a modifier after the Armature modifier),
  the cache kept through an export and a pose and dropped by a modifier or
  mesh change, nothing written or stored without physics, the import
  fold-back, suffixes, findings and the web merge.
* ``bench_game_physics_panel.py``: what the panel's draw spends on a
  ~130k-vertex skinned mesh (with and without a Subdivision Surface after its
  Armature modifier, in a rigid body) and on a 95-animation character, timed
  the way the draw calls it (a background Angelo has no layout to draw into).
  On Mac 1 (2026-11-04, the 1.0.9 release app with main's physics sources,
  then with this branch's loaded over them; 3 alternating runs of 7 rounds,
  medians of the run medians, microseconds): fitted size 6.0 before (the posed
  size) and 3.8 after (the exported one, cached); findings 28.8 and 31.3 on the
  rig, 127.5 and 162.9 on the character; a pose change with its evaluation
  563 and 626 (the handler and the timer's check). Measuring once in the timer
  costs 7,350 (the mesh's 130k vertices) and 3,740 (re-evaluated without the
  armature). Mac 1 held another session's host-suite lock (load about 3.5),
  so these are not quiet-machine numbers.
* ``test_blender_game_physics_gui`` (window): the panel and the popup drawn
  on a fitted rigid box and on a Mesh shape in a rigid body (its finding
  wrapped to the panel's and the popup's width), the + menu row, and Edit
  Fitted Size.
* ``test_blender_godot_physics_e2e`` (host-Python driver, ``ANGELO_QA_GODOT``
  with one or more Godot editors joined by ``:``, ``ANGELO_QA_PHYSICS_ASSETS``
  a folder of held-out .glb models, ``--timeout`` per Angelo or Godot step):
  every model is marked with each body and shape by one rule and exported, two
  also go to Godot as .blend files that Godot imports through the Angelo under
  test as its Blender; Godot imports the project headless and a GDScript dump
  is checked against facts Angelo doesn't compute: the body class per type with
  its mass, every exported mesh kept, each shape directly below its body, each
  fitted primitive against the bounds Godot measures of its mesh at rest in the
  shape's frame (a skinned mesh where its skin draws it), each convex or mesh
  shape's own points spanning its mesh there, and a separate body for exactly
  the shapes the ``shape_indirect`` finding flagged.
* **Held-out assets**: ``godot_physics_e2e/heldout_assets.json`` holds the
  rule, fixed before any download, and each file's source commit, URL, licence
  file and sha256; ``fetch_heldout_assets.py DEST`` downloads and checks them
  (they are never committed). The rule: every .glb in the model folder of
  Kenney's 3D starter kits on GitHub other than the 3D Platformer (Racing 13,
  City Builder 15, FPS 10, and Basic Scene's Mini Arena 22; CC0 per each kit's
  README or Mini Arena's License.txt) and every .glb of KayKit Character Pack
  Skeletons 1.0 (4 skinned, animated characters with props on bones; CC0 per
  its LICENSE.txt): 64 models, none used while developing. The Kenney kits'
  .glb files point at a shared texture outside the rule, so they import
  untextured; physics doesn't read materials.
* **Run 2026-11-04** on Mac 2 (macOS, Apple M5 Max), headless and niced,
  Angelo Release (Blender 5.2.2) built from main at 7df6ff53458 plus four
  unrelated native changes, with this branch's physics and test sources
  overlaid on a copy: 66 scenes (64 models and 2 .blend files), 436 variants.
  Godot 4.7.2: all 436 pass. Godot 4.4.1 (official build, SHA-512 checked):
  418 pass; the 18 misses are the 6 variants each of the 3 Skeletons with a
  mesh parented to a bone, whose shape 4.4.1 drops (above). The first full
  run, before this branch's fixes, failed 30 variants on each version, all on
  the 5 skinned models, from two product defects (a skinned mesh's shape was
  fitted to the current frame's pose, not the rest pose the file writes: 31 of
  its 36 skinned boxes were off by up to 0.44 m once measured correctly; and
  ``shape_indirect`` flagged skinned meshes the export hoists) and two errors
  of the test itself (it counted the glTF importer's bone-shape helper as a
  model mesh, and measured a skinned mesh at its own node, which glTF ignores).
  The convex and mesh shape check, added later and applied to the files the
  first fix exported, found a third defect: such a shape on a skinned mesh sat off by the
  model's position in the scene (10 variants, all on the 5 skinned models);
  it is now placed through the skin. The numbers above are from the final
  tree, with that check.
* The 1.0.9 record of 15 Kenney 3D Platformer models passing 106 variants is
  not reproducible (no asset list or run survives) and was not verified; that
  kit may have been seen while developing and is excluded above.
