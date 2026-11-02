.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Photo drop on the 3D view (1.0.8)
=================================

Dropping a photo from Finder or Explorer on the 3D view opens Angelo's drop card at the drop point. It offers
**Make a 3D model** (image to 3D through Media Studio's providers, placed where the photo landed when it is
ready), **Use as a reference** (the chat's next message), **Use as a material** (on the object it landed on) and
**Add as a picture** (a reference image standing at the drop point). No browser, no import dialog: two clicks from
a photo to a model in the scene. The chat bar's + menu offers the same card for a picked file (**Photo to 3D
model…**), standing at the 3D cursor. Director and Manual behave the same.

Module ownership
----------------

========================  =====================================================================================
Module                    Responsibility
========================  =====================================================================================
``photo_drop``            The file handlers, ``angelo.photo_drop`` (drop and + menu), the facts the card shows,
                          each choice (``Drop.run``), the thumbnail, registration and the + menu entry
``photo_drop_card``       The card (``card_host``): layout beside the drop point, the marker, the model picker,
                          errors that keep it open
``photo_drop_choices``    No Blender: the rows and why each is unavailable, which model bills, the price, and
                          when a made model is placed (``decide``)
``photo_drop_jobs``       Made models waiting for their record: the progress beside the drop point, placement,
                          notices
========================  =====================================================================================

Which drop takes a file
-----------------------

Blender's ``WM_OT_drop_import_file`` runs the one file handler whose ``poll_drop`` takes the area; with several it
shows a stock menu. ``ANGELO_FH_photo_drop`` takes every image Blender reads (``bpy.path.extensions_image``) in a
perspective or orthographic 3D view. A camera view keeps Blender's camera background drop.

Blender's own image drop on the 3D view (``VIEW3D_FH_empty_image``, an image empty) would make every photo drop that
stock menu, so ``photo_drop.register`` sets it aside while Angelo is enabled: its image job is the card's Add as a
picture, and movies keep it through ``ANGELO_FH_movie_empty`` (the same operator and poll, movie extensions only).
``unregister`` registers the stock handler again. On Reload Scripts the startup modules register it again before
the add-ons, so Angelo sets it aside again.

The rows
--------

``photo_drop_choices.rows`` decides each row from the facts read when the photo landed. Every disabled row says why.

======================  ===========================================  ================================================
Row                     Enabled when                                 Otherwise
======================  ===========================================  ================================================
Make a 3D model         PNG or JPEG by its bytes (what Tripo and     Not such a file: disabled, names the formats.
                        Meshy take, ``MODEL_REFERENCE_MIMES``) and   No ready model: "Connect Meshy or Tripo in AI
                        a 3D model ready to bill                     Providers first" (a click opens AI Providers)
Use as a reference      PNG, JPEG or WebP, and the chat is idle      Names the formats, or asks to wait for the turn
Use as a material       The photo landed on a local mesh object      "Drop it on an object…"
Add as a picture        Always (any image Blender reads)             —
======================  ===========================================  ================================================

The Make a 3D model row is the confirmation Media Studio's Generate dialog asks for: it names the model and the
account that pays ("your team's key" for a team route; "key not checked yet" for a key AI Providers hasn't verified,
as Generate warns) and the estimated price (``flow_prices``; an unknown price says so, never $0). Return never chooses a row by itself. With several 3D models ready, a picker above the rows
chooses which one bills. The first offered is Media Studio's current 3D choice, then the last photo drop's, then the
first ready model; a model that is not ready is never offered.

Several files dropped at once: the card uses the first and says so. Use as a reference follows the chat: disabled
while a turn runs, enabled again when it ends. A drop while another card that must be answered is on screen (a crash
report, What's New) is refused with a reason rather than queued, so a card never opens later, or in another file. A choice that fails (a file over Media Studio's
20 MiB reference limit, four generations already running, a chat that became busy) keeps the card open with the
reason; Cancel, Escape, a click outside and the close button change nothing.

What each choice does
---------------------

Each choice reuses the code the same choice already has elsewhere:

* **Make a 3D model**: the photo becomes a Media Studio reference (``ReferenceStore.import_images``), is checked by
  ``validate_generation`` and starts through ``studio_ops.start_job``, which Media Studio's Generate uses too, with
  Studio's 3D defaults (10,000 polygons, textured). The record lands in Media Studio like any generation.
  ``photo_drop_jobs`` then shows the drop point with the job's progress in every 3D view of that scene. When the
  record is ready, the model comes in as Media Studio's Add to Scene brings one in (``media_drop._prepared``, then
  ``media_place.add_models`` with the library on, or its glTF import with it off), standing at the drop point and
  selected, as one Undo step (``Place 3D model``).
* **Use as a reference**: attached to the chat's next message as a drop on the chat attaches it; a hidden chat is
  shown, once the card has closed (its modal operator would make the dock refuse), so the attachment is where the
  message is written.
* **Use as a material**: ``media_place.apply_picture_materials`` on the object under the pointer (Principled with
  the picture as Base Color and Alpha), one Undo step (``Photo as material``).
* **Add as a picture**: ``media_place.add_references`` at the drop point, facing the view, one Undo step
  (``Photo as picture``).

Material and picture leave Edit or Sculpt Mode first, as Blender's own Add does, so their Undo step holds them.

A photo lands in the file through ``studio_assets_landing.land``, keyed by its content (``photo-`` and its SHA-256),
so the same photo dropped twice is packed once.

Placement rules
---------------

``photo_drop_choices.decide``: a made model is placed only into the document and scene it was dropped in. After
another file was opened (``load_pre``), the scene was removed or no window shows it, a notice says the model is
ready in Media Studio instead. The scene is found by its pointer, so renaming it meanwhile changes nothing. It waits
while the scene's active object is in another mode than Object or a real tool is running
(``input_policy.tool_running``; a notice card or the welcome screen left open never holds it back), so nothing lands
under a drag or in Edit Mode; the drop point then says it will be placed in Object Mode. A failed or stopped
generation raises a notice with Media Studio's reason. One placement's failure never stops the others. Placements
live in this session only (a restart, or disabling the add-on, ends them without a notice): the model is still in
Media Studio.

The card and the marker
-----------------------

The card is a modal ``card_host`` card over the 3D view dropped on (``target_area``), beside the drop point and
never over it, inside the region; the point is marked with a teal ring. ``card_host`` draws and hit-tests a card in
the 3D view's first main region only, so in Quad View a drop on another quadrant opens the card centred there,
unmarked. In a right-to-left language the layout is
mirrored like every card, the point stays where the photo landed and the thumbnail stays upright. The thumbnail is
a private copy of the photo scaled to 256 pixels, freed when the card closes and before a choice runs, so it never
enters an Undo step.

Validation
----------

* ``tests/python/angelo_agent/test_native_photo_drop.py`` (host): the rows for every kind of drop, the model
  choice, prices, ``decide``, the card's layout and input, and the wiring (handlers, the stock drop set aside and
  given back, the + menu entry, Undo steps).
* ``tests/python/angelo_agent/test_blender_photo_drop_gui.py`` (GUI, Director): a real drop opens the card at the
  drop point; Make a 3D model with a stand-in provider places the model at that point; Cancel changes nothing; with
  no 3D account the row asks to connect; a format the providers can't take disables Make a 3D model and Use as a
  reference only.
