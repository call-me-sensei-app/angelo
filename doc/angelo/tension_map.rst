.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Tension map
===========

Where a posed, simulated, shape-keyed or sculpted mesh stretches and compresses: the second most-voted idea on
Right-Click Select ("Tension/Stress Maps", 648 votes), which Blender has never shipped natively. Angelo measures it
in C++ and draws it in the 3D view: blue where an edge got shorter than at rest, red where it got longer, live while
posing or playing. The value is also an attribute any material reads with an Attribute node, in EEVEE and Cycles.

One click: the chat bar's + menu, **Inspect > Tension map**. It measures the selected meshes, turns the map on and
opens a small card in the 3D view's corner (orbiting, posing and playback go on while it is open).

What is measured
----------------

The **Tension** modifier (Add Modifier > Edit > Tension; ``TensionModifier``, type ``TENSION``) writes:

==============================  ====================================================================================
Output                          Value
==============================  ====================================================================================
``tension`` (point, float)      ``sum(lengths) / sum(rest lengths) - 1`` over the vertex's edges. Weighting by rest
                                length keeps a tiny edge from dominating its vertex. ``0.1`` is 10% stretched,
                                ``-0.1`` 10% compressed.
``tension_edge`` (edge, float)  ``length / rest length - 1`` per edge. Empty name: not written.
==============================  ====================================================================================

Edges shorter than 1e-12 at rest read 0 and are left out of their vertices' sums. Each vertex gathers its own edges, in parallel and
deterministic, through ``Mesh::vert_to_edge_map()``: an Angelo topology cache beside Blender's vertex to face map
(``MeshRuntime::vert_to_edge_map_cache``), shared by every frame's evaluated copy and cleared with the other topology
caches (``BKE_mesh_runtime_clear_geometry``, ``tag_edges_split``, a checkpoint install). The work is linear in the
edges whatever the faces' sizes or the vertices' valence (a per-face corner scan made one N-sided face cost N²), loose
edges included, and nothing is allocated per frame beyond the output attributes, written in place. Smoothing gathers
neighbours the same way. Rest lengths are recomputed each frame (in parallel, a fraction of the cost): a cache keyed on
data pointers could go stale under in-place edits such as sculpting. **Smooth** averages each vertex value with its neighbours that many
times (0 by default). The modifier changes no geometry (``NonGeometrical``); it refuses an output name that is
built in (``position``, ...), internal (a leading dot), ``rest_position``, equal to the other output or the
attribute its rest is read from, with an error on the modifier.

The rest shape, ``rest_source``:

* **Original** (default): the mesh before shape keys and modifiers. If the input carries Blender's own
  ``rest_position`` attribute (``Object.add_rest_position_attribute``) it is used, so the measure stays valid after
  Subdivision Surface and Mirror (they carry it consistently). Solidify, Array and Screw copy it unchanged into the
  geometry they make: edges with no rest length there are left out (they read 0), but edges joining copies can
  read wrong, so Tension belongs above those. Otherwise the original mesh's positions, when the vertex
  counts match, and an error naming the fix (Add Rest Position, in the Shape Keys panel, or moving the modifier up)
  when they don't. In Edit Mode the edit mesh is the rest.
* **Shape Key**: a key of the mesh (``shape_key``, the reference key by default): animate other keys against it.
* **Captured** (``ATTRIBUTE``): a float vector point attribute (``rest_attribute``, default ``tension_rest``). The
  card's **Capture rest shape** stores the mesh's current shape there (in Edit Mode, the edit mesh's shape, kept
  when Edit Mode ends): capture, then sculpt or edit, and the map
  shows how the surface was stretched. Modifiers that add geometry interpolate it too.

Where it goes in the stack
--------------------------

``BKE_modifiers_add_at_end_if_possible`` places a new Tension modifier right after the last deform-only modifier
(Armature, Cloth, Lattice, ...; never past modifiers pinned last), so it measures the deformation and a later
Subdivision Surface interpolates the value instead of being measured. When a modifier that may change the vertex
count comes before it (Mirror, Array, ...), ``ed::object::modifier_add`` turns on the object's Rest Position so the
Original rest stays measurable. This applies to every way a modifier is added (the menu, ``modifiers.new``, the
agent).

The overlay
-----------

