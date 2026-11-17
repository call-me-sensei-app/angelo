.. SPDX-FileCopyrightText: 2026 Angelo Contributors
.. SPDX-License-Identifier: GPL-2.0-or-later

################
Bake Auto Smooth
################

A Blender-side speed-up for a long-standing report: Smooth by Angle on many objects that share one
mesh (`#153195 <https://projects.blender.org/blender/blender/issues/153195>`_,
`#126391 <https://projects.blender.org/blender/blender/issues/126391>`_). The operator keeps the
shading exactly as the modifier gave it, or refuses and says why.

Shade Auto Smooth adds a Smooth by Angle modifier to every selected object, and opening a file from
before 4.1 adds an Auto Smooth modifier to every object that used mesh auto smooth. Objects sharing a
mesh share one evaluated mesh only while they have no modifiers; with one each, every object evaluates
its own mesh, sharp-edge array, normals and GPU buffers. On 10,000 objects sharing one mesh this
measured 2.3x slower re-evaluation and 13x more evaluation memory than the same sharp edges baked into
the mesh (Blender 5.2.3, background, contended machine; ratios only). Apply refuses a mesh with
several users.

**Object > Bake Auto Smooth** (also in the object context menu, below Shade Auto Smooth;
``bpy.ops.object.shade_auto_smooth_bake()``) works on the meshes of the selected objects:

* It evaluates the modifier once on a copy of the mesh, as Apply does, copies ``sharp_edge`` and
  ``sharp_face`` into the mesh, and removes the modifier from **every** object that uses the mesh,
  selected or not. One Undo step.
* It bakes a mesh only when the result is exactly what the modifiers gave, and otherwise leaves it
  and every modifier unchanged and reports why (the first reason in the report, with a count):

  - the modifier is Smooth by Angle from the Essentials library (or a copy appended before 5.0) or the
    Auto Smooth group that versioning adds (a look-alike node group is not recognized);
  - its node group depends on nothing but its mesh, by the dependency analysis the depsgraph uses: no
    data-block, own transform, camera, render settings or time, no data-block inputs and no Is
    Viewport (an edited copy that reads anything else is refused);
  - it is the first modifier of every user, enabled for the viewport, render and Edit Mode,
    unanimated, with the same node group and inputs on every user, and every user is in Object Mode;
    unanimated covers the node group and every group nested in it too (an action, NLA strips or
    drivers on a group can change a socket value between frames, which the dependency analysis does
    not see);
  - no user is deformed before it (armature, lattice or curve parent), the mesh has no shape keys,
    is not in Edit Mode, and the mesh and its users are editable (not linked or overridden);
  - the result is a mesh alone whose topology, material slots and every attribute but ``sharp_edge``
    and ``sharp_face`` are byte-identical to the mesh, none added or removed.
* Afterwards the objects share one evaluated mesh again. The angle no longer follows later edits;
  Shade Auto Smooth adds the modifier back.
* Apply's "multi-user data" refusal names the operator when the modifier is first.
* Not detected: a driver elsewhere that reads the removed modifier's inputs stops finding them.

It is an operator, not a modifier option: the cost is the per-object evaluated mesh, which a modifier
setting would still create. Sharing evaluated meshes between objects with identical, dependency-free
modifier stacks (any modifier, kept live) is the general fix and a separate change.

Upstream
========

As of 2026-11-03, #153195 is open. Blender 5.3's ``1422f044cbf`` (passing geometry sets between
modifiers, PR #119968) says it is part of fixing it; its author confirmed on the issue that it does
not. Upstream PR #153359 (open) reuses one object's evaluated result for other objects with the same
mesh when Auto Smooth is their only modifier, for files versioned from before 4.1; it is narrower
than the general fix below and does not touch files saved since. Neither 5.3 nor upstream ``main``
(the 5.4 alpha) has a bake operator. If upstream ships shared evaluation, this operator stays useful
only for its ``Apply``-like result; compare at the base move.

Validation
==========

* Host: ``test_native_bake_auto_smooth.py`` (contracts read from the sources; every new string in
  Angelo's template and its 20 catalogues).
* Background: ``test_blender_bake_auto_smooth.py`` (bit-identical corner normals and sharpness before
  and after on held-out meshes, every refusal (animation keyed or driven inside the group and a
  nested group included), unselected and hidden users, Undo, Apply's hint, 10,000
  shared users).
* Measurement: ``benchmark_bake_auto_smooth.py`` (background): re-evaluation, memory and evaluated
  meshes for 10,000 shared objects before and after the bake, in one build.

How to continue
===============

Shared evaluated geometry: evaluate once for objects that share a mesh and have identical modifier
stacks without object, scene or time dependencies; upstream PR #153359 is a narrower attempt.
