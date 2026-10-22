.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Application identity and side-by-side installs
**********************************************

Angelo is based on Blender and installs and runs next to a stock Blender
install. Nothing that identifies the application to the operating system or to
the user is shared with Blender: user folders, caches, crash files, installer
codes, bundle and package ids, the Windows taskbar identity, executable names,
vendor strings and links all name Angelo. Blender stays named where it is the
truth: the license notices, the "based on Blender" attribution, the ``.blend``
file type, and documentation of the Blender features Angelo keeps.

The single source
=================

Every identity value lives in one file, ``scripts/modules/angelo_identity.json``.
Two loaders read it; nothing else repeats a value:

* ``build_files/cmake/angelo_identity.cmake`` (included from the top-level
  ``CMakeLists.txt``) sets ``ANGELO_*`` CMake variables for packaging, bundle
  metadata and templates, and global compile definitions for the few C/C++
  sources and resource scripts that need a product string. It stops the
  configure on a missing member, a malformed GUID or a value containing ``;``,
  a quote or a backslash (those would split or break a definition).
* ``scripts/modules/angelo_identity.py`` (standard library only) exposes the
  constants and ``url(key)`` to Python, including the Help menu, splash and About.

========================= ====================================================
Section                   Consumers
========================= ====================================================
``product``, ``vendor``   CPack (name, vendor, contact, description, file
                          names), version resources, About, readme, notices
``version``               installers, bundle and file version metadata,
                          splash, About, window title, status bar
``paths``                 user, system and cache folders (GHOST, ``appdir``),
                          crash file, session temp prefix, external CLI
``bundle``                ``Info.plist`` (app and thumbnailer), DMG volume,
                          Linux desktop id and metainfo
``windows``               executable and launcher names, MSI upgrade code,
                          install folder, registry key, app id (AUMID, also
                          the file-type ProgID), thumbnail handler CLSID, MSIX
                          identity, application id and protocol
``urls``                  Help menu, splash, About, readme, metainfo,
                          crash dialog, bug-report prefill, installer pages
========================= ====================================================

Two annotation members document the file. ``_decided`` gives the reason for
each fixed value (product version, vendor and publisher, the bundle
id, the minimum macOS version, distribution, the MSIX name
``HyperbondStudio.Angelo``, that the installer does not take ``.blend`` over as
the Windows default, and that Help keeps "Support Blender"); ``_pending`` lists
what a release must still confirm (that mail to the contact address is
received, and the ``angelo.studio`` pages the links point to). A value is a
one-line change in the file; the build picks it up on the next configure, and
``test_native_app_identity.py`` checks that no file hard-codes a Blender
identity.

Version
-------

Angelo has its own product version, ``version.label`` (``major.minor.patch``,
starting at ``1.0.0``), independent of the Blender release it is based on, so
Angelo keeps shipping while Blender stays at 5.2.2. The label drives every
package and file name (``Angelo-1.0.0-macos-arm64.dmg``,
``Angelo-1.0.0-windows-x64.msi``), the bundle's short and build versions, the
Windows version resource and MSI version, and the About, splash, window-title
and status-bar text, shown as "Angelo 1.0" (the patch is shown only when it is
not zero). The Blender base is read from the build (``BLENDER_VERSION``) and
noted wherever it fits: "Angelo 1.0 · Blender 5.2.2 LTS" on the splash,
"Based on Blender 5.2.2 LTS" in About, "(based on Blender 5.2.2)" in the bundle
info string, a ``Comments`` entry in the Windows version resource, and the
readme and notices.

Blender's own version plumbing is untouched: ``bpy.app.version``, ``.blend``
file versioning and compatibility, add-on and extension version checks, and the
``5.2`` config and scripts subfolders all keep following Blender. The C++
definition that carries the display version is scoped to the two directories that show
it (windowmanager, space_info) so a release does not recompile everything.

Distribution
------------

Angelo is distributed as direct downloads: a DMG and an MSI. There is no Mac
App Store build, because a Blender-based app cannot live in its sandbox; the
MSIX package stays buildable but is not a primary channel. Vendor and
publisher are Hyperbond Studio PTE. LTD.

Folders
=======

Angelo's per-version user folder is the product folder plus the version:

* macOS: ``~/Library/Application Support/Angelo/5.2`` (caches in
  ``~/Library/Caches/Angelo``), system folder
  ``/Library/Application Support/Angelo/5.2``.
* Windows: ``%APPDATA%\Angelo\5.2`` (caches in ``%LOCALAPPDATA%\Angelo\Cache``),
  system folder ``%ProgramData%\Angelo\5.2``.
* Linux: ``$XDG_CONFIG_HOME/angelo/5.2`` (default ``~/.config/angelo/5.2``). The
  system install location is unchanged; Angelo provides no Linux packages yet.

Beneath it Blender's layout is unchanged (``config``, ``scripts``,
``extensions``, ``datafiles``), and ``config`` holds ``userpref.blend``,
``startup.blend`` and the agent state folder ``angelo-agent``. The account and
Media Studio data folder (``Angelo`` under Application Support, ``%LOCALAPPDATA%``
or ``$XDG_STATE_HOME``; see ``native_editor_build.rst``) shares the product
folder; its entries and the version folders do not collide.

The explicit overrides keep Blender's names so upstream tooling and the test
harness work unchanged: ``BLENDER_USER_RESOURCES``, ``BLENDER_USER_CONFIG``,
``BLENDER_USER_SCRIPTS``, ``BLENDER_USER_DATAFILES`` and
``BLENDER_USER_EXTENSIONS`` (and the ``BLENDER_SYSTEM_*`` counterparts). A user
who exports one of them for a stock Blender therefore also points Angelo at it;
Angelo ignores the folders Blender would use by default, not the variables.

The crash file is ``angelo.crash.txt`` (or ``<file>.crash.txt``) in the
temporary folder, and the per-session temporary folder is ``angelo_XXXXXX``.
The freedesktop thumbnail cache (``~/.thumbnails``) is shared between
applications by its specification; Angelo's failed-thumbnail markers are in
``fail/angelo``, not ``fail/blender``, so neither app hides thumbnails from the
other.

Test isolation
--------------

The harness (``tests/python/angelo_agent/run_native_suites.py``) points
``HOME``, ``CFFIXED_USER_HOME`` (Cocoa reads the home folder from it, not from
``HOME``), the ``XDG_*`` variables, ``TMPDIR`` and every ``BLENDER_USER_*``
folder at a scratch directory, plus ``ANGELO_STATE_DIR``,
``ANGELO_CONNECTIONS_DIR`` and ``ANGELO_LIBRARY_DIR``. Nothing in a test needs
or may touch the real Angelo or Blender folders.

