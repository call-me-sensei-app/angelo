.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Pictures Beyond the Viewport
============================

``angelo_view`` shows the 3D viewport, a rendered look and finished render
pictures (`agent_execution.rst <agent_execution.rst>`_, "Visual observation").
Those alone do not show a model working on textures, UVs, weights, render
passes or animation the result of its own work, nor what the person is
looking at, so it would have to guess or retry. This document is the contract
of the pictures that close those gaps. Every one of them is read-only, goes
through the existing ``angelo_view`` tool, its validation, its private
reference store and its image delivery, and adds no approval, tool or
authority.

=================  ======================================================================
Argument           What the model gets
=================  ======================================================================
``image``          ``IMAGE:<name>``: an Image data-block's pixels as Blender holds them.
                   ``UV`` or ``UV:<image>``: one mesh target's active UV map over a texture.
                   ``EDITOR`` or ``EDITOR:<type>``: the window, or one editor, as the person
                   sees it. ``RENDER_RESULT:<pass>``, ``j<n>:<pass>``, ``LAST_JOB:<pass>``:
                   one pass (``Depth``) or ``<layer>.<pass>`` of a multilayer render.
``shading``        ``ATTRIBUTE``: Workbench solid colours from each mesh's active colour
                   attribute. ``WEIGHT``: the targets' vertex weights of ``group``.
``group``          The vertex group ``WEIGHT`` draws (each target's active one otherwise).
``frames``         2 to 8 frame numbers: one strip image of a viewport look over them.
=================  ======================================================================

The ``image`` forms keep the existing rules of ``image``: ``shading`` is
``RENDERED`` and ``views`` is ``["SCENE_CAMERA"]``, ``region`` shows a part at
full detail, ``resolution`` bounds the longer side and nothing is enlarged.
The part after the colon is at most 200 printable characters
(``view_spec.MAX_FILE``), compared with names, never used as a path.
Validation is idempotent: ``validate_view`` returns ``image`` as given and
``view_finished.finished_image`` splits it (``view_spec.split_image``).

Module ownership
----------------

========================  ==================================================================
Module                    Responsibility
========================  ==================================================================
``view_spec.py``          The schema additions, their validation and ``split_image``.
``view_finished.py``      Dispatch: every ``image`` source ends in one ``Source`` file and the
                          shared ``shown`` path (region, scaling, transparency, reference).
``view_image_data.py``    ``IMAGE:``: cached pixels, display conversion, the picture file.
``view_uv.py``            ``UV``: the UV layout raster over its texture.
``view_editor.py``        ``EDITOR``: Blender's own screenshot, masking, cropping.
``view_exr.py``           A bounded OpenEXR reader (scanline, single- or multi-part; no
                          compression, RLE, ZIPS, ZIP) that also lists the parts of
                          every file from its headers, and a minimal single-part writer.
``view_exr_codecs.py``    The other codecs and tiled parts, read by OpenImageIO in a child
                          process (``view_exr_worker.py``); see "Production codecs".
``view_passes.py``        Pass choice and display: colour passes through Blender's own view
                          transform, data passes as stated values.
``view_weights.py``       ``WEIGHT``: the targets' weights drawn on the GPU.
``view_motion.py``        ``frames``: the strip, its held camera, the frame put back.
``view_policy.py``        The add-on Python a view's temporary writes run, by approval mode.
``view_save.py``          Pictures Blender saves for a view without writing the scene.
``view_pixels.py``        Shared pixel helpers: RGBA PNG, box downscale, sRGB, checkerboard.
========================  ==================================================================

``view_capture`` dispatches ``WEIGHT`` and ``frames`` and owns ``ATTRIBUTE``
in its shading table; ``view_finished`` and ``view_job_output`` hand a pass to
``view_passes``; ``job_host.guard`` reads the source part of ``image`` so a
pass of a running job is still waited for; ``request_path`` counts the new
framings.

**Add-on Python a view's own writes run.** A view that sets settings for a
moment and puts them back runs Python from add-ons: depsgraph-update handlers
when the writes make Blender update the depsgraph, and ``bpy.msgbus``
subscribers to the settings written.
Every such view follows one rule: in the Read-only and Ask approval modes it
is refused before it writes anything, naming that Python; the Auto modes,
whose edits run the same Python, make it, name it (``side_effects``:
``extension_handlers_ran``) and never say the scene is unchanged
(``scene_changed`` null, ``scene_change_unverified``). Auto is a new chat's
default, so a default chat makes such views; a view no chat asked for (a
Generation Flow's or a library save's capture) keeps the strict Ask rule
(``view_policy.mode_of``). Which views write:

* SOLID, WIREFRAME, ATTRIBUTE, MATERIAL and LIT captures set the 3D view's
  shading and overlays (``view_capture.display_keys``). Those belong to the
  screen, which Blender does not copy for evaluation, so they update the
  depsgraph only when the shading type changes to or from Material Preview or
  Rendered while an object is in Sculpt, Vertex Paint or Weight Paint mode, or
  overlays are switched under the Retopology overlay (``rna_space.cc``,
  ``view_policy.display_dirties``); a cached view writes nothing.
* RENDERED looks and strips apply the same rule with what a render and a
  frame change run besides (``render_extensions``, "Motion strips").
* The Render Result, its passes and every scene-linear file write nothing,
  so an add-on's depsgraph handler never leaves the agent blind to renders.
  ``view_save`` saves them with Blender's own save and an image format of the
  save's own (``_bpy._angelo_picture.save_copy``: the scene's output format,
  the view's settings set on that copy without an RNA update, the scene's own
  view and display for display copies), and reads a file into an image
  outside the document. No setting is written, no data-block added or
  removed, nothing published or tagged, so they are automatic in every mode;
  ``test_blender_view_sources`` proves 0 calls of an add-on handler and
  subscribers, and files equal, pixel for pixel, to those of the fallback
  route. A build without the helper takes that fallback route (the scene's
  format set for the save and put back), under this rule.
