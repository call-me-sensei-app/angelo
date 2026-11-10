.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

##########################
Web export and live preview
##########################

Angelo exports scenes to the web as glTF for Three.js. It does not embed a
JavaScript engine or browser view: the person's own browser shows the result.
This document is the contract for the web export preset, its checks and size
budget, the live browser preview (Angelo 1.0.6), and the scene's look that both
carry: tone mapping, exposure and the World as an environment (Angelo 1.0.9).

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
    file, and a snapshot like any file edit. Its result also carries
    ``preflight``: the engine preflight's ``WEB`` rules, and ``readback:
    "running"`` while a background Angelo reads the written file back; that
    comparison arrives as a chat notice and in the next request's ``preflight``
    observation (:doc:`engine_preflight`), report-only. The live preview never
    runs it.
  * ``angelo_web_preview`` (approved; ``START`` or ``STOP``): starts or
    refreshes the live preview and returns its loopback URL with the budget and
    findings. It takes no snapshot: it writes nothing in the workspace.

  Check and export take ``meshes`` (``AUTO``, ``MESHOPT``, ``DRACO``,
  ``NONE``), ``textures`` (``AUTO``, ``WEBP``, ``KTX2``, ``KEEP``), ``merge``,
  ``selection_only`` and ``baked_materials`` (on: an object with a current
  baked material from a texture-set bake exports with it, in the file only;
  see ":doc:`long_jobs`, Baked materials at export"), all optional. Results
  carry ``baked`` (``used``, ``stale``, ``packing``).
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
* **The look**: the scene's view transform, look and exposure as three.js
  renderer settings, and its World as an environment, in the glTF scene's
  ``extras`` (``angelo_look``, below).
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
object has no animation, drivers or constraints on it or any parent, and no
game physics on it or a parent (a merge would leave a body or shape behind:
:doc:`game_physics`; the export writes it as OMI, which Three.js ignores); the node
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

The look
========

Blender shows a scene through its colour management (view transform, look,
exposure, gamma) and lights it with its World. Three.js has its own tone
mappings and an environment map. The export carries both, so the preview, and a
developer's own page, show the scene in the nearest look Three.js has. It is
the nearest, never the same: the findings say what differs and by how much.

**Tone mapping** (``web_tone``, ``web_look``). The three.js tone mapping is
chosen by measurement, not by name. Blender's own OpenColorIO processor for the
scene's view transform and look (on the sRGB display) and each three.js 0.185.1
tone mapping (its shader code, ported line for line) render one test set: a grey
ramp from 6.5 stops under to 6.5 over middle grey, and the 24 ColorChecker
patches at six exposures. The candidate with the smallest mean difference of
display values wins. So a studio's own OpenColorIO configuration is handled the
same way as Blender's. With Blender 5.2's configuration:

=======================  =========================  ===========
View transform           three.js                   Difference
=======================  =========================  ===========
Standard                 Linear                     0.0%
Khronos PBR Neutral      Neutral                    0.1%
Raw                      Linear, linear output      0.0%
Filmic                   AgX                        2.5%
AgX                      AgX                        3.3%
ACES 1.3                 Neutral                    5.1%
ACES 2.0                 Neutral                    7.3%
Filmic Log               AgX                        11.1%
False Color              AgX                        42.2%
=======================  =========================  ===========

At most 0.5% is a counterpart (no finding). Up to 4% the finding is
information; beyond it, a warning. Three.js's AgX is an earlier version of
Blender's, hence 3.3%. A look (AgX - Punchy, the contrast looks) is measured
with its view and can change the nearest mapping (AgX with Punchy is nearest to
Neutral, 8.1%). Exposure carries over exactly, as ``toneMappingExposure`` =
2^exposure: both apply it to scene-linear light before the curve. Gamma,
curves, white balance and a display other than sRGB have no three.js setting:
each is a finding. For Angelo's own colour configuration every view and look
is measured ahead (``web_tone_known``, written by
``tests/python/angelo_agent/make_web_tone_known.py`` and checked against the
configuration's digest by ``test_native_web_look``), so an export never waits
for it. Another configuration is measured once per view and look in a session
(about 10–40 ms, plus about 25 ms to load PyOpenColorIO the first time);
without PyOpenColorIO the export uses AgX and says the view couldn't be read.

**The World** (``web_world``, ``web_environment``). The World is read the way
the renderer reads it (``world_surface``) and travels in its cheapest faithful
form:

* none, or nothing reaches its Surface: no environment (Blender adds no light);
* a Background or Emission with a plain colour (or an RGB node) and a constant
  strength: the colour and the strength; nothing is made, so this everyday case
  costs nothing;
* that shader's colour straight from an equirectangular Environment Texture,
  along the view or through a Mapping node turning it about Z: the image
  itself, read with OpenImageIO on a worker thread, converted to scene-linear
  with Blender's OpenColorIO configuration, reduced with a box filter (a small
  sun keeps its energy) to at most 1024 × 512, and turned by the Mapping's
  angle; the strength stays a separate intensity;
