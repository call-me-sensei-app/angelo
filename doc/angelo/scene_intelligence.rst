.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Scene intelligence
==================

Integration interface
---------------------

``scene_spec`` is a pure Python contract exporting ``SCENE_TOOLS``,
``SCENE_NAMES``, and ``validate_scene(name, args)``. All five tools are read-only:
``angelo_scene_search``, ``angelo_scene_object``,
``angelo_scene_relationships``, ``angelo_scene_delta`` and
``angelo_scene_settings``.

``angelo_scene_settings`` (``scene_settings.py``) returns the bound scene's
handle (``scn:<epoch>:<session_uid>``, the same form as an object handle), its
settings ``revision`` and the current values of the scene settings
``property_set`` may write (render, sampling, colour management, frame range,
active camera, units), a summary of the World background, the World graph's
node names and types and its links (``world_graph``, at most 48 of each,
which ``node_edit`` addresses), what the World shows with its sun beside the
Sun lights and how bright it shows in the view transform (``world_shows``,
``agent_execution.rst``, "Where the sun is, and how bright the World shows"),
the loaded images an environment may use (resource IDs, names and sources; no paths and
no sizes, because reading a size loads the file), the timeline markers when
there are any (name, frame and bound camera handle, at most 64; ``marker_edit``
names them), plus the allow-listed paths this build lacks. The revision is a
digest of exactly those settings, every other scene setting ``property_set``
may write (the paths the generic rules admit beyond the reported ones, digested
but not reported), the World's complete exact state, every view
layer's collection visibility (exclude, eye and the collections' own flags,
bounded) and every timeline marker (name, frame, selection and camera), so
unrelated scene edits do not invalidate it;
it binds a program's write set as an object revision does, and
``angelo_scene_verify`` accepts the scene handle with it. The result names
them ``handle`` and ``revision``, the binding keys short identifiers shorten
and resolve, so a Claude context sees ``o<n>`` and a revision prefix like any
object's. The scene is never in a turn observation, so its binding is never
inferred: a program that writes scene settings declares it. The query reads a
few dozen RNA values directly, without caches, object scans or evaluation. A
Claude plan defers its schema.

Two more read-only tools answer what the object-scoped queries cannot
(``read_spec``): ``angelo_read_data`` reads one data path of the open document
(`Live data paths`_) and ``angelo_blend_file`` reads the file as a file
(`The file as a file`_). Like the queries they need no approval, stay available
in a read-only chat, and are refused only while a job has locked Blender.

``scene_queries`` provides main-thread ``execute(window, name, args)``,
``invalidate(window=None, depsgraph=None)``, ``resolve_object(window, handle)``,
``object_revision(window, obj)``, ``object_handle(obj)`` and
``document_epoch()``. ``angelo_view`` reports these handles, revisions and
epoch with its images (see ``agent_execution.rst``). No viewport override is
necessary. Call ``invalidate(depsgraph=depsgraph)`` from the native depsgraph
update handler (``blender_tools.track_scene_changes``, which is ``@persistent``:
Blender drops other handlers on File > Open). Call ``invalidate()`` after load
and Undo/Redo (ID replacement): this changes the document epoch and invalidates
all handles, observation tokens, and cursors. Call ``invalidate(window=window)``
after an unknown edit: cached observations are dirtied but existing baselines
remain available for a conservative delta.

Object handles are ``scn:<document epoch>:<session_uid>``. Renaming does not
change them. A Claude native context reads and sends them as short ``o<n>``
numbers, and revisions as eight-digit prefixes, which the host maps back to
exactly these long forms before any owner here sees them (``identifiers.py``;
``agent_integration.rst``, "Short identifiers"). They are never persisted as custom properties or interpreted as
names/RNA expressions. Tokens and pagination cursors are opaque admitted IDs,
bound to scene and view layer. They cannot read another document or arbitrary
datablocks. Native names/labels are untrusted data, not instructions.

``object_revision`` fails closed if an exact bounded original-state revision
cannot be produced. Its scope is original/live-edit geometry and allowlisted
configuration; it is not a universal evaluated-geometry fingerprint, a grant
of authority, or a substitute for mutation-specific preconditions. It includes
the object settings ``property_set`` writes (display, colour, pass index, ray
visibility). Curve and text objects have exact revisions from their complete
curve or text datablock (splines, points, body and formatting, at most 200,000
recursive items); a bevel or taper object dependency fails closed. Armatures add
a ``bones`` field: the armature data's settings and every value ``bone_set``
writes on each Bone and PoseBone, B-Bone custom handles included, so B-Bone
rigs have exact revisions.

Source ownership
----------------

``scene_queries.py`` remains the public entry point and sole owner of document
epochs, identities, mutable caches, admitted baselines, cursors, invalidation
and dispatch. The internal readers receive explicit identity/metric services;
they retain no Blender datablocks or evaluated pointers and own no caches.
``scene_stores.py`` implements the bounded baseline and cursor stores whose
lifetimes the entry point owns.

* ``scene_read_fields.py``: closed semantic field allowlists.
* ``scene_read_values.py``: value encoding, safe errors and per-read budgets.
* ``work_clock.py``: the per-thread CPU clock of the turn context's and the
  post-commit read-back's cooperative revision budgets.
* ``scene_metadata.py``: modifiers, material/node graphs, actions and rigs.
* ``scene_shared_reads.py``: one read per pass of a datablock that several
  objects share, with its budget charges, limitations and identity
  registrations replayed for each later object.
