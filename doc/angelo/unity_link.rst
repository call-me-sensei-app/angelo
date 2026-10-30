.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

################
Unity live link
################

The Unity counterpart of the :doc:`Godot live link <godot_link>`: a person picks a model in the Unity Editor,
edits it in Angelo, sees every change on that model's instances in the open scenes as they work, and saving in
Angelo updates the model file, which Unity re-imports. This document is the design and the contract of the Unity
package Angelo ships, ``angelo_agent/unity_package/com.hyperbond.angelo-link`` ("Angelo Link", MIT), with Angelo's
Unity adapter (``unity_link_assets``; the adapter contract is :doc:`engine_link`).

**Status:** the package runs in a real Unity 6000.5.6f1 Editor (batchmode) against a real Angelo: Edit in Angelo,
Angelo's .fbx save, the re-import on every instance and Stop (see "Validation"). Not yet run there: the live preview
(it needs glTFast, not installed), the menus and overlay drawn in a windowed editor, and Unity 6000.0-6000.3.

.. contents::
   :local:

Design
======

The Godot link's two layers carry over unchanged, and so does the whole of Angelo's side except what is per engine:

* **Live layer.** Angelo exports the linked scene to a .glb after each change and sends it over loopback. The package
  loads it from memory with Unity's glTFast and shows it in place of every instance of the model in the open scenes.
  No scene changes (see "The live preview").
* **Durable layer.** Saving in Angelo writes the model file; the package calls ``AssetDatabase.ImportAsset`` on it,
  and the instances show the re-imported model, which ends their preview.
* **Asset level, one way.** A GameObject chosen in Unity resolves to the model file its nearest prefab instance was
  made from; every instance of that file updates; placement stays Unity's. Unity sends only requests and status.

What is shared and what is per engine:

* **Transport, authentication, the record**: shared (``engine_link_protocol``, ``engine_link_server``); per engine,
  only the plugin's client (``LinkClient.cs`` here).
* **Requests and the person's card**: shared (``engine_link``, which routes each request to the engine the editor
  named when it authenticated); per engine, the adapter that resolves the asset path.
* **Following the scene**: shared (``scene_changes`` and the stillness rule); nothing per engine.
* **Asset rules**: per engine (project marker, kinds, path syntax); the working folder is shared.
* **Export profile**: the glTF exporter is shared; the options (live and durable) and the write-back kind are per
  engine.
* **Re-import**: Angelo's ``saved`` is shared; Godot calls ``reimport_files``, Unity ``AssetDatabase.ImportAsset``.

The person
==========

* **Installing**: the package is a Unity package (UPM). Today by Git URL in Package Manager
  (``https://…/angelo-studio.git?path=/scripts/addons_core/angelo_agent/unity_package/com.hyperbond.angelo-link#<tag>``;
  ``path`` must precede the revision, and Git 2.14+ must be on PATH [UPMGIT]_), or as a tarball. Proposed for
  Angelo (as the Godot plugin's "Add the plugin to a Godot project…"): copy the package into
  ``<project>/Packages/com.hyperbond.angelo-link``, which makes it an *embedded* package that Unity loads without a
  manifest entry [EMBED]_, so Angelo never edits ``Packages/manifest.json``.
* **The live preview needs glTFast** (``com.unity.cloud.gltfast``), which is *not* declared as a dependency: declaring
  it would make glTFast every project's default .glb/.gltf importer, which conflicts with UnityGLTF and UniGLTF
  ("Multiple scripted importers") [GLTFEDIT]_. Without it the link opens, saves and re-imports, and the overlay says
  the preview needs it. A later version can offer to add it on a click (``PackageManager.Client.Add``) [XML]_.
* **In Unity**: *Edit in Angelo* in the Project window's context menu on a .blend or .glb (``MenuItem("Assets/…")``),
  the Hierarchy's context menu on anything made from one (``MenuItem("GameObject/…", false, 10)``; the documented
  priority that puts a GameObject item in the Hierarchy menus [MENU]_), Tools > Angelo Link, and the Scene view's
  **Angelo Link** overlay (a toolbar dropdown: Edit, Stop Live Link and the link's state; ``ToolbarOverlay`` with an
  ``EditorToolbarDropdown`` [XML]_). Notices go to the Console and the Scene view's notification.