* anything else (a Sky texture, gradients, mixes, linked strengths, groups):
  baked. The World is written to a private folder and a background Blender
  (``web_world_bake``, Cycles, 16 samples) renders it with a panoramic
  equirectangular camera at 1024 × 512 (about 1 s). A Light Path node, a World
  volume and a missing image are findings.

The picture is a Radiance ``.hdr`` (RGBE) in Blender's equirectangular
convention, which three.js's own convention matches once the glTF is Y up, so it
needs no rotation. It is stored in the .glb as a bufferView (about 2.1 MB at
1024 × 512) and counted in the budget (``environment_bytes``). A picture is made
once per World state and kept for the last three (an undo, a toggle back).

**The picture's key** (``web_world_key``) holds everything the picture depends
on, read through Blender's RNA rather than a list, so a new node type or setting
is covered without a change: every node's settings and the data it owns (a Color
Ramp's elements, a curve's points, an image user, a texture mapping), every
socket's value (outputs too, where RGB and Value nodes keep theirs), the links,
node groups (each read once), the World's own settings, the engine, each image's
files (time and size), a digest of its packed bytes and its colour space, and a
Script node's text. An image's files are the ones the picture's maker opens:
the stored file for a picture read from the image itself, and for a bake (a
background Blender rendering the World) each file Blender names for the node's
image user at the person's frame (``blend_file_paths.image_files``: a
sequence's frame, each tile), since a file saved over by another program
updates no depsgraph (1.0.10 audit: a sequence's frame saved over kept a bake's
key). Neither reads pixels this Blender holds: an image saved over and not yet
reloaded is made from its file, so reloading it keeps the key (measured). What only the node editor shows (location, size, colour,
selection, labels, collapsed panels, frame nodes) is left out, so moving a node
never bakes again; ``test_blender_web_world_key`` holds this to Blender's own
rule for every setting and value of every shader node type: a change that
updates the depsgraph moves the key, one that doesn't keeps it.

What it can't read reliably (a datablock other than an image, a group or a text,
such as a Texture Coordinate's object; a Script node's includes; a node type
from a Python add-on or a newer file; an image painted in memory, a sequence or
a movie; a driver or animation; more than 256 nodes, 4 levels of groups or
200,000 values) adds the frame and a count of the depsgraph updates of what that
World uses (the World, its trees, images, texts and objects, an object only when
it moves or changes, never on a selection), made outside exports. Such a picture
is reused while nothing it reads changes and never after an edit of it; the
handler that counts is registered only once such a World has been keyed. A bake
renders the World as it is at that moment: its copy drops its animation and
drivers, keeping the values they gave, at the person's frame.

