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

Every glTF an adapter's exporter writes carries the objects' game physics as OMI (:doc:`game_physics`); an engine
that reads OMI (Godot 4.4 and newer) builds its physics nodes from it, others ignore it.

Requests and replies
====================

* **Requests** (from the editors): ``open`` and ``close`` with ``id``, ``project`` (the project folder, absolute),
  ``asset`` (the engine's name for it), ``project_name`` and ``node`` (shown on the card), plus what the engine's
  adapter reads (Unreal adds ``source``, the file the asset was imported from); ``status`` (reports, among them the
  acknowledgements below) and ``ping``. The engine is the one the editor named when it authenticated, never a request
  field.
* **Replies**: ``pending``, ``opened``, ``refused`` (``code``: an adapter's, or ``engine``, ``superseded``,
  ``declined``, ``failed``), ``closed``, ``scene`` (the live .glb), ``saved`` (re-import ``files``; ``scene_version``: the newest live scene the written file holds; ``save``: the save's number in this link, which an acknowledgement echoes), ``status``
  (``edit_mode``, ``export_failed``, ``write_failed``, with an English ``message`` naming the engine) and ``ended``.
* **``ended`` reasons**: ``engine`` (the editor asked), ``angelo`` (the person's Stop Editing), ``stopped`` (the link
  was turned off), ``replaced`` (another asset was opened), ``other_file``, ``saved_elsewhere``. A plugin shows a
  notice for every reason but ``engine``.
* **Who hears what**: frames about an asset go only to editors of its engine and project. Live ``scene`` frames, and
  the ``edit_mode`` and ``export_failed`` notices about them, go only to editors that show live scenes; when none is
  connected nothing is exported. ``saved``, ``ended`` and ``write_failed`` reach every editor of the project.

Acknowledgements
----------------

An editor whose auth frame lists the capability ``acks`` tells Angelo what it did with what Angelo sent, as
``status`` frames about the linked asset:

* ``previewed`` (``version``: the live scene shown, ``instances``: how many instances show it) or ``preview_failed``
  (``version``, an English ``message``) after each live ``scene``;
* ``reimported`` or ``reimport_failed`` (``message``) after re-importing on a ``saved``, with that frame's ``save``.

Angelo keeps them per editor (``engine_link_facts``) only from an editor of the linked asset's engine and project,
about that asset by its name, bounded (eight editors; numbers 0 to 2^53, text 200 characters), and drops an editor's
when it leaves. They are the editor's report, never authority: Angelo shows them to the agent and changes nothing on
them. An editor without ``acks`` may still send some (Unity's and Unreal's plugins sent ``reimported`` before
``acks`` existed); they are kept, and one without ``save`` counts for the newest save whose asset was written before
it arrived, but nothing waits for such an editor. Plugins: Godot (``previewed``, ``preview_failed``, ``reimported``),
Unity (all four) and Unreal (all four; ``previewed`` with the Angelo Link Live plugin) send them from Angelo 1.0.9's
plugins; an older copy in a project acknowledges less until it is updated (Add Angelo Link to the project again).

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
  the exporter never changes the person's frame or pose; a glTF write-back, like the live scene, writes each object
  with a current baked material with it (:doc:`long_jobs`, "Baked materials at export"), and a save with an object
  that keeps its own instead sends ``baked_stale``, ``baked_stale_nearby`` (only something within its baked AO's
  reach changed) or ``baked_packing`` status; the file is replaced in one step, keeping its permissions,
  and the editors hear ``saved`` once it is written, or ``write_failed``. Nothing in ``.angelo`` is written through
  a link. No engine imports the folder: it is hidden, and it holds a ``.gdignore`` for Godot.
* **The engine preflight** of each save runs afterwards in another background Angelo (:doc:`engine_preflight`,
  "Engine-link saves"): report-only, started once the engine has the asset, written to
  ``<project>/.angelo/<path>.preflight.json``.
* **Engines with no exporter table of their own** use ``GLTF_DURABLE``: the glTF exporter's defaults with modifiers
  applied (what the person sees).

The person
==========

* **Turning it on**: the chat bar's + menu, Share > Game engine live link > Turn on the live link, or File > Export
  (one listener for every engine). The agent turns it on with ``angelo_app_feature`` (``ENGINE_LINK``, ``ON``), on a
  card the person approves; it never runs in Auto without one (``app_feature_spec``).
* **The request card** names the engine: "Godot wants to edit crate.glb". Its text says the changes show live, or,
  for an editor that shows no live scenes, that saving updates the asset there.
* **While linked**: the status bar (Director: the top bar) says "Live in Godot: crate.glb" while an editor of the
  asset's engine and project that shows live scenes is connected, "Linked to <engine>: …" while only editors that
  re-import on save are (``engine_link.editors``), and "<engine> isn't connected: …" (in alert colours) when none is;
  Stop Editing names the engine.
* **Adding a plugin**: + menu > Game engine live link > Add Angelo Link to a Godot / Unity / Unreal Engine project…,
  or the same rows in File > Export (``ANGELO_OT_godot_link_install`` with its ``engine``). A folder browser picks the
  project; the adapter's ``is_project`` must accept it (otherwise the report names the folder to choose), the
  adapter's plugin is copied into ``PLUGIN_FOLDER`` (``install_plugin``: Godot ``addons/angelo_link``, Unity the
  embedded package ``Packages/com.hyperbond.angelo-link``, Unreal ``Plugins/AngeloLink``) and the link turns on,
  as the row's subtitle says. The report says the engine's last step: Godot, enable it in Project Settings >
  Plugins; Unity loads an embedded package by itself; Unreal, enable it in Edit > Plugins and restart the editor.
  The agent opens the same folder browser with ``ADD_TO_PROJECT``; the person still chooses the folder.
  The copy gets the person's permissions, never the bundle's (``user_copies``): an install whose scripts the person
  can't write has read-only modes, and a read-only folder can't be moved into the project (macOS and Linux need
  write permission on a folder to give it a new parent; the 1.0.8 Mac slot's "Permission denied"). Every copy
  into a person's folders does the same: engine add-ons Angelo can't link (``engine_setup``) and imported Blender
  settings (``settings_import``). Pinned by ``test_native_engine_link_assets``, ``test_native_engine_setup`` and
  ``test_native_settings_import``.

Reading the project
===================

Resolving an asset reads the project's folders, which may be huge or on a slow or network drive, so it never runs
on Angelo's main thread (``engine_link_reads``):

* **Every walk reads entry by entry** (``engine_link_assets._listing``), never a folder whole or sorted first, and
  stops at its entry bound: the by-name lookup's ``NAME_SCAN`` (20,000), the hard-link check's ``WORKING_SCAN_LIMIT``
  (50,000), Unreal's project check's ``PROJECT_SCAN`` (5,000) and the path scrubber's root and home listings'
  ``PLACES_SCAN`` (5,000, read once per run on the main thread). A walk stopped at its bound is unfinished: the lookup
  is refused as unsure, the hard-link check counts as holding the file, a folder isn't taken for an Unreal project,
  the scrubber trusts no slash inside a word. A walk on a resolve's thread also stops at its time (``READ_S``, 10 s)
  by raising ``Late``: the resolve is refused as unreadable, never as an asset outside the project. The audit of 2026-11-03 found a 100,000-entry folder read whole and
  sorted before the bound was checked.
* **Resolving runs on a daemon thread** (``Lookup``): the main thread waits ``QUICK_S`` (50 ms), which most resolves
  need, then polls. The agent's OPEN (``engine_link_tool_open``) then returns a deferred result as PUSH and SAVE do;
  deciding (ambiguity, refusals, the card policy, the card) stays on the main thread, and a refusal is the same
  unchanged result either way. An editor's requests (``Inbox``) are handled in the order that editor sent them: one
  whose asset is being read holds that editor's later ones, so a ``close`` never overtakes the ``open`` sent before it,
  never another editor's, and never its own status acknowledgements (which PUSH and SAVE wait for); an editor has at
  most one resolve running, and a pass costs time in proportion to the requests waiting. Each request keeps its
  arrival number, so the request card answers the request sent last, whichever was read first: a request sent before
  one that already raised a card (answered or not) is refused as ``superseded`` when it is read. A resolve that can't
  start (no thread to read on) is refused as unreadable; the link's timer never stops for it. A link started again
  never receives a request sent to the one before. A resolve past its time is refused: OPEN with
  ``VALIDATION`` (name it as its engine does), an editor's request with code ``unreadable``, whose message each
  plugin shows.

The agent
=========

Two deferred tools (``engine_link_tool_spec``, ``engine_link_tool_host``) act on a link the person turned on; neither
turns it on, which stays ``angelo_app_feature``'s card. Results are compact English facts naming engines, projects
and assets the way the engines do, never a path.

* ``angelo_engine_link_status`` (read-only, no card): ``link`` (on or off), ``engines_running``, the connected
  ``editors`` (engine, project name, version, ``live``, ``acks``, whether it is the linked asset's project, and what
  it acknowledged about the linked asset), the ``linked`` asset (its name, engine and kind; whether its .blend is the
  open file and unsaved; whether a live change waits; what was last ``pushed`` and ``saved`` with seconds ago, the
  write-back's state or error), a ``request`` waiting for the person's card (read, never answered), and
  ``preflight``: the engine preflight's report of the link's last save (``engine_preflight.link_report`` when the
  build has it, or ``engine_link_tool_host.PREFLIGHT``), as its status (``done``, or ``failed`` with its error),
  scene version, counts and at most three findings, **only when it describes that save** (``_describes``): it echoes
  the save's exact name (``save_key``: this Angelo process's random ``engine_link_facts.RUN`` and the save's number,
  ``engine_link_facts.save_key``; a write-back's end gives its own save's, never the last save's), which the link
  gives the check when the save asks for it, for every kind of asset
  and a failed check alike; and when it carries ``target_sha256`` (the bytes it read back), that is the digest of
  what the save's write-back wrote, recorded with the stamp. Neither a scene version (it moves only with a live push,
  so two saves share one: break-it 9-H1) nor the saved file's identity (made-up inodes and 1-2 s times on FAT, HFS+
  or some network shares: 10-N1) names a save. Otherwise ``state: "pending"``. No save of the link, or a save whose
  write-back failed, gives no report. A known limit is the preflight's own (#131): on such a volume, its check of a
  save can open a re-save made within the same tick and of the same size, so a report under one save's name may
  describe a slightly newer file, never an older one; the newer save gets its own report. **Bounded whatever the
  editors send**: at most 2,500 bytes as escaped JSON (``STATUS_BUDGET``); past it, failure messages go first, then
  long names are cut, then the editors are counted instead of listed (``editors_summary``), then the costliest
  string of any field is halved while that saves bytes (asset names last), and if nothing shrinks any more the
  answer is a fixed minimal one that says it was cut.
* **No paths**: errors from the OS, Blender and the write-back child name files. An error the agent reads fails
  closed (``engine_link_facts.plain``): after folding Unicode and escaped slashes, HTML entities and percent-encoding,
  it is kept as written only when it ends inside no quoted name (a path cut before its first separator) and none of
  its words is a path, which every word holding a slash, backslash, ``~``, ``$`` or ``..``, and every colon path
  ("Disk:Users:x", never C++'s ``std::x``), is, unless the whole word is an engine's own name (``res://``,
  ``user://``, ``uid://``, Unreal's ``/Game/``, ``/Engine/``, ``/Script/``), a lone slash, or a slash between word
  parts ("Rig/Body", "3/4", "Assets/Ríg.fbx") none of which names a place: an entry at this machine's filesystem
  root or in the home folder, a folder where any supported platform keeps people's files (``PEOPLE_ROOTS``), a part
  of the home folder's path or the user name, all folded as the text is; a place whose name holds a space or quote
  ("Jack Doe") counts wherever it touches a separator. Any other character Unicode names a slash, solidus or
  diagonal reads as a backslash, and the whole fold repeats until the text stops changing: still changing after
  four rounds, it counts as a path. Otherwise it is rebuilt from structured parts alone (the
  exception's own type when it is one, Python's own exception names, the OS's own words for its error number,
  never a name read from the text), so no path, user or folder name survives whatever surrounds it; path-free text
  with an unclosed quote is lost the same way (the safe side). Host names are not paths and stay. It reads at most 1,024 characters past the kept text, each
  once, so a long message never stalls the main thread. An engine's own asset names in results (``res://``) stay
  whole. A link of any name or case, or a hard link, that leads into Angelo's working folder is refused like
  the folder itself (``engine_link_assets.inside`` compares identities; a hard link by inode numbers from the
  folder's listing, each match confirmed with its own stat, since Windows listings carry no device; a listing
  whose numbers aren't the files' own, or a working folder that is itself a link, is refused without a walk).
* ``angelo_engine_link`` ``OPEN`` (``asset`` named as its engine names it, ``res://...`` or ``Assets/...``, or by
  its file name as a person says it, "barrel" or "Barrel.glb", in any case or Unicode form, when exactly one asset of
  the connected projects has it; ``engine`` and ``project`` pick one when several connected projects hold it) raises the same request card an
  editor's Edit in Angelo raises (``engine_link_agent.ask``), titled with who asked: "Angelo's agent wants to open
  crate.glb from Godot", or a connected app by its label ("claude-code wants to open ..."), never as the engine's
  request. Nothing opens inside the tool: the person answers on the card
  (Open, Save and Open, Discard and Open, Not now), opening replaces the open file, and the chat that asked stays with
  its own file (``document_identity``); the result says so (``opened: false``). That card is the approval, so OPEN
  has no tool card of its own (``engine_link_tool_spec.asks_only``; a read-only chat still refuses it). It resolves
  the asset with the engine's adapter against a connected editor's project, with every check an editor's request
  gets; an engine whose request needs what only its plugin knows (Unreal's source file,
  ``OPENS_BY_NAME = False``) is refused with the reason. **No repeated cards** (``engine_link_agent.refusal`` on the
  explicit state ``engine_link_facts.new_policy``), moved only by the person, never by a clock, Angelo's own turns
  or the engine's requests: OPEN is refused while any request card waits (an editor's included, which it never
  replaces). After an agent request the person didn't open (Not now, expired, replaced by an editor's, the link
  turned off), the conversation that asked asks again only once the person has sent their own words in it after
  that (``Controller.person_spoke_at``, set by ``send`` for the person's message only: never Angelo's host requests,
  escalations, worker events or an app's task; words written while the card waited don't count); an app only once
  the person opens any request or turns the link off and on themselves: a start while any conversation's agent call
  executes (a script reaching the + menu's operator, ``ENGINE_LINK``, an app's call) keeps every hold
  (``engine_link_agent.agent_acting``; unknown counts as acting). After two such requests in a row every caller waits
  the same way (a conversation the person writes in after the second is released), and so does an asset whose agent
  request they didn't open. Opening any request releases everyone; one that failed to
  open, or an engine request nobody opened, changes nothing.
  A lookup by file name (``engine_link_names.resolve``, ``engine_link_assets.named``) never guesses: several matches
  in any project, or a project it couldn't read whole (past ``NAME_SCAN`` entries or its time, an entry it can't
  read, a match whose name holds a control character; "Reading the project"), refuse with the matches named whole (bounded, the rest counted), even when
  another project has one. It reads no hidden entry, link, junction, Angelo's working folder or folder the engine
  ignores (Godot's ``.gdignore``, Unity's trailing ``~``); names in any script and spaces of any width match; the
  match it picks is resolved with every check of an engine name.
  Every refusal of the tool but ``INTERNAL_ERROR`` comes before it sent, saved, asked or ended anything and says so
  (``scene_changed: false``), so a typed plan that ends with one returns it to the model for a correction
  (``claude_outcomes._UNCHANGED_WHEN_REFUSED``) instead of ending the person's request without a reply. A refusal no
  second request could change while the person's state stands (Edit Mode, no window, the wrong open file:
  ``engine_link_tool_host._final``, ``refusal_details.final``) also says it changed nothing, so it holds no
  recovery, but ends the request with its reason shown, since a correction would only put another approval card in
  front of the person.
  When an editor's request replaces the agent's, the agent's card leaves the screen first, and every card answers
  only its own request (``engine_link.decline(request=...)``), so a click meant for one never refuses another. When
  the person opens it, every editor of that project hears ``opened`` and links, as one that asked would.
