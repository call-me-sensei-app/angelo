.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

##############
Web live link
##############

A developer edits a model in Angelo and sees every change in their own Three.js page, with its lights, shaders,
physics and state left as they are. Saving in Angelo writes the ``.glb`` the page loads, and the page reloads only
that model. The web is a fourth engine of the :doc:`engine live link <engine_link>`: the same listener, request card,
acknowledgements and agent tools. This document is the web's side: Angelo's adapter (``web_link_assets``), the
capture that lets ``angelo_view`` see the page, and the npm package Angelo's link talks to
(``web_link/angelo-live``, published as ``@call-me-sensei/angelo``, MIT, with the unscoped alias ``angelo-studio``
from ``web_link/angelo-studio`` that only re-exports it, in the same version). The wire contract between the package's two
halves and Angelo is ``web_link/angelo-live/PROTOCOL.md``.

.. contents::
   :local:

Design
======

* **The browser never talks to Angelo.** The package's Vite plugin runs in the developer's dev server (Node). Like
  Godot's plugin, it reads the endpoint record, proves the token and speaks ``angelo-link/1``. The page's half of the
  package talks only to that dev server, on the page's own origin: Vite's HMR socket for events, and same-origin
  HTTP for bytes. No CORS, no pairing code and no new listener on Angelo's side, so every protection of the live web
  preview and the engine link stays as it was.
* **Asset level, like every engine.** The page tracks each model it loads (``live.track(gltf, url)``). Every
  tracked copy of the asset updates; where the page placed it stays the developer's.
* **Nothing the developer built is rebuilt.** A live frame holds only the objects that changed, and the page
  touches only those: unchanged objects keep their Object3D, geometry, material and textures, with no GPU upload
  and no event, through reconnects and Angelo restarts too ("Live frames" below; maintainer, 2026-11-04).
* **Two layers.** Live frames (uncompressed ``.glb``, no animation) arrive while the person works. The save rewrites
  the asset file in the form it had (mesh compression, WebP), and the page applies it the same way, with no page
  reload.
* **Edits flow one way**, Angelo to the page. The page sends only requests, acknowledgements and the pictures Angelo
  asks for.
* **Pairing** with a one-time code, for pages with no Node dev server, is deferred (maintainer, 2026-11-04). It
  would need a fixed loopback port and a cross-origin surface.

The adapter
===========

``web_link_assets`` (``ENGINE`` ``web``, ``DISPLAY`` "Three.js", never translated):

* **A project** is a folder with ``package.json``: the dev server's root, which the plugin sends.
* **An asset** is a ``.glb`` named by its path in the project, ``/``-separated (``public/models/chair.glb``). The
  plugin maps the page's URL to that path; Angelo never sees a URL. Every shared check applies
  (``engine_link_assets.inside``). A path through ``node_modules``, ``dist``, ``build``, ``out``, ``coverage`` or
  ``bower_components`` is refused as outside the project's own files, because a package manager or bundler
  rewrites them.