* UV layouts, image data-blocks, WEIGHT, editor captures and files shown as
  they are write nothing.

Captures and strips are made at once on the main thread, so that Python runs
after the view returns. Without it a view pays one pass over
the two depsgraph-update lists (only when its writes update the depsgraph)
and one over the message bus for the settings it writes.

Image data-blocks (``IMAGE:<name>``)
------------------------------------

**What is shown.** The pixels Blender holds in memory for the image's default
tile, frame and view, including unsaved paint and bakes: the same buffer the
``pixels`` property returns, read with one ``foreach_get``. A colour image is
shown as the Image Editor shows it by default: 8-bit pixels as stored, float
pixels (scene-linear, premultiplied) unpremultiplied and through the standard
sRGB transform. A non-colour image (``colorspace_settings.is_data``) is shown
as its stored values from 0 to 1, with the fraction outside that range
reported as clipped, like a baked data map (``view_job_output``). Alpha is
shown over the grey checkerboard; ``CHANNEL_PACKED`` and ``NONE`` alpha are
not used as alpha. The result names the image, its size, channels, float
storage, colour space, whether it has unsaved changes, its source kind and
tile count, never a path.

**Never loads a file.** Whether the default buffer is cached is asked of the
native read helper (``_bpy._angelo_read.image_metadata``, the same exact
cache-only check ``angelo_read_data`` uses for an image's size). An image
whose buffer is not in memory is refused with what works: the person opening
it in the Image Editor, or an approved ``angelo_execute_python`` script that
loads it (automatic reads never load image files). A build
without the helper refuses rather than guessing. The Render Result is shown
through ``RENDER_RESULT``; the compositor's Viewer image is refused (its
buffer may live on the GPU). While a render, a bake or a job runs, which may
be writing image buffers, the read is refused (and a UV layout is drawn without
its texture, ``uv_texture_busy``).

**Bounds.** At most 4096 x 4096 pixels (``MAX_PIXELS``): ``pixels`` copies as
four-byte floats, so a larger image would need gigabytes. Conversion runs in
row blocks so temporary arrays stay small; the full-size picture file is then
scaled by the shared path. What the Image Editor would show differently (a
display device other than sRGB, an 8-bit image in a space other than sRGB,
*View as Render*) is shown in Standard sRGB colours and marked
``image_color_approximate``; a UDIM image shows its first tile
(``image_first_tile_only``), a sequence or movie the frame Blender holds.

UV layouts (``UV``, ``UV:<image>``)
-----------------------------------