* ``angelo_engine_link`` PUSH, SAVE and CLOSE (approved on a card that names the asset and engine; never in Auto):
  ``PUSH`` exports the
  linked scene now and sends it to the editors that show live scenes (refused when none does, or while Angelo is in
  Edit Mode, playing, rendering or in a modal tool); ``SAVE`` saves the open file as File > Save does when it is the
  linked asset's .blend (refused otherwise), so the save handler writes the asset back and the editors re-import it;
  ``CLOSE`` ends the link (``ended`` reason ``angelo``) and keeps the file open.
* Every action is refused with its reason when the link is off (``UNSUPPORTED``, saying only the person turns it on);
  PUSH, SAVE and CLOSE also when nothing is linked, before any card is shown.
* **Waiting for acknowledgements**: PUSH and SAVE return a deferred result the controller polls on the main thread
  (Stop ends the wait; the push or save stands). PUSH waits up to 8 s for each acknowledging live editor's
  ``previewed``; SAVE up to 120 s for the write-back, then up to 30 s for each acknowledging editor's ``reimported``,
  and reads the preflight's report only then, for that save as STATUS does (pending while its check runs: the audit
  of 2026-11-03 found the previous save's report returned as the new one's).
  Editors without ``acks`` are listed as ``unconfirmed`` and never waited for; one that doesn't answer in time is
  listed in ``no_answer``, one that disconnected in ``left``. A wait that runs out is a result, not a failure, and
  so is the controller's own expiry after it (``pending_delivery.expire_media_job`` delivers what stands; a turn's
  end never revokes it, since a call stays executing while its result is awaited). A wait
  is bound to its link: when the link ends or another begins, it ends with ``state: "unknown"``. A push counts only
  acknowledgements that came after it; a save only those that came once its asset was written, numbered with this
  save or with none. Saves are numbered once per Angelo process and each write-back carries its save, link and live
  scene version as its key (``engine_link_saves``), so one link's or save's write-back never answers another's.
  PUSH exports the scene the link follows, never the chat's window when another window shows it.
