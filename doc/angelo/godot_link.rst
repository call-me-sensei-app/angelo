.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

################
Godot live link
################

A person picks an asset in the Godot editor, edits it in Angelo, and sees every
change on that asset's instances in Godot as they work. Saving in Angelo updates
the asset file, and Godot re-imports it. This document is the contract for Godot's
side: Angelo's Godot adapter (``godot_link_assets``) and the Godot editor plugin
Angelo ships (``angelo_agent/godot_addon/angelo_link``, "Angelo Link"). What every
engine shares (the session, the transport, saving and working files, the request
card) is :doc:`engine_link`.

.. contents::
   :local:

Design
======

Two layers, so nothing a person sees in Godot is ever lost or half-saved:

* **Live layer (while editing).** Angelo exports the linked scene to a .glb after
  each change and sends it over loopback. The plugin builds it with Godot's own
  ``GLTFDocument`` and shows it in place of every instance of the asset in the
  edited scene. The scene itself is never changed (see "The live preview").
* **Durable layer (on save).** Saving in Angelo writes the asset file (the .blend,
  or the .glb written back), and the plugin re-imports it. The instances are
  rebuilt from the real asset, which ends their preview.

The link is at the **asset** level, not the node level: a node chosen in Godot
resolves to the scene file it was instanced from, every instance of that file
updates, and a node's placement in the level stays Godot's.

Edits flow **one way**, Angelo to Godot. Godot sends only requests (open, close)
and status. Edits in both directions would need conflict resolution.

The person
==========

* **Turning it on** (off until then): the chat bar's + menu, Godot live link >
  Turn on Godot Live Link, or File > Export > Godot Live Link. A link the person
  turned on starts again with Angelo until they turn it off
  (``engine-link-settings.json`` in the state folder). Never in a background or
  automation session.
* **Adding the plugin** to a project: + menu > Godot live link > Add the plugin
  to a Godot project…, or File > Export > Add Angelo Link to a Godot Project….
  A folder browser picks the folder that holds ``project.godot``; the plugin is
  copied into ``addons/angelo_link`` (an older copy is replaced in one step), and
  the link turns on. The person then enables Angelo Link in Godot's Project
  Settings > Plugins: Angelo never edits ``project.godot``, which a running Godot
  editor would overwrite.
* **In Godot**: *Edit in Angelo* in the Scene dock's context menu (on any node made
  from a .blend or .glb, or its children), the FileSystem dock's context menu (on a
  .blend or .glb), or the Angelo menu in the 3D editor's toolbar, which also has
  Stop Live Link, Use Angelo to Import .blend Files, and the link's state. On
  macOS the plugin brings Angelo to the front; on Windows Angelo flashes its taskbar
  button (``desktop_os``).
* **Answering in Angelo**: a corner card, "Godot wants to edit crate.glb", from the
  project, with Open and Not now. When the open file has unsaved changes it says
  so and offers Save and Open; for a file never saved, Discard and Open. Nothing
  opens without this click, because opening replaces Angelo's open file. The card
  answers its own request only, and a plain Open asks again (with Save and Open)
  when the file changed while the card waited. When a .glb's working .blend no
  longer matches the .glb, the card says Angelo starts again from the .glb and
  keeps the older working file as ``<name>.previous.blend``. A request nobody
  answers expires after ten minutes; a newer one replaces it.
* **While linked**: the status bar says "Live in Godot: crate.glb" (in Director,
  which hides the status bar, the top bar says it, with the link's menu) while a
  Godot editor of that project is connected, and "Godot isn't connected:
  crate.glb", with a broken-link icon, when none is (Godot closed or the
  connection dropped). Both bars are global areas, so a change redraws them
  through the message bus, and the top bar's item is as wide as its label in the
  current language (``top_bar_fit``).
  In Godot, the Angelo toolbar menu always shows the link's state in the editor's
  status colours: Angelo: Live, Angelo: Waiting…, Angelo: Connection Lost (until
  the person links again or dismisses it from the menu), or plain Angelo. The plugin
  counts the connection lost when the socket closes or Angelo has answered nothing
  for 60 s (it pings every 10 s); Angelo counts an editor gone when its socket
  closes or it has sent nothing for 45 s. The + menu and
  File > Export offer Stop Editing for Godot (Godot then shows the asset as last
  saved). The link ends by itself when another file is opened, when the file is
  saved under another name, or when Godot asks.