The live evaluation and benchmark harness (``eval_live_agent.py``, which
``bench_mcp_vs_angelo.py`` runs for Angelo's side) keeps ``HOME``, because the
Claude Code and Codex runtimes find their sign-ins through it, and gives every
trial its own ``BLENDER_USER_CONFIG``, ``BLENDER_USER_RESOURCES``,
``BLENDER_USER_SCRIPTS``, ``BLENDER_USER_DATAFILES`` and
``BLENDER_USER_EXTENSIONS``, so a trial never falls back to the person's own
Angelo user folder. The sign-ins a trial uses do not live in the user folder:
the account and connections folder (``Angelo`` under Application Support) is
separate; the native CLI profiles are named by absolute path in the
connection records (the Claude Code Keychain item is keyed to that path, Codex
keeps ``auth.json`` in its ``CODEX_HOME``); and the team sign-in's Keychain
items are named from the bundle identifier
(``studio.hyperbond.angelo.team-sign-in``, ``...team-grant-key``,
``...team-access-token``, ``managed_policy.DOMAIN``).

Migrating existing settings
===========================

Earlier builds kept Angelo's user folder inside Blender's
(``~/Library/Application Support/Blender/5.2``), so a stock Blender and Angelo
shared ``userpref.blend``, ``startup.blend``, keymaps, add-on state and the
``angelo-agent`` folder. Angelo never moves, writes or deletes anything in
Blender's folders:

* **Nothing is migrated automatically.** Preferences, keymaps, themes, add-ons
  and startup files are not moved or merged on their own: they are Blender's.
  Angelo starts with its own defaults, and Blender is no longer affected by
  Angelo's saved preferences. A person who wants their Blender settings chooses
  to copy them ("Bringing Blender settings over" below).
* **Agent state is moved once, on request.** Chats, snapshots and their file
  journals, attachments, task memory, telemetry and the default workspace live
  in ``<Blender folder>/5.2/config/angelo-agent``. Run, with Angelo closed,
  ``scripts/angelo/migrate_user_folder.py`` with Angelo's bundled Python
  (a dry run prints the plan; ``--apply`` performs it). It touches exactly that
  one entry in Blender's folder and leaves ``userpref.blend`` and everything
  else there alone. Entries move with same-volume renames (nothing is copied or
  overwritten); existing Angelo state in the new folder is a reported conflict
  and nothing moves; an interrupted move resumes when the command is run again
  (a marker in the new folder names the old one). Snapshot file journals and a chat's task
  memory scope follow the move, so restoring a snapshot and reusing a brief keep
  working. Native CLI sign-in profiles (``claude-profile``, ``codex-profile``)
  stay at their exact paths, as in the pre-rename folder move
  (``native_editor_build.rst``), with a note in the old folder.
* **The account and Media Studio data folder does not move.** It was already
  Angelo's own.
* **Windows and Linux** use the same command with their folder layout
  (``--platform``/``--home`` select it for tests). A user who had set
  ``BLENDER_USER_CONFIG`` or ``BLENDER_USER_RESOURCES`` has nothing to migrate.

Bringing Blender settings over
------------------------------

A person switching from Blender copies their settings when they choose to:
"Use my Blender settings" on the first-launch setup card, or later **File >
Defaults > Import Blender Settings…** (also in Preferences > Save & Load).
``settings_import.py`` finds the folder and copies (standard library only),
``settings_import_apply.py`` applies the copy in the running app and
``settings_import_ui.py`` / ``settings_import_card.py`` are the menus, the
confirmation card and the result notice. ``blender_folders.py`` is the one place
that names stock Blender's folders; ``migrate_user_folder.py`` reads it too.

* **Which folder.** On the setup card an earlier Angelo version's folder wins (an
  Angelo upgrade), and Blender's own import copies it unchanged
  (``preferences.copy_prev``: everything, the agent state included, and the
  start-up file reloaded when nothing changed yet). Otherwise, and always from the
  menu, stock Blender's folder: the newest version that is not newer than Angelo's
  Blender version (5.2 first), down to the previous major release as Blender's own
  import searches, and that holds something to bring: macOS
  ``~/Library/Application Support/Blender/<version>``, Windows
  ``%APPDATA%\Blender Foundation\Blender\<version>``, Linux
  ``$XDG_CONFIG_HOME/blender/<version>`` (default ``~/.config/blender``). With
  ``BLENDER_USER_RESOURCES`` or ``BLENDER_USER_CONFIG`` set the two apps already
  share that folder, so nothing is offered and the card says so; a
  ``BLENDER_USER_SCRIPTS``, ``BLENDER_USER_DATAFILES`` or
  ``BLENDER_USER_EXTENSIONS`` variable shares that one part, which is skipped, as
  is a part that is a link to Angelo's own. The card and the confirmation name the
  app and version ("from Blender 5.2", "from Angelo 5.1").
* **What comes over.** Everything in that per-version folder, as Blender's own
  import copies an earlier version's: ``userpref.blend`` (preferences, keymaps and
  input, add-on enablement and add-on preferences, Quick Favorites, asset library
  paths, extension repositories); user add-ons and the folders they keep their own
  settings in; extensions with their wheels and ``.user`` folders (Blender's MCP
  add-on comes over like any other); presets (operator, render, keyconfig,
  theme); app templates; startup scripts and modules; studio lights, MatCaps and
  HDRIs; fonts and colour management; bookmarks and recent files, added after
  Angelo's own entries when Angelo already has some.
* **What does not, and why.**

  - Blender's ``startup.blend``. Angelo builds its own start-up layout (Director
    with the agent chat) over the start-up file. Tested: a stock start-up file
    keeps that layout but brings its own scene and workspaces, and one saved in a
    workspace without a 3D view (Compositing) opens with no chat and no Director.
    The default scene stays in Blender's folder; the person can open it and save
    it as Angelo's start-up file (File > Defaults > Save Startup File).
  - The ``angelo-agent`` (or pre-rename ``toonlab-agent``) state an early Angelo
    build kept in Blender's ``config``: ``migrate_user_folder.py`` moves it, on
    request, with its records rewritten.
  - Blender's interface language and theme: the import keeps the language and
    look chosen in Angelo (on the setup card, or already in use), so Angelo keeps
    its own look. A theme saved as a preset comes over with the presets and is
    listed in Preferences > Themes beside Angelo Dark and Angelo Light. None can
    take their place: Angelo's two are named for Angelo (stock Blender's bundled
    "Blender Dark" / "Blender Light" live in Blender's own app, not in the folder
    copied), and the setup card always applies Angelo's bundled file
    (``setup_card.look_preset_path``), never a same-named file among the person's.
  - Anything outside Blender's per-version user folder: the machine-wide system
    folder, add-ons in other script directories (their paths come over, so they
    keep loading from where they are), caches, and autosave and recovery files in
    the temporary folder.
  - Versions outside Blender's own window (3.x and older) and versions newer than
    Angelo's Blender.

* **Copy only.** Blender's folder is read and never written, moved or deleted
  from; the host and window tests compare its every byte, time and link before
  and after. A link comes over as a link, except that one pointing into the copied
  folder points at Angelo's copy, so nothing Angelo writes through it reaches
  Blender's folder.
* **Angelo keeps working.** Reading the copied preferences makes Blender disable
  every add-on and enable the ones they list. A stock Blender's preferences do not
  list ``angelo_agent`` (Angelo's start-up enables it), so it is kept loaded and
  listed throughout and the chat and any card stay as they are, as on every other
  preference read (``prefs_reload``, "Preference reads" in the native editor
  document). The language (and
  its translation switches), the exact theme, Angelo's own add-on preferences and,
  on the card, changed mouse and Spacebar choices are put back, then the
  preferences are saved. An add-on that fails to load stops nothing: Blender prints
  its error, the corner notice after the import names it in one line, and Blender
  keeps it listed in Preferences > Add-ons, where it can be fixed or turned off; the
  next start is unaffected. Startup scripts, fonts and colour management take effect
  when Angelo next opens, and the notice says so. While an agent turn runs, the menu
  entries wait.
* **Backup and undo.** Before anything is copied, every file the import would
  replace is copied to ``settings-import-backup`` in Angelo's per-version folder,
  with a manifest of what it adds and replaces, written first so an interrupted
  import can still be undone. **Undo Settings Import** (File > Defaults,
  Preferences > Save & Load, and the notice's Undo import) asks first, then puts
  those files back, removes what the import added (a folder only when it is empty)
  and reads the preferences again with the same protections; the language and look
  stay. The confirmation card says the backup is made and how to undo. One import
  is kept, and its backup only belongs to the version folder it was made in.

Window chrome
=============

Angelo's main window is told from a stock Blender's at a glance by colour: the
operating system's title bar and the top bar (the application menu, the
workspace tabs and the scene selectors) are ``#076573``. The status bar, the
editors and their headers, the menus and the workspace tabs themselves keep the
theme's own colours.

The colour
----------

It is the darkest facet of the wordmark's "A" (``#087787``, the last entry of
``FACETS`` in ``build_files/utils/angelo_wordmark.mjs``) at 85% of its
brightness: every channel scaled alike, so the hue is the facet's. The facet
colour itself does not carry the text drawn on it; WCAG 2 asks 4.5:1 of
normal-size text (level AA):

