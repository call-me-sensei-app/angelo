.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

##########
Retopology
##########

Clean quad topology made from any mesh: a sculpt, a scan, an AI-generated model. The result is all quads, its edge
loops follow the shape's curvature, its sharp edges and the edges the person marked, it lands close to a face count
the person asks for, and it can be exactly symmetric. It is the step after generation that every generated model
needs, and the step the "Make game-ready" job calls before unwrapping and baking.

Angelo 2.1.0. Status: built from published research, measured on the unit suite and once on the held-out benchmark
against QuadriFlow and voxel remesh (see `Measurement`_); times are not yet measured on a quiet machine.

.. contents::
   :local:

Where to find it
================

* **Properties > Mesh > Remesh > Retopology** (under QuadriFlow): ``object.retopology`` on the active mesh in Object Mode. It asks for the face count and options, then runs as a background job with progress and
  Cancel in the status bar; the interface is locked meanwhile, as for QuadriFlow. One Undo step, pushed when the
  result is in place, however the job was started (this button, the chat bar's + menu, the agent's job).
* **The agent**: ``angelo_job_start`` with ``kind: RETOPOLOGY`` (`The agent's job`_).
* **Scripts**: ``bpy.ops.object.retopology(target_faces=8000)`` runs at once and returns when done; as with any
  operator a script runs, the script's caller owns the Undo step.

By default the result is a new object, ``<name>_retopo``, with the source's parent and transform, selected and active;
the source is not touched. **Keep Original** off replaces the mesh in place, as QuadriFlow does.

Options
-------

==========================  ======================================================================================
Option                      Meaning
==========================  ======================================================================================
Faces (``target_faces``)    About how many faces the result has, both sides of a symmetric result together.
Use Mesh Symmetry           Mirror across the mesh's own symmetry axes (Mesh > Symmetry, as in Sculpt Mode). When
                            off, ``symmetry`` names the axes. Planes pass through the object's origin.
Sharp Angle                 Edges where faces meet at more than this angle (35 degrees) become edges of the result.
                            0 follows only marked edges.
Follow Marked Edges         Edges marked Sharp, Seam or Crease become edges of the result, whatever their angle.
Follow Curvature            How strongly the flow turns to the directions the surface curves in (1; 0 gives the
                            smoothest flow).
Only Quads                  Every face a quad (on). Off keeps the extraction's few triangles and pentagons.
Preserve Attributes         Vertex groups, colours and other attributes carried over from the nearest surface
                            (Blender's remesh reprojection), so a rigged character keeps its weights (on).
Smooth Normals              Shade the result smooth.
Keep Original               Write a new object (on) or replace the mesh.
==========================  ======================================================================================

The result's facts are written on it as the hidden custom property ``_angelo_retopology`` (faces, quad ratio,
vertices, irregular vertices, poles of valence 3, 5 and 6 or more, boundary vertices, deviation, shells kept and left
out, feature edges, milliseconds; and where the time went: ``field_solves``, ``milliseconds_fields``,
``milliseconds_hierarchy`` and ``milliseconds_orientation``), and the report line says the main ones.

How it works
============

Owners: ``source/blender/geometry/intern/retopology`` (the core), ``GEO_mesh_retopology.hh`` and
``intern/mesh_retopology.cc`` (the ``Mesh`` interface), ``editors/object/object_retopology.cc`` (the operator).

The core uses only the C++ standard library, so its unit tests
(``source/blender/geometry/tests/retopo_core_test.cc``) build and run without Blender. It is deterministic: the
same mesh and options give the same result on any number of cores (every parallel loop writes only its own
elements; the Gauss-Seidel passes run over a graph colouring).

Methods, from published research (no add-on's code was read; Instant Meshes' code, BSD-3, was licence-checked and not
copied):

* W. Jakob, M. Tarini, D. Panozzo, O. Sorkine-Hornung, *Instant Field-Aligned Meshes*, ACM TOG 34(6), 2015: the
  orientation and position fields and the extraction.
* D. Cohen-Steiner, J.-M. Morvan, *Restricted Delaunay triangulations and normal cycle*, SoCG 2003: the curvature
  tensor.
* Y. Zheng, H. Fu, O. K.-C. Au, C.-L. Tai, *Bilateral Normal Filtering for Mesh Denoising*, TVCG 2011: the filtered
  normals the sharp edges are found on.
* J. Huang, Y. Zhou, M. Niessner, J. R. Shewchuk, L. J. Guibas, *QuadriFlow: A Scalable and Robust Method for
  Quadrangulation*, SGP 2018: integer-consistent lattice steps (written from the paper; no code read).

The stages, in order (``retopo_remesh.cc``):

