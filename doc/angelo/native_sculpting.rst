.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Native agent sculpting
**********************

The Coding Agent can sculpt through Blender's native brush engine rather than
MCP, OS pointer gestures, a remote screenshot loop or Python per-vertex edits.
This supplies execution and visual feedback, not a claim of autonomous
human-quality artistry. Shape judgment remains the selected model's job.

Artist-like control loop
========================

1. ``angelo_inspect_sculpt`` measures the target's surface, bounds, density,
   masks and topology, and returns an exact ``mesh_revision``.
2. ``angelo_prepare_sculpt`` selects the existing target, enters Sculpt Mode
   and optionally adds non-destructive Multires levels. It never remeshes,
   applies arbitrary modifiers or destroys UVs/vertex groups.
3. ``angelo_sculpt_view`` provides actual native Workbench pixels from 1–3
   framed angles. It leaves the user's camera/view, shading and overlays intact.
   ``angelo_sculpt_target`` maps top-left normalized image coordinates back to
   exact 3D ray hits. Its camera authority is the current chat's admitted native
   observation, not an attached photo or model-supplied camera JSON. Captured
   native depth rejects hits behind visible neighbors and non-visible surfaces;
   misses never supply a hidden brush target. Rays sample the containing pixel's
   center and report that normalized position. Camera depth fits the full target
   and potentially overlapping scene instances independently of the image crop.
   Visibility tolerance is bounded in world units relative to the pixel size;
   insufficient depth precision fails closed. A saved comparison camera that
   would clip the target or a new foreground occluder requires a fresh view.
4. ``angelo_sculpt_batch`` executes a deliberate brush pass with radius,
   direction, strength, pressure-controlled paths, front-face protection and
   optional local X/Y/Z symmetry. Supported brushes: Draw, Clay, Inflate,
   Smooth, Crease, Pinch, Grab, Flatten, Mask, Clay Strips, Draw Sharp, Scrape,
   Elastic Deform and Pose. Optional falloff, native automasking, per-point radius
   multipliers and pen tilt give explicit control over each stroke.
   Synthetic tilt is independent of the artist's viewport: tangent up is world
   +Z projected onto the supplied outward normal's plane (world +Y near the
   poles), and right is up cross normal. Tilt Y rotates around right, then tilt X
   around tangent up. Ordinary artist strokes retain viewport-driven tilt.
5. Use confirmed displacement/mask measurements and the next revision for
   subsequent passes. Inspect fresh images between meaningful stages, not
   after every dab. Provider instructions favor primary forms/silhouette first,
   secondary forms next, and detail/light smoothing last.

Implementation and performance
==============================

``SCULPT_OT_angelo_sculpt_pass`` is a non-modal native operator. It reuses
``SculptPaintStroke``, PBVH region gathering, threaded upstream brushes,
mask/hidden-face handling, native symmetry and delta-based sculpt undo. It does
not own a second sculpt deformation implementation. A transient non-main brush
prevents changes to artist brush assets, bindings, unified settings, automasking
preferences, gravity, radial symmetry or paint state. Temporary pass settings
are restored; existing sculpt masks, hidden faces and axis locks are respected.
Native preflight rejects non-finite controls before creating a brush, opening
Undo or changing geometry, for direct operator calls and every program step,
not only through the host tool schemas.

The host evenly resamples world-space path length and interpolates pressure.
One pass is one dispatch and one Undo, not one model/tool call per dab. Grab uses
its actual 3D anchored delta, not projected mouse movement. Target coordinates,
mesh fingerprints and measurements are read in bulk; unrelated scene meshes aren't
hashed for each sculpt pass. Live sculpt surface queries traverse Blender’s existing PBVH and its updated
node bounds directly, including Multires grids, hidden faces and current
deformation. They do not export/rebuild a second BVH. Ray queries prune nodes
farther than the best hit so far, and nearest-point traversal tests the
FullyHidden flag only on leaves, so an internal node's flag cannot hide visible
children.

