.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Native Coding Agent editor development build
********************************************

The compiled fork exposes ``CODING_AGENT`` through Blender's real
``Area.ui_type`` RNA property and native editor picker, under a separate AI
heading. Python Console remains in Scripting; Shift+F4 always selects Python.
The editor uses a Console subtype rather than introducing incompatible DNA
space IDs. The existing host still owns the GPU chat presentation, providers,
approval boundary and tool execution; those are not a new C++ inference engine.

Native integration
==================

* C++ owns separate runtime-only text buffers for each editor subtype. Host
  selection must not clear or rebuild them through the preview buffer cache.
  The buffer table releases its allocator storage before application shutdown.
* Native Python history, input, font size and interpreter namespace survive
  switching to the agent and back. Chat drafts never become Python input.
  AI menu entries do not falsely advertise Python's Shift+F4 shortcut.
* ``SpaceConsole.coding_agent_input`` exposes the initialized coding input even
  when Python or Generative AI is active, without switching editors. Session
  changes update all cached Console spaces, including inactive workspaces, so
  an old conversation's hidden draft cannot reappear in a new conversation.
  The accessor is nullable and runtime-only; it does not initialize an editor.
* The deferred ``_console_angelo`` bootstrap enables the host when the native
  editor is first selected, without registering classes inside a draw call.
  The host timer also installs input in newly opened native chat windows.
* Preference reads keep the host enabled (``prefs_reload.py``). Reading
  preferences disables every add-on and enables the ones the new preferences
  list; Angelo's start-up enables the host without a saved entry, so Load
  Factory Preferences (File > Defaults, Preferences > Save & Load), Load Factory
  Settings, Revert to Saved Preferences, an application template and Blender's
  or Angelo's settings import would otherwise leave no chat until a restart. While the
  host is registered it wraps ``addon_utils.disable_all`` and ``reset_all``: the
  host stays loaded (its chats, turns, sign-ins and in-memory keys are untouched)
  and is listed in the new preferences. A new entry has no settings, so factory
  preferences reset the host's own preferences to its defaults; saved ones come
  back on Revert; everything else follows the new preferences as in Blender.
  Quitting (an ``exit_pre`` handler takes the wrappers out), Reload Scripts and a
  person disabling the host are left alone: a disabled host is never turned back
  on. AI Providers connections are not preferences: their records are
  ``connections.json`` in Angelo's data folder and their keys are in the system's
  key store, so no preference read touches them. Covered by
  ``test_native_prefs_reload`` and, in the app, ``test_blender_prefs_and_quit``
  (which also times a quit with the welcome screen or a modal card opened just
  after it was requested).
* The fork has a real ``Preferences > AI Providers`` section. Before the host
  is enabled, it offers an enable button instead of drawing unregistered addon
  properties. No preview navigation enum or editor-header proxy is required.
* Native file drops use the existing multi-file ``FileHandler`` dispatcher.
  Both the Console quoted-path route and the window-level .blend opener defer
  to AI reference handlers. Busy turns reject drops without opening a project.
* Project saves retain the editor identity/layout, but omit AI history and
  transcript backing buffers. Saving does not clear the live draft. Python
  Console history serialization is unchanged.
* The scratch launcher detects the native fork and does not replace its built-in
  branding menus with stock-binary preview menus.
* Selecting chat in an ordinary editor slot can dock/pop out and return without
  losing the cached Properties editor or requiring unsafe joins of partial rows.
  Restoration uses the complete editor subtype (Drivers, Assets, UV, Timeline,
  node-tree type) and retains finer image/Dope Sheet editing modes.
* Native workspace/floating-editor copies retain independent unsent AI inputs,
  cursor and font size, without copying sent history or transcripts. Python's
  existing duplication/namespace behavior is unchanged.
* Return to Dock transfers the latest draft before closing its source window.
  Failed or canceled transfers keep the pop-out open and display an error;
  changing conversations during a pending transfer cannot copy an old draft
  into the new conversation.

Isolated macOS arm64 build
==========================

Build into ``../build-angelo-native``, a sibling of the checkout, never into
``/Applications``. Install means staging the app and its dependencies inside
that build directory, not replacing the user's installed Blender.

The validated development preset uses ``blender_lite.cmake`` plus OpenSubdiv, WebP and build
information. It deliberately omits Cycles and various optional codecs, simulation,
interchange and release features. It is an editor-validation build, not a full
release distribution; "Full-feature macOS arm64 build" below is the release
feature set, and "Full-feature Windows x64 build" its Windows counterpart.
Linux builds are not covered here.

From the fork root, prerequisites for a fresh checkout::

   git lfs install --local --skip-repo
   git lfs pull upstream \
     --include='assets/**,release/datafiles/**,release/darwin/Blender.app/Contents/Resources/**,scripts/startup/bl_app_templates_system/**' \
     --exclude='release/datafiles/splash.png'
   git -c submodule.lib/macos_arm64.update=checkout \
     submodule update --init --progress lib/macos_arm64
   git -C lib/macos_arm64 lfs install --local --skip-repo
   git -C lib/macos_arm64 lfs pull

These commands retain the source-pinned library commit; they do not update the
Blender source branch or overwrite customized splash artwork. The source asset
remote must supply the LFS objects for the pinned revision.

Then, with Xcode, CMake and Ninja available::

   scripts/angelo/build_native.sh

