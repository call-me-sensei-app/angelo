.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

##################
Engine live link
##################

A person picks an asset in a game engine's editor, edits it in Angelo, and the asset updates in that editor: live
while they work where the editor's plugin can show it, and on every save. One listener and one session serve every
engine; what differs per engine is an **adapter**. This document is the engine-neutral contract (``engine_link``,
``engine_link_assets``, ``engine_link_server``, ``engine_link_protocol``, ``engine_link_writeback``,
``engine_link_ui``); each engine has its own document for its plugin and rules: :doc:`Godot <godot_link>`.

.. contents::
   :local:

Engines
=======

``engine_link_assets.ADAPTERS`` lists the engines this Angelo links with. A request from an editor whose engine is
not listed is refused with code ``engine`` ("This Angelo can't link with o3de yet"), shown in that editor.

==========  ==========================  =======================  ==============================================
Engine      Adapter                     Kinds                    Status
==========  ==========================  =======================  ==============================================
``godot``   ``godot_link_assets``       .blend, .glb             Shipped plugin; verified in Godot 4.7.2
``unity``   ``unity_link_assets``       .blend, .glb, .fbx       Runs in Unity 6000.5.6f1 (batchmode, .fbx);
                                                                 live preview untested (:doc:`unity_link`)
``unreal``  ``unreal_link_assets``      .glb (source file)       Runs in UE 5.8.2 (headless, macOS): Python
                                                                 plugin saves; live with the C++ Angelo Link
                                                                 Live plugin (:doc:`unreal_link`)
==========  ==========================  =======================  ==============================================

Adapters
========

An adapter is a module of plain Python (no ``bpy``) with:

* ``ENGINE`` (the auth frame's ``engine``) and ``DISPLAY`` (the engine's name as people write it, "Godot"; never
  translated).
* ``CAPABILITIES``: what its editors show when they don't say (``durable``: re-import on save; ``live``: live
  scenes). An editor that sends ``capabilities`` is believed.
* ``is_project(folder)`` and ``REFUSED``: the English refusal text for each code (``not_project``, ``outside``,
  ``kind``, ``missing``; an engine may add its own), shown by the plugin, whose text is English.
* ``resolve(request)``: the ``engine_link_assets.Asset`` for an ``open`` or ``close`` request, or ``AssetError``.
  The asset's ``ident`` is what the engine calls it (``res://props/chair.glb``); every reply echoes it. Its file
  must be inside the project, never through ``..``, a link out of the project or Angelo's working folder
  (``engine_link_assets.inside``, shared).
* ``KINDS``: the file kinds it admits. A ``.blend`` is opened and saved as itself; any other kind is edited in a
  working .blend (below).
* ``import_params(asset)``: the person's import settings Angelo's exports honour (Godot: the ``.import`` file).
* ``live_options(params)``: glTF exporter options for the live .glb, which never animate or change the frame
  (``live_gltf``).
* ``durable(asset, params)``: how a save writes the asset: ``None`` for a .blend, else ``(exporter, options)`` with
  exporter ``gltf`` or ``fbx``.
* ``PLUGIN_SOURCE``, ``PLUGIN_FOLDER`` and ``PLUGIN_IGNORE``: the plugin Angelo ships, where
  ``install_plugin`` copies it in a project (in one step, replacing an older copy), and what the copy leaves out.

``test_native_engine_link_assets`` checks every listed adapter against this list.

Requests and replies
====================