A ray tests every triangle of each leaf it reaches (up to 2,500 faces), in the
order Blender's own PBVH ray-cast takes the leaves. A thousand rays on the
705,538-vertex statue of the dense castle scene (``performance.rst``) take about
14 times as long as ``Object.ray_cast``, which does not do the same work: it
reads the evaluated mesh, which a stroke does not update (Blender draws a
sculpt from the PBVH), which is the Multires cage in Sculpt Mode and which
includes hidden faces. Per-leaf ray indices that would close the gap are not
used: an index must answer exactly as the leaf scan does in every case first.
``angelo_sculpt_query`` refuses to run off the main thread, since it updates
the PBVH's bounds (and Multires normals) without locks. A script that moves
positions in Sculpt Mode through ``MeshVertex.co`` or ``foreach_set`` does not
tell the PBVH, so its node bounds stay stale until ``Mesh.update`` and an
evaluation, as in Blender; call both before asking for the surface. Outside
Vertex and Weight Paint the live positions do not depend on the evaluated
object, so these reads and ``angelo_sculpt_vertex`` do not look the dependency
graph up. ``tests/python/angelo_review`` holds ray-dump scripts and 27 sculpt
mutations that check a build's ray answers against a reference leaf-scan build;
the current build matches it on every one except a Draw stroke on a shape key,
whose sculpted surface is not reproducible from run to run.

Live CCG queries and snapshots share one seam/normal
canonicalization, with an empty-dirty fast path that avoids repeated grid-face
allocations. Object-mode observations
retain the bounded revision-keyed mesh BVH fallback. Public provider tool prefixes remain stable within a runtime.

``Mesh.angelo_sculpt_positions`` and ``Mesh.angelo_sculpt_topology`` expose owned,
immutable byte observations of float32 positions and int32 corner indices/face
offsets. ``Mesh.angelo_vertex_group_weights`` does the same for deform weights:
(int32 vertex, int32 group, float32 weight) records in vertex order, then each
vertex's stored weight order, which is the order ``MeshVertex.groups`` iterates.
It is empty without a deform layer, and a buffer too large for one RNA string is
a single byte, which is not a whole record, so readers fail closed. These remove
per-element RNA dispatch without lending Python a native mesh pointer. Full-content BLAKE2b-256 fingerprints cover geometry, topology,
masks, hidden faces, face sets, world transforms, modifier levels and region
metadata. Every external call still validates the actual data; no optimistic
cross-call geometry hash cache is used. The hash algorithm was selected after
profiling this bundled Python: a 2.6 MB topology buffer took about 20.25 ms with
its SHA-256 implementation versus 1.73 ms with BLAKE2b-256. The revision remains
an opaque 64-character token, not a promise that all tokens use SHA-256.

Base sculpt coordinates are newer than Blender's cached evaluated Mesh in some
contexts. Read-backs use original base geometry. ``Object.angelo_sculpt_mesh``
reads live Multires grids into an owned temporary observation, without modifier
reevaluation or pointer aliasing of the artist's SculptSession. Grid-boundary
duplicates count toward the working point budget. ``BVHTree.FromMesh`` shares
the existing native BVH construction with ``FromObject`` and queries these
observations directly; no Python vertex loops or temporary sculpt Object are
needed. Native normal updates finalize shared grid-boundary stitching before
revision hashing, so drawing doesn't invalidate a view by microscopic rounding
changes. Offscreen draws refresh base evaluated geometry once per visual
checkpoint, not once per dab. Workbench viewport image captures use live sculpt
PBVH/CCG batches; drawing the evaluated Multires coarse cage would omit the
sculpted displacement. Actual scene rendering retains render-evaluated geometry.
The offscreen color-composition quad disables and restores depth writes, preserving
the scene's actual native depth on Metal instead of replacing it with a flat 0.5.
These safeguards prevent judging an old or geometrically misleading image.