``ANGELO_NATIVE_BUILD_DIR`` and ``ANGELO_BUILD_JOBS`` optionally override the
output directory and parallelism (deprecated spellings still work, see "Legacy
names"). The script never downloads prerequisites or changes credentials. The
macOS app is staged at ``../build-angelo-native/bin/Angelo.app`` (Angelo
bundle and executable). CMake records the absolute source and build paths, so a
build directory belongs to one checkout path; build directories from before the
rename are not used, updated or deleted.

The staged executable is ad-hoc (linker) signed, so the Keychain treats each
rebuild as a new app and saved API keys need approval again (see
``ai_providers.rst``, "Saved API keys"). Optionally set
``ANGELO_DEV_SIGN_ID`` (or a deprecated spelling) to an
Apple Development signing identity already in your login keychain (Xcode >
Settings > Accounts > Manage Certificates > +);
``build_native.sh`` then runs ``scripts/angelo/dev_codesign.sh``, which
re-signs only the main executable (no resource seal) and prints its designated
requirement. One Always Allow should then last across rebuilds (not yet
verified with a real identity). A self-signed
certificate stabilizes the designated requirement but not the Keychain
partition, so it does not remove the prompt. A failed signing stops the script
with the app built but still ad-hoc signed. Running ``cmake --build`` directly
skips the hook. This is local development only; release signing is separate.

Agent runtime
-------------

The app can bundle its agent runtime, the patched OpenAI Codex build that runs
Codex connections with cache-affinity keys (``gpt_launch.rst``, "Agent
runtime and prompt caching"; pinned by
``scripts/addons_core/angelo_agent/agent_runtime.json``). Give either build
script the folder holding the build's files for the target platform (macOS:
``codex`` and ``codex-code-mode-host``)::

   ANGELO_AGENT_RUNTIME_DIR=<folder> scripts/angelo/build_native.sh

The script checks the folder first (``release_checks.py agent-runtime``: the
files, the pinned version, ``cacheAffinityKey`` in the app-server schema) and
passes it to CMake as ``ANGELO_AGENT_RUNTIME_DIR``
(``build_files/cmake/angelo_agent_runtime.cmake``), which installs the files
under their upstream names into ``Angelo.app/Contents/Helpers`` (Apple's place
for helper tools; ``<install folder>\agent-runtime`` on Windows). Building by
hand::

   cmake -S . -B ../build-angelo-native -DANGELO_AGENT_RUNTIME_DIR=<folder> ...

A development build may omit it: the configure then says "none bundled", and
Codex connections use the person's own Codex, or the build named at run time by
``ANGELO_AGENT_RUNTIME=<folder>/codex`` (no rebuild needed). The scripts pass
the variable on every configure, so leaving it unset removes it from the cache;
an earlier install's ``Contents/Helpers`` stays until the app folder is
removed. ``ANGELO_REQUIRE_AGENT_RUNTIME=ON`` (set by the release scripts) makes
a configure without it an error and installs the pin's ``release_marker``
(``Contents/Resources/agent-runtime-release.json``), with which the app accepts
only ``off`` in ``ANGELO_AGENT_RUNTIME``; a development build keeps path
overrides. The runtime adds about 300 MB uncompressed
(``codex`` 234 MB, ``codex-code-mode-host`` 65 MB on macOS arm64).

Full-feature macOS arm64 build
==============================

``scripts/angelo/build_release_macos.sh`` builds the release feature set with
Blender's own ``blender_release.cmake``: Cycles with Metal, OSL, Embree, path
guiding and OpenImageDenoise; OpenVDB (Blosc), NanoVDB, voxel/Quadriflow remesh;
fluid, Bullet and ocean simulation; USD, MaterialX, Hydra, Alembic, OBJ, STL,
PLY, FBX and Grease Pencil I/O; FFmpeg, libsndfile, OpenAL, Core Audio, Rubber
Band; translations (``WITH_INTERNATIONAL``), Freestyle, libmv, IK solvers, Draco and
meshoptimizer for glTF, OpenXR, input NDOF/IME and the Quick Look thumbnailer
(``blender-thumbnailer.appex``). The bundle is still ``Angelo.app`` with the
Angelo icons, document icon and splash: those come from the source tree, not from
a build configuration, so no override is needed. It is a separate preset, not a
replacement for ``build_native.sh``: the development build stays the quick
editor-validation build and is the only one that keeps ``BLI_assert`` compiled in.

From the fork root, with Xcode (including the Metal toolchain), CMake and Ninja,
and the agent runtime's folder (required; "Agent runtime" above)::

   ANGELO_AGENT_RUNTIME_DIR=<folder> scripts/angelo/build_release_macos.sh

``ANGELO_RELEASE_BUILD_DIR`` (default ``../build-angelo-release``, an ignored
sibling of the checkout), ``ANGELO_BUILD_JOBS`` (default 10) and ``ANGELO_LIBDIR``
select the output, parallelism and libraries; ``ANGELO_RELEASE_CONFIGURE_ONLY=1``
stops after configuring. The script uses the same optional
``ANGELO_DEV_SIGN_ID`` hook as ``build_native.sh`` and never signs beyond the
ad-hoc linker signature otherwise.

**Translations.** ``WITH_INTERNATIONAL=ON`` compiles Blender's catalogues
(``locale/po``) to ``blender.mo`` and Angelo's (``locale/angelo/po``) to a second
text domain, ``angelo.mo``, installed beside it under
``Angelo.app/Contents/Resources/5.2/datafiles/locale/<language>/LC_MESSAGES``. The
catalogues are globbed at configure time (``CONFIGURE_DEPENDS``), so adding or
changing a ``.po`` and re-running the script rebuilds only the ``locales`` target.
The development build has no translation support. The design, the 21 languages,
right-to-left handling and the limits are in ``localization.rst``.

