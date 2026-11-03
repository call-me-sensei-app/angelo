.. SPDX-FileCopyrightText: 2026 Angelo Contributors
.. SPDX-License-Identifier: GPL-2.0-or-later

##################
Voxel remesh guard
##################

`Blender #77868 <https://projects.blender.org/blender/blender/issues/77868>`_: a voxel size that
is small for the object makes OpenVDB and the new mesh need more memory than the computer has free,
and Blender freezes, swaps for minutes or is killed. It is one of the most reported sculpting
failures (about two dozen duplicate reports since 2020); a big imported object with a new Remesh
modifier hangs before the size can be changed. Upstream (through 5.3) catches only a zero size and
OpenVDB's arithmetic error. Angelo checks every voxel remesh against one estimate before it starts.

Contract
========

* ``bke::voxel_remesh_estimate`` (``BKE_mesh_remesh_voxel_estimate.hh``): one parallel pass over
  the triangles the remesher receives (``Mesh::corner_tris``, cached, so the remesh reuses them).
  Output vertices = Σ(|Ax| + |Ay| + |Az|) / size², where A is a triangle's vector area: marching
  cubes crosses the surface once per voxel column on each axis plane. Each triangle counts
  positive, because a face's own vector area cancels across a non-planar, crossed or folded face (a
  saddle quad reads as its flat shadow, a crossed quad as zero) while the level set follows every
  triangle. Overlapping parts and internal faces are all counted, so the estimate is high for
  them, never low. Peak
  memory = fixed + vertices × per-vertex cost (more with Preserve Volume/Attributes, much more with
  Fix Poles) + source triangles × cost. Mesh coordinates, as the remesh itself.
* Free memory is what the system reports available (``BLI_system_memory_available_bytes``: macOS
  physical less app, wired and compressed memory, as Activity Monitor counts it; Linux
  MemAvailable capped by a cgroup v2 limit; Windows available physical memory), less a reserve for
  the system and Blender (the larger of 1 GiB and 10% of physical memory), less what remeshes
  running at the same time reserved.
* Sizes:

  =========== ============================================================== =====================
  Size        When                                                           Result
  =========== ============================================================== =====================
  Fits        otherwise                                                      runs, no question
  Large       > max(10 million, current vertices), or > half the free memory interactive remesh asks
  TooLarge    > the free memory, > ¾ of the 32-bit corner index, non-finite  refused everywhere
  =========== ============================================================== =====================

  Beyond the free memory a remesh can only swap (OpenVDB's access pattern makes that a freeze) or
  be killed, so there is no "run anyway". The suggested size is offered instead.
* Concurrency: ``voxel_remesh_reserve`` holds the estimate's peak (an atomic check against the free
  memory the estimate saw) for the whole OpenVDB run in both ``BKE_mesh_remesh_voxel`` overloads.
  Modifiers of several objects evaluated in parallel, the viewport and a render cannot each count
  the same free memory; one that does not fit beside the others is refused with the same message.
* Where it is checked: both kernel overloads (base costs, with the reservation), so every caller is
  covered; ``OBJECT_OT_voxel_remesh`` exec with the mesh's own extras (reports the refusal: Python,
  the agent, Sculpt Mode); its invoke (Sculpt Mode's Ctrl+R, the Remesh buttons) asks on Large
  (default button Remesh) and, on TooLarge, offers the suggested size (default button Cancel), or
  reports that no size fits; the Remesh modifier shows the refusal as its error and passes the
  mesh through.
* The suggested size is the smallest that runs without asking, rounded up to two significant
  digits; it follows the surface, so it scales with the object. Accepting it writes it to the
  mesh's Voxel Size after the remesh succeeded, in its Undo step (the operator's hidden
  ``voxel_size``). Repeat Last ignores an accepted size, which belonged to another remesh. Nothing
  is written when a remesh is refused. In Sculpt Mode, Undo restores the geometry but not the Voxel
  Size, as for Edit Voxel Size (Shift+R).
