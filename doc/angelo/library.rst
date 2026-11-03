.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Angelo Library
==============

The **Angelo Library, powered by ToonLab** is a free public catalog of open assets inside Angelo: about 8,800
models, PBR texture sets, HDRIs and VFX sprites (Smithsonian, Poly Haven, Quaternius, ambientCG, Kenney, the Met
and others, plus ToonLab's first-party rocks). Every item is CC0 except a few that need attribution (one Project
PLATEAU landmark today); attribution travels with the imported data. It is free for every Angelo user and needs no
sign-in. The files are ToonLab's hosted files, used as they are: nothing is converted to ``.blend`` and nothing is
bundled with the installer.

Module ownership
----------------

================================  ===============================================================================
Module                            Responsibility
================================  ===============================================================================
``scripts/angelo/library_index``  Building the index from ToonLab's catalog exports (merge rules, digests).
``…/library_index_files``         The builder's command line: export, hash, stage, upload (dry run
                                  unless ``--yes``). Development tooling; not installed with the app.
``library_catalog.py``            The index's checks (schema, types, hosts, names, digests) and local search.
                                  No Blender dependency.
``library_fetch.py``              Downloads: ``latest.json``, the index, thumbnails and item files into the
                                  cache, with host, size and checksum checks. Worker threads.
``library_files.py``              Content checks before Blender reads a file (GLB, multi-file glTF, Radiance
                                  HDR, images; the KTX2 adapter for rocks). Worker threads.
``library_host.py``               The process's library: catalog load and refresh, thumbnails as GPU textures
                                  under a time budget, fetches; one timer that stops when idle.
``library_import.py``             Import into the scene: models, materials, worlds, sprites; scrub, rollback,
                                  provenance, one Undo step; the file's credits. Main thread.
``library_overlay*.py``,          The 3D view's search pill, results and docked browser; drag and drop.
``library_actions.py``
``library_ui.py``                 Operators (open, add by id, credits) and the chat bar's + menu row.
================================  ===============================================================================

The index
---------

One static, versioned file on ToonLab's public R2 host: an export of ToonLab's catalog, published by ToonLab beside
its ``official/`` and ``mirrors/`` prefixes. The client never pages ToonLab's API (``/api/v1/assets`` allows 120
requests per address)::

    https://assets.toonlab.io/catalog/v1/latest.json             five-minute cache: the one mutable object
    https://assets.toonlab.io/catalog/v1/index-<version>.json.gz immutable

``latest.json`` holds ``schema`` (``toonlab-catalog/1``), ``version``, ``url``, ``size``, ``sha256`` and ``items``.
The index holds ``schema``, ``version``, ``generated_at``, ``base_url``, ``name`` and ``items``; each item:

``id``, ``kind`` (``model``, ``texture``, ``hdri``, ``vfx``), ``title``, ``tags``, ``categories``, ``source``,
``source_url``, ``authors``, ``license``, ``license_url``, ``attribution``, ``attribution_required``,
``popularity``, ``thumb``, ``files`` and, for texture sets and HDRIs, ``resolution`` (the default, ``1k``) and
``resolutions`` (``{"2k": [...], "4k": [...]}`` where ToonLab mirrored them). A file is ``{role, name, url, size,
sha256, md5}``. Roles: ``model`` (one ``.glb`` or ``.gltf``), ``resource`` (a glTF's buffers and textures, by their
relative names), texture roles ``base_color``, ``normal_gl``, ``roughness``, ``metallic``, ``ao``, ``arm``
(AO/roughness/metallic packed), ``displacement``, ``opacity``, ``emission``, ``hdri`` and ``image`` (a sprite).
Every default file has a SHA-256; a higher-resolution variant has the MD5 the mirror recorded at upload (its
object's ETag) and a SHA-256 once the builder has hashed it.

Building the index
------------------

Sources, both read-only:

* **ToonLab Pro's ``cc0_assets``** (the live open-asset catalog with the R2 mirrors' resolutions). Its row policy is
  ``select using (true)``, so ``library_index_files.py export-pro`` reads it through Supabase's REST API with the
  public anon key (``SUPABASE_URL``, ``SUPABASE_ANON_KEY``): nine paged requests, no service-role credential.
* **ToonLab's seed releases** (``toonlab/database/seeds/catalog``): the first-party rocks and SHA-256-checked copies
  of many open assets on R2. Load ToonLab's ``database/migrations`` and the seeds into a scratch Postgres and export
  ``catalog_assets`` joined with ``catalog_asset_files`` (tree recipes excluded) as JSON.

