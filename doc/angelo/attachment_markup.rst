.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Marking up a picture for the agent (2.1.0)
******************************************

A picture the person attaches can be opened full size, marked up and commented on, and sent to Angelo's agent as
one message, so they can show what they mean instead of describing it. The maintainer's request (2026-11-19): click
an attached picture, it opens full screen, and the person marks it and comments.

Where it opens
==============

* **A picture chip in the composer**: a click on the chip opens it; its × still removes it
  (``chat_composer``'s ``open_reference`` hit, ``angelo.markup_picture`` with the chip's index).
* **The + menu's Mark up a picture…**, beside Attach (``markup_ui``, ``TOP_LEVEL`` id ``markup``): the composer's
  newest picture, or, with none, the 3D view as it is now, captured into the composer as a picture of its own and
  opened. The capture is the largest 3D view drawn offscreen from its own view, shading and overlays
  (``markup_ui.view_rows``): the scene, never the interface drawn over it.
* **The agent** (ruling 2026-11-20, agent first): ``angelo_app_feature`` ``{feature: MARKUP, action: OPEN,
  reference_id?}``, after its card ("Mark up a picture…"; "Nothing is sent until you send it."), opens the viewer for
  the person on a picture of this chat by its id (``markup_ui.reference_record``: ``attached_image``, never a path)
  or, without one, on the 3D view as it is now. The person draws and sends; the agent never draws. The feature guide
  teaches the call and hands over the tool's schema.

Every way in passes a picture record of the chat (``ReferenceStore``), never a path, so screenshots, 3D view
captures, Look images and attached files behave the same.

The viewer
==========

Drawn by Angelo over the main window's largest 3D view (``markup_overlay``: a POST_PIXEL handler and the passive
overlay ``angelo.markup_viewer``, ``input_policy.PASSIVE_OVERLAYS``); the chat and the rest of the window stay usable.
Its modes:

* **View**: the picture fitted, wheel or trackpad zoom about the pointer, middle drag or left drag to pan, F to fit;
  a bar with **Markup**, **Comment** and **Resize**; **Remove BG** and **Erase** join it only while
  ``markup_tools.AI_EDITS`` is on, which it is not in 2.1.0 (below).