**Waiting for it.** An explicit export (the File menu, ``angelo_web_export``)
carries the picture but never holds the main thread for it: while it is being
made the export writes nothing (``web_export_wait``). The File menu's export
runs as a modal operator, with the time in the status bar and Esc to cancel;
the agent's call is pending, polled by the chat, and a stopped turn discards its
staged file. When the picture lands the export runs, reading the scene again: an
edit meanwhile that changes the World waits for its own picture. A picture not
made in 130 s (a stalled worker), or a bake stopped at its 120 s limit, is left
out: the file's World is none (never "pending", which is the live preview's), the
report's environment status is ``failed``, a ``world_failed`` finding says why,
and the File menu's export ends with that finding as a warning in the status bar
(``web_export_wait.left_out``, also for ``world_missing``), so the World is never
dropped quietly. A bake that ran out of time is made again by the next explicit
export (never by the live preview, which would bake a slow World over and over).
Real bakes are far from the limit: on 2026-11-05 (Mac 2, M5 Max, CPU Cycles as
the bake always uses, Angelo niced while the machine ran a load average of 90 to
120, so contended), the whole background process took 2.0-4.6 s for each Sky
type, 4.2-12.1 s for an 8K HDRI turned by a tilt (so baked) and 5.9-29.3 s for a
detailed noise-and-Voronoi World, from all threads down to one; an 8K HDRI read
directly took 0.7 s. A cancelled wait leaves the picture to land for the next
export. The live preview never waits: it exports at once with the World marked
pending and exports again when the picture lands. Meanwhile the export carries
the World the page shows (the last picture or colour that preview published),
marked pending, so a page opened or reloaded during the bake lights the scene as
the open page does, for every form of World; with nothing shown yet (the preview
started on a World still being made, or after one with no light) the page
lights the scene with the neutral studio until the picture lands, never with
nothing. The report column says the picture is being made either way. The
World's picture is the only lighting the page waits for: lights and baked
materials are in every export.

**The extras** (``web_look.extras``). The glTF scene's ``extras`` hold
``angelo_look``, written in three.js's own names so a page can assign them::

    {"angelo_look": {
       "version": 1,
       "toneMapping": "AgXToneMapping",        // THREE[...]
       "toneMappingExposure": 1.0,
       "outputColorSpace": "srgb",            // or "srgb-linear" (Raw)
       "background": true,                    // false when the film is transparent
       "environment": {"type": "equirect", "mimeType": "image/vnd.radiance",
                       "bufferView": 7, "width": 1024, "height": 512,
                       "intensity": 1.0, "source": "hdri"},   // or "baked"
       "blender": {"viewTransform": "AgX", "look": "None", "exposure": 0.0,
                   "gamma": 1.0, "display": "sRGB"}}}

``environment`` is ``{"type": "color", "color": [r, g, b], "intensity": s}``
for a plain colour (linear Rec.709), ``null`` for none, and, in the live
preview only, while the World's picture is being made, the World the page
shows with ``"pending": true`` added, or ``{"type": "pending"}`` before it has
shown one. GLTFLoader puts the extras in
``gltf.scene.userData``; a developer's page applies them like this::

    const look = gltf.scene.userData.angelo_look;
    renderer.toneMapping = THREE[look.toneMapping];
    renderer.toneMappingExposure = look.toneMappingExposure;
    renderer.outputColorSpace = look.outputColorSpace;
    const env = look.environment;
    if (env && env.type === "equirect") {
      const data = await gltf.parser.getDependency("bufferView", env.bufferView);
      const texture = new HDRLoader().parse(data);   // RGBELoader before three.js r180
      const map = new THREE.DataTexture(texture.data, texture.width, texture.height,
                                        THREE.RGBAFormat, texture.type);
      map.mapping = THREE.EquirectangularReflectionMapping;
      map.flipY = true; map.needsUpdate = true;
      scene.environment = new THREE.PMREMGenerator(renderer).fromEquirectangular(map).texture;
      scene.environmentIntensity = env.intensity;
      if (look.background) { scene.background = map; scene.backgroundIntensity = env.intensity; }
    }

