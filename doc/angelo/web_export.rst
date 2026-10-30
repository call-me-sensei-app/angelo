.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

##########################
Web export and live preview
##########################

Angelo exports scenes to the web as glTF for Three.js. It does not embed a
JavaScript engine or browser view: the person's own browser shows the result.
This document is the contract for the web export preset, its checks and size
budget, and the live browser preview (Angelo 1.0.6).

.. contents::
   :local:

Surfaces
========

One implementation serves three callers with the same defaults, so a person,
Angelo's agent and an MCP app get the same file:

* **The person**: File > Export > Web (.glb, Three.js) (``angelo.web_export``,
  a file browser with the four options) and File > Export > Live Web Preview
  (``angelo.web_preview``; Stop Web Preview while it runs). The status bar
  reports the budget; the preview page lists the findings.
* **The agent**: three deferred tools (``web_export_spec``); a turn that never
  uses them sends only their names.

  * ``angelo_web_check`` (read-only, no card): the findings and an estimated
    budget; nothing is written.
  * ``angelo_web_export`` (approved): one .glb at ``output``,
    workspace-relative, default ``web/scene.glb``, the measured budget and the
    findings. It writes like a job's output (``job_outputs``): held folders
    that are never links, a free name when the name is taken, never over a
    file, and a snapshot like any file edit.
  * ``angelo_web_preview`` (approved; ``START`` or ``STOP``): starts or
    refreshes the live preview and returns its loopback URL with the budget and
    findings. It takes no snapshot: it writes nothing in the workspace.

  Check and export take ``meshes`` (``AUTO``, ``MESHOPT``, ``DRACO``,
  ``NONE``), ``textures`` (``AUTO``, ``WEBP``, ``KTX2``, ``KEEP``), ``merge`` and
  ``selection_only``, all optional.
* **MCP apps**: the check at the Read level, the export and preview at Edit
  (``external_clients``): one writes a file, the other starts a local server.

Results are English, carry no local path (the workspace-relative ``output``
and the loopback URL only) and always include a note that the web result is
Three.js rasterization. Nothing ever claims the web looks identical to Blender.

The export
==========

``web_export.export`` runs Blender's bundled glTF exporter (io_scene_gltf2)
with web settings and measures what it wrote. It never changes the person's
mode (``web_export.run_exporter``): Blender's exporter switches the active
object to Object Mode and never back, so Angelo runs it with no active object in
Sculpt and the paint modes (their data is already in the mesh), and after an
export from Edit Mode (an explicit one; the live preview waits) puts the objects
back into Edit Mode. It reads the scene only: no
Blender data changes, so an export causes no depsgraph update and no Undo step.

* **Meshes**: ``AUTO`` is meshopt when the build has Blender's meshoptimizer
  bridge (one small decoder in Three.js), else Draco, else uncompressed. A
  requested method the build lacks falls back to uncompressed with a finding.