**Libraries and Git LFS.** The pinned ``lib/macos_arm64`` submodule is multi-gigabyte
and is not populated in a linked worktree. The script looks for libraries in
``ANGELO_LIBDIR``, then ``lib/macos_arm64`` of the checkout, then the main
checkout's (``git worktree list``), passes the result as ``-DLIBDIR``, uses it
read-only, warns if it is not at the source's pinned commit, and refuses to
continue if a probe library or a source asset (``startup.blend``, ``splash.png``,
``ui.blend``, or any essential brush/node ``assets/**/*.blend``) is still a Git
LFS pointer. The release-feature suite opens each bundled essential library,
so a successful compile alone cannot hide missing brushes or nodes.
It sets ``GIT_LFS_SKIP_SMUDGE=1`` and
never fetches: in a worktree run ``git lfs checkout`` (local objects only, no
network) before building. ``CMAKE_BUILD_TYPE`` is ``Release`` (``-O2 -DNDEBUG``),
the deployment target ``26.0``.

Full-feature Windows x64 build
==============================

``scripts/angelo/build_release_windows.ps1`` is the Windows counterpart: the same
``blender_release.cmake`` with what it switches on for Windows x64, which is Cycles
with every GPU device of Blender's Windows release and its precompiled kernels
(CUDA cubins, OptiX, HIP, HIP RT and oneAPI), WASAPI audio and the TBB malloc
proxy, plus the Explorer thumbnail handler ``BlendThumb.dll`` (the release
configuration switches the handler on for macOS only). It configures with the
Visual Studio 2022 generator, as ``make.bat`` does, into
``..\build_windows_Release_x64_vc17_Release``, builds the ``INSTALL`` target and,
with ``-Package``, runs ``cpack -G WIX`` for ``Angelo-<version>-windows-x64.msi``;
the identity, icons and installer art come from the source tree, and the agent
runtime from the folder ``ANGELO_AGENT_RUNTIME_DIR`` (or ``-AgentRuntimeDir``)
names, as on macOS::

   $env:ANGELO_AGENT_RUNTIME_DIR = "C:\angelo\agent-runtime"
   .\scripts\angelo\build_release_windows.ps1 -Package

* **Nothing quietly turned off.** Blender's CMake turns a library or GPU toolkit
  it cannot find into a status line and an option that is off for this configure
  while the cache still says ``ON``. The script configures with
  ``WITH_STRICT_BUILD_OPTIONS=ON`` (a missing one is an error), refuses a configure
  log that says ``not found, disabling``, and reads every release option back
  from the cache before compiling.
* **GPU toolkits** are found where Blender's CMake looks for them: the CUDA Toolkit
  through ``CUDA_PATH`` (12.8 or newer: the default architectures include
  ``sm_120``), the OptiX SDK through ``OPTIX_ROOT_DIR`` (8.0 or newer), the AMD HIP
  SDK through ``HIP_PATH`` (6.0 or newer); HIP RT, the oneAPI DPC++ compiler and
  Level Zero come with ``lib\windows_x64`` (Level Zero as ``level-zero``, which
  ``platform_win32.cmake`` now reads besides the older ``level_zero``). The oneAPI
  kernels are compiled ahead of time with Intel's ``ocloc``, which Blender's Windows
  libraries do not carry (its dependency builder makes ``ocloc`` for Linux only):
  Intel's Graphics Offline Compiler for OpenCL, through ``OCLOC_INSTALL_DIR``. They
  are checked before configuring,
  so a missing one fails in seconds. ``-AllowMissing cuda,optix,hip,hiprt,oneapi``
  builds without the named parts; the build is then not equivalent to Blender's
  Windows release, and the manifest says so.
* **Agent runtime** (``releasing.rst``, "Agent runtime"): ``codex.exe``,
  ``codex-code-mode-host.exe`` and the two sandbox helpers, checked before
  configuring (x64 executables, OpenAI's signature on its unchanged helpers, the
  pinned version, ``cacheAffinityKey``) and installed into
  ``<install>\agent-runtime`` with the release marker; ``-AllowMissing
  agent-runtime`` builds a rehearsal without it.
* **Never fetches, never installs, signs only with -Sign.** ``-Sign`` is the
  release's Authenticode step (``releasing.rst``, "Windows"). As on macOS it sets
  ``GIT_LFS_SKIP_SMUDGE=1``, refuses libraries or source assets that are still Git
  LFS pointers, and warns when ``lib\windows_x64`` is not at the pinned commit.
  ``angelo-release-build.json`` in the build folder records the source and
  library commits, Visual Studio, the toolkits, every release option, whether the
  build is release-equivalent, the time taken and the MSI's size and SHA-256.

On the Google Cloud validation machine the ``release`` stage runs it unattended
(``app_identity.rst``, "Running it on Google Cloud").

**What differs from the development build.**

* ``WITH_ASSERT_RELEASE`` is off, as in an official release: ``BLI_assert`` is
  compiled out, so the ``native_assertion`` markers of ``native_suite_checks.py``
  can only fire on the development build. Run the suites on both for assertion
  coverage.
* Cycles is present. ``Scene.cycles`` exists, ``render.engine = "CYCLES"`` is
  accepted, and the agent's typed ``cycles.*`` settings apply. The first render
  on the GPU compiles Metal pipeline states for several minutes (see
  "Full-feature build measurements"), so never put a GPU render in a per-suite test.
* ``bpy.app.build_options`` reports every release option; the Python-side lists
  that were marked "compiled out of this build" in ``native_coverage.rst``
  describe the development build only.
* ``Contents/PlugIns/blender-thumbnailer.appex`` is added (identifier
  ``<app identifier>.thumbnailer``, display name "Angelo Thumbnailer"; ``app_identity.rst``).

