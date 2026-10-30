.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

#######################
Unreal link (save sync)
#######################

The Unreal Engine counterpart of the Godot live link (``godot_link.rst``): a person picks a mesh asset in the
Unreal Editor, edits it in Angelo, and **each save in Angelo updates the asset in Unreal** (Angelo writes its source
file; the plugin re-imports it with the asset's own settings). With the C++ Angelo Link Live plugin enabled, edits
also show live in the open level before a save ("The live layer"). This document records what the engine offers (with the source it was read
from), the architecture and phases, what ships, and what ran.

In this checkout:

* ``angelo_agent/unreal_plugin/AngeloLink``: the content-only Unreal plugin in Python that speaks Angelo's own link
  protocol (phase 1, save-time sync), MIT like the Godot plugin; Angelo's adapter ``unreal_link_assets``.
* ``tests/python/angelo_agent/unreal_link_e2e/``: the headless-editor test, and the remote-execution client
  (``unreal_link_remote.py``, phase 0, a fallback and test harness; test tooling, not shipped).
* ``tests/python/angelo_agent/test_native_unreal_link.py``: the plugin and the remote-execution client, without
  Unreal.
* ``angelo_agent/unreal_plugin/AngeloLinkLive``: the live layer, a C++ editor plugin (phase 2, "The live layer"
  below), MIT; ``test_native_unreal_link_live.py`` checks its Python side and its sources without Unreal.

Names follow the engine-neutral link (approved 2026-10-29): protocol ``angelo-link/1`` (HMAC contexts
``angelo-link/1/client`` and ``angelo-link/1/server``), record ``angelo-link.json`` in Angelo's per-user data
folder, ``ANGELO_LINK_RECORD`` to override it, and ``"engine": "unreal"`` in the auth frame's ``client``.

.. contents::
   :local:

Sources
=======

Every engine claim below was read from the installed Unreal Engine 5.8.2 (``Engine/Build/Build.version``,
changelist 56702186) unless marked *unverified*. Paths are relative to ``Engine/``. Nothing was run inside
Unreal (see "Validation"); a claim about runtime behaviour that the source implies but no run proved is marked so.

Architectures
=============

Three ways to connect, compared on what the person must do, security, latency and what each can show.

(A) Remote execution, no plugin of ours
---------------------------------------

The Python Editor Script Plugin's remote execution, which Epic's former Send to Unreal Blender add-on (send2ue)
uses. Protocol version 1, magic ``ue_py``, documented in
``Plugins/Experimental/PythonScriptPlugin/Source/PythonScriptPlugin/Private/PythonScriptRemoteExecution.cpp``:
UDP multicast ``ping``/``pong`` discovery (the pong carries user, machine, engine version and root, project root and
name), then the *client* listens on TCP, multicasts ``open_connection`` with its address, the editor connects to it,
and ``command`` / ``command_result`` messages run Python and return its log lines. Epic's client is
``Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py``.

* **What the person enables.** The Python Editor Script Plugin is off by default and still marked beta
  (``PythonScriptPlugin.uplugin``: ``EnabledByDefault: false``, ``IsBetaVersion: true``); enabling it needs an editor
  restart. Then Project Settings > Plugins > Python > *Enable Remote Execution?*. That setting is
  ``config=Engine, defaultconfig`` (``PythonScriptPluginSettings.h``), so it is written to the project's
  ``Config/DefaultEngine.ini`` and reaches every teammate through version control. Defaults
  (``PythonScriptPluginSettings.cpp``): group ``239.0.0.1:6766``, bind address ``127.0.0.1``, TTL 0, 2 MiB buffers.
* **macOS needs another bind address** (measured here, not yet against a real editor): on macOS 26 a UDP socket bound
  to 127.0.0.1 never receives datagrams addressed to the multicast group; bound to every address it does
  (``unreal/mcast_probe2.py`` in the run's scratch folder, the same with the sandbox off). The editor binds its socket
  to the bind address (``Runtime/Networking/Public/Common/UdpSocketBuilder.h``, ``Build``), and Epic's client does
  too; run against a stand-in editor, Epic's client discovered nothing with the default ``127.0.0.1`` and worked with
  ``0.0.0.0``. So on macOS the default settings very likely find no editor, and the person must also change the
  bind address. Angelo's draft binds its receiving socket on every address and reads the project's
  ``DefaultEngine.ini`` to join and send on the editor's interface (``project_settings``).
* **Security.** Neither channel is authenticated. Any process that can reach the group can make the editor connect
  to it and run arbitrary Python as the person, with the project and its credentials; with bind ``0.0.0.0`` and a
  TTL above 0 that includes the local network. Turning this on for Angelo widens every teammate's attack surface.
* **Framing.** Messages have no length prefix: the editor parses whatever bytes one read returns as one JSON
  message (``FPythonScriptRemoteExecutionCommandConnection::Tick``), and Epic's client stops reading at the first
  short read. A command must therefore arrive in one read (the draft caps it at 32 KiB and passes files by path), and
  a large result must be read until it parses (the draft does).
* **Latency.** Commands run on the editor's game thread from the plugin's tick (``FPythonScriptPlugin::Tick``).
  While Angelo is in front, the editor is in the background and ticks at most 3 times a second
  (``UEditorEngine::GetMaxTickRate`` with ``bThrottleCPUWhenNotForeground``, on by default in
  ``EditorPerformanceSettings.cpp``), so each command waits up to a third of a second to start. Every "update" is a
  real re-import (translator, pipelines, mesh build, Nanite when enabled), and it rewrites the asset itself: there is
  no live layer that leaves the asset alone. Per-change re-import time was not measured (*unverified*; it would be
  seconds for anything but small meshes).

Verdict: good as a zero-install fallback for the durable layer and as a test harness, not as the product. The draft
(``unreal_link_remote.py``) implements it. It lives with the test tooling, not in the add-on: it binds its multicast
receiver on every address (the macOS finding above), and nothing Angelo ships may listen beyond loopback
(``test_native_team_account_ui``); shipping phase 0 would mean a reviewed opt-in module.

(B) A content-only Python plugin that speaks Angelo's protocol (recommended phase 1)
---------------------------------------------------------------------------------------

Every mounted content root's ``Content/Python`` folder goes on ``sys.path`` and each ``init_unreal.py`` there runs at
start-up (``PythonScriptPlugin.cpp``, ``RegisterModulePaths`` and ``RunStartupScripts``), so a plugin with no C++ can
run Python in the editor. Its ``.uplugin`` lists ``PythonScriptPlugin`` as a dependency, so enabling Angelo Link
enables Python too. It connects *out* to Angelo exactly like the Godot plugin: Angelo's record, the mutual
HMAC-SHA256 proof, framed JSON; Unreal opens no port. It adds *Edit in Angelo* to menus with script menu entries
(``Developer/ToolMenus/Public/ToolMenuEntryScript.h``, ``UToolMenus::ExtendMenu``/``AddMenuEntryObject`` and
``UnregisterOwnerByName``, all ``UFUNCTION``) and polls its socket from ``unreal.register_slate_post_tick_callback``.

* No binaries, so one plugin serves every engine version that has the APIs it calls.
* The person still enables a beta engine plugin, and still waits for the throttled tick.
* Python can't build a transient preview (no ``RF_Transient`` spawn, no in-memory glTF), so phase 1 is the durable
  layer only. It tells Angelo so (``"capabilities": ["durable"]``) and Angelo sends no live scenes.

(C) A C++ editor plugin (recommended phase 2, the live layer)
-------------------------------------------------------------

The same protocol in C++ (a socket on a worker thread, frames handed to the game thread), plus:

* **Loading the .glb from Angelo.** Three options:

  1. *Interchange into a transient package*: Interchange imports from a file path only
     (``UInterchangeManager::CreateSourceData(FileName)``, ``Runtime/Interchange/Engine/Public/InterchangeManager.h``)
     and builds real assets through the full pipeline (mesh build, materials, Nanite). Whether it can target a
     never-saved package is *unverified*; it would also be the slowest.
  2. *glTFRuntime* (third-party, MIT, https://github.com/rdeioris/glTFRuntime): loads glTF from memory at runtime.
     Another dependency to build per engine version; API *unverified* here.
  3. **Own minimal builder (recommended):** parse with the engine's ``GLTFCore`` module
     (``Plugins/Interchange/Runtime/Source/Parsers/GLTFCore/Public/GLTFReader.h``, ``GLTF::FFileReader::ReadFile``,
     a Runtime module of the always-on Interchange plugin; it reads files, so the frame is written to a temporary
     file first) and fill a ``UDynamicMesh`` per glTF mesh (``Runtime/GeometryFramework``,
     ``UDynamicMeshComponent``), which renders with no asset build. Convert axes and units as Interchange does, so the
     preview and the re-import agree: glTF (x, y, z) becomes Unreal (x, z, y) and metres become centimetres
     (``GLTFCore/Private/GLTF/ConversionUtilities.h`` ``ConvertVec3``; ``InterchangeGltfPrivate.h``
     ``GltfUnitConversionMultiplier = 100``). Materials as ``UMaterialInstanceDynamic`` of a base PBR material the
     plugin ships; textures from the embedded images.

* **A preview that is never saved.** Spawn the preview actor with ``FActorSpawnParameters`` ``ObjectFlags =
  RF_Transient``, ``bTemporaryEditorActor`` and ``bHideFromSceneOutliner`` (``Runtime/Engine/Classes/Engine/World.h``).
  A transient actor gets no external package even in a World Partition level (``LevelActor.cpp``, ``SpawnActor``) and
  ``MarkPackageDirty`` returns early for anything transient along its outer chain
  (``CoreUObject/Private/UObject/UObjectBaseUtility.cpp``), so spawning and destroying it never dirties the level.
  Never inside a transaction, so undo never sees it.
* **Hiding the originals without dirtying.** ``AActor::SetIsTemporarilyHiddenInEditor`` sets the ``Transient``
  ``bHiddenEdTemporary`` flag, documented as "show/hide ... w/o dirtying the actor" (``GameFramework/Actor.h``,
  ``Private/ActorEditor.cpp``). Instances of the asset are the actors whose ``UStaticMeshComponent`` /
  ``USkeletalMeshComponent`` use it; one preview per instance, attached at its transform. Per-component instances
  (instanced static meshes, Blueprints with several meshes) need component-level hiding: *open question*.
* **Smooth while Angelo is in front.** ``UEditorEngine::ShouldDisableCPUThrottlingDelegates`` (public, in
  ``Editor/UnrealEd/Classes/Editor/EditorEngine.h``) lets the plugin stop the 3 Hz background throttle while a link
  session is live, and only then.
* **Distribution.** A binary plugin is built per engine minor version and platform. Fab distributes code plugins
  built by Epic for the versions the seller lists (process *unverified* here); the source can also live on GitHub for
  people who build it themselves. No Epic sign-in or download was needed for anything in this document.

The live layer
==============

Built as option C.3 above, with one change: the link client stays the Python plugin's (tested, one protocol
implementation per engine), and the C++ is only what Python can't do.

* **A second plugin, ``AngeloLinkLive``** (one ``Editor`` module, ``PostEngineInit``), not a module of
  ``AngeloLink``: Unreal refuses to start a project whose enabled plugin lists a module with no binary for that
  engine, so the content plugin must never list one, or a person without a build for their engine version would
  lose the durable layer too. It needs ``AngeloLink`` enabled (it has no client of its own) and Interchange (for
  ``GLTFCore``). ``EnabledByDefault`` is false.
* **What Python sees**: ``unreal.AngeloLinkLiveLibrary`` (``LiveVersion``, ``ShowPreview``, ``ClearPreview``,
  ``PreviewCount``, ``PreviewBounds``, ``PreviewTriangles``, ``SetSessionLive``, ``IsSessionLive``). The Python
  plugin's ``live`` module uses it when it is loaded and at least its version; then the handshake says
  ``["durable", "live"]`` and Angelo sends live scenes, else ``["durable"]``.
* **A live scene** is written to an owner-only temporary folder of the editor's (``GLTFCore`` reads files) and
  replaced in one step, and removed when the link stops or the editor quits; ``ShowPreview`` reads it with
  ``GLTF::FFileReader`` (no images), bakes every mesh node of every scene into one ``FDynamicMesh3`` in the asset's
  space as Interchange's combined static mesh import makes it: node transforms composed (a skinned mesh's ignored, as
  glTF says), glTF's axes as ``GLTFCore`` converts them, metres to centimetres, morph targets at their default
  weights, winding reversed under a mirroring transform, missing normals flat, both faces of a two-sided sheet, and
  no ``UCX_``/``UBX_``/``USP_``/``UCP_`` collision meshes. One material slot per glTF material, coloured by its base
  colour factor on ``/Engine/BasicShapes/BasicShapeMaterial``. Angelo hears ``status`` ``previewed`` (``version``,
  ``instances``) or ``preview_failed``; a frame that can't be read keeps the earlier preview.
* **Instances** are the registered static and skeletal mesh components that use the asset in the editor world, on
  actors that aren't transient. Instanced static mesh components (foliage, packed level actors) are left as they are:
  they draw many instances at their own transforms. Each instance gets an ``AAngeloLinkPreviewActor`` (a
  ``UDynamicMeshComponent``, editor-only, never selectable, so a click, Delete or Duplicate can't reach it) spawned in
  the persistent level with ``RF_Transient``, ``bTemporaryEditorActor`` and ``bHideFromSceneOutliner``, no collision,
  at the component's transform; the component is hidden with ``USceneComponent::SetIsTemporarilyHiddenInEditor``
  (the transient ``bHiddenEdTemporary``; per component, so an actor's other components stay). Spawning, updating and
  removing previews run with no undo buffer (``GUndo`` cleared), so a transaction in progress (a gizmo drag) records
  none of it. A newer scene updates the previews in place. A core ticker moves previews with their instances, hides
  them while the person hides the instance, and ends a preview whose instance is deleted, unloaded or given another
  mesh; opening another level applies the newest scene there; the previews are dropped at ``OnEditorPreExit``, while
  the world still exists. Nothing is saved with the level or the asset.
* **Ending**: the re-import of Angelo's save ends the preview once the package is saved, unless the preview shows a
  live scene newer than the one the save holds (``saved`` carries ``scene_version``: Angelo writes the asset in the
  background, and edits made meanwhile arrive first); ``ended``, Stop and a broken link end it and the unthrottled
  tick.
* **Smooth while Angelo is in front**: ``SetSessionLive`` answers ``ShouldDisableCPUThrottlingDelegates`` true from
  ``opened`` until the session ends.
* **Not yet**: textures and vertex colours (base colour factors only), skinning (a skinned mesh shows its bind pose),
  instanced static meshes (left showing the asset), an instance placed while a preview shows (it joins at the next
  scene), Play In Editor (the preview actor is editor-only; *unverified* in PIE). Every scene copies the mesh into
  each instance's preview: a level with many instances of a very large mesh pays that per update.
* **Distribution** (1.0.7): source only. People build it for their engine version (``RunUAT BuildPlugin``; the
  plugin's ``README.md`` has the steps) and enable it beside Angelo Link; Angelo doesn't install it
  (``install_plugin`` copies ``AngeloLink`` only) and no binaries are kept in the repository. A locally built binary
  embeds the builder's paths, so released binaries need path-stripped builds.

Recommendation
==============

* **Phase 0 (test tooling, not shipped):** remote execution for people who won't install a plugin: "Update in Unreal" after a save
  re-imports the asset, and "Edit the selected asset" pulls the Content Browser or level selection
  (``SCRIPTS["selection"]``). Opt-in with the security warning above.
* **Phase 1 (ships in 1.0.7):** the Python plugin, save-time sync: Edit in Angelo from the Content Browser and the level,
  Angelo asks the person, Angelo saves, Unreal re-imports and saves the asset's package. Install like Godot's:
  Angelo copies ``AngeloLink`` into ``<project>/Plugins/AngeloLink``; the person enables it in Edit > Plugins
  (Angelo never edits the ``.uproject``, which the open editor owns).
* **Phase 2 (built, "The live layer"):** the C++ live layer as its own plugin, ``AngeloLinkLive``, so the content
  plugin never lists a module and the Python stays the fallback where no binary exists for the engine version.
  Editor toasts are still to do.

The durable layer
=================

* **Identity.** An asset is its package path (``/Game/Props/SM_Crate``) plus the single source file its import data
  names: ``UAssetImportData::ExtractFilenames``/``GetFirstFilename`` (``Runtime/Engine/Classes/EditorFramework/
  AssetImportData.h``, script-callable); ``AssetImportData`` is an ``EditAnywhere`` property of ``UStaticMesh`` and
  ``USkeletalMesh``, so Python reads it with ``get_editor_property``.
* **Format.** Angelo writes the source format the asset was imported from: ``.glb`` (the shared glTF profile,
  ``GLTF_DURABLE``) and, once its profile is recorded (see "Scale and axes"), ``.fbx``; until then Angelo's adapter
  and the plugin refuse an asset imported from .fbx with the reason. Interchange imports both by default
  (``Interchange.FeatureFlags.Import.FBX`` defaults to true in ``InterchangeFbxTranslator.cpp``; glTF is an
  Interchange translator, ``InterchangeGltfTranslator.cpp``). For new assets .glb is the better default (one exporter
  shared with Godot, embedded textures, PBR materials, skins and animation); FBX stays for assets that already use
  it. ``.gltf`` with side files, ``.obj`` and multi-source assets are refused with the reason (``assets.REFUSED``).
* **Working file.** As for every engine's (:doc:`engine_link`): ``<project>/.angelo/<source path>.blend``, named by
  the source file's path in the project (``.angelo/SourceArt/crate.glb.blend``) rather than the package path, so two
  assets made from one file share it and its digest stamp; outside ``Content``.
* **Re-import keeps the asset's own settings.** Interchange stores the pipelines an asset was imported with in its
  ``UInterchangeAssetImportData`` (``GetPipelines``, ``GetTranslatorSettings``) and
  ``UInterchangeManager::ReimportAsset`` replays them (script-callable; the drafts set ``is_automated``). Legacy
  (pre-Interchange) assets re-import through an ``AssetImportTask`` over the existing asset with
  ``replace_existing_settings`` off (``Editor/UnrealEd/Public/AssetImportTask.h``). So, unlike Godot, Angelo needn't
  mirror import settings for the durable layer; it only needs an export profile per format that Unreal reads the
  way the person expects (below). The package is then saved (``UEditorAssetSubsystem::SaveLoadedAsset``, which also
  checks the file out), because the person's save in Angelo is the durable step.
* **Auto-reimport.** Unreal watches ``/Game/`` for source files by default and prompts before importing
  (``SettingsClasses.cpp``: ``bMonitorContentDirectories``, ``bPromptBeforeAutoImporting``, a 3 s threshold). It
  skips a file whose hash matches the asset's recorded source hash (``AutoReimport/ContentDirectoryMonitor.cpp``), so
  the plugin's own re-import, which records the new hash first, should leave nothing to prompt about (*unverified*
  at runtime). Sources outside ``Content`` avoid the question.
* **Static and skeletal.** Both are handled by the same re-import. Skeletal meshes keep their skeleton; a change to
  the bone hierarchy can fail or prompt (``bUpdateSkeletonReferencePose`` and the skeleton compare in
  ``InterchangeGenericMeshPipeline.h`` / ``InterchangeGenericAssetsPipeline.h``): phase 1 limits Angelo to edits that
  keep the hierarchy, and says so when the re-import fails.
* **Scale and axes.** Unreal is centimetres, Z up, left-handed. glTF converts as above. For FBX, the export profile
  (unit scale, forward and up axes, leaf bones) is to be recorded the way Godot's table was: export a calibration
  asset, import it with default settings, compare bounds and orientation, for each option the person can change
  (*not yet measured*).

Edit in Angelo
==============

* Content Browser: ``ContentBrowser.AssetContextMenu``; the entry reads ``UContentBrowserAssetContextMenuContext``
  ``SelectedAssets`` through ``context.find_by_class`` (``ToolMenusBlueprintLibrary.h``, ``FindByClass``).
* Level viewport and Outliner: ``LevelEditor.ActorContextMenu`` (``LevelEditorContextMenu.cpp``) and the selected
  actors (``UEditorActorSubsystem::GetSelectedLevelActors``). An actor maps to its asset through its mesh components
  (``UStaticMeshComponent.static_mesh``, ``USkeletalMeshComponent::GetSkeletalMeshAsset``); an actor showing several
  mesh assets asks the person to pick one in the Content Browser.
* Whether the script entry's section name ``AngeloLink`` creates a new section in both menus is *unverified*.

Versions and licence
====================

* **Floor: Unreal 5.4** (proposed; only 5.8.2 was read). The drafts use Python 3.11 syntax only (5.8 bundles 3.11),
  Interchange re-import, ``SelectedAssets`` on the Content Browser context (present before its 5.5 deprecation note
  of ``GetSelectedObjects``), ``GetSkeletalMeshAsset`` and the editor subsystems. Each must be checked against the
  5.4 and 5.5 headers before the floor is promised.
* **Licence.** The plugin is MIT (approved) and contains no Engine Code, no Blender code and no Angelo GPL code;
  Angelo's own side stays GPL. The Unreal EULA (https://www.unrealengine.com/eula/unreal; the page refused automated
  fetches, so read from search excerpts) restricts redistributing Engine Code and Engine Tools, which our own plugin
  isn't; publishing it on Fab and on GitHub needs a review of the current EULA and Fab terms before release
  (*unverified*).

Angelo's side
=============

Made (the engine link's adapter split, :doc:`engine_link`): one listener for every engine; the handshake keeps
``engine``, the engine's version (``unreal``) and ``capabilities``; live scenes go only to editors that say
``live``, saves to every editor of the project; requests are routed to the engine's adapter. Unreal's is
``unreal_link_assets``: the folder holds a ``*.uproject``; ``asset`` is a package path (refused with code
``package`` otherwise); ``source`` must be a .glb inside the project; ``import_params`` is empty (the re-import
replays the asset's own settings); saves write the .glb with ``GLTF_DURABLE``; its editors show no live scenes by
default (``CAPABILITIES = ("durable",)``); the plugin installs into ``Plugins/AngeloLink``.

Open:

* **The FBX profile** (``.fbx`` kind), recorded against Unreal's importer as "Scale and axes" describes.
* **The + menu**: "Add the plugin to an Unreal project…" once the plugin has run in an editor.
* **Remote execution, if phase 0 ships:** a separate opt-in with the security warning, run from a worker thread.

Validation
==========

Ran (2026-10-29, Mac 1, no Unreal process started):

* ``test_native_unreal_link`` with Angelo's bundled Python 3.13 and Unreal 5.8's Python 3.11: 13 tests pass. The
  remote-execution client against a stand-in editor that follows ``PythonScriptRemoteExecution.cpp`` over real
  loopback multicast and TCP (discovery, the asked editor only, a result split across reads, oversized commands
  refused, results and failures); the Unreal-side scripts parse as Python 3.11 and carry parameters as data; the
  plugin's client against Angelo's real link server with the engine-neutral names (proofs, ``open``, a 100 kB scene
  frame, a wrong token refused); descriptor, licence and refusal rules. ``test_native_engine_link_protocol`` still
  passes.
* After the adapter split (2026-10-29): ``test_native_unreal_link`` (16 tests) also checks what Angelo's server keeps
  from the plugin's handshake (engine, version, ``durable``) and that Angelo's adapter resolves the plugin's own
  ``open`` request, refuses .fbx, a source outside the project, a missing source, a bad package path and a folder
  without a ``.uproject``, and admits the plugin's kinds; the engine link's adapter-contract test covers it too.
* Epic's own ``remote_execution.py`` (from the install, under Unreal's Python) against the same stand-in: no editor
  found with the default bind ``127.0.0.1``, found and a command run with ``0.0.0.0``, which shows the stand-in speaks
  what Epic's client expects and reproduces the macOS bind problem.

In a headless Unreal Editor (2026-10-29, UE 5.8.2 on macOS; ``unreal_link_e2e/editor_run.py``): a throwaway
content-only project with the plugin installed as Angelo installs it, a background Angelo in its durable mode, and
``unreal_side.py`` as the project's start-up script in ``UnrealEditor -nullrhi -unattended -nocrashreports`` with a
private HOME and derived-data cache in a short work folder (Unreal refuses a cache path over 119 characters). The
plugin loads with the Python plugin, its menus register, ``SourceArt/crate.glb`` imports through Interchange, Edit in
Angelo links it, Angelo's save is written back, the plugin re-imports it (the added cube at z 175-225 cm: glTF's
axes and metres converted as expected), saves the package once the mesh is built, and Stop closes the link on both
sides. Passed: 9 checks, 40 s.

Found by it and fixed: the plugin saved the package before the re-imported mesh was built (the build marks it dirty
again), so it now saves from its tick once Interchange is idle; Angelo read a close from an editor that quit at once
as another engine's (the server now keeps a departed editor's info until its last requests are handled); a close is
matched by the asset's name, without resolving its file; ``-ExecutePythonScript`` quits the editor when the script
returns, so the driver is a start-up script.

Found later (the C++ live-layer session's live run): a save that added an object re-imported only that object, because
the asset's stored Interchange pipeline does not combine static meshes (UE's default, so each glTF mesh is its own
static mesh). Before each re-import the plugin now sets the asset's own mesh pipeline to Combine All and stores it
(``set_pipelines``), so a later manual re-import in Unreal keeps every mesh too; a re-import whose pipelines can't
take the setting is refused rather than dropping meshes. A source imported as several assets is refused before
Angelo opens it (the plugin counts the mesh assets naming that file: the asset registry's import tag and the asset's
folder; a stored node container holds only its own asset's node). The test compares the re-imported mesh with what
Angelo's save exported (height range, extents, triangles), and imports a two-object file to check the refusal.

The live layer (2026-10-30, UE 5.8.2 on macOS 26, Xcode 26.6): ``RunUAT BuildPlugin -TargetPlatforms=Mac`` builds
``AngeloLinkLive`` with no warnings. ``editor_run.py --live-plugin <built plugin>`` adds it to the throwaway project,
places the mesh twice in a saved level (at the origin, and moved, turned and non-uniformly scaled) and runs Angelo
live (it saves only after the editor reports ``previewed``): the first live scene shows on both instances with
their world bounds; Angelo's added cube shows in the open level while the asset, its source file, the level and the
asset's package stay unchanged; the previews' world bounds equal the instances' after the re-import of Angelo's save;
the previews end once that is saved; Stop clears the session flag (headless Unreal never throttles, so the throttle
itself is unverified). 21 checks, 22 s.

Not run yet: an .fbx source (refused until its profile is recorded), a skeletal mesh, a legacy (non-Interchange)
asset, an interactive editor (the menus' clicks, the preview drawn on screen: ``-nullrhi`` draws nothing),
textured materials, remote execution on macOS, Unreal 5.4-5.7, and Windows and Linux builds of the live layer.