* A new Remesh modifier (``modifier_add``: the Add Modifier menu and ``modifiers.new``, so the
  agent's typed path and scripts too) starts at Blender's 0.1, unless 0.1 would make fewer vertices
  than Blender's default cube gets (2,400: a small object would vanish) or more than a million
  (about half a second per evaluation, on every edit): then the size that makes that many
  (``bke::voxel_remesh_default_size``). Copied, linked and file-loaded modifiers keep their size;
  a mesh's own Voxel Size keeps Blender's default, and the dialogs offer a size when it matters.
* ``Mesh.angelo_voxel_remesh_estimate(voxel_size, use_mesh_settings=True)`` returns the vertices,
  peak and free bytes, ``FITS``/``LARGE``/``TOO_LARGE`` and the suggested size;
  ``Mesh.angelo_voxel_remesh_default_size()`` the new-modifier size. Read-only; for scripts, tests
  and agents choosing a size.
* Messages are whole translated sentences (``RPT_``/``IFACE_``, formatted to the translation's
  length) naming the size, the estimate, the memory needed and the memory free.
* Not covered (separate features with their own resolution settings): the Remesh modifier's octree
  modes, Geometry Nodes' Mesh to Volume and Mesh to SDF Grid, and the Mesh to Volume modifier.

Agents
======

The agent's typed path refuses a Remesh modifier whose bounding-box surface estimate exceeds its
evaluated-topology budget (``action_budget.py``) before this guard is reached; this guard adds the
memory check for everything else (scripts, reviewed Python) and its refusal is an ordinary
operator error or modifier error the agent reads. An agent that adds a Remesh modifier without a
voxel size now gets the default above instead of 0.1. No tool schema or ``TOOLSET_VERSION`` change.

Measurements
============

Measured on Mac 2 (M5 Max, 128 GB, macOS), Angelo 1.0.5 (Blender 5.2.2; the remesh kernel is
unchanged by this guard), background processes, niced; 127 remeshes, one per process, none
failed. Shapes: cube, UV sphere, dense sphere (259,200 faces), torus, subdivided Suzanne (open),
thin plate, 1,728 scattered cubes, open sheet, open 1 km terrain; 0.25 to 60 million estimated
vertices; operator (Preserve Volume and Attributes on, as default), operator with Fix Poles,
operator with neither, and the Remesh modifier. Memory is the growth of the process's peak during
the remesh, the larger of resident memory and the macOS physical footprint (they differ: the
footprint was about half the resident growth at 1 million vertices, 1.2x it at 24 million with Fix
Poles). Times are contended (the machine was in use) and are not part of any bound.

========================================= ==================================================
What                                      Result
========================================= ==================================================
Output / estimate, closed shapes          0.98-1.01 (Fix Poles 0.87-0.88: it merges poles)
Output / estimate, scattered cubes        1.00-1.13, up to 1.30 at 0.25 million (Preserve
                                          Volume inflates small parts at coarse sizes)
Output / estimate, open shapes            sheet 1.00, terrain 0.74, Suzanne 0.48-0.83 (with
                                          the open-surface doubling; without it 2.0, 1.48,
                                          1.65)
Peak per output vertex, operator          410-490 bytes at 4 million and above
Peak per output vertex, modifier          300-360 bytes
Peak per output vertex, Fix Poles         1,080-1,500 bytes
Fixed part                                about 0.2-0.3 GB
Largest runs                              60 million: 21.0 GB (operator), 15.9 GB
                                          (modifier); 48 million sphere: 19.8 GB
Measured peak / modelled peak             at most 0.79 over all 127 runs
========================================= ==================================================

The modelled costs (``mesh_remesh_voxel_estimate.cc``) are the largest per-vertex figures plus a
margin: 520 bytes base, +160 with Preserve Volume or Attributes, +1,250 with Fix Poles, 256 MiB
fixed, 200 bytes per source triangle. Linux and Windows run the same kernel and allocator
pattern; their figures were not measured, which the margin and the free-memory reserve cover.
The data and scripts are in ``tests/python/angelo_agent/measurements/voxel_remesh``.

Blender 5.3
===========

5.3 changes only includes and the BVH call in ``mesh_remesh_voxel.cc`` and includes in the
operator and modifier files; the guard lives in two new files and small hooks, so the port is a
replay. Open upstream PR #165518 adds a similar operator-only dialog (sampled estimate,
10 million vertices); Angelo's covers the modifier and scripts too and bounds memory. #158424
proposes a relative modifier default.

Upstream
========

As of 2026-11-03, #77868 is open, and neither Blender 5.3 nor upstream ``main`` (the 5.4 alpha) has
a count or memory check: 5.3's changes to ``mesh_remesh_voxel.cc`` and ``object_remesh.cc`` are the
Embree triangle BVH (``048d17e6dda``) and the ``.hh`` header rename (``e240930f046``), so ``BLI_system.h``
becomes ``BLI_system.hh`` at the 5.3 port. Upstream PR #165518 (open, a reworked #138960) estimates
the same way, Σ face area × (|nx| + |ny| + |nz|) / size², from a random subset of faces, and shows a
warning dialog in the operator above 10 million estimated vertices. This guard uses the same formula
(on every triangle, so non-planar faces cannot cancel, in one parallel pass) and the same 10 million threshold for its "Large" question, so the
two can be reconciled if upstream lands; it adds what the PR does not: the memory model, the refusal
on every path (modifier and Python too), the concurrent-remesh reservation and the suggested size.

Validation
==========

* Host: ``test_native_voxel_remesh_guard.py`` (every entry checks before OpenVDB, the reservation,
  the bounds, nothing written before refusals, Repeat Last, the dialog defaults, free memory, the
  default size, and the interface text in all 20 catalogues).
* Background: ``test_blender_voxel_remesh_guard.py`` (estimate against the real output on held-out
  shapes: a rotated cube, a sphere, a subdivided head, an open sheet, overlapping parts,
  adaptivity, saddle (non-planar) quads, crossed quads and a star prism whose concave caps are
  non-planar; the estimate's area equals the triangles' own, computed in Python; refusals from a script, Sculpt Mode and the modifier, immediate and writing nothing;
  non-finite positions; the offered size fits; an accepted size runs; the new-modifier size at four
  scales; zero keeps Blender's message).
* GUI: ``test_blender_voxel_remesh_guard_gui.py`` (Sculpt Mode's invoke: the refusal dialog's
  Return cancels; a size the estimate calls large on that computer: Escape keeps, Return remeshes
  near the estimate; a fitting size asks nothing; screenshots checked by eye).