* **MCP apps** (``external_clients``): the status tool at Read, the edit tool at Edit with a window only (never
  headless). ``angelo_app_feature`` stays unexposed.

Engine detection and the suggestion
===================================

Angelo suggests the link when an engine's editor is open, and tells the agent which engines run, so either can
offer it at the right moment (``engine_detect``, ``engine_suggest``, ``engine_suggest_ui``).

* **What is detected**: whether Godot, Unity or the Unreal Editor runs (macOS: ``proc_listallpids`` and
  ``proc_name`` from libproc; Windows: a Toolhelp32 snapshot; neither starts a process or asks for a permission),
  and, once a launch, whether one is installed where its installer or launcher puts it (``INSTALL_GLOBS``). Names
  are matched in memory (``engine_of``: any name starting ``godot``; ``Unity``, not the Hub; ``UnrealEditor`` and
  ``UE4Editor``, not the windowless ``UnrealEditor-Cmd``) and dropped: only engine ids leave the module, and nothing
  holding a process name or a path is stored, logged or sent. Other operating systems detect nothing.
* **Off the main thread**: a main-thread timer (``engine_suggest_ui.tick``) starts one scan at a time on a worker
  thread and reads its result 0.1 s later (``COLLECT``), so a running engine is acted on at once; the main thread
  does no detection. A scan every 10 s while an engine is left to suggest, then every 60 s (the agent's facts only).
  Never in a background session or an automation run.
* **Cost** (measured 2026-10-31 on Mac 1, Apple M4 Pro, 475 processes, Blender's Python 3.13, 50 runs): a running
  scan 1.22 ms median (1.35 ms p90) on the worker thread; the installed check 0.14 ms once a launch. That is about
  0.012% of one core while suggestions remain and 0.002% after; the main thread's two ticks per scan only swap a
  list. A scan
  that finds what the last one found changes nothing: no card, no redraw and no bytes for the agent.
* **The suggestion**: a corner ``SuggestionCard`` (card kit, translated, not modal) titled "<Engine> is open",
  with **Not now** and **Link a project…**, which opens that engine's folder picker (``godot_link_install``): the
  plugin is copied and the link turns on, as the card's text says. Nothing connects without that click. It is shown
  once per engine and person, whatever the answer (``engine-suggestions.json`` in the state folder: engine ids only;
  an unreadable file suggests nothing), and never while the link is on or once the person has turned it on before.
  One suggestion waits at a time, never replaced by another engine's (``card_host`` keeps one card of a kind); it is
  withdrawn when its engine closes or the link turns on, and checked again as it shows. An engine counts as
  suggested only once its card has shown (``SuggestionCard.opened``), so a window that could not show cards uses up
  nothing. Two engines at once: one card, the next engine's after it.
* **The agent**: a request's observation carries ``engines`` (``{"running": ["Unity"], "live_link": false, "hint":
  ...}``) only when that changed since the chat's agent was last told (``engine_suggest.Reported``, an event key in
  ``context_ledger.EVENT_OBSERVATION_KEYS``): an unchanged computer adds no bytes, and nothing is sent until an
  engine runs or the link is on. Installed engines are not in it (every first request of a game developer would pay
  for them); the ``FEATURES`` lookup lists them (``feature_guide``).

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

* Host: ``test_native_engine_detect`` (faked macOS and Windows process tables and file systems, the suggestion
  memory, the agent's note sent only on change) and ``test_native_feature_guide`` (the + menu rows of every engine,
  the agent's entry, the people's guide). ``test_native_engine_link_protocol`` (frames, proofs, the record, the server over real sockets, what an
  editor says about itself), ``test_native_engine_link_writeback`` (the background write-back with a stand-in app,
  both exporters), ``test_native_engine_link_assets`` (every adapter against the contract, the working path,
  installing only into an engine's own projects), ``test_native_engine_link_tool`` (the agent's tools: deferral,
  approval and app levels, refusals that never turn the link on, the compact status without paths, acknowledgements
  kept only from the asset's editors, the bounded waits, SAVE only of the asset's own file, OPEN's one card at a time
  and its pause after a decline) and
  ``test_native_engine_link_state`` (requests, sessions, saves
  and loads on a stand-in ``bpy``, with Godot's adapter and a stand-in second engine: routing by engine, refusing an
  engine with no adapter, live frames only to live editors of that engine and project, the adapter's exporter on
  save).
* Each engine's document lists its own suites; Godot's end-to-end and GUI suites run the whole path through this
  module.