**Running the suites against it.** The app's ``angelo_agent`` can be replaced by
a symlink to a checkout (move the installed copy out first)::

   APP=../build-angelo-release/bin/Angelo.app/Contents/Resources/5.2/scripts/addons_core
   mv "$APP/angelo_agent" ../build-angelo-release/installed-angelo_agent
   ln -s "$PWD/scripts/addons_core/angelo_agent" "$APP/angelo_agent"
   python3 tests/python/angelo_agent/run_native_suites.py --gui \
     --binary "$PWD/../build-angelo-release/bin/Angelo.app/Contents/MacOS/Angelo"

Never run ``cmake --install`` (or the script) while the symlink is in place: the
install would write through it into the checkout. Move the symlink away first.

``test_blender_release_features.py`` is the suite that only means something on
this build. Each feature is gated on ``bpy.app.build_options`` and skipped, with a
reason in the receipt, where it is compiled out, so it also passes on the
development build; ``full_feature_build`` in ``release-features.json`` says which
binary ran. On the full build it renders a real Cycles still and an OSL
(LLVM-compiled) material on the CPU, runs the voxel Remesh modifier, the voxel
remesh and Quadriflow operators, a Mesh to Volume to Mesh geometry-nodes
round trip (OpenVDB), drops a Bullet rigid body, exports and re-imports a mesh
through OBJ, STL, PLY, USD, Alembic, FBX and glTF (also Draco-compressed, which
loads the bundled Draco bridge library), encodes and decodes a three-frame H.264
clip and a WAV file, and checks that the translation catalogs load. It is the
suite that exercises Cycles, OpenVDB and the C++ importers and exporters; the
other suites' I/O goes through the glTF add-on (``test_blender_director``,
``test_blender_flows``, ``test_blender_flow_recipes``). ``test_blender_property_set``
refuses ``render.engine = "CYCLES"`` only where ``scene.cycles`` is missing and
otherwise applies Cycles settings, reads them back and undoes them.

Signing readiness of the full-feature build
===========================================

The release pipeline that signs, notarizes and packages this build is
``releasing.rst`` (``sign_notarize_macos.py``); this section records what the
unsigned build needs from it. As built, nothing is signed with an identity: the
bundle carries only the linker's ad-hoc signature (the main executable is
``adhoc,linker-signed``, ``Info.plist`` is not bound, no resources are sealed, so
``codesign --verify`` of the bundle fails) and the thumbnailer is ad-hoc signed
with its sandbox entitlements. Measured on an ad-hoc-signed full-feature build
(macOS 26.3, Xcode 26.5 SDK), without an Apple signing identity:

* **Agent runtime.** ``Contents/Helpers/codex`` and ``codex-code-mode-host``
  are signed by path with their own entitlements (``releasing.rst``,
  "Entitlements"); they are two executables beyond the counts below.
* **Code to sign, inside out.** 196 Mach-O files, all arm64: 101 Python extension
  modules, 91 dylibs, the executables ``Angelo``, ``python/bin/python3.13`` and
  ``blender-thumbnailer`` and one object file (``python/lib/python3.13/config-3.13-darwin/python.o``,
  which only builds extension modules and need not ship). There are no symbolic
  links, no frameworks and no other code. The object file and ``libpython3.13.a`` are
  no longer installed (``install_dir`` in ``source/creator/CMakeLists.txt``), and the
  release signer drops them from older builds. An ad-hoc trial on a copy signed all 196
  files with ``--options runtime`` plus the appex and the app with zero failures and
  ``codesign --verify --strict`` passed (about 6,400 sealed resources). Running the app
  writes nothing into the bundle: Python's bytecode goes to ``~/Library/Caches/Angelo/pycache``
  and the install step precompiles the whole bundled library and the scripts for any child Python
  (``releasing.rst``, "Nothing is written inside the app"; about 3 s on a first install, nothing on
  an incremental one).