gltfpack (the KTX2 route) rewrites every buffer and keeps no extras, so after it
the export appends the picture and sets the extras again (``web_glb``).

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
* area lights (glTF has none) and soft shadow radius; the World where it loses
  something (above: baked, a Light Path node, a volume, a missing image, a
  picture that couldn't be made) and the colour management Three.js can't show
  (above: a nearest tone mapping, gamma, curves, white balance, the display).
* volumes, Grease Pencil, light probes, speakers and lattices; metaballs;
  particle systems; modifiers whose result moves over time (frozen at the
  current frame).
* UDIM tiles, movie and sequence textures, missing image files, textures
  over 4,096 pixels (many phones' limit).
* motion blur, depth of field and compositor effects; more than a million
  triangles.
* baked materials: an object exporting with its baked material (``baked_used``,
  info), one whose bake inputs changed since its bake and so exports with its
  own (``baked_stale``; ``baked_stale_nearby`` when only something within its
  baked ambient occlusion's reach changed, which that AO really saw;
  ``baked_stale_update``, one line for every object bound before the
  digest's current scheme, which reads as stale once after an update that
  changed what the digest checks), and one
  baked with a packing glTF can't carry
  (``baked_packing``: Unity's mask map, Unreal's DirectX normal). An object that
  exports with its baked material is checked as that material, not its own.
  Unsaved painting on a UDIM image's tiles other than the first isn't noticed
  until the image is saved.

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
the inline wordmark, light theme from the system). It shows the scene in its
look (``web_preview/look.js``: the extras' tone mapping, exposure and World),
the size budget, what doesn't carry over and the settings used (the tone
mapping and how the World travels among them), and keeps the person's orbit
view across reloads; it starts from the scene camera when the file has one.

**The look toolbar** (bottom left) lights the scene with the World or with a
neutral studio (the preview's light before 1.0.9, and the only light for a file
without extras), and shows or hides the World behind it. The choice stays in
the page's storage like the column's.

**Compare with Angelo** (``web_preview/compare.js``, ``web_compare``) lays
Angelo's viewport over the page: the page posts its camera (``POST compare``,
4 KB at most, from its own page only), Angelo's timer draws its 3D viewport from
exactly that camera with ``angelo_view``'s LIT look (EEVEE with the scene's
lights and World, in its view transform; the drawing viewport's display is set
for the one draw and put back, nothing in the scene is written), and the page
fetches ``viewport.png`` when the event stream says ``viewport``. **Wipe** shows
Angelo left of a draggable handle and Three.js right of it; **Difference**
shows where they differ, four times brighter. Both report the average
difference in percent of the display range. While comparing, the camera stays
put and the page shows the World as Angelo does; a new export or a resize draws
Angelo again; Done or Esc ends it. The two renderers light, shade and shadow
differently, so the difference never reaches zero. Only the person starts it,
so no agent approval applies.

**Every drag is the page's** (``web_preview/drag.js``): the Wipe divider and
the scene's orbit, pan and dolly follow pointer events (mouse, touch and pen
alike) captured on their surface, and the press's default is prevented, so the
browser never drags a picture out as a file, selects text, autoscrolls or
pans the page instead. Pictures and canvases are never draggable, the stage
selects no text and every drag surface has ``touch-action:none``. In 1.0.9 the
divider barely moved on Windows: Chrome dragged Angelo's picture out (a ghost
image) and cancelled the page's pointer. Page modules never listen to pointer,
mouse, touch or drag events themselves; a new drag uses ``followDrag``.

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
world or camera, which objects show (hiding, renaming), or a setting of the scene
itself that the export reads (``web_preview.scene_key``, from
``web_facts.scene_settings``): the look (``web_look.settings``: view transform,
look, exposure, gamma, curves, white balance, display), how the World travels
(``web_world.settings``: the World, the film's transparency, the engine, the
frame), the render effects (compositor, motion blur) and what Blender's glTF
exporter reads with the export's options (``gltf_scene_reads``: frame rate, the
cameras' aspect, the scene's name, the NLA tweak state, the physics format). ``web_look.facts`` and ``web_world.plan`` read the
scene only through their ``settings``, so a value they come to read is followed
too (``test_native_web_preview_key``; ``test_blender_web_preview_key`` changes
every writable scene, render and colour-management setting and holds that each
one that changes the export schedules one). A timer re-exports once the
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
publishes each export. It keeps listening for as long as the preview runs (an
export or a bake never restarts it), with a backlog of 128 connections: its
accept thread needs Python's lock, which the main thread keeps through an
export's long C calls while a bake loads every core, and a browser opens a
page's files over several connections at once; past socketserver's default
backlog of 5, Windows refused them (a blank page) and macOS and Linux dropped
them for a second or more. A browser that reloads or leaves mid-reply (WinError
10053, a broken pipe, a reset) ends that request quietly; any other error of a
request is still printed.

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
  picture, the look the page applied (``look``: tone mapping, exposure, World)
  and each web shader's status, so the agent checks a web-shaded object
  through Three.js, not the viewport. With no page open, or a hidden tab that
  draws nothing for 8 s, the result says so. The server accepts these posts
  only from its own page (Host and Origin), up to 8 MB for a PNG and 64 KB for
  a report.

Validation
==========