* ``scene_catalog_graphs.py``: closed catalog Geometry Nodes integrity and state.
* ``scene_geometry.py``: exact original/live-edit geometry, topology and UV.
* ``scene_fingerprints.py``: object/dependency fingerprints, invalidation IDs
  and the per-pass collection membership index.
* ``scene_relationships.py``: semantic dependencies and bounded native users.
* ``scene_query_results.py``: bounded shaping, detail sections, literal filters
  and delta rows.
* ``scene_coverage.py``: observation coverage, discovery and object-scoped
  snapshots.
* ``scene_stores.py``: per-coverage baseline pools and pagination cursors.

Keep lifecycle and cache changes in the entry point, domain reads in their
named owner, and safety/schema changes in ``scene_spec.py``. Source modules are
kept below 500 lines so humans can inspect these boundaries without AI tooling.

Freshness and bounds
--------------------

Cache values and admitted baselines contain immutable serialized data, never
RNA objects or retained evaluated pointers. Every cache hit is preceded by
a fresh original-state fingerprint, including topology, selections, masks,
numeric mesh attributes, UVs, shape keys, weights, transforms, supported modifier
settings, material/node defaults and links, rigs, constraints and actions.
Live Edit Mode shading and UV seams come from BMesh face/edge flags, not stale
Mesh attribute buffers. Original (Object Mode) meshes are read from their
contiguous attributes (``position``, ``.edge_verts``, ``.corner_vert``,
``.corner_edge``, the select/hide flags, ``material_index`` and ``sharp_face``)
rather than per-element RNA, which costs a getter per element. The bytes hashed
are identical, so revisions do not change: an absent flag attribute means its
default, exactly as the element properties report it, and any unexpected layout
falls back to the element read. A native parity test compares every mapping
with its element property on meshes with and without those attributes. Vertex
groups have no attribute: revisions read ``Mesh.angelo_vertex_group_weights``
(see ``native_sculpting.rst``), the same record bytes the per-vertex walk
hashed, and use that walk only where the native property is unavailable. The ten supported constraint types have closed per-type
semantic controls, including axis enable flags, Copy Scale power/addition,
Child Of inverse state and Limit Rotation legacy behavior. Constraint UI
expansion/active flags and derived solver errors are outside this revision scope.
Catalog Noise shader graphs cover dimensions, noise type, normalization, all
numeric input/output defaults and links, texture mapping and color mapping/ramp
controls. This does not admit arbitrary texture/image graphs.
Camera and light objects have exact revisions covering every writable semantic
and display setting in ``scene_read_fields`` (``CAMERA_FIELDS`` with depth of
field and stereo, ``LIGHT_FIELDS`` across light subtypes) plus the light's node
graph, so lens, clip, energy, color, radius and focus changes change the
revision and appear in deltas as the ``camera_light`` field. A camera's focus
object is hashed as an object dependency. Every light in this build owns a node
tree; only Blender's default Emission -> Light Output graph is supported, and
any other graph, a CUSTOM (OSL) camera or camera background images leave the
revision uncertain. Object detail exposes the same data in its
``CAMERA_LIGHT`` section. Program fingerprints include the complete
Camera/Light RNA as mutable dependencies. The active scene camera and World
settings are scene/file state outside object revisions.
Depsgraph callbacks evict affected object/dependency entries. Unknown edits
cannot pass merely because no callback arrived: manual data writes are checked
again. Counters distinguish exact checks, bytes read, cache hits/misses,
invalidations, epochs and evictions, and include per-query and cumulative totals.

Field and geometry digests use BLAKE2b-256, like sculpt revisions. In the
bundled Python of the validated macOS arm64 build, SHA-256 measured about
129 MB/s against about 1.5 GB/s for BLAKE2b; an OpenSSL build with hardware
SHA-256 can be faster than BLAKE2b, but BLAKE2b's speed does not depend on how
OpenSSL was built. Revisions and observation tokens are opaque and
document-epoch scoped, and scene-query revisions are not persisted in task
memory, so a value produced by an earlier build is only ever stale.

Observation coverage
--------------------

Every admitted observation records its coverage. A ``SCENE`` observation comes
from a whole-scene pass: it fingerprints every object, so a delta against it can
report additions, removals and unchanged objects anywhere in the bound scene.
An ``OBJECTS`` observation fingerprints only named handles. Object detail and
relationship queries admit one covering just their target, built from the same
capture that produced the returned revision, so they never pay for a
whole-scene pass. ``angelo_scene_search`` with ``coverage="PAGE"`` is the
discovery mode: it applies the same literal filters as a ``SCENE`` search (one
shared predicate) to every object with inexpensive live reads, fingerprints only
the returned page, and admits an ``OBJECTS`` token for that page. Its rows carry
the same exact revisions a ``SCENE`` search returns, and its matching counts are
exact when the scan completes. ``SCENE`` remains the default search coverage.

A delta against an ``OBJECTS`` baseline compares exactly its objects. Each one
is looked up by session UID in the live scene, so a missing object is a proven
``REMOVED``; changed fields are ``MODIFIED`` and inexact comparisons
``UNCERTAIN``. It never reports ``ADDED`` objects and never proves that the rest
of the scene is unchanged: results state ``observation_coverage``,
``covered_count`` and a ``COVERED_OBJECTS_ONLY`` scope, and the before/after
object counts are informational live scene counts. An ID replaced under a
covered handle rotates the document epoch and fails the query rather than
reading as a removal.