================================================== =========== ===========
Text on the bar                                    ``#087787`` ``#076573``
================================================== =========== ===========
Menu titles (the theme's pulldown text)            3.72        4.77
Hovered menu title (white on its highlight)        4.34        5.37
Labels (the top bar's header text)                 4.53        5.80
macOS title (white at 85%)                         4.26        5.35
Windows title (white)                              5.25        6.73
================================================== =========== ===========

The macOS row is the title of the active window, which AppKit draws in its
label colour: white at 90% on macOS 26.3 (read from the app), 85% being the
lower figure used here. A window in the background has its title dimmed by
the system, as every background window has (about 2.6:1 on the teal).

Where it is set
---------------

The colour is theme data and appears nowhere in C++:

* ``space_topbar.header`` of the compiled default theme
  (``release/datafiles/userdef/userdef_default_theme.c``). "Angelo Dark"
  (``scripts/presets/interface_theme/Angelo_Dark.xml``) is an empty preset that
  restores this default.
* The top bar's ``header`` in
  ``scripts/presets/interface_theme/Angelo_Light.xml``, the other bundled theme.
  The two bundled themes are named "Angelo Dark" and "Angelo Light" everywhere: on
  the setup card, in Preferences > Themes (its label when no preset was applied
  yet is "Angelo Dark") and in Blender's Quick Setup where it still shows.

Blender already colours the title bar from the theme
(``wm_window_decoration_style_set_from_theme``): a main window takes the top
bar's colour, and any other window (Preferences, a render, an editor opened in
its own window) takes its editor's header colour or the default background.
That is unchanged. So every main window has the teal title bar, and an editor
window keeps a title bar that continues its own content. Until its first draw
a new window is cleared to the colour between the editors
(``TH_EDITOR_BORDER``), not to the top bar's colour, so a start does not flash
the whole window teal.

Two rules keep text readable on a top bar of any colour, Angelo's or a theme
author's:

* **Title text** follows ``GHOST_Window::coloredTitleBarNeedsLightText``: light
  text when white has the higher WCAG contrast against the title-bar colour
  (relative luminance up to about 0.179), dark text otherwise. A test on the
  colour's HSV value would call a saturated dark colour light: ``#087787`` has
  a value of 0.53 and would get dark title text.
* **Text drawn straight onto the top bar** (menu titles, borderless buttons)
  uses widget colours shared with every editor.
  ``widget_text_ensure_readable_on_topbar`` (``interface_widgets.cc``) replaces
  such a colour with the top bar's own header text only where it is below 4.5:1
  on the bar and the header text is better. The default theme keeps its own
  menu-title colour (4.77). In Angelo Light the shared pulldown text is dark (2.58 on the
  teal), so the top bar's menu titles use its header text (5.80). A theme whose
  text already reads on its top bar is not touched, and no other editor,
  header, menu or popup is.

The workspace tabs keep the theme's tab colours, which every editor's tabs
share. They bring their own background, so the default theme's tabs read at
5.8:1. Angelo Light's unselected tabs are 80% opaque: on the teal their dark
labels measure 3.9:1 (4.7:1 on a grey bar). The selected tab is opaque and
unaffected. Raising that would mean changing the light theme's tab colour for
every editor.

Platforms
---------

* **macOS.** The window's background colour shows through a transparent title
  bar, and the window's own appearance (dark or light, by the rule above) sets
  the title and button colours, so the result does not depend on the system
  appearance. Both are properties every window sets for itself, and nothing in
  the full-screen code changes them (full screen, where the title bar slides in
  over the content, has not been exercised by a run).
* **Windows 11** (build 22000 and later). ``DWMWA_CAPTION_COLOR`` carries the
  colour, rounded to the byte so the caption matches the top bar beside it, and
  ``DWMWA_TEXT_COLOR`` carries white or black by the rule above, so the title
  never depends on the system's own choice for a custom caption (its light or
  dark app mode is unrelated to the theme). **Written from the API
  documentation and not yet run on Windows.** Windows 10 rejects both
  attributes and keeps the system title bar; the top bar is teal there too.
* **Linux.** X11 and Wayland do not offer window decoration styles; only the
  top bar is coloured.

Other themes and saved preferences
----------------------------------