Assets
======

``godot_link_assets.resolve`` (Godot's adapter) admits a ``res://`` path inside the requesting
project (it must hold ``project.godot``), never through ``..``, a link out of the
project, or Angelo's own working folder:

* **.blend**: Angelo opens and saves the file itself. Godot re-imports it with its
  Blender importer, which runs the program in Godot's *Blender path* setting. When
  that setting is empty the plugin offers to use Angelo (a dialog with Use Angelo
  and Not now; it only sets the path on Use Angelo). Angelo works as Godot's
  Blender: Godot runs it in the background with its import server
  (``--background --python-expr``), and Angelo's add-on starts nothing in a
  background session. Saving writes Angelo's .blend format: a Blender older than
  Angelo's (5.2) may not read it, which is why the plugin offers Angelo as Godot's
  Blender.
* **.glb**: Angelo edits a working .blend at ``<project>/.angelo/<res path>.blend``
  and writes the .glb back on every save of it, in a background Angelo reading the
  file just saved (``engine_link_writeback``), as Godot's own .blend import does:
  the exporter changes the scene's frame (to sample animations), which in the
  person's session would put an unkeyed pose back to its keys. The .glb is
  replaced in one step, keeping its permissions; Godot hears ``saved`` once it is
  written, or why it wasn't. Nothing in the ``.angelo`` folder is ever written
  through a link (the folders, the working file, its stamp). The ``.angelo`` folder holds a
  ``.gdignore`` (Godot imports nothing there) and a README; it is meant to be kept
  with the project as the asset's source. ``<working>.blend.json`` records the
  digest of the .glb Angelo last imported or wrote; when the .glb changed since
  (edited elsewhere), Angelo imports it again instead of opening a stale file, and
  keeps the older working file as ``<name>.previous.blend`` (the card says so).
* Anything else (.gltf, .fbx, .obj, a node with no scene file) is refused with the
  reason, shown in Godot. Godot converts those to .glb with Scene > Export As.

Exporter options
----------------

Both layers export with the options Godot's own Blender importer passes to
Blender's glTF exporter, so the live preview and the re-imported asset match.
``GODOT_DEFAULTS`` and ``godot_options`` were recorded from Godot 4.7.2's
requests to Blender, with each ``blender/*`` import setting varied (the cases are
in ``test_native_godot_link_assets``); the asset's own ``.import`` file overrides
the defaults where the person changed them. The live export is a .glb of the
window's scene (``use_active_scene``), uncompressed, images embedded, lights in
the exporter's ``COMPAT`` mode as Godot requests, with two exceptions
(``live_options``): no animations, at the current frame, so a live export never
changes the frame. The re-import on save brings the animations.

Godot's import-time processing (generated LODs, collision from name suffixes,
lightmap UVs) is not applied to the live preview, which Godot's runtime glTF
loader builds; the re-import on save applies it as usual.

The live preview
================

``live_preview.gd`` adds, under each instance (a node whose ``scene_file_path``
is the asset), an internal child (``INTERNAL_MODE_BACK``, no owner) built from the
newest export, and hides the instance's own visual nodes on the rendering server
(``RenderingServer.instance_set_visible``). Internal, unowned children are never
saved and never listed in the Scene dock, and server visibility is not a node
property, so saving the scene in Godot while a preview shows writes exactly what
was there before. The originals are hidden again every half second (the editor
re-shows a node when its visibility changes); switching scene tabs applies the
newest export to the new tab's instances; a re-import of the asset rebuilds the
instances and ends their preview; Stop Live Link, Angelo's end of the link, a
lost connection (Angelo quit or crashed) and disabling the plugin restore
everything, with one notice.

Following the scene
===================

``engine_link`` follows the scene of the window the asset opened in, with the web
preview's change rules (``scene_changes``: geometry, transform, shading, other
data, or the frame, world, camera and what shows; never a selection click). It
exports once the scene has been still for 0.35 s and at least three times the last
export's duration (at most 10 s), never during a drag, a brush stroke, playback, a
background job or Edit Mode. Edit Mode's changes reach the mesh when the person
leaves it, and the export follows then; Godot is told once per stay ("Angelo is in
Edit Mode; Godot updates when you leave it"). Sculpt and the paint modes export as
they go. No export changes the person's mode (``web_export.run_exporter``):
saving in Edit Mode writes the .glb back and stays in Edit Mode. It exports only
while an editor of that project is connected.
Every change counts, the person's or the agent's. A failed export leaves the last
one on screen and says so in Godot.

Transport
=========

The transport, the endpoint record, the requests and replies and the working files are the engine link's, shared by
every engine: :doc:`engine_link`. Godot's editor names itself ``"engine": "godot"`` with its version under
``"godot"``; it sends no ``capabilities``, so its adapter's (``durable``, ``live``) apply.

Limits
======

* The Godot plugin's text is English; Angelo's side is translated like the rest
  of Angelo.
* Requires Godot 4.4 or newer (``EditorContextMenuPlugin``); an older Godot gets a
  message instead of a failed load. Verified with Godot 4.7.2 on macOS, headless
  and windowed (screenshots of each state); 4.4-4.6 are untested. The record path and taskbar flash are written for Windows and
  Linux too, unverified there.
* One asset is linked at a time. The agent's own edits stream like the person's,
  but the agent has no tool of its own for the link.
* The live preview is visual: no collisions, LODs or scripts until the re-import.

Validation
==========

* Host: the engine link's suites (:doc:`engine_link`, "Validation"; the session's
  requests, saves and loads run with Godot's adapter, and its plugin install),
  ``test_native_godot_link_assets`` (resolution, working files, import settings,
  the recorded Godot option table) and ``test_native_godot_link_plugin`` (the
  GDScript agrees with Angelo's side).
* End to end with a real Angelo and a real Godot editor, both without windows:
  ``tests/python/angelo_agent/test_blender_godot_link_e2e.py --binary <Angelo>
  --out <folder>`` (a native suite; ``ANGELO_QA_GODOT`` names the Godot editor).
  Five runs: a .glb and a .blend asset; a .glb in a project that raises every
  GDScript warning to an error, addons included; and two restarts, where the first
  Angelo is killed, or quits as Angelo's exit does, mid-link, and a second Angelo
  answers the next request. Angelo answers Godot's request, edits and saves
  (``godot_link_e2e/angelo_side.py``); a test-only Godot plugin
  (``godot_link_e2e/driver_addon``) drives the shipped one through Edit in Angelo
  and checks that every instance (nested ones too) shows the first export with
  meshes that draw, the edit arrives live, a scene tab switch moves the preview,
  the preview never enters the Scene dock or the saved scene file, the re-import
  holds the edit and ends the preview, disabling the plugin and Stop Live Link
  restore the scene, and a lost Angelo restores it with one notice. Any script
  error in Godot's log fails the run. HOME, Angelo's state and the record are
  private to the run.
* GUI: ``tests/python/angelo_agent/test_blender_godot_link_gui.py`` runs in a
  windowed Angelo in Director Mode: the + menu's Godot group, the request card's
  buttons for a never-saved, an unsaved and a clean file, Open through the card,
  the status bar and Director's top bar, a live edit reaching a headless Godot
  editor, the save's write-back and re-import, Godot's Stop Live Link, and turning
  the link off. It saves the cards and the live state as screenshots.