* Host: ``test_native_web_export`` (arguments, findings, budget parsing, the
  tools' families, the pinned Three.js build), ``test_native_web_preview``
  (server isolation, the event stream, a page load while the accept thread is
  held, a browser aborting a reply, merging), ``test_native_web_preview_page``
  (the report column: its hide control and the corner button back, a hidden
  column out of the way at every width, guarded storage, live updates keeping
  the choice, the key's guards, translated labels) and ``test_native_web_shader``
  (file uniforms, extras, the page's built-ins matching Blender's, the capture
  and shader report channel) and ``test_native_web_look`` (the tone mapping
  chosen by measurement, the extras, each World form, the Radiance picture as
  the page decodes it, the .glb patch after gltfpack, the Compare camera and
  route) and ``test_native_web_export_wait`` (an explicit export's wait for
  the World's picture, on a deliberately stalled worker thread: steps that
  return at once, nothing written until it lands, an edit meanwhile, the
  timeout's finding, the file and the call's result without the World, a bake
  that ran out made again for an explicit export only, cancelling, and the
  agent's pending call) and ``test_native_web_preview_bake`` (a page loaded over
  a fresh connection while the World's picture is being made gets the World the
  page shows, for a baked World, an HDRI and a colour, and the studio before
  any; the page's side).
* Background: ``test_blender_web_world_key`` (the picture's key against every
  shader node type, above) and ``test_blender_web_export_gltfpack`` (the KTX2
  route end to end through ``gltfpack_stand_in.py``, which keeps no extras and
  rewrites the buffer as gltfpack does: the flags, the look and the World's
  picture put back, the budget, the WebP fallback) and
  ``test_blender_web_preview_bake`` (the live preview's exports through a real
  Sky bake and HDRI read: pending before any picture, the picture shown carried
  while a new one is made, the new one once it lands). The KTX2 encoding itself is
  gltfpack's; no suite runs a real gltfpack, which Angelo doesn't bundle.
* Drags: ``test_native_web_preview_drag`` (host) pins the contract above in
  the page's source; ``tests/python/angelo_agent/browser_web_preview_drag.py``
  serves the page with ``PreviewServer`` and a stand-in for Angelo's Compare
  picture, then in Chromium, Firefox and WebKit (Playwright) drags the Wipe
  divider with the mouse (from the picture and from a label over it, ending
  over the report column), with touch and with a pen, and orbits the camera;
  each lands exactly where the pointer went (the divider within 2 px, the
  orbit within 0.03 rad of OrbitControls' own amount) with no native
  ``dragstart``, cancelled pointer or selection. Chromium gets real touch and
  pen input; Firefox and WebKit get touch and pen pointer events. On
  2026-11-05 (Ubuntu 24.04 VM, Playwright 1.64.1) it failed on the 1.0.9 page
  in all three engines and passed on the fixed one. Firefox needs a window
  (``ANGELO_QA_HEADED=1`` under ``xvfb-run``) for WebGL on a machine without a
  GPU.
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
* GUI, the look: ``tests/python/angelo_agent/gui_web_look.py`` shows five
  unrelated scenes in turn (an HDRI turned by a Mapping node under Standard, a
  Sky under Filmic, a plain colour under AgX, a gradient under Khronos PBR
  Neutral, no World under ACES 2.0). For each one it checks the extras and the
  World's form, that the page applied them, and that the Compare route returns
  Angelo's viewport from the scene camera; it keeps ``web.png`` and
  ``viewport.png`` per scene. ``web_look_compare.py`` lays them beside the
  pictures from an app without the look and prints each one's average
  difference from the viewport. On 2026-11-02 (macOS arm64, headless Chrome on
  SwiftShader), against 1.0.8: 25.2% to 2.7% (plain colour), 20.1% to 4.7%
  (HDRI), 11.3% to 2.5% (Sky), 11.3% to 0.5% (gradient), 22.6% to 8.7% (no
  World, ACES 2.0, the nearest mapping 7.3% away).
* GUI, the wait: ``tests/python/angelo_agent/gui_web_export_wait.py`` times a
  main-thread timer while File > Export > Web and ``angelo_web_export`` wait on
  a stalled bake (the longest gap between ticks: the time the main thread was
  held), cancels with Esc and by stopping the call, then lets a real bake land
  and checks both files carry the picture.
* Time: ``tests/python/angelo_agent/bench_web_look.py`` times the export of one
  scene with a plain colour World and with none, in an app with the look and one
  without (doc/angelo/performance.rst).
