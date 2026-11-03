.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

=====================
The game-ready check
=====================

``angelo_game_ready_check`` (toolset 48, Angelo 1.0.9) is a read-only agent tool that measures a mesh against its
reductions: LOD objects someone made, and reductions it computes in memory with meshoptimizer and with Blender's
Decimate on the same input. It returns facts, never a pass or fail. It is the instrument the later game-ready slices
(collision, lightmap UVs, LOD chains, the ``GAME_READY`` job, batch work) are judged by, so it is built to be precise
and cheap, and every fact says what it measured.

Owners: ``game_ready_spec.py`` (schema and arguments), ``game_ready_host.py`` (the host route),
``game_ready_mesh.py`` (reading objects, welding, reductions), ``game_ready_metrics.py`` (the measurements),
``game_ready_collider.py`` (the collider facts) and the
native module ``_bpy._angelo_reduce`` (``source/blender/python/intern/bpy_angelo_reduce*.cc`` and
``bpy_angelo_meshopt.cc``).

.. contents::
   :local:

The tool
========

A deferred tool (``plan_kinds.DEFERRED_TOOLS``): a turn that never prepares a game asset sends only its name. It is
at the external apps' Read level (``external_clients.READ_TOOLS``, also headless) and needs no card.

=========================  ==========================================================================================
Argument                   Meaning
=========================  ==========================================================================================
``object`` (required)      The mesh: LOD0, the source every other mesh is measured against.
``lods``                   Up to 6 other mesh objects to measure as LODs (hand-made, or made earlier by the agent).
``simplify``               Reductions made in memory: ``ratios`` (1-6, each a fraction of the source's triangles,
                           run largest first), ``methods`` (``MESHOPT`` and ``DECIMATE``, both by default),
                           ``max_error`` (meshoptimizer's relative error cap; default none, so the ratio is reached),
                           ``uv_weight`` (1), ``normal_weight`` (0.5), ``skin_weight`` (1) and ``lock_border``.
``switch_distances``       One distance in meters per LOD (the ``lods`` in order, then each ratio), for pixel error.
``resolution``             Screen height in pixels for pixel error (1080).
``fov_degrees``            Vertical field of view for pixel error (60).
``texture_size``           Texture width for texel density (1024).
``collider``               A mesh object to measure as the source's collider.
=========================  ==========================================================================================

The result has ``source`` (its triangles, vertices, bounds, UV and topology facts, how it was read), one entry in
``lods`` per LOD object and per reduction, ``lod0_vertex_buffer`` (the welded vertex count every meshopt LOD indexes),
``refused`` (a method that could not run, with the reason), ``collider``, ``meshoptimizer`` (whether the library is
usable, and why not) and ``native_ms`` (time per phase).

What each fact measures
=======================

Everything is in world space, in meters when the scene has a unit system (its unit scale applied), otherwise in
Blender units. Each block carries ``measured``: its method in one sentence, because the agent reads the number
without seeing how it was made.

* **Triangles**: ``tris`` and ``of_source_tris``. A meshopt LOD also gives ``vertices_used`` of the shared buffer.
* **Hausdorff**: the nearest-surface distance from every vertex and 32,768 area-weighted points of the LOD to the
  source, and the same from the source to the LOD. ``max_m`` is the largest sampled distance either way, a lower
  bound of the true Hausdorff distance; ``mean_m`` is over the area-weighted points both ways; ``max_of_bounds``
  divides by the source's bounding-box diagonal. Both directions count: a LOD that dropped a part is close to the
  source from its own side but far from the source's side (``source_to_lod_max_m``). Points are drawn with a fixed
  seed, so the same meshes give the same facts.
* **Silhouette**: pixel-centre coverage of the source and the LOD in 7 orthographic views at 256 px, framed on the
  source's bounding sphere: the 3 axes and the 4 cube diagonals. An orthographic view and its opposite see the same
  outline mirrored, so the research's 8 diagonal views were 4 distinct ones; these 7 cover them and the front, side
  and top. IoU is shared pixels over pixels either covers; ``iou_min`` is the worst view and ``worst_view`` the
  direction looked along. A LOD whose bounding box reaches outside that sphere is compared in views framing the
  sphere around both meshes' boxes, the source drawn again in them (coarser pixels for that LOD only): views framed on
  the source alone clipped away whatever the LOD had beyond it, so a LOD with a detached extra part read 1.0 (found
  in review of the check's first version).
* **Pixel error**: the Hausdorff max in pixels at each switch distance on a ``resolution`` px view with
  ``fov_degrees``: ``px = error × resolution / (2 tan(fov/2) × distance)``. ``distance_at_1px_m`` is where it falls
  to one pixel, which needs no distance. This is the quantity the engines' screen-size thresholds act on.
* **Normals**: the angle between the shading normal at each sample point and the other mesh's at its nearest point,
  both interpolated from corner normals (p50, p95, max). The attribute facts read both ways, like Hausdorff: the
  source's sample points against the LOD and the LOD's against the source, so a shell or part only the LOD has (a
  flipped copy a millimetre off the surface, which Hausdorff and the silhouette barely see) is measured too.
* **UV shift**: the UV distance between the UV at a sample point and the other mesh's at its nearest point. A point
  next to a seam can read the other side's UVs, so p95 is the figure to trust over max.
* **Skin weights**: for a skinned mesh, half the summed absolute difference of the deforming groups' weights at a
  sample point and the other mesh's nearest point (0 the same influences, 1 none shared). Groups are matched by name
  over both meshes' groups (one a mesh lacks weighs 0 there), never by their order.
* **UV facts** (source and every LOD): ``seam_length_m`` and ``seam_edges`` (edges whose sides have different UVs;
  a welded buffer's split vertices count as the vertex they came from), ``stretch`` (the larger over the smaller
  singular value of each triangle's surface-to-UV map, area-weighted: the UV checker's stretch, ``uv_checker.rst``),
  ``texel_density`` (``texture_size × sqrt(UV area / surface area)``, area-weighted, and its coefficient of variation
  as ``spread``: the UV checker's density), ``overlap_of_used_area`` (pixel centres under two or more UV triangles
  over those under any, on a 1024 px grid over the UV bounds, shared edges counted once), ``mirrored_tris``
  (clockwise UVs; legitimate for mirrored islands, wrong for lightmaps) and ``collapsed_tris``.
* **Topology**: open edges, non-manifold edges and zero-area triangles.
* **Collider**: whether it is closed and convex (each face plane tested either way round, as engines rebuild convex
  hulls whatever the winding) and whether its faces are wound consistently, its volume over the source's convex hull
  (``excess_over_hull``; no volume for faces wound both ways on a non-convex shape), the two-sided Hausdorff from the
  hull to the collider, and the share of source points it leaves outside with the farthest of them. The inside test
  is the half-spaces of a convex collider's faces, or the generalized winding number for a non-convex one, capped at
  16.8 million point-triangle pairs: past that ``inside_test`` says it was not measured and why. The hull comes from
  ``bmesh.ops.convex_hull``, which needs Bullet: a build without it (the development preset) reports ``hull`` as not
  measured and gives the rest.
* **Empty and apart**: a LOD with no triangles reads ``empty`` and nothing else. ``apart`` marks a LOD whose bounding
  box does not overlap the source's: everything compares objects where they are, so a LOD placed beside its source
  reads far from it. ``source.modifiers_not_applied`` counts the modifiers skipped on an armature-deformed mesh.
* **Meshoptimizer's own error** (``meshopt_error``): its quadric estimate relative to the mesh extent, reported beside
  the measured Hausdorff so the two can be compared.

Reading the meshes
------------------

A mesh is read as an export takes it: the evaluated mesh, every enabled modifier applied (``to_mesh``, freed after the
call). A mesh an enabled Armature modifier deforms is read as its base mesh in rest pose instead, with the weights of
the groups that the armature's deforming bones drive; ``source.geometry`` says which, and its other enabled
modifiers are not applied (``modifiers_not_applied``: a Mirror before the Armature leaves half the mesh). Up to
2,000,000 triangles: the measurements keep float64 copies per triangle corner on the main thread.

Reductions
==========

**meshoptimizer** (``MESHOPT``). Triangle corners are welded into the vertex buffer an engine receives: one vertex
per distinct position, UV, shading normal (Blender's corner normals, so sharp edges, flat faces and custom normals
split) and material (``_angelo_reduce.weld``, meshoptimizer's own hashing). UV seams, hard edges and material borders
are then attribute discontinuities, which meshoptimizer never collapses across (its experimental Permissive mode,
which would, is refused by the native module). ``meshopt_simplifyWithAttributes`` reduces each ratio from LOD0, with
UVs, normals and skin weights as weighted attributes, and returns an index buffer over LOD0's vertices: every LOD of
a chain shares LOD0's vertex buffer, which maps directly onto Godot's ``ImporterMesh`` LODs and Unity's
``Mesh.SetLods``. Positions never move (no ``WithUpdate``), so the LODs need no vertices of their own.

**Skinned meshes.** The weights of the deforming groups that weigh something on the mesh (a group with no weight on
it cannot move it, however many bones the rig has) are attributes beside the UV (2) and normal (3) values, so a mesh
keeps its weights in the error measure while those groups fit in meshoptimizer's 32 attributes beside the weighted
ones: 27 with a UV map and a normal weight above 0 (the defaults), 29 without a UV map, 30 with ``normal_weight`` 0. meshoptimizer takes at most 32 attributes; past
that the meshopt reduction is refused with the count and reason in ``refused``, and Decimate, which interpolates
weights itself, still runs and is measured. Shape keys play no part in either reduction.

**Blender's Decimate** (``DECIMATE``). ``_angelo_reduce.decimate`` makes the Decimate modifier's own call
(``BM_mesh_decimate_collapse``, Collapse mode, triangulated, no vertex-group weighting) on a copy of the same mesh, so
the result is the modifier's to the vertex (the native suite compares them). It moves vertices and writes new ones,
so its LODs never share LOD0's buffer, and its cost is position-only: UV seams are not part of it.

The native module
=================

``_bpy._angelo_reduce`` is created with the other ``_bpy`` modules but does nothing until called.

* ``status()``, ``attempted()``; ``simplify(positions, indices, target_index_count, target_error, *, mode, attributes,
  weights, lock, options)`` with modes ``PLAIN``, ``ATTRIBUTES``, ``UPDATE`` (moved positions and attributes come back
  as copies) and ``SLOPPY``, and the stable options ``OPTION_LOCK_BORDER``, ``OPTION_SPARSE``,
  ``OPTION_ERROR_ABSOLUTE``, ``OPTION_PRUNE`` and ``OPTION_REGULARIZE``; ``weld``; ``scale``.
* ``surface``/``nearest``: a BVH over triangles (Blender's ``BLI_kdopbvh``) and nearest points for many query points on
  all cores. ``rasterize``: triangle coverage of pixel centres, top-left rule, views in parallel.
* ``decimate`` and ``vertex_weights`` read a ``Mesh`` and nothing else.
* Arrays are C-contiguous buffers (NumPy): float32 positions, uint32 or int32 indices, uint8 locks. Each is copied
  into the module's own memory first, then every index, count and value is checked on that copy (meshoptimizer only
  asserts its preconditions, and a release build would read out of bounds), so a Python thread changing the array
  while the work runs without the GIL cannot reach a checked index. A bad argument raises ``TypeError`` or
  ``ValueError``.

**The library and its version.** The application bundles meshoptimizer 1.1 as a shared library, which Blender links
only into the glTF add-on's encode/decode bridge. A ctypes call with an older signature crashed the application in
research, so ``bpy_angelo_meshopt.cc`` types every entry point from the header it is compiled against
(``decltype``) and opens the library on first use, never at startup and never linked:

* Compile time: the header must be meshoptimizer 1.x from 1.1 (``static_assert``). Upstream promises from 1.0 that
  later 1.x releases keep every 1.0 function API, ABI and behaviour compatible, Permissive mode excepted.
* Run time: the library carries no version number (its install name is 0.0.0), so the exports first added in 1.1
  (``meshopt_decodeMeshlet``, ``meshopt_opacityMapMeasure``; either suffices) are the evidence. Both are experimental
  upstream, as is everything 1.1 added, so a later 1.x could rename them: that fails safe (the simplifier is refused
  with the reason) and the native suite's availability check catches it. A library without them, or without an entry
  point used, is refused with the reason and nothing is called; a missing library likewise. A build against a header
  other than 1.x from 1.1 (a Linux system library, or a bump) compiles without the simplifier and ``status()`` names
  the version; a Windows debug build loads ``meshoptimizer_d.dll``. The tool reports
  the reason in ``meshoptimizer`` and ``refused`` and still measures LOD objects and Decimate. A 2.x library cannot
  be told apart this way: that is the residual risk, and only for a build linked against a system library.
* Where it is looked for: only beside the executable, where each platform's build installs it (macOS
  ``Contents/Resources/lib/libmeshoptimizer.dylib``, portable Linux ``lib/libmeshoptimizer.so``, Windows
  ``blender.shared\meshoptimizer.dll`` or the executable's folder). Linux alone then asks the system linker for
  ``libmeshoptimizer.so`` by name, for a build against the system library; macOS and Windows never search by name
  (Windows would also search the current folder and PATH). **Only macOS has been run.** Windows and Linux follow those platforms' install rules
  in ``source/creator/CMakeLists.txt`` (Windows installs ``meshoptimizer.dll`` through the ``blender.shared``
  manifest; Linux bundles ``meshoptimizer/lib``) and are unverified, including whether the library is shared there.
* A build without ``WITH_MESHOPTIMIZER`` (the development preset's ``blender_lite.cmake``) has ``status()`` and the
  measuring kernels but no ``simplify``; the tool reports meshoptimizer unavailable.

Nothing runs while it is unused
===============================

``blender_tools`` imports ``game_ready_host`` inside the call; ``game_ready_mesh``, ``game_ready_metrics``,
``game_ready_collider`` and NumPy are imported by it alone, and ``game_ready_spec`` imports nothing heavy (a host test parses every add-on module
to keep it so). The library opens on the first native call that needs it. The native suite proves it in the built
app: after the add-on registered, ``_angelo_reduce.attempted()`` is false, ``libmeshoptimizer`` is not among the
process's loaded images, and none of the four modules is imported. Startup adds one ``_bpy`` submodule object.

Measurements
============

Measured on 2026-11-03 on Mac 1 (Apple M4 Pro), in the development build of this change (commit 2f5a64744c5, macOS
arm64, ``blender_lite.cmake`` plus meshoptimizer and Bullet; its build information reports the base ef6a846b7a68),
with ``bench_game_ready_check.py``. That build is not the shipping configuration, and the machine was never quiet
(1-minute load 7 to 19), so no time is stated below; the quality facts do not depend on load (the samples are
seeded).

**Cases.** Development meshes, used while building: a subdivided Suzanne (1,007,616 triangles) and a UV sphere
(16,128). Held out, never used in development, chosen by rules fixed before any run: the 12 most downloaded Poly
Haven models up to 500k polygons plus its 2 most downloaded rigged ones (CC0, polyhaven.com/license; geometry only),
and KayKit by Kay Lousberg (CC0 per each pack's LICENSE.txt): every Adventurers character (skinned) and every 8th
Furniture Bits and Dungeon Remastered model. Every mesh object with at least 500 triangles is a case: 159 held-out
cases (508 to 65,782 triangles, median 5,264), all measured without an error. The Jacaranda tree was cut off by the
45-minute run limit both times (this build's Decimate, below), and its cases are not counted. Kenney and Quaternius
kits could not be downloaded unattended (a signed-link step and an itch.io click-through), and Smithsonian's 3D API
does not state each file's rights, so none is included.

**meshoptimizer against Decimate, held-out cases** (each ratio of the source's triangles; meshopt better / worse
over 159 cases, then the medians, meshopt vs Decimate):

=======  ===============================  ===============================  ===============================
Ratio    Hausdorff max, of bounds          Normals p95 (degrees)            UV shift p95 (UV units)
=======  ===============================  ===============================  ===============================
0.5      43 / 116; 0.0012 vs 0.0016       149 / 6; 0.65 vs 11.9            132 / 26; 0.0011 vs 0.0114
0.25     25 / 134; 0.0043 vs 0.0025       136 / 23; 1.9 vs 22.8            132 / 27; 0.0021 vs 0.0180
0.1      26 / 133; 0.016 vs 0.0040        106 / 53; 22.8 vs 37.9           127 / 32; 0.0071 vs 0.0230
=======  ===============================  ===============================  ===============================

These figures predate two changes and were not measured again: normals, UV shift and skin weights then read the
source's sample points only (they now read both ways), and the silhouette views were framed on the source alone (a
reduction that reaches outside the source's bounding sphere is now framed on both). A reduction drawn from the
source's own surface without extra parts should read much the same, but that is not measured.

Worst silhouette IoU follows Hausdorff (medians 0.997 vs 0.998 at 0.5, 0.971 vs 0.979 at 0.1). On the 18 skinned
KayKit parts (every one supported: the rig has 41 bones, but no part has more than 27 groups that weigh on it), skin
weight change p95 medians are 0.040 vs 0.126 at 0.5, 0.076 vs 0.200 at 0.25 and 0.30 vs 0.25 at 0.1. Read together:
index-only meshopt keeps shading and texturing far better, and Decimate, which moves vertices, keeps the shape
closer at strong reductions. That trade is why the check reports both and decides nothing.

**Time: not measured.** Every run above was on a loaded machine (1-minute load 7 to 19), so no time is stated.
To measure on a quiet machine with ``bench_game_ready_check.py``: meshopt per LOD and the weld, Decimate on the same
inputs in a build with the shipping configuration (the development preset's Decimate is far slower than the shipped
app's, the stock modifier as much as this tool's call, so its times are no comparison), and the whole check's time
by phase (``native_ms``). A profile of the check on the 1M-triangle mesh put UV facts first (most of it a two-key edge
sort run twice per mesh); edges are now grouped once per mesh with one int64 key, and bounds are cached; the gain is
not measured either.

**Unused.** The idle proof is the native suite's (above): nothing of the check loads until it is called; natively,
startup adds one ``_bpy`` module object that opens nothing. What a session pays on the add-on side, measured
2026-11-03 06:56 on Mac 1 at a 1-minute load of 6.0 to 7.4, as 40 interleaved pairs (each pair runs base and branch
back to back in fresh host Python 3.13 processes, the order alternating) of branch cf2c9e6835e against base
origin/main 18e19b0f4db: importing the tool registry, paired difference (branch minus base) median +0.21 ms
(interquartile range -0.58 to +1.34 ms; branch slower in 22 of 40) on a base median of 32.2 ms; building every
always-sent schema (done when a chat session or Codex thread starts, not per message), median +1.04 ms (-1.14 to
+2.55 ms; 25 of 40) on 74.5 ms. Both differences are inside their spread. The bytes every request carries are in
``agent_integration.rst``, "Toolset 48". Refusals: an app copy without the library, and one with a stand-in library
lacking 1.1's exports whose functions abort if called, both refuse the meshopt method with the reason and still
measure LOD objects and Decimate.

Tests
=====

* Host: ``test_native_game_ready_check`` (arguments, registration, the lazy import, and every measurement on meshes
  with known answers against a brute-force NumPy reference of the native kernels); ``test_native_game_ready_framing``
  (comparisons cover everything either mesh has: extra or missing parts, larger copies and a LOD-only shell, each
  also with the roles swapped, and identical meshes still a perfect match).
* Native (BACKGROUND): ``test_blender_game_ready_check`` (the idle proof, the library check, refusals without a
  crash, native kernels equal to the reference, the weld, seams kept, Decimate equal to the modifier, every fact
  through the host route with the scene unchanged, skinned meshes).
* Benchmark: ``tests/python/angelo_agent/bench_game_ready_check.py`` (alone, on a quiet machine).

Not yet
=======

Collision generation, lightmap UVs, writing LOD chains into the scene or files, the ``GAME_READY`` job and batch work
are later slices. They will report through these facts.