* **Textures**: ``AUTO`` is KTX2 (Basis Universal) when gltfpack is set up
  (the glTF add-on's *Path to gltfpack* preference, or ``gltfpack`` on PATH),
  else WebP. Blender ships no KTX2 encoder of its own, so KTX2 always needs
  gltfpack; if gltfpack fails, the export is written again with WebP and a
  finding says so. ``KEEP`` writes the images' own PNG and JPEG.
* **Lights** use the exporter's unitless mode (``COMPAT``), the convention
  Three.js scenes use. Physical units would give Three.js a 3 W/m² sun as
  about 2,000 lux, which its renderer shows blown out.
* **Everything else**: modifiers applied, Y up, cameras, punctual lights,
  animations, GPU instancing for instances (``EXT_mesh_gpu_instancing``), no
  custom properties, and the exporter's own settings are never saved into the
  scene (``will_save_settings`` off).

Safe draw-call merging
----------------------

A page pays one draw call per mesh primitive of each node. With ``merge`` on
(the default), ``web_merge`` joins static objects that share a material into
one primitive, inside the exporter's in-memory glTF tree before compression
(``WebExportExtension.gather_scene_hook``; Angelo exposes it as the add-on's
``glTF2ExportUserExtension``, and it acts only on exports whose
``gltf_export_id`` is Angelo's, never on the person's own glTF exports).

A node is merged only when nothing on the web can tell the difference: its
object has no animation, drivers or constraints on it or any parent; the node
has no children, skin, morph targets, camera, light or extensions; its mesh is
drawn by no other node (merging a shared mesh would copy it into the download
once per copy); and every primitive is triangles with only positions, normals,
tangents, UVs and colours. World transforms are baked into the vertices
(normals by the inverse transpose; mirrored objects keep outward faces). A node
moves only when merging saves a draw call. Merged objects lose their own names
on the web, which is why ``merge`` false keeps every object. A merge that
fails leaves the tree as the exporter made it.

The size budget
---------------

``web_budget`` reads the written .glb itself: total bytes, triangles and draw
calls over the default scene's node tree (a GPU-instanced node draws once),
mesh and material counts, each texture's format, bytes and pixel size (PNG,
JPEG, WebP, KTX2) and the extensions the file requires, so a page knows which
decoders it needs. Compressed meshes keep their accessor counts in the JSON, so
the counts are exact for meshopt and Draco files. ``angelo_web_check`` estimates objects,
triangles, materials, textures and draw calls before merging without writing.

What doesn't carry over
=======================

``web_facts`` describes what an export would include (objects visible in the
view layer, or selected ones), and ``web_compat`` turns it into findings, each
with a severity (error, warn, info), a subject, a message and its fix:

* material nodes the exporter does not read (procedural textures, ramps,
  bump, other shaders) and nodes it reads only in simple uses (mixes, math,
  constants); displacement, volume and subsurface; a material output with no
  surface shader. The classes follow what io_scene_gltf2 5.2 reads; the fix
  is a bake.
* area lights (glTF has none) and soft shadow radius; the world (not
  exported: the preview adds a neutral studio environment).
* volumes, Grease Pencil, light probes, speakers and lattices; metaballs;
  particle systems; modifiers whose result moves over time (frozen at the
  current frame).
* UDIM tiles, movie and sequence textures, missing image files, textures
  over 4,096 pixels (many phones' limit).
* motion blur, depth of field and compositor effects; more than a million
  triangles.

The message and fix are English templates marked for translation. The agent
reads them filled in English (``web_compat.english``); the page shows them
translated (``web_compat.render``). Object, material and image names are data,
never translated.

The live preview
================

``web_preview.start`` exports the scene of the window it starts from into a
temporary folder, starts the server and opens the page in the person's normal
browser (``wm.url_open``). The page is Angelo's own design (the sign-in pages'
language: warm near-black, Fraunces display, Inter text, JetBrains Mono labels,
the inline wordmark, light theme from the system). It shows the scene, the
size budget, what doesn't carry over and the settings used, and keeps the
person's orbit view across reloads; it starts from the scene camera when the
file has one.

**The report column** (``web_preview/panel.js``) sits over the scene, on the
right or, in a window 760 px wide or less, as a sheet along the bottom. Its
chevron folds it to its title; the button beside it (Hide the report, or the
**I** key) hides it whole, so nothing covers the scene in a small window. A
hidden column is invisible, unclickable and inert (out of the tab order); one
40 px button stays in the top-right corner to bring it back (Show the report,
or **I** again), with the live state's dot (live, updating, closed), so the
person still sees when an export lands. The key is ignored with Ctrl, Cmd or
Alt held, on auto-repeat and while typing in a field. The fold and the hidden
choice are the person's: a live update rewrites only the column's text (the
page is never reloaded for one), and the page keeps both in its own storage
(``localStorage``, every access guarded, so a private window or blocked site
data only makes the choice last the visit), so a browser reload keeps them.
Storage belongs to the page's address, whose port changes each time the
preview starts, so a new preview opens with the column shown. Until the saved
choice is applied the column stays invisible, so a hidden one never flashes on
load. The controls' labels come translated with the page's text.