* **Answering in Angelo**: the same card as Godot's, which names the engine ("Unity wants to edit crate.fbx").
  Angelo has no menu entry yet that copies the package into a project (Godot's has one); it gets one once the package
  has run in a Unity Editor (``unity_link_assets.PLUGIN_FOLDER`` is the target, an embedded package).

Assets
======

* **The project and the wire path**: a project is a folder holding ``Assets/`` and
  ``ProjectSettings/ProjectVersion.txt``; an asset is named by its project path as Unity writes it
  (``Assets/Props/crate.glb``) and must be under ``Assets/`` (``unity_link_assets.resolve``).
* **Kinds**: ``.blend`` (Angelo opens and saves it; Unity imports .blend by running a local Blender in the
  background to convert it to FBX, so every teammate needs Blender [FORMATS]_) and ``.glb`` (Angelo edits a working
  .blend and writes the .glb back; a .glb is a model in Unity only with a glTF importer such as glTFast's
  ``ScriptedImporter`` [GLTFEDIT]_). Unity reads .fbx, .dae, .dxf and .obj natively and recommends .fbx [FORMATS]_.
* **.fbx**, the format most Unity projects already hold: Angelo edits a working .blend (imported with Blender's FBX
  importer's defaults) and writes the .fbx back with the options Unity itself passes to Blender for a .blend
  (``UNITY_FBX``, recorded from ``Contents/Resources/AssetImporting/Unity-BlenderToFBX.py``, identical in 6000.5.5f1
  and 6000.5.6f1: ``use_selection=False``, object types armature, camera, light, mesh, other and empty,
  ``use_mesh_modifiers=True``, ``mesh_smooth_type='OFF'``, ``use_custom_props=True``, no NLA strips or all actions,
  ``apply_scale_options='FBX_SCALE_ALL'``), so a .fbx written by Angelo imports like a .blend would. A .glb is
  written with the shared glTF profile (``GLTF_DURABLE``), the live preview too.
* **Which to write back**, recommended per project: .fbx when the model is .fbx (no extra software for anyone); .glb
  when the project uses glTFast (the preview and the import are then the same code); .blend only when the team
  already imports .blend files (each machine needs Blender — Angelo can be that Blender, unverified: how Unity 6
  finds Blender on macOS and Windows was not established).
* **The working folder** stays ``<project>/.angelo/<path>.blend``: Unity imports only ``Assets/`` and ``Packages/``,
  so it is never imported, and the ``.gdignore`` there is harmless. Verified: the end-to-end run writes
  ``.angelo/Assets/crate.glb.blend`` and writes the edit back into ``Assets/crate.glb``.
* **Import settings** live in the model's ``.meta`` (YAML). Rather than Angelo parsing it, the package reads them
  through the importer API (``AssetImporter.GetAtPath``; ``ModelImporter.globalScale``, ``useFileScale``,
  ``bakeAxisConversion``, ``importLights``, ``importCameras``, ``materialImportMode`` [XML]_) and applies what
  the preview needs itself; the prototype applies Scale Factor. The ``.meta`` is untouched by a write-back (same
  path, same GUID), so Unity re-applies the person's settings on re-import; Angelo reads none of them
  (``import_params`` is empty). Sending them in ``open`` is open item 1 below.

Finding instances
-----------------

A model file Unity imports is a Model Prefab, so ``PrefabUtility.GetNearestPrefabInstanceRoot`` and
``GetPrefabAssetPathOfNearestInstanceRoot`` [XML]_ resolve a scene object to it, looking outward through nested
prefabs (``AssetRules.AssetOf``). ``AssetRules.FindInstances`` walks every loaded scene and the open Prefab Mode stage
and collects instance roots of the model (nested instances of the same model are part of the outer one).
``PrefabUtility.FindAllInstancesOfPrefab`` [XML]_ is the alternative per scene; the walk was chosen because it also
finds instances nested in other prefabs. Unverified: that glTFast-imported .glb instances report their path the same
way (they are ScriptedImporter outputs).

The live preview
================

* **Objects**: one root GameObject per instance, made with ``EditorUtility.CreateGameObjectWithHideFlags`` as
  ``HideFlags.HideAndDontSave`` ("not shown in the Hierarchy, not saved to the Scene" [XML]_), placed at the
  instance's transform (times the importer's Scale Factor) and moved with it every editor tick. It is *not*
  parented under the instance: a child would be an added-object override on the prefab instance. glTFast builds
  the .glb under it once per instance from one load; every created object and component is then marked
  ``HideAndDontSave``. Prefab Mode and multi-scene editing move the root into the instance's scene.
* **Hiding originals**: ``Renderer.forceRenderingOff`` [XML]_ on the instance's renderers, a runtime switch, not a
  serialized property. Lights and cameras of the original can't be switched off without serialized changes, so the
  preview takes meshes only (``InstantiationSettings.Mask = ComponentType.Mesh`` [GLTFAPI]_); light changes arrive
  with the re-import.
* **Materials**: glTFast picks shaders for Built-in, URP or HDRP itself; variants are built on demand in the Editor
  [GLTFSETUP]_. Its default-Lit-material import is not supported [GLTFINDEX]_, so the preview's materials are glTFast's,
  not the project's — the same as the re-import for a .glb, an approximation for a .fbx or .blend.
* **Swap without flashing**: a new export builds new previews first, then destroys the old ones and disposes the old
  ``GltfImport`` (which destroys its meshes, materials and textures [GLTFRUN]_); a newer export supersedes an
  unfinished build.
* **Lifecycle**: Play Mode suspends the previews (Play Mode shows the project's asset); a domain reload suspends them
  and closes the socket, and the next domain rejoins from ``SessionState`` [XML]_ (Angelo answers ``opened`` and
  re-exports); leftover preview objects are destroyed at load; a re-import of the model restores every renderer of
  its instances and ends the preview; Stop Live Link, Angelo's ``ended`` and quitting restore everything.
* **Open questions** (need a running editor): whether creating and moving ``HideAndDontSave`` objects marks a scene
  dirty (the test asserts it doesn't; the fallback is drawing with ``Graphics.RenderMesh`` from the Scene view's
  callbacks, which creates no GameObjects); whether Scene-view picking can select a preview; that glTFast's async
  loading completes in Edit Mode from ``EditorApplication.update`` (its ScriptedImporter loads in the Editor, so the
  loader runs there, but the async continuation path is unverified); axis and scale parity between the glTFast
  preview and a ModelImporter re-import (both should map Blender's -Y forward, Z up the same way; to be measured with
  an asymmetric model by comparing renderer bounds before and after the re-import).

Connection
==========

``LinkClient`` is engine-free C# (``System.Net.Sockets``, ``HMACSHA256``; no UnityEngine), so the protocol harness
runs it outside Unity. ``EditorApplication.update`` ("called periodically at the editor's tick rate" [XML]_) calls
``Poll``: the connect is ``Socket.ConnectAsync`` checked for completion, then the socket is non-blocking; reads take
what ``Available`` reports, writes keep what ``Send`` didn't take. Frames, proofs, the 10 s ping and the record path
are exactly Angelo's (``engine_link_protocol``): protocol ``angelo-link/1``, record ``angelo-link.json`` in Angelo's
per-user data folder or ``ANGELO_LINK_RECORD``; the auth frame's ``client`` carries ``engine: "unity"``, ``unity``
(the editor version), ``project``, ``project_name`` and ``plugin``. Nothing Angelo sends is read before Angelo's own
proof matches (constant-time compare). The package sends no ``capabilities``, so Angelo's Unity adapter's
(``durable``, ``live``) apply; a package that finds no glTFast still receives live scenes and says the preview needs
it.

Angelo's side
=============

Made (the engine link's adapter split, :doc:`engine_link`): requests are routed by ``client.engine``; Unity's adapter
(``unity_link_assets``) checks the project, admits ``Assets/`` paths of the three kinds and writes .glb and .fbx
back; the handshake keeps the editor version and capabilities; ``ended`` reason ``engine`` and the card and status
line name the engine.

Open:

1. **Import settings in ``open``.** An optional ``import`` object from the package (Scale Factor, Convert Units,
   Bake Axis Conversion, lights, cameras) that the adapter's ``import_params`` would turn into export options.
2. **``reimported`` as a defined status.** The package reports ``status``/``reimported`` after the re-import; Angelo
   could show "Updated in Unity".
3. **Kinds in ``hello``**, so the package greys out Edit in Angelo instead of sending a request that will be refused.
4. **The + menu**: turning the link on and adding the package to a project from Angelo, named for Unity.

Unity version and distribution
==============================

* **Floor: Unity 6 LTS (6000.0)** (``"unity": "6000.0"`` in ``package.json``), the floor glTFast 6.x also declares
  [GLTFPKG]_. Compiled with zero warnings against the UnityEngine/UnityEditor assemblies of 6000.2.6f2, 6000.3.13f1,
  6000.5.5f1 and 6000.5.6f1; 6000.0 itself is not installed here and is unverified.
* **Distribution**: Git URL now; tarball for offline installs; the Asset Store later. The package is MIT (SPDX
  headers, ``LICENSE.md``) and bundles nothing of glTFast (Apache-2.0 [GLTFREPO]_), which the person installs from
  Unity's registry.
* **Size**: about 1,550 lines of C# in eight files; the editor assembly is editor-only
  (``includePlatforms: Editor``) and the glTFast loader is a second assembly that compiles only when glTFast is
  present (``versionDefines`` defines ``ANGELO_GLTFAST``, ``defineConstraints`` requires it; unverified in Unity).
* **Meta files**: a package installed by URL or tarball is immutable, so it ships a ``.meta`` per file and folder
  (deterministic GUIDs); Unity skips files without one in an immutable package (from experience, not verified here).

Limits
======

* The package's text is English, like the Godot plugin's.
* One asset at a time; the preview is visual (meshes and materials; no lights, cameras, colliders or scripts until
  the re-import).
* macOS brings Angelo to the front with ``open -a``; on Windows Angelo flashes its own taskbar button.

Validation
==========

``tests/python/angelo_agent/unity_link_e2e/run.py --unity <Unity.app> --out <folder> [--binary <Angelo>]`` (standard
library only; runs nothing of Unity but its compiler and .NET runtime, so no licence):

* **Compile**: the Editor assembly against that Unity's own assemblies with its bundled Roslyn. Passed for 6000.2.6f2,
  6000.3.13f1, 6000.5.5f1 and 6000.5.6f1 (0 warnings). The glTFast assembly was type-checked against stubs carrying
  glTFast 6.14's documented signatures [GLTFAPI]_, not against glTFast itself (not installed; no downloads).
* **Protocol**: the harness (``unity_link_e2e/Harness.cs``) computes proofs equal to Angelo's
  ``client_proof``/``server_proof`` for the same token and nonces, refuses a server with a wrong proof, and joins a
  frame split across reads.
* **End to end** with a background Angelo (``godot_link_e2e/angelo_side.py``) and a throwaway Unity project
  (``Assets/``, ``ProjectSettings/ProjectVersion.txt``) holding ``Assets/crate.glb`` and ``Assets/crate.fbx``: for
  each, as ``engine: "unity"``, pending → opened → two live .glb scenes (before and after Angelo's edit) → saved →
  (``reimported``, ``close``) → ended → closed; the model on disk then holds the edit (written back by glTF or by
  FBX with ``UNITY_FBX``) and the working .blend is ``.angelo/Assets/crate.<kind>.blend``. Passed 2026-10-29 on
  Mac 1 with Unity 6000.5.6f1's compiler and an Angelo 1.0.6 build carrying this branch's add-on.
* **FBX round trip**: the written .fbx, imported back with Blender's FBX defaults (what Angelo's next import of it
  does), has the working .blend's world-space mesh bounds (two meshes, -1..1 and up to 2.25 on Z; same to 1e-3).
  This checks Angelo's side only; how Unity's ModelImporter places it is the editor test's axis/scale check.
* Host: ``test_native_unity_link_assets`` (resolution, refusals, the FBX profile, the package's kinds and wire
  path, installing as an embedded package) and the engine link's adapter-contract test.
* **In a Unity Editor** (``unity_link_e2e/editor_run.py``; ``editor/BatchTest.cs`` runs in ``Unity -batchmode
  -nographics`` with the person's own HOME, so the licence already on the machine applies; nothing signs in). A
  throwaway project (the package embedded, built-in modules only, an existing ``ProjectSettings`` so Unity doesn't
  add its default packages) with two instances of ``Assets/crate.fbx``: Edit in Angelo links it, live scenes arrive
  and the package says the preview needs glTFast, Angelo's edit (a second object) is written back as .fbx, the
  package re-imports it, every instance shows the edit in place, no preview object enters the scene, the scene file
  is unchanged, and Stop restores every renderer. Passed 2026-10-29 with 6000.5.6f1 (13 checks, 27 s). Unity writes
  its usual caches and logs under ``~/Library`` (Caches/com.unity3d.UnityEditor, Logs/Unity, the editor's
  preferences); the report lists them.
* **Found by it and fixed**: a model with one object imports with that object as the prefab root, so each
  instance's root override holds the object's own transform; when an edit adds a second object, the new empty root
  took that override and the whole model turned (the added cube landed at y=0). The package now takes each
  instance's placement before Unity rebuilds it and sets the rebuilt root to placement x new root, one Undo step;
  the scene then waits for the person's save (the package says so).
* **Not yet run**: the live preview with glTFast (installing it is a download, the maintainer's call), a .glb model
  (it needs glTFast too), the menus and Scene view overlay in a windowed editor, and the axis/scale parity check.

References
==========

.. [XML] Unity 6000.3.13f1's own API documentation, ``Contents/Resources/Scripting/Managed/UnityEngine/
   UnityEditor.CoreModule.xml`` and ``UnityEngine.CoreModule.xml`` (HideFlags, Renderer.forceRenderingOff,
   EditorUtility.CreateGameObjectWithHideFlags, PrefabUtility, ModelImporter, EditorApplication.update,
   SessionState, ToolbarOverlay, PackageManager.Client.Add), and compiling against those assemblies.
.. [MENU] Unity Scripting API, MenuItem: https://docs.unity3d.com/2022.3/Documentation/ScriptReference/MenuItem.html
.. [FORMATS] Unity 6 manual, Model file formats:
   https://docs.unity.com/en-us/engine/6000.0/manual/assets-and-media/asset-types/models/creating-dccassets/3d-formats
.. [UPMGIT] Unity 6 manual, Install a package from a Git URL:
   https://docs.unity.com/en-us/engine/6000.0/manual/packages-list/upm-git
.. [EMBED] Unity 6 manual, Embedded dependencies: https://docs.unity3d.com/6/Documentation/Manual/upm-embed.html
.. [GLTFRUN] glTFast 6.14, Runtime import:
   https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.14/manual/ImportRuntime.html
.. [GLTFAPI] glTFast 6.14 API: ``GltfImport``, ``ImportSettings``, ``InstantiationSettings``,
   ``GameObjectInstantiator``, ``ComponentType``, ``UninterruptedDeferAgent`` under
   https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.14/api/
.. [GLTFSETUP] glTFast 6.14, Project setup (materials and shader variants):
   https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.14/manual/ProjectSetup.html
.. [GLTFINDEX] glTFast 6.14 manual: https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.14/manual/index.html
.. [GLTFEDIT] glTFast, Editor import (default importer, conflicts):
   https://docs.unity3d.com/Packages/com.unity.cloud.gltfast@6.2/manual/ImportEditor.html
.. [GLTFPKG] glTFast ``package.json`` (``"unity": "6000.0"``):
   https://github.com/Unity-Technologies/com.unity.cloud.gltfast/blob/main/Packages/com.unity.cloud.gltfast/package.json
.. [GLTFREPO] glTFast repository and licence: https://github.com/Unity-Technologies/com.unity.cloud.gltfast