``View3DOverlay.show_tension`` (off by default, per 3D view, not animatable) colours every mesh whose evaluated
geometry carries the value: the vertex attribute of the object's last viewport-enabled Tension modifier, or a float
attribute named ``tension`` from anything else (a Geometry Nodes setup). ``tension_stretch_max`` and
``tension_compress_max`` are the strain at full red and full blue (0.25 = 25%), ``tension_opacity`` the overlay's
strength; unstrained surface keeps a light veil so the map reads as on. Files saved without these fields get the
defaults (``version_angelo_tension_overlay``, not version-gated: the file version is upstream's).

Implementation (``source/blender/draw``): ``overlay_tension.hh`` (``Tension``, an overlay like the Viewer
attribute's: drawn in the line pass, depth-equal over the surface, alpha-blended, skipped in selection), the
``overlay_tension_mesh`` shader, and a ``VBOType::Tension`` corner buffer extracted from the attribute
(``extract_mesh_vbo_tension.cc``), interpolated by GPU subdivision like vertex weights. The batch cache keeps the
attribute name it extracted (``tension_used``) and re-extracts when another is asked for.

Never slower when unused
------------------------

* A mesh without the modifier pays nothing: no attribute exists, nothing is evaluated.
* A 3D view with the overlay off syncs nothing (``begin_sync`` disables the pass) and extracts no buffer.
* The overlay's shader is compiled in the background with the other overlay shaders.

The card and the + menu
-----------------------

``tension_map.py`` (operator ``angelo.tension_map``, the + menu registration, the scene and overlay edits through
``Live``), ``tension_map_card.py`` (the card, reading a ``tension_map_logic.Snapshot``) and ``tension_map_logic.py``
(steps, wording, which meshes a click measures; no Blender). The click measures the active mesh and the other
selected local meshes (one Undo step), turns the map on in this window's 3D views (and their overlays, which hold
it, when they were hidden) and opens the card: the overlay switch, the rest shape (Original / Shape key / Captured, with the key list or Capture rest shape), full red at
5–100% stretch, full blue at 5–50% compression, opacity, the attribute name materials read, **Remove** (modifiers
off, map hidden, one Undo step) and **Done**. Rest changes are one Undo step each; the overlay settings belong to the
3D view, like Blender's own overlays. Blender pushes no Undo step for an operator a script runs (the + menu runs it
from one), so the operator and the card push their own steps (``undo_push``, in the click's window) and the operator
carries no ``UNDO`` flag, which would add a second step when it is run from Blender's own interface. A range set elsewhere shows with no segment chosen.

The agent
---------

The agent adds and configures the modifier with its typed ``modifier_add`` / ``modifier_configure`` (type
``TENSION``, RNA-checked generic settings such as ``rest_source``, ``shape_key``, ``smooth_iterations``), placed as
above. The add-on's registries know it writes attributes only: ``generic_settings.DEFORM_MODIFIERS`` (its bound is the
input's vertex count), ``material_faces._INDEX_KEEPING``, ``script_kit_geometry.KEEPS_SHAPE``, and
``scene_read_fields.MODIFIER_FIELDS`` lists its settings for scene reads. No prompt text changed (``TOOLSET_VERSION``
unchanged). The overlay is a view setting the person turns on from the + menu or the card.

Performance
-----------

Measured 2026-10-31 18:01-18:24 on Mac 1 (M4 Pro, billed lock, quiet) by ``tension_map_bench.py`` (``-b``, one
untimed pass then 3 x 24 timed frames, median per frame with p25-p75) and ``tension_map_playback.py`` (a window,
Solid shading, frame dropping off, 8 s after 3 s of warm-up). Builds, all Release: **main** = origin/main
7338359b5b8 (clean); **before** = this branch before the 1.0.8 performance work (serial edge scatter, generic corner
adaptation); **after** = this branch (cached vertex-to-edge gather, parallel corner gather). Every run of every arm
followed one validity rule fixed in advance (load1 < 4.0 at its start, no foreign test suite, build tool or user
process at 80% CPU or more at its start or end; an invalid run is repeated up to twice, and runs that never became
valid are reported as missing). Scene: the 1,002,001-vertex grid unless named.

.. list-table:: Per-frame evaluation (``-b``), median ms per run
   :header-rows: 1

   * - Case
     - main
     - before
     - after
   * - Nothing measured (4 interleaved pairs)
     - 0.83, 0.78, 0.78, 0.78
     -
     - 0.81, 1.10, 0.78, 0.77
   * - Tension (vertex and edge outputs)
     - GN workaround: 18.65, 18.48, 18.27
     - 6.14, 6.23, 6.14
     - 2.21, 2.15, 2.18
   * - Tension, vertex output only
     -
     -
     - 1.76, 1.76, 1.76
   * - Tension, 4 smoothing passes
     -
     -
     - 3.87, 3.84, 3.80

* Nothing measured, 6 more interleaved pairs at 18:36: main 0.79, 0.75, 0.79, 0.78, 0.87, 0.82 against after 0.78,
  0.83, 0.86, 0.85, 0.81, 0.78 (one try rejected for a foreign git-lfs process at 93% CPU and repeated). Over all ten
  pairs the after-minus-main difference has a median of -0.003 ms (five pairs each way; the 1.10 run's whole p25-p75
  was shifted, a transient): no measurable cost when the feature is unused. On that path the branch adds one shared
  cache handle copied per evaluated mesh.
* Measuring costs about 1.4 ms per frame over doing nothing at a million vertices (0.98 ms vertex values, 0.42 ms
  edge values, 0.4 ms per smoothing pass): the measured frame is 2.8x faster than before (its overhead 3.8x
  smaller), and the overhead is about 13x smaller than the Geometry Nodes workaround's 17.7 ms.
* Beyond grids (after vs main, 2 runs each): a 100,000-gon 0.24 / 0.25 ms (nothing measured 0.11-0.12, GN 1.43 /
  1.41); a fan pole of valence 100,000 0.43 / 0.44 ms (GN 2.41 / 2.38); a wire grid of 2,002,000 loose edges 2.20 /
  2.21 ms (GN 18.27 / 18.40); the bundled male body subdivided (200,002 vertices) 0.64 / 0.61 ms (GN 4.18 / 4.13);
  smoothing 4 passes 0.45 (n-gon), 0.94 (fan), 3.84 (wire), 1.16 (character). The old face-corner gather would have
  taken about 10^10 steps for the 100,000-gon; the background suite fails on superlinear growth.

.. list-table:: Playback, frames per second per run (window 2294 x 1408)
   :header-rows: 1

   * - Case
     - main
     - before
     - after
   * - Nothing measured
     - 84.8, 84.7, 84.1 (fourth run never valid)
     -
     - 84.4, 84.0, 84.1, 84.4
   * - Measuring, map hidden
     - GN workaround: 36.5, 35.5, 36.5
     - 67.0, 66.4, 67.3
     - 82.3, 83.1, 82.2
   * - Measuring, map shown
     -
     - 55.1, 55.0, 55.8
     - 82.3 (two runs never valid)

* Playback with the map shown went from 55 to 82 fps: the corner buffer is a parallel gather of the vertex values
  (the generic point-to-corner adaptation was most of the map's cost). Measuring while playing costs about 2 fps of
  84; the Geometry Nodes route plays at 36.
* Not done, by measurement or by design: rest lengths are recomputed each frame (a parallel pass, a fraction of the
  1.4 ms; a cache keyed on data pointers could go stale under in-place edits such as sculpting); a 16-bit vertex
  format would halve the corner upload, but its backend support is marked TODO in ``GPU_format.hh``; a paused
  timeline evaluates and extracts nothing (Blender re-evaluates only on change).

Validation
----------

* ``tests/python/angelo_agent/test_native_tension_map.py`` (host): the card's choices, layout and input, the add-on
  wiring and the native wiring (modifier number 120, registration, placement, overlay phases, draw cache with GPU
  subdivision, versioning).
* ``tests/python/angelo_agent/test_blender_tension_map.py`` (background): closed-form strain on stretched, squashed
  and sheared grids, shape-key and captured rests, an armature bend, placement and Rest Position, refused names,
  Cycles reading the attribute, save/reload, overlay defaults, the agent's generic settings.
* ``tests/python/angelo_agent/test_blender_tension_map_gui.py`` (GUI, Director): the + menu's operator on a posed
  base-mesh character, the card, a range click, a new pose updating live, Material Preview and EEVEE, a simulated
  cloth, Remove and one Undo, with screenshots.

Limits
------

* The modifier number is 120, kept clear of upstream's sequential numbers; stock Blender opening an Angelo file drops
  the modifier (unknown type), as it does any modifier it does not know.
* The edit-mode BMesh is not measured unless the modifier is shown in Edit Mode. Shown there, the Original rest
  without ``rest_position`` is the edit mesh's positions (the active shape key's while one is edited), and a Shape
  Key rest reads the key as last stored.
* Remove is one Undo step for the modifiers; the overlay switch is a view setting and stays off after Undo, like
  Blender's own overlays.
* Sculpt Mode draws its own surface; the map shows there only when the sculpt display draws the evaluated mesh.
