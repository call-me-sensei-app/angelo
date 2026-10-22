.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Native coverage checklist
*************************

This is the living inventory of what an agent may ask Blender to do and how
Angelo covers it. The goal is native coverage: a closed, typed action inside
``angelo_scene_program`` (staging, exact revisions, host verification and one
Undo step) or another bounded native tool, instead of reviewed Python
(``angelo_execute_python``), which is slower, costs more tokens and is not
transactional (``agent_execution.rst``). Update the affected rows, the counts
and the token figures in the same change that adds, removes or narrows a
capability; ``test_native_coverage_doc.py`` fails when an action kind, modifier
type or check kind is missing here or the counts disagree with the rows.

Status values
=============

* **Native typed**: a closed action kind or bounded native tool covers the
  capability end to end, with exact preconditions and host read-back.
* **Native partial**: a native path covers a stated subset; the rest needs
  reviewed Python.
* **Reviewed Python**: only ``angelo_execute_python`` covers it. The script is
  shown to the user and approved individually; it runs on the live scene, its
  Undo does not cover external effects, and the host cannot verify the result.
* **Not covered**: no supported path in this build. Either the feature is
  compiled out of the validated editor build (``bpy.app.build_options``: no
  Cycles, Bullet, fluid, FFmpeg, OpenVDB, remesh, USD, Alembic or the C++
  OBJ/STL/PLY importers and exporters), or policy excludes it. The full-feature
  build (``native_editor_build.rst``, "Full-feature macOS arm64 build") compiles
  all of those in, but they are still reached only through reviewed Python there:
  this checklist describes native typed coverage, which that build does not extend.

Tests: ``H`` names dependency-free host suites (``test_native_*.py``), ``N``
native Blender suites (``test_blender_*.py``, run by ``run_native_suites.py``).

Evidence
========

The rows are traced from the source: the action registry
(``action_schemas.py``, ``action_catalog.py``), the kernels
(``action_kernels.py`` and its siblings), the commit families
(``program_commit.py``, ``program_identities.py``), the check registry
(``validation_spec.py``) and the
tool list (``tool_specs.py``). The benchmark and evaluation task sets
(``bench_mcp_vs_angelo.py``, ``eval_live_agent.py``, ``eval_agent_tasks.py``)
use only core kinds: their request telemetry carries an empty
``fallback_categories`` map, so it shows no reviewed-Python fallback and does
not by itself rank the gaps. Priorities therefore follow the fallback
categories ``fallback_categories.py`` counts in ordinary use (geometry,
modifiers, materials, geometry nodes, rigging, animation, camera and lighting,
render and world, external I/O, objects) and how often a modeling request
needs each capability; a live evaluation with these tasks would rank them by
measurement.

Coverage metrics
================

Functional coverage is 100% today: reviewed Python can do anything ``bpy``
can. "Native" is about speed, cost and safety, so the honest answer to "how
much is native?" depends on the denominator. Breadth is small (a few percent of
operators and settings); the recorded agent work is 100% native only because
those task sets exercise the core kinds. Breadth figures come from
``native_coverage_metrics.py`` run in this build (Blender 5.2.2, factory
startup, background); the stock Blender 5.2.0 figures come from the same
``bpy`` introspection of a stock build, for reference. "Baseline" columns give
the figures before the native coverage work, when programs had only the
original core kinds.

**1. Operators.** This build registers 2,439 operators in 76 ``bpy.ops``
modules (stock 5.2.0: 2,498 in 77). Classification, listed in the script:

* editor chrome, 587: ``wm``, ``screen``, ``ui``, ``view2d``, ``file``,
  ``text``, ``text_editor``, ``console``, ``info``, ``preferences``,
  ``extensions``, ``buttons``, ``uilist``, ``spreadsheet``, ``gizmogroup``,
  ``workspace``, ``script``, ``ed``, ``asset``, ``brush``, ``paintcurve``,
  ``palette``, all of ``outliner`` and all of ``view3d`` except ten
  scene-changing operators (active camera from view, camera to view, snapping);
* out of scope, 249: ``sequencer`` (111), ``clip`` (91), ``mask`` (40) and
  ``sound`` (7);
* agent-relevant, 1,603: everything else. The largest modules are ``object``
  250, ``node`` 182, ``mesh`` 164, ``grease_pencil`` 122, ``graph`` 67,
  ``anim`` 66, ``uv`` 55, ``paint`` 55, ``curve`` 51, ``pose`` 51,
  ``armature`` 49, ``image`` 48, ``sculpt`` 41, ``nla`` 39, ``action`` 37 and
  ``particle`` 37. Selection, editor-specific and modal operators stay in this
  pool, so it overstates what an agent needs; it is a breadth measure.

Native actions and tools provide the effect of 554 of the 1,603 (34.6%; 47,
2.9%, at the baseline), several only partially: 202 through typed kinds
(``COVERED_OPERATORS`` names each one and its action kind; ``bone_set`` covers
12 of them: bone parenting and its clearing, roll, the pose clears and
rotation mode, and pose and edit bone hiding; ``modifier_add`` covers
``object.particle_system_add``; ``create_object`` makes Grease Pencil objects;
78 of the 220 ``graph``, ``anim``, ``action``, ``nla`` and ``marker``
operators are covered by effect, 66 of them by the animation-editing kinds:
``ANIMATION_OPERATORS`` in the script; ``node_edit`` covers 28 ``node``
operators, so 31 of the module's 182 are covered: delete with and without
reconnect, links made, cut and detached, connect to output, reroutes, frames
(join, attach, detach, parent), duplicates (exact copies, with inputs, linked),
mute, collapse, options, preview, width, colour, Swap Node, make and ungroup
group and interface sockets of nested shader groups; selection, view, viewer,
clipboard, zone, panel, item-list and modal move operators stay uncovered) and
394 through ``operator_run``, 352 of them not covered by a typed kind:
202 + 352 = 554. ``operator_run`` allow-lists 419 C operators; the
installed-RNA rules admit all 419 in this build and the poll of 394
passes on a staged sample of their target type (mesh 116, Grease Pencil 55,
object subset 53, curve 41, armature 32, UV 24, hair curves 18, transform 17,
text 12, constraint 10, lattice 8, pose 8; ``native_coverage_metrics.py``,
``operator_run``). The Edit-mode and data-local families
(``action_operator_families``) account for 104 of them: Grease Pencil strokes
43, hair curves 18, transforms 14 (translate, rotate and resize are the typed
``transform``), text 12, constraints 10 and object operators 7; five more of
those families are denied for their work ("operator_run work bounds"). The
other 25 need state the sample lacks (an active bone collection or layer
group, a second layer, a sculpt mask or face sets, a selected attribute, a
non-basis active shape key, an image editor for some UV selection operators,
breakdown keys or vertex-paint mode for two stroke operators).