* **Requests** (from the editors): ``open`` and ``close`` with ``id``, ``project`` (the project folder, absolute),
  ``asset`` (the engine's name for it), ``project_name`` and ``node`` (shown on the card), plus what the engine's
  adapter reads (Unreal adds ``source``, the file the asset was imported from); ``status`` (reports such as
  ``reimported``) and ``ping``. The engine is the one the editor named when it authenticated, never a request field.
* **Replies**: ``pending``, ``opened``, ``refused`` (``code``: an adapter's, or ``engine``, ``superseded``,
  ``declined``, ``failed``), ``closed``, ``scene`` (the live .glb), ``saved`` (re-import ``files``; ``scene_version``: the newest live scene the written file holds), ``status``
  (``edit_mode``, ``export_failed``, ``write_failed``, with an English ``message`` naming the engine) and ``ended``.
* **``ended`` reasons**: ``engine`` (the editor asked), ``angelo`` (the person's Stop Editing), ``stopped`` (the link
  was turned off), ``replaced`` (another asset was opened), ``other_file``, ``saved_elsewhere``. A plugin shows a
  notice for every reason but ``engine``.
* **Who hears what**: frames about an asset go only to editors of its engine and project. Live ``scene`` frames, and
  the ``edit_mode`` and ``export_failed`` notices about them, go only to editors that show live scenes; when none is
  connected nothing is exported. ``saved``, ``ended`` and ``write_failed`` reach every editor of the project.

What an editor says about itself
--------------------------------

The auth frame's ``client`` object keeps, bounded (``engine_link_server.client_info``): ``engine`` (default
``godot``), ``version`` (sent under the engine's own name, ``"godot": "4.7.2"``), ``capabilities`` (at most eight),
``project``, ``project_name`` and ``plugin``. Nothing else is kept.

Saving and working files
========================

* **A .blend** asset is Angelo's own file: saving it is the asset's save, and the editors hear ``saved``.
* **Any other kind** is edited in a working .blend at ``<project>/.angelo/<path in the project>.blend``, imported
  from the asset when there is none or the asset changed since Angelo last read or wrote it (a digest beside it,
  ``<working>.blend.json``; the older working file is kept as ``<name>.previous.blend`` and the card says so). Each
  save writes the asset back in a background Angelo reading the file just saved (``engine_link_writeback``, with
  the adapter's exporter: ``export_scene.gltf`` or ``export_scene.fbx``), as an engine's own .blend import does, so
  the exporter never changes the person's frame or pose; the file is replaced in one step, keeping its permissions,
  and the editors hear ``saved`` once it is written, or ``write_failed``. Nothing in ``.angelo`` is written through
  a link. No engine imports the folder: it is hidden, and it holds a ``.gdignore`` for Godot.
* **Engines with no exporter table of their own** use ``GLTF_DURABLE``: the glTF exporter's defaults with modifiers
  applied (what the person sees).

The person
==========

* **Turning it on**: the + menu's Godot group or File > Export (one listener for every engine).
* **The request card** names the engine: "Godot wants to edit crate.glb". Its text says the changes show live, or,
  for an editor that shows no live scenes, that saving updates the asset there.
* **While linked**: the status bar (Director: the top bar) says "Live in Godot: crate.glb" while an editor of the
  asset's engine and project that shows live scenes is connected, "Linked to <engine>: …" while only editors that
  re-import on save are (``engine_link.editors``), and "<engine> isn't connected: …" (in alert colours) when none is;
  Stop Editing names the engine.
* **Adding a plugin**: Godot's from the + menu and File > Export (``godot_link.rst``). Other engines' plugins get
  their menu entries when they are verified in their engine.

Transport
=========

One listener serves every engine's plugin (protocol ``angelo-link/1``), each naming
its engine when it authenticates.

* **Server** (``engine_link_server``): 127.0.0.1, a free port, at most eight
  editors and four connections still authenticating (the oldest of those makes way
  for a new one, so silent connections can't keep an editor out). Threads never
  touch Blender; the main thread takes requests with
  ``drain`` on a 0.1 s timer and sends with ``send``. Each client has its own
  writer thread, and only the newest scene waits to be sent (other frames keep
  their order), so a slow editor skips versions instead of falling behind or
  blocking Angelo. Stopping gives queued frames (``ended``) up to a second to
  leave.
* **Frames** (``engine_link_protocol``): a 4-byte big-endian length, a UTF-8 JSON
  header of at most 64 KiB, then ``bytes`` bytes of payload (at most 512 MiB).
  Clients send only ``open``, ``close``, ``status`` and ``ping``, never a payload:
  a client's header that announces one ends the connection before any of it is
  read.
  Angelo sends ``pending``, ``opened``, ``refused`` (with ``code`` and an English
  ``message``), ``scene`` (the .glb), ``saved``, ``ended`` (with ``reason``),
  ``closed``, ``status`` and ``pong``.
* **Authentication**: a mutual HMAC-SHA256 challenge. The token never crosses the
  socket; a client that hasn't proved it within 10 s is dropped, and the plugin
  trusts nothing until the server has proved it too (so another program on a
  stale port learns nothing).
* **The endpoint record** ``angelo-link.json`` in Angelo's per-user data folder
  (``~/Library/Application Support/Angelo``, ``%LOCALAPPDATA%\Angelo``,
  ``$XDG_STATE_HOME/Angelo``; ``ANGELO_LINK_RECORD`` overrides it on both
  sides): protocol, port, process ID, the 32-byte token in hex, the application
  (empty for a background session, so the plugin never starts another Angelo)
  and its executable (for an engine that runs Blender itself, such as Godot's Blender path). Written owner-only and
  exclusively, never through a link; a record whose process is gone, or whose port
  no longer answers (a process ID reused after a crash), is replaced; one of
  another live Angelo is never touched (that Angelo has the link). Removed
  when the link stops, the add-on unloads, at ``exit_pre`` and, files and sockets
  only, at ``atexit``.

Same-user processes can read the token, as with the external CLI; the link
carries scene data out and accepts only requests that the person answers in
Angelo, so it grants no authority beyond showing an asset the person chose.

Validation
==========

* Host: ``test_native_engine_link_protocol`` (frames, proofs, the record, the server over real sockets, what an
  editor says about itself), ``test_native_engine_link_writeback`` (the background write-back with a stand-in app,
  both exporters), ``test_native_engine_link_assets`` (every adapter against the contract, the working path,
  installing only into an engine's own projects) and ``test_native_engine_link_state`` (requests, sessions, saves
  and loads on a stand-in ``bpy``, with Godot's adapter and a stand-in second engine: routing by engine, refusing an
  engine with no adapter, live frames only to live editors of that engine and project, the adapter's exporter on
  save).
* Each engine's document lists its own suites; Godot's end-to-end and GUI suites run the whole path through this
  module.
