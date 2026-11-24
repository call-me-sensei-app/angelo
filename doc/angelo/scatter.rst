.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Scattering with biomes
======================

A scatter system places layers of assets on a surface by rules, as plain geometry nodes the person can open and edit
(``scatter_nodes``, written by code; no node-group file ships). The surface is never changed: the system is a new
object whose Geometry Nodes modifier reads it. Assets are objects in the file, from the person's scene or imported
from the Angelo Library (``angelo_library_search`` / ``angelo_library_import``: ToonLab's open catalog, Fab when
connected); Angelo ships no content pack.

Status (2.1.0, in progress): layers, rules, the biome file format and six presets (``scatter_spec``), the node
group builder (``scatter_nodes``), the system with culling and proxies (``scatter_system``), the operator and its
+ menu row (``scatter_ui``), the Scatter card with Library models per biome layer (``scatter_card``) and the agent's
declaration (``scatter_ops``) are implemented and proven in the app (``test_blender_scatter``, and in Director Mode
``test_blender_scatter_card``). Not built yet: the agent operation's wiring (it waits for the shared ``wrapped_ops``
code).

Layers and rules
----------------

A layer has a name, 1 to 16 assets with weights, a density (points per square metre, Poisson-disk spaced), a
spacing (collision radius multiplier), a transform (scale range, align to the surface 0 to 1, random yaw, tilt) and a
seed. Its density is multiplied by its rules, each a factor from 0 to 1 eased over ``soft`` with the Map Range node's
Smooth Step (``scatter_spec.band``):

* ``slope`` {min, max, soft}: degrees from horizontal.
* ``height`` {min, max, soft, relative}: world Z in scene units, or 0 to 1 over the surface's height.
* ``mask`` {name, invert}: a float attribute or vertex group on the surface; painting it is Blender's own weight or
  attribute paint.
* ``distance`` {objects, keep_away | near, falloff}: keep out of a radius around other objects (paths, buildings),
  or stay within one.
* ``noise`` {scale, amount}: patchiness.

Layers are placed in order; each keeps its points apart by its collision radius (half its asset's footprint
diagonal at its largest scale, times its spacing; ``collision_radius``) and avoids the earlier layers' points by the
sum of both radii, so big trees placed first keep shrubs out of their trunks.

Biome files
-----------

``*.angelo-biome.json``, schema ``angelo-biome/1``, at most 64 KiB: ``name``, ``description`` and ``layers``. An
asset in a file is a Library item (``library_id``, or a ``query`` with ``kind`` resolved through the Library when
the biome is applied; the chosen items are then kept so reapplying is deterministic) or an object by
``object_name``; a file never holds a scene handle. ``dump_biome`` checks before writing, so a saved file always
loads. Presets (``assets/biomes``): conifer slopes, mixed temperate forest, meadow, rocky scree, desert scrub and
dune grass, written from scratch. A preset whose search finds nothing says so and lets the person pick.

The system
----------

``angelo.scatter`` adds one object (named by ``system_name``, at the world origin) with one Geometry Nodes modifier
running a new ``Angelo Scatter`` node group built for its layers; it never edits an existing group, so Undo removes the
system in one step. The group reads the surface through an Object input in world space; each layer is a Poisson-disk
distribution whose density factor is its rules' product (``scatter_spec.band`` as the Map Range node's Smooth Step),
whose minimum distance is twice its collision radius, minus the points within the sum of both radii of an earlier
layer's, instanced from its assets by weight (``angelo_layer`` on each instance names its layer). A distance rule
measures to its objects' surfaces in 3D (a curve as its wire). The modifier's inputs change the system without a
rebuild:

* **Density Scale** (0 to 1) and **Seed**.
* **Cull Camera**, **Cull Viewport**, **Cull Render**, **Cull Distance**: keep only what the camera can see, widened
  by each layer's radius so nothing visible pops, and nothing farther than the distance (0: no limit). The camera's
  field of view is read when culling is set (``Cull Tan X``/``Y``); without a camera nothing is culled.