Codex gets native ``inputImage`` tool outputs; Claude gets admitted pixels in
its next bounded typed-plan verification round. Base64 images aren't saved in
the host transcript. Private PNG references and measured tool results are.
Readonly collaborators can inspect/capture/target but cannot sculpt.
Claude discards preceding visual evidence after any tool annotated as mutating,
including later plan rounds and coalesced edits. It retains up to six distinct
sculpt images by target, angle, resolution, region and framing, and recaptures
them before verification unless already refreshed after the last mutation.
Views from the general ``angelo_view`` tool share the refresh rule and a
request's total of twelve images (``agent_execution.rst``, "Visual
observation"). Reviews retain their original baseline and outline/landmark
goals. A plan exceeding either number is returned to the model before its
first operation with the number it has left, not silently truncated.
Failed captures or cancellation stop verification;
they never forward stale images or silently switch providers/billing.

Programs, regions and reference corrections
===========================================

A typed commit while objects are in Sculpt Mode adds exactly one visible Undo
step. Its exact pre-commit boundary is a hidden step whenever landing on the
active step reproduces that state: a Sculpt step of any object (the decoder
recognizes the adjacent restored baseline and never forces another window's
object into Sculpt mode), a MemFile with an exact Angelo Sculpt snapshot, such
as a restored checkpoint, or an ordinary MemFile (a property edit in Sculpt,
Dyntopo disable, an Object Mode operator in another window) that is the latest
step, while no Dyntopo session is live and every sculpted Multires mesh has its
grids in one graph. Otherwise the exact boundary stays a visible step of its
own, because exact Undo matters more than one fewer step: an ordinary MemFile
reached by Undo (it has Redo steps) does not decode to the live state Undo left,
decoding one leaves a live Dyntopo session (stock Blender 5.2 behaviour), and
grids shown by several graphs (one Object in two Scenes, or Objects sharing a
mesh) come back from their displacement data, not bit for bit.

The commit is refused before any preparation callback runs when Global Undo has
no Undo steps (``undo_steps`` 0): ``UNSUPPORTED``, ``scene_changed`` false and no
partial changes, because nothing ran that could change the scene.

``angelo_sculpt_program`` executes 1–8 different brush passes in one native
transaction with one Undo. The *whole program*, not each step, shares the
512-dab/dense-mesh work budget. Paths are resampled locally; each pass projects
onto the surface changed by the preceding pass. Named region anchors move
between steps too. Native failures after an earlier successful pass retain a
single program Undo and report partial changes through the normal host error
path; they do not discard the Undo entry by returning a cancelled transaction.
Each stroke has its own lazy original-position/normal cache, separate from the
whole-program first-touch Undo state. Anchored Grab/Elastic/Pose therefore keep
earlier passes instead of restoring program-start geometry. Mask and geometry
changes have independent storage within the same Undo/Redo step, including
Multires masks that must be recreated after a memfile restore.
Synthetic Pose also uses immutable per-iteration weights, avoiding upstream
parallel blur scheduling differences and addressing hidden-grid weights by
their global indices. Ordinary artist strokes retain their previous path.
Longer work is split into programs with visual checkpoints. Stop takes effect
between bounded programs: one native execution is admitted per shared UI timer
tick across chats, with round-robin scheduling and a short follow-up timer while
work remains. Already-running native operations remain atomic; this is not an
asynchronous brush job or per-dab viewport animation.

``angelo_sculpt_region`` stores up to 64 named vertex landmarks/regions on the
object, saved with the blend file and covered by chat approval Undo. A regional
path contains WORLD offsets from the current anchor. The region radius bounds
stroke centers; it is not a hard mask on the entire brush falloff. A positive
``face_set`` binds an existing face set and enables native face-set automasking;
zero leaves existing masks unchanged. Regions do not segment or name anatomy,
author face sets or discover face-set IDs. Topology or working-level changes
invalidate mismatched anchors instead of guessing a new surface correspondence.
Inspect lists current/stale regions. New anchors may snap only within half the
requested region radius; coarse meshes require a higher working level or an
observed vertex instead of silently editing a distant corner. A rejection
reports the measured distance and how to resolve it, without changing the
object. Legacy unvalidated anchors are stale and must be redefined. Elastic Deform and Pose use native view-normal automasking for the
front-face guard because those upstream kernels do not use the ordinary brush
front-face factor.