1. **Intake** (``retopo_intake.cc``). AI generators and scanners deliver triangle soups, flipped faces, fins,
   duplicates, loose specks and holes; the intake decides from each mesh's own measured topology, never from a name
   or size. Vertices within a millionth of the bounding-box diagonal are welded; triangles without area and duplicates
   are dropped; the winding is made consistent across manifold edges, and a patch without borders faces outwards.
   *Flaps* (open patches hanging off a non-manifold edge, smaller than one output face) are left out. Shells are
   counted, and those below ``min_shell_area_fraction`` (1e-4) of the whole area are left out. Everything dropped is
   counted in the result.
2. **Symmetry cut**: the mesh is cut at each mirror plane; the cut lies exactly on the plane and is a border.
3. **Features** (``retopo_features.cc``): borders, non-manifold edges, marked edges, and edges sharper than the Sharp
   Angle on bilaterally filtered face normals. A chain of sharp edges shorter than one output edge is dropped: the
   result cannot draw it, and on a scan it is noise. Vertices where feature lines meet or turn by more than 40 degrees
   are corners.
4. **Refinement**: edges longer than 0.35 lattice steps are split (up to 24 million working triangles), so a coarse
   input still resolves the output lattice.
5. **Fields** (``retopo_hierarchy.cc``, ``retopo_field.cc``): a multi-resolution graph of the vertices (neighbour
   pairs merged, most similar normals and areas first). The orientation field (four directions per vertex) is smoothed
   coarse to fine, held along feature lines and guides, and pulled towards the principal curvature direction measured
   at the output scale (a tensor summed up the hierarchy to the level whose cells match the output faces), weighted by
   how anisotropic and how curved the surface is there: a flat or round region has no preferred direction. The
   position field (a lattice per vertex) is smoothed the same way, with a lattice line through every feature line and
   a lattice point on every corner.
6. **Extraction** (``retopo_extract.cc``, ``retopo_integer.cc``, ``retopo_graph_faces.cc``): every surface edge
   carries the integer lattice step between its ends' lattice points. Around a triangle the steps must sum to zero; a
   triangle where they don't is a position singularity, cancelled against an opposite one (or moved out through a
   border or into an orientation singularity) along the cheapest path of triangles, changing steps only to join or
   split lattice points, and the position field is smoothed again with the steps fixed. Edges with no step are
   collapsed into one vertex; edges of one step become output edges; vertices on the same lattice point facing the
   same way are merged. Faces are the cycles around each vertex in angular order: counter-clockwise ones whose middle
   lies on the surface are faces, the rest are borders. On parts curved tighter than about one face (fingers, ears,
   thin tubes) a quad's middle sits inside the surface by its sag, so smooth cycles are allowed that sag; cycles
   around a thin tube (normals spread past 120 degrees) are refused, and cycles on a hard feature keep the plain test.