**2. Properties.** The denominator is every editable leaf setting (boolean,
integer, float, string, enum) reachable from an ID type through nested non-ID
structs up to three levels deep, without collections: 2,724 settings on Object
(372), Mesh (68), Curve (76), TextCurve (99), Camera (108), Light subtypes (75),
Material (113), World (59), Scene (1,330), Armature (48) and ParticleSettings
(376; ``particle_set`` writes a particle system's own settings). Modifier,
constraint and node settings live in
collections and are counted by type below instead; bone settings are
collection members too and have their own table. The active
camera is an ID pointer, so it is outside this denominator although
``property_set`` writes it; ``world_configure``'s background colour, strength,
environment and rotation are node inputs, also outside it. The allow-list deliberately favours settings agents
ask for (render, colour management, visibility, frame range, text and curve
geometry) over breadth; most Scene settings are tool, editor and add-on
options.

.. list-table:: Editable settings written natively
   :header-rows: 1

   * - Path
     - Baseline
     - Current
   * - Typed action kinds (transform channels, camera, light and World colour)
     - 22 of 2,724 (0.8%)
     - 23 of 2,724 (0.8%)
   * - ``property_set`` allow-list (Object 27, Camera 15, Light 13, Curve 9, TextCurve 17, Scene 69; the
       active camera and six Cycles paths fall outside this build's denominator)
     - 0
     - 150 of 2,724
   * - Either (before the generic paths)
     - 22 of 2,724 (0.8%)
     - 173 of 2,724 (6.4%)
   * - Generic ``property_set`` paths (``rna_rules``: Object 279, Mesh 30, Curve 40, TextCurve 63, Camera 71,
       Light 39, Material 77, World 23, Scene 305, Armature 12), with the typed kinds
     - 22 of 2,724 (0.8%)
     - 942 of 2,724 (34.6%)
   * - ``particle_set`` paths (``rna_rules`` on ParticleSettings)
     - 0 of 376
     - 337 of 376 (89.6%)
   * - Every native path
     - 22 of 2,724 (0.8%)
     - 1,279 of 2,724 (47.0%)

Armature data settings are ``property_set`` ``data.`` paths. Bone settings
live in the armature's bone collections
(Bone and EditBone) and the object's pose (PoseBone). The denominator follows
the same rule (``native_coverage_metrics.bone_metrics``); a struct is not
entered twice, so a PoseBone's 107 paths include the 43 of its Bone (``bone.``),
which ``bone_set`` reaches as Bone settings:

.. list-table:: Bone settings written natively
   :header-rows: 1

   * - Struct
     - Before ``bone_set``
     - Current
   * - Bone (rest settings, ``settings``)
     - 0 of 43
     - 42 of 43: all but ``name``
   * - EditBone (rest geometry and the same settings)
     - 0 of 53
     - 46 of 53: not ``name``, ``length``, ``matrix``, ``lock`` or selection
   * - PoseBone (pose channels and ``pose_settings``; 64 own paths)
     - 0 of 107
     - 54 of 64 own: not ``name``, the matrices, selection or the motion-path display
   * - Total (own paths)
     - 0 of 160
     - 142 of 160 (88.8%); 142 of 203 counting the PoseBone's ``bone.`` paths

**3. Usage-weighted.** Every recorded agent operation of the evaluation task
sets ran natively:

* Recorded transcripts of the default and 40-task long sets contain only
  ``angelo_scene_program`` calls of core kinds (``transform``,
  ``create_primitive``, ``material_principled``, ``modifier_configure``,
  ``modifier_add``, ``geometry_nodes``), ``angelo_apply_recipe`` calls and
  reads, and no reviewed Python.
* The task sets themselves: the default set has five edits and one question,
  the long set 32 edits and eight questions; every edit is native.

This is not a measure of real use: the tasks were written around the core
kinds and the evaluation harness declines reviewed Python. Weighting by real
requests needs the ``fallback_categories`` telemetry of ordinary sessions.

The ``coverage`` task set (``eval_live_tasks.build_coverage_tasks``) adds 26
edits only the native-coverage kinds cover: render resolution and film
transparency, the World background, frame rate and range, a duplicate moved
with the original into a new collection, render visibility, a two-bone rig
posed and configured (``bone_set``: a pose rotation and a Bone setting), a hair
particle system (``modifier_add`` and ``particle_set``, graded by
``particle_values``), points scattered with Distribute Points on Faces
(graded by the named Geometry Nodes modifier and a ``node_graph`` check of its
graph), that graph edited (``node_edit``: a lower density and a Set Position
node inserted on a link), the cube's mesh triangulated with Triangulate Faces
(``operator_run``), the World graph edited (``node_edit``: a Value node
driving the Background strength), a procedural material (``shader_nodes``)
then edited (``node_edit``: a Color Ramp inserted and both nodes in a frame;
``node_graph`` matches nodes by type, so the model's node names do not matter)
and a Grease Pencil object with a second layer
(``create_object``, ``operator_run``; no closed check reads Grease Pencil
layers, so only the object is graded); then one per ``operator_run`` Edit-mode
or data-local family (``eval_operator_tasks``): the cube made 2.5 m tall by moving its top
vertices in its mesh (Edit-mode ``transform.translate``), strokes and hair
curves subdivided once (``curve_counts``), a two-line text (``font.line_break``;
``property_set`` cannot write a line break) and a Copy Location constraint
applied (``constraint.apply``). No native kind draws strokes or adds a
hair-curves object, so both trial drivers make those two objects before their
task (``prepare_fixture``, recorded as ``harness_fixture``); the agent never
makes them. Then seven animation edits (``eval_animation_tasks``,
after the operator tasks so the copy is not animated when its constraint is
applied): linear keys with linear extrapolation (``keyframe_configure``), a
time offset (``keyframe_edit``), a Noise modifier (``fcurve_modifier``), keys
copied onto the copy (``keyframe_copy``), a new Action with the old one
stashed (``action_set``), an NLA track and strip of the cube's Action
(``nla_edit``) and timeline markers (``marker_edit``), graded by
``keyframes``, ``fcurve_modifier``, ``nla`` and ``markers`` checks. Each is
graded by closed host checks on ``$scene``, ``$eval``, ``$copy``, ``$rig``,
``$sketch``, ``$text``, ``$strokes`` or ``$hair`` through the trial driver's
``grading_targets``, on both benchmark sides. A live run (the model provider
bills its requests) measures whether a model finds the deferred kinds (one
lookup round on first use); ``test_blender_coverage_tasks.py`` proves the
grading without a model. The live commands::

   python3 tests/python/angelo_agent/eval_live_agent.py --task-set coverage --trials 5 \
     --connection-id CONNECTION_UUID --blender BINARY --authorize-billed-runs
   python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --task-set coverage ... --authorize-billed-runs

**4. Types.** Allow-listed, not generic: a node, modifier or constraint type
outside these sets falls back to reviewed Python.

.. list-table:: Native types
   :header-rows: 1

   * - Family
     - Baseline
     - Current
   * - Modifiers created (this build and stock: 83 types)
     - 7: ``BEVEL``, ``SUBSURF``, ``SOLIDIFY``, ``MIRROR``, ``BOOLEAN``,
       ``NODES`` (``geometry_nodes``), ``ARMATURE`` (``armature_bind``)
     - 47: 16 typed (also ``ARRAY``, ``DECIMATE``, ``WEIGHTED_NORMAL``, ``TRIANGULATE``,
       ``SHRINKWRAP``, ``DISPLACE``, ``SCREW``, ``CURVE``, ``REMESH``, refused in this build) and 31 generic
       (every mesh type with a bound, ``PARTICLE_SYSTEM`` included). Not created: 10 denied
       (files, caches, particle instances, volumes, fluid, ocean, node groups) and the Grease Pencil types,
       which do not apply to meshes
   * - Modifiers verified (``modifier`` or ``modifier_values`` check)
     - 9
     - 46: every created type but ``NODES``, whose graph is checked instead (a particle system's settings:
       ``particle_values``)
   * - Constraints (29 types)
     - 5
     - 29: 10 typed, 19 generic, all configurable
   * - Shader nodes (100 in this build, 102 stock)
     - 10, allow-listed
     - 95: every type but script, group, IES, point density and line style (``node_edit`` adds the same types,
       frames and reroutes to existing trees)
   * - Geometry nodes (266 in this build, 274 stock)
     - 8, allow-listed, plus group input and output
     - 181: every field and value node, and the geometry nodes with a size rule (Distribute Points on
       Faces by area); the rest (zones, volumes, imports, object and image readers,
       volume and grid point distribution, unbounded generators) are denied

Summary
=======

.. list-table:: Capability rows by status
   :header-rows: 1

   * - Status
     - Baseline
     - Current
   * - Native typed
     - 29
     - 83
   * - Native partial
     - 5
     - 52
   * - Reviewed Python
     - 70
     - 26
   * - Not covered
     - 9
     - 5

Objects
=======

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create mesh primitives (cube, plane, UV and ico sphere, cylinder, cone, grid, circle, torus)
     - Native typed
     - ``create_primitive``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``, ``test_blender_derived_checks``
     - Name, location, rotation and scale are derived checks. The torus uses Blender's Add > Torus defaults (48 x 12 quads). No monkey.
   * - Create empties, text and Bezier curves (line or circle)
     - Native typed
     - ``create_object``
     - H ``test_native_coverage_kinds``; N ``test_blender_object_management``
     - Shape them further with ``property_set``; cameras, lights and armatures have their own kinds.
   * - Create a lattice (cage for the Lattice modifier)
     - Native partial
     - ``create_object`` (``LATTICE``), ``property_set`` (``data.*``)
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - Points per axis at creation; interpolation and other cage settings are generic data paths. Lattices are fingerprinted exactly (cage settings, point positions and selection). ``operator_run`` runs ``lattice.make_regular``, ``lattice.flip`` and the selection operators; moving points is reviewed Python.
   * - Create other object types (metaball, NURBS surface, hair, volume)
     - Reviewed Python
     - none
     -
     -
   * - Set location, rotation and scale (absolute or relative)
     - Native typed
     - ``transform``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``
     - ``SET`` channels are derived checks; ``DELTA`` derives nothing.
   * - Parent and unparent (keep transform)
     - Native typed
     - ``parent_set``
     - H ``test_native_derived_checks``; N ``test_blender_action_audit_fixes``
     - Bone and vertex parenting are not supported.
   * - Rename
     - Native typed
     - ``object_rename``
     - H ``test_native_object_management``, ``test_native_program_names``, ``test_native_program_names_fuzz``; N ``test_blender_object_management``, ``test_blender_program_names``
     - A taken name fails rather than being suffixed; handles survive renames. Names follow program order: one
       an earlier rename or delete of the same program released is free and one a pending rename gives is
       taken, for renames and new objects alike; a name asked for before its holder is renamed away or deleted
       later in the same program is taken at once, and an ambiguous request is refused before staging.
   * - Duplicate (full or linked data)
     - Native typed
     - ``object_duplicate``, ``batch``
     - H ``test_native_object_management``, ``test_native_batch``, ``test_native_typed_review``; N ``test_blender_object_management``, ``test_blender_batch``, ``test_blender_typed_review``
     - Into the source's collections at that point of the program (a copy made after its source moved joins the new ones), with its live materials; animated sources are reviewed Python. ``batch`` applies bounded array or scatter transforms with at most 1,024 generated copies per program. Each generated group has a mandatory ``copies`` check for its count, object type and shared data when linked. One material for every member of a linked group is admitted; a copy a material step named commits only with its root's materials and face indices.
   * - Delete
     - Native typed
     - ``object_delete``
     - H ``test_native_object_management``; N ``test_blender_object_management``
     - Refused while another object, a scene camera or another datablock depends on it.
   * - Join meshes
     - Native partial
     - ``object_join``
     - H ``test_native_join_separate``; N ``test_blender_join_separate``
     - Declared mesh sources (write set) merge in world space, slots mapped by live material, then are deleted. Vertex groups, shape keys, object-linked materials and sources still in use are refused; source modifiers are not applied, as in Blender.
   * - Separate (loose parts, material, selection)
     - Native partial
     - ``mesh_separate``
     - H ``test_native_join_separate``; N ``test_blender_join_separate``
     - Up to 64 parts, named and placed like Blender's, in the target's collections. Animated, vertex-group and shape-key meshes are refused; no derived check (part names are Blender-suffixed).
   * - Apply transforms (bake location, rotation and scale into the mesh)
     - Native partial
     - ``transform_apply``
     - H ``test_native_object_management``; N ``test_blender_object_management``
     - Meshes without children or delta transforms; world geometry is unchanged.
   * - Set origin
     - Native partial
     - ``origin_set``
     - H ``test_native_object_management``; N ``test_blender_object_management``
     - Bounds centre, median or a world point on meshes without children; the 3D cursor is view state.
   * - Apply the visual (constrained) transform or the parent inverse; clear the parent-inverse origin or tracking constraints
     - Native partial
     - ``operator_run`` (``object.visual_transform_apply``, ``parent_inverse_apply``, ``origin_clear``, ``track_clear``)
     - H ``test_native_operator_run_families``; N ``test_blender_operator_run_objects``
     - Object mode on the private copy; commit copies the transforms, the parent inverse and the constraint stack (``transforms``, ``constraints`` families). Parents and constraint targets are declared dependencies; a track clear on a stack with an Armature constraint is refused (``CONSTRAINT_TARGETS``). ``object.modifiers_clear`` is denied (``COVERED``): ``modifier_remove`` removes modifiers by name, and clearing the stack at once would drop Multires, bind, hook, particle or cache data a failed commit cannot rebuild.
   * - Display settings (display type, wire, name, axis, bounds and bounds type, in front, transparency, texture space, object colour, pass index, empty display)
     - Native typed
     - ``property_set``
     - H ``test_native_property_set``; N ``test_blender_property_set``
     - Allow-listed paths only; derived ``property`` check.
   * - Selection and active object
     - Reviewed Python
     - none
     -
     - Programs deliberately never change selection, the active object or mode.

Mesh editing
============

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Extrude and inset faces
     - Native typed
     - ``mesh_extrude``, ``mesh_inset``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``, ``test_blender_native_programs``
     - Explicit indexes (at most 4,096) or a rule-based face selector (normal, material index, face set, area; it matches up to the mesh limit).
   * - Bevel edges (destructive)
     - Native typed
     - ``mesh_bevel``
     - N ``test_blender_action_kernels``
     - Explicit indexes (at most 4,096) or a rule-based edge selector (angle, boundary, sharp; it matches up to the mesh limit).
   * - Merge by distance and recalculate normals
     - Native typed
     - ``mesh_merge_by_distance``, ``mesh_recalculate_normals``
     - H ``test_native_action_audit_fixes``; N ``test_blender_action_kernels``
     - Also the ``clean-mesh`` recipe with exact topology digests.
   * - Subdivide and delete faces
     - Native typed
     - ``mesh_subdivide``, ``mesh_delete``
     - H ``test_native_coverage_kinds``; N ``test_blender_mesh_tools``
     - Explicit indexes or rule-based selectors, with topology steps; non-destructive subdivision is ``SUBSURF``.
   * - Bow a whole mesh: an arched deck or rail, a sagging span, a bench curved in plan (destructive)
     - Native typed
     - ``mesh_bend``
     - H ``test_native_coverage_kinds``, ``test_native_derived_geometry``; N ``test_blender_mesh_bend``,
       ``test_blender_program_commit_effects``
     - Along one object axis (default the longest), after ``segments`` even cuts, every vertex moves along ``up``
       by ``rise * (1 - t * t)``: the ends and the length stay, the middle moves ``rise`` metres at the object's
       own scale, sections stay upright. Its exact schema is in the Codex program reference and in Claude's
       plan, with a note of what its numbers mean (``action_schemas.BEND_NOTE``). No check reads a mesh's
       shape, so a bend is never host-certified.
   * - Bridge, fill, grid fill, dissolve, delete vertices or edges, poke, triangulate, solidify, spin, screw, symmetrize, split, merge and every other allow-listed Edit-mode mesh operator
     - Native partial
     - ``operator_run`` (``mesh.*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - Blender's own operator on the private copy in isolated Edit mode, on all, none or indexed elements; amplifiers (cuts, steps, segments) bounded before running. Loop cut, knife, rip and other interactive, cursor or macro operators are reviewed Python; ``mesh.intersect`` and ``mesh.separate`` are denied.
   * - Move, rotate, scale, mirror, slide, shrink or fatten, push or pull, to-sphere selected elements; creases, bevel weights, skin radii and marks, custom-normal rotation, random offsets
     - Native partial
     - ``operator_run`` (``transform.*``, ``object.skin_*``)
     - H ``test_native_operator_run_families``; N ``test_blender_operator_run_families``, ``test_blender_operator_run_windows``, ``test_blender_program_commit_effects``
     - Explicit values, never modal, on meshes, curves, lattices, edit bones, strokes and hair curves. The pivot is the selection's bounding-box centre or an explicit world point (``center_override``), never the 3D cursor; snapping, auto-merge and attribute correction are pinned off; view and cursor orientations are refused; proportional editing is opt-in. Bend, shear, warp and vertex slide (cursor, view or mouse) are denied; skin roots above 2,048 vertices are refused (``RECURSION_BOUND``).
   * - Shade smooth or flat
     - Native typed
     - ``operator_run`` (``object.shade_smooth``, ``shade_flat``, ``shade_smooth_by_angle``, ``mesh.faces_shade_*``), ``geometry_nodes`` (``SET_SHADE_SMOOTH``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``, ``test_blender_scene_validation``
     - Whole objects or indexed faces; ``bevel-and-shade`` recipe. Shade Auto Smooth appends an asset from disk and stays reviewed Python.
   * - Mark seams and sharp edges
     - Native typed
     - ``operator_run`` (``mesh.mark_seam``, ``mesh.mark_sharp``), ``uv_unwrap`` (``seam_edge_indices``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``, ``test_blender_action_kernels``
     - Indexed edges or those a selection operator chose (``select: KEEP``), or seams as part of an unwrap.
   * - Vertex groups and weights
     - Native partial
     - ``armature_bind``, ``operator_run`` (``object.vertex_group_*``, ``object.vertex_weight_*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``, ``test_blender_action_kernels``
     - Add, remove, assign the selection one weight, normalize, mirror, smooth, clean, quantize, limit, lock, sort and move on meshes; explicit per-vertex weights only for bone groups (``armature_bind``). Lattice weights are reviewed Python.
   * - Shape-key safe topology edits
     - Native partial
     - ``operator_run`` (``mesh.*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - Blender's Edit-mode operators keep the shape keys; the typed topology kernels still refuse shape-key meshes.

Modifiers
=========

``modifier_add`` and ``modifier_configure`` create and configure a modifier
on a staged copy; ``modifier_remove``, ``modifier_move`` and ``modifier_apply``
edit the stack. Commit copies each staged modifier's settings, then removes
the live modifiers the stage removed or applied and orders the stack like the
stage, journaling every removed modifier (type, index, settings and Geometry
Nodes inputs) for rollback. The object's active modifier is journaled too and
set again last, because ``modifiers.new`` activates each modifier it adds,
rollback's rebuilt ones included; the rollback read-back covers that flag. A program can stage an object only when every
existing modifier has a bounded evaluated size (``action_budget.py``): fixed
count Arrays (at most 1,024), Screws, Shrinkwraps, Curves, Decimate and Weld
qualify; fit-to-length or curve Arrays, caps, Remesh, Wireframe, Skin and
other unbounded types are refused, which also blocks every other action on
that object. A particle system passes its mesh through and is bounded by its
own settings (Physics, particles and hair). The bound counts the Subdivision
levels the stage evaluates (viewport; render levels stay capped at 3), grows
``(s + 1)^2 + 4`` times for a Bevel of ``s`` segments (``2 (s + 1)^2 + 4`` with
arc or patch miters; Blender's measured worst cases stay below both), and
estimates a Boolean as both operands plus the smaller one, holding the
evaluated Boolean result to the budget after the stage's update
(``action_budget``). Modifier types beyond Bevel,
Subdivision, Solidify, Mirror and Boolean are named, not specified, inside
Claude's core ``modifier_add`` and ``modifier_configure`` schemas; a lookup of
those kinds returns every type. A setting a call leaves out stays as it is, and a new
modifier keeps Blender's default for it (``agent_execution.rst``, "Settings a
call leaves out").

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - ``BEVEL`` modifier
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_derived_checks``; N ``test_blender_action_kernels``, ``test_blender_derived_checks``
     - Derived ``modifier`` check.
   * - ``SUBSURF`` modifier
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_derived_checks``; N ``test_blender_action_kernels``
     - Needs the OpenSubdiv build feature.
   * - ``SOLIDIFY`` modifier
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_derived_checks``; N ``test_blender_derived_checks``
     -
   * - ``MIRROR`` modifier, including an origin object
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_mirror_object``, ``test_native_mirror_dependencies``;
       N ``test_blender_mirror_object``, ``test_blender_scene_queries``
     - ``settings.mirror_object`` is a bound object handle or earlier alias.
       Omission/null preserves the origin; ``settings.clear: true`` clears it.
       A nonnull origin together with ``clear: true`` is refused.
       An origin used exclusively by Mirror may have its own modifiers when
       it has no rigid body or rigid body constraint. Other nested transform
       dependencies (parents, constraints, drivers) retain their existing
       refusal. Mixed dependency roles keep the ordinary modifier-stack
       guard; the origin's separately observed full revision and commit-time
       fingerprint still bind its modifier settings.
   * - ``BOOLEAN`` modifier with an operand handle
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``, ``test_blender_modifier_stack``
     - Exact, Float or Manifold solver, self and hole-tolerant options; the operand is a declared read dependency. Collection operands are reviewed Python.
   * - ``ARRAY`` modifier (fixed count, relative and constant offset, merge)
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - Up to 64 copies; fit-to-length, curve fitting, caps and offset objects are reviewed Python.
   * - ``DECIMATE``, ``WEIGHTED_NORMAL``, ``TRIANGULATE`` modifiers
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - Derived ``modifier`` checks; vertex-group options are reviewed Python.
   * - ``ARMATURE`` modifier
     - Native partial
     - ``armature_bind``
     - N ``test_blender_action_kernels``
     - Only with explicit weights.
   * - ``SHRINKWRAP`` (mesh target) and ``CURVE`` (curve object) modifiers
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - The target or curve is a declared read dependency.
   * - ``SCREW`` modifier
     - Native typed
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_modifier_stack``
     - Up to 256 steps and 16 iterations; an axis object is reviewed Python.
   * - ``DISPLACE`` modifier
     - Native partial
     - ``modifier_add``, ``modifier_configure``
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - Uniform displacement only; a texture is another datablock and stays reviewed Python.
   * - ``REMESH`` modifier
     - Not covered
     - ``modifier_add`` (``REMESH``, refused here)
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - The kind exists with bounded voxel and octree settings, but this build lacks OpenVDB and the remesh feature, so it is refused as ``REMESH_UNAVAILABLE`` instead of passing geometry through; a build with them runs it.
   * - Every other mesh modifier type (Weld, Wireframe, Skin, Simple Deform, Lattice, Cast, Smooth, Hook, Wave, Cloth...)
     - Native partial
     - ``modifier_add``, ``modifier_configure`` (generic)
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - Settings are RNA paths on the modifier, checked against its definition and read back (``modifier_values`` check); object pointers are declared handles. Deforming, reducing and listed generating types are bounded; file, cache, particle-instance, volume, fluid, ocean and node-group types are denied. ``PARTICLE_SYSTEM``: see Physics, particles and hair.
   * - Apply a modifier
     - Native partial
     - ``modifier_apply``
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - The first modifier of a mesh without shape keys, with Blender's own operator; derived ``modifier_stack`` check. Particle systems are never applied.
   * - Remove or reorder modifiers
     - Native typed
     - ``modifier_remove``, ``modifier_move``
     - H ``test_native_modifier_stack``; N ``test_blender_modifier_stack``
     - Multires, particle systems and bound deform caches are refused; a forced commit failure restores the stack exactly.
   * - Modifier viewport and render toggles
     - Native partial
     - ``modifier_configure`` (generic types: ``show_viewport``, ``show_render``)
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - Typed types' configure preserves the existing toggles.

Materials and shader nodes
==========================

``material_assign`` reuses a material held by a bound source or stated earlier
in the program, and can create an independent named copy with ``copy_as``. It
supports target slot and face selection plus ``also`` targets. Mandatory final
assignment checks retain the intended resource identity; source revisions and
sharing rules still apply.

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create and assign a Principled material (all or listed faces)
     - Native typed
     - ``material_principled``
     - H ``test_native_derived_checks``; N ``test_blender_derived_checks``, ``test_blender_action_kernels``, ``test_blender_survey_fixes``
     - Named materials derive assignment and value checks. Naming a material the target already has edits it in place (same name; refused when other users share it); a ``material_principled`` that states no input (decided from the call, never by comparing values: a stated default is applied) keeps it unchanged, assigning its slot to the faces it names by indices or selector (a name alone that makes a new material makes Blender's default one); stated inputs are set in place on a held material's one Principled BSDF, every other input, node and link kept (agent_execution.rst, "Settings a call leaves out"). ``material-setup`` recipe.
   * - Any other registered shader node by ``bl_idname`` (Voronoi, wave, gradient, color ramp, mix shader, emission...)
     - Native partial
     - ``shader_nodes`` (generic nodes)
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - RNA-checked settings and input defaults, links of compatible socket types (shader outputs only into shader inputs); script, group, IES and point-density nodes are denied; image nodes take loaded images by ID.
   * - Closed procedural shader graphs (value, RGB, texture coordinate, noise, mapping, math, mix, bump)
     - Native typed
     - ``shader_nodes``
     - N ``test_blender_action_kernels``, ``test_blender_action_audit_fixes``
     - No script or group nodes. A named graph derives a ``material_assignment`` check.
   * - Image texture materials (loaded images)
     - Native partial
     - ``shader_nodes`` (``TEX_IMAGE``)
     - N ``test_blender_material_images``
     - The image ID from ``angelo_scene_settings``; the stage holds only the ID and commit assigns the live image, so a staged preview shows the node unfilled. Loading files is reviewed Python.
   * - Edit an existing material's settings (blend and shadow modes, viewport colour, roughness, metallic, culling...)
     - Native partial
     - ``property_set`` (``active_material.*``)
     - H ``test_native_property_set``, ``test_native_material_sharing``; N ``test_blender_property_paths``, ``test_blender_material_sharing``
     - In place, as Blender edits it. A material other objects, meshes, datablocks or a fake user also use is refused unless ``shared: ALL_USERS``, which changes every object of the bound scene that shows it and names them in the approval, the result and the post-commit revisions; users outside the scene are refused, and the users are bound so a change before commit refuses it. A material from a linked library, or a library override, is refused (``LINKED_MATERIAL``): an edit would not be saved with the file. An own copy first (``material_assign`` ``copy_as`` into the active slot) works in the same program.
   * - Edit an existing material's node graph: add, remove (with reconnect), link, unlink, detach, set values and settings, mute, collapse, labels, colours, frames, duplicate, swap, insert and reroute, make group, ungroup, group interface sockets
     - Native partial
     - ``node_edit``
     - H ``test_native_node_edit``; N ``test_blender_node_edit``, ``test_blender_program_commit_effects``
     - On a private copy; a material other objects, meshes or a fake user share is refused unless ``shared: ISOLATE`` gives only this object the edited copy. Nodes by exact name; ``node_graph`` check derived. Materials with image textures stay unbindable (their pixels are not in the revision); animated trees, copies a native rebuild cannot reproduce exactly, and groups of groups are reviewed Python.
   * - Remove or reorder material slots
     - Native typed
     - ``operator_run`` (``object.material_slot_*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - Add, remove, remove unused or all, move, assign to the selection, select and deselect; the active slot index commits with the data. Copying slots to other objects is denied.
   * - Color attributes and vertex colours
     - Reviewed Python
     - none
     -
     -

Geometry nodes
==============

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Closed geometry-node graph (transform, set position, subdivide, extrude, cube, grid, join, shade smooth)
     - Native typed
     - ``geometry_nodes``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``, ``test_blender_scene_catalog_graphs``
     - Evaluated size bounded before evaluation.
   * - Any other registered geometry node (fields, attributes, curves, instances, primitives) by ``bl_idname``
     - Native partial
     - ``geometry_nodes`` (generic nodes)
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - RNA-checked settings and input defaults, links of compatible socket types. A node with a geometry output needs a size rule fed by literal inputs (``node_generic.GEOMETRY_BOUNDS``); zones, volumes, imports, object, collection and image readers, groups and volume or grid point distribution are denied.
   * - Scatter points on a surface (Distribute Points on Faces, random or Poisson disk)
     - Native partial
     - ``geometry_nodes`` (generic node ``GeometryNodeDistributePointsOnFaces``)
     - H ``test_native_particles``; N ``test_blender_particles``
     - Bounded by its literal density times the surface area of its mesh (``node_generic.AREA_BOUNDS``): the base mesh's triangulated area when the Geometry Nodes modifier is first in the stack, a literal Grid or Cube, or through nodes that cannot enlarge a surface; anything else or a linked density is refused before evaluation. ``node_edit`` changes the density under the same rule.
   * - Edit an existing Geometry Nodes graph: add, remove, link, unlink, detach, set inputs and settings, duplicate, swap, insert and reroute
     - Native partial
     - ``node_edit`` (``modifier``)
     - H ``test_native_node_edit``; N ``test_blender_node_edit``, ``test_blender_program_commit_effects``
     - Closed catalog graphs only (at most 16 nodes, 32 links, identifier names, a Geometry-only interface): the edited graph's provenance is regenerated and audited and the stack bounded again before evaluation (a raised density or level over budget is refused). Frames, mutes, groups and interface sockets are outside the contract; a group another modifier shares is refused unless ``shared: ISOLATE``. Graphs built in the Node Editor stay unbindable.
   * - Edit an existing node-group modifier's inputs
     - Reviewed Python
     - none
     -
     - Catalog graphs expose only Geometry; group interface inputs on a modifier need the catalog contract to carry an interface and the modifier's input values in the revision.

UV
==

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Planar, cube and spherical projection
     - Native typed
     - ``uv_project``
     - N ``test_blender_action_kernels``, ``test_blender_native_programs``
     - ``uv-prep`` recipe; ``uv_layer`` check.
   * - Angle-based and conformal unwrap with seams
     - Native typed
     - ``uv_unwrap``
     - H ``test_native_uv_unwrap_bound``; N ``test_blender_action_kernels``
     - Work-bounded: more than 2.5e9 elements x selected elements is refused (``WORK_BOUND``, see "operator_run work bounds"); use ``uv_smart_project`` for a dense mesh.
   * - Smart UV project and pack islands
     - Native typed
     - ``uv_smart_project``, ``uv_pack``
     - H ``test_native_coverage_kinds``; N ``test_blender_uv_tools``
     - Blender's own operators on the selected staged faces; derived ``uv_layer`` check.
   * - Average island scale, align, weld, arrange, seams from islands, cube, cylinder and sphere projection, reset, pin
     - Native partial
     - ``operator_run`` (``uv.*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - With mesh selection as UV selection (sync). Minimize stretch (modal), stitch, copy and paste (clipboard), view and cursor operators stay reviewed Python.

Lights
======

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create point, sun, spot and area lights (with look-at, at a world location or by ``orbit`` in the scene camera's frame)
     - Native typed
     - ``light_create``
     - H ``test_native_camera_light_actions``, ``test_native_camera_orbits``; N ``test_blender_camera_light_programs``
     - ``light_settings`` and ``aim`` checks. An orbit (a rim light: ``azimuth`` pi, behind the point from the camera) is resolved to an exact location before approval (``agent_execution.rst``, "Cameras and lights").
   * - Configure light settings and aim
     - Native typed
     - ``light_configure``
     - H ``test_native_camera_light_actions``; N ``test_blender_camera_light_programs``
     - Shared light data follows the shared-data policy.
   * - Light contributions, temperature, exposure, normalize, soft falloff and shadow settings
     - Native typed
     - ``property_set`` (``data.*`` on light objects)
     - H ``test_native_property_set``; N ``test_blender_property_paths``
     - Commit swaps the light data copy, as ``light_configure`` does.
   * - Light node graphs, IES, light linking
     - Reviewed Python
     - none
     -
     - The catalog lists ``custom_light_node_graphs`` as unsupported.

Cameras
=======

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create and configure perspective and orthographic cameras (with look-at; a new one also by ``orbit`` around a point from the scene camera)
     - Native typed
     - ``camera_create``, ``camera_configure``
     - H ``test_native_camera_light_actions``, ``test_native_camera_orbits``; N ``test_blender_camera_light_programs``
     - ``camera_settings`` and ``aim`` checks.
   * - Set the active scene camera
     - Native typed
     - ``property_set`` (scene ``camera``)
     - H ``test_native_property_set``; N ``test_blender_property_set``
     - Binds the scene's settings revision; the camera (a handle or ``$alias``, bare or as ``{"object": ...}``) is a declared read dependency. Timeline markers can still switch cameras.
   * - Camera display and lens unit (passepartout, limits, mist, sensor, display size, millimetres or field of view)
     - Native typed
     - ``property_set`` (``data.*`` on camera objects)
     - H ``test_native_property_set``; N ``test_blender_property_paths``
     -
   * - Depth of field and lens shift (enable, focus distance, f-stop, blades, aperture ratio and rotation, shift)
     - Native partial
     - ``property_set`` (``data.dof.*``, ``data.shift_*``)
     - H ``test_native_property_set``; N ``test_blender_property_set``
     - The focus object is an ID pointer and stays reviewed Python.
   * - Panoramic and custom cameras
     - Reviewed Python
     - none
     -
     - Panoramic cameras need Cycles, which this build lacks.

World
=====

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Background colour and strength
     - Native typed
     - ``world_configure``
     - H ``test_native_world``; N ``test_blender_world``
     - Default World graph only; a shared World is copied for the bound scene. ``world_settings`` check.
   * - HDRI environment texture (with Z rotation)
     - Native typed
     - ``world_configure`` (``environment``, ``rotation``)
     - H ``test_native_world``; N ``test_blender_world``
     - Images already loaded in the document, by resource ID; no file paths. Loading the file is reviewed Python or a file tool.
   * - Custom World node graphs
     - Native partial
     - ``node_edit`` (``world``)
     - H ``test_native_node_edit``; N ``test_blender_node_edit``, ``test_blender_program_commit_effects``
     - Any World graph on a private copy (images kept by reference, a shared World refused unless ``shared: ISOLATE``); not in a program with ``world_configure`` or ``world.*`` settings. ``angelo_scene_settings`` lists the graph's node names and links (``world_graph``). Nested groups inside the World (and ungroup) are reviewed Python: their contents are not in the scene revision. ``world_configure`` still refuses custom graphs (``CUSTOM_WORLD_GRAPH``).
   * - Mist and other World settings
     - Native typed
     - ``property_set`` (scene ``world.*``)
     - H ``test_native_property_set``; N ``test_blender_property_paths``, ``test_blender_world``
     - Written in place on the bound scene's World when only that scene uses it (a private copy without its graph is staged). A World other scenes or a fake user also use gets an edited copy, whole graph included, for the bound scene only, reported in ``isolated_shared_data``; the others keep the original. The scene settings revision covers the World exactly.

Render settings and rendering
=============================

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Render engine, resolution, percentage, pixel aspect, samples, film transparency, motion blur, simplify, border, persistent data, compositing and sequencer, Freestyle, dither
     - Native typed
     - ``property_set`` (scene ``render.*``, ``eevee.*``)
     - H ``test_native_property_set``; N ``test_blender_property_set``, ``test_blender_property_paths``
     - Engines this build has (EEVEE, Workbench). Every path is checked against the installed RNA.
   * - EEVEE quality (volumetrics, fast GI, ray tracing method, shadow rays and steps, clamping, overscan, motion blur steps)
     - Native typed
     - ``property_set`` (scene ``eevee.*``)
     - H ``test_native_property_set``; N ``test_blender_property_paths``
     -
   * - Cycles samples, adaptive sampling, denoising and bounces
     - Native partial
     - ``property_set`` (scene ``cycles.*``)
     - H ``test_native_property_set``; N ``test_blender_property_paths``
     - Allow-listed and RNA-checked at write time; this build lacks Cycles, so ``angelo_scene_settings`` lists them as unavailable.
   * - Colour management (display device, view transform, look, exposure, gamma)
     - Native typed
     - ``property_set`` (scene ``view_settings.*``, ``display_settings.*``)
     - H ``test_native_property_set``; N ``test_blender_property_set``
     - Items checked by RNA against this build's OpenColorIO configuration.
   * - Output path and file format
     - Native partial
     - ``property_set`` (``render.image_settings.*``)
     - H ``test_native_property_set``
     - Format, colour mode and depth are typed; the output path is a file path and is refused (``FILE_PATH``).
   * - Viewport images for visual judgment
     - Native typed
     - ``angelo_view`` (tool)
     - H ``test_native_view_observation``, ``test_native_view_capture``; N ``test_blender_view_observation``
     - Solid, wireframe or material preview pixels; ``LIT`` draws with the scene's own lights and world. Viewport data, not a render.
   * - See the scene as it renders (camera, lights, world, volumes, compositing)
     - Native typed
     - ``angelo_view`` (tool: ``RENDERED``, ``region``, ``image`` and its ``:<pass>``, ``frame``, ``file``)
     - H ``test_native_view_render``, ``_bounds``, ``_region``, ``_delivery``, ``test_native_view_picture``, ``test_native_view_finished``, ``test_native_view_job_output``, ``test_native_view_wait``, ``_delivery``, ``_recovery``, ``test_native_view_passes``, ``test_native_view_exr``; N ``test_blender_exr_codecs``, ``test_blender_rendered_view``, ``_pixels``, ``_safety``, ``test_blender_job_output_views``, ``test_blender_view_wait``, ``test_blender_view_sources``
     - A bounded render of the scene camera by the scene's engine, whole or a ``region`` at full detail, or a finished picture: a job's still, one frame of its animation, the Render Result, or with ``file`` one output of a job that published several (a stereo view, a File Output item, a UDIM tile, a baked map; a map of data as its stored values). Render-job views wait within a shared request allowance; ``LAST_JOB`` names the latest render and remains fixed while waiting. One picture a call; one pass of a multilayer render with ``<image>:<pass>`` (``view_sources.rst``); a look renders the scene camera at the current frame only.
   * - See textures, UV layouts, vertex weights and colour attributes
     - Native typed
     - ``angelo_view`` (tool: ``image`` ``IMAGE:<name>``, ``UV``; ``ATTRIBUTE``, ``WEIGHT``, ``group``)
     - H ``test_native_view_sources``, ``test_native_view_weights``; N ``test_blender_view_sources``
     - An Image's pixels as Blender holds them, painted and baked pixels included, never loading a file; one mesh's active UV map over its texture; each mesh's active colour attribute; the targets' weights of a vertex group drawn at any view camera (``view_sources.rst``).
   * - See what the person sees (the window or one editor)
     - Native typed
     - ``angelo_view`` (tool: ``image`` ``EDITOR``, ``EDITOR:<type>``)
     - H ``test_native_view_sources``; N ``test_blender_view_sources``
     - Blender's own screenshot of the bound window, or the largest editor of a type in any window, with text editors, consoles, Info, Preferences, the file browser and the status bar greyed out (``view_sources.rst``).
   * - See motion over a few frames
     - Native typed
     - ``angelo_view`` (tool: ``frames``)
     - H ``test_native_view_motion``; N ``test_blender_view_sources``
     - One labelled strip of a viewport look at 2 to 8 frames with a held camera (or the scene camera at each frame); the frame is put back, frame-change handlers run as when scrubbing (``view_sources.rst``).
   * - Render a still or animation to files
     - Native typed
     - ``angelo_job_start`` (tool: ``RENDER_STILL``, ``RENDER_ANIMATION``)
     - H ``test_native_long_jobs``, ``test_native_long_jobs_ui``, ``test_native_job_render_outputs``; N ``test_blender_long_jobs``, ``test_blender_job_card``, ``test_blender_production_renders``
     - A background job (``long_jobs.rst``): progress, Cancel and a time budget; new files in the chat's workspace, whole frames kept on cancel; the job's resolution and samples are restored. Stereo and multi-view scenes and compositor File Output nodes are rendered too: every view and item is listed on the approval, written inside the job's output and identified in its receipt, and the scene's own paths are restored. Undo does not cover files.
   * - Cycles rendering
     - Native partial
     - ``angelo_job_start`` (tool)
     - N ``test_blender_long_jobs``
     - Compiled out of the development build. In the full build, renders and texture bakes run as background jobs; Cycles settings are ``property_set`` paths.
   * - Bake textures (Cycles)
     - Native partial
     - ``angelo_job_start`` (tool: ``BAKE_TEXTURE``)
     - H ``test_native_long_jobs``; N ``test_blender_long_jobs``, ``test_blender_production_bakes``, ``test_blender_udim_bakes``, ``test_blender_job_restore``
     - Full build only. Onto a target from its own surface or from ``sources`` (high to low, with an optional cage, extrusion and ray distance), into a named image (``image``), a new one (``resolution``) or each material's active Image Texture node; the person's selection and the materials are left as they were; a cancel puts the images back up to 4096 x 4096 pixels in all. UDIM images are baked into the tiles the target's faces lie in, each tile identified in the receipt, and put back from their saved tiles on cancel. Displacement needs Blender's Multires baker (reviewed Python).
   * - Video encoding
     - Not covered
     - none
     -
     - FFmpeg is compiled out of the development build (the full build has it); image sequences remain possible from Python.

Collections
===========

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create collections; link, unlink and move objects
     - Native typed
     - ``collection_link``
     - H ``test_native_object_management``; N ``test_blender_object_management``
     - By exact name in the bound scene, a local collection only (a library collection of the same name is another ID and cannot hold the object); an object always keeps one collection. ``collection_membership`` check.
   * - Collection visibility and exclusion
     - Native typed
     - ``visibility_set``
     - H ``test_native_visibility``; N ``test_blender_visibility``
     - The scene handle plus a collection name: the bound view layer's exclude and eye, and the collection's own viewport, render and select flags. The scene settings revision covers them; ``visibility`` check.
   * - Collection instances
     - Reviewed Python
     - none
     -
     - Programs refuse collection-instance objects as inputs.

Visibility
==========

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Object viewport, render and selectability (``hide_viewport``, ``hide_render``, ``hide_select``)
     - Native typed
     - ``property_set``
     - H ``test_native_property_set``; N ``test_blender_property_set``
     -
   * - View-layer hiding (the eye icon)
     - Native typed
     - ``visibility_set``
     - H ``test_native_visibility``; N ``test_blender_visibility``
     - Applied to the bound view layer at commit (a hidden object is also deselected, as in Blender); an object the program leaves with no included collection (exclusion is each collection's own flag, an edit sets the descendants; already there, moved or created there, or its collection excluded later) is refused while staging (``VALIDATION``); the program's exclusion edits apply in program order. ``visibility`` check.
   * - Local view
     - Reviewed Python
     - none
     -
     - Editor view state, not scene data.
   * - Holdout, shadow catcher and light-probe visibility (volume, sphere, plane)
     - Native typed
     - ``property_set``
     - H ``test_native_property_set``; N ``test_blender_property_paths``
     -
   * - Ray visibility (camera, shadow, diffuse, glossy, transmission, volume)
     - Native typed
     - ``property_set`` (``visible_*``)
     - H ``test_native_property_set``; N ``test_blender_property_set``
     -

Curves and text
===============

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create Bezier, NURBS and path curves
     - Native partial
     - ``create_object`` (``CURVE``), ``curve_points``
     - H ``test_native_curves``; N ``test_blender_object_management``, ``test_blender_curves``
     - A Bezier line or circle, then one poly, Bezier or NURBS spline through up to 256 explicit points (cyclic, auto or vector handles); ``curve_points`` derives a ``curve_counts`` check of that many points on one spline and their local ``positions`` (Z 0 when it makes the curve 2D). Several splines, tilt, radius and per-point handles are reviewed Python.
   * - Text objects (create, body, size, spacing, alignment, extrude, bevel; line breaks, case, bold, italic, underline, small caps, kerning and text boxes in Edit mode)
     - Native partial
     - ``create_object`` (``TEXT``), ``property_set`` (``data.*``), ``operator_run`` (``font.*``)
     - N ``test_blender_property_set``, ``test_blender_object_management``, ``test_blender_operator_run_families``
     - ``property_set`` writes a single-line body; ``font.*`` operators insert and delete text and line breaks and style characters, on the whole body (``select: ALL``) or at its end (``NONE``): the text cursor is not observable, so moving it is denied. ``curve_counts`` checks characters and lines. Fonts and the clipboard are reviewed Python.
   * - Curve geometry settings (dimensions, extrude, bevel depth, resolution, offset, fill)
     - Native typed
     - ``property_set`` (``data.*`` on curve objects)
     - H ``test_native_property_set``
     - Commit swaps the curve data copy (shared-data policy). Bevel and taper objects fail closed.
   * - Convert curve or text to mesh
     - Native partial
     - ``object_convert``
     - H ``test_native_curves``; N ``test_blender_curves``
     - A new mesh object placed and linked like its source, which is kept or deleted (then the result keeps its name unless named); bounded before evaluation. Curves with modifiers, bevel or taper objects are refused.
   * - Edit curve points (subdivide, extrude, duplicate, split, spline type, handle types, cyclic, radius, tilt, weight, smooth, delete, switch direction, curve primitives)
     - Native partial
     - ``operator_run`` (``curve.*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - On all, none or the kept selection in isolated Edit mode; commit swaps the curve data, which the revision covers exactly. Draw, pen, separate and pick operators are denied; deleting every point leaves data the host refuses (``UNREADABLE_RESULT``).
   * - Convert mesh to curve, or between other types
     - Reviewed Python
     - none
     -
     -
   * - Edit hair curves (subdivide, extrude, duplicate, delete, split, curve type, handles, cyclic, switch direction, tilt, selection, Bezier or circle curves)
     - Native partial
     - ``operator_run`` (``curves.*``)
     - H ``test_native_operator_run_families``; N ``test_blender_operator_run_families``
     - Isolated Edit mode on the private copy; the revision hashes the hair data's complete RNA (attributes, offsets, surface). A curve-type change is rated at three times the points and a result above the 100,000-point budget is refused (``GROWTH_BOUND``). ``curves.attribute_set`` is denied (``UNBOUNDED_GROWTH``): the active attribute can be the curves' resolution, which Blender clamps only below. Creating hair curves objects, surface attachment, sculpting and objects with hair modifiers (no native evaluation bound) are reviewed Python; ``curve_counts`` checks points and curves.

Armatures, constraints, drivers and shape keys
==============================================

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create an armature from explicit bones
     - Native typed
     - ``armature_create``
     - H ``test_native_action_audit_fixes``; N ``test_blender_action_kernels``
     - Float32-canonical endpoints; ``rig`` check.
   * - Edit armature bones (subdivide, extrude, duplicate, symmetrize, fill, split, dissolve, delete, roll, align, parent, switch direction, bone collections)
     - Native partial
     - ``operator_run`` (``armature.*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - Edit mode on the private armature with the staged bones as context; commit swaps the armature data and Blender rebuilds the pose. Flip and auto-side names are denied (bone renames rewrite animation paths across the file); the revision covers bone collections and head and tail selection.
   * - Bind a mesh with explicit weights
     - Native typed
     - ``armature_bind``
     - N ``test_blender_action_kernels``
     - ``armature_binding`` check.
   * - Automatic or envelope weights
     - Reviewed Python
     - none
     -
     - The catalog lists ``automatic_weights`` as unsupported.
   * - Object constraints (copy location, rotation and scale, track to, limit location)
     - Native typed
     - ``constraint_add``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``, ``test_blender_kept_settings``
     - A new constraint keeps Blender's defaults for every setting the call leaves out.
   * - Damped track, child of (with set inverse), copy transforms, limit rotation and scale
     - Native typed
     - ``constraint_add``
     - H ``test_native_coverage_kinds``; N ``test_blender_constraints``
     - Targets are declared dependencies; camera and light targets keep exact revisions.
   * - Every other constraint type (IK, follow path, stretch to, transformation, pivot, shrinkwrap...), and configuring any existing constraint
     - Native partial
     - ``constraint_add`` (generic), ``constraint_configure``
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - RNA-checked settings read back (``constraint_values`` check); every object it points at is a declared dependency without a cycle. Armature constraint target lists stay reviewed Python.
   * - Copy, delete, reorder and apply object constraints; set or clear a Child Of inverse; reset Stretch To and Limit Distance
     - Native partial
     - ``operator_run`` (``constraint.*``, ``properties.constraint`` names one)
     - H ``test_native_operator_run_families``; N ``test_blender_operator_run_objects``, ``test_blender_program_commit_effects``
     - Object mode on the private copy; commit gives the live object the staged stack by name (``constraints`` family, journaled and rebuilt on rollback), before typed constraint kinds append. Bone constraints, copies to other objects, path animation and motion-tracking inverses are refused, and so is any constraint operator on a stack with an Armature constraint (``CONSTRAINT_TARGETS``): commit and its rollback rebuild constraints from RNA settings, which hold no target list, so a forced rollback dropped the targets of an untouched Armature constraint.
   * - Edit an existing armature's bones (head, tail, roll, parent, connect)
     - Native typed
     - ``bone_set``
     - H ``test_native_bones``; N ``test_blender_bones``
     - Staged Edit mode on the private armature copy; X-axis mirror editing does not apply; connected neighbours move as in Blender and are reported. ``bone`` check.
   * - Pose bones (location, rotation in any rotation mode, scale)
     - Native typed
     - ``bone_set``
     - H ``test_native_bones``; N ``test_blender_bones``
     - Rotation is XYZ Euler, kept in the bone's own mode; commit copies the pose (``pose`` family). Animated channels are refused; keying a pose stays reviewed Python.
   * - Clear or flip pose transforms, set rotation modes, apply the visual (constrained) transform
     - Native partial
     - ``operator_run`` (``pose.loc_clear``, ``rot_clear``, ``scale_clear``, ``transforms_clear``, ``user_transforms_clear``, ``rotation_mode_set``, ``quaternions_flip``, ``visual_transform_apply``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - Pose mode on the private armature, on all, none or the kept pose selection; commit copies every bone's pose (``pose`` family) and not the pose selection. Animated or driven poses are refused (``ANIMATED_POSE``); motion paths, the pose clipboard, Apply Pose as Rest, bone constraints and pose selection and visibility operators are not admitted.
   * - Bone and pose bone settings (deform, envelopes, inheritance, B-Bones, IK limits, locks, custom shapes, colours)
     - Native typed
     - ``bone_set`` (generic ``settings``, ``pose_settings``)
     - H ``test_native_bones``; N ``test_blender_bones``
     - RNA paths by ``rna_rules`` plus the bone rules (no selection, no other bone); a custom shape is a declared dependency.
   * - Renaming bones (one by one, flip or auto-side names)
     - Reviewed Python
     - none
     -
     - Adding, deleting, mirroring and subdividing bones and bone collections are the ``armature`` operators above. ``armature.flip_names`` and ``autoside_names`` are denied: a bone rename rewrites animation paths across the whole file.
   * - Bone constraints
     - Reviewed Python
     - none
     -
     - ``pose.constraint_add``, ``ik_add`` and the constraint clears and copies are denied in ``operator_run``: bone constraints are not part of the pose commit family.
   * - Drivers
     - Reviewed Python
     - none
     -
     - Scripted expressions are arbitrary Python. Expression-free types (average, sum, min or max of declared transform channels) could be made safe, but staging refuses every driver before evaluation and revisions treat drivers as unsupported; admitting even those needs a reviewed change to both guards.
   * - Shape keys (add, set value, keyframe the value)
     - Native partial
     - ``shape_key_add``, ``shape_key_value``
     - H ``test_native_animation_edits``; N ``test_blender_animation_edits``
     - ``operator_run`` adds remove, clear, copy, mirror, move, lock, retime and apply to basis (removing the last key, which deletes the Key datablock, is refused). Shaping a key's vertices is reviewed Python; typed topology kinds still refuse shape-key meshes.
   * - Rigify rigs
     - Reviewed Python
     - none
     -
     -

Animation
=========

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Keyframe location, rotation or scale with interpolation
     - Native typed
     - ``keyframe_insert``
     - H ``test_native_action_catalog``; N ``test_blender_action_kernels``
     - ``simple-animation`` recipe (location and rotation); ``animation`` check. A scale key is bounded as a transform's scale (1,000), a location or rotation key at 100,000. Animation goes through the Action's channelbag.
   * - Keyframe other properties (object, curve, text and camera settings)
     - Native partial
     - ``property_keyframe``
     - H ``test_native_animation_edits``; N ``test_blender_animation_edits``
     - Numeric and boolean ``property_set`` paths of an object or its data, set and keyed at one frame; data keys share the object's Action (another slot), as in Blender. Material and light settings are not in the allow-list; shape-key values are keyed by ``shape_key_value``. ``keyframes`` check.
   * - Delete keyframes (at frames, in a range, or whole channels)
     - Native typed
     - ``keyframe_delete``, ``keyframe_edit`` (``DELETE``)
     - H ``test_native_anim_edits``; N ``test_blender_anim_keys``
     - Any channel of the object's slot (``data.<path>`` for its data's); a channel left without keys or modifiers is removed, as Delete Keyframes does. ``keyframes`` check.
   * - Interpolation, easing, handles and key type of keys; extrapolation and mute of channels
     - Native typed
     - ``keyframe_configure``
     - H ``test_native_anim_edits``; N ``test_blender_anim_keys``
     - Any channel or every channel, keys at frames, in a range or all; a named frame without a key fails. Cyclic extrapolation is an F-Curve ``CYCLES`` modifier.
   * - Insert, move, scale, mirror and snap keys in time and value
     - Native typed
     - ``keyframe_edit`` (``INSERT``, ``OFFSET``, ``SCALE``, ``SNAP``)
     - H ``test_native_anim_edits``; N ``test_blender_anim_keys``
     - Moved keys replace the keys they land on, as a transform does; a negative factor mirrors; INSERT keeps the curve's value (or takes one) and the split segment's interpolation.
   * - Clean, decimate, smooth, bake and Euler-filter channels
     - Native partial
     - ``keyframe_edit`` (``CLEAN``, ``DECIMATE``, ``SMOOTH``, ``BAKE``, ``EULER_FILTER``)
     - H ``test_native_anim_edits``; N ``test_blender_anim_keys``
     - Clean, Smooth and the Euler filter are Blender's rules (``anim_filters``); BAKE is ``FCurve.bake`` over a range; DECIMATE simplifies in value space, not Blender's Bezier fit. The Graph Editor's slider operators (blend, ease, push/pull, breakdown, shear, Gaussian and Butterworth smoothing) stay reviewed Python.
   * - F-Curve modifiers (Generator, Function, Cycles, Noise, Limits, Stepped, Smooth)
     - Native partial
     - ``fcurve_modifier``
     - H ``test_native_anim_edits``; N ``test_blender_anim_keys``
     - Add, configure (settings by RNA path) and remove by name; per-frame cost bounded (Noise octaves, Smooth width, Generator order). Envelope control points, modifier copy/paste and NLA strip modifiers stay reviewed Python. ``fcurve_modifier`` check.
   * - Copy and paste keys between objects, or duplicate keys in time
     - Native partial
     - ``keyframe_copy``
     - H ``test_native_anim_edits``; N ``test_blender_anim_keys``
     - An explicit source object (declared, read only), channels and frame range, merged or replacing; object channels only (data channels and flipped pose paste stay reviewed Python).
   * - Actions: new, assign, single-user copy, unlink, rename, stash, slots
     - Native typed
     - ``action_set``
     - H ``test_native_anim_edits``; N ``test_blender_anim_structure``
     - The object's own Action; existing Actions by their ``dat:`` ID. An Action left unused is refused unless stashed. Replacing an Action for every user, separating or merging slots, and the data's own Action stay reviewed Python. ``nla`` check (``action``).
   * - Scene frame range, preview range, current frame and frame rate
     - Native typed
     - ``property_set`` (``frame_start``, ``frame_end``, ``frame_step``, ``use_preview_range``, ``frame_preview_*``, ``frame_current``, ``render.fps``, ``render.fps_base``)
     - H ``test_native_property_set``, ``test_native_typed_review``; N ``test_blender_property_set``, ``test_blender_typed_review``
     - A ``frame_current`` write is refused beside camera-bound markers and beside an unkeyed write of a value an F-curve or driver holds; other writes of animated objects are admitted.
   * - NLA push down
     - Native typed
     - ``nla_push_down``
     - H ``test_native_animation_edits``; N ``test_blender_animation_edits``
     - Commit appends the staged track and strip; ``nla`` check.
   * - NLA tracks and action strips: add, remove, rename, reorder, mute, solo, lock; strip timing, blending, extrapolation, repeat, scale, influence
     - Native partial
     - ``nla_edit``
     - H ``test_native_anim_edits``; N ``test_blender_anim_structure``
     - Tracks and strips by unique name; commit rebuilds the live stack. Meta, transition and sound strips, tweak mode, animated influence or time, strip modifiers, split, swap and apply scale stay reviewed Python. ``nla`` check (tracks, a strip's settings).
   * - Timeline markers and camera switching
     - Native typed
     - ``marker_edit``
     - H ``test_native_anim_edits``; N ``test_blender_anim_structure``
     - The bound scene's markers by unique name: add, rename, move, remove, bind a declared camera (or none); markers join the scene settings revision. Pose markers and markers of other scenes stay reviewed Python. ``markers`` check.
   * - Editing an Action shared with other objects, NLA strips or a fake user, for every user
     - Reviewed Python
     - none
     -
     - Refused natively (``ACTION_SHARED``); ``action_set`` ``SINGLE_USER`` gives the object its own copy explicitly.
   * - Other Graph and Dope Sheet tools (sound to samples, samples and keys conversion, ghost curves, handle equalizing, channel groups, keying sets)
     - Reviewed Python
     - none
     -
     -

Physics, particles and hair
===========================

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Scene gravity and audio volume
     - Native typed
     - ``property_set`` (``use_gravity``, ``gravity``, ``audio_volume``)
     - H ``test_native_property_set``; N ``test_blender_property_paths``
     -
   * - Rigid bodies
     - Not covered
     - none
     -
     - Bullet is compiled out of the development build.
   * - Fluid and smoke
     - Native partial
     - ``angelo_job_start`` ``BAKE_SIMULATION`` with a fluid domain target (the bake, as a background job)
     - H ``test_native_job_fluid_plan``; N ``test_blender_fluid_jobs``, ``test_blender_mantaflow``,
       ``test_blender_release_features``
     - The bake only (``long_jobs.rst``, "Fluid bakes"); domain, flow and effector setup stays reviewed Python
       (``FLUID`` is a denied modifier). Fluid is compiled out of the development build.
   * - Cloth, soft body and collision
     - Native partial
     - ``modifier_add``, ``modifier_configure`` (generic, nested ``settings.*`` paths)
     - H ``test_native_generic_settings``; N ``test_blender_generic_settings``
     - Settings only; point caches (disk caching, file paths) are denied. Baking is a background job (``BAKE_PHYSICS``, ``long_jobs.rst``) that leaves baked caches as they are. Simulated results depend on the frame.
   * - Force fields (wind, vortex, force, turbulence... strength, falloff, shape)
     - Native partial
     - ``property_set`` (``field.*``), ``operator_run`` (``object.forcefield_toggle``)
     - H ``test_native_generic_settings``, ``test_native_operator_run``; N ``test_blender_generic_settings``, ``test_blender_operator_run``
     - Generic settings paths; force fields are fingerprinted by their settings unless they use a texture or another datablock. The toggle runs only on objects that already have force-field settings: no RNA path creates them on the live object at commit.
   * - Particle systems and particle hair (emitter and hair, No or Newtonian physics, children, path, halo and object rendering)
     - Native partial
     - ``modifier_add`` (``PARTICLE_SYSTEM``), ``particle_set``, ``modifier_configure`` (``particle_system.*``)
     - H ``test_native_particles``; N ``test_blender_particles``, ``test_blender_program_commit_effects``
     - ParticleSettings by RNA path on a private staged copy (``particle_values`` check); per-system settings are modifier paths. Bounded by count, children, path steps, substeps and instanced size before evaluation. Shared settings, combed hair, baked or disk caches, keyed, boids and fluid physics, particle force fields, collections, textures, removal, apply, join and separate are reviewed Python.
   * - Simulation baking
     - Native partial
     - ``angelo_job_start`` (tool: ``BAKE_SIMULATION``)
     - H ``test_native_long_jobs``; N ``test_blender_long_jobs``, ``test_blender_disk_simulation``
     - One target's geometry-nodes simulations as a background job: packed into the file, or with ``output`` cached on disk inside the job's folder, which the scene then reads by a path from the .blend (a saved file; survives reopening and moving the project). Staged programs still list ``simulation`` as unsupported.

Grease Pencil
=============

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Create a Grease Pencil object; add, remove, move, duplicate, merge, hide, reveal, lock, isolate, mask and group layers
     - Native partial
     - ``create_object`` (``GREASEPENCIL``), ``operator_run`` (``grease_pencil.layer_*``)
     - H ``test_native_operator_run``; N ``test_blender_operator_run``
     - A blank object with one active layer and a frame at the current frame; layer operators run in Object mode on the private copy and commit swaps the data. Grease Pencil data is fingerprinted by its complete exact RNA (layers, frames, drawings) and its materials by their stroke and fill settings (an image keeps the revision inexact); objects with Grease Pencil modifiers are refused before evaluation. Copying layers to other objects is denied.
   * - Edit strokes (delete, dissolve, smooth, simplify, subdivide, extrude, duplicate, split, join, cyclic, caps, corners, thickness, opacity, material, curve and handle types, reorder, move to layer, frames, selection)
     - Native partial
     - ``operator_run`` (``grease_pencil`` stroke operators)
     - H ``test_native_operator_run_families``; N ``test_blender_operator_run_families``, ``test_blender_operator_run_windows``
     - Isolated Edit mode on the private copy, on the drawings of the current frame; commit swaps the data. Extrusion is bounded by its work (``WORK_BOUND``), a curve-type change is rated at three times the points, and a result above the 100,000-point budget is refused (``GROWTH_BOUND``). Simplify runs in its FIXED mode only (resampling: ``UNBOUNDED_GROWTH``; adaptive: ``SUPERLINEAR``; merge: ``UPSTREAM_DEFECT``) and closing a stroke adds no points (density matching: ``UNBOUNDED_GROWTH``). Similar selection, curve-type resampling (``convert_curve_type``), merge by distance and operators that write shared Grease Pencil materials, read the view or the 3D cursor, separate or copy to other objects are denied; ``curve_counts`` checks points and strokes.
   * - Draw new strokes, edit Grease Pencil materials
     - Reviewed Python
     - none
     -
     - Drawing and the stroke primitives are interactive; material operators write datablocks other objects share.
   * - Grease Pencil modifiers and Line Art
     - Reviewed Python
     - none
     -
     -

Import and export
=================

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Import a ready Media Studio GLB into the scene
     - Native typed
     - ``angelo_studio_import_3d`` (tool)
     - H ``test_native_studio``; N ``test_blender_studio``
     - Separate approval; ``generative_studio.rst``.
   * - Export a Media Studio output to the file workspace
     - Native typed
     - ``angelo_studio_export`` (tool)
     - H ``test_native_studio_export_checkpoints``; N ``test_blender_studio_export_checkpoints``
     -
   * - Export the scene or selection (glTF, FBX)
     - Reviewed Python
     - none
     -
     - The Python glTF and FBX add-ons are bundled; the catalog lists ``export`` as unsupported.
   * - Import arbitrary files from disk
     - Reviewed Python
     - none
     -
     - The catalog lists ``external_assets`` as unsupported.
   * - OBJ, STL and PLY import and export
     - Not covered
     - none
     -
     - The C++ importers and exporters are compiled out of the development build.
   * - USD and Alembic
     - Native partial
     - ``angelo_job_start`` (tool: ``EXPORT_USD``, ``EXPORT_ALEMBIC``)
     - H ``test_native_long_jobs``; N ``test_blender_long_jobs``
     - Compiled out of the development build. In the full build, export runs as a background job to a new workspace file; import stays reviewed Python.
   * - Append or link from another .blend
     - Reviewed Python
     - none
     -
     -
   * - Summarize the open file: datablock counts with unused data, linked libraries, external files (packed, present, missing) and unresolved references
     - Native typed
     - ``angelo_blend_file`` (tool)
     - H ``test_native_blend_file``; N ``test_blender_read_tools``
     - Read-only, no approval; deferred. Files are named relative to the workspace or by base name, never by an absolute path (``scene_intelligence.rst``, "The file as a file").
   * - List the datablocks of another .blend in the file workspace without opening it
     - Native typed
     - ``angelo_blend_file`` (tool, ``file``)
     - H ``test_native_blend_file``, ``test_native_blend_file_pending``; N ``test_blender_read_tools``, ``test_blender_metadata_scan``
     - Names and counts through bounded descriptor-only scans of plain, gzip and Zstandard files on a cancellable worker. No linking lifecycle or live Main access. Exact native ordering and deep pages are preserved. User counts and missing files need the file open; code execution against another file is out of scope.

Sculpting
=========

See ``native_sculpting.rst``.

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Native brush passes (Draw, Clay, Inflate, Smooth, Crease, Pinch, Grab, Flatten, Mask, Clay Strips, Draw Sharp, Scrape, Elastic Deform, Pose)
     - Native typed
     - ``angelo_sculpt_batch``, ``angelo_sculpt_program`` (tools)
     - H ``test_native_sculpt``; N ``test_blender_sculpt``, ``test_blender_sculpt_workflow``
     - One dispatch and one Undo per pass or program.
   * - Prepare sculpt mode and add Multires levels
     - Native typed
     - ``angelo_prepare_sculpt``, ``angelo_sculpt_resolution`` (tools)
     - H ``test_native_sculpt_workflow``; N ``test_blender_sculpt_workflow``
     - Never remeshes or applies modifiers.
   * - Sculpt views, surface targeting, landmarks and regions
     - Native typed
     - ``angelo_sculpt_view``, ``angelo_sculpt_target``, ``angelo_sculpt_region``, ``angelo_sculpt_review`` (tools)
     - H ``test_native_sculpt``; N ``test_blender_sculpt_regressions``
     -
   * - Voxel remesh and dynamic topology
     - Not covered
     - none
     -
     - Voxel remesh needs OpenVDB, which this build lacks; dynamic topology is outside the sculpt contract.
   * - Apply or reshape Multires, face-set and mask utilities
     - Reviewed Python
     - none
     -
     -

Scene and view-layer settings
=============================

.. list-table::
   :header-rows: 1
   :widths: 24 12 18 22 24

   * - Capability
     - Status
     - Action kinds
     - Tests
     - Notes
   * - Units (system, scale, length)
     - Native typed
     - ``property_set`` (``unit_settings.*``)
     - H ``test_native_property_set``
     -
   * - Read the scene's settings and settings revision
     - Native typed
     - ``angelo_scene_settings`` (tool)
     - H ``test_native_property_set``; N ``test_blender_property_set``
     - Deferred in a Claude plan.
   * - Read live values, property names, enum choices and the socket or modifier-input identifiers of existing data, add-on settings (Cycles) included
     - Native typed
     - ``angelo_read_data`` (tool)
     - H ``test_native_data_path``, ``test_native_read_tools``, ``test_native_read_access``, ``test_native_read_tools_delivery``, ``test_native_read_tools_recovery``; N ``test_blender_read_tools``, ``test_blender_read_access``
     - One bounded data path a call, read-only, no approval. Const vertex weights, exact cached image metadata and noncreating stored/default add-on settings retain traversal. Python computed getters are not run; their values have explicit provenance, and computed access needs an approved script. File paths, scripts, driver expressions, socket text, interface data, other scenes and per-element attribute values are withheld (``scene_intelligence.rst``, "Live data paths").
   * - Read the sockets of a node type that no node in the file has
     - Reviewed Python
     - none
     -
     - Sockets are read from a node; a type without one needs a script that makes a scratch node.
   * - Current frame
     - Reviewed Python
     - none
     -
     - A program fails closed when the frame changes during staging.
   * - Create, rename or switch scenes
     - Reviewed Python
     - none
     -
     -
   * - View layers and render passes
     - Reviewed Python
     - none
     -
     -

Verification surfaces
=====================

Every native action is verified by host checks (``local_verification_and_recipes.rst``).
The check kinds, all available to programs and to ``angelo_scene_verify``:
``dimensions``, ``transform``, ``object_count``, ``object_type``,
``object_name``, ``finite_coordinates``, ``mesh_counts``, ``mesh_degenerate``,
``mesh_manifold``, ``material_assignment``, ``material_values``, ``uv_layer``,
``smooth_shading_graph``, ``parent``, ``rig``, ``armature_binding``,
``animation``, ``camera_settings``, ``light_settings``, ``aim``,
``object_exists``, ``collection_membership``, ``keyframes`` (any channel path,
key type and extrapolation), ``shape_key``, ``nla`` (also the
active Action's name, track names present or absent and one strip's settings),
``fcurve_modifier`` (a channel's named F-Curve modifier and its settings, or its
absence), ``markers`` (the scene's markers by name, frame and bound camera, or
absent), ``bone`` (one bone's rest geometry, stored pose and settings),
``curve_counts`` (points and curves of Grease Pencil drawings at the current
frame, hair curves, curves and lattices; characters and lines of a text; with
``positions``, a curve's only spline's local points),
``particle_values`` (a particle system's ParticleSettings by RNA path),
``node_graph`` (one material, Geometry Nodes or World tree's nodes, absent
nodes, links and unlinked inputs, by name or type),
``property`` (allow-listed settings of an object, its data or the scene),
``world_settings``, ``modifier_stack`` (names at exact indexes, names absent)
and ``modifier`` (``BEVEL``, ``SUBSURF``, ``SOLIDIFY``, ``ARRAY``, ``MIRROR``,
``DECIMATE``, ``WEIGHTED_NORMAL``, ``TRIANGULATE``, ``ARMATURE``,
``SHRINKWRAP``, ``DISPLACE``, ``SCREW``, ``CURVE``).

Prompt and schema cost
======================

New action kinds are deferred: Claude's always-sent plan schema names a
deferred kind in one generic variant and the model reads its exact schema with
``angelo_agent_capabilities`` (``agent_integration.rst``, "Deferred plan
schemas"). Codex chat threads read the core kinds and checks from the program
reference in their instructions (``codex_reference``, which names every
deferred kind) and declare program items by kind only; voice threads'
``tool_specs.dynamic_tools`` defer the same kinds and checks in their schemas
(``plan_schema.codex_arguments``) and say how to look one up
(``providers.CODEX_DEFERRED_GUIDANCE``). A call the host rejects returns the
exact schemas of the deferred items it used (``exact_schemas``), so Codex pays
one lookup call the first time a session uses a deferred kind.

The current figures count every tool, including the team library family's
eight deferred names (205 bytes; a request without a team sign-in leaves them
out, ``team_library.rst``, "Prompt bytes") and the two background-job tools
(``long_jobs.rst``, "Model-visible bytes"). String length limits are not in the
Claude plan schema; the host enforces them.

Bytes use the encoding of ``test_native_plan_schema.py`` (compact JSON) and
are measured with ``coverage_prefix.measure()`` (``coverage_prefix.py`` prints
them); ``test_native_coverage_doc.py`` fails when the current column disagrees
with the code. Token estimates use 1.93 bytes per token (``performance.rst``,
"Token diet"). ``coverage_prefix.py --fit`` instead fits
``token_accounting.py``'s tokenizer approximation to a recorded run; that
approximation is Claude's tokenizer, so Codex figures from it are relative
estimates.

.. list-table:: Always-sent prefix, bytes (estimated tokens)
   :header-rows: 1

   * - Part
     - Baseline
     - Current
   * - Claude plan schema (core plus deferred names)
     - 39,519 (about 20,480)
     - 56,092 (about 29,063)
   * - Claude appended instructions
     - 8,875
     - 15,606
   * - Codex dynamic tools
     - 134,781 (about 69,840)
     - 102,841 (about 53,285)
   * - Codex instructions
     - 13,077
     - 15,680
   * - Action kinds: core, deferred
     - 7, 16
     - 7, 57

**What a deferred kind costs.** A deferred kind or check adds only its name to
the always-sent schemas (about 15 bytes on each side for one name, for example
``operator_run`` or ``curve_counts``) and to Codex chat threads' program
reference, and no instruction text; its exact schema is sent only when a
lookup asks for it. Modifier types beyond Bevel, Subdivision, Solidify, Mirror
and Boolean are named inside the core modifier kinds with one shared generic
variant of about 250 bytes, kept in the core because modifiers are frequent.
New modifier and node types (``PARTICLE_SYSTEM``, Distribute Points on Faces)
add nothing: the generic modifier type pattern and node ``bl_idname`` cover
them. ``operator_run``'s families are allow-list entries, and the allow-list is
in no schema; a refusal names its reason and, for an unknown property, the
operator's settable properties.

**Keeping exact schemas small.** Values are typed by RNA rather than listed:
``property_set``'s exact schema is an open map of 891 bytes instead of the
9,435 bytes a list of its 150 allow-listed paths would take; a type's writable
settings come from ``angelo_blender_api`` domain ``WRITABLE`` only when asked,
and an ``angelo_scene_settings`` read lists the 76 common scene values.
``bone_set`` (Bone and PoseBone settings) and ``particle_set`` are open maps
typed by RNA too. The ``property`` check advertises only its shape (plus the
active-camera path, so short identifiers resolve it) and the host validates its
values against ``property_set``'s allow-list, so that 5 KB list is never sent
twice. Operation-rich kinds use a flat schema: one ``operation``, ``mode`` or
``op`` enum and a per-operation field table (``EDIT_FIELDS``, ``NLA_FIELDS``,
``node_edit``'s ``FIELDS``). The host validates each step against its
operation's closed variant and refuses a field another operation owns, so
accepted steps and their canonical form are those of the ``oneOf`` variants the
flat schemas replace (8.8 KB for ``keyframe_edit``, 6.1 KB for ``nla_edit``,
6.6 KB for ``node_edit``), and the complete internal catalog stays under its
150 KB bound.

Lookup sizes, measured with ``coverage_prefix.py`` when each was added: kinds
``property_set`` 891 bytes, ``bone_set`` 2,200, ``particle_set`` 1,009,
``operator_run`` 2,179, ``keyframe_configure`` 1,385, ``keyframe_edit`` 2,366,
``fcurve_modifier`` 1,473, ``keyframe_copy`` 932, ``action_set`` 724,
``nla_edit`` 2,584, ``marker_edit`` 687 and ``node_edit`` 4,355
(``create_object``'s Grease Pencil variant adds 812); checks
``particle_values`` 500, ``fcurve_modifier`` 785, ``markers`` 635, ``nla``
1,832, ``keyframes`` 1,103, ``node_graph`` 2,670 and ``curve_counts`` 545.

Animation work bounds
=====================

Every key operation reads and writes at most 8,000 keys
(``anim_channels.MAX_KEYS``) over at most 1,024 channels, checked before
anything is written (BAKE and INSERT count the keys they add). The bound sits
under the object observation budget (``scene_queries.MAX_METADATA_ITEMS``,
12,000 items, about one per key: above it no exact revision exists and no
program can bind the object) and the ``keyframes`` check's 8,192 keys, so an
edited object stays bindable and checkable. Curves are rewritten whole from
key tables (``foreach_get``/``foreach_set``; removing keys one by one is
quadratic in Blender) and moved keys are merged with a sorted search, so every
operation is linear in its keys except DECIMATE (n log n, a heap). F-Curve
modifiers bound their per-frame cost (Noise ``depth`` 16, Smooth
``filter_width`` 64, Generator ``poly_order`` 16, 8 modifiers per channel); NLA
edits admit 64 tracks and 256 strips, markers 1,024.

Measured on the development machine (Apple silicon, ``test_blender_anim_keys``
``timings``), one channel of n and 4n keys:

.. list-table::
   :header-rows: 1

   * - Operation
     - Kernel at 2,000 keys
     - Kernel at 7,900 keys
     - Whole program at 7,900 keys
   * - OFFSET, SCALE
     - 2 ms
     - 7 ms
     - 1.6 s
   * - CLEAN, SMOOTH
     - 3-4 ms
     - 11-14 ms
     - 1.6 s
   * - DECIMATE
     - 5 ms
     - 20 ms
     - 1.6 s
   * - INSERT (63 frames)
     - 4 ms
     - 47 ms
     - 1.9 s

A ``transform`` program on the same 7,900-key object takes 1.6 s (2,000 keys:
0.41 s): the program's binding, exact fingerprints, staging and verification
read every key of the object's Actions, so they, not the key kernels, set the
cost of editing a densely keyed object natively (with any kind).

operator_run work bounds
========================

Rule: an admitted operator whose time grows faster than linearly with its
input gets a bound checked before it runs, or is denied. Measure a new
operator on a grid, a random wire tree and loose points at n and 4n elements,
everything selected; a ratio near 16 is quadratic. Bones: armature operators
are bounded by their estimated bones (``bone_estimate``) within the 1,024-bone
evaluation budget, because unique bone names are probed against every bone
(1,024 to 2,048 bones: 14.5 s by subdivide, 8.7 s by duplicate; 32 cuts on a
1,024-bone rig would have run for hours before the evaluation bound refused
the result). Mesh and UV operators: ``WORK_BOUNDS`` bounds elements times
selected elements (V+E+F each, after the selection pass) at the work the worst
measured input takes about 2 s for. Measured on the development machine (Apple
silicon), worst input at each bound, everything selected:

.. list-table::
   :header-rows: 1

   * - Operator
     - Bound
     - Worst input at the bound
   * - ``mesh.select_edge_loop_multi``
     - 4e7
     - wire tree, 6,323 elements: 2.3 s (grid 0.01 s)
   * - ``mesh.bridge_edge_loops``
     - 9e8
     - wire tree, 30,000: 2.4 s
   * - ``mesh.edge_face_add``
     - 1.5e9
     - loose points, 38,729: 2.0 s; wire tree 0.9 s
   * - ``mesh.fill``
     - 2e9
     - wire tree, 44,719: 2.2 s
   * - ``mesh.fill_holes``
     - 7e9
     - wire tree, 83,665: 2.5 s
   * - ``mesh.delete_edgeloop``, ``dissolve_edges``, ``dissolve_mode``, ``dissolve_verts``
     - 1.5e10
     - grid, 121,801: 2.1-2.4 s
   * - ``mesh.select_similar``
     - 1.5e10
     - loose points, 122,474: 2.1 s
   * - ``uv.unwrap``
     - 2.5e10
     - grid, 157,609: 2.3 s
   * - ``mesh.vertices_smooth_laplacian``
     - 4e10
     - grid, 199,809: 2.1 s
   * - ``mesh.select_edge_ring_multi``
     - 5e10
     - grid, 223,729: 1.9 s
   * - ``mesh.unsubdivide``
     - 1e11
     - grid, 316,969: 2.8 s
   * - ``mesh.edge_rotate``
     - 1.5e11
     - grid, 388,129: 2.6 s
   * - ``uv_unwrap`` (the typed action, ``UNWRAP_BOUND`` in ``action_mesh.py``)
     - 2.5e9
     - quad grid, 48,841: 1.65 s (``CONFORMAL`` 1.55 s); triangle grid, 48,961: 1.7 s (``CONFORMAL`` 0.7 s); one island

**The typed uv_unwrap action** (``benchmark_blender_uv_unwrap_work.py``, which
runs locally without a model: a quad or triangle grid with no seams, so one
island, everything selected; an empty ``face_indices`` selects all faces and
explicit indices stop at 4,096). Without a bound the action froze Blender for
25 s on a 198,000-element quad grid and 45 s at the native budget (393,000
elements; ``CONFORMAL`` 9 s and 40 s), and 4,900 to 19,900 vertices took 0.97
to 2.9 s. ``_uv_unwrap`` counts the mesh's V+E+F and those of the faces it will
unwrap with the vertices and edges they touch, and refuses
``elements x selected > 2.5e9`` as ``WORK_BOUND`` before it creates a map or
touches a selection (0.02 s to 0.2 s to refuse a native-budget mesh). The bound is about 2 s for the worst
measured input. It also refuses 4,096 faces (17,000 elements touched) of a mesh
above about 147,000 elements, which took 2.4 s at 396,000; 1,024 faces ran in
2.6 s and are allowed. The error names the fallback, ``uv_smart_project``,
which took 0.17 s at 48,841 elements, 0.9 s at 198,000 and 2.4 s at 396,000.
Not bounded: ``uv_pack`` and ``uv_smart_project`` themselves. The
``operator_run`` ``uv.unwrap`` bound above (2.5e10) comes from the operator
measurements, not from this one: in the same build that operator took 2.5 s on a 78,961-element grid, so an
input at that bound (157,609 elements) probably takes well over 2 s.

Without the bounds the same operators took, at the native element budget, up
to about 70 s (``edge_face_add`` on a 100,000-vertex wire tree, extrapolated
from 0.47 s at 16,000) and about 30 minutes (``select_edge_loop_multi``, 50.6 s
at 16,000 vertices). Other admitted operators stayed under about 1 s at the
budget on these inputs; inputs not measured here may still be slower.

**Edit-mode and data-local families** (``benchmark_blender_operator_run_work.py``:
every operator of these families through the kernel at n and 4n, everything
selected; meshes as grid, wire tree and loose points at 20,000 and 80,000
vertices, strokes and hair curves of ten
points at 20,000 and 80,000 points, text at 1,000 and 4,000 characters,
16 and 64 constraints, 256 and 1,024 bones, 5,000 and 20,000 curve points;
same machine, other benchmarks running, so compare ratios):

.. list-table::
   :header-rows: 1

   * - Operator
     - Measured
     - Bound
   * - ``grease_pencil.extrude``
     - Two-point strokes: 0.16 s at 20,000 points, 1.8 s at 80,000 (ratio 11); ten-point strokes 1.2 s;
       1,000-point strokes 0.23 s
     - ``WORK_BOUNDS`` 7e9 stroke points x selected points (``_selected_stroke_points``, every drawing)
   * - ``object.skin_root_mark``
     - Recurses once per connected vertex (``skin_root_clear``): a 5,000-vertex chain ran, a 10,000-vertex chain
       overflowed the main thread's stack and crashed Blender
     - ``RECURSION_BOUNDS`` 2,048 vertices (``RECURSION_BOUND``)
   * - Proportional editing (``transform.*``), plain or connected
     - Ratio 5-8 with half, corner, striped or 1% random selections; at the 99,000-vertex budget at most 0.8 s
       (grid, connected, half selected), hair 80,000 points 1.7 s, curves 20,000 points 0.8 s, all linear in
       points for non-mesh data
     - None beyond the element budget (falloff by KD-tree or a mesh walk)
   * - Every other operator of these families
     - Ratio 2-5: transforms 0.02-0.22 s at 80,000 vertices; strokes 0.05 s and hair 0.07 s at 80,000 points;
       text 0.7 s at 4,000 characters (mostly Blender's own text Edit-mode enter and exit); constraints and object
       operators under
       0.1 s
     - None beyond the element budget

Hair-curve digests and revisions skip the RNA views of packed attributes
(``points``, ``curves``, ``position_data``) and derived normals, as
``_rna_values`` skips a mesh's element collections: walking them visits every
point three times (3.2 s per 80,000-point operator, and the revision budget is
exceeded near 20,000 points), while the attributes and curve offsets it still
hashes hold the same values (0.07 s).

**Options and long strokes.** The benchmark above runs default properties
on ten-point strokes. A sweep of every point-count-changing stroke and hair
operator over each enum value and Boolean, on Poly, Bezier, NURBS and
Catmull-Rom strokes, and of every one on a single 20,000- and 80,000-point
stroke, measured the rows below. Each is denied (by name, or by value in
``REFUSED_VALUES``) or rated rather than bounded, since a bound needs new
kernel code and none has measured demand:

.. list-table::
   :header-rows: 1

   * - Operator or option
     - Measured
     - Outcome
   * - ``grease_pencil.stroke_simplify`` ``mode=SAMPLE``
     - Resamples at ``length`` (minimum 0): 16 points became 300,004 at 1e-5 (about 10⁹ at 1e-9)
     - Refused by value (``UNBOUNDED_GROWTH``)
   * - ``stroke_simplify`` ``mode=ADAPTIVE``
     - Ramer-Douglas-Peucker on one zigzag stroke: 0.18, 0.47, 2.0 and 8.3 s at 5,000, 10,000, 20,000 and
       40,000 points (quadratic)
     - Refused by value (``SUPERLINEAR``)
   * - ``stroke_simplify`` ``mode=MERGE``, ``grease_pencil.stroke_merge_by_distance``
     - ``curves_merge_by_distance`` copies per-evaluated-point lengths into a per-point array: Blender asserts
       (``copy_from`` size mismatch) on NURBS and Catmull-Rom strokes and merges by wrong distances
     - Refused by value and denied (``UPSTREAM_DEFECT``); FIXED stays
   * - ``grease_pencil.cyclical_set`` ``subdivide_cyclic_segment`` (default on)
     - Adds the stroke's density times the closing segment: x1.75 to x2.05 here; for NURBS the evaluated
       length can fall to the closing distance, so the cuts have no bound
     - Defaults off; on is refused (``UNBOUNDED_GROWTH``)
   * - ``grease_pencil.convert_curve_type`` to POLY or CATMULL_ROM
     - Resamples at the stroke resolution: x10.3 (NURBS) and x11.5 (Catmull-Rom) at the default 12, up to
       10,000
     - Denied (``UNBOUNDED_GROWTH``)
   * - ``grease_pencil.select_similar``
     - Every unselected point against every distinct selected value (three times with Bezier handles):
       0.03, 0.09 and 0.57 s at 8,000, 16,000 and 32,000 points, half selected
     - Denied (``SUPERLINEAR``)
   * - ``grease_pencil.set_curve_type``, ``curves.curve_type_set``
     - Bezier or Catmull-Rom with handles, or kept as NURBS: x3
     - Rated 3
   * - ``curves.attribute_set``
     - Writes the active attribute; ``resolution`` is clamped only below (``curve_type`` up to one past the
       last type): resolution 1e9 on eight Catmull-Rom hair points crashed Blender's evaluation (offsets overflow)
     - Denied (``UNBOUNDED_GROWTH``)
   * - ``object.modifiers_clear``
     - Removes every modifier; rollback rebuilds them from RNA settings, losing Multires, bind, hook,
       particle and simulation-cache data
     - Denied (``COVERED`` by ``modifier_remove``)

After every operator, curve, Grease Pencil and hair-curve objects must hold at
most 100,000 points plus splines, layers or curves (``GROWTH_BOUND``), which
backs every rating. Every other stroke and hair operator stays under 30 ms on
the 80,000-point stroke.

Other ``operator_run`` safeguards:

* Constraint operators and ``object.track_clear`` refuse a stack with an
  Armature constraint (``CONSTRAINT_TARGETS``): commit and rollback rebuild
  constraints from RNA settings, which hold no target list.
* The stage scene pins Auto Keying off when it is created; the artist's live
  settings are untouched.
* ``operator_run`` transforms wait while an artist's modal tool runs
  (``operation_waits``) instead of running; commits are not deferred ("Known
  limitations").
* Derived checks that an ``operator_run`` step could invalidate are dropped
  (``derived_checks``) rather than narrowed: a dropped check never passes
  wrongly, and narrowing them would tie derived checks to the kernel's family
  table.
* Grease Pencil and hair objects with modifiers are refused before evaluation:
  no evaluation bound exists for them.

Grading contract
================

A run of a harness task is **passed only when a check read its result**
(``eval_grading.py``). Every other run is ``failed``, ``inconclusive``,
``skipped`` or ``ungraded``, and every total the harness prints states all
five over every run, so ungraded and inconclusive work is never counted as
passed and never leaves the denominator. A run is ``inconclusive`` when its
checks ran and every one it did not pass is one the grader could not decide
(``scenario_checks.is_inconclusive``: a colour or count the grader cannot
read, or the closed verifier's own ``inconclusive`` status); a measured fail
beside it, a grading error, a stopped turn or an unmarked failure is
``failed``. A record stamped ``failed`` by a harness that had no
``inconclusive`` verdict reads as ``inconclusive`` when its own host check says
only undecided checks failed; nothing reads as passed that was not stamped so. Planned runs that never
ran (a trial that stopped early) are stated beside the total as
``not_run``. The reason: an unchanged scene says
nothing about a reply, so a question graded on it alone passes on an empty
reply or on "I cannot see the scene", and a model that never received what it
needed to look at goes unnoticed. A tool being available, or a request
completing, shows nothing about the result.

Questions (``eval_answers.py``, ``eval_answer_forms.py``, ``survey_truth.py``,
``survey_fixtures.py``):

* The prompt asks for one last line in a fixed form (``ANSWER: yes``,
  ``COUNTS: 12 20 10``, ``OBJECTS: ...``, ``RENDER: EEVEE 1600x900``,
  ``SHORTCUT: Ctrl+B``; for the gate sets ``MESHES: ...`` and ``DIMENSIONS: x
  y z; LOCATION: x y z``). Only that line is read, so the check does not
  depend on phrasing or on the reply's language; a translated yes or no is
  accepted for the 20 other languages the app ships. The value has to state the
  answer: ``ANSWER: no idea`` is no answer.
* A form is not stricter than it needs to be. Where a reply plainly gives the
  answer and wraps it, it is read, and the run records ``parsed:
  "leniently"`` with the liberty taken and ``stated``, the line as written;
  every total of answers states how many were read that way (``answers``; a
  column of the survey report; ``answer_forms_report.py RUN_DIR`` prints each
  line and a total per form). The liberties: quotes or the prompt's own
  brackets around the answer; other words before the label on its line, or
  text after the line; a short clause after a yes or a no (``no, it is cut
  off``); three counts with count labels, read by their names when all three are
  named; a count before or after the names when it agrees with their number,
  and "and" between them; engine, resolution and pixel labels around one size; words around
  a shortcut, and the same keys for the other platform named second
  (``Cmd+B (Ctrl+B on Windows)``); metre units or punctuation among six numbers,
  decimal commas, the location on its own line. Numeric context notes are
  bounded: ``(after 2 extrusions)`` for counts, ``(unchanged since step 3)``
  for dimensions/location, and ``(100%)`` or ``at 100%`` for a render scale.
  Arbitrary numeric prose, other units, negative mesh counts, and extra or
  alternative resolutions are not discarded to extract a matching answer.
  These liberties are grading contract 4; they change neither the gate-set
  version nor its prompts and digests, and a recorded receipt keeps the
  contract and result it was graded under. Totals and measured-result pooling refuse different grading contracts
  (including unrecorded ones) unless ``--mixed-versions`` is explicit, and
  then identify the difference. Benchmark reports also state the contracts.
* What could hold a second answer is never dropped, so such a line is wrong
  or no answer: a remark after the names that is not their count
  (``... (and Suzanne)``), a count that disagrees with them, a second
  shortcut that is not the first one's other-platform form, a second
  location, a clause after a yes or a no that itself holds a yes or a no
  (``no, actually yes``, and so also ``yes, no part is cut off``), asks, or
  refuses (``no, I cannot see it``). Without a separator the first word is
  not the answer (``no idea``, ``no part of it is cut off``). The refusals
  recognised are English; in another language such a clause is read as its
  first word, which the camera question's evidence and its two variants
  still guard.
* The harness computes the ground truth from the scene before it sends the
  prompt and compares: an object's bounding box projected through the active
  camera, mesh counts, the scene's object names, the render engine and
  resolution, the keymap's entry for a tool. A fixture first moves the truth
  off the factory default, where a guess would pass (an extra named object, a
  changed resolution), and is undone after grading.
* The camera question runs twice, once with the object clearly inside the
  frame and once cut by it (the harness sets the active camera's lens and
  shift, never the camera object). Both runs fail when both get the same
  answer, and one variant cannot pass while the other was not graded.
* A visual question also needs an observation that could support the answer:
  a completed ``angelo_view`` through the scene camera, captured before the
  answer line was last stated. The driver samples the chat on every tick for
  this, because a chat row's position is not when its text arrived (the Claude
  runtime keeps one reply row per turn and rewrites it each round); an answer
  stated first and only repeated after looking is not supported. The record
  states whether an image was captured at all (``image_captured``) and how
  many before the answer.
* The picture must have reached the model, by the task's receipt, not by the
  model's words: its ``path`` section must list a scene-camera view
  (``view:<shading>:SCENE_CAMERA``) delivered in a provider turn the provider
  accepted (``eval_answers.image_delivery``; ``delivery`` in the record).
  None delivered fails; delivery the runtime cannot prove (Codex sends tool
  pictures without proof that the model received the pixels, so its receipts
  list them as ``unconfirmed_images``) or a receipt without the section is
  ``unverified``.
* An answering reply that says the model had no image ("I can't see the
  image", "the render didn't load", "no picture was returned";
  ``eval_answers.no_image_claim``, recorded as ``no_image_claim``) fails
  whatever its answer line: the answer was a guess.
* Outcomes: ``right`` passes; ``empty``, ``no_answer`` (a refusal has no answer
  line), ``wrong``, ``unsupported`` (right, but nothing it could rest on, a
  reply that says it saw no image, or no picture delivered) and ``constant``
  fail. ``unverified`` and a question whose truth the harness could not
  establish are ``ungraded``, never passed.

Edits:

* ``checks``: the add-on's closed kinds.
* ``result_checks`` (``survey_checks.py``): what a closed kind cannot read. A
  material's transmission, a colour attribute on the highest vertices, a closed
  surface's genus (a torus, a through hole confirmed by rays), the growth an
  Array adds, the world's brightness, how an object fills the camera frame,
  bounces and an orbit sampled over frames (the orbit has to span the frame
  range, not end early and hold), a procedural texture feeding a surface, the
  number of scattered instances, cloth above a collider, a baked cache, no
  data-block left without a user. They measure the result, never which tool
  ran; closed kinds can be listed among them with a modifier name the agent
  chose (``$modifier:SUBSURF``). An object a task created is followed by its
  session identity, so a later rename does not lose it.
* A graded survey edit is probed before its turn: a check that already holds
  cannot show that the turn did anything, so that run is ``ungraded`` (the
  bounce and the orbit, which step frames, are not probed). Fixtures give two
  tasks a starting state the factory scene would satisfy (the world is
  already dark grey; a new file has nothing to purge).
* ``ungraded``, with the reason in the task: the eight requests whose result
  is a file, an editor state, or behind an approval the harness always
  declines.

Gate sets. The ``default`` and ``long`` sets are compared across merges, so
they change only as a new version (``eval_gate.py``; ``GATE_DIGESTS`` pins
both sets at every version and ``test_native_grading_contract.py`` fails on a
drift), and the grading helpers both benchmark sides share are pinned too.

* Version 1's questions (one task of the default set; eight of the long
  set's forty) ask for no answer form and were graded on the unchanged scene
  alone, so the contract reports those runs as ``ungraded``: a version-1
  summary marks them, keeps the recorded pass figures and prints a
  ``Graded`` row beside them.
* Version 2 reads them. Every edit is byte-identical to version 1 (prompt,
  checks, order), so the edits' figures compare across the line. Each question
  keeps its wording and ends with the sentence that asks for its answer line;
  the truth is computed from the scene before the prompt is sent, and the run
  passes only on the right answer with the scene unchanged. Neither answer
  can be given without reading the scene. The opening question ("how many
  mesh objects, and their names") is asked with one more mesh in the scene, a
  small plate the harness adds for that turn and removes after grading,
  because the factory scene's answer could be guessed. The later questions
  ask for the cube's dimensions and location, read within 0.005 m, with the
  cube moved by a fixed offset for the turn and back after grading, because
  where the chat's own edits put it could be recalled. Neither is a yes/no
  question, so no pair of variants is needed. A version-2 run has one figure,
  the contract's.
* Both benchmark sides read the answer the same way: the Angelo driver in
  ``eval_grading_live``, the Blender MCP driver through
  ``bench_mcp_answers`` (truth and fixture over the add-on socket; the answer
  line read by the driver in every assistant message of the turn, as the
  Angelo side reads every assistant row; a turn stopped at its time limit
  does not pass; the driver checks that Blender undid each fixture).
* A recorded result states its version (``grading.gate_version`` in a trial
  report and in ``eval-live-agent.json``, ``gate_version`` in each benchmark
  record and in the plan); one without it is version 1. A version-2 question
  carries ``gate_version``, so the task list's digest covers it. No report
  shows runs of two versions together without saying so: ``SUMMARY.md`` names
  the sides' versions when Blender MCP trials are reused from an earlier run,
  ``bench_mcp_gate.py`` and ``measured_results.py`` refuse runs of two
  versions unless ``--mixed-versions`` is given, and a summary rebuilt from an
  earlier run uses that run's own task list.
* The coverage set opens with the gate's question as asked now; it is not a
  versioned gate set. The stress replay asks version 1's questions, because
  its plans and replies were recorded for them.

Where the states are printed: the driver's per-task and per-trial lines and
``trial.json`` (``grade``, ``grading.totals``), ``eval-live-agent.json``
(``grading``, per-task counts), ``survey-report`` (totals, a Graded column,
questions, ungraded runs), ``SUMMARY.md`` and ``bench-mcp-vs-angelo.json``
(``graded`` per task and side; ``bench_mcp_gate.py RUN_DIR`` prints a
finished run's totals in one line; every pass cell and the pass rate show the
failed and inconclusive runs beside the passed of all runs), the scenario
scorecard and report (turns passed, failed, inconclusive, ungraded and not
attempted; each is shown as such, and the saved scene's checks as passed /
failed / inconclusive of all), ``ONESHOT-REPORT.md`` and ``ONESHOT-GROUP.md``
(each trial's verdict, its closed checks as passed / failed / inconclusive of
all, and every side's passed, failed and inconclusive trials),
and ``measured_results.py`` (an ungraded run is neither a pass nor a failure,
so a kind measured only by ungraded runs has ``tasks`` 0 and cannot admit a
model to Auto routing).

Guard: ``test_native_grading_contract.py`` fails when a task of any set
(default, long, delegation, coverage, survey, scenarios) has neither a check
nor a stated reason. ``test_blender_survey_tasks.py`` proves each survey
check in the application: it fails before its edit and on a result that only
looks like the request (four copies for five, a hole along the other axis),
and passes on the reference result; each question fails on an empty, a
refusing, a wrong and an unsupported answer. ``test_blender_gate_answers.py``
does the same for the forty-task gate set at version 2, on both benchmark
sides' routes.

Survey versions. Every trial records ``grading``: the contract, the task
list's digest and ``survey_version``. ``survey_report.py`` refuses runs of
another version unless ``--mixed-versions`` is given, and then names them.
Version 1 (no version recorded) is the 64-task survey. Version 2 differs in:
65 tasks (``view_check_cropped`` is new, after ``top_camera_active``; the two
camera questions weigh 1 each instead of one at 2); every question's prompt
ends with its answer-form sentence and is graded on its answer;
``mesh_stats`` moved after ``extrude_top``; 18 edits gained result checks and
8 an ungraded reason; fixtures change a starting state, transiently for
``scene_overview``, ``render_info`` and the two camera questions, and for the
rest of the chat where ``world_grey`` starts from a blue world and
``purge_unused`` from one unused material.

``--task-set survey-questions`` is the survey's six questions and the two
edits that make the cube they ask about (extruded, so its counts are not a
default cube's), in survey order (eight messages a trial). It shows whether a
model follows every answer form; the survey's figures need the survey.

Whether a model follows the answer forms is measured, not assumed. The
shortest runs that ask every form are the first seven tasks of the long set
(both gate forms) and ``survey-questions`` (the survey's five), one trial
each, with the gate's own options; ``answer_forms_report.py`` then prints every
answer line as written and how it was read (15 messages a model, billed by the
model provider)::

   python3 tests/python/angelo_agent/eval_live_agent.py --task-set long --long-tasks 7 --trials 1 \
     --connection-id CONNECTION_UUID --model MODEL --effort low --blender BINARY --observation turn-context \
     --after-failure reset --output OUT/MODEL-gate7 --authorize-billed-runs
   python3 tests/python/angelo_agent/eval_live_agent.py --task-set survey-questions --trials 1 \
     --connection-id CONNECTION_UUID --model MODEL --effort low --blender BINARY --observation turn-context \
     --after-failure reset --output OUT/MODEL-survey-questions --authorize-billed-runs
   python3 tests/python/angelo_agent/answer_forms_report.py OUT/MODEL-gate7 OUT/MODEL-survey-questions

Automatic fluid evaluation
==========================

The automatic read path returns stored native fluid values; it does not run
the fluid solver. A request that needs to execute the fluid solver's Python
uses the explicitly trusted or individually approved script path, and must not
return an unevaluated value as a verified simulation result. This exception
covers fluid only; other native read families evaluate as usual.

Enabling automatic fluid evaluation requires:

* Source and lifetime authority for the internal Python wrappers and every
  selected solver/cache-step callback.
* Source cache reads preserved while all generated cache outputs go to
  bounded, request-owned storage with cancellation and complete cleanup.
* The retained solver-state work compiled and validated against warm and cold
  fluid cases, source immutability, resource failures, and ordinary native
  evaluation parity, with its latency measured before any benefit is claimed.

How to continue
===============

**Selection rule.** Native coverage aims at a fast, safe and verified
environment for a coding agent, with latency a primary measure. New coverage
work follows this rule:

1. Every item states its expected return before work begins: seconds saved
   per occurrence × how often it occurs, from recorded receipts
   (``receipt.phases``: first native action, staging, verification, commit,
   time after the verified result) or a request survey.
2. Priority: latency sinks on every request first; then host latency on
   production-size scenes (not only the benchmark cube); then coverage gaps
   weighted by how often real requests fall back to reviewed Python
   (``fallback_categories``, a request survey). Operator-count breadth is never
   a goal by itself.
3. Never native: code execution (text blocks, drivers, script nodes, add-ons)
   stays reviewed Python; effects outside the scene (files, network,
   preferences, credentials) are approval-gated tools; human gestures (brush
   strokes, knife, click-select, viewport navigation) become intent-level kinds
   or nothing; editor chrome is out of scope.
4. Long or unbounded work (simulation bakes, renders, heavy remeshing) needs a
   job contract with progress and cancellation, not a typed program.
5. Reviewed Python stays the escape hatch: the target is high usage-weighted
   native coverage, not 100% of operators; a domain stops when what remains
   has no measured demand.
6. Every new breadth mechanism gets an independent safety review that tries to
   break it with reproductions, and passes the gate task sets without a
   failure, before merging: breadth is where crashes, multi-hour freezes,
   preview/commit mismatches and moved dependents come from. When a fix is not
   small and obviously correct, the operator or value is denied instead.

**Request survey (the demand side of rule 1).** The benchmark sets are natively
covered by design, so they cannot rank gaps. The ``survey`` task set
(``eval_survey_tasks.py``) is 65 requests phrased as a Blender user types them,
in one chat from the factory scene, across 17 categories. Each carries an
estimated ``weight`` (category totals sum to 100; the rationale table is in the
module, and the figures are estimates until ordinary sessions report
``fallback_categories``) and an ``expect`` class: 49 ``native``, 2 ``approval``
(sculpting, Media Studio), 9 ``python`` (requests expected to need reviewed
Python: torus, vertex colours, file renders, markers, node-group inputs, automatic weights,
Grease Pencil strokes, bakes, orphan purge) and 5 ``never-native`` routing
checks (files, viewport chrome, scripts) that never count as gaps. Every task
is graded or says why it cannot be (`Grading contract`_): 33 edits by the
closed checks, 18 by result checks, 8 declared ungraded, and the 6 questions
on their answers. ``test_blender_survey_tasks.py`` proves, through the trial
driver's own grading steps and without a model, that every graded edit fails
before its edit and passes after it, and that every question passes only on
the right answer with the observation it needs. The trial driver records each
turn's tool names, capability lookups and rejected plans
(``survey_report.turn_activity``; counts only). ``survey_report.py`` reads a
run and ranks the gaps (a declined script or a failure, not ``never-native``)
by weight × gap rate × saving, where saving = the observed time plus an
assumed 20 s review per declined script (``--approval-wait-s``) minus the
median native time of the same category, and ranks extra-round sinks (lookup
rounds, rejections, invalid calls) by weight × seconds. It also states every
run's grade and lists the ungraded ones with their reasons. One trial is 65
messages, billed by the model provider::

   python3 tests/python/angelo_agent/eval_live_agent.py --task-set survey --trials 1 \
     --connection-id CONNECTION_UUID --blender BINARY --observation turn-context \
     --after-failure reset --output OUT --authorize-billed-runs
   python3 tests/python/angelo_agent/survey_report.py OUT

**Generic first.** Before adding a typed kind, check whether a generic path
already covers the request: a settings path (``rna_rules``), a generic
modifier, constraint or node type, or a describe call (``angelo_blender_api``
domain ``WRITABLE``). A new typed kind is worth it only for semantics RNA
assignment cannot express (topology edits, multi-object effects, intents on
live structure). To widen a generic path, change the deny-list or a bound
table, add host cases to ``test_native_generic_settings.py`` and a native case
that commits and reads the value back.

**Adding an action kind, end to end.**

1. Schema: add terse parameter schemas to ``action_schemas.PARAMETER_SCHEMAS``
   (or a pure spec module such as ``action_camera_light_spec.py`` or
   ``action_properties.py``), its mutated families to ``_mutations`` and a
   short ``CAPABILITIES`` entry. Defaults are explicit; no ``bpy`` import.
2. Validation: cross-field rules in ``action_catalog.validate_operation``,
   every object it depends on in ``affected_references`` (each must be
   declared in the read set), every object it changes besides its target in
   ``written_references`` (``object_join``'s deleted sources: they must be in
   the write set, and inference puts them there), and the families it really writes in
   ``resource_dependencies`` (narrow per operation).
3. Kernel: a module whose function takes the ``StagedActionContext`` and the
   canonical operation, dispatched from ``action_kernels.apply_operation``.
   Touch only staged IDs (``context.resolve``, ``require_private``,
   ``track_id`` for new IDs); bound evaluated work before any depsgraph update
   (``action_budget``); return a JSON read-back.
4. Commit: every family the kind writes must transfer in
   ``program_commit.ProgramCommitMixin.commit`` with a journal and rollback
   (object settings: ``_transfer_settings``; data: ``DATA_SWAP_RESOURCES``),
   and appear in ``program_objects.delta``. A family nothing transfers is
   silently lost at commit.
5. Revisions: anything the kind can change must be covered by
   ``scene_fingerprints.capture`` (the observed ``object_revision``) or, for
   scene state, ``scene_settings.settings_revision``; otherwise a stale plan
   could overwrite a manual edit.
6. Checks: derive what the operation states in
   ``derived_checks._operation_checks`` and review the ``*_KEEPS`` tables (a
   kind missing from a table conservatively drops that check). Add a check kind
   to ``validation_spec.CHECK_VARIANTS`` with its predicate only when no
   existing one can verify the result; predicates are read-only and
   inconclusive when unsure.
7. Prompt cost: new kinds and checks are deferred automatically
   (``plan_kinds.PLAN_CORE_ACTIONS`` is the exact core); do not add to the
   core without measuring. Run ``coverage_prefix.py`` before and after and
   update the figures in "Prompt and schema cost"; ``test_native_plan_schema``'s
   standards vectors give every deferred kind and check one host-valid example
   that must match exactly one plan-schema variant (and check variant).
8. Contract version: bump ``routing.TOOLSET_VERSION`` with a comment and a
   paragraph in ``agent_integration.rst``; update the pinned counts in
   ``test_native_action_catalog.py`` and ``test_native_legacy_names.py``.
9. Tests: a host suite ``test_native_<feature>.py`` (schemas, every rejection,
   derived checks, kernel and predicate with RNA stand-ins) and a native suite
   ``test_blender_<feature>.py`` (staging isolation, APPLY and PREVIEW/accept,
   stale revisions, commit, Undo/Redo), registered in
   ``native_suite_manifest.py``. Also add a case to
   ``test_blender_program_commit_effects.py`` (GUI): it fails unless every kind
   runs through its staged-equals-committed, Undo and Redo harness, which also
   combines kinds in one program (``keys_and_nodes``, ``nodes_and_operator``,
   ``keys_and_operator``, ``markers_and_world``). Add a task to
   ``eval_live_tasks.build_coverage_tasks`` per generic mechanism and family,
   graded by closed checks.
10. Docs: update the rows, counts and metrics here (``native_coverage_metrics.py``
    in Blender for breadth), and the contract in ``agent_execution.rst`` or
    ``local_verification_and_recipes.rst``.

**Admitting an operator to operator_run.** The allow-list and pure rules
live in ``action_operator_spec.py``, the kernel in ``action_operators.py`` and
the digest, revision field and commit of object-level state in
``operator_state.py``; "Allow-listed operators" in ``agent_execution.rst``
records the deny-by-default rules. A new operator is admitted by adding it to
``ALLOWED`` with its growth spec after reading its source for effects outside
the edited object; the RNA rules (Python, modal, macro, internal, file and
pointer properties) still apply. Measure its time ("operator_run work
bounds"), on long chains as well as grids, and check that commit carries
everything it changes: operators that change a modifier or constraint stack,
or pose data no family carries, are refused at run time
(``UNCOMMITTED_STATE``, ``POSE_DATA``); a reindexing operator is refused when
live children or Hooks depend on the old order (``REINDEXED_DEPENDENTS``). The
object-level state commit writes cannot revert a manual edit: commit
re-verifies the full program fingerprint (every object setting), not only the
observed revision.

* ``pose`` transform operators run in Pose mode (``POSE_MODULES``,
  ``mode_override`` with the staged pose-bone context lists) and commit through
  the ``pose`` family (``bone_state.transfer_pose`` of every bone, recorded in
  ``posed_bones``; ``names=None`` copies every bone). Pose selection,
  visibility, constraints, motion paths, the clipboard and Apply Pose as Rest
  are not admitted.
* ``object.particle_system_add`` is ``DENIED`` as ``COVERED``: ``modifier_add``
  with ``PARTICLE_SYSTEM`` and ``particle_set`` add and configure a particle
  system with their own commit. ``forcefield_toggle`` needs existing
  force-field settings (no RNA path creates them on the live object). Bone
  renames, superlinear, clipboard, cursor, view and new-object operators are
  ``DENIED`` by name.
* The Edit-mode and data-local families (``action_operator_families``) are
  deny by default: Edit-mode ``transform.*`` with explicit values (the pivot is
  the selection's bounds or ``center_override``), Grease Pencil stroke
  operators in Edit mode, hair ``curves.*`` and text ``font.*`` in their Edit
  modes (text selection is ALL or NONE because the text cursor is not
  observable), ``constraint.*`` naming one of the target's constraints, and
  data-local object operators (skin marks, transform clears and applies).
  The ``constraints`` commit family gives the live object the whole stack by
  name (``operator_state.sync_constraints``); ``transforms`` is reused and the
  parent inverse is copied by ``commit_object_state``. Revisions cover hair
  curves (``curves_data``), Grease Pencil material settings and skin radii and
  marks. Modifier copy, hooks, shader effects, multires and bind operators
  stay reviewed Python.

**Domain rules.** New kinds and operators in these domains keep the rules the
existing ones follow:

* Animation: every kind works through the object's own Action slot
  channelbag (``anim_channels``: channels and the private-Action guard;
  ``anim_stage``: live facts recorded at staging; ``anim_commit``: commit
  steps). Shared Actions are refused, not edited for everyone or split
  silently: ``anim_stage`` counts the live Action's users besides the object
  and its own data (other objects, NLA strips, a fake user) and every editing
  kind refuses them (``ACTION_SHARED``, ``keyframe_delete`` included);
  ``action_set`` ``SINGLE_USER`` isolates explicitly and is reported in
  ``isolated_shared_data``, which also reports the silent split older kinds
  (``keyframe_insert``, ``property_keyframe``) make. An object's data sharing
  the replaced Action (a camera keyed on both, Blender 5's default) follows the
  committed copy instead of being split.
* Animation: existing Actions are referenced by the ``dat:<epoch>:ACTION:<uid>``
  ID an object read shows, played on the stage from a private copy, and
  committed live after an exact digest check (``referenced_actions``); a
  program that assigns one refuses to edit its keys (``ACTION_ASSIGNED``). An
  Action left without users is refused unless ``stash_previous`` keeps it.
  Commit adopts names by recorded source (``anim_commit.replaced_actions``,
  from ``anim_stage``'s ``action_sources``) instead of "the active Action or
  the last pushed strip", so a new or assigned Action keeps its own name.
* Animation: NLA and marker edits commit by rebuilding the whole live list
  from the staged one (``nla_stack``, ``markers``) with a snapshot restored on
  rollback (``undo_journal``, run before the object journals); only plain
  action strips are admitted so a snapshot holds every setting a rebuild can
  write. The snapshot keeps the active track, which a rebuild sets again
  (Blender activates every new track), and a push-down's rollback sets the
  object's active track and modifier again (``program_transfers.active_state``).
  A strip's active flag cannot be set from Python, so a committed rebuild
  clears the active strip, and the rollback of a stack that had one is
  reported as unverified with partial changes possible, never as an exact
  rollback. Markers join the scene settings revision (``REVISION_VERSION`` 4),
  so a manual marker edit, selection included, makes scene-bound programs
  stale.
* Animation: keys are bounded at 8,000 per operation ("Animation work
  bounds"); every refusal path has a test (``test_native_anim_edits``, the
  refusal tables of ``test_blender_anim_keys`` and
  ``test_blender_anim_structure``; the kernels' ``DRIVEN`` guard is a second
  wall behind ``action_budget``'s driver refusal). A new ``keyframe_edit``
  operation is a row in ``EDIT_FIELDS``, a field, a branch in
  ``anim_key_edits.edit`` and a host and native case.
* Bones: ``bone_set`` (``action_bones_spec``, ``action_bones``) edits one
  existing bone: rest geometry in staged Edit mode, Bone settings
  (``settings``) and the pose (``rotation_mode``, ``location``, ``rotation``,
  ``scale``, ``pose_settings``). Commit swaps the armature data for rest edits
  and copies the pose with ``bone_state.transfer_pose`` (the ``pose`` family,
  journaled); the revision's ``bones`` field covers every writable bone value,
  so B-Bone rigs are bindable. X-axis mirror editing is off while the kernel
  edits (only the named bone is written; connected neighbours still move and
  are reported); animated or NLA-driven pose channels and settings are refused
  rather than written under an Action; shared armature data follows the
  shared-data policy (only the edited object gets the copy).
* Particles: ``PARTICLE_SYSTEM`` is a generic modifier type with a settings
  bound (``particle_rules``: count, children, path steps, substeps, instanced
  size; render children and render steps count, as Subdivision counts render
  levels) and a revision field (``action_particles.systems_revision``).
  Settings another system or a fake user keeps are refused rather than
  isolated or edited for every user; removing, applying, joining and
  separating particle systems stay reviewed Python; the Distribute Points on
  Faces area rule (``node_generic.AREA_BOUNDS``) needs the Geometry Nodes
  modifier first in the stack.
* Grease Pencil: ``create_object`` makes a blank object (one layer, one
  frame); ``operator_run`` runs the ``grease_pencil`` layer and layer-group
  operators in Object mode (``OBJECT_MODULES``) and stroke operators in Edit
  mode; the revision hashes the data's complete RNA (``grease_pencil_data``),
  and objects with Grease Pencil materials are bindable because their stroke
  and fill settings join the revision.
* Node graphs: ``node_edit`` edits one existing material, Geometry Nodes
  catalog or World tree by exact node names (see "Editing existing node
  trees" in ``agent_execution.rst``). A tree with any other user (another
  object or mesh, modifier, group node, scene or a fake user) is refused
  unless ``shared: ISOLATE``, which gives only the target's references the
  edited copy, never edits it for everyone; a slot on shared mesh data is not
  isolated; users gained between staging and commit refuse the commit. The
  revision covers every writable node setting but selection and position,
  which ``node_edit`` never writes on existing nodes; Geometry Nodes edits keep
  the closed catalog contract (no frames, mutes, groups or interfaces there);
  the World refuses nested-group edits; copies a native rebuild cannot
  reproduce exactly are refused.

**Open gaps.** What these domains leave to reviewed Python:

* Animation: keying a pose (pose-bone channels are editable once keyed, not
  keyable natively), the data's own Action in ``action_set``, data channels in
  ``keyframe_copy``, Envelope modifiers and modifier copy/paste, meta and
  transition strips, tweak mode, strip modifiers and split, Blender's
  Bezier-fit decimation and the Graph Editor's slider operators, NLA bake
  (visual keying steps the frame), pose markers, and drivers (row under
  "Armatures").
* Bones: renaming bones, bone constraints and keying a pose.
* Particles: isolating shared settings (``particle.new`` on the live system,
  then ``user_remap``), collection instancing, combed hair, and a check on
  evaluated particle or point counts.
* Operator families: native stroke drawing and hair-object creation (the
  coverage tasks use harness-made fixtures), Grease Pencil and hair modifier
  bounds, Armature constraint target lists, bone constraints, observable text
  cursors, and a closed check for stroke attributes (thickness, cyclic).
* Node graphs, in order of value:

  1. Geometry Nodes interface inputs exposed on the modifier, and the
     modifier's input values (``modifier.properties`` in 5.2): the catalog
     provenance must carry the interface, the revision must read the values
     and ``tree_bound`` must treat a group input feeding an amplifier as
     linked.
  2. Geometry Nodes graphs built in the Node Editor (no provenance): bound
     them from the real nodes (``tree_bound``) and fingerprint them
     completely, instead of the closed catalog.
  3. Image-texture materials: their pixels make them unbindable; a revision of
     the image's identity and source (not pixels) would let ``node_edit``
     reach them, with ``finish``'s image markers already in place.
  4. Item-list nodes (Capture Attribute, Index and Menu Switch, Format
     String): their ``*_item_add``/``remove``/``move`` operators (about 40
     uncovered) as ``node_edit`` steps, then ``node_copy`` could rebuild their
     sockets.
  5. Moving existing nodes (``location`` in the revision first), link mute
     (``links_mute``), socket hide (``hide_socket_toggle``), nested groups of
     groups and inside the World.

**Commands**, from the repository root (with ``GIT_LFS_SKIP_SMUDGE=1`` for
Git, so a checkout does not download LFS content)::

   PYTHON=/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13
   $PYTHON tests/python/angelo_agent/run_host_tests.py
   $PYTHON -m unittest discover -s tests/python/angelo_agent -p 'test_native_property_set.py'
   $PYTHON tests/python/angelo_agent/coverage_prefix.py
   python3 tests/python/angelo_agent/run_native_suites.py --binary BINARY --jobs 3 --include 'test_blender_property_set*'
   python3 tests/python/angelo_agent/run_native_suites.py --binary BINARY --gui --include 'test_blender_program_commit_effects*'
   BINARY --background --factory-startup --disable-autoexec --python-exit-code 1 \
     --python tests/python/angelo_agent/native_coverage_metrics.py -- --json OUT

Run the host suite with Blender's bundled Python 3.13 (any 5.2 build's
``Resources/5.2/python/bin/python3.13``), as CI on Linux does: Python 3.14
defers annotations, so an annotation error in an operator or property passes
there and fails in Blender. Tests that depend on ``sys.platform`` pin it. The
host suite currently has five expected failures in ``test_native_branding.py``:
its fixtures are LFS pointer files in a checkout without LFS content. ``run_native_suites.py`` isolates HOME (and
Cocoa's, ``CFFIXED_USER_HOME``), TMPDIR, Blender user folders and Angelo state for every suite; run any ad-hoc native
script the same way. Use a build directory of your own (for example a copy of
an already built ``Angelo.app`` when the C++ sources are unchanged), never one
a running benchmark uses: the native tests import the add-on from this
checkout, not from the app bundle.

**Pitfalls.**

* ``bl_rna`` enum items are only the static ones; build- and context-dependent
  items (render engines, colour-management views and looks) are validated by
  assigning them, as ``action_property`` does.
* ``action_schema_types.nullable`` adds ``default: None``, so an absent key
  becomes ``null``; never use it inside an open map such as ``property_set``
  values. The shared validator also refuses control characters and blank strings.
* Every exact schema is sent in full to Codex; share fragments, keep
  allow-lists in one place and advertise check shapes rather than repeating
  them (see the ``property`` check).
* In native suites, Python references go stale after Undo; look objects up
  again by name and call ``scene_queries.invalidate()``.
* Host tests use ``SimpleNamespace`` stand-ins that lack most RNA attributes;
  observation code reads optional settings with ``getattr`` defaults.
* An existing modifier of an unsupported type, or one whose bound exceeds the
  budget (a huge Array count, a fine Remesh voxel size), makes
  ``action_budget`` refuse to stage the object at all.
* Generic values bypass the schema's number canonicalisation: catalog
  validation copies the raw JSON (``copy.deepcopy`` of the operation's values)
  so integers stay integers for INT properties. Keep that when adding a
  generic field.
* A generic write is stale-protected by ``program_preimages`` (recorded live
  values), not by the object revision; anything a generic kind can change on an
  object must also be fingerprinted (``generic_settings.values``) or the object
  stops being bindable after the first commit.
* Every poll suspends the stage to a private bundle and resumes it on the
  next one. IDs owned through another ID (a mesh's shape-key ``Key``) must be
  re-owned in ``program_storage.resume_stage``; run a native case under
  ``program_qa_helpers.spent_budget()`` so every step yields.
* The validated build lacks Cycles, Bullet, fluid, OpenVDB/remesh, FFmpeg,
  USD, Alembic and the C++ OBJ/STL/PLY I/O; mark such rows "Not covered".
* ``temp_override(window=...)`` clears a ``scene`` override given in the same
  call, and every context member not overridden (``visible_objects``,
  ``selected_editable_bones``...) resolves through the window's view layer,
  the artist's. Kernels that run operators override ``window=None`` first and
  pass the stage's scene, view layer, objects and bones in a nested override
  (``action_operators.mode_override``). The program executor
  (``program_objects._override``) likewise enters its window first and the
  stage's scene and view layer in a nested override, so an operator a kernel
  calls with only some members of its own (``particle.new`` in
  ``action_particles.copy_settings``) still updates the stage's depsgraph.
  Given in one call, the pair splits into the artist's scene and the stage's
  view layer: stock Blender crashed in the operator's view-layer update, and
  Angelo's ``temp_override`` raises ``TypeError`` on enter instead
  (``test_blender_context_override_pair.py``);
  ``test_native_stage_override_pair.py`` refuses any add-on override that
  gives ``window`` with ``scene`` or ``view_layer``.
* With two or more windows (never in ``--background``), an Edit or Pose mode
  switch sets each other window as the context window to refresh its tool
  system, which drops the Python ``scene`` override (also from overrides
  nested inside it) while ``view_layer`` stays. Stock Blender's view-layer
  update after the ``bpy.ops`` call then built a depsgraph for the artist's
  scene with the stage's view layer and crashed; Angelo resolves the view layer
  from the artist's scene instead (``CTX_data_view_layer``), so the work after
  the switch would run on the artist's scene. Re-entering an
  override after the call is too late; ``action_edit_context.switch_mode``
  runs each switch with no window and hides the inherited view layer (a
  non-RNA value counts as not overridden), so it resolves from the stage
  scene. Only a GUI suite with a second window on another workspace
  reproduces this (``test_blender_operator_run_windows.py``).
* A freed ID's Python wrapper raises ``ReferenceError`` even for
  ``as_pointer()``; filter wrappers you may have freed with a guarded read
  (``action_operators._kept``).
* An operator can change more than the swapped data: pose channels
  (armature duplicate and symmetrize), modifiers (Shade Flat and Smooth, force
  field shapes), other objects' indices (vertex parents, Hooks). Read its
  source and add the family to commit or refuse the effect; a preview that
  shows what commit drops is a bug.
* A crashed GUI suite makes macOS ask "reopen windows?" at every later launch
  of the bundle; the runner sets ``CFFIXED_USER_HOME`` to the suite's home so
  that state stays isolated. A launch outside the runner still shows the prompt
  once after such a crash.
* An operator that frees a datablock (removing the last shape key frees the
  Key) leaves its Python wrapper dangling: never dereference it. Guard the
  known paths before running and compare Main by pointer and session UID after
  (``action_operators._account_ids``).
* Live-structure edits (renames, deletions, duplicates' collections, collection
  links) are intents in ``stage.extras``, carried out by commit with a journal;
  a staged copy keeps its private ``__angelo_copy`` name and stays in the stage
  collection, so verification reads intents (``memberships``, ``renames``,
  ``deleted``) rather than the staged object's own state.
* Stage meshes must stay single-user: a linked duplicate gets a private staged
  copy and is pointed at its link root's live data only at commit
  (``program_rules`` refuses editing linked data after the link, and
  ``commit_fidelity.verify_links`` proves the relation live).
* Factory startup's ``Cube``, ``Camera`` and ``Light`` (objects and meshes) take
  names a native case expects; remove them first or a duplicate's name shifts.
* ``scene_queries.invalidate()`` with no arguments starts a new document epoch,
  so every handle taken before it stops resolving. In native suites build a
  program after the last ``invalidate`` (a stale-plan case that invalidates in
  between fails for the wrong reason); revisions are computed fresh anyway.
* Setting a pose bone's (or object's) ``rotation_mode`` converts its stored
  rotation. A journal that restores channels must restore the mode first, so
  ``transfer_pose`` journals it after the channels (rollback runs in reverse).
* Never assign ``ParticleSystem.settings`` from Python: its setter counts users
  while the property is not tagged as counting them, so every assignment trips
  Blender's reference-count assertion (a failed native suite). Re-point a
  system with ``particle.new`` (a copy) or ``ID.user_remap`` (every user), as
  ``action_particles`` does.
* Point caches rewrite their ``info`` text during playback; the exact program
  fingerprint skips it (``program_dependencies._RNA_SKIP``), or an object with a
  particle system or cloth would look edited after the timeline played.
* Never call ``copy()`` on an embedded node tree (a material's or World's):
  Blender asserts on its owner (``node_owner_pointer_get``); copy the owning
  material or World instead, as ``node_edit`` does for copy-on-write.
* Removing a group interface socket removes the links to it in every tree
  using the group; a Python wrapper of such a link then points at freed
  memory. Capture the sockets you need before an interface or node removal
  (``node_copy.ungroup`` reads the group node's values first).
* A Geometry Nodes tree is stageable only while its provenance matches its
  real graph; any kernel that edits one must regenerate the provenance and
  ``context.node_graphs`` (``node_edit_geometry.refresh``) or the next budget
  check refuses the stage.
* Staging clones each material slot's material separately, so one live
  material used in two slots becomes two clones; a kernel editing it must
  point every slot of the target at one edited copy, or commit would keep the
  other clone's unedited state.
* ``bpy.data.user_map`` reports an embedded tree's owner (the material) as the
  user of a nested group, and a staged copy can hold hidden object-level
  material references; count users outside the stage (``node_edit_commit.other_users``).
* A program's approved precondition is ``program_context.ProgramBinding``, read
  by ``native_programs.rebind`` and ``precondition_change``. A binding field
  ``rebind`` does not handle makes every frame move look like a changed target
  ("The target changed after approval"), so ``frame_step`` and ``playback``
  programs fail. Add a binding field to ``ProgramBinding`` together with how
  ``rebind`` carries it across a frame move (settings revisions carry no frame,
  so they must be equal).
* A cooperative budget that skips optional evidence (revisions) measures
  ``work_clock.WorkClock``, the calling thread's CPU time, never wall or
  process time. In wall time a loaded machine (or the snapshot worker) makes
  the turn context omit a named object's revision, so a recipe that leaves it
  to inference is refused ("has no revision in your latest observation").
* Blender picks an Action slot by the animated ID's name when an Action is
  assigned or a strip is added; staged copies have private names
  (``__angelo_copy_...``), so a kernel that assigns an Action or adds a strip
  chooses the slot itself with the live object's name (``anim_actions.auto_slot``)
  and syncs the strip's action range to that slot (``anim_nla.slot_range``):
  a strip made with no slot keeps a 0-1 range after its slot is set.
* A canonical operation carries every field at its default, and is validated
  again after normalization; a flat schema's "field another operation owns"
  rule compares with the defaults (``anim_spec.given_fields``), not with the
  keys present.
* In native suites, store names, not Python references, across Undo, and run
  ``scene.frame_set(scene.frame_current)`` after keying values by hand: until
  animation is evaluated the object's stored value differs from what Undo
  restores, and a fingerprint comparison fails for the wrong reason.
* NLA track names may repeat in Blender (strip names are unique per object):
  ``nla_edit`` refuses an ambiguous track rather than pick one.
* Transform operators dereference the context window in their status-bar
  text even when run with ``EXEC_DEFAULT`` (``WorkspaceStatus``): with
  ``window=None`` Shrink/Fatten crashed Blender. Only their call gets the
  program's window (``action_operator_modes.call_override``), entered before
  the stage's scene and members; mode switches keep their own window-less
  override. Without an editor area the pivot is the selection's bounds and
  translate, rotate and resize need a window and screen to pass their polls.
  The one visible effect is UI: the status text the call writes clears that
  window's status bar, as running the operator by hand would.
* Some operators recurse per element: ``object.skin_root_mark`` overflowed the
  main thread's stack (a crash, not an exception) on a 10,000-vertex chain.
  Measure new operators on long chains too, not only grids.
* A text's cursor and selection live in the Curve data but not in RNA, so no
  revision or digest sees them; an operator that only moves the cursor would
  pass staging unobserved and then change where a later insertion lands.
* RNA views over packed attributes (hair ``points``, ``curves``,
  ``position_data``) make ``_rna_values`` walk every element; skip them as a
  mesh's element collections are skipped, or digests become seconds long.
* Typed constraint kinds append and configure by count and name; the
  ``constraints`` family must sync the whole stack first, or a deleted
  constraint makes ``constraint_configure``'s commit fail.

Known limitations
=================

* A commit's replaced originals stay in the file as orphan data until it is
  saved or purged; typed programs have no purge, so the result names that data
  and the materials only it uses (``orphan_data.held_only_by_them``, which a
  save still writes) with the reviewed-script call that removes both
  (``agent_execution.rst``, "Stage isolation"). Commit restores unmodified material and node-group clones
  to their originals and gives swapped data, Actions and Worlds their original
  names when nothing else uses the originals, so a committed edit does not
  leave the object on suffixed copies (``Cube.001`` data, ``Material.001``, a
  ``.001`` node group, an Action named after the private staged copy;
  ``test_blender_commit_names.py``).
* Text bodies are single-line: the shared schema validator refuses control
  characters, including newlines.
* Only ``operator_run`` transform calls wait for an artist's modal tool. A
  program's commit (any kind) still runs from the timer while a tool is
  active, so its Undo step can land mid-tool; commit re-verifies the bound
  revisions first, so it refuses an object the tool has already changed.
* Curve, Grease Pencil and hair objects above the 100,000-point budget are
  refused after any ``operator_run`` operator, even one that adds no points.
* Large rigs commit slowly with any kind: a one-bone pose on a 500-bone rig
  commits in about 1 s (``test_blender_bones.py`` reports it), nearly all of it
  the full program fingerprint (``program_dependencies._fingerprint``) and the
  embedded-link walks over every pose bone, not the ``pose`` transfer, which
  copies only the posed bones.
* External (non-OpenAI) Codex connections get the complete schemas together
  with the deferred-lookup guidance; they do not use ``codex_arguments`` yet
  ("Prompt and schema cost").