``angelo_sculpt_resolution`` selects an existing Multires sculpt level without
adding/deleting levels or applying modifiers. AUTO selects the lowest admitted
level estimated to have enough samples for the requested brush footprint and
FORMS, SECONDARY or DETAIL stage. The estimate uses global point counts and
object bounds, not measured density at the brush region. EXPLICIT selects an admitted level directly.
It reports insufficient existing density rather than silently adding geometry.
Higher levels and their detail remain available.

An existing Multires asset above the working budget can still be lowered:
inspection returns a clearly marked ``MULTIRES_CONTROLS_ONLY`` observation and
revision without exporting its large grids. This token permits only zero-level
preparation and selection of an existing working level. It cannot authorize
brush edits, landmarks, pixels or surface probes. After lowering the working
level, a fresh full-content observation is required for geometry operations.

Views can reuse an admitted ``framing_reference`` camera or zoom to a named
``region``. ``angelo_sculpt_review`` reuses a baseline’s exact camera and sends
fresh pixels, target-only silhouette overlap/area and optional errors against
an explicit reference outline and named landmark pixel targets. Silhouettes
come from an additional native flat target-only draw; other objects’ visibility,
selection, the active object, display settings and the artist’s view are all
restored. The normal color/depth observation still includes visible neighbors
for correct occlusion-aware targeting. Both render paths normalize culling,
X-ray, clip planes and In Front overrides, including collection-instance
sources outside the view layer, and restore them on success or failure.

Outline targets must already be aligned/traced into this camera’s normalized
image coordinates; references are not aligned and landmarks are not detected
automatically. Landmark errors measure explicit targets, not anatomy or
artistic quality. Baseline IoU alone measures *change*, not improvement. The
outline rasterizer uses bounded scanline/ROI parity rather than testing all
edges against every pixel; the pixel-center even/odd result remains identical.
The agent compares reference overlap/error before and after edits and checks the
actual images from multiple angles. Crops reduce image payload size but do not
replace whole-object proportion checks. Camera storage is bounded LRU, so an
actively reused baseline remains available; expired/restarted cameras require
a new native observation. Codex and Claude both receive review pixels, and a
later edit invalidates and refreshes Claude’s prior review images.

Safety and current limits
=========================

* Ask-before-changes and readonly policies apply to every mutation. Automatic
  bounded edits permit validated sculpt passes, never unrestricted Python.
  Human-readable approval Details contain every ordered stroke control and
  protection setting. Expired or stopped queued/manual requests cannot execute;
  expiry is rechecked after precondition reads, immediately before mutation.
* Every request uses the existing pre-request scene checkpoint. Each pass has
  native Undo. Both native canvas approval and automatic dispatch explicitly
  enable operator Undo; Python's default operator call suppresses it.
* Failures after mutation report possible partial edits, the affected fields,
  execution stage, available lightweight state and request checkpoint. Failed
  final read-back is not presented as success. Inspect and restore the request
  snapshot or check Undo history; do not blindly retry or automatically Undo
  potentially unrelated user work. Known preflight failures do not claim edits:
  every refusal before a sculpt edit runs, in its approval binding
  (``sculpt.precondition``) or its own checks (``sculpt_preflight``), is a
  classified refusal (VALIDATION; STALE_REVISION for a changed surface;
  UNSUPPORTED for the build or a setting, such as Global Undo; a stale or
  deleted handle keeps its own code) that states ``scene_changed`` false, so it
  never holds the request's other edits. A fault there (an exception that is
  not a check's refusal) still says nothing ran, but is INTERNAL_ERROR, named
  by its type, and holds them until someone looks. Sculpt tools take an object
  by name or by scene handle, as every other tool does, resolved once inside
  the edit's preflight; approval cards name the object, never its handle.
  Malformed/non-finite result serialization retains the checkpoint, recovery
  guidance and uncertain Changes entry rather than dropping the recovery path.
* Revisions bind approvals to geometry, topology, masks, hidden faces, object
  identity, world transforms and modifier levels. Changed surfaces require new
  inspection; pixel targets also require a matching fresh camera observation.
* Observations are not incremental: every external call reads and hashes the
  complete target, and controller preconditions add further complete scans.
* Base-mesh Undo is bit-exact. Ordinary Multires CCG seam averaging can differ
  by a few float32 ULPs after Undo/Redo. Fingerprints are not relaxed for this:
  every revision hashes the full exact content, and a new observation is
  required after Undo.