7. **Pole-pair cleanup** (``retopo_cleanup.cc``, ``retopo_cleanup_split.cc``): what is left of a singularity extracts
   as a dislocation, a triangle (or pentagon) beside a valence-5 (or 3) vertex where a half row ends. Each pair of
   them, or one and a border, is cancelled either by removing the half row between them (collapsing its quad strip)
   or by inserting the half row that completes it the other way round (splitting the edge line from a triangle's apex,
   or the strip from a pentagon). Edits are tried shortest first, except that while the mesh has fewer faces than
   asked for, edits that remove faces wait until the rest were tried (and the other way round): where rows change
   length (a torus's outer equator is twice its inner one), removing every half row would leave each ring with the
   shortest ring's count. An edit is kept only when it lowers the irregularity and leaves the mesh manifold.
8. **Spacing correction** (``retopo_remesh.cc``): area the lattice cannot represent yields no faces (pores and
   cavities below the output scale, parts thinner than a face, a half row cancelled by removal). When the finished
   mesh would miss the face target by more than 10%, the spacing is corrected (faces measured as spacing to the power
   k, k from the previous round when there is one, within 0.35 to 1.4 times the first spacing) and the fields are
   solved again, at most twice. A re-solve whose refinement splits no edge reuses the hierarchy (only its curvature
   depends on the spacing). The result reports ``field_solves``.
9. **Finish** (``retopo_finish.cc``): every face is split once into quads (the extraction ran at twice the edge
   length), vertices are projected onto the surface (feature vertices onto their lines, corners stay), and relaxed
   tangentially on it.
10. **Mirror** and **facts** (``retopo_facts.cc``): quad ratio, valences, borders, non-manifold edges, face-count
    error, and the two-sided distance between result and source sampled at vertices and face centres, over the
    source's bounding-box diagonal. The benchmark grades with the game-ready check's instrument instead
    (``game_ready_check.rst``).

Never slower when unused: nothing loads or runs until the operator is called.

The agent's job
===============

``angelo_job_start`` kind ``RETOPOLOGY`` (``long_jobs.rst``): ``target`` (an observed mesh handle, required),
``faces`` (16 to 2,000,000, default 5,000) and ``symmetry`` (``NONE``, ``X``, ``Y``, ``Z``, ``XY``, ``XZ``, ``YZ``,
``XYZ``). Default time budget 30 minutes. "Retopologise this for animation at about 8k faces" is one call.

* Admission refuses a target that is not a mesh, is linked, is outside Object Mode, has no faces, or has more than
  20 million triangles (with the count and the advice to decimate first).
* The job always writes a new object (``job_retopology_spec.operator_arguments``: ``keep_original``), tagged with the
  job ID, and never replaces the person's mesh.
* The receipt (``job_retopology_spec.receipt``) is the new object's name and the facts measured on it: ``faces`` and
  ``target_faces``, ``quad_ratio``, ``irregular_vertices``, ``poles`` (valence 3, 5, 6+), ``boundary_vertices``,
  ``deviation_of_size`` (max and mean), ``parts`` kept and left out, and ``seconds``.

The "Make game-ready" job (not yet built; its owner is not yet assigned) is to call the same runner and read the
same receipt (faces = triangle budget / 2).

Measurement
===========

Held-out and symmetric (``coordinator-handover`` rules): a dev set used while building and a held-out set frozen
before any tuning, each with at least six meshes per class (characters, hard surface, scans, AI-generated), licence-
checked and kept out of the repository. On the same inputs, machine and grading: Angelo's Retopology, QuadriFlow
(same face target, its sharp and boundary options on) and voxel remesh (voxel size chosen for the same face count). A
refusal or crash counts as a failure. Reported per mesh and per class with spread: quad ratio, irregular vertices and
poles, face-count error, two-sided Hausdorff and silhouette IoU (game-ready check), feature recall, symmetry error,
border edges and border length of the source and of each result (a result's border length above its source's counts
holes it made), time and peak memory.

Held-out results (2026-11-18, run once: 42 runs per method on the frozen set, every run counted; a Linux VM shared
with other work, so seconds are indicative only; the grader is the game-ready check of the add-on bundled with a
2026-11-14 build, the same for every method). Medians over the runs each method finished:

==========  =========  ==============  ========  =========  ===========  =========  =====  ======  ==========  ====
Method      Finished   Face-count err  >20% off  Irregular  6+ poles/1k  Hausdorff  IoU    Recall  Open edges  s
==========  =========  ==============  ========  =========  ===========  =========  =====  ======  ==========  ====
Angelo      42 of 42   4.1%            1         8.0%       11.8         0.0346     0.950  0.660   0           6.1
QuadriFlow  22 of 42   16.2%           10        8.7%       0.0          0.0452     0.913  0.751   833         6.8
Voxel       42 of 42   1.7%            2         42.6%      25.6         0.0322     0.969  0.729   0           0.1
==========  =========  ==============  ========  =========  ===========  =========  =====  ======  ==========  ====

QuadriFlow refused 16 runs, raised an error on 3 and crashed on 1. Open: Angelo's feature recall
is below QuadriFlow's and voxel remesh's, it leaves more poles of valence six or more than QuadriFlow, and it takes
about 1.6 times its earlier time (two or three field solves); its extraction can span an opening of the input four
to six faces wide.

Tests
=====

* Core: ``source/blender/geometry/tests/retopo_core_test.cc`` (GTest; also built alone with the standard library):
  closed pure-quad output of the right genus (the sum of 4 - valence is 4 x Euler characteristic) and size on a
  sphere, a torus and several face targets; a torus nearly regular (its rows change length); clean surfaces meeting
  the face target in one solve; parts curved tighter than a face staying closed; rough scan noise making no feature
  lines and not steering the flow; structure below the output scale not costing the face target; box edges and corners kept; marked edges followed; triangle soup with
  flipped faces, a fin and a duplicate; several shells with a speck left out; scan-like noise; an open surface keeping
  its rim; coarse input refined; exact symmetry on two axes; determinism; cancellation; empty and degenerate input;
  the facts on a hand-made mesh.
* Host: ``test_native_retopology_job.py`` (the job kind's fields, bounds, operator call, receipt and wiring).

Not yet built
=============

Guided and sketch retopology (strokes, loops, patch fill, relax brush, "Fill the rest" around hand-drawn loops) and
guide curves from the interface; a card in the chat bar's + menu; density painting; the quantised patch layout.