**Every change re-exports.** A ``depsgraph_update_post`` handler notes changes
to the followed scene, whoever made them (the person, the agent, an MCP app).
An update counts when it touches data other than the scene, or an object's
geometry, transform or shading; an update without those (a selection click)
counts only when something the export reads moved: the frame, frame range,
world or camera, or which objects show (hiding, renaming). A timer re-exports once the
scene has been still for the debounce delay, and never during a modal
interaction (``input_policy.interaction_active``: a drag, a brush stroke),
animation playback, a background job or Edit Mode (``scene_changes``): Edit
Mode's changes reach the mesh when the person leaves it, and the export follows
then. The delay is at least 0.6 s and at
least three times the last export's duration (at most 10 s), so the preview
takes at most about a quarter of the main thread however large the scene.
Nothing runs while the preview is off: the handler and timer exist only while
it runs. The page learns of a new export from a server-sent event stream and
reloads it; a failed export keeps the last good one on screen and says so.

**The server** (``web_preview_server``) listens on 127.0.0.1 on a free port,
serves only GET and HEAD under a random 16-byte path, refuses a Host header
other than its own loopback address (so another site cannot read the scene
through DNS rebinding) and anything outside its page folder. Every response
has ``Cache-Control: no-store``, ``nosniff``, no referrer and a strict
Content-Security-Policy (``default-src 'none'``; scripts from the page and its
import map's hash; ``wasm-unsafe-eval`` for the decoders; ``blob:`` workers and
images for the loaders). Its threads never touch Blender: the main thread
publishes each export.

**It stops** on Stop Web Preview or ``angelo_web_preview`` ``STOP``, when the add-on unloads, at
Blender's ``exit_pre`` and, for its socket and threads only, at ``atexit``.
The temporary folder is removed.

**Three.js is bundled**, never fetched: ``web_preview/vendor/three`` holds the
three.js 0.185.1 files the page loads (core, GLTFLoader, DRACOLoader,
KTX2Loader, OrbitControls, RoomEnvironment, MIT, ``LICENSE``, and the libraries
three.js ships prebuilt: the Draco and Basis Universal decoders, Apache-2.0; the
meshoptimizer decoder, KTX-Parse and zstddec, MIT, zstddec with a BSD-licensed
Zstandard decoder), 1.9 MB unmodified. ``MANIFEST`` lists each file's SHA-256
and ``test_native_web_export`` checks them, and that the page names no network
URL. Their licenses, and Basis Universal's NOTICE, ship in
``release/license/web-preview``, whose ``README.txt`` lists every bundled file
under its component (``test_native_web_preview_notices``). Refresh all of them
together from one npm ``three`` release, and the license texts from each
project's repository. Angelo bundles no gltfpack: KTX2 textures use the
person's own (the glTF add-on's gltfpack path, or one on ``PATH``).

**Translation**: the page's text (``web_preview.PAGE_TEXT``) and the findings
are translated when the preview starts or exports, with the interface
language. Arabic shows English on the page, because Angelo's Arabic catalogue
holds the visual form Blender draws, and a browser lays out right-to-left text
itself.

Web shaders
===========

A material can carry a **web shader** (``Material.angelo_web_shader``, set in
Properties > Material > Web Shader): one of the built-ins (Toon, Rim Glow,
Hologram) or a GLSL file in the project, and its uniforms, edited as ordinary
sliders and colour fields. The material's own nodes stay the stand-in in
Blender; the panel says plainly that the browser shows the real look, with
Live Web Preview one click away. The nodes are never changed.

* **Export**: ``WebExportExtension.gather_material_hook`` writes
  ``{"angelo_web_shader": {"kind", "uniforms", "file", "language"}}`` into the
  material's glTF ``extras`` (Angelo's web exports only). Other tools ignore
  extras. ``file`` is relative to the .blend's folder, or the bare file name for
  a file outside it, never a local path. ``web_compat`` replaces the stand-in's
  node findings with one note that the browser shows the web shader, and an
  unusable file (missing, not GLSL, over 256 KB) is an error: the web then
  shows the nodes, whose findings count again.