* No edits or observations during an active native interaction; no cursor grab.
* Passes/programs are synchronous but bounded: 32 paths and 256 control points
  per pass, 512 maximum dabs across the whole program, with a tighter point-count
  work budget for dense meshes. Stop takes effect between programs, not inside
  a native brush operation.
* The working surface is limited to one million evaluated points. This is a
  responsiveness bound, not a limitation of Blender's sculpt engine. Use lower
  Multires working levels for heavier assets. Broad passes must use bounded
  radii; Grab displacement is limited to two brush radii per pass.
* Current bounded preparation requires a local single-user mesh, positive
  uniform local/world scale without inherited reflection or shear, no shape
  keys, no dyntopo and no visible non-Multires modifiers. Native brushes compensate
  using local scale: reflected components can reverse Add strokes, and parent
  compensation does not make non-uniform local scale safe. Both host tools and
  direct native operator/Repeat calls reject these transforms before editing.
  Unsupported preparation requires explicit review; nothing is applied or
  deleted silently.
* Adding levels budgets the actual destination from total Multires levels and
  base corner-grid counts, not the currently selected coarse level. Existing
  higher levels must be selected before adding more. Zero additional
  levels only checks the existing working-point limit, not an inflated estimate
  based on face count; dense triangulated meshes can enter Sculpt Mode unchanged.
* Adding Multires levels requires ``WITH_OPENSUBDIV=ON``. The native build helper
  enables the pinned library; a build without it rejects detail preparation
  rather than claiming a disabled modifier added useful geometry.
* Views use solid geometry, not final materials/render lighting, and can include
  visible neighbors. ``angelo_view`` provides wireframe and Material Preview
  observations, but those cameras are not sculpt targeting authority. Display
  overrides are temporarily normalized for opaque depth and restored afterward. Observation cameras/depth are runtime-only and bounded
  to twelve views (~34 MiB maximum depth and silhouette storage); after a restart or old-token
  expiry take a new view. Scene visibility, selection and artist view are not
  changed to isolate the target.

Validation
==========

No suite makes a model request, authenticates an account or opens a user's
project. Provider image forwarding and protocol behaviour are tested with local
mocked turns, not live inference.

Host suites (no Blender process):

* ``test_native_sculpt.py`` covers schemas, pressure interpolation, bounds,
  approval/readonly guards, readonly collaborator roles, image-result protocols,
  private camera authority and Claude's visual verification handoff.
* ``test_native_sculpt_workflow.py`` covers control interpolation, whole-program
  bounds, anchored-brush endpoints, region and level parameters, AUTO level
  selection and review goals.
* ``test_native_sculpt_safety_fixes.py`` checks destination budgets from actual
  grid storage and the bounded depth-precision tolerance;
  ``test_native_sculpt_metrics_fixes.py`` checks region admission and exact
  silhouette metrics, and compares the outline rasterizer with an independent
  per-pixel oracle for ordinary, degenerate, self-crossing, near-horizontal and
  randomized outlines.
* ``test_native_provider_approval_fixes.py`` covers retained sculpt evidence and
  readable approval Details; ``test_native_dispatch_fixes.py`` covers main-thread
  scheduling, expiry and durable failure recovery.

Native suites run in an isolated factory-startup Angelo GUI
(``run_native_suites.py``):

* ``test_blender_sculpt.py``: real brush deformation, masks, symmetry, pressure,
  exact one-step Undo, actual offscreen images, image-to-surface rays, Multires,
  approval and request snapshots. Snapshots are verified against genuinely
  changed live geometry, not just modifier metadata after a prior Undo.
* ``test_blender_sculpt_regressions.py``: reflected/local/inherited scale guards,
  dense zero-level preparation, occluded native pixel targeting, image resource
  cleanup and injected post-mutation read-back failures through the actual
  approval/Undo operator.
