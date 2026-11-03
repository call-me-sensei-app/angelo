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
* **Size**: Fit to Mesh (default) fits the primitive to the object's
  evaluated bounds at every export, the way Blender's own Rigid Body fits a
  shape: a box is the bounds, a sphere's radius half the longest side, a
  capsule or cylinder runs along **Axis** (Longest by default; Z on a tie, then
  Y) with the radius of the larger other side; a capsule is at least its two
  hemispheres. Manual takes **Box Size**, **Radius**, **Height** (full, end to
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
  correct OMI and which Godot reads as a separate static body or area; the
  export log and the ``shape_indirect`` finding say so. An object's own node
  carries its shape when it draws nothing and the shape sits at its origin.
* **Fitted shapes** are written in the shape node's axes with any scale on the
  way baked into the size (OMI discourages scaled shape nodes); a mesh shape
  can't be resized, so its node keeps the scale.
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

* ``shape_indirect`` (warn): a shape moves inside its body (an animated
  object between them, or the shape's own object, when it has a visible mesh
  or an offset shape).
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
* Verified with Godot 4.4.1 and 4.7.2 on macOS; Godot's importer code is the
  same on every platform.

Validation
==========

* Host: ``test_native_game_physics`` (fitting, axes, transforms, both capsule
  forms, the suffix table, every finding and a clean scene, and that the
  settings run no Python, can't be keyed and match the agent's paths).
* ``test_blender_game_physics`` (native, background): the installed
  definitions, ``property_set`` through a staged program with commit, one Undo
  and a refusal, the exported tree (mesh child, shapes directly below bodies, a
  grandchild hoisted with its composed transform, an animated one left in
  place, compound triggers, collision only, the capsule form, baked scale),
  nothing written or stored without physics, the import fold-back, suffixes,
  findings and the web merge.
* ``test_blender_game_physics_gui`` (window): the panel and the popup drawn
  on a fitted rigid box and on a Mesh shape in a rigid body (its finding
  wrapped to the panel's and the popup's width), the + menu row, and Edit
  Fitted Size.
* ``test_blender_godot_physics_e2e`` (host-Python driver, ``ANGELO_QA_GODOT``
  with one or more Godot editors joined by ``:``, ``ANGELO_QA_PHYSICS_ASSETS``
  a folder of held-out .glb models): every model is marked with each body and
  shape by one rule and exported, two also go to Godot as .blend files that
  Godot imports through the Angelo under test as its Blender; Godot imports
  the project headless and a GDScript dump is checked against facts Angelo
  doesn't compute: the body class per type with its mass, every mesh kept,
  each shape directly below its body, each fitted primitive against the bounds
  Godot measures of its mesh in the shape's frame, and a separate body for
  exactly the shapes the ``shape_indirect`` finding flagged. Run with Kenney's
  3D platformer starter kit (15 CC0 models, MIT-licensed project, none used
  while developing): 106 variants pass on Godot 4.4.1 and 4.7.2.