* **Files** (``web_shader_spec``): a GLSL fragment shader (``.glsl``,
  ``.frag``) ending with its ``main()``'s closing brace. Angelo declares
  ``uTime``, ``map`` and ``uHasMap`` (the material's base colour texture from
  the glTF), ``uResolution``, ``uLightDirection`` (toward the scene's first sun,
  in view space) and the varyings ``vUv``, ``vNormal``, ``vViewPosition``,
  ``vWorldPosition``; the file declares its own ``uniform float`` and
  ``uniform vec3`` values, which become the panel's sliders (``// <min> <max>``
  sets a range, ``= <value>`` a default) and colours. A ``#pragma angelo
  vertex`` line starts an optional vertex shader that replaces Angelo's. Read
  Uniforms re-reads the file and keeps the values of names it already had.
  Three.js TSL shaders need its WebGPU renderer, which the preview does not use
  yet: a TSL file is a finding ("TSL web shaders arrive in 1.1"), never a
  broken look, and the web shows the nodes. TSL comes in 1.1 with a WebGPU
  page (maintainer decision, 2026-10-22).
* **Preview**: the page swaps each web-shaded material for a ``ShaderMaterial``
  (``web_preview/shaders.js``) that binds the glTF's texture, the uniforms and
  time. Each shader is compiled alone, drawn once off screen; one that fails
  keeps the stand-in, and its compiler log goes back to Angelo
  (``POST feedback``). A uniform edit or a shader file save reaches the page
  within a quarter of a second without a new export (``web_preview``
  watches the shaders' settings and files' modification times on its timer);
  the debounced re-export follows as for any change. Skinned and morphing
  meshes keep their stand-in look in a custom shader's vertex stage.
* **Verifying**: ``angelo_view`` with ``image: "WEB"`` (``view_web``) asks the
  open page for a picture over its event stream; the page draws its canvas into
  a PNG of at most 1,280 pixels and posts it back. The result carries that
  picture and each web shader's status, so the agent checks a web-shaded object
  through Three.js, not the viewport. With no page open, or a hidden tab that
  draws nothing for 8 s, the result says so. The server accepts these posts
  only from its own page (Host and Origin), up to 8 MB for a PNG and 64 KB for
  a report.

Validation
==========

* Host: ``test_native_web_export`` (arguments, findings, budget parsing, the
  tools' families, the pinned Three.js build), ``test_native_web_preview``
  (server isolation, the event stream, merging), ``test_native_web_preview_page``
  (the report column: its hide control and the corner button back, a hidden
  column out of the way at every width, guarded storage, live updates keeping
  the choice, the key's guards, translated labels) and ``test_native_web_shader``
  (file uniforms, extras, the page's built-ins matching Blender's, the capture
  and shader report channel).
* Browser, for a change to the page: ``PreviewServer`` needs no Blender, so a
  short script can serve this checkout's page, publish a small .glb and report,
  and publish again on demand. Check the column at a wide (1280 x 800) and a
  narrow (400 x 760) window: hide it, publish a live update while it is
  hidden, reload the browser, and bring it back with the corner button and with
  **I**; the column stays hidden through the update and the reload and shows
  the new budget when it returns.
* GUI: the replayable scenario ``tests/python/angelo_agent/gui_web_preview.py``
  builds a scene with merges, losses and three web shaders (a built-in, a file,
  a broken file), starts the preview with the person's operator, checks the
  page, the debounce and the agent's route, then opens the page in a headless
  browser (``headless_page.py``: Chrome, Chromium or Edge with no window, a
  throw-away profile and WebGL on SwiftShader, so no person is needed) for the
  page's side: the shaders' compile reports (the broken one's error included),
  a uniform edit reaching the page without an export, and a capture through
  ``angelo_view`` WEB that is not blank (``web-capture.png``, for a look).
  ``ANGELO_QA_BROWSER`` names the browser; ``none`` waits for a person to open
  ``preview-url.txt`` instead. ``test_native_headless_page`` pins the browser's
  command line.
