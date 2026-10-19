.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Local scene verification and deterministic recipes
**************************************************

Host assertions are independent of a provider's optional reasoning-round
``verify`` flag. Every staged program must run its closed host assertions and
the host's stage validity checks before commit. A model's prose, FINISHED
operator result, or fast execution is not evidence of task success. Explicit
expected checks are also what lets a Claude plan with ``verify=false`` (or no
``verify``, which means false) end with
the host's own read-back summary instead of another model round; a plan whose
edits lack checks, or whose checks fail or are inconclusive, never gets that
summary (``agent_integration.rst``, "One model round per edit").

Derived checks
==============

Many results are stated exactly by a program's own operations.
``program_spec.validate_program`` keeps the public ``expected_checks`` field
authored-only (at most 32 checks) and admits the complete host obligations
before staging. ``program_checks.resolve_obligations`` combines those checks
with final-state obligations derived from the canonical operations and, where
necessary, fresh private binding facts:
a named ``create_primitive``'s ``object_name``, its location and
any non-identity rotation or scale, an unrotated cube's or plane's dimensions,
the channels a ``SET`` ``transform`` writes, the allowlisted settings of
``modifier_add`` (Bevel, Subdivision, Solidify, Mirror, Array, Decimate,
Weighted Normal, Triangulate, Shrinkwrap, Displace, Screw, Curve), a Mirror
origin set or clear (an additional ``modifier_values`` check of exact object
identity or null), the stack position of a ``modifier_move`` and the absence of a
removed or applied modifier (one ``modifier_stack`` check per object), a named
``material_principled``'s material (present at the intended slot with proved
resource identity) and colour values,
``parent_set``'s parent, every value ``property_set`` writes (one
``property`` check per owner; later ``property_set`` operations on the same
owner merge into it), every value ``world_configure`` sets (one
``world_settings`` check, merged the same way) and every ParticleSettings path
``particle_set`` writes (one ``particle_values`` check per particle system,
merged the same way) and the known nodes, absent nodes, links and removed
edges a ``node_edit`` states. Repeated edits merge final facts by proved tree
identity. Exact qualified unlink obligations preserve endpoint-selection
semantics, including duplicate socket names; later removal of an endpoint can
make its earlier edge-absence obligation vacuous. Only equivalent complete
predicates are deduplicated, and an authored check of a derived one's values
whose tolerance is at most 0.00001, which no number's own precision is below,
stands in for it (``program_checks._keys``): an authored
check with fewer fields, different values or a looser tolerance never suppresses
a mandatory assertion. Conflicting authored constraints fail honestly. A
later operation in the same program that may change a derived property of the
same object drops that check, so the checks describe the final state; relative
transforms, Boolean operands, whole shader graphs and topology edits derive
nothing. An edit that changes an object's geometry (``derived_geometry``: an
operation whose own mutation families, what it changes or what it makes from
another object, include mesh, curve or operator data, unless its kind keeps or
states that geometry, ``GEOMETRY_KEEPS``) has effect classes (``effects``):
topology (elements added, removed or reconnected), extent (how far the
geometry reaches: a resize of everything, an extrude's displacement), shape
(where vertices or points sit within it: rotating, translating or moving some
of them, smoothing, inset depth, applied modifiers and conversions), order
(element order, face winding) and point data (a curve's or lattice's handles,
radius, tilt or weights). Each check kind observes classes (``OBSERVES``):
``mesh_counts``, ``mesh_degenerate``, ``mesh_manifold`` and ``curve_counts``
see topology; a mesh's ``dimensions`` see its extent only (bounds cannot see a
top face turned 90 degrees, every vertex moved by one offset or one vertex
moved inward); a ``curve_counts`` check with ``positions`` sees a curve's
extent and shape; nothing sees a mesh's shape, order or point data. A change is
verified only when every one of its classes is observed by some check of the
final program on that object. An extrude, bevel or inset is fully determined
by its explicit parameters on the staged mesh, so its topology is derived from
the staged result and proved live by ``commit_fidelity`` (``DERIVED_TOPOLOGY``):
an extrude certifies with ``dimensions`` alone. A bevel or inset certifies on
its derived topology, because its parameters state its offset. The known
residual: an inward extrude whose displacement stays within the bounds is
certified by dimensions it does not change. A delete, merge, join or
subdivide needs a topology check; an ``operator_run`` operator has the classes
of its family (``transform`` operators shape; a resize of every element extent,
unless a factor is negative, ``center_override`` moves the pivot or the mesh
has hidden elements, which leave them out (recorded while it runs as an
unverified effect), all of which change the shape;
smoothing shape, sorting and flipped normals order, radius and tilt point
data), an unknown operator every class, and ``keeps_geometry`` operators
(selection, hiding, flags, UV maps, custom normals and split data, vertex
groups and weights, material slots, shading, adding or locking shape keys, each
proved by the operator smoke run) none. Two writers are decided by their
parameters: a ``property_set`` of a data path that rewrites points
(``POINT_PATHS``, pinned by sweeping every settable curve, lattice and mesh
data setting in ``test_blender_geometry_measures.py``: a curve's
``data.dimensions`` flattens its points, a lattice's ``data.points_u/v/w``
regenerate them and ``data.use_outside`` moves its inner points; keying such a
path is refused), and an ``operator_run`` as above.
Checks the operations derive count: ``curve_points`` states its point and
spline counts and every point's local position (Z 0 when it makes the curve
2D), and ``transform_apply`` states the identity it gives each applied channel,
rotation included. Otherwise the edit is an explicit unverified effect
(``unchecked_geometry_edit``, with the ``unobserved`` classes and the check
kinds that would observe them): the approval says verification is partial, the
result's ``verification`` is ``partial`` and the host never certifies the
program, even when every other check passes. It reads only the
static families of most kinds (about 0.08 ms for each geometry edit, measured).
Across the 362 distinct programs the host suite validates (285 of them
resolvable without native material binding) and the 26 reference tasks of
``eval_agent_tasks.py``: all 26 reference tasks are complete; of the 285, 263
are complete and the others partial, among them an extrude checked only by
counts, a modifier apply checked only by counts and a conversion checked only
by dimensions.
Default names are not derived, because Blender suffixes a colliding
name; a requested name that is already taken fails before commit, as a
declared name check would. Derived checks pass the same strict single-check
schema and have a separate allowance. They run like authored ones: on the staged
copy, again on the prospective live scene before commit, and on Accept. They
appear in the approval, the result and the host summary ("n implied by the
operations"). Recipe arguments keep their own expansion unchanged and their
operations receive the same mandatory verification. Derived checks prove
that each operation landed as stated; a requested result the operations do not
state (dimensions after a scale, counts, relationships) still needs a declared
check. ``test_native_derived_checks.py`` covers the derivations, the drop rules,
idempotent normalization and execution.

For Mirror, ``mirror_object`` omission/null leaves the pointer untouched;
``clear: true`` explicitly clears it and cannot accompany a nonnull origin.
The other typed Mirror settings retain their existing defaults and write
semantics. A pointer check from an earlier set/clear survives later scalar-only
configuration and edits of other named modifiers. Setting or clearing the same
origin replaces that obligation; removing or applying that modifier retires it.
The pointer is compared by resolved object identity, never its display name.
Passing this check does not certify opaque effects elsewhere in the program.

The host allows at most 1,056 mandatory rows (32 operations with at most 33
checks each), plus the 32 authored checks. Nested node conjunctions and merged
setting maps are split into equivalent rows within the existing individual
schema limits, without dropping any fields. The effective predicates also have
structural limits of 131,072 scalar leaves and 1 MiB of compact UTF-8 JSON.
A program exceeding a bound, or whose mandatory checks cannot be represented,
is refused before staging with the limit and a remedy to split the program.
The byte and leaf limits include every frozen check, identity guard, binding
fact, effect rule and unknown-effect descriptor. Operational copy-provenance
records remain separately bounded by existing stage-ID, batch and fingerprint
work limits; they cannot carry uncharged semantic expectations. These are
planning/storage admission bounds, not total memory or native wall-time
ceilings. The per-predicate observation limits below still apply.

Commit fidelity
---------------

Checks run on the stage (``program_jobs`` VALIDATE, and again immediately
before commit). After every write, rename, collection and visibility intent
and a view-layer update, inside the commit's rollback scope, commit re-runs
every declared and derived check on the live objects (``live_checks``): the
targets are the live originals, the new objects by alias and the bound scene,
with no stage overlay, so a certificate's "Host checks passed on the live
result" is literal, sharing and linking included (a batch's ``copies ...
linked`` check reads whether the live copies share the live mesh). A guarded
material check reads the same slot of the live object (its identity proof held
on the stage). An object its depsgraph does not evaluate (disabled in
viewports, excluded), on the stage or live, is measured from base data: its
world matrix is its base channels times its parents' (evaluated or computed);
when base data cannot reproduce a measure (constraints, animation, a
non-object parent, a curve parent that follows its path, a rigid body,
evaluated modifier bounds) the check is unobservable, it
certifies nothing and the run is partial (``unobservable_check``), so a stale
``matrix_world`` neither certifies nor refuses.
``commit_fidelity`` adds the structural part for every object the program
changed:

* data: the live object holds the very data block the checks measured, or one
  of the same type and element counts (a linked duplicate pointed at its link
  root's live data, a written object whose data commit does not swap). Bounds
  are never compared: cached bounds are stale or a fallback for curves and
  lattices, objects hidden or disabled in viewports and data after an Undo,
  so comparing them would roll back correct curve and lattice moves;
* transforms: a written object's local matrix equals its staged copy's, unless
  animation drives it;
* links: every linked duplicate and linked batch copy shares its link root's
  live data, through duplicates of duplicates and copies of a duplicate.

A failure raises ``COMMIT_FIDELITY`` (``UNSUPPORTED`` for the structure,
``CHECK_FAILED`` for a check) before the commit returns: the journal rolls the
whole commit back, the result has ``rollback_verified`` and
``partial_changes_possible: false``, and nothing is certified. Measured in
``test_blender_commit_fidelity.py`` (median of three): one moved object 0.06 ms
for the guard and 0.24 ms for its one live check; 32 moved objects about 1 ms
and 10 ms for 32 live checks, beside an 0.8 s job; 959 new copies about 4 ms
and 2 ms, beside a 5.3 s job; 1,023 linked copies about 7 ms and 4 ms. The
guard reads no coordinates (60 to 80 us an object on its most expensive path).
The result's ``verified`` counts the objects compared (``matches_stage``) and
the checks read live (``live_checks``).

A linked duplicate or linked copy shares its link root's live data: the first
source that is a declared object (after any data swap) or a new unlinked
object. A program that writes linked data after the link (the duplicate, a
copy or the source) is refused before approval (``LINKED_EDIT``,
``program_rules``); an edit before the duplicate is copied with it. One later
write is admitted: every member of the group (the root and each copy), each an
object the program made without material slots (``create_primitive``,
``create_object`` or a copy of one) that no earlier operation gave any, given
the same material on every face (no face selection, slot 0 or the default),
in one operation's ``also`` or across several. The same material is the same
name, except where staging makes a new datablock: a ``copy_as`` copy (a later
reference to its name is that copy) and a statement without a name (each one a
new material, which the refusal says). Every member then ends with the same one
slot and every face on it, so the copy shows what it was given when commit
points it at the root's data. A batch's linked copies share one staged mesh,
which the material kinds may then write once for all of them
(``StagedActionContext.require_group_slots``, only when every user is one of
the program's linked copies). A member that holds the material only in an
added ``also`` slot while its faces stay on another material, a member with
slots before the write, and a declared object are refused, because the copy
would not show what it was given. Which datablock a name reaches is staging's
(``source``, an isolated copy, a library material of that name), so commit also
refuses, before any write, a linked copy a material step gave a material whose
staged materials or face material indices are not its root's
(``commit_fidelity.linked_layouts``). A copy no material step named
follows its root, as Blender's linked duplicate does. A material restated with
other values on a program object the program never gave it is refused before
approval (``MATERIAL_REDEFINED``), where staging refuses it; a target that
holds the material edits it in place.

Objects outside the program whose evaluated shape or placement may follow an
object it changed, in its data or its transform, are named in
``unchecked_dependents`` and in the certificate ("Not checked: the evaluated
shape or placement of 'Other' (uses 'Lat') ..."). The walk follows Blender's
reference graph upwards (``bpy.data.user_map``) through objects, node groups
(Object Info, Collection Info, Geometry Nodes sockets), collections
(instancers), particle settings, curve data, Grease Pencil data (a layer's
parent) and hair curves (their surface) until it reaches objects; 0.04
to 2.6 ms per commit, 1.1 ms in a 2,000-object scene.

Material and tree identity
--------------------------

An existing material's name alone does not establish sharing. The native binder
collects bounded stored identities, slots and supported socket roles without
invoking third-party computed getters. Fresh materials, copies and isolated
resources have distinct request-owned identities. Final-state replay carries
unaffected values and graph facts across proved shared owners; a later colour
edit replaces that colour expectation on every shared owner while retaining
other properties. Authored name/value constraints remain strict and conjunctive.
Fresh resources with no source UID never unify merely because both UIDs are
absent. Copy and ISOLATE preserve their independent identity and logical lookup.

``ProgramJob`` owns immutable encoded checks, facts, effect rules and unknowns
separately from canonical arguments. Approval and retained-preview bindings
cover their identity. Native resolution is repeated at existing bind boundaries;
staged, precommit and Accept verification use the same frozen obligations.
Private copy-output records validate actual stage resources and owner edges,
including after retained-preview hydration. Editable marker text alone never
proves lineage. A changed verification contract requires fresh review/staging;
it cannot take the frame-only rebind path.

Generic or custom node edits, and geometry changes no check measures, can have
effects that the closed verifier cannot prove. These remain typed edits with
explicit partial verification, naming the operation, root and reason. Known and unaffected facts still run in every phase,
and authored contradictions still fail. An unknown effect never grants callback
execution permission or relaxes approval, confinement or read boundaries. Partial
status survives preview Accept, history and provider presentation, and blocks
all automatic completion certification and successful-check compaction. A pure
``required_checks`` consumer refuses partial bundles instead of discarding their
unknowns. Known builtin operations retain their complete verification path.

Approval Details displays every effective check and partial-verification reason
using a separate request-owned snapshot. It preserves authored arguments and
the invocation digest. Preparation happens outside draw; draw only reads prepared
visible rows. Opened Details that are still being prepared show "Preparing
details…" instead of partial rows. When only display target labels arrive late,
the canvas keeps drawing the last complete rows of the same review until their
replacement is published; new geometry, fonts or language never reuse them.
Replacing request sources invalidates the display immediately; explicit
in-place updates rebuild it, and approval clicks reject unannounced in-place
changes. Dispatch still validates fresh authority. Oversized scalar-array displays
use ordered full-value rows; ordinary layouts and full raw copy are unchanged.

Completion evidence is private execution state, not a public result field. The
controller captures the finished job's frozen obligations and positive results,
then seals them only after normal cleanup and durable result handling. Provider
consumption requires the same request and actual result object. Request closure
revokes retained evidence under the same synchronization used for recording and
consumption; a late actual result can be delivered without reviving certification.
Runtime certification cannot be granted by copied or edited JSON, plausible
counters or detached history. Standalone compatibility helpers can summarize
only pure programs whose complete obligations require no native binding. ``test_native_complete_checks.py``, material/partial obligation tests,
and completion lifecycle tests cover these boundaries.

Completely verified successful typed-program results with more than 32 checks
retain every raw check and a host ``check_summary`` with required, checked and passed counts
and the frozen check digest. Successful execution with partial verification
instead retains its explicit ``verification`` record and has no check summary.
Only model-facing tool responses and Claude
continuations may display the first 32 passing checks with an explicit
``check_presentation`` count of the omitted passing rows. Both paths use the
same policy: exact host counts, positive evidence in every row, and no warning,
failure or uncertainty. Other tools, ordinary results and ambiguous evidence
keep their full representation. History, certification, retained previews,
identities, revisions and views remain complete.
The sole reason-code exception is ``BUDGET`` at a typed result's exact
``live_revisions.objects[*].revision_omitted.reason`` path. That entire metadata
remains visible and supplies no missing revision or authority for a later edit;
sibling warnings and the same reason at any other path keep the full result.

Contracts
=========

The implementation separates ``scene_validation`` (binding, observation and
structured reporting), ``validation_reader`` (bounded per-call base-data reads
and caches), and ``validation_checks`` (closed predicate implementations).
Dependencies are explicit and acyclic; callers retain the public
``scene_validation`` API.

``validation_spec.validate_checks`` validates independent copies of 1–32 flat
assertions, such as ``{kind: dimensions, target: $mesh, expected: [4,1,3],
tolerance: 0.0001}``. No kind requires ``tolerance``: left out, each number
compares at its own float32 read-back precision, as the host's derived checks,
which state none, do (``check_defaults``; an ``aim`` 1e-4 radians). Precision
is per number because a tolerance taken from a check's largest number would
let its small numbers pass far off (a light's spot size beside an energy of
1,000,000 W). A ``material_values``
check states any of ``base_color``, ``roughness`` and ``metallic``. A field
naming another object takes the handle or ``{"object": handle}``
(``agent_execution.rst``, "Referring to objects, data and values"). Unknown fields, arbitrary RNA paths/code, nonfinite
parameters, boolean-as-number arguments and unbounded inputs are rejected.
``check_references`` includes parent and armature dependencies, not just the
assertion's primary target.

``scene_validation.VerificationContext`` contains already resolved target
aliases/handles. It never looks up an arbitrary Blender object name. The
``scene_objects`` overlay must include the complete staged scene plus unchanged
live objects for a scene-wide count. ``verify`` returns JSON aggregate metrics,
per-check ``pass``/``fail``/``inconclusive`` and a strict ``passed`` boolean.
Unsupported or over-budget observations cannot pass. No geometry arrays,
evaluated mesh pointers, paths, operators, frame switches or depsgraph updates
are exposed or used by verification. The staging owner updates its view layer
before calling it.

The public read-only ``angelo_scene_verify`` tool binds each target/dependency
by its observed document handle and exact object revision, checking freshness
before and after the read. ``observe`` is an aggregate typed summary for an
already bound internal context, not a mutation authorization.

Supported assertions and limits
===============================

* Local/world-axis-aligned dimensions, local transforms, object
  count/type/name and base-mesh vertex/edge/face counts. Dimensions measure the
  base mesh, or, whenever Blender deforms it (a viewport-enabled modifier, shape
  keys, or an armature, curve or lattice parent's deform, which Blender runs as
  virtual modifiers with no modifier listed), the evaluated bounds Blender
  caches (the dimensions observations report; the stage is evaluated before
  every check, and nothing is evaluated for the check;
  ``validation_reader.deformed``).
  ``space`` ``WORLD`` (the default) measures along the world axes; ``LOCAL``
  measures along the object's own axes with its scale applied: Blender's
  ``Object.dimensions``, the object-space bounds times the length of each axis
  of the world matrix, so parent and delta scale count and rotation does not.
  That is what observations call ``dimensions``, and the scaled size is what a
  model states for an object's own dimensions; measuring the unscaled mesh
  would fail such a check and cost a correction round. On a deformed object
  world dimensions need an axis-aligned rotation and are otherwise
  inconclusive.
* Finite base coordinates/transforms, degenerate local face areas/edge lengths
  and closed/open topological manifold expectations, including disconnected
  vertex fans. These do not certify geometric self-intersection absence.
* Effective object material slot/face assignments (including OBJECT-linked
  overrides) and a direct simple Principled material's scalar values;
  linked/procedural shader inputs are inconclusive. These checks do not prove
  a material's rendered appearance, lighting or artistic match.
* Object-mode UV layer presence, complete corner coverage and finite UV values.
  Live Edit-mode UV reads are inconclusive; they never interpret inaccessible
  original RNA data as empty or invalid, and never change the user's mode.
* Closed allowlisted modifier properties and an exact direct smooth-shading
  geometry graph's configuration, never unbounded evaluated output.
  ``modifier_stack`` asserts modifier names at exact stack indexes and names
  no longer in the stack.
* Parent identity, bounded rest-bone names/counts/coordinates, and explicit
  armature wiring plus normalized rest weights. These are not a pose or
  deformation-quality certification.
* ``bone`` asserts one named bone of an armature: rest ``head``, ``tail``
  (armature space), ``roll`` (from the rest matrix, modulo a full turn),
  ``parent`` (``null`` for none) and ``connected``; the stored pose's
  ``rotation_mode``, ``location``, ``scale`` and ``rotation`` (XYZ Euler,
  compared as an orientation in any rotation mode); and Bone (``settings``) and
  PoseBone (``pose_settings``) settings by RNA path, like ``modifier_values``.
  An animated, driven or NLA-driven pose or setting is inconclusive. It
  certifies stored values, not the evaluated, constrained pose.
* Direct transform action-channel samples at up to 32 explicit frames, each
  read through its keys' interpolation. ``require_keyframes`` asserts that every
  channel is keyframed; it does not require a key at each sampled frame.
  Drivers, NLA, transform constraints and unsupported layers are inconclusive;
  this does not claim world-space evaluated animation correctness.
* ``camera_settings``/``light_settings`` compare the stored camera/light data
  values named by the action catalog's closed ``settings`` objects (same names
  and bounds) within a tolerance. A setting the light type lacks (for example
  ``spot_size`` on a point light) fails; animated or driven data and custom light
  node graphs are inconclusive. These certify stored settings, not rendered
  exposure or appearance.
* ``property`` compares allow-listed settings (``property_set``'s paths) of an
  object, its camera or curve/text data, or the bound scene: numbers within the
  check's tolerance or float32 read-back precision, everything else exactly,
  the active camera and other object pointers by bound reference: a value that
  names no object (a name, an undeclared alias) fails and is listed as
  ``unresolved``, as in the generic modifier, constraint, particle, bone and
  node settings checks. A setting an action curve, driver or
  NLA track animates is inconclusive. Its advertised schema names only the
  shape; ``validate_checks`` validates paths and values against
  ``property_set``'s exact allow-list.
* ``keyframes`` asserts keys present (with interpolation, easing, handle
  type or key type) or absent at frames of any channel path (``data.<path>``
  reads the data's slot), and the channels' extrapolation, read from the
  object's Action slot channelbag; ``fcurve_modifier`` asserts a channel's
  named F-Curve modifier (every index of the path for -1) with its settings by
  RNA path, or its absence; ``shape_key`` asserts a shape key's existence and
  stored value (an animated value is inconclusive); ``nla`` asserts a minimum
  track count and whether an Action is still active, and optionally the active
  Action's name (on a stage, the name commit gives the copy), track names
  present (exactly once) or absent, strips absent and one strip's track and
  typed settings; ``markers`` asserts the bound scene's markers by unique name,
  with frame and bound camera when stated, or their absence. They describe
  stored keys and structure, not evaluated motion.
* ``particle_values`` compares ParticleSettings paths (``particle_set``'s RNA
  rules) of the particle system a named PARTICLE_SYSTEM modifier holds: each
  number at its float32 precision (or the check's tolerance), everything else
  exactly, an instanced object by bound reference. It certifies stored
  settings, not the evaluated particles.
* ``node_graph`` reads one tree of an object (a material by name, a Geometry
  Nodes modifier's group) or of the bound scene's World, optionally inside a
  group node, and proves expected nodes (by exact name and/or ``bl_idname``,
  with frame, input and output defaults and RNA settings), absent nodes, valid
  unmuted links between nodes named by name or type, and unlinked inputs.
  Matching by type lets a grader check a graph whatever the model named its
  nodes. In a stage, a material name is mapped to its staged copy. It
  certifies the graph, not evaluated geometry or pixels.
* ``curve_counts`` counts what stroke, hair-curve, curve and text operators
  change and no other check reads (``validation_curves``): the points and
  strokes of every Grease Pencil layer's drawing at the current frame, a hair
  object's points and curves, a curve's control points and splines, and a
  text's characters and lines. Each stated count is an inclusive range; a
  count the target's type does not have fails (``applies: False``). Original
  data only, no evaluation.
* ``copies`` proves a ``batch``'s copies of one source: that ``count`` of them
  exist, of the source's type, and on its data when ``linked``. A program
  finds them by the aliases the batch gave them; a standalone verification
  finds them by name, continuing the source's own or from a stated ``first``
  name every ``stride`` (a batch result states both).
* ``world_settings`` compares the bound scene's World background colour,
  strength, environment image (by resource ID, or ``NONE``), rotation and the
  stated members of a sky (or ``NONE``). A
  custom World graph or an animated World is inconclusive. In a stage it
  compares the image commit will assign, since staged Worlds hold none.
* ``aim`` measures the angle (radians) between a camera/light world -Z axis and
  a world point. Point lights, other object types, a point at the origin and
  singular axes are inconclusive. It reads the world matrix the staging owner
  updates; it is not a framing or composition check.

Each call limits its mesh scan to 250,000 vertices and 750,000 mesh elements,
with 1,000,000 total element work units, 2,048 scene objects, 32 modifiers,
32 material slots, 16 UV layers, 4,096 bones, 96 curves and 8,192 keyframes or
explicit rig weight entries. Truncated evidence is not absence or success.

Native recipes
==============

``action_recipes.expand_recipe`` is pure planning: it produces exact canonical
``action_catalog`` operations and host checks before normal program approval.
``angelo_expand_recipe`` is read-only and revision bound. Neither can execute
or implicitly authorize its returned program. Defaults and expanded operations
must be visible in the approval.

``angelo_apply_recipe`` takes the same recipe, target handle, exact revision and
parameters plus a program ``mode`` (APPLY or PREVIEW), so a model applies a
tested recipe in one call instead of an expansion round followed by a program
round. At its first precondition, on the main thread, the controller runs the
same read-only, revision-bound expansion (``action_recipes.program_for``) and
fixes the resulting canonical ``angelo_scene_program`` arguments on the call.
The approval shows that program exactly as an ``angelo_scene_program`` approval
does, every operation, setting, declared revision and expected check, followed
by the recipe inputs with defaults; the transcript records it on the activity
row. Only that reviewed program is staged and committed, through the normal
program job, Undo operator and result (checks and ``live_revisions``
included). Later preconditions bind the reviewed program's own revisions and
fingerprints; nothing is re-expanded inside the commit's Undo operator, and a
target changed after the expansion fails closed. A stale revision is refused
before any approval is requested. Read-only chats refuse it and Auto bounded
mode admits it like the program it stages. A target with object dependencies
(parent, constraint or modifier targets) still needs ``angelo_expand_recipe``
and a program that declares them in its read set.

Six recipes are available: bevel-and-shade, fit-dimensions, clean-mesh,
material-setup, simple-animation and UV prep. Cleanup requires explicit vertex
indexes and approved distance; there is no delete/dissolve/remesh, modifier
application, inferred selection or unrelated-data cleanup. Fit dimensions
performs one analytic positive scale solve only for an axis-aligned,
unparented, unconstrained mesh, against minimum/maximum scale and maximum
ratio bounds the approval shows. A call may leave them out: they then are
the widest a transform takes (0.000001 to 1,000, ratio 100;
``action_recipes.FIT_BOUNDS``), so a plan need not spend output tokens
restating them (``performance.rst``, "Plan output tokens"). Out-of-bound
correction is rejected before approval.
Simple animation keys a location or rotation channel; ``keyframe_insert``
keys a scale, bounded as a transform's. There are no LLM retries,
post-failure mutations or automatic recovery loops.

``bevel-and-shade`` measures its width in object space, as Blender's Bevel
modifier does, so a scale other than one stretches it. With ``apply_scale``
(default false) the recipe first bakes the object's own scale into its mesh
with ``transform_apply`` (scale) in the same staged program; its checks then
also state a scale of one, and a scale already one adds nothing. The expansion
reads the target on the main thread (``action_recipes.scale_facts``) and
refuses before approval, with the reason:

* whatever the own scale, anything else that stretches the bevel, which baking
  the own scale cannot even out: a delta scale, a parent's or bone's scale or
  shear (``matrix_world`` against ``matrix_basis``) and any constraint;
* when there is a scale to bake, what ``transform_apply`` refuses (children,
  delta transforms, anything but a mesh) and what would change the world
  result: a scale keyed, driven or in NLA (the next frame scales the baked mesh
  again), a zero axis (it would flatten the mesh) and any modifier but
  Subdivision Surface. Other modifiers measure in object space or depend on its
  shape (a Solidify's thickness, an Array's constant offset, a Hook's stored
  inverse, Multires displacements) and would make a different result;
  subdivision is affine-invariant (``action_recipes.SCALE_INVARIANT_MODIFIERS``).

What it bakes leaves the evaluated world result exactly as it was, as
Blender's Apply Scale does: ``transform_apply`` bakes the scale matrix alone,
keeps the rotation channels (also for a mirrored axis, whose faces are flipped;
``transform_bake``), and copies a mesh other objects share for this object at
commit while the others keep theirs. Without ``apply_scale`` nothing changes.
The recipe's parameter carries the line "Bevel width is in object space;
apply_scale bakes the object's own scale first, for an even bevel."
(``action_recipes.APPLY_SCALE_NOTE``). It is there so a model asked for an even
bevel on a scaled object requests the bake in the same call instead of
discovering the stretch and spending another round on it.

Fit eligibility uses the observed effective world basis, including the active
quaternion/axis-angle rotation and delta channels, not inactive Euler values.
Axis-aligned delta scale and identity quaternion objects remain supported;
non-axis-aligned effective transforms are refused before approval. This remains
a base-mesh dimension solve, not a rendered or evaluated-modifier guarantee.

``clean-mesh`` with no face indexes recalculates all surviving faces. With
explicit initial face indexes, the public recipe adapter performs a bounded
read-only merge simulation on temporary, unlinked data and tracks surviving
face lineage. It returns remapped ``CURRENT`` IDs at ``topology_step: 1`` and
an exact ``topology_digest`` in the normals action for approval. The real
program rechecks coordinates and vertex/edge/face index order after its merge
and before using those IDs. Removed/replaced/ambiguous faces or a mismatched
digest are rejected; an empty selection never silently becomes "all faces".
Pure callers must provide the same complete planning observation for explicit
faces. Planning supports at most 100,000 vertices, 200,000 edges/faces and
800,000 corners; temporary data is always discarded.

Native action precision and preserved state
===========================================

``parent_set`` with ``keep_transform`` preserves the existing basis/delta
channels through the parent inverse and checks its actual world-matrix
readback. Muted or zero-influence owner constraints remain untouched. Active
owner constraints, non-Object previous parenting, singular new parents and
unparenting a world shear that cannot fit local transform channels are rejected
rather than silently disabling/baking constraints or changing the world result.
Explicit ``keep_transform: false`` remains available for local parenting.

``modifier_configure`` changes only its approved type-specific settings; it
preserves viewport/render/Edit-mode/cage enable flags. New modifiers use the
native enabled defaults. Graph provenance resolves approved socket identities,
not translated/domain-dependent display labels (for example Geometry versus
Mesh on FACE smooth-shading nodes). Graph assertions certify this closed local
configuration only, not rendered smoothness or unrestricted evaluated output.

``armature_create`` canonicalizes bone endpoints to native float32 coordinates
before approval. Lengths at/below Blender's 1e-6 native adjustment threshold,
precision-collapsed endpoints and connected heads that do not exactly meet the
canonical parent tail are rejected. The kernel checks Edit/rest endpoints and
hierarchy against those coordinates and returns the actual bounded
``bone_endpoints`` readback, rather than treating count/name metadata as proof
of the requested rig orientation. This does not certify posed deformation.

Local task-success matrix
=========================

``tests/python/angelo_agent/eval_agent_tasks.py`` defines 26 provider-independent
small tasks: 21 success fixtures and five failure/inconclusive rejection
controls, spanning dimensions/counts, modeling/topology, modifiers,
materials/UV, parenting, rest rigs/weights, direct animation channels, and
aimed cameras and lights (including a light-energy negative control).
``test_blender_agent_task_eval.py`` runs real action programs through native
staging, host assertions and commit, then re-verifies committed output. Rejected
programs must leave the live scratch scene unchanged. Every fixture owns a
fresh empty scratch scene in a factory-startup process; no provider call occurs.

``test_blender_scene_validation.py`` additionally proves that a genuinely
unmet dimension goal becomes satisfied by a real kernel transform on an
isolated copy, checks real material/UV/bevel-and-shade recipes, rejects corrupt
geometry/stale revisions, and preserves the source scene, geometry, selection
and active object. ``test_native_scene_validation.py`` covers pure strict
schemas, no geometry leaks, resource-inconclusive states and recipe bounds.
``test_native_camera_light_actions.py`` covers the camera/light schemas, type
defaults, cross-field rules, aim math and predicates on the host;
``test_blender_camera_light_programs.py`` runs them through real staging,
APPLY/PREVIEW, commit, Undo/Redo, stale-revision rejection, the public verify
tool, shared light data and focus-object remapping.

Run the native scripts with ``--background --factory-startup --disable-autoexec
--python-exit-code 1 --python <script>``. Reports go to ``ANGELO_QA_DIR`` or a
dedicated temporary-directory default. A matrix pass certifies only these
bounded host predicates, not broad artistic quality or model/provider parity.