One mesh target (``targets``, exactly one) is required. Its active UV map's
polygon edges are drawn as one-pixel orange lines with a dark shadow over a
texture: the image named after the colon, otherwise the active material's
active Image Texture node, otherwise its first one, otherwise a neutral
checkerboard. A texture whose pixels are not in memory is not loaded either:
the layout is drawn over the checkerboard and the result says so
(``uv_texture_not_loaded``). UV space 0 to 1 fills the picture, which has the
texture's aspect; the fraction of face corners outside it (other UDIM tiles,
overflow) is reported. ``region`` draws a part of the 0 to 1 square at up to
4096 pixels across, so a dense layout can be read.

In Edit Mode the mesh's own arrays are stale until it leaves Edit Mode, so the
live edit mesh is read through ``bmesh.from_edit_mesh`` (read only, at most
200,000 faces); in Object Mode the arrays are read with ``foreach_get`` (at
most 4,000,000 face corners). Lines are drawn in bounded chunks; a layout over
16 million line pixels is drawn with a uniform subset of its edges and marked
``uv_edges_subsampled``.

What the person sees (``EDITOR``, ``EDITOR:<type>``)
----------------------------------------------------

``EDITOR`` is the bound window; ``EDITOR:<type>`` is the largest open editor
whose ``ui_type`` or ``type`` is ``<type>`` (``UV``, ``IMAGE_EDITOR``,
``ShaderNodeTree``, ``GeometryNodeTree``, ``CompositorNodeTree``,
``PROPERTIES``, ``OUTLINER``, ``DOPESHEET``, ``GRAPH_EDITOR`` ...), in the bound
window first, then in Blender's other windows. A missing type is refused with
the editors that are open. So "look at what I'm seeing" works for the Image and
UV Editors with the person's zoom and overlays, node trees, the Properties of
what they selected, and the Compositor's Viewer image in an Image Editor.