The teal belongs to Angelo's two bundled themes. A theme a user makes or
imports keeps its own top bar colour, and the title bar follows it with the
text chosen by the same rule. Themes are saved in ``userpref.blend``:
preferences saved by an earlier development build keep the top bar they saved
until Angelo Dark or Angelo Light is chosen again in Preferences > Themes. Those
saved before the presets were renamed still name "Blender Dark" or "Blender Light"
in Preferences > Themes (the label is the saved file's name) until a look is chosen
again; the colours are unaffected.
Nothing migrates them, because no released build wrote another colour.

Installer artwork
=================

The disk image's Finder window and the pages of the Windows installer are lit
like the splash (``release/branding/angelo/README.md``, "Installer artwork").
``build_files/utils/angelo_installer_art.mjs`` exports the files. Their sizes
and formats are what CPack and WiX consume:

* ``release/darwin/background.tif``: 1080 x 700 pixels at 144 dpi, LZW TIFF;
  ``CPACK_DMG_BACKGROUND_IMAGE`` when ``ANGELO_DMG_STYLED`` is on.
  ``release/darwin/buildbot/background.tif`` is the same file; nothing in this
  repository reads it (upstream's build bot did).
* ``release/windows/installer_wix/WIX_UI_DIALOG.png``: 493 x 312, 8-bit RGB
  PNG; ``CPACK_WIX_UI_DIALOG``, the bitmap of the welcome and finish pages.
* ``release/windows/installer_wix/WIX_UI_BANNER.bmp``: 493 x 58, 24-bit BMP;
  ``CPACK_WIX_UI_BANNER``, the top of every other page.

Both hosts draw their own text over these pictures, in a colour the artwork
does not choose. The exporter measures the WCAG 2 contrast of that text on the
pixels it writes and stops below 4.5:1 for text, and below 3:1 for the arrow (a
graphic) and for the white names explained below. The lowest contrast anywhere
in each text's box:

======================================================== ========
Text over the artwork                                    Contrast
======================================================== ========
Disk image: an icon's name in black, as Finder draws it  5.10
Disk image: the same at 85% strength                     4.56
Disk image: a name in white                              4.10
Disk image: the same at 85% strength                     3.40
Disk image: the dimmest chevron of the drag arrow        3.28
WiX dialog: title and description, black on the paper    21.00
WiX dialog: the finish page's check box text             18.42
WiX banner: title and description                        21.00
======================================================== ========

The exporter records these figures, the files' sizes and their checksums under
``installer_art`` in ``release/branding/angelo/coverage.json``.

The disk image window
---------------------

* **Size.** Finder draws a window's background picture at its size in points,
  and AppKit takes that size from the file's resolution: 1080 x 700 pixels at
  144 dpi are 540 x 350 points, drawn sharp on a Retina display. Upstream
  Blender's picture has the same size and resolution. The exporter reads both
  back from the file it wrote.
* **Layout.** The window is laid out as upstream Blender's: Finder bounds 100,
  100, 640, 472 (540 points wide, 372 tall with the title bar), 128-point icons
  with 12-point names, the application at 135, 190 and the ``Applications``
  link at 400, 190. ``DMG`` in ``build_files/utils/angelo_installer_scenes.mjs``
  holds these numbers and the picture is drawn from them;
  ``release/darwin/dmg_window.applescript.in`` applies them, and
  ``test_native_branding.py`` fails when the two differ.
* **Who applies it.** CPack itself only copies the picture into the image, as
  ``.background/background.tif``. With ``ANGELO_DMG_STYLED=ON``,
  ``packaging.cmake`` also gives CPack the script
  (``CPACK_DMG_DS_STORE_SETUP_SCRIPT``): CPack mounts the image and runs the
  script with the volume's name, and Finder stores the window's settings in the
  volume's ``.DS_Store``. That needs a GUI session and the user's Automation
  approval for Finder, so the option is off by default.
* **Title bar.** The window's 372 points include the title bar, which has been
  22 to 32 points tall (32 on macOS 26.3, from AppKit's frame for a titled
  window). The window therefore shows the top 340 to 350 points of the picture
  and never a strip below it; nothing is drawn in the last 10.
* **Names.** Finder draws an icon's name in black in a window that has a
  background picture, in the light and the dark appearance alike, because it
  treats such a window as light. The developer of a disk image tool reports
  this for macOS 10.14 and 10.15, and upstream Blender's picture depends on it
  (a light plate under the application's name). Each name therefore sits on a
  plaque whose middle facet is the A's ``#0b8a99``, the one teal of the A that
  carries black at 4.5:1 and white above 4:1; the measured box is 92 points
  wide, from 74 to 91 points below the icon's centre, where upstream's plate
  puts a 12-point name. The white rows of the table are the margin for a
  Finder that drew the names in the dark appearance's colour.
* **Other files.** Anything else at the top of the volume (``copyright.txt``)
  is placed in a row below the picture, outside the window.
* **Not run.** No styled image has been built. The script compiles
  (``osacompile``: its syntax and Finder's terminology) and has never been
  executed, and the names' colour and place come from the sources above, not
  from a capture of Finder on macOS 26.

The Windows installer pages
---------------------------

The MSI is built with WiX 3 and the UI set ``WixUI_Angelo``
(``release/windows/installer_wix/WixUI_Angelo.wxs``). Its pages are WiX's own
dialogs, laid out in installer units (370 x 270, each 4/3 of a pixel at 96
dpi). The set's three text styles declare no colour, so Windows Installer draws
every text in its default black, and the text controls over a bitmap are
transparent.

* **Dialog bitmap** (370 x 234 units at the top of ``WelcomeDlg``,
  ``ExitDialog`` and the other pages without a banner). The title is at 135, 20
  and the description below it, both 220 units wide: they begin at pixel 180,
  right of the 164-pixel panel, on white. The finish page's check box (135,
  190, 220 x 40) paints the dialog colour behind itself, so the paper settles
  into that colour (``#f0f0f0``, ``COLOR_3DFACE`` of the default Windows theme)
  above it: the check box and the button row below the bitmap continue the
  page. The exporter requires the check box's area to be exactly that colour.
* **Banner** (370 x 44 units at the top of ``InstallDirDlg`` and the pages that
  follow it). The title is at 15, 6 and the description at 25, 23; the longest
  line, the description of ``InstallDirDlg``, is in a control 280 units wide
  that ends at pixel 407. The paper is white to there, and the cap with the
  raven begins at pixel 418 on the banner's bottom edge and further right above
  it.
* **Scaling.** Windows Installer stretches a bitmap to its control, so at a
  display scale above 100% the artwork grows with the dialog and stays aligned
  with its text; it is not drawn sharper.
* **Not seen.** These pages have not been looked at on Windows: the validated
  installs were silent. The control positions are read from the WiX 3 sources
  of the three dialogs named above.

macOS notes
===========

* The bundle is ``Angelo.app`` (executable ``Angelo``). Its identifier is
  ``bundle.macos_id``, ``studio.hyperbond.angelo``, and is final; the
  thumbnailer extension appends ``.thumbnailer``, and the Linux desktop id is
  the same. macOS keeps per-identifier state (permission grants, saved window
  state), so a different identifier makes it treat Angelo as a new app and ask
  again; earlier development builds carried other identifiers, and nothing of
  their per-identifier state is migrated.
* The ``.blend`` type belongs to Blender. Angelo imports its definition
  (``UTImportedTypeDeclarations``), never exports it, and is an "Open With"
  editor that does not claim default ownership.
* ``CFBundleGetInfoString`` and ``NSHumanReadableCopyright`` come from the
  identity. The DMG is named ``Angelo-<version>-macos-<arch>.dmg``
  with volume name ``bundle.macos_dmg_volume_name``; its styled window
  (``ANGELO_DMG_STYLED=ON``) is described under "Installer artwork".

Windows
=======

The CI host renders and checks the Windows templates but does not build or
install them:

* Executables are ``Angelo.exe`` and ``Angelo-launcher.exe`` (the launcher is
  the shortcut target); every code path that looked for ``blender.exe`` or
  ``blender-launcher.exe`` uses the identity's names.
* The MSI has a new, fixed upgrade code (``windows.msi_upgrade_code``, never
  derived from the install folder and never to change once released), installs
  into ``Angelo`` (``%LOCALAPPDATA%\Programs\Angelo`` for the installing user by
  default, ``Program Files\Angelo`` with ``ALLUSERS=1``;
  ``updates_and_reports.rst``, "Windows installs per user") and registers
  under the ``Angelo`` registry key. The WiX
  template is configured from ``WIX.template.in``; the welcome art
  ("Installer artwork") and the executable file id
  (``CM_FP_<component>.<file>``) follow the identity. The
  register and unregister steps run ``Angelo.exe`` itself: the launcher returns
  immediately, so on uninstall Windows Installer would remove the files under
  the still-starting program and leave the registrations behind.
* The app id (``BLENDER_WIN_APPID``) is ``windows.app_user_model_id``; it is the
  taskbar identity and the file-type ProgID. Registration adds Angelo as an
  "Open with" choice for ``.blend`` and makes it the default only when no
  application is (so an Angelo-only machine opens files by double-click); it never
  replaces another default unless ``windows.claim_blend_default`` is true.
* The thumbnail handler has its own CLSID (``windows.thumbnail_clsid``). The
  ``.blend`` shell-extension key itself is shared by nature (one handler per
  extension): the last app to register wins, and unregistering removes it only
  while it still names Angelo's handler, so uninstalling Angelo cannot remove a
  Blender handler registered afterwards.
* The MSI is ``Angelo-<version>-windows-<arch>.msi``. The MSIX manifest
  (``AppxManifest.xml.template.in``; buildable, not the primary channel) uses the identity's
  package name, publisher display name, application id and ``angelo://``
  protocol. The package name and publisher must match the Store reservation.

Windows validation
------------------

The Windows checks run in four stages on a throwaway Windows Server 2022
machine with an NVIDIA T4 GPU ("Running it on Google Cloud", below); what a
stage does not establish is marked. Server 2022 is not Windows 10
or 11, and the machine's build folder has no git history, so About shows Date,
Hash and Branch as "Unknown" there.

*Identity*, ``windows_validate.ps1``:

* A full ``make.bat lite`` build (Visual Studio Build Tools 2022 17.14); the
  executables are ``Angelo.exe`` and ``Angelo-launcher.exe``; the version
  resource reads Hyperbond Studio PTE. LTD., Angelo, 1.0.0; a background run
  resolves its config folder to ``...\Roaming\Angelo\5.2\config``; ``cpack -G
  WIX`` produces ``Angelo-1.0.0-windows-x64.msi``.
* Alone: the silent install registers ``Angelo.Desktop`` as the ``.blend`` default
  (nothing owned it), the installed program runs, and the silent uninstall removes
  the ProgID, the Open With entry and the default.
* Beside a stock Blender 5.2.2 MSI installed first: the stock folders are first
  shown not to change by themselves; Angelo's install and a run then leave every
  entry in them unchanged; Blender stays the ``.blend`` default; Angelo is an Open
  With choice; after Angelo's uninstall Blender still starts (``Blender 5.2.2
  LTS``) with its default.

*Team account client*, ``windows_team_checks.py`` (the fake console; the installed
app's own Python and add-on), both as SYSTEM and in the desktop session as a
signed-in user:

* **Credential Manager:** the sign-in and the grant key are two items, named
  ``studio.hyperbond.angelo.team-sign-in`` and ``...team-grant-key``, listed by
  ``cmdkey /list`` without the secret; a 2 KB secret round-trips; a secret over the
  2,560-byte limit is refused as ``too_long``; deleting removes the item.
* **Sign-in:** the PKCE loopback sign-in completes; the loopback server listens on
  ``127.0.0.1`` only and the sign-in adds no Windows Defender Firewall rule. In the
  desktop session the loopback address opens in the default browser (Microsoft
  Edge) and the branded "You're signed in" page renders.
* **Refresh lock:** four threads asking at once cause one refresh and one rotation;
  two more processes restoring the same stored sign-in at the same moment (the
  ``msvcrt.locking`` lock) both end signed in and nothing is revoked; the lock is
  released at once when its holder is killed.
* **Group Policy values** under ``HKLM\SOFTWARE\Policies\Hyperbond\Angelo``: every
  key and value type (``REG_SZ``, ``REG_DWORD``, ``REG_MULTI_SZ``, and
  ``REG_EXPAND_SZ``, which is expanded) becomes the policy the app
  enforces (``ConsoleURL``, ``GatewayURL``, ``RequireTeamSignIn``, personal
  connections blocked, ``Organization``, ``EmailDomains``, ``AdminContact``,
  ``ExtraCACertificates``); HKLM wins over HKCU, a key only in HKCU still applies;
  unusable values read restrictively and are reported; ``HKLM\SOFTWARE\Policies``
  is one shared key, so 32- and 64-bit programs read the same values.
* **Sign-out:** the grant is revoked, both Credential Manager items are gone and a
  later refresh is refused.

*Thumbnail handler*, ``windows_thumbnailer_build.ps1`` and the desktop checks:

* The Lite build leaves ``BlendThumb.dll`` out (``WITH_BLENDER_THUMBNAILER`` is
  off), so a normal Lite build never compiles the handler. The step switches the
  option on in the build folder, builds only that target and switches it off
  again: the DLL compiles and links (470 KB).
* ``BlendThumb.dll`` registers per user (its ``DllRegisterServer`` writes
  ``HKEY_CURRENT_USER``, as upstream's does), COM creates the class by the Angelo
  CLSID, and called directly on a ``.blend`` saved by Angelo's own window it
  returns a 256 by 256 thumbnail (the saved scene's own preview);
  unregistering removes it. It needs ``tbb12.dll`` beside it, which the Lite
  install does not ship (a build that ships the handler ships TBB).
* **Not verified:** Explorer's own thumbnail route. Asked for the same file, the
  shell answers ``0x80040154`` (class not registered) although it renders a PNG
  on the same machine. The cause is not established; the machine's
  configuration (an administrator with User Account Control off, Server 2022)
  may be why, since per-user COM registrations are treated differently for
  elevated processes. Check it by hand on a normal Windows 10 or 11 desktop.
* An install for all users (``ALLUSERS=1``) runs the registration step as
  SYSTEM (``Angelo.exe --register-allusers``), so ``DllRegisterServer`` writes
  SYSTEM's own ``HKCU``: a signed-in user gets no handler from the installer
  alone, until ``Angelo.exe --register`` runs as that user. This is how the
  upstream code is written; it is recorded here, not changed. The default
  per-user install runs ``Angelo.exe --register`` as the installing user, so
  that user gets the handler.

*Desktop session* (an automatically signed-in local user on the NVIDIA T4,
OpenGL), ``windows_gui_checks.ps1`` and the identity suite's Windows window
phase:

* Angelo's window starts and reports the graphics card (``Tesla T4``, OpenGL).
  The splash reads "Angelo 1.0 · Blender 5.2.2 LTS". About reads "Angelo — based on
  Blender", "Version: Angelo 1.0.0", "Based on Blender 5.2.2 LTS", and offers Donate,
  What's New, Credits, License, Angelo Website, Blender Website and Support Blender.
  The status bar reads "Angelo 1.0 (Blender 5.2.2)". The Help menu lists Manual,
  Blender Manual, Support, User Communities, Release Notes, Donate, Report a Bug
  and Save System Info. The executable is ``Angelo.exe`` and the config folder is
  Angelo's.

The desktop checks start from the user's own logon, through the Run key with
User Account Control off (``windows_cloud_bootstrap.ps1``), because a program
started by a scheduled task, even with the user's elevated token, has no
Credential Manager vault ("not available in this sign-in session").

**Not covered** by any run: Windows 10 and 11 as such; an in-place upgrade by a
newer MSI; the optional MSIX package; the crash and GPU dialogs; the taskbar
group and pinned name by eye, the Open With menu as Explorer draws it and the
installer's exit-page checkbox; Explorer's thumbnails (above); a release build
with the full feature set and its installer shipping ``BlendThumb.dll``; the team
Claude and Codex runtimes on Windows (the machine has neither tool); an
``ExtraCACertificates`` value behind a TLS-inspecting proxy; the team account's
status bar on Windows (the GUI suite that draws it is macOS-only, because
Windows resolves its folders from known-folder APIs, which the suite's home
isolation cannot redirect); a sign-in against a real console; and code
signing.

By hand, on a Windows 10 or 11 x64 machine with Visual Studio 2022, the pinned
libraries (``lib/windows_x64``), Git LFS, CMake and WiX Toolset 3.x on ``PATH``,
for what is not covered:

#. Install the MSI (with and without a stock Blender): the Start and taskbar names
   read Angelo, the taskbar group is Angelo's, and File Properties > Details shows
   Hyperbond Studio PTE. LTD., Angelo and 1.0.0.0 with the "Based on Blender" comment.
#. Right-click a ``.blend``: Open with lists Angelo; with a stock Blender
   installed, Blender stays the default; thumbnails for ``.blend`` files appear
   after ``Angelo.exe --register`` (a build that ships ``BlendThumb.dll``); install
   a newer Angelo MSI over an older one and confirm it upgrades in place (same
   upgrade code).
#. The crash and GPU dialogs only appear on a real crash or an unsupported
   graphics card; if one appears, read the wording ("Angelo has stopped working",
   "Report a Problem", the ``angelo.crash.txt`` log in the temporary folder).
#. MSIX (optional, not primary): package from
   ``<build>\release\windows\msix\AppxManifest.xml.template`` once a Store identity
   is reserved.

The scripts: ``scripts/angelo/windows_validate.ps1`` runs the identity stage and
prints a PASS, FAIL or SKIP line per check (``-StockBlenderMsi <installer>`` adds
the side-by-side scenarios; ``-ResultsDir`` keeps ``results.json``, the MSI logs and
the folder diffs as evidence). The others are named under "Running it on Google
Cloud". All of them install and uninstall Angelo (the identity stage also the
stock Blender) on the machine they run on, so use a throwaway one. None touches
credentials or a real console; the tools and repository access are yours.

Running it on Google Cloud
--------------------------

``scripts/angelo/gcp_windows_validate.sh`` runs the whole check unattended on a
throwaway Compute Engine machine (Windows Server 2022, ``n1-standard-16``, a T4
virtual workstation GPU, 300 GB SSD). It needs a signed-in ``gcloud``,
``ANGELO_GCP_PROJECT`` (your Google Cloud project), ``ANGELO_GCP_ZONE`` (a zone
of it with T4 GPUs) and ``ANGELO_GCP_NAME`` (the machine's name; its network,
firewall rule, service account and default bucket are named after it); the
script stops with a message naming any of them that is missing::

   scripts/angelo/gcp_windows_validate.sh up       # create everything and start (commit first)
   scripts/angelo/gcp_windows_validate.sh status   # stage, result objects, newest log lines
   scripts/angelo/gcp_windows_validate.sh fetch    # results, MSI logs, folder diffs, screenshots, the MSI
   scripts/angelo/gcp_windows_validate.sh redo     # after more commits: send what changed and run again
   scripts/angelo/gcp_windows_validate.sh stop     # power off (the disk stays; redo starts it again)
   scripts/angelo/gcp_windows_validate.sh down     # delete the VM, network, bucket and service account

``ANGELO_GCP_STAGES`` picks what a run does, joined by ``+`` (default
``identity+teams``):

* ``identity``: ``make.bat lite 2022b``, then ``windows_validate.ps1``: the
  executable names, version resource, default folders, ``cpack -G WIX`` and the
  install, run and uninstall scenarios, alone and beside a stock Blender. The
  Lite build carries Blender's build info (``blender_lite.cmake`` forces it off;
  the script configures with ``nobuild``, turns ``WITH_BUILDINFO`` on and then
  builds), and no MSI is packaged from a build without it: every MSI an install
  check accepts names its commit by the hash the program reports. Nothing else
  about Lite changes.
* ``teams``: ``windows_teams_validate.ps1`` installs the MSI and runs
  ``windows_team_checks.py`` with the installed app's own Python against the
  fake console (no account, no network): Credential Manager, the loopback sign-in,
  the refresh lock, the Group Policy registry values and the sign-out wipe.
  Every install, uninstall and MSI check on the machine takes only the build of
  the commit under test (``windows_msi_commit.ps1``, ``msi_commit.py``): a build
  manifest in the MSI's folder (``angelo-identity-build.json`` from ``identity``,
  ``angelo-release-build.json`` from ``release``) must name the file, its SHA-256,
  that commit and the build hash the built program reports for it. The newest
  MSI on the disk is never taken: when a build was skipped it is an earlier run's
  build of another commit. Without that install the later checks fail as skipped.
  The commit under test is the commit sent, returned only once the tree is proven
  to be it (``Get-SentCommit``), never instance metadata or an environment
  variable set at boot: a queued ``sync`` changes the tree while those keep the
  old value. A tree the send did not make proves nothing, so no commit is
  returned for it; the bench reads its commit inside each mode's own error
  handling, so a refusal still ends the desktop session with its ``DONE`` and
  reason instead of leaving the stage waiting out its hours.
* ``thumbs``: ``windows_thumbnailer_build.ps1`` builds ``BlendThumb.dll`` in the
  existing build folder (the Lite build leaves it out) and switches the option
  off again.
* ``agent``: ``windows_agent_validate.ps1`` installs CI's Python 3.13 (NumPy,
  jsonschema) and Node.js LTS, builds Lite with the macOS development build's
  OpenSubdiv and WebP options, then runs the host suite, the storage suites, the
  real-NTFS checks (``windows_agent_checks.py ntfs``, also inside ``Angelo.exe``)
  and the BACKGROUND native suites. With ``gui`` as well, the desktop session runs
  ``windows_agent_gui.ps1``: the official Claude Code and Codex CLIs installed for
  that user, ``windows_agent_checks.py cli`` inside the app started by its
  launcher, and the native suites with ``--gui``. No provider sign-in, no token.
  Results: ``agent_execution.rst``, "Windows validation".
* ``gui``: the checks that need a signed-in desktop. The setup creates a
  throwaway local administrator (random password, never printed),
  turns automatic sign-in on, sets the browser policy that skips its first-run
  page, turns User Account Control off (the checks need an administrator's full
  token in the user's own logon), adds a Run-key entry and restarts once. At
  sign-in ``windows_gui_checks.ps1`` runs in that session: the team checks with
  the sign-in page opened in the default browser and captured, Angelo's real
  window (the identity suite's Windows window phase: splash, About, Help menu,
  graphics renderer) and the thumbnail handler. Afterwards the user, the
  automatic sign-in, the Run key and the UAC setting are put back. A failure in
  this stage never loses the other results: everything uploads at the end.

* ``release``: the full-feature build (``native_editor_build.rst``, "Full-feature
  Windows x64 build") and its MSI, the MSI's agent runtime checked and run from
  the unpacked MSI (``windows_agent_runtime_check.ps1``), then the BACKGROUND
  native suites on it; ``test_blender_release_features`` must report
  ``full_feature_build: true`` (``windows_release_validate.ps1``). The agent
  runtime comes from the bucket's ``agent-runtime/`` folder, uploaded with
  ``agent-runtime DIR`` (``releasing.rst``, "Agent runtime"); the machine checks
  it against its ``SHA256SUMS`` and builds with it as
  ``ANGELO_AGENT_RUNTIME_DIR``. Before it the machine installs the GPU
  toolkits found in the bucket's ``toolkits/`` folder and brings
  ``lib\windows_x64`` to the pinned commit. The CUDA Toolkit, the OptiX SDK and
  the AMD HIP SDK are licensed downloads: whoever runs the stage downloads them,
  accepting their terms, and uploads them with ``toolkits FILE...``; the machine
  never fetches them itself. ``windows_toolkits.ps1`` installs them, each only
  after its Authenticode signature is ``Valid`` and from the vendor itself
  (NVIDIA Corporation for CUDA and OptiX, Advanced Micro Devices for HIP);
  anything else is refused and logged in ``toolkits.log``, and so is an OptiX
  ``.zip``, which carries no signature. Intel's ``ocloc`` (the Graphics Offline
  Compiler for OpenCL, the oneAPI kernels' compiler) comes as Intel's oneAPI
  offline installer (``ocloc*.exe``, signed by Intel Corporation), run silently
  with ``--eula accept`` (supplying it accepts Intel's license for it), or as an
  ``ocloc*.zip``; either way every ``.exe`` and ``.dll`` beside its ``ocloc.exe``
  must carry Intel Corporation's signature before ``OCLOC_INSTALL_DIR`` names that
  folder. No display driver is installed: CUDA
  gets its compiler and runtime parts only, never ``Display.Driver``, and the
  HIP bundle (``AMD-Software-PRO-Edition-...-For-HIP.exe``, whose documented
  ``-install`` installs "both driver and applications") is not run: only the
  SDK's own MSIs inside it (SDK core, runtime compiler, ray tracing), each
  signature checked again, go through ``msiexec``, and the Radeon driver and
  software beside them are never extracted. ``angelo-hip-args`` set on the VM
  runs the bundle itself with those arguments instead. A refused or failed
  installer leaves the stage to run again next time, and the build then stops
  in seconds for the missing toolkit. ``release-build`` sends ``HEAD``, runs only this
  stage and then ``wait-off``, which waits for the run's end and confirms the
  machine is powered off, stopping it itself if it is still running 15 minutes
  later (it refuses to start while the machine is running, which means another
  run is using it). The release stage uploads ``release-results.json`` as soon
  as it ends, and a ``FAIL`` in it ends the wait at once, hold or not:
  ``wait-off`` names the failed checks, stops the machine and exits 1. A run that reaches the machine's maximum run time first is
  stopped by it; ``release-build`` again continues the incremental build.

With ``ANGELO_GCP_HOLD_MINUTES`` above zero (``redo``), the machine waits before
the desktop stage, and again at the end of the desktop session, running
PowerShell scripts put in its bucket (``windows_command_queue.ps1``):
``send FILE.ps1`` runs one as the administrator (``send FILE.ps1 gui`` in the
desktop session), ``sync`` sends what changed since the confirmed commit to the
machine's tree without a restart, ``out`` prints the newest outputs and
``release`` (``release gui``) ends the wait; otherwise it ends after that many
idle minutes. Whoever can write the run's bucket already decides what the
machine builds and runs, so this adds no new trust. The archives are made with
``COPYFILE_DISABLE=1`` (macOS tar otherwise adds an AppleDouble ``._name`` entry
per file with extended attributes, which would reach the machine and its build)
and the delta lists with ``--no-renames`` (a renamed file's old path is deleted
there too) and ``core.quotePath=false`` (a non-ASCII path travels as its UTF-8
name, not git's octal escapes); the delta archive's paths are literal pathspecs
(``gcp_windows_source.sh``).
The launcher's wait for a run's end (its result, the stall guard, the power-off
check) is ``gcp_windows_wait.sh``. On the machine, the stages that run from the
sent tree (GPU toolkits, desktop session, bench) are in
``windows_bootstrap_stages.ps1``, which the startup script dot-sources only once
the tree is proven to be the sent commit; a stage that needs a restart says so,
and only the startup script restarts the machine.

The machine unpacks every archive (the first send, a full or delta refresh,
``sync``) with ``source_extract.py``, sent beside them: Python's ``tarfile``
keeps each name its exact UTF-8 path. Windows ``tar.exe`` decoded names through
the OEM code page and wrote a garbled twin of each non-ASCII path beside the real
file, which the release's ``source-clean`` check then refused. A delta's deleted
files go first; a path whose type the commit changed (a file that became a
folder, or the reverse, even a folder kept by ignored files) is made way for in
the tree, though never a folder holding a repository; and a name differing only
in letter case is renamed to the commit's spelling (NTFS and APFS keep an old
spelling otherwise). Only files whose content differs are written, and those are
stamped with the current time, so an unchanged file keeps its time and a changed
one is always newer than the last build, even when an older commit is sent again.
Nothing is written outside the tree: absolute, ``..``, ``:`` (an NTFS stream),
``.git`` and link members are refused, and a link already in the tree (a
symbolic link or a Windows junction) in the way of a member or a listed deletion
is itself removed, never written or deleted through. The launcher refuses to send
a commit the machine could never make exact: one that tracks a symbolic link, or
two paths (files or folders) that differ only in letter case
(``source_tree.py sendable``).
Every send also carries the commit itself (its object and the tree
objects the machine lacks), which the machine installs (``source_tree.py
commit``); it then restores any tracked file that differs from the commit's own
objects (never a Git LFS file, whose object is its pointer), removes every
untracked, non-ignored file (leftovers of an earlier extraction, files deleted
since; only once HEAD and the index are the sent commit, since a stale HEAD or
index would make newly sent files untracked too), and proves the tree is the
commit by ``release_checks.py source-clean``'s rule, submodules aside, with two
checks git status cannot make: every tracked path has the commit's exact
spelling on disk, and no large file the build needs (``angelo-lfs-real.txt``, in
every send) is a Git LFS pointer, which the LFS clean filter passes unchanged.
Every send refuses a pointer in the launcher's checkout first. After each stage
the machine names any untracked file the stage wrote into the tree (it would make
the next send all of ``HEAD``); it removes none. The tools let git open the tree
although another account (a desktop or bench session) runs them, for that tree
alone; ``release_checks.py`` keeps git's ownership check. A send already received and
proven (the machine stopped before recording it) is done, not refused.

Which commit the machine has is known only from the machine
(``source_send.py``, ``windows_source_send.ps1``). Each send is its own folder,
``input/send-<nonce>/``, finished by ``send.json`` (commit, mode, base); the
machine is told the nonce only after every file is up. Only once the tree is
proven does the machine write ``input/confirmed-commit``, the one base the
launcher computes a delta from; the launcher writes ``input/pending-commit``
(what it is sending) and never the machine's state. A send cut off on either
side (the upload, or the machine stopping mid-receive) leaves that marker as it
was, so the next send is a delta from what the machine really has; a delta is
applied only onto its own base, and a tree that cannot be proven clears the
marker, so the next send is all of ``HEAD``. Every boot proves the confirmed
commit again and publishes it (a clone publishes its own tree's). Every stage,
and every script that builds or tests the source (``windows_validate.ps1``,
``windows_agent_validate.ps1``, ``windows_thumbnailer_build.ps1``,
``windows_final_runs.ps1 -Stage``, so also when a queued command runs one),
refuses a tree that is not exactly the commit sent, in ``source-clean``'s words.
The launcher's "uploaded commit" marker, written before the machine had the
commit, is gone: twice a cut-off send left it naming a commit the machine never
received, and the next send was an empty delta from it.

One send is one commit. The launcher reads ``HEAD`` once; the archives, the
commit pack, ``send.json`` and the machine's metadata all take that commit. The
Git LFS files and the sendable check read the working tree, so a send whose
``HEAD`` moved, or whose tracked files changed, before ``send.json`` went up is
refused with nothing naming it. A send that read ``HEAD`` at each step once
named one commit and packed a newer one made during it.

Nothing is pushed anywhere: the source travels as an archive of ``HEAD`` plus
the ~40 MB of Git LFS files the build installs (``assets``, ``release/datafiles``,
``release/windows``, ``release/branding`` and every one under ``scripts``: the
application templates and the agent add-on's images) through a private bucket
that only the machine's service account can read. The launcher refuses to send
while any of them is a large-file pointer in the checkout, and the release
build refuses to install one. The machine sits in its
own network whose only inbound rule is IAP's RDP range, so nothing is reachable
from the internet. The Windows libraries come from Blender's own server at the
commit the checkout pins. A startup script installs the GPU driver, the build
tools and the Cloud SDK, runs the stages, uploads the results and powers the
machine off. ``redo`` uploads only the files that changed since the commit the
machine confirmed (``redo --full``, or no confirmed commit, sends all of
``HEAD``), and the large files every time; the machine writes only the files that
differ, so the incremental build recompiles just what depends on them, a full
send included.

Run time and lifetime: on this machine a cold run (tools, libraries, full
build, all checks) takes about 80 minutes and a full rebuild plus the checks
about 50; a run that only changes a few files is mostly the checks (the desktop
stage adds about 10 minutes for its restart). A running machine is billed by
the hour, and its disk also while the machine is stopped. The machine stops
itself when a run ends and, at the latest, after its run's own limit, which
each start sets on the machine and Compute Engine enforces outside it
(``ANGELO_GCP_RUN_HOURS``; ``ANGELO_GCP_MAX_HOURS``, 8, by default; a bench
its ``ANGELO_GCP_BENCH_HOURS`` plus one), put back to the default once the
machine is off (or when its start is refused), so no later send inherits it. While a run is on, the machine
uploads a heartbeat every 5 minutes (``live/HEARTBEAT``: the time, the last
write under its output folders and its newest log's tail), and the Mac's wait
stops a run (on ``ANGELO_GCP_STALL_CONFIRM``, 2, readings in a row) whose
heartbeat is older than ``ANGELO_GCP_HEARTBEAT_MINUTES``
(20), or a bench whose output has not changed for ``ANGELO_GCP_STALL_MINUTES``
(45), saying where it stalled: a hung step ends there, not at the maximum run
time (a bench's preparation once hung silently for 8 hours). A bench's
preparation before its desktop session also has its own deadline on the
machine (``ANGELO_GCP_BENCH_PREP_MINUTES``, 90; a prep's sign-in wait added):
past it the stage is ended and the run fails with ``result/FAILED`` naming
the last lines. Builds can be quiet for longer, so they are held to the
heartbeat and the limit only. ``ANGELO_GCP_TEST_PREP_HANG_MINUTES`` (test
only) makes the preparation hang, and ``ANGELO_GCP_RUN_DURATION`` (e.g.
``25m``) sets the limit outright, so each guard can be shown to fire with an
unbilled ``bench dry``. Before anything else writes, a new send (its nonce
read and new) removes the A/B arm copies under ``bench\ab`` that are not its
own: other labels' ``src-``, ``overlay-``, ``arm-*-overlay.tar.gz`` and
``candidate-`` copies, and app copies of an older candidate under its own
labels. Each is logged with its size (``logs\disk-hygiene-*.log``); links are
never followed, and sign-ins, profiles, the frozen candidates and every
``out*`` result folder stay. A send that builds or benches then refuses to
start below ``ANGELO_GCP_MIN_FREE_GB`` (30, set on every send) free, naming
the largest folders; a held send never does, so a full machine can be cleaned
by hand. The heartbeat reports ``free_gb``; per-label copies once filled a
300 GB disk. The bench user, new on every send, takes its rights from one
local group granted on ``C:\angelo\bench`` once per machine (that one walk
lists each item, so it shows as progress). A desktop session left from an
earlier send exits, and an arm
whose copies were removed is prepared again. It is
stopped, never deleted, so the build tree survives. Run
``down`` when the machine is no longer needed. ``gcloud`` asks for a new
sign-in (``gcloud auth login``) after some hours; a run that is waiting keeps
going without it.

For checks by eye, connect with an RDP client through an IAP tunnel (``gcloud
compute start-iap-tunnel <vm-name> 3389 --local-host-port=localhost:3389
--zone <zone>``, where ``<vm-name>`` is ``ANGELO_GCP_NAME`` and ``<zone>``
``ANGELO_GCP_ZONE``; user and password from ``gcloud
compute reset-windows-password``), which keeps RDP off the public internet.

Links
=====

Help menu entries (Manual, Support, User Communities, Release Notes, then
Blender Manual under a "Blender" heading, Report a Bug, the splash and About
links) come from the ``urls`` table. Donate is not a Help entry. The Angelo
menu's About opens Angelo's About card (``about_card.py``, ``angelo.show_about``;
Blender's About popup is the fallback without the add-on): Angelo's links (What's
New, Credits, License, angelo.studio) first, then Donate, blender.org and Support
Blender below a rule. Every About (card, popup, the macOS app menu's panel) shows
the version in the title bar's convention (``APP_VERSION_DISPLAY``: "1.0" for
1.0.0) with "Based on Blender 5.2.x" and the build date and hash, never the
source branch.
OS windows and commands say Angelo or what they do, never Blender: a file
browser window is titled with its operation ("Save As", "Import FBX"), F12 opens
"Render", the quit command is "Quit Angelo", and the factory resets are "Load
Factory Settings" and "Load Factory Preferences". The Angelo destinations are pages on ``angelo.studio``; ``pending_urls``
lists those a release must still confirm. The Blender
entries (Blender Manual, Python API, license, credits, website, extensions,
developer links, and "Support Blender") document or credit Blender itself.
"Report a Bug" opens ``urls.report_bug`` with ``os``, ``gpu`` and ``version``
query values (the forms ``version`` takes are under "Known limits").
``urls.source`` is the GPL source page that ``release/text/source_offer.txt``
names in both installers (``releasing.rst``, "Source code"); it must list each
release's source archive before that release is offered for download.

The agent add-on appends its own entries below Blender's at runtime: Welcome
to Angelo (``intro.py``), then Check for Updates… and What's New
(``update_ui.py``, see ``updates_and_reports.rst``). Release Notes opens the
release notes page on the site; What's New shows this version's notes in the
app and links to the same page (``app_version.link("release_notes")``, which
reads this table).

The interface text the identity adds to Blender's own scripts (the Help menu,
the URL presets' tooltips, the splash and About) is Angelo's, not Blender's, so
it lives in Angelo's text domain: ``bl_operators/wm.py`` and
``bl_ui/space_topbar.py`` are in ``sources.SHARED_PYTHON_FILES``, the extractor
keeps what Blender's catalogues do not know, and
``test_native_translation.py`` fails when such a string is missing from the
template (``localization.rst``).

Attribution
===========

Nothing here removes or hides a license or attribution notice. ``COPYING``,
``release/license/`` and the SPDX headers are unchanged; About states
"Based on Blender"; ``copyright.txt`` (installed by default) keeps Blender's
original notice beside Angelo's; the readme and metainfo say Angelo is based on
Blender; the Blender manual, credits and license links remain.

Validation
==========

``tests/python/angelo_agent/test_native_app_identity.py`` (host suite) pins the
single source, renders every template through the real CMake loader, and fails
if a file that carries identity hard-codes a Blender value.
``test_native_user_folder_migration.py`` covers the migration. The compiled
checks (config folder, About, splash, Help menu, the unchanged stock Blender
settings) are in ``native_editor_build.rst``. ``test_blender_app_identity.py``
(GUI, ``run_native_suites.py --gui``) also checks the merged Help menu (the
identity's entries, then the agent add-on's Welcome to Angelo, Check for
Updates… and What's New), reads the real window's title from AppKit inside the
app on macOS (Blender exposes no title to Python, and a screen capture would
need a Screen Recording grant) and captures the window frame AppKit draws,
title bar included (``window-title.png``, "Untitled - Angelo 1.0"), and the
folders the window's own process resolved (``paths.png``).

The window chrome has its own pair. ``test_native_window_chrome.py`` (host
suite) derives the colour from the facet, finds it on the top bar of both
bundled themes and nowhere else, recomputes the contrasts in the table above
from the theme files, and pins the two rules. ``test_blender_window_chrome.py``
(GUI) measures the compiled app: the top bar's pixels, the status bar and an
editor header that must not change, the colour the menu titles are drawn in for
both themes, and on macOS (through ``macos_window.title_bar``) the colour and
text appearance AppKit holds for the title bar of the first window, of a second
main window, of an editor window, and after a theme sets a top bar colour of
its own. ``title-bar.png`` is the frame as AppKit draws it. Not covered by a
run: Windows and Linux, macOS full screen, and the seam between the title bar
and the top bar as a display shows it (the capture draws the frame, not the
GPU content beside it).

The installer artwork is pinned by ``test_native_branding.py`` (host suite):
the three files are the exporter's output at the sizes above (by content, or by
the Git LFS pointer where the file is not fetched), the recorded contrasts hold
the floors, the disk image script and the picture agree on the window, and the
artwork takes its light from the splash module rather than a copy of it.
``test_native_app_identity.py`` renders the script through the CMake loader.
Nothing here opens a disk image or an installer.

Checks by eye
-------------

* **Installer artwork.** Build one disk image with ``ANGELO_DMG_STYLED=ON`` in
  a GUI session and open it in the light and the dark appearance: the window is
  540 points wide and shows the picture to its bottom edge, both names are
  black and centred on their plaques, the two icons stand above them, and
  ``copyright.txt`` is out of sight. Run the MSI with its pages on Windows 10 or
  11: the title and description are on white, right of the panel; the banner's
  text ends before its cap; the finish page's check box shows no box of another
  grey.
* **Window chrome on Windows.** Run ``test_blender_window_chrome`` in the
  desktop stage and look at a main window on Windows 11 in both system modes:
  the caption is the top bar's teal, its title is white, and the minimise,
  maximise and close glyphs read on it (Windows draws them; nothing sets their
  colour). On Windows 10 the caption stays the system's.
* **Windows.** What no automated run covers is listed under "Windows
  validation"; check those by hand on Windows 10 or 11.

Known limits
============

* **The report page's ``version``.** Three senders fill it differently: Help >
  Report a Bug (``Angelo 1.0.0 (Blender 5.2.2 LTS), branch: …, hash: …``), the
  in-app crash dialog (``1.0.0 (<build hash>)``, ``crash_flow.py``) and the
  Windows crash dialog (Blender's ``5.2.2 LTS, Commit date: …, hash: …``,
  built in ``creator_signals.cc`` from ``BKE_blender_version_string``). The
  page has to accept all three, unless the Windows dialog is changed to send
  the product version (a Windows-only C++ change).
* **Two "What's New".** The splash and About keep Blender's What's New entry,
  which opens the release notes page (``RELEASE_NOTES``); the Help menu's What's
  New, from the agent add-on, shows this version's notes in the app. The two
  share a label but open different things.
* **Signing.** Development builds are ad-hoc signed, so the macOS Keychain
  identifies them by the executable's hash. A team sign-in or a saved API key
  stored by one build cannot be read by the next build whose executable
  differs (any C++ change), and the team sign-in has no "approve" route: sign
  in again with the new build (``ai_providers.rst``, "Development builds").
  Items stored by a build signed with an Apple-issued identity (Apple
  Development or Developer ID) carry that team's partition, so later builds
  signed by the same team should read them without a new sign-in (Apple's
  partition rule; not yet exercised by a run). Developer ID signing, the
  hardened runtime and notarization are in ``releasing.rst``.
* **Migration.** ``migrate_user_folder.py`` is a command the person runs; the
  app does not offer it.
* **Linux.** Only macOS and Windows rename the executable; on Linux it is still
  ``blender``, so ``crash_log.ANGELO_IMAGE`` does not recognise a Linux crash as
  Angelo's.