* ``test_blender_sculpt_workflow.py``: the Clay Strips, Draw Sharp, Scrape,
  Elastic Deform and Pose brushes, back-face/face-set protection, full and
  partial-program Undo, moving region anchors, bulk-byte integrity/ownership,
  exact framing/reference metrics, Multires detail preservation, dense-asset
  working-level recovery and actual chat approval Undo for programs, regions
  and working levels.
* ``test_blender_sculpt_engine_fixes.py``: per-stroke originals across program
  passes, mixed mask/geometry Undo, the synthetic tilt frame and non-finite
  input, compared on actual live base and CCG arrays through the native bpy API.
  Base-mesh Undo is checked bit for bit; Multires seam differences are measured
  against separate native Undo controls.
* ``test_blender_sculpt_safety_fixes.py``: destination budgets and view safety.
  ``test_blender_sculpt_workflow_fixes.py``: landmark placement, reproducible
  silhouettes and reference-directed sculpts that measurably improve toward an
  explicit outline/landmark target (not autonomous artistic judgment).
* ``test_blender_dispatch_fixes.py``: the shared UI timer runs 32 queued native
  programs in 32 distinct ticks, Stop after the third cancels the remaining 29,
  expired calls and approvals do not execute, and a malformed post-mutation
  result keeps its checkpoint and durable recovery record. The Changes operator
  is invoked natively; readable UI content is covered by host tests, not by
  pixel-level popup checks.
* ``test_blender_sculpt_undo_accounting.py`` and
  ``test_blender_program_sculpt_undo.py``: encoded-byte Undo accounting and
  pruning, and typed commits that keep the artist's Sculpt mode, geometry and
  individual Undo steps.

Performance
-----------

``benchmark_blender_sculpt.py`` compares one 32-dab pass with 32 individually
dispatched local edits on a dense sphere. Both use the same control
points/radius/pressure; individual calls reproject to each updated surface,
whereas the batch projects its paths to the pre-pass surface. On the
130,562-point fixture one pass took about 34 ms against about 569 ms for the
individual calls; native brush work was about 13 ms of the pass, the rest host
observation overhead. The same benchmark runs eight alternating Draw/Inflate
passes (32 dabs in all) as one program and as separate calls, both following
the changed surface between passes; their results differ by at most about
1.2e-7 in any coordinate, and the program is undone by one Undo step.

``benchmark_blender_sculpt_audit.py`` measures brush, controller and observation
costs. Run it alone, after native tests and builds have stopped, in an isolated
factory-startup GUI. Each variant gets one excluded warmup and five recorded
samples, with variant order rotated. Timings exclude model and network latency,
request snapshot capture, event-loop queue waiting and UI redraw. The controller
column includes dispatch and repeated precondition reads, with an immediate
approval callback instead of the visible approval UI and persistence. The
programs alternate Draw and Inflate passes, 32 dabs in all.

Median milliseconds on a macOS arm64 Release build (single-machine
measurements, not cross-platform guarantees):

.. list-table:: Native execution and visual review
   :header-rows: 1

   * - Working surface
     - 32-dab pass
     - 8-pass program
     - 8 separate passes
     - Controller pass
     - 3 x 768px views
     - 768px reference review
   * - Base 130,562
     - 35.379
     - 38.070
     - 159.972
     - 51.050
     - 152.512
     - 78.482
   * - Base 523,276
     - 102.689
     - 110.818
     - 574.824
     - 166.012
     - 282.986
     - 185.952
   * - Multires 49,600
     - 12.060
     - 13.003
     - 53.602
     - 18.459
     - 107.391
     - 41.627
   * - Multires 573,376
     - 84.397
     - 85.319
     - 602.544
     - 154.228
     - 241.830
     - 151.107

Rasterizing a 128-edge outline at 768 px takes a median 1.450 ms. With 32
queued programs on the 130,562-point base fixture, each dispatcher pump
executes one program and returns: 34.727 ms for the first pump and a median
35.044 ms for each later one (redraw and user interaction excluded). A review also performs a
target-only silhouette draw and hides and restores neighboring objects with
dependency-graph updates; these fixtures use a small scene, so capture scaling
across large assemblies is unmeasured.

These measurements exclude inference and network latency. They measure local
mechanisms; they are not a comparison with MCP-driven sculpting and do not
establish artistic quality.