Pixels come from Blender's own screenshot operator (``screen.screenshot``,
which redraws the window into an off-screen buffer on Metal and Vulkan and
reads it): no Undo step, no data-block, no lasting UI change, and nothing is
opened, scrolled or switched. Editors that can show code, credentials or file
paths are filled with a flat grey before the pixels leave ``view_editor`` and
listed in ``masked``: text editors, Python and chat consoles, the Info log,
Preferences and the file browser (the asset browser is shown), and the status
bar below every editor, whose reports can carry paths. ``EDITOR:<type>`` of
such an editor is refused. The result lists the editors the window shows and
the window's pixel size. A screen wider than 4096 pixels is reduced in whole
steps first (the PNG encoder's bound); ``region`` then gives a part of the
window or editor at full detail, so small interface text can be read.

The capture needs a window (refused in background mode) and is refused during a
modal interaction. It shows what is drawn at that moment: a menu or popup the
person has open is part of the picture (the same holds for any screenshot).
**Parity, disclosed.** Masking is by editor, not by
text: an editor capture can include file paths drawn in the editors it shows
(the output path in Properties, an image's path in the Image Editor's sidebar,
a node's file field), and they reach the model provider with the picture, as
they do with other tools that screenshot Blender. ``angelo_read_data`` still
withholds path values; a capture is the person's screen as they see it.
Every capture is disclosed: once its picture is ready for the model, the chat
shows a host-only notice row (a ``system`` row with ``notice``, which no model
is shown) naming what was sent, for example "The agent was sent a picture of
your Image Editor as you see it, with any file paths shown in it.", or for the
window the editors it shows. There is no approval click. The notice is
translatable (``ui_text.notice`` templates marked with ``n_`` in
``view_editor``); a conversation that cannot show it (no chat to emit to) gets
no capture, and a capture that fails is not announced. The model's result says
that the person was told, and the request's receipt counts editor captures
sent (``path.editor_captures``, ``request_path``).

Render passes and view layers (``<source>:<pass>``)
---------------------------------------------------

Blender reads only a multilayer file's first layer and pass into an Image, and
Python cannot ask ``Image.pixels`` or ``save_render`` for another (no
ImageUser reaches them), so passes are read from a file:

* ``RENDER_RESULT:<pass>``: Blender saves its current Render Result (the
  person's slot) once as a multilayer OpenEXR, 32-bit, ZIP, into the
  conversation's private folder (``Image.save_render``). The scene's image
  format is set for that one save and put back exactly
  (``view_render_restore.LookSettings``); refused while a job or render runs,
  as for the existing display copy. The file is discarded before the call
  returns. A stereo Render Result is refused. One value cannot be put back:
  a scene whose output colour space was never set (a factory scene) has it
  named by Blender on any format change, and Python cannot unset it; the
  default it names writes the same files, and the result says so
  (``side_effects``: ``output_color_space_named``) instead of claiming an
  exact restore.
* ``j<n>:<pass>`` and ``LAST_JOB:<pass>``: a job's multilayer output (``file``
  names it when the job published several; ``frame`` one frame of an
  animation), read through the job output's bounded private copy; a running
  job is waited for. A one-picture output says it has no passes.

``view_exr`` reads only the requested pass's channels (in a multi-part file,
only that part's chunks) and refuses what it cannot decode with the reason:
deep files, sub-sampled channels and pictures over 36 million pixels. Every
size and offset is checked against the file before use.

Production codecs
~~~~~~~~~~~~~~~~~

PIZ, PXR24, B44, B44A, DWAA, DWAB and HTJ2K compression and tiled parts are read
by OpenImageIO, the library Blender already ships (with OpenEXR and OpenJPH), so
nothing is added to the installer. ``view_exr`` still parses every header itself,
so the parts, passes and channels a model is told about never depend on the
library, and keeps its own decoder as the fast path for no compression, RLE,
ZIPS and ZIP (what Blender writes by default). Only a part that needs the
library goes to it, and only after the size limit above has been applied.

The library never runs inside Angelo. ``view_exr_codecs`` starts the bundled
interpreter (``sys.prefix``/``bin``) as a child running ``view_exr_worker.py``
with the path, the part, the channel names and the pixel limit as plain
arguments and a short allow-list of environment variables (never the application's, which can
hold provider keys); the child answers with one JSON line and the requested channels as
little-endian float32 planes, which are read straight into one buffer (the application holds
the answer once, and the planes are views of it). Neighbouring channels are one library call,
and an integer channel (object and material IDs) is read as integers, so both decoders return
the same numbers. A part whose scanline blocks would need more than 256 MB to decode (width
times lines times every channel) is refused before the child starts. A crash, a hang past 40 s (the child is killed), a
damaged answer, a wrong size or a library refusal is an ``ExrError`` with a
fixed message (the library's own text, which can hold paths, is never passed
on), and the application goes on. A build with no bundled interpreter or
library refuses these parts with the same reason as before. Cost: about 0.06 s
to start the child and import the library for a small file (measured on the
development Mac with the bundled interpreter; ``test_blender_exr_codecs`` records
the 1080p figures per build), paid only by files the own decoder does not read;
the call still runs on the main thread, like the own decoder.

``test_blender_exr_codecs`` writes a known picture with every codec and bit depth
the build offers, plus a small multilayer render with Depth, Normal and
Cryptomatte passes, with Blender itself, and checks each read three ways (the
known picture for lossless codecs, Blender's own loader for every codec, the
same render saved with ZIP for the passes). Integer channels, a mixed-codec multi-part file (the offset tables of parts the library
reads are passed, not skipped) and the application's memory peak against its output are
checked there too. The files it can rewrite are the
committed fixtures in ``tests/python/angelo_agent/exr_fixtures`` (stored as
``.exrbin``: ``*.exr`` is a Git LFS pattern). It also feeds damaged copies of
them to the reader and checks a path with spaces and non-ASCII letters.

``<pass>`` is a pass name (``Depth``, ``Normal``, ``Combined``,
``CryptoObject00`` ...) on the scene's active view layer, else on the only
layer that has it; ``<layer>.<pass>`` chooses the layer (layer names may hold
dots; Blender's pass names do not). A name that matches nothing is refused
with the layers and passes the file holds (bounded list), an ambiguous one
with the layers to choose from. ``Composite.Combined`` is the composited
picture, ``Combined`` the layer's own.

**What each pass looks like.** Colour passes (R, G and B channels: Combined,
Diffuse and Glossy colours, Emission, AO, Environment, Shadow, colour AOVs) are
written as one RGBA OpenEXR and go through Blender's own display copy
(``view_finished.linear_file``), so they look as the Image Editor shows that
pass in the scene's view transform. Data passes are shown as stated values and
the result names the mapping (``mapping``) and its numbers: Depth near-white
to far-black between the nearest and farthest finite hit (``range`` in scene
units; no hit, beyond 1e9, black); Mist as ``1 - mist``; any other single
value lowest black to highest white over its range; Normal as
``(n + 1) / 2``; Position per axis over its range; IndexOB, IndexMA and
Cryptomatte one colour per ID (weighted by coverage), with the number of IDs;
Vector, UV and other channels as stored values from 0 to 1, clipped values
counted.

Vertex weights and colour attributes
------------------------------------

``ATTRIBUTE`` is the Workbench solid draw with *Attribute* colours, set on the
drawing viewport for the call and restored like every other shading
(``view_capture.SHADING``): it shows each mesh's active colour attribute, as
the viewport does, at any view.

``WEIGHT`` needs mesh targets. Blender shows weights only in Weight Paint
mode (or as an Edit Mode overlay), and a view never changes the mode, so the
host draws only the targets, on the GPU into an off-screen buffer with depth
test, at the views' exact cameras (axis, FIT, SCENE_CAMERA or a REUSE
view_id): Blender's weight ramp (blue 0, cyan, green 0.5, yellow, red 1),
unassigned vertices black, shaded by the angle to the camera. Weights come from
the original mesh's vertex groups (``group``, else each target's active
group; a target without it is refused, naming its groups); positions come from
the evaluated mesh when its vertex count matches (deformed by armatures and
shape keys as the viewport shows), the original mesh otherwise
(``weights_undeformed``). At most 500,000 vertices in all. In Edit Mode the
weights are those of the last mode change (``weights_edit_mode``). The
evaluated copy is the object's temporary ``to_mesh``, cleared at once;
nothing enters the file. WEIGHT views are never cached.

Motion strips (``frames``)
--------------------------

``frames`` (2 to 8 distinct frame numbers, with one ``views`` item and a
viewport shading: SOLID, WIREFRAME, MATERIAL, LIT or ATTRIBUTE) draws the look
at each frame and returns one picture: the frames left to right, top to
bottom (2 or 3 in a row, 4 in a 2 x 2 grid, up to 4 x 2), each tile labelled
with its frame number in its top-left corner, the strip's longer side at
``resolution``. An axis or FIT camera is fitted to the targets over all those
frames and then held, so movement shows (a REUSE ``view_id`` of the strip
reuses that camera); ``SCENE_CAMERA`` follows the scene's camera at each
frame; a REUSE view_id keeps its camera. A RENDER_ANIMATION job's frames are
shown with ``image`` and ``frame``; a strip never renders.

The frame is changed with ``scene.frame_set`` and put back exactly (frame and
subframe) in ``finally``, also after a failed draw; the viewport display is
normalized and restored as for any view. Changing the frame runs what
scrubbing the timeline runs: frame-change handlers and drivers, and unbaked
physics, particles and simulations compute the frames shown. A MATERIAL or LIT
strip of a scene that renders with motion blur draws each tile on a new
offscreen, so EEVEE does not blur it toward the tile before; the view says the
blur is left out (``motion_blur``: ``NOT_DRAWN``; ``agent_execution.rst``,
"Visual observation").

**Extension code needs approval, as for a look.** A strip runs without the
person's approval, so the rule of RENDERED looks applies
(``render_extensions``): when changing the frame would
run Python from add-ons or scripts, meaning a frame-change handler that does
not ship with the application or a scripted driver the scene reaches whose own
Python would run (``render_extensions.inspect`` with ``FRAME_BAKE``), the strip
is refused, naming that code and the approved route (an
``angelo_job_start`` RENDER_ANIMATION, whose approval names the Python, then
``angelo_view`` with the job's ID and ``frame``). The check runs at admission
and again just before the first frame change, and a refused strip changes no
frame. Depsgraph-update handlers are mode-aware, because a look's own setting
writes run them: a ``depsgraph_update_pre``/``post`` handler that does not
ship refuses the view in the Read-only and Ask approval modes (a Read-only
refusal adds that the job route is refused too), while the Auto modes, whose
typed edits already run those handlers, let the view run and name them
(``side_effects``: ``extension_handlers_ran``). ``bpy.msgbus`` subscribers
count the same way, because a look's writes notify them: a subscriber that
does not ship, to a setting the view sets and puts back or to that setting's
struct (for a strip, the viewport display settings ``view_capture.display_keys``
lists; for a RENDERED look, its render, format and colour settings, its Render
Result slot and the render display preference), refuses the view in Read-only
and Ask and is named in Auto. Blender calls subscribers on its next event, so
those of a strip, and those of a look's restore, run after the view: its
result then never says the scene is unchanged (``scene_changed`` null, or true
when the check found a change). Python lists no subscribers; the application
does (``_bpy._angelo_jobs.msgbus_subscribers``), and on a build without that
list the view runs and says they are unknown (``message_bus_unknown``,
``scene_changed`` null). Python an add-on runs beside a view rather than in
answer to it, from a timer, a modal operator or a draw callback, cannot be
listed by Python or the application: the check after the view still compares
what it reads, but cannot name it. Otherwise it stays automatic, and the
document is checked afterwards as after a look (``agent_execution.rst``,
"Checked after every view"). The result reports shipped frame-change
handlers that ran (``side_effects``: ``frame_change_handlers_ran``) and physics
in the scene (``uncertainty``: ``simulation_frames``). The strip is refused during playback, a modal
interaction or a running render or job. One that takes longer than
``STRIP_SECONDS`` (20) stops drawing, leaves the remaining tiles dark and
lists them (``missing_frames``, ``strip_truncated``). Strips are never cached.

Budgets and receipts
--------------------

``IMAGE``, ``UV``, ``EDITOR`` and pass pictures are finished pictures:
delivered once, not counted in the twelve kept views of a Claude request, not
captured again after an edit. A pass of a job counts as ``job:<id>``, a pass
of the Render Result as ``view:RENDERED:FINISHED_IMAGE``. Editor
captures sent are also counted on their own (``editor_captures``).
``WEIGHT``, ``ATTRIBUTE`` and strip views are kept views: a strip is one image
of the request's twelve and is captured again after an edit within the
refresh's time budget. Receipts count them as ``view:<SHADING>:<FRAMING>``
with the framings ``IMAGE_DATA``, ``UV_LAYOUT``, ``EDITOR`` and ``STRIP``
(``view_spec.CURRENT_FRAMINGS``),
which carry the request's edit count because they show current state, not an
earlier render.

Model-visible bytes
-------------------

The schema adds three ``image`` alternatives and a ``:<name>`` suffix, two
shadings, ``group`` and ``frames`` (whose frame bounds and least count the host
checks, so the schema stays under ``test_native_view_observation``'s
1,200-character bound); the tool description adds five sentences. Measured
with ``plan_budgets.py`` against the same sources without these forms: the
Claude plan schema 50,575 -> 50,710 bytes (+135, within its 50,750 budget),
every tool declared in full 98,675 -> 100,115 bytes (+1,440, within 102,000;
``angelo_view`` alone 4,057 -> 5,497), Claude's instructions unchanged
(11,811). The Claude plan carries the schema but not the description, and its
instruction budget had 19 bytes left in that measurement, so the plan
instructions do not explain the new forms; the validation errors and result
notes do.

Validation
----------

Host: ``test_native_view_sources`` (IMAGE, UV and EDITOR through the real
``view_finished`` and ``view_picture``, masking, the capture notice,
validation), ``test_native_request_path`` (``editor_captures``) and
``test_native_view_weights`` (the ramp, weights, geometry, and
``view_capture``'s WEIGHT and ATTRIBUTE dispatch), ``test_native_view_exr``
(the reader against files written to the OpenEXR layout in every supported
compression and layout, refusals, damaged files, and the hand-off to the child
decoder against stand-in children: a good answer, a crash, a hang, garbage, a
wrong size; the worker against a stand-in library) and
``test_native_view_passes`` (layer and pass choice, each mapping, the Render
Result flow with the format put back, job and File Output passes) and
``test_native_view_motion`` (the held camera over every frame, the frame and
display put back after success, failure and a time-out, refusals, layout) on
the existing stand-ins.
Native, in a window: ``test_blender_view_sources`` (GUI) paints and bakes into
images in memory, textures and unwraps a cube, weights it and colours a
sphere, shows a planted secret in a text editor and a red image in an Image
Editor (with the real controller: the capture's notice row appears in the
chat), renders Depth, Normal and Cryptomatte passes with EEVEE, animates a
cube across three frames of a strip (refused while a planted add-on
frame-change handler is registered), then checks each picture's pixels in both runtimes' payloads against
Blender's own data, that an image file loaded but never displayed is still not
loaded, that the secret's editor arrives grey, and that no data-block, editor,
setting, mode, selection or frame changed::

  python3 tests/python/angelo_agent/run_native_suites.py --gui \
    --binary "$PWD/../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo" \
    --include test_blender_view_sources