* **A by-name lookup** (the agent's ``OPEN`` with a file name) reads the whole project but leaves out those folders
  (``NAME_SKIPPED``), along with every engine's dot-folders and links. Otherwise a web project's ``node_modules``
  alone would pass ``NAME_SCAN`` and make every lookup unsure.
* **The save** (``durable``) writes what Angelo's web export writes: Y up, lights in the unitless ``COMPAT`` mode,
  cameras, GPU instancing, modifiers applied, no custom properties. It keeps the mesh compression and image format
  the file had (``import_params`` reads its JSON chunk's ``extensionsUsed``):
  * meshopt over Draco;
  * WebP if it had WebP, else the images' own PNG and JPEG.

  A KTX2 file is written with PNG and JPEG, since the write-back runs no gltfpack.
* **Live scenes** (``live_options``) have the save's layout, uncompressed, with images embedded as they are and no
  animation, at the current frame. They are exported under ``LIVE_EXPORT_ID`` (``angelo_link_web``), and
  ``web_export.WebExportExtension`` gives each node ``extras.angelo_uid``:
  * an object's ``session_uid`` (stable for one Angelo process);
  * a bone's armature uid and bone name, with ``#leaf`` for the exporter's leaf node of a bone.

  That is how the page keeps its own state on an object through a rename. Saved files never carry it, and the
  hooks act on no other export. Baked materials reach live scenes as on every engine (``export_baked.EXPORT_IDS``).
* **Nothing is copied** into the project (``PLUGIN_SOURCE`` None). The + menu row **Add Angelo Link to a Three.js
  project…** checks the folder, puts ``npm i -D @call-me-sensei/angelo`` on the clipboard, names the Vite plugin to add
  and turns the link on. Angelo never runs npm and never edits a Vite config.

Live frames
===========

``web_link_live`` makes the web adapter's frames (its ``live_frames``, which ``engine_link_live.publish`` calls
instead of exporting the whole scene); ``web_link/angelo-live/PROTOCOL.md`` §1.2 is the wire form.

* **What changed** comes from the depsgraph (``note``, called by ``engine_link._on_depsgraph`` for a web link):
  every object it re-evaluated, plus the users of each mesh, material, image, node group or other data it updated
  (``changed_objects`` walks material node trees and their groups, each once). It finds users in the original
  objects and in what the depsgraph evaluated (``web_link_users.evaluated_users``: each evaluated object of
  ``depsgraph.ids`` judged once), so a material set by a modifier (geometry nodes, a Boolean's transfer) and an
  object of a collection that isn't in the scene reach their objects (a collection only geometry nodes instance is
  judged on its originals, which the depsgraph doesn't hold); then every object that shows one through instancing is
  found from the originals (``Shown``): a collection instance at any depth, geometry nodes that name the object or
  its collection (in a node socket, or in a modifier input, which Blender 5.2 keeps in
  ``modifier.properties.inputs``), a particle system, a vertex- or face-instancing parent. Its cost follows the
  scene's objects and references, never the copies they make (``test_blender_web_link_cost``: deciding took 0.05 to
  0.13 ms from 10,000 to a million geometry-nodes instances, where walking them took 11 to 1,046 ms; 1,000 and
  10,000 instancers of one collection, 5.5 and 61 ms; Mac 2, contended, 2026-11-05). If deciding fails, the frame is
  whole.
  Scene and collection updates name no object: each export reads which objects a full export would hold first, so
  a removal is always seen.
* **Pixels** written with no depsgraph update (through the API, ``update``, ``scale``, a bake's restore) or dropped
  (``buffers_free``), and image files rewritten on disk, start a publish too: whenever no edit waits, even while a
  still-scene check does, the link reads every image's ``Image.angelo_pixel_revision`` (Angelo's native count of
  pixel writes: drawing another pass, layer, frame or render slot never counts), unsaved state and files at most
  every 0.5 s (``web_link_live.due``), at most 256 files a time, in turn. Every file an image reads, as Blender
  names it (``blend_file_paths.read_files``: each UDIM tile's, the sequence frame its Image Texture nodes read, each
  view's), is stamped under the file it names (Make Paths Relative changes no stamp) by device, inode, size,
  modification and change times, so a same-size file replaced by a rename keeping its time is seen. A file first
  seen while the file system's clock hasn't passed its times (a volume that keeps times in coarse steps, such as FAT,
  exFAT or a share, moments after a save) is stamped by the SHA-256 of its bytes instead, read again at each poll
  until the clock passes them, once more then, and never after while it is unchanged (``change_keys``): each file
  keeps one stamp, in one form, while it is unchanged, so a save is one live update, never a second one when the
  clock passes it. Those reads are bounded to 64 MiB a poll (the image that crosses it is read whole, so each
  image's turn ends); an image not stamped yet waits for a later poll and is no change. A file first seen settled
  (on a volume that stamps fine-grained times, such as APFS, NTFS or a Linux disk, always) is never read.
  Render results and Viewer pictures are left out.
* **The export** writes only those objects. The exporter's ``gather_tree_filter_tag_hook`` (``filter_tree``)
  keeps the changed objects, every ancestor as an empty (so each transform stays local to its real parent), and the
  armatures and bones a changed skin needs. ``gather_scene_hook`` (``mark_nodes``) gives every node its key
  (``extras.angelo_uid``: an object's ``session_uid``; ``<armature>/<bone>`` for a bone, ``#leaf`` for the
  exporter's leaf node; an object inside a collection instance under its instancer's key, ``<instancer>><uid>``)
  and its role, ``content`` or ``parent``. ``gather_image_hook`` (``image``) names each image by the SHA-256 of its
  bytes and writes one every receiving page already has as a 1 x 1 stub. The exporter logs a failed hook and goes
  on, so a frame whose hooks didn't finish, or with a node without a key, is refused as a failed export.
* **Digests** (``node_digests``): each content node's name, transform, children's keys, mesh data, materials (with
  their images' digests), skin, camera and extensions, written into its ``extras``. A frame where no digest moved
  and nothing was removed is not sent.
* **Full frames** hold every node, all content, no stubs: a page's first frame, a page that asked again
  (``resync``), a page that fell behind, the frame after an undo, redo or file read (``engine_link._unsure``:
  objects may get new session uids), and when more than half the scene changed. Adding, deleting, renaming or
  reparenting an object makes Blender re-evaluate every object, so those edits get a full frame too; the page still
  changes only the nodes whose digest moved.
* **Delta frames** say ``base``, the version they apply to, and list ``removed`` keys; a removal list too long for a
  header (32 KiB) makes the frame whole. ``LinkServer.send`` never drops a delta: past ``MAX_DELTAS`` (8) waiting,
  the waiting scenes go, the editor is ``lagging``, and the next publish, scheduled at once, is whole.
* **The still-scene check**: after a delta (or a whole frame whose particle copies never settled, below), once the
  scene has been still for 2 s, for three times the last whole export's time and for ten times the last export's,
  one whole export compares every digest with what the pages have, and sends a full frame only when one moved
  (whatever deciding what changed missed). Its time never lengthens the wait before the next edit is sent. Each
  check that finds nothing doubles the time to the next one, up to 60 s; a correction brings it back to 2 s. Edits
  never push a check past that time: it runs at the first long enough pause. The check keeps its own time (``due``
  tells the link), so the pixel poll runs while it waits, and a publish that sent nothing never pushes it back.
* **A frame that raises** (the exporter failing, a full disk) is tried again: what it would have sent stays noted,
  and ``due`` asks for it after a back-off of at least three times the failed attempt, doubling with each raise in
  a row up to 60 s; a frame that returns resets it.
* **Particle copies** settle over more than one evaluation: a frame that holds any is exported until two exports
  agree, at most four times in all. A whole frame that settled has nothing a check could find; one that didn't is
  checked like a delta.
* **Measured** (``test_blender_web_link_delta``, Mac 2, contended, 2026-11-04): in a scene of 120 boxes, a skinned
  mesh and two collection instances, a one-box colour change exported in 5.4 ms (median of 15) and 2,096 bytes,
  a whole frame in 155.9 ms and 234,644 bytes (Mac 1, 2026-11-11: 6.2 ms and 451 ms; "Cadence" below).
* **Other engines** still get the whole scene. The same keys and digests could serve them: Godot's preview and
  Unity's and Unreal's live layers each build a whole glTF today, and would need a node-level patch path in their
  plugins before a delta saves them anything. Not changed yet.

Cadence
========

Frames go at the first change after a pause, then at most once a tick (0.1 s) while changes go on, each waiting for
the page's ``previewed`` of the one before (up to 1 s), and always the last change (:doc:`engine_link`, "Cadence").
The tick is the floor because both ends cost far less than it (Mac 1, M4 Pro, 2026-11-11):

* **Angelo's export** of a delta: a one-box colour change in the 120-box scene above, 6.2 ms median (p75 6.5,
  5.9 to 7.1; 15 runs, ``test_blender_web_link_delta``, the 1.0.10 app); the sample asset's colour change, 4.2 ms
  median over 33 frames. A whole frame of the 120-box scene took 451 ms, so its gap is 1.35 s: whole frames keep to
  a quarter of the main thread.
* **The page** (``e2e/bench.mjs``, SDK 0.1.1, headless Chrome on SwiftShader, Angelo's real link server): from the
  frame leaving the server to the page's ``previewed``, a colour delta took 1.3 to 2.5 ms median (one object to
  250k triangles) and a whole frame 2.5 to 20.6 ms median, p75 at most 22.5 ms (20 objects with two 2K textures).

So a cheap delta costs about 6% of the main thread at ten frames a second, and a page answers each well inside a tick;
a slower page or engine paces the frames itself through its answers.

**Measured in the app** (the 1.0.10 app, and the same app with this cadence's three files, on one machine; the
e2e sample page in a visible Chrome; Angelo fades the sample's ``metal`` material grey to green over 3 s, one change
every 0.1 s, after two single edits; three takes each; each colour timed when Angelo set it and when the page's
animation frame first showed it, on one wall clock):

==========================================  ==========================  ============================
                                            Before (1.0.10)             Cadence (1.0.11)
==========================================  ==========================  ============================
A single edit after a pause, on the page    0.391 to 0.409 s            0.077 to 0.088 s
The fade's last step, on the page           0.391 to 0.408 s            0.075 to 0.085 s
The fade's 29 inner steps the page showed   0, 0, 0                     29, 29, 28
Their lag (median; max)                     none shown                  0.077 to 0.087 s; 0.112 s
Page updates during the fade (interval)     none                        0.100 s median, 0.175 s max
==========================================  ==========================  ============================

A step the page didn't show was not lost: two of Angelo's changes fell in one tick and went in one frame. The lag
within a tick depends on when the change lands in it: from about the export and transfer (under 10 ms) to a tick
more.

The page
========

The package's browser half (``@call-me-sensei/angelo``), as ``web_link/angelo-live/README.md`` describes it:

* **Using it**: ``createAngeloLive({ renderer, scene, loader })``. The developer's own ``GLTFLoader`` is used, so
  their decoders load the saved files. ``live.track(gltf, url)`` registers a model. Alt-click on a tracked model, or
  ``live.edit(url)``, raises Angelo's request card. In a production build the module is a no-op stub, and the Vite
  plugin applies only to ``serve``.
* **Matching**: by glTF node identity, never a scene-wide name lookup. The first load matches by the node's name
  path, then by ``angelo_uid``; a key the page doesn't know in a full frame is matched by name path before anything
  is created. A delta whose ``base`` isn't what the page shows, or that names a parent or image stub the page
  doesn't have, is never applied: the page asks for a full frame (``resync``).
  * A changed node keeps its Object3D, swaps its geometry, and swaps its material only if the mesh still holds the
    material the package last put there, so a material the developer replaced stays theirs.
  * The tracked root's transform, parent and ``userData`` are always the developer's.
  * A rename renames in place. A deleted node goes, and children the developer attached to it move to the nearest
    surviving node, keeping their world transform (with an event and a console warning).
  * Only resources the package created are disposed.
* **Acknowledgements**: ``previewed`` with the number of tracked copies showing the version, summed over the dev
  server's tabs, and ``reimported`` after the saved file is applied (:doc:`engine_link`, "Acknowledgements").
* **The look** (``look: "apply"``) is reserved. The API and the capability name exist, but Angelo sends no look
  frames yet; the page's own renderer settings stay.

Captures
========

``angelo_view`` ``image: "WEB"`` shows the developer's page when one shows the linked asset live and said it can
``capture`` (``engine_link_capture``); otherwise the live web preview (:doc:`web_export`). The result's facts carry
``source: "link"``, the asset, the project's name, the live version and the version the page last acknowledged.

* **The request**: Angelo sends ``capture`` (an id, the asset, at most 1,280 pixels) to that one editor, and waits
  up to 8 s on the calling thread (``LinkServer.capture``).
* **The answer**: ``status`` ``captured`` with one PNG, or ``capture_failed`` with a reason (captures are off in
  the plugin's config, the tab is hidden, or no tracked copy is on screen).
* **What the page sends**: its WebGL canvas only, never the DOM, and only while Angelo asks. Captures are on by
  default and can be turned off in the plugin's config (maintainer, 2026-11-04).
* **The bound**: the only payload a client may ever send (:doc:`engine_link`, "Transport"). The server's frame
  reader allows it for a ``captured`` answer to that client's outstanding request, from a client that said it can
  capture, before its deadline. It must be a PNG of at most 8 MiB. One capture per client at a time, accepted once.
  Every other payload, and a non-PNG one, ends the connection before any of it is buffered.
* **Trust**: the picture is the page's, so it is data for the agent, never instructions.

Security
========

The trust boundary is the engine link's: same-user processes on this machine (:doc:`engine_link`, "Transport").

* **Angelo's listener** is unchanged and unreachable from a browser. It speaks only length-prefixed frames, and an
  HTTP request's first four bytes are longer than any header it accepts.
* **The bridge's routes** are checked on every request: a loopback peer whatever the ``Host`` says (so
  ``vite --host`` gives LAN devices nothing), a loopback or allowed ``Host``, ``Sec-Fetch-Site`` same-origin (or the
  dev server's own ``Origin``), and a per-start path token. They send no CORS headers and use
  ``Cross-Origin-Resource-Policy: same-origin``.
* **The token** is never logged, printed or sent to the page.
* **The web link is as private as the developer's dev server.** Another local user can already read what a dev
  server serves.

Validation
==========

* **Host** (no Blender):
  * ``test_native_web_link_assets``: projects, assets and refusals, the by-name lookup past a large
    ``node_modules``, the save's compression and image format, live options, the node-identity hooks with a
    stand-in ``bpy``, and the npm package's name.
  * ``test_native_engine_link_capture``: the capture over real sockets, every payload that must end the connection,
    one at a time, accepted once.
  * ``test_native_engine_link_state``: a web page's first live frame is whole, exported under the web's export id.
  * ``test_native_web_link_live``: digests, image stubs, the tree filter, keys in collection instances.
  * ``test_native_web_link_frames``: the frame decisions (full, delta, nothing, removals, resync, lagging, undo, a
    long removal list, a new link, the still-scene check) and the server's delta queue.
  * ``test_native_web_link_changes``: the pixel poll (only with the native revision, at most every 0.5 s, render
    results left out), unsaved pixels dropped, ``due`` (pixels while a check waits, a due check), and changed data
    reaching its users.
  * ``test_native_web_link_users``: evaluated users and instancers found without walking instances; every file an
    image reads stamped (UDIM tiles, a sequence's frame, a same-size replace by rename).
* **Blender** (``test_blender_web_link_delta``, inside Angelo ``--background``): the same with the real glTF
  exporter, and the timings above. ``test_blender_web_link_changes``: each edit the depsgraph doesn't report
  reaches the page on its own, with no other edit and no still-scene check (pixels, also while a check waits; users
  only the depsgraph knows; rewritten tile, frame and renamed files). ``test_blender_web_link_cost``: deciding a delta
  stays flat as geometry-nodes instances grow to a million.
  * ``test_native_engine_link_assets``: every adapter, including one with an npm plugin.
* **Package** (Node, ``web_link/angelo-live/test``): unit, security and contract tests (the Node bridge against the
  real ``LinkServer``), and an end-to-end run with a stand-in Angelo, a real Vite dev server and headless Chrome over
  sample apps the matcher was never developed on.