* **Viewport Display** (the share of instances drawn in the viewport) and **Proxy** (points at the instances'
  places in the viewport instead of the assets). Renders always place the assets.

The operator
------------

From the chat bar's **+** > **Create** > **Scatter** (or F3), ``angelo.scatter`` opens the Scatter card (`The
card`_). From its properties alone (the agent's path and scripts), ``layers`` takes the layers as JSON and ``biome`` a
preset whose layers take their objects from ``biome_assets`` ({layer name: [object names]}). It refuses, saying the
fix: a Library item in a layer (import it first with ``angelo_library_import``), layers given with a biome, a call with
neither, an asset that is the surface or has no width, a missing object, culling without a camera, and more than
1,000,000 instances at full density (lower ``density`` or ``density_scale``). ``from_selection`` makes one layer of
the selected objects with the operator's density, spacing, steepest slope, follow-surface, scale range and seed (its
redo panel shows them and the culling options).

The card
--------

``scatter_card`` (``card_host``), centred over the 3D view, on the mesh that was active when it opened:

* **Selected objects**: everything else selected, as one layer at a chosen density (0.1, 0.5, 1 or 4 per square
  metre) and the defaults otherwise (``scatter_ui.selection_layers``).
* **Biome**: one of the presets, named and described in the interface language (``scatter_text``: a host test fails
  when a preset or a layer has no translatable label). Each layer shows the objects it will scatter and two buttons:
  **Use Selection** (the selected objects other than the surface) and **Find in Library** (up to four models for the
  layer's own words, the preset's queries; a notice when the Library has none, and the person picks their own
  instead; nothing is ever substituted). **Add** downloads a model and imports it through the Library's own import
  (one Undo step, provenance and credits kept), then hides its collection in the viewport (the view layer's eye, so
  Geometry Nodes still reads it) and in renders, so only its scattered copies show.
* **Scatter** is enabled once every layer has objects. It runs the operator's own path (``scatter_ui.apply_request``)
  and pushes one Undo step; the card then moves to the corner with the instances per layer and names any layer that
  placed nothing. A refusal stays on the card, in the operator's words.

The agent's operation (``scatter_ops``) is declared for the shared typed-operation pattern: target the surface
(``MESH``, Object mode), parameters as above, ``creates`` one object and ``new_ids`` one node group, nothing existing
mutated, levers ``density_scale``, ``cull_distance`` and ``proxy``. Its checks: the surface unchanged, one new system,
every layer placed something (or which layer to loosen), no two instances closer than the sum of their radii.

Validation
----------

``test_native_scatter_spec``: every rule's factor in closed form and their product, layer defaults and refusals,
collision radius, caps, biome round trip, no scene handle in a file, schema and size limits, and the presets.

``test_native_scatter_ops``: the declaration is plain data, its schema valid with defaults that validate, every
parameter an operator property with the same default, revision 4's fields, the presets fit the schema, and the checks
and receipt on snapshots.

``test_blender_scatter`` (background): on a terrain whose slope and height are known in closed form, every instance
holds its rule (slope, height, relative height, mask and its inverse, keep away, near); spacing across layers; the
same seed gives the same instances and another seed others; Density Scale halves a layer; Viewport Display and Proxy;
camera culling keeps only what the camera sees, the cull distance, render culling leaves the viewport and no camera
culls nothing; the surface and assets unchanged; ``angelo.scatter``'s one object and one group and its refusals; and
every preset with stand-in objects.

``test_native_scatter_card``: every preset and layer has a label; the card's layouts for a selection and a biome
(controls per layer, Scatter waiting for every layer), Library results, a new biome starting empty, the result, the
request, and layouts that fit at several region sizes and scales.

``test_blender_scatter_card`` (GUI, Director Mode, a Library mirror served by the suite): the + menu row opens the
card on the active mesh; Meadow's layers filled by Use Selection and two Library models found and added (hidden in
the viewport and renders); Scatter places every layer on the unchanged surface; the 3D view shows the instances; one
Undo removes the system and keeps the imports. Screenshots at each step.