Each whole-scene pass resolves collection membership from one index built after
its read budget starts, with the same membership and order as
``Object.users_collection``. That property is implemented in Python and rescans
every collection's object list on each call, which made per-object reads
quadratic in the object count. The index visits every collection link in the
file once but records only the pass's scanned objects, so its memory follows
the bound scene rather than other scenes or linked libraries. It checks the
pass deadline every 4,096 links and caps the recorded links at
``MAX_MEMBERSHIP_LINKS`` (a total of four times ``MAX_OBJECTS`` for all scanned
objects together, not a per-object limit). It is all-or-nothing: on either
bound it is discarded, never used partially, each object reads its own
membership once, and the ``membership_fallbacks`` counter records the pass.
It is not retained after the pass.

Untracked mutable dependencies are explicit, not guessed. In particular live
Sculpt/PBVH/Multires displacement, image pixels, arbitrary Geometry Nodes modifier
inputs, drivers Blender cannot evaluate natively (scripted expressions, context
variables, Python-defined properties; ``driver_rules``), unsupported ID types, nested modifier
dependencies and other unadmitted configurations do not receive a program
revision. The existing sculpt engine must prove its own exact live surface.
Delta ``UNCHANGED`` counts cover only complete supported original state;
over-budget or unsupported objects remain uncertain. Simulations, arbitrary
custom properties, external effects and universal evaluated geometry are not
part of this delta's scope.

Catalog-generated Geometry Nodes have a narrower supported exception. The reader
validates the bounded reserved ``_angelo_native_graph_v1`` metadata through the
action catalog and checks the actual tree with the shared native graph auditor.
It separately proves the exact two-Geometry-port interface, sole fallback modifier
input, native socket types/defaults/flags, node settings, links and multi-input
order. Extra generated modifier values, resource ports, custom properties,
animation/drivers, nested/unknown nodes and unadmitted implicit modes are rejected.
This is the only named custom-property content read: raw provenance and unknown
property values are never returned. Presence checks are restricted to graph-owned
groups; a metadata flag alone cannot assert integrity or grant permission. The
program backend still performs independent full preconditions and evaluated-work
checks; this reader never evaluates or exports meshes or retains native pointers.

Queries scan at most 20,000 objects, with 2,000,000 per-element reads, 256 MB
copied by single C calls (attribute arrays and native buffers), 12,000 metadata
items and a cooperative two-second deadline per read pass. Per-element RNA
getters and Python walks charge elements; whole-array copies charge bytes, since
they cost orders of magnitude less per element. Each read is charged before its
buffer is allocated. Detail
sections share their deadline. Pages contain at most 100 entries, detail
collections share a 500-item budget, and compact UTF-8 payloads are limited to
128,000 bytes. Responses report omitted lists/sections, uncertain revisions,
scanned counts and limits. Over-budget meshes still expose inexpensive material,
rig and UV layer metadata. No observation exports a full mesh coordinate array.

Within one read pass, a material's node graph and its node-tree revision are
read once however many objects share the material (``scene_shared_reads.py``).
Every later object replays the first read's budget charges, appended
limitations and identity registrations in their original order, so a pass
exhausts its budget at the same object and reports the same limitations as
reading each object afresh; a read that fails is not reused. The shared result
is only hashed and walked, and it is dropped with the pass's budget, so nothing
is reused across passes, edits or documents.

Baselines are retained in two independent LRU pools: at most 16 ``SCENE``
baselines (16 MB compressed) and 128 ``OBJECTS`` baselines (8 MB), so frequent
object-scoped queries never evict a whole-scene baseline or the reverse. At most
32 cursors are retained; evicted or fabricated IDs fail closed. ``SCENE`` search
and delta cursors pin the original admitted token and exact query and reject
any scanned change instead of skipping or duplicating objects. Discovery cursors
reject a changed ordered match list, and each discovery page admits its own
token. Relationship cursors reject changed edges or labels; unrelated scene
edits do not affect them. Incomplete membership scans cannot prove absence: deltas
label ``NEWLY_OBSERVED``/``NO_LONGER_OBSERVED`` instead of inventing additions or
deletions. A payload-byte-limit omission clears the cursor and explicitly asks
for a smaller page, rather than silently skipping entries.

Incoming native ID object users use ``bpy.data.user_map`` only within the closed
object/work budget and emit only users in the bound scene. Private staged Main
IDs, unrelated scenes, library paths, image paths, strings in sockets, scripts,
driver expressions and custom-property contents are never emitted. Outgoing
dependencies use closed semantic fields; unsupported modifier coverage is
reported incomplete. Names and asset metadata remain untrusted data.
Object detail requests containing ``RELATIONSHIPS`` read those edges once before
cache lookup: incoming labels and native ID users can change without changing
the target. The exact fresh edges/completeness hash joins the cache key, so
unchanged rich mesh details can be reused without trusting target-only evidence.
Incomplete relation scans and work-budget omissions are not cached. Incoming
reads retain the same object/item/deadline and output/payload bounds.

Turn context and change feed
----------------------------