* **Entitlements** (``release/darwin/entitlements.plist``, applied to the app and the
  bundled ``python3.13``; the appex keeps ``thumbnailer_entitlements.plist``: sandbox
  plus read-only user-selected files, required to sign even locally). The file
  holds the four keys ``releasing.rst`` justifies ("Entitlements"): upstream's
  ``allow-jit`` is left out because ``allow-unsigned-executable-memory`` already
  permits ``MAP_JIT``. Measured under the hardened runtime, with an ad-hoc copy and
  ``test_blender_release_features.py`` plus a numpy/ctypes/OIIO import smoke:

  .. list-table::
     :header-rows: 1
     :widths: 38 62

     * - Entitlements
       - Result
     * - All five upstream keys
       - Passes (OSL JIT, Cycles, I/O, FFmpeg, numpy, ctypes).
     * - None, or without ``disable-library-validation``
       - Aborts at launch: dyld rejects ``liboslexec.dylib`` because ad-hoc libraries share no
         Team ID with the executable. With one Developer ID team signing every nested file this
         check passes for Angelo's own libraries; the key is still needed for add-on or
         extension binaries the user installs (upstream's reason). Not measurable without a
         certificate.
     * - Without ``allow-unsigned-executable-memory``
       - Killed (SIGKILL) at the first OSL material render: OSL's LLVM JIT maps executable memory.
         Required.
     * - Without ``allow-jit``
       - Everything still passes. Upstream lists it for LLVM; Angelo's file leaves it out (above).
     * - ``device.audio-input``, ``device.camera``
       - Not testable headless. Needed for the Voice feature (AVFoundation through a child
         ``ffmpeg``, attributed to the app) and for extensions; the usage-description strings
         are already in ``Info.plist``.

  Not needed: ``keychain-access-groups`` (saved keys use the file-based login Keychain
  through ``SecItem*``, not the data protection Keychain), the App Sandbox for the app
  (only the Quick Look appex is sandboxed), Apple Events, and DYLD environment access.
  Metal pipeline compilation runs in the system's ``MTLCompilerService``, outside the
  process: a Cycles GPU render (Apple M4 Pro, 20 cores) also works under the hardened
  runtime with all five keys, 11 s with a warm kernel cache.
* **Outside the bundle.** No Mach-O file should depend on an absolute path outside
  the bundle, the system, or ``@rpath``/``@loader_path``/``@executable_path``.
  The macOS app's glTF Draco and MeshOptimizer bridges use
  ``@loader_path/../../../../lib`` to find ``Contents/Resources/lib``. They build
  with that install RPATH because ``install(PROGRAMS)`` copies them unchanged
  beside the add-on; retaining the build RPATH would expose build-machine paths.
  Check both the dependencies and ``LC_RPATH`` entries before signing. The
  release-feature suite exercises both compressed glTF round trips.
* **Deployment floor.** Angelo requires macOS **26.0**. The
  single source is ``bundle.macos_min_version`` in ``angelo_identity.json``;
  CMake uses it for the compiler deployment target and the app and Quick Look
  extension use it for ``LSMinimumSystemVersion``. This covers the 19 NumPy
  extension modules in the pinned libraries that declare ``minos 26.0``.
  Older macOS versions are not supported.
* **Identity.** The thumbnailer identity is Angelo's
  (the app identifier plus ``.thumbnailer``, the convention for embedded extensions), the
  info strings name the vendor, and the app imports the UTI
  ``org.blenderfoundation.blender.file`` instead of exporting it, so it does not declare
  a type an installed Blender also exports. All of it comes from
  ``scripts/modules/angelo_identity.json`` (``app_identity.rst``).
* **Size.** The installed bundle is 893 MB uncompressed (executable 175 MB, ``Resources/lib``
  350 MB, bundled Python 215 MB, datafiles 127 MB of which 77 MB are 50 translation
  catalogs, scripts 29 MB) and 296 MB zipped, against 683 MB for the development
  app. The whole build directory is 1.7 GB.

Product name, data and state folders, and environment
=====================================================

The product is called Angelo (for its earlier names, see "Legacy names"). The
bundle is ``Angelo.app`` with executable ``Angelo`` and identifier
``studio.hyperbond.angelo``; window titles, menus, the splash, About,
desktop/MSIX metadata, messages, the external CLI (``angelo``) and every
product identifier use the name. macOS treats a new identifier as a new app,
so a build with a different identifier asks for permissions (microphone,
camera) again.
Angelo's own version (1.0.0, independent of Blender's 5.2.2) is shown as
"Angelo 1.0" with the Blender base noted.
Every identity value (name, vendor, bundle id, GUIDs, URL scheme, version label,
links, folder names) is defined once in ``scripts/modules/angelo_identity.json``;
Angelo shares no folder, installer code or identifier with a stock Blender
(``app_identity.rst``).