* **Markup**: select and move, pen, text, arrow, ellipse, rectangle, eraser (removes every mark its path crosses), a
  palette of six named colours and three widths, undo, redo and Done. Keys: V, P, T, A, O, R, E, C (``TOOL_KEYS``,
  one table, shown in each tool's tooltip); they are letters while a field takes typing.
* **Comment**: a click pins a numbered comment, a drag comments on a region; each opens its note field. A new pin
  left without a note is taken back (``MarkupDoc.retract``).

* **Erase** (only while ``AI_EDITS`` is on): brush over what to remove (three brush sizes, undo, clear), then
  **Erase the brushed areas…**.
* **Resize**: drag on the picture to crop, choose 100, 75, 50 or 25%, then make the resized picture; the bar's hint
  says the size it will make.

Markup and Comment show an **Add instructions…** field with a send button, enabled when there is something to send.

**Keyboard** (``markup_input``, ``keyboard_owner``; break-it of 2298d608dd1, M3 and S6; re-reviews of 4e712847d3b and
b5808fd5a2e). One owner per window. A press on the viewer takes the keyboard, from the chat too (the chat's
``blur_request``); opening with it takes it too, and the chat doesn't take it back as its + menu closes. A press
anywhere else (the chat's field, another editor) gives it back. It opens with the keyboard when the person opened it
and without it when the agent did. Three states, and none hands a key to the 3D view the viewer covers:

* **The viewer holds it**: a field being typed in takes its keys; otherwise ``canvas_focus`` gives every control a
  Tab stop (arrows move between them, the ring is the console's) and Return or Space runs the focused control on the
  key's release, as its click does; then the tool letters, Cmd/Ctrl+Z, Delete, F and Escape; any other key goes
  nowhere.
* **A chat holds it** (its field, or its open menu): over the viewer a key goes to the chat only when the chat acts on
  it (``keyboard_owner.chat_takes``, the live view's rule: the field's keys, the input method's events and every key
  while the window composes, so Chinese, Japanese and Korean typing reaches the chat), else nowhere; away from the
  viewer, to the chat. Whatever
  the chat still hands on (a key it doesn't use, every key while its composer is hidden, a navigation key, a dropped
  file) is held when an Angelo overlay covers the editor under the pointer (``overlay_holds.holds_covered``, the
  chat's one modal, and ``overlay_holds.COVERS``, where the viewer registers what it covers), so Ctrl+J, Ctrl+Numpad 0
  or Delete never reach the hidden scene.
* **Nobody holds it**: a key pressed over the viewer makes it the owner and is its own (Escape closes, F fits); a
  release or an input method's event goes nowhere; away from it, to the editor under the pointer.

Save (Cmd/Ctrl+S) and the quit key are handed on in every state (``overlay_holds.app_shortcut``, ``overlay_passes``).
The viewer's handler sits ahead of the chat's: when the chat's is made again (``ensure_input``), the viewer adds its
own again ahead of it (``overlay_holds.AHEAD``, ``markup_overlay.come_forward``). While the viewer hides part of the
scene and a chat holds the keyboard, the person's Undo and Redo keys (their Screen key map) that the chat hands on,
having no draft history of their own, are held (``overlay_holds.COVERS_WINDOW``), so Cmd/Ctrl+Z typed at the chat
never undoes a scene the person can't see. With no chat holding the keyboard, Undo over a visible editor is the
person's and goes on, viewer open or not; Edit > Undo and Undo History, chosen from a menu the person sees, undo the
scene too. With no overlay open the chat's Undo falls back to the scene's as before.
An open card (Remove BG, Erase) answers only a click or the armed key on one of its two buttons, never
a bare Return; Escape cancels it.

**Bounds and failures** (M2). The picture takes at most 200 marks and 20 comments: past either, the viewer says so
and the session goes on; a comment drag with no area pins a point. Any other exception in the viewer's modal closes it
cleanly (its draft kept) and says so in the chat: never a scrim left over the 3D view.

**Size** (M1, ``picture_budget``). Every picture the viewer writes (the marked picture, Resize's result, Erase's
source, the 3D view's capture) fits the chat's picture limit: encoded as it is when it fits, else stepped down by one
whole factor on both sides until it does; Resize says the size it made when it had to step down. A picture's size is
read past any metadata a camera writes first (``picture_size``, S9).

Nothing in a draw reads a file: the chip's small picture shows at once (``studio_tile_previews``) and a timer reads
the picture at 2048 px a side and swaps it in. Every colour, size and radius is a ``ui_tokens`` value; glyphs are
shapes and strokes; the chrome mirrors for a right-to-left language and the picture never does.

The document
============

``markup_doc.MarkupDoc`` holds marks (``pen``, ``arrow``, ``ellipse``, ``rect``, ``text``) and comments (a point or
a region with a note) in the original picture's pixels, origin top-left, so the viewer, a reopened draft and what the
model reads agree at any zoom. Its undo stack is its own (200 steps), never Blender's: marking up never touches the
scene's history. A drag is one step; a command that changes nothing is not one. Bounds: 200 marks, 20 comments,
2,000 points a stroke after simplification (bounded time for any stroke), 500 characters a text mark, 1,000 a note,
4,000 the instructions.

The unsent markup is a draft, ``<picture id>.markup-draft.json`` in the chat's private reference folder, saved at
most once a second while it changes and on close: closing never loses marks, and reopening the picture brings them
back. A damaged draft is dropped, never half-loaded.

What the agent gets
===================

Send flattens the marks onto the picture on the GPU with the viewer's own drawing (``markup_flatten``,
``markup_draw.marks``), at the original's resolution brought under 4,096 px a side by a whole step, encodes it on a
worker within the chat's picture limit (``picture_budget``), writes it as a new private picture and records the
markup with it once
(``<marked id>.markup.json``, ``markup_store.freeze``). One message goes through the person's own send
(``console.send_message``: held in the tray while a turn runs, like any message), with:

1. the **marked picture** (its record names its original in ``markup_of``);
2. the **original picture**, so the model sees what a mark covers. A JPEG whose file is stored turned (an EXIF
   orientation other than 1, as phones write portraits) goes as an upright copy made from the flatten's own upright
   pixels (at the marked picture's size), and the recorded markup names that copy, so the block's
   ``original_picture`` is a picture the message carries (re-reviews of 4e712847d3b and b5808fd5a2e, S3). Every
   picture is written before the markup is recorded as sent, so a failed write keeps the draft;
3. a **structured block** in the turn's attachment text, under its own heading after the attached files' untrusted
   section (``markup_store.turn_context``): the picture's size, each comment's number, place and note, each mark's
   kind, colour and place, and the instructions, all in the original's pixels. Its comment numbers are the numbers
   drawn on the pins.

The block is built from the frozen markup file alone, never from a record's fields: a record that only claims to be
a marked picture (an app's, a damaged state file's) brings nothing. A read-only collaborator (``delegation``) gets the
same section. The block's explanation is fixed English for the model; the person's words go as they typed them. The
sent message carries both pictures, the marked one first. It is a message of its own, so the message box's limit of
four pictures never counts them.

Remove BG, Erase and Resize
===========================

Each makes a **new picture** in the composer and keeps the original; the viewer switches to the new one.

* **The switch** ``markup_tools.AI_EDITS``, **off in 2.1.0**: the viewer offers Markup, Comment and Resize only,
  and nothing reaches Remove BG or Erase (no bar item, no mode, no card, and ``markup_edit_host.run`` returns before
  a job). They turn on only with a live end-to-end proof on a named image connection in the branch (coordinator,
  2026-11-20; break-it of 2298d608dd1, M4): no image connection usable without the person was available for 2.1.0.
  The feature guide teaches them exactly when the switch is on (``test_native_markup_entry`` holds the two together).
* **Remove BG and Erase** are one Media Studio image generation each (``markup_edit``, ``markup_edit_host``), with
  the picture as its reference, run only after the person answers a card that names the model and account, the
  estimated cost (``flow_prices``; "isn't known" for an unpriced model, never zero), whose key pays, that it takes a
  few seconds and that the original is kept. The model is the image model the person last chose in Media Studio
  (``studio_views.chosen``, forgotten at a file load as Studio's own states are) when it is ready, for both edits;
  else the first ready one, Remove BG preferring one that returns a transparent background (OpenAI's). On a model
  without one Remove BG still runs and the card says the background comes back plain. Erase paints the brushed areas solid magenta (``ERASE_HEX``) on a copy (``markup_flatten.erase_source``) and
  asks for them to be filled from their surroundings. Prompts are fixed English for the image model. The result also
  stays in Media Studio's library, as every generation does; a result over the attachment limit stays there and the
  viewer says so.
* **Resize** is local and free (``markup_resize``): crop, then an area average and bilinear resample, written as a
  PNG with its alpha (``rgba_png``), so a removed background stays removed. The longest side is at most 4,096 px.

Not in 2.1.0 (break-it of 2298d608dd1 names each):

* A sent picture in the transcript opening read-only with "Mark up a copy" (sent pictures are not drawn in the
  transcript today).
* IME composition in the viewer's text fields: Chinese, Japanese and Korean input methods reach a comment, a text
  mark or the instructions by paste only (S10); in Chinese, Japanese and Korean the instructions field's tooltip
  says so (N4, N5).
* EXIF orientation is read from JPEG files only; a WebP's EXIF chunk or a PNG's ``eXIf`` orientation opens as stored
  (re-review of b5808fd5a2e, N7).
* Decoding off the main thread: a picture is read with ``bpy.data.images.load`` on the main thread. A PNG, JPEG or
  WebP over 8,192 x 8,192 pixels (``PIXEL_LIMIT``), or one whose header can't be read, is refused by its header
  before Blender decodes a pixel, wherever
  a picture is read (``studio_tile_previews.decode``: the viewer, the chip, Send, Resize; re-review of
  4e712847d3b, S1), and the viewer opens nothing over it (``picture_size``). Within the bound Blender's own buffer
  is at most 0.25 GiB (1 GiB for a 16-bit PNG, which it reads as floats), scaled before it is expanded to floats;
  loading a picture near the bound pauses the window for a moment (S4).
* Remove BG's and Erase's card under-states an OpenAI edit's price: ``flow_prices`` leaves out the input picture's
  tokens (break-it of 2298d608dd1, N6). Moot while ``AI_EDITS`` is off; fixed before it is on.
* A turned photo's upright copy is at the marked picture's size (``MAX_FLAT``) while the block's coordinates and size
  are the original's full pixels; the two differ for a picture over 4,096 px a side, and the block says the size
  (re-read of 0690d5c, N1).
* Names in records the agent reads: a marked picture's, a capture's and a resized picture's names are in the
  interface language (``MARKED_NAME``, ``VIEW_NAME``, ``RESIZED_NAME``), as the chip shows them; the agent reads them
  as file names only (re-review of 4e712847d3b, N6).
* The 3D view's capture is encoded on the main thread (``markup_ui.capture_view``), after it is brought under
  ``view_png.MAX_DIMENSION`` (4,096) a side; a picture stepped down to fit the chat's limit is encoded again (N5).
* Limits inherited from the agent's other tools (N9): the agent's capture draws the 3D view from the tool executor,
  as ``angelo_view`` does, with no window-resize deferral; and an agent script can reach the viewer's module in the
  same process and stage a markup with the person's heading, which is the confinement limit named in
  ``script_confinement.rst`` ("Objects shared in place") until agent scripts run in a helper process.

Testing
=======

Host: ``test_native_markup_doc``, ``_view``, ``_tools``, ``_layout`` (the tooltip rows), ``_geometry``, ``_message``,
``_store`` (the sent original's id), ``_edit``, ``_resize``, ``_entry``, ``_input`` (the keyboard's three states),
``covered_editors`` (the chat's hold, one owner), ``picture_budget``, ``picture_size`` (the pixel bound, EXIF
orientation, each refusal's sentence) and ``tile_previews`` (the header refusal, each orientation's turn). In the app
(``test_blender_markup_director_gui``): open from the + menu with one keyboard owner, every tool, comment, undo and
redo, the chat keeping its letters, the viewer taking the keyboard from the chat (a letter and Return reach neither
the draft nor a send), Delete, Space, an arrow and G reaching nothing in the scene, the chat holding the keyboard over
the picture (Ctrl+Numpad 0, Ctrl+J and the numpad reach neither the scene nor its view; Cmd/Ctrl+Z with no draft
history, over the picture and over the chat, leaves an undoable move in place, and over the Outliner with no chat
holding the keyboard undoes it), and send (by the button, and
in Arabic by Return in the instructions), in English, Arabic and a narrow window.