``turn_context.py`` builds the observation sent with each user turn (see
``agent_execution.rst``). It reads only bounded state: at most 32 entries
(active object, then objects the user's message names, then selected objects in
view-layer order, then objects the chat's change feed recorded, then scene
order), and exact revisions for the active object and at most eight named, eight
selected and eight recently changed objects. Name matching reads the scene's
object names in one call (``bpy_prop_collection.keys``), at most 20,000 of them,
and resolves a match by exact name; a recently changed row is resolved by its
full name and accepted only when its session UID matches. The feed keeps full
names (at most 256 characters, above Blender's 255-byte limit) for this lookup
and still reports 128-character labels.
Toolset 20: entries that get a revision also state Blender's ``dimensions``
(evaluated bounds along the object's axes, scale applied: cached RNA reads),
and ``scale`` and Euler ``rotation`` when not the identity, so a size question
or a scale to exact dimensions needs no lookup round. An object that cannot
report them simply lacks the fields.
Each revision is ``object_revision`` itself, the function that programs and
``angelo_scene_verify`` re-run when binding and before commit, so no separate
admission path exists or is needed: a program's ``read_set``/``write_set`` is
accepted when its handle resolves in the current epoch and its revision equals
a fresh ``object_revision``, regardless of which observation supplied it.
The builder admits no observation token and never runs a whole-scene pass.
A revision also covers the object's selection/active state, mode and view
layer, so selecting other objects after a turn context makes the affected
revisions stale and their program fails closed. It covers the current frame
only for an object that can change with time (``revision_time.still`` is
false: animation or drivers, constraints, physics or particles, a modifier
outside the time-independent set, a time-reading geometry node, or a parent,
Mirror or Array object or focus object that can itself change with time): a
frame change stales those revisions and no others
(``scene_fingerprints.context_hash``). Stillness is judged only for a capture
that gives a revision, once per object and once per node group a pass
(``revision_time.still_in_pass``, in the pass budget's shared reads, one work
item each); a capture with no exact revision keeps the frame. A time-dependent
revision also carries a subframe that is not 0, as a program's binding does,
so every revision of a whole frame keeps its earlier value. Judging stillness
costs at most about 3 µs an object (1.6% of a revision on 400 objects). The
per-pass memory matters where objects share a node group: 2,000 objects
sharing one 300-node group take 0.133 s a pass against 0.104 s without the
stillness check; judging the group again for each object instead makes the
pass 3.9 times as long as one without the check. Whether the frame is included is itself hashed, so an object that
gains animation changes revision. Still objects leave the frame out because a
script's own ``frame_set`` would otherwise stale the bindings of objects it
never touched, while the script result re-reports only the objects it changed,
so the next program would be refused as ``STALE_REVISION``.
Revisions are attempted only while a cooperative 25 ms budget lasts (checked
between objects) and are skipped for meshes beyond the program input limits;
omissions state ``BUDGET``, ``UNSUPPORTED`` (with the fail-closed limitation
codes) or ``OVER_LIMIT``. The scene settings' revision is never left out
(2 ms on a plain scene, 33 ms with a 300-node World graph), because a program
that sets a scene setting is refused without it, and a small
revision may use an allowance past the budget as long as the budget itself
(``revision_budget``): an object without geometry, or a mesh of at most 4,096
vertices (under 1 ms), either without a parent, modifier, constraint or focus
object, whose geometry its revision would also hash (a cube shrinkwrapped to a
95,000-vertex terrain costs 15 ms). A read-back after an edit (a script's
``after``, a program's post-commit revisions) gives every changed object
within the program input limits a revision within 0.5 s
(``READ_BACK_SECONDS``; 24 rows at the 100,000-vertex limit measure about
0.4 s), rather than the 25 ms budget, which two meshes of about 90,000 vertices
can use up. The budget counts the main thread's own CPU time (``work_clock.WorkClock``),
never its waits for a CPU or the GIL: in wall
time, a send on a heavily loaded machine, or while the snapshot worker runs,
could omit the revision of the object its message names, and a program or
recipe that left that binding to inference would be refused for want of it.

``change_feed.py`` keeps per-chat hints between planning boundaries. The
depsgraph handler records, for the bound scene only, up to 64 changed objects
(transform, geometry, or flagless updates such as renames), a count of up to 64
shading-only objects and up to 16 other changed datablocks, each with an
overflow flag, reading at most 4,096 updates per call. Shading-only object
updates are counted rather than listed because a relations rebuild (any ID added
or removed, including a program's private stage) flags shading on every object.
Selection, active object, mode, frame and object count reach the depsgraph only
as an anonymous scene update, so they are compared with the previous boundary;
a change reverted in between is not reported. Load, Undo/Redo and an
unannounced ID replacement suppress object rows and report the epoch change
once. The feed retains only integers and bounded labels, at most 32 chats, and
is cleared on unregister. It is never freshness evidence.

Live data paths
---------------

``angelo_read_data`` (``data_path``, ``data_read``, ``data_read_rows``,
``data_read_values``, ``data_read_policy``) reads one bounded path of the open
document and changes nothing. It exists because the queries above return closed
field lists and ``angelo_blender_api`` documents types without values: the
socket identifiers of a node group, a modifier's input identifiers, an add-on's
settings or the current value of a setting could otherwise only be read by a
reviewed script, which costs an approval and a model round, or be guessed, and
a guessed name fails the script that uses it.

**Grammar.** Closed, and parsed a character at a time; there is no ``eval``::

   path = root *step
   root = "scene" / "data"      ; the bound scene / the open file's datablocks (bpy.data)
   step = "." name              ; an RNA property of the struct reached so far
        / "[" integer "]"       ; an item of a collection, negative from the end
        / "[" quoted-name "]"   ; an item of a collection by its name

``bpy.context.scene`` and ``bpy.data`` are accepted spellings of the roots. No
call, slice, operator, expression or name starting with an underscore parses,
and at most 16 steps and 512 characters do. A parsed step is resolved against
the installed RNA definition (``bl_rna.properties``), never by ``getattr`` on a
name the model chose: a function is named in the answer and never called, and a
Python attribute that is not an RNA property does not exist for the reader. The
walk never looks a type up in ``bpy.types``, so the settings an add-on
registers (Cycles) resolve like Blender's own.

**Answers.** The answer describes where the path ends:

* a struct: its properties with types and current values, a page at a time. A
  pointer is a reference (struct, name, the ``data.<type>["name"]`` path of a
  datablock, the scene handle of an object in the bound scene) and a collection
  its length; a datablock lists its own settings before its bookkeeping;
* a collection: its length and a page of items, each with its index, name and,
  where the struct has them, ``identifier``, ``type`` and ``enabled`` (a node
  socket's row is the name, identifier, type and whether the node's current
  mode offers it);
* one property: its value, an enum's choices and a number's hard range. Blender
  computes some choice lists for the interface (render engines); RNA gives
  their fixed part and the answer says ``choices_partial``.

A property Blender plans to remove is marked ``deprecated``; reading it does
not print Blender's deprecation warning on the console.

``depth`` 2 adds each row's own values one level down (a modifier stack with
its settings, a modifier's inputs with their values) in one call. A path that
does not resolve is an answer, not an error: ``found`` false with a stable
``code`` (``UNKNOWN_MEMBER``, ``UNKNOWN_NAME``, ``INDEX_RANGE``, ``FUNCTION``,
``DENIED``, ``OTHER_SCENE``, ``SYNTAX``, ``CALL`` ...), how far the path
resolved and the valid names there. Only the shape and bounds of the arguments
are validated before the call, so a wrong guess costs one read and neither a
rejected plan nor the turn. In a Claude plan such an answer is a lookup's data
(``agent_integration.rst``, "Failed plan steps"): the rest of the plan runs. A
tool error is a failed lookup instead, which returns to the model in the same
turn, and the plan's later edits do not run. No schema a runtime checks
carries the path's length; ``validate_call`` refuses a path over 512
characters in one line that names the limit and the length it got.

**Bounds.** 64 rows a call (32 by default), 12,000 characters of rows, 256
characters a text, 64 elements an array, 64 enum choices and a two-second
cooperative deadline. Whichever ends a page first sets ``truncated`` and
``next_offset``, and the answer says which offset continues. Nothing is cached:
every read is the current state.

**Where a path may go** (``data_read_policy``). Every struct on the way, and
every struct a row would show, is admitted first: it belongs to a datablock of
the open document; that datablock is original data, never an evaluated copy;
it is not interface or script data (screens, window managers, workspaces, text
blocks) and not a library (the file tool reports those); a scene is the bound
scene, as the queries never report another one; it is not a dependency graph,
RNA's own description of a type, a preview image or packed bytes. A datablock
whose values come from an external file that a read could load (movie clips,
sounds, volumes, cache files) shows its identity only. The reader refuses
everything while a staged change's private copies are in the file.

**What is never returned** (``data_read_values``). A withheld value is still
listed, with its type and a closed reason, and it is not fetched: the decision
uses the RNA definition and, for ordinary text, the text itself.

* ``PATH``: file paths, folders and file names by RNA subtype or property name,
  and any text that looks like a path or a URL. This is the privacy rule of the
  observations: a path can carry a user's or a client's name to a model
  provider.
* ``SECRET``: password fields, credential-like property names and text the
  task-memory rules recognise as a credential.
* ``SCRIPT``: script source, bytecode and driver expressions.
* ``SOCKET_TEXT``: the text of a string socket on a node, on a node group's
  interface or in a Geometry Nodes modifier input, and of a String input node.
* ``BYTES``: raw byte strings. ``LOADS_FILE``: an image property whose read
  would load the image file (size, channels, depth, format; pixels always).
  An image's pixels are seen as a picture instead: ``angelo_view`` with
  ``image: "IMAGE:<name>"`` shows a buffer already in memory and never loads a
  file (``view_sources.rst``).

Names (a struct's RNA name property, identifiers, labels) are returned as the
other scene reads return them, unless the definition says the "name" is
something else: a packed image file is named by its file path, which is
withheld. An enum whose identifiers are file paths (an add-on's file previews)
is withheld too. Custom properties are not RNA properties, so no path reaches
them.

**Non-mutating access** (``data_read_access``, ``data_read_fetch``,
``data_read_batches``, ``data_read_records``, native ``_bpy._angelo_read`` in
``bpy_angelo_read*.cc``). Every live read goes through
the native address-read ABI (``ADDRESS_READ_VERSION = 2``): Python hands native an
address (``scene``/``data`` root plus the parsed steps) and receives only copied
primitives: declarations, node facts, values and pages. Native resolves each
step in C against the RNA declarations of the current document, every time;
Python never holds an RNA wrapper. Python attribute access, ``bpy.types``
classes and anything an add-on installs on them (``__getattribute__``,
``__getattr__``, ``__init__`` overrides) are therefore never involved in an
automatic read. Builds without that ABI return ``NATIVE_READER_REQUIRED`` for
every live read; there is no Python fallback.

Each declaration is admitted by its native fingerprint, a hash of everything
its description depends on (owner facts, declaration, hard limits, routes);
native recomputes it on every use and answers ``STALE_ADMISSION`` when it no
longer matches. A node names its declarations key, a hash of its whole
declaration list, so Python copies and parses a type's descriptions once and
shares them between reads by that key (``_DECLARATIONS``); a changed list has
another key. Page values and references are copied in batches
(``fetch_many``, ``reference_many``: one resolution, then each struct reached
by at most two relative steps); anything a batch cannot answer is fetched
alone. Node records are compact tagged tuples. Results of the native module the
add-on imported are used as built, since they are fresh primitives by
construction; any other backend's results (host-test stand-ins) are copied and
checked against the closed record shapes first.

The document epoch is checked before and after every native operation and the
native context checks that its window, scene and document are still current;
``EXPIRED`` and ``STALE_ADMISSION`` discard the answer. ``BUDGET`` stops new work
and returns the rows already copied with the usual truncation fields. Contexts
close in ``finally``. The accessor combines stored values with declared defaults
without creating missing PropertyGroups or repairing incompatible storage (the
latter is ``INVALID_STORAGE``). Fetching an element of a geometry attribute
(``Attribute.data``) makes Blender rewrite single-value storage as an array, so
that collection is never entered; its length, data type, domain and storage
stay readable. Cache availability is checked for the exact image tile, frame and
view; uncached image metadata is withheld as ``LOADS_FILE``. Automatic reads
never load an image, movie, font, volume grid or cache file.

**Getter policy.** Native decides, per declaration and from the provenance
makesrna records for every generated property (``RNA_property_read_callback_source``;
the build writes the full inventory to ``rna_read_sources.tsv``), how a value is
read:

* An add-on's (Python) property is read from its storage or its declared
  default. Its getter, transform and item callbacks never run. The row says
  which with ``value_source`` (``stored`` or ``default``) and
  ``computed_getter: not_run``; a reference label carries the same in
  ``read_notes``. Unknown dynamic enum values are ``COMPUTED_VALUE``. The
  computed value stays available through an approved (or trusted) script.
* A catalogued built-in getter, one that loads a file, changes data or a cache,
  or runs add-on code, never runs as such. It has a native replacement that
  computes the same answer without the side effect, runs only under a guard
  that makes it inert, or is withheld with its reason (below). A declaration
  that uses a catalogued callback but no longer matches its replacement exactly
  is withheld (``NATIVE_READER_REQUIRED``) rather than run.
* Every other built-in getter, collection iterator, choice list and struct
  refiner runs as for any RNA read. Enum values use the bound context only
  while no Python context override is active, every view layer is
  synchronised and the window's layer name resolves; otherwise the
  declaration's context-free choices are used.

Reasons a catalogued value is withheld (rows show the code; a single requested
value also carries a sentence for the model):

* ``LOADS_FILE``: Blender would load a file to answer.
* ``NOT_EVALUATED``: the value comes from an evaluation Blender has not made
  (a pending subdivision wrapper, the bounds of a text object without evaluated
  bounds, a data-transfer source layer list before the scene is evaluated, a
  node tree whose topology cache is stale). A read never evaluates.
* ``VIEW_LAYER_OUT_OF_SYNC``: the view layer is out of date and Blender would
  resynchronise it to answer.
* ``EXTENSION_CODE``: answering would run add-on code (an add-on node tree's
  socket rule).
* ``RESOURCE_LIMIT``: the value is larger than a read may copy.

**Catalogued built-in getters.** Native replacements (``bpy_angelo_read_scalar_const``,
``bpy_angelo_read_geometry_const``, ``bpy_angelo_read_traversal_const`` and the
core's own routes); each is bound to its exact source callback:

* Mesh, curves, point cloud and Grease Pencil element values and collections
  (``Mesh.vertices``/``edges``/``loops``/``polygons``/``loop_triangles``/normals,
  their element properties, ``Curves.points``/``curves``/``normals``,
  ``PointCloud.points``, Grease Pencil layers, groups, frames and offsets,
  ``Mesh``/``Curve`` texture space): the getters take writable spans (which
  un-share arrays) or fill the artist's lazy caches; the replacements read const
  storage or compute in an operation-private runtime.
* ``MeshVertex.groups``: the iterator creates the deform layer.
* ``Image.size``/``channels``/``depth``/``is_float``/``resolution``/``file_format``/
  ``frame_duration`` and ``UDIMTile.size``/``channels``: the getters load the
  image; cached metadata only.
* ``Object.bound_box``/``dimensions``: evaluated bounds when present, otherwise
  bounds of the stored data (``LOADS_FILE`` for text and volume objects, whose
  bounds would load a font or grid). ``Object.matrix_local`` (parent vertex
  lookup ensures BMesh tables), ``Object.data`` (would compute a pending
  subdivision), ``Object.active_material``/``MaterialSlot.material``/``name``
  (material helpers can repair the slot count).
* ``Driver.is_simple_expression`` (compiles and caches), ``RenderPass.rect``
  and fluid grids (copy whole buffers; a bounded prefix is copied),
  ``RenderSettings.threads`` (publishes the system thread count),
  ``PoseBone.matrix_basis``/``is_in_ik_chain``/``bone`` (rebuild bone indices),
  ``Action.frame_range``/``curve_frame_range``, cryptomatte ``matte_id``.
* Colour-space index and choices, view-transform choices (may initialise
  colour-management state), particle duplicate-weight and target names, particle
  edit state, hair-key location (writable layers and normal caches on the
  evaluated mesh), strip timing and lock (strip lookup cache), point cache
  index (fills collection caches), socket-type choices of node items, implicit
  conversions and interfaces (``EXTENSION_CODE`` for add-on trees), menu socket
  choices, annotation layer choices (create icon previews), action slot ID
  types, data-transfer layer choices (``NOT_EVALUATED`` until the scene is
  evaluated; the getter evaluates the dependency graph).
* ``Strip.fps``: movie strips are ``LOADS_FILE`` (the getter reopens the
  movie); clip strips answer from an already open clip; scene strips are
  computed. ``PointCache.info`` is ``LOADS_FILE`` (the getter checks cache
  files on disk and rewrites the stored text).
* ``TextureSlot.output_node`` (resets the stored output), ``CollectionImport``/
  ``CollectionExport`` properties (create the group), ``NodesModifierBake.node``
  and ``NodeSocket.node`` (ensure the topology cache),
  ``SequenceEditor.strips_all`` and ``NodeTreeInterface.items_tree`` (fill
  caches), ``Collection.all_objects`` (fills the object cache),
  ``NodesModifier.node_warnings`` (reduces the live evaluation log; the
  replacement reduces a request-local copy), legacy UV and colour layer data,
  ``HookModifier.vertex_indices``, ``SplineIKConstraint.joint_bindings`` and
  ``FModifierGenerator.coefficients`` (bounded prefixes).

Supplemental catalogue (``supplemental_catalogue`` in ``bpy_angelo_read.cc``;
keep the two lists in step). Guarded rows run the built-in getter only while
the guard holds:

* ``NodeSocket.label``/``is_linked``/``is_inactive``/``is_icon_visible``,
  ``NodeLink.is_hidden``: guarded by a current topology cache (the getters
  rebuild it); otherwise ``NOT_EVALUATED``.
* ``ViewLayer.layer_collection``/``active_layer_collection``/``objects``,
  ``LayerObjects.active``/``selected``, ``LayerCollection.children``: guarded by
  every view layer of the scene being synchronised (the getters resynchronise);
  otherwise ``VIEW_LAYER_OUT_OF_SYNC``. An out-of-date layer is refused rather
  than copied into a private snapshot.
* Brush ``map_mode``/``direction``/``stroke_method``, particle-edit ``tool`` and
  particle ``material_slot`` choices: guarded by a current editor context (no
  Python context override, synchronised view layers, the window's layer name
  resolving); otherwise ``VIEW_LAYER_OUT_OF_SYNC``.
* ``Image.pixels``, ``MovieClip.fps``, ``Text.is_modified``, ``Volume.grids``
  and its ``frame_filepath``/``error_message``, ``CacheFile.active_layer``:
  ``LOADS_FILE`` (they load or stat a file).
* ``ImagePreview.icon_id``: ``DENIED`` (creates the preview icon).

Values a replacement produces may live in the read's private runtime; on those
elements only replacement-routed and generated (DNA) properties are read.
Constraint target-space choices are an ordinary built-in getter (the callback
only builds temporary target descriptors).

**Limits.**

* A node's sockets are read from a node in the file. The sockets of a node type
  that no node in the file has are not data: finding them still takes a
  reviewed script that makes a scratch node.
* Per-element attribute values are not read (above).

The file as a file
------------------

``angelo_blend_file`` (``blend_file``, ``blend_file_listing``, ``blend_file_pending``,
``blend_file_paths``) answers for the file, where the queries answer for
objects of the bound scene. It has two scopes.

**The open file** (no ``file`` argument): datablock counts by type with the
number that have no user (a save and reload drops them), are kept only by a
fake user, or are linked; the first unused datablocks by name; linked libraries
with their file, status and datablock count; external files (images, sounds,
fonts, caches) as ``packed``, ``present`` or ``missing``, missing first; and
unresolved references: linked data its library no longer has, a missing
library, a node of a type this build does not know and a group node without a
group. One pass reads identity only (name, users, fake user, library, missing
flag); external files come from Blender's own path walk
(``bpy.data.file_path_map``), packed ones from their datablocks, and each
existence check is one ``stat``. ``list`` pages one part (``UNUSED``,
``FILES``, ``LIBRARIES``, ``UNRESOLVED``) or one datablock type with user
counts, 64 rows a page. Scenes, screens, window managers, workspaces and
libraries are never counted as unused: Blender keeps them without users. Data
used only by other unused data is not counted.

**Another .blend in the file workspace** (``file``, relative to the workspace
the file tools use): its datablock names and counts by type, the libraries it
links and the Blender version that saved it, without opening it. The path is
admitted by ``files.FileWorkspace.path`` (relative, not hidden, no link on the
way, no credential folders) and the file is opened anchored, without following
a link, to check that it is one regular file of at most 2 GiB that starts as a
.blend does. The native scanner reads only that held descriptor; it never
reopens the pathname or enters Blender's library-linking lifecycle. Plain,
gzip and Zstandard files share bounded streaming scans of headers, DNA and ID
name/library fields. No datablock, asset metadata, or script is loaded. The
held descriptor and directory identities, size, timestamps and link count are
checked again before returning; a change discards the answer (``CHANGED``).
User counts, missing files and unresolved references need the file's data and
are reported for the open file only. Running code against another file is out
of scope.

Workspace scanning uses the existing pending-operation controller and at most
two workers, with no live ``bpy`` access off the main thread. Stop signals the
native scanner at a read boundary; the worker retains and closes its descriptor
before releasing its slot. Summary and first-page scans make two passes; a deep
page makes three, preserving Blender's native name order without retaining all
names. A plain file is read in 64 KiB windows of positioned reads, one system
call per window rather than per block (Blender writes one block per weighted
vertex), and a payload past the window is skipped without being read. Each
request bounds aggregate compressed and decoded bytes, block count (32,000,000
headers over all passes: a 250 MB rigged scene walks 2.2 million in a summary,
a 2 GiB one about 29 million in a deep page) and size, DNA, codec allocations,
Zstandard window size and retained strings.
The production work budgets and a five-second deadline are declared in
``blend_file_listing``. Exceeding a work budget returns ``LIMIT_EXCEEDED`` and
the deadline ``TIME_LIMIT``, both distinct from a damaged file; cancellation
returns ``CANCELLED``. A budget refusal is not a complete listing. Five seconds
because a valid file with 2 GiB of decoded work took 3.46 s on a Windows test
VM and over two seconds on a loaded Mac; the scan runs on a worker,
so the wait blocks no editor, and Stop still ends it at a read boundary.

**Paths** (``blend_file_paths``). A referenced file is named relative to the
file workspace or to the .blend's own folder when it lies inside one of them,
and otherwise by its base name alone, marked ``outside``. No answer carries an
absolute path, ``..`` never appears, and a path the workspace refused is not
echoed. Blender's own message for a file it cannot read names the absolute
path, so the answer carries a code instead.

Every failure is an answer (``readable`` false with ``OUTSIDE_WORKSPACE``,
``NOT_FOUND``, ``NOT_BLEND``, ``UNREADABLE``, ``TOO_LARGE``, ``CHANGED`` ...).
Open-file bounds: 200,000 datablocks, 512 existence checks, 20,000 nodes and a two-second
cooperative deadline (the open-file scan runs on the main thread); a part that stops early is listed
in ``incomplete``.

Where the two tools sit, their bytes and their measured cost are in
``agent_integration.rst`` ("Toolset 36") and ``performance.rst`` ("Live read
tools").

Verification
------------

Host tests: ``tests/python/angelo_agent/test_native_scene_queries.py``,
``test_native_scene_coverage.py``, ``test_native_scene_query_freshness.py`` and
``test_native_scene_catalog_graphs.py`` in the same directory. The coverage tests
count fingerprint captures, so they pin how much of the scene each query reads.
Disposable native tests:
``tests/python/angelo_agent/test_blender_scene_queries.py`` with isolated
``BLENDER_USER_CONFIG``, ``ANGELO_CONNECTIONS_DIR``, ``ANGELO_STATE_DIR`` and
recovery directory, ``--background --factory-startup --python-exit-code 1``.
The native report records ``bpy.app.binary_path`` and OpenSubdiv availability.
It also counts real captures for object-scoped detail and discovery, and checks
covered deltas against outside edits, covered edits and a covered removal.
``test_blender_scene_query_freshness.py`` additionally checks every writable
semantic native field on supported constraints, unsynchronized live shading,
incoming edits without target revisions, full Noise controls and a two-turn
production Noise-to-transform program, including operator Undo/Redo. It uses
the production revision store, not a permissive fixture admission adapter.
``test_blender_camera_light_programs.py`` checks that unsynchronized camera
lens/clip/orthographic scale/focus and light energy/color/radius edits change
only their own object's revision, that a non-default light graph has none, and
the ``CAMERA_LIGHT`` detail section and delta field.
``test_blender_scene_catalog_graphs.py`` checks every catalog GN node with two
production turns, the standard bevel-and-shade recipe, and tampered metadata,
settings, links, interfaces, untracked dependencies and work-budget rejection.
``test_native_turn_context.py`` (host) and ``test_blender_turn_context.py``
(native, background) cover the turn context and change feed: ordering beyond
the 32-entry cap, a production APPLY bound only by turn-context revisions,
fail-closed commit after a manual edit, named and recently changed objects
(exact, delimited, longest-first matching; bounds, ambiguity, UID checks and
untrusted names), post-commit revisions binding the next program, feed reset
per turn, and epoch reports after Undo and load.
``benchmark_blender_turn_context.py`` measures its cost.
The live read tools: ``test_native_data_path.py`` (what parses and that
nothing else does), ``test_native_read_tools.py`` (resolution, bounds, every
withheld class and every wrong-path answer on RNA stand-ins, with the reads
counted so a withheld value is proven unfetched), ``test_native_read_access.py``
(stored/default values, callback suppression and exact-cache dispatch),
``test_native_blend_file_pending.py`` (worker admission, cancellation and cleanup),
``test_native_blend_file.py``
(path naming, the open-file summary, the workspace gates on real files with a
stand-in for Blender's reader), ``test_native_read_tools_delivery.py`` (where
each tool sits for each runtime, the real controller in a read-only chat, and
the real Claude and Codex providers over scripted stand-ins) and
``test_native_read_tools_recovery.py`` (in a turn: an answer with a code does
not stop a plan, a tool error is a failed lookup the model reads, a document
change still ends the turn, and an overlong text is refused in one line before
anything runs).
``test_blender_read_tools.py`` (native, background) plants a project and checks
the answers on Blender's own RNA and files; compares datablock identities and
user counts, the dirty flag, which images are loaded, the workspace's files and
the dependency-graph update count around every call, successful or not, and a
digest of every datablock's values around a 1,500-read sweep of the whole
document; reads again after an edit; drives both providers with Blender's own
answers; and searches every answer for the paths, script text, driver
expression and socket text it planted. ``benchmark_blender_read_tools.py``
measures time and answer size on a given .blend.
``test_blender_read_access.py`` checks real const vertex-group reads, exact
cached and uncached image tiles/frames, absent and malformed Cycles storage,
planted Python getters and dynamic enums, stored ID pointers and the approved
script route. ``test_blender_metadata_scan.py`` compares plain, gzip and both
Zstandard forms with Blender's native library listing, including deep pages of
100,013 similarly named IDs, a streamed 2 GiB decoded-work fixture, distinct
resource/deadline refusals and cancellation. Native unit tests
``MetadataScan.*`` and ``MetadataScanDNA.*`` cover malformed headers, codec
footers, DNA validation and descriptor ownership without loading a file.

These tests intentionally alter only their factory-startup fixture, including
Undo/Redo and saving/loading a temporary fixture blend. They never open a user
project, access credentials/accounts, start a provider, build Blender, or make
paid inference requests.