**Environment variables.** Every product variable is ``ANGELO_<NAME>``. Python
reads them only through ``angelo_agent/product_env.py``, which also accepts the
two pre-rename spellings as deprecated fallbacks; ``ANGELO_<NAME>`` wins and an
empty value counts as unset. ``build_native.sh``, ``dev_codesign.sh`` and the
branding scripts apply the same rule inline, and ``run_native_suites.py``
passes a LIVE suite's opt-in variable set under a deprecated spelling to the
suite under its ``ANGELO_`` name. Registering the add-on prints the deprecated
names that are still set. The product variables are ``STATE_DIR``,
``CONNECTIONS_DIR``, ``LIBRARY_DIR``, ``DISABLE_DEFAULT_CHAT``,
``DISABLE_LOCAL_CLI``, ``CLI_RUNTIME_DIR``, ``CLIENT``, ``TURN_CONTEXT``,
``NATIVE_BUILD_DIR``, ``BUILD_JOBS``, ``NODE_MODULES``, ``DISABLE_KEYCHAIN`` and
``DEV_SIGN_ID`` (``ai_providers.rst``, "Saved API keys"), ``AGENT_RUNTIME`` (the
patched Codex build's ``codex`` executable, or ``off``), ``CACHE_KEY_TRACE``
(prints each cache-affinity key's public inputs), ``CACHE_KEY_SECRET`` (a
fixed secret in place of the stored per-user one, so a measurement can
reproduce its keys across machines or users; all three ``gpt_launch.rst``, "Agent runtime
and prompt caching") and ``TEAM_CACHE_KEY_SECRET`` (a fixed team secret in
place of the console's, for a team measurement against a console that sends
none; ``gpt_launch.rst``, "Agent runtime and prompt caching", Team chats;
being newer than the rename, these four have no deprecated spelling and are read only as ``ANGELO_``), and ``CONSOLE_URL``
and ``GATEWAY_URL``, the Angelo console and gateway origins of the team
account (defaults ``https://console.angelo.studio`` and
``https://gateway.angelo.studio``; plain ``http://`` only for a loopback
development host, ``127.0.0.1``, ``::1`` or ``localhost`` on any port; read
only by ``team_endpoints.py``;
``ai_providers.rst``, "Team account and team connections"); the test harness adds
``QA_DIR``, ``QA_LOG``, the ``QA_LIVE*`` and ``EVAL_*`` opt-ins, ``BINARY``,
``EXPECTED_BINARY``, ``RUNTIME_QA_DIR``, ``PROVIDER_QA_REPORT`` and
``ORDERED_RECOVERY_REPORT``. The child-process API-key variables
(``ANGELO_XAI_API_KEY`` and its MiniMax/Kimi siblings, and
``ANGELO_TEAM_GRANT_KEY`` for team Codex connections) are set by the app, never
read from the user; inherited copies under any spelling are removed.

**Data folder.** Account metadata, Media Studio's library and the account
store's isolated native CLI profiles live in ``~/Library/Application Support/Angelo``
(Windows: ``%LOCALAPPDATA%\Angelo``; Linux: ``$XDG_STATE_HOME/Angelo``). On
registration, ``angelo_agent/data_migration.py`` moves the pre-rename data
folder once; ``recover_connections.py`` runs the same move before writing the
default store.

**State folder.** Chats, snapshots (including the deduplicated
``snapshot-store``), attachments, task memory, telemetry, the default file
workspace and the isolated native CLI profiles of chat sign-ins live in
``angelo-agent`` inside Angelo's own user config folder
(``<Angelo user folder>/5.2/config``, for example
``~/Library/Application Support/Angelo/5.2/config`` on macOS; never Blender's,
see ``app_identity.rst``), unless ``ANGELO_STATE_DIR`` selects another folder.
On registration, before anything opens it, ``angelo_agent/state_migration.py``
moves the pre-rename state folder once. State written by builds that still
used Blender's folder is moved by the explicit, dry-run-by-default
``scripts/angelo/migrate_user_folder.py`` (``app_identity.rst``, "Migrating
existing settings").

Both moves follow the same rules (``folder_moves.py``):

* With the explicit override (``ANGELO_CONNECTIONS_DIR`` or ``ANGELO_STATE_DIR``,
  any spelling) set, nothing is moved: that folder is isolated.
* Entries are moved one by one with same-volume renames. Nothing is copied,
  overwritten or deleted. If any destination already exists (an empty
  destination folder is fine), nothing moves; Angelo uses its own folder,
  prints the conflict and shows it in Preferences > AI Providers until the user
  merges or renames the old folder.
* Native CLI profiles (``claude-profile`` and ``codex-profile``) stay at their
  exact original paths, because a native CLI may key its stored sign-in to the
  profile path. ``storage.native_profile_directory`` keeps using a retained
  profile until one exists in the new folder; new profiles are created there.
  Absolute ``native_profile`` references remain valid because those folders do
  not move. A ``README-Angelo.txt`` note in the old folder says it is still in
  use.
* An interrupted move resumes on the next launch; only folders emptied by the
  move are removed.

The state folder records absolute paths inside itself, so its move also keeps
those identities working:

* Ordered snapshot records' file-journal roots, and every record's original
  document path, that point into the old folder are rewritten under the profile
  transaction; a marker in the new folder keeps this resumable. Restoring a
  snapshot's file changes therefore writes into the moved default workspace.
  Pre-ordered (version 1/2) journals keep their recorded roots: they never
  allowed a combined restore, and their content is the timeline's admission
  evidence (``checkpoint_policy.rst``). The deduplicated store records no paths.
* Attachment records are found by file name inside the chat's own references
  folder (``references.py``), not by their recorded absolute path.
* Task memory scoped to the pre-rename folder's profile is re-scoped on load
  instead of being dropped (``task_memory.rst``).

API keys saved in the Keychain are namespaced by the resolved connections
folder, so moving that folder starts a new, empty namespace; the state-folder
move does not affect it. Quit every running pre-rename build before the first
Angelo launch: it would recreate an empty old folder, which later launches
report as a conflict.

Legacy names
============

The product was called ToonLab and then Sensei before it was renamed Angelo.
New code, tests and documentation use only Angelo names. Pre-rename names
remain only where something saved, exported or recorded may still contain
them:

* **Environment fallbacks:** ``SENSEI_<NAME>`` and ``TOONLAB_<NAME>`` for every
  ``ANGELO_<NAME>`` (``product_env.py``; inline in ``build_native.sh``,
  ``dev_codesign.sh``, ``angelo_branding.mjs`` and ``angelo_letters.mjs``).
* **Folders, in migration code only:** the ``Sensei`` data folder
  (``storage.py``, ``data_migration.py``) and the ``toonlab-agent`` state folder
  (``state_paths.py``, ``state_migration.py``), including the native CLI
  profiles retained in them, and Blender's own per-user folder, which earlier
  builds shared (``scripts/angelo/migrate_user_folder.py`` is the only code that
  names it).
* **Preferences, keymaps and editors (C++):** the ``toonlab_agent`` add-on
  module, the 68 operator idnames the product shipped (``toonlab.*``,
  ``sensei.*``, ``toonlab.sensei_about``/``_welcome`` and
  ``SCULPT_OT_sensei_sculpt_pass``/``_program``), its 7 ``TOONLAB_MT_*`` and
  ``SENSEI_MT_*`` menus, the ``toonlab_preferences_section`` property and the
  ``toonlab`` and ``sensei_genai`` Console languages, in the frozen tables of
  ``blenkernel/intern/angelo_legacy_names.cc``. One shipped operator has since
  been retired (Director's internal input modal); its row stays and maps to
  nothing, because no keymap or preference could name it. Reading preferences renames
  them in the add-on list (keeping its saved preferences), user keymaps and
  Quick Favorites; ``KeyMapItems.new`` maps them for exported keymap presets and
  scripts; reading a .blend renames the Console languages. ``bpy.ops`` calls in
  user scripts are not mapped.
* **Saved custom properties:** ``sensei_director``, ``sensei_manual_workspace``,
  ``_sensei_native_graph_v1``, ``_sensei_sculpt_regions_v1`` and
  ``_sensei_program_stage`` are read when the Angelo key is absent; writes use
  ``angelo_director``, ``angelo_manual_workspace`` and ``_angelo_*`` and drop
  the old key (``legacy_names.py``).
* **Agent history:** chats saved before toolset 15 name tools ``toonlab_*``.
  They stay readable in the transcript, handoff, task memory and Studio
  previews, and approved receipts are checked against the recorded name, but a
  pre-rename tool name is never executed and their native contexts are not
  resumed (``agent_integration.rst``, ``agent_execution.rst``). Task memory
  scoped to the ``toonlab-agent`` profile is re-scoped (``task_memory.rst``).
* **Recorded history:** the ``previous_name``/``previous_app_name`` records in
  ``release/branding/angelo/selection.json`` and ``design-brief.json`` keep the
  name in use when the mark was selected.

Pre-rename runtime identities changed without a fallback, because nothing that
outlives a session depends on them: the topology digest domain (a pending
planned selection is expanded again), the external CLI protocol (now 2; a
mismatched peer reports ``incompatible``), staging datablock names, private
preview folders, thread names, test completion markers and the default build
directory.

Director presentation
=====================

Normal GUI startup defaults to a duplicate Director workspace: the 3D view plus
chat, no manual editing chrome, and no input handler of its own, so every native
viewport input still works. The top-right Director/Manual menu
sits beside the Scene scope selector. The host owns this layout and its persisted
non-secret mode preference; no additional DNA space IDs or inference engine are
introduced. Manual restoration preserves original editor data and current drafts.
See ``generative_studio.rst`` for chat-only Studio operation and billing gates.

Acceptance tests
================

From the fork root, using that compiled binary rather than stock Blender::

   NATIVE=../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo
   ANGELO_QA_DIR=/tmp/angelo-compiled-core-qa "$NATIVE" \
     --background --factory-startup --python-exit-code 1 \
     --python tests/python/angelo_agent/test_blender_compiled_core.py
   ANGELO_DISABLE_DEFAULT_CHAT=1 ANGELO_QA_DIR=/tmp/angelo-compiled-gui-qa "$NATIVE" \
     --factory-startup --no-window-focus --enable-event-simulate \
     --python tests/python/angelo_agent/test_blender_compiled_gui.py
   ANGELO_DISABLE_DEFAULT_CHAT=1 ANGELO_QA_DIR=/tmp/angelo-editor-regressions "$NATIVE" \
     --factory-startup --no-window-focus --python-exit-code 1 \
     --python tests/python/angelo_agent/test_blender_editor_regressions.py

The core contract is also registered with CTest::

   ctest --test-dir ../build-angelo-native -R '^angelo_native_editor_core$' --output-on-failure

CTest derives the installed executable and macOS runtime-library paths from the
actual ``blender`` target's output name, not a literal ``Blender.app`` path. A
leftover app from an earlier build cannot silently stand in for ``Angelo.app``.

``tests/python/angelo_agent/run_native_suites.py`` runs these and every other
classified native suite with fresh isolated state and automatic receipt and log
checks; see ``agent_execution.rst``. The compiled GUI and editor-regression
fixtures import the app's installed host modules, so they validate the built
bundle rather than uncommitted checkout Python.

The core test requires native RNA and exercises actual C++ buffer switching,
Python execution/resume, shortcut routing and .blend save/load. It enables no
addon or account. The GUI test isolates both state and account metadata, disables
account probes, and exercises lazy bootstrap, the native picker, synthetic
Shift+F4, the real C++ multi-file dispatcher, busy rejection, docking, pop-out
and return. It captures PNGs and writes ``compiled-gui.json``; check its
``success`` field because Blender's normal quit does not signal fixture failure
with an exit code. Neither test submits inference, starts a microphone or uses
paid Fast mode. The GUI test uses synthetic events and native file-handler calls,
not physical keyboard input or OS drag-and-drop acceptance.
The GUI fixture disables preference saving and isolates its recovery-file temp
directory, as well as using fresh state/account directories for every run.
The editor-regression fixture writes ``editor-regressions.json``. Check its
``success`` field and ``phase == "complete"`` as well as process status. It
exercises hidden drafts, inactive editors/workspaces, seven cached editor modes,
workspace-dependent return, deliberate docking failure, pending-transfer edits
and a conversation switch during a pending return. It uses no live providers.
Director/Studio acceptance (fresh QA directory, local API fixtures only)::

   ANGELO_QA_DIR=/tmp/angelo-director-qa "$NATIVE" \
     --factory-startup --no-window-focus --enable-event-simulate \
     --python tests/python/angelo_agent/test_blender_director.py

Check ``director-qa.json`` for ``success=true`` and ``phase=complete``.
``test_blender_director_navigation.py``, ``test_blender_first_run.py`` and
``test_blender_chat_cards.py`` (``run_native_suites.py --gui``) cover Director
input, the first-launch welcome screen, the one-step Connect flow and the
plain-language cards against stand-in
``claude``/``codex`` executables (``fake_provider_cli.py``); none signs in,
opens a browser or runs inference. This covers
native layout, chat billing approvals, outputs/previews, enhancement, library
organization, export, GLB import/Undo and a healthy preview after Undo. It is not
proof of live provider access or natural-language model reliability.

Renamed-identity migration (two window launches and one background launch
sharing one isolated Blender config folder; no provider or account)::

   python3 tests/python/angelo_agent/run_native_suites.py --binary "$PWD/$NATIVE" \
     --gui --include test_blender_legacy_migration

Check ``fixture/legacy-migration.json`` for ``success=true``. The fixture saves
preferences that enable the pre-rename add-on module with saved add-on
preferences and a user keymap item naming a pre-rename operator, a .blend with
the pre-rename Console languages and custom properties, and, through the real
add-on, a pre-rename state folder (chat with brief, attachment, old toolset and
native context; deduplicated snapshot; journaled file write; retained native
profile). A launch without state override must then show the renamed add-on with
its preferences, the renamed keymap item, ``KeyMapItems.new`` mapping, the moved
state folder with the retained profile, the brief, the attachment, no resumed
native context, a real snapshot restore (scene and file) through the product
operator, and the .blend data read under the new names.

Application identity (default folders, bundle, Help menu, About, splash; one
background and one window launch with only home-folder isolation, so the
product's real default folders are used)::

   python3 tests/python/angelo_agent/run_native_suites.py --binary "$PWD/$NATIVE" \
     --gui --include test_blender_app_identity

Check ``fixture/app-identity.json`` for ``success=true`` and view the three PNGs
beside it. The suite checks that the default user, scripts, datafiles,
extensions, cache and temporary folders are ``Angelo`` folders under the
scratch home, that no Blender folder is created there, that the
failed-thumbnail folder is ``fail/angelo``, and that no file under the real
``Blender`` folders changes during the run (checked against the run's time
window); it reads the splash, About, the Help menu and the status bar from the
running app. The app's and the thumbnailer's ``Info.plist`` resolve from the
identity (bundle id ``studio.hyperbond.angelo``, version 1.0.0 with "(based on
Blender 5.2.2)", vendor, copyright, imported ``.blend`` type, no exported type,
default not claimed), and ``bpy.app.version`` stays Blender's 5.2. ``cpack -G
DragNDrop`` produces ``Angelo-1.0.0-macos-arm64.dmg`` with volume ``Angelo``;
mounted, it holds ``Angelo.app`` (with the thumbnailer extension), the
``Applications`` link and ``copyright.txt``. See ``app_identity.rst``.

The host must allow Metal device discovery even for the background test; a
restricted subprocess sandbox may deny that access before any fixture runs.

These suites validate the native editor, not complete Codex parity, live
provider authentication, paid features or a full release package. They are
macOS-only: Windows is checked on a cloud machine (``app_identity.rst``,
"Windows validation", including what it does not cover), and Linux is not
covered. A styled DMG (``ANGELO_DMG_STYLED``), code signing and notarization
(``releasing.rst``) are outside these suites.

Full-feature build measurements
===============================

Measured on macOS 26.3, an Apple M4 Pro (14 cores, 48 GB), Xcode 26.5, Blender
5.2.2 LTS sources with the pinned libraries, while other work kept the
1-minute load average at 45-55, so wall times are upper bounds.

* **Build.** 8,088 build steps from an empty directory in about 40 minutes
  (including a 100-second configure) at ``-j10``, ``nice 10``; the development
  build of the same sources takes 6,793 steps in 12.5 minutes. Neither build has
  a compile error, link error or compiler warning: Angelo's own C++
  (``angelo_sculpt``, ``angelo_legacy_names``, the sculpt program and Console
  editor changes) builds unchanged with release flags (``-O2 -DNDEBUG``,
  ``WITH_ASSERT_RELEASE=OFF``).
* **Suites.** Every non-live native suite passes on the full build and on the
  development build (``run_native_suites.py --gui``, the add-on symlinked to the
  checkout), including ``test_blender_release_features`` (36 checks on the full
  build; 3 checks and 15 recorded skips on the development build). A paused
  suite (``SIGSTOP``) still counts against its timeout, so lower a run's
  priority instead of pausing it. Registered CTests: ``angelo_native_editor_core``
  and the upstream ``script_load_*`` and ``script_pyapi_*`` tests pass;
  ``script_load_modules`` passes on an installed bundle and fails only with the
  add-on symlinked in (its file walk does not follow the link). The other
  upstream tests need ``tests/files`` Git LFS data, which these runs do not fetch.
  Angelo's public source leaves that folder (Blender's regression data, about
  790 MB) out, as Blender's own source archive does; nothing Angelo builds,
  ships or tests reads it. To run Blender's upstream regression tests, take
  ``tests/files`` from Blender's repository
  (https://projects.blender.org/blender/blender) at the release Angelo is based
  on (``BLENDER_VERSION`` in ``source/blender/blenkernel/BKE_blender_version.h``);
  without it CMake registers none of those tests.
* **Metal.** The first Cycles GPU render on a machine compiles pipeline states:
  8 minutes 12 seconds under that load, 2 seconds afterwards (cache under
  ``$HOME/.cache/cycles``). No suite renders on the GPU; only CPU renders run in
  tests.
* **Performance** (``benchmark_blender_agent_execution.py``,
  ``..._stage_compression.py``, ``..._snapshot_cost.py``, two runs each, retired
  instructions; run-to-run spread 0.0-7.3%). Full build against the development
  build of the same sources: median 0.94 (range 0.72-1.10) over 61 operations;
  58 operations are cheaper and only the three dense-snapshot captures are
  dearer (+1%, +6%, +9% for 200, 50 and 500 MB). Those captures write files, and
  their spread is 4-7% on the full build against 1-3% on the development build,
  so the +6% and +9% are not clearly more than noise. The ``darwin-arm64``
  ceilings in ``perf_ceilings.json`` belong to the development build; if the
  full build becomes the measured binary, give it its own platform key rather
  than raising those ceilings.