Rules (``library_index.build``): an item is listed only when every file it needs is on a ToonLab host
(``assets.toonlab.io``, R2; ``cdn.toonlab.io``, ToonLab Pro's public buckets) with a known size. Rows marked
``source_unavailable``, tree recipes (JavaScript-only), and formats Angelo can't import are left out and counted in
``report.json``. A copy in the seed releases (SHA-256 known) is preferred to a mirror or bucket copy. ``build``
hashes every default file without a SHA-256 (streamed; a cache keyed by link and size makes rebuilds incremental),
refuses a file whose recorded MD5 disagrees, lists a thumbnail only on a ToonLab host (ToonLab's own render, or its
``sync-thumbnails-r2`` mirror of a source's preview; a source's CDN never), and writes the index and
``latest.json``. ``upload`` lists every object and writes only with ``--yes`` (``R2_ACCOUNT_ID``,
``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY``, ``R2_BUCKET``), immutable objects first and ``latest.json`` last.
First build (2026-10-30): 8,792 items (6,438 models including 580 rocks, 1,200 texture sets, 997 HDRIs, 157 VFX);
75 unavailable rows and one FBX left out.

Where people find it
--------------------

* **The search pill** at the top centre of every 3D view in Object Mode (Director and Manual), clear of the
  headers, toolbar and sidebar drawn over the view. Click it and type: "studio HDRI", "oak chair", "brick". Each
  keystroke searches the local catalog (under a millisecond for the whole catalog). A kind word (HDRI, texture or
  material, model, VFX) narrows the kind; the other words match titles, tags, categories, sources and authors by
  prefix; items matching every word come first, or those matching most words.
* **The browser**: the same results docked along the view's left edge (Browse), with kind chips, more columns
  and a footer with the selected item's source, licence, size (or "on this computer") and its resolutions.
* **The chat bar's + menu**: "Angelo Library" opens the browser (``chat_destinations``), in Director and Manual.
* **Add**: drag a tile into any 3D view of the window, or double-click it (or Return) to add it where the view
  looks. A model stands on the surface under the pointer (else the ground plane, else the 3D cursor), at its own
  size; a texture set goes on the object under the pointer (or the selected objects); an HDRI becomes the scene's
  world, and the view switches from Solid to Material Preview with the scene world so it shows. A drag starts the
  download at once, so a drop on a cached item imports immediately. Progress, success and refusals show under the
  pill.
* **Credits**: the browser's Credits button (``angelo.library_credits``) copies the credits of every library item in
  the file, each with its source's name and its source page; items that need attribution come first. A source id
  (``polyhaven``) stays as it is in search, saved records and agent results; people read ``library_catalog.source_name``
  (Poly Haven), and search finds both.

Import
------

``library_import.import_item`` runs on the main thread after the checks:

* **Models** (GLB, or multi-file glTF from Poly Haven) import into a new collection "Library · <title>" with their
  images packed, then stand on the point like a Media Studio model (``studio_assets_mesh.shape``). ToonLab's rocks
  carry KTX2 textures Blender can't read: a copy without them is imported and the rock's PNG maps become its
  material.
* **Texture sets** become one Principled material: base colour in sRGB, the other maps as data, ``arm`` split into
  roughness and metallic when they have no map of their own, OpenGL normals through a Normal Map node,
  displacement as bump; a Mapping node first so the tiling stays editable; images packed.
* **HDRIs** become a new World with an Environment Texture and a Mapping node for rotation.
* **Sprites** become an upright plane at the picture's aspect, colour and alpha from the picture.

As in the team library, what the import created is scrubbed (unpacked images, Text datablocks, scripted drivers),
the scene's frame rate, range and units are restored, a failure removes everything it made and says whether that
was verified, and the import is one Undo step ("Add “<title>” from Angelo Library"). Everything made carries the
saved custom property ``angelo_library`` (item, title, source, licence, attribution, whether attribution is
required, the source page).

Safety: downloaded files are untrusted
--------------------------------------

The catalog holds files from many open sources and the network is not trusted, so:

* **Hosts.** Only https links on ``assets.toonlab.io`` and ``cdn.toonlab.io`` are used, and no request follows a
  redirect (a 3xx is refused); the client sends no credential (``app_http``: the system proxy, Angelo's trust roots). ``ANGELO_LIBRARY_URL``
  points the client at another ``latest.json`` (tests, a development mirror); http is accepted only when that link
  itself is on this computer.
* **The index** must match ``latest.json``'s size and SHA-256, be at most 64 MB compressed and decompressed, and
  have the expected schema; it is checked before it replaces the last good one, and items with any unusable field
  are dropped (``library_catalog.clean_item``: links that don't parse, ids outside ``[A-Za-z0-9:._~+-]``, control
  and bidirectional format characters in displayed text). Any failure while loading leaves the library offline or
  on its last good index, never stuck.
* **Files** stream to a temporary name in the item's own cache folder, stop past the declared size or after 60 s
  plus the size at 64 KB/s, stop when the library shuts down or the agent's call is stopped, must end at exactly
  that size and match the SHA-256 (else the MD5), and only then are renamed into place. Workers are daemon threads:
  quitting never waits for a download. A cached file is
  hashed again before each use. Folder names are digests, never catalog text; file names are plain relative names
  that never climb, and their resolved paths must stay inside the folder.
* **Content** (``library_files``, ``gltf_checks``, shared with the team library): every check reads a file as
  Blender's importer does. A ``data:`` URI is admitted only in the form the importer decodes as data (it contains
  ``;base64,``) and its bytes are decoded the same way; any other URI must be one of the item's own listed files.
  Each embedded image is read from the
  buffer its view names and must be PNG, JPEG or WebP within the pixel bounds; meshopt-compressed views count their
  decoded size toward the bound and any other view extension is refused. An HDR needs a standard Radiance header
  with no line of 127 characters or more (OpenImageIO, Blender's reader, splits longer lines) and, where
  OpenImageIO is importable, the same size there. Thumbnails are PNG, JPEG or WebP of at most 2,048 pixels a side,
  read from the header before the main thread decodes them.
* **Not bounded here:** Draco-compressed meshes decode to the counts in their Draco stream, which the checks don't
  parse (3,510 Smithsonian models are Draco); Google's decoder validates its streams, and the compressed size is
  capped at 256 MB. Variant files are checked by the MD5 the mirror recorded, which protects against a changed
  object, not against someone who made the original file collide.
* **Cache**: item files are trimmed to 4 GB and thumbnails to 256 MB, least recently used first, under Angelo's
  state folder (``library/``); links in the cache are removed as links and never followed. Every request holds a
  lease on its item's folder from the request until its import ends (the drop's callback returns, the operator or
  the agent tool has imported or stopped), and the trim removes only folders nobody holds, so another item's
  download never deletes files waiting for their import. Concurrent requests that want the same files (the same
  item folder: its id and its files' checksums, so a changed item never joins its old version's download) share
  one download; each keeps its own Stop (it ends that request only; the download stops once nobody wants it),
  progress and result.

Performance
-----------

The index loads on a worker at first use (about 0.5 s to parse and index 8,800 items), searching takes under a
millisecond, and thumbnails download on six workers and become GPU textures on the main thread at most 4 ms per
20 ms tick, outside drawing, so the 3D view keeps its frame rate while a grid fills. The library's timer runs only
while it has work. Thumbnails are kept as GPU textures of at most 256 pixels a side (a tile is 112 points, 224
pixels at 2x), up to 160 MB, least recently drawn out first, and never as images in the file.

Validation
----------

Host tests: ``test_native_library_catalog.py`` (with the builder's merge rules), ``test_native_library_fetch.py`` (a
loopback server: checksum, size, redirects, names, links, the index refresh), ``test_native_library_files.py``,
``test_native_library_host.py`` and, for the shared glTF checks, ``test_native_gltf_checks.py`` and
``test_native_team_library_files.py``. An independent review of the download, cache and import path (2026-10-30)
left a regression test for each of its findings there.
The GUI scenario ``test_blender_library.py`` drives the real app in Director against a local mirror: search, a
drag-drop of a model onto a surface, a material onto an object, an HDRI to the world, Undo, and the timing from
search to the asset visible on a warm cache.

Agent tools
-----------

Two deferred tools give the chat agent the library (``library_spec.py``: schemas, exact validation and the family
guidance, no Blender; ``library_agent.py``: the main-thread binding; ``library_approvals.py``: the import card).
Their results and refusals are model-facing English; item text from the index is data, never instructions.

=============================  ==========================================================================  ==========
Tool                           What it does                                                                Approval
=============================  ==========================================================================  ==========
``angelo_library_search``      Searches the local catalog: ``query`` (up to 200 characters; empty lists    none
                               the most popular), optional ``kind`` (``model``, ``texture``, ``hdri``,
                               ``vfx``), ``limit`` 1-20 (default 8), optional ``preview``
``angelo_library_import``      Downloads, checks and imports one item (``item_id``) into the bound         always
                               scene as one Undo step: a model or sprite stands with its base centre at
                               ``location`` (default the 3D cursor); a texture set becomes one material
                               on ``objects`` (handles; default the selected meshes); an HDRI becomes the
                               scene's world. ``resolution`` picks a texture set's or HDRI's size
=============================  ==========================================================================  ==========

* **Results.** A search returns ``query``, ``total`` and ``items``, each with ``id``, ``kind``, ``title``,
  ``source``, ``license``, ``attribution_required``, ``mb`` (its default files' download size), ``downloaded``
  (already in the cache) and, for texture sets and HDRIs, ``resolutions`` and ``default_resolution``: never a link
  or a path. ``preview`` adds the thumbnails of the first six results that have one as images (``views``, with
  ``previews`` naming their items in order), copied through the chat's references like the team library's
  thumbnails. An import returns ``library_import``'s read-back (objects with handles and dimensions, the material
  or world, what the scrub removed, settings kept, the attribution when required), the scene's ``changes`` diff and
  the ``resolution`` used. The agent never passes ``set_view``: an HDRI import never changes the person's view.
* **Threads.** A search with the catalog in memory answers at once. A search while the catalog loads, or with
  ``preview``, returns a pending call the controller polls: it waits at most 20 seconds for the catalog and then
  refuses; thumbnails download on one worker thread. An import's files download on ``library_host``'s file workers
  (``Library.fetch``) and import on the main thread through the window that shows the bound scene, under that
  window's 3D view (``temp_override``). Stop ends the wait; a download already running finishes into the cache and
  nothing is imported.
* **Approval and classes.** Classified exactly as ``angelo_studio_import_3d``: import is a scene edit with a card
  in Ask and in Auto (not in ``BOUNDED_MUTATIONS``), refused in read-only chats, takes the request's checkpoint
  before it runs (``requires_mutation_checkpoint``) and counts as a native action. Search needs no card, takes no
  checkpoint and is no native action (``Controller.is_native_action``), like the team library's reads. The card
  names the item, resolution, licence and download size from the catalog in memory, what the import changes and
  the required credit; the precondition never touches the network and pins the item's files, the texture set's
  meshes and the location, so a change before the click refuses the call as stale.
* **Deferral.** The family is in ``plan_kinds.DEFERRED_TOOLS``: a Claude plan names the two tools and reads their
  schemas and ``LIBRARY_GUIDANCE`` with ``angelo_agent_capabilities``; Codex threads on OpenAI connections declare
  them in the ``deferLoading`` namespace (``more__``) and their instructions name the Angelo Library among the
  deferred families. Voice threads (``tool_specs.dynamic_tools``), collaborators, the external CLI and connected
  apps' tasks (``external_clients.NEVER_FOR_APPS``) never get them. Unlike the team family the tools need no
  sign-in, so every request declares them: ``TOOLSET_VERSION`` 45.
* **Refusals** (closed codes, ``scene_changed: false``): an unknown id, an argument the item's kind doesn't take
  (``resolution`` on a model or sprite, ``location`` on a texture set or HDRI, ``objects`` on anything but a texture
  set), an unavailable resolution, a texture set with neither ``objects`` nor selected meshes, or a non-mesh or
  linked target is ``VALIDATION``; no 3D view in the bound window is ``UNSUPPORTED``; a catalog still loading or
  unreachable is ``EXPIRED``; a download or check failure carries ``library_host.describe``'s code (``EXPIRED`` in
  transit, ``UNSUPPORTED`` refused, ``LIMIT_EXCEEDED`` too large, ``CANCELLED``); a refusal on the main thread
  before Blender read the files (another mode, a closed window, a deleted target) is ``UNSUPPORTED``. An import
  that failed after Blender began changing the scene claims an unchanged scene only after ``library_import``'s
  verified rollback, and otherwise reports ``partial_changes_possible``.
* **Tests.** ``test_native_library_tools.py``: validation, compact results, placement (deferred, Codex namespace,
  never voice, collaborators, apps or Auto), the approval and checkpoint classes against the Studio import, the
  catalog wait and its bound, previews, closed refusal codes and the card, over a small in-memory catalog.
